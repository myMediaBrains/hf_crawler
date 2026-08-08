"""
models.py
전체 DB 스키마.

주의: 이 파일은 실제 원본을 기준으로 작성됐다. 기존 Article/Notification/
UserPreference의 필드는 하나도 건드리지 않았고(기존 DB 데이터 보존),
신규 기능에 필요한 필드/테이블만 추가했다.

테이블명 규칙: 기존 코드가 __tablename__을 복수형으로 명시하는 관례
(articles, notifications, user_preferences)를 따라, 새 테이블도 전부
복수형으로 명시했다. 외래키는 반드시 이 복수형 테이블명을 참조해야 한다
(예: "articles.id", "keywords.id") - 예전 버전에서 이 부분을 단수형으로
잘못 썼던 실수를 여기서 바로잡았다.
"""

from enum import Enum
from typing import Optional

from sqlmodel import SQLModel, Field, Column, DateTime, func
from datetime import datetime


# ============================================================
# 공통 열거형
# ============================================================

class ContentOrigin(str, Enum):
    """콘텐츠(기사 본문, 번역 등)가 어떻게 만들어졌는지"""
    RAW_CRAWL = "raw_crawl"           # 크롤링 직후, 가공 전
    LLM_CLEANED = "llm_cleaned"       # LLM이 노이즈 제거/문단 정리
    LLM_TRANSLATED = "llm_translated" # LLM이 번역
    USER_EDITED = "user_edited"       # 사용자가 에디터에서 직접 수정


class SourceOrigin(str, Enum):
    """수집 소스가 어떤 경로로 등록됐는지"""
    MANUAL = "manual"                 # 초기 고정 RSS 목록 (기존 TARGET_SOURCES)
    AUTO_PROMOTED = "auto_promoted"   # 같은 키워드에서 같은 출처가 3회 이상 등장 -> 자동 승격
    MANUAL_ADDED = "manual_added"     # 사용자가 직접 찾아서 즉시 등록
    BLOCKED = "blocked"               # 크롤링이 막혀 기사를 한 건도 저장 못한 도메인 (블록리스트)


class SourceStatus(str, Enum):
    ACTIVE = "active"
    FAILING = "failing"    # 연속 3회 실패 - "탈락 후보"로 표시, 최종 삭제는 사용자 판단


class CandidateStatus(str, Enum):
    CANDIDATE = "candidate"
    PROMOTED = "promoted"
    DROPPED = "dropped"


# ============================================================
# 기존 Article - 필드는 전혀 건드리지 않고, 신규 필드만 끝에 추가
# ============================================================

class Article(SQLModel, table=True):
    """통합 아티클 모델"""
    __tablename__ = "articles"

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(index=True, nullable=False)
    url: str = Field(unique=True, index=True, nullable=False)
    content: Optional[str] = Field(default="내용 없음")
    source: Optional[str] = Field(default="Unknown")
    published_at: Optional[str] = None
    collected_at: Optional[datetime] = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, server_default=func.now())
    )

    # 추가 필드 (기존)
    summary: Optional[str] = None
    category: Optional[str] = None
    is_read: bool = Field(default=False)
    is_favorite: bool = Field(default=False)

    # --- 신규: 원본 보존 (ML 학습 대비) ---
    raw_content: Optional[str] = None
    # 크롤링 직후 텍스트를 그대로 담아둔다. content는 정제/편집할 때마다 계속
    # 갱신되지만, 이 필드는 절대 덮어쓰지 않는다 (정제 전/후 쌍이 있어야
    # 나중에 "정제 모델"을 학습시킬 수 있기 때문).

    # --- 신규: 키워드 수집 태깅 ---
    keyword: Optional[str] = Field(default=None, index=True)
    # 사용자가 등록한 키워드로 수집된 기사면 그 키워드 이름이 들어간다.
    # 기존 고정 RSS로 수집된 기사는 None. 키워드 버튼 클릭 시
    # 정확한 매칭(WHERE keyword = ...)에 이 필드를 쓴다.

    # --- 신규: 이력 메타정보 ---
    origin: ContentOrigin = Field(default=ContentOrigin.RAW_CRAWL)
    model_used: Optional[str] = None
    # collected_at이 이미 "언제 생성됐는지"를 담당하고 있어 created_at은
    # 별도로 추가하지 않았다 (중복 필드 방지).

    def __repr__(self):
        return f"<Article(id={self.id}, title={self.title[:30]}...)>"


# ============================================================
# 기존 Notification - 그대로 보존 (신규 코드에서 참조하지 않음)
# ============================================================

