"""
content_utils.py
크롤링/정제 관련 순수 함수 모음.

main.py와 collectors.py 양쪽에서 공용으로 쓰기 위해 별도 모듈로 분리했다.
이 모듈은 main.py나 collectors.py 어느 쪽도 참조하지 않으므로 순환 import가 없다.

주요 특징:
- _crawl_single_target_async()는 "정제되지 않은 원본" 마크다운을 반환한다.
  정제(clean_article_content)는 호출부(collectors.py)에서 별도로 수행해서,
  raw_content(원본)와 content(정제본)를 둘 다 남길 수 있게 했다.
- PruningContentFilter로 크롤링 시점에 본문 밀도가 낮은 영역(nav/광고/사이드바)을
  통계적으로 걸러낸다. 이후 정제 단계의 부담을 크게 줄여준다.
- 20초 타임아웃 — 특정 URL이 멈추면 전체 파이프라인(및 DB 잠금)까지 물고
  늘어지는 문제가 있었기 때문에 반드시 필요하다.
- is_crawl_failure()로 크롤링/정제가 실패했음을 나타내는 플레이스홀더 텍스트를
  판별한다. 봇 차단(WSJ/Bloomberg 등) 같은 케이스는 DB에 빈 기사로 남기지 않는다.
- activity_tracker와 연동해 "지금 어떤 URL을 크롤링 중인지"를 실시간으로 노출한다.
"""

import re
import asyncio
import logging
import threading
from urllib.parse import urlparse

import trafilatura
from curl_cffi import requests as curl_requests

from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
from crawl4ai.content_filter_strategy import PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

import model_router
import priority
import activity_tracker



logger = logging.getLogger(__name__)


def _strip_boilerplate_edges(lines: list[str]) -> list[str]:
    """본문 앞/뒤에 붙은 짧고 광고성/안내성인 줄들을 제거한다."""
    boilerplate_hint = re.compile(
        r'(공유|구독|팔로우|follow|subscribe|sponsored|©|더보기|바로가기|앱\s*다운로드)',
        re.IGNORECASE
    )

    def is_edge_noise(line: str) -> bool:
        s = line.strip()
        if not s:
            return True
        if len(s) < 40 and not s.endswith(('.', '!', '?', '다', '요', '음')):
            return True
        if boilerplate_hint.search(s) and len(s) < 60:
            return True
        return False

    start = 0
    while start < len(lines) and is_edge_noise(lines[start]):
        start += 1

    end = len(lines)
    while end > start and is_edge_noise(lines[end - 1]):
        end -= 1

    return lines[start:end]


