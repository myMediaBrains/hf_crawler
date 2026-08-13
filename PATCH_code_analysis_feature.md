# PATCH_code_analysis_feature.md — 코딩분석 채팅 기능

> 작성일: 2026-08-13 (KST)
> 관련: `PROJECT_STATUS_09_personalization_and_typora.md` 이후 신규 기능
> 목표: 메인화면 "💻 코딩분석" 버튼 → 웹 클로드 방식 채팅창 → qwen2.5-coder:32b가
> hf_crawler + hf-frontend 코드베이스 전체를 참고해서 답변

핵심 전제: 백엔드/프론트엔드/Ollama/VS Code 전부 **같은 맥북 로컬**에서 돕니다.
"VS Code 연결"은 네트워크 연결이 아니라 **VS Code가 편집 중인 폴더 경로를 백엔드가
파일시스템으로 직접 읽는 것**입니다 — VS Code에서 저장하면 바로 최신 내용이 반영됩니다.

---

## 0. 왜 "전체 코드를 한 번에 다 넣기"가 아닌가

qwen2.5-coder:32b도 컨텍스트 한계(권장 32K 토큰 내외, M1 Max 32GB 메모리 고려 시
16K~32K가 안전)가 있어서, 프로젝트 전체 소스(수십 개 파일)를 매 질문마다 통째로 넣으면
곧바로 한계를 넘거나 응답이 느려집니다.

**해결 방식(웹 클로드 Projects와 동일한 UX)**: 파일트리는 항상 보여주고, 실제로
LLM에게 넣을 **파일 내용은 사용자가 체크박스로 선택**합니다. "이 질문엔 main.py랑
model_router.py만 보면 된다"처럼 필요한 만큼만 골라 넣는 방식이라 사람이 코드 리뷰
맡길 때와 똑같이 씁니다. 나중에 자동 검색(RAG)으로 확장할 수 있게 구조는 열어둡니다.

---

## 1. qwen2.5-coder:32b 다운로드

터미널에서:

```bash
ollama pull qwen2.5-coder:32b
```

- 기본 태그는 Q4_K_M 양자화로 약 19~20GB입니다. M1 Max 32GB 유니파이드 메모리에서
  로드는 되지만, **qwen3.5:35b-a3b-nvfp4(HEAVY 티어)와 동시에 메모리에 올라가면
  스와핑이 심해집니다.** 그래서 아래 model_router 설정에서 on-demand 로드 +
  적당한 keep_alive로 관리합니다 (동시 상주 방지).
- 다운로드 후 확인:

```bash
ollama list
```

`qwen2.5-coder:32b`가 보이면 완료. 간단 테스트:

```bash
ollama run qwen2.5-coder:32b "def fibonacci(n): 를 파이썬으로 완성해줘"
```

---

## 2. `model_router.py` — CODE 티어 추가

기존 `LIGHT`/`HEAVY` 2티어 구조에 `CODE` 티어를 하나 더 추가합니다. 별도 모델이므로
같은 티어에 끼워넣지 말고 새 티어로 분리해야 keep_alive/메모리 정책을 독립적으로
관리할 수 있습니다.

```python
class ModelTier(str, Enum):
    LIGHT = "light"
    HEAVY = "heavy"
    CODE = "code"      # 신규 — 코딩분석 전용


TIER_MODELS: dict[ModelTier, str] = {
    ModelTier.LIGHT: "qwen3.5:9b",
    ModelTier.HEAVY: "qwen3.5:35b-a3b-nvfp4",
    ModelTier.CODE: "qwen2.5-coder:32b",   # 신규
}

TIER_KEEP_ALIVE: dict[ModelTier, str | int] = {
    ModelTier.LIGHT: -1,
    ModelTier.HEAVY: "5m",
    ModelTier.CODE: "10m",   # 대화가 이어질 동안은 재로드 비용 없게, 끝나면 반납
}
```

`TASK_PROFILES`에 추가:

```python
"code_analysis": {
    # 코딩분석 채팅 전용. HEAVY와 동시 로드 시 메모리 압박이 크므로
    # 코딩분석 중엔 HEAVY(RAG 보고서 등) 요청을 백엔드에서 순차화하는 걸 권장.
    "tier": ModelTier.CODE,
    "options": {
        "temperature": 0.15,       # 코드 작업은 창의성보다 정확성이 우선
        "num_predict": 3072,
        "num_ctx": 16384,          # 메모리 여유 보고 32768까지 늘려도 됨(실측 후 조정)
        "presence_penalty": 0.1,
    },
},
```

