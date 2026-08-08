# PROJECT_STATUS_05 — 크롤러/텍스트 생성기 우선순위 양보 메커니즘

> 시작일: 2026-08-09 (KST) / 마지막 갱신: 2026-08-09 (KST)
> 이전 단계: `PROJECT_STATUS_04_text_generator.md`

---

## 1. 이번 단계의 목표

- 텍스트 생성기(8001) 테스트 중 응답이 느려지는 문제를 겪음 — 처음엔 "백그라운드
  자동 크롤링" 때문이라고 추정했으나, 조사 결과 **자동 스케줄러 틱 경로에는
  Ollama 호출이 전혀 없다는 것을 확인**. 실제 경합 지점은 크롤러(8000)의
  **수동 번역/정제 버튼**(`study-translate`, `study-translate-stream`,
  `/articles/{id}/clean`)이었음.
- `priority.py`(파일 마커 기반 상호 배제)를 실제 호출 지점에 연결하고,
  번역 진행 중 텍스트 생성기 요청이 들어오면 크롤러가 즉시 양보하는 것을
  로그로 실증.

## 2. 핵심 발견 — 진단 과정에서 밝혀진 것

1. **자동 백그라운드 수집(`collectors.py`/`scheduler.py`)은 Ollama를 쓰지 않는다.**
   RSS/구글뉴스 수집은 크롤링(`crawl4ai`)과 정규식 기반 정제(`clean_article_content`)만
   하고, LLM 호출이 전혀 없음. `personalization.py`의 분류도 정규식/키워드
   기반이라 Ollama 미사용.
2. 크롤러(8000)에서 Ollama를 호출하는 곳은 정확히 3곳뿐이며, **전부 사람이
   버튼을 눌러야 실행되는 수동 엔드포인트**:
   - `main.py` `study_translate_article()` (동기 번역)
   - `main.py` `study_translate_article_stream()` (스트리밍 번역)
   - `content_utils.py` `extract_body_via_llm()` (기사 정제, `/articles/{id}/clean`)
3. `priority.py` 자체(마커 파일 기반 양보 로직)는 이미 잘 설계되어 있었지만,
   위 3곳 어디에도 `yield_to_person()` 호출이 없어서 실제로는 동작하지
   않고 있었음. 텍스트 생성기 쪽 `mark_busy()`/`mark_idle()`도 마찬가지로
   `import`만 있고 실제 호출이 빠져 있었음.
4. `.generator_busy` 마커 파일은 `priority.py`가 있는 폴더(프로젝트 루트)에
   생성됨 — `generators/text/`가 아니라 `hf_crawler/` 루트 기준.

## 3. 적용한 패치

| 파일 | 위치 | 내용 |
|---|---|---|
| `main.py` | `study_translate_article()`, `for seg in segments:` 루프 내부 | `priority.yield_to_person()` 삽입 (Ollama 호출 직전) |
| `main.py` | `study_translate_article_stream()`, `_producer()` 내부 `for seg in segments:` 루프 | 동일하게 `priority.yield_to_person()` 삽입 |
| `content_utils.py` | `extract_body_via_llm()`, `model_router.chat(task="extract_body", ...)` 직전 | `priority.yield_to_person()` 삽입 |
| `generators/text/main.py` | `/generate` 엔드포인트, `model_router.chat("personalized_qa", ...)` 호출부 | `priority.mark_busy()` / `try...finally: priority.mark_idle()`로 감쌈 |
| 두 파일 상단 | import 구역 | `import priority` 추가 |

## 4. 부수적으로 발견하고 고친 버그

- **`translations` 테이블 스키마 불일치**: `models.py`의 `Translation` 클래스에
  `block_reason` 필드가 정의되어 있었으나(아마 `Source.block_reason` 추가 시
  실수로 같이 들어감), `migrate_db.py`에는 이 테이블에 대한 마이그레이션이
  없어서 실제 SQLite 파일에는 컬럼이 없었음. 번역 저장 시
  `sqlite3.OperationalError: table translations has no column named block_reason`
  500 에러 발생.
  - **해결**: `migrate_db.py`에 `TRANSLATIONS_MIGRATIONS` + `migrate_translations()`
    추가, `main.py`의 `lifespan()`에서 `migrate_db.migrate_translations(DB_NAME)`
    호출 연결. 반영 확인 완료.

## 5. 검증 결과 (실증 로그)

번역이 진행되던 도중(05:02:38 시작) `/generate` 요청을 05:02:41에 보냈을 때:

