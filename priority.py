"""
priority.py
크롤러(8000)와 텍스트 생성기(8001)는 별개 프로세스라 model_router.is_generating()의
in-memory 상태를 서로 볼 수 없다. 대신 파일 시스템의 타임스탬프 파일을 신호로 삼아
"사람이 지금 응답을 기다리고 있다"는 사실을 프로세스 경계 너머로 전달한다.

동작 방식:
- 텍스트 생성기는 Ollama 호출 직전 mark_busy(), 직후(성공/실패 무관) mark_idle() 호출
- 크롤러는 자신의 Ollama 호출 직전 yield_to_person()을 호출해서, 마커가 최근 것이면
  짧게 대기하며 양보한다. 단, 무한정 굶지 않도록 최대 대기 시간을 둔다
  (크롤링은 백그라운드 작업이라 좀 늦어져도 되지만, 영원히 멈추면 안 되므로).
"""
import os
import time
import logging

logger = logging.getLogger(__name__)

_MARKER_PATH = os.path.join(os.path.dirname(__file__), ".generator_busy")
_STALE_SECONDS = 5        # 마커가 이보다 오래됐으면 죽은 신호로 간주 (생성기가 비정상 종료된 경우 대비)
_MAX_YIELD_SECONDS = 15   # 크롤러가 최대 이만큼만 양보하고 그 뒤엔 강행 (완전 아사 방지)


def mark_busy() -> None:
    """텍스트 생성기가 Ollama 호출 직전에 호출."""
    try:
        with open(_MARKER_PATH, "w") as f:
            f.write(str(time.time()))
    except OSError:
        logger.warning("[priority] busy 마커 기록 실패 (무시하고 진행)")


def mark_idle() -> None:
    """텍스트 생성기가 Ollama 호출 완료 직후(성공/실패 무관) 호출."""
    try:
        if os.path.exists(_MARKER_PATH):
            os.remove(_MARKER_PATH)
    except OSError:
        pass


def _is_person_waiting() -> bool:
    try:
        mtime = os.path.getmtime(_MARKER_PATH)
    except OSError:
        return False
    return (time.time() - mtime) < _STALE_SECONDS


def yield_to_person(check_interval: float = 0.5) -> None:
    """크롤러가 자신의 Ollama 호출 직전에 부른다."""
    waited = 0.0
    while _is_person_waiting() and waited < _MAX_YIELD_SECONDS:
        logger.info(f"[priority] 텍스트 생성기 응답 대기 중 - {check_interval}초 양보")
        time.sleep(check_interval)
        waited += check_interval
    if waited > 0:
        logger.info(f"[priority] 총 {waited:.1f}초 양보 후 크롤링 진행")