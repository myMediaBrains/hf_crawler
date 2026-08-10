// SourceEvaluation.jsx
// ---------------------------------------------------------------------------
// '출처 평가' 버튼 + '장르별 출처 평가' 패널.
//
// 화면 구성:
//   1) 제목 "장르별 출처 평가"
//   2) 스코어 평가기준 설명 (제목 바로 아래)
//   3) 장르 버튼들 ("종합 TOP 50" + 장르별 버튼) - 클릭한 뷰만 교체해서 보여줌
//      (여러 장르를 한 화면에 죽 나열해서 스크롤로 훑는 방식이 아니라,
//       버튼으로 뷰를 전환하는 방식)
//   4) 선택된 뷰의 표: 기본은 전체 종합 TOP 50 (순위/장르/출처/건수/스코어/상태),
//      장르 버튼을 누르면 그 장르 안에서의 순위표로 전환 (순위/출처/건수/스코어/상태)
//
// hf-frontend/src/ 에 이 파일 그대로 추가한 뒤, App.jsx에서
//   import SourceEvaluation from "./SourceEvaluation";
// 로 불러와 "⚙️ 출처 관리" 버튼 옆에 <SourceEvaluation /> 를 렌더링하면 됩니다.
// ---------------------------------------------------------------------------

import { useState, useMemo, useEffect } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

const SCORE_CRITERIA_TEXT =
  "스코어는 100점 만점 — 수집 건수(최대 40점, 많을수록 유리하나 로그 스케일로 완만하게 반영) · " +
  "안정성(최대 30점, 수집 실패가 적을수록 高점) · 최신성(최대 15점, 최근에 수집 성공했을수록 高점) · " +
  "콘텐츠 밀도(최대 15점, 평균 본문 길이가 길수록 高점)를 합산해서 계산합니다.";

function ScoreBar({ score }) {
  const pct = Math.min(100, Math.max(0, score));
  const color = pct >= 70 ? "#2e7d32" : pct >= 40 ? "#f9a825" : "#c62828";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <div style={{ background: "rgba(255,255,255,0.1)", borderRadius: 4, height: 8, width: 70 }}>
        <div style={{ width: `${pct}%`, background: color, height: "100%", borderRadius: 4 }} />
      </div>
      <span style={{ fontSize: 13, fontVariantNumeric: "tabular-nums" }}>{score}</span>
    </div>
  );
}

export default function SourceEvaluation({ embedded = false }) {
  // 2026-08-10: embedded=true면(장르편집기 탭 안에 끼워 넣을 때) 자체 버튼/오버레이
  // 없이 항상 열린 상태로 내용만 렌더링한다.
  const [open, setOpen] = useState(embedded);
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [selectedGenre, setSelectedGenre] = useState(null); // null = 종합 TOP 50

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/sources/evaluation`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      setData(json);
      setSelectedGenre(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const handleOpen = async () => {
    setOpen(true);
    if (!data) await refresh();
  };

  useEffect(() => {
    if (embedded && !data) {
      refresh();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [embedded]);

  // 전체 장르를 합쳐 스코어 기준 내림차순 정렬 후 상위 50개만 추출.
  // 백엔드 /sources/evaluation은 장르(카테고리)별로 이미 나뉘어 오므로,
  // 여기서 한 번 펼쳐서(flat) 다시 정렬한다.
  const overallTop50 = useMemo(() => {
    if (!data) return [];
    const flat = data.categories.flatMap((cat) =>
      cat.sources.map((s) => ({ ...s, genre: cat.category }))
    );
    return [...flat]
      .sort((a, b) => b.score - a.score)
      .slice(0, 50)
      .map((s, i) => ({ ...s, overallRank: i + 1 }));
  }, [data]);

  const genreView = useMemo(() => {
    if (!data || !selectedGenre) return null;
    return data.categories.find((c) => c.category === selectedGenre) || null;
  }, [data, selectedGenre]);

  // embedded 모드일 때 실제로 렌더링할 내용물 (버튼/오버레이/패널 껍데기 없이)
  const content = (
    <>
      <div className="source-eval-header">
        <h3>장르별 출처 평가</h3>
        <div>
          <button onClick={refresh} disabled={loading}>
            {loading ? "갱신 중..." : "새로고침"}
          </button>
          {!embedded && (
            <button onClick={() => setOpen(false)} style={{ marginLeft: 8 }}>
              닫기
            </button>
          )}
        </div>
      </div>

            <p className="source-eval-criteria">{SCORE_CRITERIA_TEXT}</p>

            {error && <p style={{ color: "#f87171" }}>불러오기 실패: {error}</p>}
            {loading && !data && <p>불러오는 중...</p>}

            {data && (
              <>
                <div className="source-eval-genre-buttons">
                  <button
                    className={
                      "source-eval-genre-btn" + (selectedGenre === null ? " active" : "")
                    }
                    onClick={() => setSelectedGenre(null)}
                  >
                    종합 TOP 50
                  </button>
                  {data.categories.map((cat) => (
                    <button
                      key={cat.category}
                      className={
                        "source-eval-genre-btn" +
                        (selectedGenre === cat.category ? " active" : "")
                      }
                      onClick={() => setSelectedGenre(cat.category)}
                    >
                      {cat.category} ({cat.source_count})
                    </button>
                  ))}
                </div>

                {selectedGenre === null ? (
                  <div className="source-eval-table-wrap">
                    <table className="source-eval-table">
                      <thead>
                        <tr>
                          <th>순위</th>
                          <th>장르</th>
                          <th>출처</th>
                          <th>건수</th>
                          <th>스코어</th>
                          <th>상태</th>
                        </tr>
                      </thead>
                      <tbody>
                        {overallTop50.map((s) => (
                          <tr key={`${s.genre}-${s.id}`}>
                            <td>{s.overallRank}</td>
                            <td>{s.genre}</td>
                            <td>
                              <a href={s.url} target="_blank" rel="noreferrer">{s.name}</a>
                            </td>
                            <td>{s.article_count}</td>
                            <td><ScoreBar score={s.score} /></td>
                            <td>{s.status === "failing" ? "⚠️ 실패누적" : "정상"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : genreView ? (
                  <div className="source-eval-table-wrap">
                    <table className="source-eval-table">
                      <thead>
                        <tr>
                          <th>순위</th>
                          <th>출처</th>
                          <th>건수</th>
                          <th>스코어</th>
                          <th>상태</th>
                        </tr>
                      </thead>
                      <tbody>
                        {genreView.sources.map((s) => (
                          <tr key={s.id}>
                            <td>{s.rank}</td>
                            <td>
                              <a href={s.url} target="_blank" rel="noreferrer">{s.name}</a>
                            </td>
                            <td>{s.article_count}</td>
                            <td><ScoreBar score={s.score} /></td>
                            <td>{s.status === "failing" ? "⚠️ 실패누적" : "정상"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : null}
              </>
            )}
    </>
  );

  // 2026-08-10: embedded면 버튼/오버레이 껍데기 없이 content만 그대로 반환.
  // 아니면(기존 독립 사용) 원래대로 버튼 + 오버레이 + 패널로 감싼다.
  if (embedded) {
    return content;
  }

  return (
    <>
      <button onClick={handleOpen} className="source-eval-btn">
        출처 평가
      </button>

      {open && (
        <div className="source-eval-overlay" onClick={() => setOpen(false)}>
          <div className="source-eval-panel" onClick={(e) => e.stopPropagation()}>
            {content}
          </div>
        </div>
      )}
    </>
  );
}
