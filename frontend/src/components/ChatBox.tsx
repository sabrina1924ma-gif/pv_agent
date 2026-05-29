import { useState, useEffect, type FC } from "react";
import ReactMarkdown from "react-markdown";
import { useWebSocket } from "../hooks/useWebSocket";
import { sendChatMessage, deleteSession, getSessionId, downloadReportPdf } from "../api/client";
import type { ChatResponse } from "../types";

/** 电站列表（与后端 mock 数据对齐） */
const STATION_OPTIONS = [
  { id: "station-001", name: "1号电站 — 甘肃敦煌" },
  { id: "station-002", name: "2号电站 — 江苏盐城" },
  { id: "station-003", name: "3号电站 — 青海海南州" },
  { id: "station-004", name: "4号电站 — 浙江宁波" },
  { id: "station-005", name: "5号电站 — 河北张家口" },
] as const;

interface ChatBoxProps {
  sessionId: string;
}

const ChatBox: FC<ChatBoxProps> = ({ sessionId }) => {
  const [input, setInput] = useState("");
  const [stationId, setStationId] = useState<string>("");
  /** 报告类意图：对应后端 report_generator / multi_station_summary 工具 */
  const REPORT_INTENTS = new Set(["report_generator", "multi_station_summary"]);

  const [messages, setMessages] = useState<
    Array<{ role: string; content: string; isReport?: boolean }>
  >([]);
  const [loading, setLoading] = useState(false);
  const [streamingContent, setStreamingContent] = useState("");
  const [pdfDownloading, setPdfDownloading] = useState(false);

  const {
    isConnected,
    tokens,
    reportMd,
    lastIntent,
    toolCalls,
    lastError,
    connect,
    send,
    disconnect,
    clearState,
  } = useWebSocket(sessionId);

  // 挂载时连接 WebSocket
  useEffect(() => {
    connect();
  }, [connect]);

  // 新 token 到达时更新流式内容
  useEffect(() => {
    if (tokens.length > 0) {
      setStreamingContent(tokens.join(""));
    }
  }, [tokens]);

  // 报告就绪时完成助手消息（仅报告意图打 isReport 标记）
  useEffect(() => {
    if (reportMd) {
      const isReport = lastIntent ? REPORT_INTENTS.has(lastIntent) : false;
      setMessages((prev) => [...prev, { role: "assistant", content: reportMd, isReport }]);
      setStreamingContent("");
      setLoading(false);
      clearState();
    }
  }, [reportMd, clearState, lastIntent]);

  // 处理 WebSocket 错误
  useEffect(() => {
    if (lastError) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `错误: ${lastError}` },
      ]);
      setLoading(false);
    }
  }, [lastError]);

  const handleSend = async () => {
    if (!input.trim()) return;

    const userMsg = { role: "user", content: input };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);
    setStreamingContent("");

    if (isConnected) {
      // WebSocket 路径：发送消息，状态更新通过事件到达
      clearState();
      send(input, stationId || undefined);
    } else {
      // HTTP 降级路径：直接调用 REST API
      try {
        const resp = await sendChatMessage(input, stationId || undefined);
        const data: ChatResponse = await resp.json();
        const isReport = data.intent
          ? REPORT_INTENTS.has(data.intent)
          : false;
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: data.report_md || data.message, isReport },
        ]);
      } catch (err) {
        console.error("HTTP 降级失败:", err);
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: `请求失败: ${err}` },
        ]);
      } finally {
        setLoading(false);
      }
    }
  };

  const handleDownloadPdf = async (markdown: string) => {
    setPdfDownloading(true);
    try {
      await downloadReportPdf(markdown);
    } catch (err) {
      console.error("PDF 下载失败:", err);
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `PDF 生成失败: ${err}` },
      ]);
    } finally {
      setPdfDownloading(false);
    }
  };

  const handleClear = async () => {
    try {
      await deleteSession(sessionId);
    } catch {
      // 静默失败
    }
    setMessages([]);
    clearState();
    // 断开并重建 WebSocket，生成新 session
    disconnect();
    const newId = getSessionId();
    localStorage.setItem("pv_session_id", newId);
    window.location.reload();
  };

  return (
    <div className="chat-container">
      {/* 工具栏 */}
      <div className="toolbar">
        <select
          value={stationId}
          onChange={(e) => setStationId(e.target.value)}
          className="station-select"
        >
          <option value="">全部电站</option>
          {STATION_OPTIONS.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <div className="toolbar-right">
          <span className={`ws-status ${isConnected ? "online" : "offline"}`}>
            {isConnected ? "● 在线" : "○ 离线"}
          </span>
          <button
            onClick={handleClear}
            className="btn-clear"
            disabled={loading}
          >
            清除会话
          </button>
        </div>
      </div>

      {/* 消息区域 */}
      <div className="messages">
        {messages.map((msg, i) => (
          <div key={i} className={`message ${msg.role}`}>
            {msg.role === "assistant" ? (
              <>
                <ReactMarkdown>{msg.content}</ReactMarkdown>
                {msg.isReport && (
                  <button
                    className="btn-download-pdf"
                    onClick={() => handleDownloadPdf(msg.content)}
                    disabled={pdfDownloading}
                    title="下载为 PDF"
                  >
                    {pdfDownloading ? "生成中..." : "📥 PDF"}
                  </button>
                )}
              </>
            ) : (
              <p>{msg.content}</p>
            )}
          </div>
        ))}

        {/* 工具调用指示器 */}
        {toolCalls.length > 0 && (
          <div className="tool-calls">
            {toolCalls.map((tc, i) => (
              <div key={i} className={`tool-call ${tc.status || "pending"}`}>
                {tc.status === "ok"
                  ? `[OK] ${tc.toolName} (${tc.elapsedMs}ms)`
                  : tc.status === "error"
                  ? `[FAIL] ${tc.toolName}`
                  : `[RUN] ${tc.toolName}...`}
              </div>
            ))}
          </div>
        )}

        {/* 流式助手内容 */}
        {loading && streamingContent && (
          <div className="message assistant streaming">
            <ReactMarkdown>{streamingContent}</ReactMarkdown>
          </div>
        )}

        {/* 加载指示器 */}
        {loading && !streamingContent && (
          <div className="loading">Agent is thinking...</div>
        )}
      </div>

      {/* 输入区域 */}
      <div className="input-area">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="请输入您的查询，例如：查看3号电站的实时状态..."
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
        />
        <button onClick={handleSend} disabled={loading || !input.trim()}>
          {loading ? "处理中..." : "发送"}
        </button>
      </div>

      <style>{`
        .chat-container {
          display: flex;
          flex-direction: column;
          height: calc(100vh - 250px);
          border: 1px solid #e0e0e0;
          border-radius: 12px;
          overflow: hidden;
          background: #fafafa;
        }

        .toolbar {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 0.5rem 1rem;
          border-bottom: 1px solid #e0e0e0;
          background: white;
          flex-shrink: 0;
        }

        .toolbar-right {
          display: flex;
          align-items: center;
          gap: 0.75rem;
        }

        .station-select {
          padding: 0.35rem 0.5rem;
          border: 1px solid #ccc;
          border-radius: 6px;
          font-size: 0.85rem;
          background: white;
        }

        .btn-clear {
          padding: 0.35rem 0.75rem;
          border: 1px solid #dc3545;
          background: white;
          color: #dc3545;
          border-radius: 6px;
          cursor: pointer;
          font-size: 0.8rem;
        }

        .btn-clear:hover:not(:disabled) {
          background: #dc3545;
          color: white;
        }

        .btn-clear:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }

        .ws-status {
          font-size: 0.8rem;
        }

        .ws-status.online {
          color: #28a745;
        }

        .ws-status.offline {
          color: #dc3545;
        }

        .messages {
          flex: 1;
          overflow-y: auto;
          padding: 1rem;
          display: flex;
          flex-direction: column;
          gap: 0.5rem;
        }

        .message {
          max-width: 80%;
          padding: 0.75rem 1rem;
          border-radius: 12px;
          word-wrap: break-word;
          white-space: pre-wrap;
        }

        .message.user {
          background: #007bff;
          color: white;
          align-self: flex-end;
          border-bottom-right-radius: 4px;
        }

        .message.assistant {
          background: #e9ecef;
          color: #333;
          align-self: flex-start;
          border-bottom-left-radius: 4px;
        }

        .btn-download-pdf {
          display: inline-block;
          margin-top: 0.5rem;
          padding: 0.25rem 0.75rem;
          border: 1px solid #2980b9;
          background: white;
          color: #2980b9;
          border-radius: 6px;
          cursor: pointer;
          font-size: 0.8rem;
          transition: all 0.15s;
        }

        .btn-download-pdf:hover:not(:disabled) {
          background: #2980b9;
          color: white;
        }

        .btn-download-pdf:disabled {
          opacity: 0.6;
          cursor: not-allowed;
        }

        .message.assistant.streaming {
          opacity: 0.85;
        }

        .message p {
          margin: 0;
        }

        .message p:first-child {
          margin-top: 0;
        }

        .message p:last-child {
          margin-bottom: 0;
        }

        .message pre {
          background: #f4f4f4;
          padding: 0.5rem;
          border-radius: 4px;
          overflow-x: auto;
        }

        .message code {
          background: #f0f0f0;
          padding: 0.125rem 0.25rem;
          border-radius: 3px;
          font-size: 0.9em;
        }

        .message ul,
        .message ol {
          margin: 0.25rem 0;
          padding-left: 1.25rem;
        }

        .message table {
          border-collapse: collapse;
          width: 100%;
          font-size: 0.9em;
        }

        .message th,
        .message td {
          border: 1px solid #ccc;
          padding: 0.35rem 0.5rem;
          text-align: left;
        }

        .message th {
          background: #e9ecef;
        }

        .tool-calls {
          align-self: flex-start;
          display: flex;
          flex-direction: column;
          gap: 0.25rem;
          margin: 0.25rem 0 0.25rem 1rem;
        }

        .tool-call {
          font-size: 0.8rem;
          padding: 0.25rem 0.5rem;
          border-radius: 4px;
          background: #f8f9fa;
          font-family: monospace;
        }

        .tool-call.pending {
          color: #6c757d;
        }

        .tool-call.ok {
          color: #28a745;
        }

        .tool-call.error {
          color: #dc3545;
        }

        .loading {
          align-self: flex-start;
          color: #6c757d;
          font-size: 0.9rem;
          padding: 0.5rem;
          font-style: italic;
        }

        .input-area {
          display: flex;
          gap: 0.5rem;
          padding: 1rem;
          border-top: 1px solid #e0e0e0;
          background: white;
        }

        .input-area textarea {
          flex: 1;
          padding: 0.75rem;
          border: 1px solid #ccc;
          border-radius: 8px;
          resize: none;
          font-family: inherit;
          font-size: 0.95rem;
          line-height: 1.4;
          min-height: 44px;
          max-height: 120px;
        }

        .input-area textarea:focus {
          outline: none;
          border-color: #007bff;
          box-shadow: 0 0 0 2px rgba(0, 123, 255, 0.15);
        }

        .input-area button {
          padding: 0.75rem 1.5rem;
          background: #007bff;
          color: white;
          border: none;
          border-radius: 8px;
          cursor: pointer;
          font-size: 0.95rem;
          white-space: nowrap;
          align-self: flex-end;
        }

        .input-area button:hover:not(:disabled) {
          background: #0056b3;
        }

        .input-area button:disabled {
          background: #ccc;
          cursor: not-allowed;
        }

        @media (max-width: 640px) {
          .chat-container {
            height: calc(100vh - 180px);
            border-radius: 0;
            border-left: none;
            border-right: none;
          }

          .message {
            max-width: 90%;
          }

          .toolbar {
            flex-direction: column;
            gap: 0.5rem;
            align-items: flex-start;
          }
        }
      `}</style>
    </div>
  );
};

export default ChatBox;
