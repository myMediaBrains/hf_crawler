# 맥북 AI 플랫폼 — 프로젝트 상태 문서

마지막 갱신: 2026-08-03
용도: 새 대화를 시작할 때 이 문서를 Claude에게 첨부하면, 지금까지의 모든 맥락을 즉시 이어받을 수 있음.

---

## 1. 프로젝트 개요

- 이름: `hf_crawler` (백엔드) + `hf-frontend` (프론트엔드, Vite+React)
- 목적: 외부 RSS/키워드 기반으로 기사를 수집 → 로컬 Ollama LLM으로 정제/번역 →
  Typora 스타일 에디터로 편집 → SQLite(구조화 데이터)와 로컬 파일시스템(대용량/개인 문서)에
  분리 저장 → 향후 RAG 기반 보고서 생성, 동영상/음악/이미지 수집으로 확장 예정.
- 하드웨어: M1 Max, 32GB, Ollama 0.32.5, MLX 가속 확인됨.

## 2. 핵심 아키텍처 결정 (왜 이렇게 했는지)

1. **모델 라우팅 분리**: `model_router.py`가 작업 성격별로 LIGHT(qwen3.5:9b, 상시 상주)/
   HEAVY(qwen3.5:35b-a3b-nvfp4, 온디맨드) 티어를 나눔. `think=False`를 API 파라미터로
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

## 3. 겪었던 주요 버그와 교훈 (다시 반복하지 않도록)

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

## 4. 파일 인벤토리

| 파일 | 역할 | 상태 |
|---|---|---|
| `main.py` | FastAPI 엔트리포인트, 전체 API 엔드포인트 | 지속 수정 중 |
| `models.py` | 전체 DB 스키마 (SQLModel) | 완료 |
| `database.py` | DB 엔진/세션 설정 | 기존 그대로 |
| `content_utils.py` | 크롤링/정제 순수 함수 (main.py에서 분리) | 완료 |
| `collectors.py` | Collector 플러그인 (RSS, Google뉴스검색) | 완료 |
| `scheduler.py` | 틱 기반 스케줄러, 승격/실패 로직 | 완료 |
| `migrate_db.py` | 기존 테이블 컬럼 마이그레이션 | 완료 |
| `model_router.py` | LLM 티어 라우팅, think 제어, 스트리밍 | 완료 |
| `hf-frontend/src/App.jsx` | 메인 React 컴포넌트 | 지속 수정 중 |
| `hf-frontend/src/ArticleCard.jsx` | 기사 카드 (정제/번역/편집/Vault 저장) | 완료 |
| `hf-frontend/src/MarkdownEditor.jsx` | Crepe 기반 WYSIWYG 에디터, 이미지 업로드 | 완료 |
| `hf-frontend/src/App.css` | 스타일 | 지속 추가 중 |

## 5. 완료된 기능

- [x] Ollama+qwen3.5 조합 검증, M1 Max 실측 벤치마크
- [x] 모델 라우터 (LIGHT/HEAVY 티어, think 제어, keep_alive 정책)
- [x] 번역 SSE 스트리밍 안정화 (하트비트, 큐 기반 타임아웃)
- [x] 문서 정제 파이프라인 (정규식 1차 + LLM 2차 + 문단 강제 재구성)
- [x] TARGET_SOURCES 해외 소스로 정리 (한국어 소스 제거 - 번역 학습 목적과 안 맞아서)
- [x] Typora 스타일 에디터(Milkdown/Crepe) + 이미지 업로드
- [x] 카드 목록 - 펼치기/접기, 화면 이탈 시 자동 접힘(편집 중엔 예외)
- [x] Collector 플러그인 아키텍처, Source/Keyword DB화
- [x] 키워드 등록 → 즉시 수집 → 백그라운드 반복 수집
- [x] 출처 자동 승격/실패 관리, 소스 관리 패널(추가/삭제)
- [x] 스케줄러 틱 간격 사용자 설정
- [x] 개인저장방(Vault) - DB 기본 저장 + 선택적 파일 내보내기
- [x] GPU 사용량 표시 정확도 개선 (실제 생성 중 여부 추적)
- [x] 플랫폼 구성요소 안내 패널

## 6. 남은 작업 / 다음 단계 후보

- [ ] RAG 파이프라인 구축 (LanceDB 등 벡터DB, HEAVY 티어 모델과 연계한 보고서 생성)
- [ ] 동영상/음악/이미지 Collector 실제 구현 (YouTubeCollector 등 - 인터페이스만 준비된 상태)
- [ ] Vault 폴더 브라우징 UI (지금은 export만 가능, 볼트 내 파일을 플랫폼에서 직접 열람하는
      "내 채널" 화면은 아직 미구현)
- [ ] `run_collection_job()`/`TARGET_SOURCES` 관련 죽은 코드 정리 (동작엔 지장 없으나 청소 필요)
- [ ] CandidateSource 관리 UI (지금은 자동 추적만 되고, 사용자가 후보 목록을 직접 보는 화면 없음)
- [ ] ML 학습용 데이터 export 스크립트 (원본/정제본/번역 쌍을 JSONL 등으로 뽑는 것 - 실제
      학습을 시작하는 시점에 만들 예정, 지금은 DB 설계만 대비해둔 상태)

## 7. 새 대화 시작 시 사용법

1. 이 문서를 그대로 첨부
2. 필요하면 현재 `main.py`, `models.py` 등 실제 파일도 같이 첨부 (제가 드린 버전과
   실제 반영본이 다를 수 있으므로, 최신 실제 파일을 보내주시는 게 가장 정확함)
3. "이어서 [다음 작업]을 하고 싶다"고 요청
