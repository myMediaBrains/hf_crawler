# PROJECT_STATUS_07 — Tag 통합 재설계 완료 + 한글 검색 지원 + 양방향 번역 예정

> 관련 마스터플랜 장: 해당사항 없음 (대화 중 도출된 요구사항의 연속)
> 시작일: 2026-08-09 (KST) / 마지막 갱신: 2026-08-09 (KST)
> 이전 단계: `PROJECT_STATUS_06_chat_and_taxonomy_redesign.md`
> ⚠️ 이 문서는 **주간 사용량 75% 경고 시점에 작업을 일시 중단**하며 작성됨.
> 다음 세션은 "8. 다음 세션 최우선 작업"부터 시작할 것.

---

## 1. 이번 단계에서 한 일 (06번 문서 이후)

06번 문서에서 "분류 체계 통합 재설계 착수 결정"까지 하고 멈췄던 걸, **이번 세션에서 실제로 전부 구현 완료**했다. 추가로 실사용하며 나온 버그 3건과 신규 기능 2건도 처리했다.

1. **분류 체계 통합 재설계 — 전체 구현 완료** (06번 문서 9번 항목의 실행)
2. **`_paused2` 오타 버그 수정** (이번 개편과 무관한 별개 문제, 발견 즉시 수정)
3. **재개/중지 버튼의 "즉시 반영 안 됨" 문제 수정**
4. **선호 장르 선택 기능 신규 추가**
5. **한글 검색 지원 추가**
6. **(예정, 미착수) 번역 버튼 양방향화** — 이번 세션 마지막에 나온 신규 요구사항

---

## 2. 핵심 아키텍처 결정

### 2-1. 분류 체계 통합 (Tag 시스템) — 전체 구현 완료

06번 문서에서 설계만 했던 걸 실제로 다 만들었다.

- **`Tag`/`TagKeyword`/`TagBlacklist`/`ArticleTag`/`TagRelation`** 5개 테이블 신규 (`models.py`)
- **`tagging.py`** 신규 모듈 — 분류 로직의 유일한 원천. `get_or_create_tag()`, `score_tags_for_text()`, `assign_tags_to_article()` 등. `main.py`/`collectors.py`가 순환참조 없이 공용으로 씀 (기존 "지연 import" 임시방편을 근본적으로 제거)
- **`CATEGORY_CONFIG`/`SUBCATEGORY_CONFIG`/`TAXONOMY`** 3개 하드코딩 딕셔너리 폐기. `personalization_taxonomy.py`/`taxonomy.py` 파일 자체를 삭제
- **`Source.category`/`Keyword.major_category`/`Keyword.mid_category`/`InteractionSignal.subcategory`/`InteractionSignal.top_category`** 전부 삭제 → `tag_id`(FK) 기반으로 통일
- **다중 태그 지원**: 기존엔 기사 하나에 카테고리 1개만(`max()`로 나머지 버림) → 이제 임계값 넘는 태그 전부를 `ArticleTag`로 저장
- **`Keyword.search_query`** 신규: 분류용 이름(`Keyword.name`)과 실제 검색 문구를 분리 — "trending food recipes..." 같은 자연어 문구가 그대로 분류명에 박혀버리던 오염 문제의 근본 해결
- **Tag는 빈 상태로 시작** (사용자 명시적 결정) — 하드코딩 시딩 데이터 없음. 장르편집기(사람) + 채팅 자동수집(LLM)으로만 채워짐
- **DB 전체 초기화** — 마이그레이션이 아니라 재생성 (기존 데이터 보존 안 함, 사용자 명시적 동의)
- **BlockList 판별 중복 정리**: `Source.category="BlockList"`와 `source_type="blocked"`가 같은 정보를 이중으로 갖고 있던 걸 `source_type` 하나로 통일

### 2-2. 재개/중지의 "즉시성" 개선

- **`/scheduler/resume`**: 기존엔 "다음 틱부터 건너뛰지 않기"만 하고 즉시 수집은 안 됐음(최대 30분 대기) → 재개 즉시 백그라운드 스레드로 1회 틱을 바로 돌리도록 수정
- **`/scheduler/pause`**: 기존엔 "다음 틱부터 건너뛰기"만 하고 **이미 진행 중인 틱은 안 멈췄음** → 지금 도는 게 백그라운드 틱이면 `job_control.cancel_current_job()`도 같이 호출해서 진행 중인 것도 취소 신호를 받도록 수정
- **주기 카운팅 방식 확인**: "중지한 시점부터 24시간"이 아니라 "**각 키워드가 마지막으로 실제 수집된 시점부터** 24시간" — 항목별 독립 타이머 방식을 유지하기로 결정 (표준적이고 예측 가능함)

