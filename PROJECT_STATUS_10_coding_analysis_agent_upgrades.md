# PROJECT_STATUS — 코딩분석(hf_coder) 에이전트 모드 개선

**대상 프로젝트:** `hf_coder` (hf_crawler의 `/codeanalysis/*` 기능을 독립시킨 별도 서비스, 기본 포트 :8100)
**작업일:** 2026-08-14 (KST)
**범위:** 에이전트 모드 로컬 모델 선택, Typora 실시간 연동, 대화 이력 오염 버그 수정

---

## 1. 배경

`hf_coder`는 GitHub 오픈소스를 불러와 별도 샌드박스에서 테스트하고, 로컬 프로젝트(VS Code로 편집 중인 `hf_crawler`)를 분석/리뷰하는 용도의 서비스다. VS Code 저장 감지 → 자동 리뷰 → 사람 승인 후 적용 → 커밋/푸시로 이어지는 흐름을 갖고 있으며, 커밋/푸시/파일쓰기는 항상 사람이 버튼을 눌러야만 실행된다(에이전트 도구엔 쓰기 권한이 전혀 없음).

이번 세션은 에이전트 모드(도구콜링 기반 자율 조사)의 **모델 선택을 고정 3개에서 로컬에 설치된 전체 ollama 모델로 확장**하는 작업에서 시작해, 그 과정에서 발견된 두 가지 실사용 버그(빈 응답, 대화 이력 오염)를 고쳤다.

---

## 2. 변경사항

### 2.1 에이전트 모드 — 로컬 모델 자유 선택

**문제:** 에이전트 모델 선택이 `qwen3-coder:30b`/`qwen2.5-coder:14b`/`glimmer` 3개로 하드코딩돼 있었음. 사용자가 원하는 어떤 로컬 모델이든 골라 쓸 수 있어야 함.

**변경 파일:**
- `model_router.py`
  - `list_available_models()` — `ollama.list()` 결과 전체 조회 (이름, 용량, tool-calling 지원 여부 포함)
  - `agent_task_for_model(model_name, fast_mode)` — 선택 모델이 `TIER_MODELS`에 등록된 모델(예: glimmer)이면 그 모델 전용 옵션 프로필(`num_ctx` 등)을 자동 매칭, 미등록 모델은 `fast_mode` 기준 기본 프로필로 폴백
  - `model_supports_tools(model_name)` — `ollama.show()`의 `capabilities` 필드로 tool-calling 지원 여부 best-effort 판별
  - `unload_model()` / `unload_other_models(keep_model)` — `keep_alive=0`으로 다른 로드된 모델 강제 언로드 (`CODE_LIGHT`의 `keep_alive=-1` 상시 상주로 인해 예전 모델이 계속 GPU를 점유하던 문제 해결)
  - `TIER_MODELS[GLIMMER]`를 실제 설치 태그(`hf.co/bartowski/Muse-Glimmer-30B-GGUF:IQ3_M`)로 정정 (기존 `muse-glimmer:30b-mlx`는 잘못된 값)
- `main.py`
  - `GET /codeanalysis/models` — 모델 목록 조회 엔드포인트 신규
  - `AgentChatRequest.model` 필드로 임의 모델명 직접 지정 가능 (`fast_mode`보다 우선, 하위호환 유지)
  - 모델 로드 전 tool-calling 지원 여부 사전 체크 → 미지원 확실하면 GPU에 올리지 않고 즉시 안내
  - 도구 호출도 없고 내용도 빈 응답이면 "정상 종료"로 조용히 넘기지 않고 명시적 경고 표시
- `CodeAnalysisChat.jsx`
  - 고정 라디오 3개 → `GET /codeanalysis/models` 기반 동적 라디오 (🔄 새로고침 버튼 포함)
  - tool-calling 미지원 모델은 ⚠️ 라벨 + 툴팁으로 표시 (선택 자체는 막지 않음)

### 2.2 Typora 실시간 연동

**요청:** 코딩분석 출력창을 맥북 Typora에서도 실시간으로 볼 수 있게.

