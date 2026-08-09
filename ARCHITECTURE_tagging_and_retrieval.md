# ARCHITECTURE_tagging_and_retrieval.md — 다중 태그 분류 + 연관성 그래프 + 검색 확장

> 작성: 2026-08-09 (KST)
> 문제의식: 수만 건 규모로 데이터가 쌓일 때, 지금처럼 "기사 하나 = 카테고리 하나"
> 구조로는 활용도가 떨어진다. 음식-여행-건강, 음악-여행-출생지-아티스트처럼
> **한 기사가 여러 축에 걸쳐 있고, 태그끼리도 서로 연관**되는 구조를 만들어야
> LLM이 질문 의도에 맞는 데이터를 폭넓게 찾아낼 수 있다.

---

## 1. 지금 구조의 정확한 문제

| 구성요소 | 지금 하는 일 | 한계 |
|---|---|---|
| `Article.category` | 기사 하나당 카테고리 **1개**만 저장 | 여행+음식 기사면 둘 중 하나만 남고 나머지는 소실 |
| `_score_categories_for_article()` | 사실 **모든** 카테고리 점수를 계산함 | 계산은 다 해놓고 `max()`로 1등만 취하고 나머지 버림 |
| `personalization_taxonomy.py` | 서브카테고리도 동일하게 1개만 선택 | 개인화 프로필도 같은 손실을 겪음 |
| `retrieval.py` | 키워드 문자열 매칭 → 실패 시 카테고리 매칭 → 그마저 실패 시 최신순 | "연관은 있지만 직접 언급 안 된" 데이터를 찾을 방법이 없음 |
| 태그 간 관계 | **존재하지 않음** | "제주도 여행" 질문에 "제주 흑돼지 맛집"(음식) 기사가 안 딸려옴 |

**핵심 발견**: 다중 태그에 필요한 계산 로직은 이미 있습니다. `max()`로 버리는 부분만 바꾸면 됩니다 — 이게 이번 설계의 가장 저렴하면서도 효과가 큰 부분입니다.

## 2. 데이터 모델 — 다중 태그 + 태그 관계 그래프

### 2-1. 신규 테이블 3개

```python
class Tag(SQLModel, table=True):
    """
    태그(해시) 사전. 기존 CATEGORY_CONFIG/SUBCATEGORY_CONFIG의 카테고리들을
    그대로 초기 태그로 시딩하고, 이후 세분화된 태그(지역/인물/장르 등)를 추가한다.
    """
    __tablename__ = "tags"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)          # 예: "Travel", "TRAVEL.JEJU", "jazz"
    dimension: str = Field(index=True, default="topic")  # "topic" | "location" | "person" | "genre" 등
    label_ko: Optional[str] = None                       # 화면 표시용 한글 라벨


class ArticleTag(SQLModel, table=True):
    """기사 ↔ 태그 다대다 연결. 기사 하나가 여러 태그를 가질 수 있다."""
    __tablename__ = "article_tags"
    id: Optional[int] = Field(default=None, primary_key=True)
    article_id: int = Field(foreign_key="articles.id", index=True)
    tag_id: int = Field(foreign_key="tags.id", index=True)
    score: float = Field(default=1.0)   # 매칭 점수 (기존 _score_categories_for_article 점수 그대로 저장)


class TagRelation(SQLModel, table=True):
    """
    태그 간 연관성 그래프의 엣지(무방향). weight가 높을수록 강한 연관.
    source로 어떻게 생긴 관계인지 구분 - 신뢰도 판단에 활용 가능.
    """
    __tablename__ = "tag_relations"
    id: Optional[int] = Field(default=None, primary_key=True)
    tag_a_id: int = Field(foreign_key="tags.id", index=True)
    tag_b_id: int = Field(foreign_key="tags.id", index=True)
    weight: float = Field(default=0.5)         # 0~1, 연관 강도
    source: str = Field(default="manual")      # "manual" | "co_occurrence" | "llm_inferred"
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

**기존 `Article.category`는 그대로 둡니다** (하위호환, `/stats/keywords` 등 지금 코드가 계속 씀). `ArticleTag`가 그 위에 "여러 개 버전"으로 얹히는 구조입니다.

### 2-2. 저장 시점: 1등만 뽑지 말고 임계값 넘는 것 전부 저장

**기존** (main.py):
```python
def _best_category_for_article(article: Article) -> str | None:
    scores = _score_categories_for_article(article)
    if not scores:
        return None
    return max(scores, key=scores.get)   # ← 1등만 취함, 나머지 버림
