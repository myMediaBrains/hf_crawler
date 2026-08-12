"""
tagging.py
분류 체계 통합(2026-08-09)의 핵심 로직. Tag/TagKeyword/TagBlacklist/ArticleTag를
다루는 공용 함수 모음.

main.py와 collectors.py 양쪽에서 공용으로 쓰기 위해 별도 모듈로 분리했다 -
기존에 collectors.py가 "from main import _best_category_for_article"로
지연 import(함수 호출 시점에만 import)하던 방식의 근본적인 해결책이다.
이 모듈은 models.py만 참조하므로 main.py/collectors.py 어느 쪽도 이 모듈을
import할 때 순환참조가 생기지 않는다.

핵심 원칙:
- Tag는 빈 상태로 시작한다. 하드코딩 시딩 데이터 없음 (2026-08-09 사용자 결정 -
  기존 CATEGORY_CONFIG/SUBCATEGORY_CONFIG/TAXONOMY 3개 딕셔너리는 시딩 데이터로도
  이관하지 않고 완전히 폐기). 장르편집기(사람)와 채팅 자동수집(LLM)을 통해서만
  천천히 채워진다.
- 매칭 점수제는 기존 _score_categories_for_article()과 동일한 방식(제목 가중치
  3배, 본문 1배, 블랙리스트 걸리면 제외)을 그대로 재사용한다 - 검증된 로직을
  버릴 이유가 없다.
- **1등만 취하지 않는다**: 기존 _best_category_for_article()은 모든 카테고리
  점수를 계산해놓고 max()로 1등만 취하고 나머지를 버렸다. 이게 다중 태그가
  필요했던 이유이자 지금까지 계산 낭비였다 - 이제 임계값 넘는 태그를 전부 채택한다.
"""

import re
import logging
from sqlmodel import Session, select
from sqlmodel import func as sql_func

from models import Tag, TagKeyword, TagBlacklist, ArticleTag

logger = logging.getLogger(__name__)

# 임계값 이상인 태그는 전부 채택(다중 태그 부여). 실측하며 조정 가능.
DEFAULT_TAG_SCORE_THRESHOLD = 3

_HANGUL_PATTERN = re.compile(r'[\uac00-\ud7a3]')


def _contains_hangul(text: str) -> bool:
    return bool(_HANGUL_PATTERN.search(text or ""))


def _clean_llm_short_output(raw: str) -> str:
    """
    LLM이 "짧은 단어 하나만 출력해"라고 지시받아도 가끔 목록 기호(-, *, •)나
    번호(1. 2.)를 붙여서 응답하는 경우가 있다 (2026-08-10 발견 - "반도체" ->
    "-Semiconductor"로 저장되던 버그의 원인). 따옴표 제거만으로는 안 잡히므로
    이 패턴들도 함께 벗겨낸다. translate_keyword_en/ko 등 "짧은 결과 하나"를
    기대하는 모든 LLM 호출 후처리에 공용으로 쓴다.
    """
    text = raw.strip().strip('"').strip("'").strip()
    text = re.sub(r'^[\-\*•]+\s*', '', text)      # 목록 기호 제거
    text = re.sub(r'^\d+[\.\)]\s*', '', text)      # "1. " "2) " 같은 번호 매기기 제거
    return text.strip()


def _translate_to_english_tag_name(korean_text: str) -> str:
    """
    한글 태그 이름을 짧은 영어로 압축 번역한다. model_router는 여기서 지연
    import한다 - tagging.py는 대부분 순수 DB 로직이라 평소엔 이 무거운
    의존성이 필요 없고, 한글 이름이 실제로 들어올 때만(드문 경로) 필요하기
    때문이다. 실패하면 원문을 그대로 반환.
    """
    import model_router
    prompt = (
        f"다음 한글 단어나 문구를 짧은 영어 태그 이름(1~3단어)으로 번역해줘. "
        f"번역 결과만 출력하고 다른 설명은 붙이지 마: \"{korean_text}\""
    )
    try:
        raw = model_router.chat("translate_keyword_en", [{"role": "user", "content": prompt}])
        translated = _clean_llm_short_output(raw)
        return translated if translated else korean_text
    except Exception as e:
        logger.warning(f"[tagging] 한글 태그명 영어 변환 실패, 원문 유지: {e}")
        return korean_text