```
05:02:43.028  크롤러 - 번역 문장 하나 Ollama 호출 완료
05:02:43.148  [priority] 텍스트 생성기 응답 대기 중 - 0.5초 양보   ← 감지 즉시 양보 시작
   ... (0.5초 간격 반복, 총 7회)
05:02:46.882  [priority] 총 3.5초 양보 후 크롤링 진행              ← 사람 요청 종료 감지, 즉시 재개
```

`_MAX_YIELD_SECONDS=15` 한도 안에서 정상 종료(아사 방지 확인). **파일 마커
기반 프로세스 간 통신이 의도대로 동작함을 확인.**

## 6. 남은 성능 이슈 (이번 단계에서는 보류)

- `/generate` 단독 실행 시에도 14~43초로 편차가 큼 (컨텍스트 4013자, 기사 8건,
  답변 623자 기준 14.02초 / 크롤링 동시 진행 시 43.15초 측정).
- `num_predict=1536` 상한 자체는 원인이 아님을 확인함 (실제 생성은 250~300토큰
  수준, 상한의 20% 미만 사용).
- 43초 vs 14초 차이의 정확한 원인(경합 잔여 효과인지 단순 시스템 부하 변동인지)은
  **확정하지 않고 보류**하기로 결정 — 지금 속도(14초대)가 허용 가능하다고 판단.
- 디버깅용으로 `generators/text/main.py`에 추가한 로그 한 줄은 성능에 영향 없어
  그대로 유지 중:
  ```python
  logger.info(f"[generate] 컨텍스트 글자수: {len(context_block)}, 답변 글자수: {len(answer)}, 참고기사 {len(articles)}건")
  ```

## 7. 프론트엔드 추가 작업 (이번 단계 부수 작업)

- `CrawlToggleButton.jsx` 신규 생성 — 백그라운드 크롤링 중지/재개 토글 버튼.
  `GET /scheduler/status`로 초기 상태 조회, `POST /scheduler/pause`/`resume`로 토글.
- 백엔드에 `GET /scheduler/status` 엔드포인트 신규 추가 (`job_control.is_paused()` 반환).
- `App.jsx`에 `import CrawlToggleButton` + `<CrawlToggleButton />` 배치 필요
  (실제 반영 여부 **미확인 — 다음 세션에서 확인 필요**).

## 8. 검증 체크리스트 (다음 단계 진행 전 참고용 — 필수는 아님)

- [x] `priority.yield_to_person()` 3곳 모두 grep으로 반영 확인
- [x] `priority.mark_busy()`/`mark_idle()` 반영 확인 (마커 파일 생성/삭제로 실증)
- [x] 번역 동시 진행 중 `/generate` 호출 시 실제 양보 로그(`[priority] ...`) 확인
- [x] `_MAX_YIELD_SECONDS` 상한 도달 시 강행 전환되는지 확인 (3.5초 사례로 간접 확인,
      15초 상한 자체를 직접 강제로 넘겨본 적은 없음 — 필요시 추가 검증 가능)
- [x] `translations` 테이블 `block_reason` 컬럼 마이그레이션 반영 확인
- [ ] `content_utils.py`의 `extract_body_via_llm()` 경로(정제 버튼)도 동일하게
      실제 양보 로그가 찍히는지는 아직 직접 실증 안 함 (원리상 동일하게 동작할
      것으로 예상되나 실제 로그로 본 적은 없음)
- [ ] `CrawlToggleButton.jsx`가 `App.jsx`에 실제로 연결되어 브라우저에서
      정상 동작하는지 미확인
- [ ] `/scheduler/status` 엔드포인트가 실제 `main.py`에 반영됐는지 미확인

## 9. 새 대화 시작 시 사용법

1. `PROJECT_STATUS_INDEX.md` + 이 파일(`PROJECT_STATUS_05_priority_scheduling.md`)을 첨부
2. 가능하면 최신 `priority.py`, `main.py`, `content_utils.py`,
   `generators/text/main.py`, `migrate_db.py`, `App.jsx`도 함께 첨부
   (8번 체크리스트 미완 항목 확인에 필요)
3. 다음 중 하나를 요청:
   - "8번 체크리스트의 미완 항목(정제 버튼 경로 검증, 프론트엔드 연결 확인)부터
     이어서 하고 싶다"
   - "6번 성능 이슈(14초~43초 편차)를 다시 파보고 싶다"
   - "다음 단계(음성/이미지 생성기 등)를 새로 시작하고 싶다"
