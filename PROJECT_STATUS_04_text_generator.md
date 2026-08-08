# PROJECT_STATUS_04 — 개인화 텍스트 생성기 (RAG 기반 채팅형 응답)

> 관련 마스터플랜 장: 마스터플랜에 대응 장 없음 (신규 도출 요구사항 — 개인화 서비스
> 플랫폼의 첫 생성기 컴포넌트. `PROJECT_STATUS_02_personalization.md`의 "6. 남은 작업"에
> 적혀 있던 "실제 개인화 답변 생성 단계"와, `requirements.origin.md`의 "RAG 파이프라인
> 구축" 항목 두 가지가 만나는 지점)
> 시작일: 2026-08-09 (KST) / 마지막 갱신: 2026-08-09 (KST)
> 이전 단계: `PROJECT_STATUS_03_source_reliability.md`

---

## 1. 이번 단계의 목표

- hf_crawler는 지금까지 수집(01) → 개인화 신호 수집/집계 인프라(02) → 출처 안정성(03)까지
  왔지만, 실제로 사용자가 "물어보면 답해주는" 개인화 서비스는 아직 없었다.
- 이번 단계의 목적: **"이것저것 물어보면 최근 수집한 기사를 근거로 재밌게 답해주는"
  채팅형 텍스트 생성기**를 별도 서비스로 구축한다. 벡터DB(LanceDB 등)는 아직 도입 전이므로,
  1차 버전은 Keyword/Category 매칭 기반의 경량 RAG로 시작하고, 추후 임베딩 검색으로
  교체 가능하도록 조회 로직만 인터페이스로 분리해둔다.
- 이번 단계는 텍스트 생성기 하나의 뼈대 구축까지만 다루고, 음성/이미지/애니메이션
  생성기는 다음 단계 이후로 넘긴다.

## 2. 핵심 아키텍처 결정

1. **별도 프로세스, 같은 레포, 같은 SQLite DB 파일 공유** — `generators/text/` 폴더를
   신설해 크롤러 백엔드(포트 8000)와 분리된 FastAPI 프로세스(포트 8001)로 띄운다.
   DB는 기존 `hf_crawler.db`(SQLite)를 그대로 공유해서 참조한다 — "DB를 서비스 간
   계약으로 삼는다"는 이전 대화에서 정리한 원칙을 그대로 적용.
2. **동시 쓰기 충돌 방지용 별도 락은 도입하지 않음** — `job_control.py`는 같은 프로세스
   안에서 무거운 크롤링 루프 두 개가 충돌했던 문제(01단계)의 해법이었고, 텍스트
   생성기는 요청 1건당 `text_generations` 테이블에 가벼운 INSERT 1건만 발생시키므로
   기존에 적용해둔 WAL 모드 + busy_timeout(30초)만으로 충분하다고 판단. 향후 이미지/
   음성 생성기처럼 쓰기 빈도나 트랜잭션이 무거워지면 이 판단을 재검토해야 함.
3. **모델 티어는 LIGHT 고정** — `model_router.py`의 실측 벤치마크(HEAVY 콜드 로드
   34.89초)를 근거로, 채팅형 즉시 응답에는 HEAVY 티어를 쓰지 않기로 함. `TASK_PROFILES`에
   `personalized_qa` 프로필을 LIGHT 티어로 신규 추가(temperature 0.8, presence_penalty
   0.6 — "재밌게" 요청받았으므로 다양성을 기존 프로필들보다 높게 설정).
4. **경량 RAG 조회 로직을 `retrieval.py`로 분리** — `get_context_articles(query, session,
   top_interest_categories, ...)` 시그니처를 고정해두고, 내부 구현(현재는 Keyword
   부분 문자열 매칭 → 카테고리 매칭 → 최신순 폴백)만 나중에 LanceDB 임베딩 검색으로
   교체 가능하게 설계.
5. **원본 보존 원칙 재사용** — `Article.raw_content`, `Translation` 테이블과 동일한
   맥락으로, `TextGeneration` 테이블도 append-only로 설계(UPDATE 없음). 질문-답변 쌍을
   전부 이력으로 남겨 추후 "어떤 질문에 어떤 근거 기사가 쓰였는지" 학습/분석 데이터로
   활용 가능하게 함.
6. **타임스탬프 정책** — 02단계와 동일하게 DB에는 UTC로 저장하고, API 응답 시점에만
   `to_kst()`로 KST(UTC+9) ISO 8601 문자열로 변환.

## 3. 겪은 버그와 교훈

- **패키지 경로 실행 시 상대 import 필요** — `uvicorn generators.text.main:app`처럼
  점(`.`) 표기로 실행하면, 같은 폴더의 `retrieval.py`라도 `from retrieval import ...`가
  아니라 `from .retrieval import ...`로 상대 import해야 함. 절대 import로 두면
  `ModuleNotFoundError: No module named 'retrieval'` 발생.
- **`__init__.py` 누락 시 패키지로 인식 안 됨** — `generators/`, `generators/text/`
  양쪽에 빈 `__init__.py`가 있어야 `generators.text.main` 경로 자체가 성립함.
- **venv 위치 혼선** — 원래 `hf-frontend/`(React/Vite, Node.js 기반) 폴더 안에
  Python venv가 잘못 만들어져 있었음. **프론트엔드는 Python venv가 전혀 필요 없다**는
  걸 재확인 — Node.js는 `npm`이 `node_modules/`로 의존성을 관리하므로 venv 개념 자체가
  해당 없음. 정리 과정에서 실수로 실제 패키지가 설치돼 있던 venv를 삭제하게 됨.
- **`pip install -r requirements.txt` 시 `externally-managed-environment` 에러** —
  venv가 정상 활성화된 상태라면 원래 발생하면 안 되는 에러. `which python`/`which pip`
  결과가 venv 경로가 아니라 Homebrew 시스템 Python을 가리키고 있었던 것이 원인으로
  추정됨 — venv 손상 또는 Homebrew Python 업그레이드로 인한 참조 깨짐 가능성.
  `rm -rf venv` 후 `python3 -m venv venv`로 완전히 새로 생성하는 방식으로 해결.
- **`localhost:8000`/`localhost:8001` 루트 경로 접속 시 `{"detail":"Not Found"}`는
  정상 동작** — 사용자가 오작동으로 오해할 뻔했던 부분. FastAPI 백엔드는 사람이 보는
  화면이 아니라 API 서버이므로, 루트(`/`)에 별도 정의가 없으면 404가 정상이다.
  `/docs`(Swagger UI)로 접속해야 실제 동작 여부를 확인할 수 있다는 점을 팀 내
  공유 지식으로 남겨둠. 실제 화면은 `localhost:5173`(Vite 프론트엔드)에서 확인.

## 4. 파일 인벤토리

| 파일 | 역할 | 상태 |
|---|---|---|
| `generators/__init__.py` | 패키지 인식용 빈 파일 | 완료 |
| `generators/text/__init__.py` | 패키지 인식용 빈 파일 | 완료 |
| `generators/text/main.py` | 텍스트 생성기 FastAPI 앱 (포트 8001), `/generate`, `/generate/history` | 구현 완료 (검증 대기) |
| `generators/text/retrieval.py` | 경량 RAG 조회 로직 (Keyword/Category 매칭 + 최신순 폴백) | 구현 완료 (검증 대기) |
| `models.py` (기존 파일에 추가) | `TextGeneration` 테이블, `ContentOrigin.LLM_GENERATED` 추가 | 코드 제공 완료, **실제 적용 여부 확인 필요** |
| `model_router.py` (기존 파일에 추가) | `TASK_PROFILES`에 `personalized_qa` 프로필 추가 | 코드 제공 완료, **실제 적용 여부 확인 필요** |

## 5. 완료된 기능

- [x] `TextGeneration` 테이블 스키마 설계 (append-only, source_article_ids/matched_categories를 JSON 문자열로 보존)
- [x] 경량 RAG 조회 로직: 질문 속 등록 키워드 매칭 → 관심 카테고리 매칭 → 최신 기사 폴백, 3단계 우선순위
- [x] `personalized_qa` 모델 프로필 (LIGHT 티어, 대화체·다양성 지향 옵션)
- [x] `/generate` (질의응답 생성 + 이력 저장), `/generate/history` (최근 이력 조회) 엔드포인트
- [x] KST 타임스탬프 변환 응답 반영
- [x] 로컬 실행 환경(venv, PYTHONPATH, 상대 import) 정리 및 실행 확인

## 6. 남은 작업 / 다음 단계 후보

- [ ] **`/docs`(Swagger UI)에서 `/generate` 실제 호출 테스트** — Ollama 기동 여부,
      DB에 기사가 존재하는 상태에서의 응답 품질까지 확인 필요 (다음 대화 최우선 작업)
- [ ] `models.py`/`model_router.py`에 안내한 코드가 실제로 반영됐는지 `grep -n`으로 확인
      (이전 단계들에서 반복된 "패치 가이드만 주고 실제 적용 확인을 놓치는" 실수 재발 방지)
- [ ] `hf-frontend`에 채팅형 UI 컴포넌트 신설 — 질문 입력창 → `POST :8001/generate` 호출 →
      `answer` 표시 + `source_article_ids`로 근거 기사 함께 노출 (프론트엔드 미작업)
- [ ] `retrieval.py`의 키워드 매칭이 단순 부분 문자열 비교라 정확도가 낮음 — 추후 LanceDB
      임베딩 검색으로 교체 검토 (`get_context_articles()` 시그니처는 유지, 내부만 교체)
- [ ] 개인화 프로필(`personalization.get_top_interests()`)을 `top_interest_categories`
      인자로 실제 연결하는 부분 — 현재는 API 스펙만 열어두고 호출부(프론트엔드/오케스트레이터)
      연결은 안 되어 있음
- [ ] 응답에 사용된 근거 기사를 사용자가 "이 답변 마음에 안 든다"고 피드백할 수 있는
      경로 — `InteractionSignal`과 연결해 텍스트 생성기 답변 품질에도 개인화 신호가
      누적되도록 확장 검토
- [ ] `hf-frontend`에 잘못 만들어져 있던 Python venv 정리 — 삭제 후 `npm run dev` 정상
      동작 재확인 필요 (부수적으로 발견된 정리 작업)
- [ ] 텍스트 생성기가 쓰기 빈도/트랜잭션이 늘어날 경우, WAL만으로 충분한지 재검토
      (지금은 요청당 1행 INSERT라 문제없다고 판단했으나, 이후 음성/이미지 생성기까지
      늘어나면 크롤러 쓰기와의 경합 양상이 달라질 수 있음)

## 7. 새 대화 시작 시 사용법

1. `PROJECT_STATUS_INDEX.md` + 이 파일(`PROJECT_STATUS_04_text_generator.md`)을 첨부
2. 가능하면 실제 반영된 최신 `models.py`, `model_router.py`, `generators/text/main.py`,
   `generators/text/retrieval.py`도 함께 첨부 (패치가 실제로 적용됐는지 확인이 필요하므로)
3. "8번 검증 체크리스트를 이어서 진행하고 싶다" 또는 "다음 단계(프론트엔드 채팅 UI /
   음성 생성기 시작)를 진행하고 싶다"고 요청

