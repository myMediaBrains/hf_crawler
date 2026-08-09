# PROJECT_STATUS_06 — 채팅 서비스 구축 + 성능 위기 대응 + 분류체계 통합 재설계 착수

> 관련 마스터플랜 장: 해당사항 없음 (대화 중 도출된 요구사항의 연속)
> 시작일: 2026-08-09 (KST) / 마지막 갱신: 2026-08-09 (KST)
> 이전 단계: `PROJECT_STATUS_05_priority_scheduling.md`
> 관련 설계 문서(이번 세션에서 별도 파일로 생성됨, 함께 첨부 권장):
> - `ARCHITECTURE_personalized_chat.md` (단문/장문 2단계 채팅 설계)
> - `ARCHITECTURE_personalization_intelligence.md` (취향 축적/자가개선/군집화/푸시 - Phase 2~3은 보류 중)
> - `ARCHITECTURE_tagging_and_retrieval.md` (다중 태그 + 관계 그래프 설계 — **다음 단계에서 통합 재설계로 흡수될 예정**)
> - `PATCH_user_profile_v2.md` (사용자 프로필, 적용 완료)
> - `PATCH_chat_phase1.md` (단문/장문 채팅 + 배송, 적용 완료)

---

## 1. 이번 단계의 목표

01~05단계로 hf_crawler(수집)+텍스트 생성기(채팅)까지 기반이 갖춰진 상태에서, 이번 세션은 3막으로 진행됐다.

1. **개인화 대화 서비스 실제 구축** — 사용자 프로필 등록, 단문(≤300자, HEAVY 티어로 깊이 고민 후 압축)/장문(≥1000자) 2단계 채팅, ntfy/mailto 배송, 대화 기반 취향 신호 자동 축적.
2. **실사용 중 터진 성능 위기 대응** — 채팅을 실제로 몇 번 쓰자마자 백엔드가 통째로 먹통이 되는 사고가 여러 차례 발생, 원인을 하나씩 벗겨내며 진단.
3. **분류 체계 자체의 구조적 결함 발견 → 근본 재설계 착수 결정** — 성능 위기의 근본 원인 중 하나가 "분류 시스템이 4개나 따로 존재해서 서로 안 맞고 매번 무겁게 재계산된다"는 것으로 드러남. 데이터를 전부 지우고 통합 스키마로 재출발하기로 **방금 합의**했고, 실제 구현은 **다음 세션**으로 넘어감.

---

## 2. 핵심 아키텍처 결정 (1막 — 채팅 서비스)

1. **단문/장문 2단계, 둘 다 HEAVY 티어** — 사용자가 "100자로 줄이라는 게 아니라 충분히 고민한 뒤 압축한 결론을 달라"고 명확히 요구. `personalized_teaser`(신규 TASK_PROFILE, HEAVY, num_predict=550→약 300자) / 장문은 기존 `rag_report`(HEAVY, "최소 1000자 이상" 프롬프트 명시) 재사용. LIGHT였던 기존 `personalized_qa`는 레거시 `/generate`용으로 그대로 둠.
2. **근거 없음을 코드로 판정, LLM에 안 물어봄** — `retrieval.py`의 `get_context_articles()`가 `(articles, matched: bool)` 튜플을 반환하도록 변경. `matched=False`(실제 연관 근거를 못 찾음)면 LLM을 아예 호출하지 않고 "1시간 후 다시 물어봐주세요" 안내 + 백그라운드 자동 수집(`/collect/deep-incremental`) 트리거.
3. **키워드 매칭 2단계**: 1차 부분 문자열 매칭 → 실패 시 2차로 LLM에게 "등록된 키워드 중 의미상 관련 있는 것"을 고르게 함(`_match_keyword_semantically()`, LIGHT `classify` 프로필). 판정은 완전일치가 아니라 "LLM 출력에 키워드 문자열이 포함되는가"로 관대하게(LLM이 지시를 완벽히 안 지켜도 안정적으로 매칭되도록).
4. **배송은 무자격증명으로**: ntfy.sh(가입/키 불필요, 실제 발송) + 이메일은 `mailto:` 링크만 서버가 만들어 반환(서버가 직접 SMTP 발송 안 함).
5. **취향 축적 3단계 가중치**: 질문 자체(1.0) < 장문 확장 클릭(1.5) < 배송 클릭(2.5). `classify_and_store()`를 `/chat/short`, `/chat/expand`, `/deliver` 세 곳에 연결.
6. **대화 맥락**: `TextGeneration`에 `conversation_id`/`stage`(short|long)/`parent_id` 추가, 새로고침해도 이어지도록 프론트 `localStorage`에 저장.

