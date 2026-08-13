import React, { useState, useEffect, useRef } from 'react';

// 2026-08-13: 코딩분석 채팅. 다른 컨트롤 패널 컴포넌트들(PersonalRepository,
// GitHubRepos 등)과 동일하게 자기 버튼 + 자기 모달을 스스로 들고 있는
// 독립 컴포넌트다. user_id는 props로 안 받고 다른 컴포넌트들처럼
// localStorage('hf_user_id')에서 직접 읽는다.

// 2026-08-13: hf_coder(별도 서비스, 기본 :8100)를 직접 호출한다. hf_crawler와는
// 완전히 다른 프로세스라 별도 env 변수를 쓴다 - VITE_CODE_API_URL이 없으면
// 로컬 개발 기본값인 8100번 포트로 접속한다.
const API_URL = import.meta.env.VITE_CODE_API_URL || 'http://localhost:8100';

function CodeAnalysisChat() {
    const [open, setOpen] = useState(false);
    const [sessionId, setSessionId] = useState(null);
    const [fileTree, setFileTree] = useState([]);
    const [fileFilter, setFileFilter] = useState('');
    const [includedFiles, setIncludedFiles] = useState([]);
    const [messages, setMessages] = useState([]);
    const [input, setInput] = useState('');
    const [streaming, setStreaming] = useState(false);
    const [loadingTree, setLoadingTree] = useState(false);
    const [llmStatus, setLlmStatus] = useState(null);
    const [watching, setWatching] = useState(false);
    const [watcherBusy, setWatcherBusy] = useState(false);
    const [applyingId, setApplyingId] = useState(null);
    const [commitMessage, setCommitMessage] = useState('');
    const [commitBusy, setCommitBusy] = useState(false);
    const [commitResult, setCommitResult] = useState(null);
    const bottomRef = useRef(null);

    const refreshHistory = (sid) => {
        if (!sid) return;
        fetch(`${API_URL}/codeanalysis/history/${sid}`)
            .then(r => r.json())
            .then(data => setMessages(Array.isArray(data) ? data : []))
            .catch(() => {});
    };

    // 모달을 열 때마다: 세션 확보(새로고침해도 이어지도록 localStorage에 저장),
    // 파일트리 최신화(그새 VS Code에서 저장한 새 파일도 바로 잡히게), 이력 로드.
    useEffect(() => {
        if (!open) return;

        let sid = localStorage.getItem('hf_code_chat_session');
        if (!sid) {
            sid = crypto.randomUUID();
            localStorage.setItem('hf_code_chat_session', sid);
        }
        setSessionId(sid);

        setLoadingTree(true);
        fetch(`${API_URL}/codeanalysis/files`)
            .then(r => r.json())
            .then(data => setFileTree(data.files || []))
            .catch(() => setFileTree([]))
            .finally(() => setLoadingTree(false));

        fetch(`${API_URL}/codeanalysis/history/${sid}`)
            .then(r => r.json())
            .then(data => setMessages(Array.isArray(data) ? data : []))
            .catch(() => setMessages([]));

        fetch(`${API_URL}/watcher/status`)
            .then(r => r.json())
            .then(data => setWatching(!!data.watching))
            .catch(() => {});
    }, [open]);

    // 2026-08-13: VS Code에서 저장한 변경사항이 감시기를 통해 자동으로 채팅에
    // 추가되므로, 모달이 열려있는 동안 주기적으로 이력을 다시 불러온다.
    // 사람이 직접 타이핑 중인 스트리밍 응답을 덮어쓰지 않도록 스트리밍 중에는 건너뛴다.
    useEffect(() => {
        if (!open || !sessionId) return;
        const interval = setInterval(() => {
            if (!streaming) refreshHistory(sessionId);
        }, 6000);
        return () => clearInterval(interval);
    }, [open, sessionId, streaming]);

    useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages, streaming]);

    // 2026-08-13: LLM 상태 모니터링 - 모달이 열려있는 동안 3초마다 ollama.ps()
    // 기반 상태를 폴링해서 "지금 모델이 로드돼있는지 / 생성 중인지 / 언제
    // 자동 해제되는지"를 헤더에 실시간으로 보여준다.
    useEffect(() => {
        if (!open) return;

        const fetchStatus = () => {
            fetch(`${API_URL}/codeanalysis/llm-status`)
                .then(r => r.json())
                .then(setLlmStatus)
                .catch(() => setLlmStatus(null));
        };

        fetchStatus();
        const interval = setInterval(fetchStatus, 3000);
        return () => clearInterval(interval);
    }, [open]);

    const toggleFile = (path) => {
        setIncludedFiles(prev =>
            prev.includes(path) ? prev.filter(f => f !== path) : [...prev, path]
        );
    };

    const startNewSession = () => {
        const fresh = crypto.randomUUID();
        localStorage.setItem('hf_code_chat_session', fresh);
        setSessionId(fresh);
        setMessages([]);
        setIncludedFiles([]);
    };

    const sendMessage = async () => {
        if (!input.trim() || streaming || !sessionId) return;
        const userId = localStorage.getItem('hf_user_id') || null;
        const userText = input;

        setMessages(prev => [...prev, { role: 'user', content: userText }, { role: 'assistant', content: '' }]);
        setInput('');
        setStreaming(true);

        try {
            const res = await fetch(`${API_URL}/codeanalysis/chat/stream`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    session_id: sessionId,
                    message: userText,
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
                        const parsed = JSON.parse(payload);
                        if (parsed.error) {
                            setMessages(prev => {
                                const last = prev[prev.length - 1];
                                const updatedLast = { ...last, content: last.content + `\n\n⚠️ 오류: ${parsed.error}` };
                                return [...prev.slice(0, -1), updatedLast];
                            });
                        } else if (parsed.delta) {
                            setMessages(prev => {
                                const last = prev[prev.length - 1];
                                const updatedLast = { ...last, content: last.content + parsed.delta };
                                return [...prev.slice(0, -1), updatedLast];
                            });
                        }
                    } catch { /* 조각난 청크는 다음 루프에서 이어붙여지므로 무시 */ }
                }
            }
        } catch (err) {
            setMessages(prev => {
                const last = prev[prev.length - 1];
                const updatedLast = { ...last, content: last.content + `\n\n⚠️ 연결 오류: ${err.message}` };
                return [...prev.slice(0, -1), updatedLast];
            });
        } finally {
            setStreaming(false);
            refreshHistory(sessionId);
        }
    };

    const toggleWatcher = async () => {
        if (!sessionId || watcherBusy) return;
        setWatcherBusy(true);
        try {
            if (watching) {
                await fetch(`${API_URL}/watcher/stop`, { method: 'POST' });
                setWatching(false);
            } else {
                const userId = localStorage.getItem('hf_user_id') || null;
                const res = await fetch(`${API_URL}/watcher/start`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: sessionId, user_id: userId }),
                });
                if (!res.ok) {
                    const detail = await res.json().catch(() => ({}));
                    throw new Error(detail.detail || '감시 시작 실패');
                }
                setWatching(true);
            }
        } catch (err) {
            alert(`감시 상태 변경 실패: ${err.message}`);
        } finally {
            setWatcherBusy(false);
        }
    };

    const applyProposal = async (messageId) => {
        if (applyingId) return;
        setApplyingId(messageId);
        try {
            const res = await fetch(`${API_URL}/codeanalysis/apply-patch`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message_id: messageId }),
            });
            if (!res.ok) {
                const detail = await res.json().catch(() => ({}));
                throw new Error(detail.detail || '적용 실패');
            }
            setMessages(prev => prev.map(m => m.id === messageId ? { ...m, applied: true } : m));
        } catch (err) {
            alert(`제안 적용 실패: ${err.message}`);
        } finally {
            setApplyingId(null);
        }
    };

    const commitAndPush = async () => {
        if (!commitMessage.trim() || commitBusy) return;
        setCommitBusy(true);
        setCommitResult(null);
        try {
            const res = await fetch(`${API_URL}/codeanalysis/git/commit-push`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: commitMessage, push: true }),
            });
            const data = await res.json();
            setCommitResult(data);
            if (data.status === 'success') setCommitMessage('');
        } catch (err) {
            setCommitResult({ status: 'error', steps: [{ cmd: '(연결)', stderr: err.message }] });
        } finally {
            setCommitBusy(false);
        }
    };

    const visibleFiles = fileFilter.trim()
        ? fileTree.filter(f => f.toLowerCase().includes(fileFilter.trim().toLowerCase()))
        : fileTree;

    const renderLlmStatusLine = () => {
        if (!llmStatus) return <span className="code-llm-status code-llm-status-unknown">⚪ 상태 확인 중...</span>;

        if (llmStatus.is_generating) {
            return <span className="code-llm-status code-llm-status-active">🟢 생성 중</span>;
        }

        const codeModelEntry = (llmStatus.loaded_models || []).find(m => m.name === llmStatus.code_model);
        if (llmStatus.code_model_loaded && codeModelEntry) {
            let expiresLabel = '';
            if (codeModelEntry.expires_at) {
                const diffMs = new Date(codeModelEntry.expires_at).getTime() - Date.now();
                const diffMin = Math.max(0, Math.round(diffMs / 60000));
                expiresLabel = diffMin > 0 ? ` · ${diffMin}분 후 자동 해제` : ' · 곧 자동 해제';
            }
            const vramLabel = codeModelEntry.size_vram_gb ? ` · VRAM ${codeModelEntry.size_vram_gb}GB` : '';
            return (
                <span className="code-llm-status code-llm-status-idle-loaded">
                    🟡 대기 중 (메모리 로드됨{vramLabel}{expiresLabel})
                </span>
            );
        }

        return (
            <span className="code-llm-status code-llm-status-unloaded">
                ⚪ 언로드됨 (다음 질문 시 재로드, 콜드스타트 지연 있을 수 있음)
            </span>
        );
    };

    return (
        <>
            <button className="collect-btn code-analysis-btn" onClick={() => setOpen(true)}>
                💻 코딩분석
            </button>

            {open && (
                <div className="code-analysis-overlay" onClick={() => setOpen(false)}>
                    <div className="code-analysis-panel" onClick={(e) => e.stopPropagation()}>

                        <div className="code-analysis-header">
                            <h3>💻 코딩분석 <span className="code-analysis-model-badge">{llmStatus?.code_model || '모델 확인 중...'}</span></h3>
                            <div style={{ display: 'flex', gap: '8px' }}>
                                <button
                                    className={watching ? 'code-watcher-toggle code-watcher-on' : 'code-watcher-toggle'}
                                    onClick={toggleWatcher}
                                    disabled={watcherBusy}
                                    title="VS Code에서 저장할 때마다 자동으로 리뷰합니다"
                                >
                                    {watching ? '🔴 실시간 감시 중 (끄기)' : '⚪ 실시간 감시 켜기'}
                                </button>
                                <button onClick={startNewSession}>🆕 새 대화</button>
                                <button onClick={() => setOpen(false)}>✕ 닫기</button>
                            </div>
                        </div>
                        <div className="code-analysis-status-row">
                            {renderLlmStatusLine()}
                        </div>

                        <div className="code-analysis-body">
                            <div className="code-analysis-sidebar">
                                <div className="code-analysis-sidebar-title">
                                    📁 참고할 파일 ({includedFiles.length}개 선택됨)
                                </div>
                                <input
                                    type="text"
                                    className="code-analysis-file-filter"
                                    placeholder="파일명 검색..."
                                    value={fileFilter}
                                    onChange={(e) => setFileFilter(e.target.value)}
                                />
                                <div className="code-file-tree">
                                    {loadingTree && <div className="code-file-tree-empty">불러오는 중...</div>}
                                    {!loadingTree && visibleFiles.length === 0 && (
                                        <div className="code-file-tree-empty">
                                            파일을 찾을 수 없습니다.<br />
                                            CODE_PROJECT_ROOT 경로를 확인해주세요.
                                        </div>
                                    )}
                                    {visibleFiles.map(path => (
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
                                <div className="code-analysis-messages">
                                    {messages.length === 0 && (
                                        <div className="code-analysis-empty-hint">
                                            왼쪽에서 참고할 파일을 선택한 뒤 질문해보세요.<br />
                                            예: "main.py의 SQLite 락 방지 로직을 설명해줘"
                                        </div>
                                    )}
                                    {messages.map((m, i) => (
                                        <div key={m.id ?? i} className={`code-msg code-msg-${m.role}`}>
                                            <div className="code-msg-role">
                                                {m.role === 'user' ? '나' : (llmStatus?.code_model || 'LLM')}
                                                {m.source === 'watcher' && <span className="code-msg-watcher-badge">🔍 자동 리뷰</span>}
                                            </div>
                                            <pre>{m.content || (streaming && i === messages.length - 1 ? '생각 중...' : '')}</pre>
                                            {m.proposed_path && (
                                                <div className="code-proposal-box">
                                                    <div className="code-proposal-header">
                                                        📝 수정 제안: {m.proposed_path}
                                                    </div>
                                                    <details>
                                                        <summary>제안된 전체 내용 보기</summary>
                                                        <pre className="code-proposal-content">{m.proposed_content}</pre>
                                                    </details>
                                                    {m.applied ? (
                                                        <span className="code-proposal-applied">✅ 적용됨</span>
                                                    ) : (
                                                        <button
                                                            className="code-proposal-apply-btn"
                                                            onClick={() => applyProposal(m.id)}
                                                            disabled={applyingId === m.id}
                                                        >
                                                            {applyingId === m.id ? '적용 중...' : '이 제안 적용하기'}
                                                        </button>
                                                    )}
                                                </div>
                                            )}
                                        </div>
                                    ))}
                                    <div ref={bottomRef} />
                                </div>
                                <div className="code-analysis-input">
                                    <textarea
                                        value={input}
                                        onChange={(e) => setInput(e.target.value)}
                                        onKeyDown={(e) => {
                                            if (e.key === 'Enter' && !e.shiftKey) {
                                                e.preventDefault();
                                                sendMessage();
                                            }
                                        }}
                                        placeholder="코드에 대해 질문하세요 (Enter로 전송, Shift+Enter로 줄바꿈)"
                                        disabled={streaming}
                                    />
                                    <button
                                        className="collect-btn code-analysis-send-btn"
                                        onClick={sendMessage}
                                        disabled={streaming || !input.trim()}
                                    >
                                        {streaming ? '생성 중...' : '전송'}
                                    </button>
                                </div>

                                <div className="code-commit-row">
                                    <input
                                        type="text"
                                        className="code-commit-input"
                                        placeholder="커밋 메시지 (예: 코딩분석 제안 반영)"
                                        value={commitMessage}
                                        onChange={(e) => setCommitMessage(e.target.value)}
                                        disabled={commitBusy}
                                    />
                                    <button
                                        className="code-commit-btn"
                                        onClick={commitAndPush}
                                        disabled={commitBusy || !commitMessage.trim()}
                                    >
                                        {commitBusy ? '커밋 중...' : '📤 커밋 & 푸시'}
                                    </button>
                                </div>
                                {commitResult && (
                                    <div className={commitResult.status === 'success' ? 'code-commit-result ok' : 'code-commit-result error'}>
                                        {commitResult.status === 'success' ? '✅ 커밋/푸시 완료' : '⚠️ 실패'}
                                        <details>
                                            <summary>상세 로그</summary>
                                            <pre>{(commitResult.steps || []).map(s => `$ git ${s.cmd}\n${s.stdout || ''}${s.stderr || ''}`).join('\n\n')}</pre>
                                        </details>
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </>
    );
}

export default CodeAnalysisChat;