```

**추가** (기존 함수는 안 건드리고 새 함수만 추가):
```python
_TAG_SCORE_THRESHOLD = 3  # 이 점수 이상인 카테고리는 전부 태그로 채택 (임계값은 실측하며 조정)

def _all_categories_above_threshold(article: Article) -> dict[str, int]:
    """1등만 취하지 않고, 임계값을 넘는 카테고리를 전부 반환한다 (다중 태그용)."""
    scores = _score_categories_for_article(article)
    return {cat: score for cat, score in scores.items() if score >= _TAG_SCORE_THRESHOLD}
```

`collectors.py`의 저장 지점에서, 기존 `article.category = _best_category_for_article(article)` 한 줄에 이어 아래도 같이:
```python
for category, score in _all_categories_above_threshold(article).items():
    tag = _get_or_create_tag(session, category)
    session.add(ArticleTag(article_id=article.id, tag_id=tag.id, score=score))
```

이러면 "제주도 흑돼지 맛집" 같은 기사가 `Travel`(제목에 "제주도")과 `Culture.Food`(본문에 "맛집", "돼지") 둘 다 임계값을 넘으면 **두 태그 모두** 붙습니다. 예전엔 둘 중 점수 높은 것 하나만 남았습니다.

## 3. 태그 관계 그래프 — 어떻게 채울까

세 가지 방법을 **섞어서** 쓰는 걸 권장합니다. 하나만으로는 부족합니다.

### 방법 1: 수동 큐레이션 (가장 신뢰도 높음, 적은 개수만)

`taxonomy.py`가 이미 쓰고 있는 "딕셔너리 하나만 편집하면 끝" 패턴을 그대로 재사용:

```python
# tag_relations_manual.py (신규)
MANUAL_TAG_RELATIONS: list[tuple[str, str, float]] = [
    ("Travel", "Culture.Food", 0.7),      # 여행-음식
    ("Travel", "Health", 0.3),            # 여행-건강(약함)
    ("Music", "Travel", 0.4),             # 음악-여행(페스티벌 등)
    ("Music", "Culture.Media", 0.6),      # 음악-엔터테인먼트
    # 필요한 만큼 계속 추가 - 이 리스트만 편집하면 됨
]
```
서버 기동 시 `taxonomy.seed_taxonomy_keywords()`처럼 한 번 시딩하는 함수를 만들면 됩니다. 말씀하신 "음식-여행-건강", "음악-여행-출생지-아티스트" 같은 **사람만 아는 상식적 연관**은 이 방식이 가장 정확합니다.

### 방법 2: 공동 출현(co-occurrence) 자동 집계 (수동으로 못 짚은 것 보완)

같은 기사에 두 태그가 자주 같이 붙으면, 그 자체가 연관성의 증거입니다. SQL로 주기적으로(예: 하루 1회, 스케줄러 틱에 얹어서) 집계:

```python
def compute_cooccurrence_relations(session: Session, min_cooccur: int = 5):
    """
    같은 article_id에 같이 붙은 태그 쌍의 빈도를 세서, min_cooccur번 이상
    같이 나온 쌍은 TagRelation(source="co_occurrence")으로 기록/갱신한다.
    """
    # article_tags를 자기 자신과 조인해서 (tag_a, tag_b, count) 집계
    # weight = count를 0~1로 정규화해서 저장
    ...
```

이건 수동으로는 절대 못 짚는, **실제 수집된 기사들이 스스로 알려주는 연관성**이라 시간이 지날수록 더 정확해집니다.

### 방법 3: LLM 추론 (선택적, 비용 있음)

태그 전체 목록을 LLM에게 한 번에 보여주고 "이 중 연관 있는 쌍을 찾아줘"라고 시키는 방식. **태그 개수가 많아지면 쌍의 개수(N²)가 폭발**하므로, 방법 1·2로 걸러진 "애매한 후보"만 LLM에게 확인시키는 정도로 제한하는 게 현실적입니다. 지금 단계에선 굳이 필요 없고, 방법 1+2만으로 충분히 시작할 수 있습니다.

## 4. 검색(RAG) 쪽 — 질문 의도 파악 → 태그 확장

`retrieval.py`를 아래 순서로 업그레이드합니다 (기존 3단계 우선순위에 **태그 확장 단계**를 끼워 넣는 구조):

```
1. 등록 키워드 직접 매칭 (기존 유지)
2. 신규: 질문에서 태그 추출 (LLM 또는 태그명 부분매칭)
   → 매칭된 태그로 ArticleTag 조회
   → 결과가 부족하면(예: 3건 미만) TagRelation을 1홉 확장해서
     "직접 언급 안 됐지만 연관된 태그"의 기사도 같이 포함
