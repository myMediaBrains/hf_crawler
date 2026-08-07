# main.py에 model_router 연결하기

`model_router.py`를 main.py와 같은 디렉터리(예: `hf_crawler/`)에 두고,
아래 4곳만 수정하면 됩니다.

---

## 1. import 추가

```python
import model_router
```

기존의 `import ollama`, `OLLAMA_MODEL = os.getenv(...)` 줄은 그대로 둬도 되지만,
번역 엔드포인트에서는 더 이상 직접 쓰지 않습니다 (라우터가 대신 관리).

---

## 2. lifespan에 워밍업 추가

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    logger.info("📊 데이터베이스 테이블이 준비되었습니다.")

    # 경량 모델(9b)을 미리 메모리에 올려 첫 요청 지연 제거
    await model_router.warmup(model_router.ModelTier.LIGHT)

    scheduler.start()
    logger.info("🚀 APScheduler 백그라운드 수집기가 시작되었습니다.")
    yield
    scheduler.shutdown()
    logger.info("🛑 APScheduler가 안전하게 종료되었습니다.")
```

---

## 3. 동기 번역 엔드포인트 교체

기존 `study_translate_article` 안의 `system_prompt` 구성과 `ollama.chat(...)` 호출부를
아래처럼 바꿉니다. **`/no_think` 텍스트는 이제 필요 없습니다** (think=False가 라우터에서 명시되므로).

```python
@app.post("/articles/{article_id}/study-translate")
def study_translate_article(
    article_id: int,
    request: StudyTranslateRequest,
    session: Session = Depends(get_session)
):
    article = session.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="해당 기사를 찾을 수 없습니다.")

    mode_instruction = (
        "Provide a strict, literal translation (직역) into Korean, preserving English word order as much as possible."
        if request.mode == "literal"
        else "Provide a natural, fluent Korean translation (의역)."
    )

    system_prompt = (
        "You are an expert English-Korean bilingual assistant. "
        "Your task is to translate the article paragraph by paragraph for side-by-side reading.\n\n"
        "### CRITICAL FORMATTING RULES:\n"
        "1. Process the original text paragraph by paragraph.\n"
        "2. For EVERY paragraph, output the [Original English Paragraph] first, followed IMMEDIATELY by its [Korean Translation].\n"
        "3. Add a line break and '---' divider between each (English - Korean) paragraph pair.\n"
        "4. Preserve markdown formatting (such as ### headers or bullet lists) in both English and Korean.\n"
        f"5. Translation Style: {mode_instruction}"
    )

    task = "translate_literal" if request.mode == "literal" else "translate_natural"

    try:
        translated_content = model_router.chat(
            task=task,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': article.content}
            ],
        )
    except Exception as e:
        logger.error(f"Ollama 번역 오류: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Ollama 번역 처리 중 오류 발생: {str(e)}")

    return {
        "status": "success",
        "article_id": article_id,
        "mode": request.mode,
        "translated_content": translated_content
    }
```

---

## 4. SSE 스트리밍 엔드포인트 교체

`get_ollama_stream()` 내부 호출과 `stream = await loop.run_in_executor(...)` 부분을 걷어내고,
`model_router.achat_stream()`으로 교체합니다. 청크 구조(`chunk['message']['content']`)는 동일하므로
그 아래 로직(`chunk_buffer`, SSE payload 생성 등)은 손댈 필요 없습니다.

```python
@app.get("/articles/{article_id}/study-translate-stream")
async def study_translate_article_stream(
    article_id: int,
    mode: str = Query("literal"),
    session: Session = Depends(get_session)
):
    article = session.get(Article, article_id)
    if not article:
        raise HTTPException(status_code=404, detail="해당 기사를 찾을 수 없습니다.")

    mode_instruction = (
        "Provide a strict, literal translation (직역) into Korean, preserving English word order as much as possible."
        if mode == "literal"
        else "Provide a natural, fluent Korean translation (의역)."
    )

    system_prompt = (
        "You are an expert English-Korean bilingual assistant. "
        "Your task is to translate the article paragraph by paragraph for side-by-side reading.\n\n"
        "### CRITICAL FORMATTING RULES:\n"
        "1. Process the original text paragraph by paragraph.\n"
        "2. For EVERY paragraph, output the [Original English Paragraph] first, followed IMMEDIATELY by its [Korean Translation].\n"
        "3. Add a line break and '---' divider between each (English - Korean) paragraph pair.\n"
        "4. Preserve markdown formatting (such as ### headers or bullet lists) in both English and Korean.\n"
        f"5. Translation Style: {mode_instruction}"
    )

    task = "translate_literal" if mode == "literal" else "translate_natural"
    estimated_total_chars = max(len(article.content) * 2, 100)

    async def event_generator() -> AsyncGenerator[str, None]:
        full_translated_text = []
        accumulated_chars = 0
        chunk_buffer = ""
        chunk_threshold = 50

        initial_payload = json.dumps({"status": "processing", "progress": 0, "chunk": ""}, ensure_ascii=False)
        yield f"data: {initial_payload}\n\n"

        try:
            async for chunk in model_router.achat_stream(
                task=task,
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': article.content}
                ],
            ):
                content_chunk = chunk['message']['content']
                if content_chunk:
                    chunk_buffer += content_chunk
                    full_translated_text.append(content_chunk)
                    accumulated_chars += len(content_chunk)

                    if len(chunk_buffer) >= chunk_threshold:
                        calc_percent = min(99, max(1, int((accumulated_chars / estimated_total_chars) * 100)))
                        payload = json.dumps({
                            "status": "processing",
                            "progress": calc_percent,
                            "chunk": chunk_buffer
                        }, ensure_ascii=False)
                        yield f"data: {payload}\n\n"
                        chunk_buffer = ""
                        await asyncio.sleep(0.01)

            if chunk_buffer:
                payload = json.dumps({
                    "status": "processing",
                    "progress": 99,
                    "chunk": chunk_buffer
                }, ensure_ascii=False)
                yield f"data: {payload}\n\n"

            final_content = "".join(full_translated_text).strip()
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
```

---

## 확인 방법

1. 서버 기동 후 로그에 `[model_router] warmup 완료: qwen3.5:9b (keep_alive=-1)`가 찍히는지 확인
2. 번역 API 호출 시 로그에 `[model_router] task=translate_literal model=qwen3.5:9b`가 찍히는지 확인
3. 응답 속도가 이전(수십 초~1분대)보다 확연히 빨라졌는지 확인 (thinking 제거 효과)
4. `ollama ps`를 API 호출 직후 실행해서 `qwen3.5:9b`가 `100% GPU`로 상주 중인지 확인

---

## 아직 안 만든 것 (다음 단계)

- `rag_report` task를 실제로 호출하는 `/reports/generate` 엔드포인트 — RAG 파이프라인(벡터DB 연동)이 먼저 필요하므로 이번 단계에서는 라우터에 프로필만 등록해두고 실제 엔드포인트는 보류
- 카테고리 분류(`classify` task)를 지금의 정규식 기반 `get_keyword_stats`에 선택적으로 결합할지는 별도 논의 필요
