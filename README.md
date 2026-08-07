# WebCrawler
# hf_crawler
# hf_crawler

# hf_crawler 개인화 레이어 추가 — 통합 가이드

> 작성: 2026-08-07 (KST) · 기존 `hf_crawler` 프로젝트(`main.py`, `models.py`, `database.py`) 위에
> 얹는 확장. 기존 파일의 필드/로직은 **하나도 변경하지 않고 추가만** 합니다.

## 파일 구성

| 파일                                 | 무엇을 하는 파일인가                                         | 적용 방법                                    |
| ------------------------------------ | ------------------------------------------------------------ | -------------------------------------------- |
| `personalization_taxonomy.py`        | 기존 `CATEGORY_CONFIG`를 세분화한 서브카테고리 정의 + 점수제 분류 함수 | 프로젝트 루트에 새 파일로 추가               |
| `models_addon_interaction_signal.py` | `InteractionSignal` 테이블 정의                              | 내용을 기존 `models.py` **맨 끝에 붙여넣기** |
| `personalization.py`                 | 저장/집계 로직 (분류→저장, 프로필 조회, 명시적 피드백)       | 프로젝트 루트에 새 파일로 추가               |
| `main_integration_snippet.py`        | FastAPI 엔드포인트 3개 + 기사 저장 훅 예시                   | 내용을 참고해 `main.py`에 반영               |

## 적용 순서

1. `personalization_taxonomy.py`, `personalization.py`를 `hf_crawler/` 루트에 복사
2. `models_addon_interaction_signal.py`의 클래스 정의를 `models.py` 맨 끝에 붙여넣기
   (import는 이미 `models.py` 상단에 다 있으므로 추가 import 불필요)
3. 서버 재시작 시 `create_db_and_tables()`가 `SQLModel.metadata.create_all(engine)`을
   호출하므로 `interaction_signals` 테이블은 **자동 생성**됩니다. `migrate_db.py`에
   손댈 필요 없음 (새 테이블이지 기존 테이블 컬럼 추가가 아니기 때문).
4. `main_integration_snippet.py`의 import 3줄 + 엔드포인트 3개를 `main.py`에 추가
5. (선택, 권장) `collectors.py`의 기사 저장 직후 지점에 `classify_and_store()` 훅 추가
   — 주석의 예시 코드 참고

## 검증한 부분 / 검증 못한 부분

- ✅ `personalization_taxonomy.py`의 분류 로직은 독립적으로 실행해 확인했습니다.
  예: `"Fed raises interest rate amid inflation fears"` → `ECON.MACRO` (민감 카테고리로 자동 플래그),
  `"New open source framework for developers on github"` → `TECH.DEV`,
  `"Bon Appetit... recipe... cooking"` → `CULTURE.FOOD`
- ⚠️ `personalization.py`의 SQLModel 저장/조회 부분은 이 환경에 `sqlmodel` 패키지가
  없어(네트워크 차단) 실제 DB round-trip 테스트는 못 했습니다. 기존 `main.py`에서
  이미 쓰고 있는 것과 동일한 `Session`/`select`/`session.exec()` 패턴을 그대로
  사용했으니 문법상 문제는 없을 것으로 보이지만, **맥북 실제 환경에서 서버 기동 후
  `/personalization/profile` 한 번 호출해 정상 동작하는지 확인**해주세요.

## 다음에 할 일 (제안)

- `ArticleCard.jsx`에 👍/👎 버튼 추가 → `/personalization/feedback` 호출 연결
- 브라우저 확장(별도 프로젝트)에서 방문 이벤트를 `classify_and_store()` 형태로 저장하는
  API 엔드포인트 하나 더 추가 (`POST /personalization/extension-event` 같은 형태)
- `POL`, `Economy` 같은 민감 카테고리는 `is_sensitive()`로 판별되므로, 실제 답변/보고서
  생성 프롬프트에서 이 플래그가 True인 항목은 "결론 유도"가 아니라 "필터링"에만
  쓰도록 프롬프트 조립 코드에서 분기 처리하는 것을 권장합니다.
- `PROJECT_STATUS_INDEX.md`에 이 작업을 새 단계(`PROJECT_STATUS_02_personalization.md`)로
  등록하고, 검증 체크리스트(프로필 API 응답 확인, 피드백 저장 확인 등)를 채워 넣으세요.