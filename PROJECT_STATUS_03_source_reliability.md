# PROJECT_STATUS_03 — 출처관리 정합성 + 크롤링 안정성 (BlockList / 폴백 체인)

> 관련 마스터플랜 장: 마스터플랜에 대응 장 없음 (신규 도출 요구사항 — 대화 중 발견된 버그
> 수정 + 크롤링 성공률 개선 작업)
> 시작일: 2026-08-08 (KST) / 마지막 갱신: 2026-08-08 (KST)
> 이전 단계: `PROJECT_STATUS_02_personalization.md`

---

## 1. 이번 단계의 목표

두 가지 축의 작업을 한 세션에서 진행했다.

1. **출처관리 정합성 버그 수정** — "출처관리에 등록은 됐는데 건수가 0건"인 항목이 계속
   쌓이는 문제의 근본 원인을 찾아 고치고, 그 부산물로 "크롤링이 막혀 기사를 못 가져온
   도메인"을 사용자가 직접 확인·정리할 수 있는 BlockList 기능을 새로 만들었다.
2. **크롤링 성공률 개선** — Google News RSS(XML)만 성공하고 실제 언론사 웹사이트 본문은
   전부 실패하는 문제를 진단하고, 실패 유형(JS 렌더링 필요 / TLS 지문 차단 / Cloudflare
   챌린지)별로 다단계 폴백 체인을 구축했다.

## 2. 핵심 아키텍처 결정

### 2-1. "발견됨"과 "저장됨"을 분리 (출처관리 0건 버그의 핵심)

`GoogleNewsSearchCollector._fetch_and_save()`가 RSS 항목에서 도메인을 파싱하자마자
`discovered` 리스트에 추가하고, 그 이후에야 중복/크롤링 실패 여부를 판정하는 순서였다.
`scheduler.py`의 `PROMOTE_THRESHOLD=1` 때문에 "발견"만 되면 즉시 Source로 승격되므로,
**한 번도 기사를 저장 못 한 도메인도 출처관리에 등록되는** 구조적 결함이었다. 실제로
기사를 1건이라도 저장(`new_count += 1`)한 뒤에만 `discovered`에 추가하도록 순서를
바꿔서 근본 해결했다.

### 2-2. BlockList — "실패도 기록으로 남긴다"

2-1을 고치고 나면 "크롤링이 막혀서 저장 못 한 도메인"은 이제 출처관리에 아예 안 뜨게
되는데, 사용자가 수작업으로 어떤 사이트가 왜 막히는지 확인하고 싶다는 요구가 있어
정반대 방향의 기능을 추가했다: 실패 원인을 `Source.category="BlockList"` 행으로 **의도적으로
남기고**, 실패 사유를 사람이 읽을 수 있는 한글 키워드(`block_reason`)로 분류해 UI에
노출한다. 삭제하면 그 도메인을 `BlockedDomain` 테이블에 영구 기록해서, 이후 수집에서
같은 도메인이 다시 뜨지 않도록 했다 — "한 번 판단한 걸 매번 다시 만나지 않게" 하는 원칙.

### 2-3. 크롤링 3단계(→2단계로 축소) 폴백 체인

- 1차: `crawl4ai`(Playwright 헤드리스 브라우저) — JS 렌더링 필요한 페이지에 강함.
  `magic`/`simulate_user`/`override_navigator` 옵션을 켜서 동의배너·기본 봇탐지 대응.
- 2차: `Trafilatura` + `curl_cffi`(Chrome TLS/HTTP2 지문 위장) — 순수 HTTP GET이라
  브라우저보다 지문이 단순해 오히려 더 잘 통과하는 사이트가 있고, TLS 핸드셰이크
  단계(JA3)에서 걸러지는 걸 막기 위해 `curl_cffi`로 실제 Chrome 지문을 재현.
- 3차(도입 후 제거): `nodriver`(로컬 실제 Chrome을 CDP로 직접 제어, Cloudflare Turnstile
  같은 "투명 챌린지" 우회 목적) — Python 3.14 환경에서 라이브러리 내부 파싱 에러로
  전면 실패하여 제거. 1·2차만으로도 Google 외 다수 사이트가 정상 수집됨을 확인.

