"""
generators/text/retrieval.py
벡터DB 없이 Article/Keyword 테이블만으로 "질문과 관련 있어 보이는 최근 기사"를
찾는 경량 검색. 추후 LanceDB 임베딩 검색으로 교체할 때도
get_context_articles(query, ...) 시그니처만 유지하면 호출부는 안 바뀐다.
"""
from datetime import datetime, timedelta
import logging

from sqlmodel import Session, select
from models import Article, Keyword
import model_router

logger = logging.getLogger(__name__)

def _extract_keywords(query: str, session: Session) -> list[str]:
    """1차: 등록된 Keyword.name 중 질문에 그대로 포함된 것만 골라낸다 (부분 문자열
    매칭, 빠르고 비용 없음). 언어가 다르거나(예: 한국어 질문 vs 영어 등록 키워드)
    표현이 다르면 실패한다 - 그 경우는 _match_keyword_semantically()가 보완한다."""
    all_keywords = session.exec(select(Keyword.name)).all()
    return [kw for kw in all_keywords if kw.lower() in query.lower()]


def _match_keyword_semantically(query: str, session: Session) -> list[str]:
    """
    2차: 1차(부분 문자열) 매칭이 실패했을 때만 호출된다.
    2026-08-09 실사용 중 발견된 문제: "Open source code star"로 키워드를
    등록해 기사를 6건 수집해뒀는데, "최근 오픈소스 스타수가 많은 것들을
    소개해줘"(한국어)라고 물으면 영어 키워드 문자열이 질문 안에 그대로
    없으니 1차 매칭이 무조건 실패했다. LIGHT 모델에게 의미상 관련 키워드를
    고르게 해서 이 언어/표현 차이를 보완한다.

    매칭은 "LLM 출력 줄과 키워드가 정확히 같은가"가 아니라 "키워드 문자열이
    LLM 출력 줄에 포함돼 있는가"로 판정한다 - LLM이 "다른 설명 붙이지 말라"는
    지시를 완벽히 안 지키고 "관련 키워드: X"처럼 군더더기를 붙이는 경우가
    실사용에서 흔하기 때문에, 완전 일치보다 포함 여부가 훨씬 안정적이다.
    """
    all_keywords = session.exec(select(Keyword.name)).all()
    if not all_keywords:
        return []

    keyword_list = list(all_keywords)[:50]
    prompt = (
        "다음은 등록된 검색 키워드 목록입니다:\n"
        + "\n".join(f"- {k}" for k in keyword_list)
        + f"\n\n사용자 질문: \"{query}\"\n\n"
        "이 질문과 의미상 관련 있는 키워드를 목록에 있는 이름 그대로, 한 줄에 "
        "하나씩만 출력하세요 (여러 개 가능). 관련 있는 게 하나도 없으면 NONE만 "
        "출력하세요. 다른 설명이나 부연은 절대 붙이지 마세요."
    )
    try:
        raw = model_router.chat("classify", [{"role": "user", "content": prompt}])
    except Exception as e:
        logger.warning(f"[retrieval] 의미 기반 키워드 매칭 LLM 호출 실패: {e}")
        return []

    logger.info(f"[retrieval] 의미 기반 키워드 매칭 - 질문: '{query}' / LLM 원본 출력: {raw!r}")

    raw_lower = raw.lower()
    picked = [k for k in keyword_list if k.lower() in raw_lower]

    logger.info(f"[retrieval] 의미 기반 키워드 매칭 - 최종 선택: {picked}")
    return picked


def get_context_articles(
    query: str,
    session: Session,
    top_interest_categories: list[str] | None = None,
    limit: int = 8,
    recent_days: int = 14,
) -> tuple[list[Article], bool]:
    """
    우선순위: (1) 질문에 등장한 등록 키워드와 일치하는 최근 기사 (부분 문자열 →
                 실패 시 의미 기반 매칭으로 보완)
             (2) 없으면 개인화 프로필 상위 관심 카테고리 + 최신순
             (3) 그마저 없으면 그냥 최신 기사로 채움

    반환: (articles, matched)
    matched=True  -> 질문과 실제로 연관된 근거(키워드/카테고리 매칭)를 찾음
    matched=False -> 아무 연관성도 못 찾아 그냥 최신 기사로 채운 것
                      (호출부가 이걸로 "근거 부족" 상황을 판정한다)
    """
    cutoff = datetime.utcnow() - timedelta(days=recent_days)
    matched_keywords = _extract_keywords(query, session)

    if not matched_keywords:
        matched_keywords = _match_keyword_semantically(query, session)

    stmt = select(Article).where(Article.collected_at >= cutoff)
    matched = False
    if matched_keywords:
        stmt = stmt.where(Article.keyword.in_(matched_keywords))
        matched = True
    elif top_interest_categories:
        stmt = stmt.where(Article.category.in_(top_interest_categories))
        matched = True

    stmt = stmt.order_by(Article.collected_at.desc()).limit(limit)
    articles = session.exec(stmt).all()

    if not articles:
        matched = False
        stmt = select(Article).order_by(Article.collected_at.desc()).limit(limit)
        articles = session.exec(stmt).all()

    return articles, matched