import { useRef, useEffect, useState } from 'react';
import axios from 'axios';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import useCurrentUser from './useCurrentUser';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';

// 2026-08-09: 번역 버튼 양방향화 - wantKorean=true면 한글 줄만(기존 동작,
// 영→한 기사의 "한글보기"), false면 한글이 "없는" 줄만 남긴다(한→영 기사의
// "영어보기" - 번역된 영어 문장만 보여주고 한글 원문은 숨김).
function extractByLanguage(text, wantKorean) {
    if (!text) return '';

    const lines = text.split('\n');
    const kept = [];
    let inCodeBlock = false;

    for (const line of lines) {
        const trimmed = line.trim();

        if (trimmed.startsWith('```')) {
            inCodeBlock = !inCodeBlock;
            kept.push(line);
            continue;
        }
        if (inCodeBlock || trimmed === '') {
            kept.push(line);
            continue;
        }

        const hasKorean = /[\uAC00-\uD7A3]/.test(line);
        if (wantKorean ? hasKorean : !hasKorean) {
            kept.push(line);
        }
    }

    return kept.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}

export default function ArticleCard({
    article,
    isEditing,
    isTranslating,
    showTranslation,
    showKoreanOnly,
    isExpanded,
    translatedContent,
    progress,
    statusMessage,
    editContent,
    dispatch,
    onTranslate,
    onToggleKoreanOnly,
    onToggleEdit,
    onSaveContent,
    onDeleteArticle,
    onCleanArticle,
    onExpandArticle,
    MarkdownComponents,
    normalizeParagraphs,
    vaultFolders,
    onFetchVaultFolders,
    onExportToVault,
}) {
    const cardRef = useRef(null);
    const [showVaultPopover, setShowVaultPopover] = useState(false);
    const [vaultFolder, setVaultFolder] = useState('');
    const [vaultNewFolder, setVaultNewFolder] = useState('');
    const [vaultFilename, setVaultFilename] = useState(article.title);
    const [typoraOpened, setTyporaOpened] = useState(false);
    const [typoraBusy, setTyporaBusy] = useState(false);
    const displayName = useCurrentUser();

    // 카드가 화면(뷰포트)에서 완전히 벗어나면 자동으로 접는다.
    // 단, 편집 중(isEditing)일 때는 저장하지 않은 내용을 보존하기 위해 감시 자체를 하지 않는다.
    useEffect(() => {
        if (!isExpanded || isEditing || !cardRef.current) return;

        const observer = new IntersectionObserver(
            ([entry]) => {
                if (!entry.isIntersecting) {
                    dispatch({ type: 'SET_EXPANDED', id: article.id, value: false });
                }
            },
            { threshold: 0 }
        );

        observer.observe(cardRef.current);
        return () => observer.disconnect();
    }, [isExpanded, isEditing, article.id, dispatch]);

    const handleOpenVaultPopover = async () => {
        if (!showVaultPopover) {
            await onFetchVaultFolders();
        }
        setShowVaultPopover(!showVaultPopover);
    };

    const handleConfirmVaultExport = () => {
        const folder = vaultNewFolder.trim() || vaultFolder;
        if (!folder) {
            alert('폴더를 선택하거나 새 폴더 이름을 입력해주세요.');
            return;
        }
        onExportToVault(article.id, folder, vaultFilename || article.title, article.content, article.title, `article:${article.id}`);
        setShowVaultPopover(false);
        setVaultNewFolder('');
    };

    // Typora 편집: 백엔드가 기사 내용을 .md 파일로 저장하고 Typora를 실행한다.
    // Typora에서 저장(Cmd+S)한 뒤, '가져오기'를 눌러야 실제로 DB에 반영된다
    // (한 번 열었으면 다시 열 필요 없이 바로 '가져오기'만 눌러도 됨).
    const handleEditInTypora = async () => {
        setTyporaBusy(true);
        try {
            await axios.post(`${API_BASE}/articles/${article.id}/edit-in-typora`);
            setTyporaOpened(true);
        } catch (err) {
            console.error('Typora 편집 시작 에러:', err);
            alert('Typora를 여는 데 실패했습니다.');
        } finally {
            setTyporaBusy(false);
        }
    };

    const handleImportFromTypora = async () => {
        setTyporaBusy(true);
        try {
            const res = await axios.post(`${API_BASE}/articles/${article.id}/import-from-typora`);
            dispatch({ type: 'SET_EDIT_CONTENT', id: article.id, value: undefined });
            alert(res.data.message);
            window.location.reload();
            // 2026-08-12: 이 카드가 article 목록 상태를 직접 갖고 있지 않아서
            // (부모의 dispatch가 편집 버퍼만 관리) 가장 확실하게 최신 content를
            // 반영하는 방법은 새로고침. 부모에 fetchArticles 콜백이 이미 있다면
            // 그걸 호출하는 방식으로 나중에 더 매끄럽게 바꿀 수 있다.
        } catch (err) {
            console.error('Typora 가져오기 에러:', err);
            alert(err.response?.data?.detail || 'Typora에서 가져오는 데 실패했습니다.');
        } finally {
            setTyporaBusy(false);
        }
    };

    return (
        <div ref={cardRef} className="article-card">
            <div
                className="article-card-header"
                onClick={() => onExpandArticle(article)}
                style={{ cursor: 'pointer' }}
            >
                <h2 style={{ width: '100%' }}>{article.title}</h2>
                <p className="published-date" style={{ width: '100%' }}>
                    출처: <strong>{article.source}</strong> | 발행일: {article.published_at}
                </p>
                <span style={{ fontSize: '0.8rem', color: '#94a3b8' }}>
                    {isExpanded ? '▲ 접기' : '▼ 펼쳐서 보기 / 편집'}
                </span>
            </div>

            {isExpanded && (
                <div className="article-editor-panel">
                    <div style={{ display: 'flex', gap: '8px', margin: '12px 0', flexWrap: 'wrap', position: 'relative' }}>
                        <button
                            onClick={() => onCleanArticle(article.id)}
                            style={{ backgroundColor: '#0ea5e9', color: 'white', border: 'none', padding: '4px 12px', borderRadius: '4px', cursor: 'pointer', fontSize: '0.8rem', fontWeight: 'bold' }}
                        >
                            🧹 정제
                        </button>
                        <button
                            onClick={() => onTranslate(article.id)}
                            disabled={isTranslating}
                            style={{ backgroundColor: showTranslation ? '#0d9488' : '#14b8a6', color: 'white', border: 'none', padding: '4px 12px', borderRadius: '4px', cursor: isTranslating ? 'not-allowed' : 'pointer', fontSize: '0.8rem', fontWeight: 'bold', opacity: isTranslating ? 0.85 : 1 }}
                        >
                            {isTranslating ? `⏳ 번역 중 ${progress}%` : (showTranslation ? '📑 번역 숨기기' : '🌐 번역')}
                        </button>
                        {translatedContent && !isTranslating && !translatedContent.startsWith('❌') && (
                            <button
                            onClick={() => onToggleKoreanOnly(article.id)}
                            style={{ backgroundColor: showKoreanOnly ? '#b45309' : '#f59e0b', color: 'white', border: 'none', padding: '4px 12px', borderRadius: '4px', cursor: 'pointer', fontSize: '0.8rem', fontWeight: 'bold' }}
                        >
                            {/* 2026-08-09: 원문이 한글 기사면(번역 버튼 양방향화) "영어보기"로 라벨 전환 */}
                            {showKoreanOnly
                                ? '🌐 원문/번역 대조 보기'
                                : /[\uAC00-\uD7A3]/.test(article.content || '') ? '🇺🇸 영어보기' : '🇰🇷 한글보기'}
                        </button>
                        )}
                        <button
                            onClick={handleEditInTypora}
                            disabled={typoraBusy}
                            title="Typora에서 열어 편집합니다. 저장(Cmd+S) 후 옆의 '가져오기'를 눌러주세요."
                            style={{ backgroundColor: '#faad3f', color: 'white', border: 'none', padding: '4px 12px', borderRadius: '4px', cursor: typoraBusy ? 'not-allowed' : 'pointer', fontSize: '0.8rem', fontWeight: 'bold', opacity: typoraBusy ? 0.7 : 1 }}
                        >
                            📝 Typora 편집
                        </button>
                        {typoraOpened && (
                            <button
                                onClick={handleImportFromTypora}
                                disabled={typoraBusy}
                                title="Typora에서 저장한 내용을 다시 불러와 DB에 반영합니다."
                                style={{ backgroundColor: '#0d9488', color: 'white', border: 'none', padding: '4px 12px', borderRadius: '4px', cursor: typoraBusy ? 'not-allowed' : 'pointer', fontSize: '0.8rem', fontWeight: 'bold', opacity: typoraBusy ? 0.7 : 1 }}
                            >
                                {typoraBusy ? '⏳ 가져오는 중...' : '📥 Typora에서 가져오기'}
                            </button>
                        )}
                        <button
                            onClick={handleOpenVaultPopover}
                            style={{ backgroundColor: '#8b5cf6', color: 'white', border: 'none', padding: '4px 12px', borderRadius: '4px', cursor: 'pointer', fontSize: '0.8rem', fontWeight: 'bold' }}
                        >
                            📥 {displayName} 저장소
                        </button>
                        <button
                            onClick={() => onDeleteArticle(article.id)}
                            style={{ backgroundColor: '#ef4444', color: 'white', border: 'none', padding: '4px 12px', borderRadius: '4px', cursor: 'pointer', fontSize: '0.8rem', fontWeight: 'bold' }}
                        >
                            🗑️ 삭제
                        </button>

                        {showVaultPopover && (
                            <div className="vault-export-popover">
                                <div className="vault-export-row">
                                    <label>폴더 선택</label>
                                    <select value={vaultFolder} onChange={(e) => setVaultFolder(e.target.value)}>
                                        <option value="">-- 선택 --</option>
                                        {(vaultFolders || []).map(f => (
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

                    {isTranslating && (
                        <div className="progress-container">
                            {!translatedContent ? (
                                <>
                                    <div style={{ fontSize: '0.8rem', color: '#94a3b8', marginBottom: '4px' }}>
                                        🔍 {statusMessage || '번역을 준비하고 있습니다'}
                                        <span className="dot-pulse"></span>
                                    </div>
                                    <div className="progress-bar-indeterminate" />
                                </>
                            ) : (
                                <div style={{ fontSize: '0.8rem', color: '#14b8a6', marginBottom: '4px' }}>
                                    ✍️ 번역 중 (실시간 수신 중...)
                                </div>
                            )}
                        </div>
                    )}

                    <div className="article-content-preview">
                        <ReactMarkdown remarkPlugins={[remarkGfm]} components={MarkdownComponents}>
                            {showKoreanOnly && translatedContent
                                ? normalizeParagraphs(
                                    extractByLanguage(
                                        translatedContent,
                                        !/[\uAC00-\uD7A3]/.test(article.content || '')
                                        // 원문이 영어 기사면(true) 한글 줄만 남기고,
                                        // 원문이 한글 기사면(false) 영어 줄만 남긴다.
                                    )
                                )
                                : showTranslation && translatedContent
                                    ? normalizeParagraphs(translatedContent)
                                    : normalizeParagraphs(article.content)}
                        </ReactMarkdown>
                    </div>
                </div>
            )}
        </div>
    );
}