## 3. 핵심 아키텍처 결정 (2막 — 성능 위기 대응)

7. **`job_control` 기본값을 "일시정지"로 반전**: 예전엔 서버 재시작 때마다 `_paused=False`로 리셋되어 매번 백그라운드 수집이 자동으로 다시 돌았음. `_paused=True` 기본값으로 바꿔, **사용자가 명시적으로 재개하기 전엔 절대 스스로 안 돎**.
8. **"사람 vs 시스템" 우선권 양보**: `job_control.start_job_with_priority()` 신규 — 백그라운드 틱(`BACKGROUND_TICK_JOB_NAME`)이 돌고 있을 때 사람이 수동 작업을 걸면, 틱에 취소 신호를 보내고 짧게 기다렸다가 사람 작업을 우선 진입시킴. **사람 vs 사람** 충돌은 기존처럼 그대로 거부(동시쓰기 충돌 재발 방지 원칙 유지).
9. **스케줄러 틱에 상한 추가**: 키워드는 기존에 이미 `MAX_KEYWORDS_PER_TICK=5`가 있었으나, **소스 쪽(`_tick_sources()`)엔 상한이 아예 없었던 게 이번에 발견된 진짜 문제 중 하나**. `MAX_SOURCES_PER_TICK=8` 신규 추가. 또한 백그라운드 틱의 키워드당 크롤링 건수도 `KEYWORD_TICK_MAX_ENTRIES=10`으로 축소(사용자가 누르는 "실시간 수집" 버튼은 여전히 20건 유지).
10. **채팅 자동수집도 가볍게**: `max_entries=5`로 제한, `timeout=180`(fire-and-forget이라 타임아웃 늘려도 채팅 응답엔 영향 없음).
11. **`/stats/keywords` 근본 최적화**: 기사 저장 시점에 `Article.category`를 미리 계산해서 저장(기존엔 매번 기사 전체를 파이썬으로 순회하며 정규식 재매칭 — 3,600건 기준 실측 **116초**). 조회는 이제 SQL `GROUP BY` 한 방. 정규식도 서버 시작 시 1회만 컴파일하도록 `_COMPILED_CATEGORY_PATTERNS` 추가. 캐시(처음 8초→60초로 연장)와 프론트 폴링 주기(10초→30초)도 같이 조정.
12. **`/articles` 응답을 미리보기로 축소**: 목록 조회 시 본문을 400자로 잘라 보내고(`content_truncated` 플래그), 펼치기/편집 시에만 `GET /articles/{id}/full`로 전체 본문을 따로 불러오도록 변경. 삭제도 낙관적 업데이트(서버 응답 기다리지 않고 화면에서 즉시 제거)로 체감 지연 제거.
13. **서버 종료 시 진행 중 작업에 취소 신호 전송**: `scheduler.shutdown(wait=False)`만으로는 부족했음 — 종료 직전 `job_control.cancel_current_job()`을 호출해 크롤링 루프가 스스로 멈출 계기를 줌 (Ctrl+C 반응 속도 개선, 완전 즉시는 아니고 최대 30초 이내로 단축).

## 4. 핵심 발견 (3막 — 분류 체계 결함)

