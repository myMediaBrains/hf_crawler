// SourceManager.jsx
// ---------------------------------------------------------------------------
// '출처 관리' 화면. 원래 App.jsx 안에 거대한 인라인 블록으로 있던 걸
// 2026-08-10에 분리했다 - 장르편집기 패널 안에 탭으로 끼워 넣기 위함.
// 이 컴포넌트는 자체 토글 버튼이 없다 - 마운트되면 바로 데이터를 불러온다
// (GenreEditor의 탭 전환이 렌더링 여부를 담당).
// ---------------------------------------------------------------------------

import { useState, useEffect, useCallback } from "react";
import axios from "axios";
import { toast } from "react-hot-toast";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

function isAutoOrigin(origin) {
    return typeof origin === 'string' && origin.toLowerCase().includes('auto');
}

function isSensitiveCategory(group) {
    return !!group.sensitive;
}

function groupSourcesByCategory(list) {
    const buckets = {};
    list.forEach((src) => {
        const cat = src.major_category && src.major_category.trim() ? src.major_category : '미분류';
        if (!buckets[cat]) buckets[cat] = [];
        buckets[cat].push(src);
    });

    const categoryNames = Object.keys(buckets).sort((a, b) => {
        if (a === '미분류') return 1;
        if (b === '미분류') return -1;
        return a.localeCompare(b);
    });

    return categoryNames.map((category) => ({
        category,
        sources: buckets[category].sort((a, b) => a.name.localeCompare(b.name)),
        sensitive: buckets[category].some((src) => src.sensitive),
    }));
}

function isFailingCandidate(src) {
    return (src.fail_count || 0) >= 3;
}

function applySourceFilters(list, categoryFilter, showFailingOnly) {
    if (showFailingOnly) {
        return list.filter(isFailingCandidate);
    }
    if (categoryFilter) {
        return list.filter((src) => {
            const cat = src.major_category && src.major_category.trim() ? src.major_category : '미분류';
            return cat === categoryFilter;
        });
    }
    return list;
}

function getCategoryCounts(list) {
    const counts = {};
    list.forEach((src) => {
        const cat = src.major_category && src.major_category.trim() ? src.major_category : '미분류';
        counts[cat] = (counts[cat] || 0) + 1;
    });
    const names = Object.keys(counts).sort((a, b) => {
        if (a === '미분류') return 1;
        if (b === '미분류') return -1;
        return a.localeCompare(b);
    });
    return names.map((category) => ({ category, count: counts[category] }));
}

function formatNextCheck(lastAt, hours) {
    if (!lastAt) return '대기 중 (다음 틱에 점검)';
    const next = new Date(new Date(lastAt).getTime() + hours * 3600 * 1000);
    const diffMin = Math.round((next - new Date()) / 60000);
    if (diffMin <= 0) return '대기 중 (다음 틱에 점검)';
    return diffMin < 60 ? `${diffMin}분 후` : `${(diffMin / 60).toFixed(1)}시간 후`;
}

