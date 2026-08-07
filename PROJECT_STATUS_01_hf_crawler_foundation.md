# PROJECT_STATUS_01 — hf_crawler 기반 구축 (수집/정제/번역/편집 플랫폼)

> 관련 마스터플랜 장: Part1-4(Skills), Part1-7(전처리 파이프라인), Part1-9(저장/백업)
> 시작일: 2026-08-02 (KST) / 마지막 갱신: 2026-08-07 (KST)
> 이전 단계: 없음 (최초 단계)

---

## 1. 이번 단계의 목표

- 이름: `hf_crawler`(백엔드) + `hf-frontend`(프론트엔드, Vite+React)
- 목적: 외부 RSS/키워드 기반으로 기사를 수집 → 로컬 Ollama LLM으로 정제/번역 →
  Typora 스타일 에디터로 편집 → SQLite(구조화 데이터)와 로컬 파일시스템(대용량/개인 문서)에
  분리 저장 → 향후 RAG 기반 보고서 생성, 동영상/음악/이미지 수집으로 확장 예정.
- 하드웨어: M1 Max, 32GB, Ollama 0.32.5, MLX 가속 확인됨.

## 2. 핵심 아키텍처 결정

1. **모델 라우팅 분리**: `model_router.py`가 작업 성격별로 LIGHT(`qwen3.5:9b`, 상시 상주)/
   HEAVY(`qwen3.5:35b-a3b-nvfp4`, 온디맨드) 티어를 나눔. `think=False`를 API 파라미터로
   반드시 명시 (텍스트 트릭 `/no_think`는 안 먹힘 - 실측으로 확인됨).
2. **Collector 플러그인 패턴**: `BaseCollector` 추상클래스 + `COLLECTOR_REGISTRY`.
   새 미디어 타입(유튜브, 팟캐스트 등) 추가 시 클래스 하나 + 레지스트리 등록 한 줄이면 됨.
   Source/스케줄러/승격/실패 관리 인프라는 미디어 타입과 무관하게 재사용.
3. **저장소 분리 원칙**: 구조화 데이터(기사 메타/본문)는 SQLite, 대용량 미디어는
   `media_storage/{type}/{source_id}/{item_id}/`(향후), 개인 문서는 `~/Documents/AI-Vault`
   (Typora 등 외부 에디터와 직접 호환, DB와는 무관한 별도 스냅샷).
4. **ML 학습 대비**: `Article.raw_content`(원본 영구 보존, 정제해도 안 건드림),
   `Translation` 테이블(번역 이력 영구 저장), 모든 신규 테이블에 `origin`/`model_used`/
   `created_at` 공통 패턴.
5. **키워드 기반 자동 소스 발굴**: 키워드 검색(Google 뉴스 RSS) → 등장하는 출처(도메인)를
   `CandidateSource`로 추적 → 같은 출처가 3회 이상 등장하면 `Source` 테이블에
   `auto_promoted`로 자동 승격 → 이후 독립적인 주기로 고정 수집. 3회 연속 실패하면
   `status=failing`으로 표시, 삭제는 사용자 판단.
6. **스케줄러**: 고정 3시간 주기 대신, 짧은 틱(`SchedulerConfig.tick_minutes`, 사용자 설정 가능)
   마다 깨어나서 각 Source/Keyword가 "자기 interval_hours를 넘겼는지" 개별 판단.
7. **번역 파이프라인 재설계 (2026-08-07)**: 원래는 LLM 한 번 호출로 전체 기사를 번역하면서
   "영어 원문 그대로 반복 → 한글 번역" 형식까지 함께 맡겼으나, 로컬 경량 모델(9b)이 그
   형식 지시를 자주 빼먹는 문제가 있어, **문장 분리는 Python이 직접 담당**하고 LLM에게는
   "문장 하나만 한국어로 번역"이라는 단순 작업만 맡기는 구조로 전환. 영어 원문 줄이
   구조적으로 누락될 수 없게 됨 (`_segment_article_for_translation`,
   `_build_sentence_translation_system_prompt`, `model_router.py`의
   `translate_sentence_literal`/`translate_sentence_natural` 프로필).

## 3. 겪은 버그와 교훈

### 8/2~8/3 (최초 구현)
- **`think=False`를 API 파라미터로 안 주면** thinking이 안 꺼짐 (텍스트 트릭 무효).
- **`asyncio.wait_for()`를 async generator의 `__anext__()`에 직접 걸면 위험** — 타임아웃 취소가
  제너레이터를 손상시킴. 대신 `asyncio.Queue.get()`에 타임아웃을 걸 것 (안전하게 재시도 가능).
