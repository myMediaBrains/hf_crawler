import React, { useState, useEffect, useRef, useCallback, useReducer } from 'react';
import axios from 'axios';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { toast, Toaster } from 'react-hot-toast';
import ArticleCard from './ArticleCard';
import './App.css';

// ============================================
// 상태 관리 리듀서
// ============================================
const initialState = {
    editing: {},
    editContent: {},
    translating: {},
    translated: {},
    showTranslation: {},
    showKoreanOnly: {},
    progress: {},
    statusMessage: {},
    expanded: {}
};

function articleReducer(state, action) {
    switch (action.type) {
        case 'SET_EDITING':
            return { ...state, editing: { ...state.editing, [action.id]: action.value } };
        case 'SET_EDIT_CONTENT':
            return { ...state, editContent: { ...state.editContent, [action.id]: action.value } };
        case 'SET_TRANSLATING':
            return { ...state, translating: { ...state.translating, [action.id]: action.value } };
        case 'SET_TRANSLATED':
            return { ...state, translated: { ...state.translated, [action.id]: action.value } };
        // SSE 스트리밍 청크 누적 전용. handleTranslate의 onmessage 콜백은 연결이 열릴 때
        // 딱 한 번 생성되어 그 시점의 articleStates를 클로저로 붙잡고 있으므로,
        // "articleStates.translated[id] + chunk" 방식으로 계산하면 항상 그 시점의(오래된)
        // 값만 보고 매번 덮어쓰게 된다 (실시간 누적이 안 되던 원인). reducer 안에서
        // 항상 최신 state를 기준으로 누적해야 이 문제가 사라진다.
        case 'APPEND_TRANSLATED':
            return {
                ...state,
                translated: {
                    ...state.translated,
                    [action.id]: (state.translated[action.id] || '') + action.value
                }
            };
        case 'SET_SHOW_TRANSLATION':
            return { ...state, showTranslation: { ...state.showTranslation, [action.id]: action.value } };
        case 'SET_SHOW_KOREAN_ONLY':
            return { ...state, showKoreanOnly: { ...state.showKoreanOnly, [action.id]: action.value } };
        case 'SET_PROGRESS':
            return { ...state, progress: { ...state.progress, [action.id]: action.value } };
        case 'SET_STATUS_MESSAGE':
            return { ...state, statusMessage: { ...state.statusMessage, [action.id]: action.value } };
        case 'SET_EXPANDED':
            return { ...state, expanded: { ...state.expanded, [action.id]: action.value } };
        case 'RESET_ARTICLE_STATE':
            const newState = { ...state };
            ['editing', 'editContent', 'translating', 'translated', 'showTranslation', 'showKoreanOnly', 'progress', 'statusMessage', 'expanded'].forEach(key => {
                if (newState[key] && newState[key][action.id] !== undefined) {
                    delete newState[key][action.id];
                }
            });
            return newState;
        default:
            return state;
    }
}

// 소스 출처(origin)가 자동 승격인지 판별 - models.py의 SourceOrigin
// (MANUAL/AUTO_PROMOTED/MANUAL_ADDED) 값이 그대로 내려오므로 문자열에
// "auto"가 포함되는지로 판별한다 (대소문자 무관하게 안전히 처리).
function isAutoOrigin(origin) {
    return typeof origin === 'string' && origin.toLowerCase().includes('auto');
}

// 정치/경제 카테고리는 personalization 레이어에서 "민감 카테고리"로 다루므로
// (프로필을 결론 유도가 아니라 필터링에만 쓰는 원칙), 소스 관리 화면에서도
// 같은 기준으로 살짝 표시해준다. 카테고리 문자열 목록만 여기서 관리하면 되고,
// 새 민감 카테고리가 생기면 이 배열에 추가하기만 하면 된다.
const SENSITIVE_CATEGORIES = ['Politics', 'Economy'];

function isSensitiveCategory(category) {
    return SENSITIVE_CATEGORIES.includes(category);
}

// 소스 목록을 카테고리별로 묶는다. 카테고리가 비어있으면 "미분류"로 모으고
// 항상 맨 뒤에 배치한다. 카테고리 이름/소스 이름 모두 가나다(알파벳)순 정렬.
function groupSourcesByCategory(list) {
    const buckets = {};
    list.forEach((src) => {
        const cat = src.category && src.category.trim() ? src.category : '미분류';
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
    }));
}

// fail_count가 3 이상이면 "탈락 후보"로 취급 (SourceStatus.FAILING 판정 기준과 동일)
function isFailingCandidate(src) {
    return (src.fail_count || 0) >= 3;
}

// 카테고리 필터 / 탈락 후보 필터를 적용한 목록을 반환한다.
// 두 필터는 상호 배타적으로 쓰인다 (동시에 켜면 어느 한쪽이 무시된 것처럼
// 보여 혼란스러우므로, 호출부에서 토글 시 반대쪽을 항상 꺼준다).
function applySourceFilters(list, categoryFilter, showFailingOnly) {
    if (showFailingOnly) {
        return list.filter(isFailingCandidate);
    }
    if (categoryFilter) {
        return list.filter((src) => {
            const cat = src.category && src.category.trim() ? src.category : '미분류';
            return cat === categoryFilter;
        });
    }
    return list;
}

// 필터 칩에 표시할 "카테고리별 개수"는 항상 전체 목록 기준으로 계산한다
// (필터를 걸어도 다른 카테고리 버튼의 숫자가 사라지면 안 되기 때문).
function getCategoryCounts(list) {
    const counts = {};
    list.forEach((src) => {
        const cat = src.category && src.category.trim() ? src.category : '미분류';
        counts[cat] = (counts[cat] || 0) + 1;
    });
    const names = Object.keys(counts).sort((a, b) => {
        if (a === '미분류') return 1;
        if (b === '미분류') return -1;
        return a.localeCompare(b);
    });
    return names.map((category) => ({ category, count: counts[category] }));
}