class Notification(SQLModel, table=True):
    """알림 모델"""
    __tablename__ = "notifications"

    id: Optional[int] = Field(default=None, primary_key=True)
    article_id: int = Field(foreign_key="articles.id", nullable=False)
    user_id: Optional[str] = None
    type: str = Field(default="new_article")
    read: bool = Field(default=False)
    created_at: Optional[datetime] = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, server_default=func.now())
    )

    def __repr__(self):
        return f"<Notification(id={self.id}, article_id={self.article_id})>"


# ============================================================
# 기존 UserPreference - 그대로 보존
# ============================================================

class UserPreference(SQLModel, table=True):
    """사용자 선호도 모델"""
    __tablename__ = "user_preferences"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str = Field(index=True, nullable=False)
    categories: str = Field(default="[]")
    keywords: str = Field(default="[]")
    updated_at: Optional[datetime] = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, server_default=func.now(), onupdate=func.now())
    )

    def __repr__(self):
        return f"<UserPreference(id={self.id}, user_id={self.user_id})>"


# ============================================================
# 번역 이력 (신규)
# 지금까지는 SSE로 스트리밍만 되고 DB 어디에도 남지 않았다. 이 테이블이
# 생기면 번역 결과가 영구 보존되어, 나중에 번역 모델을 파인튜닝하고 싶을 때
# 그대로 학습 데이터로 쓸 수 있다.
# ============================================================

class Translation(SQLModel, table=True):
    __tablename__ = "translations"

    id: Optional[int] = Field(default=None, primary_key=True)
    article_id: int = Field(foreign_key="articles.id", index=True)
    mode: str                          # "literal" | "natural"
    translated_content: str

    origin: ContentOrigin = Field(default=ContentOrigin.LLM_TRANSLATED)
    model_used: Optional[str] = None
    block_reason: Optional[str] = None
    # 블록리스트(category="블록리스트")에만 채워지는 사유 키워드.
    # 예: "타임아웃" / "동의배너차단" / "본문추출실패" / "차단(원인불명)"

    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================
# 수집 소스 (신규)
# 기존 main.py의 TARGET_SOURCES 하드코딩 리스트를 대체한다.
# source_type이 Collector 레지스트리 조회 키가 되어, 미디어 타입이
# 늘어나도(유튜브/팟캐스트 등) 이 테이블 구조는 그대로 재사용된다.
# ============================================================

class Source(SQLModel, table=True):
    __tablename__ = "sources"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    url: str = Field(unique=True, index=True)
    category: Optional[str] = None

    source_type: str = Field(default="rss", index=True)

    origin: SourceOrigin = Field(default=SourceOrigin.MANUAL)
    status: SourceStatus = Field(default=SourceStatus.ACTIVE)
    interval_hours: float = Field(default=3.0)
    fail_count: int = Field(default=0)

    last_success_at: Optional[datetime] = None
    last_attempt_at: Optional[datetime] = None

    keyword_id: Optional[int] = Field(default=None, foreign_key="keywords.id")

    model_used: Optional[str] = None
    block_reason: Optional[str] = None   # ← 이 줄이 있는지 확인
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================
# 사용자 등록 키워드 (신규)
# ============================================================

class Keyword(SQLModel, table=True):
    __tablename__ = "keywords"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)

    months_back: int = Field(default=1)
    # 검색창 옆 입력창 A: 즉시 수집 시 "최근 N개월" 데이터만 가져온다.

    interval_hours: float = Field(default=24.0)
    # 검색창 옆 입력창 B: 백그라운드에서 이 키워드를 몇 시간 간격으로 재수집할지.

    major_category: Optional[str] = None
    # 대분류(장르) - taxonomy.py가 자동 시딩할 때 채운다. "장르별 출처 평가"의
    # 장르 버튼과 동일한 값이 되도록, 승격 시 Source.category에도 그대로 들어간다.
    mid_category: Optional[str] = None
    # 중분류 - 지금은 Keyword.name과 동일한 값이지만, 나중에 사람이 읽기 쉬운
    # 라벨을 name과 다르게 붙이고 싶을 때를 대비해 분리해뒀다.

    last_collected_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

# ============================================================
# 승격 후보 소스 추적 (신규)
# ============================================================

class CandidateSource(SQLModel, table=True):
    __tablename__ = "candidate_sources"

    id: Optional[int] = Field(default=None, primary_key=True)
    keyword_id: int = Field(foreign_key="keywords.id", index=True)
    domain: str = Field(index=True)     # 예: "techcrunch.com"
    source_name: str                    # Google 뉴스가 제공하는 표시 이름

    hit_count: int = Field(default=1)
    status: CandidateStatus = Field(default=CandidateStatus.CANDIDATE)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class BlockedDomain(SQLModel, table=True):
    """
    사용자가 출처관리에서 '블록리스트' 소스를 삭제하면 여기에 도메인이 기록된다.
    이후 수집(collect_for_keyword/collect)에서 이 도메인이 RSS 결과에 다시 나와도
    크롤링을 시도하지 않고 즉시 건너뛴다 - "삭제 = 앞으로도 검색 안 함"을 보장하기 위함.
    """
    __tablename__ = "blocked_domains"

    id: Optional[int] = Field(default=None, primary_key=True)
    domain: str = Field(unique=True, index=True)
    reason: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================