def get_or_create_tag(
    session: Session,
    name: str,
    major_category: str,
    mid_category: str | None = None,
    label_ko: str | None = None,
    sensitive: bool = False,
) -> Tag:
    """
    이름으로 태그를 찾고 없으면 새로 만든다. 새로 만들 때는 태그 이름 자체를
    첫 TagKeyword로 자동 등록해서, 매칭 키워드가 하나도 없어 전혀 안 걸리는
    상황을 방지한다 (사람/LLM이 이후 동의어를 더 추가할 수 있음).

    2026-08-09: 이름/대분류에 한글이 섞여 있으면 자동으로 영어로 번역한다.

    2026-08-10 (1차 수정): 기존 태그를 찾으면 major/mid_category를 갱신하도록
    변경했었는데, 이러면 "소분류 텍스트는 같지만 중분류가 다른" 별개 항목을
    새로 만들려 할 때 기존 걸 덮어써버리는 문제가 있었다 (예: Tech>Chips가
    이미 있는데 Food>Chips를 새로 만들려 하면, 기존 Tech>Chips가 Food로
    바뀌어버림 - 사용자가 "입력이 안 된다"고 느낀 원인).

    2026-08-10 (2차 수정, 이번 건): 이름은 같아도 중분류가 다르면 완전히
    별개의 Tag로 분리한다. 내부 식별자(Tag.name, DB 유일해야 함)만 구분을
    위해 살짝 다르게("Chips (Food)") 만들고, 사람이 실제로 보고 입력한 깨끗한
    텍스트("Chips")는 label_ko 필드에 그대로 보존해서 화면 표시/검색어 조합에
    항상 그 값을 쓴다 - 기술적 구분과 사용자 표시를 분리한 것.
    """
    if _contains_hangul(name):
        name = _translate_to_english_tag_name(name)
    if major_category and _contains_hangul(major_category):
        major_category = _translate_to_english_tag_name(major_category)
    if mid_category and _contains_hangul(mid_category):
        mid_category = _translate_to_english_tag_name(mid_category)

    clean_name = name  # 사람이 실제로 입력한 원본 - 화면 표시/검색어 조합에 항상 이걸 씀

    # 2026-08-10: 대소문자 무시 매칭 - GitHub 토픽은 소문자("ai")로 오는데
    # 뉴스 태그는 대문자("AI")로 등록돼 있어서, 정확히 같은 문자열만 매칭하면
    # 서로 다른 태그로 쪼개져 "뉴스와 GitHub가 태그를 공유한다"는 목표가
    # 깨지는 문제가 있었다.
    existing = session.exec(
        select(Tag).where(sql_func.lower(Tag.name) == name.lower())
    ).first()

    if existing and mid_category and existing.mid_category and existing.mid_category != mid_category:
        # 이름은 같지만 중분류가 다른 별개 주제 - 기술적 이름만 구분해서 새로 만든다.
        disambiguated_name = f"{name} ({mid_category})"
        existing2 = session.exec(select(Tag).where(Tag.name == disambiguated_name)).first()
        if existing2:
            return existing2
        tag = Tag(
            name=disambiguated_name,
            major_category=major_category,
            mid_category=mid_category,
            label_ko=clean_name,  # 화면엔 항상 이 값을 보여줌
            sensitive=sensitive,
        )
        session.add(tag)
        session.commit()
        session.refresh(tag)
        # 검색 매칭용 TagKeyword는 사람이 실제로 보는 깨끗한 이름 그대로 등록
        session.add(TagKeyword(tag_id=tag.id, term=clean_name))
        session.commit()
        logger.info(f"[tagging] 중분류 충돌로 별도 태그 생성: '{disambiguated_name}' (표시명: '{clean_name}')")
        return tag

    if existing:
        changed = False
        if major_category and existing.major_category != major_category:
            existing.major_category = major_category
            changed = True
        if mid_category and existing.mid_category != mid_category:
            existing.mid_category = mid_category
            changed = True
        if changed:
            session.add(existing)
            session.commit()
            session.refresh(existing)
            logger.info(f"[tagging] 기존 태그 분류 갱신: '{existing.name}' ({existing.major_category} > {existing.mid_category})")
        return existing

    tag = Tag(
        name=name,
        major_category=major_category,
        mid_category=mid_category,
        label_ko=label_ko or clean_name,
        sensitive=sensitive,
    )
    session.add(tag)
    session.commit()
    session.refresh(tag)

    session.add(TagKeyword(tag_id=tag.id, term=name))
    session.commit()

    logger.info(f"[tagging] 신규 태그 생성: '{name}' ({major_category})")
    return tag

