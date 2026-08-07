# PROJECT_STATUS_02 — 개인화 프로필 레이어 (카테고리 세분화 + 만족도 신호 수집)

> 관련 마스터플랜 장: 해당사항 없음 — `requirements_refactoring.md`의 기존 장 계획이
> 아니라 사용자와의 대화 중 새로 도출된 요구사항. (아래 "6. 남은 작업"에 역편입 검토 항목 기록)
> 시작일: 2026-08-07 (KST) / 마지막 갱신: 2026-08-08 (KST)
> 이전 단계: `PROJECT_STATUS_01_hf_crawler_foundation.md`

---

## 1. 이번 단계의 목표

- hf_crawler는 지금까지 "기사를 수집·정제·번역·편집"하는 데까지만 완성돼 있고,
  기존 `UserPreference` 테이블(categories/keywords를 JSON 문자열로 뭉뚱그려 저장)은
  정의만 되어 있을 뿐 실제로 채워지지 않는 상태였다.
- 목적: 수집된 기사 + 사용자 행동(피드백)으로부터 **경제/사회/문화/독서/철학/기술/정치
  등 세분화된 관심사 프로필**을 자동으로 쌓아, 향후 "오늘 아침 뭐 먹을까", "오늘 증권은
  어떨까" 같은 개인화 질의응답이나 보고서 생성에 이 프로필을 컨텍스트로 주입할 수 있게
  하는 기반을 만든다.
- 이번 단계는 **수집/저장/집계 인프라**까지만 다루고, 실제 개인화 답변 생성(챗봇
  프롬프트에 프로필을 주입해 응답을 만드는 부분)은 다음 단계로 넘긴다.

## 2. 핵심 아키텍처 결정

1. **기존 `CATEGORY_CONFIG`(대분류)는 건드리지 않고, 그 위에 서브카테고리 레이어만
   추가**했다. `personalization_taxonomy.py`가 `SUBCATEGORY_CONFIG`를 별도로 정의하고,
   기존 `_score_categories_for_article`과 동일한 점수제(제목 가중치 3배, 본문 1배)
   방식을 그대로 재사용해 일관성을 유지했다.
2. **새 테이블 `InteractionSignal`(append-only 로그)**을 추가했다. `Article.raw_content`를
   절대 덮어쓰지 않는 기존 원칙과 동일한 이유로, 신호는 절대 UPDATE하지 않고 매번
   새 행으로 쌓은 뒤 **조회 시점에 시간 가중 감쇠(반감기 30일)를 적용해 동적으로
   집계**한다 (`personalization.get_profile()`).
3. **명시적 부정 피드백(👎)은 weight를 음수로 저장**해서, 프로필 점수가 실제로
   깎이도록 설계했다 (해당 카테고리를 계속 추천하지 않도록 하는 신호).
4. **민감 카테고리(`Politics`, `Economy`) 자동 플래그**: `is_sensitive()` 함수로 판별.
   향후 답변 생성 로직에서 이 플래그가 True인 항목은 "정보 필터링"에만 쓰고 "결론
   유도"에는 쓰지 않도록 강제할 계획.
5. **수집 경로별 신호 강도 차등화**: `RSSCollector`(고정 피드, 사용자가 직접 요청하지
   않은 백그라운드 수집)는 `weight=0.3, implicit`으로, `GoogleNewsSearchCollector`
   (사용자가 검색창에 직접 입력한 키워드)는 `weight=0.5, explicit`으로 구분해서
   신호 강도를 다르게 기록한다.
6. **타임스탬프 정책**: DB에는 기존 `Article.collected_at`과 동일하게 UTC로 저장하고,
   API 응답 시점에만 `to_kst()`로 KST(UTC+9) ISO 8601 문자열로 변환한다 — 사용자가
   "다운로드 시간/타임스탬프는 KST로 표기해달라"고 요청했기 때문.

## 3. 겪은 버그와 교훈

- **샌드박스 네트워크 차단으로 `sqlmodel` 패키지를 설치할 수 없었음** — `personalization.py`의
  분류 로직(`personalization_taxonomy.py`)은 표준 라이브러리만 써서 독립적으로 실행
  검증했지만, SQLModel `Session`/`select` 기반 저장·조회 로직은 문법 검증(`py_compile`,
  `ast.parse`)까지만 하고 실제 DB round-trip 테스트는 하지 못했다. **맥북 실제 환경에서
  반드시 1차 검증 필요** (아래 8번 체크리스트 참조).
- **`session.commit()`을 배치 끝에서 한 번만 호출하는 기존 Collector 구조와, 신호 저장에
  `article.id`(외래키)가 필요하다는 요구사항이 충돌**했다. `Article`을 `session.add()`한
  직후 `session.flush()`를 호출해 커밋 전에 `id`를 확보하는 방식으로 해결 — 기존
  Collector의 배치 커밋 구조 자체는 변경하지 않았다.

## 4. 파일 인벤토리

| 파일 | 역할 | 상태 |
|---|---|---|
| `personalization_taxonomy.py` | 서브카테고리 정의 + 점수제 분류 함수 | 구현 완료, 독립 실행 검증 완료 ✅ |
| `personalization.py` | 신호 저장/집계(시간 가중 감쇠)/명시적 피드백 처리 | 구현 완료, 구문 검증만 완료 (DB round-trip 미검증) |
| `models.py` (기존 파일에 추가) | `InteractionSignal` 테이블 정의 | 사용자가 직접 붙여넣기 완료 (2026-08-07) |
| `main.py` (기존 파일 패치 대상) | `/personalization/feedback`, `/personalization/profile`, `/personalization/top-interests` 3개 엔드포인트 | 패치 가이드 제공 완료, **실제 적용 여부 확인 필요** |
| `collectors.py` (기존 파일 패치 대상) | `RSSCollector`/`GoogleNewsSearchCollector`에 자동 신호 기록 훅 | 패치 가이드 제공 완료, **실제 적용 여부 확인 필요** |
| `PATCH_main_py.md` | main.py 패치용 찾기/바꾸기 가이드 | 완료 |
| `PATCH_collectors_py.md` | collectors.py 패치용 찾기/바꾸기 가이드 | 완료 |

