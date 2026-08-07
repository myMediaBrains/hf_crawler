import asyncio
import re
from email.utils import parsedate_to_datetime
import os
import json
import subprocess
import threading
import psutil
import ollama
import logging
import uuid
from fastapi import FastAPI, Query, HTTPException, Depends, Request, UploadFile, File
from fastapi.staticfiles import StaticFiles
import model_router
import activity_tracker
import migrate_db
import scheduler as scheduler_module
from content_utils import (
    clean_article_content,
    extract_body_via_llm,
    crawl_url_sync,
)
from collectors import COLLECTOR_REGISTRY
from datetime import datetime
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from typing import AsyncGenerator
from dotenv import load_dotenv

from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlmodel import Session, select, func as sql_func, or_

import job_control  # 파일 상단에 추가

# 환경 변수 로드
load_dotenv()

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 데이터베이스 및 모델 import
from database import engine, get_session, create_db_and_tables
from models import (
    Article, Translation, Keyword, Source, CandidateSource, SchedulerConfig,
    SourceStatus, SourceOrigin, CandidateStatus, ContentOrigin, BlockedDomain,
)

# 개인화 레이어 import (신규)
from personalization import (
    classify_and_store, store_explicit_feedback,
    get_profile, get_top_interests,
)

# 환경 변수
DB_NAME = os.getenv("DB_NAME", "local_deep_trend.db")

# TARGET_SOURCES 정의 (해외 소스만 - 번역 학습 목적과 맞지 않는 한국어 소스는 제외)
# 최초 기동 시 scheduler_module.seed_manual_sources()가 이 리스트를 Source 테이블로 옮긴다.
# 이후 실제 소스 관리(추가/삭제/승격)는 DB(Source 테이블)에서 이루어진다.
TARGET_SOURCES = [
    # AI 관련 (해외만, 8개)
    {"name": "[AI] OpenAI Newsroom", "url": "https://openai.com/news/rss.xml"},
    {"name": "[AI] Hugging Face Blog", "url": "https://huggingface.co/blog/feed.xml"},
    {"name": "[AI] Google AI Blog", "url": "https://blog.google/technology/ai/rss/"},
    {"name": "[AI] MarkTechPost", "url": "https://www.marktechpost.com/feed/"},
    {"name": "[AI] MIT Tech Review (AI)", "url": "https://www.technologyreview.com/topic/artificial-intelligence/feed/"},
    {"name": "[AI] The Gradient", "url": "https://thegradient.pub/rss/"},
    {"name": "[AI] arXiv cs.AI", "url": "https://rss.arxiv.org/rss/cs.AI"},
    {"name": "[AI] The Verge (AI)", "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"},
    # 정치 (해외만, 1개)
    {"name": "[Politics] Politico", "url": "https://rss.politico.com/politics-news.xml"},
    # 골프 (해외만, 1개)
    {"name": "[Golf] Golf.com", "url": "https://golf.com/feed/"},
    # 당뇨 (해외만, 2개 - MedlinePlus)
    {"name": "[Diabetes] MedlinePlus Diabetes", "url": "https://medlineplus.gov/feeds/topics/diabetes.xml"},
    {"name": "[Diabetes] MedlinePlus Diabetes Complications", "url": "https://medlineplus.gov/feeds/topics/diabetescomplications.xml"},
    # 실버 건강 (해외만, 2개 - MedlinePlus)
    {"name": "[Senior Health] MedlinePlus Older Adult Health", "url": "https://medlineplus.gov/feeds/topics/olderadulthealth.xml"},
    {"name": "[Senior Health] MedlinePlus Healthy Aging", "url": "https://medlineplus.gov/feeds/topics/healthyaging.xml"},
    # 여행 (해외만, 2개)
    {"name": "[Travel] Travel + Leisure", "url": "https://www.travelandleisure.com/rss"},
    {"name": "[Travel] Conde Nast Traveler", "url": "https://www.cntraveler.com/feed/rss"},
    # 음악 (해외만, 2개)
    {"name": "[Music] Billboard", "url": "https://www.billboard.com/feed/"},
    {"name": "[Music] Pitchfork", "url": "https://pitchfork.com/feed/feed-news/rss"},
    # 음식 (해외만, 1개)
    {"name": "[Food] Bon Appetit", "url": "https://www.bonappetit.com/feed/rss"},
    # 경제 (해외만, 1개)
    {"name": "[Economy] CNBC Finance", "url": "https://www.cnbc.com/id/10000664/device/rss/rss.html"}
]

# ============================================
# 카테고리 설정 (기존 고정 소스용 느슨한 분류 - 신규 키워드는 Article.keyword로 정확 매칭)
# ============================================
CATEGORY_CONFIG = {
    "Politics": {
        "keywords": ["politics", "election", "congress", "president", "government", "vote", "senate", "parliament", "policy"],
        "blacklist": []
    },
    "AI": {
        "keywords": ["ai", "artificial intelligence", "machine learning", "openai", "hugging face", "claude", "llm", "gpt"],
        "blacklist": []
    },
    "Python": {
        "keywords": ["python"],
        "blacklist": []
    },
    "Tech": {
        "keywords": ["tech", "technology", "software", "startup"],
        "blacklist": []
    },
    "Golf": {
        "keywords": ["golf", "pga", "lpga", "masters"],
        "blacklist": []
    },
    "Diabetes": {
        "keywords": ["diabetes", "blood sugar", "insulin", "glucose"],
        "blacklist": []
    },
    "Health": {
        "keywords": ["health", "senior", "medical", "disease", "wellness"],
        "blacklist": ["vote", "election", "president", "politics", "congress", "senate", "parliament"]
    },
    "Travel": {
        "keywords": ["travel", "tourism", "vacation", "destination", "lonely planet"],
        "blacklist": ["vote", "election", "president", "politics", "congress", "senate", "parliament"]
    },
    "Music": {
        "keywords": ["music", "billboard", "song", "album", "artist"],
        "blacklist": ["vote", "election", "president", "politics", "congress", "senate", "parliament"]
    },
    "Economy": {
        "keywords": ["economy", "market", "cnbc", "stock", "finance", "trade"],
        "blacklist": ["vote", "election", "president", "politics", "congress", "senate", "parliament"]
    }
}

# ============================================
# 요청 모델
# ============================================
class StudyTranslateRequest(BaseModel):
    mode: str = "literal"  # 'literal' (직역) 또는 'natural' (의역)


class ContentUpdateRequest(BaseModel):
    new_content: str


class KeywordCreateRequest(BaseModel):
    name: str
    months_back: int = 1
    interval_hours: float = 24.0


class KeywordIntervalSetRequest(BaseModel):
    """'검색주기설정' 버튼 전용 - 즉시 수집 없이 등록/주기 갱신만 한다."""
    name: str
    months_back: int = 1
    interval_hours: float = 24.0


class SourceCreateRequest(BaseModel):
    name: str
    url: str
    category: str | None = None
    source_type: str = "rss"
    interval_hours: float = 3.0


class SourceUpdateRequest(BaseModel):
    interval_hours: float


class KeywordUpdateRequest(BaseModel):
    interval_hours: float


class SchedulerConfigUpdateRequest(BaseModel):
    tick_minutes: int


class VaultExportRequest(BaseModel):
    folder: str
    filename: str
    content: str