def _compile_all_patterns(session: Session) -> dict[int, list[re.Pattern]]:
    """
    tag_id -> 컴파일된 정규식 패턴 리스트. 대량 처리(예: 여러 기사를 한 번에
    태깅) 시에는 호출부에서 한 번만 로드해서 재사용하는 걸 강력 권장한다
    (기존 _COMPILED_CATEGORY_PATTERNS와 동일한 이유 - 3,600건 기준 실측
    116초가 걸렸던 성능 사고의 재발 방지).
    """
    patterns: dict[int, list[re.Pattern]] = {}
    rows = session.exec(select(TagKeyword)).all()
    for row in rows:
        pattern = re.compile(
            r'(?:^|\b|[^\w])' + re.escape(row.term) + r'(?:$|\b|[^\w])',
            re.IGNORECASE,
        )
        patterns.setdefault(row.tag_id, []).append(pattern)
    return patterns


def _blacklist_by_tag(session: Session) -> dict[int, list[str]]:
    blacklist: dict[int, list[str]] = {}
    rows = session.exec(select(TagBlacklist)).all()
    for row in rows:
        blacklist.setdefault(row.tag_id, []).append(row.term.lower())
    return blacklist


def score_tags_for_text(
    session: Session,
    title: str,
    content: str,
    compiled_patterns: dict[int, list[re.Pattern]] | None = None,
    blacklist: dict[int, list[str]] | None = None,
) -> dict[int, float]:
    """
    제목/본문 텍스트에 대해 태그별 매칭 점수를 계산한다 (기존
    _score_categories_for_article()과 동일한 점수제: 제목 3배, 본문 1배).
    compiled_patterns/blacklist를 안 넘기면 이번 호출에서 즉석 로드한다.

    반환: {tag_id: score}
    """
    if compiled_patterns is None:
        compiled_patterns = _compile_all_patterns(session)
    if blacklist is None:
        blacklist = _blacklist_by_tag(session)

    title_lower = (title or "").lower()
    content_lower = (content or "").lower()

    scores: dict[int, float] = {}
    for tag_id, patterns in compiled_patterns.items():
        bad_terms = blacklist.get(tag_id, [])
        if any(bad in title_lower or bad in content_lower for bad in bad_terms):
            continue

        score = 0
        for pattern in patterns:
            score += len(pattern.findall(title_lower)) * 3
            score += len(pattern.findall(content_lower)) * 1

        if score > 0:
            scores[tag_id] = score

    return scores


def tags_above_threshold(
    session: Session,
    title: str,
    content: str,
    threshold: int = DEFAULT_TAG_SCORE_THRESHOLD,
    compiled_patterns: dict[int, list[re.Pattern]] | None = None,
    blacklist: dict[int, list[str]] | None = None,
) -> dict[int, float]:
    """다중 태그 부여용 - 임계값 넘는 태그를 전부 반환 (1등만 취하지 않음)."""
    scores = score_tags_for_text(session, title, content, compiled_patterns, blacklist)
    return {tag_id: score for tag_id, score in scores.items() if score >= threshold}


def assign_tags_to_article(
    session: Session,
    article_id: int,
    title: str,
    content: str,
    threshold: int = DEFAULT_TAG_SCORE_THRESHOLD,
    compiled_patterns: dict[int, list[re.Pattern]] | None = None,
    blacklist: dict[int, list[str]] | None = None,
) -> list[ArticleTag]:
    """기사 하나에 임계값 넘는 태그를 전부 ArticleTag로 저장한다."""
    matched = tags_above_threshold(session, title, content, threshold, compiled_patterns, blacklist)
    created = []
    for tag_id, score in matched.items():
        at = ArticleTag(article_id=article_id, tag_id=tag_id, score=score)
        session.add(at)
        created.append(at)
    if created:
        session.commit()
    return created

def display_name_for_tag(tag: Tag) -> str:
    """
    화면 표시용 태그 이름. 소분류 중복 방지로 내부 이름이 구분된 태그(label_ko가
    Tag.name과 다름)는 "중분류(소분류)" 형식으로 보여주고, 아니면 원래 이름 그대로
    보여준다 (2026-08-10). 예: Travel 아래 Worldwide가 다른 대분류의 Worldwide와
    겹쳐서 내부적으로 "Worldwide (Travel)"로 구분됐다면 -> 화면엔 "Travel(Worldwide)".
    """
    clean = tag.label_ko or tag.name
    if tag.name != clean and tag.mid_category:
        return f"{tag.mid_category}({clean})"
    return clean