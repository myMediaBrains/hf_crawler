# -*- coding: utf-8 -*-
"""
source_scoring.py
------------------
소스별 품질 점수 계산 (MVP: 휴리스틱 기반, DB 스키마 변경 없음 - 요청 시 즉석 계산).
hf_crawler 프로젝트 루트에 그대로 추가하세요. models.py/migrate_db.py 손댈 필요 없음.

점수 구성 (총 100점):
- volume_score      (0~40): 수집된 기사 건수 (log 스케일 - 건수가 많을수록 좋지만 100건과
                              1000건의 차이는 10건과 100건의 차이보다 덜 중요하게 취급)
- reliability_score (0~30): fail_count 기반 안정성. scheduler.py의 FAIL_THRESHOLD(=3)와
                              같은 기준을 쓴다 (순환 import를 피하려 값만 복제, 값을 바꾸면
                              두 파일 모두 맞춰줄 것).
- freshness_score   (0~15): 마지막 수집 성공 시점이 얼마나 최근인지
- content_score     (0~15): 수집된 기사 평균 본문 길이 (정보 밀도의 값싼 근사치)

향후 확장 지점
----------------
지금은 비용 없는 휴리스틱만으로 MVP를 먼저 굴린다. 다음 단계로 content_score를
LLM 채점으로 보강/대체하려면 아래 함수를 추가하고 compute_score()의 content_score
자리에 가중 평균으로 섞으면 된다 (예: content_score*0.4 + llm_score*0.6):

    def compute_llm_quality_score(sample_articles: list[str]) -> float:
        '''LIGHT 모델(model_router)로 기사 2~3건을 샘플링해 0~15점 채점.
        "정보 밀도 / 원본성 / 출처 명시 여부" 3축을 프롬프트로 채점시키고
        평균을 낸다. 매 요청마다 돌리면 비용이 크므로, 스케줄러 틱에서
        하루 1회 정도만 갱신하고 Source에 캐시 컬럼을 추가하는 게 좋다
        (이때는 models.py 마이그레이션이 필요해짐).'''
        ...
"""

import math
from datetime import datetime

FAIL_THRESHOLD = 3  # scheduler.py의 FAIL_THRESHOLD와 동일하게 유지할 것


def _volume_score(article_count: int) -> float:
    if article_count <= 0:
        return 0.0
    return min(40.0, 10.0 * math.log2(article_count + 1))


def _reliability_score(fail_count: int) -> float:
    ratio = max(0.0, 1.0 - (fail_count / FAIL_THRESHOLD))
    return 30.0 * ratio


def _freshness_score(last_success_at: datetime | None) -> float:
    if last_success_at is None:
        return 0.0
    days = (datetime.now() - last_success_at).total_seconds() / 86400
    if days <= 1:
        return 15.0
    if days <= 3:
        return 10.0
    if days <= 7:
        return 5.0
    return 0.0


def _content_score(avg_content_length: float) -> float:
    # 500자 미만은 요약/스텁 수준으로 보고 감점, 3000자 이상이면 만점
    if avg_content_length <= 0:
        return 0.0
    return min(15.0, 15.0 * (avg_content_length / 3000.0))


def compute_score(
    article_count: int,
    fail_count: int,
    last_success_at: datetime | None,
    avg_content_length: float,
) -> dict:
    """
    개별 점수 항목과 총점을 함께 반환한다 (프론트엔드에서 breakdown을 보여주기 위함 -
    사용자가 "왜 이 점수인지" 바로 이해할 수 있게).
    """
    volume = _volume_score(article_count)
    reliability = _reliability_score(fail_count)
    freshness = _freshness_score(last_success_at)
    content = _content_score(avg_content_length)
    total = round(volume + reliability + freshness + content, 1)

    return {
        "total": total,
        "breakdown": {
            "volume": round(volume, 1),
            "reliability": round(reliability, 1),
            "freshness": round(freshness, 1),
            "content": round(content, 1),
        },
    }
