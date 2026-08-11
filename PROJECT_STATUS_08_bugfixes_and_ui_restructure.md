# PROJECT_STATUS_08 — Tag 시스템 안정화 + UI 재구성 + 대량 버그 수정

> 관련 마스터플랜 장: 해당사항 없음 (대화 중 도출된 요구사항의 연속)
> 시작일: 2026-08-10 (KST) / 마지막 갱신: 2026-08-10 (KST)
> 이전 단계: `PROJECT_STATUS_07_tag_system_complete.md`

---

## 1. 이번 단계에서 한 일 (07번 문서 이후)

07번 문서 끝에서 "번역 버튼 양방향화"를 다음 세션 최우선 과제로 남겼는데, 그 작업은 **완료된 상태로 확인됨** (사용자가 실제 `main.py`를 업로드했을 때 `source_lang` 감지 로직이 이미 반영되어 있는 걸 확인). 이번 세션은 그 이후 실사용하며 터진 **버그 수정 다수**와 **UI 재구성(장르편집기 통합, 레이아웃 조정)**, **키워드별 현황 2단계 구조 개편**이 핵심이었다.

## 2. ⚠️ 가장 중요한 발견 — GitHub 프로젝트 지식 동기화가 계속 낡아있음

이번 세션 내내 `project_knowledge_search`가 **Tag 통합 재설계 이전(구식) 코드**를 반복적으로 반환했다. 반면 사용자가 **직접 파일을 업로드**했을 때는 최신(Tag 기반) 코드가 정확히 보였다. 즉:

- **project_knowledge(GitHub 동기화)는 신뢰하지 말 것** — 최소 몇 세션째 재동기화가 안 된 상태로 추정됨
- **패치 지시는 반드시 사용자가 직접 업로드한 최신 파일 기준으로만** 할 것
- 다음 세션 시작 시, 사용자가 GitHub에 최신 상태를 올리고 프로젝트 지식 "Sync" 버튼을 눌렀는지 먼저 확인 권장

## 3. 핵심 버그 발견 및 수정 — main.py 실 파일 대조에서 나온 것들

지난 세션 끝에 "패치가 실제 코드와 안 맞는다"며 사용자가 `main.py`/`tagging.py`/`model_router.py`/`collectors.py` **실 파일을 직접 업로드**했고, 정밀 대조한 결과 다음 버그들을 발견/수정:

| 버그 | 위치 | 증상 |
|---|---|---|
| `_contains_hangul()` 접두어 누락 | `main.py`(번역 함수 2곳) | `tagging.` 없이 호출 → `NameError` |
| 삭제된 `category` 필드 참조 | `main.py`의 `create_source()` | 출처 수동 등록 시 `TypeError` |
| 삭제된 `subcategory` 필드 참조 | `main.py`의 `/personalization/feedback` | 👍/👎 피드백 저장 시 `AttributeError` |
| `search_query`/`region` 파라미터 미반영 | `/collect/deep-incremental`, `_collect_single_keyword()` | 이전 세션에 이미 드렸다고 생각한 패치가 실제로는 누락 |
| `source.category` 참조 | `collectors.py`의 `GoogleNewsSearchCollector.collect()` | **승격된 소스가 백그라운드 틱마다 조용히 실패** (가장 심각 — 화면엔 안 보이고 fail_count만 계속 올라감) |
| `search_query`/`language` 미반영 | `collectors.py`의 `collect_for_keyword()` | 예전 단순 버전으로 되돌아가 있었음 |
| `translate_keyword_en`/`ko` 프로필 누락 | `model_router.py` | 한글 태그명 번역 시도 시 `ValueError`(등록 안 된 task) |

**교훈**: 대화가 길어질수록 "이미 적용했다고 생각한 패치"와 "실제 파일 상태"가 어긋나는 사고가 반복됐다. **주기적으로 실제 파일을 통째로 다시 업로드받아 대조하는 게, 패치를 계속 쌓는 것보다 훨씬 신뢰도가 높다**는 게 이번 세션에서 명확히 확인됨.

## 4. 장르편집기 — 인라인 수정 기능 + 관련 버그 3종

