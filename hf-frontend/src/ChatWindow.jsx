import { useEffect, useRef, useState } from "react";

const TEXT_API = "http://localhost:8001";
const MAIN_API = "http://localhost:8000";
const CONV_STORAGE_KEY = "hf_conversation_id";
const NTFY_TOPIC_STORAGE_KEY = "hf_ntfy_topic";

function getUserId() {
  return localStorage.getItem("hf_user_id") || null;
}

// LLM이 응답을 생성하는 동안 보여줄 깜빡이는 점 3개. 채팅창 안에서 "지금 뭔가
// 하고 있다"를 시각적으로 알려준다 (HEAVY 티어라 콜드로드 시 최대 35초 걸릴 수
// 있어서, 이 표시가 없으면 멈춘 것처럼 보일 수 있다).
function TypingDots() {
  return (
    <span style={{ display: "inline-flex", gap: "4px", alignItems: "center", padding: "2px 0" }}>
      <style>{`
        @keyframes hf-typing-blink {
          0%, 80%, 100% { opacity: 0.2; transform: scale(0.85); }
          40% { opacity: 1; transform: scale(1); }
        }
      `}</style>
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          style={{
            width: "6px",
            height: "6px",
            borderRadius: "50%",
            background: "#94a3b8",
            animation: "hf-typing-blink 1.2s infinite ease-in-out",
            animationDelay: `${i * 0.15}s`,
          }}
        />
      ))}
    </span>
  );
}

