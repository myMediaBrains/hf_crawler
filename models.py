"""
models.py
전체 DB 스키마.

2026-08-09 대개편: CATEGORY_CONFIG(main.py) / SUBCATEGORY_CONFIG
(personalization_taxonomy.py) / TAXONOMY(taxonomy.py) 3개의 하드코딩 딕셔너리
기반 분류 시스템을 Tag/TagKeyword/TagBlacklist/ArticleTag/TagRelation 5개
테이블로 통합했다. Article/Keyword/Source/InteractionSignal이 전부 이 Tag
하나를 참조한다.

이번 개편은 기존 데이터(기사 전체)를 보존하지 않고 DB를 새로 만드는 걸
전제로 한다 (마이그레이션이 아니라 재생성) - 그래서 예전 category/
major_category/mid_category(Keyword), subcategory/top_category
(InteractionSignal) 같은 필드는 새 스키마에 아예 없다.

테이블명 규칙: 전부 복수형(__tablename__)을 명시한다. 외래키는 반드시 이
복수형 테이블명을 참조해야 한다 (예: "articles.id", "tags.id").
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
    LLM_GENERATED = "llm_generated"   # 텍스트 생성기가 처음부터 만든 콘텐츠


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
# 분류 체계 통합 (신규, 2026-08-09) — 모든 분류의 유일한 원천
# ============================================================

class Tag(SQLModel, table=True):
    """
    기사/키워드/소스/개인화신호가 전부 참조하는 유일한 분류 단위.
    다중 부여 가능(ArticleTag를 통해 기사 하나에 태그 여러 개).

    빈 상태로 시작한다 - 하드코딩 시딩 데이터 없음. 장르 편집기(사람이 직접
    등록)와 채팅 자동수집(LLM이 근거 부족 시 제안)을 통해서만 채워진다.
    """
    __tablename__ = "tags"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    # 정규화된 고유 이름. 예: "Travel", "AI.PRODUCT". 장르편집기의 "소분류" 칸,
    # Keyword.name과 1:1로 맞춰 쓰는 걸 기본 규칙으로 한다.

    major_category: str = Field(index=True)
    # 대분류. 예: "AI", "Life". 장르편집기의 "대분류" 칸.

    mid_category: Optional[str] = None
    # 중분류(사람이 읽는 라벨). 예: "AI 제품/서비스". 장르편집기의 "중분류" 칸.

    label_ko: Optional[str] = None
    # 화면 표시용 한글 라벨 (없으면 name/mid_category로 대체 표시)

    sensitive: bool = Field(default=False)
    # 기존 SENSITIVE_TOP_CATEGORIES({"Politics","Economy"}) 대체.
    # True면 개인화 프로필에서 "결론 유도"가 아니라 "정보 필터링"에만 쓴다.

    dimension: str = Field(default="topic")
    # "topic"(기본) - 향후 "location"/"person"/"genre" 등으로 확장 가능한 자리.
    # 지금 당장은 전부 "topic"으로만 씀 (과설계 방지).

    created_at: datetime = Field(default_factory=datetime.utcnow)

    def __repr__(self):
        return f"<Tag(name={self.name}, major={self.major_category})>"


class TagKeyword(SQLModel, table=True):
    """
    Tag 하나가 매칭에 쓰는 키워드(용어) 목록. 기존 CATEGORY_CONFIG[category]
    ["keywords"] 대체. 정규식 매칭에 그대로 쓰인다 - 제목 3배/본문 1배 가중치
    점수제는 기존 _score_categories_for_article() 로직을 그대로 재사용.

    새 Tag 생성 시 name 자체가 자동으로 첫 TagKeyword로 등록된다 (최소한의
    매칭이 바로 작동하도록) - 사람/LLM이 이후 동의어를 더 추가할 수 있음.
    """
    __tablename__ = "tag_keywords"

    id: Optional[int] = Field(default=None, primary_key=True)
    tag_id: int = Field(foreign_key="tags.id", index=True)
    term: str = Field(index=True)


class TagBlacklist(SQLModel, table=True):
    """Tag 하나를 오분류에서 제외시키는 단어 목록. 기존 CATEGORY_CONFIG[category]["blacklist"] 대체."""
    __tablename__ = "tag_blacklists"

    id: Optional[int] = Field(default=None, primary_key=True)
    tag_id: int = Field(foreign_key="tags.id", index=True)
    term: str


class ArticleTag(SQLModel, table=True):
    """
    기사 ↔ 태그 다대다. 기사 하나가 여러 태그를 가질 수 있다 (기존
    Article.category 단일값의 한계를 해결하는 핵심 테이블).
    """
    __tablename__ = "article_tags"

    id: Optional[int] = Field(default=None, primary_key=True)
    article_id: int = Field(foreign_key="articles.id", index=True)
    tag_id: int = Field(foreign_key="tags.id", index=True)
    score: float = Field(default=1.0)
    # _score_categories_for_article()이 계산한 매칭 점수를 그대로 저장
    # (나중에 랭킹/정렬에 활용 가능).

    created_at: datetime = Field(default_factory=datetime.utcnow)


class TagRelation(SQLModel, table=True):
    """
    태그 간 연관성 그래프(무방향 엣지). 완전 신규 개념 - 기존 3개 분류
    시스템 어디에도 없던 것. "여행-음식", "음악-여행" 같은 관계를 명시한다.
    """
    __tablename__ = "tag_relations"

    id: Optional[int] = Field(default=None, primary_key=True)
    tag_a_id: int = Field(foreign_key="tags.id", index=True)
    tag_b_id: int = Field(foreign_key="tags.id", index=True)
    weight: float = Field(default=0.5)     # 0~1, 연관 강도
    source: str = Field(default="manual")  # "manual" | "co_occurrence" | "llm_inferred"
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================
# Article - 기존 필드 보존, category는 레거시로 방치(더 이상 안 채움)
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

    summary: Optional[str] = None
    category: Optional[str] = None
    # 2026-08-09부터 레거시. 더 이상 채우지 않음 - 다중 분류는 ArticleTag를 참조할 것.
    # 컬럼 자체는 하위호환을 위해 남겨둠(과거 데이터가 있었다면 보존되지만,
    # 이번 개편은 DB를 새로 만드는 전제라 실질적으로는 항상 비어있게 됨).
    is_read: bool = Field(default=False)
    is_favorite: bool = Field(default=False)

    raw_content: Optional[str] = None
    # 크롤링 직후 텍스트를 그대로 담아둔다. content는 정제/편집할 때마다 계속
    # 갱신되지만, 이 필드는 절대 덮어쓰지 않는다.

    keyword: Optional[str] = Field(default=None, index=True)
    # 사용자가 등록한 키워드로 수집된 기사면 그 키워드 이름이 들어간다.
    # 기존 고정 RSS로 수집된 기사는 None.

    origin: ContentOrigin = Field(default=ContentOrigin.RAW_CRAWL)
    model_used: Optional[str] = None

    def __repr__(self):
        return f"<Article(id={self.id}, title={self.title[:30]}...)>"


# ============================================================
# Notification - 그대로 보존
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
# 사용자 프로필 — 비밀번호 없는 로컬 개인용 식별자
# ============================================================

class User(SQLModel, table=True):
    """
    사용자가 직접 정하는 문자열 ID로 등록한다 (인증 없음, 로컬 개인용).
    """
    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str = Field(unique=True, index=True)
    display_name: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    def __repr__(self):
        return f"<User(user_id={self.user_id})>"


# ============================================================
# 배송 로그 — 어떤 생성 결과를 언제 어디로 보냈는지 기록
# ============================================================

class Delivery(SQLModel, table=True):
    """
    text_generations 한 건을 외부 채널로 보낸 기록. channel="ntfy"(실제 발송)
    / channel="email"(mailto 링크만 만들어준 것 — 사용자가 직접 클릭해서 보냄).
    """
    __tablename__ = "deliveries"

    id: Optional[int] = Field(default=None, primary_key=True)
    generation_id: int = Field(foreign_key="text_generations.id", index=True)
    channel: str = Field(index=True)          # "ntfy" | "email"
    target: Optional[str] = None              # ntfy topic 또는 이메일 주소
    status: str = Field(default="pending")    # "pending" | "sent" | "failed"
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    def __repr__(self):
        return f"<Delivery(id={self.id}, channel={self.channel}, status={self.status})>"


# ============================================================
# UserPreference - 그대로 보존
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
# 번역 이력
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
    # 블록리스트(Source.source_type="blocked")에만 채워지는 사유 키워드.
    # 예: "타임아웃" / "동의배너차단" / "본문추출실패" / "차단(원인불명)"

    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================
# 수집 소스 — category(문자열) 대신 tag_id(FK)로 분류
# ============================================================

class Source(SQLModel, table=True):
    __tablename__ = "sources"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    url: str = Field(unique=True, index=True)

    tag_id: Optional[int] = Field(default=None, foreign_key="tags.id")
    # 2026-08-09: category(문자열) 대체. 승격된 소스는 원본 키워드의 tag_id를
    # 그대로 물려받는다. BlockList 여부는 이제 tag_id가 아니라 source_type으로만
    # 판별한다 (예전엔 category="BlockList"와 source_type="blocked"가 같은 걸
    # 이중으로 나타내는 중복 정보였음 - source_type 하나로 정리).

    source_type: str = Field(default="rss", index=True)
    # "rss" | "google_news_search" | "blocked"

    origin: SourceOrigin = Field(default=SourceOrigin.MANUAL)
    status: SourceStatus = Field(default=SourceStatus.ACTIVE)
    interval_hours: float = Field(default=3.0)
    fail_count: int = Field(default=0)

    last_success_at: Optional[datetime] = None
    last_attempt_at: Optional[datetime] = None

    keyword_id: Optional[int] = Field(default=None, foreign_key="keywords.id")

    model_used: Optional[str] = None
    block_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================
# 사용자 등록 키워드 — "무엇을 언제 재검색할지"만 담당 (분류는 Tag로 위임)
# ============================================================

class UserGenrePreference(SQLModel, table=True):
    """
    2026-08-12: '선호 장르 선택'은 이제 태그 하나하나가 아니라 '대분류' 단위로
    동작한다. 사용자가 대분류를 선택하면, 그 대분류 밑의 모든 키워드(지금
    있는 것 + 나중에 새로 생기는 것 전부)가 자동으로 그 사용자에게 보인다.
    """
    __tablename__ = "user_genre_preferences"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    major_category: str = Field(index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class KeywordSearchInterest(SQLModel, table=True):
    """
    2026-08-12: 실시간 검색으로 새 키워드가 만들어질 때, 아직 '미분류'
    (Tag.major_category == Tag.name인 placeholder 상태) 키워드라면 누가
    검색했는지 여기에 기록해둔다. 나중에 관리자가 이 키워드를 정식 장르로
    분류하면(POST /admin/classify-keyword), 여기 기록된 사용자들의 선호
    신호를 소급으로 남겨준다. 처리가 끝나면 해당 행은 삭제한다(중복 처리 방지).
    """
    __tablename__ = "keyword_search_interests"

    id: Optional[int] = Field(default=None, primary_key=True)
    keyword_id: int = Field(foreign_key="keywords.id", index=True)
    user_id: str = Field(index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Keyword(SQLModel, table=True):
    """
    백그라운드 검색 구독. 2026-08-09부터 분류(major_category/mid_category)는
    더 이상 여기서 안 하고 tag_id로 위임한다 - 이 테이블은 순수하게
    "무엇을 검색어로, 얼마나 자주, 최근 몇 개월치를 가져올지"만 관리한다.
    """
    __tablename__ = "keywords"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    # 구독 식별자. Tag.name과 1:1로 맞추는 걸 기본 규칙으로 한다
    # (장르편집기에서 등록하면 Keyword.name == Tag.name이 되도록 만든다).

    tag_id: Optional[int] = Field(default=None, foreign_key="tags.id", index=True)
    # 분류는 여기로 위임. None이면 아직 분류가 안 된 임시 구독.

    search_query: Optional[str] = None
    # 2026-08-09 신규: 실제 Google 뉴스 검색에 쓸 자유로운 자연어 문구.
    # name(분류용, 짧고 정규화)과 분리 - 채팅 자동수집이 "trending food
    # recipes, popular dishes now..." 같은 문장을 name에 박아버리던 오염
    # 문제(2026-08-09 실사용 중 발견)의 재발 방지. 비어있으면 name을 그대로
    # 검색어로 쓴다 (collectors.py에서 폴백 처리).

    months_back: int = Field(default=1)
    interval_hours: float = Field(default=24.0)
    last_collected_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================
# 승격 후보 소스 추적 - 그대로 보존
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
    이후 수집에서 이 도메인이 다시 나와도 크롤링을 시도하지 않고 즉시 건너뛴다.
    """
    __tablename__ = "blocked_domains"

    id: Optional[int] = Field(default=None, primary_key=True)
    domain: str = Field(unique=True, index=True)
    reason: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================