# ============================================
# FastAPI 앱 설정
# ============================================
scheduler = BackgroundScheduler()
# 틱 잡 등록은 lifespan()에서 SchedulerConfig.tick_minutes를 읽은 뒤에 한다
# (앱이 뜨기 전까지는 DB에서 설정값을 읽을 수 없으므로, 여기서는 인스턴스만 생성)


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    migrate_db.migrate(DB_NAME)
    migrate_db.migrate_sources(DB_NAME)
    logger.info("📊 데이터베이스 테이블이 준비되었습니다.")

    scheduler_module.seed_manual_sources(TARGET_SOURCES)

    config = scheduler_module.get_or_create_config()
    scheduler.add_job(
        lambda: scheduler_module.run_tick(),
        IntervalTrigger(minutes=config.tick_minutes),
        id="tick_scheduler",
        replace_existing=True
    )
    logger.info(f"[scheduler] 틱 스케줄러 등록 완료 (간격: {config.tick_minutes}분)")

    # 경량 모델(9b)을 미리 메모리에 올려 첫 요청 지연 제거
    await model_router.warmup(model_router.ModelTier.LIGHT)

    scheduler.start()
    logger.info("🚀 APScheduler 백그라운드 수집기가 시작되었습니다.")
    yield
    scheduler.shutdown()
    logger.info("🛑 APScheduler가 안전하게 종료되었습니다.")


app = FastAPI(lifespan=lifespan)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 개발 환경에서는 모든 오리진 허용
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 폴링용 엔드포인트는 "사용자 요청"에 항상 자기 자신이 뜨는 게 무의미해서 제외
_ACTIVITY_EXCLUDED_PATHS = {"/stats/system"}


@app.middleware("http")
async def track_request_activity(request: Request, call_next):
    """모든 API 요청을 자동으로 추적해서 /stats/system의 activity.requests에 노출한다."""
    if request.url.path in _ACTIVITY_EXCLUDED_PATHS:
        return await call_next(request)

    label = f"{request.method} {request.url.path}"
    with activity_tracker.track_request(label):
        response = await call_next(request)
    return response


UPLOAD_DIR = "uploads/images"
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

VAULT_DIR = os.path.expanduser("~/Documents/AI-Vault")
os.makedirs(VAULT_DIR, exist_ok=True)


def _safe_vault_path(relative_path: str) -> str:
    """경로 조작(../ 등) 방지 - 반드시 VAULT_DIR 내부인지 검증"""
    full_path = os.path.normpath(os.path.join(VAULT_DIR, relative_path))
    if not full_path.startswith(VAULT_DIR):
        raise HTTPException(status_code=400, detail="잘못된 경로입니다.")
    return full_path


def _unique_vault_filename(folder_path: str, filename: str) -> str:
    """같은 이름 파일이 있으면 덮어쓰지 않고 (1), (2)... 번호를 붙인다."""
    base, ext = os.path.splitext(filename)
    candidate = filename
    counter = 1
    while os.path.exists(os.path.join(folder_path, candidate)):
        candidate = f"{base} ({counter}){ext}"
        counter += 1
    return candidate


# ============================================
# API 엔드포인트
# ============================================

# 문장 경계 추정용 정규식. 완벽하지는 않다(약어 등 예외 케이스 존재) - 실용적인
# 수준의 휴리스틱. ".", "!", "?" 뒤에 공백과 대문자/숫자/따옴표/괄호가 오면 문장
# 경계로 본다.
_SENTENCE_BOUNDARY = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9"\'\(])')

# 이미지/링크 단독 줄(![alt](url) 또는 [text](url)만 있는 줄) 감지용
_STANDALONE_LINK_LINE = re.compile(r'^!?\[.*?\]\(.*?\)$')


def _split_paragraph_into_sentences(paragraph: str) -> list[str]:
    """빈 줄 없는 문단 하나를 문장 단위로 쪼갠다."""
    paragraph = paragraph.strip()
    if not paragraph:
        return []
    parts = _SENTENCE_BOUNDARY.split(paragraph)
    return [p.strip() for p in parts if p.strip()]


def _segment_article_for_translation(content: str) -> list[dict]:
    """
    기사 본문을 번역 파이프라인이 순서대로 처리할 세그먼트 목록으로 쪼갠다.
    각 세그먼트: {"type": "translate" | "verbatim", "text": str, "paragraph_end": bool}

    - code fence(```...```) 안의 줄, 이미지/링크 단독 줄은 번역하지 않고 그대로
      통과(verbatim)시킨다.
    - 그 외 일반 문단은 문장 단위(translate)로 쪼갠다. 문단의 마지막 문장에는
      paragraph_end=True를 붙여서, 그 뒤에 빈 줄 하나를 넣을지 판단한다.
    - 헤더(#...)나 리스트 항목(-, *, |)은 문장으로 더 쪼개지 않고 줄 전체를
      하나의 번역 단위로 취급한다.

    설계 의도: 예전엔 LLM에게 "영어 원문을 그대로 반복해서 출력하고 그 뒤에
    한국어 번역을 붙여라"는 복합 지시를 맡겼는데, 로컬 경량 모델(9b)이 그 형식
    지시를 종종 빼먹고 한국어 번역문만 내놓는 문제가 있었다. 그래서 영어 원문은
    이제 LLM에게 전혀 맡기지 않고 여기서 소스 그대로 가져다 쓰고, LLM에게는
    "이 한 문장만 한국어로 번역해줘"라는 훨씬 단순한 일만 시킨다 - 영어 줄이
    구조적으로 누락될 수 없다.
    """
    segments: list[dict] = []
    in_code_block = False
    paragraph_buffer: list[str] = []

    def flush_paragraph():
        if not paragraph_buffer:
            return
        text = ' '.join(paragraph_buffer).strip()
        paragraph_buffer.clear()
        if not text:
            return
        sentences = _split_paragraph_into_sentences(text)
        for i, sentence in enumerate(sentences):
            segments.append({
                "type": "translate",
                "text": sentence,
                "paragraph_end": i == len(sentences) - 1,
            })

    for line in content.split('\n'):
        stripped = line.strip()

        if stripped.startswith('```'):
            flush_paragraph()
            in_code_block = not in_code_block
            segments.append({"type": "verbatim", "text": line, "paragraph_end": False})
            continue

        if in_code_block:
            segments.append({"type": "verbatim", "text": line, "paragraph_end": False})
            continue

        if not stripped:
            flush_paragraph()
            continue

        if stripped.startswith(('#', '-', '*', '|')) or _STANDALONE_LINK_LINE.match(stripped):
            flush_paragraph()
            segments.append({"type": "translate", "text": stripped, "paragraph_end": True})
            continue

        paragraph_buffer.append(stripped)

    flush_paragraph()
    return segments