### 4-1. 기능
- 장르편집기 테이블에서 대분류/중분류/소분류를 **직접 인라인으로 수정** 가능 (`PATCH /genres/{keyword_id}`)
- 미분류 항목이 **항상 테이블 맨 위**에 오도록 정렬 (문자열이 "미분류"든 "Uncategorized"든 명시적 우선순위 판정으로 처리 — 언더스코어 등 문자 순서 트릭은 대문자 알파벳보다 코드값이 커서 오히려 안 통할 수 있음이 확인됨)

### 4-2. 버그 1 — LLM 번역 결과에 목록기호(`-`)가 안 지워짐
`_translate_to_english_tag_name()`이 `strip('"')`만 하고 `-Semiconductor`처럼 붙는 목록기호/번호는 안 걸러냈음. `_clean_llm_short_output()` 신규 추가(정규식으로 `-`, `*`, `•`, `1.` 등 제거)해서 `tagging.py`/`main.py`의 모든 짧은 LLM 출력 후처리에 공용으로 적용.

### 4-3. 버그 2 — 기존 태그를 찾으면 갱신을 안 하고 그냥 반환만 함
`get_or_create_tag()`가 이름으로 기존 태그를 찾으면, 새로 넘어온 major/mid_category로 **갱신을 안 하고 그냥 기존 값을 반환**하고 있었음 — "소분류는 그대로 두고 중분류만 고치는" 흔한 수정이 조용히 무시되던 원인. 값이 다르면 갱신하도록 수정.

### 4-4. 버그 3 — 자기 자신 수정이 "충돌"로 오인됨
4-3을 고치는 과정에서 "이름은 같은데 중분류가 다르면 별도 태그로 분리"하는 로직(아래 5번)까지 같이 만들었는데, 이게 **"자기 자신을 수정하는 것"까지 새로운 충돌로 오인**해서 빈 이름(`''`) 태그를 만들려다 에러가 나는 사고가 있었음. `update_genre()`에서 "지금 이 키워드에 이미 연결된 태그가 바로 그 태그 자신인지"를 먼저 판별해서, 자기 자신이면 충돌 검사 없이 그 자리에서 갱신하도록 수정.

## 5. 소분류 중복 허용 — 중분류가 다르면 같은 이름 허용

**요구사항**: `Food > Chips`와 `Tech > Chips`처럼, 소분류 텍스트는 같아도 **중분류가 다르면 별개 항목으로 등록 가능**해야 함 (단, 대분류+중분류+소분류가 완전히 같으면 안 됨).

**구현**: `Tag.name`(DB에서 유일해야 하는 내부 식별자)만 `"Chips (Tech)"`처럼 구분하고, 사람이 실제로 보는 깨끗한 이름은 `Tag.label_ko`에 별도 보존. 화면(장르편집기/키워드현황 등)과 검색어 조합에는 항상 `label_ko`를 사용.

## 6. 출처관리 ↔ 장르편집기 자동 연동

**요구사항**: 출처관리에 있는 카테고리/소스 이름이 장르편집기에도 자동으로 반영돼야 함. 규칙: 대괄호 카테고리(예: `[Golf] Golf.com`의 `Golf`)를 **중분류**로 배치하고, 그 값이 이미 다른 곳에서 중분류로 쓰이면 **그 대분류를 물려받고**, 없으면 "미분류".

**구현**:
- `/admin/backfill-source-tags`: 기존 소스의 `tag_id` 연결 복구 (1회성)
- `/admin/sync-sources-to-genres`: 대괄호 카테고리를 가진 소스를 장르편집기(Keyword 테이블)에 자동 등록 (1회성)
- `scheduler.py`의 `seed_manual_sources()` 수정: **앞으로 추가되는 고정 소스도 자동으로 이 규칙을 따름**

## 7. 검색 정밀도 개선 — 중분류를 검색어에 반영

**문제 제기**: 소분류(`Samsung Electronics`) 단독으로만 검색하면 반도체 외 다른 사업부 뉴스까지 섞여 들어옴.

