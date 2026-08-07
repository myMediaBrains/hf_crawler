import { useRef, useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import MarkdownEditor from './MarkdownEditor';

// 번역 결과(영어 문장 -> 한글 문장이 줄 단위로 번갈아 나오는 포맷)에서
// 한글 문장만 걸러낸다. 코드블록(```)과 빈 줄은 구조 보존을 위해 그대로 둔다.
function extractKoreanOnly(text) {
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

        // 한글(완성형 음절)이 하나라도 포함된 줄만 남긴다 - 영어 원문 줄은 제거.
        if (/[\uAC00-\uD7A3]/.test(line)) {
            kept.push(line);
        }
    }

    // 영어 줄을 제거하고 나면 빈 줄이 3개 이상 겹칠 수 있으므로 문단 간격만 남기고 정리.
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
        onExportToVault(article.id, folder, vaultFilename || article.title, editContent);
        setShowVaultPopover(false);
        setVaultNewFolder('');
    };

    return (
        <div ref={cardRef} className="article-card">
            <div
                className="article-card-header"
                onClick={() => dispatch({ type: 'SET_EXPANDED', id: article.id, value: !isExpanded })}
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
                    <div style={{ display: 'flex', gap: '8px', margin: '12px 0' }}>
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
                                {showKoreanOnly ? '🌐 영/한 대조 보기' : '🇰🇷 한글보기'}
                            </button>
                        )}
                        <button
                            onClick={() => onToggleEdit(article)}
                            style={{ backgroundColor: isEditing ? '#6b7280' : '#3b82f6', color: 'white', border: 'none', padding: '4px 12px', borderRadius: '4px', cursor: 'pointer', fontSize: '0.8rem', fontWeight: 'bold' }}
                        >
                            {isEditing ? '❌ 취소' : '✏️ 편집'}
                        </button>
                        <button
                            onClick={() => onDeleteArticle(article.id)}
                            style={{ backgroundColor: '#ef4444', color: 'white', border: 'none', padding: '4px 12px', borderRadius: '4px', cursor: 'pointer', fontSize: '0.8rem', fontWeight: 'bold' }}
                        >
                            🗑️ 삭제
                        </button>
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

                    {isEditing ? (
                        <div>
                            <MarkdownEditor
                                key={article.id}
                                defaultValue={editContent}
                                onChange={(markdown) =>
                                    dispatch({ type: 'SET_EDIT_CONTENT', id: article.id, value: markdown })
                                }
                            />
                            <div style={{ marginTop: '12px', display: 'flex', gap: '8px', alignItems: 'flex-start', flexWrap: 'wrap' }}>
                                <button
                                    onClick={() => onSaveContent(article.id)}
                                    style={{ backgroundColor: '#10b981', color: 'white', border: 'none', padding: '6px 16px', borderRadius: '4px', cursor: 'pointer', fontSize: '0.9rem', fontWeight: 'bold' }}
                                >
                                    💾 저장하기
                                </button>
                                <button
                                    onClick={handleOpenVaultPopover}
                                    style={{ backgroundColor: '#8b5cf6', color: 'white', border: 'none', padding: '6px 16px', borderRadius: '4px', cursor: 'pointer', fontSize: '0.9rem', fontWeight: 'bold' }}
                                >
                                    📥 개인저장방에도 저장
                                </button>
                            </div>

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
                    ) : (
                        <div className="article-content-preview">
                            <ReactMarkdown remarkPlugins={[remarkGfm]} components={MarkdownComponents}>
                                {showKoreanOnly && translatedContent
                                    ? normalizeParagraphs(extractKoreanOnly(translatedContent))
                                    : showTranslation && translatedContent
                                        ? normalizeParagraphs(translatedContent)
                                        : normalizeParagraphs(article.content)}
                            </ReactMarkdown>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}
