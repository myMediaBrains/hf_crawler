"""
collectors.py
Collector 플러그인 레지스트리.

각 Collector는 BaseCollector를 상속하고 collect(source, session)만 구현하면
COLLECTOR_REGISTRY에 등록되어 스케줄러/API에서 바로 쓸 수 있다.

지금 구현: RSSCollector(기존 34개 고정 소스), GoogleNewsSearchCollector(키워드 기반)
향후 추가 예: YouTubeCollector, PodcastCollector, ImageGalleryCollector 등
  -> 새 클래스 하나 작성 + COLLECTOR_REGISTRY에 한 줄 등록만 하면 확장 끝.
  (Source/스케줄러/승격/실패 관리 인프라는 전혀 안 건드림)
"""

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from urllib.parse import urlparse, quote

import job_control  # 파일 상단에 추가

from googlenewsdecoder import gnewsdecoder

import requests
import feedparser
from sqlmodel import Session, select

from models import Article, Source, Keyword, ContentOrigin, SourceOrigin, SourceStatus, BlockedDomain
from content_utils import clean_article_content, crawl_url_sync, is_crawl_failure, classify_block_reason
from personalization import classify_and_store

logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


class CollectResult:
    """
    Collector.collect()의 반환값.
    discovered_domains: 이번 수집에서 발견된 (도메인, 표시이름) 목록.
    키워드 기반 수집에서만 채워지고, scheduler.py가 이걸로 CandidateSource
    승격 여부를 판단한다. RSS 고정 소스 수집에서는 항상 빈 리스트.
    """
    def __init__(self, new_count: int = 0, discovered_domains: list[tuple[str, str]] | None = None):
        self.new_count = new_count
        self.discovered_domains = discovered_domains or []


class BaseCollector(ABC):
    @abstractmethod
    def collect(self, source: Source, session: Session) -> CollectResult:
        """Source 한 건을 받아 수집을 실행하고 결과를 반환한다."""
        ...


class RSSCollector(BaseCollector):
    """
    고정 RSS 피드 수집기. 기존 main.py run_collection_job()의 개별-소스
    처리 로직과 동일한 동작을 하되, TARGET_SOURCES 리스트 대신 Source 테이블의
    행 하나를 받아 처리하도록 바뀌었다.
    """

    def collect(self, source: Source, session: Session) -> CollectResult:
        new_count = 0

        response = requests.get(source.url, headers=HEADERS, timeout=8)
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}")

        feed = feedparser.parse(response.content)

        for entry in feed.entries[:2]:
            title = entry.get("title", "No Title")
            link = entry.get("link", "")
            published = entry.get("published", entry.get("updated", str(datetime.now())))

            if not link:
                continue

            existing = session.exec(select(Article).where(Article.url == link)).first()
            if existing:
                continue

            raw_content = crawl_url_sync(link)
            if is_crawl_failure(raw_content):
                logger.warning(f"[RSSCollector] 크롤링 실패/본문 추출 불가로 건너뜀: {link}")
                continue

            cleaned = clean_article_content(raw_content)

            article = Article(
                title=title,
                url=link,
                published_at=published,
                content=cleaned,
                raw_content=raw_content,
                source=source.name,
                origin=ContentOrigin.RAW_CRAWL,
            )
            # 저장 시점에 카테고리를 미리 계산해둔다 (2026-08-09) - 예전엔
            # /stats/keywords가 매번 기사 전체를 순회하며 재계산해서, 기사가
            # 쌓일수록(3,600건+ 기준 실측 116초) 점점 느려지는 근본 원인이었다.
            # main.py는 collectors.py를 import하므로, 반대 방향 import는
            # 순환참조가 나서 함수 호출 시점에 지연 import한다.
            from main import _best_category_for_article
            article.category = _best_category_for_article(article)

            session.add(article)
            session.flush()  # commit 전에 article.id를 확보하기 위한 flush
            # 고정 RSS 수집 = 사용자가 직접 요청한 게 아니라 백그라운드에서
            # 자동으로 들어온 기사이므로 약한 암묵적 신호(weight=0.3)로 기록한다.
            classify_and_store(
                session, article.title, article.content or "",
                source="extension", signal_type="implicit", weight=0.3,
                article_id=article.id,
            )
            new_count += 1

        session.commit()
        return CollectResult(new_count=new_count)