> **주의**: `is_generating()` / `_active_lock` 로직은 그대로 재사용됩니다. 손댈 필요 없음.

---

## 3. DB 스키마 — `models.py`에 채팅 이력 테이블 추가

기존 `Translation`/`TextGeneration` 테이블의 `origin`/`model_used`/`created_at` 공통
패턴을 그대로 따릅니다.

```python
class CodeChatMessage(SQLModel, table=True):
    __tablename__ = "code_chat_messages"

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)          # 프론트에서 생성한 UUID, 새로고침해도 유지
    user_id: Optional[str] = Field(default=None, index=True)
    role: str                                     # "user" | "assistant"
    content: str
    included_files: Optional[str] = None           # JSON 배열 문자열, 이 메시지에 포함된 파일 경로들
    model_used: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

마이그레이션은 이 프로젝트 관례대로 `migrate_db.py`에 `PRAGMA table_info` 체크 +
`CREATE TABLE IF NOT EXISTS` 방식으로 추가하거나, 신규 테이블이므로
`SQLModel.metadata.create_all()`만으로도 충분합니다(새 테이블 생성은 `create_all()`이
처리해줍니다 — 기존 컬럼 추가만 안 될 뿐).

---

## 4. `main.py` — 코드베이스 파일트리 / 파일 읽기 API

프로젝트 루트 경로를 환경변수로 지정합니다(VS Code에서 열어둔 그 폴더와 동일 경로).

```python
import os
from pathlib import Path

CODE_PROJECT_ROOT = Path(
    os.getenv("CODE_PROJECT_ROOT", str(Path.home() / "Projects" / "hf_crawler"))
).resolve()

CODE_FILE_EXTENSIONS = {".py", ".jsx", ".js", ".ts", ".tsx", ".css", ".md", ".json", ".html"}
CODE_EXCLUDED_DIRS = {"node_modules", ".git", "__pycache__", "venv", ".venv", "dist", "build", ".vite"}


def _build_code_file_tree(root: Path) -> list[str]:
    paths = []
    for p in root.rglob("*"):
        if any(part in CODE_EXCLUDED_DIRS for part in p.parts):
            continue
        if p.is_file() and p.suffix in CODE_FILE_EXTENSIONS:
            paths.append(str(p.relative_to(root)))
    return sorted(paths)


def _resolve_code_path(rel_path: str) -> Path:
    """경로 탈출(../) 방지 — 반드시 CODE_PROJECT_ROOT 하위여야 함."""
    target = (CODE_PROJECT_ROOT / rel_path).resolve()
    if not str(target).startswith(str(CODE_PROJECT_ROOT)):
        raise HTTPException(status_code=400, detail="허용되지 않은 경로입니다.")
    return target


@app.get("/codeanalysis/files")
def list_code_files():
    """VS Code에서 편집 중인 프로젝트의 파일트리. 저장 즉시 최신 상태로 반영됨."""
    return {
        "root": str(CODE_PROJECT_ROOT),
        "files": _build_code_file_tree(CODE_PROJECT_ROOT),
    }


@app.get("/codeanalysis/file")
def read_code_file(path: str):
    target = _resolve_code_path(path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")
    try:
        content = target.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"파일 읽기 실패: {e}")
    return {"path": path, "content": content}
```

`CODE_PROJECT_ROOT`는 `.env`나 실행 시 환경변수로 지정:

```bash
export CODE_PROJECT_ROOT="/Users/본인계정/실제프로젝트경로/hf_crawler_root"
```

(hf_crawler와 hf-frontend가 같은 상위 폴더 밑에 있다면 그 상위 폴더를 루트로 지정하면
두 쪽 다 한 번에 파일트리에 잡힙니다.)

---

## 5. `main.py` — 코딩분석 SSE 채팅 엔드포인트

```python
from pydantic import BaseModel
import json as _json


class CodeChatRequest(BaseModel):
    session_id: str
    message: str
    included_files: list[str] = []
    user_id: Optional[str] = None


