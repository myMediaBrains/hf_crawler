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


def start_job(name: str) -> bool:
    """
    새 작업 시작을 시도한다. 이미 다른 작업이 실행 중이면 시작하지 않고
    False를 반환한다 (호출부는 반드시 반환값을 확인해야 함).

    8/7 세션에서 "검색/등록"과 "파이프라인 수집"이 같은 키워드를 거의 동시에
    수집하려다 SQLite 쓰기 충돌("database is locked")이 난 적이 있어서,
    애초에 두 작업이 동시에 도는 상황 자체를 막기 위해 추가됨. 이전에는
    start_job()이 항상 성공하는 것으로 가정하고 무조건 덮어썼는데, 그게
    바로 이 충돌을 허용하는 원인이었다.
    """
    with _lock:
        global _active_job
        if _active_job is not None:
            return False
        _active_job = name
        _cancel_event.clear()
        return True


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

_paused = False


def pause_collection() -> None:
    """테스트/디버깅 목적으로 백그라운드 수집(scheduler.run_tick)을 일시정지한다.
    이미 진행 중인 작업을 즉시 중단시키진 않고, 다음 틱부터 건너뛰게 한다."""
    global _paused
    _paused = True


def resume_collection() -> None:
    global _paused
    _paused = False


def is_paused() -> bool:
    return _paused