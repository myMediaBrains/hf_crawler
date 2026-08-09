# -*- coding: utf-8 -*-
"""
personalization.py
-------------------
기존 프로젝트의 database.py(engine, get_session)와 models.py(Article,
InteractionSignal, Tag, User, TextGeneration)를 그대로 사용하는 개인화
저장/집계 레이어.

2026-08-09 대개편: personalization_taxonomy.py(SUBCATEGORY_CONFIG 기반)를
더 이상 참조하지 않는다. 분류는 이제 tagging.py(Tag/TagKeyword 기반)가
전담한다 - main.py/collectors.py와 동일한 분류 원천을 쓰게 되어, 예전에
CATEGORY_CONFIG/SUBCATEGORY_CONFIG가 서로 다른 답을 낼 수 있었던 문제가
구조적으로 사라진다.

main.py에 이렇게 import해서 쓴다:
    from personalization import (
        classify_and_store, store_explicit_feedback,
        get_profile, get_top_interests,
        to_kst, register_user_and_backfill,
    )
"""

import math
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from models import Article, InteractionSignal, User, TextGeneration, Tag
import tagging

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
    user_id: str | None = None,
) -> InteractionSignal | None:
    """
    텍스트를 분류해 InteractionSignal로 저장한다. 2026-08-09: subcategory
    문자열 대신 Tag(tag_id)를 참조한다. tagging.score_tags_for_text()는
    다중 태그 점수를 전부 반환하지만, 신호 하나는 "주된 관심사 하나"로
    기록하는 게 프로필 집계에 더 적합하므로 여기서는 최고 점수 태그 하나만
    채택한다 (다중 채택은 ArticleTag에서만 의미가 있음).

    매칭되는 태그가 하나도 없으면(예: 아직 Tag가 비어있는 초기 상태) 저장하지
    않고 None을 반환한다.
    """
    scores = tagging.score_tags_for_text(session, text_title, text_content)
    if not scores:
        return None

    best_tag_id = max(scores, key=scores.get)
    tag = session.get(Tag, best_tag_id)
    if tag is None:
        return None

    signal = InteractionSignal(
        user_id=user_id,
        article_id=article_id,
        source=source,
        tag_id=tag.id,
        major_category=tag.major_category,
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
    user_id: str | None = None,
) -> InteractionSignal | None:
    """
    특정 기사에 대한 명시적 피드백(👍/👎)을 저장한다. 기사의 title/content로
    태그를 재분류해서, 최고 점수 태그에 +weight(긍정) 또는 -weight(부정)를
    기록한다.
    ⚠️ 부정 신호는 weight를 음수로 저장한다 — get_profile()에서 프로필 점수가
    실제로 깎이도록 하기 위함이다 (해당 주제를 계속 추천하면 안 된다는 신호).
    """
    article = session.get(Article, article_id)
    if article is None:
        return None

    scores = tagging.score_tags_for_text(session, article.title, article.content or "")
    if not scores:
        return None

    best_tag_id = max(scores, key=scores.get)
    tag = session.get(Tag, best_tag_id)
    if tag is None:
        return None

    weight = 1.5 if positive else -1.5
    signal = InteractionSignal(
        user_id=user_id,
        article_id=article_id,
        source="feedback_explicit",
        tag_id=tag.id,
        major_category=tag.major_category,
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


def get_profile(
    session: Session,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    user_id: str | None = None,
) -> dict:
    """
    InteractionSignal을 시간 가중 감쇠와 함께 집계해 태그별 프로필 점수를
    계산한다. 2026-08-09: subcategory(문자열) 대신 tag_id로 그룹핑하고,
    결과 딕셔너리의 키는 Tag.name(사람이 읽을 수 있는 이름)을 쓴다.
    user_id를 넘기면 그 사용자의 신호만 집계하고, 넘기지 않으면(None)
    하위호환을 위해 전체를 집계한다.

    반환: {tag_name: {"score": float, "n_signals": int, "major_category": str,
                       "sensitive": bool, "last_signal_kst": str}}
    """
    stmt = select(InteractionSignal)
    if user_id is not None:
        stmt = stmt.where(InteractionSignal.user_id == user_id)
    rows = session.exec(stmt).all()

    profile: dict = {}
    latest_created_at: dict = {}  # tag_name -> datetime(UTC), 내부 계산용
    tag_cache: dict[int, Tag] = {}

    for row in rows:
        tag = tag_cache.get(row.tag_id)
        if tag is None:
            tag = session.get(Tag, row.tag_id)
            if tag is None:
                continue  # 태그가 이후 삭제된 경우 등 - 건너뜀
            tag_cache[row.tag_id] = tag

        decay = _decay_factor(row.created_at, half_life_days)
        contribution = row.confidence * row.weight * decay
        entry = profile.setdefault(
            tag.name,
            {"score": 0.0, "n_signals": 0, "major_category": row.major_category,
             "sensitive": tag.sensitive},
        )
        entry["score"] += contribution
        entry["n_signals"] += 1

        prev = latest_created_at.get(tag.name)
        if prev is None or row.created_at > prev:
            latest_created_at[tag.name] = row.created_at

    for tag_name, dt in latest_created_at.items():
        profile[tag_name]["last_signal_kst"] = to_kst(dt)

    return dict(sorted(profile.items(), key=lambda kv: kv[1]["score"], reverse=True))


def get_top_interests(
    session: Session,
    n: int = 5,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    user_id: str | None = None,
):
    """답변 생성 시 시스템 프롬프트에 주입할 상위 관심사 n개."""
    profile = get_profile(session, half_life_days=half_life_days, user_id=user_id)
    return list(profile.items())[:n]


def register_user_and_backfill(
    session: Session,
    user_id: str,
    display_name: str | None = None,
) -> dict:
    """
    새 사용자를 등록하고, 그동안 user_id가 비어있던(등록 전에 쌓인)
    InteractionSignal/TextGeneration 행을 전부 이 사용자에게 일괄 귀속시킨다.
    이미 등록된 user_id면 ValueError를 raise한다 (호출부 main.py에서 409로 변환).
    """
    existing = session.exec(select(User).where(User.user_id == user_id)).first()
    if existing is not None:
        raise ValueError(f"이미 등록된 사용자 ID입니다: {user_id}")

    user = User(user_id=user_id, display_name=display_name)
    session.add(user)

    orphan_signals = session.exec(
        select(InteractionSignal).where(InteractionSignal.user_id.is_(None))
    ).all()
    for s in orphan_signals:
        s.user_id = user_id
        session.add(s)

    orphan_generations = session.exec(
        select(TextGeneration).where(TextGeneration.user_id.is_(None))
    ).all()
    for g in orphan_generations:
        g.user_id = user_id
        session.add(g)

    session.commit()
    session.refresh(user)

    return {
        "user_id": user.user_id,
        "display_name": user.display_name,
        "backfilled_signals": len(orphan_signals),
        "backfilled_generations": len(orphan_generations),
        "created_at_kst": to_kst(user.created_at),
    }

def store_tag_preference(
    session: Session,
    tag_id: int,
    user_id: str | None = None,
    weight: float = 2.0,
) -> InteractionSignal:
    """
    '선호 장르 선택'에서 사용자가 직접 체크한 태그의 명시적 선호 신호.
    이미 태그가 정해진 상태(사용자가 직접 골랐음)라 텍스트 재분류가 필요
    없다 - classify_and_store()와 달리 곧바로 InteractionSignal을 만든다.
    weight=2.0은 기존 배송 클릭(2.5)보다 살짝 낮고 장문 확장 클릭(1.5)보다
    높은 수준 - "직접 선언한 선호"이니 채팅 신호들보다는 강하게 잡는다.
    """
    tag = session.get(Tag, tag_id)
    if tag is None:
        raise ValueError(f"존재하지 않는 태그입니다: {tag_id}")

    signal = InteractionSignal(
        user_id=user_id,
        article_id=None,
        source="preference_explicit",
        tag_id=tag.id,
        major_category=tag.major_category,
        signal_type="explicit",
        confidence=1.0,
        weight=weight,
        raw_snippet=f"선호 장르 선택: {tag.name}",
    )
    session.add(signal)
    session.commit()
    session.refresh(signal)
    return signal