@app.post("/codeanalysis/chat/stream")
async def code_analysis_chat_stream(request: CodeChatRequest, session: Session = Depends(get_session)):

    # 1) 이전 대화 이력 로드 (같은 session_id)
    history_rows = session.exec(
        select(CodeChatMessage)
        .where(CodeChatMessage.session_id == request.session_id)
        .order_by(CodeChatMessage.created_at)
    ).all()

    # 2) 선택된 파일 내용 조립
    context_blocks = []
    for rel_path in request.included_files:
        try:
            target = _resolve_code_path(rel_path)
            content = target.read_text(encoding="utf-8", errors="ignore")
            context_blocks.append(f"### {rel_path}\n```\n{content}\n```")
        except Exception:
            continue  # 파일이 그새 삭제/이동됐으면 조용히 건너뜀

    tree_str = "\n".join(_build_code_file_tree(CODE_PROJECT_ROOT))

    system_prompt = (
        "You are an expert coding assistant analyzing a local project "
        "(FastAPI backend `hf_crawler` + React/Vite frontend `hf-frontend`).\n\n"
        f"Full project file tree (for reference; not all files are included below):\n{tree_str}\n\n"
        + ("Included file contents:\n\n" + "\n\n".join(context_blocks) if context_blocks
           else "No file contents were explicitly included for this question — "
                "answer from the file tree and conversation context, and ask the user "
                "to select relevant files via the sidebar if you need to see actual code.")
    )

    messages = [{"role": "system", "content": system_prompt}]
    for row in history_rows:
        messages.append({"role": row.role, "content": row.content})
    messages.append({"role": "user", "content": request.message})

    # 3) 사용자 메시지 즉시 저장
    session.add(CodeChatMessage(
        session_id=request.session_id,
        user_id=request.user_id,
        role="user",
        content=request.message,
        included_files=_json.dumps(request.included_files, ensure_ascii=False),
    ))
    session.commit()

    async def event_generator():
        full_text = ""
        try:
            async for chunk in model_router.achat_stream(task="code_analysis", messages=messages):
                delta = chunk.get("message", {}).get("content", "")
                if delta:
                    full_text += delta
                    yield f"data: {_json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {_json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
            return
        finally:
            if full_text:
                with Session(engine) as save_session:
                    save_session.add(CodeChatMessage(
                        session_id=request.session_id,
                        user_id=request.user_id,
                        role="assistant",
                        content=full_text,
                        model_used=model_router.model_for_task("code_analysis"),
                    ))
                    save_session.commit()
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/codeanalysis/history/{session_id}")
def get_code_chat_history(session_id: str, session: Session = Depends(get_session)):
    rows = session.exec(
        select(CodeChatMessage)
        .where(CodeChatMessage.session_id == session_id)
        .order_by(CodeChatMessage.created_at)
    ).all()
    return [{"role": r.role, "content": r.content, "created_at": r.created_at.isoformat()} for r in rows]
```

기존 프로젝트의 SSE 패턴(번역 스트리밍 등)과 동일하게 `data: ... \n\n` 형식을 그대로
따랐습니다. 프론트엔드도 기존 `EventSource`/`fetch` 스트림 파싱 로직을 재사용하면 됩니다.

---

## 6. 프론트엔드 — `CodeAnalysisChat.jsx` 신규

웹 클로드 방식: 왼쪽 파일트리(체크박스) + 오른쪽 채팅창.

```jsx
import { useState, useEffect, useRef } from 'react';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';

