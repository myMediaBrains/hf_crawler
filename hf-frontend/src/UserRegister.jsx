import { useEffect, useRef, useState, memo } from "react";

const API_BASE = "http://localhost:8000";
const STORAGE_KEY = "hf_user_id";

/**
 * UserRegister (= CPU 좌측 사용자 배지/등록 폼)
 *
 * 성능 메모:
 * - 입력창은 controlled(value+onChange)가 아니라 uncontrolled(ref)로 구현했다.
 *   즉 타이핑해도 React state가 바뀌지 않으므로 리렌더링이 전혀 발생하지 않는다
 *   (브라우저 네이티브 입력 처리만 일어남) — system-monitor-bar가 2초마다
 *   폴링으로 리렌더링되는 상황에서도 입력 지연이 생기지 않는다.
 * - React.memo로 감싸서, 부모(App)의 다른 state 변화로는 이 컴포넌트가
 *   아예 리렌더링되지 않도록 이중으로 격리했다.
 */
function UserRegisterInner() {
  const userIdRef = useRef(null);
  const displayNameRef = useRef(null);

  const [checked, setChecked] = useState(false);
  const [registered, setRegistered] = useState(false);
  const [currentUserId, setCurrentUserId] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [showForm, setShowForm] = useState(false);

  // 마운트 시 딱 한 번만 - localStorage에 등록된 ID가 있는지 확인
  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (!stored) {
      setChecked(true);
      return;
    }

    // 낙관적 표시: 서버 응답을 기다리지 않고 localStorage에 저장된 ID를
    // 즉시 배지에 반영한다. "새로고침하면 잠깐 사라졌다가 나타나는" 지연을 없앤다.
    setRegistered(true);
    setCurrentUserId(stored);
    setChecked(true);
    window.dispatchEvent(new CustomEvent("hf-user-registered", { detail: { user_id: stored } }));

    // 백그라운드에서 실제 등록 여부를 재확인한다.
    // ⚠️ 핵심 수정: 진짜 404(서버가 "그런 사용자 없음"이라고 명시적으로 답한 경우)일
    // 때만 되돌린다. 500 에러나 네트워크 에러 같은 애매한 실패는 등록을 지우지 않고
    // 그대로 낙관적 표시를 유지한다 (일시적 문제로 등록 정보가 사라지는 사고 방지).
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/users/me?user_id=${encodeURIComponent(stored)}`);
        if (res.status === 404) {
          localStorage.removeItem(STORAGE_KEY);
          setRegistered(false);
          setCurrentUserId(null);
        }
        // res.ok(200)이면 그대로 유지, 그 외(500 등)도 조용히 유지.
      } catch (e) {
        // 네트워크 에러도 조용히 넘어감 - 이미 낙관적으로 표시 중이므로 문제 없음
      }
    })();
  }, []);

  const handleSubmit = async (e) => {
    e.preventDefault();
    const trimmed = (userIdRef.current?.value || "").trim();
    if (!trimmed) {
      setError("사용자 ID를 입력해주세요.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/users/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_id: trimmed,
          display_name: (displayNameRef.current?.value || "").trim() || null,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || `HTTP ${res.status}`);
      }
      localStorage.setItem(STORAGE_KEY, trimmed);
      setRegistered(true);
      setCurrentUserId(trimmed);
      setShowForm(false);
      window.dispatchEvent(new CustomEvent("hf-user-registered", { detail: { user_id: trimmed } }));
    } catch (e) {
      setError(e.message || "등록에 실패했습니다.");
    } finally {
      setLoading(false);
    }
  };

  if (!checked) return null;

  // 등록 완료 - CPU 좌측에 작은 배지 버튼만 표시
  if (registered) {
    return (
      <button
        type="button"
        title="등록된 사용자"
        style={{
          padding: "2px 8px",
          borderRadius: "999px",
          border: "1px solid #475569",
          background: "#1e293b",
          color: "#94a3b8",
          fontSize: "10.5px",
          fontWeight: "normal",
          cursor: "default",
        }}
      >
        👤 {currentUserId}
      </button>
    );
  }

  // 미등록 - 배지 버튼을 누르면 인라인 폼이 펼쳐짐
  if (!showForm) {
    return (
      <button
        type="button"
        onClick={() => setShowForm(true)}
        style={{
          padding: "2px 8px",
          borderRadius: "999px",
          border: "1px solid #475569",
          background: "#1e293b",
          color: "#64748b",
          fontSize: "10.5px",
          cursor: "pointer",
        }}
      >
        👤 사용자 등록
      </button>
    );
  }

  return (
    <form
      onSubmit={handleSubmit}
      style={{
        display: "flex",
        gap: "6px",
        alignItems: "center",
        padding: "4px 8px",
        borderRadius: "999px",
        border: "1px solid #475569",
        background: "#1e293b",
      }}
    >
      <input
        ref={userIdRef}
        placeholder="사용자 ID"
        defaultValue=""
        autoFocus
        style={{ width: "100px", padding: "2px 6px", borderRadius: "4px", fontSize: "12.5px" }}
      />
      <input
        ref={displayNameRef}
        placeholder="표시이름(선택)"
        defaultValue=""
        style={{ width: "100px", padding: "2px 6px", borderRadius: "4px", fontSize: "12.5px" }}
      />
      <button type="submit" disabled={loading} style={{ padding: "2px 8px", borderRadius: "4px", fontSize: "12.5px" }}>
        {loading ? "..." : "등록"}
      </button>
      <button
        type="button"
        onClick={() => setShowForm(false)}
        style={{ padding: "2px 6px", borderRadius: "4px", fontSize: "12.5px", background: "transparent", color: "#94a3b8" }}
      >
        ✕
      </button>
      {error && <span style={{ fontSize: "11px", color: "#f87171" }}>{error}</span>}
    </form>
  );
}

export default memo(UserRegisterInner);