## 5. 완료된 기능

- [x] 서브카테고리 taxonomy 정의 (`ECON.*`, `POL.*`, `TECH.*`, `CULTURE.*`, `READING.*` 등)
- [x] 민감 카테고리(`Politics`, `Economy`) 자동 플래그 함수
- [x] `InteractionSignal` 테이블 스키마 설계 및 `models.py`에 반영 (사용자 직접 적용)
- [x] `personalization.py`: 분류→저장(`classify_and_store`), 명시적 피드백(`store_explicit_feedback`),
      시간 가중 감쇠 프로필 집계(`get_profile`, `get_top_interests`)
- [x] `main.py` 패치 가이드 (엔드포인트 3개)
- [x] `collectors.py` 패치 가이드 (자동 수집 훅, 수집 경로별 신호 강도 차등화)
- [x] KST 타임스탬프 변환 유틸(`to_kst`)

## 6. 남은 작업 / 다음 단계 후보

- [ ] **`main.py`/`collectors.py` 패치를 실제로 적용하고 8번 체크리스트로 검증** (다음 대화의 최우선 작업)
- [ ] `ArticleCard.jsx`에 👍/👎 버튼 추가 → `/personalization/feedback` 연결 (프론트엔드 미작업)
- [ ] 브라우저 확장(현재 미착수 프로젝트)에서 방문 이벤트를 저장할 API 엔드포인트
      (`POST /personalization/extension-event`) 추가
- [ ] 실제 개인화 답변 생성 단계 — `get_top_interests()`를 챗봇/보고서 프롬프트에
      주입하는 로직은 아직 없음. 특히 민감 카테고리(`is_sensitive()=True`)는
      "정보 필터링 전용, 결론 유도 금지" 원칙을 프롬프트 조립 코드에서 실제로
      분기 처리해야 함 (설계만 돼 있고 구현 안 됨).
- [ ] `personalization_taxonomy.py`의 키워드 기반 분류는 정확도가 낮음 — 향후 LLM
      기반 분류(로컬 Qwen 9b 또는 필요시 Claude API)로 교체 검토
      (`requirements.origin.md`의 "카테고리 분류(classify task)를 지금의 정규식 기반
      get_keyword_stats에 선택적으로 결합할지는 별도 논의 필요" 메모와 연결되는 지점)
- [ ] **마스터플랜 역편입 검토**: 이번 개인화 기능이 `requirements_refactoring.md`의
      어느 장/스킬 네임스페이스(`skills/personalization/` 등)에 속하는지 정리해서
      마스터플랜 문서 자체에도 장 번호를 부여할지 결정 필요

## 7. 새 대화 시작 시 사용법

1. `PROJECT_STATUS_INDEX.md` + 이 파일(`PROJECT_STATUS_02_personalization.md`)을 첨부
2. 가능하면 실제 적용된 최신 `models.py`, `main.py`, `collectors.py`도 함께 첨부
   (패치가 실제로 적용됐는지, 어긋난 부분은 없는지 확인이 필요하므로)
3. "8번 검증 체크리스트를 이어서 진행하고 싶다" 또는 "다음 단계(프론트엔드 피드백
   버튼 / 실제 개인화 답변 생성)를 시작하고 싶다"고 요청

## 8. 검증 체크리스트 (다음 단계 진행 전 필수)

**아래 항목을 실제로 하나씩 실행/확인하기 전까지는 다음 번호(03)의 새 단계를 시작하지 않습니다.**

- [ ] `main.py`, `collectors.py`에 패치 가이드 내용을 실제로 반영했다
- [ ] 서버 기동 후 `GET /personalization/profile` 호출 시 에러 없이 `{"profile": {}}` (또는
      기존 신호가 있다면 그 내용)이 반환된다
- [ ] 키워드 하나를 새로 등록(`POST /keywords`)해서 즉시 수집이 트리거된 뒤,
      `GET /personalization/profile`을 다시 호출하면 해당 키워드와 관련된 서브카테고리에
      `n_signals` ≥ 1로 신호가 실제로 쌓여 있다
- [ ] `POST /personalization/feedback`으로 임의 기사에 👍/👎를 보내면, 이후
      `get_profile()` 결과에서 해당 서브카테고리 점수가 (긍정이면 올라가고 부정이면
      내려가는 방향으로) 변한다
- [ ] `interaction_signals` 테이블에 저장된 `created_at`이 UTC로, API 응답의
      `last_signal_kst`가 KST(UTC+9)로 올바르게 변환되어 나온다 (`date` 명령으로
      현재 시각과 대조)
- [ ] (선택) 민감 카테고리(`Politics`, `Economy`)로 분류된 신호에 `sensitive: true`가
      정상적으로 붙어 나온다

전부 체크되면:
1. 위 표의 미체크 항목을 모두 체크
2. 이 문서 최상단에 `> 검증 완료: YYYY-MM-DD (KST)` 한 줄 추가
3. `PROJECT_STATUS_INDEX.md`의 `02`번 행 상태를 `검증 완료 ✅`로 갱신

검증 중 발견됐지만 지금 단계에서 해결하지 않기로 한 문제가 있다면, 위 "6. 남은 작업"에
사유와 함께 기록하고 넘어갑니다 (조용히 넘어가지 않기).