### 2-3. 선호 장르 선택 (신규 기능)

- **"⭐ 선호 장르 선택" 버튼** — "실시간 수집" 버튼 우측에 배치
- 사용자가 사전 정의한 22개 관심사(요리/AI/여행/스포츠/역사/정치/경제/소설/음악/라이프스타일/영화/드라마/다큐멘터리/양자컴퓨팅/비트코인/주식/IT기업/소프트웨어/실버건강/당뇨/헬스/음식)를 체크박스로 제공 + 직접 입력으로 항목 추가 가능
- 체크 → **① Tag/Keyword 등록(수집 시작) + ② `InteractionSignal`에 명시적 선호 신호로 동시 기록**(`store_tag_preference()`, weight=2.0) — 단순 등록이 아니라 개인화 신호로도 즉시 반영됨
- 사용자 이름은 패널 안에 표시(`👤 {userId} 님의 선호 장르`) — 화면 최상단 표시는 이미 있는 `UserRegister.jsx` 배지로 충분하다고 판단, 중복 안 만듦

### 2-4. 한글 검색 지원 (신규 기능)

- **기본값(영어 검색)은 완전히 하위호환** — 변화 없음
- **한글 입력 감지 시**: `Keyword.language="ko"`로 표시 → Google 뉴스 검색을 한국 로케일(`hl=ko&gl=KR`)로 전환 → 한글 기사 그대로 수집
- **단, `Keyword.name`/`Tag.name`(분류용)은 검색 언어와 무관하게 항상 영어로 통일** — LLM(`translate_keyword_en` 프로필)이 한글 검색어를 짧은 영어 태그로 압축 번역해서 저장. 5개 관리 화면(키워드 현황/선호 장르 선택/장르 편집기/출처 관리/출처 평가)의 가독성을 위해서 (사용자 명시적 요구사항)
- **출처(Source)는 언어 처리 대상에서 제외** — URL은 원래 항상 영문/ASCII라 별도 처리 불필요 (사용자가 직접 지적, 맞는 지적이었음)
- **기사 표시 화면(제목/본문)은 원문 그대로** — 번역 안 하고 한글이면 한글로 그대로 노출 (기존 원칙 유지)

---

## 3. 겪은 버그와 교훈

- **`job_control.py`의 `_paused2` 오타**: 이번 개편과 무관한, 이전 세션 어느 시점의 편집 실수로 추정. `is_paused()`가 항상 `NameError`를 내고 있었음 — grep으로 즉시 확인 후 수정.
- **"재개해도 가만히 있는 것 같다"**: 원인은 버그가 아니라 **설계상 빈틈**이었음 — 재개는 "다음 틱부터 건너뛰지 않기"만 할 뿐 즉시 실행을 안 시켰음. 사용자 체감과 실제 동작 사이의 괴리를 사용자가 직접 짚어줘서 발견.
- **"중지해도 계속 도는 것 같다"**: 마찬가지로 설계상 빈틈 — 이미 진행 중인 틱에는 취소 신호가 안 갔음. "이미 시작한 액션이 진행되는 건지, 버튼이 아예 안 먹는 건지 모르겠다"는 사용자의 정확한 문제 제기로 원인 확정.
- **Source.category 제거의 연쇄 여파**: `models.py`에서 필드 하나를 지웠을 뿐인데, 그 필드를 읽던 곳이 `main.py`(`/sources`, `/sources/evaluation`) + `scheduler.py`(`seed_manual_sources`, `_promote_candidate`) + `collectors.py`(`_record_blocked_source`) 총 5곳에 흩어져 있어서, 스키마 변경 하나가 5개 파일에 걸친 연쇄 수정을 요구했다. **모델 필드를 삭제/변경할 때는 반드시 전수 검색(`grep -rn "필드명"`)으로 참조처를 먼저 확인하는 습관이 중요하다는 걸 재확인.**
- **App.jsx의 `isSensitiveCategory` 시그니처 변경 파급**: `category` 문자열을 받던 함수를 `src`/`group` 객체를 받게 바꾸면서, 호출부 2곳(1403, 1441번 줄)도 함께 안 바꾸면 즉시 깨지는 상황이었음 — 사용자가 `grep`으로 직접 호출부를 찾아줘서 정확히 수정.
- **패치 지시서에서 "어느 `Keyword(...)`인지" 모호했던 사고**: `main.py`에 `Keyword(...)` 생성 지점이 여러 곳(`create_genre`, `_collect_single_keyword`, `select_preferred_genres`)이라 헷갈렸음. **이후로는 그 지점에만 있는 고유한 문자열(에러 메시지 등)로 `grep` 앵커를 잡아서 정확히 짚어주는 방식으로 전환** — 효과 있었음, 계속 유지 권장.
- **대규모 파일(main.py) 패치 전략**: main.py는 너무 커서 전체를 기억만으로 재작성하면 위험 판단 → **분류 관련 부분만 정밀 타격(Find/Replace) + 나머지는 전혀 안 건드리는 전략**으로 전환. 반면 크기가 작고 전체 내용을 확신할 수 있는 파일(`models.py`, `tagging.py`, `personalization.py`)은 **전체 재생성** 방식 유지. **파일 크기/확신도에 따라 전략을 다르게 가져가는 게 맞다는 원칙 재확인.**