def clean_article_content(raw_markdown: str) -> str:
    """아티클 내용 정제"""
    if not raw_markdown:
        return "내용 없음"

    content = raw_markdown
    noise_patterns = [
        r'Advertisement', r'Sponsored', r'AD\b',
        r'광고', r'협찬', r'홍보',
        r'All rights reserved', r'무단\s*전재\s*및?\s*재배포\s*금지', r'저작권자?\s*ⓒ',
        r'구독하기', r'구독\s*신청', r'뉴스레터\s*구독', r'Sign up for our newsletter',
        r'Share this article', r'이\s*기사를?\s*공유', r'카카오톡\s*공유', r'페이스북\s*공유',
        r'Related Posts', r'관련\s*기사', r'많이\s*본\s*기사', r'인기\s*기사', r'Read more:', r'Related:',
        r'댓글\s*\d*개?', r'로그인\s*후\s*이용', r'기자\s*프로필', r'Follow us on',
    ]

    for pattern in noise_patterns:
        content = re.sub(pattern, '', content, flags=re.IGNORECASE)

    # 소셜 공유 위젯 줄 제거 (예: "* email (opens in new window)", "twitter (opens in new window)")
    # 특정 플랫폼 이름을 나열하지 않고, 이 패턴으로 끝나는 짧은 줄 자체를 일반적으로 걸러낸다.
    content = re.sub(
        r'^\s*[\*\-\+]?\s*[\w][\w\s]{0,25}\(opens in new window\)\s*$',
        '',
        content,
        flags=re.IGNORECASE | re.MULTILINE
    )

    lines = content.split('\n')
    cleaned_lines = []
    link_list_streak = 0

    for line in lines:
        stripped = line.strip()
        is_link_item = bool(re.match(r'^[\*\-\+]\s*\[.+?\]\(.+?\)$', stripped) or re.match(r'^\[.+?\]\(.+?\)$', stripped))

        if is_link_item:
            link_list_streak += 1
        else:
            if link_list_streak < 3:
                cleaned_lines.extend(lines[max(0, len(cleaned_lines) - link_list_streak):len(cleaned_lines)])
            link_list_streak = 0
            cleaned_lines.append(line)

    if link_list_streak >= 3:
        cleaned_lines = cleaned_lines[:-link_list_streak] if len(cleaned_lines) >= link_list_streak else []

    cleaned_lines = _strip_boilerplate_edges(cleaned_lines)
    content = '\n'.join(cleaned_lines)
    content = re.sub(r'\n\s*\n', '\n\n', content)

    content = re.sub(r'^#\s+', '### ', content, flags=re.MULTILINE)
    content = re.sub(r'^##\s+', '### ', content, flags=re.MULTILINE)

    content = re.sub(r'<[^>]*dable-api[^>]*>', '', content)
    content = re.sub(r'https?://[^\s]*dable-api[^\s]*', '', content)

    content = re.sub(r'<h1\b[^>]*>(.*?)<\/h1>', r'<h3>\1</h3>', content, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r'<h2\b[^>]*>(.*?)<\/h2>', r'<h3>\1</h3>', content, flags=re.IGNORECASE | re.DOTALL)

    def limit_font_size(match):
        full_match, property_name, size_val, unit = match.groups()
        try:
            val = float(size_val)
            if unit.lower() == 'pt' and val > 20:
                return 'font-size: 20pt;'
            elif unit.lower() == 'px' and val > 26.6:
                return 'font-size: 20pt;'
        except ValueError:
            pass
        return full_match

    content = re.sub(r'(font-size\s*:\s*([0-9.]+)\s*(pt|px))', limit_font_size, content, flags=re.IGNORECASE)
    content = content.strip()

    if len(content) < 30:
        return "유효한 본문 내용을 추출하지 못했습니다."

    return content


def enforce_max_paragraph_lines(text: str, max_chars: int = 300) -> str:
    """
    LLM은 '몇 줄'을 정확히 세지 못하므로, 문단 길이 제한은 후처리로 강제한다.
    이미 있는 문단 구분(빈 줄)은 존중하고, max_chars를 넘는 문단만 문장 경계 기준으로 쪼갠다.
    """
    SENTENCE_SPLIT = re.compile(r'(?<=[.!?다요음됨임함습니다])\s+')

    paragraphs = re.split(r'\n\s*\n', text.strip())
    result_paragraphs = []

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if para.startswith(('#', '```', '-', '*', '|')) or len(para) <= max_chars:
            result_paragraphs.append(para)
            continue

        sentences = SENTENCE_SPLIT.split(para)
        chunk = ""
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            candidate = f"{chunk} {sentence}".strip() if chunk else sentence
            if len(candidate) > max_chars and chunk:
                result_paragraphs.append(chunk)
                chunk = sentence
            else:
                chunk = candidate
        if chunk:
            result_paragraphs.append(chunk)

    return '\n\n'.join(result_paragraphs)


