import React, { useState, useEffect } from 'react';

// 2026-08-13: GitHub 브라우징. hf_coder(:8100)의 /github/* 를 그대로 호출한다.
// clone 없이 GitHub API로만 조회하는 읽기전용 탐색기.
// 탭 두 개: "타 저장소 탐색"(owner/repo 직접 입력) / "내 저장소 확인"(로컬 git
// remote로 자동 감지 + 최신 커밋으로 push 반영 여부 확인).

const API_URL = import.meta.env.VITE_CODE_API_URL || 'http://localhost:8100';

function GitHubBrowser() {
    const [open, setOpen] = useState(false);
    const [tab, setTab] = useState('mine'); // 'mine' | 'other'

    // 공통: 파일트리/파일뷰어 상태
    const [repoInfo, setRepoInfo] = useState(null);
    const [fileTree, setFileTree] = useState([]);
    const [fileFilter, setFileFilter] = useState('');
    const [selectedFile, setSelectedFile] = useState(null);
    const [fileContent, setFileContent] = useState('');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [rateLimit, setRateLimit] = useState(null);

    // "타 저장소 탐색" 전용 입력
    const [inputOwner, setInputOwner] = useState('');
    const [inputRepo, setInputRepo] = useState('');

    // "내 저장소 확인" 전용
    const [commits, setCommits] = useState([]);

    const resetView = () => {
        setFileTree([]);
        setSelectedFile(null);
        setFileContent('');
        setError(null);
        setCommits([]);
    };

    const loadOtherRepo = async () => {
        if (!inputOwner.trim() || !inputRepo.trim()) return;
        resetView();
        setLoading(true);
        try {
            const infoRes = await fetch(`${API_URL}/github/repo-info?owner=${inputOwner}&repo=${inputRepo}`);
            const info = await infoRes.json();
            if (!infoRes.ok) throw new Error(info.detail || '저장소 정보를 가져오지 못했습니다.');
            setRepoInfo(info);
            setRateLimit(info.rate_limit);

            const treeRes = await fetch(`${API_URL}/github/tree?owner=${inputOwner}&repo=${inputRepo}&branch=${info.default_branch}`);
            const tree = await treeRes.json();
            if (!treeRes.ok) throw new Error(tree.detail || '파일트리를 가져오지 못했습니다.');
            setFileTree(tree.files || []);
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    const loadMyRepo = async () => {
        resetView();
        setLoading(true);
        try {
            const infoRes = await fetch(`${API_URL}/github/my-repo`);
            const info = await infoRes.json();
            if (!infoRes.ok) throw new Error(info.detail || '내 저장소 정보를 가져오지 못했습니다.');
            setRepoInfo(info);
            setRateLimit(info.rate_limit);

            const branch = info.local_branch || info.default_branch;
            const [treeRes, commitsRes] = await Promise.all([
                fetch(`${API_URL}/github/tree?owner=${info.owner}&repo=${info.repo}&branch=${branch}`),
                fetch(`${API_URL}/github/commits?owner=${info.owner}&repo=${info.repo}&branch=${branch}&limit=8`),
            ]);
            const tree = await treeRes.json();
            const commitsData = await commitsRes.json();
            if (treeRes.ok) setFileTree(tree.files || []);
            if (commitsRes.ok) setCommits(commitsData.commits || []);
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        if (!open) return;
        if (tab === 'mine') loadMyRepo();
        else resetView();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, tab]);

    const openFile = async (path) => {
        if (!repoInfo) return;
        setSelectedFile(path);
        setFileContent('불러오는 중...');
        const branch = repoInfo.local_branch || repoInfo.default_branch;
        try {
            const res = await fetch(`${API_URL}/github/file?owner=${repoInfo.owner}&repo=${repoInfo.repo}&path=${encodeURIComponent(path)}&branch=${branch}`);
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || '파일을 열지 못했습니다.');
            setFileContent(data.content);
            setRateLimit(data.rate_limit);
        } catch (err) {
            setFileContent(`⚠️ ${err.message}`);
        }
    };

    const visibleFiles = fileFilter.trim()
        ? fileTree.filter(f => f.path.toLowerCase().includes(fileFilter.trim().toLowerCase()))
        : fileTree;

    return (
        <>
            <button className="collect-btn github-browser-btn" onClick={() => setOpen(true)}>
                🐙 GitHub
            </button>

            {open && (
                <div className="code-analysis-overlay" onClick={() => setOpen(false)}>
                    <div className="code-analysis-panel" onClick={(e) => e.stopPropagation()}>
                        <div className="code-analysis-header">
                            <h3>🐙 GitHub 탐색기</h3>
                            <div style={{ display: 'flex', gap: '8px' }}>
                                {rateLimit?.rate_limit_remaining && (
                                    <span className="github-rate-limit">
                                        API {rateLimit.rate_limit_remaining}/{rateLimit.rate_limit_limit}
                                    </span>
                                )}
                                <button onClick={() => setOpen(false)}>✕ 닫기</button>
                            </div>
                        </div>

                        <div className="github-tabs">
                            <button
                                className={tab === 'mine' ? 'github-tab active' : 'github-tab'}
                                onClick={() => setTab('mine')}
                            >
                                📍 내 저장소 확인
                            </button>
                            <button
                                className={tab === 'other' ? 'github-tab active' : 'github-tab'}
                                onClick={() => setTab('other')}
                            >
                                🔍 타 저장소 탐색
                            </button>
                        </div>

                        {tab === 'other' && (
                            <div className="github-other-input-row">
                                <input
                                    type="text" placeholder="owner (예: facebook)"
                                    value={inputOwner} onChange={(e) => setInputOwner(e.target.value)}
                                />
                                <input
                                    type="text" placeholder="repo (예: react)"
                                    value={inputRepo} onChange={(e) => setInputRepo(e.target.value)}
                                    onKeyDown={(e) => e.key === 'Enter' && loadOtherRepo()}
                                />
                                <button onClick={loadOtherRepo} disabled={loading}>탐색</button>
                            </div>
                        )}

                        {error && <div className="github-error">⚠️ {error}</div>}

                        {repoInfo && (
                            <div className="github-repo-summary-row">
                                <strong>{repoInfo.full_name}</strong>
                                {repoInfo.description && <span> — {repoInfo.description}</span>}
                                {typeof repoInfo.stars === 'number' && <span className="github-stars"> ⭐ {repoInfo.stars}</span>}
                                {repoInfo.local_branch && <span className="github-branch-badge">브랜치: {repoInfo.local_branch}</span>}
                            </div>
                        )}

                        <div className="code-analysis-body">
                            <div className="code-analysis-sidebar">
                                <div className="code-analysis-sidebar-title">📁 파일 ({fileTree.length}개)</div>
                                <input
                                    type="text" className="code-analysis-file-filter"
                                    placeholder="파일명 검색..."
                                    value={fileFilter} onChange={(e) => setFileFilter(e.target.value)}
                                />
                                <div className="code-file-tree">
                                    {loading && <div className="code-file-tree-empty">불러오는 중...</div>}
                                    {!loading && visibleFiles.length === 0 && (
                                        <div className="code-file-tree-empty">
                                            {tab === 'other' ? 'owner/repo를 입력하고 탐색을 눌러주세요.' : '파일이 없습니다.'}
                                        </div>
                                    )}
                                    {visibleFiles.map(f => (
                                        <div
                                            key={f.path}
                                            className={selectedFile === f.path ? 'code-file-item github-file-selected' : 'code-file-item'}
                                            onClick={() => openFile(f.path)}
                                        >
                                            <span>{f.path}</span>
                                        </div>
                                    ))}
                                </div>
                            </div>

                            <div className="code-analysis-chat">
                                {tab === 'mine' && commits.length > 0 && !selectedFile && (
                                    <div className="github-commits-panel">
                                        <div className="github-commits-title">🕒 최근 커밋 (원격 기준)</div>
                                        {commits.map(c => (
                                            <a key={c.sha} href={c.html_url} target="_blank" rel="noreferrer" className="github-commit-row">
                                                <span className="github-commit-sha">{c.sha}</span>
                                                <span className="github-commit-msg">{c.message}</span>
                                                <span className="github-commit-meta">{c.author} · {new Date(c.date).toLocaleString('ko-KR')}</span>
                                            </a>
                                        ))}
                                    </div>
                                )}
                                {selectedFile && (
                                    <>
                                        <div className="github-file-path">{selectedFile}</div>
                                        <pre className="github-file-content">{fileContent}</pre>
                                    </>
                                )}
                                {!selectedFile && tab === 'other' && !loading && fileTree.length === 0 && (
                                    <div className="code-analysis-empty-hint">
                                        왼쪽 상단에 owner/repo를 입력해서 공개 저장소를 읽기전용으로 훑어볼 수 있습니다.
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

export default GitHubBrowser;
