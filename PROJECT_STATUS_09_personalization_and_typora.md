# PROJECT_STATUS_09_personalization_and_typora.md

이번 세션에서 다룬 내용을 기능 단위로 정리했습니다. 시간순이 아니라 주제별로
묶었으니, 실제 커밋/적용 순서와는 다를 수 있습니다.

---

## 1. 출처 평가 (Source Evaluation)

- `source_scoring.py` 신규 — 소스 스코어 계산(수집 건수 40 + 안정성 30 + 최신성 15 + 콘텐츠 밀도 15, 총 100점)
- `SourceEvaluation.jsx` — "장르별 출처 평가" 패널. 종합 TOP 50 + 장르별 버튼 전환 방식
- 표시 이름 정정: "출처" → **"해당자료"**로 변경, 링크도 `Source.url`(검색용 URL) 대신 **해당자료 URL**(가장 최근 기사의 실제 원문 링크)로 연결

## 2. 장르 체계 (대분류 / 중분류 / 소분류)

- `Keyword`에 `major_category`, `mid_category` 필드 추가
- `taxonomy.py` — 앱 기동 시 초기 관심사 키워드 자동 시딩
- **데이터 편집**(`GenreEditor.jsx`) 모달 — 탭 구조로 통합:
  - 🗂️ 장르 목록 (대/중/소분류 등록 + 인라인 수정)
  - ⚙️ 출처 관리 (`SourceManager.jsx`)
  - 📊 출처 평가
  - ⏱️ 검색주기설정 (`IntervalSettings.jsx` — 전체 키워드 일괄 적용 방식, `PUT /keywords/interval/bulk`)
  - 🛠 관리자 (`AdminPanel.jsx` — 아래 4번 참고)
- **Admin으로 로그인했을 때만 "데이터 편집" 버튼 자체가 노출됨**

## 3. UI 스크롤/레이아웃 통일

- 장르목록 탭의 "고정 헤더 + `genre-editor-table-wrap`만 내부 스크롤" 패턴을 출처관리/출처평가/GitHub 저장소에 동일 적용
- 출처관리 표에서 구분/주기 열 삭제(주기는 이제 검색주기설정에서 일괄 관리), URL 칸 `table-layout:fixed`로 가로 넘침 해결

## 4. 미분류 키워드 → 관리자 승인 워크플로우

- `KeywordSearchInterest` 테이블 신규 — 실시간 검색/직접입력으로 만들어진 **미분류**(자기 이름을 대분류로 쓰는 placeholder) 키워드를 누가 검색했는지 기록
- `AdminPanel.jsx` — 미분류 키워드 목록 + 대분류/중분류 지정 → 저장 시 관심 있던 사용자들에게 **소급으로 선호 반영**
- `personalization.py`의 `get_profile()`을 신호 생성 시점 스냅샷 대신 **태그의 현재 분류를 그때그때 조회**하도록 수정 (append-only 원칙 유지하면서 소급 반영 가능하게)

## 5. 다중 사용자 시스템

- `UserRegister.jsx` 전면 개편 — **가입**(신규 ID) / **로그인**(기존 ID 선택) / **로그아웃** 분리, `localStorage` 키는 `hf_user_id`
- `GET /users/list` 신규
- **버그 수정**: `register_user_and_backfill()`이 예전엔 가입자마다 매번 익명 데이터를 몽땅 물려줘서, 두 번째 이후 가입자도 이미 선호가 있는 것처럼 보이던 문제 → **"시스템 최초 가입자"에게만 1회** 물려주도록 수정

## 6. 개인화 — 선호 장르 (메인화면 게이팅)

- `UserGenrePreference` 테이블 신규 — **대분류 단위**로 선호 저장(태그 단위 아님 → 대분류 밑에 나중에 뭐가 생겨도 자동 반영)
- `GenrePreferenceSelector.jsx` 전면 재작성:
  - 하드코딩된 22개 목록 → 실제 등록된 대분류를 `GET /genres/major-categories`로 동적 로드(한글 라벨)
  - 직접입력창 1개로 통합 → 입력하면 **즉시(실시간) 수집**까지 실행 (`_collect_single_keyword` 재사용)
  - 다시 열면 이미 선택한 항목이 체크 표시됨, "모두" 옵션
