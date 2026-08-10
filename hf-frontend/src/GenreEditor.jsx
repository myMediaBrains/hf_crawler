// GenreEditor.jsx
// ---------------------------------------------------------------------------
// '장르 편집기' 버튼 + 패널.
// 맨 위: 대분류/중분류/소분류 입력창 + 추가 버튼
// 그 아래: 등록된 전체 분류를 보여주는 테이블 (대분류/중분류/소분류/건수/수정/삭제)
//
// 2026-08-10: 테이블 행에서 대분류/중분류/소분류를 직접 인라인으로 수정할 수
// 있는 기능 추가. PATCH /genres/{keyword_id}를 호출한다.
// ---------------------------------------------------------------------------

import { useState } from "react";
import axios from "axios";
import SourceManager from "./SourceManager";
import SourceEvaluation from "./SourceEvaluation";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

export default function GenreEditor() {
  // 2026-08-10: 출처관리/출처평가를 메인 화면에서 빼서 이 안에 탭으로 통합
  const [activeTab, setActiveTab] = useState("genres"); // "genres" | "sources" | "evaluation"
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [genres, setGenres] = useState([]);
  const [confirmDeleteId, setConfirmDeleteId] = useState(null);

  const [majorCategory, setMajorCategory] = useState("");
  const [midCategory, setMidCategory] = useState("");
  const [subCategory, setSubCategory] = useState("");

  // 인라인 수정 상태 - 지금 수정 중인 행의 id와, 그 행의 입력값 초안.
  const [editingId, setEditingId] = useState(null);
  const [editValues, setEditValues] = useState({ major: "", mid: "", sub: "" });
  const [savingEdit, setSavingEdit] = useState(false);

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

  const handleStartEdit = (g) => {
    setEditingId(g.id);
    setEditValues({ major: g.major_category, mid: g.mid_category, sub: g.sub_category });
    setError(null);
  };

  const handleCancelEdit = () => {
    setEditingId(null);
  };

  const handleSaveEdit = async (id) => {
    if (!editValues.major.trim() || !editValues.mid.trim() || !editValues.sub.trim()) {
      setError("대분류/중분류/소분류를 모두 입력해주세요.");
      return;
    }
    setSavingEdit(true);
    setError(null);
    try {
      await axios.patch(`${API_BASE}/genres/${id}`, {
        major_category: editValues.major.trim(),
        mid_category: editValues.mid.trim(),
        sub_category: editValues.sub.trim(),
      });
      setEditingId(null);
      await refresh();
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setSavingEdit(false);
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

            <div className="genre-editor-tabs">
              <button
                className={`genre-editor-tab ${activeTab === "genres" ? "active" : ""}`}
                onClick={() => setActiveTab("genres")}
              >
                🗂️ 장르 목록
              </button>
              <button
                className={`genre-editor-tab ${activeTab === "sources" ? "active" : ""}`}
                onClick={() => setActiveTab("sources")}
              >
                ⚙️ 출처 관리
              </button>
              <button
                className={`genre-editor-tab ${activeTab === "evaluation" ? "active" : ""}`}
                onClick={() => setActiveTab("evaluation")}
              >
                📊 출처 평가
              </button>
            </div>

            {activeTab === "sources" ? (
              <SourceManager />
            ) : activeTab === "evaluation" ? (
              <SourceEvaluation embedded />
            ) : (
              <>

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
                    {genres.map((g) => {
                      const isEditing = editingId === g.id;
                      return (
                        <tr key={g.id}>
                          {isEditing ? (
                            <>
                              <td>
                                <input
                                  type="text"
                                  value={editValues.major}
                                  onChange={(e) => setEditValues((prev) => ({ ...prev, major: e.target.value }))}
                                  className="genre-editor-inline-input"
                                />
                              </td>
                              <td>
                                <input
                                  type="text"
                                  value={editValues.mid}
                                  onChange={(e) => setEditValues((prev) => ({ ...prev, mid: e.target.value }))}
                                  className="genre-editor-inline-input"
                                />
                              </td>
                              <td>
                                <input
                                  type="text"
                                  value={editValues.sub}
                                  onChange={(e) => setEditValues((prev) => ({ ...prev, sub: e.target.value }))}
                                  className="genre-editor-inline-input"
                                />
                              </td>
                              <td>{g.article_count}</td>
                              <td>
                                <span className="genre-editor-edit-actions">
                                  <button onClick={() => handleSaveEdit(g.id)} disabled={savingEdit}>
                                    {savingEdit ? "저장 중..." : "저장"}
                                  </button>
                                  <button onClick={handleCancelEdit} disabled={savingEdit}>
                                    취소
                                  </button>
                                </span>
                              </td>
                            </>
                          ) : (
                            <>
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
                                  <span className="genre-editor-row-actions">
                                    <button
                                      className="genre-editor-edit-btn"
                                      onClick={() => handleStartEdit(g)}
                                      title="분류 수정"
                                    >
                                      ✏️
                                    </button>
                                    <button
                                      className="genre-editor-delete-btn"
                                      onClick={() => setConfirmDeleteId(g.id)}
                                      title="삭제"
                                    >
                                      🗑️
                                    </button>
                                  </span>
                                )}
                              </td>
                            </>
                          )}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
