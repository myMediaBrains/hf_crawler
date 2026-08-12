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
import time
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
import source_scoring
from statistics import mean

import priority
import delivery as delivery_channels

import github_repository

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
    User, TextGeneration, Delivery, Tag, TagKeyword, TagBlacklist, ArticleTag, TagRelation,
    GitHubRepo, GitHubRepoSnapshot, GitHubReadmeHistory, GitHubRepoTag,
    InteractionSignal,
)
import tagging

# 개인화 레이어 import (신규)
from personalization import (
    classify_and_store, store_explicit_feedback,
    get_profile, get_top_interests,
    to_kst, register_user_and_backfill, store_tag_preference,
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

class BulkIntervalUpdateRequest(BaseModel):
    months_back: int = 1
    interval_hours: float = 24.0


class GenreCreateRequest(BaseModel):
    major_category: str
    mid_category: str
    sub_category: str
    search_query: str | None = None
    # 2026-08-09: 분류명(sub_category)과 별개로, 실제 검색에 쓸 자유로운 영어
    # 문구를 받을 수 있게 함. 없으면 기존처럼 sub_category를 그대로 검색어로 씀.
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
    migrate_db.migrate_translations(DB_NAME)
    migrate_db.migrate_interaction_signals(DB_NAME)
    migrate_db.migrate_text_generations(DB_NAME)
    migrate_db.migrate_github_repos(DB_NAME)
    logger.info("📊 데이터베이스 테이블이 준비되었습니다.")

    scheduler_module.seed_manual_sources(TARGET_SOURCES)
    # taxonomy.seed_taxonomy_keywords() 삭제 (2026-08-09) - Tag는 빈 상태로
    # 시작하기로 결정. 장르편집기/채팅 자동수집으로만 채워진다.

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
    # 종료 직전에 지금 실행 중인 크롤링 작업이 있다면 취소 신호부터 보낸다.
    # 이 신호가 없으면 크롤링 루프가 스스로 멈출 계기가 없어서, wait=False를
    # 줘도 그 작업을 돌리고 있는 스레드(APScheduler 내부 ThreadPoolExecutor -
    # daemon이 아님)가 자연 종료될 때까지 프로세스가 못 죽는다. 크롤링 루프는
    # URL 하나 처리할 때마다 is_cancelled()를 확인하므로, 이 신호를 보내면
    # 늦어도 현재 처리 중인 URL의 하드 타임아웃(최대 30초) 안에는 멈춘다.
    cancelled_job = job_control.cancel_current_job()
    if cancelled_job:
        logger.info(f"[shutdown] 진행 중이던 작업에 취소 신호 전송: {cancelled_job}")
    scheduler.shutdown(wait=False)
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

# 2026-08-09: 한글 문장 경계용 - 한글은 대소문자 구분이 없어서 영어용 패턴의
# "다음 글자가 대문자/숫자"라는 조건이 무의미하다. 구두점 뒤 공백만으로 판단.
_SENTENCE_BOUNDARY_KO = re.compile(r'(?<=[.!?])\s+')

# 이미지/링크 단독 줄(![alt](url) 또는 [text](url)만 있는 줄) 감지용
_STANDALONE_LINK_LINE = re.compile(r'^!?\[.*?\]\(.*?\)$')


def _split_paragraph_into_sentences(paragraph: str, source_lang: str = "en") -> list[str]:
    """빈 줄 없는 문단 하나를 문장 단위로 쪼갠다."""
    paragraph = paragraph.strip()
    if not paragraph:
        return []
    boundary = _SENTENCE_BOUNDARY_KO if source_lang == "ko" else _SENTENCE_BOUNDARY
    parts = boundary.split(paragraph)
    return [p.strip() for p in parts if p.strip()]


def _segment_article_for_translation(content: str, source_lang: str = "en") -> list[dict]:    
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
        sentences = _split_paragraph_into_sentences(text, source_lang=source_lang)
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


def _build_sentence_translation_system_prompt(mode: str, source_lang: str = "en") -> str:
    """
    문장 하나만 번역시키는 시스템 프롬프트. 원문 재출력을 요구하지 않으므로
    (그건 파이썬이 담당) 모델이 지켜야 할 지시가 단순해서 훨씬 안정적으로 따른다.

    2026-08-09: source_lang="ko"면 한→영 방향으로 뒤집는다 (번역 버튼 양방향화).
    """
    if source_lang == "ko":
        mode_instruction = (
            "Provide a strict, literal translation into English, preserving Korean "
            "sentence structure as much as possible (직역)."
            if mode == "literal"
            else "Provide a natural, fluent English translation (의역)."
        )
        return (
            "You are an expert Korean-to-English translator. "
            "You will be given exactly ONE Korean sentence or line (it may include markdown "
            "syntax such as a '#' header marker, a '-' or '*' list marker, or [text](url) "
            "link syntax).\n\n"
            "Output ONLY the English translation of that sentence — nothing else. "
            "Do NOT repeat or quote the Korean sentence. Do NOT add any preamble, notes, "
            "or explanation of what you translated. Do NOT wrap the output in quotes.\n"
            "If the source starts with a markdown marker (#, -, *), keep that same marker "
            "at the start of your English output. If the source contains a [text](url) link, "
            "translate only the visible text portion and keep the (url) exactly as-is.\n"
            "Do NOT translate proper nouns, company names, product/brand names, trademarks, "
            "inline code, or URLs — keep those in their original form.\n"
            f"Translation style: {mode_instruction}"
        )

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

@app.post("/admin/backfill-source-tags")
def backfill_source_tags(session: Session = Depends(get_session)):
    """
    일회성 관리 작업 - tag_id가 비어서 '미분류'로 뜨는 기존 출처를 복구한다.
    1순위: 연결된 keyword_id가 있고 그 키워드에 tag_id가 있으면 그대로 물려받음
           (자동 승격된 소스가 원본 키워드와 다시 정확히 일치하게 됨).
    2순위: Source.name이 "[카테고리] 표시이름" 형식이면(고정 RSS 소스, 또는
           승격 시점에 이미 이 형식으로 저장된 이름) 대괄호 안 문자열을 대분류로
           추출해서 태그를 만들거나 재사용한다.
    """
    sources = session.exec(select(Source).where(Source.tag_id.is_(None))).all()
    fixed = 0

    for s in sources:
        tag_id = None

        if s.keyword_id:
            kw = session.get(Keyword, s.keyword_id)
            if kw and kw.tag_id:
                tag_id = kw.tag_id

        if tag_id is None:
            m = re.match(r'^\[(.+?)\]', s.name or "")
            if m:
                category = m.group(1).strip()
                if category:
                    tag = tagging.get_or_create_tag(session, name=category, major_category=category)
                    tag_id = tag.id

        if tag_id:
            s.tag_id = tag_id
            session.add(s)
            fixed += 1

    session.commit()
    return {
        "status": "success",
        "fixed": fixed,
        "checked": len(sources),
        "message": f"미분류 출처 {len(sources)}건 중 {fixed}건의 분류를 복구했습니다.",
    }

@app.post("/admin/sync-sources-to-genres")
def sync_sources_to_genres(session: Session = Depends(get_session)):
    """
    출처관리에는 있지만 아직 장르편집기(Keyword 테이블)엔 없는 소스를 자동 등록.
    Source.name의 대괄호 텍스트(예: "[Golf] Golf.com"의 "Golf")를 소분류로,
    그 값을 중분류로 재배치한다. 이미 다른 곳에서 그 값이 중분류로 쓰이고
    있으면 그 대분류를 물려받고, 없으면 "미분류"로 표시한다.

    keyword_id가 이미 있는 소스(키워드 검색으로 발견/승격된 것)는 이미 자기
    키워드를 통해 장르편집기에 올라가 있으므로 대상에서 제외한다.
    """
    sources = session.exec(select(Source).where(Source.keyword_id.is_(None))).all()
    created, skipped = 0, 0

    for s in sources:
        m = re.match(r'^\[(.+?)\]', s.name or "")
        if not m:
            skipped += 1
            continue
        word = m.group(1).strip()
        if not word:
            skipped += 1
            continue

        existing_kw = session.exec(select(Keyword).where(Keyword.name == word)).first()
        if existing_kw:
            skipped += 1
            continue

        # 이 값이 이미 다른 태그의 중분류로 쓰이고 있으면 그 대분류를 물려받는다
        matched_major = session.exec(
            select(Tag.major_category).where(Tag.mid_category == word)
        ).first()
        major = matched_major if matched_major else "미분류"

        tag = tagging.get_or_create_tag(session, name=word, major_category=major, mid_category=word)

        keyword = Keyword(
            name=tag.name,
            tag_id=tag.id,
            months_back=1,
            interval_hours=s.interval_hours or 24.0,
        )
        session.add(keyword)
        session.commit()
        session.refresh(keyword)

        s.tag_id = tag.id  # 이 소스도 방금 정리된 태그로 다시 연결
        session.add(s)
        session.commit()
        created += 1

    return {
        "status": "success",
        "created": created,
        "skipped": skipped,
        "message": f"{created}건을 장르편집기에 새로 등록했습니다 ({skipped}건은 이미 있거나 대상 아님)."
    }

@app.post("/admin/purge-orphaned-tags")
def purge_orphaned_tags(session: Session = Depends(get_session)):
    """
    일회성 정리 - 실제 기사가 없는데 남아있는 ArticleTag(유령 연결)를 전부 지운다.
    지금까지 기사 삭제 시 ArticleTag를 안 지웠던 버그(2026-08-10 수정)로 이미
    쌓인 찌꺼기를 청소하는 용도. 한 번만 호출하면 됨.
    """
    existing_article_ids = set(session.exec(select(Article.id)).all())
    all_links = session.exec(select(ArticleTag)).all()
    orphaned = [link for link in all_links if link.article_id not in existing_article_ids]

    for link in orphaned:
        session.delete(link)
    session.commit()

    return {
        "status": "success",
        "purged": len(orphaned),
        "message": f"유령 태그 연결 {len(orphaned)}건을 정리했습니다.",
    }
    
@app.post("/admin/merge-duplicate-tags")
def merge_duplicate_tags(session: Session = Depends(get_session)):
    """
    일회성 정리 - 대소문자만 다른 중복 태그(예: "AI"/"ai")를 하나로 병합한다.
    2026-08-10 대소문자 무시 매칭 패치 이전에 이미 생긴 중복을 청소하는 용도.
    먼저 생성된(created_at이 빠른) 쪽을 대표로 남기고, 나머지는 참조를
    전부 옮긴 뒤 삭제한다.
    """
    all_tags = session.exec(select(Tag)).all()
    groups: dict[str, list[Tag]] = {}
    for tag in all_tags:
        groups.setdefault(tag.name.lower(), []).append(tag)

    merged = 0
    for lower_name, tags in groups.items():
        if len(tags) < 2:
            continue
        tags.sort(key=lambda t: t.created_at)
        keep = tags[0]
        for dup in tags[1:]:
            session.exec(
                select(ArticleTag).where(ArticleTag.tag_id == dup.id)
            )
            for row in session.exec(select(ArticleTag).where(ArticleTag.tag_id == dup.id)).all():
                row.tag_id = keep.id
                session.add(row)
            for row in session.exec(select(GitHubRepoTag).where(GitHubRepoTag.tag_id == dup.id)).all():
                row.tag_id = keep.id
                session.add(row)
            for row in session.exec(select(TagKeyword).where(TagKeyword.tag_id == dup.id)).all():
                session.delete(row)
            for row in session.exec(select(InteractionSignal).where(InteractionSignal.tag_id == dup.id)).all():
                row.tag_id = keep.id
                session.add(row)

            keyword_row = session.exec(select(Keyword).where(Keyword.tag_id == dup.id)).first()
            if keyword_row:
                keyword_row.tag_id = keep.id
                session.add(keyword_row)

            session.delete(dup)
            merged += 1

    session.commit()
    return {
        "status": "success",
        "merged": merged,
        "message": f"대소문자만 다른 중복 태그 {merged}건을 병합했습니다.",
    }

# ============================================
# GitHub README
# ============================================
@app.post("/admin/reanalyze-github-repos")
def reanalyze_github_repos(session: Session = Depends(get_session)):
    """일회성 - 기존 분석을 전부 지우고 새 기준(내 플랫폼 연관성)으로 재생성한다."""
    repos = session.exec(select(GitHubRepo)).all()
    count = 0
    for repo in repos:
        repo.analysis_hash = None  # 강제로 재생성 트리거
        session.add(repo)
        count += 1
    session.commit()
    return {"status": "success", "message": f"{count}건을 다음 상세 조회 시 재분석하도록 초기화했습니다."}

# ============================================
# 사용자 프로필 API (신규)
# ============================================

class UserRegisterRequest(BaseModel):
    user_id: str
    display_name: str | None = None


@app.post("/users/register")
def register_user(request: UserRegisterRequest, session: Session = Depends(get_session)):
    """
    비밀번호 없는 로컬 개인용 사용자 등록. user_id는 사용자가 직접 정한 문자열.
    등록 성공 시, 그동안 user_id 없이 쌓인 InteractionSignal/TextGeneration을
    전부 이 사용자에게 일괄 귀속시킨다.
    """
    try:
        result = register_user_and_backfill(
            session, user_id=request.user_id, display_name=request.display_name
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return {
        "status": "success",
        "message": (
            f"'{result['user_id']}' 사용자로 등록되었습니다. "
            f"기존 데이터 {result['backfilled_signals']}건(신호), "
            f"{result['backfilled_generations']}건(생성이력)을 귀속했습니다."
        ),
        **result,
    }


@app.get("/users/me")
def get_current_user(user_id: str, session: Session = Depends(get_session)):
    """프론트가 localStorage에 저장해둔 user_id로 등록 여부/표시이름을 확인할 때 사용."""
    user = session.exec(select(User).where(User.user_id == user_id)).first()
    if not user:
        raise HTTPException(status_code=404, detail="등록되지 않은 사용자입니다.")
    return {
        "user_id": user.user_id,
        "display_name": user.display_name,
        "created_at_kst": to_kst(user.created_at),
    }


# ============================================
# 개인화 프로필 API (기존 + user_id 연결)
# ============================================

# ============================================
# 개인화 프로필 API (신규)
# ============================================

class ExplicitFeedbackRequest(BaseModel):
    article_id: int
    positive: bool  # True=👍, False=👎


@app.post("/personalization/feedback")
def submit_feedback(request: ExplicitFeedbackRequest, session: Session = Depends(get_session)):
    """
    기사 카드에 👍/👎 버튼을 추가하고 여기로 연결하세요.
    프론트에서는 ArticleCard.jsx 하단에 버튼 두 개만 추가하면 됩니다.
    """
    signal = store_explicit_feedback(session, article_id=request.article_id, positive=request.positive)
    if signal is None:
        raise HTTPException(status_code=404, detail="해당 기사를 찾을 수 없거나 분류할 수 없습니다.")
    return {"status": "success", "subcategory": signal.subcategory, "weight": signal.weight}


@app.get("/personalization/profile")
def get_personalization_profile(session: Session = Depends(get_session)):
    """
    현재까지 쌓인 개인화 프로필 전체를 반환한다 (시간 가중 감쇠 적용된 상태).
    설정 화면 등에 "당신의 관심사" 시각화로 바로 붙일 수 있는 형태.
    """
    return {"profile": get_profile(session)}


@app.get("/personalization/top-interests")
def get_personalization_top_interests(n: int = Query(5), session: Session = Depends(get_session)):
    """
    챗봇/보고서 생성 프롬프트에 주입할 상위 관심사.
    사용 예 (증권 브리핑 프롬프트 조립 시):

        top = get_top_interests(session, n=5)
        interest_text = ", ".join(f"{s}({round(d['score'],2)})" for s, d in top)
        system_prompt = f"사용자의 최근 관심 분야: {interest_text}. ..."
    """
    top = get_top_interests(session, n=n)
    return {"top_interests": [{"subcategory": s, **d} for s, d in top]}


# ============================================
# 배송 API (신규, 실험 단계 - ntfy/mailto, 무자격증명)
# ============================================

class DeliverRequest(BaseModel):
    generation_id: int
    channel: str              # "ntfy" | "email"
    target: str | None = None  # ntfy는 topic, email은 (선택) 수신 주소


@app.post("/deliver")
def deliver_generation(request: DeliverRequest, session: Session = Depends(get_session)):
    generation = session.get(TextGeneration, request.generation_id)
    if generation is None:
        raise HTTPException(status_code=404, detail="해당 생성 결과를 찾을 수 없습니다.")

    dlv = Delivery(
        generation_id=generation.id,
        channel=request.channel,
        target=request.target,
        status="pending",
    )

    response_extra: dict = {}

    if request.channel == "ntfy":
        if not request.target:
            raise HTTPException(status_code=400, detail="ntfy는 topic(target)이 필요합니다.")
        ok, err = delivery_channels.send_ntfy(
            topic=request.target,
            title="hf_crawler",
            message=generation.answer,
        )
        dlv.status = "sent" if ok else "failed"
        dlv.error_message = err
        if not ok:
            session.add(dlv)
            session.commit()
            raise HTTPException(status_code=502, detail=f"ntfy 발송 실패: {err}")

    elif request.channel == "email":
        mailto_url = delivery_channels.build_mailto_link(
            to_hint=request.target,
            subject="hf_crawler 보고서",
            body=generation.answer,
        )
        dlv.status = "sent"  # "사용자에게 전달할 준비 완료"라는 의미로 기록
        response_extra["mailto_url"] = mailto_url

    else:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 채널: {request.channel}")

    session.add(dlv)
    session.commit()

    # 취향 축적 (5단계) - 배송 클릭은 가장 강한 긍정 신호(weight=2.5)
    if generation.user_id:
        classify_and_store(
            session, text_title=generation.query, source="chat_delivered",
            signal_type="implicit", weight=2.5, user_id=generation.user_id,
        )

    return {"status": "success", "channel": request.channel, **response_extra}


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
    # 2026-08-09: 번역 버튼 양방향화 - 원문 언어를 감지해서 방향을 정한다.
    # 한글이 있으면 한국어 원문으로 보고 한→영, 없으면 기존처럼 영→한.
    source_lang = "ko" if tagging._contains_hangul(article.content or "") else "en"
    if source_lang == "ko":
        task = "translate_sentence_literal_ko_en" if mode == "literal" else "translate_sentence_natural_ko_en"
    else:
        task = "translate_sentence_literal" if mode == "literal" else "translate_sentence_natural"
    system_prompt = _build_sentence_translation_system_prompt(mode, source_lang=source_lang)
    segments = _segment_article_for_translation(article.content, source_lang=source_lang)

    parts: list[str] = []
    try:
        for seg in segments:
            if seg["type"] == "verbatim":
                parts.append(seg["text"] + "\n")
                continue

            priority.yield_to_person()
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
        "source_lang": source_lang,
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

    # 2026-08-09: 번역 버튼 양방향화 - 동기 엔드포인트(study_translate_article)와
    # 동일한 언어 감지 로직.
    source_lang = "ko" if tagging._contains_hangul(article.content or "") else "en"
    if source_lang == "ko":
        task = "translate_sentence_literal_ko_en" if mode == "literal" else "translate_sentence_natural_ko_en"
    else:
        task = "translate_sentence_literal" if mode == "literal" else "translate_sentence_natural"
    system_prompt = _build_sentence_translation_system_prompt(mode, source_lang=source_lang)
    segments = _segment_article_for_translation(article.content, source_lang=source_lang)
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
                        priority.yield_to_person()
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
    max_entries: int = 20,
    register: bool = True,
    major_category: str | None = None,
    search_query: str | None = None,
    region: str | None = None,
) -> dict:
    """
    검색창에 입력된 키워드 하나만 즉시 강제 수집한다 (due 체크 무시, 다른
    소스/키워드는 절대 건드리지 않는다).

    2026-08-09 검색 지역 강제: region이 명시되면 입력 언어와 무관하게 그
    지역 언어로 검색어를 강제 변환한다 (예: 영어 입력이어도 region="KR"이면
    한글로 번역해서 한국 사이트를 검색). region 없으면(자동) 입력 언어를
    그대로 따른다. Keyword.name(분류용)은 지역/검색언어와 무관하게 항상
    영어로 통일한다 (tagging.get_or_create_tag가 보장).
    """
    base_query_text = search_query or keyword_name
    resolved_query, language = _resolve_search_query_and_language(base_query_text, region)
    search_query = resolved_query

    canonical_name = (
        tagging._translate_to_english_tag_name(keyword_name)
        if tagging._contains_hangul(keyword_name) else keyword_name
    )

    keyword = session.exec(select(Keyword).where(Keyword.name == canonical_name)).first()
    auto_registered = False
    if not keyword:
        # 2026-08-10: major_category가 없으면(검색창에 직접 입력해서 등록하는
        # 가장 흔한 경로) tag_id가 계속 None으로 남아, 나중에 이 키워드가 소스로
        # 자동 승격될 때도 "미분류"로 남는 근본 원인이었다. 명시된 대분류가 없으면
        # 키워드 이름 자체를 대분류로 써서, 항상 최소한의 분류는 갖도록 보장한다
        # (다른 고정 대분류에 속한 태그가 이미 있으면 tagging.get_or_create_tag가
        # 그걸 재사용하므로 무의미하게 늘어나지 않는다).
        tag = tagging.get_or_create_tag(
            session, name=canonical_name, major_category=major_category or canonical_name
        )

        keyword = Keyword(
            name=canonical_name,
            tag_id=tag.id,
            search_query=search_query,
            language=language,
            months_back=months_back,
            interval_hours=interval_hours,
        )
        if register:
            session.add(keyword)
            session.commit()
            session.refresh(keyword)
            auto_registered = True
            logger.info(f"[collect] '{canonical_name}' 키워드 자동 등록 (언어: {language})")
        else:
            logger.info(f"[collect] '{canonical_name}' 일회성 수집 (영구 등록 안 함)")

    collector = COLLECTOR_REGISTRY["google_news_search"]
    if not job_control.start_job_with_priority(f"키워드 수집: {keyword.name}"):
        raise HTTPException(
            status_code=409,
            detail=f"다른 작업이 이미 진행 중입니다 (현재: {job_control.current_job()}). 잠시 후 다시 시도해주세요."
        )
    try:
        result = collector.collect_for_keyword(keyword, session, max_entries=max_entries)
        if register:
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

def _translate_text_llm(task: str, text: str) -> str:
    """범용 번역 헬퍼 - 실패하면 원문을 그대로 반환."""
    prompt = f"다음 문구를 번역해줘. 번역 결과만 출력하고 다른 설명은 붙이지 마: \"{text}\""
    try:
        raw = model_router.chat(task, [{"role": "user", "content": prompt}])
        # tagging._clean_llm_short_output()과 동일한 정리(목록기호/번호 제거) 적용
        return tagging._clean_llm_short_output(raw) or text
    except Exception as e:
        logger.warning(f"[collect] 번역 실패({task}), 원문 유지: {e}")
        return text


def _resolve_search_query_and_language(text: str, region: str | None) -> tuple[str, str]:
    """
    2026-08-09: 검색 지역(region)에 맞춰 검색어의 실제 언어를 강제한다.
    region이 None이면(자동) 입력 텍스트의 언어를 그대로 따른다. region="US"/"KR"이
    명시되면 입력 언어와 무관하게 그 지역 언어로 강제 번역한다 (나중에 JP/CN/ES/DE
    추가는 이 함수 분기만 늘리면 됨).
    """
    is_korean_input = tagging._contains_hangul(text)

    if region is None:
        return text, ("ko" if is_korean_input else "en")

    if region == "KR":
        if is_korean_input:
            return text, "ko"
        return _translate_text_llm("translate_keyword_ko", text), "ko"

    # region == "US" (그 외 미지원 값도 일단 영어권으로 폴백)
    if not is_korean_input:
        return text, "en"
    return _translate_text_llm("translate_keyword_en", text), "en"


# 3. 수집 실행 API - keyword 파라미터가 있으면 그 키워드만(없으면 자동 등록), 없으면 전체 소스/키워드 점검
@app.get("/collect/deep-incremental")
def collect_deep_incremental(
    keyword: str | None = Query(None),
    months_back: int = Query(1),
    interval_hours: float = Query(24.0),
    max_entries: int = Query(20),
    register: bool = Query(True),
    major_category: str | None = Query(None),
    search_query: str | None = Query(None),
    region: str | None = Query(None),
    session: Session = Depends(get_session),
):
    if keyword and keyword.strip():
        return _collect_single_keyword(
            keyword.strip(), session, months_back, interval_hours, max_entries, register,
            major_category=major_category, search_query=search_query, region=region,
        )

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


class GitHubCollectRequest(BaseModel):
    query: str
    max_entries: int = 10


@app.post("/collect/github")
def collect_github(request: GitHubCollectRequest, session: Session = Depends(get_session)):
    """
    GitHub Search API로 레포를 발굴/재수집한다. query 예시: "rag stars:>500",
    "language:python topic:llm" 등 GitHub 검색 문법 그대로 사용 가능.
    """
    count = github_repository.discover_and_collect(session, request.query, max_entries=request.max_entries)
    return {
        "status": "success",
        "message": f"'{request.query}' 검색으로 {count}건 처리했습니다.",
    }

from datetime import timedelta

@app.get("/github/repos")
def list_github_repos_table(session: Session = Depends(get_session)):
    """1단계 - 표(분야/오픈소스/스타수/응용분야/연관성/구성요소), 단어 위주."""
    repos = session.exec(select(GitHubRepo).order_by(GitHubRepo.full_name)).all()
    result = []
    for repo in repos:
        latest_snapshot = session.exec(
            select(GitHubRepoSnapshot).where(GitHubRepoSnapshot.repo_id == repo.id)
            .order_by(GitHubRepoSnapshot.snapshot_at.desc())
        ).first()
        result.append({
            "id": repo.id,
            "field": repo.field_short or repo.primary_language or "-",
            "full_name": repo.full_name,
            "stars": latest_snapshot.stars if latest_snapshot else 0,
            "application": repo.application_short or "-",
            "relevance": repo.relevance_short or "-",
            "components": repo.components_short or repo.primary_language or "-",
        })
    result.sort(key=lambda r: -r["stars"])
    return {"repos": result}


@app.get("/github/repos/{repo_id}/detail")
def get_github_repo_detail(repo_id: int, session: Session = Depends(get_session)):
    """2단계 - 상세. 필요하면 이 시점에 LLM 분석을 생성(지연 생성+캐시)."""
    repo = github_repository.get_or_generate_detail(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="해당 레포를 찾을 수 없습니다.")

    latest_snapshot = session.exec(
        select(GitHubRepoSnapshot).where(GitHubRepoSnapshot.repo_id == repo.id)
        .order_by(GitHubRepoSnapshot.snapshot_at.desc())
    ).first()

    return {
        "id": repo.id,
        "full_name": repo.full_name,
        "url": repo.url,
        "created_at_github": repo.created_at_github,
        "pushed_at_github": repo.pushed_at_github,
        "stars": latest_snapshot.stars if latest_snapshot else 0,
        "forks": latest_snapshot.forks if latest_snapshot else 0,
        "detailed_overview": repo.detailed_overview,
        "detailed_application": repo.detailed_application,
        "detailed_relations": repo.detailed_relations,
        "future_direction": repo.future_direction,
    }


@app.get("/github/repos/{repo_id}/readme")
def get_github_repo_readme(repo_id: int, session: Session = Depends(get_session)):
    """3단계 - 원본 README 그대로."""
    repo = session.get(GitHubRepo, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="해당 레포를 찾을 수 없습니다.")
    return {"id": repo.id, "full_name": repo.full_name, "readme_content": repo.readme_content or ""}



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
    if not job_control.start_job_with_priority(f"키워드 등록: {keyword.name}"):
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
    if not job_control.start_job_with_priority(f"키워드 재수집: {keyword.name}"):
        raise HTTPException(
            status_code=409,
            detail=f"다른 작업이 이미 진행 중입니다 (현재: {job_control.current_job()}). 잠시 후 다시 시도해주세요."
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

@app.put("/keywords/interval/bulk")
def update_all_keywords_interval(
    request: BulkIntervalUpdateRequest,
    session: Session = Depends(get_session),
):
    keywords = session.exec(select(Keyword)).all()

    count = 0
    for kw in keywords:
        kw.months_back = request.months_back
        kw.interval_hours = request.interval_hours
        session.add(kw)
        count += 1

    session.commit()

    return {
        "status": "success",
        "message": (
            f"등록된 키워드 {count}개에 "
            f"'최근 {request.months_back}개월 이내 / {request.interval_hours}시간마다'를 "
            f"일괄 적용했습니다."
        ),
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
        # 2026-08-10: 기사 삭제 시 ArticleTag(다중 태그 연결)도 함께 정리 -
        # delete_article()과 동일한 이유(유령 연결로 인한 건수/조회 불일치 방지).
        tag_links = session.exec(select(ArticleTag).where(ArticleTag.article_id == article.id)).all()
        for link in tag_links:
            session.delete(link)
        session.delete(article)

    # 2026-08-10 버그 수정: 이 키워드로 자동 승격된 출처(Source.keyword_id로
    # 연결)도 함께 삭제한다. 예전엔 키워드만 지워지고 승격된 출처는 그대로 남아,
    # 삭제한 키워드가 백그라운드 크롤링(스케줄러 틱)에 계속 포함되는 버그가 있었다.
    linked_sources = session.exec(select(Source).where(Source.keyword_id == keyword.id)).all()
    deleted_sources = len(linked_sources)
    for src in linked_sources:
        session.delete(src)

    session.delete(keyword)
    session.commit()
    return {
        "status": "success",
        "message": f"'{name}' 키워드와 수집된 기사 {deleted_count}건, 연결된 출처 {deleted_sources}건을 함께 삭제했습니다."
    }

# 3-1-5. 장르(대분류/중분류/소분류) 등록 - '장르 편집기' 버튼 전용.
@app.post("/genres")
def create_genre(request: GenreCreateRequest, session: Session = Depends(get_session)):
    major = request.major_category.strip()
    mid = request.mid_category.strip()
    sub = request.sub_category.strip()

    if not major or not mid or not sub:
        raise HTTPException(status_code=400, detail="대분류/중분류/소분류를 모두 입력해주세요.")

    # 2026-08-09: 분류는 이제 Tag가 담당. Keyword는 tag_id로 연결만 한다.
    tag = tagging.get_or_create_tag(session, name=sub, major_category=major, mid_category=mid)
    # 2026-08-09: sub가 한글이었다면 tag.name은 이미 영어로 번역된 상태다.
    # Keyword.name도 원문(sub)이 아니라 이 tag.name으로 통일해야, 태그는
    # 영어인데 키워드 이름만 한글로 남는 불일치가 안 생긴다. 검색 자체는
    # 여전히 한글 원문으로 해야 하므로, search_query가 명시 안 됐고 원문이
    # 한글이면 원문을 검색어로 폴백시킨다.
    was_korean = tagging._contains_hangul(sub)
    final_search_query = request.search_query or (sub if was_korean else None)

    existing = session.exec(select(Keyword).where(Keyword.name == tag.name)).first()
    if existing:
        existing.tag_id = tag.id
        session.add(existing)
        session.commit()
        return {
            "status": "success",
            "message": f"이미 있던 '{tag.name}' 키워드의 분류를 '{major} > {mid}'로 갱신했습니다."
        }

    keyword = Keyword(
        name=tag.name,
        tag_id=tag.id,
        search_query=final_search_query,
        language="ko" if was_korean else "en",
        months_back=request.months_back,
        interval_hours=request.interval_hours,
    )
    session.add(keyword)
    session.commit()
    return {
        "status": "success",
        "message": f"'{major} > {mid} > {sub}' 장르를 등록했습니다. 백그라운드 수집이 곧 시작됩니다."
    }


# 3-1-6. 장르 목록 조회 - '장르 편집기' 테이블용. 대분류/중분류/소분류 순으로 정렬.
@app.get("/genres")
def list_genres(session: Session = Depends(get_session)):
    keywords = session.exec(select(Keyword)).all()
    result = []

    # 2026-08-10: 건수 계산을 Article.keyword(검색 수집분만 채워짐) 대신
    # ArticleTag(수집 경로 불문하고 저장 시점에 항상 부여됨) 기준으로 전환.
    # RSS 고정 소스로 들어온 기사는 Article.keyword가 항상 비어있어서, 이전
    # 방식으로는 "Golf" 같은(출처관리에서 동기화된) 항목이 실제로 골프 기사가
    # 쌓여도 건수가 0으로 나오던 버그의 원인이었다. tag_id별로 한 번에 집계해서
    # 키워드마다 개별 쿼리를 반복하지 않는다.
    tag_counts = dict(
        session.exec(
            select(ArticleTag.tag_id, sql_func.count(ArticleTag.id)).group_by(ArticleTag.tag_id)
        ).all()
    )

    for k in keywords:
        if k.tag_id and k.tag_id in tag_counts:
            article_count = tag_counts[k.tag_id]
        else:
            # tag_id가 아직 없는 예외적인 경우를 위한 안전망(하위호환)
            article_count = len(session.exec(select(Article).where(Article.keyword == k.name)).all())

        tag = session.get(Tag, k.tag_id) if k.tag_id else None
        display_sub = (tag.label_ko if tag and tag.label_ko else k.name)
        result.append({
            "id": k.id,
            "major_category": (tag.major_category if tag and tag.major_category else "미분류"),
            "mid_category": (tag.mid_category or "-") if tag else "-",
            "sub_category": display_sub,
            "article_count": article_count,
            "interval_hours": k.interval_hours,
        })

    # 2026-08-10: "미분류"를 맨 위로 - 편집이 필요한 항목을 바로 찾을 수 있게.
    # (major_category != "미분류")가 False(0)/True(1)로 평가되어, 미분류가
    # 항상 먼저 정렬된다.
    # 2026-08-10: 문자열이 "미분류"든 "Uncategorized"든 상관없이, 대분류가
    # 비어있거나 미분류를 뜻하는 값이면 항상 맨 위로 오도록 명시적으로 판정한다.
    # (언더스코어 등으로 문자열 자체를 바꿔서 알파벳 순서에 맡기는 방식은 대문자
    # 알파벳(A-Z)보다 언더스코어의 문자 코드가 더 커서 오히려 안 먹힐 수 있음)
    _UNCATEGORIZED_VALUES = {"미분류", "uncategorized", "misc", ""}

    def _is_uncategorized(major: str) -> bool:
        return not major or major.strip().lower() in _UNCATEGORIZED_VALUES

    result.sort(key=lambda x: (not _is_uncategorized(x["major_category"]), x["major_category"], x["mid_category"], x["sub_category"]))
    return {"genres": result}
class GenreUpdateRequest(BaseModel):
    major_category: str
    mid_category: str
    sub_category: str


@app.patch("/genres/{keyword_id}")
def update_genre(keyword_id: int, request: GenreUpdateRequest, session: Session = Depends(get_session)):
    """
    장르편집기 테이블에서 대분류/중분류/소분류를 직접 수정할 때 사용.

    2026-08-10 버그 수정: "소분류는 그대로 두고 중분류만 고치는" 흔한 케이스에서,
    get_or_create_tag()의 "이름은 같은데 중분류가 다르면 별개 태그로 분리"
    로직이 "자기 자신을 수정하는 것"까지 새로운 충돌로 오인해서 엉뚱한 태그를
    새로 만들어버리는 버그가 있었다. 지금 이 키워드에 이미 연결된 태그가 바로
    그 태그 자신이면(=소분류 이름이 그대로), 충돌 검사를 거칠 필요 없이 그
    자리에서 바로 갱신한다. 소분류(이름) 자체가 바뀔 때만 새 이름 기준으로
    찾거나 만드는 기존 경로(get_or_create_tag)를 탄다.
    """
    keyword = session.get(Keyword, keyword_id)
    if not keyword:
        raise HTTPException(status_code=404, detail="해당 장르를 찾을 수 없습니다.")

    major = request.major_category.strip()
    mid = request.mid_category.strip()
    sub = request.sub_category.strip()
    if not major or not mid or not sub:
        raise HTTPException(status_code=400, detail="대분류/중분류/소분류를 모두 입력해주세요.")

    current_tag = session.get(Tag, keyword.tag_id) if keyword.tag_id else None
    current_display_name = (current_tag.label_ko if current_tag and current_tag.label_ko else keyword.name)

    if current_tag and sub == current_display_name:
        # 소분류(이름)는 그대로, 대분류/중분류만 수정하는 경우 - 지금 태그를 그대로 갱신
        if tagging._contains_hangul(major):
            major = tagging._translate_to_english_tag_name(major)
        if tagging._contains_hangul(mid):
            mid = tagging._translate_to_english_tag_name(mid)
        current_tag.major_category = major
        current_tag.mid_category = mid
        session.add(current_tag)
        session.commit()
        return {
            "status": "success",
            "message": f"'{major} > {mid} > {sub}'로 분류를 수정했습니다.",
        }

    # 소분류(이름) 자체가 바뀐 경우 - 새 이름 기준으로 태그를 찾거나 만든다.
    tag = tagging.get_or_create_tag(session, name=sub, major_category=major, mid_category=mid)
    new_display_name = tag.label_ko or tag.name

    if tag.name != keyword.name:
        clash = session.exec(
            select(Keyword).where(Keyword.name == tag.name, Keyword.id != keyword.id)
        ).first()
        if clash:
            raise HTTPException(
                status_code=400,
                detail=f"'{new_display_name}' 이름의 키워드가 이미 존재합니다."
            )
        keyword.name = tag.name

    keyword.tag_id = tag.id
    session.add(keyword)
    session.commit()

    return {
        "status": "success",
        "message": f"'{major} > {mid} > {sub}'로 분류를 수정했습니다.",
    }

class GenreSelectItem(BaseModel):
    major_category: str
    mid_category: str
    sub_category: str
    search_query: str | None = None


class GenreSelectRequest(BaseModel):
    items: list[GenreSelectItem]
    user_id: str | None = None


@app.post("/genres/select")
def select_preferred_genres(request: GenreSelectRequest, session: Session = Depends(get_session)):
    """
    '선호 장르 선택' 패널에서 체크한 항목들을 한 번에 등록 + 선호 신호로 기록한다.
    """
    registered, preferred = 0, 0
    for item in request.items:
        major = item.major_category.strip()
        mid = item.mid_category.strip()
        sub = item.sub_category.strip()
        if not major or not mid or not sub:
            continue

        tag = tagging.get_or_create_tag(session, name=sub, major_category=major, mid_category=mid)
        was_korean = tagging._contains_hangul(sub)
        final_search_query = item.search_query or (sub if was_korean else None)

        existing = session.exec(select(Keyword).where(Keyword.name == tag.name)).first()
        if existing:
            existing.tag_id = tag.id
            session.add(existing)
        else:
            keyword = Keyword(
                name=tag.name,
                tag_id=tag.id,
                search_query=final_search_query,
                language="ko" if was_korean else "en",
                months_back=1,
                interval_hours=24.0,
            )
            session.add(keyword)
        session.commit()
        registered += 1

        store_tag_preference(session, tag_id=tag.id, user_id=request.user_id)
        preferred += 1

    return {
        "status": "success",
        "registered": registered,
        "preferred": preferred,
        "message": f"{registered}개 장르를 등록하고 선호 신호로 기록했습니다.",
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

    # tag_id -> Tag 캐시 (소스마다 개별 쿼리 하지 않도록)
    tag_cache: dict[int, Tag] = {}

    def _tag_for(tag_id):
        if tag_id is None:
            return None
        if tag_id not in tag_cache:
            tag_cache[tag_id] = session.get(Tag, tag_id)
        return tag_cache[tag_id]

    result = []
    for s in sources:
        tag = _tag_for(s.tag_id)
        # '해당자료 URL' - 이 소스에서 가장 최근에 수집된 기사의 실제 원문 URL.
        # Source.url(=출처 URL)은 재수집에 쓰는 구글뉴스 검색/피드 URL이라
        # 사람이 클릭해서 보기엔 안 맞음 - 실제 기사 링크는 Article.url에 이미
        # 올바르게 저장돼 있다(collectors.py가 구글 리다이렉트를 미리 해제해둠).
        latest_article = session.exec(
            select(Article)
            .where(Article.source == s.name)
            .order_by(Article.collected_at.desc())
        ).first()
        result.append({
            "id": s.id,
            "name": s.name,
            "url": s.url,
            "article_url": latest_article.url if latest_article else None,
            "major_category": tag.major_category if tag else None,
            # 2026-08-09: category(문자열) 대신 tag_id로 조인한 major_category.
            "sensitive": tag.sensitive if tag else False,
            "source_type": s.source_type,
            "origin": s.origin,
            "status": s.status,
            "interval_hours": s.interval_hours,
            "fail_count": s.fail_count,
            "last_success_at": s.last_success_at.isoformat() if s.last_success_at else None,
            "last_attempt_at": s.last_attempt_at.isoformat() if s.last_attempt_at else None,
            "block_reason": s.block_reason,
        })
    return {"sources": result}

@app.get("/sources/evaluation")
def evaluate_sources(session: Session = Depends(get_session)):
    """
    '출처 평가' 버튼 전용 엔드포인트.
    2026-08-09: Source.category(문자열) 대신 tag_id로 조인한 major_category별로
    묶는다. 블록리스트(source_type="blocked")는 평가 대상에서 제외한다.
    """
    sources = session.exec(
        select(Source).where(Source.source_type != "blocked")
    ).all()

    categories: dict[str, list[dict]] = {}

    for s in sources:
        articles = session.exec(
            select(Article).where(Article.source == s.name)
        ).all()

        article_count = len(articles)
        lengths = [len(a.content) for a in articles if a.content]
        avg_length = mean(lengths) if lengths else 0.0

        # '해당자료 URL' - 이미 조회해둔 articles 중 가장 최근 것의 실제 URL.
        # (list_sources와 같은 이유 - Source.url은 재수집용 검색 URL이라
        # 클릭해서 보기엔 안 맞고, Article.url이 실제 기사 원문 링크다.)
        latest_article = (
            max(articles, key=lambda a: a.collected_at or datetime.min)
            if articles else None
        )

        score_result = source_scoring.compute_score(
            article_count=article_count,
            fail_count=s.fail_count,
            last_success_at=s.last_success_at,
            avg_content_length=avg_length,
        )

        tag = session.get(Tag, s.tag_id) if s.tag_id else None
        category = tag.major_category if tag else "미분류"
        categories.setdefault(category, []).append({
            "id": s.id,
            "name": s.name,
            "url": s.url,
            "article_url": latest_article.url if latest_article else None,
            "status": s.status,
            "article_count": article_count,
            "fail_count": s.fail_count,
            "last_success_at": s.last_success_at.isoformat() if s.last_success_at else None,
            "score": score_result["total"],
            "breakdown": score_result["breakdown"],
        })

    result = []
    for category, items in categories.items():
        ranked = sorted(items, key=lambda x: x["score"], reverse=True)
        for i, item in enumerate(ranked, start=1):
            item["rank"] = i
        result.append({
            "category": category,
            "source_count": len(ranked),
            "sources": ranked,
        })

    result.sort(key=lambda c: c["source_count"], reverse=True)

    return {"categories": result}


# 3-4. 소스 수동 등록 (사용자가 직접 발견한 출처 즉시 확정)
@app.post("/sources")
def create_source(request: SourceCreateRequest, session: Session = Depends(get_session)):
    existing = session.exec(select(Source).where(Source.url == request.url)).first()
    if existing:
        raise HTTPException(status_code=400, detail="이미 등록된 URL입니다.")

    # 2026-08-09: Source.category(문자열) 필드가 삭제되고 tag_id(FK)로 대체됐다.
    # 이 엔드포인트에서 이걸 놓쳐서 존재하지 않는 필드에 값을 넣으려다
    # TypeError가 나는 버그가 있었다.
    tag_id = None
    if request.category and request.category.strip():
        tag = tagging.get_or_create_tag(
            session, name=request.category.strip(), major_category=request.category.strip()
        )
        tag_id = tag.id

    source = Source(
        name=request.name,
        url=request.url,
        tag_id=tag_id,
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

    # 2026-08-10 버그 수정: Article만 지우고 ArticleTag(다중 태그 연결)를 안 지워서,
    # 기사는 삭제됐는데 "이 기사가 이 태그다"라는 연결 정보만 유령처럼 남는 문제가
    # 있었다. /stats/keywords는 이 유령 연결까지 세서 실제로는 0건인 항목이 "N건"
    # 으로 표시되고, 클릭하면 "데이터 없음"이 뜨는 원인이었다.
    tag_links = session.exec(select(ArticleTag).where(ArticleTag.article_id == article_id)).all()
    for link in tag_links:
        session.delete(link)

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
# CATEGORY_CONFIG의 키워드별 정규식을 프로세스 시작 시 딱 한 번만 컴파일해서 재사용한다.
# 예전엔 _score_categories_for_article()이 호출될 때마다(기사마다!) re.escape+정규식
# 컴파일을 새로 했는데, 기사가 수천 건으로 늘어나면서 /stats/keywords 한 번 호출에
# 이 컴파일이 "기사 수 x 카테고리 수 x 키워드 수"만큼 반복되어 몇 분씩 걸리는 상태였다.
# 이 무거운 계산이 GIL을 오래 쥐고 있으면서 다른 API 요청들까지 전부 지연되는
# 원인이었다 (2026-08-09, 출처관리/키워드현황 등 전체 버튼 지연 사태의 근본 원인).
_COMPILED_CATEGORY_PATTERNS: dict[str, list[tuple[str, re.Pattern]]] = {
    category: [
        (term, re.compile(r'(?:^|\b|[^\w])' + re.escape(term) + r'(?:$|\b|[^\w])', re.IGNORECASE))
        for term in config["keywords"]
    ]
    for category, config in CATEGORY_CONFIG.items()
}


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

        for term, compiled_pattern in _COMPILED_CATEGORY_PATTERNS[category]:
            title_matches = len(compiled_pattern.findall(title_lower))
            content_matches = len(compiled_pattern.findall(content_lower))
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
_keyword_stats_cache: dict = {"data": None, "computed_at": 0.0}
# 8초였던 걸 60초로 대폭 연장 (2026-08-09) - 기사가 3,600건+ 쌓인 지금은 이
# 계산 자체가 몇 초~심하면 100초 넘게 걸릴 수 있는데, 캐시 유효시간이 폴링
# 주기(10초)보다 짧으면 캐시가 사실상 무의미해서 계산이 끝나기도 전에 다음
# 폴링이 또 새 계산을 시작하는 악순환(점점 느려지는 요청이 계속 쌓임)에 빠졌었다.
_KEYWORD_STATS_CACHE_TTL = 60.0

@app.get("/stats/keywords")
def get_keyword_stats(session: Session = Depends(get_session)):
    """
    2026-08-10 전면 개편: 소분류를 평면으로 나열하지 않고, 중분류로 1차 그룹핑해서
    반환한다. 화면에서 중분류 버튼을 누르면 그 아래 소분류들이 펼쳐지는 2단계
    UI용 구조. Article과 조인해서, 삭제된 기사에 남아있는 유령 ArticleTag
    연결은 자동으로 집계에서 빠진다.
    """
    now = time.monotonic()
    if _keyword_stats_cache["data"] is not None and (now - _keyword_stats_cache["computed_at"]) < _KEYWORD_STATS_CACHE_TTL:
        return _keyword_stats_cache["data"]

    tag_counts = session.exec(
        select(Tag.id, Tag.name, Tag.label_ko, Tag.mid_category, sql_func.count(ArticleTag.id))
        .join(ArticleTag, ArticleTag.tag_id == Tag.id)
        .join(Article, Article.id == ArticleTag.article_id)
        .group_by(Tag.id)
    ).all()

    mid_groups: dict[str, dict] = {}
    seen_labels: set[str] = set()

    for tag_id, name, label_ko, mid_category, cnt in tag_counts:
        if not name or not cnt:
            continue
        clean = label_ko or name
        mid_key = mid_category or "미분류"
        group = mid_groups.setdefault(
            mid_key, {"mid_category": mid_key, "total_count": 0, "sub_categories": []}
        )
        group["sub_categories"].append({"label": clean, "tag_id": tag_id, "count": cnt})
        group["total_count"] += cnt
        seen_labels.add(clean)

    # 태그 연결이 없는(레거시) Article.keyword 기반 데이터는 "미분류" 아래로
    registered_counts = session.exec(
        select(Article.keyword, sql_func.count(Article.id))
        .where(Article.keyword.is_not(None))
        .group_by(Article.keyword)
    ).all()
    for kw_name, cnt in registered_counts:
        if kw_name and kw_name not in seen_labels:
            group = mid_groups.setdefault(
                "미분류", {"mid_category": "미분류", "total_count": 0, "sub_categories": []}
            )
            group["sub_categories"].append({"label": kw_name, "tag_id": None, "count": cnt})
            group["total_count"] += cnt

    result_list = sorted(mid_groups.values(), key=lambda g: (g["mid_category"] == "미분류", g["mid_category"]))
    for g in result_list:
        g["sub_categories"].sort(key=lambda s: -s["count"])

    result = {"mid_categories": result_list}
    _keyword_stats_cache["data"] = result
    _keyword_stats_cache["computed_at"] = now
    return result


# 10. 아티클 목록 조회 API
# 목록 조회 시 본문을 이 길이까지만 잘라서 보낸다. 기사가 많아지면서 전체 본문을
# 매번 통째로 내려주는 게 /articles 응답을 느리게 만드는 주요 원인이었음 (2026-08-09).
# 펼치기/편집 시에만 아래 GET /articles/{id}/full로 전체 본문을 따로 불러온다.
_ARTICLE_PREVIEW_LENGTH = 400


def _serialize_article_preview(a: Article) -> dict:
    content = a.content or ""
    truncated = len(content) > _ARTICLE_PREVIEW_LENGTH
    preview = content[:_ARTICLE_PREVIEW_LENGTH].rstrip() + "…" if truncated else content
    return {
        "id": a.id,
        "title": a.title,
        "url": a.url,
        "published_at": a.published_at,
        "content": preview,
        "content_truncated": truncated,
        "source": a.source,
    }


# 아티클 목록 조회 API (본문은 미리보기만 - 전체 본문은 /articles/{id}/full 참고)
@app.get("/articles")
def get_articles(
    keyword: str = Query(None),
    tag_id: int | None = Query(None),
    session: Session = Depends(get_session)
):
    # 2026-08-10: 키워드별 현황 버튼이 이제 태그 고유번호로 정확히 조회한다.
    # 텍스트 이름 매칭(아래 기존 분기)보다 항상 우선한다.
    if tag_id is not None:
        query = (
            select(Article)
            .join(ArticleTag, ArticleTag.article_id == Article.id)
            .where(ArticleTag.tag_id == tag_id)
            .order_by(Article.id.desc())
        )
        articles = session.exec(query).all()
        return {"articles": [_serialize_article_preview(a) for a in articles]}

    if not keyword:
        query = select(Article).order_by(Article.id.desc())
        articles = session.exec(query).all()
        return {"articles": [_serialize_article_preview(a) for a in articles]}

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
        matched_tag = session.exec(
            select(Tag).where(
                (sql_func.lower(Tag.name) == clean_kw_lower)
                | (sql_func.lower(Tag.major_category) == clean_kw_lower)
            )
        ).first()

        if matched_tag:
            query = (
                select(Article)
                .join(ArticleTag, ArticleTag.article_id == Article.id)
                .where(ArticleTag.tag_id == matched_tag.id)
                .order_by(Article.id.desc())
            )
            articles = session.exec(query).all()
        else:
            query = select(Article).where(
                (Article.title.contains(clean_kw)) | (Article.content.contains(clean_kw))
            ).order_by(Article.id.desc())
            articles = session.exec(query).all()

    return {"articles": [_serialize_article_preview(a) for a in articles]}


# 아티클 전체 본문 조회 (펼치기/편집 시에만 호출)
@app.get("/articles/{article_id}/full")
def get_article_full_content(article_id: int, session: Session = Depends(get_session)):
    article = session.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="해당 기사를 찾을 수 없습니다.")
    return {"id": article.id, "content": article.content}


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

# 백그라운드 크롤링 중단 요청 
@app.post("/scheduler/pause")
def pause_scheduler():
    job_control.pause_collection()
    # 2026-08-09: 일시정지는 "다음 틱부터 건너뛰기"만 하고, 지금 이미 진행
    # 중인 틱은 안 멈췄다 - "중지 눌렀는데 계속 도는 것 같다"는 증상의 원인.
    # 지금 도는 게 백그라운드 틱이면 취소 신호도 같이 보내서, 다음 소스/키워드로
    # 넘어가기 직전(안전한 지점)에 최대한 빨리 멈추게 한다.
    cancelled_job = None
    if job_control.current_job() == job_control.BACKGROUND_TICK_JOB_NAME:
        cancelled_job = job_control.cancel_current_job()

    message = "백그라운드 수집이 일시정지되었습니다."
    if cancelled_job:
        message += " 지금 진행 중이던 점검에도 중단 신호를 보냈습니다 (완전히 멈추기까지 수 초~수십 초 걸릴 수 있습니다)."
    return {"status": "success", "message": message}

@app.post("/scheduler/resume")
def resume_scheduler():
    job_control.resume_collection()
    # 2026-08-09: 재개 버튼이 실제로는 "다음 틱부터 건너뛰지 않기"만 할 뿐,
    # 즉시 수집을 시작시키진 않았다. 스케줄러 틱은 30분 간격이라, 재개를
    # 누른 시점이 주기 중간이면 최대 30분간 "가만히 있는 것처럼" 보이는
    # 문제가 있었다 - 재개 즉시 한 번 틱을 백그라운드로 돌려서 바로 체감되게 한다.
    threading.Thread(
        target=scheduler_module.run_tick, daemon=True, name="resume-immediate-tick"
    ).start()
    return {"status": "success", "message": "백그라운드 수집이 재개되었습니다. 즉시 1회 점검을 시작합니다."}

# 백그라운드 크롤링 일시정지 상태 조회
@app.get("/scheduler/status")
def get_scheduler_pause_status():
    return {"paused": job_control.is_paused()}