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
3. **저장소 분리 원칙**: 구조화 데이터(기사 메타/본문)는 SQLite, 대용량 미디어는
   `media_storage/{type}/{source_id}/{item_id}/`(향후), 개인 문서는 `~/Documents/AI-Vault`.
4. **ML 학습 대비**: `Article.raw_content`(원본 영구 보존), `Translation` 테이블(번역 이력
   영구 저장), 모든 신규 테이블에 `origin`/`model_used`/`created_at` 공통 패턴.
5. **키워드 기반 자동 소스 발굴**: 키워드 검색(Google 뉴스 RSS) → 등장 출처를
   `CandidateSource`로 추적 → 3회 이상 등장 시 `Source`로 자동 승격.
6. **스케줄러**: 고정 3시간 주기 대신, 짧은 틱(`SchedulerConfig.tick_minutes`)마다 깨어나서
   각 Source/Keyword가 "자기 interval_hours를 넘겼는지" 개별 판단.
7. **번역 파이프라인 재설계**: 문장 분리는 Python이 전담(`_segment_article_for_translation`),
   LLM에게는 "문장 하나만 번역"이라는 단순 작업만 맡김 → 영어 원문 누락 구조적 방지.
8. **Google 뉴스 URL 디코딩 (신규, 8/7 후반)**: 구글은 `news.google.com/rss/articles/...`를
   표준 HTTP 302가 아니라 자체 `batchexecute` 내부 API로만 실제 URL을 내려준다는 사실을
   실측으로 확인. 직접 리버스엔지니어링하는 대신 검증된 `googlenewsdecoder` 라이브러리
   (`gnewsdecoder`)를 채택해 `collectors.py`에 적용.
9. **협조적 작업 취소 (신규, 8/7 후반)**: `job_control.py`를 신설해 "파이프라인 수집"/
   "검색·등록" 버튼 재클릭 시 실제로 백엔드 수집 루프를 중단할 수 있게 함. 동시에
   **같은 시점엔 오직 하나의 수집 작업만 실행되도록 강제**하는 락 역할도 겸함
   (`start_job()`이 이미 다른 작업이 있으면 `False`를 반환) — SQLite 동시 쓰기 충돌
   ("database is locked") 재발 방지의 핵심 장치.
10. **SQLite 동시성 설정 (신규, 8/7 후반)**: `database.py`에서 커넥션 생성 시
    `PRAGMA journal_mode=WAL`, `busy_timeout=30000`, `synchronous=NORMAL`을 적용.
    읽기(폴링)와 쓰기(크롤링)가 동시에 일어나는 이 앱의 특성상 기본 rollback-journal
    모드로는 충돌이 잦았음.
11. **키워드 파이프라인 수집의 자동 등록 (신규, 8/7 후반)**: "파이프라인 수집" 버튼에
    아직 등록되지 않은 키워드를 입력해도, 더 이상 404로 막지 않고 그 자리에서 자동
    등록 후 즉시 수집하도록 변경 (`_collect_single_keyword`).

## 3. 겪은 버그와 교훈

### 8/2~8/3 (최초 구현)
- **`think=False`를 API 파라미터로 안 주면** thinking이 안 꺼짐 (텍스트 트릭 무효).
- **`asyncio.wait_for()`를 async generator의 `__anext__()`에 직접 걸면 위험** — 대신
  `asyncio.Queue.get()`에 타임아웃을 걸 것.
- **SQLModel의 `create_all()`은 기존 테이블에 새 컬럼을 추가해주지 않음** — 별도
  마이그레이션(`migrate_db.py`) 필요.
- **`__tablename__`은 복수형 관례** — 외래키도 복수형 테이블명 기준.
- **Python str Enum은 SQLAlchemy 저장 시 `.name`(대문자) 기준**, `.value` 아님.
- **`ollama ps`의 GPU%는 "상주 여부"이지 "실시간 연산 여부"가 아님** — 직접 추적 필요.
- **LLM은 정확한 개수(줄 수, 글자 수)를 못 셈** — 후처리(파이썬 코드)로 강제해야 함.
- **함수 중복 정의 사고가 잦았음** — 항상 `grep -n "def 함수명"`으로 확인하는 습관 필요.