14. **분류 시스템이 4개나 따로 존재**: `CATEGORY_CONFIG`(main.py, `Article.category`) / `SUBCATEGORY_CONFIG`(personalization_taxonomy.py, `InteractionSignal.subcategory`) / `TAXONOMY`(taxonomy.py, `Keyword.major_category`/`mid_category`) / 이번에 설계만 하고 미적용인 `Tag`/`ArticleTag`/`TagRelation`(`ARCHITECTURE_tagging_and_retrieval.md`). 같은 질문("이 기사가 무슨 주제인가")에 서로 다른 4개의 답이 존재하고 서로 동기화가 안 됨.
15. **채팅 자동수집 키워드 오염 문제**: "trending food recipes, popular dishes now, viral foods today" 같은 자연어 검색 문구가 그대로 `Keyword.name`(분류용 필드)에 박혀버림. "검색어(자유 문구)"와 "분류 태그(짧고 정규화)"가 같은 필드를 겸용한 게 원인. `search_query` 필드 분리 + LLM이 기존 대분류 목록을 먼저 보고 재사용을 우선하도록 하는 패치안까지는 설계했으나, **사용자가 "서두르지 말고 근본적으로 가자"고 판단해 이 패치는 적용 보류**.
16. **최종 결정**: 위 4개 분류 시스템을 **`Tag` 하나로 통합**하는 근본 재설계에 들어가기로 합의. 데이터(현재 기사 3,600여 건)는 **전부 삭제하고 새로 시작해도 무관**하다는 사용자 확인을 받음. 크롤링/채팅/동시성 제어 등 다른 레이어는 이미 안정화됐다고 판단해 손대지 않고, **분류 체계만** 근본적으로 다시 짬.

## 5. 겪은 버그와 교훈 (이번 세션 — 매우 많음, 재발 방지용으로 상세히 기록)

### 패치 적용 관련
- **Find/Replace 표기가 "교체"인지 "삽입"인지 모호해서 여러 번 헷갈림** → 이후 모든 패치 문서를 🟢삽입/🟡교체로 명시하고 "적용 후 최종 모습"을 함께 제공하는 방식(`PATCH_user_profile_v2.md`)으로 전환. 효과 있었음, 계속 이 방식 유지 권장.
- **줄이 잘려서 콜론(`:`)이 통째로 날아가거나 클래스 정의가 중복되는 사고가 반복됨** (`generators/text/main.py`의 `context_block = ...`, `collectors.py`의 `class GoogleNewsSearchCollector` 중복). **긴 함수/클래스를 패치할 땐 스니펫이 아니라 함수·클래스 전체를 통째로 교체하는 방식이 훨씬 안전**하다는 게 다시 한번 확인됨(01번 문서의 교훈 재확인).
- **`import time` 등 사소한 의존성 추가를 "확인해달라"고 미루고 실제로 안 챙긴 사고** → 500 에러로 이어짐. 앞으로는 Claude가 직접 앞뒤 import를 확인하고 필요 여부를 판단해서 안내하기로 함.

### 성능 진단 과정 (중요 — 다음에 비슷한 증상 나오면 이 순서로 접근)
- **처음엔 "DB 잠금"으로 오판**했다가 → **GIL/CPU 문제(정규식 재컴파일)**로 재진단 → 그래도 재현되자 **"HMR 잔재 setInterval 중복"**으로 오판(브라우저 완전 재시작 후에도 재현되어 기각) → **캐시 TTL(8초) < 폴링 주기(10초)라 캐시가 사실상 무효화**되는 게 진짜 원인 중 하나로 확인 → 마지막엔 **`curl`조차 응답 없음 → 알고 보니 사용자가 서버를 꺼놓은 상태**였던 해프닝도 있었음.
- **교훈**: 이런 "느려짐/멈춤" 계열 버그는 **직접 시간을 재보는 것**(`time curl ...`)이 가장 빠르고 정확한 진단 수단이었음. 실측 없이 추측만으로 여러 차례 헛다리를 짚었음 — 다음에도 증상이 애매하면 추측보다 실측을 먼저 요청할 것.
- **좀비 프로세스 확인 습관 필요**: `lsof -i :PORT`로 실제 누가 포트를 물고 있는지 먼저 확인하는 습관이 여러 혼선을 줄여줌. 이번에 브라우저 탭 하나가 8000번에 연결 6개를 물고 있던 것도 이걸로 확인함.
- **`GET /articles`가 페이지네이션 없이 본문 전체를 매번 반환**하고 있었음 — 기사가 늘면서 이것도 체감 지연의 원인이었음(2번 항목에서 미리보기+지연로딩으로 해결).

### 설계 관련
- **`_score_categories_for_article()`이 사실 모든 카테고리 점수를 계산해놓고 `max()`로 1등만 취하고 버리고 있었다**는 걸 뒤늦게 발견 — 다중 태그 지원에 필요한 계산은 이미 존재했다는 뜻. (`ARCHITECTURE_tagging_and_retrieval.md` 참고)
- **"검색어"와 "분류 태그"를 같은 필드에 욱여넣으면 안 된다**는 걸 실사용으로 체감(15번 항목). 표준 검색엔진 패턴("질의 정규화")과 동일한 문제였음.
- **분류 체계는 하나로 통합돼야 한다** — 여러 개로 쪼개져 있으면 매번 어긋나고, 어긋난 걸 고치는 패치가 계속 다른 패치를 필요로 하는 악순환이 생김.

