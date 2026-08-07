# -*- coding: utf-8 -*-
"""
personalization.py
-------------------
기존 프로젝트의 database.py(engine, get_session)와 models.py(Article,
InteractionSignal)를 그대로 사용하는 개인화 저장/집계 레이어.

main.py에 이렇게 import해서 쓴다:
    from personalization import (
        classify_and_store, store_explicit_feedback,
        get_profile, get_top_interests,
    )
"""

import math
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from models import Article, InteractionSignal
from personalization_taxonomy import (
    SUBCATEGORY_CONFIG,
    best_subcategory_for_text,
    get_top_category,
    is_sensitive,
)

KST = ZoneInfo("Asia/Seoul")
DEFAULT_HALF_LIFE_DAYS = 30.0


def to_kst(dt_utc: datetime) -> str:
    """DB에 UTC로 저장된 시각을 KST ISO 8601 문자열로 변환 (사용자 응답용)."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=ZoneInfo("UTC"))
    return dt_utc.astimezone(KST).isoformat()


def classify_and_store(
    session: Session,
    text_title: str,
    text_content: str = "",
    source: str = "chat",
    signal_type: str = "implicit",
    weight: float = 1.0,
    article_id: int | None = None,
) -> InteractionSignal | None:
    """
    텍스트를 분류해 InteractionSignal로 저장한다.
    매칭되는 서브카테고리가 없으면 저장하지 않고 None을 반환한다 (기존
    _best_category_for_article이 None을 반환하는 경우와 동일한 처리).
    """
    subcat = best_subcategory_for_text(text_title, text_content)
    if subcat is None:
        return None

    top = get_top_category(subcat)
    # 매칭 신뢰도는 우선 고정값(0.6)으로 둔다. 향후 LLM 분류로 교체 시
    # personalization_taxonomy에 confidence 반환 로직을 추가하면 된다.
    signal = InteractionSignal(
        article_id=article_id,
        source=source,
        subcategory=subcat,
        top_category=top,
        signal_type=signal_type,
        confidence=0.6,
        weight=weight,
        raw_snippet=(text_title or "")[:200],
    )
    session.add(signal)
    session.commit()
    session.refresh(signal)
    return signal


def store_explicit_feedback(
    session: Session,
    article_id: int,
    positive: bool,
) -> InteractionSignal | None:
    """
    특정 기사에 대한 명시적 피드백(👍/👎)을 저장한다.
    기사의 title/content로 서브카테고리를 재분류해서, 그 서브카테고리에
    +weight(긍정) 또는 -weight(부정)를 기록한다.
    ⚠️ 부정 신호는 weight를 음수로 저장한다 — get_profile()에서 프로필 점수가
    실제로 깎이도록 하기 위함이다 (해당 카테고리를 계속 추천하면 안 된다는 신호).
    """
    article = session.get(Article, article_id)
    if article is None:
        return None

    subcat = best_subcategory_for_text(article.title, article.content or "")
    if subcat is None:
        return None

    weight = 1.5 if positive else -1.5
    signal = InteractionSignal(
        article_id=article_id,
        source="feedback_explicit",
        subcategory=subcat,
        top_category=get_top_category(subcat),
        signal_type="explicit",
        confidence=0.9,
        weight=weight,
        raw_snippet=article.title[:200],
    )
    session.add(signal)
    session.commit()
    session.refresh(signal)
    return signal


def _decay_factor(created_at_utc: datetime, half_life_days: float) -> float:
    if created_at_utc.tzinfo is None:
        created_at_utc = created_at_utc.replace(tzinfo=ZoneInfo("UTC"))
    now = datetime.now(ZoneInfo("UTC"))
    age_days = (now - created_at_utc).total_seconds() / 86400.0
    return math.pow(0.5, age_days / half_life_days)


def get_profile(session: Session, half_life_days: float = DEFAULT_HALF_LIFE_DAYS) -> dict:
    """
    전체 InteractionSignal을 시간 가중 감쇠와 함께 집계해 서브카테고리별
    프로필 점수를 계산한다. (1단계는 멀티유저가 아니므로 user_id 필터 없음)

    반환: {subcategory: {"score": float, "n_signals": int, "top_category": str,
                          "sensitive": bool, "last_signal_kst": str}}
    """
    rows = session.exec(select(InteractionSignal)).all()

    profile: dict = {}
    latest_created_at: dict = {}  # subcategory -> datetime(UTC), 내부 계산용
    for row in rows:
        decay = _decay_factor(row.created_at, half_life_days)
        contribution = row.confidence * row.weight * decay
        entry = profile.setdefault(
            row.subcategory,
            {"score": 0.0, "n_signals": 0, "top_category": row.top_category,
             "sensitive": is_sensitive(row.subcategory)},
        )
        entry["score"] += contribution
        entry["n_signals"] += 1

        prev = latest_created_at.get(row.subcategory)
        if prev is None or row.created_at > prev:
            latest_created_at[row.subcategory] = row.created_at

    for subcat, dt in latest_created_at.items():
        profile[subcat]["last_signal_kst"] = to_kst(dt)

    return dict(sorted(profile.items(), key=lambda kv: kv[1]["score"], reverse=True))


def get_top_interests(session: Session, n: int = 5, half_life_days: float = DEFAULT_HALF_LIFE_DAYS):
    """답변 생성 시 시스템 프롬프트에 주입할 상위 관심사 n개."""
    profile = get_profile(session, half_life_days=half_life_days)
    return list(profile.items())[:n]
