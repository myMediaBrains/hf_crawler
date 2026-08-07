"""
activity_tracker.py
플랫폼 상태를 두 층으로 나눠서 실시간 추적한다.

1. "사용자 요청" — 지금 어떤 API 요청이 처리되고 있는지 (main.py의 미들웨어가
   모든 엔드포인트를 자동으로 감싸서 추적하므로, 엔드포인트마다 코드를 추가할
   필요가 없다).
2. "컴포넌트별 상태" — 고정된 컴포넌트 목록 각각이 지금 무슨 작업을 하고 있는지.
   할 일이 없으면 기본값 "대기 중"으로 표시된다. 컴포넌트가 고정 목록이라
   UI에 항상 같은 순서로, 빠짐없이 표시할 수 있다.

두 층을 분리한 이유: "사용자 요청 1건"이 여러 컴포넌트를 연쇄적으로 건드릴 수
있기 때문이다 (예: 키워드 등록 요청 하나가 → 수집기(키워드) → 수집기(크롤링)를
차례로 거침). 요청과 컴포넌트 상태를 한 줄로 뭉치면 이 흐름이 안 보인다.
"""

import threading

_lock = threading.Lock()

# 고정 컴포넌트 목록 - 항상 이 순서, 이 이름으로 UI에 표시된다.
# 같은 컴포넌트를 중첩해서 track_component()로 감싸면 안쪽이 끝나는 순간
# 바깥쪽 상태가 사라지므로(단순화를 위한 설계상 트레이드오프), 서로 다른
# 레벨의 작업은 컴포넌트를 분리해서 등록한다 (예: 수집기를 "소스/키워드"
# 단위와 "크롤링"(개별 URL) 단위로 나눔).
COMPONENTS = [
    "수집기 · 소스/키워드",
    "수집기 · 크롤링",
    "LLM 모델 라우터",
]

_component_status: dict[str, str] = {c: "대기 중" for c in COMPONENTS}
_request_stack: list[str] = []


class _ComponentActivity:
    def __init__(self, component: str, action: str):
        if component not in _component_status:
            _component_status[component] = "대기 중"
        self.component = component
        self.action = action

    def __enter__(self):
        with _lock:
            _component_status[self.component] = self.action
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        with _lock:
            _component_status[self.component] = "대기 중"
        return False


def track_component(component: str, action: str) -> _ComponentActivity:
    """with activity_tracker.track_component("수집기 · 크롤링", "크롤링 중: ..."): 형태로 사용."""
    return _ComponentActivity(component, action)


class _RequestActivity:
    def __init__(self, label: str):
        self.label = label

    def __enter__(self):
        with _lock:
            _request_stack.append(self.label)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        with _lock:
            if self.label in _request_stack:
                _request_stack.remove(self.label)
        return False


def track_request(label: str) -> _RequestActivity:
    """with activity_tracker.track_request("GET /articles"): 형태로 사용 (주로 미들웨어에서)."""
    return _RequestActivity(label)


def get_snapshot() -> dict:
    """/stats/system 등에서 그대로 JSON으로 응답할 수 있는 스냅샷을 반환한다."""
    with _lock:
        return {
            "requests": list(_request_stack),
            "components": dict(_component_status),
        }