---

## 4. 파일 인벤토리 (06번 이후 변경분)

| 파일 | 이번 세션에서 한 일 | 상태 |
|---|---|---|
| `models.py` | Tag 5종 테이블, `Source.tag_id`, `Keyword.tag_id`/`search_query`/`language`, `InteractionSignal.tag_id`/`major_category` | 적용 완료 |
| `tagging.py` | 신규 생성 (분류 로직 유일 원천) | 적용 완료 |
| `collectors.py` | import, 다중태그 부여 2곳, BlockList `category` 제거, 한글 로케일 분기(`_build_keyword_search_url`, `collect_for_keyword`) | 적용 완료 |
| `scheduler.py` | import, `seed_manual_sources`/`_promote_candidate`의 `category`→`tag_id` | 적용 완료 |
| `main.py` | import, lifespan(마이그레이션 3종 추가/제거), `/stats/keywords`, `/admin/backfill-categories` 삭제, `/articles`, `/genres`, `/genres/select`(신규), `/sources`, `/sources/evaluation`, `_collect_single_keyword`(한글 감지+번역 포함), `/scheduler/resume`(즉시 틱), `/scheduler/pause`(즉시 취소) | 적용 완료 |
| `personalization.py` | 전체 교체(Tag 기반) + `store_tag_preference()` 신규 | 적용 완료 |
| `personalization_taxonomy.py`, `taxonomy.py` | 삭제 | 적용 완료 |
| `generators/text/main.py` | import, `_classify_and_prepare_keyword`(대분류 재사용 유도), `_trigger_background_collection` | 적용 완료 |
| `model_router.py` | `propose_taxonomy`, `translate_keyword_en` 프로필 추가 | 적용 완료 |
| `migrate_db.py` | `migrate_keywords()`(search_query), `migrate_keywords_language()`(language) 신규 | 적용 완료 |
| `job_control.py` | `_paused2` 오타 수정 | 적용 완료 |
| `hf-frontend/src/App.jsx` | 그룹핑/필터 3함수(`major_category` 기준), BlockList 판별(`source_type` 기준), `isSensitiveCategory` 시그니처 변경 + 호출부 2곳, `<GenrePreferenceSelector />` 연결 | 적용 완료 |
| `hf-frontend/src/GenrePreferenceSelector.jsx` | 신규 생성 | 적용 완료 |

---

## 5. 완료된 기능 (06번 이후 추가분)

- [x] 분류 체계 통합(Tag 시스템) 전체 구현 및 DB 재생성
- [x] 다중 태그 부여(기사 하나에 여러 태그 가능)
- [x] 검색어/분류명 분리(`search_query` vs `name`)
- [x] 재개 버튼 — 즉시 1회 점검 트리거
- [x] 중지 버튼 — 진행 중인 틱에도 취소 신호 전송
- [x] 선호 장르 선택 UI(체크박스 22개 + 직접입력) + 개인화 신호 동시 기록
- [x] 한글 검색 지원(검색은 한글, 분류명은 영어로 통일)

## 6. 미해결/보류 항목