- **SQLModel의 `create_all()`은 기존 테이블에 새 컬럼을 추가해주지 않음** — 반드시 별도
  마이그레이션(`migrate_db.py`, `PRAGMA table_info` + `ALTER TABLE`)이 필요.
- **`__tablename__`이 복수형으로 명시된 기존 프로젝트 관례**를 따라야 함 — 외래키는
  실제 테이블명(`articles.id`, `keywords.id`) 기준으로 작성.
- **Python str Enum을 SQLAlchemy가 저장/조회할 때는 `.name`(대문자) 기준**이지 `.value`가
  아님 — 마이그레이션 DEFAULT 값도 대문자로 맞춰야 함 (`'RAW_CRAWL'`, 소문자 아님).
- **`ollama ps`의 GPU% 는 "상주 여부"이지 "실시간 연산 여부"가 아님** — `keep_alive=-1`로
  상주 모델은 항상 100%로 보여서 의미 없음. `model_router.is_generating()`으로 직접 추적해야 함.
- **LLM은 정확한 개수(줄 수, 글자 수)를 못 셈** — 문단 길이 제한 등은 후처리(파이썬 코드)로
  강제해야 함, LLM에게 맡기지 말 것.
- **main.py를 여러 번 반복 수정하면서 함수가 중복 정의되는 사고가 잦았음** — 항상 `grep -n
  "def 함수명"`으로 중복 여부를 먼저 확인하는 습관 권장.

### 8/7 (검증 대기 중 발견된 버그, 이번 세션에서 수정)
- **프론트/백엔드 필드명 불일치**: `App.jsx`가 존재하지 않는 `systemStats.current_activities`를
  참조하고 있어서, 백엔드가 `activity: {requests, components}`로 정상 응답해도 구성요소
  패널이 항상 "대기 중"만 표시. → 실제 응답 구조에 맞게 렌더링 로직 수정.
- **stale closure로 번역 스트리밍이 완료 시점에만 화면에 나타남**: SSE `onmessage` 콜백이
  클릭 시점의 옛 `articleStates`를 클로저로 캡처해서, 매 청크마다 최신 누적값이 아니라
  "시작 시점 빈 문자열 + 방금 온 청크"로 계속 덮어써짐. → 이미 정의돼 있던
  `APPEND_TRANSLATED` 리듀서 액션을 실제로 사용하도록 수정.
- **키워드별 현황 카운트 ≠ 실제 목록 건수**: `/stats/keywords`(건수)는 카테고리별 점수를
  매겨 "베스트 카테고리 1개만" 채택하는 엄격한 기준을 쓰고, `/articles?keyword=`(목록)는
  카테고리 키워드 아무거나 하나라도 포함되면 통과하는 훨씬 느슨한 기준을 써서 숫자가
  어긋남. → `_score_categories_for_article`/`_best_category_for_article` 공용 함수로
  두 엔드포인트의 판정 로직을 통일.
- **쿠키/개인정보 동의 배너가 본문으로 그대로 저장됨**: Crawl4AI가 "성공"으로 보고해도,
  CMP(Consent Management Platform)가 실제 본문을 가리는 사이트에서는 동의 배너 문구가
  밀도 기준을 통과해 본문처럼 저장될 수 있음. → `_looks_like_consent_boilerplate()`
  휴리스틱 추가, 걸리면 `raw_markdown`으로 재시도 후 그래도 배너면 실패 처리.
- **정의만 되고 안 쓰이던 `is_crawl_failure()`**: 크롤링 타임아웃/에러/추출 실패가 나도
  그대로 DB에 저장되고 있었음 (원래 설계 의도와 다름). → `collectors.py`의 두 Collector
  모두에 실제로 연결, 실패 시 그 항목만 조용히 건너뜀.
- **키워드 검색이 한국어 기사를 수집**: Google 뉴스 검색 URL에 `hl=ko&gl=KR&ceid=KR:ko`가
  박혀 있어서, "번역 학습 목적상 해외(영어) 소스만"이라는 원래 설계 의도와 달리 한국어
  기사가 섞여 들어옴 (원문이 한국어라 번역 시 한글만 나오는 버그의 원인이기도 했음). →
  `hl=en-US&gl=US&ceid=US:en`으로 수정.
