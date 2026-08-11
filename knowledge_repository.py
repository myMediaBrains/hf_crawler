"""
knowledge_repository.py
ZIM 기반 오프라인 지식 저장소 프레임워크.

이름 그대로 "백과사전 전용"이 아니라 범용이다 - 앞으로 전문 지식창고
(의학/요리/StackExchange 등 Kiwix 생태계의 다른 ZIM 아카이브)를 추가할 때도
KNOWLEDGE_REPOSITORIES 딕셔너리에 항목만 늘리면 된다. 조회 로직은 공용.

핵심 원칙: DB에 내용을 복사하지 않는다. ZIM 파일 자체가 이미 전문검색
색인까지 포함된 완결된 저장소이므로, 여기서는 읽기 전용으로 조회만 한다.
"""

import os
import re
import logging
from html import unescape

from libzim.reader import Archive
from libzim.search import Query, Searcher

logger = logging.getLogger(__name__)

# ============================================================
# 저장소 레지스트리 - 새 전문지식창고를 추가할 땐 여기만 늘리면 된다.
# ============================================================
KNOWLEDGE_REPOSITORIES = {
    "encyclopedia_en": {
        "zim_path": os.getenv("ZIM_ENCYCLOPEDIA_EN", "data/knowledge_repositories/encyclopedia_en.zim"),
        "language": "en",
        "label": "영어 백과사전",
    },
    "encyclopedia_ko": {
        "zim_path": os.getenv("ZIM_ENCYCLOPEDIA_KO", "data/knowledge_repositories/encyclopedia_ko.zim"),
        "language": "ko",
        "label": "한국어 백과사전",
    },
    # 예시(나중에 실제로 다운받으면 주석 해제):
    # "medical_en": {
    #     "zim_path": "data/knowledge_repositories/medical_en.zim",
    #     "language": "en",
    #     "label": "의학 지식창고",
    # },
}

_archives: dict[str, Archive] = {}
_searchers: dict[str, Searcher] = {}


def _get_archive(repo_key: str) -> Archive | None:
    """지연 로딩 - 첫 조회 시점에만 연다. 파일이 아직 없으면 조용히 None(비활성)."""
    if repo_key not in KNOWLEDGE_REPOSITORIES:
        logger.warning(f"[knowledge_repository] 등록 안 된 저장소: {repo_key}")
        return None
    if repo_key not in _archives:
        path = KNOWLEDGE_REPOSITORIES[repo_key]["zim_path"]
        if not os.path.exists(path):
            logger.warning(f"[knowledge_repository] ZIM 파일 없음: {path} ('{repo_key}' 비활성)")
            return None
        _archives[repo_key] = Archive(path)
        _searchers[repo_key] = Searcher(_archives[repo_key])
        logger.info(f"[knowledge_repository] '{repo_key}' 로드 완료: {path}")
    return _archives.get(repo_key)


_TAG_STRIP = re.compile(r'<[^>]+>')


def _html_to_text(html: str, max_chars: int = 3000) -> str:
    """ZIM 항목은 HTML(위키 문서)이라, LLM 컨텍스트로 쓰기 전에 태그를 벗겨낸다."""
    text = _TAG_STRIP.sub(' ', html)
    text = unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:max_chars]


def search_repository(repo_key: str, query: str, limit: int = 3) -> list[dict]:
    """저장소 하나를 지정해서 조회. 반환: [{"title", "path", "content"}]"""
    archive = _get_archive(repo_key)
    if archive is None:
        return []
    searcher = _searchers[repo_key]
    search = searcher.search(Query().set_query(query))
    results = []
    for path in search.getResults(0, limit):
        entry = archive.get_entry_by_path(path)
        item = entry.get_item()
        raw_html = bytes(item.content).decode("UTF-8", errors="ignore")
        results.append({
            "title": entry.title,
            "path": str(path),
            "content": _html_to_text(raw_html),
        })
    return results


def search_encyclopedia(query: str, prefer_korean: bool = False, limit: int = 3) -> list[dict]:
    """
    백과사전 전용 편의 함수. 기본은 영어판, prefer_korean=True면 한국어판을
    우선 조회하고 결과가 없으면 영어판으로 자동 폴백한다.

    prefer_korean 판단은 호출부(저장소 라우터)에서 정해서 넘겨준다 - 예를
    들어 질문에 한글이 있는지(tagging._contains_hangul) 또는 "한국"/"조선"
    같은 한국 관련 키워드가 있는지로 판단할 수 있다.
    """
    repo_key = "encyclopedia_ko" if prefer_korean else "encyclopedia_en"
    results = search_repository(repo_key, query, limit=limit)
    if not results and repo_key == "encyclopedia_ko":
        results = search_repository("encyclopedia_en", query, limit=limit)
    return results