def _build_sentence_translation_system_prompt(mode: str) -> str:
    """
    문장 하나만 번역시키는 시스템 프롬프트. 영어 원문 재출력을 요구하지 않으므로
    (그건 이제 파이썬이 담당) 모델이 지켜야 할 지시가 단순해서 훨씬 안정적으로
    따른다.
    """
    mode_instruction = (
        "Provide a strict, literal translation (직역) into Korean, preserving English word order as much as possible."
        if mode == "literal"
        else "Provide a natural, fluent Korean translation (의역)."
    )

    return (
        "You are an expert English-to-Korean translator. "
        "You will be given exactly ONE English sentence or line (it may include markdown "
        "syntax such as a '#' header marker, a '-' or '*' list marker, or [text](url) "
        "link syntax).\n\n"
        "Output ONLY the Korean translation of that sentence — nothing else. "
        "Do NOT repeat or quote the English sentence. Do NOT add any preamble, notes, "
        "or explanation of what you translated. Do NOT wrap the output in quotes.\n"
        "If the source starts with a markdown marker (#, -, *), keep that same marker "
        "at the start of your Korean output. If the source contains a [text](url) link, "
        "translate only the visible text portion and keep the (url) exactly as-is.\n"
        "Do NOT translate proper nouns, company names, product/brand names, trademarks, "
        "inline code, or URLs — keep those in their original form.\n"
        f"Translation style: {mode_instruction}"
    )


def _save_translation(article_id: int, mode: str, task: str, translated_content: str, session: Session) -> None:
    """
    번역 결과를 Translation 테이블에 영구 저장한다.
    지금까지는 SSE/응답으로만 나가고 DB 어디에도 안 남았는데, models.py의
    Translation 테이블 설계 의도(추후 번역 모델 파인튜닝용 학습 데이터 확보)를
    실제로 채우기 위해 두 번역 엔드포인트 모두 이 함수를 호출한다.
    """
    translation = Translation(
        article_id=article_id,
        mode=mode,
        translated_content=translated_content,
        origin=ContentOrigin.LLM_TRANSLATED,
        model_used=model_router.model_for_task(task),
    )
    session.add(translation)
    session.commit()

@app.post("/collect/cancel")
def cancel_collection():
    name = job_control.cancel_current_job()
    if name:
        return {"status": "success", "message": f"'{name}' 작업 중단을 요청했습니다."}
    return {"status": "success", "message": "현재 실행 중인 수집 작업이 없습니다."}



# 1회성 백필 - PROMOTE_THRESHOLD를 3 -> 1로 낮추기 전에 이미 쌓여 있던
# CandidateSource(status=candidate)를 지금 한 번에 전부 승격시킨다.
# 여러 번 호출해도 안전하다 (이미 promoted/dropped인 건 건드리지 않고,
# _promote_candidate() 자체도 같은 url이 이미 Source에 있으면 그냥 건너뜀).
@app.post("/sources/promote-all-candidates")
def promote_all_candidates(session: Session = Depends(get_session)):
    pending = session.exec(
        select(CandidateSource).where(CandidateSource.status == CandidateStatus.CANDIDATE)
    ).all()

    promoted_count = 0
    for candidate in pending:
        keyword = session.get(Keyword, candidate.keyword_id)
        if keyword is None:
            continue  # 키워드가 이미 삭제된 경우 - 승격 불가, 건너뜀
        candidate.status = CandidateStatus.PROMOTED
        scheduler_module._promote_candidate(session, keyword, candidate)
        session.add(candidate)
        session.commit()
        promoted_count += 1

    return {
        "status": "success",
        "promoted_count": promoted_count,
        "message": f"기존 후보 {promoted_count}건을 출처로 승격했습니다."
    }

# 1회성 백필 - PROMOTE_THRESHOLD=1 도입 이전/직후 키워드 광역 검색으로 저장된
# 기사는 Article.source에 "[키워드] " 접두어가 없어서, 승격된 Source.name과
# 형식이 안 맞아 건수 집계(/stats/sources)에서 빠졌다. 이 접두어를 소급으로
# 맞춰준다. 여러 번 호출해도 안전 (이미 접두어가 붙은 기사는 대상에서 제외).
@app.post("/sources/fix-article-source-names")
def fix_article_source_names(session: Session = Depends(get_session)):
    promoted_sources = session.exec(
        select(Source).where(
            Source.origin == SourceOrigin.AUTO_PROMOTED,
            Source.keyword_id.is_not(None),
        )
    ).all()

    fixed_count = 0
    for source in promoted_sources:
        keyword = session.get(Keyword, source.keyword_id)
        if keyword is None:
            continue
        prefix = f"[{keyword.name}] "
        if not source.name.startswith(prefix):
            continue
        label = source.name[len(prefix):]

        articles = session.exec(
            select(Article).where(
                Article.keyword == keyword.name,
                Article.source == label,
            )
        ).all()
        for article in articles:
            article.source = source.name
            session.add(article)
            fixed_count += 1

    session.commit()
    return {
        "status": "success",
        "fixed_count": fixed_count,
        "message": f"{fixed_count}건의 기사 출처 표기를 정정했습니다."
    }


# ============================================
# 개인화 프로필 API (신규)
# ============================================

class ExplicitFeedbackRequest(BaseModel):
    article_id: int
    positive: bool  # True=👍, False=👎


@app.post("/personalization/feedback")
def submit_feedback(request: ExplicitFeedbackRequest, session: Session = Depends(get_session)):
    """
    기사 카드에 👍/👎 버튼을 추가하고 여기로 연결한다.
    프론트에서는 ArticleCard.jsx 하단에 버튼 두 개만 추가하면 됨.
    """
    signal = store_explicit_feedback(session, article_id=request.article_id, positive=request.positive)
    if signal is None:
        raise HTTPException(status_code=404, detail="해당 기사를 찾을 수 없거나 분류할 수 없습니다.")
    return {"status": "success", "subcategory": signal.subcategory, "weight": signal.weight}


@app.get("/personalization/profile")
def get_personalization_profile(session: Session = Depends(get_session)):
    """현재까지 쌓인 개인화 프로필 전체 (시간 가중 감쇠 적용된 상태)."""
    return {"profile": get_profile(session)}


@app.get("/personalization/top-interests")
def get_personalization_top_interests(n: int = Query(5), session: Session = Depends(get_session)):
    """챗봇/보고서 생성 프롬프트에 주입할 상위 관심사."""
    top = get_top_interests(session, n=n)
    return {"top_interests": [{"subcategory": s, **d} for s, d in top]}


# 1. 동기식 문장 대조 번역 API
@app.post("/articles/{article_id}/study-translate")
def study_translate_article(
    article_id: int,
    request: StudyTranslateRequest,
    session: Session = Depends(get_session)
):
    article = session.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="해당 기사를 찾을 수 없습니다.")

    mode = request.mode
    task = "translate_sentence_literal" if mode == "literal" else "translate_sentence_natural"
    system_prompt = _build_sentence_translation_system_prompt(mode)
    segments = _segment_article_for_translation(article.content)

    parts: list[str] = []
    try:
        for seg in segments:
            if seg["type"] == "verbatim":
                parts.append(seg["text"] + "\n")
                continue

            translated = model_router.chat(
                task=task,
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': seg["text"]},
                ],
            )
            chunk = f"{seg['text']}\n{translated}\n"
            if seg["paragraph_end"]:
                chunk += "\n"
            parts.append(chunk)
    except Exception as e:
        logger.error(f"Ollama 번역 오류: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Ollama 번역 처리 중 오류 발생: {str(e)}")

    translated_content = "".join(parts).strip()

    _save_translation(article_id, mode, task, translated_content, session)

    return {
        "status": "success",
        "article_id": article_id,
        "mode": mode,
        "translated_content": translated_content
    }


