"""
generators/text/main.py
개인화 텍스트 생성기 — 독립 FastAPI 서비스 (포트 8001).
크롤러 백엔드(포트 8000)와 별개 프로세스로 띄운다. database.py/models.py/
model_router.py는 크롤러 백엔드와 같은 디렉터리에 있다고 가정하고 그대로 import한다
(지금은 무거운 의존성이 없어 venv 분리 없이 같은 레포 안에서 포트만 나눈다).
"""
import json
import logging
import threading
import uuid
from datetime import datetime, timezone, timedelta
import time

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlmodel import Session, select

from database import engine, create_db_and_tables
from models import Article, TextGeneration, ContentOrigin
import model_router
from .retrieval import get_context_articles
from personalization import classify_and_store

import priority

MAIN_API_BASE = "http://localhost:8000"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

app = FastAPI(title="hf_text_generator")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 실제 배포 시 프론트엔드 origin으로 제한 권장
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    create_db_and_tables()  # text_generations 테이블만 신규 생성됨


def to_kst(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).isoformat()


class GenerateRequest(BaseModel):
    query: str
    top_interest_categories: list[str] | None = None  # personalization.get_top_interests() 결과 전달 가능
    user_id: str | None = None

class GenerateResponse(BaseModel):
    id: int
    query: str
    answer: str
    source_article_ids: list[int]
    model_used: str
    created_at_kst: str


SYSTEM_PROMPT = (
    "당신은 사용자가 수집해둔 최신 기사들을 바탕으로 흥미롭고 신선한 답변을 "
    "들려주는 개인 브리핑 도우미입니다. 아래 [참고 기사] 안의 정보만 근거로 삼아 "
    "답하되, 딱딱한 요약이 아니라 대화하듯 재미있게 풀어서 설명하세요. "
    "참고 기사에 없는 내용은 지어내지 말고, 관련 정보가 부족하면 솔직히 부족하다고 말하세요."
)


def _build_context_block(articles: list[Article]) -> str:
    if not articles:
        return "(참고할 만한 최근 기사가 없습니다)"
    return "\n".join(
        f"- [{a.source}] {a.title}\n  {(a.summary or a.content or '')[:400]}"
        for a in articles
    )


@app.post("/generate", response_model=GenerateResponse)
def generate_text(req: GenerateRequest):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query는 비어있을 수 없습니다.")

    with Session(engine) as session:
        t0 = time.monotonic()
        articles, _matched = get_context_articles(
            query=req.query,
            session=session,
            top_interest_categories=req.top_interest_categories,
        )
        t1 = time.monotonic()
        logger.info(f"[generate] DB 조회: {t1-t0:.2f}초")
        context_block = _build_context_block(articles)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"[참고 기사]\n{context_block}\n\n[질문]\n{req.query}"},
        ]

        priority.mark_busy()
        try:
            answer = model_router.chat("personalized_qa", messages)
            logger.info(f"[generate] 컨텍스트 글자수: {len(context_block)}, 답변 글자수: {len(answer)}, 참고기사 {len(articles)}건")
        finally:
            priority.mark_idle()
            
        t2 = time.monotonic()
        logger.info(f"[generate] Ollama 응답: {t2-t1:.2f}초")
        model_used = model_router.model_for_task("personalized_qa")

        record = TextGeneration(
            user_id=req.user_id,
            query=req.query,
            answer=answer,
            source_article_ids=json.dumps([a.id for a in articles]),
            matched_categories=json.dumps(req.top_interest_categories or []),
            origin=ContentOrigin.LLM_GENERATED,
            model_used=model_used,
        )
        session.add(record)
        session.commit()
        session.refresh(record)

        return GenerateResponse(
            id=record.id,
            query=record.query,
            answer=record.answer,
            source_article_ids=[a.id for a in articles],
            model_used=model_used,
            created_at_kst=to_kst(record.created_at),
        )