def extract_body_via_llm(raw_content: str) -> str:
    """LLM으로 본문 여부를 판단해 노이즈를 제거하고, 문단을 대략적으로 재구성한다."""
    system_prompt = (
        "You are a precise content extraction and formatting assistant. You will be given "
        "the raw text/markdown of a crawled web article, which may contain non-article "
        "elements mixed in with the real body content, and may lack paragraph breaks entirely.\n\n"
        "### YOUR TASK\n"
        "1. Output ONLY the actual article body — remove everything else.\n"
        "2. Ensure the body is broken into readable paragraphs.\n\n"
        "### KEEP (this counts as body content):\n"
        "1. Sentences that convey the article's core facts (who/what/when/where/why/how).\n"
        "2. Reporting content: quotes, statistics, data, expert commentary.\n"
        "3. Subheadings that are part of the narrative structure of the story itself.\n"
        "4. Image/chart captions ONLY if they add descriptive information (not pure photo credits).\n\n"
        "### REMOVE (this is NOT body content):\n"
        "1. Navigation menus, category link lists.\n"
        "2. Advertisements, sponsored content markers.\n"
        "3. Social share / subscribe / follow prompts.\n"
        "4. 'Related articles', 'Most read', recommendation widgets.\n"
        "5. Comment section prompts, login prompts.\n"
        "6. Reporter bio boxes with email/social links.\n"
        "7. Copyright notices, reproduction-prohibited legal text.\n"
        "8. Newsletter signup prompts.\n"
        "9. Cookie/privacy consent banners.\n"
        "10. Site-wide UI text (Home, Login, Search, etc.).\n"
        "11. Repeated title/subtitle (the title is stored separately elsewhere).\n"
        "12. Pure photo-credit-only captions (e.g. 'Photo: John Doe').\n\n"
        "### DECISION RULE (for keep/remove)\n"
        "For any ambiguous line, ask: 'If this sentence were deleted, would the article's "
        "facts and argument still be fully intact?' If yes, remove it. If no, keep it.\n\n"
        "### PARAGRAPH FORMATTING\n"
        "1. If the source already has natural paragraph breaks, preserve them as-is.\n"
        "2. If the source is a single unbroken wall of text with NO paragraph breaks, "
        "insert paragraph breaks at natural topic shifts so it reads more comfortably. "
        "Do not worry about matching an exact line count — just make reasonable breaks.\n\n"
        "### OUTPUT FORMAT\n"
        "Output the cleaned, paragraph-formatted body text only, in the original language, "
        "preserving markdown formatting (headers, lists, etc.) of what remains. Do NOT add "
        "any preamble, explanation, or notes about what you removed or reformatted. "
        "Do NOT translate anything."
    )

    try:
        priority.yield_to_person() # 추가: 사람이 기다리고 있으면 최대 15초 양보
        llm_output = model_router.chat(
            task="extract_body",
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': raw_content}
            ],
        )
        return enforce_max_paragraph_lines(llm_output, max_chars=300)
    except Exception as e:
        logger.error(f"LLM 본문 추출 오류: {str(e)}")
        return raw_content


_CRAWL_FAILURE_MARKERS = [
    "Crawl4AI 본문 추출 실패",
    "크롤링 타임아웃",
    "크롤링 중 에러 발생",
    "크롤링 실패",
    "유효한 본문 내용을 추출하지 못했습니다",
    "쿠키/개인정보 동의 배너로 판별됨",
]

# OneTrust/Sourcepoint/Quantcast 등 CMP(Consent Management Platform)가 뿌리는
# 쿠키 동의 배너 문구의 특징적인 표현들. 실제 기사 안에서 "cookie"란 단어가
# 한두 번 스치는 정상적인 경우를 오탐하지 않도록, 여러 개가 함께 나와야만
# "동의 배너 자체가 본문으로 잘못 크롤링됐다"고 판단한다.
_CONSENT_BOILERPLATE_HINTS = [
    r'consent management platform',
    r'tracking technolog',
    r'opt[- ]out',
    r'\bgdpr\b',
    r'adchoices',
    r'privacy policy',
    r'these cookies are set by',
    r'track(?:ing)? your browser across (?:other )?sites',
    r'build(?:ing)? up a profile of your interests',
    r'share (?:our|this) content with your friends',
]


def _looks_like_consent_boilerplate(text: str) -> bool:
    """
    크롤링된 텍스트가 실제 기사 본문이 아니라 쿠키/개인정보 동의 배너(CMP) 문구인지
    판별한다. Crawl4AI가 "성공"으로 보고해도, 사이트가 동의 배너로 본문을 가려버린
    상태에서 캡처하면 그 배너 문구가 밀도(density) 기준을 통과해 fit_markdown/
    raw_markdown으로 그대로 반환되는 경우가 있다.
    """
    if not text:
        return False
    lower = text.lower()
    hits = sum(1 for pattern in _CONSENT_BOILERPLATE_HINTS if re.search(pattern, lower))
    # 힌트 문구가 3개 미만이면 진짜 기사에서 쿠키를 언급한 정상적인 경우일 수 있으므로 통과.
    # 4000자 이상의 충분히 긴 글이면 힌트가 몇 개 섞여 있어도 대부분 실제 본문일 가능성이 높음.
    return hits >= 3 and len(text.strip()) < 4000