**구현 방식:** Typora가 열려있는 파일의 디스크 변경을 자동 감지/리로드하는 특성을 이용 — 별도 API 연동이 아니라 고정 경로(`~/hf_coder_live.md`)에 스트리밍 내용을 계속 덮어쓰는 방식.

**변경 파일:**
- `typora_sync.py` (신규) — `write_live()`(0.4초 throttle로 덮어쓰기, 스트림 종료 시 강제 최종 반영), `open_in_typora()`(`open -a Typora`로 직접 실행)
- `main.py` — `sync_to_typora` 요청 필드(일반/Architect/에이전트 전 모드), `GET /codeanalysis/typora/status`, `POST /codeanalysis/typora/open`
- `CodeAnalysisChat.jsx` — "📝 Typora로 보기" 체크박스 + "🖥 Typora에서 열기" 버튼

### 2.3 버그 수정 — 대화 이력 오염 (가장 중요)

**증상:** 같은 세션에서 여러 모델을 연달아 비교 테스트하면, 이전 턴의 화면 표시용 조사 트레이스(`🔧 도구호출(args)` / `→ 결과` / `🤔 조사 N/10`)가 다음 요청의 대화 이력(`messages`)에 텍스트 그대로 재주입됨. 이 패턴을 이전 턴에서 반복해서 본 소형 모델(예: 14b)이 실제 `tool_calls` API 호출 대신 **JSON 텍스트를 그냥 content로 출력**해버리는 오작동으로 이어짐. 부수적으로 세션이 길어질수록 컨텍스트가 계속 불어나 `agent_loop_light`(`num_ctx=16384`)처럼 작은 프로필에 특히 부담을 줌.

**변경 파일:**
- `models.py` — `CodeChatMessage.agent_clean_answer` 컬럼 신규 (조사 과정 없는 순수 최종 답변만 저장)
- `database.py` — `_migrate_add_missing_columns()` 신규. `PRAGMA table_info`로 컬럼 존재 확인 후 없으면 `ALTER TABLE`로 추가 — **기존 대화 데이터 보존**, 서버 재시작 시 자동 적용
- `main.py` — 에이전트 이력 재구성 시 `content`(트레이스 포함) 대신 `agent_clean_answer`(순수 답변) 우선 사용, 저장 지점 3곳 모두에서 `agent_clean_answer` 병행 저장

---

## 3. 적용 방법

1. 위 변경 파일들을 실제 `hf_coder` 프로젝트 폴더에 반영
   - 신규 파일: `typora_sync.py`
   - 수정 파일: `main.py`, `model_router.py`, `models.py`, `database.py`, `CodeAnalysisChat.jsx`(hf-frontend 쪽)
2. hf_coder 서버 재시작 → `create_db_and_tables()`가 마이그레이션을 자동 실행 (`agent_clean_answer` 컬럼 추가)
3. Typora 연동을 쓰려면 `/Applications/Typora.app` 설치 필요. 경로를 바꾸고 싶으면 `.env`에 `TYPORA_SYNC_PATH=/원하는/경로.md` 추가

---

## 4. 알려진 이슈 / 다음 단계

- **이전 세션 오염 데이터**: 이번 작업 이전에 저장된 에이전트 대화 기록은 `agent_clean_answer`가 비어있어 여전히 `content`(트레이스 포함)로 폴백됨 → 모델 비교 테스트는 **새 세션에서 시작** 권장
- **glimmer(GGUF) tool-calling 미지원**: `hf.co/bartowski/Muse-Glimmer-30B-GGUF:IQ3_M`은 에이전트 모드(도구 호출 필수)에서 빈 응답 확인됨 — 일반 채팅 모드(비-에이전트)에서만 사용 가능. 일반 채팅 모드 쪽에도 모델 선택 드롭다운이 필요하면 추가 작업 필요
- **일반 채팅 모드 모델 선택 미지원**: 지금은 에이전트 모드만 임의 모델 선택 가능. `/codeanalysis/chat/stream`(일반/Architect)은 아직 `fast_mode` 2단계 고정
- **CSS 미반영**: `CodeAnalysisChat.jsx`에 추가된 클래스(`code-agent-model-refresh`, `code-agent-model-option-warn`, `code-typora-open-btn` 등)는 아직 스타일 없음 — 기본 스타일로만 렌더링됨
