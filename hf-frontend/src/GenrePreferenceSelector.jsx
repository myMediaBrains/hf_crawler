// GenrePreferenceSelector.jsx
// ---------------------------------------------------------------------------
// '선호 장르 선택' 버튼 + 패널.
//
// 2026-08-12 전면 재설계:
// - 선택지는 하드코딩된 22개 목록이 아니라, 데이터편집 > 장르목록에 실제
//   등록된 '대분류'를 GET /genres/major-categories로 불러와서 그대로 보여준다
//   (한글 라벨 포함, 미분류 placeholder는 제외).
// - 필터링이 대분류 단위로 바뀌었으므로, 대분류 하나를 선택하면 그 밑의
//   모든 키워드(지금 것 + 나중에 새로 생기는 것)가 전부 보이게 된다.
// - 직접 입력창은 이제 1개뿐이다. 여기에 입력하면 대분류/중분류/소분류
//   전부 같은 값으로 새로 등록되고(= '미분류' 상태), 즉시 이 사용자의
//   선호로도 잡힌다. 나중에 관리자가 데이터편집 > 관리자 탭에서 정식
//   분류하면 이 사용자의 선호도 자동으로 그 대분류로 갱신된다.
// ---------------------------------------------------------------------------

import { useState } from "react";
import axios from "axios";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

export default function GenrePreferenceSelector() {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [majorCategories, setMajorCategories] = useState([]);
  const [checked, setChecked] = useState(() => new Set());
  const [customEntry, setCustomEntry] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState(null);

  // UserRegister.jsx가 등록/로그인 시 localStorage에 저장해둔 값을 그대로 읽는다.
  const userId = localStorage.getItem("hf_user_id") || null;

  const fetchMajorCategories = async () => {
    setLoading(true);
    try {
      const res = await axios.get(`${API_BASE}/genres/major-categories`, {
        params: { user_id: userId || undefined },
      });
      const list = res.data.major_categories || [];
      setMajorCategories(list);
      // 이미 선택해둔(또는 Admin이라 전부 selected로 오는) 항목을 체크 상태로 반영
      setChecked(new Set(list.filter((m) => m.selected).map((m) => m.value)));
    } catch (e) {
      setMessage("대분류 목록을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  };

  const handleOpen = async () => {
    setOpen(true);
    setMessage(null);
    await fetchMajorCategories();
  };

  const toggleCheck = (value) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(value)) next.delete(value);
      else next.add(value);
      return next;
    });
  };

  const allValues = majorCategories.map((m) => m.value);
  const isAllChecked = allValues.length > 0 && allValues.every((v) => checked.has(v));
  const toggleAll = () => {
    setChecked(isAllChecked ? new Set() : new Set(allValues));
  };

  const handleSubmit = async () => {
    if (!userId) {
      setMessage("사용자 등록/로그인 후 이용할 수 있습니다.");
      return;
    }
    if (checked.size === 0 && !customEntry.trim()) {
      setMessage("선택하거나 직접 입력한 장르가 없습니다.");
      return;
    }

    setSubmitting(true);
    setMessage(null);
    try {
      const res = await axios.post(`${API_BASE}/genres/preferences`, {
        user_id: userId,
        major_categories: Array.from(checked),
        custom_entry: customEntry.trim() || null,
      });
      setMessage(res.data.message);
      setCustomEntry("");
      await fetchMajorCategories(); // 직접 입력이 새 대분류로 등록됐을 수 있으니 갱신
    } catch (e) {
      setMessage(e.response?.data?.detail || "등록 중 오류가 발생했습니다.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <button onClick={handleOpen} className="collect-btn genre-preference-btn">
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
                    ⚠️ 사용자 미등록 상태 - 로그인 후 이용할 수 있습니다
                  </div>
                )}
              </div>
              <button onClick={() => setOpen(false)}>닫기</button>
            </div>

            {loading ? (
              <p style={{ color: "var(--text-muted)" }}>불러오는 중...</p>
            ) : majorCategories.length === 0 ? (
              <p style={{ color: "var(--text-muted)" }}>
                아직 등록된 대분류가 없습니다. 아래에서 직접 입력해서 새로 만들어보세요.
              </p>
            ) : (
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
                <label
                  style={{
                    display: "flex", alignItems: "center", gap: "6px", fontSize: "14px",
                    cursor: "pointer", fontWeight: 700, color: "var(--primary-color)",
                  }}
                >
                  <input type="checkbox" checked={isAllChecked} onChange={toggleAll} />
                  모두
                </label>
                {majorCategories.map((m) => (
                  <label
                    key={m.value}
                    style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "14px", cursor: "pointer" }}
                  >
                    <input
                      type="checkbox"
                      checked={checked.has(m.value)}
                      onChange={() => toggleCheck(m.value)}
                    />
                    {m.label_ko}
                  </label>
                ))}
              </div>
            )}

            <div style={{ display: "flex", gap: "8px", margin: "12px 0" }}>
              <input
                type="text"
                placeholder="직접 입력 (예: 골프여행) - 새 장르로 등록됩니다"
                value={customEntry}
                onChange={(e) => setCustomEntry(e.target.value)}
                style={{ flex: 1 }}
              />
            </div>

            {message && <p style={{ color: "#94a3b8", fontSize: 13 }}>{message}</p>}

            <button
              className="collect-btn"
              onClick={handleSubmit}
              disabled={submitting}
              style={{ width: "100%" }}
            >
              {submitting
                ? "저장 중..."
                : `선택한 ${checked.size}개${customEntry.trim() ? " + 직접입력 1개" : ""} 저장`}
            </button>
          </div>
        </div>
      )}
    </>
  );
}
