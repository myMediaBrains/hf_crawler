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
import time

_cancel_event = threading.Event()
_lock = threading.Lock()
_active_job: str | None = None

# scheduler.py의 run_tick()이 쓰는 작업 이름과 반드시 일치해야 한다 (문자열 직접
# 하드코딩 대신 이 상수를 참조하게 해서 오타로 어긋나는 사고를 방지한다).
BACKGROUND_TICK_JOB_NAME = "파이프라인 점검"


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


def start_job_with_priority(name: str, max_wait: float = 20.0, poll_interval: float = 0.5) -> bool:
    """
    사용자가 직접 트리거한 작업(수동 키워드 수집 등) 전용 진입점.
    2026-08-09 추가 - 백그라운드 스케줄러 틱이 job_control 락을 쥐고 있으면,
    지금까지는 사람의 수동 요청이 그냥 409로 거부됐다. 이제는 반대로:
    - 지금 도는 게 백그라운드 틱(BACKGROUND_TICK_JOB_NAME)이면, 즉시 취소
      신호를 보내고 틱이 양보할 때까지 짧게 기다렸다가 사람 작업을 우선 진입시킨다.
    - 지금 도는 게 다른 사람의 수동 작업이면, 기존처럼 거부한다 (사람 vs 사람은
      순서를 그대로 지켜야 동시쓰기 충돌 재발 방지 원칙이 유지됨).
    """
    with _lock:
        global _active_job
        if _active_job is None:
            _active_job = name
            _cancel_event.clear()
            return True
        if _active_job != BACKGROUND_TICK_JOB_NAME:
            return False
        _cancel_event.set()  # 백그라운드 틱에 "양보해" 신호 전송

    waited = 0.0
    while waited < max_wait:
        time.sleep(poll_interval)
        waited += poll_interval
        with _lock:
            if _active_job is None:
                break
            if _active_job != BACKGROUND_TICK_JOB_NAME:
                # 기다리는 사이 다른 사람 작업이 먼저 새치기함 - 이번엔 양보
                return False

    with _lock:
        if _active_job is not None:
            # max_wait 안에 틱이 못 끝냈다면(개별 URL 크롤링 중이라 최대 30초까지
            # 걸릴 수 있음) 강제로 빼앗지 않고 이번엔 실패 처리한다 - 데이터
            # 정합성보다 우선권을 앞세우면 안 되므로.
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

# 2026-08-09: 기본값을 True(일시정지)로 변경. 예전엔 False라서, 서버를 재시작할
# 때마다 (이전에 "중지"를 눌러놨어도) 항상 백그라운드 수집이 자동으로 다시 돌기
# 시작했다 - 사용자가 명시적으로 "재개"를 누르기 전까지는 절대 스스로 시작하지
# 않아야 한다는 원칙을 반영.
_paused = True


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