export default function SourceManager() {
    const [sourcesList, setSourcesList] = useState([]);
    const [newSource, setNewSource] = useState({ name: '', url: '', category: '', interval_hours: 3 });
    const [tickMinutes, setTickMinutes] = useState(30);
    const [collapsedCategories, setCollapsedCategories] = useState(new Set());
    const [categoryFilter, setCategoryFilter] = useState(null);
    const [showFailingOnly, setShowFailingOnly] = useState(false);
    const [confirmDeleteId, setConfirmDeleteId] = useState(null);
    const [sourceCounts, setSourceCounts] = useState({});

    const fetchSources = useCallback(async () => {
        try {
            const response = await axios.get(`${API_BASE}/sources`);
            setSourcesList(response.data.sources || []);
        } catch (err) {
            console.error("소스 목록 조회 에러:", err);
        }
    }, []);

    const fetchSourceCounts = useCallback(async () => {
        try {
            const response = await axios.get(`${API_BASE}/stats/sources`);
            setSourceCounts(response.data.source_counts || {});
        } catch (err) {
            console.error("출처별 건수 조회 에러:", err);
        }
    }, []);

    useEffect(() => {
        (async () => {
            await Promise.all([fetchSources(), fetchSourceCounts()]);
            try {
                const res = await axios.get(`${API_BASE}/scheduler/config`);
                setTickMinutes(res.data.tick_minutes);
            } catch (err) {
                console.error("스케줄러 설정 조회 에러:", err);
            }
        })();
    }, [fetchSources, fetchSourceCounts]);

    const handleAddSource = async () => {
        if (!newSource.name.trim() || !newSource.url.trim()) {
            toast('이름과 URL을 입력해주세요.');
            return;
        }
        const toastId = toast.loading('소스 추가 중...');
        try {
            const response = await axios.post(`${API_BASE}/sources`, {
                name: newSource.name.trim(),
                url: newSource.url.trim(),
                category: newSource.category.trim() || null,
                source_type: 'rss',
                interval_hours: newSource.interval_hours,
            });
            toast.success(response.data.message, { id: toastId });
            setNewSource({ name: '', url: '', category: '', interval_hours: 3 });
            await fetchSources();
        } catch (err) {
            console.error("소스 추가 에러:", err);
            toast.error(err.response?.data?.detail || '소스 추가 실패', { id: toastId });
        }
    };

    const handleDeleteSource = async (sourceId) => {
        const toastId = toast.loading('삭제 중...');
        try {
            const response = await axios.delete(`${API_BASE}/sources/${sourceId}`);
            toast.success(response.data.message, { id: toastId });
            setConfirmDeleteId(null);
            await fetchSources();
        } catch (err) {
            console.error("소스 삭제 에러:", err);
            toast.error('삭제 실패', { id: toastId });
            setConfirmDeleteId(null);
        }
    };

    const handleUpdateTickMinutes = async () => {
        const toastId = toast.loading('스케줄러 간격 변경 중...');
        try {
            const res = await axios.put(`${API_BASE}/scheduler/config`, { tick_minutes: tickMinutes });
            if (res.data.warning) {
                toast(res.data.warning, { id: toastId, icon: '⚠️', duration: 7000 });
            } else {
                toast.success('변경 완료!', { id: toastId });
            }
        } catch (err) {
            console.error("스케줄러 설정 변경 에러:", err);
            toast.error('변경 실패', { id: toastId });
        }
    };

    const handleUpdateSourceInterval = async (sourceId, newHours) => {
        if (!(newHours > 0)) return;
        try {
            const res = await axios.patch(`${API_BASE}/sources/${sourceId}`, { interval_hours: newHours });
            toast.success(res.data.message);
            await fetchSources();
        } catch (err) {
            console.error("소스 주기 변경 에러:", err);
            toast.error('주기 변경 실패');
        }
    };

    return (
        <>
            <div className="scheduler-config-row">
                <span title="이 값은 '몇 시에 시계를 볼지'이고, 아래 각 소스/키워드의 주기(시간)는 '실제로 얼마 만에 한 번씩 도는지'입니다. 스케줄러 점검 간격이 가장 짧은 주기보다 크면 그 항목은 정시에 안 돕니다.">
                    스케줄러 점검 간격 (ⓘ 소스/키워드별 주기보다 짧아야 함):
                </span>
                <input
                    type="number"
                    min="1"
                    value={tickMinutes}
                    onChange={(e) => setTickMinutes(Number(e.target.value))}
                    style={{ width: '70px' }}
                />
                <span>분마다</span>
                <button onClick={handleUpdateTickMinutes} className="scheduler-config-save">저장</button>
            </div>

            <div className="source-manager-add">
                <input
                    placeholder="이름"
                    value={newSource.name}
                    onChange={(e) => setNewSource({ ...newSource, name: e.target.value })}
                />
                <input
                    placeholder="URL"
                    value={newSource.url}
                    onChange={(e) => setNewSource({ ...newSource, url: e.target.value })}
                />
                <input
                    placeholder="카테고리(선택)"
                    value={newSource.category}
                    onChange={(e) => setNewSource({ ...newSource, category: e.target.value })}
                />
                <input
                    type="number"
                    placeholder="주기(시간)"
                    value={newSource.interval_hours}
                    onChange={(e) => setNewSource({ ...newSource, interval_hours: Number(e.target.value) })}
                    style={{ width: '70px' }}
                />
                <button onClick={handleAddSource}>+ 직접 추가</button>
            </div>

            <div className="source-filter-bar">
                    <button
                        className={`source-filter-chip ${!categoryFilter && !showFailingOnly ? 'active' : ''}`}
                        onClick={() => { setCategoryFilter(null); setShowFailingOnly(false); }}
                    >
                        전체 ({sourcesList.length})
                    </button>
                    {getCategoryCounts(sourcesList).map(({ category, count }) => (
                        <button
                            key={category}
                            className={`source-filter-chip ${categoryFilter === category ? 'active' : ''}`}
                            onClick={() => {
                                setShowFailingOnly(false);
                                setCategoryFilter((prev) => (prev === category ? null : category));
                            }}
                        >
                            {category} ({count})
                        </button>
                    ))}
                    <button
                        className={`source-filter-chip source-filter-chip-warning ${showFailingOnly ? 'active' : ''}`}
                        onClick={() => {
                            setCategoryFilter(null);
                            setShowFailingOnly((prev) => !prev);
                        }}
                    >
                        ⚠️ 탈락 후보 ({sourcesList.filter(isFailingCandidate).length})
                    </button>
                </div>

                {(() => {
                    const filtered = applySourceFilters(sourcesList, categoryFilter, showFailingOnly);
                    if (filtered.length === 0) {
                        return (
                            <p style={{ margin: 0, color: '#94a3b8' }}>
                                {sourcesList.length === 0
                                    ? '등록된 소스가 없습니다.'
                                    : '이 필터에 해당하는 소스가 없습니다.'}
                            </p>
                        );
                    }

                    return (
                        <div className="genre-editor-table-wrap">
                            <table className="source-table">
                                <colgroup>
                                    <col style={{ width: '110px' }} />
                                    <col style={{ width: '22%' }} />
                                    <col style={{ width: '60px' }} />
                                    <col />
                                    <col style={{ width: '90px' }} />
                                </colgroup>
                                <thead>
                                    <tr>
                                        <th>카테고리</th>
                                        <th>소스</th>
                                        <th>건수</th>
                                        <th>출처 URL</th>
                                        <th></th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {groupSourcesByCategory(filtered).map((group) => {
                                        const isCollapsed = collapsedCategories.has(group.category);
                                        const hasFailing = group.sources.some(isFailingCandidate);

                                        const toggleCollapse = () => {
                                            setCollapsedCategories((prev) => {
                                                const next = new Set(prev);
                                                if (next.has(group.category)) {
                                                    next.delete(group.category);
                                                } else {
                                                    next.add(group.category);
                                                }
                                                return next;
                                            });
                                        };

                                        if (isCollapsed) {
                                            return (
                                                <tr key={group.category} className="source-table-category-row-collapsed">
                                                    <td
                                                        className="source-table-category"
                                                        colSpan={5}
                                                        onClick={toggleCollapse}
                                                        style={{ cursor: 'pointer' }}
                                                    >
                                                        <span className="source-table-category-toggle">▶</span>
                                                        {group.category}
                                                        {isSensitiveCategory(group) && (
                                                            <span
                                                                className="source-table-sensitive-badge"
                                                                title="민감 카테고리 — 개인화 프로필에서 결론 유도가 아닌 정보 필터링 용도로만 사용"
                                                            >
                                                                ⚠️
                                                            </span>
                                                        )}
                                                        {hasFailing && (
                                                            <span
                                                                className="source-table-category-failing-badge"
                                                                title="이 카테고리에 탈락 후보(연속 3회 이상 실패)가 포함되어 있습니다"
                                                            >
                                                                ⚠️
                                                            </span>
                                                        )}
                                                        <span className="source-table-category-count">
                                                            {group.sources.length}개 · 펼치려면 클릭
                                                        </span>
                                                    </td>
                                                </tr>
                                            );
                                        }

                                        return group.sources.map((src, idx) => (
                                            <tr
                                                key={src.id}
                                                className={src.status === 'failing' ? 'source-row-failing' : ''}
                                            >
                                                {idx === 0 && (
                                                    <td
                                                        className="source-table-category"
                                                        rowSpan={group.sources.length}
                                                        onClick={toggleCollapse}
                                                        style={{ cursor: 'pointer' }}
                                                    >
                                                        <span className="source-table-category-toggle">▼</span>
                                                        {group.category}
                                                        {isSensitiveCategory(group) && (
                                                            <span
                                                                className="source-table-sensitive-badge"
                                                                title="민감 카테고리 — 개인화 프로필에서 결론 유도가 아닌 정보 필터링 용도로만 사용"
                                                            >
                                                                ⚠️
                                                            </span>
                                                        )}
                                                        {hasFailing && (
                                                            <span
                                                                className="source-table-category-failing-badge"
                                                                title="이 카테고리에 탈락 후보(연속 3회 이상 실패)가 포함되어 있습니다"
                                                            >
                                                                ⚠️
                                                            </span>
                                                        )}
                                                        <span className="source-table-category-count">
                                                            {group.sources.length}개
                                                        </span>
                                                    </td>
                                                )}
                                                <td className="source-table-name">
                                                    {src.article_url ? (
                                                        <a
                                                            href={src.article_url}
                                                            target="_blank"
                                                            rel="noreferrer"
                                                            title="해당자료 URL (가장 최근 수집된 기사 원문)"
                                                        >
                                                            {src.name}
                                                        </a>
                                                    ) : (
                                                        src.name
                                                    )}
                                                    {src.status === 'failing' && (
                                                        <span className="source-row-warning">
                                                            {' '}⚠️ 연속 {src.fail_count}회 실패
                                                        </span>
                                                    )}
                                                    {src.source_type === 'blocked' && (
                                                        <span
                                                            className="source-table-block-reason"
                                                            title="크롤링이 계속 막혀 기사를 저장하지 못한 사유"
                                                        >
                                                            {' '}🚫 {src.block_reason || '차단(원인불명)'}
                                                        </span>
                                                    )}
                                                </td>
                                                <td className="source-table-count-cell">
                                                    {(sourceCounts[src.name] ?? 0)}
                                                </td>
                                                <td className="source-table-url" title={src.url}>
                                                    <a href={src.url} target="_blank" rel="noreferrer">
                                                        {src.url}
                                                    </a>
                                                </td>
                                                <td className="source-table-delete-cell">
                                                    {confirmDeleteId === src.id ? (
                                                        <span className="source-delete-confirm">
                                                            <button
                                                                onClick={() => handleDeleteSource(src.id)}
                                                                className="source-delete-confirm-yes"
                                                                title="관련 기사까지 함께 삭제되며 되돌릴 수 없습니다"
                                                            >
                                                                확인
                                                            </button>
                                                            <button
                                                                onClick={() => setConfirmDeleteId(null)}
                                                                className="source-delete-confirm-no"
                                                            >
                                                                취소
                                                            </button>
                                                        </span>
                                                    ) : (
                                                        <button
                                                            onClick={() => setConfirmDeleteId(src.id)}
                                                            className="source-row-delete"
                                                            title="이 출처를 삭제하면 지금까지 수집된 관련 기사도 함께 삭제되며, 되돌릴 수 없습니다."
                                                        >
                                                            🗑️
                                                        </button>
                                                    )}
                                                </td>
                                            </tr>
                                        ));
                                    })}
                                </tbody>
                            </table>
                        </div>
                    );
                })()}
        </>
    );
}