- `GET /articles`: `user_id` 있으면 그 사용자의 선호 대분류로 필터링, 선호 없으면 빈 배열 → 프론트가 "선호 장르를 선택해주세요" 안내 표시
- **Admin은 모든 게이팅을 건너뛰고 기본값으로 전체 열람**

## 7. Typora 연동 + 개인저장소(Vault)

- `main.py`: 기사/GitHub 상세문서 각각 `POST /articles/{id}/edit-in-typora`·`import-from-typora`, `POST /github/repos/{id}/edit-in-typora`·`import-from-typora`
- GitHub 상세문서는 4개 섹션을 `##` 헤더로 합쳐 하나의 md로 왕복, **새로 추가한 `##` 섹션은 `extra_notes`에 자동 보존**
- `ArticleCard.jsx`: 인라인 편집기 제거, "📝 Typora 편집" + "📥 {사용자} 저장소" 버튼으로 통일 (GitHub와 동일 패턴)
- **Vault를 `VAULT_DIR/{user_id}/`로 완전히 사용자별 분리** (예전엔 전체 공용 폴더였음)
- Vault 파일에 `<!-- hf-source: article:123 -->` 숨김 주석을 심어서, **개인저장소 "새로고침" 버튼이 원본 DB까지 역으로 동기화**
- `PersonalRepository.jsx` 신규 — 메인화면 "📂 {사용자} 저장소" 버튼, 폴더/파일명/원본제목/최종수정일시 표, 파일명 클릭 시 Typora

## 8. GitHub 저장소 기능

- 데이터편집 탭에서 독립 → 메인화면 자체 버튼+모달로 전환
- **관심분야 개인화**: `GET /github/fields`(그동안 모아온 태그) + `POST /github/select-fields`(선택/직접입력) + "내 관심분야만 보기" 토글, "모두" 옵션
- **직접입력은 즉시 GitHub 검색+수집** 실행
- **🔄 크롤링 재개** (Admin 전용) — 스타 5만+ 또는 "최근 1주 생성 + 이미 1000+ 스타"(급성장 근사치) 기준 자동 발굴. **토글 방식**(재클릭 시 `/collect/cancel`로 중단)
- 표시 규칙 강화: **분야/응용분야/연관성(R)은 반드시 채워짐** (H/M/L 3단계, 구성요소는 연관성 L일 때만 공란). 상세보기 열 때마다 이 4항목을 점검해서 부실하면 LLM 재생성
- 상세보기 로딩 중 텍스트 스피너(CSS 애니메이션이 시스템 설정에 막힐 수 있어 JS `setInterval` 방식으로 구현)

## 9. 개인화 신호 정확도

- `collectors.py`: `collect_for_keyword()`가 `user_id`를 받아 `classify_and_store()`까지 전달 — 실시간 검색으로 수집된 기사의 개인화 신호가 **실제로 그 사용자에게 귀속**되도록 수정 (예전엔 항상 익명)
- `_collect_single_keyword()` 한 곳에 `UserGenrePreference` 등록 로직을 중앙화해서, 실시간 검색이든 선호장르 직접입력이든 어느 경로로 들어와도 자동으로 개인 취향에 반영

---

## 아직 남아있는 것 / 다음에 볼만한 것

- `/keywords`(등록+즉시수집), `/keywords/{id}/recollect` 두 경로는 아직 `user_id` 미연결
- Typora "가져오기"는 수동 버튼 클릭 방식 — 자동 감지(파일 watcher)는 아님
- GitHub "크롤링 재개"의 "최근 1주 급상승" 기준은 GitHub 공식 API 한계로 "최근 생성 + 이미 인기"로 근사한 것 — 오래된 레포의 급상승은 못 잡음