### 8/7 세션 전반 (검증 대기 중 발견된 버그)
- **프론트/백엔드 필드명 불일치**: `systemStats.current_activities`(존재 안 함) 참조 →
  구성요소 패널이 항상 "대기 중"만 표시. → 실제 응답 구조에 맞게 수정.
- **stale closure로 번역 스트리밍이 완료 시점에만 화면에 나타남**: SSE `onmessage`가
  옛 상태를 클로저로 캡처 → `APPEND_TRANSLATED` 리듀서 액션으로 해결.
- **키워드별 현황 카운트 ≠ 실제 목록 건수**: 판정 기준이 서로 달랐음 →
  `_score_categories_for_article`/`_best_category_for_article` 공용 함수로 통일.
- **쿠키/동의 배너가 본문으로 그대로 저장됨**: `_looks_like_consent_boilerplate()` 휴리스틱
  추가.
- **`is_crawl_failure()`가 정의만 되고 실제로 안 쓰이고 있었음** → Collector 두 곳에 연결.
- **키워드 검색이 한국어 기사를 수집**: `hl=ko&gl=KR` → `hl=en-US&gl=US`로 수정.
- **Google 뉴스 RSS `<link>`가 리다이렉트 래퍼 URL**: 처음엔 단순 HTTP 리다이렉트 추적으로
  해결 시도했으나 실사용 중 여전히 0건 → 아래 8/7 후반 항목에서 근본 해결.
- **non-daemon 스레드 때문에 Ctrl+C가 안 먹힘** → `threading.Thread(daemon=True)`로 교체.
- **CATEGORY_CONFIG/TARGET_SOURCES가 한국어로 하드코딩** → 전부 영어로 재작성.

### 8/7 세션 후반 (이어서 발견/수정)
- **zero-width space(U+200B) 혼입**: `App.jsx`/`ArticleCard.jsx`의 `<span>` 태그 사이에
  보이지 않는 유니코드 문자가 섞여 있어 JSX 파싱에 영향을 줄 수 있었음(복붙 과정에서
  유입된 것으로 추정). `perl -CSD -i -pe 's/\x{200B}//g'`로 제거, 재발 여부 매 세션 확인 필요.
- **"출처 보기" 버튼이 상태만 바뀌고 실제 렌더링 블록이 아예 없었음**: `showSourceStats`가
  `true`가 돼도 그걸 그리는 JSX가 App.jsx에 존재하지 않아 아무것도 안 보임 → 렌더링 블록
  추가.
- **`_collect_single_keyword()` 호출은 있는데 정의가 없었음**: `/collect/deep-incremental?keyword=`가
  존재하지 않는 함수를 호출해 500(NameError)이 나는 구조였음 → 실제 구현 추가.
- **구글 뉴스 URL이 단순 HTTP 302로 안 풀림**: 구글은 JS/내부 API 기반으로만 실제 URL을
  내려줌 → `googlenewsdecoder`(`gnewsdecoder`) 라이브러리 도입으로 해결, 실사용 로그로
  techcrunch/theverge/cnbc 등 정상 크롤링 확인됨 (Bloomberg/WSJ/Reuters는 PerimeterX/
  DataDome 안티봇에 막힘 — 이건 정상적으로 `is_crawl_failure()`가 걸러내는 별개 이슈).
- **`scheduler.py`에 `run_tick()`/`_tick_sources()`가 각각 두 번 정의됨**: 패치 스니펫을
  파일 중간에 삽입만 하고 기존 정의를 안 지워서 발생. Python은 마지막 정의만 유효하므로,
  **job_control 취소 로직이 감싼 버전이 조용히 무시되고 옛날(취소 불가) 버전만 살아있는
  상태**였음 → 두 버전을 하나로 병합해 제거. 8/2~8/3에 스스로 남긴 "함수 중복 정의 주의"
  교훈이 그대로 재발한 사례 — 파일을 통째로 다시 받아서 처음부터 재작성하는 방식으로만
  재발 방지 가능하다고 판단, 이후 모든 파일 수정은 "스니펫 안내"가 아니라 "완전한 파일
  재생성"으로 전환.