## 3. 겪은 버그와 교훈

- **"발견됨(discovered)"과 "저장됨(new_count)"을 같은 시점으로 착각한 게 근본 원인**이었다.
  겉보기엔 사소한 코드 순서 문제였지만, 사용자가 "DB를 지우고 다시 해도 0건이 나온다"고
  보고한 것 자체가 중요한 단서였다 — 초기화 여부와 무관하게 재현된다는 건 데이터
  누적 문제가 아니라 **매 수집마다 반복되는 로직 자체의 버그**라는 뜻이었다.
- **AttributeError 재발 패턴**: `models.py`에 필드를 추가하는 diff를 안내했지만 실제로는
  적용되지 않았거나 `Article` 클래스에 잘못 들어간 채로 재현됨. 코드 수정을 안내할 때
  "찾을 부분/바꿀 부분"의 정확한 위치(어느 클래스 안인지)를 명시해도, 실제 적용 여부는
  `grep -n`으로 직접 확인시키는 절차가 필요했다.
- **React 상태 관리 - "필터"와 "지금 화면에 떠 있는 것"을 같은 변수로 겸용하면 위험**:
  검색창의 `keyword` state가 "표시 필터"이자 "다음 수집 대상 키워드"를 겸하고 있어서,
  실시간 수집이 끝나고 `fetchArticles(keyword)`를 다시 부르면 그 필터에 안 걸리는,
  지금 편집 중인 기사가 화면에서 통째로 사라지는 문제가 있었다. `editingIdsRef`로
  현재 편집 중인 기사 id를 별도로 추적해, 새로 받아온 목록에 없어도 병합해서 보존하는
  방식으로 해결.
- **`eventSourceRef` 실수 삭제**: 편집 관련 ref를 추가하는 과정에서 기존
  `eventSourceRef` 선언 줄이 함께 지워져 `Uncaught ReferenceError`가 남. diff를
  줄 단위로 안내해도 인접한 다른 선언을 실수로 건드릴 수 있다는 사례 — 이후 diff는
  "찾을 부분"에 인접 컨텍스트를 충분히 포함시켜 재발 방지.
- **크롤링 실패 100:0(Google만 성공) 진단 과정**: 처음엔 "안티봇이 강해서"라고
  가정했으나, 실제로는 Playwright/Chromium 자체는 정상(`SUCCESS: Example Domain`
  확인됨)이었고 진짜 원인은 개별 사이트의 Cloudflare JS challenge였다. **가정으로
  단정하지 않고 단계별로(패키지 설치 → 브라우저 실행 → 실제 크롤링 로그) 하나씩
  검증**해서 진짜 원인(사이트별 안티봇 방어 수준 차이)에 도달함.
- **nodriver + Python 3.14 비호환**: `network.py, line 1345`에서 나는
  `Non-UTF-8 code ... PEP 263` 에러는 크롤링 대상 사이트와 무관한, **라이브러리
  소스 코드 자체를 인터프리터가 못 읽는** 에러였다. 최신 Python 버전(3.14)을 쓰는
  프로젝트에서는 서드파티 라이브러리 호환성을 먼저 의심해야 한다는 교훈. 성공/실패
  확률을 매기지 않고 "URL과 무관하게 같은 줄에서 재현되는지"를 먼저 확인해 라이브러리
  문제로 확정한 뒤, 비용 대비 효과가 낮다고 판단해 제거(Python 버전을 낮추는 대안은
  기록만 남기고 보류).

## 4. 파일 인벤토리