# 페이월/구독 유도 문구 힌트. 실제 기사 안에서 "subscribe"란 단어가 한두 번
# 스치는 정상적인 경우를 오탐하지 않도록, consent 배너와 동일한 원칙(여러 개가
# 함께 나와야만 판정)을 적용한다. TradingView/Reuters 신디케이션처럼 본문 대신
# "Get unlimited access to articles from..." 한 줄만 반환하는 사이트에서 발견됨
# (2026-08-10).
_PAYWALL_BOILERPLATE_HINTS = [
    r'unlimited access to articles',
    r'subscribe to (?:continue|read)',
    r'sign in to (?:continue|read)',
    r'start your free trial',
    r'become a (?:member|subscriber)',
    r'subscription required',
    r'this content is (?:for|reserved for) subscribers',
    r'to continue reading',
    r'create a free account',
    r'already a subscriber\??',
]


def _looks_like_paywall_boilerplate(text: str) -> bool:
    """
    크롤링된 텍스트가 실제 기사 본문이 아니라 페이월/구독 유도 안내문인지 판별한다.
    _looks_like_consent_boilerplate()와 동일한 원칙: 힌트가 2개 이상 겹치고,
    전체 길이가 짧을 때만(진짜 기사 본문 안에 우연히 한 문구가 섞인 경우를 오탐하지
    않기 위해) 페이월로 판정한다. 동의배너보다 훨씬 짧게(보통 한두 문장) 나오는
    경우가 많아 임계값을 낮게(1000자) 잡았다.
    """
    if not text:
        return False
    lower = text.lower()
    hits = sum(1 for pattern in _PAYWALL_BOILERPLATE_HINTS if re.search(pattern, lower))
    return hits >= 2 and len(text.strip()) < 1000


def _select_crawled_markdown(fit: str | None, raw: str | None) -> str:
    """
    fit_markdown(밀도 필터링본)을 우선 쓰되, 쿠키 동의 배너나 페이월 안내문으로
    판별되면 raw_markdown으로 한 번 더 시도한다. 둘 다 그렇게 보이면 실패로
    처리해서(플레이스홀더 반환) DB에 쌓이지 않도록 한다 (is_crawl_failure()가
    걸러줌). 2026-08-10: 페이월 판별 추가 (TradingView/Reuters 신디케이션 등에서
    "Get unlimited access..." 같은 안내문이 본문으로 저장되던 문제 방지).
    """
    def _is_boilerplate(text: str) -> bool:
        return _looks_like_consent_boilerplate(text) or _looks_like_paywall_boilerplate(text)

    candidates = []
    if fit and len(fit.strip()) >= 200:
        candidates.append(fit)
    if raw:
        candidates.append(raw)

    for candidate in candidates:
        if not _is_boilerplate(candidate):
            return candidate

    if candidates:
        last = candidates[-1]
        if _looks_like_paywall_boilerplate(last):
            return "크롤링 결과가 페이월/구독 안내문으로 판별됨 (본문 추출 실패)"
        return "크롤링 결과가 쿠키/개인정보 동의 배너로 판별됨 (본문 추출 실패)"
    return raw or "Crawl4AI 본문 추출 실패"

def is_crawl_failure(text: str) -> bool:
    """
    크롤링/정제 결과가 실패를 나타내는 플레이스홀더 텍스트인지 판별한다.
    이걸로 걸러진 기사는 DB에 저장하지 않는다 (WSJ/Bloomberg처럼 봇 차단이
    걸린 사이트를 굳이 빈 내용으로 쌓아둘 필요가 없음).
    """
    if not text:
        return True
    stripped = text.strip()
    if len(stripped) < 30:
        return True
    return any(marker in stripped for marker in _CRAWL_FAILURE_MARKERS)


