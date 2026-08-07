"""
scheduler.py
Source/Keyword 기반 수집 스케줄링.

기존 main.py의 APScheduler IntervalTrigger(hours=3) 고정 방식을 대체한다.
run_tick()이 짧은 간격(SchedulerConfig.tick_minutes)으로 반복 실행되며,
Source/Keyword 각각의 interval_hours가 지났는지 개별 판단해서 수집을 트리거한다.
이렇게 해야 소스마다(수동 등록/자동 승격 모두) 서로 다른 수집 주기를 가질 수 있다.
"""

import logging
from datetime import datetime, timedelta
from urllib.parse import quote

from sqlmodel import Session, select

from database import engine
from models import (
    Source, Keyword, CandidateSource, SchedulerConfig,
    SourceOrigin, SourceStatus, CandidateStatus,
)
from collectors import COLLECTOR_REGISTRY
import activity_tracker

logger = logging.getLogger(__name__)

FAIL_THRESHOLD = 3      # 연속 실패 임계값 - 넘으면 status=FAILING (삭제는 사용자 판단)
PROMOTE_THRESHOLD = 3   # CandidateSource.hit_count 임계값 - 넘으면 Source로 자동 승격


def seed_manual_sources(target_sources: list[dict]):
    """
    앱 최초 기동 시, 기존 TARGET_SOURCES 하드코딩 리스트를 Source 테이블로 옮긴다.
    이미 같은 url이 Source 테이블에 있으면 건너뛰므로, 재기동해도 중복 생성되지 않는다.
    main.py의 lifespan()에서 한 번 호출하면 된다.
    """
    with Session(engine) as session:
        added = 0
        for item in target_sources:
            existing = session.exec(select(Source).where(Source.url == item["url"])).first()
            if existing:
                continue

            category = None
            name = item["name"]
            if name.startswith("[") and "]" in name:
                category = name[1:name.index("]")]

            session.add(Source(
                name=name,
                url=item["url"],
                category=category,
                source_type="rss",
                origin=SourceOrigin.MANUAL,
                interval_hours=3.0,
            ))
            added += 1
        session.commit()
    logger.info(f"[scheduler] TARGET_SOURCES 시딩 완료 (신규 {added}건, 나머지는 이미 존재)")


def get_or_create_config() -> SchedulerConfig:
    """SchedulerConfig는 항상 1행만 존재하는 싱글턴. 없으면 기본값으로 생성."""
    with Session(engine) as session:
        config = session.exec(select(SchedulerConfig)).first()
        if config is None:
            config = SchedulerConfig(tick_minutes=30)
            session.add(config)
            session.commit()
            session.refresh(config)
        return config


def _due(last_attempt: datetime | None, interval_hours: float) -> bool:
    if last_attempt is None:
        return True
    return datetime.now() - last_attempt >= timedelta(hours=interval_hours)


def run_tick() -> dict:
    """스케줄러의 매 틱마다 호출된다. Source와 Keyword를 각각 점검하고 결과를 요약해서 반환한다."""
    logger.info("[scheduler] tick 시작")
    source_stats = _tick_sources()
    keyword_stats = _tick_keywords()
    logger.info("[scheduler] tick 완료")
    return {
        "sources_checked": source_stats["checked"],
        "sources_new_articles": source_stats["new_articles"],
        "keywords_checked": keyword_stats["checked"],
        "keywords_new_articles": keyword_stats["new_articles"],
    }


def _tick_sources() -> dict:
    checked = 0
    new_articles = 0

    with Session(engine) as session:
        sources = session.exec(
            select(Source).where(Source.status == SourceStatus.ACTIVE)
        ).all()

        for source in sources:
            if not _due(source.last_attempt_at, source.interval_hours):
                continue

            collector = COLLECTOR_REGISTRY.get(source.source_type)
            if not collector:
                logger.warning(f"[scheduler] 알 수 없는 source_type: {source.source_type} ({source.name})")
                continue

            checked += 1
            source.last_attempt_at = datetime.now()
            with activity_tracker.track_component("수집기 · 소스/키워드", f"소스 점검 중: {source.name}"):
                try:
                    result = collector.collect(source, session)
                    new_articles += result.new_count
                    source.fail_count = 0
                    source.status = SourceStatus.ACTIVE
                    source.last_success_at = datetime.now()
                    logger.info(f"[scheduler] {source.name}: 신규 {result.new_count}건")
                except Exception as e:
                    source.fail_count += 1
                    if source.fail_count >= FAIL_THRESHOLD:
                        source.status = SourceStatus.FAILING
                        logger.warning(f"[scheduler] {source.name}: {FAIL_THRESHOLD}회 연속 실패 -> FAILING 표시")
                    else:
                        logger.warning(f"[scheduler] {source.name} 수집 실패({source.fail_count}회): {e}")

            session.add(source)
            session.commit()

    return {"checked": checked, "new_articles": new_articles}