@app.get("/generate/history")
def get_history(limit: int = 20):
    with Session(engine) as session:
        stmt = select(TextGeneration).order_by(TextGeneration.created_at.desc()).limit(limit)
        records = session.exec(stmt).all()
        return [
            {"id": r.id, "query": r.query, "answer": r.answer, "created_at_kst": to_kst(r.created_at)}
            for r in records
        ]


# ============================================
# 대화창 - 단문/장문 2단계 생성 (신규)
# ============================================

SYSTEM_PROMPT_TEASER = (
    "당신은 사용자가 수집해둔 최신 기사들을 바탕으로 답하는 개인 브리핑 도우미입니다. "
    "아래 [참고 기사]만 근거로 삼아, 여러 근거를 충분히 종합하고 판단한 뒤 "
    "결론을 300자 내외로 압축해서 답하세요. 핵심 판단과 그 근거를 자연스러운 "
    "한두 문단으로 풀어 쓰되, 장황한 서론이나 반복 없이 밀도 있게 작성하세요. "
    "참고 기사에 없는 내용은 지어내지 마세요."
)

SYSTEM_PROMPT_REPORT = (
    "당신은 사용자가 수집해둔 최신 기사들을 바탕으로 상세한 보고서를 작성하는 "
    "개인 리서치 도우미입니다. 아래 [참고 기사]만 근거로 삼아, 제목과 섹션을 갖춘 "
    "마크다운 보고서를 최소 1000자 이상 분량으로 작성하세요. 배경, 핵심 내용, "
    "시사점 등 여러 섹션으로 나눠 충분히 상세하게 다루세요. 참고 기사에 없는 "
    "내용은 지어내지 말고, 정보가 부족한 부분은 솔직히 부족하다고 밝히세요."
)

# 모델이 분량 지시를 못 지켰을 때만 작동하는 안전장치. "300자"를 하드 기준으로 삼되
# 문장이 끊기지 않게 약간의 여유(380자)를 두고, 그 이상이면 그때만 자른다.
_TEASER_HARD_LIMIT = 380


def _enforce_teaser_length(text: str) -> str:
    text = text.strip()
    if len(text) <= _TEASER_HARD_LIMIT:
        return text
    return text[:_TEASER_HARD_LIMIT].rstrip() + "…"


class ChatShortRequest(BaseModel):
    query: str
    user_id: str | None = None
    conversation_id: str | None = None


class ChatShortResponse(BaseModel):
    generation_id: int
    conversation_id: str
    message: str
    model_used: str
    created_at_kst: str
    insufficient_evidence: bool = False  # True면 프론트가 장문/배송 버튼을 숨겨야 함


class ChatExpandResponse(BaseModel):
    generation_id: int
    parent_id: int
    report_markdown: str
    model_used: str
    created_at_kst: str


_INSUFFICIENT_EVIDENCE_MESSAGE = (
    "지금 갖고 있는 자료로는 답변드리기 어렵습니다. 관련 자료를 백그라운드에서 "
    "수집하도록 요청해뒀으니, 1시간쯤 뒤 다시 물어봐주세요."
)


def _extract_search_keyword(query: str) -> str:
    """
    채팅 질문(한국어일 수 있음)을 영어 뉴스 검색어로 압축한다. 실패하면 안전하게
    원문을 그대로 반환한다 (검색 품질은 떨어지지만 최소한 에러는 안 남).
    """
    prompt = (
        f"다음 질문을 영어 뉴스 검색에 적합한 3~5단어 키워드로 압축해줘. "
        f"키워드만 출력하고 다른 설명은 붙이지 마: \"{query}\""
    )
    try:
        raw = model_router.chat("extract_keyword", [{"role": "user", "content": prompt}])
        keyword = raw.strip().strip('"').strip("'")
        return keyword if keyword else query[:80]
    except Exception as e:
        logger.warning(f"[chat] 검색 키워드 압축 실패, 원문으로 대체: {e}")
        return query[:80]


