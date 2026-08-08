"""
generators/text/main.py
개인화 텍스트 생성기 — 독립 FastAPI 서비스 (포트 8001).
크롤러 백엔드(포트 8000)와 별개 프로세스로 띄운다. database.py/models.py/
model_router.py는 크롤러 백엔드와 같은 디렉터리에 있다고 가정하고 그대로 import한다
(지금은 무거운 의존성이 없어 venv 분리 없이 같은 레포 안에서 포트만 나눈다).
"""
import json
import logging
from datetime import datetime, timezone, timedelta
import time

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlmodel import Session, select

from database import engine, create_db_and_tables
from models import Article, TextGeneration, ContentOrigin
import model_router
from .retrieval import get_context_articles

import priority

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
        articles = get_context_articles(
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

        answer = model_router.chat("personalized_qa", messages)
        t2 = time.monotonic()
        logger.info(f"[generate] Ollama 응답: {t2-t1:.2f}초")
        model_used = model_router.model_for_task("personalized_qa")

        record = TextGeneration(
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)