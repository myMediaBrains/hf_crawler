// IntervalSettings.jsx
// ---------------------------------------------------------------------------
// '검색주기설정' 탭 (데이터편집 안).
//
// 2026-08-11: 개별 키워드별로 "최근 N개월/N시간마다"를 따로 설정하던 방식을
// 폐기하고, 여기서 저장한 값이 모든 키워드에 일괄 적용되도록 변경. 그래서
// 키워드 이름 입력창을 없앴다 - 더 이상 "어떤 키워드"를 특정할 필요가 없다.
//
// PUT /keywords/interval/bulk 를 호출한다 (전체 키워드 일괄 갱신 전용
// 엔드포인트 - 기존 PUT /keywords/interval는 키워드 하나만 갱신하는 것이라
// 이 용도엔 안 맞아서 새로 추가해야 함, main_bulk_interval_endpoint_snippet.py
// 참고).
//
// onSaved: 저장 성공 시 호출되는 콜백. GenreEditor가 이걸 받아서 저장 후
// "장르 목록" 탭으로 돌아가게 만든다.
// ---------------------------------------------------------------------------

import { useState } from "react";
import axios from "axios";
import { toast } from "react-hot-toast";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

export default function IntervalSettings({ onSaved }) {
    const [monthsBack, setMonthsBack] = useState(1);
    const [intervalHours, setIntervalHours] = useState(24);
    const [submitting, setSubmitting] = useState(false);

    const handleSubmit = async (e) => {
        e.preventDefault();
        setSubmitting(true);
        const toastId = toast.loading("전체 키워드에 일괄 적용 중...");
        try {
            const res = await axios.put(`${API_BASE}/keywords/interval/bulk`, {
                months_back: monthsBack,
                interval_hours: intervalHours,
            });
            toast.success(res.data.message, { id: toastId });
            if (onSaved) onSaved();
        } catch (err) {
            console.error("검색 주기 일괄 설정 에러:", err);
            toast.error(err.response?.data?.detail || "일괄 적용 중 오류가 발생했습니다.", { id: toastId });
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <form onSubmit={handleSubmit} className="genre-editor-form">
            <p style={{ width: "100%", margin: "0 0 10px 0", fontSize: 13, color: "var(--text-muted)" }}>
                여기서 저장하면 <strong>등록된 모든 키워드</strong>에 아래 값이 일괄 적용됩니다.
                (개별 키워드마다 따로 설정하지 않습니다.)
            </p>
            <label style={{ display: "flex", alignItems: "center", gap: "4px", whiteSpace: "nowrap" }}>
                최근
                <input
                    type="number"
                    min="1"
                    value={monthsBack}
                    onChange={(e) => setMonthsBack(Number(e.target.value))}
                    style={{ width: "60px" }}
                />
                개월 이내
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: "4px", whiteSpace: "nowrap" }}>
                <input
                    type="number"
                    min="1"
                    value={intervalHours}
                    onChange={(e) => setIntervalHours(Number(e.target.value))}
                    style={{ width: "60px" }}
                />
                시간마다
            </label>
            <button type="submit" className="collect-btn" disabled={submitting}>
                {submitting ? "적용 중..." : "저장"}
            </button>
        </form>
    );
}
