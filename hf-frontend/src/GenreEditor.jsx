// GenreEditor.jsx
// ---------------------------------------------------------------------------
// '장르 편집기' 버튼 + 패널.
// 맨 위: 대분류/중분류/소분류 입력창 + 추가 버튼
// 그 아래: 등록된 전체 분류를 보여주는 테이블 (대분류/중분류/소분류/건수/삭제)
//
// hf-frontend/src/ 에 이 파일 그대로 추가한 뒤, App.jsx에서
//   import GenreEditor from "./GenreEditor";
// 로 불러와 렌더링하면 됩니다. (메인 화면 "데이터 정제" 버튼 자리를 대체)
// ---------------------------------------------------------------------------

import { useState } from "react";
import axios from "axios";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

export default function GenreEditor() {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [genres, setGenres] = useState([]);
  const [confirmDeleteId, setConfirmDeleteId] = useState(null);

  const [majorCategory, setMajorCategory] = useState("");
  const [midCategory, setMidCategory] = useState("");
  const [subCategory, setSubCategory] = useState("");

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await axios.get(`${API_BASE}/genres`);
      setGenres(res.data.genres || []);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const handleOpen = async () => {
    setOpen(true);
    await refresh();
  };

  const handleAdd = async (e) => {
    e.preventDefault();
    if (!majorCategory.trim() || !midCategory.trim() || !subCategory.trim()) {
      setError("대분류/중분류/소분류를 모두 입력해주세요.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await axios.post(`${API_BASE}/genres`, {
        major_category: majorCategory.trim(),
        mid_category: midCategory.trim(),
        sub_category: subCategory.trim(),
      });
      setMajorCategory("");
      setMidCategory("");
      setSubCategory("");
      await refresh();
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id) => {
    setConfirmDeleteId(null);
    try {
      await axios.delete(`${API_BASE}/keywords/${id}`);
      await refresh();
    } catch (e) {
      setError(e.message);
    }
  };

  return (
    <>
      <button onClick={handleOpen} className="collect-btn genre-editor-btn">
        🗂️ 장르 편집기
      </button>

      {open && (
        <div className="genre-editor-overlay" onClick={() => setOpen(false)}>
          <div className="genre-editor-panel" onClick={(e) => e.stopPropagation()}>
            <div className="genre-editor-header">
              <h3>장르 편집기</h3>
              <button onClick={() => setOpen(false)}>닫기</button>
            </div>

            <form onSubmit={handleAdd} className="genre-editor-form">
              <input
                type="text"
                placeholder="대분류 (예: AI)"
                value={majorCategory}
                onChange={(e) => setMajorCategory(e.target.value)}
              />
              <input
                type="text"
                placeholder="중분류 (예: 챗봇)"
                value={midCategory}
                onChange={(e) => setMidCategory(e.target.value)}
              />
              <input
                type="text"
                placeholder="소분류 - 실제 검색어 (예: ChatGPT)"
                value={subCategory}
                onChange={(e) => setSubCategory(e.target.value)}
              />
              <button type="submit" className="collect-btn" disabled={submitting}>
                {submitting ? "추가 중..." : "추가"}
              </button>
            </form>

            {error && <p style={{ color: "#f87171", fontSize: 13 }}>{error}</p>}

            <div className="genre-editor-table-wrap">
              {loading ? (
                <p>불러오는 중...</p>
              ) : genres.length === 0 ? (
                <p style={{ color: "var(--text-muted)" }}>등록된 장르가 없습니다. 위에서 추가해보세요.</p>
              ) : (
                <table className="genre-editor-table">
                  <thead>
                    <tr>
                      <th>대분류</th>
                      <th>중분류</th>
                      <th>소분류</th>
                      <th>건수</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {genres.map((g) => (
                      <tr key={g.id}>
                        <td>{g.major_category}</td>
                        <td>{g.mid_category}</td>
                        <td>{g.sub_category}</td>
                        <td>{g.article_count}</td>
                        <td>
                          {confirmDeleteId === g.id ? (
                            <span className="genre-editor-delete-confirm">
                              <button onClick={() => handleDelete(g.id)}>확인</button>
                              <button onClick={() => setConfirmDeleteId(null)}>취소</button>
                            </span>
                          ) : (
                            <button
                              className="genre-editor-delete-btn"
                              onClick={() => setConfirmDeleteId(g.id)}
                            >
                              🗑️
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