_FALLBACK_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}


def _try_trafilatura_fallback(url: str) -> str | None:
    """
    crawl4ai(헤드리스 브라우저)가 실패로 판정됐을 때 마지막으로 시도하는 폴백.

    일반 requests 라이브러리는 TLS Handshake 지문(JA3)이 매우 특징적이라
    Cloudflare/PerimeterX/Akamai 같은 방화벽이 브라우저 JS 실행 여부를 보기도
    전에 즉시 차단한다. curl_cffi(impersonate="chrome124")는 실제 Chrome의
    TLS/HTTP2 지문을 그대로 재현해서 이 1차 차단선을 통과한다.
    (그래도 강한 상용 안티봇에 CAPTCHA/JS 챌린지까지 걸린 사이트는 못 뚫는다 -
    그런 사이트는 계속 is_crawl_failure()로 걸러지는 게 정상.)
    """
    try:
        resp = curl_requests.get(
            url,
            headers=_FALLBACK_HEADERS,
            impersonate="chrome124",
            timeout=10,
        )
        if resp.status_code != 200 or not resp.text:
            return None

        extracted = trafilatura.extract(
            resp.text,
            output_format="markdown",
            include_formatting=True,
            include_tables=True,
            include_images=False,
            favor_recall=True,
        )
        return extracted
    except Exception as e:
        logger.warning(f"[Trafilatura 폴백] 실패: {url} ({e})")
        return None


def classify_block_reason(raw_content: str) -> str:
    """
    is_crawl_failure()가 True를 반환한 텍스트를 보고, 출처관리 '블록리스트'에
    사람이 한눈에 훑어볼 수 있는 짧은 한글 키워드 사유로 변환한다.
    """
    if not raw_content:
        return "본문없음"
    text = raw_content.strip()
    if "타임아웃" in text:
        return "타임아웃"
    if "페이월" in text or "구독 안내문" in text:
        return "페이월차단"
    if "동의 배너" in text or "쿠키" in text:
        return "동의배너차단"
    if "크롤링 중 에러" in text:
        return "크롤링에러"
    if "본문 추출 실패" in text or "유효한 본문" in text:
        return "본문추출실패"
    if len(text) < 30:
        return "본문없음"
    return "차단(원인불명)"


async def _crawl_single_target_async(target_url: str) -> str:
    """
    단일 URL 크롤링 비동기 헬퍼.

    PruningContentFilter로 크롤링 시점에 본문 밀도가 낮은 영역(내비게이션/광고/
    사이드바 등)을 통계적으로 걸러낸 fit_markdown을 우선 사용한다. 20초 안에
    안 끝나면 포기한다 (타임아웃 없으면 특정 URL에서 영원히 멈춰서 DB 잠금까지
    물고 늘어지는 문제가 있었음).
    """
    domain = urlparse(target_url).netloc or target_url
    with activity_tracker.track_component("수집기 · 크롤링", f"크롤링 중: {domain}"):
        try:
            prune_filter = PruningContentFilter(
                threshold=0.48,          # 낮을수록 더 많이 남기고, 높을수록 더 많이 쳐냄
                threshold_type="dynamic",
                min_word_threshold=5,    # 단어 5개 미만인 노드는 애초에 무시
            )
            md_generator = DefaultMarkdownGenerator(content_filter=prune_filter)
            config = CrawlerRunConfig(
                markdown_generator=md_generator,
                excluded_tags=["nav", "footer", "header", "aside", "form"],
                exclude_external_links=True,
                word_count_threshold=10,
                magic=True,               # 쿠키/동의 배너, 팝업 오버레이 자동 처리
                simulate_user=True,       # 사람처럼 마우스 이동/스크롤을 흉내내 봇 탐지 회피
                override_navigator=True,  # 헤드리스 브라우저임을 드러내는 navigator 지문 숨김
            )

            async with AsyncWebCrawler(verbose=False) as crawler:
                result = await asyncio.wait_for(
                    crawler.arun(url=target_url, config=config),
                    timeout=20,
                )
                if not result.success:
                    return "Crawl4AI 본문 추출 실패"

                fit = getattr(result.markdown, "fit_markdown", None)
                raw = getattr(result.markdown, "raw_markdown", None) or str(result.markdown)

                # fit_markdown이 지나치게 짧으면(필터가 과하게 걸렀을 가능성) 원본으로 폴백,
                # 쿠키 동의 배너로 판별되면 원본으로 한 번 더 시도(그래도 배너면 실패 처리)
                return _select_crawled_markdown(fit, raw)

        except asyncio.TimeoutError:
            logger.error(f"크롤링 타임아웃(20초): {target_url}")
            return "크롤링 타임아웃"
        except Exception as e:
            logger.error(f"크롤링 에러 ({target_url}): {type(e).__name__}: {e}", exc_info=True)
            return f"크롤링 중 에러 발생: {str(e)}"


