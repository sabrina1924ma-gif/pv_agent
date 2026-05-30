import { useState, useEffect, useRef, useCallback, type FC } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useWebSocket } from "../hooks/useWebSocket";
import {
  sendChatMessage,
  deleteSession,
  getSessionMessages,
} from "../api/client";
import type { ChatResponse, MessageItem, SessionMessagesResponse } from "../types";

/** 单条本地消息 */
interface LocalMessage {
  role: "user" | "assistant";
  content: string;
  isReport?: boolean;
}

// --- 电站选项 ---

const STATION_OPTIONS = [
  { id: "station-001", name: "1号电站 · 甘肃敦煌" },
  { id: "station-002", name: "2号电站 · 江苏盐城" },
  { id: "station-003", name: "3号电站 · 青海海南州" },
  { id: "station-004", name: "4号电站 · 浙江宁波" },
  { id: "station-005", name: "5号电站 · 河北张家口" },
] as const;

// --- 报告意图集合 ---

const REPORT_INTENTS = new Set([
  "report_generator",
  "multi_station_summary",
  "report",
  "life_assessment",
  "power_curve",
  "fault_detection",
]);

// ============================================================================

interface ChatBoxProps {
  sessionId: string;
  /** 会话清除后的回调（通知父组件刷新列表） */
  onSessionCleared?: () => void;
}

/** 将后端 MessageItem 转为前端 LocalMessage */
function toLocalMessage(msg: MessageItem): LocalMessage {
  const isReport = msg.intent ? REPORT_INTENTS.has(msg.intent) : false;
  return {
    role: msg.role === "user" ? "user" : "assistant",
    content: msg.content,
    isReport,
  };
}