def _trigger_background_collection(query: str) -> None:
    """
    근거가 없을 때, 질문을 영어 검색어로 압축해 크롤러(8000)의 기존
    /collect/deep-incremental?keyword=... 를 호출한다. 이 엔드포인트는 이미
    "키워드가 없으면 자동 등록 후 즉시 수집" 로직을 갖고 있어 그대로 재사용한다.
    채팅 응답을 기다리게 하면 안 되므로 별도 daemon 스레드에서 fire-and-forget으로
    실행하고, 실패해도 채팅 자체에는 영향 없게 예외를 삼킨다.

    ⚠️ 질문 원문을 그대로 키워드로 등록하면 안 됨(2026-08-09 실사용 중 발견):
    (1) 매번 새로운 문장이 키워드로 계속 쌓여 키워드 목록이 오염되고,
    (2) 이 프로젝트 소스가 영어권 전용(hl=en-US&gl=US)이라 한국어 원문으로는
        검색 결과가 안 나온다. 그래서 반드시 _extract_search_keyword()로
        압축한 영어 키워드를 써야 한다.
    """
    def _run():
        search_keyword = _extract_search_keyword(query)
        try:
            resp = requests.get(
                f"{MAIN_API_BASE}/collect/deep-incremental",
                # max_entries=5: 위와 동일한 이유.
                # register=false: 채팅에서 자동 트리거된 수집은 영구 키워드로
                # 등록하지 않는다 (2026-08-09) - 등록해버리면 사용자가 검색창에
                # 입력한 적도 없는 키워드(채팅 질문 원문 포함)가 스케줄러에 영구
                # 등록되어 매일 반복 수집되고, 이게 쌓일수록 스케줄러 틱마다 할 일이
                # 계속 늘어나는 근본 문제가 있었음.
                params={
                    "keyword": search_keyword, "months_back": 1, "interval_hours": 24,
                    "max_entries": 5, "register": "false",
                },
                timeout=180,
            )
            logger.info(f"[chat] 근거 부족 자동 수집 요청 결과: '{search_keyword}' -> HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"[chat] 근거 부족 자동 수집 요청 실패 ('{search_keyword}'): {e}")

    threading.Thread(target=_run, daemon=True, name="chat-auto-collect").start()


@app.post("/chat/short", response_model=ChatShortResponse)
def chat_short(req: ChatShortRequest):
    if not req.query.strip():
        raise HTTPException(status_code=400, detail="query는 비어있을 수 없습니다.")

    conversation_id = req.conversation_id or uuid.uuid4().hex

    with Session(engine) as session:
        articles, matched = get_context_articles(query=req.query, session=session, limit=3)

        # 근거 부족 - LLM을 부르지 않고(어차피 "모른다"고 할 게 뻔함) 바로 안내하고
        # 백그라운드 수집을 트리거한다.
        if not matched:
            _trigger_background_collection(req.query)

            record = TextGeneration(
                user_id=req.user_id,
                conversation_id=conversation_id,
                stage="short",
                query=req.query,
                answer=_INSUFFICIENT_EVIDENCE_MESSAGE,
                source_article_ids=json.dumps([]),
                matched_categories=json.dumps([]),
                origin=ContentOrigin.LLM_GENERATED,
                model_used="none",
            )
            session.add(record)
            session.commit()
            session.refresh(record)

            # 근거는 없었지만, 질문 자체는 여전히 취향 신호로서 가치가 있음 (약하게)
            if req.user_id:
                classify_and_store(
                    session, text_title=req.query, source="chat_no_evidence",
                    signal_type="implicit", weight=0.5, user_id=req.user_id,
                )

            return ChatShortResponse(
                generation_id=record.id,
                conversation_id=conversation_id,
                message=_INSUFFICIENT_EVIDENCE_MESSAGE,
                model_used="none",
                created_at_kst=to_kst(record.created_at),
                insufficient_evidence=True,
            )

        context_block = _build_context_block(articles)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT_TEASER},
            {"role": "user", "content": f"[참고 기사]\n{context_block}\n\n[질문]\n{req.query}"},
        ]

        priority.mark_busy()
        try:
            raw_answer = model_router.chat("personalized_teaser", messages)
        finally:
            priority.mark_idle()

        answer = _enforce_teaser_length(raw_answer)
        model_used = model_router.model_for_task("personalized_teaser")

        record = TextGeneration(
            user_id=req.user_id,
            conversation_id=conversation_id,
            stage="short",
            query=req.query,
            answer=answer,
            source_article_ids=json.dumps([a.id for a in articles]),
            matched_categories=json.dumps([]),
            origin=ContentOrigin.LLM_GENERATED,
            model_used=model_used,
        )
        session.add(record)
        session.commit()
        session.refresh(record)

        # 취향 축적 (5단계) - 질문 자체를 분류해 신호로 저장
        if req.user_id:
            classify_and_store(
                session, text_title=req.query, source="chat",
                signal_type="implicit", weight=1.0, user_id=req.user_id,
            )

        return ChatShortResponse(
            generation_id=record.id,
            conversation_id=conversation_id,
            message=answer,
            model_used=model_used,
            created_at_kst=to_kst(record.created_at),
        )