- [ ] `taxonomy.py`/`personalization_taxonomy.py` 삭제 후 **실제로 서버가 에러 없이 뜨는지 최종 검증 안 됨** — 다음 세션에서 재확인 필요
- [ ] `Tag.sensitive` 필드가 `/genres`, `/genres/select` API에서 아직 설정 불가 (항상 `False`로 등록됨) — Politics/Economy 계열 태그는 나중에 DB에서 직접 `UPDATE tags SET sensitive=1 WHERE major_category IN ('Politics','Economy')` 하거나, API에 파라미터 추가 필요
- [ ] `TagRelation`(태그 간 연관성 그래프)은 스키마만 있고 **아직 아무 데이터도 없음, 활용 로직도 미구현** — `ARCHITECTURE_tagging_and_retrieval.md`의 로드맵 C~E단계(수동 큐레이션, co-occurrence 집계, 검색 확장)는 전부 다음 단계
- [ ] 번역 학습 데이터 export 시, 한글 원문으로 들어온 기사(`Keyword.language="ko"`)를 자동 제외하는 필터 — 그 export 스크립트를 실제로 만드는 시점에 반영하기로 함 (지금 당장 코드 없음)

---

## 7. 다음 세션 최우선 작업 — 번역 버튼 양방향화

### 요구사항 (사용자 최종 확인)

지금 "번역" 버튼은 (한글 검색 지원 이전 설계 그대로) **항상 영어 원문 → 한글 번역**만 가정하고 있다. 한글 검색 지원이 추가된 지금은 **한글 원문 기사도 DB에 들어오므로, 번역 버튼이 원문 언어를 감지해서 반대 언어로 번역**해야 한다:

- 기사 원문이 **영어**면 → 한글로 번역 (기존과 동일)
- 기사 원문이 **한글**이면 → **영어로 번역** (신규)

### 영향 범위 예상 (다음 세션에서 확인 필요)

- `main.py`의 `study_translate_article()`(동기), `study_translate_article_stream()`(SSE 스트리밍) — 둘 다 지금은 "영어 원문 → 한국어" 고정 프롬프트/문장분리 로직을 갖고 있을 가능성이 높음. 원문 언어 감지 로직 추가 필요 (이번 세션에서 만든 `_contains_hangul()` 재사용 가능)
- `model_router.py`의 `translate_literal`/`translate_natural`/`translate_sentence_literal`/`translate_sentence_natural` 프로필들 — 방향이 반대(한→영)일 때도 같은 프로필을 재사용할지, 별도 프로필이 필요한지 검토 필요
- `_segment_article_for_translation()`(문장 분리 로직) — 한글 문장 경계 인식이 영어와 다를 수 있어 확인 필요 (기존 `_SENTENCE_BOUNDARY` 정규식이 영어 전제로 작성됐을 가능성)
- `Translation` 테이블의 `mode`("literal"/"natural") 필드는 언어 방향과 무관하게 그대로 유지 가능해 보이나 재확인 필요
- 프론트엔드(`ArticleCard.jsx`)의 번역 버튼 UI/라벨("🌐 번역", "🇰🇷 한글보기" 등)이 "항상 한→영 대조"를 전제로 한 문구라, 방향에 따라 라벨이 달라져야 할 수 있음(예: 한글 원문 기사는 "🇺🇸 영어보기"가 되어야 함)

### 진행 순서 제안

1. 원문 언어 감지(`_contains_hangul()` 재사용) → 번역 방향 결정
2. `model_router.py` 프로필 재사용 가능 여부 확인, 필요시 반대 방향 프로필 추가
3. `main.py`의 두 번역 엔드포인트에 방향 분기 추가
4. `ArticleCard.jsx`의 버튼 라벨/한글보기 로직을 방향에 맞게 조정
5. 실제 한글 원문 기사로 테스트 (한글 검색으로 수집한 기사 필요 — 6번 미해결 항목 중 한글 검색 자체가 잘 작동하는지부터 먼저 확인 후 진행)

---

## 8. 새 대화 시작 시 사용법

1. 이 문서(`PROJECT_STATUS_07_...md`) + `PROJECT_STATUS_06_chat_and_taxonomy_redesign.md` + `ARCHITECTURE_tagging_and_retrieval.md`를 함께 첨부
2. **먼저 6번(미해결 항목)부터 검증** — 특히 서버가 에러 없이 뜨는지, 한글 검색이 실제로 잘 작동하는지 확인
3. 검증 끝나면 "7번(번역 버튼 양방향화)부터 진행하고 싶다"고 요청
4. 이전 세션들처럼 grep으로 실제 적용 여부를 먼저 확인한 뒤 새 작업을 시작하는 방식 유지 권장
