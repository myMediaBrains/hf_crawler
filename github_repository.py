"""
github_repository.py
GitHub 오픈소스 레포 저장소. Article/뉴스와 별개 테이블(GitHubRepo 등)에
저장하지만, 태그는 지금 있는 Tag 시스템을 그대로 재사용한다.

핵심 원칙 (2026-08-10, 사용자와 합의):
1. topics + primary_language를 tagging.get_or_create_tag()로 흘려보내
   기존 뉴스와 같은 Tag 체계를 공유한다 - "AI" 태그 하나로 뉴스도 GitHub
   레포도 동시에 찾을 수 있게 됨.
2. 스타 수는 덮어쓰지 않고 GitHubRepoSnapshot에 스냅샷으로 계속 쌓는다
   ("얼마나 빨리 늘고 있는지" = 트렌딩 여부의 진짜 지표, 최신값 하나만
   있으면 이걸 알 수 없음).
3. README는 해시가 바뀔 때만 GitHubReadmeHistory에 새 이력을 남기고,
   요약(summary)도 그때만 재생성한다 (변화 없는데 매번 LLM을 부르는
   낭비 방지 - Article.raw_content 원본 보존 원칙과 같은 철학).
"""

import os
import re
import hashlib
import logging
import requests
from datetime import datetime

from sqlmodel import Session, select

from models import GitHubRepo, GitHubRepoSnapshot, GitHubReadmeHistory, GitHubRepoTag
import tagging
import model_router

logger = logging.getLogger(__name__)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_API_BASE = "https://api.github.com"


def _headers() -> dict:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers


def _content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def search_repositories(query: str, sort: str = "stars", max_entries: int = 10) -> list[dict]:
    """
    GitHub Search API로 레포를 발굴한다. sort="stars"면 스타순 정렬(트렌딩 발굴에 적합).
    Search API는 core API보다 더 좁은 요율 제한이 걸려있으니(인증 시 분당 30회)
    너무 잦은 호출은 피할 것.
    """
    resp = requests.get(
        f"{GITHUB_API_BASE}/search/repositories",
        headers=_headers(),
        params={"q": query, "sort": sort, "order": "desc", "per_page": max_entries},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("items", [])


def _fetch_readme(full_name: str) -> str | None:
    """README.md 본문을 가져온다. raw.githubusercontent.com을 통해 원문 그대로 받는다."""
    resp = requests.get(f"{GITHUB_API_BASE}/repos/{full_name}/readme", headers=_headers(), timeout=10)
    if resp.status_code != 200:
        return None
    download_url = resp.json().get("download_url")
    if not download_url:
        return None
    raw = requests.get(download_url, timeout=10)
    if raw.status_code != 200:
        return None
    return raw.text


def _summarize_readme(readme_content: str, repo_name: str) -> str:
    """README를 짧게 요약한다. 실패하면 앞부분을 잘라서 대체."""
    prompt = (
        f"다음은 GitHub 오픈소스 프로젝트 '{repo_name}'의 README입니다. "
        f"이 프로젝트가 무엇을 하는지 2~3문장으로 한국어로 요약해줘.\n\n{readme_content[:4000]}"
    )
    try:
        return model_router.chat("summarize_readme", [{"role": "user", "content": prompt}])
    except Exception as e:
        logger.warning(f"[github_repository] 요약 실패 ({repo_name}): {e}")
        return readme_content[:200]


def collect_repo(session: Session, repo_json: dict) -> GitHubRepo:
    """
    GitHub API가 반환한 레포 정보 하나를 저장/갱신한다.
    - 처음 보는 레포면 새로 등록
    - README 해시가 바뀌었으면(또는 처음이면) 이력 추가 + 요약 재생성
    - 스타 수는 항상 새 스냅샷으로 추가 (덮어쓰지 않음)
    - topics + primary_language를 Tag로 연결
    """
    full_name = repo_json["full_name"]

    repo = session.exec(select(GitHubRepo).where(GitHubRepo.full_name == full_name)).first()
    is_new = repo is None
    if is_new:
        repo = GitHubRepo(full_name=full_name, url=repo_json["html_url"])

    repo.description = repo_json.get("description")
    repo.primary_language = repo_json.get("language")
    repo.created_at_github = repo_json.get("created_at")
    repo.pushed_at_github = repo_json.get("pushed_at")  # 2026-08-10: 마지막 업데이트 시점
    repo.last_checked_at = datetime.utcnow()

    readme_content = _fetch_readme(full_name)
    if readme_content:
        new_hash = _content_hash(readme_content)
        if repo.readme_hash != new_hash:
            summary = _summarize_readme(readme_content, full_name)
            repo.readme_content = readme_content
            repo.readme_hash = new_hash
            repo.summary = summary
            session.add(repo)
            session.commit()
            session.refresh(repo)

            session.add(GitHubReadmeHistory(
                repo_id=repo.id, content=readme_content, content_hash=new_hash, summary=summary,
            ))
            logger.info(f"[github_repository] README 변경 감지, 이력 추가: {full_name}")
        else:
            session.add(repo)
            session.commit()
            session.refresh(repo)
    else:
        session.add(repo)
        session.commit()
        session.refresh(repo)

    session.add(GitHubRepoSnapshot(
        repo_id=repo.id,
        stars=repo_json.get("stargazers_count", 0),
        forks=repo_json.get("forks_count", 0),
        open_issues=repo_json.get("open_issues_count", 0),
    ))
    session.commit()

    # 태그 연결: topics + primary_language를 기존 Tag 체계로 흘려보냄
    existing_tag_ids = set(
        session.exec(
            select(GitHubRepoTag.tag_id).where(GitHubRepoTag.repo_id == repo.id)
        ).all()
    )
    candidate_terms = list(repo_json.get("topics", []) or [])
    if repo.primary_language:
        candidate_terms.append(repo.primary_language)

    for term in candidate_terms:
        term_clean = term.strip()
        if not term_clean:
            continue
        tag = tagging.get_or_create_tag(session, name=term_clean, major_category="Tech", mid_category="Open Source")
        if tag.id not in existing_tag_ids:
            session.add(GitHubRepoTag(repo_id=repo.id, tag_id=tag.id, score=1.0))
            existing_tag_ids.add(tag.id)

    session.commit()
    logger.info(
        f"[github_repository] {'신규 등록' if is_new else '갱신'}: "
        f"{full_name} (★{repo_json.get('stargazers_count', 0)})"
    )
    return repo


def discover_and_collect(session: Session, query: str, max_entries: int = 10) -> int:
    """검색 -> 결과 전부 collect_repo(). 반환: 처리한 레포 수."""
    items = search_repositories(query, max_entries=max_entries)
    for item in items:
        try:
            collect_repo(session, item)
        except Exception as e:
            logger.error(f"[github_repository] 수집 실패 ({item.get('full_name')}): {e}")
    return len(items)

_DETAIL_MARKERS = {
    "분야": "field_short",
    "응용분야": "application_short",
    "연관성": "relevance_short",
    "구성요소": "components_short",
    "상세개요": "detailed_overview",
    "상세응용분야": "detailed_application",
    "구성요소연관성": "detailed_relations",
    "향후방향": "future_direction",
}


def _parse_detail_sections(raw: str) -> dict:
    """
    LLM 출력을 "[마커] 내용" / "[마커]\n여러 줄..." 형식으로 파싱한다.
    짧은 단어 항목(분야/응용분야 등)은 한 줄, 상세 항목은 다음 마커가 나올
    때까지의 줄을 전부 이어붙인다.
    """
    sections = {v: "" for v in _DETAIL_MARKERS.values()}
    current_key = None
    for line in raw.splitlines():
        stripped = line.strip()
        matched_key = None
        for marker, key in _DETAIL_MARKERS.items():
            if stripped.startswith(f"[{marker}]"):
                matched_key = key
                rest = stripped.split("]", 1)[-1].strip()
                if rest.startswith(":"):
                    rest = rest[1:].strip()
                sections[key] = rest
                current_key = key
                break
        if matched_key:
            continue
        if current_key and stripped:
            sections[current_key] = (sections[current_key] + " " + stripped).strip()
    return sections


# 2026-08-10: "내 플랫폼(hf_crawler)" 기준 판단으로 전면 재설계. 오픈소스
# 자체가 뭘 하는지가 아니라, "이게 내가 만들고 있는 LLM 엔진 플랫폼의 어느
# 기능 영역에 해당하고, 내 플랫폼과 얼마나 관련 있고, 관련 있다면 내
# 플랫폼의 어느 구성요소와 연결되는지"를 판단시킨다.
MY_PLATFORM_DESCRIPTION = (
    "로컬 macOS(M1 Max)에서 돌아가는 개인용 LLM 기반 뉴스/지식 수집·개인화·"
    "채팅 플랫폼(hf_crawler)이다. RSS/키워드검색/GitHub/백과사전(Kiwix) 등 "
    "여러 저장소를 수집하고, 태그 기반으로 분류하고, 로컬 LLM(Ollama, qwen3.5)으로 "
    "번역·요약하며, 개인화된 채팅 답변을 생성한다."
)

MY_PLATFORM_COMPONENTS = [
    "수집기", "태깅시스템", "번역파이프라인", "채팅/RAG", "개인화", "지식창고", "스케줄러",
]

APPLICATION_FIELDS = ["텍스트", "이미지", "동영상", "번역", "에이전트", "스킬", "하네스"]


def _generate_detail_analysis(repo: GitHubRepo) -> dict:
    """README를 근거로, "내 플랫폼" 기준 응용분야/연관성/구성요소를 판단한다."""
    prompt = (
        f"내 플랫폼: {MY_PLATFORM_DESCRIPTION}\n\n"
        f"다음은 검토 대상 GitHub 오픈소스 프로젝트 '{repo.full_name}'의 정보입니다.\n"
        f"설명: {repo.description or '(없음)'}\n"
        f"주 언어: {repo.primary_language or '(알 수 없음)'}\n\n"
        f"README:\n{(repo.readme_content or '')[:6000]}\n\n"
        "아래 형식 그대로, 각 항목을 채워줘. 대괄호 표시([...])는 그대로 유지하고 "
        "그 뒤에 내용만 써:\n\n"
        f"[분야]: (한 단어, 예: AI/웹개발/데이터베이스)\n"
        f"[응용분야]: (다음 목록 중에서만 골라 쉼표로 나열, 해당 없으면 \"해당없음\": "
        f"{', '.join(APPLICATION_FIELDS)})\n"
        "[연관성]: (이 오픈소스가 위 \"내 플랫폼\"과 얼마나 관련 있는지, 다음 중 "
        "하나만: 매우높음, 높음, 보통, 낮음, 매우낮음)\n"
        f"[구성요소]: (연관성이 \"보통\" 이상일 때만, 다음 목록 중 관련된 내 플랫폼 "
        f"구성요소를 쉼표로 나열, 아니면 \"해당없음\": {', '.join(MY_PLATFORM_COMPONENTS)})\n"
        "[상세개요]\n(이 프로젝트가 무엇을 하는지 3~5문장으로 자세히)\n\n"
        "[상세응용분야]\n(위에서 고른 응용분야에 왜 해당하는지, 실제 사용 상황 3~4문장)\n\n"
        "[구성요소연관성]\n(내 플랫폼의 어느 구성요소와 왜/어떻게 연결될 수 있는지 "
        "구체적으로 3~4문장. 연관성이 낮음/매우낮음이면 \"내 플랫폼과 직접적인 "
        "관련성은 낮음\"이라고만 써)\n\n"
        "[향후방향]\n(README의 로드맵/이슈 등을 참고해 향후 발전 방향을 추론, 2~3문장)\n"
    )
    try:
        raw = model_router.chat("analyze_repo_detail", [{"role": "user", "content": prompt}])
        return _parse_detail_sections(raw)
    except Exception as e:
        logger.warning(f"[github_repository] 상세 분석 실패 ({repo.full_name}): {e}")
        return {}


def get_or_generate_detail(session: Session, repo_id: int) -> GitHubRepo | None:
    """
    2단계(상세) 조회 시 호출. 분석이 없거나 README가 바뀐 뒤 재분석이 안 됐으면
    그 자리에서 생성하고 캐시한다.
    """
    repo = session.get(GitHubRepo, repo_id)
    if repo is None:
        return None

    needs_generation = (repo.analysis_hash != repo.readme_hash) or not repo.detailed_overview
    if needs_generation and repo.readme_content:
        sections = _generate_detail_analysis(repo)
        if sections:
            for key, value in sections.items():
                if value:
                    setattr(repo, key, value)
            repo.analysis_hash = repo.readme_hash
            session.add(repo)
            session.commit()
            session.refresh(repo)
    return repo