export default function ChatWindow() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([]); // { id, query, message, longReport?, expanding? }
  const [inputValue, setInputValue] = useState("");
  const [sending, setSending] = useState(false);
  const inputRef = useRef(null);
  const conversationIdRef = useRef(localStorage.getItem(CONV_STORAGE_KEY) || null);
  const scrollRef = useRef(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, open]);

  const handleSend = async (e) => {
    e.preventDefault();
    const query = inputValue.trim();
    if (!query || sending) return;
    setSending(true);
    setInputValue("");

    // 응답을 기다리는 동안 채팅창에 즉시 "대기 중" 버블을 먼저 넣는다.
    // 실제 응답이 오면 같은 id를 찾아 내용으로 교체한다.
    const pendingId = `pending-${Date.now()}`;
    setMessages((prev) => [...prev, { id: pendingId, query, message: null, pending: true, longReport: null }]);

    try {
      const res = await fetch(`${TEXT_API}/chat/short`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query,
          user_id: getUserId(),
          conversation_id: conversationIdRef.current,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

      conversationIdRef.current = data.conversation_id;
      localStorage.setItem(CONV_STORAGE_KEY, data.conversation_id);

      setMessages((prev) =>
        prev.map((m) =>
          m.id === pendingId
            ? {
                id: data.generation_id,
                query,
                message: data.message,
                longReport: null,
                expanding: false,
                insufficientEvidence: !!data.insufficient_evidence,
              }
            : m
        )
      );
    } catch (err) {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === pendingId
            ? { ...m, pending: false, message: `⚠️ 응답 실패: ${err.message}` }
            : m
        )
      );
    } finally {
      setSending(false);
      inputRef.current?.focus();
    }
  };

  const handleExpand = async (msg) => {
    setMessages((prev) => prev.map((m) => (m.id === msg.id ? { ...m, expanding: true } : m)));
    try {
      const res = await fetch(`${TEXT_API}/chat/expand/${msg.id}`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      setMessages((prev) =>
        prev.map((m) =>
          m.id === msg.id
            ? { ...m, expanding: false, longReport: { id: data.generation_id, markdown: data.report_markdown } }
            : m
        )
      );
    } catch (err) {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === msg.id ? { ...m, expanding: false, expandError: err.message } : m
        )
      );
    }
  };

  const handleDeliverNtfy = async (generationId) => {
    let topic = localStorage.getItem(NTFY_TOPIC_STORAGE_KEY);
    if (!topic) {
      topic = window.prompt(
        "ntfy.sh topic을 입력하세요 (스마트폰 ntfy 앱에서 구독할 이름 - 아무 문자열이나 가능, 추측하기 어려운 걸 권장):"
      );
      if (!topic) return;
      localStorage.setItem(NTFY_TOPIC_STORAGE_KEY, topic);
    }
    try {
      const res = await fetch(`${MAIN_API}/deliver`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ generation_id: generationId, channel: "ntfy", target: topic }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      window.alert(`ntfy(${topic})로 보냈습니다. 스마트폰에서 ntfy 앱으로 같은 topic을 구독해보세요.`);
    } catch (err) {
      window.alert(`ntfy 발송 실패: ${err.message}`);
    }
  };

  const handleDeliverEmail = async (generationId) => {
    try {
      const res = await fetch(`${MAIN_API}/deliver`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ generation_id: generationId, channel: "email" }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      window.location.href = data.mailto_url;
    } catch (err) {
      window.alert(`메일 준비 실패: ${err.message}`);
    }
  };

  return (
    <div style={{ position: "fixed", right: "20px", bottom: "20px", zIndex: 1000 }}>
      {!open && (
        <button
          onClick={() => setOpen(true)}
          style={{
            width: "52px", height: "52px", borderRadius: "50%", border: "none",
            background: "#0d9488", color: "#fff", fontSize: "22px", cursor: "pointer",
            boxShadow: "0 4px 10px rgba(0,0,0,0.3)",
          }}
        >
          💬
        </button>
      )}

      {open && (
        <div
          style={{
            width: "380px", maxWidth: "90vw", height: "520px", maxHeight: "80vh",
            background: "#1e293b", border: "1px solid #334155", borderRadius: "12px",
            display: "flex", flexDirection: "column", boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
          }}
        >
          <div style={{
            display: "flex", justifyContent: "space-between", alignItems: "center",
            padding: "10px 14px", borderBottom: "1px solid #334155",
          }}>
            <strong style={{ fontSize: "13px", color: "#e2e8f0" }}>💬 대화</strong>
            <button onClick={() => setOpen(false)} style={{ background: "transparent", border: "none", color: "#94a3b8", cursor: "pointer" }}>✕</button>
          </div>

          <div ref={scrollRef} style={{ flex: 1, overflowY: "auto", padding: "10px 14px", display: "flex", flexDirection: "column", gap: "12px" }}>
            {messages.length === 0 && (
              <div style={{ color: "#64748b", fontSize: "12.5px", textAlign: "center", marginTop: "20px" }}>
                무엇이든 물어보세요. 먼저 짧은 답을 드리고,<br />마음에 들면 장문 보고서로 확장할 수 있어요.
              </div>
            )}
            {messages.map((m) => (
              <div key={m.id} style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                <div style={{ alignSelf: "flex-end", fontSize: "12.5px", color: "#94a3b8", background: "#334155", padding: "6px 10px", borderRadius: "10px", maxWidth: "85%" }}>
                  {m.query}
                </div>

                {m.pending ? (
                  <div style={{ alignSelf: "flex-start", background: "#0f172a", padding: "8px 12px", borderRadius: "10px" }}>
                    <TypingDots />
                  </div>
                ) : (
                  <div style={{ alignSelf: "flex-start", fontSize: "13px", color: "#e2e8f0", background: "#0f172a", padding: "8px 12px", borderRadius: "10px", maxWidth: "90%" }}>
                    {m.message}
                    {m.insufficientEvidence && (
                      <div style={{ fontSize: "11px", color: "#fbbf24", marginTop: "4px" }}>
                        📡 관련 자료 수집을 요청했습니다
                      </div>
                    )}
                    {typeof m.id === "number" && !m.insufficientEvidence && (
                      <div style={{ display: "flex", gap: "6px", marginTop: "6px", flexWrap: "wrap" }}>
                        {!m.longReport && (
                          <button
                            onClick={() => handleExpand(m)}
                            disabled={m.expanding}
                            style={{ fontSize: "11px", padding: "3px 8px", borderRadius: "6px", border: "1px solid #475569", background: "transparent", color: "#94a3b8", cursor: "pointer" }}
                          >
                            📄 장문 보고서 보기
                          </button>
                        )}
                        <button
                          onClick={() => handleDeliverNtfy(m.id)}
                          style={{ fontSize: "11px", padding: "3px 8px", borderRadius: "6px", border: "1px solid #475569", background: "transparent", color: "#94a3b8", cursor: "pointer" }}
                        >
                          📨 ntfy로 보내기
                        </button>
                      </div>
                    )}
                    {m.expandError && <div style={{ fontSize: "11px", color: "#f87171", marginTop: "4px" }}>{m.expandError}</div>}
                  </div>
                )}

                {m.expanding && (
                  <div style={{ alignSelf: "flex-start", background: "#0f172a", border: "1px solid #334155", padding: "8px 12px", borderRadius: "10px" }}>
                    <TypingDots />
                    <span style={{ fontSize: "11px", color: "#64748b", marginLeft: "8px" }}>보고서 작성 중 (최대 35초)</span>
                  </div>
                )}

                {m.longReport && (
                  <div style={{ alignSelf: "flex-start", fontSize: "12.5px", color: "#e2e8f0", background: "#0f172a", border: "1px solid #334155", padding: "10px 12px", borderRadius: "10px", maxWidth: "95%", whiteSpace: "pre-wrap" }}>
                    {m.longReport.markdown}
                    <div style={{ display: "flex", gap: "6px", marginTop: "8px" }}>
                      <button
                        onClick={() => handleDeliverEmail(m.longReport.id)}
                        style={{ fontSize: "11px", padding: "3px 8px", borderRadius: "6px", border: "1px solid #475569", background: "transparent", color: "#94a3b8", cursor: "pointer" }}
                      >
                        📧 메일로 보내기
                      </button>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>

          <form onSubmit={handleSend} style={{ display: "flex", gap: "6px", padding: "10px 14px", borderTop: "1px solid #334155" }}>
            <input
              ref={inputRef}
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              placeholder="무엇이든 물어보세요..."
              style={{ flex: 1, padding: "6px 10px", borderRadius: "6px", border: "1px solid #475569", background: "#0f172a", color: "#e2e8f0", fontSize: "13px" }}
            />
            <button type="submit" disabled={sending} style={{ padding: "6px 12px", borderRadius: "6px", border: "none", background: "#0d9488", color: "#fff", fontSize: "13px", cursor: "pointer" }}>
              {sending ? "..." : "전송"}
            </button>
          </form>
        </div>
      )}
    </div>
  );
}