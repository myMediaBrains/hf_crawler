// useCurrentUser.js
// ---------------------------------------------------------------------------
// 로그인한 사용자의 display_name을 가져오는 공용 훅.
// localStorage의 user_id로 GET /users/me를 호출한다. 여러 컴포넌트
// (ArticleCard, GitHubRepos, PersonalRepository)가 각자 부르면 중복 요청이
// 생기므로, 모듈 스코프 캐시로 한 번만 fetch하고 재사용한다.
// ---------------------------------------------------------------------------

import { useState, useEffect } from "react";
import axios from "axios";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

let cachedDisplayName = null;
let inFlight = null;

async function fetchDisplayName() {
  const userId = localStorage.getItem("user_id");
  if (!userId) {
    console.warn("[useCurrentUser] localStorage에 user_id가 없습니다.");
    return null;
  }

  if (cachedDisplayName) return cachedDisplayName;
  if (inFlight) return inFlight;

  inFlight = axios
    .get(`${API_BASE}/users/me`, { params: { user_id: userId } })
    .then((res) => {
      cachedDisplayName = res.data.display_name || res.data.user_id;
      return cachedDisplayName;
    })
    .catch((err) => {
      console.warn("[useCurrentUser] /users/me 조회 실패:", err.response?.data || err.message);
      return null;
    })
    .finally(() => {
      inFlight = null;
    });

  return inFlight;
}

export default function useCurrentUser() {
  const [displayName, setDisplayName] = useState(cachedDisplayName);

  useEffect(() => {
    if (displayName) return;
    let alive = true;
    fetchDisplayName().then((name) => {
      if (alive) setDisplayName(name);
    });
    return () => {
      alive = false;
    };
  }, [displayName]);

  // 아직 못 받아왔을 때 버튼 라벨이 비어 보이지 않도록 기본값 제공
  return displayName || "개인";
}