# 스케줄러 전역 설정 - 그대로 보존
# ============================================================

class SchedulerConfig(SQLModel, table=True):
    __tablename__ = "scheduler_config"

    id: Optional[int] = Field(default=None, primary_key=True)
    tick_minutes: int = Field(default=30)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================
# 개인화 신호 원본 로그 — subcategory/top_category(문자열) 대신 tag_id
# ============================================================

class InteractionSignal(SQLModel, table=True):
    """
    개인화 프로필의 원재료가 되는 신호 원본 로그. 절대 UPDATE하지 않는다
    (append-only). 집계는 항상 조회 시점에 계산한다.

    2026-08-09: subcategory(SUBCATEGORY_CONFIG 코드 문자열) 대신 tag_id(FK)로
    변경. major_category는 집계 시 매번 조인하지 않도록 비정규화해서 그대로 둔다
    (기존 원칙 유지 - "조인 없이 바로 집계하기 위한 비정규화 필드").
    """
    __tablename__ = "interaction_signals"

    id: Optional[int] = Field(default=None, primary_key=True)

    user_id: Optional[str] = Field(default=None, foreign_key="users.user_id", index=True)
    article_id: Optional[int] = Field(default=None, foreign_key="articles.id", index=True)

    source: str = Field(index=True)
    # "extension" | "chat" | "chat_expand" | "chat_delivered" | "chat_no_evidence" | "feedback_explicit"

    tag_id: int = Field(foreign_key="tags.id", index=True)
    major_category: str = Field(index=True)
    # Tag.major_category를 신호 발생 시점에 복사해둔 비정규화 필드.

    signal_type: str = Field(default="implicit")     # "explicit" | "implicit"
    confidence: float = Field(default=0.5)
    weight: float = Field(default=1.0)
    raw_snippet: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============================================================