@app.post("/chat/expand/{generation_id}", response_model=ChatExpandResponse)
def chat_expand(generation_id: int):
    with Session(engine) as session:
        parent = session.get(TextGeneration, generation_id)
        if parent is None:
            raise HTTPException(status_code=404, detail="해당 단문 응답을 찾을 수 없습니다.")

        articles, _matched = get_context_articles(query=parent.query, session=session, limit=15)        
        context_block = _build_context_block(articles)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT_REPORT},
            {"role": "user", "content": f"[참고 기사]\n{context_block}\n\n[질문]\n{parent.query}"},
        ]

        priority.mark_busy()
        try:
            report = model_router.chat("rag_report", messages)
        finally:
            priority.mark_idle()

        model_used = model_router.model_for_task("rag_report")

        record = TextGeneration(
            user_id=parent.user_id,
            conversation_id=parent.conversation_id,
            stage="long",
            parent_id=parent.id,
            query=parent.query,
            answer=report,
            source_article_ids=json.dumps([a.id for a in articles]),
            matched_categories=json.dumps([]),
            origin=ContentOrigin.LLM_GENERATED,
            model_used=model_used,
        )
        session.add(record)
        session.commit()
        session.refresh(record)

        # 취향 축적 (5단계) - 장문 확장 클릭은 강한 긍정 신호(weight=1.5)
        if parent.user_id:
            classify_and_store(
                session, text_title=parent.query, source="chat_expand",
                signal_type="implicit", weight=1.5, user_id=parent.user_id,
            )

        return ChatExpandResponse(
            generation_id=record.id,
            parent_id=parent.id,
            report_markdown=report,
            model_used=model_used,
            created_at_kst=to_kst(record.created_at),
        )


@app.get("/chat/history")
def get_chat_history(conversation_id: str, limit: int = 20):
    with Session(engine) as session:
        stmt = (
            select(TextGeneration)
            .where(TextGeneration.conversation_id == conversation_id)
            .where(TextGeneration.stage == "short")
            .order_by(TextGeneration.created_at.desc())
            .limit(limit)
        )
        records = session.exec(stmt).all()
        result = []
        for r in records:
            has_long = session.exec(
                select(TextGeneration.id).where(TextGeneration.parent_id == r.id)
            ).first()
            result.append({
                "id": r.id,
                "query": r.query,
                "message": r.answer,
                "has_long_version": has_long is not None,
                "created_at_kst": to_kst(r.created_at),
            })
        return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)