# 2. SSE 기반 실시간 스트리밍 번역 API
# 문장 하나가 번역될 때마다 그 즉시 (영어 원문 + 한국어 번역) 한 쌍을 통째로
# SSE 이벤트 하나로 흘려보낸다. 문장 분리는 파이썬이 담당하므로(_segment_article_for_translation),
# 영어 원문 줄이 누락되는 일이 구조적으로 없다.
@app.get("/articles/{article_id}/study-translate-stream")
async def study_translate_article_stream(
    article_id: int,
    mode: str = Query("literal"),
    session: Session = Depends(get_session)
):
    article = session.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="해당 기사를 찾을 수 없습니다.")

    task = "translate_sentence_literal" if mode == "literal" else "translate_sentence_natural"
    system_prompt = _build_sentence_translation_system_prompt(mode)
    segments = _segment_article_for_translation(article.content)
    total_units = sum(1 for s in segments if s["type"] == "translate") or 1

    async def event_generator() -> AsyncGenerator[str, None]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        SENTINEL = object()
        full_translated_parts: list[str] = []

        heartbeat_messages = [
            "기사 원문을 문장 단위로 나누고 있습니다",
            "문장을 하나씩 번역하고 있습니다",
            "번역을 준비하고 있습니다",
        ]
        heartbeat_idx = 0

        def _producer():
            """
            문장을 순서대로 하나씩 번역하며 완성되는 대로 큐에 넣는다. 프로세스
            종료 시 join으로 붙잡히지 않도록 daemon 스레드로 직접 띄운다
            (concurrent.futures 기반 executor는 non-daemon이라 Ctrl+C가 안 먹히는
            문제가 있었음).
            """
            done_units = 0
            try:
                for seg in segments:
                    if seg["type"] == "verbatim":
                        loop.call_soon_threadsafe(
                            queue.put_nowait,
                            {"chunk": seg["text"] + "\n", "progress": None}
                        )
                        continue

                    source_sentence = seg["text"]
                    try:
                        translated = model_router.chat(
                            task=task,
                            messages=[
                                {'role': 'system', 'content': system_prompt},
                                {'role': 'user', 'content': source_sentence},
                            ],
                        )
                    except Exception as e:
                        translated = f"[번역 실패: {e}]"

                    done_units += 1
                    progress = min(99, max(1, int(done_units / total_units * 100)))

                    chunk_text = f"{source_sentence}\n{translated}\n"
                    if seg["paragraph_end"]:
                        chunk_text += "\n"

                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {"chunk": chunk_text, "progress": progress}
                    )
            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, e)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, SENTINEL)

        initial_payload = json.dumps({"status": "starting", "progress": 0}, ensure_ascii=False)
        yield f"data: {initial_payload}\n\n"

        threading.Thread(
            target=_producer, daemon=True, name=f"translate-stream-{article_id}"
        ).start()

        try:
            first_chunk_received = False

            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=1.2)
                except asyncio.TimeoutError:
                    if not first_chunk_received:
                        msg = heartbeat_messages[heartbeat_idx % len(heartbeat_messages)]
                        heartbeat_idx += 1
                        payload = json.dumps(
                            {"status": "thinking", "progress": 0, "message": msg},
                            ensure_ascii=False
                        )
                        yield f"data: {payload}\n\n"
                    continue

                if item is SENTINEL:
                    break
                if isinstance(item, Exception):
                    raise item

                first_chunk_received = True
                chunk_text = item["chunk"]
                full_translated_parts.append(chunk_text)

                progress = item["progress"] if item["progress"] is not None else 1
                payload = json.dumps({
                    "status": "processing",
                    "progress": progress,
                    "chunk": chunk_text
                }, ensure_ascii=False)
                yield f"data: {payload}\n\n"
                await asyncio.sleep(0.01)

            final_content = "".join(full_translated_parts).strip()

            try:
                _save_translation(article_id, mode, task, final_content, session)
            except Exception as e:
                # 번역 자체는 성공했으니 저장 실패로 사용자 응답을 막지 않고 로그만 남긴다.
                logger.error(f"번역 결과 저장 실패 (article_id={article_id}): {str(e)}")

            final_payload = json.dumps({
                "status": "completed",
                "progress": 100,
                "translated_content": final_content
            }, ensure_ascii=False)
            yield f"data: {final_payload}\n\n"

        except Exception as e:
            logger.error(f"스트리밍 번역 오류: {str(e)}")
            error_payload = json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)
            yield f"data: {error_payload}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


def _collect_single_keyword(
    keyword_name: str,
    session: Session,
    months_back: int = 1,
    interval_hours: float = 24.0,
) -> dict:
    """
    검색창에 입력된 키워드 하나만 즉시 강제 수집한다 (due 체크 무시, 다른
    소스/키워드는 절대 건드리지 않는다). "파이프라인 수집" 버튼에 검색어가
    들어있을 때 이 함수가 호출된다.

    아직 등록되지 않은 키워드면 여기서 자동으로 등록한 뒤 바로 수집한다
    ("검색/등록"을 따로 먼저 눌러야만 하는 불편함을 없애기 위함 - 8/7 세션에서
    사용자 피드백으로 반영됨). months_back/interval_hours는 검색창 옆
    수집 옵션(⚙)에서 설정한 값을 그대로 받아 신규 등록 시 적용한다.
    """
    keyword = session.exec(select(Keyword).where(Keyword.name == keyword_name)).first()
    auto_registered = False
    if not keyword:
        keyword = Keyword(
            name=keyword_name,
            months_back=months_back,
            interval_hours=interval_hours,
        )
        session.add(keyword)
        session.commit()
        session.refresh(keyword)
        auto_registered = True
        logger.info(f"[collect] '{keyword_name}' 키워드 자동 등록 (파이프라인 수집에서)")

    collector = COLLECTOR_REGISTRY["google_news_search"]
    if not job_control.start_job(f"키워드 수집: {keyword.name}"):
        raise HTTPException(
            status_code=409,
            detail=f"다른 수집 작업이 이미 진행 중입니다 (현재: {job_control.current_job()}). 잠시 후 다시 시도해주세요."
        )
    try:
        result = collector.collect_for_keyword(keyword, session)
        keyword.last_collected_at = datetime.now()
        session.add(keyword)
        session.commit()
        scheduler_module._track_candidates(session, keyword, result.discovered_domains)
        new_count = result.new_count
    except Exception as e:
        logger.error(f"키워드 단독 수집 실패 ({keyword_name}): {e}")
        raise HTTPException(status_code=500, detail=f"수집 중 오류: {e}")
    finally:
        job_control.finish_job()

    message = (
        f"✨ '{keyword_name}' 키워드를 새로 등록하고 즉시 수집했습니다! (신규 {new_count}건)"
        if auto_registered
        else f"'{keyword_name}' 키워드만 수집 완료! (신규 {new_count}건)"
    )

    return {
        "status": "success",
        "total_count": new_count,
        "detail": {
            "sources_checked": 0,
            "sources_new_articles": 0,
            "keywords_checked": 1,
            "keywords_new_articles": new_count,
        },
        "message": message
    }


