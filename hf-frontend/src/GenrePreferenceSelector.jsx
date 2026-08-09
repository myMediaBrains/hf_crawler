// GenrePreferenceSelector.jsx
// ---------------------------------------------------------------------------
// '선호 장르 선택' 버튼 + 패널. 미리 정의된 22개 관심사를 체크박스로 보여주고,
// 사용자가 직접 항목을 추가할 수도 있다. 저장하면 /genres/select 로 한 번에
// 전송 - 백엔드가 Tag/Keyword 등록과 동시에 개인화 선호 신호(InteractionSignal)로도
// 기록한다.
//
// GenreEditor.jsx와 같은 CSS 클래스(genre-editor-overlay/panel/header)를
// 재사용해서 새 스타일을 따로 안 만들어도 된다.
// ---------------------------------------------------------------------------

import { useState } from "react";
import axios from "axios";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

// (한글 라벨, 대분류, 중분류=Tag 이름, 영어 검색문구)
const DEFAULT_GENRES = [
  { label: "AI", major: "AI", mid: "AI", query: "artificial intelligence news" },
  { label: "요리", major: "Life", mid: "Cooking", query: "cooking recipes food trends" },
  { label: "여행", major: "Life", mid: "Travel", query: "travel destinations guide" },
  { label: "스포츠", major: "Sports", mid: "Sports", query: "sports news highlights" },
  { label: "역사", major: "Culture", mid: "History", query: "history articles documentary" },
  { label: "정치", major: "Politics", mid: "Politics", query: "politics news" },
  { label: "경제", major: "Economy", mid: "Economy", query: "economy market news" },
  { label: "소설", major: "Culture", mid: "Books", query: "novel bestseller fiction books" },
  { label: "음악", major: "Culture", mid: "Music", query: "music new releases billboard" },
  { label: "라이프스타일", major: "Life", mid: "Lifestyle", query: "lifestyle trends" },
  { label: "영화", major: "Entertainment", mid: "Movies", query: "movie reviews new releases" },
  { label: "드라마", major: "Entertainment", mid: "TV Drama", query: "tv drama series review" },
  { label: "다큐멘터리", major: "Entertainment", mid: "Documentary", query: "documentary film review" },
  { label: "양자컴퓨팅", major: "Tech", mid: "Quantum Computing", query: "quantum computing news" },
  { label: "비트코인", major: "Economy", mid: "Bitcoin", query: "bitcoin cryptocurrency news" },
  { label: "주식", major: "Economy", mid: "Stock Market", query: "stock market news" },
  { label: "IT기업", major: "Tech", mid: "IT Companies", query: "tech company news" },
  { label: "소프트웨어", major: "Tech", mid: "Software", query: "software development news" },
  { label: "실버건강", major: "Health", mid: "Senior Health", query: "senior health elderly wellness" },
  { label: "당뇨", major: "Health", mid: "Diabetes", query: "diabetes management news" },
  { label: "헬스", major: "Health", mid: "Fitness", query: "fitness health news" },
  { label: "음식", major: "Life", mid: "Food", query: "food trends cuisine" },
];

export default function GenrePreferenceSelector() {
  const [open, setOpen] = useState(false);
  const [genres, setGenres] = useState(DEFAULT_GENRES);
  const [checked, setChecked] = useState(() => new Set());
  const [customLabel, setCustomLabel] = useState("");
  const [customMajor, setCustomMajor] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState(null);

  // UserRegister.jsx가 등록 시 localStorage에 저장해둔 값을 그대로 읽는다.
  const userId = localStorage.getItem("hf_user_id") || null;

  const toggleCheck = (idx) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  };

  const handleAddCustom = () => {
    const label = customLabel.trim();
    if (!label) return;
    const major = customMajor.trim() || "Custom";
    const newIdx = genres.length;
    setGenres((prev) => [...prev, { label, major, mid: label, query: label }]);
    setChecked((prev) => new Set(prev).add(newIdx)); // 추가하면 바로 체크된 상태로 시작
    setCustomLabel("");
    setCustomMajor("");
  };

  const handleSubmit = async () => {
    const items = genres
      .filter((_, idx) => checked.has(idx))
      .map((g) => ({
        major_category: g.major,
        mid_category: g.mid,
        sub_category: g.mid,
        search_query: g.query,
      }));

    if (items.length === 0) {
      setMessage("선택된 장르가 없습니다.");
      return;
    }

    setSubmitting(true);
    setMessage(null);
    try {
      const res = await axios.post(`${API_BASE}/genres/select`, { items, user_id: userId });
      setMessage(res.data.message);
    } catch (e) {
      setMessage(e.response?.data?.detail || "등록 중 오류가 발생했습니다.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <button onClick={() => setOpen(true)} className="collect-btn genre-preference-btn">
        ⭐ 선호 장르 선택
      </button>

      {open && (
        <div className="genre-editor-overlay" onClick={() => setOpen(false)}>
          <div className="genre-editor-panel" onClick={(e) => e.stopPropagation()}>
            <div className="genre-editor-header">
              <div>
                <h3>선호 장르 선택</h3>
                {userId ? (
                  <div style={{ fontSize: "13px", color: "#94a3b8", marginTop: "4px" }}>
                    👤 {userId} 님의 선호 장르
                  </div>
                ) : (
                  <div style={{ fontSize: "13px", color: "#f87171", marginTop: "4px" }}>
                    ⚠️ 사용자 미등록 상태 - 선호 신호가 특정 사용자에게 귀속되지 않습니다
                  </div>
                )}
              </div>
              <button onClick={() => setOpen(false)}>닫기</button>
            </div>

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(3, 1fr)",
                gap: "8px",
                margin: "16px 0",
                maxHeight: "260px",
                overflowY: "auto",
              }}
            >
              {genres.map((g, idx) => (
                <label
                  key={`${g.label}-${idx}`}
                  style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "14px", cursor: "pointer" }}
                >
                  <input type="checkbox" checked={checked.has(idx)} onChange={() => toggleCheck(idx)} />
                  {g.label}
                </label>
              ))}
            </div>

            <div style={{ display: "flex", gap: "8px", marginBottom: "12px" }}>
              <input
                type="text"
                placeholder="직접 입력 (예: 골프여행)"
                value={customLabel}
                onChange={(e) => setCustomLabel(e.target.value)}
              />
              <input
                type="text"
                placeholder="대분류(선택, 없으면 Custom)"
                value={customMajor}
                onChange={(e) => setCustomMajor(e.target.value)}
              />
              <button type="button" onClick={handleAddCustom}>
                추가
              </button>
            </div>

            {message && <p style={{ color: "#94a3b8", fontSize: 13 }}>{message}</p>}

            <button
              className="collect-btn"
              onClick={handleSubmit}
              disabled={submitting}
              style={{ width: "100%" }}
            >
              {submitting ? "저장 중..." : `선택한 ${checked.size}개 장르 저장`}
            </button>
          </div>
        </div>
      )}
    </>
  );
}
