// GitHubRepos.jsx
// ---------------------------------------------------------------------------
// 'GitHub 저장소' 독립 버튼 + 모달. 3단계 구조:
// 1단계(table)  - 분야/오픈소스/스타수/응용분야/연관성/구성요소 표, 단어 위주
// 2단계(detail) - 레포 클릭 시 상세 분석(문단 단위) + 기본 정보
// 3단계(readme) - "README 원문 보기" 클릭 시 원본 그대로
//
// 2026-08-11: 데이터편집(GenreEditor) 탭에서 빠져나와 메인화면 독립 버튼으로
// 전환. 모달 껍데기(overlay/panel/header)는 genre-editor-*의 것을 그대로
// 재사용해서 다른 데이터관리 모달들과 톤을 맞췄다.
// ---------------------------------------------------------------------------

import { useState, useEffect, useCallback } from "react";
import axios from "axios";
import { toast } from "react-hot-toast";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

export default function GitHubRepos() {
    const [open, setOpen] = useState(false);
    const [view, setView] = useState("table"); // "table" | "detail" | "readme"
    const [repos, setRepos] = useState([]);
    const [loading, setLoading] = useState(true);
    const [query, setQuery] = useState("");
    const [maxEntries, setMaxEntries] = useState(10);
    const [discovering, setDiscovering] = useState(false);

    const [selectedId, setSelectedId] = useState(null);
    const [detail, setDetail] = useState(null);
    const [detailLoading, setDetailLoading] = useState(false);
    const [readme, setReadme] = useState(null);

    const fetchRepos = useCallback(async () => {
        setLoading(true);
        try {
            const res = await axios.get(`${API_BASE}/github/repos`);
            setRepos(res.data.repos || []);
        } catch (err) {
            console.error("GitHub 레포 조회 에러:", err);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        if (open) {
            fetchRepos();
        }
    }, [open, fetchRepos]);

    const handleDiscover = async (e) => {
        e.preventDefault();
        if (!query.trim()) {
            toast("검색어를 입력해주세요.");
            return;
        }
        setDiscovering(true);
        const toastId = toast.loading("GitHub 레포 발굴 중...");
        try {
            const res = await axios.post(`${API_BASE}/collect/github`, {
                query: query.trim(),
                max_entries: maxEntries,
            });
            toast.success(res.data.message, { id: toastId });
            await fetchRepos();
        } catch (err) {
            console.error("GitHub 발굴 에러:", err);
            toast.error(err.response?.data?.detail || "발굴 실패", { id: toastId });
        } finally {
            setDiscovering(false);
        }
    };

    const openDetail = async (repoId) => {
        setSelectedId(repoId);
        setView("detail");
        setDetail(null);
        setDetailLoading(true);
        try {
            const res = await axios.get(`${API_BASE}/github/repos/${repoId}/detail`);
            setDetail(res.data);
        } catch (err) {
            console.error("상세 조회 에러:", err);
            toast.error("상세 정보를 불러오지 못했습니다.");
            setView("table");
        } finally {
            setDetailLoading(false);
        }
    };

    const openReadme = async () => {
        if (!selectedId) return;
        try {
            const res = await axios.get(`${API_BASE}/github/repos/${selectedId}/readme`);
            setReadme(res.data);
            setView("readme");
        } catch (err) {
            console.error("README 조회 에러:", err);
            toast.error("README를 불러오지 못했습니다.");
        }
    };

    // renderView(): 현재 view 값에 따라 표/상세/README 중 하나를 그려준다.
    // 모달(overlay+panel)은 이 함수 바깥, 컴포넌트의 최종 return에서 한 번만 감싼다.
    const renderView = () => {
    // ============ 1단계: 표 ============
    if (view === "table") {
        return (
            <>
                <form onSubmit={handleDiscover} style={{ display: "flex", gap: "8px", marginBottom: "16px" }}>
                    <input
                        type="text"
                        placeholder="검색어 (예: rag stars:>1000, language:python topic:llm)"
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        style={{ flex: 1 }}
                    />
                    <input
                        type="number"
                        min="1"
                        max="30"
                        value={maxEntries}
                        onChange={(e) => setMaxEntries(Number(e.target.value))}
                        style={{ width: "60px" }}
                        title="최대 발굴 건수"
                    />
                    <button type="submit" className="collect-btn" disabled={discovering}>
                        {discovering ? "발굴 중..." : "🔍 발굴"}
                    </button>
                </form>

                <div className="genre-editor-table-wrap">
                    {loading ? (
                        <p>불러오는 중...</p>
                    ) : repos.length === 0 ? (
                        <p style={{ color: "var(--text-muted)" }}>
                            등록된 GitHub 레포가 없습니다. 위에서 검색어를 입력해 발굴해보세요.
                        </p>
                    ) : (
                        <table className="genre-editor-table">
                            <thead>
                                <tr>
                                    <th>분야</th>
                                    <th>오픈소스</th>
                                    <th>스타수</th>
                                    <th>응용분야</th>
                                    <th>연관성</th>
                                    <th>구성요소</th>
                                </tr>
                            </thead>
                            <tbody>
                                {repos.map((r) => (
                                    <tr
                                        key={r.id}
                                        onClick={() => openDetail(r.id)}
                                        style={{ cursor: "pointer" }}
                                        title="클릭하면 상세 정보를 볼 수 있습니다"
                                    >
                                        <td>{r.field}</td>
                                        <td style={{ color: "#60a5fa", fontWeight: 600 }}>{r.full_name}</td>
                                        <td>⭐ {r.stars.toLocaleString()}</td>
                                        <td>{r.application}</td>
                                        <td>{r.relevance}</td>
                                        <td>{r.components}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )}
                </div>
            </>
        );
    }

    // ============ 2단계: 상세 ============
    if (view === "detail") {
        return (
            <>
                <button onClick={() => setView("table")} className="collect-btn" style={{ marginBottom: "14px" }}>
                    ← 목록으로
                </button>

                <div className="genre-editor-table-wrap">
                    {detailLoading || !detail ? (
                        <p>상세 정보를 분석하는 중입니다... (처음 조회 시 시간이 걸릴 수 있습니다)</p>
                    ) : (
                        <div>
                            <h3 style={{ marginBottom: "4px" }}>{detail.full_name}</h3>
                            <a href={detail.url} target="_blank" rel="noreferrer" style={{ fontSize: "0.85rem" }}>
                                {detail.url}
                            </a>

                            <div
                                style={{
                                    display: "flex",
                                    gap: "16px",
                                    margin: "12px 0",
                                    fontSize: "0.85rem",
                                    color: "#94a3b8",
                                    flexWrap: "wrap",
                                }}
                            >
                                <span>게재: {detail.created_at_github ? detail.created_at_github.slice(0, 10) : "-"}</span>
                                <span>업데이트: {detail.pushed_at_github ? detail.pushed_at_github.slice(0, 10) : "-"}</span>
                                <span>⭐ {(detail.stars ?? 0).toLocaleString()}</span>
                                <span>🍴 {(detail.forks ?? 0).toLocaleString()}</span>
                            </div>

                            <button onClick={openReadme} className="collect-btn" style={{ marginBottom: "16px" }}>
                                📄 README 원문 보기
                            </button>

                            <div className="github-detail-section">
                                <h4>상세 개요</h4>
                                <p>{detail.detailed_overview || "정보 없음"}</p>
                            </div>
                            <div className="github-detail-section">
                                <h4>상세 응용분야</h4>
                                <p>{detail.detailed_application || "정보 없음"}</p>
                            </div>
                            <div className="github-detail-section">
                                <h4>구성요소와의 연관성</h4>
                                <p>{detail.detailed_relations || "정보 없음"}</p>
                            </div>
                            <div className="github-detail-section">
                                <h4>향후 발전 방향</h4>
                                <p>{detail.future_direction || "정보 없음"}</p>
                            </div>
                        </div>
                    )}
                </div>
            </>
        );
    }

    // ============ 3단계: README 원문 ============
    if (view === "readme") {
        return (
            <>
                <button onClick={() => setView("detail")} className="collect-btn" style={{ marginBottom: "14px" }}>
                    ← 상세로
                </button>
                <h3>{readme?.full_name} — README</h3>
                <div className="genre-editor-table-wrap">
                    <pre className="github-readme-raw">{readme?.readme_content}</pre>
                </div>
            </>
        );
    }

    return null;
    };

    return (
        <>
            <button onClick={() => setOpen(true)} className="collect-btn github-repos-btn">
                🐙 GitHub 저장소
            </button>

            {open && (
                <div className="genre-editor-overlay" onClick={() => setOpen(false)}>
                    <div className="genre-editor-panel" onClick={(e) => e.stopPropagation()}>
                        <div className="genre-editor-header">
                            <h3>GitHub 저장소</h3>
                            <button onClick={() => setOpen(false)}>닫기</button>
                        </div>
                        {renderView()}
                    </div>
                </div>
            )}
        </>
    );
}