# 3. 수집 실행 API - keyword 파라미터가 있으면 그 키워드만(없으면 자동 등록), 없으면 전체 소스/키워드 점검
@app.get("/collect/deep-incremental")
def collect_deep_incremental(
    keyword: str | None = Query(None),
    months_back: int = Query(1),
    interval_hours: float = Query(24.0),
    session: Session = Depends(get_session),
):
    if keyword and keyword.strip():
        return _collect_single_keyword(keyword.strip(), session, months_back, interval_hours)

    stats = scheduler_module.run_tick()
    total = stats["sources_new_articles"] + stats["keywords_new_articles"]

    return {
        "status": "success",
        "total_count": total,
        "detail": stats,
        "message": (
            f"파이프라인 수집 완료! 소스 {stats['sources_checked']}건 점검"
            f"(신규 {stats['sources_new_articles']}건), "
            f"키워드 {stats['keywords_checked']}건 점검"
            f"(신규 {stats['keywords_new_articles']}건)."
        )
    }



# 3-1. 키워드 등록 + 즉시 1회 수집
@app.post("/keywords")
def create_keyword(request: KeywordCreateRequest, session: Session = Depends(get_session)):
    existing = session.exec(select(Keyword).where(Keyword.name == request.name)).first()
    if existing:
        raise HTTPException(status_code=400, detail="이미 등록된 키워드입니다.")

    keyword = Keyword(
        name=request.name,
        months_back=request.months_back,
        interval_hours=request.interval_hours,
    )
    session.add(keyword)
    session.commit()
    session.refresh(keyword)

    collector = COLLECTOR_REGISTRY["google_news_search"]
    if not job_control.start_job(f"키워드 등록: {keyword.name}"):
        logger.warning(f"키워드 등록 즉시수집 스킵 - 다른 작업 진행 중 (현재: {job_control.current_job()})")
        return {
            "status": "success",
            "keyword": keyword.name,
            "new_articles": 0,
            "message": (
                f"'{keyword.name}' 키워드가 등록됐지만, 다른 수집 작업이 진행 중이라 "
                f"즉시 수집은 건너뛰었습니다. 잠시 후 '검색/등록'을 다시 눌러 재수집해주세요."
            )
        }
    try:
        result = collector.collect_for_keyword(keyword, session)
        keyword.last_collected_at = datetime.now()
        session.add(keyword)
        session.commit()

        scheduler_module._track_candidates(session, keyword, result.discovered_domains)
        new_count = result.new_count
    except Exception as e:
        logger.error(f"키워드 즉시 수집 실패 ({request.name}): {e}")
        new_count = 0
    finally:
        job_control.finish_job()

    return {
        "status": "success",
        "keyword": keyword.name,
        "new_articles": new_count,
        "message": f"'{keyword.name}' 키워드 등록 및 즉시 수집 완료 (신규 {new_count}건)."
    }

# 3-1-1. 이미 등록된 키워드를 강제로 즉시 재수집 (due 체크 무시)
@app.post("/keywords/{keyword_id}/recollect")
def recollect_keyword(keyword_id: int, session: Session = Depends(get_session)):
    keyword = session.get(Keyword, keyword_id)
    if not keyword:
        raise HTTPException(status_code=404, detail="해당 키워드를 찾을 수 없습니다.")

    collector = COLLECTOR_REGISTRY["google_news_search"]
    if not job_control.start_job(f"키워드 재수집: {keyword.name}"):
        raise HTTPException(
            status_code=409,
            detail=f"다른 수집 작업이 이미 진행 중입니다 (현재: {job_control.current_job()}). 잠시 후 다시 시도해주세요."
        )
    try:
        result = collector.collect_for_keyword(keyword, session)
        keyword.last_collected_at = datetime.now()
        session.add(keyword)
        session.commit()
        scheduler_module._track_candidates(session, keyword, result.discovered_domains)
        new_count = result.new_count
    except Exception as e:
        logger.error(f"키워드 강제 재수집 실패 ({keyword.name}): {e}")
        raise HTTPException(status_code=500, detail=f"재수집 중 오류: {e}")
    finally:
        job_control.finish_job()

    return {
        "status": "success",
        "keyword": keyword.name,
        "new_articles": new_count,
        "message": f"'{keyword.name}' 재수집 완료 (신규 {new_count}건)."
    }


# 3-1-2. 키워드 수집 주기(interval_hours) 변경
@app.patch("/keywords/{keyword_id}")
def update_keyword(keyword_id: int, request: KeywordUpdateRequest, session: Session = Depends(get_session)):
    keyword = session.get(Keyword, keyword_id)
    if not keyword:
        raise HTTPException(status_code=404, detail="해당 키워드를 찾을 수 없습니다.")
    if request.interval_hours <= 0:
        raise HTTPException(status_code=400, detail="interval_hours는 0보다 커야 합니다.")

    keyword.interval_hours = request.interval_hours
    session.add(keyword)
    session.commit()
    return {
        "status": "success",
        "message": f"'{keyword.name}' 수집 주기를 {request.interval_hours}시간으로 변경했습니다."
    }


# 3-1-3. '검색주기설정' 버튼 전용 - 키워드가 없으면 등록, 있으면 개월수/주기 갱신.
# '실시간 수집' 버튼이 즉시 수집을 전담하므로, 여기서는 절대 크롤링을 트리거하지 않는다
# (8/7 세션 후반 - 두 버튼의 역할을 "지금 당장 수집" vs "설정만 정하기"로 명확히 분리).
# 이미 등록된 키워드에 대해 버튼을 다시 눌러도(재클릭) months_back/interval_hours를
# 그대로 덮어써 수정할 수 있다 (upsert 방식).
@app.put("/keywords/interval")
def set_keyword_interval(request: KeywordIntervalSetRequest, session: Session = Depends(get_session)):
    if request.interval_hours <= 0:
        raise HTTPException(status_code=400, detail="interval_hours는 0보다 커야 합니다.")
    if request.months_back <= 0:
        raise HTTPException(status_code=400, detail="months_back은 0보다 커야 합니다.")

    keyword = session.exec(select(Keyword).where(Keyword.name == request.name)).first()
    if keyword:
        keyword.months_back = request.months_back
        keyword.interval_hours = request.interval_hours
        session.add(keyword)
        session.commit()
        return {
            "status": "success",
            "message": (
                f"'{request.name}' 키워드 설정을 변경했습니다 "
                f"(최근 {request.months_back}개월 이내 자료, {request.interval_hours}시간마다 재수집)."
            )
        }

    keyword = Keyword(name=request.name, months_back=request.months_back, interval_hours=request.interval_hours)
    session.add(keyword)
    session.commit()
    return {
        "status": "success",
        "message": (
            f"'{request.name}' 키워드를 등록했습니다 "
            f"(최근 {request.months_back}개월 이내 자료, {request.interval_hours}시간마다 재수집)."
        )
    }


# 3-1-4. 키워드 삭제 - 등록 취소와 함께, 그 키워드로 수집된 기사도 전부 함께 삭제한다
# (8/7 세션 후반 요청 반영 - 이전엔 기사를 남겼으나 이제는 완전 삭제로 변경).
@app.delete("/keywords/{keyword_id}")
def delete_keyword(keyword_id: int, session: Session = Depends(get_session)):
    keyword = session.get(Keyword, keyword_id)
    if not keyword:
        raise HTTPException(status_code=404, detail="해당 키워드를 찾을 수 없습니다.")

    name = keyword.name

    articles_to_delete = session.exec(select(Article).where(Article.keyword == name)).all()
    deleted_count = len(articles_to_delete)
    for article in articles_to_delete:
        session.delete(article)

    session.delete(keyword)
    session.commit()
    return {
        "status": "success",
        "message": f"'{name}' 키워드와 수집된 기사 {deleted_count}건을 함께 삭제했습니다."
    }