3. 기존 카테고리 매칭 (하위호환 폴백)
4. 최신순 폴백 (기존 유지)
```

예시로 설명하면: **"제주도 여행 갈만한 곳 추천해줘"** 라는 질문이 오면:
1. `Travel` 태그가 매칭되어 여행 기사들이 잡힘
2. 태그 확장으로 `Travel`과 연관도 0.7인 `Culture.Food`(음식) 태그도 함께 끌려옴
3. 결과: 여행지 기사뿐 아니라 "제주 흑돼지", "제주 카페" 같은 음식 기사까지 자연스럽게 참고자료에 포함됨

이건 지난번에 만든 `_match_keyword_semantically()`(채팅 자동수집용 LLM 매칭)와 **거의 동일한 패턴**이라, 그 코드를 거의 그대로 재사용해서 "Keyword 목록" 대신 "Tag 목록"을 대상으로 돌리면 됩니다.

## 5. 정직하게 짚을 한계 — 태그만으로는 결국 천장이 있습니다

여기까지가 **지금 SQLite 구조로 바로 만들 수 있는, 비용 대비 효과가 가장 큰 현실적인 방법**입니다. 다만 한계가 있습니다:

- 태그와 관계는 **사람이 예상한 범주 안에서만** 작동합니다. "이 기사와 저 기사가 의미상 비슷하다"는, 미리 정의한 태그/관계 밖의 연결은 못 잡습니다.
- 태그 개수가 수백 개로 늘어나면 관계 그래프 관리 자체가 부담이 됩니다.

**진짜 완전한 해결은 임베딩(벡터) 검색**입니다 — 이미 `PROJECT_STATUS.md`의 "남은 작업"에 LanceDB 도입이 계속 미뤄져 온 항목으로 적혀 있었죠. 임베딩은 "정의 안 된 연관성"까지 의미 유사도로 잡아냅니다.

**권장 조합**: 태그는 계속 **빠른 1차 필터**로 쓰고, 임베딩을 도입하면 그 필터링된 후보군 안에서 **정교한 재순위(re-ranking)**를 임베딩이 담당하는 하이브리드 구조로 가는 게 실무에서 가장 널리 쓰이는 방식입니다. 즉 이번 설계가 "임베딩 대신"이 아니라 "임베딩 도입 전까지의 실속 있는 중간 단계 + 임베딩 도입 후에도 계속 쓰이는 빠른 사전 필터" 역할을 합니다.

## 6. 단계별 로드맵

| 단계 | 내용 | 규모 |
|---|---|---|
| **A** | `Tag`/`ArticleTag`/`TagRelation` 테이블 추가, 저장 시점에 다중 태그 부여 (기존 `_score_categories_for_article` 재사용) | 작음 |
| **B** | 기존 3,600여 건 기사에 다중 태그 백필 (`/admin/backfill-categories`와 유사한 일회성 엔드포인트) | 작음 |
| **C** | `MANUAL_TAG_RELATIONS` 수동 큐레이션 (말씀하신 음식-여행-건강, 음악-여행-출생지-아티스트 같은 관계를 직접 정의) | 작음~중간 (직접 관계를 얼마나 정의하느냐에 따라) |
| **D** | co-occurrence 자동 집계 배치 추가 | 중간 |
| **E** | `retrieval.py`에 태그 확장 검색 로직 연결 (채팅에서 바로 체감됨) | 중간 |
| **F (장기)** | 임베딩/LanceDB 도입, 태그는 1차 필터로 계속 활용 | 큼 (별도 단계로 진행 권장) |

---

A~E는 지금 아키텍처(SQLite, 별도 인프라 추가 없음)로 바로 만들 수 있고, 지금 채팅 기능이나 근거 부족 감지 로직과도 자연스럽게 맞물립니다. F(임베딩)는 이후 별도 단계로 진행하는 걸 권장합니다.

이 방향으로 진행해도 괜찮으시면, **A(스키마 + 저장 로직)**부터 실제 패치로 만들어드리겠습니다. 혹시 방법 1(수동 태그 관계)에 먼저 넣고 싶으신 관계들이 있다면 몇 개 예시로 알려주시면 초기 `MANUAL_TAG_RELATIONS` 리스트에 반영해서 시작하겠습니다.