# 텍스트 생성기 이력 - 그대로 보존
# ============================================================

class TextGeneration(SQLModel, table=True):
    """개인화 텍스트 생성기의 질의-응답 이력 (append-only, 절대 UPDATE하지 않음)."""
    __tablename__ = "text_generations"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[str] = Field(default=None, foreign_key="users.user_id", index=True)

    conversation_id: Optional[str] = Field(default=None, index=True)
    stage: str = Field(default="short", index=True)   # "short" | "long"
    parent_id: Optional[int] = Field(default=None, foreign_key="text_generations.id")

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

# ============================================================
# GitHub 오픈소스 저장소 (신규, 2026-08-10)
# ============================================================

class GitHubRepo(SQLModel, table=True):
    """GitHub 레포 최신 상태 1건당 1행. 스타 수 등 시계열은 GitHubRepoSnapshot에 별도 보관."""
    __tablename__ = "github_repos"

    id: Optional[int] = Field(default=None, primary_key=True)
    full_name: str = Field(unique=True, index=True)  # "owner/repo"
    url: str
    description: Optional[str] = None
    primary_language: Optional[str] = None
    readme_content: Optional[str] = None
    readme_hash: Optional[str] = None
    summary: Optional[str] = None
    created_at_github: Optional[str] = None  # 게재 시점
    pushed_at_github: Optional[str] = None   # 2026-08-10: 마지막 업데이트 시점 (신규)
    last_checked_at: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # 2026-08-10: 3단계 화면(테이블/상세/README) 지원용 - LLM 분석 결과.
    # 지연 생성(첫 상세 조회 시)하고, analysis_hash가 readme_hash와 다를 때만 재생성.
    field_short: Optional[str] = None          # 분야 (짧은 단어)
    application_short: Optional[str] = None    # 응용분야 (짧은 단어)
    relevance_short: Optional[str] = None      # 연관성 (짧은 단어)
    components_short: Optional[str] = None     # 구성요소 (짧은 단어)
    detailed_overview: Optional[str] = None
    detailed_application: Optional[str] = None
    detailed_relations: Optional[str] = None
    future_direction: Optional[str] = None
    extra_notes: Optional[str] = None
    # 2026-08-12: Typora 편집 시 알려진 4개 섹션(상세개요/상세응용분야/구성요소연관성/
    # 향후방향) 헤더가 아닌 새 "##" 섹션을 추가하면, 그 내용을 잃어버리지 않고
    # 여기에 (헤더명 보존한 채로) 모아서 저장한다.
    analysis_hash: Optional[str] = None        # 이 분석이 만들어진 시점의 readme_hash