- **Google 뉴스 RSS `<link>`가 리다이렉트 래퍼 URL**: `news.google.com/rss/articles/...`를
  그대로 크롤링하면 실제 기사 대신 구글 자체 안내 화면만 잡혀서, `is_crawl_failure()`
  연결 이후 키워드 파이프라인이 거의 0건만 수집되는 부작용 발생. → `_resolve_real_url()`로
  표준 HTTP 리다이렉트를 먼저 따라가 실제 발행사 URL로 풀어낸 뒤 크롤링/저장.
  ⚠️ 구글이 표준 302로 응답한다는 전제의 수정이라, 실사용 중 여전히 0건이면 JS 기반
  동의 화면 우회(쿠키 주입 등)가 추가로 필요할 수 있음 — 미검증.
- **non-daemon 스레드 때문에 Ctrl+C가 안 먹힘**: `loop.run_in_executor(None, ...)`(asyncio
  기본 실행기)와 `concurrent.futures.ThreadPoolExecutor`는 daemon이 아니라서, 인터프리터
  종료 시 `atexit`이 진행 중인 작업(번역 생성, 크롤링)이 끝날 때까지 프로세스를 붙잡음.
  → `model_router.py`/`content_utils.py` 모두 `threading.Thread(daemon=True)`로 직접
  띄우는 방식으로 교체.
- **CATEGORY_CONFIG/TARGET_SOURCES가 한국어로 하드코딩**: 수집되는 기사가 전부 영어인데
  카테고리 키워드가 한국어라 죽은 코드였음. → 전부 영어로 재작성.
  ⚠️ 기존에 이미 DB에 있는 `Source.name`(예: `"[정치] Politico"`)은 자동으로 안 바뀜 —
  카테고리 점수의 보너스(+10점) 계산에 소소한 영향, 완전히 통일하려면 기존 소스 삭제 후
  재시딩 또는 수동 `UPDATE` 필요 (아직 미실행).

## 4. 파일 인벤토리

| 파일 | 역할 | 상태 |
|---|---|---|
| `main.py` | FastAPI 엔트리포인트, 전체 API 엔드포인트 | 재작업 완료 (검증 대기) |
| `models.py` | 전체 DB 스키마 (SQLModel) | 완료 (이번 세션엔 미첨부 — 다음 대화 땐 추가 권장) |
| `database.py` | DB 엔진/세션 설정 | 완료 (이번 세션엔 미첨부 — 다음 대화 땐 추가 권장) |
| `content_utils.py` | 크롤링/정제 순수 함수 | 재작업 완료 (검증 대기) |
| `collectors.py` | Collector 플러그인 (RSS, Google뉴스검색) | 재작업 완료 (검증 대기) |
| `scheduler.py` | 틱 기반 스케줄러, 승격/실패 로직 | 재작업 완료 (검증 대기) |
| `migrate_db.py` | 기존 테이블 컬럼 마이그레이션 | 완료 |
| `model_router.py` | LLM 티어 라우팅, think 제어, 스트리밍 | 재작업 완료 (검증 대기) |
| `activity_tracker.py` | 구성요소별 실시간 활동 상태 추적 | 완료 |
| `hf-frontend/src/App.jsx` | 메인 React 컴포넌트 | 재작업 완료 (검증 대기) |
| `hf-frontend/src/ArticleCard.jsx` | 기사 카드 (정제/번역/편집/Vault 저장) | 재작업 완료 (검증 대기) |
| `hf-frontend/src/MarkdownEditor.jsx` | Crepe 기반 WYSIWYG 에디터, 이미지 업로드 | 완료 |
| `hf-frontend/src/App.css` | 스타일 | 지속 추가 중 |
| `requirements.txt` | 백엔드 의존성 목록 | 미생성 — 다음 대화 전 생성 권장 |

## 5. 완료된 기능

- [x] Ollama+qwen3.5 조합 검증, M1 Max 실측 벤치마크
- [x] 모델 라우터 (LIGHT/HEAVY 티어, think 제어, keep_alive 정책)
- [x] 번역 SSE 스트리밍 안정화 (하트비트, 큐 기반 타임아웃)
- [x] 문서 정제 파이프라인 (정규식 1차 + LLM 2차 + 문단 강제 재구성)
- [x] TARGET_SOURCES 해외 소스로 정리 (한국어 소스 제거)
- [x] Typora 스타일 에디터(Milkdown/Crepe) + 이미지 업로드
- [x] 카드 목록 - 펼치기/접기, 화면 이탈 시 자동 접힘(편집 중엔 예외)
- [x] Collector 플러그인 아키텍처, Source/Keyword DB화
- [x] 키워드 등록 → 즉시 수집 → 백그라운드 반복 수집
- [x] 출처 자동 승격/실패 관리, 소스 관리 패널(추가/삭제)
- [x] 스케줄러 틱 간격 사용자 설정
- [x] 개인저장방(Vault) - DB 기본 저장 + 선택적 파일 내보내기
- [x] GPU 사용량 표시 정확도 개선
- [x] 플랫폼 구성요소 안내 패널
- [x] 문서 최상단 고정(정제/번역/편집 클릭 시), 번역 완료 후 한글보기 버튼
- [x] 번역 파이프라인을 문장 단위로 재설계 (영어 원문 누락 구조적 방지)