- **SQLite "database is locked"**: 같은 키워드를 두 개의 수집 작업(검색/등록의 재수집 +
  파이프라인 수집)이 동시에 처리하면서 INSERT 충돌 발생. → (a) `database.py`에 WAL 모드
  + `busy_timeout` 적용, (b) `job_control.start_job()`이 이미 실행 중인 작업이 있으면
  `False`를 반환해 동시 실행 자체를 차단하도록 이중 방어.
- **키워드 미등록 시 404로 막던 것 → 자동 등록으로 UX 개선**: "파이프라인 수집"에 새
  키워드를 입력했을 때 "먼저 검색/등록으로 등록해주세요"라고 막던 것을, 그 자리에서
  자동 등록 후 즉시 수집하도록 변경 (사용자 피드백 반영).
- **409(다른 작업 진행 중) 응답이 너무 순간적으로 지나가 사용자가 못 알아챔**: DB 재생성
  직후 전체 소스 초기 수집이 오래 걸리는 동안 사용자가 개별 키워드 수집을 누르면 즉시
  409로 거부되는데, 토스트가 스쳐 지나가듯 짧아 "아무 반응 없음"처럼 보였음 → 409 수신
  시 3초 후 자동 재시도로 완화 (근본적으로는 최초 대량 수집 자체의 소요 시간 단축이
  필요 — 아래 "6. 남은 작업" 참고).

## 4. 파일 인벤토리

| 파일 | 역할 | 상태 |
|---|---|---|
| `main.py` | FastAPI 엔트리포인트, 전체 API 엔드포인트 | 재작업 완료 (검증 대기) |
| `models.py` | 전체 DB 스키마 (SQLModel) | 완료, 8/7 후반 재확인 — 문제없음 |
| `database.py` | DB 엔진/세션 설정 | 재작업 완료 (검증 대기) — WAL/busy_timeout 추가 |
| `content_utils.py` | 크롤링/정제 순수 함수 | 재작업 완료 (검증 대기) |
| `collectors.py` | Collector 플러그인 (RSS, Google뉴스검색) | 재작업 완료 (검증 대기) — googlenewsdecoder 적용 |
| `scheduler.py` | 틱 기반 스케줄러, 승격/실패 로직 | 재작업 완료 (검증 대기) — 중복 함수 정리, job_control 연동 |
| `migrate_db.py` | 기존 테이블 컬럼 마이그레이션 | 완료 |
| `model_router.py` | LLM 티어 라우팅, think 제어, 스트리밍 | 완료 |
| `activity_tracker.py` | 구성요소별 실시간 활동 상태 추적 | 완료 |
| `job_control.py` | 협조적 작업 취소 + 동시 실행 방지 락 | 신규 (검증 대기) |
| `hf-frontend/src/App.jsx` | 메인 React 컴포넌트 | 재작업 완료 (검증 대기) |
| `hf-frontend/src/ArticleCard.jsx` | 기사 카드 (정제/번역/편집/Vault 저장) | 완료, zero-width space 제거 확인 필요 |
| `hf-frontend/src/MarkdownEditor.jsx` | Crepe 기반 WYSIWYG 에디터, 이미지 업로드 | 완료 |
| `hf-frontend/src/App.css` | 스타일 | 지속 추가 중 |
| `requirements.txt` | 백엔드 의존성 목록 | `googlenewsdecoder>=0.1.7` 추가 완료 |

## 5. 완료된 기능