const ChatBox: FC<ChatBoxProps> = ({ sessionId, onSessionCleared }) => {
  const [input, setInput] = useState("");
  const [stationId, setStationId] = useState<string>("");
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [streamingContent, setStreamingContent] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);

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

  // --- 从后端加载会话历史 ---
  useEffect(() => {
    let cancelled = false;

    async function loadHistory() {
      setLoadingHistory(true);
      try {
        const resp = await getSessionMessages(sessionId);
        if (!resp.ok || cancelled) return;
        const data: SessionMessagesResponse = await resp.json();
        if (cancelled) return;

        const msgs = data.messages
          .filter((m) => m.role === "user" || m.role === "assistant")
          .map(toLocalMessage);
        setMessages(msgs);
      } catch {
        // 后端不可用时静默回退到空消息列表
      } finally {
        if (!cancelled) setLoadingHistory(false);
      }
    }

    loadHistory();
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  // 自动滚底
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamingContent, toolCalls]);

  // 挂载 WebSocket
  useEffect(() => {
    connect();
  }, [connect]);

  // 流式 token
  useEffect(() => {
    if (tokens.length > 0) setStreamingContent(tokens.join(""));
  }, [tokens]);

  // 报告就绪
  useEffect(() => {
    if (reportMd) {
      const isReport = lastIntent ? REPORT_INTENTS.has(lastIntent) : false;
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: reportMd, isReport },
      ]);
      setStreamingContent("");
      setLoading(false);
      clearState();
    }
  }, [reportMd, clearState, lastIntent]);

  // 错误处理
  useEffect(() => {
    if (lastError) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `⚠️ 错误: ${lastError}` },
      ]);
      setLoading(false);
    }
  }, [lastError]);

  // --- 发送消息 ---

  const handleSend = useCallback(async () => {
    if (!input.trim()) return;

    const userMsg: LocalMessage = { role: "user", content: input };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);
    setStreamingContent("");

    if (isConnected) {
      clearState();
      send(input, stationId || undefined);
    } else {
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
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: `请求失败: ${err}` },
        ]);
      } finally {
        setLoading(false);
      }
    }
  }, [input, stationId, isConnected, send, clearState]);

  // --- 清除当前会话 ---

  const handleClear = async () => {
    // 先断开 WebSocket
    disconnect();
    clearState();

    // 尝试从后端删除
    try {
      await deleteSession(sessionId);
    } catch {
      /* ignore */
    }

    // 通知父组件（父组件会创建新会话并切换）
    onSessionCleared?.();
  };

  // ============================================================================
  // JSX
  // ============================================================================

  return (
    <div className="chat-shell">
      {/* ---- 工具栏 ---- */}
      <div className="chat-toolbar">
        <div className="toolbar-left">
          <select
            value={stationId}
            onChange={(e) => setStationId(e.target.value)}
            className="station-picker"
          >
            <option value="">🌐 全部电站</option>
            {STATION_OPTIONS.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
        </div>
        <div className="toolbar-right">
          <span className={`ws-dot ${isConnected ? "live" : "dead"}`} />
          <span className="ws-label">
            {isConnected ? "在线" : "离线"}
          </span>
          <button
            onClick={handleClear}
            className="btn-ghost"
            disabled={loading}
          >
            清除会话
          </button>
        </div>
      </div>

      {/* ---- 消息列表 ---- */}
      <div className="chat-messages">
        {loadingHistory && messages.length === 0 && (
          <div className="msg-row assistant">
            <div className="msg-bubble thinking">
              <span className="dot-pulse" />
              <span>加载历史消息…</span>
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`msg-row ${msg.role}`}>
            <div className="msg-bubble">
              {msg.role === "assistant" ? (
                <div className="markdown-body">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {msg.content}
                  </ReactMarkdown>
                </div>
              ) : (
                <p>{msg.content}</p>
              )}
            </div>
          </div>
        ))}

        {/* 工具调用指示器 */}
        {toolCalls.length > 0 && (
          <div className="tool-strip">
            {toolCalls.map((tc, i) => (
              <span
                key={i}
                className={`tool-chip ${tc.status === "ok" ? "ok" : tc.status === "error" ? "err" : "pending"}`}
              >
                {tc.status === "pending"
                  ? `⟳ ${tc.toolName}`
                  : tc.status === "ok"
                    ? `✓ ${tc.toolName} (${tc.elapsedMs}ms)`
                    : `✗ ${tc.toolName}`}
              </span>
            ))}
          </div>
        )}

        {/* 流式内容 */}
        {loading && streamingContent && (
          <div className="msg-row assistant">
            <div className="msg-bubble streaming">
              <div className="markdown-body">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {streamingContent}
                </ReactMarkdown>
              </div>
              <span className="cursor-blink">▌</span>
            </div>
          </div>
        )}

        {/* 加载占位 */}
        {loading && !streamingContent && (
          <div className="msg-row assistant">
            <div className="msg-bubble thinking">
              <span className="dot-pulse" />
              <span>正在分析...</span>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* ---- 输入区 ---- */}
      <div className="chat-input-bar">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="输入查询，例如：3号电站的运行状态和故障情况…"
          rows={2}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
        />
        <button
          onClick={handleSend}
          disabled={loading || !input.trim()}
          className="btn-send"
        >
          {loading ? "处理中" : "发送"}
        </button>
      </div>

      {/* ================================================================ */}
      {/*  STYLES                                                        */}
      {/* ================================================================ */}
      <style>{`
        /* ---------- shell ---------- */
        .chat-shell {
          display: flex;
          flex-direction: column;
          height: 100%;
          background: #060b14;
        }

        /* ---------- toolbar ---------- */
        .chat-toolbar {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 10px 20px;
          background: #0b1120;
          border-bottom: 1px solid #1a2740;
          flex-shrink: 0;
          gap: 12px;
        }
        .toolbar-left { display: flex; align-items: center; gap: 10px; }
        .toolbar-right { display: flex; align-items: center; gap: 14px; }

        .station-picker {
          background: #111b2e;
          color: #a0b4d0;
          border: 1px solid #1e3050;
          border-radius: 8px;
          padding: 7px 12px;
          font-size: 13px;
          font-family: inherit;
          cursor: pointer;
          outline: none;
          transition: border-color 0.2s;
        }
        .station-picker:focus {
          border-color: #00e5ff;
        }

        .ws-dot {
          width: 8px; height: 8px; border-radius: 50%;
          flex-shrink: 0;
        }
        .ws-dot.live {
          background: #00e676;
          box-shadow: 0 0 8px #00e67666;
        }
        .ws-dot.dead {
          background: #ff5252;
          box-shadow: 0 0 8px #ff525244;
        }
        .ws-label { font-size: 12px; color: #5a7290; }

        .btn-ghost {
          background: transparent;
          color: #6b7f9e;
          border: 1px solid #1e3050;
          border-radius: 7px;
          padding: 5px 14px;
          font-size: 12px;
          cursor: pointer;
          transition: all 0.2s;
          font-family: inherit;
        }
        .btn-ghost:hover:not(:disabled) {
          color: #ff6b6b;
          border-color: #ff6b6b44;
          background: #ff6b6b0d;
        }
        .btn-ghost:disabled { opacity: 0.4; cursor: not-allowed; }

        /* ---------- messages ---------- */
        .chat-messages {
          flex: 1;
          overflow-y: auto;
          padding: 20px 20px 8px;
          display: flex;
          flex-direction: column;
          gap: 10px;
        }

        .msg-row { display: flex; }
        .msg-row.user { justify-content: flex-end; }
        .msg-row.assistant { justify-content: flex-start; }

        .msg-bubble {
          max-width: 82%;
          padding: 14px 18px;
          border-radius: 14px;
          word-wrap: break-word;
          line-height: 1.65;
          font-size: 14px;
        }

        .msg-row.user .msg-bubble {
          background: linear-gradient(135deg, #0077ff, #0055cc);
          color: #fff;
          border-bottom-right-radius: 4px;
        }

        .msg-row.assistant .msg-bubble {
          background: #0d1525;
          border: 1px solid #1a2a45;
          border-bottom-left-radius: 4px;
          color: #c8d6e5;
        }

        .msg-bubble.streaming {
          display: flex;
          align-items: flex-end;
          gap: 4px;
          min-height: 24px;
        }
        .cursor-blink {
          color: #00e5ff;
          font-size: 16px;
          animation: blink 1s step-end infinite;
        }
        @keyframes blink {
          50% { opacity: 0; }
        }

        .msg-bubble.thinking {
          display: flex;
          align-items: center;
          gap: 10px;
          color: #5a7290;
          font-style: italic;
          font-size: 13px;
        }
        .dot-pulse {
          width: 8px; height: 8px;
          border-radius: 50%;
          background: #00e5ff;
          animation: pulse 1.4s ease-in-out infinite;
        }
        @keyframes pulse {
          0%, 100% { opacity: 0.3; transform: scale(0.8); }
          50% { opacity: 1; transform: scale(1.2); }
        }

        /* ---------- markdown body ---------- */
        .markdown-body { font-size: 14px; line-height: 1.7; }
        .markdown-body h1 {
          font-size: 1.4em; font-weight: 700; color: #e8f0fe;
          margin: 0 0 12px; padding-bottom: 8px;
          border-bottom: 1px solid #1e3050;
        }
        .markdown-body h2 {
          font-size: 1.15em; font-weight: 600; color: #00e5ff;
          margin: 18px 0 8px;
        }
        .markdown-body h3 {
          font-size: 1em; font-weight: 600; color: #b0c8e8;
          margin: 14px 0 6px;
        }
        .markdown-body p { margin: 0 0 8px; }
        .markdown-body ul, .markdown-body ol {
          margin: 6px 0 10px; padding-left: 20px;
        }
        .markdown-body li { margin-bottom: 3px; }
        .markdown-body strong { color: #e8f0fe; font-weight: 600; }

        /* ---------- TABLES ---------- */
        .markdown-body table {
          width: 100%;
          border-collapse: collapse;
          margin: 12px 0 16px;
          font-size: 13px;
          font-family: "JetBrains Mono", "Cascadia Code", "Fira Code", monospace;
          overflow: hidden;
          border-radius: 8px;
        }
        .markdown-body thead {
          background: linear-gradient(135deg, #0f2847, #0d1f35);
        }
        .markdown-body th {
          padding: 10px 14px;
          text-align: left;
          font-weight: 600;
          color: #00e5ff;
          font-size: 12px;
          text-transform: uppercase;
          letter-spacing: 0.5px;
          border-bottom: 2px solid #00e5ff33;
          white-space: nowrap;
        }
        .markdown-body td {
          padding: 8px 14px;
          border-bottom: 1px solid #1a2a4511;
          color: #b0c4dc;
          font-variant-numeric: tabular-nums;
        }
        .markdown-body tr:last-child td { border-bottom: none; }
        .markdown-body tbody tr:hover {
          background: #00e5ff06;
        }
        .markdown-body tbody tr:nth-child(even) {
          background: #0a152410;
        }

        .markdown-body code {
          background: #0d1f35;
          color: #00e5ff;
          padding: 2px 6px;
          border-radius: 4px;
          font-size: 0.9em;
          font-family: "JetBrains Mono", "Fira Code", monospace;
        }
        .markdown-body pre {
          background: #080e18;
          border: 1px solid #1a2a45;
          border-radius: 8px;
          padding: 14px 16px;
          overflow-x: auto;
          margin: 10px 0;
        }
        .markdown-body pre code {
          background: none;
          color: #b0c4dc;
          padding: 0;
        }

        .markdown-body blockquote {
          border-left: 3px solid #00e5ff;
          margin: 8px 0;
          padding: 6px 16px;
          color: #7a94b8;
          background: #00e5ff04;
          border-radius: 0 6px 6px 0;
        }

        /* ---------- tool chips ---------- */
        .tool-strip {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
          padding: 4px 0 4px 20px;
        }
        .tool-chip {
          font-size: 11px;
          font-family: "JetBrains Mono", monospace;
          padding: 4px 10px;
          border-radius: 5px;
        }
        .tool-chip.pending {
          background: #1a2740; color: #5a7290;
        }
        .tool-chip.ok {
          background: #00e67610; color: #00e676;
          border: 1px solid #00e67622;
        }
        .tool-chip.err {
          background: #ff525210; color: #ff5252;
          border: 1px solid #ff525222;
        }

        /* ---------- input ---------- */
        .chat-input-bar {
          display: flex;
          gap: 10px;
          padding: 14px 20px;
          background: #0b1120;
          border-top: 1px solid #1a2740;
          flex-shrink: 0;
        }
        .chat-input-bar textarea {
          flex: 1;
          padding: 12px 16px;
          background: #080e18;
          border: 1px solid #1e3050;
          border-radius: 10px;
          color: #c8d6e5;
          font-family: inherit;
          font-size: 14px;
          line-height: 1.5;
          resize: none;
          outline: none;
          transition: border-color 0.2s, box-shadow 0.2s;
        }
        .chat-input-bar textarea::placeholder {
          color: #3a5070;
        }
        .chat-input-bar textarea:focus {
          border-color: #00e5ff;
          box-shadow: 0 0 0 3px #00e5ff10;
        }

        .btn-send {
          padding: 0 28px;
          background: linear-gradient(135deg, #0077ff, #0055cc);
          color: #fff;
          border: none;
          border-radius: 10px;
          font-size: 14px;
          font-weight: 600;
          cursor: pointer;
          white-space: nowrap;
          transition: all 0.2s;
          font-family: inherit;
          align-self: flex-end;
          min-height: 46px;
        }
        .btn-send:hover:not(:disabled) {
          background: linear-gradient(135deg, #0088ff, #0066ee);
          box-shadow: 0 4px 20px #0077ff33;
        }
        .btn-send:disabled {
          background: #1a2740;
          color: #3a5070;
          cursor: not-allowed;
        }

        /* ---------- responsive ---------- */
        @media (max-width: 640px) {
          .chat-messages { padding: 12px 10px 6px; }
          .msg-bubble { max-width: 92%; padding: 12px 14px; }
          .chat-toolbar { padding: 8px 12px; flex-wrap: wrap; gap: 8px; }
          .chat-input-bar { padding: 10px 12px; }
          .btn-send { min-width: 64px; padding: 0 18px; }
        }
      `}</style>
    </div>
  );
};

export default ChatBox;
