"""
model_router.py
역할별 Ollama 모델 라우팅 계층

실측 벤치마크 (M1 Max 32GB, Ollama 0.32.5, 2026-08)
------------------------------------------------------
                        load       eval rate     eval duration(짧은 응답 기준)
qwen3.5:9b              0.28s      40.59 tok/s   1.72s
qwen3.5:35b-a3b-nvfp4   34.89s     52.00 tok/s   1.88s

핵심 발견
- thinking을 끄지 않으면(system prompt의 "/no_think" 텍스트만으로는 불충분)
  200자 요약에 3000~4500 토큰을 소모하며 총 소요시간이 90초~110초까지 늘어남.
  => 반드시 API 최상위 파라미터 think=False 로 명시해야 함.
- 순수 생성 속도(eval rate) 자체는 두 모델이 큰 차이 없음(28% 내외).
- 결정적 차이는 "모델 전환 비용"(load duration) 쪽. 9b<->35b를 매 요청마다
  전환하면 34초 지연이 매번 발생하므로, 짧고 잦은 요청(번역/분류)과
  길고 드문 요청(RAG 보고서)의 모델을 분리하고 각각 다른 keep_alive 정책을 준다.

설계 원칙
1. LIGHT 티어(9b)는 상시 메모리 상주(keep_alive=-1) - 번역/분류/채팅형 응답
2. HEAVY 티어(35b-a3b-nvfp4)는 온디맨드 로드, 사용 후 일정 시간 뒤 자동 해제
   - RAG 보고서/학습서 생성처럼 길고 상대적으로 드문 요청에만 사용
3. 모든 호출에 think=False를 API 파라미터로 명시 (텍스트 트릭 사용 금지)
4. 작업 성격별 옵션(num_ctx, presence_penalty 등)을 TASK_PROFILES로 중앙 관리
   -> main.py 등 호출부는 task 이름만 넘기면 되고, 튜닝은 이 파일 한 곳에서만 함
"""

import asyncio
import logging
from enum import Enum
from typing import AsyncGenerator, Iterator, Optional

import threading

import ollama
import activity_tracker

logger = logging.getLogger(__name__)

_active_lock = threading.Lock()
_active_count = 0


def is_generating() -> bool:
    """지금 이 순간 실제로 생성 중인 요청이 있는지 (GPU 실사용 여부 추정에 사용)."""
    with _active_lock:
        return _active_count > 0


def _mark_start():
    global _active_count
    with _active_lock:
        _active_count += 1


def _mark_end():
    global _active_count
    with _active_lock:
        _active_count = max(0, _active_count - 1)


class ModelTier(str, Enum):
    LIGHT = "light"   # 번역, 분류, 짧은 응답 - 상시 상주
    HEAVY = "heavy"    # RAG 보고서/학습서 생성 - 온디맨드 로드


# 티어별 실제 Ollama 모델 태그 (ollama list 기준으로 확정된 이름만 사용)
TIER_MODELS: dict[ModelTier, str] = {
    ModelTier.LIGHT: "qwen3.5:9b",
    ModelTier.HEAVY: "qwen3.5:35b-a3b-nvfp4",
}

# 티어별 keep_alive 정책
# LIGHT: 무제한 상주(-1) - 로드 비용을 아예 없앰
# HEAVY: 5분 - 보고서를 연속으로 여러 건 생성할 때는 재사용, 이후엔 메모리 반납
TIER_KEEP_ALIVE: dict[ModelTier, str | int] = {
    ModelTier.LIGHT: -1,
    ModelTier.HEAVY: "5m",
}