- [x] Ollama+qwen3.5 조합 검증, M1 Max 실측 벤치마크
- [x] 모델 라우터, 번역 SSE 스트리밍, 문서 정제 파이프라인
- [x] TARGET_SOURCES/CATEGORY_CONFIG 영어로 통일
- [x] Typora 스타일 에디터, 카드 목록 펼치기/접기
- [x] Collector 플러그인 아키텍처, Source/Keyword DB화
- [x] 출처 자동 승격/실패 관리, 소스 관리 패널
- [x] 개인저장방(Vault), GPU 사용량 표시, 플랫폼 구성요소 안내 패널
- [x] 문서 최상단 고정, 한글보기 버튼
- [x] 번역 파이프라인 문장 단위 재설계
- [x] **(신규) Google 뉴스 URL 디코딩** — googlenewsdecoder로 실제 발행사 URL 해석,
      실사용 로그로 정상 크롤링 확인
- [x] **(신규) 버튼 재클릭 시 실제 작업 중단** — AbortController + `/collect/cancel` +
      백엔드 루프 내 취소 체크
- [x] **(신규) 동시 수집 작업 차단** — `job_control`이 한 번에 하나의 수집만 허용
- [x] **(신규) SQLite WAL 모드 + busy_timeout** 적용
- [x] **(신규) 파이프라인 수집 시 미등록 키워드 자동 등록**
- [x] **(신규) 소스별 점검 주기 인라인 편집 + "다음 점검까지" 표시**
- [x] **(신규) 스케줄러 점검 간격(tick_minutes)과 소스/키워드 개별 주기의 정합성 경고**

## 6. 남은 작업 / 다음 단계 후보

### 최우선 — 검증 완료 전까지는 다음 단계로 못 넘어감
- [ ] **8번 검증 체크리스트 실제 실행/확인** (아래 항목 그대로, 아직 대부분 미완료)

### 확인은 됐지만 완전히 매듭짓지는 못한 것
- 구글 뉴스 디코딩·크롤링 자체는 로그로 성공 확인됨(techcrunch/theverge/cnbc 등)했으나,
  그 직후 SQLite 락 문제로 최종 저장까지는 확인 못 함 → WAL/job_control 적용 후
  **처음부터 끝까지 한 번에 성공하는 것**을 아직 재확인 못 함 (다음 세션 최우선 확인 대상)

### 구조 정리 (착수 시점에 대한 합의: 8번 체크리스트 통과 후에만)
- [ ] **로직 변경 없는 파일 분해** — `main.py`(1400줄+), `App.jsx`(1200줄+)가 계속 커지면서
      복붙 기반 편집의 구조적 위험(중복 정의, 함수 누락, zero-width space 혼입)이 반복
      되고 있음이 확인됨. 체크리스트 통과 후, 로직은 그대로 두고 파일만 나누는 작업을
      별도 세션으로 진행 예정:
      - 백엔드: `main.py`를 `routers/{articles,translate,collect,stats}.py` + `schemas.py`로 분리
      - 프론트: `api.js`(axios 호출 모음) + `hooks/{useCollection,useSourceManager,useArticleActions}.js`로 분리
      - **원칙**: 분해와 동시에 새 기능/개선을 절대 섞지 않기. 분해 직후 8번 체크리스트
        전체를 다시 한번 재검증.
      - 본격적인 아키텍처 리팩토링(계층 분리, 상태관리 라이브러리 도입 등)은 이 프로젝트
        규모에서는 불필요하다고 판단 — 과설계 방지 차원에서 "분해"만 하고 멈춤.

### 그 외 기존 항목
- [ ] RAG 파이프라인 구축 (LanceDB, HEAVY 티어 모델 연계 보고서 생성)
- [ ] 동영상/음악/이미지 Collector 실제 구현
- [ ] Vault 폴더 브라우징 UI ("내 채널" 화면)
- [ ] `run_collection_job()` 등 죽은 코드 정리
- [ ] CandidateSource 관리 UI
- [ ] ML 학습용 데이터 export 스크립트
- [ ] 기존 DB의 `Source.name` 한국어 라벨(`[정치]` 등) 영어 마이그레이션
- [ ] **(신규) DB 재생성 직후 전체 소스 초기 수집이 무겁게 한꺼번에 도는 문제** — 지금은
      409 자동 재시도로 완화했지만, 소스 개수가 늘어나면 최초 기동이 오래 걸릴 수 있음.
      우선순위 낮음(급하지 않음), 나중에 초기 수집을 배치/점진적으로 나누는 방식 검토.

