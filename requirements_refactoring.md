로컬 AI 에이전트 플랫폼 구축 가이드 (리팩토링판)


작성일: 2026-08-04 22:23 (KST) · 리팩토링: 2026-08-06 05:47 (KST) · 최종 수정: 2026-08-06 18:07 (KST)
환경: FastAPI 백엔드 + Vite React 프론트엔드 + Ollama/Qwen (M1 Max 32GB, 내장 2TB SSD)

리팩토링 노트: 원본은 37개 장이 대화 순서대로 나열되어, 특히 도구 평가 12건이 같은 판단 원칙을 매번 반복 서술했습니다. 이 판은 5개 파트로 재편하고, 반복되는 평가 원칙을 Part 4 서두에 1회만 명시한 뒤 각 도구는 매트릭스+짧은 근거로 압축했습니다. 내용은 원본에서 손실 없이 재배치했으며, 각 항목에 원본 장 번호를 [원#]로 남겨 추적 가능하게 했습니다.
⸻
전체 전략 로드맵 (3단계)


전환 트리거: 1→2단계 = 실험한 스킬 중 "상시 자동화할 가치가 있다"는 판단이 설 때. 2→3단계 = 수익이 실제 발생하거나 명확히 예측 가능해질 때(미니서버의 동시성·신뢰성·단일장애점 한계가 실제 걸림돌이 되는 시점). 전자상거래(구매대행·쇼핑몰)는 어느 단계에서도 별도 프로젝트로 분리.

운영 전제 (1단계 기준)

개인 전용 (멀티유저 아님) — 인증/권한 체계 최소화
데이터는 우선 내장 2TB SSD에 저장 (외장 SSD는 추후 옵션, 9장)
상용 서비스화가 목표 아님(실험/학습 목적) — 상용화는 3단계에서 별도 인프라로 추진
맥북 온디맨드 운영 확정 — 상시 서비스는 비용·보안·1인 운영 부담으로 보류, 이용할 때만 가동 (근거: 18장)
자동 업데이트 없음 — Claude(API/Code)는 필요시 수동 호출로만 사용
⸻
PART 1. 핵심 아키텍처


1. 스택 평가 및 모델 구성
[원1]

FastAPI + Vite React + Ollama(Qwen) 조합 적합. 32GB 유니파이드 메모리가 "여러 무거운 에이전트 동시 실행"의 한계점
채택 모델: 코딩 Qwen3-Coder 30B-A3B(~24GB) / 비전 Qwen3-VL 30B-A3B / 경량 라우팅 Qwen3.5 9B(~7GB)
실측치(9B=38tok/s, 30B=55tok/s)는 Ollama 0.19+의 MLX 네이티브 통합 기준 M1 Max(비-M5) 세대 특성상 정상 범주로 판단됨 [원31 정정]

2. 메모리 제약 극복 전략
[원2]

모델 상주 최소화: OLLAMA_MAX_LOADED_MODELS=1, 짧은 keep_alive
경량 라우터(9B 상시) + 온디맨드 전문 모델(30B) 구조가 플랫폼의 중심 설계 원칙
작업 큐로 직렬화(Redis+RQ), 컨텍스트는 청크 단위 RAG로 관리(num_ctx 작업별 조정)
실험 후보 — Liquid AI LFM: MIT CSAIL 스핀아웃 Liquid AI의 초경량 온디바이스 모델군(예: LFM2.5-350M, 서브-1GB급 "프론티어" 추론 주장), MLX/llama.cpp/ONNX/CoreML 네이티브 지원. 2026.8 MacPaw와 파트너십 발표(Eney 비서 통합)로 온디바이스 Mac AI 트렌드 검증됨(Part4 참고). 경량 라우터(현재 Qwen3.5:9B)를 더 가벼운 모델로 대체할 수 있는지 벤치마크 가치 있음 — 23장 교체기준에 따라 먼저 토큰레이트·정확도·메모리사용량 비교 후 결정, 8장 첫 구현 이후 성능튜닝 단계에서 실험 권장

3. 자체구축 vs 오픈소스 원칙 ★
[원3]

핵심 원칙: "인프라는 빌리고, 로직은 짓는다." (이 문서 전체, 특히 Part 4 도구 평가의 판단 기준)


판단 순서: ①이미 안정적 오픈소스 존재? ②아키텍처에 깔끔히 붙는가? ③안 붙거나 메모리 예산 초과 → 직접 경량 구현 ④차별화 포인트 가능성 있으면 → 직접 구축

4. Skills 시스템
[원5]

Skills = 재사용 가능한 절차 지식 폴더(SKILL.md + 선택적 스크립트). 점진적 로딩(평소 이름/설명만 상주, 필요시 전체 로드)
라이브러리와의 차이: 라이브러리="무엇을 할 수 있나(capability)", Skills="언제·왜·어떻게(judgment)"까지 자연어로 포함
실행은 100% 로컬 파일시스템, GitHub은 버전관리·배포 채널일 뿐
로컬 소형 모델 보정: 모델 자율 판단 대신 명시적 라우팅 로직(분류기/규칙)으로 스킬 선택, 도구 실행도 라우터가 명시적 호출

스킬 제작 메타도구 — Skill Creator (Anthropic 공식, anthropics/skills): 스킬을 만들고 테스트하고 정량 평가(eval)로 반복 개선하는 절차 자체를 담은 공식 메타스킬. 새 스킬 제작 시 이걸로 부트스트랩 — 테스트케이스 실행→정량평가→피드백 기반 개정 루프를 표준화. 25장 TODO "평가/벤치마크 체계" 공백을 메우는 도구로 채택. 위험도 사실상 없음(공식 콘텐츠, 순수 프롬프트 방법론).

스킬 부트스트랩 대안 — 로컬 워크플로 레코더 (microsoft/skill-recorder 개념 차용, 도구 자체는 미채택): Microsoft의 skill-recorder는 화면 작업을 녹화해 GitHub Copilot 클라우드로 분석시켜 SKILL.md를 자동 생성하는 도구지만, "분석" 단계에서 화면 이미지·URL·클립보드가 GitHub 클라우드로 전송되어 6장 로컬완결 원칙과 충돌 — 도구 자체는 미채택. 다만 "실제 작업을 시연·기록해서 SKILL.md를 자동 생성한다"는 개념은 유용하여 완전 로컬 버전으로 재구현:
skills/meta/workflow-recorder/
SKILL.md
screen_capture.py # 로컬 화면 녹화(ffmpeg 등)
whisper_narration.py # whishper(15장A)로 내레이션 로컬 전사
reconstruct_skill.py # Qwen이 녹화+전사 데이터로 SKILL.md 초안 생성

녹화·전사·재구성 전부 로컬 완결, 외부 전송 없음 — Skill Creator(위)의 "제작 후 테스트·개선" 단계 앞에 "시연 기반 부트스트랩" 단계로 자연스럽게 연결
스킬 라이프사이클: workflow-recorder(시연→초안 생성) → skill-creator(테스트→반복개선) → planning-with-files(실행 중 검증·복구, 아래)

실행 중 검증/복구 — planning-with-files (OthmanAdi, 적극 채택): Claude Code 스킬(Cursor·Codex·Kiro·OpenCode 등 60+ 에이전트에서 Agent Skills 표준 호환). task_plan.md·findings.md·progress.md 세 파일을 디스크에 유지하고 매 턴 재주입해 컨텍스트 손실·/clear·크래시·컴팩션에도 계획이 살아남게 함. 결정론적 완료 게이트(stop 훅)가 미완료 단계에서 에이전트가 멈추지 못하게 강제(최대 3회 재시도), 세션 복구 자동화. SKILL.md+훅 패턴이라 별도 프로세스·외부 네트워크 호출 없음 — mattpocock/ponytail와 동일한 저리스크 계열. 25장 TODO 최우선순위(검증 설계·멀티스킬 연쇄 오케스트레이션)를 직접 해소하는 도구로 채택.
skills/self-maintenance/planning-with-files/ # Claude Code 플러그인 설치

16장(자체 업데이트) Claude Code 유지보수 워크플로에 상시 적용 — "작은 PR 지향" 원칙과 결합해 세션 중단·컨텍스트 소진에도 작업 유실 방지
5장 하네스 6요소 중 "검증"·"메모리/상태"를 실제 파일 메커니즘으로 구현한 참고 패턴 — Qwen 기반 스킬 라우터에도 유사한 "계획 파일 재주입" 개념 이식 고려

skills/
knowledge/{notebook-qa, obsidian-sync}/
coding/{code-review, tdd-loop, lazy-dev-ruleset, karpathy-claude-md}/
content-generation/{translation, image-postprocess, office-docs, sns-publish, video-shorts, capcut-draft}/
audio/audio-overview/
monetization/affiliate-content/
self-maintenance/{security-audit, planning-with-files}/
meta/{skill-creator, workflow-recorder}/


5. 하네스(Harness) 개념
[원9]

하네스 = 모델을 감싸 실제 작업을 수행하게 만드는 소프트웨어 뼈대. 공식: 에이전트 = 모델 + 하네스
6대 구성요소: ①루프 ②도구실행 ③메모리/상태 ④검증 ⑤가드레일 ⑥컨텍스트 엔지니어링
검증·메모리/상태의 실제 구현 참고: planning-with-files(4장)의 파일기반 계획 재주입 + 완료게이트 패턴


좋은 하네스는 중간급 모델도 프로덕션급으로, 부실한 하네스는 최상위 모델도 신뢰 불가하게 만듦 — 32GB 환경에서 모델 크기 한계를 하네스 품질로 보완하는 게 핵심 전략. 이 문서는 이 6요소 기준으로 계속 점검

심화 학습 자료 — bojieli/ai-agent-book: 《深入理解 AI Agent：设计原理与工程实践》(이보걸 저, Apache 2.0). 핵심 공식 "Agent = LLM + Context + Tools"와 "Harness 공학이야말로 경쟁력"이라는 1장의 선언이 이 장의 철학과 정확히 일치. 10개 장 + 93~95개 오픈소스 실습 실험(이론뿐 아니라 직접 실행 가능), 13개 언어 지원(한국어 book-ko 포함), SiliconFlow Qwen·Aliyun Qwen3.7-plus 등 Qwen 계열을 실험 프로바이더로 명시 지원. 문서가 아닌 실행 코드 없는 참고자료라 오케스트레이션 리스크 없음(23장 참고자료류와 동일 저리스크). → 5장 이론 보강 + 15장 G 실습 커리큘럼으로 활용(아래).

개념 참고(도구 미채택) — RLM(Recursive Language Model), PrimeIntellect-ai/prime-agent: "컨텍스트를 변수처럼 다루고(prompt-as-a-variable), 도구·서브에이전트 호출을 지속형 REPL 안의 함수 호출로 처리"하는 아키텍처 개념. 도구 자체는 22-5 참고(미채택, 관찰 대상) — 개념만 하네스 이론 참고자료로 기록.

6. 보안 체계
[원6]

우선순위 순:

프롬프트 인젝션 방어(최우선) — 외부 콘텐츠는 격리된 컨텍스트에 "데이터일 뿐 지시가 아니다"로 명시, 인젝션 패턴 사전 필터링, 출력 모니터링
실증 사례: Block "Operation Pale Fire"(2026.1) — 자체 보안팀이 보이지 않는 유니코드에 악성 지시를 숨긴 오염된 레시피로 자사 에이전트 Goose 해킹 성공. 보안 전담팀 있는 조직도 뚫렸다는 건 이 원칙이 실전 위협 방어라는 실증 [원37]
실행 샌드박싱 — 코드 실행은 Docker 컨테이너 격리(네트워크 차단), 되돌릴 수 없는 행동은 항상 사용자 승인
승인/권한 분류 체계 — 모든 스킬 호출을 read/write_local/exec/external 4등급으로 태깅, 등급별 처리(낮음 자동, 중간 확인, 높음 항상 승인). SKILL.md 메타데이터에 명시 [OpenWorker 패턴 차용, 원21]
최소 권한 원칙 — API 키는 Keychain/.env(600), 최소권한 강제 시 사고율 17% vs 76%(연구결과)
공급망 검증 — 외부 스킬/MCP는 직접 검증, 설치스크립트는 실행 전 확인. Bumblebee(Perplexity, Apache 2.0) 도입 — 읽기전용 공급망 감사, baseline/project/deep 프로필
API 서버 표준 보안 — JWT 인증, rate limiting, CORS, 입력검증, 민감정보 미로그
데이터 보안 — FileVault 암호화, 접근통제 폴더 분리, git-secrets
감사 체계 — 모든 행동 로그화, 민감 액션은 실행 전/후 분리 로그
macOS 자체 보안 — FileVault·Gatekeeper·방화벽·Little Snitch(아웃바운드 모니터링)·TCC 권한 점검·Quarantine 속성 유지
외부 웹 수집 시 robots.txt 준수 — Agent Reach(22-3)·트렌드리서치·웹검색증강 QA(15장A) 등 웹 fetch 스킬은 대상 사이트를 가져오기 전 robots.txt 확인을 필수 단계로 삽입. RFC 9309상 법적 강제력은 약하나(실제 준수율도 낮은 편), 평판·법무 리스크 관리 및 서버 차단 예방 차원에서 원칙적으로 준수. 3단계(수익화)에서 공개 서비스 운영 시에는 반대로 AI 학습봇 차단 여부를 정책적으로 결정해야 함
# skills/*/robots_check.py — 웹 fetch 스킬 공통 적용
import urllib.robotparser
def can_fetch(url: str, user_agent: str = "*") -> bool:
rp = urllib.robotparser.RobotFileParser()
rp.set_url(urljoin(url, "/robots.txt")); rp.read()
return rp.can_fetch(user_agent, url)

주기적 침투테스트 — Strix — 오픈소스 자율 펜테스트 에이전트(Apache 2.0, 4.8만+ 스타). 실제 익스플로잇으로 PoC를 검증하고서만 취약점 보고(오탐 최소화), Ollama/LMStudio 로컬모델 지원, Docker 필수. OWASP Top10(SQL/NoSQL 인젝션, SSRF, XXE, RCE, IDOR, XSS, CSRF 등) 커버. Bumblebee(5번)와 같은 계열 — "주기적으로 호출하는 전문 감사 도구"로 분류, 상시실행 아님
적용 시점: 18장(2단계 미니서버) 전환 직전, 또는 새 스킬이 외부노출 기능(SNS게시·결제 등)을 가질 때 1회성 실행
로컬 Qwen으로 비용 없이 우선 실행, 배포 직전 등 중요 시점엔 Claude API로 재검증(17장 원칙과 일치)
주의: Strix 자신이 셸실행·익스플로잇 코드작성을 하는 도구이므로, 2번(실행 샌드박싱) 원칙을 Strix 자신에게도 적용 — 반드시 OrbStack 컨테이너 안에서, 네트워크는 테스트 대상 API로만 제한
skills/self-maintenance/security-audit/strix_scan.py


온디맨드 시작 시 루틴: bumblebee scan --profile baseline → 직전 스캔과 diff → 이상시 격리 → 정상시 서비스 시작(10장 on_startup()과 결합)

7. 외부 데이터 전처리 파이프라인
[원7]

수집 → 검증/보안 → 추출 → 정규화 → 청킹 → 임베딩 → 저장/색인

문서: 텍스트/OCR추출 → 노이즈 제거 → 문단 청킹(overlap 100~200토큰)
이미지: Qwen3-VL 캡션+OCR → 캡션만 임베딩
영상: 오디오분리→Whisper 자막화(타임스탬프)+프레임샘플링 → 씬단위 청킹
음악: 메타데이터 추출, 신호처리는 librosa(가사 원문 보관은 저작권 주의)
전처리는 LLM이 아닌 전통 도구(OCR/Whisper/librosa)가 담당, LLM 추론 서버와 별도 워커로 분리
벡터DB: LanceDB/Chroma, 메타데이터: SQLite. 리소스 소모 순: 영상>이미지>음악>문서

8. Docker/컨테이너 구성
[원13]

도입 이유: 보안 샌드박싱 필수, 오픈소스 도구 표준 배포방식, 프로세스 격리, 환경 재현성
Docker Desktop 대신 OrbStack(Apple Silicon 네이티브 가상화, 훨씬 가벼움)


핵심: LLM 추론만 네이티브, 주변 인프라(큐/DB/샌드박스)만 컨테이너화. 각 컨테이너 mem_limit 명시로 Ollama 메모리 여유 확보.

9. 데이터 저장 및 백업
[원15]

현재: 내장 2TB SSD(외장 SSD 구매 계획 없음). NVMe라 랜덤 I/O 성능 문제 없음
경로는 .env의 DATA_ROOT로 분리(추후 외장 전환 대비)
용량 관리: 임베딩·모델캐시 주기적 모니터링, 안 쓰는 Ollama 모델 정리
백업: 내장 단일 디스크라 오히려 백업이 더 중요 — Time Machine 정기 백업, 코드/스킬은 Git+비공개 GitHub, 수집 데이터(임베딩·원본)는 별도 백업 스크립트
외장 SSD 도입 시: DATA_ROOT 변경 + 마운트 확인 로직 재추가 + 3-2-1 백업 원칙 적용
향후 옵션 — Spacedrive(jamiepine/Voicebox 개발자의 별도 프로젝트, Rust, VDFS): 내장·외장·클라우드에 흩어진 파일을 복제 없이 하나의 가상 라이브러리로 통합 관리(볼륨 인식, 실시간 마운트 감지, 파일 지문). 외장 SSD를 실제 도입하는 시점에 재검토 — 지금은 저장 위치가 내장 하나뿐이라 VDFS의 핵심 가치(다중 위치 통합)가 발휘되지 않음. 검토 시 확인할 점: Spacedrive Technology Inc.(VC투자) 제품이라 Pro 등급 등 상업적 요소가 라이선스에 섞여 있는지, 아직 베타(0.5)라 안정성 검증 필요

외장 SSD 도입 재검토 (2026-08-06) — "지금 서두를 필요 없음" 재확인

외장 SSD 옵션(SanDisk PRO-G40/E30, Samsung T7/T9, UGREEN 인클로저+NVMe DIY 등)을 실제 가격 조사한 결과, 2026년 스토리지 위기(AI 데이터센터 수요發 낸드 가격 급등)로 시장가가 비정상적으로 높은 시점임을 확인 — 특히 국내(쿠팡) 유통가는 PRO-G40 기준 100만원에 근접할 정도로 왜곡됨.

재점검한 실제 데이터 축적 현황: 벡터DB·임베딩(텍스트 위주, 수백MB~수GB급)·옵시디언 볼트(수백MB급)로 극히 작고, 영상/이미지 대량 생성은 25장에서 이미 하드웨어 한계로 후순위 처리 중이라 대용량 축적 계획 자체가 없음. 모델 캐시는 계속 내장에 유지(본 원칙 그대로).

결론: "외장 SSD 구매 계획 없음, 필요해지면 추후 도입" 원칙을 그대로 유지 — 지금 서둘러 구매할 근거가 약함. 재검토 트리거: ①내장 디스크 사용량이 70~80%에 근접할 때, ②그 시점에 스토리지 가격이 안정화됐는지 함께 확인. 그때 실측 데이터 기반으로 정확한 용량을 산정해 구매하는 것이 지금 선제 구매보다 합리적.

10. 온디맨드 운영 원칙
[원16]

"상시 서버"가 아닌 "필요할 때 켜는 워크스테이션"
맥북 켤 때 → 밀린 작업 재개 → 정상 서비스
자동화는 cron이 아니라 "켜졌을 때" 트리거 기반
Claude 관련 작업은 전부 수동 호출

구조 변경: 작업 큐 영속성 필수(Redis AOF/RDB 또는 SQLite 디스크 큐), "시작 시 복구" 패턴:
def on_startup():
resume_pending_queue_jobs()
log_downtime_gap()

외부정보 수집도 "켜져 있을 때만", 시간지정 자동화(OpenClaw heartbeat 등)는 이 제약을 인지하고 사용.
⸻
PART 2. 기능 확장


11. NotebookLM 스타일 기능
[원8]

핵심 기능: 소스기반 QA(출처인용), 오디오/비디오 오버뷰, 마인드맵·플래시카드·리포트, 대용량 컨텍스트 종합
참고 아키텍처: Open Notebook(Docker, MIT), SurfSense(Ollama 네이티브), InsightsLM(로컬 Ollama+Qwen3+Whisper+CoquiTTS — 스택 유사도 최고)
적용: 소스QA=기존 RAG 파이프라인 재사용(근거 메타데이터 반환) / 오디오오버뷰=신규 스킬(스크립트생성+TTS 별도워커) / 마인드맵 등=구조화 출력
구현 우선순위: ①소스QA+인용 ②요약/리포트/플래시카드 ③팟캐스트/비디오(후순위)
대안 경쟁 트랙: Khoj vs RAGFlow 2파전 비교 (35장 GitHub Top500 탐색에서 RAGFlow 발견, 23장 교체기준 적용 결과 "대안후보 기록"으로 병행 검토 결정)
Khoj(30장) — AGPL-3.0, Obsidian 플러그인 기본제공, "개인 세컨드브레인" 전반을 포괄하는 넓은 스코프
RAGFlow(infiniflow, 8.7만+ 스타) — RAG 엔진에 스코프를 좁힌 프로덕션급 오픈소스, Docker+MCP 지원, OpenClaw 공식 스킬 존재(4장과 접점). 라이선스는 Apache 2.0으로 알려져 있으나(재검증 권장) 맞다면 Khoj의 AGPL 리스크(27/28장 수익화 충돌) 없이 더 유리
두 후보 모두 Docker로 가볍게 병행 테스트 후 결정, 기존 커스텀 구현이 우선순위 1(소스QA+인용)을 검증 완료할 때까지는 착수 보류

착수 확정 사항 (2026-08-06, 첫 구현 세션용)

탐색 종료 — 아래 4가지로 확정, 추가 도구 탐색 없이 바로 구현 착수:

벡터DB: LanceDB 확정 — 임베디드(파일기반), 별도 서버 프로세스 불필요(16장 온디맨드 원칙과 부합), pip install lancedb, pyarrow 기반이라 출처·타임스탬프 메타데이터 컬럼 저장 용이. Chroma는 클라이언트/서버 구조가 기본이라 현 단계엔 과함
임베딩 모델: nomic-embed-text 확정 — ollama pull nomic-embed-text, 137M 초경량, Ollama 표준 라이브러리 등재로 검증 불필요, CPU로 충분. Qwen 임베딩 계열은 라이브러리 등재 상태 유동적이라 보류, 안정화되면 23장 교체기준으로 재검토
첫 데이터 소스: 이 가이드 문서 자체 — 옵시디언 볼트 준비 전이라, 지금까지 작성된 이 마크다운 문서를 첫 청킹·임베딩 테스트 자료로 사용. 챕터 구조가 이미 있어 청킹 테스트에 적합
오늘 밤 MVP 범위: "질문을 받으면 이 가이드 문서에서 관련 청크를 임베딩 검색으로 찾고, Qwen이 어느 장을 근거로 답했는지 출처와 함께 답변한다." — 팟캐스트·마인드맵·플래시카드는 범위 밖, 우선순위①(소스QA+인용)만

실행 순서:
1. ollama pull nomic-embed-text
2. pip install lancedb fastapi uvicorn
3. 문서를 장(chapter) 단위 청크로 분할(~500토큰)
4. 각 청크 임베딩 → LanceDB 저장(출처=장번호 메타데이터 포함)
5. FastAPI 엔드포인트: POST /ask → 검색 → Qwen 답변+출처


12. 옵시디언 연동
[원22]

볼트(Vault) = 노트를 담는 일반 폴더. .obsidian/ 설정폴더 + .md 파일들. 폴더 스캔만으로 접근 가능(별도 API 불필요)
유료 Sync 미사용 — 볼트는 로컬(현재 내장 SSD)에만 존재
우선순위 높음(초기 단계 특성상 더욱): 전처리 부담 거의 0(이미 텍스트), NotebookLM 기능의 최적 테스트베드, 스킬구조·RAG파이프라인을 가장 단순한 케이스로 먼저 검증 가능
구현: skills/knowledge/obsidian-sync/(scan_vault.py, parse_links.py), 위키링크는 메타데이터로 저장, mtime 기반 증분 동기화
시딩 제안: 노트가 적으면 검증이 어려우니 테스트용 1020개 노트(설계 회의록 등) 먼저 작성 또는 23주 실사용 후 통합

13. 콘텐츠 생성 에이전트
[원25]

저장 콘텐츠 활용 단계 — 새 오픈소스 대거 도입보다 기존 채택 도구의 조합으로 대부분 해결.


동영상 생성 상세: Wan/HunyuanVideo 등 확산모델은 NVIDIA GPU 16~24GB 전제, Apple Silicon 지원 미흡 — 근본 한계 유효. 부분 대안:
MoneyPrinterTurbo(MIT): LLM스크립트+스톡영상(Pexels)+TTS+자막+MoviePy 조립 방식, GPU불필요, Ollama 네이티브 — 숏폼 자동화의 실질적 해법
CapCutAPI/capcut-mate: Python+FastAPI, MCP지원, CapCut 드래프트 생성 → 트렌드 스타일 템플릿 활용. 단 수동 export 필수(오히려 승인게이트 역할), 리버스엔지니어링 기반 취약성·ToS 회색지대 있음
삼각편대: 표준 스타일=MoneyPrinterTurbo, 트렌드 스타일=CapCutAPI, 코드 기반 정밀 렌더링=HyperFrames(아래). 본격 생성형 영상 필요시 2단계(18장) NVIDIA 옵션 재검토 또는 클라우드 API 부분 하이브리드

HyperFrames(heygen-com, Apache 2.0) — 코드 기반 영상 렌더링, 15장B 시너지: "HTML을 쓰면 영상이 나오는" 오픈소스 프레임워크, 2만3천+ 스타(2026.3 출시, 활발한 개발). HTML·CSS·JS·GSAP로 장면 정의 → Puppeteer+FFmpeg로 결정론적 MP4 렌더링(같은 입력=항상 같은 출력). "렌더링 크레딧·시트제한·상업등급 없음"을 라이선스 철학으로 명시. Claude Code 등에서 스킬로 설치(npx skills add heygen-com/hyperframes).
MoneyPrinterTurbo(스톡조합)·CapCutAPI(템플릿편집)와 달리 HTML/CSS를 직접 작성하는 방식이라 Qwen 코딩스킬·OpenCode(15장B)가 영상을 "코드로" 직접 생성 가능 — 영상편집SW 조작보다 안정적
특히 강점: 데이터시각화(차트 애니메이션, 17장H 재개 시 직결), 설명형 영상·정밀 모션그래픽. 완전 로컬(Puppeteer+FFmpeg), GPU 불필요
skills/content-generation/hyperframes-video/

npx hyperframes skills update # 비대화형 코어셋 설치


이미지 프롬프트 라이브러리 — YouMind-OpenLab/ai-image-prompts-skill: YouMind(상용 창작스튜디오)가 별도 운영하는 무료 오픈소스 스킬 저장소(YouMind-OpenLab)에서 배포. 1만개+ 커뮤니티 큐레이션 이미지 프롬프트(모델 무관, Stable Diffusion 등 포함), GitHub Actions로 하루 2회 동기화. API 키 불필요, 즉시 채택 — ComfyUI로 넘기기 전 검증된 프롬프트를 먼저 검색해 품질 향상.
skills/content-generation/image-postprocess/ai-image-prompts-skill/

같은 조직의 YouTube 자막 추출 스킬 등은 YouMind API 키가 필요해 부분적 클라우드 의존 — 로컬 대안(yt-dlp, Part4) 우선 사용, 이쪽은 보완적으로만 고려

14. 기능별 아키텍처 분리 원칙
[원26]

핵심: 무거운 전문 도구만 프로세스 분리, 오케스트레이션은 통합 유지
분리 대상: Ollama, ComfyUI, Voicebox, OfficeCLI(각각 독립 프로세스/API·CLI 호출)
분리 금지: 스킬 라우팅, 작업 큐, 메모리 예산 관리, 승인 게이트
완전 분리(마이크로서비스) 회피 이유: ①2장 메모리 최적화 붕괴 ②큐/승인체계 중복 ③1인 운영 한계 조기 도달
실제 적용: 스킬 폴더 계층화(4장 구조 참고)로 네임스페이스만 분리, 실행은 단일 FastAPI+큐+라우터
예외(물리 분리 고려): 2단계 전환시 리소스 확대되면 영상생성 등 별도 GPU박스로 분리 가능, 특정 도구가 잦은 크래시로 독립 재시작 필요할 때

15. 실험 카탈로그 — A~H
[원17]

목표: 상용화 아닌 폭넓은 실험. 전부 skills/ 단위 독립 구현, NotebookLM 인프라 재사용.

A. 지식/문서: NotebookLM QA(11장, 1순위) · 옵시디언 세컨드브레인(12장) · 웹검색증강QA · 회의녹음요약(whishper — 100%로컬 FasterWhisper 전사, Part4 참고)
B. 코딩: Qwen3-Coder 미니하네스(OpenCode+Ollama로 대체, 자체구축 불필요, 아래 상세) · 코드베이스 채팅(Understand-Anything, Part4) · PR리뷰봇 (ponytail 규칙 적용 대상, Part4)

OpenCode(anomalyco, MIT) — 코딩 미니하네스 완성품 채택: 터미널 네이티브 오픈소스 코딩 에이전트, 19만+ 스타. 75개+ 프로바이더(로컬 Ollama 포함) 지원, LSP통합(컴파일러 진단 실시간 피드백), Build/Plan 모드, 클라이언트/서버 구조. Claude Code와 같은 "외부 도구"로 취급 — FastAPI 백엔드에 통합하지 않고 독립 CLI로 호출. 23장 교체기준 적용 결과 Claude Code는 정상작동 중이라 교체 아닌 역할분담: 일상 코딩은 OpenCode+로컬Qwen(무료), 고위험·복잡한 작업은 Claude Code(기존 유지, 17장). 직접 짜려던 코딩 미니하네스(LSP통합·세션관리 포함)를 검증된 완성품으로 대체해 개발 비용 절감.
curl -fsSL https://opencode.ai/install | bash
# Ollama에 Qwen3-Coder 연결하여 사용, 스킬 폴더 편입 불필요

C. 음성: 로컬 음성비서(Voicebox로 대부분 해결) · AI팟캐스트 · 음악분석
D. 이미지/영상: 영수증처리 · 사진아카이브검색 · 이미지생성(ComfyUI) · 영상요약기
E. 언어학습: 회화연습(STT+TTS) · 원서기반학습(NotebookLM 재사용) · 번역+뉘앙스
F. 개인자동화: SNS파이프라인 · 이메일트리아지 · 일정비서 · 저널도우미 (10장 온디맨드 제약 적용)
G. 멀티에이전트 실험: 역할분리 협업 · 모델라우팅 벤치마크 · 컴퓨터사용 에이전트(후순위) · ai-agent-book 93개 실습(5장 참고)을 Qwen으로 순차 진행하는 학습 트랙, 6~7장(MiniMind) 실습은 nanochat과 묶어 "모델 학습 내부 이해" 서브트랙으로
H. 데이터분석: 재무분석기(전체 보류 — 도구후보 검토완료, 착수보류·Part4 참고) · 건강로그 비전분석(참고용)

설계원칙: 스킬 독립구현, RAG인프라 재사용, 다양성>성능, Ollama API로 클라우드 이식 대비.
시작순서: NotebookLM(A) → 코딩(B)/음성(C) → 언어학습(E) → 나머지 자유선택
⸻
PART 3. 운영 및 협업


16. 플랫폼 자체 업데이트 — Qwen 자율성 범위
[원11]

완전 자율 비권장 — 수동 트리거 방식:
당신이 "점검해줘" 명시적 실행 → Qwen 변경제안 → 격리 브랜치 로컬테스트
→ 필요시 Claude Code/API 호출 → PR생성 → 사람 리뷰·승인 → merge

위험요인: 30B급 모델의 복잡 변경 판단력 한계, 자기검증의 맹점, 보안설정 자가수정 위험, 사람없는 반복수정의 품질저하


자율범위는 skills/self-maintenance/SKILL.md로 명시 제한, 모든 자율수정은 브랜치+PR만, 테스트커버리지 있는 부분만 허용. Claude Code 세션에는 planning-with-files(4장)를 상시 적용 — 컨텍스트 소진·세션중단 시에도 진행상황이 파일로 보존되어 "작은 PR 지향" 원칙이 실제로 지켜지도록 보장.

17. Claude의 역할 [원12]


워크플로: 평소 설계는 Claude와 함께 → 일상 코딩은 OpenCode+로컬Qwen(무료) → 유지보수·복잡한 리팩토링은 주기적 Claude Code → 고위험 결정만 Claude API. 정리: Claude는 "매순간 관여하는 운영자"가 아니라 "설계자·상위검토자", 일상 코딩 비용은 OpenCode로 완전히 절감.

18. 2단계(과도기) 옵션 — 미니서버/상시운영
[원19]

로드맵상 착수 시점 전 옵션. "상시 자동화할 가치가 있다"는 판단이 설 때 착수.

전환 시 대응해야 할 한계: ①모델 판단력 한계 ②물리적 한계(동시성/단일장애점/마모) ③보안노출 확대 ④신뢰성/자동복구 한계 ⑤1인운영 한계 ⑥법적책임 확대 ⑦숨은비용. 현실적 목표: 완전무인이 아닌 "리스크 액션은 승인 거치는 반자동 상시서비스".하드웨어 옵션(128GB급이 32GB 병목의 근본 해법):


전환 시 재검토: on_startup 복구로직→헬스체크/watchdog, 보안체계→상시노출 기준 강화, Claude 수동호출→자동트리거 상한선 추가.

19. 수익화 모델 분석 및 콘텐츠 기반 수익화
[원27+28]


19-1. 소비자 AI 서비스 수익화 3원형 (SaaS형)

구독중심(Character.AI/Claude/Cursor) · 광고결합구독(ChatGPT) · 마켓플레이스(Poe). 미국 앱스토어 소비자매출 80%가 구독. 가장 빠른 성장은 기업가격 생산성앱(Cursor류)과 광고결합 소비자앱 — 순수구독 단일레버는 빠르게 정체.


3단계 전환 요건: 멀티테넌시 전환, 실시간 entitlement 시스템, 결제연동(Stripe/PG사), 외부클라우드(18장 한계 극복), 사업자등록·약관, 고객지원, 2~3개 가격등급.

19-2. 콘텐츠 기반 클릭/제휴 수익화 (지금 단계도 착수 가능)

SaaS형과 달리 "내 콘텐츠 채널이 트래픽을 만드는 것"이라 멀티테넌시·결제연동 불필요 — 4장/13장/15장F의 자연스러운 확장. 해외구매대행·쇼핑몰과는 성격이 다름(결제·재고·통관 미개입이라 "설계 밖" 아님).

쿠팡파트너스: API로 인기상품 조회→딥링크→콘텐츠 자동생성이 실사용 패턴
네이버 애드포스트: 블로그 개설 30일+, 게시글 2030+, 주제일관성 필요. RPM은 애드센스 1/51/10이나 진입장벽 낮음 — 트래픽 쌓이면 워드프레스+애드센스로 확장
법정 고지 필수(자동화 파이프라인에 하드코딩): 제휴링크 고지("쿠팡파트너스 활동의 일환으로...") + 2026년 강화된 AI생성 콘텐츠 표기 의무

skills/monetization/affiliate-content/
coupang_partners_api.py / naver_adpost_setup.py
content_generator.py / disclosure_injector.py(필수, 생략불가)

파이프라인: 상품조회/주제기획 → Qwen생성 → 법정고지 자동삽입 → 이미지생성 → 승인게이트(6장, external등급) → 게시.
주의: 물량보다 품질·주제일관성(애드포스트 승인조건 자체가 요구), 승인없는 자동발행 금지(19장 법적책임과 직결)
⸻
PART 4. 외부 도구 평가 레지스트리


20. 평가 원칙 (모든 도구 평가에 공통 적용)


이 문서는 지금까지 20개 이상의 오픈소스/서비스를 검토했습니다. 반복 적용된 판단 기준은 다음 4가지이며, 아래 21장 매트릭스의 "판정" 열은 이 기준들로 결정되었습니다:

오케스트레이션 주도권 원칙(3장과 직결): 메모리·라우팅·스킬·검증·가드레일을 총괄하는 하네스(5장)의 주도권은 넘기지 않는다. 범용 에이전트 프레임워크(메모리+도구+워크플로 전체 포괄)는 아무리 인기 있어도 신중/보류, 스코프가 좁은 단일기능 유틸리티는 채택 가능성 높음
라이선스: MIT/Apache 2.0은 19장 수익화 계획과 충돌 없음. AGPL-3.0(예: Khoj)은 3단계 상용화 시 재검토 필요
스택 적합성: Python/FastAPI 기반은 통합 마찰이 적음. TypeScript/Node 기반(VoltAgent 등)은 별도 프로세스 필요해 통합비용 큼
성숙도/거버넌스: 재단 이관(Goose→Linux Foundation)이나 장기 운영 이력은 신뢰도를 높이나, 오케스트레이션 중복 문제 자체를 해소하지는 않음(평가가 바뀌지 않음)

공통 절차: 신규 도구 발견 시 → 6장 Bumblebee 스캔 → 위 4원칙 판정 → 채택시 skills/ 네임스페이스(14장)에 격리 배치

21. 도구 평가 요약 매트릭스


22. 개별 상세 평가


22-1. 오케스트레이션 프레임워크군 (신중/보류 등급)


OpenClaw [원4] — 12개+ 메시징 플랫폼 연동, Ollama 지원, MIT 유사. 보안사고 이력, Node.js 상시 프로세스, heartbeat는 온디맨드(10장)와 충돌. → SNS 송출만 서브모듈로 격리.

OpenWorker [원21] — Andrew Ng 프로젝트, FastAPI+React+Tauri, read/write_local/exec/external 4등급 승인체계. 신생(2026.7 출시). → 승인체계 패턴만 6장에 이식, 프레임워크 자체는 미도입.

AionUi [원23] — Electron+React 멀티에이전트 cowork 앱, skills/ 디렉터리+MCP통합관리, 20개+ CLI에이전트 조율. → 개인 보조 데스크톱 툴로만 병행, 메인 통합 안함.

Khoj [원30] — AGPL-3.0, Y Combinator 지원, 3.4만+ 스타. Postgres+pgvector+Django+Terrarium 샌드박스 등 무거운 스택. Obsidian 플러그인 기본제공. → 11장(NotebookLM+옵시디언)의 경쟁 대안 트랙으로 Docker 병행 테스트 후 결정. AGPL이 3단계 수익화와 충돌 가능성 있어 신중.

Hermes Agent [원31] — Nous Research, MIT, 21만4천+ 스타, $1.5B 밸류. "5개 층(플래너·도구·스킬·메모리·게이트웨이) 전부 자동화"가 핵심 셀링. "자가개선" 브랜딩이 16장 자율성 제한 원칙과 정면충돌, 원래 학습목적과도 충돌. → 63페이지 하네스 핸드북만 5장 학습자료로, Hermes Desktop은 개인 비교도구로만.

VoltAgent [원33] — TypeScript, MIT, 1만 스타, n8n스타일 관측성(VoltOps) 특화. TS/Python 스택 불일치로 통합비용 가장 큼. → 관측성 대시보드 아이디어만 6장 감사체계 개선과제로.

Goose [원37] — Block→Linux Foundation(AAIF) 기증, Rust, Apache 2.0, 5.2만 스타. "레시피"(YAML 워크플로) 개념. → 메인 미도입, Operation Pale Fire 보안사례는 6장에 채택 완료, 레시피 개념은 스킬 설계 참고, Rust 경량성으로 개인 보조도구 후보.

Dify/Langflow [원24] — 각 13.6만/14.6만 스타의 시각적 워크플로 빌더. → 세밀한 2장 메모리최적화를 UI 레이어가 방해할 가능성, 설계 참고자료로만.

LangChain [원24] — 체인/에이전트 실행로직 전체 대신 텍스트스플리터·리트리버 등 개별 컴포넌트만 7장 청킹 구현에 라이브러리로 활용.

22-2. 좁은 스코프 유틸리티군 (적극 채택 등급)


OfficeCLI [원23] — Word/Excel/PPT 조작 CLI, Office설치 불필요, SKILL.md 자체내장(8천토큰), 보안스캔 통과, 무료. → skills/content-generation/office-docs/에 즉시 배치, 13장·11장(리포트생성)·15장H(재무분석)에서 호출.

Supertonic [원20] — Supertone(한국기업), 99M 파라미터, 31개 언어, GPU불필요, 코드 MIT/모델 OpenRAIL-M. → 한국어 콘텐츠·빠른 UI피드백·정확한 수치낭독에 우선 사용.

Voicebox [원20] — 로컬 AI 음성스튜디오, 몇초 샘플 클로닝, 7개 TTS엔진, 전역받아쓰기, MCP지원(Claude Code 연동). → 20장 자체구현 대체, 17장 Claude 역할 표에 음성피드백으로 반영.

Vocello(舊QwenVoice, 실험적 병행 후보) — Apple Silicon 전용 로컬 음성 스튜디오, MLX+Qwen 네이티브 조합으로 지금까지 발견한 도구 중 하드웨어 정합성이 가장 높음. 단 스타 343개로 검증 초기 단계. 23장 교체기준("기존 도구 문제 여부") 적용 결과 Voicebox가 정상 작동 중이라 교체 아님 — 안정성(스타·이슈대응) 검증되면 재평가할 실험적 후보로만 기록.

ComfyUI [원24] — 노드기반 이미지생성 워크플로, 10.6만 스타, MPS백엔드 지원. → 13장 이미지생성 기본 파이프라인.

nanochat [원24] — Karpathy, 토크나이징~채팅UI 전체 LLM학습 파이프라인, 5.5만 스타. M1에서 실행은 느림. → 실행보다 코드를 읽는 학습자료로 15장 G에 활용.

mattpocock/skills [원32] — Matt Pocock의 .claude/skills/ 공개, 4.8만~7.5만 스타. 순수 마크다운, 실행엔진 없음. → SKILL.md 작성법 참고 + TDD/트리아지/디버깅 등 언어독립 스킬 이식.

ponytail [원36] — "게으른 시니어개발자" 규칙세트, MIT, 9.6만 스타. Claude Code 공식지원. → Claude Code 직접설치 + 규칙텍스트를 자체 코딩스킬로 수동이식. 2장 메모리최적화와 시너지(짧은코드=적은토큰).

Karpathy's CLAUDE.md(forrestchang/andrej-karpathy-skills) — Karpathy의 LLM코딩 실패패턴 관찰을 4원칙(생각먼저→단순함우선→외과적수정→목표기반실행)으로 정리, 10.9만+ 스타. ponytail과 같은 부류(순수 규칙파일, 실행엔진 아님). → skills/coding/karpathy-claude-md/로 병행 채택, Claude Code에 두 규칙세트 동시 적용 가능.

Caveman(JuliusBrussee/caveman, MIT, 9.2만+ 스타) — 출력토큰 65% 절감 스킬, Claude Code/Codex/Gemini CLI/OpenClaw/Hermes 호환. 저자가 "정직한 수치" 문서로 한계(입력·추론 토큰은 미절감, 스킬 자체가 턴당 1~1.5k 입력토큰 추가) 공개해 신뢰도 높음. 동반도구 caveman-compress(CLAUDE.md 압축)·caveman-shrink(MCP 도구설명 압축)가 진짜 입력토큰을 줄임 — 2장 목표와 정확히 일치. → Claude Code 설치 + 로컬 Qwen 라우터 시스템프롬프트에도 "간결한 응답" 원칙 이식.

Skill Creator(anthropics/skills 공식) — 스킬 제작·테스트·정량평가(eval) 반복개선 절차를 담은 공식 메타스킬. → 4장에 반영, 25장 TODO "평가/벤치마크 체계" 공백 해소 도구로 신규 스킬 제작 시 표준 부트스트랩.

Strix(usestrix/strix, Apache 2.0, 4.8만+ 스타) — 자율 펜테스트 에이전트, 실제 익스플로잇으로 PoC 검증 후에만 보고(오탐 최소화), Ollama/LMStudio 지원. → 6장 11번에 반영, Bumblebee와 같은 "주기적 전문 감사 도구"로 분류, 18장 전환 직전 등 1회성 실행.

MoneyPrinterTurbo [원25] — 스톡영상+TTS+자막 조립(확산모델 아님), MIT, 9.9만 스타, GPU불필요, Ollama지원. → 13장 영상생성 "표준스타일" 담당.

CapCutAPI/capcut-mate [원35] — Python+FastAPI, MCP지원, CapCut 드래프트 자동생성. 수동 export 불가피(오히려 6장 승인게이트 역할). → 13장 영상생성 "트렌드스타일" 담당, MoneyPrinterTurbo와 이원화.

22-3. 부적합/조건부 (개별 사유)


vLLM [원24] — 6.8만 스타, CUDA 중심 고성능 추론서버. Apple Silicon 이점 없음(이미 Ollama/MLX가 Metal 가속 활용). → 미도입.

Agent Reach [원29] — 12개+ 소셜플랫폼 읽기접근 CLI, MIT. 쿠키인증 플랫폼(Twitter·샤오홍슈)은 계정정지 위험을 프로젝트 자체가 경고. → 리서치 전용(skills/content-generation/trend-research/)으로 스코프 제한, 19-2장 수익화 채널 계정과 절대 혼용 금지, 전용 부계정 사용, 쿠키불필요 채널(YouTube·GitHub·RSS) 우선. 웹 fetch 시 robots.txt 준수(6장 10번) 함께 적용.

Firecrawl — 일반 웹사이트·블로그·기술문서 스크래핑 API(Mendable.ai, YC투자), Scrape/Crawl/Map/Search/Extract 엔드포인트, 13개 도구 MCP서버, 마크다운 변환으로 LLM 입력토큰 93% 절감. Agent Reach와 달리 소셜미디어는 정책상 제한(Instagram·YouTube·TikTok 등). 미채택 — 크레딧 기반 유료 정책: 무료등급(월 5001000크레딧) 존재하고 오픈소스 셀프호스팅도 가능하지만, Stealth모드·Extract가 5크레딧씩 소모되어 "체감가보다 57배 비싸다"는 사용자 불만이 흔함 — 11장 "비용 예측 가능하게" 원칙과 마찰, 결정에 따라 도입 보류. 대안 필요시 yt-dlp(Part4)·markitdown(24장)·Agent Reach 조합으로 대체.

NotebookLM 커넥터(notebooklm-mcp 계열) — Google NotebookLM을 MCP로 연결하는 여러 구현체. 브라우저 쿠키 자동화 방식이라 계정정지 위험 명시적 경고, 무료등급 일50쿼리 제한, 쿠키 2~4주 재로그인 필요. 11장에서 로컬 Qwen 기반 NotebookLM 기능을 직접 구축한 이유(프라이버시·오프라인·비용無)와 정면 충돌 → 미채택. 자체 구현 품질을 가끔 비교 검증하는 1회성 벤치마크 용도로만 고려 가능.

whishper(pluja/whishper) — 100% 로컬 전사·번역·자막편집 웹UI, FasterWhisper 백엔드, GPU/CPU 겸용, 전문검색. → 15장 A(회의녹음요약) 백엔드로 참고채택 — 로컬완결 철학과 가장 잘 맞음. 유사 프로젝트(vbrazo/whisper-pyannote-transcription-api)의 관리자 대시보드·화자분리 설계는 참고자료로만(해당 프로젝트는 OAuth·클라우드 지향).

22-4. 재무분석 도구군 — 전체 보류 (결정 완료)


15장 H(재무분석기) 후보로 검토했으나 라이브러리 성격(EdgarTools, FinanceToolkit)을 포함해 전체 보류로 결정. 검토 내역만 기록, 착수는 하지 않음.


재검토 트리거: 15장 H가 실제 착수 우선순위에 오를 때, 또는 2단계(18장) 전환으로 리소스 여유가 생길 때.

22-5. OpenHuman — 미채택 (강한 보안 경고)


tinyhumansai의 데스크톱 AI 에이전트, Rust+Tauri+TypeScript/React, GPL-3.0. 핵심 개념 "메모리 트리(Memory Tree)": Obsidian 호환 마크다운+로컬 SQLite 계층적 메모리 그래프, 118개+ 서비스(Gmail·Notion·GitHub·캘린더 등)에 OAuth로 연결해 콘텐츠를 마크다운화·청킹(~3000토큰)·색인, 대화 종료 후에도 지속 업데이트되는 사용자 컨텍스트 유지. 2026.5 출시 후 급성장(3.4만+ 스타), Ollama 로컬 LLM 지원.

미채택 사유 — 지금까지 중 가장 강한 거부 신호
오케스트레이션 완전 중복 — Hermes/Goose와 동일하게 메모리·에이전트루프·도구실행 전체를 포괄, 9장·2장과 정면 충돌
다수 독립 리뷰의 명시적 보안 경고: 파이프 셸 설치가 공급망 공격 벡터로 지적됨, 이메일·코드·캘린더·결제 도구 전반 OAuth 집적에 대해 공식 독립 감사 부재 지적, 스킬 런타임이 재구축 중이라 리뷰어 스스로 "현재는 플러그인 런타임이 아니라 프롬프트 인젝션 위험으로 취급하라"고 경고, 5일 테스트에서 동기화 실패 2건, 80% 압축 성능 주장 미검증
금융 리스크 표면 추가 — 지갑·시장거래를 다루는 crypto_agent 기능 존재
6장 핵심 원칙과 정반대 방향 — 외부 콘텐츠를 데이터로 격리하고 최소권한을 유지하는 설계 철학과 달리, 여러 클라우드 서비스에 지속적 OAuth 접근을 몰아주는 구조가 공격 표면을 극대화
라이선스: GPL-3.0 — Khoj(AGPL)와 유사한 카피레프트 우려, 27/28장 수익화 계획과 충돌 가능성
스택 불일치: Rust+Tauri+TS/React — FastAPI/Python과 별도 프로세스 필요

참고할 가치는 있음: 마크다운+SQLite 계층적 메모리, Obsidian 호환 설계 개념은 12장(옵시디언 연동)·11장(RAGFlow/Khoj 비교) 아이디어로 참고 — 단 OAuth 기반 외부서비스 연결 없이 로컬 파일에만 적용하는 방식으로 제한. Karpathy의 "LLM wiki" 개념은 9장 하네스 설계 철학 학습자료로 활용 가능.

22-6. Prime Agent — 미채택, 관찰 대상 (극도의 신생성)


PrimeIntellect-ai/prime-agent, 2026년 8월 공개(발견 시점 기준 출시 약 10시간). 자가개선형 코딩·리서치 에이전트, 1.6천+ 스타(급상승 중). 핵심 개념: RLM(Recursive Language Model) — 컨텍스트를 변수처럼 다루고(prompt-as-a-variable), 도구·서브에이전트 호출을 지속형 IPython REPL 안의 함수 호출로 처리. Continual Harness — 보조 프롬프트·메모리·스킬 설명·서브에이전트 사양을 지속 상태로 저장하고 에이전트가 스스로 작은 근거기반 업데이트로 정제. Opus 5 조합 ARC-AGI-3 벤치마크 95.5%(인간 전문가 기준선 상회).

미채택 사유
극도의 신생성 — 발견 시점 출시 10시간, 20장 4원칙 중 "성숙도" 판단 자체가 불가능
자가개선 하네스가 16장 원칙과 정면 충돌 — "Continual Harness가 스스로를 정제한다"는 설계가, Qwen의 완전 자율 플랫폼 수정을 금지한 16장 원칙과 직접 충돌. 31장(Hermes)의 "자가개선 브랜딩"보다 더 노골적으로 하네스 자체를 자기수정 대상으로 설계
프로젝트 스스로 밝힌 비-샌드박스 경고 — 사용자 권한으로 모델생성 Python·프로젝트 명령을 실행하며, 워커/커널 프로세스는 생명주기 격리·복구 개선일 뿐 보안 샌드박스가 아니라고 명시. 신뢰 안 되는 코드·지시는 반드시 외부 샌드박스에서 실행하라고 프로젝트가 직접 경고 — 6장 2번(실행 샌드박싱, OrbStack 컨테이너) 필수
15장 B는 이미 OpenCode로 해결됨 — 코딩 하네스가 추가로 필요한 상황이 아님

개념만 참고: RLM("컨텍스트를 변수로 다루기", 프로그래매틱 도구호출)은 5장 하네스 이론 참고자료로 기록. 재검토 시점: 최소 몇 달간 커뮤니티 검증(스타 추이·보안이슈 대응·프로덕션 사례) 축적 후.

22-7. Liquid AI LFM / MacPaw 파트너십 — 실험 후보 + 트렌드 검증


뉴스 사실관계(2026.8.5 발표): MacPaw(Eney AI비서 개발사)와 Liquid AI(MIT CSAIL 스핀아웃)가 온디바이스 Mac AI 스택 공동개발 전략적 파트너십 체결. Liquid Foundation Models(LFM)을 macOS 전용 튜닝해 MacPaw 자체 기술 Elix(추론엔진)·Mnemos(메모리레이어)와 결합, 1차 적용은 Eney 비서, 장기적으로 Setapp 통해 서드파티 개발자에게도 개방 계획.

두 층위 평가
Eney/MacPaw 통합 자체 — 미채택: 폐쇄형 상용 제품이라 직접 통합 대상 아님. Setapp SDK가 실제 개발자에게 개방되면 그때 재검토
Liquid Foundation Models 자체 — 실험적 채택 후보: Liquid AI가 별도 공개 중인 온디바이스 모델군(예: LFM2.5-350M, 서브-1GB급 "프론티어" 추론 주장), MLX 네이티브 지원이라 기존 Ollama+MLX 스택과 궁합 좋음(31장에서 확인한 Ollama의 MLX 통합과 직결). 2장(메모리 최적화) 경량 라우터(현재 Qwen3.5:9B)를 더 가벼운 모델로 대체할 수 있는지 벤치마크 가치 있음

의의: 산업 검증 — MIT 스핀아웃과 상용 소프트웨어 회사가 "로컬 우선·온디바이스·프라이버시 중심 Mac AI"에 베팅한다는 건, 이 플랫폼 전체가 지켜온 방향(1단계 로컬완결, 6장 프라이버시 원칙)이 업계 트렌드와 일치함을 시사.

적용 방법: 23장 교체기준 적용 — 현재 Qwen3.5:9B 라우터가 정상 작동 중이므로 즉시 교체 아님, 8장 첫 구현 이후 성능튜닝 단계에서 토큰레이트·정확도·메모리사용량 벤치마크 비교 후 결정.

22-8. agent-fm — macOS 앰비언트 모니터링 (실험 후보)


이름 구분 주의: "Agent FM"은 완전히 다른 두 프로젝트를 가리킴 — ①agentfm-ai/agent-fm(아래, 채택 검토) ②Agent-FM/agentfm-core(P2P 분산연산망, 미채택).

agentfm-ai/agent-fm: macOS 전용, Claude Code·Codex 세션을 "실시간 라디오 방송"처럼 청취하는 앰비언트 모니터링 앱, Apache 2.0. BYOK(API키 직접입력) 후 macOS Keychain에 저장(6장 최소권한 원칙과 일치), 프록시 서버·호스팅 계정 시스템 없음(세션 컨텍스트가 설정한 프로바이더로 직접 전송, 중간서버 경유 없음), 원격 워크스페이스는 기존 SSH 연결 그대로 사용(SSH키·비밀번호 미저장). 하나의 에이전트에 "튜닝"하거나 로컬+원격 여러 워크스페이스를 "글로벌 믹스"로 동시 청취 가능.

적용: 16장(자체업데이트)·17장(Claude역할) Claude Code 유지보수 세션을 터미널을 계속 보지 않고 백그라운드에서 청각적으로 모니터링하는 용도. 20장(Voicebox)·26장(iPhone연동)에서 이미 관심 보인 "음성/앰비언트 피드백" 방향과 결이 맞음. macOS 전용이라 호환성 문제 없고, 스코프 좁은 모니터링 유틸리티라 오케스트레이션 리스크 없음. 판단: 실험적 채택 후보 — 저리스크, 부가기능이지만 필수는 아님.

Agent-FM/agentfm-core(P2P 분산연산망) — 미채택: 유휴 컴퓨팅 자원을 낯선 타인의 AI 워크로드에 내주거나 반대로 작업을 알 수 없는 원격 피어에 분산 전송하는 구조. "클라우드 계정 없음, 데이터 유출 없음"을 표방하지만 P2P 메시 참여 자체가 6장 보안 원칙(공격표면 최소화)·10장(혼자 사용, 개인전용) 전제와 근본적으로 맞지 않음 — 대규모 워크로드 분산 확장이 필요한 상황이 아니면 무관.

23. 참고 자료 디렉토리 — 탐색 프로토콜
[원34]

소프트웨어가 아닌 큐레이션 색인 저장소들 — 개별 도구들의 "입구" 역할, 실행코드 없어 오케스트레이션 리스크 자체 없음.


원칙: 여기서 찾은 모든 것은 20장의 4대 원칙을 동일하게 통과해야 함(목록에 있다고 자동신뢰 금지). 월 1회 정도 훑어보는 습관을 10장 온디맨드 루틴에 결합 권장.

GitHub Top500 탐색 방법론


전체 500개를 순서대로 훑는 대신 카테고리 필터링으로 접근 — Top500 절대다수(범용 개발 학습자료·프레임워크)는 플랫폼과 무관하며, 실제 채택 사례(OfficeCLI·Supertonic·mattpocock/skills 등) 다수가 스타 순위표 밖에서 발견됨.

타겟 탐색 카테고리 (GitHub Topics 기준):
github.com/topics/local-llm
github.com/topics/ai-agent
github.com/topics/rag
github.com/topics/mcp-server
github.com/topics/text-to-speech

관련성 높은 후보를 스타 수·순위와 무관하게 발굴하는 게 목적 — "Top500 안에 있는가"는 판단 기준이 아님.

기존 도구 교체 판단 기준


"스타가 더 많다" ≠ "내 플랫폼에 더 낫다" — 스타 수만으로 교체를 결정하지 않음. 교체 검토 시 체크리스트:
20장 4대 원칙(오케스트레이션 주도권/라이선스/스택 적합성/성숙도) 통과 여부 — 신규 후보가 기존 채택 도구와 동등하거나 더 잘 충족하는가
기존 도구가 실제로 문제(느림·불안정·유지보수 중단 등)를 겪고 있는가 — 문제없이 잘 작동 중이면 "더 유명한 게 있다"는 이유만으로는 교체하지 않음
문제가 없다면 신규 후보는 "대안 후보"로만 22장 매트릭스에 기록, 실제 교체는 보류

이 기준을 충족할 때만 21장 매트릭스의 기존 판정을 갱신.
⸻
PART 5. 부록


24. 전체 아키텍처 요약도

[외부 데이터] → 전처리 워커(OCR/Whisper, 컨테이너) → 임베딩 → LanceDB/SQLite(컨테이너)
↓
[사용자 요청] → FastAPI → 작업 큐(Redis+RQ, 컨테이너) → 경량 라우터(9B, 네이티브 Ollama)
↓
스킬 판단(skills/ 로컬 폴더, 4장 네임스페이스)
↓
필요시 전문 모델 로드(30B, 네이티브 Ollama, 온디맨드)
↓
[보안 게이트: 샌드박스 실행(컨테이너) + 승인 단계(6장 4등급)]
↓
[검증 단계: 출처/근거 일치 확인, 필요시 Claude API 2차 검증]
↓
결과 반환 / SNS·블로그 송출(OpenClaw 서브모듈, 법정고지 자동삽입)

[플랫폼 자체 유지보수] Qwen: 소규모 변경 자율제안→브랜치/PR→테스트
Claude Code: 복잡한 변경, 하네스·보안 로직은 사람과 함께만


25. TODO 목록
[원10]

우선순위 높음: 검증(verification) 구체 설계, 멀티스킬 연쇄 오케스트레이션 (planning-with-files 도입으로 해소 — 4장/5장/16장 참고)

중간(성장 시): 실패복구/재시도 정책, 평가/벤치마크 체계(Skill Creator 도입으로 해소, 4장), 모니터링 대시보드(VoltOps 개념 참고, Part4), 외국어학습 파이프라인 구체화

서비스화 단계: 저작권/법적 이슈 심화 검토, 무중단 배포/업데이트 전략
⸻
26. iPhone 연동


핵심 원칙: 공인 IP 노출 없이 사설망으로만 연결 — 라우터 포트포워딩으로 FastAPI를 인터넷에 직접 노출하는 방식은 6장 보안 원칙 위반이라 절대 사용하지 않음.

1. Tailscale — 연결의 뼈대

WireGuard 기반 메시 VPN, 맥북·아이폰 양쪽에 설치해 사설 암호화 네트워크 구성. 포트포워딩·고정IP·인증서 설정 불필요, 무료 개인 플랜 최대 100기기. MagicDNS로 내맥북.tailnet이름.ts.net 형태 접속, 인터넷에서 스캔해도 서비스가 보이지 않음(Tailscale 네트워크 소속 기기만 접근 가능).

2. iOS 단축어(Shortcuts) — 실제 상호작용

Tailscale 연결 상태에서 FastAPI 엔드포인트를 직접 호출하는 단축어 구성:
홈 화면 위젯 또는 Siri 음성 명령으로 8장(NotebookLM) 등에 질문하고 응답 수신
예: 단축어 → POST https://내맥북.tailnet.ts.net/ask → 응답을 알림/음성으로 표시

3. 승인 게이트 원격 처리 — ntfy.sh

가장 실질적으로 중요한 부분. 6장 3번(승인/권한 분류) external 등급 작업은 항상 사람 승인이 필요한데, 지금까지는 맥북 앞에 있어야만 승인 가능했음. 외출 중 승인을 위해:
ntfy.sh(무료, 셀프호스팅 가능, 계정 불필요) — 승인 필요 작업 발생 시 아이폰으로 푸시알림 발송
알림 탭 → Tailscale 경유 간단한 승인/거부 웹페이지 오픈
SNS 게시 승인(28장) 등을 외출 중에도 처리 가능해짐

4. 온디맨드 원칙과의 관계 — 한계 인지

10장 원칙상 맥북이 켜져 있을 때만 아이폰 접근 가능. 맥북이 꺼진 동안은 접근 불가 — 의도된 제약으로 유지(상시 서버화는 18장에서 이미 보류한 결정과 일관).

5. 대화형 인터페이스 — 기존 Telegram 채널 재활용

4장에서 OpenClaw를 SNS 서브모듈로 계획한 것과 같은 Telegram 봇 채널을 개인용 대화 인터페이스로도 재사용 — 별도 앱 없이 아이폰 Telegram 앱에서 플랫폼과 바로 대화.

구성 요약

아이폰 (Tailscale 앱 + iOS 단축어 + ntfy 앱)
↕ (Tailscale 암호화 사설망, 맥북 켜져 있을 때만)
맥북 (Tailscale + FastAPI 백엔드 + ntfy 발송 로직)

⸻
이 문서는 하네스의 6가지 구성 요소(5장: 루프·도구실행·메모리·검증·가드레일·컨텍스트 엔지니어링)와 20장의 도구평가 4원칙을 기준으로 계속 점검·보완해나가는 것을 권장합니다.