# 작업 성격별 기본 옵션 프로필
# presence_penalty: qwen3.5 계열 모델 기본값이 1.5로 상당히 높아서,
# 직역처럼 원문 어순/반복 구조를 따라야 하는 작업엔 명시적으로 낮춰야 함.
TASK_PROFILES: dict[str, dict] = {
    "translate_literal": {
        "tier": ModelTier.LIGHT,
        "options": {
            "temperature": 0.1,
            "num_predict": 2048,
            "num_ctx": 8192,           # 기존 4096 -> 상향 (긴 기사 잘림 방지)
            "presence_penalty": 0.2,   # 모델 기본값 1.5 -> 하향 (직역 어순 보존)
        },
    },
    "translate_natural": {
        "tier": ModelTier.LIGHT,
        "options": {
            "temperature": 0.3,
            "num_predict": 2048,
            "num_ctx": 8192,
            "presence_penalty": 0.4,
        },
    },
    # 문장 단위 번역 전용 프로필. 기사 전체를 한 번에 맡기면 "영어 원문을 그대로
    # 반복해서 출력하라"는 형식 지시를 로컬 경량 모델(9b)이 종종 빼먹고 한국어
    # 번역문만 내놓는 문제가 있었다. 그래서 영어 원문은 이제 파이썬에서 직접
    # 문장 단위로 잘라 쓰고, LLM에게는 "이 한 문장만 한국어로 번역해줘"라는
    # 훨씬 단순한 작업만 맡긴다 - 입력이 짧으므로 num_predict/num_ctx도 작게.
    "translate_sentence_literal": {
        "tier": ModelTier.LIGHT,
        "options": {
            "temperature": 0.1,
            "num_predict": 256,
            "num_ctx": 1024,
            "presence_penalty": 0.2,
        },
    },
    "translate_sentence_natural": {
        "tier": ModelTier.LIGHT,
        "options": {
            "temperature": 0.3,
            "num_predict": 256,
            "num_ctx": 1024,
            "presence_penalty": 0.4,
        },
    },
    "classify": {
        "tier": ModelTier.LIGHT,
        "options": {
            "temperature": 0.0,
            "num_predict": 64,
            "num_ctx": 2048,
            "presence_penalty": 0.0,
        },
    },
    "rag_report": {
        "tier": ModelTier.HEAVY,
        "options": {
            "temperature": 0.4,
            "num_predict": 4096,
            "num_ctx": 16384,          # RAG로 검색된 다수 문서를 담기 위해 넉넉히
            "presence_penalty": 0.3,
        },
    },
    "extract_body": {
        "tier": ModelTier.LIGHT,
        "options": {
            "temperature": 0.0,        # 판단 작업이므로 창의성 불필요, 일관성 우선
            "num_predict": 4096,
            "num_ctx": 8192,
            "presence_penalty": 0.0,
        },
    },
}


def model_for_task(task: str) -> str:
    """
    task 프로필이 실제로 사용하는 모델 태그를 반환한다.
    Article.model_used / Translation.model_used 같은 이력 필드에 기록할 때 쓴다
    (호출부가 TIER_MODELS/TASK_PROFILES 내부 구조를 직접 알 필요 없게 캡슐화).
    """
    if task not in TASK_PROFILES:
        raise ValueError(
            f"등록되지 않은 task 프로필입니다: '{task}'. "
            f"사용 가능: {list(TASK_PROFILES.keys())}"
        )
    return TIER_MODELS[TASK_PROFILES[task]["tier"]]


def _build_request(
    task: str,
    messages: list[dict],
    extra_options: Optional[dict] = None,
) -> dict:
    """task 프로필을 기반으로 ollama.chat() 호출 인자를 구성한다."""
    if task not in TASK_PROFILES:
        raise ValueError(
            f"등록되지 않은 task 프로필입니다: '{task}'. "
            f"사용 가능: {list(TASK_PROFILES.keys())}"
        )

    profile = TASK_PROFILES[task]
    tier = profile["tier"]
    model = TIER_MODELS[tier]
    options = {**profile["options"], **(extra_options or {})}

    return {
        "model": model,
        "messages": messages,
        "options": options,
        "think": False,  # 핵심: 텍스트 트릭("/no_think")이 아닌 API 파라미터로 명시
        "keep_alive": TIER_KEEP_ALIVE[tier],
    }


def chat(
    task: str,
    messages: list[dict],
    extra_options: Optional[dict] = None,
) -> str:
    """동기 호출 - 완성된 텍스트를 반환한다."""
    request = _build_request(task, messages, extra_options)
    logger.info(
        f"[model_router] task={task} model={request['model']} "
        f"keep_alive={request['keep_alive']}"
    )
    _mark_start()
    with activity_tracker.track_component("LLM 모델 라우터", f"{task} 처리 중 ({request['model']})"):
        try:
            response = ollama.chat(**request)
            return response["message"]["content"].strip()
        finally:
            _mark_end()


