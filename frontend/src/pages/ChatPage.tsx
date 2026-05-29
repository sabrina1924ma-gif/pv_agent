import { useEffect, useState } from "react";
import ChatBox from "../components/ChatBox";
import { getSessionId } from "../api/client";

/**
 * Main chat page.
 *
 * Initializes a session on mount and renders the ChatBox component.
 * Acts as the primary landing page for the PV Agent application.
 *
 * TODO:
 *   - Add a sidebar with session history / station list.
 *   - Add a theme toggle (light/dark mode).
 *   - Implement responsive layout for mobile.
 */
function ChatPage() {
  const [sessionId, setSessionId] = useState("");

  useEffect(() => {
    setSessionId(getSessionId());
  }, []);

  return (
    <main style={{ maxWidth: 800, margin: "0 auto", padding: "1rem" }}>
      <h1>PV Agent - 电力智能助手</h1>
      <p>Session: {sessionId || "initializing..."}</p>
      {sessionId && <ChatBox sessionId={sessionId} />}
    </main>
  );
}

export default ChatPage;
