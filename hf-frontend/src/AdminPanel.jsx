// AdminPanel.jsx
// ---------------------------------------------------------------------------
// '관리자' 탭 콘텐츠 (데이터편집 모달 안에 들어감 - 독립 버튼/모달 아님).
//
// 실시간 검색으로 자동 등록됐지만 아직 정식 대분류/중분류가 없는(자기 이름을
// 대분류로 쓰는 placeholder 상태) 키워드를 모아서 보여주고, 관리자가 대분류/
// 중분류를 입력해서 저장하면:
//   1) 그 키워드의 Tag가 정식 분류로 바뀌고
//   2) 그동안 이 키워드를 검색했던 사용자들의 선호 신호가 소급으로 기록된다
//      (GET /admin/unclassified-keywords가 보여주는 '관심 사용자' 목록 기준)
//
// 2026-08-12: GenreEditor.jsx의 탭으로 이동. "Admin"으로 로그인했을 때만
// 데이터편집 버튼 자체가 보이므로, 여기서 별도 권한 체크는 안 한다.
// ---------------------------------------------------------------------------

import { useState, useEffect } from "react";
import axios from "axios";
import { toast } from "react-hot-toast";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

export default function AdminPanel() {
  const [loading, setLoading] = useState(false);
  const [keywords, setKeywords] = useState([]);
  const [drafts, setDrafts] = useState({}); // keyword_id -> { major, mid }
  const [submittingId, setSubmittingId] = useState(null);

  const refresh = async () => {
    setLoading(true);
    try {
      const res = await axios.get(`${API_BASE}/admin/unclassified-keywords`);
      setKeywords(res.data.keywords || []);
    } catch (err) {
      console.error("미분류 키워드 조회 에러:", err);
      toast.error("목록을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const updateDraft = (keywordId, field, value) => {
    setDrafts((prev) => ({
      ...prev,
      [keywordId]: { ...(prev[keywordId] || { major: "", mid: "" }), [field]: value },
    }));
  };

  const handleClassify = async (kw) => {
    const draft = drafts[kw.keyword_id] || {};
    if (!draft.major?.trim() || !draft.mid?.trim()) {
      toast("대분류/중분류를 모두 입력해주세요.");
      return;
    }
    setSubmittingId(kw.keyword_id);
    try {
      const res = await axios.post(`${API_BASE}/admin/classify-keyword`, {
        keyword_id: kw.keyword_id,
        major_category: draft.major.trim(),
        mid_category: draft.mid.trim(),
      });
      toast.success(res.data.message);
      await refresh();
    } catch (err) {
      console.error("키워드 분류 에러:", err);
      toast.error(err.response?.data?.detail || "분류 저장에 실패했습니다.");
    } finally {
      setSubmittingId(null);
    }
  };

  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
        <div style={{ fontSize: "13px", color: "var(--text-muted)" }}>
          대분류/중분류를 입력해서 정식 장르로 분류하면, 검색했던 사용자들의 선호도에도 소급 반영됩니다.
        </div>
        <button onClick={refresh} disabled={loading} className="collect-btn">
          {loading ? "갱신 중..." : "새로고침"}
        </button>
      </div>

      <div className="genre-editor-table-wrap">
        {loading ? (
          <p>불러오는 중...</p>
        ) : keywords.length === 0 ? (
          <p style={{ color: "var(--text-muted)" }}>미분류 키워드가 없습니다. 👍</p>
        ) : (
          <table className="genre-editor-table">
            <thead>
              <tr>
                <th>키워드</th>
                <th>관심 사용자</th>
                <th>대분류</th>
                <th>중분류</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {keywords.map((kw) => (
                <tr key={kw.keyword_id}>
                  <td>{kw.name}</td>
                  <td title={kw.interested_users.join(", ")}>
                    {kw.interested_count > 0
                      ? `${kw.interested_count}명 (${kw.interested_users.join(", ")})`
                      : "-"}
                  </td>
                  <td>
                    <input
                      type="text"
                      placeholder="예: AI"
                      value={drafts[kw.keyword_id]?.major || ""}
                      onChange={(e) => updateDraft(kw.keyword_id, "major", e.target.value)}
                      className="genre-editor-inline-input"
                    />
                  </td>
                  <td>
                    <input
                      type="text"
                      placeholder="예: 챗봇"
                      value={drafts[kw.keyword_id]?.mid || ""}
                      onChange={(e) => updateDraft(kw.keyword_id, "mid", e.target.value)}
                      className="genre-editor-inline-input"
                    />
                  </td>
                  <td>
                    <button
                      onClick={() => handleClassify(kw)}
                      disabled={submittingId === kw.keyword_id}
                      className="collect-btn"
                    >
                      {submittingId === kw.keyword_id ? "저장 중..." : "분류 저장"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}