## 6. 업계 리서치 결과 (참고용, `ARCHITECTURE_tagging_and_retrieval.md`에 상세)

- 넷플릭스: Feature Store(온라인/오프라인 정합성) + 콘텐츠 임베딩 파이프라인 + **Entertainment Knowledge Graph**(태그 관계 그래프와 동일 아이디어) + 2단계 검색(retrieval→ranking).
- 아마존: SageMaker Feature Store(온라인/오프라인).
- 참고할 만한 오픈소스: **mem0**(21,000+ 스타, 벡터+키값+그래프 하이브리드, 로컬 self-host 가능 — 단 "대화 속 사용자 사실 기억" 용도지 "대량 코퍼스 검색" 용도는 아님), Qdrant/Weaviate(하이브리드 검색 지원 벡터DB), Gorse(전통 추천엔진, 사용자 여러 명 생기면 참고).
- **실측 벤치마크**: 하이브리드(키워드+벡터) 검색이 벡터 단독보다 꾸준히 우세(91% vs 78% recall@10 등 여러 사례). **순수 키워드/태그 방식이 "뒤처진 방식"이 아니라, 오히려 최적해(하이브리드)의 절반을 이미 갖춘 상태**라는 결론. 지금 만드는 태그 시스템은 나중에 임베딩을 얹어도 버려지지 않고 하이브리드의 "키워드 절반"으로 계속 쓰인다.

## 7. 파일 인벤토리 (이번 세션에서 변경된 것만)

| 파일 | 이번 세션에서 한 일 | 상태 |
|---|---|---|
| `models.py` | `User`, `Delivery` 테이블 신규, `TextGeneration`에 `conversation_id`/`stage`/`parent_id` 추가 | 적용 완료 |
| `personalization.py` | `register_user_and_backfill()`, `user_id` 파라미터 확장 | 적용 완료 |
| `migrate_db.py` | `migrate_interaction_signals`, `migrate_text_generations`, `MAX_SOURCES_PER_TICK` 관련 없음(scheduler.py 소속) | 적용 완료 |
| `main.py` | 사용자 등록 API, `/deliver`, `/stats/keywords`(정규식 사전컴파일+캐시+GROUP BY), `/admin/backfill-categories`, `/articles`(미리보기+`/full` 분리), `import time` 추가, `lifespan()` 종료 시 취소신호 전송 | 적용 완료 |
| `delivery.py` | 신규 파일 — ntfy 발송, mailto 링크 생성 | 적용 완료 |
| `model_router.py` | `personalized_teaser`(단문), `extract_keyword`(미사용으로 폐기 예정), `propose_taxonomy`(제안만 하고 미적용) 프로필 | `personalized_teaser`만 적용 완료 |
| `generators/text/main.py` | `/chat/short`, `/chat/expand`, `/chat/history` 신규, 근거부족 감지+자동수집 트리거, 채팅 응답 분량(300자/1000자) 확대 | 적용 완료 |
| `generators/text/retrieval.py` | `get_context_articles()`가 `(articles, matched)` 튜플 반환, `_match_keyword_semantically()`(2차 LLM 매칭) 추가 | 적용 완료 |
| `job_control.py` | `_paused` 기본값 `True`로 반전, `start_job_with_priority()` 신규 | 적용 완료 |
| `scheduler.py` | `MAX_SOURCES_PER_TICK` 추가, `job_control.BACKGROUND_TICK_JOB_NAME` 참조로 통일 | 적용 완료 |
| `collectors.py` | `max_entries` 파라미터화, 저장 시점 `article.category` 계산(지연 import), `GoogleNewsSearchCollector` 문법 오류 2회 수정 | 적용 완료 (문법 오류 수정 확인 필요 — 8번 참고) |
| `hf-frontend/src/UserRegister.jsx` | 신규 — 제목 우측 작은 배지, uncontrolled input, 낙관적 표시 | 적용 완료 |
| `hf-frontend/src/ChatWindow.jsx` | 신규 — 우측하단 플로팅 버튼, 대기 인디케이터(TypingDots), 배송 버튼 | 적용 완료 |
| `hf-frontend/src/App.jsx` | `<UserRegister/>`/`<ChatWindow/>` 연결, 삭제 낙관적 업데이트, 폴링 주기 조정 | 적용 완료 |
| `Keyword.search_query` 필드 분리 패치 | 설계는 마쳤으나 **미적용** — 3막 결정으로 통합 재설계에 흡수됨 | **보류** |
| `Tag`/`ArticleTag`/`TagRelation` 스키마 | 설계만 완료(`ARCHITECTURE_tagging_and_retrieval.md`) | **미적용, 다음 세션에서 통합 재설계로 진행** |

