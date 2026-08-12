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
import useCurrentUser from "./useCurrentUser";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

// 2026-08-12: CSS @keyframes 애니메이션이 시스템의 "동작 줄이기(Reduce Motion)"
// 설정이나 다른 전역 CSS에 막혀 안 도는 경우가 있어서, CSS에 전혀 의존하지
// 않고 자바스크립트 setInterval로 직접 프레임 문자를 바꾸는 방식으로 변경.
const SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

function TextSpinner() {
    const [frame, setFrame] = useState(0);
    useEffect(() => {
        const id = setInterval(() => {
            setFrame((f) => (f + 1) % SPINNER_FRAMES.length);
        }, 90);
        return () => clearInterval(id);
    }, []);
    return (
        <span style={{ display: "inline-block", width: "1.2em", fontSize: "1.1em" }}>
            {SPINNER_FRAMES[frame]}
        </span>
    );
}

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

    const [typoraOpened, setTyporaOpened] = useState(false);
    const [typoraBusy, setTyporaBusy] = useState(false);
    const [showVaultPopover, setShowVaultPopover] = useState(false);
    const [vaultFolders, setVaultFolders] = useState([]);
    const [vaultFolder, setVaultFolder] = useState("");
    const [vaultNewFolder, setVaultNewFolder] = useState("");
    const [vaultFilename, setVaultFilename] = useState("");
    const displayName = useCurrentUser();
    // 2026-08-12: plain const로 localStorage를 읽으면, 이 컴포넌트가 다른
    // 이유로 리렌더링되기 전까지 로그인 변경을 못 알아챌 수 있었다(모달을
    // 이미 열어둔 채로 로그인/로그아웃하는 경우 등). 'hf-user-registered'
    // 이벤트를 직접 구독하는 state로 바꿔서 확실하게 반응하도록 함.
    const [userId, setUserId] = useState(() => localStorage.getItem("hf_user_id") || null);
    useEffect(() => {
        const handler = (e) => setUserId(e.detail?.user_id || null);
        window.addEventListener("hf-user-registered", handler);
        return () => window.removeEventListener("hf-user-registered", handler);
    }, []);

    // 개인화 - "그동안 모아온 분야" 선택 + "내 관심분야만 보기"
    const [fields, setFields] = useState([]);
    const [hasFieldPreferences, setHasFieldPreferences] = useState(false);
    const [selectedFieldIds, setSelectedFieldIds] = useState(() => new Set());
    const [customField, setCustomField] = useState("");
    const [savingFields, setSavingFields] = useState(false);
    const [onlyPreferred, setOnlyPreferred] = useState(false);

    const fetchRepos = useCallback(async (preferredOnly) => {
        setLoading(true);
        try {
            const res = await axios.get(`${API_BASE}/github/repos`, {
                params: {
                    user_id: userId || undefined,
                    only_preferred: preferredOnly ?? onlyPreferred,
                },
            });
            setRepos(res.data.repos || []);
        } catch (err) {
            console.error("GitHub 레포 조회 에러:", err);
        } finally {
            setLoading(false);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [userId, onlyPreferred]);

    const fetchFields = useCallback(async () => {
        try {
            const res = await axios.get(`${API_BASE}/github/fields`, {
                params: { user_id: userId || undefined },
            });
            const list = res.data.fields || [];
            setFields(list);
            setHasFieldPreferences(!!res.data.has_preferences);
            // 이미 선택해둔(또는 Admin이라 전부 selected로 오는) 분야를 체크 상태로 반영
            setSelectedFieldIds(new Set(list.filter((f) => f.selected).map((f) => f.tag_id)));
        } catch (err) {
            console.error("GitHub 관심분야 조회 에러:", err);
        }
    }, [userId]);

    useEffect(() => {
        if (open) {
            fetchRepos();
            fetchFields();
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, userId]);

    const toggleField = (tagId) => {
        setSelectedFieldIds((prev) => {
            const next = new Set(prev);
            if (next.has(tagId)) next.delete(tagId);
            else next.add(tagId);
            return next;
        });
    };

    const allFieldIds = fields.map((f) => f.tag_id);
    const isAllFieldsChecked = allFieldIds.length > 0 && allFieldIds.every((id) => selectedFieldIds.has(id));
    const toggleAllFields = () => {
        setSelectedFieldIds(isAllFieldsChecked ? new Set() : new Set(allFieldIds));
    };

    const handleSaveFields = async () => {
        if (selectedFieldIds.size === 0 && !customField.trim()) {
            toast("선택하거나 직접 입력한 분야가 없습니다.");
            return;
        }
        setSavingFields(true);
        try {
            const res = await axios.post(`${API_BASE}/github/select-fields`, {
                tag_ids: Array.from(selectedFieldIds),
                custom_field: customField.trim() || null,
                user_id: userId,
            });
            toast.success(res.data.message);
            setCustomField("");
            await fetchFields();
            setOnlyPreferred(true);
            await fetchRepos(true);
        } catch (err) {
            console.error("관심분야 저장 에러:", err);
            toast.error("저장에 실패했습니다.");
        } finally {
            setSavingFields(false);
        }
    };

    const handleToggleOnlyPreferred = async () => {
        if (!userId) {
            toast("사용자 등록 후 이용할 수 있습니다.");
            return;
        }
        const next = !onlyPreferred;
        setOnlyPreferred(next);
        await fetchRepos(next);
    };

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

    const [trendingBusy, setTrendingBusy] = useState(false);
    const [trendingToastId, setTrendingToastId] = useState(null);

    const startTrendingCrawl = async () => {
        setTrendingBusy(true);
        const toastId = toast.loading("GitHub 트렌딩 크롤링 중... (다시 누르면 중단)");
        setTrendingToastId(toastId);
        try {
            // 이 axios 호출은 백엔드 작업이 끝날 때까지(또는 아래 중단 요청으로
            // 중간에 멈출 때까지) 대기 상태로 남아있는다 - 그동안 버튼을 다시
            // 누르면 별도의 /collect/cancel 요청이 나가서 실제로 중단시킨다.
            const res = await axios.post(`${API_BASE}/collect/github/trending`, null, {
                params: { user_id: userId },
            });
            toast.success(res.data.message, { id: toastId });
            await fetchRepos();
            await fetchFields(); // 새로 수집된 레포의 태그가 관심분야 목록에 새로 뜨도록
        } catch (err) {
            console.error("트렌딩 크롤링 에러:", err);
            toast.error(err.response?.data?.detail || "크롤링 실패", { id: toastId });
        } finally {
            setTrendingBusy(false);
            setTrendingToastId(null);
        }
    };

    const stopTrendingCrawl = async () => {
        try {
            await axios.post(`${API_BASE}/collect/cancel`);
            if (trendingToastId) {
                toast.loading("중단 요청됨 - 진행 중인 항목까지 마무리하고 멈춥니다...", { id: trendingToastId });
            }
        } catch (err) {
            console.error("크롤링 중단 요청 에러:", err);
        }
    };

    const handleTrendingCrawl = () => {
        if (trendingBusy) {
            stopTrendingCrawl();
        } else {
            startTrendingCrawl();
        }
    };

    const openDetail = async (repoId) => {
        setSelectedId(repoId);
        setView("detail");
        setDetail(null);
        setDetailLoading(true);
        setTyporaOpened(false);
        setShowVaultPopover(false);
        try {
            const res = await axios.get(`${API_BASE}/github/repos/${repoId}/detail`);
            setDetail(res.data);
            setVaultFilename(res.data.full_name.replace("/", "_"));
            // 2026-08-12: 상세 분석(분야/응용분야/R/구성요소)이 이 조회 중에
            // 새로 갱신됐을 수 있으니, 표(repos) 데이터도 같이 새로고침해둔다.
            // await 안 하는 이유: 상세보기 로딩 스피너는 detail이 오는 즉시
            // 멈춰야 하고, 표 갱신은 어차피 "목록으로" 돌아갈 때만 보이므로
            // 백그라운드로 조용히 처리하면 됨.
            fetchRepos();
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

    // Typora 편집: 상세문서(개요/응용분야/연관성/향후방향 + 추가메모)를
    // 하나의 .md로 합쳐서 Typora로 연다. '##' 헤더로 구분되어 있어서, 새
    // 섹션을 추가해도 가져오기 시 자동으로 extra_notes에 보존된다.
    const handleEditInTypora = async () => {
        setTyporaBusy(true);
        try {
            await axios.post(`${API_BASE}/github/repos/${selectedId}/edit-in-typora`);
            setTyporaOpened(true);
        } catch (err) {
            console.error("Typora 편집 시작 에러:", err);
            toast.error("Typora를 여는 데 실패했습니다.");
        } finally {
            setTyporaBusy(false);
        }
    };

    const handleImportFromTypora = async () => {
        setTyporaBusy(true);
        try {
            const res = await axios.post(`${API_BASE}/github/repos/${selectedId}/import-from-typora`);
            toast.success(res.data.message);
            await openDetail(selectedId); // 최신 내용으로 화면 갱신
        } catch (err) {
            console.error("Typora 가져오기 에러:", err);
            toast.error(err.response?.data?.detail || "Typora에서 가져오는 데 실패했습니다.");
        } finally {
            setTyporaBusy(false);
        }
    };

    const handleOpenVaultPopover = async () => {
        if (!userId) {
            toast("사용자 등록/로그인 후 이용할 수 있습니다.");
            return;
        }
        if (!showVaultPopover) {
            try {
                const res = await axios.get(`${API_BASE}/vault/folders`, { params: { user_id: userId } });
                setVaultFolders(res.data.folders || []);
            } catch (err) {
                console.error("저장소 폴더 조회 에러:", err);
            }
        }
        setShowVaultPopover(!showVaultPopover);
    };

    const handleConfirmVaultExport = async () => {
        const folder = vaultNewFolder.trim() || vaultFolder;
        if (!folder) {
            toast("폴더를 선택하거나 새 폴더 이름을 입력해주세요.");
            return;
        }
        const sections = [
            `## 상세 개요\n\n${detail.detailed_overview || ""}`,
            `## 상세 응용분야\n\n${detail.detailed_application || ""}`,
            `## 구성요소와의 연관성\n\n${detail.detailed_relations || ""}`,
            `## 향후 발전 방향\n\n${detail.future_direction || ""}`,
        ];
        if (detail.extra_notes) sections.push(detail.extra_notes);
        const content = sections.join("\n\n");

        try {
            await axios.post(`${API_BASE}/vault/export`, {
                folder,
                filename: vaultFilename || detail.full_name.replace("/", "_"),
                content,
                user_id: userId,
                source_title: detail.full_name,
                source_ref: `github:${selectedId}`,
            });
            toast.success(`${displayName} 저장소에 저장했습니다.`);
            setShowVaultPopover(false);
            setVaultNewFolder("");
        } catch (err) {
            console.error("저장소 저장 에러:", err);
            toast.error("저장에 실패했습니다.");
        }
    };

    // renderView(): 현재 view 값에 따라 표/상세/README 중 하나를 그려준다.
    // 모달(overlay+panel)은 이 함수 바깥, 컴포넌트의 최종 return에서 한 번만 감싼다.
    const renderView = () => {
    // ============ 1단계: 표 ============
    if (view === "table") {
        return (
            <>
                <div className="github-fields-picker">
                    <div className="github-fields-header">
                        <span>⭐ 관심 분야 ({displayName}) — 그동안 모아온 분야 중 골라서 저장하면, "내 관심분야만 보기"로 걸러볼 수 있습니다.</span>
                        <button
                            onClick={handleToggleOnlyPreferred}
                            className={"genre-eval-genre-btn-toggle" + (onlyPreferred ? " active" : "")}
                            style={{
                                backgroundColor: onlyPreferred ? "var(--primary-color)" : "transparent",
                                color: onlyPreferred ? "#fff" : "var(--text-secondary)",
                            }}
                        >
                            {onlyPreferred ? "✅ 내 관심분야만 보는 중" : "☆ 내 관심분야만 보기"}
                        </button>
                    </div>

                    {fields.length > 0 && (
                        <div className="github-fields-chips">
                            <label className="github-field-chip" style={{ fontWeight: 700, color: "var(--primary-color)" }}>
                                <input
                                    type="checkbox"
                                    checked={isAllFieldsChecked}
                                    onChange={toggleAllFields}
                                />
                                모두
                            </label>
                            {fields.map((f) => (
                                <label key={f.tag_id} className="github-field-chip">
                                    <input
                                        type="checkbox"
                                        checked={selectedFieldIds.has(f.tag_id)}
                                        onChange={() => toggleField(f.tag_id)}
                                    />
                                    {f.name}
                                </label>
                            ))}
                        </div>
                    )}

                    <div className="github-fields-custom-row">
                        <input
                            type="text"
                            placeholder="직접 입력 (예: agentic-coding)"
                            value={customField}
                            onChange={(e) => setCustomField(e.target.value)}
                        />
                        <button onClick={handleSaveFields} disabled={savingFields} className="collect-btn">
                            {savingFields ? "저장 중..." : "관심분야 저장"}
                        </button>
                    </div>
                </div>

                {userId && !hasFieldPreferences ? (
                    <p className="no-data" style={{ marginTop: "8px" }}>
                        ⭐ 위에서 관심 분야를 선택하거나 직접 입력해서 저장하면, GitHub 레포 목록이 여기 나타납니다.
                    </p>
                ) : (
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
                            {userId === "Admin" && (
                                <button
                                    type="button"
                                    onClick={handleTrendingCrawl}
                                    className="collect-btn"
                                    style={{
                                        backgroundColor: trendingBusy ? "#dc2626" : "#0891b2",
                                        display: "inline-flex",
                                        alignItems: "center",
                                        gap: "6px",
                                    }}
                                    title={
                                        trendingBusy
                                            ? "클릭하면 진행 중인 크롤링을 중단합니다."
                                            : "스타 5만개 이상, 또는 최근 1주일 내 생성돼 이미 스타 1000개 이상인 오픈소스를 자동으로 찾아 저장합니다."
                                    }
                                >
                                    {trendingBusy && <TextSpinner />}
                                    {trendingBusy ? "크롤링 중지" : "🔄 크롤링 재개"}
                                </button>
                            )}
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
                                            <th title="연관성 - H(높음)/M(보통)/L(낮음)">R</th>
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
                )}
            </>
        );
    }

    // ============ 2단계: 상세 ============
    if (view === "detail") {
        return (
            <>
                <button onClick={() => { setView("table"); fetchRepos(); }} className="collect-btn" style={{ marginBottom: "14px" }}>
                    ← 목록으로
                </button>

                <div className="genre-editor-table-wrap">
                    {detailLoading || !detail ? (
                        <p style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                            <TextSpinner />
                            상세 정보를 분석하는 중입니다... (처음 조회 시 시간이 걸릴 수 있습니다)
                        </p>
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

                            <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginBottom: "16px", position: "relative" }}>
                                <button onClick={openReadme} className="collect-btn">
                                    📄 README 원문 보기
                                </button>
                                <button
                                    onClick={handleEditInTypora}
                                    disabled={typoraBusy}
                                    className="collect-btn"
                                    style={{ backgroundColor: "#faad3f" }}
                                    title="Typora에서 열어 편집합니다. 저장(Cmd+S) 후 옆의 '가져오기'를 눌러주세요."
                                >
                                    📝 Typora 편집
                                </button>
                                {typoraOpened && (
                                    <button
                                        onClick={handleImportFromTypora}
                                        disabled={typoraBusy}
                                        className="collect-btn"
                                        style={{ backgroundColor: "#0d9488" }}
                                    >
                                        {typoraBusy ? "⏳ 가져오는 중..." : "📥 Typora에서 가져오기"}
                                    </button>
                                )}
                                <button
                                    onClick={handleOpenVaultPopover}
                                    className="collect-btn"
                                    style={{ backgroundColor: "#8b5cf6" }}
                                >
                                    📥 {displayName} 저장소에 저장
                                </button>

                                {showVaultPopover && (
                                    <div className="vault-export-popover">
                                        <div className="vault-export-row">
                                            <label>폴더 선택</label>
                                            <select value={vaultFolder} onChange={(e) => setVaultFolder(e.target.value)}>
                                                <option value="">-- 선택 --</option>
                                                {vaultFolders.map((f) => (
                                                    <option key={f} value={f}>{f}</option>
                                                ))}
                                            </select>
                                        </div>
                                        <div className="vault-export-row">
                                            <label>또는 새 폴더</label>
                                            <input
                                                value={vaultNewFolder}
                                                onChange={(e) => setVaultNewFolder(e.target.value)}
                                                placeholder="새 폴더 이름"
                                            />
                                        </div>
                                        <div className="vault-export-row">
                                            <label>파일명</label>
                                            <input
                                                value={vaultFilename}
                                                onChange={(e) => setVaultFilename(e.target.value)}
                                            />
                                        </div>
                                        <div className="vault-export-actions">
                                            <button onClick={handleConfirmVaultExport} className="vault-export-confirm">확인</button>
                                            <button onClick={() => setShowVaultPopover(false)} className="vault-export-cancel">취소</button>
                                        </div>
                                    </div>
                                )}
                            </div>

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
                            {detail.extra_notes && (
                                <div className="github-detail-section">
                                    <h4>추가 메모 (Typora에서 새로 추가한 섹션)</h4>
                                    <p style={{ whiteSpace: "pre-wrap" }}>{detail.extra_notes}</p>
                                </div>
                            )}
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