## 8. 검증 체크리스트 (다음 단계 진행 전 필수)

**아래 항목을 실제로 하나씩 실행/확인하기 전까지는 다음 번호(05)의 새 단계를 시작하지 않습니다.**

- [ ] `models.py`에 `TextGeneration` 테이블과 `ContentOrigin.LLM_GENERATED`가 실제로
      반영되어 있는지 `grep -n`으로 확인
- [ ] `model_router.py`의 `TASK_PROFILES`에 `personalized_qa`가 실제로 반영되어 있는지 확인
- [ ] `generators/text/main.py`의 `from .retrieval import ...`가 상대 import로 되어 있는지 확인
- [ ] 크롤러(8000)와 텍스트 생성기(8001)를 동시에 띄운 상태에서 `/docs`가 각각 정상
      렌더링되는지
- [ ] `/generate`에 등록된 키워드가 포함된 질문을 넣었을 때, 실제로 해당 키워드로 수집된
      기사가 `source_article_ids`에 포함되는지
- [ ] 관련 기사가 전혀 없는 낯선 질문을 넣었을 때, 에러 없이 최신 기사 기반 폴백 답변이
      나오는지
- [ ] 크롤러가 백그라운드 수집 중인 상태에서 동시에 `/generate`를 호출했을 때
      "database is locked" 에러 없이 정상 응답되는지
- [ ] `/generate/history`에 방금 생성한 질의응답이 KST 시간으로 정확히 표시되는지
- [ ] `hf-frontend/venv` 삭제 후 `npm run dev`가 정상 동작하는지

전부 체크되면:
1. 위 표의 미체크 항목을 모두 체크
2. 이 문서 최상단에 `> 검증 완료: YYYY-MM-DD (KST)` 한 줄 추가
3. `PROJECT_STATUS_INDEX.md`의 이 단계 행 상태를 `검증 완료 ✅`로 갱신

검증 중 발견됐지만 지금 단계에서 해결하지 않기로 한 문제가 있다면, 위 "6. 남은 작업"에
사유와 함께 기록하고 넘어갑니다 (조용히 넘어가지 않기).
