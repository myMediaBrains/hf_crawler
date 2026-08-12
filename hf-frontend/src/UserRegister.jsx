import { useEffect, useRef, useState, memo } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";
const STORAGE_KEY = "hf_user_id";

/**
 * UserRegister (= 상단 사용자 배지/가입/로그인/로그아웃)
 *
 * 2026-08-12: 가입(신규 user_id 생성)과 로그인(이미 있는 user_id로 전환)을
 * 분리했다. 비밀번호는 없다 - user_id 자체가 유일 식별자.
 *
 * - 미등록 상태: [가입] [로그인] 두 버튼 -> 각각 인라인 폼
 *   - 가입 폼: user_id + 표시이름(선택) 입력 -> POST /users/register
 *   - 로그인 폼: GET /users/list로 기존 사용자 목록을 드롭다운으로 보여주고 선택
 * - 등록/로그인 완료 상태: "👤 이름" 배지 + "로그아웃" 버튼
 *   - 로그아웃: localStorage만 지우고 미등록 상태로 돌아감 (서버에 별도 세션 없음)
 *
 * 성능 메모(기존 유지): 가입 폼의 입력창은 uncontrolled(ref)로 구현해서
 * 타이핑 시 리렌더링이 없다.
 */
function UserRegisterInner() {
  const userIdRef = useRef(null);
  const displayNameRef = useRef(null);

  const [checked, setChecked] = useState(false);
  const [registered, setRegistered] = useState(false);
  const [currentUserId, setCurrentUserId] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  // "signup" | "login" | null(아무 폼도 안 열림)
  const [mode, setMode] = useState(null);

  // 로그인 폼용 - 기존 사용자 목록 + 선택값
  const [userList, setUserList] = useState([]);
  const [loginSelection, setLoginSelection] = useState("");

  // 마운트 시 딱 한 번만 - localStorage에 등록된 ID가 있는지 확인
  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (!stored) {
      setChecked(true);
      return;
    }

    setRegistered(true);
    setCurrentUserId(stored);
    setChecked(true);
    window.dispatchEvent(new CustomEvent("hf-user-registered", { detail: { user_id: stored } }));

    (async () => {
      try {
        const res = await fetch(`${API_BASE}/users/me?user_id=${encodeURIComponent(stored)}`);
        if (res.status === 404) {
          localStorage.removeItem(STORAGE_KEY);
          setRegistered(false);
          setCurrentUserId(null);
        }
      } catch (e) {
        // 네트워크 에러는 조용히 넘어감 - 이미 낙관적으로 표시 중
      }
    })();
  }, []);

  const openLoginForm = async () => {
    setMode("login");
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/users/list`);
      const data = await res.json();
      setUserList(data.users || []);
      if (data.users?.length > 0) setLoginSelection(data.users[0].user_id);
    } catch (e) {
      setError("사용자 목록을 불러오지 못했습니다.");
    }
  };

  const handleSignup = async (e) => {
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
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

      localStorage.setItem(STORAGE_KEY, trimmed);
      setRegistered(true);
      setCurrentUserId(trimmed);
      setMode(null);
      window.dispatchEvent(new CustomEvent("hf-user-registered", { detail: { user_id: trimmed } }));
    } catch (e) {
      setError(e.message || "가입에 실패했습니다.");
    } finally {
      setLoading(false);
    }
  };

  const handleLogin = (e) => {
    e.preventDefault();
    if (!loginSelection) {
      setError("로그인할 사용자를 선택해주세요.");
      return;
    }
    localStorage.setItem(STORAGE_KEY, loginSelection);
    setRegistered(true);
    setCurrentUserId(loginSelection);
    setMode(null);
    window.dispatchEvent(new CustomEvent("hf-user-registered", { detail: { user_id: loginSelection } }));
  };

  const handleLogout = () => {
    localStorage.removeItem(STORAGE_KEY);
    setRegistered(false);
    setCurrentUserId(null);
    setMode(null);
    window.dispatchEvent(new CustomEvent("hf-user-registered", { detail: { user_id: null } }));
  };

  if (!checked) return null;

  // 로그인/가입 완료 - 배지 + 로그아웃 버튼
  if (registered) {
    return (
      <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
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
        <button
          type="button"
          onClick={handleLogout}
          title="로그아웃"
          style={{
            padding: "2px 8px",
            borderRadius: "999px",
            border: "1px solid #475569",
            background: "transparent",
            color: "#64748b",
            fontSize: "10.5px",
            cursor: "pointer",
          }}
        >
          로그아웃
        </button>
      </span>
    );
  }

  // 미등록 - [가입] [로그인] 버튼, 눌렀을 때만 각각의 인라인 폼
  if (mode === null) {
    return (
      <span style={{ display: "flex", alignItems: "center", gap: "6px" }}>
        <button
          type="button"
          onClick={() => { setMode("signup"); setError(null); }}
          style={{
            padding: "2px 8px", borderRadius: "999px", border: "1px solid #475569",
            background: "#1e293b", color: "#64748b", fontSize: "10.5px", cursor: "pointer",
          }}
        >
          👤 가입
        </button>
        <button
          type="button"
          onClick={openLoginForm}
          style={{
            padding: "2px 8px", borderRadius: "999px", border: "1px solid #475569",
            background: "#1e293b", color: "#64748b", fontSize: "10.5px", cursor: "pointer",
          }}
        >
          로그인
        </button>
      </span>
    );
  }

  if (mode === "signup") {
    return (
      <form
        onSubmit={handleSignup}
        style={{
          display: "flex", gap: "6px", alignItems: "center", padding: "4px 8px",
          borderRadius: "999px", border: "1px solid #475569", background: "#1e293b",
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
          {loading ? "..." : "가입"}
        </button>
        <button
          type="button"
          onClick={() => setMode(null)}
          style={{ padding: "2px 6px", borderRadius: "4px", fontSize: "12.5px", background: "transparent", color: "#94a3b8" }}
        >
          ✕
        </button>
        {error && <span style={{ fontSize: "11px", color: "#f87171" }}>{error}</span>}
      </form>
    );
  }

  // mode === "login"
  return (
    <form
      onSubmit={handleLogin}
      style={{
        display: "flex", gap: "6px", alignItems: "center", padding: "4px 8px",
        borderRadius: "999px", border: "1px solid #475569", background: "#1e293b",
      }}
    >
      {userList.length === 0 ? (
        <span style={{ fontSize: "11px", color: "#94a3b8" }}>등록된 사용자가 없습니다.</span>
      ) : (
        <select
          value={loginSelection}
          onChange={(e) => setLoginSelection(e.target.value)}
          style={{ padding: "2px 6px", borderRadius: "4px", fontSize: "12.5px" }}
        >
          {userList.map((u) => (
            <option key={u.user_id} value={u.user_id}>
              {u.display_name ? `${u.display_name} (${u.user_id})` : u.user_id}
            </option>
          ))}
        </select>
      )}
      <button type="submit" style={{ padding: "2px 8px", borderRadius: "4px", fontSize: "12.5px" }} disabled={userList.length === 0}>
        로그인
      </button>
      <button
        type="button"
        onClick={() => setMode(null)}
        style={{ padding: "2px 6px", borderRadius: "4px", fontSize: "12.5px", background: "transparent", color: "#94a3b8" }}
      >
        ✕
      </button>
      {error && <span style={{ fontSize: "11px", color: "#f87171" }}>{error}</span>}
    </form>
  );
}

export default memo(UserRegisterInner);
