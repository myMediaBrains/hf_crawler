import { useEffect, useState, useCallback } from "react";

const API_BASE = "http://localhost:8000";

/**
 * CrawlToggleButton
 * 백그라운드 크롤링(스케줄러 틱)을 즉시 정지/재개하는 토글 버튼.
 *
 * 동작 방식:
 * - 마운트 시 GET /scheduler/status로 현재 일시정지 여부를 가져와 초기 상태를 맞춘다.
 * - 클릭 시 POST /scheduler/pause 또는 /scheduler/resume 호출 후 버튼 상태를 즉시 반영한다.
 * - "정지"는 이미 진행 중인 개별 기사 크롤링을 즉시 끊지는 않는다. job_control.is_paused()가
 *   다음 스케줄러 틱부터 건너뛰게 하는 방식이라, 최대 한 틱(tick_minutes)만큼의 지연 후
 *   실제로 새 수집이 시작되지 않는다. 즉시 중단이 필요하면 기존 "파이프라인 수집 중단" 버튼
 *   (/collect/cancel, job_control.cancel_current_job)을 함께 사용해야 한다.
 */
export default function CrawlToggleButton() {
  const [paused, setPaused] = useState(null); // null = 아직 로딩 전
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/scheduler/status`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setPaused(data.paused);
      setError(null);
    } catch (e) {
      setError("상태를 불러오지 못했습니다. 재시도 중...");
      setTimeout(fetchStatus, 3000);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  const handleToggle = async () => {
    if (paused === null || loading) return;
    setLoading(true);
    setError(null);
    const endpoint = paused ? "/scheduler/resume" : "/scheduler/pause";
    try {
      const res = await fetch(`${API_BASE}${endpoint}`, { method: "POST" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setPaused((prev) => !prev);
    } catch (e) {
      setError("요청이 실패했습니다. 다시 시도해주세요.");
    } finally {
      setLoading(false);
    }
  };

  const label =
    paused === null ? "확인 중..." : paused ? "크롤링 재개" : "백그라운드 크롤링 중지";

  return (
    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
      <button
        onClick={handleToggle}
        disabled={paused === null || loading}
        style={{
          padding: "6px 14px",
          borderRadius: "6px",
          border: "1px solid",
          borderColor: paused ? "#2e7d32" : "#c62828",
          backgroundColor: paused ? "#e8f5e9" : "#ffebee",
          color: paused ? "#2e7d32" : "#c62828",
          fontWeight: 600,
          cursor: paused === null || loading ? "not-allowed" : "pointer",
          opacity: loading ? 0.6 : 1,
          transition: "background-color 0.15s ease",
        }}
        title={
          paused
            ? "클릭하면 백그라운드 수집을 재개합니다."
            : "클릭하면 다음 틱부터 백그라운드 수집을 건너뜁니다."
        }
      >
        {loading ? "처리 중..." : label}
      </button>
      {paused !== null && (
        <span style={{ fontSize: "12px", color: "#666" }}>
          {paused ? "⏸ 일시정지됨" : "● 수집 중"}
        </span>
      )}
      {error && <span style={{ fontSize: "12px", color: "#c62828" }}>{error}</span>}
    </div>
  );
}
