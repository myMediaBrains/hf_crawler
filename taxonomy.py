# -*- coding: utf-8 -*-
"""
taxonomy.py
------------
대분류(장르) > 중분류 키워드 계층을 정의하고, 앱 기동 시 Keyword 테이블에
자동으로 시딩한다. 여기서 만들어진 Keyword들은 기존 scheduler.run_tick()이
그대로 집어서 백그라운드 수집을 돈다 - 수집/승격/스코어링 로직은 새로 안
만들고 기존 인프라(CandidateSource 승격, source_scoring)를 그대로 재사용한다.

이 파일만 편집하면 관심 분야를 늘리거나 줄일 수 있다. 이미 등록된 키워드
(Keyword.name)는 중복 시딩되지 않는다.

주의 - 검색어 품질: "Creators"의 "Music"/"Movie"처럼 대분류 맥락 밖에서 보면
너무 광범위한 단어는 구글 뉴스 검색 결과가 흐려질 수 있다. 실제 수집 품질을
"장르별 출처 평가"에서 스코어/건수로 확인해보고, 필요하면 아래 TAXONOMY의
값 자체를 더 구체적인 문구로 바꾸는 걸 권장한다
(예: "Music" -> "music content creator", "Movie" -> "film director interview").
"""

import logging

from sqlmodel import Session, select

from database import engine
from models import Keyword

logger = logging.getLogger(__name__)

# 대분류(장르) -> 중분류(검색어 = Keyword.name) 목록.
# 필요하면 이 딕셔너리만 편집하면 됨 - 다른 코드는 안 건드려도 됨.
TAXONOMY: dict[str, list[str]] = {
    "AI": ["ChatGPT", "Claude", "Gemini", "Kimi", "DeepSeek", "Grok"],
    "Health": ["Diabetes", "Silver Health"],
    "Sports": ["Golf", "Football", "Baseball"],
    "Music": ["Best Song", "Pop", "Ballads"],
    "Movie": ["Best Movies", "Documentaries"],
    "Politics": ["America", "Korea"],
    "Economy": ["Stock", "Bitcoin"],
    "Books": ["Novel", "Best Seller"],
    "Creators": ["Music", "Movie", "Animation"],
    "Life": ["Cars", "Travel", "Foods", "Cook Recipe"],
}

# 전부 동일 주기(24시간)로 시작 - 시딩 직후 한꺼번에 몰리는 문제는
# scheduler.py의 MAX_KEYWORDS_PER_TICK 상한으로 완화한다 (여러 틱에 걸쳐 분산 처리).
DEFAULT_INTERVAL_HOURS = 24.0
DEFAULT_MONTHS_BACK = 1


def seed_taxonomy_keywords() -> int:
    """
    앱 기동 시 1회 호출. TAXONOMY에 정의된 (대분류, 중분류) 쌍마다 Keyword가
    없으면 새로 만든다. 이미 같은 이름의 키워드가 있으면(예: 예전에 수동으로
    등록했던 경우) 분류 정보만 채워주고 나머지는 건드리지 않는다.

    반환값: 새로 생성한 키워드 수 (로그 확인용).
    """
    added = 0
    with Session(engine) as session:
        for major, mids in TAXONOMY.items():
            for mid in mids:
                existing = session.exec(
                    select(Keyword).where(Keyword.name == mid)
                ).first()

                if existing:
                    if not existing.major_category:
                        existing.major_category = major
                        existing.mid_category = mid
                        session.add(existing)
                        session.commit()
                    continue

                session.add(Keyword(
                    name=mid,
                    major_category=major,
                    mid_category=mid,
                    months_back=DEFAULT_MONTHS_BACK,
                    interval_hours=DEFAULT_INTERVAL_HOURS,
                ))
                added += 1

        session.commit()

    logger.info(f"[taxonomy] 키워드 시딩 완료 (신규 {added}건)")
    return added
