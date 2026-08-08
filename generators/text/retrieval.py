"""
generators/text/retrieval.py
벡터DB 없이 Article/Keyword 테이블만으로 "질문과 관련 있어 보이는 최근 기사"를
찾는 경량 검색. 추후 LanceDB 임베딩 검색으로 교체할 때도
get_context_articles(query, ...) 시그니처만 유지하면 호출부는 안 바뀐다.
"""
from datetime import datetime, timedelta
from sqlmodel import Session, select
from models import Article, Keyword


def _extract_keywords(query: str, session: Session) -> list[str]:
    """등록된 Keyword.name 중 질문에 포함된 것만 골라낸다 (부분 문자열 매칭, 1차 버전)."""
    all_keywords = session.exec(select(Keyword.name)).all()
    return [kw for kw in all_keywords if kw.lower() in query.lower()]


def get_context_articles(
    query: str,
    session: Session,
    top_interest_categories: list[str] | None = None,
    limit: int = 8,
    recent_days: int = 14,
) -> list[Article]:
    """
    우선순위: (1) 질문에 등장한 등록 키워드와 일치하는 최근 기사
             (2) 없으면 개인화 프로필 상위 관심 카테고리 + 최신순
             (3) 그마저 없으면 그냥 최신 기사로 채움
    """
    cutoff = datetime.utcnow() - timedelta(days=recent_days)
    matched_keywords = _extract_keywords(query, session)

    stmt = select(Article).where(Article.collected_at >= cutoff)
    if matched_keywords:
        stmt = stmt.where(Article.keyword.in_(matched_keywords))
    elif top_interest_categories:
        stmt = stmt.where(Article.category.in_(top_interest_categories))

    stmt = stmt.order_by(Article.collected_at.desc()).limit(limit)
    articles = session.exec(stmt).all()

    if not articles:
        stmt = select(Article).order_by(Article.collected_at.desc()).limit(limit)
        articles = session.exec(stmt).all()
    return articles