# 3-2. 키워드 목록 조회 - 키워드 관리 패널(전체 보기)에서 쓸 통계(건수/게시일 범위) 포함
@app.get("/keywords")
def list_keywords(session: Session = Depends(get_session)):
    keywords = session.exec(select(Keyword)).all()
    result = []

    for k in keywords:
        articles = session.exec(select(Article).where(Article.keyword == k.name)).all()

        # published_at은 RSS 원문의 pubDate 문자열을 그대로 저장한 것(RFC822 형식이 대부분).
        # 파싱 실패하는 건(형식이 다른 소수 케이스)은 날짜 범위 계산에서 조용히 제외한다 -
        # 목적이 "대략 언제부터 언제까지 모았는지" 보여주는 것이라 일부 누락은 괜찮음.
        parsed_dates = []
        for a in articles:
            if not a.published_at:
                continue
            try:
                parsed_dates.append(parsedate_to_datetime(a.published_at))
            except Exception:
                continue

        result.append({
            "id": k.id,
            "name": k.name,
            "months_back": k.months_back,
            "interval_hours": k.interval_hours,
            "last_collected_at": k.last_collected_at.isoformat() if k.last_collected_at else None,
            "article_count": len(articles),
            "earliest_published_at": min(parsed_dates).isoformat() if parsed_dates else None,
            "latest_published_at": max(parsed_dates).isoformat() if parsed_dates else None,
        })

    return {"keywords": result}


# 3-3. 소스 목록 조회 (관리 패널용)
@app.get("/sources")
def list_sources(session: Session = Depends(get_session)):
    sources = session.exec(select(Source).order_by(Source.id.desc())).all()
    return {
        "sources": [
            {
                "id": s.id,
                "name": s.name,
                "url": s.url,
                "category": s.category,
                "source_type": s.source_type,
                "origin": s.origin,
                "status": s.status,
                "interval_hours": s.interval_hours,
                "fail_count": s.fail_count,
                "last_success_at": s.last_success_at.isoformat() if s.last_success_at else None,
                "last_attempt_at": s.last_attempt_at.isoformat() if s.last_attempt_at else None,
                "block_reason": s.block_reason,
            }
            for s in sources
        ]
    }


# 3-4. 소스 수동 등록 (사용자가 직접 발견한 출처 즉시 확정)
@app.post("/sources")
def create_source(request: SourceCreateRequest, session: Session = Depends(get_session)):
    existing = session.exec(select(Source).where(Source.url == request.url)).first()
    if existing:
        raise HTTPException(status_code=400, detail="이미 등록된 URL입니다.")

    source = Source(
        name=request.name,
        url=request.url,
        category=request.category,
        source_type=request.source_type,
        origin=SourceOrigin.MANUAL_ADDED,
        interval_hours=request.interval_hours,
    )
    session.add(source)
    session.commit()

    return {"status": "success", "message": f"'{request.name}' 소스가 등록되었습니다."}


# 3-4-1. 소스 점검 주기(interval_hours) 변경
@app.patch("/sources/{source_id}")
def update_source(source_id: int, request: SourceUpdateRequest, session: Session = Depends(get_session)):
    source = session.get(Source, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="해당 소스를 찾을 수 없습니다.")
    if request.interval_hours <= 0:
        raise HTTPException(status_code=400, detail="interval_hours는 0보다 커야 합니다.")

    source.interval_hours = request.interval_hours
    session.add(source)
    session.commit()
    return {
        "status": "success",
        "message": f"'{source.name}' 점검 주기를 {request.interval_hours}시간으로 변경했습니다."
    }


# 3-5. 소스 삭제 (탈락 후보 등 사용자 최종 판단)
@app.delete("/sources/{source_id}")
def delete_source(source_id: int, session: Session = Depends(get_session)):
    source = session.get(Source, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="해당 소스를 찾을 수 없습니다.")

    name = source.name
    message = f"'{name}' 소스가 삭제되었습니다."

    # 블록리스트 항목을 삭제하면, 그 도메인을 blocked_domains에 영구 기록해서
    # 앞으로의 수집(키워드 검색 등)에서 다시는 검색/크롤링 시도조차 하지 않게 한다.
    if source.source_type == "blocked":
        domain = urlparse(source.url).netloc
        already_blocked = session.exec(
            select(BlockedDomain).where(BlockedDomain.domain == domain)
        ).first()
        if not already_blocked:
            session.add(BlockedDomain(domain=domain, reason=source.block_reason))
        message = f"'{name}' 소스가 삭제되었고, 앞으로 '{domain}' 도메인은 검색에서 제외됩니다."

    session.delete(source)
    session.commit()

    return {"status": "success", "message": message}


# 3-6. 스케줄러 틱 간격 조회
@app.get("/scheduler/config")
def get_scheduler_config():
    config = scheduler_module.get_or_create_config()
    return {"tick_minutes": config.tick_minutes}


# 3-7. 스케줄러 틱 간격 변경 (즉시 재스케줄)
@app.put("/scheduler/config")
def update_scheduler_config(request: SchedulerConfigUpdateRequest, session: Session = Depends(get_session)):
    if request.tick_minutes < 1:
        raise HTTPException(status_code=400, detail="tick_minutes는 1 이상이어야 합니다.")

    config = session.exec(select(SchedulerConfig)).first()
    if config is None:
        config = SchedulerConfig(tick_minutes=request.tick_minutes)
    else:
        config.tick_minutes = request.tick_minutes
        config.updated_at = datetime.now()
    session.add(config)
    session.commit()

    scheduler.reschedule_job("tick_scheduler", trigger=IntervalTrigger(minutes=request.tick_minutes))
    logger.info(f"[scheduler] 틱 간격 변경: {request.tick_minutes}분")

    # 등록된 소스/키워드 중 점검 주기(시간)가 새 tick_minutes보다 촘촘한 게 있으면,
    # 실제로는 tick_minutes 간격으로만 점검되니 경고를 같이 돌려준다. 이렇게 해야
    # "스케줄러 점검 간격"과 "소스/키워드별 주기"가 서로 무관하게 따로 노는 게 아니라,
    # 하나의 정합성 규칙으로 묶여서 보인다.
    warning = None
    candidates = [
        v for v in [
            session.exec(select(sql_func.min(Source.interval_hours))).first(),
            session.exec(select(sql_func.min(Keyword.interval_hours))).first(),
        ] if v is not None
    ]
    if candidates:
        tightest_hours = min(candidates)
        if request.tick_minutes > tightest_hours * 60:
            warning = (
                f"⚠️ 가장 짧은 소스/키워드 점검 주기({tightest_hours}시간)보다 "
                f"스케줄러 점검 간격({request.tick_minutes}분)이 더 깁니다. "
                f"이 항목은 설정한 주기가 아니라 {request.tick_minutes}분마다만 점검됩니다."
            )

    return {"status": "success", "tick_minutes": request.tick_minutes, "warning": warning}