# 주의: concurrent.futures.ThreadPoolExecutor는 daemon 스레드가 아니라서, 인터프리터
# 종료 시 atexit이 진행 중인 작업이 끝날 때까지 join으로 붙잡는다 - 크롤링 도중
# Ctrl+C를 눌러도 그 크롤링이 끝나야만 프로세스가 죽는 원인이 된다. 아래에서는
# ThreadPoolExecutor 대신 daemon=True 스레드를 호출마다 직접 띄워서, 프로세스
# 종료 시 join 없이 즉시 함께 죽도록 한다 (crawl_url_sync는 어차피 매번 동기적으로
# 결과를 기다렸다가 반환하는 방식이라, 풀을 재사용해서 얻는 이득이 거의 없었다).


def _crawl_via_crawl4ai_sync(url: str, timeout: int = 30) -> str:
    """
    crawl4ai(헤드리스 브라우저) 경로. 지정 시간 내 안 끝나면 그 URL은 포기하고
    즉시 반환한다 (전체 파이프라인이 하나의 느린 URL 때문에 통째로 멈추는 걸 방지).
    """
    result_holder: dict = {}

    def _run():
        try:
            result_holder["value"] = asyncio.run(_crawl_single_target_async(url))
        except Exception as e:
            result_holder["error"] = e

    domain = urlparse(url).netloc or url
    thread = threading.Thread(target=_run, daemon=True, name=f"crawl-{domain}")
    thread.start()
    thread.join(timeout=timeout)

    if thread.is_alive():
        logger.error(f"crawl_url_sync 타임아웃({timeout}초): {url}")
        return "크롤링 타임아웃"

    if "error" in result_holder:
        logger.error(f"crawl_url_sync 에러: {url}: {result_holder['error']}")
        return f"크롤링 중 에러 발생: {result_holder['error']}"

    return result_holder.get("value", "크롤링 타임아웃")


def crawl_url_sync(url: str, timeout: int = 30) -> str:
    """
    동기 컨텍스트(APScheduler job, 동기 API 엔드포인트)에서 크롤링을 실행하는 헬퍼.

    1차: crawl4ai(헤드리스 브라우저) - JS 렌더링이 필요한 페이지에 강함.
    2차(폴백): 1차가 실패로 판정되면 Trafilatura(순수 HTTP GET)로 한 번 더 시도한다.
    둘 다 실패하면 1차 결과(플레이스홀더 텍스트)를 그대로 반환해 is_crawl_failure()가
    걸러내게 한다.
    """
    primary = _crawl_via_crawl4ai_sync(url, timeout=timeout)
    if not is_crawl_failure(primary):
        return primary

    fallback = _try_trafilatura_fallback(url)
    if fallback and not is_crawl_failure(fallback):
        logger.info(f"[crawl_url_sync] crawl4ai 실패 -> Trafilatura 폴백 성공: {url}")
        return fallback

    # nodriver(3차, Cloudflare 챌린지 우회 시도)는 Python 3.14와 라이브러리
    # 내부 호환 문제로 매번 예외만 내며 실패해서 제거함 (2026-08-08).
    # Python 버전을 낮추면 재도입을 검토할 수 있음 - 그 전까지는 1·2차까지만 사용.
    return primary