def chat_stream(
    task: str,
    messages: list[dict],
    extra_options: Optional[dict] = None,
) -> Iterator[dict]:
    """동기 스트리밍 - ollama chunk 제너레이터를 그대로 반환한다."""
    request = _build_request(task, messages, extra_options)
    request["stream"] = True
    logger.info(
        f"[model_router] (stream) task={task} model={request['model']} "
        f"keep_alive={request['keep_alive']}"
    )
    return ollama.chat(**request)


async def achat_stream(
    task: str,
    messages: list[dict],
    extra_options: Optional[dict] = None,
) -> AsyncGenerator[dict, None]:
    """
    비동기 스트리밍 래퍼. FastAPI의 SSE 엔드포인트에서 await 루프로 순회 가능.
    동기 ollama 스트림을 스레드풀에서 돌리고, 청크를 큐로 넘겨 비동기화한다.

    주의: 이 제너레이터의 __anext__()에 asyncio.wait_for(...)로 타임아웃을 걸지 말 것.
    타임아웃 시 내부 취소가 제너레이터를 손상시켜 재사용이 불가능해진다.
    타임아웃/하트비트가 필요하면 start_stream()을 써서 큐를 직접 폴링할 것.
    """
    queue, sentinel = await start_stream(task, messages, extra_options)

    while True:
        item = await queue.get()
        if item is sentinel:
            break
        if isinstance(item, Exception):
            raise item
        yield item


async def start_stream(
    task: str,
    messages: list[dict],
    extra_options: Optional[dict] = None,
) -> tuple[asyncio.Queue, object]:
    """
    스트림 생산을 백그라운드에서 시작하고 (queue, SENTINEL)을 반환한다.

    achat_stream()과 달리 큐를 직접 노출하므로, 호출부에서
    asyncio.wait_for(queue.get(), timeout=...)로 안전하게 폴링할 수 있다.
    (asyncio.Queue.get()은 타임아웃으로 취소돼도 큐 자체는 멀쩡해서 재시도가 안전하다.
    반면 async generator의 __anext__()에 타임아웃을 걸면 취소가 제너레이터 내부까지
    전파되어 제너레이터가 손상된다 — 이게 실제로 겪었던 재연결 폭주 버그의 원인이었음.)
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    SENTINEL = object()

    def _producer():
        _mark_start()
        model_name = TIER_MODELS[TASK_PROFILES[task]["tier"]]
        with activity_tracker.track_component("LLM 모델 라우터", f"{task} 스트리밍 중 ({model_name})"):
            try:
                for chunk in chat_stream(task, messages, extra_options):
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, e)
            finally:
                _mark_end()
                loop.call_soon_threadsafe(queue.put_nowait, SENTINEL)

    logger.info(f"[model_router] (start_stream) task={task}")
    # 주의: loop.run_in_executor(None, ...)을 쓰면 asyncio 기본 실행기인
    # concurrent.futures.ThreadPoolExecutor가 자동 생성되는데, 이 스레드풀은
    # daemon이 아니라서 인터프리터 종료 시 atexit이 작업이 끝날 때까지 join으로
    # 붙잡는다. 그러면 번역이 진행 중일 때 Ctrl+C를 눌러도 그 스트리밍 생성이
    # 끝나야만 프로세스가 죽는 문제가 생긴다. daemon=True 스레드를 직접 띄우면
    # 프로세스 종료 시 join 없이 바로 함께 죽는다 (번역 결과는 완료 시점에만
    # DB에 저장되므로, 중간에 끊겨도 데이터 정합성 문제는 없다).
    threading.Thread(target=_producer, daemon=True, name=f"model_router-stream-{task}").start()
    return queue, SENTINEL


async def warmup(tier: ModelTier = ModelTier.LIGHT) -> None:
    """
    앱 시작 시 경량 모델을 미리 메모리에 올려 첫 요청 지연(콜드 스타트)을 없앤다.
    FastAPI lifespan에서 호출할 것.
    HEAVY 티어는 기본적으로 warmup하지 않는다 (온디맨드 정책 유지 목적).
    """
    model = TIER_MODELS[tier]
    keep_alive = TIER_KEEP_ALIVE[tier]
    loop = asyncio.get_running_loop()

    def _warmup_call():
        ollama.chat(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            think=False,
            keep_alive=keep_alive,
            options={"num_predict": 1},
        )

    await loop.run_in_executor(None, _warmup_call)
    logger.info(f"[model_router] warmup 완료: {model} (keep_alive={keep_alive})")
