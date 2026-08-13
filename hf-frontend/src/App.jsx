import React, { useState, useEffect, useRef, useCallback, useReducer } from 'react';
import axios from 'axios';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { toast, Toaster } from 'react-hot-toast';
import ArticleCard from './ArticleCard';
import GenreEditor from "./GenreEditor";
import PersonalRepository from "./PersonalRepository";
import GitHubRepos from "./GitHubRepos";
import GenrePreferenceSelector from "./GenrePreferenceSelector";
import CrawlToggleButton from "./CrawlToggleButton";
import UserRegister from "./UserRegister";
import ChatWindow from "./ChatWindow";
import CodeAnalysisChat from "./CodeAnalysisChat";
import GitHubBrowser from "./GitHubBrowser";
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
    const [keywordStats, setKeywordStats] = useState([]); // 이제 중분류 그룹 배열: [{mid_category, total_count, sub_categories}]
    const [expandedMidCategory, setExpandedMidCategory] = useState(null);
    // 2026-08-12: UserRegister.jsx가 가입/로그인/로그아웃마다 쏘는
    // 'hf-user-registered' 이벤트를 받아서 현재 로그인 사용자를 추적한다.
    // 관리자 버튼("Admin"으로 로그인했을 때만 노출) 조건 판단에 쓴다.
    const [currentUserId, setCurrentUserId] = useState(() => localStorage.getItem('hf_user_id'));
    useEffect(() => {
        const handler = (e) => setCurrentUserId(e.detail?.user_id || null);
        window.addEventListener('hf-user-registered', handler);
        return () => window.removeEventListener('hf-user-registered', handler);
    }, []);

    // 2026-08-12: 선호 장르를 하나도 선택 안 한 사용자에게는 메인화면 기사
    // 목록을 숨기고 안내만 보여준다. null=아직 확인 전(이때도 숨김 - "확인
    // 전엔 일단 보여주자"로 했다가 새 사용자에게 잠깐이라도 전체 공개되는
    // 버그가 있었음, 2026-08-12 수정: 기본값을 '숨김' 쪽으로 안전하게 변경).
    // 실제 확인/재조회 로직은 fetchArticles 정의 이후로 내려가 있음(TDZ 문제 방지).
    const [hasPreferences, setHasPreferences] = useState(null);

    // '검색주기설정' 옆 상시 노출 입력창 2개 - 현시점 기준 최근 몇 개월 자료를 가져올지,
    // 몇 시간마다 백그라운드로 재수집할지. 예전엔 ⚙ 토글 버튼을 눌러야 나오는
    // 팝오버였는데 없애고 상시 노출로 단순화, 이후 '실시간 수집'이 즉시 수집을
    // 전담하면서 잠깐 months_back을 뺐다가 다시 복원함 (8/7 세션 후반).
    const [monthsBack, setMonthsBack] = useState(1);
    const [intervalHours, setIntervalHours] = useState(24);

    

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
    const fetchArticles = useCallback(async (targetKeyword = '', tagId = null) => {
        try {
            const storedUserId = localStorage.getItem('hf_user_id') || '';
            const url = tagId
                ? `${API_URL}/articles?tag_id=${encodeURIComponent(tagId)}`
                : targetKeyword.trim()
                    ? `${API_URL}/articles?keyword=${encodeURIComponent(targetKeyword.trim())}`
                    : `${API_URL}/articles?user_id=${encodeURIComponent(storedUserId)}`;
            
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

    // 2026-08-12: 로그인/가입/로그아웃으로 currentUserId가 바뀔 때마다
    // (a) 이 사용자의 선호 장르 보유 여부를 다시 확인하고
    // (b) 메인화면 기사 목록도 그 사용자 기준으로 다시 불러온다.
    // fetchArticles가 이 시점엔 이미 정의돼 있어야 해서 여기(정의 바로 다음)로 옮김.
    useEffect(() => {
        if (!currentUserId) {
            setHasPreferences(true); // 미로그인 상태는 게이트 대상 아님(전체 공개 유지)
            fetchArticles();
            return;
        }
        setHasPreferences(null); // 사용자가 바뀌는 순간 일단 숨김 상태로(이전 사용자 화면이 새 사용자에게 안 새어나가게)
        axios.get(`${API_URL}/personalization/has-preferences`, { params: { user_id: currentUserId } })
            .then((res) => setHasPreferences(!!res.data.has_preferences))
            .catch(() => setHasPreferences(false)); // 조회 실패 시에도 안전하게 숨기는 쪽으로
        fetchArticles();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [currentUserId]);

    // 목록에는 미리보기 본문만 있으므로, 펼치기/편집 시 전체 본문을 따로 불러와
    // articles 상태의 해당 항목만 갱신한다. 반환값을 직접 써야 하는 호출부(편집
    // 시작 등)를 위해 가져온 전체 본문 문자열도 함께 반환한다.
    const fetchFullArticleContent = useCallback(async (articleId) => {
        try {
            const response = await axios.get(`${API_URL}/articles/${articleId}/full`);
            const fullContent = response.data.content;
            setArticles((prev) =>
                prev.map((a) =>
                    a.id === articleId ? { ...a, content: fullContent, content_truncated: false } : a
                )
            );
            return fullContent;
        } catch (err) {
            console.error("전체 본문 조회 에러:", err);
            toast.error('본문을 불러오지 못했습니다.');
            return null;
        }
    }, [API_URL]);

    // 카드 헤더 클릭(펼치기/접기) - 아직 미리보기 상태(content_truncated)인데
    // 펼치는 경우에만 전체 본문을 불러온다. 이미 불러온 적 있으면 재요청 안 함.
    const handleExpandArticle = (article) => {
        const next = !articleStates.expanded[article.id];
        dispatch({ type: 'SET_EXPANDED', id: article.id, value: next });
        if (next && article.content_truncated) {
            fetchFullArticleContent(article.id);
        }
    };

    const fetchKeywordStats = useCallback(async () => {
        try {
            const response = await axios.get(`${API_URL}/stats/keywords`);
            setKeywordStats(response.data.mid_categories || []);
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


    const fetchVaultFolders = useCallback(async () => {
        const storedUserId = localStorage.getItem('hf_user_id');
        if (!storedUserId) return; // 로그인 전에는 조회 자체를 안 함
        try {
            const response = await axios.get(`${API_URL}/vault/folders`, {
                params: { user_id: storedUserId },
            });
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
        // (너무 짧으면 백엔드에 불필요한 부하). 기사량이 늘면서 계산 자체가 무거워져
        // 5배(10초)로는 부족했음 - 15배(30초)로 늘려 백엔드 캐시(60초) 주기와 여유
        // 있게 맞춘다 (2026-08-09).
        const keywordStatsInterval = setInterval(() => {
            fetchKeywordStats();
        }, POLLING_INTERVAL * 15);

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
            const storedUserId = localStorage.getItem('hf_user_id') || '';
            const url = targetKeyword
                ? `${API_URL}/collect/deep-incremental?keyword=${encodeURIComponent(targetKeyword)}&months_back=${monthsBack}&interval_hours=${intervalHours}&user_id=${encodeURIComponent(storedUserId)}`
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

        // 낙관적 업데이트: 서버 응답(전체 재조회)을 기다리지 않고 화면에서
        // 즉시 지운다. 예전엔 삭제 API 응답 후 GET /articles 전체를 다시
        // 불러와서 화면을 갱신했는데, 기사가 많이 쌓인 지금은 이 재조회 자체가
        // 체감될 만큼 걸려서 "삭제 완료 메시지는 바로 뜨는데 실제로는 1초 뒤에
        // 사라지는" 어색한 지연이 있었다 (2026-08-09).
        setArticles((prev) => prev.filter((a) => a.id !== articleId));
        if (pinnedArticleIdRef.current === articleId) {
            pinnedArticleIdRef.current = null;
        }
        dispatch({ type: 'RESET_ARTICLE_STATE', id: articleId });

        try {
            const response = await axios.delete(`${API_URL}/articles/${articleId}`);
            setMessage(response.data.message);
            toast.success('삭제 완료!', { id: toastId });
            // 통계는 화면을 막지 않고 백그라운드로만 갱신 (await 안 함)
            fetchKeywordStats();
            fetchSystemStats();
        } catch (err) {
            console.error("삭제 에러:", err);
            setMessage('기사 삭제 중 에러가 발생했습니다.');
            toast.error('삭제 실패 - 목록을 다시 불러옵니다', { id: toastId });
            // 실패했다면 낙관적으로 지운 게 잘못된 것이므로 서버 기준으로 복구
            await fetchArticles(keyword);
        }
    };

    const handleToggleEdit = async (article) => {
        pinArticleToTop(article.id);
        const isEditing = !!articleStates.editing[article.id];
        if (!isEditing) {
            // 미리보기(잘린 본문)로 편집창을 채우면 저장 시 뒷부분이 통째로 날아가므로,
            // 편집을 시작하기 전엔 반드시 전체 본문을 먼저 확보한다.
            let fullContent = article.content;
            if (article.content_truncated) {
                const fetched = await fetchFullArticleContent(article.id);
                if (fetched !== null) fullContent = fetched;
            }
            dispatch({ type: 'SET_EDIT_CONTENT', id: article.id, value: fullContent });
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

    const handleStatClick = async (item) => {
        setKeyword(item.label);
        setLoading(true);
        await fetchArticles(item.label, item.tag_id);
        setLoading(false);
    };

    const handleExportToVault = async (articleId, folder, filename, content, sourceTitle, sourceRef) => {
        const storedUserId = localStorage.getItem('hf_user_id');
        if (!storedUserId) {
            toast.error('사용자 등록/로그인 후 이용할 수 있습니다.');
            return;
        }
        const toastId = toast.loading('저장소에 저장 중...');
        try {
            const response = await axios.post(`${API_URL}/vault/export`, {
                folder, filename, content, user_id: storedUserId,
                source_title: sourceTitle, source_ref: sourceRef,
            });
            toast.success(`저장 완료: ${response.data.filename}`, { id: toastId });
            await fetchVaultFolders();
        } catch (err) {
            console.error("저장소 저장 에러:", err);
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

            <ChatWindow />
            
            <header className="app-header">
                <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
                    <h1>🚀 Local Trend & Deep Content Inspector</h1>
                    <UserRegister />
                </div>

                <div className="system-monitor-bar">
                    <div>🖥️ <strong>CPU:</strong> <span style={{ color: '#60a5fa' }}>{systemStats.cpu_usage}</span></div>
                    <div>🎮 <strong>GPU:</strong> <span style={{ color: '#34d399' }}>{systemStats.gpu_usage}</span></div>
                    <div>🧠 <strong>메모리:</strong> <span style={{ color: '#fbbF24' }}>{systemStats.memory_usage}</span></div>
                    <div>💾 <strong>DB:</strong> <span style={{ color: '#f87171' }}>{systemStats.db_usage}</span></div>
                    <button className="platform-info-btn" onClick={handleTogglePlatformInfo}>
                        🧩 구성요소
                    </button>
                    <CrawlToggleButton />
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

            {/* 2026-08-10: 두 줄이었던 걸 한 줄로 병합 (출처관리/출처평가가 장르편집기
                탭 안으로 옮겨가면서 버튼 수가 줄어 한 줄에 다 들어갈 여유가 생김) */}
            <div className="control-panel" style={{ display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' }}>
                <form onSubmit={handleRealtimeSubmit} className="search-form">
                    <input 
                        type="text" 
                        placeholder="검색어 입력 (예: 테슬라, AI, 정치)" 
                        value={keyword} 
                        onChange={(e) => setKeyword(e.target.value)}
                        onBlur={handleKeywordInputBlur}
                    />
                    <button
                        type="submit"
                        className="collect-btn"
                        style={{ backgroundColor: pipelinePending ? '#991b1b' : '#ef4444' }}
                    >
                        {pipelinePending ? '⏹ 중단 (클릭)' : '⚡ 실시간 수집'}
                    </button>
                </form>

                <GenrePreferenceSelector />

                {/* 2026-08-11: 검색주기설정 팝오버는 데이터편집 탭(IntervalSettings)으로
                    이동했고, 이 자리엔 데이터편집 탭에서 빠져나온 GitHub 저장소가 들어왔다. */}
                <GitHubRepos />

                {/* 2026-08-12: 데이터편집은 Admin으로 로그인했을 때만 노출.
                    관리자(미분류 키워드 처리) 탭도 이 안으로 옮겨졌다. */}
                {currentUserId === 'Admin' && <GenreEditor />}

                <PersonalRepository />

                {/* 2026-08-13: 코딩분석 - VS Code로 편집 중인 이 프로젝트를
                    qwen2.5-coder:32b에게 참고시켜 대화하는 기능 */}
                <CodeAnalysisChat />

                {/* 2026-08-13: hf_coder의 GitHub 브라우징 - 타 저장소 탐색 + 내
                    저장소(push 반영) 확인. 위의 <GitHubRepos />(hf_crawler의
                    트렌딩 저장소 수집 기능)와는 완전히 다른 별개 기능. */}
                <GitHubBrowser />
            </div>

            {message && (
                <div className="status-message">
                    {message.split('\n').map((line, i) => (
                        <div key={i}>{line}</div>
                    ))}
                </div>
            )}


            <div className="stats-section">
                <p>
                    총 저장된 출처: <strong>{new Set(articles.map(a => a.source)).size}건</strong> 
                    {keyword && ` (검색어: "${keyword}")`}
                </p>
                <div className="keyword-stats-bar">
                    <span className="stats-label">키워드별 현황 (중분류):</span>
                    {keywordStats.map((group) => (
                        <button
                            key={group.mid_category}
                            className="stat-badge-btn"
                            style={expandedMidCategory === group.mid_category ? { backgroundColor: '#3b82f6', color: '#fff' } : undefined}
                            onClick={() =>
                                setExpandedMidCategory((prev) => (prev === group.mid_category ? null : group.mid_category))
                            }
                        >
                            {group.mid_category}: <strong>{group.total_count}건</strong>
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

                {expandedMidCategory && (
                    <div className="keyword-stats-bar keyword-stats-sub-bar">
                        <span className="stats-label">└ {expandedMidCategory} 소분류:</span>
                        {(keywordStats.find((g) => g.mid_category === expandedMidCategory)?.sub_categories || []).map((item) => (
                            <button
                                key={item.tag_id ?? item.label}
                                className="stat-badge-btn stat-badge-btn-sub"
                                onClick={() => handleStatClick(item)}
                            >
                                {item.label}: <strong>{item.count}건</strong>
                            </button>
                        ))}
                    </div>
                )}
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
                {currentUserId && !hasPreferences ? (
                    <div className="no-data preference-prompt">
                        <p>⭐ 아직 선호 장르를 선택하지 않으셨네요.</p>
                        <p>상단의 <strong>"⭐ 선호 장르 선택"</strong> 버튼에서 관심 있는 장르를 골라 저장하면, 그에 맞는 자료가 여기 나타납니다.</p>
                    </div>
                ) : articles.length === 0 ? (
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
                            onExpandArticle={handleExpandArticle}
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