| 파일 | 이번 단계에서 한 일 | 상태 |
|---|---|---|
| `models.py` | `SourceOrigin.BLOCKED` 추가, `Source.block_reason` 필드 추가, `BlockedDomain` 테이블 신규 추가 | 안내 완료, **실제 적용 여부 확인 필요** |
| `migrate_db.py` | `SOURCES_MIGRATIONS`/`migrate_sources()` 추가 (block_reason 컬럼 마이그레이션) | 안내 완료, **적용 확인 필요** |
| `main.py` | `/sources` 응답에 `block_reason` 추가, `delete_source`에서 BlockList 삭제 시 `BlockedDomain` 기록 로직 추가, `urlparse` import 추가 | 안내 완료, **적용 확인 필요** |
| `collectors.py` | `discovered.append()` 위치를 저장 성공 이후로 이동(핵심 버그 수정), `_record_blocked_source()` 신규, `blocked_domains` 조회 후 루프 진입 시 스킵 로직 추가 | 안내 완료, **적용 확인 필요** |
| `content_utils.py` | 크롤링 실패 로그에 `exc_info` 추가, `classify_block_reason()` 신규, Trafilatura(2차)+curl_cffi 폴백 추가, nodriver(3차) 추가 후 제거 | 안내 완료, **적용 확인 필요** (특히 nodriver 제거 diff는 이번 대화 마지막에 안내함 — 실제 삭제 여부 미확인) |
| `requirements.txt` | `trafilatura`, `curl_cffi` 추가. `nodriver`는 추가했다가 제거 | 안내 완료, **적용 확인 필요** |
| `App.jsx` | 주기 셀 우측정렬+단위 표기, BlockList 카테고리 분기 렌더링, `editingIdsRef` 추가, `fetchArticles` 병합 로직 수정 | 안내 완료, **적용 확인 필요** (직전 세션에서 `eventSourceRef` 삭제 사고가 있었으므로 특히 꼼꼼히 확인 필요) |
| `App.css` | `.source-table-block-reason` 스타일 추가 | 적용 확인됨 (사용자가 직접 붙여넣음) |

## 5. 완료된 기능

- [x] 출처관리 "건수 0" 근본 원인 진단 및 수정 방향 확정 (discovered/저장 시점 분리)
- [x] BlockList 카테고리 설계 — 크롤링 실패 도메인을 사유(키워드)와 함께 기록
- [x] BlockList 삭제 시 `BlockedDomain`에 영구 기록해 재검색 방지하는 로직 설계
- [x] 소스관리 테이블 "주기" 열 우측정렬 + 단위 라벨 위치 수정
- [x] 실시간 수집 후 편집 중인 기사가 화면에서 사라지는 버그 원인 규명 및 수정 방향 확정
- [x] 크롤링 1차(crawl4ai 안티봇 옵션) + 2차(Trafilatura+curl_cffi) 폴백 체인 설계
- [x] Playwright/Chromium 정상 동작 확인 (`SUCCESS: Example Domain`)
- [x] nodriver 3차 폴백 시도 → Python 3.14 비호환 확인 → 제거 결정

## 6. 남은 작업 / 다음 단계 후보

### 최우선 — 실제 적용 여부 전수 확인
- [ ] 위 "4. 파일 인벤토리"의 **모든 항목이 실제 파일에 반영됐는지** `grep`으로 재확인
      (이번 세션은 diff 안내 위주로 진행되어, 실제 반영 여부를 코드로 직접 확인하지 못함)
- [ ] 서버 재시작 후 에러 없이 뜨는지 확인 (`AttributeError`, `ReferenceError` 재발 여부 특히 주의)
- [ ] `nodriver` 관련 코드/의존성이 실제로 깨끗하게 제거됐는지 확인
      (`requirements.txt`, `content_utils.py` 양쪽 다)

### 기능 검증 (8번 체크리스트로 아래 옮겨서 진행 예정)
- [ ] 새로 등록한 키워드로 실시간 수집 시 출처관리에 건수 0인 항목이 더 이상 생기지 않는지
- [ ] 크롤링 실패 도메인이 실제로 BlockList 카테고리에 뜨고, `block_reason`이 표시되는지
- [ ] BlockList 항목 삭제 시 같은 도메인이 이후 수집에서 다시 시도되지 않는지
- [ ] 실시간 수집 도중/직후에도 편집 중인 기사 내용이 화면에서 사라지지 않는지
- [ ] Google News 외 사이트(Cloudflare 안 걸린 곳)가 실제로 수집되는지

