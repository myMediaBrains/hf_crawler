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
    const [vectorAvailable, setVectorAvailable] = useState(false);
    const [vectorIndexBusy, setVectorIndexBusy] = useState(false);
    const [vectorIndexResult, setVectorIndexResult] = useState(null);
    const [architectMode, setArchitectMode] = useState(false);
    const [agentMode, setAgentMode] = useState(false);
    const [fastMode, setFastMode] = useState(false);
    // 2026-08-14(개정): 예전엔 agentModel이 '30b'/'14b'/'glimmer' 3개 고정값이었는데,
    // 이제 백엔드(GET /codeanalysis/models)가 맥북에 실제로 설치된 ollama 모델을
    // 전부 알려주므로 그 목록을 그대로 라디오로 그린다. agentModel은 이제 ollama list의
    // 실제 모델명 문자열('' = 미선택 = 백엔드 기본값 사용)을 그대로 담는다.
    const [agentModel, setAgentModel] = useState(''); // '' = 자동(백엔드 기본값), 그 외엔 ollama 모델명 그대로
    const [availableModels, setAvailableModels] = useState([]); // GET /codeanalysis/models 결과
    const [modelsLoading, setModelsLoading] = useState(false);
    // 2026-08-14(신규): 켜면 스트리밍 출력을 로컬 Typora 파일로도 미러링한다
    // (typora_sync.py). typoraPath는 "🖥 Typora에서 열기" 버튼/안내 문구용.
    const [syncToTypora, setSyncToTypora] = useState(false);
    const [typoraPath, setTyporaPath] = useState('');
    const [typoraBusy, setTyporaBusy] = useState(false);
    const [watcherBusy, setWatcherBusy] = useState(false);
    const [applyingId, setApplyingId] = useState(null);
    const [commitMessage, setCommitMessage] = useState('');
    const [commitBusy, setCommitBusy] = useState(false);
    const [commitResult, setCommitResult] = useState(null);
    const [undoConfirm, setUndoConfirm] = useState(null);
    const [undoBusy, setUndoBusy] = useState(false);
    const [undoResult, setUndoResult] = useState(null);
    const [gitStatus, setGitStatus] = useState(null); // null=아직 안 불러옴, []=변경사항 없음, [...]=변경된 파일들
    const [checkedGitPaths, setCheckedGitPaths] = useState(new Set());
    const bottomRef = useRef(null);

    const refreshHistory = (sid) => {
        if (!sid) return;
        fetch(`${API_URL}/codeanalysis/history/${sid}`)
            .then(r => r.json())
            .then(data => setMessages(Array.isArray(data) ? data : []))
            .catch(() => {});
    };

    // 2026-08-13: 파일트리 새로고침 - 모달 열 때뿐 아니라 되돌리기/커밋 이후에도
    // 호출한다. git 작업으로 파일이 삭제/생성될 수 있는데, 그때마다 사이드바가
    // 저절로 갱신 안 되면 실제로는 지워진 파일이 체크된 채로 화면에 남아있는
    // 것처럼 보이는 문제가 있었다 - 사라진 파일은 선택 목록에서도 같이 뺀다.
    // 2026-08-14(신규): ollama list에 실제로 설치된 모델 전체를 불러온다 - 에이전트
    // 모드 라디오를 이 결과로 그린다. 실패해도 조용히 빈 배열로 - 에이전트 모드
    // 자체는 여전히 동작해야 하고(백엔드 기본값으로), 목록만 못 보여주면 된다.
    const refreshAvailableModels = () => {
        setModelsLoading(true);
        fetch(`${API_URL}/codeanalysis/models`)
            .then(r => r.json())
            .then(data => setAvailableModels(data.models || []))
            .catch(() => setAvailableModels([]))
            .finally(() => setModelsLoading(false));
    };

    // 2026-08-14(신규): Typora 미러링 파일 경로 조회 - 안내 문구에 표시.
    const refreshTyporaStatus = () => {
        fetch(`${API_URL}/codeanalysis/typora/status`)
            .then(r => r.json())
            .then(data => setTyporaPath(data.path || ''))
            .catch(() => {});
    };

    const openInTypora = async () => {
        if (typoraBusy) return;
        setTyporaBusy(true);
        try {
            const res = await fetch(`${API_URL}/codeanalysis/typora/open`, { method: 'POST' });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'Typora를 열지 못했습니다.');
            setTyporaPath(data.path || typoraPath);
        } catch (err) {
            alert(`Typora 열기 실패: ${err.message}`);
        } finally {
            setTyporaBusy(false);
        }
    };

    const refreshFileTree = () => {
        setLoadingTree(true);
        fetch(`${API_URL}/codeanalysis/files`)
            .then(r => r.json())
            .then(data => {
                const files = data.files || [];
                setFileTree(files);
                const stillExists = new Set(files);
                setIncludedFiles(prev => prev.filter(p => stillExists.has(p)));
            })
            .catch(() => setFileTree([]))
            .finally(() => setLoadingTree(false));
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

        refreshFileTree();
        refreshAvailableModels();
        refreshTyporaStatus();

        fetch(`${API_URL}/codeanalysis/history/${sid}`)
            .then(r => r.json())
            .then(data => setMessages(Array.isArray(data) ? data : []))
            .catch(() => setMessages([]));

        fetch(`${API_URL}/watcher/status`)
            .then(r => r.json())
            .then(data => setWatching(!!data.watching))
            .catch(() => {});

        loadGitStatus();

        fetch(`${API_URL}/codeanalysis/vector-status`)
            .then(r => r.json())
            .then(data => setVectorAvailable(!!data.available))
            .catch(() => setVectorAvailable(false));
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
            const endpoint = agentMode ? '/codeanalysis/agent/stream' : '/codeanalysis/chat/stream';
            // 2026-08-14(개정): 에이전트 모드는 이제 agentModel에 ollama list의 실제
            // 모델명 문자열이 그대로 담겨있으므로, 그걸 model 필드로 넘긴다.
            // agentModel이 ''(미선택)이면 model 자체를 아예 안 보내서 백엔드
            // 기본값(agent_loop, 30b)을 쓰게 한다.
            const body = agentMode
                ? {
                    session_id: sessionId, message: userText, user_id: userId,
                    ...(agentModel ? { model: agentModel } : {}),
                    sync_to_typora: syncToTypora,
                }
                : {
                    session_id: sessionId,
                    message: userText,
                    included_files: includedFiles,
                    user_id: userId,
                    architect: architectMode,
                    fast_mode: fastMode,
                    sync_to_typora: syncToTypora,
                };

            const res = await fetch(`${API_URL}${endpoint}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
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
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || '적용 실패');
            const anyApplied = (data.results || []).some(r => r.status === 'applied');
            setMessages(prev => prev.map(m => m.id === messageId
                ? { ...m, applied: anyApplied, apply_results: data.results }
                : m));
        } catch (err) {
            alert(`제안 적용 실패: ${err.message}`);
        } finally {
            setApplyingId(null);
        }
    };

    const loadGitStatus = async () => {
        try {
            const res = await fetch(`${API_URL}/codeanalysis/git/status`);
            const data = await res.json();
            if (res.ok) {
                setGitStatus(data.files || []);
                // 2026-08-13(개정): 기본을 "전부 체크"에서 "전부 해제"로 변경.
                // "전부 체크"가 기본이었을 때, 무관한 파일(hf-frontend 소스 등)이
                // test.py 같은 의도한 파일과 함께 커밋되는 사고가 실제로 반복됐다 -
                // 매번 사람이 직접 골라야만 커밋되게 해서 원천 차단한다.
                setCheckedGitPaths(new Set());
            }
        } catch { /* 조용히 무시 - 커밋 버튼 누를 때 다시 시도됨 */ }
    };

    const refreshVectorIndex = async () => {
        setVectorIndexBusy(true);
        setVectorIndexResult(null);
        try {
            const res = await fetch(`${API_URL}/codeanalysis/vector-index`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ force: false }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || '인덱스 빌드 실패');
            setVectorIndexResult(data);
        } catch (err) {
            setVectorIndexResult({ error: err.message });
        } finally {
            setVectorIndexBusy(false);
        }
    };

    const toggleGitFile = (path) => {
        setCheckedGitPaths(prev => {
            const next = new Set(prev);
            if (next.has(path)) next.delete(path); else next.add(path);
            return next;
        });
    };

    const commitAndPush = async () => {
        if (!commitMessage.trim() || commitBusy) return;
        if (gitStatus && gitStatus.length > 0 && checkedGitPaths.size === 0) {
            alert('커밋할 파일을 하나 이상 선택해주세요.');
            return;
        }
        setCommitBusy(true);
        setCommitResult(null);
        try {
            const paths = (gitStatus && gitStatus.length > 0) ? Array.from(checkedGitPaths) : undefined;
            const res = await fetch(`${API_URL}/codeanalysis/git/commit-push`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: commitMessage, push: true, ...(paths ? { paths } : {}) }),
            });
            const data = await res.json();
            setCommitResult(data);
            if (data.status === 'success') {
                setCommitMessage('');
                loadGitStatus(); // 커밋 후 남은 변경사항만 다시 보이게 갱신
                refreshFileTree();
                // 2026-08-13: 푸시 성공 시, 로컬 git 결과만 믿지 않고 실제로
                // GitHub 원격에 반영됐는지 최신 커밋을 다시 조회해서 확인한다.
                try {
                    const myRepoRes = await fetch(`${API_URL}/github/my-repo`);
                    const myRepo = await myRepoRes.json();
                    if (myRepoRes.ok) {
                        const branch = myRepo.local_branch || myRepo.default_branch;
                        const commitsRes = await fetch(
                            `${API_URL}/github/commits?owner=${myRepo.owner}&repo=${myRepo.repo}&branch=${branch}&limit=1`
                        );
                        const commitsData = await commitsRes.json();
                        if (commitsRes.ok && commitsData.commits?.[0]) {
                            setCommitResult(prev => ({ ...prev, githubCheck: commitsData.commits[0] }));
                        }
                    }
                } catch { /* 확인 실패해도 커밋/푸시 자체는 성공했으니 조용히 무시 */ }
            }
        } catch (err) {
            setCommitResult({ status: 'error', steps: [{ cmd: '(연결)', stderr: err.message }] });
        } finally {
            setCommitBusy(false);
        }
    };

    const requestUndoConfirm = async () => {
        setUndoResult(null);
        try {
            const res = await fetch(`${API_URL}/codeanalysis/git/last-commit`);
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || '마지막 커밋 정보를 가져오지 못했습니다.');
            setUndoConfirm(data);
        } catch (err) {
            alert(`되돌리기 확인 실패: ${err.message}`);
        }
    };

    const confirmUndo = async () => {
        setUndoBusy(true);
        try {
            const res = await fetch(`${API_URL}/codeanalysis/git/undo`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ push: true }),
            });
            const data = await res.json();
            setUndoResult(data);
            if (data.status === 'success') {
                setUndoConfirm(null);
                refreshFileTree(); // 되돌리기로 파일이 삭제/복원됐을 수 있으니 사이드바 갱신
                loadGitStatus();
            }
        } catch (err) {
            setUndoResult({ status: 'error', steps: [{ cmd: '(연결)', stderr: err.message }] });
        } finally {
            setUndoBusy(false);
        }
    };

    const cancelUndo = () => setUndoConfirm(null);

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
            <button type="button" className="collect-btn code-analysis-btn" onClick={() => setOpen(true)}>
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
                                 type="button">
                                    {watching ? '🔴 실시간 감시 중 (끄기)' : '⚪ 실시간 감시 켜기'}
                                </button>
                                <button onClick={startNewSession} type="button">🆕 새 대화</button>
                                <button type="button" onClick={() => setOpen(false)}>✕ 닫기</button>
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
                                {vectorAvailable && (
                                    <div className="code-vector-index-row">
                                        <button type="button" onClick={refreshVectorIndex} disabled={vectorIndexBusy}>
                                            {vectorIndexBusy ? '인덱싱 중...' : '🧠 의미검색 인덱스 새로고침'}
                                        </button>
                                        {vectorIndexResult && (
                                            <span className="code-vector-index-result">
                                                {vectorIndexResult.error
                                                    ? `⚠️ ${vectorIndexResult.error}`
                                                    : `✅ ${vectorIndexResult.indexed}개 갱신 · ${vectorIndexResult.skipped}개 변경없음`}
                                            </span>
                                        )}
                                    </div>
                                )}
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
                                            {m.proposed_edits && m.proposed_edits.length > 0 && (
                                                <div className="code-proposal-box">
                                                    <div className="code-proposal-header">
                                                        📝 수정 제안 ({m.proposed_edits.length}개 파일/블록)
                                                    </div>
                                                    {m.proposed_edits.map((edit, ei) => {
                                                        const result = m.apply_results?.find(r => r.path === edit.path && r.search === edit.search);
                                                        return (
                                                            <div key={ei} className="code-edit-block">
                                                                <div className="code-edit-path">{edit.path}</div>
                                                                <details>
                                                                    <summary>변경 내용 보기</summary>
                                                                    <div className="code-edit-diff">
                                                                        <pre className="code-edit-search">- {edit.search}</pre>
                                                                        <pre className="code-edit-replace">+ {edit.replace}</pre>
                                                                    </div>
                                                                </details>
                                                                {result && (
                                                                    <div className={result.status === 'applied' ? 'code-edit-result ok' : 'code-edit-result error'}>
                                                                        {result.status === 'applied' ? '✅ 적용됨' : `⚠️ ${result.detail}`}
                                                                    </div>
                                                                )}
                                                                {result?.lint?.available && !result.lint.ok && (
                                                                    <details className="code-lint-box">
                                                                        <summary>
                                                                            🔎 {result.lint.tool} 검사에서 {result.lint.issues?.length || 0}개 이슈 발견
                                                                        </summary>
                                                                        <pre className="code-lint-issues">
                                                                            {(result.lint.issues || []).join('\n')}
                                                                        </pre>
                                                                    </details>
                                                                )}
                                                                {result?.lint?.available && result.lint.ok && (
                                                                    <div className="code-lint-ok">✅ {result.lint.tool} 통과</div>
                                                                )}
                                                            </div>
                                                        );
                                                    })}
                                                    {!m.applied && (
                                                        <button
                                                            type="button"
                                                            className="code-proposal-apply-btn"
                                                            onClick={() => applyProposal(m.id)}
                                                            disabled={applyingId === m.id}
                                                        >
                                                            {applyingId === m.id ? '적용 중...' : '이 제안 전체 적용하기'}
                                                        </button>
                                                    )}
                                                </div>
                                            )}
                                        </div>
                                    ))}
                                    <div ref={bottomRef} />
                                </div>
                                <div className="code-architect-toggle-row">
                                    {!agentMode && (
                                        <label className="code-architect-toggle">
                                            <input
                                                type="checkbox"
                                                checked={fastMode}
                                                onChange={(e) => { setFastMode(e.target.checked); if (e.target.checked) setArchitectMode(false); }}
                                                disabled={streaming}
                                            />
                                            ⚡ 빠른 모드 (qwen2.5-coder:14b · Architect와는 같이 못 씀)
                                        </label>
                                    )}
                                    <label className="code-architect-toggle">
                                        <input
                                            type="checkbox"
                                            checked={architectMode}
                                            onChange={(e) => { setArchitectMode(e.target.checked); if (e.target.checked) { setAgentMode(false); setFastMode(false); } }}
                                            disabled={streaming}
                                        />
                                        🏗️ Architect 모드 (설계 → 패치 작성 2단계, 복잡한 수정에 권장 · 느림)
                                    </label>
                                    <label className="code-architect-toggle">
                                        <input
                                            type="checkbox"
                                            checked={agentMode}
                                            onChange={(e) => { setAgentMode(e.target.checked); if (e.target.checked) setArchitectMode(false); }}
                                            disabled={streaming}
                                        />
                                        🤖 에이전트 모드 (파일을 스스로 조사, 왼쪽 체크박스 무시됨 · 가장 느림)
                                    </label>
                                    <label className="code-architect-toggle" title={typoraPath ? `미러링 파일: ${typoraPath}` : undefined}>
                                        <input
                                            type="checkbox"
                                            checked={syncToTypora}
                                            onChange={(e) => setSyncToTypora(e.target.checked)}
                                        />
                                        📝 Typora로 보기 (실시간 출력을 로컬 마크다운 파일로 미러링)
                                    </label>
                                    {syncToTypora && (
                                        <button
                                            type="button"
                                            className="code-typora-open-btn"
                                            onClick={openInTypora}
                                            disabled={typoraBusy}
                                            title={typoraPath || 'Typora에서 열기'}
                                        >
                                            {typoraBusy ? '여는 중...' : '🖥 Typora에서 열기'}
                                        </button>
                                    )}
                                    {agentMode && (
                                        <div className="code-agent-model-select" role="radiogroup" aria-label="에이전트 모드 LLM 선택">
                                            <span className="code-agent-model-select-label">
                                                에이전트 모델:
                                                <button
                                                    type="button"
                                                    className="code-agent-model-refresh"
                                                    onClick={refreshAvailableModels}
                                                    disabled={modelsLoading}
                                                    title="ollama list 다시 불러오기"
                                                >
                                                    🔄
                                                </button>
                                            </span>
                                            <label className="code-agent-model-option">
                                                <input
                                                    type="radio"
                                                    name="agentModel"
                                                    value=""
                                                    checked={agentModel === ''}
                                                    onChange={() => setAgentModel('')}
                                                    disabled={streaming}
                                                />
                                                자동 (기본값)
                                            </label>
                                            {modelsLoading && availableModels.length === 0 && (
                                                <span className="code-agent-model-loading">모델 목록 불러오는 중...</span>
                                            )}
                                            {!modelsLoading && availableModels.length === 0 && (
                                                <span className="code-agent-model-empty">
                                                    설치된 모델을 찾지 못했습니다 (ollama가 켜져있는지 확인해주세요)
                                                </span>
                                            )}
                                            {availableModels.map(m => (
                                                <label
                                                    className={
                                                        m.supports_tools === false
                                                            ? 'code-agent-model-option code-agent-model-option-warn'
                                                            : 'code-agent-model-option'
                                                    }
                                                    key={m.name}
                                                    title={
                                                        m.supports_tools === false
                                                            ? '이 모델은 도구 호출(tool calling)을 지원하지 않는 것으로 확인됨 - 에이전트 모드에서 빈 응답이 나올 수 있습니다.'
                                                            : undefined
                                                    }
                                                >
                                                    <input
                                                        type="radio"
                                                        name="agentModel"
                                                        value={m.name}
                                                        checked={agentModel === m.name}
                                                        onChange={() => setAgentModel(m.name)}
                                                        disabled={streaming}
                                                    />
                                                    {m.name}{typeof m.size_gb === 'number' ? ` (${m.size_gb}GB)` : ''}
                                                    {m.supports_tools === false && ' ⚠️ 도구 호출 미지원'}
                                                </label>
                                            ))}
                                        </div>
                                    )}
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
                                     type="button">
                                        {streaming ? '생성 중...' : '전송'}
                                    </button>
                                </div>

                                {gitStatus && gitStatus.length > 0 && (
                                    <div className="code-git-status-box">
                                        <div className="code-git-status-header">
                                            📋 커밋될 파일 ({checkedGitPaths.size}/{gitStatus.length}개 선택됨)
                                            <span>
                                                <button
                                                    type="button"
                                                    className="code-git-status-refresh"
                                                    onClick={() => setCheckedGitPaths(new Set(gitStatus.map(f => f.path)))}
                                                >
                                                    전체선택
                                                </button>
                                                <button type="button" className="code-git-status-refresh" onClick={loadGitStatus} title="새로고침">🔄</button>
                                            </span>
                                        </div>
                                        <div className="code-git-status-list">
                                            {gitStatus.map(f => (
                                                <label key={f.path} className="code-git-status-item">
                                                    <input
                                                        type="checkbox"
                                                        checked={checkedGitPaths.has(f.path)}
                                                        onChange={() => toggleGitFile(f.path)}
                                                    />
                                                    <span className="code-git-status-code">{f.status}</span>
                                                    <span className="code-git-status-path">{f.path}</span>
                                                </label>
                                            ))}
                                        </div>
                                    </div>
                                )}
                                {gitStatus && gitStatus.length === 0 && (
                                    <div className="code-git-status-clean">✅ 변경된 파일 없음 (커밋할 게 없습니다)</div>
                                )}

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
                                     type="button">
                                        {commitBusy ? '커밋 중...' : '📤 커밋 & 푸시'}
                                    </button>
                                    <button
                                        className="code-undo-btn"
                                        onClick={requestUndoConfirm}
                                        disabled={undoBusy || !!undoConfirm}
                                        title="마지막 커밋을 revert로 되돌립니다 (히스토리를 지우지 않고 반대 커밋을 추가)"
                                     type="button">
                                        ↩️ 되돌리기
                                    </button>
                                </div>

                                {undoConfirm && (
                                    <div className="code-undo-confirm">
                                        마지막 커밋을 되돌릴까요?{' '}
                                        <span className="github-commit-sha">{undoConfirm.sha}</span> {undoConfirm.message}
                                        <div className="code-undo-confirm-actions">
                                            <button onClick={confirmUndo} disabled={undoBusy} className="code-undo-confirm-yes" type="button">
                                                {undoBusy ? '되돌리는 중...' : '되돌리기 (revert + push)'}
                                            </button>
                                            <button onClick={cancelUndo} disabled={undoBusy} className="code-undo-confirm-no" type="button">취소</button>
                                        </div>
                                    </div>
                                )}
                                {undoResult && (
                                    <div className={undoResult.status === 'success' ? 'code-commit-result ok' : 'code-commit-result error'}>
                                        {undoResult.status === 'success' ? '✅ 되돌리기 완료 (푸시됨)' : '⚠️ 되돌리기 실패'}
                                        <details>
                                            <summary>상세 로그</summary>
                                            <pre>{(undoResult.steps || []).map(s => `$ git ${s.cmd}\n${s.stdout || ''}${s.stderr || ''}`).join('\n\n')}</pre>
                                        </details>
                                    </div>
                                )}

                                {commitResult && (
                                    <div className={commitResult.status === 'success' ? 'code-commit-result ok' : 'code-commit-result error'}>
                                        {commitResult.status === 'success' ? '✅ 커밋/푸시 완료' : '⚠️ 실패'}
                                        {commitResult.githubCheck && (
                                            <div className="github-verify-row">
                                                🐙 GitHub 확인됨: <span className="github-commit-sha">{commitResult.githubCheck.sha}</span>{' '}
                                                {commitResult.githubCheck.message}{' '}
                                                <a href={commitResult.githubCheck.html_url} target="_blank" rel="noreferrer">보기</a>
                                            </div>
                                        )}
                                        {commitResult.status === 'success' && !commitResult.githubCheck && (
                                            <div className="github-verify-row github-verify-pending">
                                                🐙 GitHub 반영 확인 중...
                                            </div>
                                        )}
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