class GoogleNewsSearchCollector(BaseCollector):
    """
    Google 뉴스 검색 기반 컬렉터. 두 가지 진입점이 있다:

    1. collect_for_keyword(): 키워드 자체의 폭넓은 검색.
       사용자가 검색창에 새 키워드를 입력했을 때(즉시 1회) 그리고
       백그라운드 반복 수집(스케줄러 틱)에서 호출된다.
       기사마다 실제 출처가 다르므로, 발견된 (도메인, 출처명) 목록을 함께
       반환해서 scheduler.py가 CandidateSource.hit_count를 추적할 수 있게 한다.

    2. collect(): BaseCollector 표준 인터페이스. 이미 "키워드+특정 도메인"으로
       좁혀진 검색 URL을 가진 Source(자동 승격된 소스)를 처리한다.
       도메인이 이미 확정된 상태라 추가 후보 추적은 하지 않는다.
    """

    def collect(self, source: Source, session: Session) -> CollectResult:
        return self._fetch_and_save(
            query_url=source.url,
            fixed_source_name=source.name,
            keyword_name=source.category,
            session=session,
            track_domains=False,
        )

    def collect_for_keyword(self, keyword: Keyword, session: Session, max_entries: int = 20) -> CollectResult:
        query_url = self._build_keyword_search_url(keyword.name, keyword.months_back)
        return self._fetch_and_save(
            query_url=query_url,
            fixed_source_name=None,
            keyword_name=keyword.name,
            session=session,
            track_domains=True,
            max_entries=max_entries,
        )

    def _fetch_and_save(
        self,
        query_url: str,
        fixed_source_name: str | None,
        keyword_name: str | None,
        session: Session,
        track_domains: bool,
        max_entries: int = 20,
    ) -> CollectResult:
        new_count = 0
        discovered: list[tuple[str, str]] = []

        response = requests.get(query_url, headers=HEADERS, timeout=8)
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}")

        feed = feedparser.parse(response.content)

        # 사용자가 출처관리에서 삭제해 blocked_domains에 등록한 도메인들 - 이번 수집
        # 전체에서 한 번만 조회해두고, 아래 루프에서 나올 때마다 즉시 건너뛴다.
        blocked_domains = {b.domain for b in session.exec(select(BlockedDomain)).all()}

        # max_entries*30초(개별 크롤링 하드 타임아웃)가 이 함수 전체의 이론상 최대
        # 소요 시간이다 - 기본 20건이면 최악 10분까지 동기 요청 스레드 하나를 붙잡을
        # 수 있어서, 채팅에서 자동으로 트리거되는 가벼운 확인 수집은 max_entries를
        # 낮게(예: 5) 넘겨서 부담을 줄인다 (2026-08-09, 자동수집 기능 추가 후 실사용
        # 중 다른 버튼들이 전부 지연되는 문제를 겪고 나서 추가됨).
        for entry in feed.entries[:max_entries]:
            if job_control.is_cancelled():
                logger.info("[GoogleNewsSearchCollector] 사용자 요청으로 수집 중단")
                break

            title = entry.get("title", "No Title")
            link = entry.get("link", "")
            published = entry.get("published", entry.get("updated", str(datetime.now())))

            if not link:
                continue

            domain, extracted_name = self._extract_source(entry, title)
            if domain in blocked_domains:
                continue  # 사용자가 삭제로 영구 제외한 도메인 - 크롤링 시도조차 안 함

            if fixed_source_name:
                source_name = fixed_source_name
            elif keyword_name:
                # 나중에 이 도메인이 승격되면 Source.name이 "[키워드] 표시이름"
                # 형식이 되므로(scheduler.py _promote_candidate), 승격 전
                # 키워드 광역 검색으로 저장하는 기사도 처음부터 같은 형식으로
                # 맞춰야 건수 집계(/stats/sources)가 어긋나지 않는다.
                source_name = f"[{keyword_name}] {extracted_name}"
            else:
                source_name = extracted_name
                
            # Google 뉴스 RSS의 link는 news.google.com/rss/articles/... 리다이렉트
            # 래퍼 URL이라, 이 상태로 그대로 크롤링하면 실제 기사 대신 구글 자체
            # 안내/동의 화면만 잡혀서 is_crawl_failure()에 전부 걸러진다. googlenewsdecoder로
            # 실제 발행사 URL로 풀어낸 뒤 그 주소로 크롤링/중복확인/저장을 한다.
            resolved_link = self._resolve_real_url(link)

            existing = session.exec(select(Article).where(Article.url == resolved_link)).first()
            if existing:
                continue

            raw_content = crawl_url_sync(resolved_link)
            if is_crawl_failure(raw_content):
                logger.warning(f"[GoogleNewsSearchCollector] 크롤링 실패/본문 추출 불가로 건너뜀: {resolved_link}")
                self._record_blocked_source(session, domain, extracted_name, keyword_name, raw_content)
                continue

            cleaned = clean_article_content(raw_content)

            article = Article(
                title=title,
                url=resolved_link,
                published_at=published,
                content=cleaned,
                raw_content=raw_content,
                source=source_name,
                keyword=keyword_name,
                origin=ContentOrigin.RAW_CRAWL,
            )
            # 저장 시점에 카테고리 미리 계산 (1-1과 동일한 이유)
            from main import _best_category_for_article
            article.category = _best_category_for_article(article)

            session.add(article)
            session.flush()  # commit 전에 article.id를 확보하기 위한 flush

            # 키워드 검색 수집 = 사용자가 검색창에 직접 입력한 키워드에서 나온 결과이므로
            # RSS 고정 수집보다 더 강한 신호로 취급한다(weight=0.5, signal_type="explicit").
            classify_and_store(
                session, article.title, article.content or "",
                source="extension", signal_type="explicit", weight=0.5,
                article_id=article.id,
            )
            new_count += 1

            if track_domains:
                discovered.append((domain, extracted_name))

            # 기사 하나 처리할 때마다 즉시 커밋한다. 예전엔 루프 전체가 끝나야
            # 커밋해서, 크롤링이 진행되는 몇 분 동안 DB 쓰기 트랜잭션이 계속
            # 열려있었다 - 그 사이 다른 요청(키워드 삭제 등)이 락에 걸려
            # "아무것도 안 먹는" 것처럼 보이는 문제의 원인이었다 (2026-08-09).
            session.commit()

        return CollectResult(new_count=new_count, discovered_domains=discovered)

    
    @staticmethod
    def _resolve_real_url(google_news_link: str) -> str:
        """
        news.google.com/rss/articles/... 래퍼 URL을 실제 발행사 URL로 풀어낸다.
        구글이 표준 HTTP 302가 아니라 자체 batchexecute 엔드포인트를 통해서만
        실제 URL을 내려주기 때문에(단순 리다이렉트 추적으로는 안 풀림 - 8/7 세션에서
        확인된 사실), googlenewsdecoder 라이브러리로 서명 파라미터를 추출하고
        구글 내부 API를 호출해서 디코딩한다.
        실패하면(레이트리밋 429 등) 원래 링크를 그대로 반환 - 이후 크롤링 단계에서
        is_crawl_failure()가 걸러줄 것이다.
        """
        try:
            result = gnewsdecoder(google_news_link, interval=1)
            if result.get("status") and result.get("decoded_url"):
                return result["decoded_url"]
            logger.warning(
                f"[GoogleNewsSearchCollector] URL 디코딩 실패: {result.get('message')} "
                f"({google_news_link})"
            )
        except Exception as e:
            logger.warning(f"[GoogleNewsSearchCollector] URL 디코딩 중 예외: {google_news_link} ({e})")
        return google_news_link

    @staticmethod
    def _build_keyword_search_url(keyword: str, months_back: int) -> str:
        after_date = (datetime.now() - timedelta(days=30 * months_back)).strftime("%Y-%m-%d")
        query = f"{keyword} after:{after_date}"
        # hl/gl/ceid을 영어/미국으로 고정 - 한국어 로케일(hl=ko&gl=KR)을 쓰면 구글
        # 뉴스가 한국어 기사를 우선 반환해서, "원문은 영어"를 전제하는 번역 파이프라인
        # (영어 문장 -> 한글 문장 대조)이 깨진다 (원문이 이미 한글이라 번역할 영어가 없음).
        return f"https://news.google.com/rss/search?q={quote(query)}&hl=en-US&gl=US&ceid=US:en"

    @staticmethod
    def _extract_source(entry, title: str) -> tuple[str, str]:
        """
        Google 뉴스 RSS의 <source url="..."> 태그에서 출처를 추출한다.
        feedparser가 이 필드를 못 잡는 경우를 대비해, 제목 끝의
        " - 발행사명" 패턴(Google 뉴스의 관례적 표기)에서도 뽑아내려 시도한다.
        둘 다 실패하면 "unknown"으로 표시하고 후보 추적에서 제외한다.

        주의: entry.link는 news.google.com으로 시작하는 리다이렉트 URL이라
        실제 발행사 도메인이 아니다. 반드시 <source> 태그나 제목에서 추출해야 한다.
        """
        source_field = entry.get("source")
        if source_field:
            href = source_field.get("href") if hasattr(source_field, "get") else None
            name = source_field.get("title") if hasattr(source_field, "get") else None
            if href:
                domain = urlparse(href).netloc.replace("www.", "")
                return domain, name or domain

        if " - " in title:
            name = title.rsplit(" - ", 1)[-1].strip()
            if name:
                return name.lower().replace(" ", ""), name

        return "unknown", "Unknown"

    @staticmethod
    def _record_blocked_source(
        session: Session,
        domain: str,
        extracted_name: str,
        keyword_name: str | None,
        raw_content: str,
    ):
        """
        크롤링이 막혀 기사를 저장하지 못한 도메인을 '블록리스트' 카테고리의
        Source 행으로 기록한다. 실제 재수집 대상 URL이 아니라 기록용이므로
        url은 도메인 기반 고유값(blocked://도메인)을 쓴다.
        """
        if domain == "unknown":
            return

        reason = classify_block_reason(raw_content)
        block_url = f"https://{domain}"
        display_name = f"[{keyword_name}] {extracted_name}" if keyword_name else extracted_name

        existing = session.exec(select(Source).where(Source.url == block_url)).first()
        if existing:
            existing.block_reason = reason
            existing.fail_count += 1
            existing.last_attempt_at = datetime.now()
            session.add(existing)
        else:
            session.add(Source(
                name=display_name,
                url=block_url,
                category="BlockList",
                source_type="blocked",
                origin=SourceOrigin.BLOCKED,
                status=SourceStatus.FAILING,
                fail_count=1,
                block_reason=reason,
                last_attempt_at=datetime.now(),
            ))
        session.commit()


COLLECTOR_REGISTRY: dict[str, BaseCollector] = {
    "rss": RSSCollector(),
    "google_news_search": GoogleNewsSearchCollector(),
    # 향후 추가 예 (새 미디어 타입은 여기 한 줄만 늘어남):
    # "youtube_channel": YouTubeCollector(),
    # "podcast_rss": PodcastCollector(),
    # "image_gallery": ImageGalleryCollector(),
}