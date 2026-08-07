"""
job_control.py
파이프라인 수집/키워드 즉시 수집처럼 시간이 걸리는 작업을 사용자가 버튼
재클릭으로 중단할 수 있게 해주는 협조적(cooperative) 취소 플래그.

FastAPI 동기 엔드포인트는 스레드풀에서 돌기 때문에, 클라이언트가 연결을
끊어도(AbortController) 서버 쪽 루프는 저절로 멈추지 않는다. 그래서 각
수집 루프가 항목 하나를 처리할 때마다 이 플래그를 확인해서 스스로 멈추는
방식으로 구현한다.
"""

import threading

_cancel_event = threading.Event()
_lock = threading.Lock()
_active_job: str | None = None


def start_job(name: str) -> None:
    with _lock:
        global _active_job
        _active_job = name
        _cancel_event.clear()


def finish_job() -> None:
    with _lock:
        global _active_job
        _active_job = None
        _cancel_event.clear()


def cancel_current_job() -> str | None:
    with _lock:
        name = _active_job
    if name:
        _cancel_event.set()
    return name


def is_cancelled() -> bool:
    return _cancel_event.is_set()


def current_job() -> str | None:
    with _lock:
        return _active_job