## 7. 새 대화 시작 시 사용법

1. `PROJECT_STATUS_INDEX.md` + 이 파일을 첨부
2. **코드 파일은 project knowledge 검색이 아니라 직접 업로드 권장** — 8/7 세션에서
   project knowledge 검색으로는 긴 파일 전체를 빠짐없이 가져오지 못한다는 것을 확인함
   (일부 함수가 검색에 누락됨). `main.py`, `App.jsx`, `scheduler.py`, `collectors.py`,
   `models.py`, `database.py`, `job_control.py` 등 실제 최신 파일을 매 세션 시작 시
   첨부하는 것이 가장 정확함.
3. "이어서 [다음 작업]을 하고 싶다"고 요청

## 8. 검증 체크리스트 (다음 단계 진행 전 필수)

**아래 항목을 실제로 하나씩 실행/확인하기 전까지는 다음 번호(02)의 새 단계를 시작하지 않습니다.**

- [ ] 구성요소 사용량 패널이 실제 작업(수집/번역) 중 상태를 정확히 반영하는지
- [ ] 정제/번역/편집 버튼 클릭 시 해당 문서가 목록 최상단으로 이동/유지되는지
- [ ] 번역 버튼 클릭 시 문장이 완성되는 대로 즉시 화면에 이어서 렌더링되는지
- [ ] 번역 결과에 영어 원문과 한글 번역이 항상 번갈아 나오는지
- [ ] 한글보기 버튼이 정상적으로 한글 문장만 필터링해서 보여주는지
- [ ] 키워드별 현황 버튼에 찍힌 건수와, 클릭해서 열리는 실제 목록 건수가 일치하는지
- [ ] 쿠키/동의 배너 텍스트가 본문으로 잘못 저장되는 사례가 재발하지 않는지
- [ ] 번역 진행 중 Ctrl+C를 눌렀을 때 프로세스가 즉시 종료되는지
- [ ] 새로 등록한 키워드가 한국어가 아닌 영어 기사를 수집하는지
- [ ] **키워드 파이프라인이 0건이 아니라 실제로 기사를 수집하고, DB에 최종 저장까지
      완료되는지** — googlenewsdecoder 자체는 성공 확인됐으나 WAL 적용 후
      처음부터 끝까지 재확인 필요 (최우선)
- [ ] `TARGET_SOURCES`/`CATEGORY_CONFIG`의 영어 라벨이 실제 화면에 정상 반영됐는지
- [ ] **(신규) "파이프라인 수집" 버튼 재클릭 시 실제로 작업이 중단되는지**
- [ ] **(신규) "검색/등록" 버튼 재클릭 시 실제로 작업이 중단되는지**
- [ ] **(신규) 미등록 키워드로 "파이프라인 수집"을 눌렀을 때 자동 등록되고 바로
      수집되는지**
- [ ] **(신규) 두 수집 작업이 겹칠 때 "database is locked" 없이 하나는 정상 처리되고
      나머지는 409/자동 재시도로 매끄럽게 처리되는지**
- [ ] **(신규) 소스관리에서 개별 소스의 점검 주기를 바꾸면 "다음 점검까지" 표시가
      실제로 그에 맞게 갱신되는지**
- [ ] **(신규) 출처 보기 버튼 클릭 시 출처별 목록이 실제로 렌더링되는지**

전부 체크되면:
1. 위 표의 미체크 항목을 모두 체크
2. 이 문서 최상단에 `> 검증 완료: YYYY-MM-DD (KST)` 한 줄 추가
3. `PROJECT_STATUS_INDEX.md`의 01단계 행 상태를 `검증 완료 ✅`로 갱신

검증 중 발견됐지만 지금 단계에서 해결하지 않기로 한 문제가 있다면, 위 "6. 남은 작업"에
사유와 함께 기록하고 넘어갑니다 (조용히 넘어가지 않기).