**해결**: `search_query`가 명시적으로 없을 때는 `"{소분류} {중분류}"`로 합성해서 검색 (`_build_precise_search_text()`). **`collect_for_keyword()`(직접 검색)와 `_promote_candidate()`(자동 승격) 둘 다 동일 로직 적용**해서 일관성 확보. 명시적 `search_query`(채팅 자동수집이 만든 자연스러운 문구 등)가 있으면 이 합성을 건너뛰고 그대로 사용.

## 8. 한국어 검색 — 지역과 실제 언어 불일치 문제

**문제 제기**: `region="KR"`로 검색해도 Google 뉴스가 영어 기사(로이터 등 통신사 기사)를 섞어서 줌 — "한글로 검색하면 한글 자료만"이라는 요구를 `hl/gl` 파라미터만으로는 못 지킴.

**해결**: 크롤링된 **실제 본문**에 한글이 있는지 우리 쪽에서 한 번 더 검사(`require_korean_content` 플래그, `tagging._contains_hangul()` 재사용). 한국어 검색인데 본문이 영어면 저장하지 않고 건너뜀(실패 처리 아님, 그냥 스킵).

## 9. 페이월 감지 추가

기존엔 쿠키/동의배너(`_looks_like_consent_boilerplate`)만 걸렀는데, TradingView/Reuters 신디케이션처럼 **"Get unlimited access to articles..."** 같은 구독 유도 문구가 본문 대신 저장되는 사례 발견. `_looks_like_paywall_boilerplate()` 신규 추가(동일한 "힌트 여러 개 동시 검출" 원칙), `classify_block_reason()`에 `"페이월차단"` 사유 추가.

## 10. UI 재구성 — 장르편집기로 출처관리/출처평가 통합

**요구사항**: 화면이 두 줄로 붐벼서 답답함 → 출처관리/출처평가를 장르편집기 안에 탭으로 넣고 메인 화면에서 없앰.

**구현**:
- `SourceManager.jsx` 신규 생성 — App.jsx 안에 거대한 인라인 블록으로 있던 출처관리 로직/UI 전체를 추출 (자체 토글 버튼 없이, 마운트되면 바로 데이터를 불러오는 임베드 전용 컴포넌트)
- `SourceEvaluation.jsx`에 `embedded` prop 추가 — true면 자체 버튼/오버레이 껍데기 없이 콘텐츠만 렌더링
- `GenreEditor.jsx`에 탭 3개(🗂️ 장르 목록 / ⚙️ 출처 관리 / 📊 출처 평가) 추가
- `App.jsx`에서 `showSourceManager` 관련 state/함수/렌더링 블록 전체, `<SourceEvaluation />` 독립 렌더링, 관련 헬퍼 함수 6개(`isAutoOrigin` 등) 전부 제거

## 11. 레이아웃/색상 조정 (여러 차례 시행착오)

- **화면 진짜 왼쪽 끝까지 붙이기 시도 → 실패 → 원상복구**: `position:relative + left:50%`, `margin-left: calc(50% - 50vw)`, JS `getBoundingClientRect()` 실측 방식까지 순서대로 시도했으나 전부 `.control-panel`의 flex 레이아웃과 얽혀 어긋났고, 마지막엔 **줄 전체가 화면에서 사라지는 회귀**까지 발생. **결국 트릭을 전부 제거하고 다른 요소들과 같은 여백을 쓰는 방식으로 안전하게 원상복구.**
- 대신 `.app-container`의 `max-width`를 `1200px` 고정값에서 **`95vw`(화면 폭 비례)로 변경** — 부작용 없이 전체 화면을 넓게 쓰는 목적을 달성하는 훨씬 안전한 대안으로 확인됨
- 검색창+실시간수집+선호장르선택+검색주기설정+장르편집기를 **두 줄 → 한 줄로 병합** (출처관리/출처평가가 빠지면서 자리가 생김)
- 버튼 색상: 실시간수집(빨강 계열), 검색주기설정(보라 계열)로 변경
- 출처관리 테이블 높이를 여러 차례 확대 시도(540px → 640px → 900px) — 컨테이너가 `overflow-y: auto`로 이미 내부 스크롤 박스라, 높이를 키우면 `.stats-section`(총 저장된 출처)이 자동으로 그만큼 아래로 밀리는 정상적인 문서 흐름임을 확인(별도 위치 조정 불필요)