## 6. 남은 작업 / 다음 단계 후보

- [ ] RAG 파이프라인 구축 (LanceDB 등 벡터DB, HEAVY 티어 모델과 연계한 보고서 생성)
- [ ] 동영상/음악/이미지 Collector 실제 구현 (YouTubeCollector 등 - 인터페이스만 준비된 상태)
- [ ] Vault 폴더 브라우징 UI ("내 채널" 화면 미구현, 지금은 export만 가능)
- [ ] `run_collection_job()`/`TARGET_SOURCES` 관련 죽은 코드 정리
- [ ] CandidateSource 관리 UI (사용자가 후보 목록을 직접 보는 화면 없음)
- [ ] ML 학습용 데이터 export 스크립트 (원본/정제본/번역 쌍 JSONL 등)
- [ ] 기존 DB의 `Source.name` 한국어 라벨(`[정치]` 등)을 영어로 마이그레이션
- [ ] Google 뉴스 리다이렉트 해석(`_resolve_real_url`)이 실제로 동작하는지 실사용 검증 —
      안 되면 JS 기반 동의 화면 우회(쿠키 주입) 후속 조치 필요

## 7. 새 대화 시작 시 사용법

1. `PROJECT_STATUS_INDEX.md` + 이 파일을 첨부
2. 가능하면 `models.py`, `database.py`, `requirements.txt`도 함께 첨부 (지금까지 필드
   정합성 검증이 막혔던 부분)
3. "이어서 [다음 작업]을 하고 싶다"고 요청

## 8. 검증 체크리스트 (다음 단계 진행 전 필수)

**아래 항목을 실제로 하나씩 실행/확인하기 전까지는 다음 번호(02)의 새 단계를 시작하지 않습니다.**

- [ ] 구성요소 사용량 패널이 실제 작업(수집/번역) 중 상태를 정확히 반영하는지
- [ ] 정제/번역/편집 버튼 클릭 시 해당 문서가 목록 최상단으로 이동/유지되는지
- [ ] 번역 버튼 클릭 시 문장이 완성되는 대로 즉시 화면에 이어서 렌더링되는지 (완료까지
      기다리지 않는지)
- [ ] 번역 결과에 영어 원문과 한글 번역이 항상 번갈아 나오는지 (한글만 나오는 경우가
      재발하지 않는지, 특히 기존에 문제였던 여행 키워드 기사로 재확인)
- [ ] 한글보기 버튼이 정상적으로 한글 문장만 필터링해서 보여주는지
- [ ] 키워드별 현황 버튼에 찍힌 건수와, 클릭해서 열리는 실제 목록 건수가 일치하는지
- [ ] 쿠키/동의 배너 텍스트가 본문으로 잘못 저장되는 사례가 재발하지 않는지
- [ ] 번역 진행 중 Ctrl+C를 눌렀을 때 프로세스가 즉시 종료되는지
- [ ] 새로 등록한 키워드가 한국어가 아닌 영어 기사를 수집하는지
- [ ] 키워드 파이프라인이 0건이 아니라 실제로 기사를 수집하는지 (Google 뉴스 리다이렉트
      해석 검증)
- [ ] `TARGET_SOURCES`/`CATEGORY_CONFIG`의 영어 라벨이 실제 화면(키워드별 현황)에 정상
      반영됐는지

전부 체크되면:
1. 위 표의 미체크 항목을 모두 체크
2. 이 문서 최상단에 `> 검증 완료: YYYY-MM-DD (KST)` 한 줄 추가
3. `PROJECT_STATUS_INDEX.md`의 01단계 행 상태를 `검증 완료 ✅`로 갱신

검증 중 발견됐지만 지금 단계에서 해결하지 않기로 한 문제가 있다면, 위 "6. 남은 작업"에
사유와 함께 기록하고 넘어갑니다 (조용히 넘어가지 않기).