class GitHubRepoSnapshot(SQLModel, table=True):
    """스타/포크 시계열 스냅샷 (append-only) - 절대 덮어쓰지 않음."""
    __tablename__ = "github_repo_snapshots"

    id: Optional[int] = Field(default=None, primary_key=True)
    repo_id: int = Field(foreign_key="github_repos.id", index=True)
    stars: int = 0
    forks: int = 0
    open_issues: int = 0
    snapshot_at: datetime = Field(default_factory=datetime.utcnow)


class GitHubReadmeHistory(SQLModel, table=True):
    """README 변경 이력 (append-only) - 해시가 바뀔 때만 새 행 추가."""
    __tablename__ = "github_readme_history"

    id: Optional[int] = Field(default=None, primary_key=True)
    repo_id: int = Field(foreign_key="github_repos.id", index=True)
    content: str
    content_hash: str
    summary: Optional[str] = None
    recorded_at: datetime = Field(default_factory=datetime.utcnow)


class GitHubRepoTag(SQLModel, table=True):
    """ArticleTag와 동일 패턴 - 뉴스와 같은 Tag 체계를 공유한다."""
    __tablename__ = "github_repo_tags"

    id: Optional[int] = Field(default=None, primary_key=True)
    repo_id: int = Field(foreign_key="github_repos.id", index=True)
    tag_id: int = Field(foreign_key="tags.id", index=True)
    score: float = Field(default=1.0)