## 12. 키워드별 현황 — 표시 형식 및 구조 전면 개편 (중요, 순서대로 3단계)

### 12-1. 1차: `소분류(중분류)` → `중분류(소분류)` 형식 변경
소분류 중복 방지로 내부적으로 구분된 태그(`"Worldwide (Travel)"`)의 **내부 식별자를 그대로 화면에 노출**하고 있던 문제. `label_ko`(사람이 보는 깨끗한 이름) 기준으로 `중분류(소분류)` 형식 표시하도록 수정.

### 12-2. 버그 — 표시 문구를 그대로 검색 키로 써서 조회 실패
1차 수정 직후, **화면 표시 문구를 그대로 `/articles?keyword=`에 넘겨서** 실제 DB 이름과 안 맞아 조회가 실패하는 문제 발견("조건에 일치하는 데이터가 없습니다"). **표시(label)와 조회 키(tag_id)를 분리** — `/stats/keywords`가 `{label, tag_id, count}` 구조로 반환, `/articles`에 `tag_id` 쿼리 파라미터 신규 지원, 프론트는 항상 `tag_id`로 조회.

### 12-3. 2차: 완전 재설계 — 중분류 1차 그룹 + 소분류 펼치기
사용자가 "소분류를 평면 나열하지 말고, 중분류 버튼을 누르면 그 안의 소분류가 펼쳐지게" 요청. `/stats/keywords` 응답을 `{mid_categories: [{mid_category, total_count, sub_categories: [...]}]}` 구조로 전면 개편, 프론트도 2단계 버튼(중분류 클릭 → 소분류 줄이 아래에 나타남 → 소분류 클릭 → 기사 목록)으로 재구성.

## 13. ⚠️ 가장 중요한 근본 버그 — 기사 삭제 시 `ArticleTag` 고아 레코드

**증상**: 장르편집기에서 키워드를 삭제했는데도(`Samsung Electronics Semiconductors`), 키워드별 현황에 "3건"으로 계속 나타나고, 클릭하면 "데이터 없음".

**원인**: `delete_article()`/`delete_keyword()`가 **`Article` 행만 지우고 `ArticleTag`(다중 태그 연결) 행은 안 지웠음.** SQLite는 이런 연결을 자동으로 같이 안 지워줘서(cascade 아님), 기사는 사라졌는데 "이 기사가 이 태그다"라는 연결 정보만 유령처럼 남음 — 건수 집계는 이 유령 연결까지 세고, 실제 조회는 `Article`과 조인하니 정직하게 0건.

**수정**:
1. `delete_article()`/`delete_keyword()` 양쪽에서 기사 삭제 시 `ArticleTag`도 함께 삭제하도록 수정 (앞으로의 삭제부터 재발 방지)
2. `/stats/keywords`가 `Article`과도 조인하도록 변경 — 이미 생긴 유령 연결도 별도 정리 없이 자동으로 화면에서 걸러짐
3. `/admin/purge-orphaned-tags` 1회성 정리 엔드포인트 신규 추가 — **실행 결과 18건 정리 확인됨**

## 14. 부수적으로 반복 발견된 것 — 제로폭 공백(U+200B) 버그

`CrawlToggleButton.jsx`(이전 세션), `GenreEditor.jsx`, `App.jsx`, `SourceEvaluation.jsx`에서 **동일한 제로폭 공백 버그가 계속 발견됨**. `GenreEditor.jsx`는 전체 재작성 시 정리했고, `SourceEvaluation.jsx`는 `embedded` 패치 시 정리 지시했으나 **실제 적용 여부 미확인**. `App.jsx`는 `<span className="stats-label">` 등 위치 확인만 하고 아직 정리 안 함.

## 15. 겪은 사소한 실수들 (패치 적용 과정)