### 우선순위 낮음 / 추후 검토
- [ ] Python 3.12 별도 venv로 nodriver 재도입 여부 (Cloudflare 강한 사이트까지 뚫고
      싶다면 검토 — 지금은 보류 상태, 비용 대비 효과 낮다고 판단)
- [ ] `_extract_source()`의 제목-파싱 폴백 경로(도메인에 `.com` 등이 안 붙는 경우)로 인해
      BlockList의 일부 URL이 클릭해도 실제 사이트로 안 열릴 수 있음 — 아직 미해결
- [ ] `PROJECT_STATUS_01`의 "로직 변경 없는 파일 분해" 항목이 계속 미뤄지고 있음 —
      `main.py`/`App.jsx`가 이번 세션에서도 계속 커졌으므로, 다음 검증 완료 후 우선순위
      재검토 필요

## 7. 새 대화 시작 시 사용법

1. `PROJECT_STATUS_INDEX.md` + 이 파일(`PROJECT_STATUS_03_source_reliability.md`)을 첨부
2. **반드시 실제 최신 `models.py`, `main.py`, `collectors.py`, `content_utils.py`,
   `App.jsx`, `requirements.txt`를 함께 첨부** — 이번 단계는 diff 안내 위주로
   진행되어 실제 반영 여부가 불확실하므로, 다음 대화에서 가장 먼저 할 일이 "각 diff가
   실제로 적용됐는지 파일을 보고 확인하는 것"이다.
3. "6번 최우선 작업(실제 적용 여부 확인)부터 이어서 하고 싶다"고 요청

## 8. 검증 체크리스트 (다음 단계 진행 전 필수)

**아래 항목을 실제로 하나씩 실행/확인하기 전까지는 다음 번호(04)의 새 단계를 시작하지 않습니다.**

- [ ] `models.py`에 `SourceOrigin.BLOCKED`, `Source.block_reason`, `BlockedDomain`
      테이블이 실제로 존재하는지 (`grep -n` 확인)
- [ ] 서버가 에러 없이 기동되는지 (`AttributeError`, `ReferenceError` 등 재발 없음)
- [ ] `migrate_db.migrate_sources()`가 lifespan에서 실제로 호출되고,
      `sources` 테이블에 `block_reason` 컬럼이 생겼는지 (`PRAGMA table_info(sources)`)
- [ ] 크롤링이 실패하는 키워드로 실시간 수집을 돌렸을 때, 출처관리에 건수 0인 항목이
      더 이상 새로 생기지 않는지
- [ ] 그 대신 BlockList 카테고리에 실패 도메인 + 사유 키워드가 뜨는지
- [ ] BlockList 항목을 삭제한 뒤, 같은 도메인이 다음 수집에서 실제로 스킵되는지
      (로그에 크롤링 시도 자체가 안 찍히는지)
- [ ] 소스관리 테이블 "주기" 열이 우측정렬되고, 숫자 입력창 바로 오른쪽에 "시간"이
      표시되는지
- [ ] 실시간 수집을 돌리는 동안 다른 기사를 편집해보고, 수집이 끝난 뒤에도 편집
      내용이 화면에서 사라지지 않는지
- [ ] Google News RSS가 아닌 일반 언론사 사이트(예: TechCrunch, Hugging Face Blog 등
      Cloudflare가 안 걸린 곳)가 실제로 본문까지 수집되는지
- [ ] `nodriver`가 `requirements.txt`와 `content_utils.py` 양쪽에서 완전히 제거됐는지
- [ ] `content_utils.py`의 크롤링 실패 로그에 `exc_info`가 포함되어, 향후 새로운
      실패 유형이 생겨도 원인 파악이 빠른지

전부 체크되면:
1. 위 표의 미체크 항목을 모두 체크
2. 이 문서 최상단에 `> 검증 완료: YYYY-MM-DD (KST)` 한 줄 추가
3. `PROJECT_STATUS_INDEX.md`의 `03`번 행 상태를 `검증 완료 ✅`로 갱신

검증 중 발견됐지만 지금 단계에서 해결하지 않기로 한 문제가 있다면, 위 "6. 남은 작업"에
사유와 함께 기록하고 넘어갑니다 (조용히 넘어가지 않기).