## 8. 검증 필요 항목 (다음 세션 시작 전 확인 권장)

- [ ] `collectors.py`의 `GoogleNewsSearchCollector` 클래스 중복 정의 수정이 실제로 반영됐는지 (`grep -n "class GoogleNewsSearchCollector" collectors.py` → 정확히 1번만 나와야 함)
- [ ] `job_control.py`의 `_paused=True` 기본값이 실제 파일에 반영됐는지 (`grep -n "_paused = " job_control.py`)
- [ ] `scheduler.py`의 `MAX_SOURCES_PER_TICK` 적용 여부 (`grep -n "MAX_SOURCES_PER_TICK" scheduler.py` → 상수 정의 + `_tick_sources()` 사용처 2곳)
- [ ] `main.py`의 `import time`, `_COMPILED_CATEGORY_PATTERNS`, `/admin/backfill-categories` 반영 여부
- [ ] **`/admin/backfill-categories`를 아직 한 번도 호출 안 했다면, 통합 재설계 전에 굳이 호출할 필요 없음** (곧 스키마 자체가 바뀌므로 — 백필은 새 스키마 확정 후에 새 방식으로 다시 하게 됨)

## 9. 다음 단계 — 분류 체계 통합 재설계 (합의됨, 미착수)

### 9-1. 결정된 방향
- `CATEGORY_CONFIG` / `SUBCATEGORY_CONFIG` / `TAXONOMY` 3개 딕셔너리 기반 시스템을 **폐기**
- `Tag`(계층: `major_category` > `mid_category`, 다중 부여 가능) / `ArticleTag` / `TagRelation` 3개 테이블로 통합
- `Article`, `Keyword`, `InteractionSignal` 전부 이 `Tag` 하나를 참조하도록 재배선
- **기존 데이터(기사 3,600여 건) 전체 삭제 후 새 스키마로 재출발** — 사용자가 명시적으로 동의함
- "검색어(자유 문구)"와 "분류 태그(정규화)"를 처음부터 분리해서 설계 (15번 항목 재발 방지)
- LLM이 새 태그/분류를 자동 생성하되, 기존 목록 재사용을 강하게 유도(taxonomy 무한 증식 방지) + 사용자가 "장르 편집기"에서 관찰/수정하는 기존 패턴 그대로 재사용

### 9-2. 다음 세션에서 할 일 (순서)
1. 통합 `Tag` 스키마 최종안을 **표로 확정** (무엇이 무엇을 대체하는지 명확히) — 코드부터 던지지 않고 설계 문서로 먼저 합의
2. 기존 3개 분류 시스템을 참조하는 코드 위치 전수 파악 (main.py, collectors.py, personalization.py, personalization_taxonomy.py, taxonomy.py, retrieval.py, App.jsx의 장르편집기)
3. DB 백업 후 초기화 방법 확정 (`local_deep_trend.db` → 백업 파일명 정하고 삭제)
4. 새 스키마로 마이그레이션/모델 코드 작성
5. 키워드 2~3개만으로 소규모 검증 → 정상 확인되면 전체 재수집

### 9-3. 새 대화 시작 시 사용법
1. 이 문서(`PROJECT_STATUS_06_...md`) + `ARCHITECTURE_tagging_and_retrieval.md`를 함께 첨부
2. "9-2의 1번(통합 Tag 스키마 최종 설계)부터 이어서 진행하고 싶다"고 요청
3. 가능하면 8번 검증 항목을 먼저 grep으로 확인한 결과도 같이 공유 (이전 패치들이 실제로 다 적용된 상태인지 확정하고 시작하기 위함)
