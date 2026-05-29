import { useState, type FC } from "react";
import ReactMarkdown from "react-markdown";

interface ChatBoxProps {
  sessionId: string;
}

/**
 * Main chat interface component.
 *
 * Handles user input, displays message history, and renders
 * streaming LLM responses. Supports both HTTP and WebSocket modes.
 *
 * TODO:
 *   - Implement message history display (user/assistant bubbles).
 *   - Show tool invocation progress indicators.
 *   - Render Markdown reports with syntax highlighting.
 *   - Add station_id selector dropdown.
 *   - Add "clear session" button.
 */
const ChatBox: FC<ChatBoxProps> = ({ sessionId }) => {
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<
    Array<{ role: string; content: string }>
  >([]);
  const [loading, setLoading] = useState(false);

  const handleSend = async () => {
    if (!input.trim()) return;

    const userMsg = { role: "user", content: input };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);

    // TODO: Call sendChatMessage() from api/client.ts
    // TODO: Handle streaming response via WebSocket or SSE
    // TODO: Append assistant response to messages

    setLoading(false);
  };

  return (
    <div className="chat-container">
      <div className="messages">
        {messages.map((msg, i) => (
          <div key={i} className={`message ${msg.role}`}>
            {msg.role === "assistant" ? (
              <ReactMarkdown>{msg.content}</ReactMarkdown>
            ) : (
              <p>{msg.content}</p>
            )}
          </div>
        ))}
        {loading && <div className="loading">Agent is thinking...</div>}
      </div>

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
          发送
        </button>
      </div>
    </div>
  );
};

export default ChatBox;