- `def _segment_article_for_translation(...):    """`처럼 함수 시그니처와 독스트링이 줄바꿈 없이 붙어버려 `IndentationError`
- `@@app.get("/articles")`처럼 데코레이터 `@`가 중복돼 `SyntaxError`
- `return (\n        return (`처럼 `return`이 중복돼 `Unexpected token`
- **공통 교훈**: 큰 함수/블록을 패치할 때 "찾을 부분"과 "바꿀 부분"의 경계가 애매하면 실수가 반복됨 — 앞으로도 **가능하면 함수/블록 전체를 정확히 지정**하고, 애매하면 적용 전 `grep`/`sed -n`으로 주변 문맥을 먼저 확인하는 습관 유지 필요.

---

## 16. 파일 인벤토리 (07번 이후 변경분)

| 파일 | 이번 세션에서 한 일 |
|---|---|
| `main.py` | 버그 6종 수정(3번), `_translate_text_llm`/`_resolve_search_query_and_language`(지역 강제), `_clean_llm_short_output`, `update_genre` 자기충돌 수정, `sync_sources_to_genres`/`backfill_source_tags`/`purge_orphaned_tags` 신규, `/stats/keywords` 2단계 구조 개편, `/articles`에 `tag_id` 지원, `delete_article`/`delete_keyword`의 `ArticleTag` 정리 |
| `tagging.py` | `_clean_llm_short_output`, `get_or_create_tag` 갱신 로직 + 중복 소분류 분리 로직, `display_name_for_tag` |
| `collectors.py` | `collect()`/`collect_for_keyword()` 버그 수정, `_build_precise_search_text`, `require_korean_content` 필터 |
| `scheduler.py` | `_promote_candidate` 정밀검색어+지역 반영, `seed_manual_sources` 자동 장르편집기 등록 |
| `content_utils.py` | `_looks_like_paywall_boilerplate`, `_select_crawled_markdown`/`classify_block_reason` 확장 |
| `model_router.py` | `translate_keyword_en`/`translate_keyword_ko` 프로필 추가 |
| `hf-frontend/src/SourceManager.jsx` | 신규 생성 (App.jsx에서 추출) |
| `hf-frontend/src/SourceEvaluation.jsx` | `embedded` prop 추가 |
| `hf-frontend/src/GenreEditor.jsx` | 인라인 수정 기능, 탭 3개 추가 |
| `hf-frontend/src/App.jsx` | 출처관리 블록/state/함수 전체 제거, 레이아웃 여러 차례 조정, 키워드현황 2단계 구조 반영, `handleStatClick`/`fetchArticles` tag_id 지원 |
| `App.css` | 여러 스타일 조정(테이블 높이, 탭, 소분류 줄, `app-container` 폭) |

## 17. 미해결/검증 필요 항목

- [ ] **키워드별 현황 2단계 구조가 실제로 화면에서 정상 작동하는지 최종 확인 안 됨** — 유령 데이터 18건 정리까지는 확인됐으나, 중분류 클릭→소분류 펼침→소분류 클릭→기사 표시까지의 전체 흐름은 사용자 확인 대기 중
- [ ] `SourceEvaluation.jsx`의 제로폭 공백 정리가 실제로 적용됐는지 미확인
- [ ] `App.jsx`의 제로폭 공백(`stats-label` 등)은 아직 정리 지시조차 안 함
- [ ] `App.jsx`에서 출처관리 관련 코드 제거가 정확히 다 됐는지(`grep -n "showSourceManager"` 등으로) 재확인 필요
- [ ] project_knowledge(GitHub 동기화) 최신화 필요 — 다음 세션 시작 전 사용자가 직접 동기화 권장

## 18. 새 대화 시작 시 사용법

1. 이 문서(`PROJECT_STATUS_08_...md`) + `PROJECT_STATUS_07_tag_system_complete.md`를 함께 첨부
2. **project_knowledge보다 실제 파일을 직접 업로드받아 시작하는 걸 강력 권장** (16번 항목 참고 — 이번 세션에서 project_knowledge가 계속 낡은 코드를 반환했음)
3. 17번(미해결/검증 필요 항목)부터 확인
4. 특히 "키워드별 현황 2단계 구조" 최종 검증을 최우선으로 진행