// ============================================
// 메인 App 컴포넌트
// ============================================
function App() {
    // ✅ Vite 환경 변수 사용
    const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';
    const POLLING_INTERVAL = parseInt(import.meta.env.VITE_SYSTEM_POLLING_INTERVAL) || 2000;
    const MAX_RETRIES = parseInt(import.meta.env.VITE_TRANSLATION_RETRY_MAX) || 3;
    const RETRY_DELAY = parseInt(import.meta.env.VITE_TRANSLATION_RETRY_DELAY) || 2000;
    
    // 상태 관리
    const [articles, setArticles] = useState([]);
    const [keyword, setKeyword] = useState('');
    const [loading, setLoading] = useState(false);
    const [message, setMessage] = useState('');
    const [keywordStats, setKeywordStats] = useState({});
    

    // '검색주기설정' 옆 상시 노출 입력창 2개 - 현시점 기준 최근 몇 개월 자료를 가져올지,
    // 몇 시간마다 백그라운드로 재수집할지. 예전엔 ⚙ 토글 버튼을 눌러야 나오는
    // 팝오버였는데 없애고 상시 노출로 단순화, 이후 '실시간 수집'이 즉시 수집을
    // 전담하면서 잠깐 months_back을 뺐다가 다시 복원함 (8/7 세션 후반).
    const [monthsBack, setMonthsBack] = useState(1);
    const [intervalHours, setIntervalHours] = useState(24);
    const [showIntervalPopover, setShowIntervalPopover] = useState(false);

    // 소스 관리 패널
    const [showSourceManager, setShowSourceManager] = useState(false);
    const [sourcesList, setSourcesList] = useState([]);
    const [newSource, setNewSource] = useState({ name: '', url: '', category: '', interval_hours: 3 });
    const [tickMinutes, setTickMinutes] = useState(30);

    // 소스 관리 테이블 - 접기/펼치기 + 필터 상태
    const [collapsedCategories, setCollapsedCategories] = useState(new Set());
    const [categoryFilter, setCategoryFilter] = useState(null);   // null = 전체
    const [showFailingOnly, setShowFailingOnly] = useState(false); // 탈락 후보만 보기

    // 삭제 버튼 - 브라우저 confirm() 대신 인라인 확인/취소. 한 번에 하나의 행만
    // 확인 상태를 가지도록 id 하나만 저장한다 (다른 행 삭제 버튼을 누르면 자동 전환).
    const [confirmDeleteId, setConfirmDeleteId] = useState(null);

    // 출처별 수집 건수 (기존 "출처 보기" 패널의 데이터를 출처 관리 테이블에 통합)
    const [sourceCounts, setSourceCounts] = useState({}); // { [소스이름]: 건수 }

    // 키워드 관리(삭제) 패널 - "키워드별 현황"의 "전체 보기" 버튼에서 연다.
    const [showKeywordManager, setShowKeywordManager] = useState(false);
    const [registeredKeywords, setRegisteredKeywords] = useState([]);
    // 삭제 버튼 클릭 시 브라우저 기본 confirm() 대신, 그 자리 바로 옆에 확인/취소를
    // 인라인으로 보여주기 위한 상태 - 지금 확인 중인 키워드 id 하나만 기억한다.
    const [confirmDeleteKeywordId, setConfirmDeleteKeywordId] = useState(null);

    // 재클릭 시 진행 중인 작업을 중단하기 위한 개별 상태/컨트롤러.
    // 예전엔 loading 하나를 모든 버튼이 공유해서, 하나가 실행 중이면 나머지
    // 버튼까지 전부 disabled로 잠겨 "눌러도 반응이 없는" 것처럼 보였다.
    // 이제 액션별로 독립된 pending 상태를 쓰고, 시간이 걸리는 실시간 수집은
    // AbortController + 백엔드 /collect/cancel로 실제 중단도 지원한다.
    const pipelineControllerRef = useRef(null);
    const [pipelinePending, setPipelinePending] = useState(false);

    // 개인저장방(Vault)
    const [vaultFolders, setVaultFolders] = useState([]);

    const [showPlatformInfo, setShowPlatformInfo] = useState(false);
    const [platformInfo, setPlatformInfo] = useState(null);

    const [systemStats, setSystemStats] = useState({
        cpu_usage: '측정 중...',
        memory_usage: '측정 중...',
        gpu_usage: '측정 중...',
        db_usage: '측정 중...'
    });
    
    // 리듀서 상태
    const [articleStates, dispatch] = useReducer(articleReducer, initialState);

    // articleStates.editing이 바뀔 때마다 editingIdsRef를 최신 상태로 유지한다.
    useEffect(() => {
        editingIdsRef.current = new Set(
            Object.entries(articleStates.editing)
                .filter(([, isEditing]) => isEditing)
                .map(([id]) => Number(id))
        );
    }, [articleStates.editing]);
    
    // EventSource 참조 관리
    const eventSourceRef = useRef({});
    
    // EventSource 참조 관리
    const pinnedArticleIdRef = useRef(null);

    // 지금 편집 중인 기사 id 집합 - fetchArticles()가 다른 키워드 필터로 목록을
    // 새로 받아와도, 편집 중인 기사가 그 필터에 안 걸려 화면에서 사라지는 일이
    // 없도록 하기 위한 참조. 아래 useEffect가 articleStates.editing이 바뀔 때마다
    // 최신값으로 동기화한다.
    const editingIdsRef = useRef(new Set());

    const moveIdToTop = (list, id) => {
        if (!id) return list;
        const idx = list.findIndex((a) => a.id === id);
        if (idx <= 0) return list; // 이미 최상단이거나 목록에 없으면 그대로
        const target = list[idx];
        const rest = list.filter((_, i) => i !== idx);
        return [target, ...rest];
    };

    // 정제/번역/편집 버튼 클릭 시 호출 - 해당 문서를 즉시 최상단으로 옮기고,
    // 이후 fetchArticles()가 재호출돼도 계속 최상단에 유지되도록 pin해둔다.
    const pinArticleToTop = useCallback((articleId) => {
        pinnedArticleIdRef.current = articleId;
        setArticles((prev) => moveIdToTop(prev, articleId));
    }, []);

    // ============================================
    // API 호출 함수
    // ============================================
    const fetchArticles = useCallback(async (targetKeyword = '') => {
        try {
            const url = targetKeyword.trim()
                ? `${API_URL}/articles?keyword=${encodeURIComponent(targetKeyword.trim())}`
                : `${API_URL}/articles`;
            
            const response = await axios.get(url);
            const fetched = response.data.articles || [];

            setArticles((prev) => {
                const fetchedIds = new Set(fetched.map((a) => a.id));
                // 지금 편집 중인 기사가 이번 검색 필터에 안 걸려 새 목록에서
                // 빠지더라도, 저장하지 않은 편집 내용을 잃지 않도록 이전 목록에서
                // 그대로 가져와 뒤에 붙여둔다 - 실시간 수집이 끝나 목록이 갱신돼도
                // 편집 작업이 화면에서 사라지지 않게 하기 위함.
                const editingButMissing = prev.filter(
                    (a) => editingIdsRef.current.has(a.id) && !fetchedIds.has(a.id)
                );
                const merged = editingButMissing.length > 0 ? [...fetched, ...editingButMissing] : fetched;
                return moveIdToTop(merged, pinnedArticleIdRef.current);
            });
        } catch (err) {
            console.error("아티클 조회 에러:", err);
            toast.error('아티클을 불러오는데 실패했습니다.');
        }
    }, [API_URL]);

    const fetchKeywordStats = useCallback(async () => {
        try {
            const response = await axios.get(`${API_URL}/stats/keywords`);
            setKeywordStats(response.data.keyword_stats || {});
        } catch (err) {
            console.error("키워드 통계 조회 에러:", err);
        }
    }, [API_URL]);

    const fetchSystemStats = useCallback(async () => {
        try {
            const response = await axios.get(`${API_URL}/stats/system`);
            if (response.data.status === 'success') {
                setSystemStats(prev => {
                    const newStats = {
                        cpu_usage: response.data.cpu_usage,
                        memory_usage: response.data.memory_usage,
                        gpu_usage: response.data.gpu_usage,
                        db_usage: response.data.db_usage,
                        activity: response.data.activity || { requests: [], components: {} }
                    };
                    return JSON.stringify(prev) !== JSON.stringify(newStats) ? newStats : prev;
                });
            }
        } catch (err) {
            console.error("시스템 자원 조회 에러:", err);
        }
    }, [API_URL]);


    const fetchSources = useCallback(async () => {
        try {
            const response = await axios.get(`${API_URL}/sources`);
            setSourcesList(response.data.sources || []);
        } catch (err) {
            console.error("소스 목록 조회 에러:", err);
        }
    }, [API_URL]);

    const fetchVaultFolders = useCallback(async () => {
        try {
            const response = await axios.get(`${API_URL}/vault/folders`);
            setVaultFolders(response.data.folders || []);
        } catch (err) {
            console.error("볼트 폴더 조회 에러:", err);
        }
    }, [API_URL]);

    const handleTogglePlatformInfo = async () => {
        if (showPlatformInfo) {
            setShowPlatformInfo(false);
            return;
        }
        if (!platformInfo) {
            try {
                const response = await axios.get(`${API_URL}/platform/info`);
                setPlatformInfo(response.data);
            } catch (err) {
                console.error("플랫폼 정보 조회 에러:", err);
                toast.error('구성요소 정보를 불러오지 못했습니다.');
                return;
            }
        }
        setShowPlatformInfo(true);
    };

    // ============================================
    // useEffect 훅
    // ============================================
    useEffect(() => {
        fetchArticles();
        fetchKeywordStats();
        fetchSystemStats();

        const statsInterval = setInterval(() => {
            fetchSystemStats();
        }, POLLING_INTERVAL);

        // 키워드별 현황은 초 단위로 자주 바뀔 필요는 없으니 조금 더 긴 주기로 폴링
        // (너무 짧으면 백엔드에 불필요한 부하)
        const keywordStatsInterval = setInterval(() => {
            fetchKeywordStats();
        }, POLLING_INTERVAL * 5);

        return () => {
            clearInterval(statsInterval);
            clearInterval(keywordStatsInterval);
            Object.values(eventSourceRef.current).forEach(es => {
                if (es) es.close();
            });
        };
    }, [fetchArticles, fetchKeywordStats, fetchSystemStats, POLLING_INTERVAL]);

    // ============================================
    // 이벤트 핸들러
    // ============================================
    const handleCollectPipeline = async () => {
        // 이미 진행 중이면: 재클릭은 "중단"으로 동작한다.
        if (pipelineControllerRef.current) {
            pipelineControllerRef.current.abort();
            pipelineControllerRef.current = null;
            try { await axios.post(`${API_URL}/collect/cancel`); } catch (_) { /* 취소 요청 실패는 무시 */ }
            setPipelinePending(false);
            toast.dismiss();
            toast('파이프라인 수집을 중단했습니다.');
            // 토스트는 몇 초 뒤 사라지지만, 화면에 계속 남는 고정 메시지 박스(message)는
            // 여기서 갱신해주지 않으면 시작할 때 찍힌 "점검하고 있습니다..." 문구가
            // 영원히 남아있게 된다 (8/7 세션에서 발견된 버그) - 함께 갱신한다.
            setMessage('⏹ 파이프라인 수집을 사용자 요청으로 중단했습니다.');
            return;
        }

        const targetKeyword = keyword.trim();
        const controller = new AbortController();
        pipelineControllerRef.current = controller;
        setPipelinePending(true);

        const toastId = toast.loading(
            targetKeyword ? `'${targetKeyword}' 키워드 수집 중... (다시 누르면 중단)` : '파이프라인 수집 중... (다시 누르면 중단)'
        );
        setMessage(
            targetKeyword
                ? `'${targetKeyword}' 키워드만 점검하고 있습니다...`
                : '파이프라인 가동 중: 등록된 소스와 키워드를 점검하고 있습니다...'
        );

        try {
            const url = targetKeyword
                ? `${API_URL}/collect/deep-incremental?keyword=${encodeURIComponent(targetKeyword)}&months_back=${monthsBack}&interval_hours=${intervalHours}`
                : `${API_URL}/collect/deep-incremental`;

            const response = await axios.get(url, { signal: controller.signal });
            const detail = response.data.detail || {};

            const detailMsg = targetKeyword
                ? response.data.message  // 백엔드가 상황(신규 등록/재수집)에 맞는 메시지를 만들어 보내줌
                : `✨ 파이프라인 수집 완료! (총 신규: ${response.data.total_count}건)\n` +
                `• 고정 소스: ${detail.sources_checked ?? 0}건 점검 (신규 ${detail.sources_new_articles ?? 0}건)\n` +
                `• 키워드: ${detail.keywords_checked ?? 0}건 점검 (신규 ${detail.keywords_new_articles ?? 0}건)`;

            setMessage(detailMsg);
            toast.success(response.data.message || `수집 완료! ${response.data.total_count}건 추가됨`, { id: toastId });
            await fetchArticles(keyword);
            await fetchKeywordStats();
            await fetchSystemStats();
        } catch (err) {
            if (axios.isCancel(err) || err.code === 'ERR_CANCELED') {
                return; // 위에서 이미 중단 안내 토스트를 띄웠음
            }
            if (err.response?.status === 404) {
                toast.error(err.response.data?.detail || '등록되지 않은 키워드입니다.', { id: toastId });
                setMessage(err.response.data?.detail || '등록되지 않은 키워드입니다.');
            } else if (err.response?.status === 409) {
                // 다른 수집 작업(예: DB 재생성 직후 대량 초기 수집)이 이미 진행 중이라 백엔드가
                // 거부한 것 - 에러라기보다 "잠깐 기다려야 함" 안내에 가깝다. 사용자가 이 타이밍을
                // 놓쳐도 알아서 처리되도록 3초 후 자동으로 한 번 더 시도한다.
                const msg = err.response.data?.detail || '다른 수집 작업이 진행 중입니다.';
                toast(`${msg} 3초 후 자동으로 다시 시도합니다.`, { id: toastId, icon: '⏳', duration: 3500 });
                setMessage(`${msg} 3초 후 자동으로 다시 시도합니다.`);
                setTimeout(() => { handleCollectPipeline(); }, 3000);
            } else {
                console.error("파이프라인 수집 에러:", err);
                setMessage('파이프라인 수집 중 에러가 발생했습니다.');
                toast.error('수집 중 오류가 발생했습니다.', { id: toastId });
            }
        } finally {
            pipelineControllerRef.current = null;
            setPipelinePending(false);
        }
    };

    // 출처별 수집 건수 조회 - 예전엔 별도 "출처 보기" 패널용이었지만,
    // 이제 출처 관리 테이블을 열 때 함께 불러와서 건수 열에 바로 채운다.
    const fetchSourceCounts = useCallback(async () => {
        try {
            const response = await axios.get(`${API_URL}/stats/sources`);
            setSourceCounts(response.data.source_counts || {});
        } catch (err) {
            console.error("출처별 건수 조회 에러:", err);
        }
    }, [API_URL]);

    const handleCleanExisting = async () => {
        const toastId = toast.loading('데이터 정제 중...');
        setLoading(true);
        setMessage('기존 저장된 데이터의 노이즈를 일괄 정제하는 중입니다...');
        
        try {
            const response = await axios.get(`${API_URL}/clean-existing-articles`);
            setMessage(response.data.message);
            toast.success('데이터 정제 완료!', { id: toastId });
            await fetchArticles(keyword);
            await fetchKeywordStats();
            await fetchSystemStats();
        } catch (err) {
            console.error("정제 에러:", err);
            setMessage('데이터 정제 중 에러가 발생했습니다.');
            toast.error('정제 중 오류 발생', { id: toastId });
        } finally {
            setLoading(false);
        }
    };

    const handleCleanSingleArticle = async (articleId) => {
        pinArticleToTop(articleId);
        const toastId = toast.loading('정제 중...');
        try {
            const response = await axios.post(`${API_URL}/articles/${articleId}/clean`);
            if (response.data.status === 'success') {
                toast.success(`정제 완료! ${response.data.removed_chars}자 제거됨`, { id: toastId });
                dispatch({ type: 'SET_TRANSLATED', id: articleId, value: null });
                dispatch({ type: 'SET_SHOW_TRANSLATION', id: articleId, value: false });
                await fetchArticles(keyword);
            }
        } catch (err) {
            console.error("개별 정제 에러:", err);
            toast.error('정제 실패');
        }
    };

    const handleDeleteArticle = async (articleId) => {
        const toastId = toast.loading('삭제 중...');
        try {
            const response = await axios.delete(`${API_URL}/articles/${articleId}`);
            setMessage(response.data.message);
            toast.success('삭제 완료!', { id: toastId });

            if (pinnedArticleIdRef.current === articleId) {
                pinnedArticleIdRef.current = null;
            }
            dispatch({ type: 'RESET_ARTICLE_STATE', id: articleId });
            await fetchArticles(keyword);
            await fetchKeywordStats();
            await fetchSystemStats();
        } catch (err) {
            console.error("삭제 에러:", err);
            setMessage('기사 삭제 중 에러가 발생했습니다.');
            toast.error('삭제 실패', { id: toastId });
        }
    };

    const handleToggleEdit = (article) => {
        pinArticleToTop(article.id);
        const isEditing = !!articleStates.editing[article.id];
        if (!isEditing) {
            dispatch({ type: 'SET_EDIT_CONTENT', id: article.id, value: article.content });
        }
        dispatch({ type: 'SET_EDITING', id: article.id, value: !isEditing });
    };

    const handleSaveContent = async (articleId) => {
        const newContent = articleStates.editContent[articleId];
        const toastId = toast.loading('저장 중...');
        
        try {
            const response = await axios.put(`${API_URL}/articles/${articleId}/content`, {
                new_content: newContent
            });
            
            if (response.data.status === 'success') {
                setMessage(response.data.message);
                toast.success('저장 완료!', { id: toastId });
                dispatch({ type: 'SET_EDITING', id: articleId, value: false });
                
                dispatch({ type: 'SET_TRANSLATED', id: articleId, value: null });
                dispatch({ type: 'SET_SHOW_TRANSLATION', id: articleId, value: false });
                dispatch({ type: 'SET_PROGRESS', id: articleId, value: 0 });
                
                await fetchArticles(keyword);
            }
        } catch (err) {
            console.error("내용 수정 에러:", err);
            setMessage('기사 내용 수정 중 에러가 발생했습니다.');
            toast.error('저장 실패', { id: toastId });
        }
    };



    // 🌐 번역 버튼: 이중언어(영/한 대조) 보기를 켜고 끈다. 한글보기 모드였다면 항상
    // 이중언어 보기로 되돌린다 - 한글보기는 별도의 🇰🇷 한글보기 버튼으로만 켠다.
    const handleTranslate = (articleId, retryCount = 0) => {
        pinArticleToTop(articleId);
        if (articleStates.translated[articleId] && 
            !articleStates.translating[articleId] && 
            !articleStates.translated[articleId].startsWith('❌')) {
            dispatch({ type: 'SET_SHOW_KOREAN_ONLY', id: articleId, value: false });
            dispatch({ type: 'SET_SHOW_TRANSLATION', id: articleId, value: !articleStates.showTranslation[articleId] });
            return;
        }

        if (eventSourceRef.current[articleId]) {
            eventSourceRef.current[articleId].close();
            delete eventSourceRef.current[articleId];
        }

        dispatch({ type: 'SET_TRANSLATING', id: articleId, value: true });
        dispatch({ type: 'SET_PROGRESS', id: articleId, value: 0 });
        dispatch({ type: 'SET_STATUS_MESSAGE', id: articleId, value: null });
        dispatch({ type: 'SET_SHOW_TRANSLATION', id: articleId, value: true });
        dispatch({ type: 'SET_SHOW_KOREAN_ONLY', id: articleId, value: false });
        dispatch({ type: 'SET_TRANSLATED', id: articleId, value: '' });

        const eventSource = new EventSource(
            `${API_URL}/articles/${articleId}/study-translate-stream?mode=literal`
        );
        eventSourceRef.current[articleId] = eventSource;

        eventSource.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);

                if (data.status === 'thinking') {
                    dispatch({ type: 'SET_STATUS_MESSAGE', id: articleId, value: data.message });
                } else if (data.status === 'processing') {
                    dispatch({ type: 'SET_STATUS_MESSAGE', id: articleId, value: null });
                    dispatch({ type: 'SET_PROGRESS', id: articleId, value: data.progress });

                    if (data.chunk) {
                        dispatch({
                            type: 'APPEND_TRANSLATED',
                            id: articleId,
                            value: data.chunk
                        });
                    }
                } else if (data.status === 'completed') {
                    dispatch({ type: 'SET_PROGRESS', id: articleId, value: 100 });
                    if (data.translated_content) {
                        dispatch({ type: 'SET_TRANSLATED', id: articleId, value: data.translated_content });
                    }
                    dispatch({ type: 'SET_TRANSLATING', id: articleId, value: false });
                    toast.success('번역 완료!');
                    eventSource.close();
                    delete eventSourceRef.current[articleId];
                } else if (data.status === 'error') {
                    console.error("번역 처리 오류:", data.message);
                    dispatch({ type: 'SET_TRANSLATED', id: articleId, value: `❌ 번역 오류: ${data.message}` });
                    dispatch({ type: 'SET_TRANSLATING', id: articleId, value: false });
                    toast.error('번역 중 오류 발생');
                    eventSource.close();
                    delete eventSourceRef.current[articleId];
                }
            } catch (err) {
                console.error("SSE 파싱 에러:", err);
            }
        };

        eventSource.onerror = (err) => {
            console.error("EventSource 연결 오류:", err);
            eventSource.close();
            delete eventSourceRef.current[articleId];
            
            if (retryCount < MAX_RETRIES) {
                setMessage(`⚠️ 연결 재시도 중... (${retryCount + 1}/${MAX_RETRIES})`);
                toast.loading(`재연결 시도 ${retryCount + 1}/${MAX_RETRIES}`, { duration: 2000 });
                setTimeout(() => {
                    handleTranslate(articleId, retryCount + 1);
                }, RETRY_DELAY);
            } else {
                setMessage('❌ 최대 재시도 횟수 초과. 서버 상태를 확인하세요.');
                toast.error('번역 연결 실패');
                dispatch({ type: 'SET_TRANSLATING', id: articleId, value: false });
                dispatch({ type: 'SET_SHOW_TRANSLATION', id: articleId, value: false });
            }
        };
    };

    // 🇰🇷 한글보기 버튼: 번역이 완료된 문서에서 한글 문장만 걸러서 보여준다.
    // 켤 때는 번역 보기 자체도 함께 켜준다 (원문만 보고 있던 상태에서 눌러도 바로 보이도록).
    const handleToggleKoreanOnly = (articleId) => {
        pinArticleToTop(articleId);
        const next = !articleStates.showKoreanOnly[articleId];
        dispatch({ type: 'SET_SHOW_KOREAN_ONLY', id: articleId, value: next });
        if (next) {
            dispatch({ type: 'SET_SHOW_TRANSLATION', id: articleId, value: true });
        }
    };

    // 검색어 입력창에서 Enter를 누르면 바로 옆의 '실시간 수집'이 실행되도록 - 인접한
    // 두 요소를 하나의 폼으로 묶어서 Enter 키 동작을 자연스럽게 만든다.
    const handleRealtimeSubmit = (e) => {
        e.preventDefault();
        handleCollectPipeline();
    };

    // '검색주기설정' 버튼: 클릭하면 개월/시간 입력창(팝오버)이 나타나고, 그 안에서
    // 저장해야 실제로 반영된다. 즉시 수집은 하지 않고, 키워드를 등록하거나(없으면)
    // 이미 있으면 개월수/검색 주기만 갱신한다. 지금 당장 수집하고 싶으면
    // '실시간 수집' 버튼을 쓰면 된다 - 두 버튼의 역할을 명확히 분리 (8/7 세션 후반).
    // DB 쓰기 한 번뿐이라 오래 걸리지 않으므로 취소 로직은 두지 않았다.
    const handleSetSearchInterval = async (e) => {
        e.preventDefault();
        if (!keyword.trim()) {
            toast('검색어를 입력해주세요.');
            return;
        }

        const toastId = toast.loading(`'${keyword}' 검색 주기 설정 중...`);
        try {
            const response = await axios.put(`${API_URL}/keywords/interval`, {
                name: keyword.trim(),
                months_back: monthsBack,
                interval_hours: intervalHours,
            });
            toast.success(response.data.message, { id: toastId });
            setShowIntervalPopover(false);
            await fetchKeywordStats();
            if (showKeywordManager) {
                await fetchRegisteredKeywords();
            }
        } catch (err) {
            console.error("검색 주기 설정 에러:", err);
            toast.error('검색 주기 설정 중 오류가 발생했습니다.', { id: toastId });
        }
    };

    // 검색창에 이미 등록된 키워드 이름을 입력하고 포커스를 벗어나면, 그 키워드의
    // 기존 설정(최근 N개월/N시간 주기)을 불러와 입력창에 미리 채워준다.
    // "검색주기설정을 다시 눌러서 수정한다"는 게, 지금 어떤 값으로 돼있는지도
    // 모른 채 덮어쓰는 게 아니라 기존 값을 보고 조정할 수 있게 하기 위함.
    const handleKeywordInputBlur = async () => {
        const name = keyword.trim();
        if (!name) return;
        try {
            const res = await axios.get(`${API_URL}/keywords`);
            const existing = (res.data.keywords || []).find(k => k.name === name);
            if (existing) {
                setMonthsBack(existing.months_back);
                setIntervalHours(existing.interval_hours);
            }
        } catch (_) {
            // 불러오기는 부가 기능이라 실패해도 조용히 넘어감 (입력 자체는 계속 가능해야 함)
        }
    };

    // 등록된 키워드 전체 목록 (키워드 관리 패널용)
    const fetchRegisteredKeywords = useCallback(async () => {
        try {
            const response = await axios.get(`${API_URL}/keywords`);
            setRegisteredKeywords(response.data.keywords || []);
        } catch (err) {
            console.error("키워드 목록 조회 에러:", err);
        }
    }, [API_URL]);

    // 키워드 삭제 - 등록 취소와 함께, 그 키워드로 수집된 기사도 백엔드에서 전부
    // 함께 삭제된다 (8/7 세션 후반 - 이전엔 기사를 남겼으나 완전 삭제로 변경).
    // 브라우저 기본 confirm() 팝업 대신, 삭제 버튼 자리에 바로 확인/취소가 나오도록
    // 2단계로 나눔: 🗑️ 클릭 -> 확인/취소 인라인 노출 -> 확인 클릭 시 실제 삭제.
    const handleRequestDeleteKeyword = (keywordId) => {
        setConfirmDeleteKeywordId(keywordId);
    };

    const handleCancelDeleteKeyword = () => {
        setConfirmDeleteKeywordId(null);
    };

    const handleConfirmDeleteKeyword = async (keywordId) => {
        setConfirmDeleteKeywordId(null);
        const toastId = toast.loading('키워드와 관련 기사 삭제 중...');
        try {
            const response = await axios.delete(`${API_URL}/keywords/${keywordId}`);
            toast.success(response.data.message, { id: toastId });
            await fetchRegisteredKeywords();
            await fetchKeywordStats();
            await fetchArticles(keyword);
        } catch (err) {
            console.error("키워드 삭제 에러:", err);
            toast.error('삭제 실패', { id: toastId });
        }
    };

    // 키워드 관리 패널의 "게시 날짜" 컬럼 - 그 키워드로 모은 기사들의 published_at 중
    // 가장 이른 날짜 ~ 가장 늦은 날짜를 "YYYY.MM.DD ~ YYYY.MM.DD" 형태로 보여준다.
    const formatKeywordDateRange = (earliestIso, latestIso) => {
        if (!earliestIso || !latestIso) return '날짜 정보 없음';
        const fmt = (iso) => {
            const d = new Date(iso);
            return `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, '0')}.${String(d.getDate()).padStart(2, '0')}`;
        };
        const start = fmt(earliestIso);
        const end = fmt(latestIso);
        return start === end ? start : `${start} ~ ${end}`;
    };

    // "전체 보기" 버튼: 필터를 초기화하는 기존 기능 + 등록된 키워드를 관리(삭제)할
    // 수 있는 패널을 여닫는 기능을 함께 담당한다 (8/7 세션 후반 요청 반영).
    const handleToggleAllView = async () => {
        setKeyword('');
        await fetchArticles('');
        setConfirmDeleteKeywordId(null);

        if (showKeywordManager) {
            setShowKeywordManager(false);
            return;
        }
        await fetchRegisteredKeywords();
        setShowKeywordManager(true);
    };

    const handleStatClick = async (targetKw) => {
        setKeyword(targetKw);
        setLoading(true);
        await fetchArticles(targetKw);
        setLoading(false);
    };

    const handleToggleSourceManager = async () => {
        if (showSourceManager) {
            setShowSourceManager(false);
            setConfirmDeleteId(null);
            return;
        }
        await Promise.all([fetchSources(), fetchSourceCounts()]);
        try {
            const res = await axios.get(`${API_URL}/scheduler/config`);
            setTickMinutes(res.data.tick_minutes);
        } catch (err) {
            console.error("스케줄러 설정 조회 에러:", err);
        }
        setShowSourceManager(true);
    };

    const handleAddSource = async () => {
        if (!newSource.name.trim() || !newSource.url.trim()) {
            toast('이름과 URL을 입력해주세요.');
            return;
        }
        const toastId = toast.loading('소스 추가 중...');
        try {
            const response = await axios.post(`${API_URL}/sources`, {
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
        // 확인은 이제 버튼 클릭 시점(인라인 UI)에서 이미 끝난 상태이므로
        // 여기서는 실제 삭제만 수행한다. window.confirm은 더 이상 쓰지 않는다.
        const toastId = toast.loading('삭제 중...');
        try {
            const response = await axios.delete(`${API_URL}/sources/${sourceId}`);
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
            const res = await axios.put(`${API_URL}/scheduler/config`, { tick_minutes: tickMinutes });
            if (res.data.warning) {
                // 가장 짧은 소스/키워드 주기보다 스케줄러 점검 간격이 더 길면,
                // 그 항목은 설정한 주기대로 안 돌기 때문에 경고를 보여준다.
                toast(res.data.warning, { id: toastId, icon: '⚠️', duration: 7000 });
            } else {
                toast.success('변경 완료!', { id: toastId });
            }
        } catch (err) {
            console.error("스케줄러 설정 변경 에러:", err);
            toast.error('변경 실패', { id: toastId });
        }
    };

    // 소스 하나의 점검 주기(시간)를 변경한다. 소스관리 패널의 각 행에서 인라인으로 편집.
    const handleUpdateSourceInterval = async (sourceId, newHours) => {
        if (!(newHours > 0)) return;
        try {
            const res = await axios.patch(`${API_URL}/sources/${sourceId}`, { interval_hours: newHours });
            toast.success(res.data.message);
            await fetchSources();
        } catch (err) {
            console.error("소스 주기 변경 에러:", err);
            toast.error('주기 변경 실패');
        }
    };

    // 마지막 점검 시각 + 점검 주기(시간)로 "다음 점검까지"를 계산해서 보여준다.
    // 스케줄러 점검 간격(tick_minutes)/소스 주기/키워드 주기가 실제로 어떻게
    // 맞물려 도는지 화면에서 바로 체감할 수 있게 하기 위함.
    const formatNextCheck = (lastAt, hours) => {
        if (!lastAt) return '대기 중 (다음 틱에 점검)';
        const next = new Date(new Date(lastAt).getTime() + hours * 3600 * 1000);
        const diffMin = Math.round((next - new Date()) / 60000);
        if (diffMin <= 0) return '대기 중 (다음 틱에 점검)';
        return diffMin < 60 ? `${diffMin}분 후` : `${(diffMin / 60).toFixed(1)}시간 후`;
    };

    const handleExportToVault = async (articleId, folder, filename, content) => {
        const toastId = toast.loading('개인저장방에 저장 중...');
        try {
            const response = await axios.post(`${API_URL}/vault/export`, { folder, filename, content });
            toast.success(`저장 완료: ${response.data.filename}`, { id: toastId });
            await fetchVaultFolders();
        } catch (err) {
            console.error("볼트 저장 에러:", err);
            toast.error('저장 실패', { id: toastId });
        }
    };

    // Enter 키는 <form onSubmit>이 이미 네이티브로 처리하므로 별도 핸들러 불필요
    // (예전엔 onKeyDown으로 따로도 호출해서 Enter 한 번에 요청이 두 번 나가던 잠재 버그가
    // 있었음 - 8/7 세션 후반에 발견 및 제거).

    // ============================================
    // 마크다운 컴포넌트 설정
    // ============================================

    // 저장된 원문은 그대로 두고, "보여줄 때만" 문단을 보정한다.
    // 마크다운은 단일 줄바꿈(\n)을 문단 구분으로 인식하지 않아서,
    // 원문에 줄바꿈이 있어도 하나의 거대한 문단으로 뭉쳐 보이는 문제를 해결.
    const normalizeParagraphs = (text, maxParagraphChars = 300) => {
        if (!text) return text;

        // 1) 기존 단일 줄바꿈을 전부 문단 구분(빈 줄)으로 승격
        let normalized = text.replace(/\n(?!\n)/g, '\n\n');

        // 2) 그래도 한 문단이 지나치게 길면(원문에 줄바꿈 자체가 없던 경우)
        //    문장 경계 기준으로 강제 분할
        const paragraphs = normalized.split(/\n\s*\n/);
        const result = [];

        for (const para of paragraphs) {
            const trimmed = para.trim();
            if (!trimmed) continue;

            if (trimmed.length <= maxParagraphChars || trimmed.startsWith('#') || trimmed.startsWith('```')) {
                result.push(trimmed);
                continue;
            }

            const sentences = trimmed.split(/(?<=[.!?다요음됨임함습니다])\s+/);
            let chunk = '';
            for (const sentence of sentences) {
                const candidate = chunk ? `${chunk} ${sentence}` : sentence;
                if (candidate.length > maxParagraphChars && chunk) {
                    result.push(chunk);
                    chunk = sentence;
                } else {
                    chunk = candidate;
                }
            }
            if (chunk) result.push(chunk);
        }

        return result.join('\n\n');
    };
    const MarkdownComponents = {
        img: ({node, ...props}) => (
            <img
                {...props}
                style={{
                    maxWidth: '100%',
                    height: 'auto',
                    display: 'block',
                    margin: '16px auto',
                    borderRadius: '8px',
                    boxShadow: '0 4px 6px rgba(0,0,0,0.3)'
                }}
                alt={props.alt || 'Article image'}
                loading="lazy"
            />
        ),
        p: ({node, ...props}) => (
            <p 
                {...props} 
                style={{ 
                    margin: '0.8em 0', 
                    lineHeight: '1.6',
                    wordBreak: 'keep-all',
                    overflowWrap: 'break-word'
                }} 
            />
        ),
        hr: ({node, ...props}) => (
            <hr 
                {...props} 
                style={{ 
                    border: 'none', 
                    borderTop: '1px dashed #475569', 
                    margin: '20px 0' 
                }} 
            />
        ),
        table: ({node, ...props}) => (
            <div style={{ overflowX: 'auto' }}>
                <table {...props} />
            </div>
        )
    };

    // ============================================
    // 렌더링
    // ============================================
    return (
        <div className="app-container">
            <Toaster 
                position="top-right"
                toastOptions={{
                    duration: 3000,
                    style: {
                        background: '#1e293b',
                        color: '#f0f0f0',
                        border: '1px solid #334155'
                    },
                    success: {
                        iconTheme: {
                            primary: '#10b981',
                            secondary: '#f0f0f0'
                        }
                    },
                    error: {
                        iconTheme: {
                            primary: '#ef4444',
                            secondary: '#f0f0f0'
                        }
                    }
                }}
            />
            
            <header className="app-header">
                <h1>🚀 Local Trend & Deep Content Inspector</h1>
                
                <div className="system-monitor-bar">
                    <div>🖥️ <strong>CPU:</strong> <span style={{ color: '#60a5fa' }}>{systemStats.cpu_usage}</span></div>
                    <div>🎮 <strong>GPU:</strong> <span style={{ color: '#34d399' }}>{systemStats.gpu_usage}</span></div>
                    <div>🧠 <strong>메모리:</strong> <span style={{ color: '#fbbF24' }}>{systemStats.memory_usage}</span></div>
                    <div>💾 <strong>DB:</strong> <span style={{ color: '#f87171' }}>{systemStats.db_usage}</span></div>
                    <button className="platform-info-btn" onClick={handleTogglePlatformInfo}>
                        🧩 구성요소
                    </button>
                </div>

                {(() => {
                    const requests = systemStats.activity?.requests || [];
                    const components = systemStats.activity?.components || {};
                    // 컴포넌트 중 "대기 중"이 아닌(실제로 뭔가 하고 있는) 것만 배지로 노출
                    const activeComponents = Object.entries(components).filter(
                        ([, status]) => status && status !== '대기 중'
                    );
                    const badges = [
                        ...requests.map((label) => ({ key: `req-${label}`, text: label })),
                        ...activeComponents.map(([name, status]) => ({ key: `comp-${name}`, text: `${name}: ${status}` })),
                    ];

                    return badges.length > 0 ? (
                        <div className="activity-bar">
                            {badges.map((b) => (
                                <span key={b.key} className="activity-badge">
                                    <span className="activity-dot"></span>
                                    {b.text}
                                </span>
                            ))}
                        </div>
                    ) : (
                        <div className="activity-bar activity-bar-idle">대기 중 — 실행 중인 작업 없음</div>
                    );
                })()}
            </header>

            {showPlatformInfo && platformInfo && (
                <div className="platform-info-panel">
                    <h3>🧩 플랫폼 구성요소</h3>

                    <div className="platform-info-grid">
                        <div className="platform-info-block">
                            <h4>백엔드</h4>
                            <ul>
                                <li>프레임워크: {platformInfo.backend.framework}</li>
                                <li>서버: {platformInfo.backend.server}</li>
                                <li>언어: {platformInfo.backend.language}</li>
                                <li>스케줄러: {platformInfo.backend.scheduler}</li>
                            </ul>
                        </div>
                        <div className="platform-info-block">
                            <h4>프론트엔드</h4>
                            <ul>
                                <li>프레임워크: {platformInfo.frontend.framework}</li>
                                <li>에디터: {platformInfo.frontend.editor}</li>
                                <li>상태 관리: {platformInfo.frontend.state}</li>
                            </ul>
                        </div>
                        <div className="platform-info-block">
                            <h4>데이터베이스</h4>
                            <ul>
                                <li>엔진: {platformInfo.database.engine}</li>
                                <li>ORM: {platformInfo.database.orm}</li>
                                <li>파일: {platformInfo.database.file}</li>
                                <li>테이블: {platformInfo.database.tables.join(', ')}</li>
                            </ul>
                        </div>
                        <div className="platform-info-block">
                            <h4>LLM</h4>
                            <ul>
                                <li>런타임: {platformInfo.llm.runtime}</li>
                                <li>경량 티어: {platformInfo.llm.light_tier}</li>
                                <li>고품질 티어: {platformInfo.llm.heavy_tier}</li>
                                <li>라우터: {platformInfo.llm.router}</li>
                            </ul>
                        </div>
                        <div className="platform-info-block">
                            <h4>데이터 수집</h4>
                            <ul>
                                <li>패턴: {platformInfo.collection.pattern}</li>
                                <li>구현됨: {platformInfo.collection.implemented.join(', ')}</li>
                                <li>향후 예정: {platformInfo.collection.planned.join(', ')}</li>
                                <li>승격 규칙: {platformInfo.collection.promotion_rule}</li>
                            </ul>
                        </div>
                        <div className="platform-info-block">
                            <h4>저장소</h4>
                            <ul>
                                <li>구조화 데이터: {platformInfo.storage.structured_data}</li>
                                <li>개인저장방: {platformInfo.storage.personal_vault}</li>
                                <li>업로드: {platformInfo.storage.uploads}</li>
                            </ul>
                        </div>
                    </div>

                    <h4 style={{ marginTop: '16px' }}>아키텍처 (데이터 흐름)</h4>
                    <ol className="platform-info-architecture">
                        {platformInfo.architecture_layers.map((layer, i) => (
                            <li key={i}><strong>{layer.name}</strong> — {layer.desc}</li>
                        ))}
                    </ol>
                </div>
            )}

            <div className="control-panel">
                <div style={{ display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' }}>
                    {/* 검색어 입력창 바로 옆에 실시간 수집 - Enter 키로도 바로 실행되도록 한 폼으로 묶음 */}
                    <form onSubmit={handleRealtimeSubmit} className="search-form">
                        <input 
                            type="text" 
                            placeholder="검색어 입력 (예: 테슬라, AI, 정치)" 
                            value={keyword} 
                            onChange={(e) => setKeyword(e.target.value)}
                            onBlur={handleKeywordInputBlur}
                        />
                        <button type="submit" className="collect-btn">
                            {pipelinePending ? '⏹ 중단 (클릭)' : '⚡ 실시간 수집'}
                        </button>
                    </form>

                    {/* 검색주기설정: 클릭하면 개월/시간 입력창(팝오버)이 나타나고, 그 안에서
                        저장을 눌러야 실제로 반영된다 (8/7 세션 후반 - 상시 노출 대신 토글로 변경) */}
                    <div style={{ position: 'relative' }}>
                        <button
                            type="button"
                            className="collect-btn"
                            style={{ backgroundColor: showIntervalPopover ? '#0d9488' : '#14b8a6' }}
                            onClick={() => setShowIntervalPopover(!showIntervalPopover)}
                        >
                            ⏱️ 검색주기설정
                        </button>

                        {showIntervalPopover && (
                            <form onSubmit={handleSetSearchInterval} className="search-collect-options-popover">
                                <label>
                                    최근
                                    <input
                                        type="number"
                                        min="1"
                                        value={monthsBack}
                                        onChange={(e) => setMonthsBack(Number(e.target.value))}
                                    />
                                    개월 이내 자료
                                </label>
                                <label>
                                    <input
                                        type="number"
                                        min="1"
                                        value={intervalHours}
                                        onChange={(e) => setIntervalHours(Number(e.target.value))}
                                    />
                                    시간마다 재수집
                                </label>
                                <button type="submit" className="search-btn">
                                    저장
                                </button>
                            </form>
                        )}
                    </div>
                </div>

                <div style={{ display: 'flex', gap: '10px', marginLeft: 'auto' }}>

                    <button 
                        className="collect-btn" 
                        style={{ backgroundColor: '#ef4444' }}
                        onClick={handleCleanExisting} 
                        disabled={loading}
                    >
                        {loading ? '⏳ 작업 중...' : '🧹 데이터 정제'}
                    </button>

                    <button
                        className="collect-btn"
                        style={{ backgroundColor: showSourceManager ? '#7c3aed' : '#8b5cf6' }}
                        onClick={handleToggleSourceManager}
                    >
                        ⚙️ 출처 관리
                    </button>
                </div>
            </div>

            {message && (
                <div className="status-message">
                    {message.split('\n').map((line, i) => (
                        <div key={i}>{line}</div>
                    ))}
                </div>
            )}


            {showSourceManager && (
                <div className="source-manager-panel">
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

                    <div className="source-manager-list">
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
                                <div className="source-table-scroll">
                                    <table className="source-table">
                                        <colgroup>
                                            <col style={{ width: '110px' }} />  {/* 카테고리 */}
                                            <col style={{ width: '22%' }} />    {/* 소스 */}
                                            <col style={{ width: '60px' }} />   {/* 건수 */}
                                            <col style={{ width: '64px' }} />   {/* 구분 */}
                                            <col />                              {/* URL - 남는 공간 전부 */}
                                            <col style={{ width: '70px' }} />   {/* 주기 */}
                                            <col style={{ width: '56px' }} />   {/* 삭제 */}
                                        </colgroup>
                                        <thead>
                                            <tr>
                                                <th>카테고리</th>
                                                <th>소스</th>
                                                <th>건수</th>
                                                <th>구분</th>
                                                <th>URL</th>
                                                <th>주기</th>
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
                                                                colSpan={7}
                                                                onClick={toggleCollapse}
                                                                style={{ cursor: 'pointer' }}
                                                            >
                                                                <span className="source-table-category-toggle">▶</span>
                                                                {group.category}
                                                                {isSensitiveCategory(group.category) && (
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
                                                                {isSensitiveCategory(group.category) && (
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
                                                            {src.name}
                                                            {src.status === 'failing' && (
                                                                <span className="source-row-warning">
                                                                    {' '}⚠️ 연속 {src.fail_count}회 실패
                                                                </span>
                                                            )}
                                                        </td>
                                                        <td className="source-table-count-cell">
                                                            {(sourceCounts[src.name] ?? 0)}건
                                                        </td>
                                                        <td>
                                                            <span
                                                                className={`source-origin-badge ${isAutoOrigin(src.origin) ? 'auto' : 'manual'}`}
                                                            >
                                                                {isAutoOrigin(src.origin) ? '자동' : '수동'}
                                                            </span>
                                                        </td>
                                                        <td className="source-table-url" title={src.url}>
                                                            <a href={src.url} target="_blank" rel="noreferrer">
                                                                {src.url}
                                                            </a>
                                                        </td>
                                                        <td className="source-table-interval">
                                                            {src.category === 'BlockList' ? (
                                                                <span
                                                                    className="source-table-block-reason"
                                                                    title="크롤링이 계속 막혀 기사를 저장하지 못한 사유"
                                                                >
                                                                    🚫 {src.block_reason || '차단(원인불명)'}
                                                                </span>
                                                            ) : (
                                                                <>
                                                                    <input
                                                                        type="number"
                                                                        min="0.5"
                                                                        step="0.5"
                                                                        defaultValue={src.interval_hours}
                                                                        style={{ width: '50px', textAlign: 'right' }}
                                                                        title={`다음 점검: ${formatNextCheck(src.last_attempt_at, src.interval_hours)}`}
                                                                        onBlur={(e) => {
                                                                            const v = Number(e.target.value);
                                                                            if (v > 0 && v !== src.interval_hours) {
                                                                                handleUpdateSourceInterval(src.id, v);
                                                                            }
                                                                        }}
                                                                    />
                                                                    <span style={{ marginLeft: '4px' }}>시간</span>
                                                                </>
                                                            )}
                                                        </td>
                                                        <td className="source-table-delete-cell">
                                                            {confirmDeleteId === src.id ? (
                                                                <span className="source-delete-confirm">
                                                                    <button
                                                                        onClick={() => handleDeleteSource(src.id)}
                                                                        className="source-delete-confirm-yes"
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
                    </div>
                </div>
            )}

            <div className="stats-section">
                <p>
                    총 저장된 출처: <strong>{new Set(articles.map(a => a.source)).size}건</strong> 
                    {keyword && ` (검색어: "${keyword}")`}
                </p>
                <div className="keyword-stats-bar">
                    <span className="stats-label">키워드별 현황:</span>
                    {Object.entries(keywordStats).map(([kw, count]) => (
                        <button 
                            key={kw} 
                            className="stat-badge-btn"
                            onClick={() => handleStatClick(kw)}
                        >
                            {kw}: <strong>{count}건</strong>
                        </button>
                    ))}
                    <button 
                        className="stat-badge-btn"
                        style={{ backgroundColor: showKeywordManager ? '#4b5563' : '#374151', color: '#ffffff' }}
                        onClick={handleToggleAllView}
                        title="필터 해제 + 등록된 키워드 관리(삭제)"
                    >
                        전체 보기 {showKeywordManager ? '▲' : '↺'}
                    </button>
                </div>

                {showKeywordManager && (
                    <div className="source-manager-panel" style={{ marginTop: '12px' }}>
                        <h4 style={{ margin: '0 0 10px 0', color: '#94a3b8', fontSize: '0.85rem' }}>
                            🔑 등록된 키워드 관리 (총 {registeredKeywords.length}개)
                        </h4>
                        <div
                            className="source-manager-list"
                            style={{ maxHeight: '50vh', overflowY: 'auto', display: 'block' }}
                        >
                            {registeredKeywords.length === 0 ? (
                                <p style={{ margin: 0, color: '#94a3b8' }}>등록된 키워드가 없습니다.</p>
                            ) : (
                                registeredKeywords.map(kw => (
                                    <div key={kw.id} className="source-row">
                                        <span className="source-row-name">{kw.name}</span>
                                        <span className="source-row-badge">{kw.article_count}건</span>
                                        <span className="source-row-interval">
                                            {formatKeywordDateRange(kw.earliest_published_at, kw.latest_published_at)}
                                        </span>

                                        {confirmDeleteKeywordId === kw.id ? (
                                            <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                                <span style={{ fontSize: '0.78rem', color: '#f87171' }}>삭제할까요?</span>
                                                <button
                                                    onClick={() => handleConfirmDeleteKeyword(kw.id)}
                                                    style={{
                                                        backgroundColor: '#dc2626', color: '#fff', border: 'none',
                                                        borderRadius: '4px', padding: '3px 10px', fontSize: '0.78rem',
                                                        cursor: 'pointer', fontWeight: 'bold'
                                                    }}
                                                >
                                                    확인
                                                </button>
                                                <button
                                                    onClick={handleCancelDeleteKeyword}
                                                    style={{
                                                        backgroundColor: '#374151', color: '#fff', border: 'none',
                                                        borderRadius: '4px', padding: '3px 10px', fontSize: '0.78rem',
                                                        cursor: 'pointer'
                                                    }}
                                                >
                                                    취소
                                                </button>
                                            </span>
                                        ) : (
                                            <button
                                                onClick={() => handleRequestDeleteKeyword(kw.id)}
                                                className="source-row-delete"
                                            >
                                                🗑️
                                            </button>
                                        )}
                                    </div>
                                ))
                            )}
                        </div>
                    </div>
                )}
            </div>

            <div className="article-list">
                {articles.length === 0 ? (
                    <p className="no-data">📭 조건에 일치하는 데이터가 없습니다.</p>
                ) : (
                    articles.map((article) => (
                        <ArticleCard
                            key={article.id}
                            article={article}
                            isEditing={!!articleStates.editing[article.id]}
                            isTranslating={!!articleStates.translating[article.id]}
                            showTranslation={!!articleStates.showTranslation[article.id]}
                            showKoreanOnly={!!articleStates.showKoreanOnly[article.id]}
                            isExpanded={!!articleStates.expanded[article.id]}
                            translatedContent={articleStates.translated[article.id]}
                            progress={articleStates.progress[article.id] || 0}
                            statusMessage={articleStates.statusMessage[article.id]}
                            editContent={
                                articleStates.editContent[article.id] !== undefined
                                    ? articleStates.editContent[article.id]
                                    : article.content
                            }
                            dispatch={dispatch}
                            onTranslate={handleTranslate}
                            onToggleKoreanOnly={handleToggleKoreanOnly}
                            onToggleEdit={handleToggleEdit}
                            onSaveContent={handleSaveContent}
                            onDeleteArticle={handleDeleteArticle}
                            onCleanArticle={handleCleanSingleArticle}
                            MarkdownComponents={MarkdownComponents}
                            normalizeParagraphs={normalizeParagraphs}
                            vaultFolders={vaultFolders}
                            onFetchVaultFolders={fetchVaultFolders}
                            onExportToVault={handleExportToVault}
                        />
                    ))
                )}
            </div>
        </div>
    );
}

export default App;
