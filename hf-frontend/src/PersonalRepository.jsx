// PersonalRepository.jsx
// ---------------------------------------------------------------------------
// '{사용자} 저장소' 메인화면 버튼 + 모달.
// 표: 폴더 / 파일명(클릭→Typora) / 원래 자료 제목 / 마지막 업데이트 일시
//
// 목록은 GET /vault/files, 파일 열기는 POST /vault/open-in-typora.
// Vault 파일은 실제 디스크의 .md라서, Typora에서 편집하고 저장하면 그 파일
// 자체가 최신 상태가 된다 - 원본 DB로 다시 가져오는 절차는 필요 없다
// (Vault는 애초에 'DB와 무관한 사본 저장소'이기 때문).
// ---------------------------------------------------------------------------

import { useState } from "react";
import axios from "axios";
import { toast } from "react-hot-toast";
import useCurrentUser from "./useCurrentUser";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

export default function PersonalRepository() {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [files, setFiles] = useState([]);
  const displayName = useCurrentUser();
  const userId = localStorage.getItem("hf_user_id") || null;

  const refresh = async () => {
    if (!userId) {
      toast.error("사용자 등록/로그인 후 이용할 수 있습니다.");
      return;
    }
    setLoading(true);
    try {
      const res = await axios.post(`${API_BASE}/vault/sync`, null, { params: { user_id: userId } });
      setFiles(res.data.files || []);
      toast.success(res.data.message);
    } catch (err) {
      console.error("개인저장소 동기화 에러:", err);
      toast.error("동기화에 실패했습니다.");
    } finally {
      setLoading(false);
    }
  };

  const handleOpen = async () => {
    setOpen(true);
    await refresh();
  };

  const handleOpenFile = async (file) => {
    try {
      await axios.post(`${API_BASE}/vault/open-in-typora`, {
        folder: file.folder,
        filename: file.filename,
        user_id: userId,
      });
    } catch (err) {
      console.error("Typora 열기 에러:", err);
      toast.error(err.response?.data?.detail || "Typora에서 여는 데 실패했습니다.");
    }
  };

  return (
    <>
      <button onClick={handleOpen} className="collect-btn personal-repo-btn">
        📂 {displayName} 저장소
      </button>

      {open && (
        <div className="genre-editor-overlay" onClick={() => setOpen(false)}>
          <div className="genre-editor-panel" onClick={(e) => e.stopPropagation()}>
            <div className="genre-editor-header">
              <h3>
                개인저장소 <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>- {displayName}</span>
              </h3>
              <div>
                <button onClick={refresh} disabled={loading}>
                  {loading ? "동기화 중..." : "새로고침"}
                </button>
                <button onClick={() => setOpen(false)} style={{ marginLeft: 8 }}>
                  닫기
                </button>
              </div>
            </div>

            <div className="genre-editor-table-wrap">
              {loading ? (
                <p>불러오는 중...</p>
              ) : files.length === 0 ? (
                <p style={{ color: "var(--text-muted)" }}>
                  저장된 자료가 없습니다. 기사나 GitHub 레포 상세에서 "저장소에 저장"을 눌러보세요.
                </p>
              ) : (
                <table className="genre-editor-table">
                  <thead>
                    <tr>
                      <th>폴더</th>
                      <th>파일명</th>
                      <th>원래 자료 제목</th>
                      <th>마지막 업데이트</th>
                    </tr>
                  </thead>
                  <tbody>
                    {files.map((f) => (
                      <tr key={`${f.folder}/${f.filename}`}>
                        <td>{f.folder}</td>
                        <td>
                          <a
                            href="#"
                            onClick={(e) => {
                              e.preventDefault();
                              handleOpenFile(f);
                            }}
                            title="클릭하면 Typora에서 열립니다"
                          >
                            {f.filename}
                          </a>
                        </td>
                        <td>{f.title}</td>
                        <td>{f.last_modified}</td>
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