# 4. 시스템 통계 API
@app.get("/stats/system")
def get_system_stats():
    cpu_usage = psutil.cpu_percent(interval=None)

    mem = psutil.virtual_memory()
    memory_usage = mem.percent
    memory_used_gb = round(mem.used / (1024 ** 3), 2)
    memory_total_gb = round(mem.total / (1024 ** 3), 2)

    gpu_usage = get_gpu_usage()

    db_size_mb = 0.0
    if os.path.exists(DB_NAME):
        db_size_bytes = os.path.getsize(DB_NAME)
        db_size_mb = round(db_size_bytes / (1024 * 1024), 2)

    return {
        "status": "success",
        "cpu_usage": f"{cpu_usage}%",
        "memory_usage": f"{memory_usage}% ({memory_used_gb}GB / {memory_total_gb}GB)",
        "gpu_usage": gpu_usage,
        "db_usage": f"{db_size_mb} MB",
        "activity": activity_tracker.get_snapshot()
    }


def get_gpu_usage() -> str:
    """
    실제 Metal GPU 사용률(%)을 macmon(sudo 불필요, brew install macmon)으로 조회한다.
    macmon이 없으면 model_router의 생성 중 여부로 대략적인 추정치를 대체 표시한다.
    """
    try:
        result = subprocess.run(
            ['macmon', 'pipe', '-s', '1', '-i', '300'],
            capture_output=True, text=True, timeout=3
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout.strip().splitlines()[-1])
            gpu_ratio = data.get("gpu_usage", [None, None])[1]
            if gpu_ratio is not None:
                return f"{gpu_ratio * 100:.1f}%"
    except FileNotFoundError:
        logger.debug("macmon이 설치되어 있지 않음 (brew install macmon)")
    except Exception as e:
        logger.debug(f"macmon 조회 실패: {e}")

    # macmon이 없을 때의 대체 표시 (정확한 %는 아니고 생성 중 여부만)
    try:
        return "생성 중 (macmon 미설치)" if model_router.is_generating() else "0% (macmon 미설치)"
    except Exception:
        return "확인 불가"


# 5. 소스 통계 API
@app.get("/stats/sources")
def get_source_stats(session: Session = Depends(get_session)):
    query = select(Article.source, sql_func.count(Article.id)).group_by(Article.source).order_by(sql_func.count(Article.id).desc())
    results = session.exec(query).all()

    source_counts = {row[0] if row[0] else "Unknown": row[1] for row in results}
    total_articles = sum(source_counts.values())

    return {
        "status": "success",
        "total_articles": total_articles,
        "source_counts": source_counts
    }


# 6. 기존 데이터 정제 API
@app.get("/clean-existing-articles")
def clean_existing_articles(session: Session = Depends(get_session)):
    articles = session.exec(select(Article)).all()
    updated_count = 0

    for article in articles:
        if article.content:
            new_content = clean_article_content(article.content)
            if new_content != article.content:
                article.content = new_content
                updated_count += 1

    session.commit()

    return {
        "status": "success",
        "message": f"총 {len(articles)}개의 기존 아티클 중 h3 이하 제목 규칙 및 노이즈가 정제된 {updated_count}개의 아티클이 성공적으로 업데이트되었습니다."
    }


# 6-1. 개별 아티클 정제 API (정규식 1차 + LLM 2차)
@app.post("/articles/{article_id}/clean")
def clean_single_article(article_id: int, session: Session = Depends(get_session)):
    article = session.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="해당 기사를 찾을 수 없습니다.")

    original_len = len(article.content or "")

    step1 = clean_article_content(article.content or "")
    step2 = extract_body_via_llm(step1)

    removed = original_len - len(step2)

    article.content = step2
    article.origin = ContentOrigin.LLM_CLEANED
    article.model_used = model_router.model_for_task("extract_body")
    session.commit()

    return {
        "status": "success",
        "article_id": article_id,
        "removed_chars": removed,
        "content": step2
    }


# 7. 아티클 삭제 API
@app.delete("/articles/{article_id}")
def delete_article(article_id: int, session: Session = Depends(get_session)):
    article = session.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="해당 기사를 찾을 수 없습니다.")

    session.delete(article)
    session.commit()

    return {"status": "success", "message": f"ID {article_id} 기사가 데이터베이스에서 완전히 삭제되었습니다."}


# 8. 아티클 내용 수정 API
@app.put("/articles/{article_id}/content")
def update_article_content(article_id: int, request: ContentUpdateRequest, session: Session = Depends(get_session)):
    article = session.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="해당 기사를 찾을 수 없습니다.")

    article.content = request.new_content
    article.origin = ContentOrigin.USER_EDITED
    article.model_used = None  # 사람이 직접 수정했으니 "어떤 모델이 만들었는지"는 더 이상 의미 없음
    session.commit()

    return {"status": "success", "message": f"ID {article_id} 기사의 내용이 성공적으로 수정되었습니다."}


# 이미지 업로드 API (편집기용)
@app.post("/upload/image")
async def upload_image(file: UploadFile = File(...)):
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="이미지 파일만 업로드 가능합니다.")

    ext = os.path.splitext(file.filename)[1] or ".png"
    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = os.path.join(UPLOAD_DIR, filename)

    with open(filepath, "wb") as f:
        content = await file.read()
        f.write(content)

    return {
        "status": "success",
        "url": f"http://localhost:8000/uploads/images/{filename}"
    }


# 개인저장방(Vault) - 폴더 목록 조회
@app.get("/vault/folders")
def list_vault_folders():
    folders = [
        d for d in os.listdir(VAULT_DIR)
        if os.path.isdir(os.path.join(VAULT_DIR, d))
    ]
    return {"folders": sorted(folders)}