function CodeAnalysisChat({ userId, onClose }) {
  const [sessionId] = useState(() => {
    const saved = localStorage.getItem('hf_code_chat_session');
    if (saved) return saved;
    const fresh = crypto.randomUUID();
    localStorage.setItem('hf_code_chat_session', fresh);
    return fresh;
  });
  const [fileTree, setFileTree] = useState([]);
  const [includedFiles, setIncludedFiles] = useState([]);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const bottomRef = useRef(null);

  useEffect(() => {
    fetch(`${API_BASE}/codeanalysis/files`)
      .then(r => r.json())
      .then(data => setFileTree(data.files || []));

    fetch(`${API_BASE}/codeanalysis/history/${sessionId}`)
      .then(r => r.json())
      .then(setMessages);
  }, [sessionId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const toggleFile = (path) => {
    setIncludedFiles(prev =>
      prev.includes(path) ? prev.filter(f => f !== path) : [...prev, path]
    );
  };

  const sendMessage = async () => {
    if (!input.trim() || streaming) return;
    const userMsg = { role: 'user', content: input };
    setMessages(prev => [...prev, userMsg, { role: 'assistant', content: '' }]);
    setInput('');
    setStreaming(true);

    const res = await fetch(`${API_BASE}/codeanalysis/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId,
        message: userMsg.content,
        included_files: includedFiles,
        user_id: userId,
      }),
    });

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n\n');
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const payload = line.slice(6);
        if (payload === '[DONE]') continue;
        try {
          const { delta, error } = JSON.parse(payload);
          if (error) {
            setMessages(prev => {
              const copy = [...prev];
              copy[copy.length - 1].content += `\n\n⚠️ 오류: ${error}`;
              return copy;
            });
            continue;
          }
          if (delta) {
            setMessages(prev => {
              const copy = [...prev];
              copy[copy.length - 1].content += delta;
              return copy;
            });
          }
        } catch { /* skip malformed chunk */ }
      }
    }
    setStreaming(false);
  };

  return (
    <div className="code-analysis-modal">
      <div className="code-analysis-sidebar">
        <h4>📁 프로젝트 파일 ({includedFiles.length}개 선택됨)</h4>
        <div className="code-file-tree">
          {fileTree.map(path => (
            <label key={path} className="code-file-item">
              <input
                type="checkbox"
                checked={includedFiles.includes(path)}
                onChange={() => toggleFile(path)}
              />
              <span>{path}</span>
            </label>
          ))}
        </div>
      </div>

      <div className="code-analysis-chat">
        <div className="code-analysis-header">
          <h3>💻 코딩분석 (qwen2.5-coder:32b)</h3>
          <button onClick={onClose}>✕</button>
        </div>
        <div className="code-analysis-messages">
          {messages.map((m, i) => (
            <div key={i} className={`code-msg code-msg-${m.role}`}>
              <pre>{m.content}</pre>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
        <div className="code-analysis-input">
          <textarea
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); } }}
            placeholder="코드에 대해 질문하세요 (왼쪽에서 참고할 파일을 먼저 선택)"
            disabled={streaming}
          />
          <button onClick={sendMessage} disabled={streaming}>
            {streaming ? '생성 중...' : '전송'}
          </button>
        </div>
      </div>
    </div>
  );
}

export default CodeAnalysisChat;
```

`App.jsx`에 메인화면 버튼 + 모달 열기 상태만 추가하면 됩니다:

```jsx
const [showCodeAnalysis, setShowCodeAnalysis] = useState(false);

// 버튼 (기존 다른 메인 버튼들 옆에)
<button onClick={() => setShowCodeAnalysis(true)}>💻 코딩분석</button>

// 모달
{showCodeAnalysis && (
  <CodeAnalysisChat userId={currentUserId} onClose={() => setShowCodeAnalysis(false)} />
)}
```

`App.css`에는 기존 다른 모달들(GenreEditor 등)과 동일한 2단 레이아웃(고정 헤더 + 내부
스크롤) 패턴을 그대로 재사용하면 스타일 일관성이 유지됩니다.

---

## 7. 실행 순서 정리

1. `ollama pull qwen2.5-coder:32b`
2. `model_router.py`에 CODE 티어 + `code_analysis` 프로필 추가
3. `models.py`에 `CodeChatMessage` 테이블 추가, 서버 재시작(자동 `create_all()`)
4. `main.py`에 파일트리/파일읽기/채팅스트림/히스토리 4개 엔드포인트 추가
5. `export CODE_PROJECT_ROOT=...`로 실제 프로젝트 경로 지정 후 서버 재시작
6. `CodeAnalysisChat.jsx` 추가, `App.jsx`에 버튼+모달 연결
7. 테스트: 파일트리에서 `main.py` 체크 → "이 파일에서 SQLite 락 방지를 어떻게 하고
   있어?" 같은 질문으로 확인

---

## 8. (선택, 나중 단계) 진짜 VS Code 확장까지 원할 때

지금 단계는 "VS Code가 저장한 파일을 백엔드가 읽는" 방식이라 확장 설치가 필요 없습니다.
나중에 **에디터 안에서 커서 위치/선택 영역까지 실시간으로 인식**하게 하고 싶으면,
직접 확장을 만드는 대신 오픈소스 **Continue** 확장을 로컬 Ollama에 붙이는 게 훨씬
가볍습니다:

1. VS Code 확장 마켓에서 "Continue" 설치
2. `~/.continue/config.json`에서 provider를 `ollama`, model을 `qwen2.5-coder:32b`로 지정
3. 에디터 안에서 바로 인라인 채팅/자동완성 사용 가능 (같은 로컬 Ollama 인스턴스 공유,
   메모리 동시 로드 주의 — 코딩분석 채팅창과 동시에 크게 쓰면 위 keep_alive 정책과
   충돌할 수 있으니 필요할 때 우선순위 조율)

이건 지금 만드는 "코딩분석 대화창"과는 별개 도구로, 둘 다 같은 로컬 qwen2.5-coder:32b를
가리키기만 하면 됩니다. 지금 단계에서는 필요 없으니 원하실 때 별도로 진행하시면 됩니다.