def _tick_keywords() -> dict:
    checked = 0
    new_articles = 0

    with Session(engine) as session:
        keywords = session.exec(select(Keyword)).all()
        collector = COLLECTOR_REGISTRY["google_news_search"]

        for kw in keywords:
            if not _due(kw.last_collected_at, kw.interval_hours):
                continue

            checked += 1
            with activity_tracker.track_component("수집기 · 소스/키워드", f"키워드 수집 중: {kw.name}"):
                try:
                    result = collector.collect_for_keyword(kw, session)
                    new_articles += result.new_count
                    kw.last_collected_at = datetime.now()
                    session.add(kw)
                    session.commit()
                    logger.info(f"[scheduler] 키워드 '{kw.name}': 신규 {result.new_count}건")

                    _track_candidates(session, kw, result.discovered_domains)
                except Exception as e:
                    logger.warning(f"[scheduler] 키워드 '{kw.name}' 수집 실패: {e}")

    return {"checked": checked, "new_articles": new_articles}


def _track_candidates(session: Session, keyword: Keyword, discovered: list[tuple[str, str]]):
    """
    이번 수집에서 발견된 출처(도메인)별로 CandidateSource.hit_count를 올리고,
    임계값(PROMOTE_THRESHOLD)을 넘으면 Source 테이블로 자동 승격한다.
    """
    seen_this_run = set()

    for domain, source_name in discovered:
        if domain in seen_this_run or domain == "unknown":
            continue
        seen_this_run.add(domain)

        candidate = session.exec(
            select(CandidateSource).where(
                CandidateSource.keyword_id == keyword.id,
                CandidateSource.domain == domain,
            )
        ).first()

        if candidate is None:
            session.add(CandidateSource(
                keyword_id=keyword.id,
                domain=domain,
                source_name=source_name,
                hit_count=1,
            ))
            session.commit()
            continue

        if candidate.status != CandidateStatus.CANDIDATE:
            continue  # 이미 승격됐거나 사용자가 제외 처리한 후보는 더 안 올림

        candidate.hit_count += 1
        candidate.updated_at = datetime.now()

        if candidate.hit_count >= PROMOTE_THRESHOLD:
            candidate.status = CandidateStatus.PROMOTED
            _promote_candidate(session, keyword, candidate)

        session.add(candidate)
        session.commit()


def _promote_candidate(session: Session, keyword: Keyword, candidate: CandidateSource):
    """후보 출처를 Source 테이블에 고정 소스로 승격한다 (origin=auto_promoted)."""
    # collectors.py의 _build_keyword_search_url()과 동일한 이유로 영어/미국 로케일 사용
    # (한국어 로케일이면 승격된 소스도 한국어 기사를 계속 수집하게 됨).
    query_url = (
        f"https://news.google.com/rss/search?q={quote(keyword.name)}+site:{candidate.domain}"
        f"&hl=en-US&gl=US&ceid=US:en"
    )

    existing = session.exec(select(Source).where(Source.url == query_url)).first()
    if existing:
        return

    session.add(Source(
        name=f"[{keyword.name}] {candidate.source_name}",
        url=query_url,
        category=keyword.name,
        source_type="google_news_search",
        origin=SourceOrigin.AUTO_PROMOTED,
        interval_hours=keyword.interval_hours,
        keyword_id=keyword.id,
    ))
    session.commit()
    logger.info(f"[scheduler] 승격: {candidate.source_name} ({candidate.domain}) -> Source 등록 완료")