# 개인저장방(Vault) - 내보내기 (DB와 무관한 1회성 스냅샷 저장)
@app.post("/vault/export")
def export_to_vault(request: VaultExportRequest):
    folder_path = _safe_vault_path(request.folder)
    os.makedirs(folder_path, exist_ok=True)

    filename = request.filename if request.filename.endswith(".md") else f"{request.filename}.md"
    filename = _unique_vault_filename(folder_path, filename)
    filepath = os.path.join(folder_path, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(request.content)

    return {"status": "success", "path": filepath, "filename": filename}


# ============================================
# 카테고리 매칭 - /stats/keywords(건수)와 /articles?keyword=(목록)이
# 반드시 "같은 기준"으로 판정하도록 로직을 한 곳에 모은다.
# 예전엔 건수는 "베스트 카테고리 1개만 채택"하는 엄격한 점수제였고, 목록은
# "카테고리에 속한 단어 아무거나 하나라도 포함되면 통과"하는 느슨한 방식이라
# 버튼에 찍힌 건수와 실제로 열리는 기사 수가 서로 달랐다 (예: 정치 2건 표시,
# 클릭하면 4건 조회). 아래 함수 하나로 두 엔드포인트를 통일한다.
# ============================================
def _score_categories_for_article(article: Article) -> dict[str, int]:
    """기사 하나에 대해, 블랙리스트에 걸리지 않은 카테고리별 매칭 점수를 계산한다."""
    title_lower = (article.title or "").lower()
    content_lower = (article.content or "").lower()
    source_lower = (article.source or "").lower()

    category_scores: dict[str, int] = {}

    for category, config in CATEGORY_CONFIG.items():
        has_blacklisted = False
        for bad_word in config["blacklist"]:
            if bad_word in title_lower or bad_word in content_lower:
                has_blacklisted = True
                break
        if has_blacklisted:
            continue

        score = 0
        if category.lower() in source_lower:
            score += 10

        for term in config["keywords"]:
            pattern = r'(?:^|\b|[^\w])' + re.escape(term) + r'(?:$|\b|[^\w])'
            title_matches = len(re.findall(pattern, title_lower, re.IGNORECASE))
            content_matches = len(re.findall(pattern, content_lower, re.IGNORECASE))
            score += (title_matches * 3) + (content_matches * 1)

        if score > 0:
            category_scores[category] = score

    return category_scores


def _best_category_for_article(article: Article) -> str | None:
    """기사 하나가 최종적으로 속하는 카테고리(가장 점수가 높은 것 1개)를 반환한다."""
    scores = _score_categories_for_article(article)
    if not scores:
        return None
    return max(scores, key=scores.get)


# 9. 키워드 통계 API
@app.get("/stats/keywords")
def get_keyword_stats(session: Session = Depends(get_session)):
    articles = session.exec(select(Article)).all()

    stats = {cat: 0 for cat in CATEGORY_CONFIG.keys()}

    for article in articles:
        best_category = _best_category_for_article(article)
        if best_category:
            stats[best_category] += 1

    filtered_stats = {k: v for k, v in stats.items() if v > 0}

    # 사용자가 등록한 실제 키워드(Keyword.name)의 정확한 카운트도 병합
    registered_counts = session.exec(
        select(Article.keyword, sql_func.count(Article.id))
        .where(Article.keyword.is_not(None))
        .group_by(Article.keyword)
    ).all()
    for kw_name, cnt in registered_counts:
        if kw_name:
            filtered_stats[kw_name] = cnt

    return {"keyword_stats": filtered_stats}


# 10. 아티클 목록 조회 API
@app.get("/articles")
def get_articles(
    keyword: str = Query(None),
    session: Session = Depends(get_session)
):
    if not keyword:
        query = select(Article).order_by(Article.id.desc())
        articles = session.exec(query).all()
        return {
            "articles": [
                {
                    "id": a.id,
                    "title": a.title,
                    "url": a.url,
                    "published_at": a.published_at,
                    "content": a.content,
                    "source": a.source
                }
                for a in articles
            ]
        }

    clean_kw = keyword.strip()

    # 사용자가 등록한 키워드와 정확히 일치하면, 그 키워드로 태깅된 기사만 정확히 매칭
    registered = session.exec(
        select(Keyword).where(Keyword.name == clean_kw)
    ).first()

    if registered:
        query = select(Article).where(Article.keyword == registered.name).order_by(Article.id.desc())
        articles = session.exec(query).all()
    else:
        # 고정 카테고리(정치/AI/골프 등) 이름이나, 그 카테고리에 속한 키워드와 정확히
        # 일치하면 /stats/keywords와 완전히 동일한 점수제 판정으로 필터링한다.
        # (건수와 목록이 항상 같은 숫자를 가리키도록 보장하는 부분)
        clean_kw_lower = clean_kw.lower()
        matched_category = None
        for cat, config in CATEGORY_CONFIG.items():
            if clean_kw_lower == cat.lower() or clean_kw_lower in [s.lower() for s in config["keywords"]]:
                matched_category = cat
                break

        if matched_category:
            all_articles = session.exec(select(Article).order_by(Article.id.desc())).all()
            articles = [
                a for a in all_articles
                if _best_category_for_article(a) == matched_category
            ]
        else:
            # 어떤 고정 카테고리에도 안 걸리는 임의의 검색어 - 기존처럼 단순 텍스트 포함 검색으로 폴백
            query = select(Article).where(
                (Article.title.contains(clean_kw)) | (Article.content.contains(clean_kw))
            ).order_by(Article.id.desc())
            articles = session.exec(query).all()

    return {
        "articles": [
            {
                "id": a.id,
                "title": a.title,
                "url": a.url,
                "published_at": a.published_at,
                "content": a.content,
                "source": a.source
            }
            for a in articles
        ]
    }


# 11. 수집 상태 확인 API
@app.get("/collect/status")
def get_collection_status():
    job = scheduler.get_job("tick_scheduler")
    return {
        "status": "success",
        "last_run": job.last_run_time.isoformat() if job and job.last_run_time else None,
        "next_run": job.next_run_time.isoformat() if job and job.next_run_time else None,
        "running": scheduler.running
    }


# 12. 플랫폼 구성요소 조회 API
@app.get("/platform/info")
def get_platform_info():
    config = scheduler_module.get_or_create_config()

    return {
        "backend": {
            "framework": "FastAPI",
            "server": "Uvicorn",
            "language": "Python",
            "scheduler": f"APScheduler (틱 기반, {config.tick_minutes}분 간격)",
        },
        "frontend": {
            "framework": "React (Vite)",
            "editor": "Milkdown/Crepe - Typora 스타일 WYSIWYG 마크다운 에디터",
            "state": "useReducer 기반 커스텀 상태 관리",
        },
        "database": {
            "engine": "SQLite",
            "orm": "SQLModel (SQLAlchemy)",
            "file": DB_NAME,
            "tables": [
                "articles", "translations", "sources", "keywords",
                "candidate_sources", "scheduler_config",
                "notifications", "user_preferences",
            ],
        },
        "llm": {
            "runtime": "Ollama (MLX 가속, Apple Silicon)",
            "light_tier": model_router.TIER_MODELS.get(model_router.ModelTier.LIGHT),
            "heavy_tier": model_router.TIER_MODELS.get(model_router.ModelTier.HEAVY),
            "router": "model_router.py - 작업별 티어 라우팅, think=False 강제, keep_alive 정책 분리",
        },
        "collection": {
            "pattern": "Collector 플러그인 아키텍처",
            "implemented": ["RSSCollector (고정 소스)", "GoogleNewsSearchCollector (키워드 기반)"],
            "planned": ["YouTubeCollector", "PodcastCollector", "ImageGalleryCollector"],
            "promotion_rule": "같은 키워드에서 같은 출처가 3회 이상 등장 시 자동 승격",
        },
        "storage": {
            "structured_data": "SQLite (기사 메타정보·본문)",
            "personal_vault": "~/Documents/AI-Vault (마크다운 파일, Typora 등 외부 편집기와 호환)",
            "uploads": "uploads/images/ (에디터 이미지 첨부)",
        },
        "architecture_layers": [
            {"name": "수집 계층", "desc": "Source/Keyword 기반 Collector 플러그인이 외부 데이터를 가져옴"},
            {"name": "모델 계층", "desc": "model_router가 작업 성격에 따라 경량/고품질 모델로 라우팅"},
            {"name": "저장 계층", "desc": "구조화 데이터는 SQLite, 대용량·개인 문서는 로컬 파일시스템"},
            {"name": "API 계층", "desc": "FastAPI가 수집/번역/편집/관리 기능을 REST와 SSE로 노출"},
            {"name": "프론트엔드 계층", "desc": "React가 카드 UI, 에디터, 소스/키워드 관리 패널을 렌더링"},
        ],
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