# 스케줄러 전역 설정 (신규, 항상 1행만 존재하는 싱글턴 테이블)
# ============================================================

class SchedulerConfig(SQLModel, table=True):
    __tablename__ = "scheduler_config"

    id: Optional[int] = Field(default=None, primary_key=True)
    tick_minutes: int = Field(default=30)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

# -*- coding: utf-8 -*-
"""
models_addon_interaction_signal.py
------------------------------------
이 내용을 기존 models.py 맨 끝에 그대로 붙여넣으세요 (import는 이미 models.py
상단에 있는 것들만 사용하므로 추가 import 불필요 — Optional, SQLModel, Field,
datetime 이미 있음).

기존 UserPreference 테이블(categories/keywords를 JSON 문자열로 뭉뚱그려 저장)은
그대로 두고 건드리지 않는다. 대신 "신호 하나하나의 원본 로그"를 남기는 새 테이블을
추가한다 — UserPreference는 나중에 이 로그로부터 집계된 요약본을 캐싱하는 용도로
계속 쓸 수 있다 (지금 당장은 비워두고, personalization.py가 즉석 계산으로 대체).

__tablename__ 복수형 규칙 유지: interaction_signals
"""


class InteractionSignal(SQLModel, table=True):
    """
    개인화 프로필의 원재료가 되는 신호 원본 로그.
    - 브라우저 확장 이벤트, 대화 발화, 명시적 피드백(👍👎) 모두 이 한 테이블에 쌓인다.
    - 절대 UPDATE하지 않는다 (append-only 로그). 집계는 항상 조회 시점에 계산한다
      (raw_content를 절대 안 건드리는 Article 원칙과 같은 이유 — 나중에 다른 방식으로
      재집계하고 싶을 때 원본이 훼손되지 않아야 하기 때문).
    """
    __tablename__ = "interaction_signals"

    id: Optional[int] = Field(default=None, primary_key=True)

    # 어떤 기사/맥락에서 나온 신호인지 (없을 수도 있음 - 예: 순수 채팅 질문)
    article_id: Optional[int] = Field(default=None, foreign_key="articles.id", index=True)

    source: str = Field(index=True)
    # "extension" | "chat" | "feedback_explicit"

    subcategory: str = Field(index=True)
    # personalization_taxonomy.SUBCATEGORY_CONFIG 의 코드 (예: "ECON.STOCK")

    top_category: str = Field(index=True)
    # 기존 CATEGORY_CONFIG 키와 동일한 값 (예: "Economy") - 조인 없이 바로 집계하기 위한 비정규화 필드

    signal_type: str = Field(default="implicit")
    # "explicit" | "implicit"

    confidence: float = Field(default=0.5)
    weight: float = Field(default=1.0)

    raw_snippet: Optional[str] = None
    # 원문 일부 (근거 제시용). 너무 길게 넣지 말 것 (제목/발췌 수준 권장).

    created_at: datetime = Field(default_factory=datetime.utcnow)
    # DB에는 UTC로 저장 (기존 Article.collected_at과 동일한 관례 유지).
    # KST 표시는 조회/응답 시점에 personalization.py의 to_kst()로 변환한다.

class ContentOrigin(str, Enum):
    RAW_CRAWL = "raw_crawl"
    LLM_CLEANED = "llm_cleaned"
    LLM_TRANSLATED = "llm_translated"
    USER_EDITED = "user_edited"
    LLM_GENERATED = "llm_generated"  # 신규: 텍스트 생성기가 처음부터 만든 콘텐츠


class TextGeneration(SQLModel, table=True):
    """개인화 텍스트 생성기의 질의-응답 이력 (append-only, 절대 UPDATE하지 않음)."""
    __tablename__ = "text_generations"

    id: Optional[int] = Field(default=None, primary_key=True)
    query: str = Field(nullable=False)
    answer: str = Field(nullable=False)

    source_article_ids: Optional[str] = None   # JSON 배열 문자열, 예: "[12, 45, 89]"
    matched_categories: Optional[str] = None   # JSON 배열 문자열

    origin: ContentOrigin = Field(default=ContentOrigin.LLM_GENERATED)
    model_used: Optional[str] = None

    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, server_default=func.now())
    )