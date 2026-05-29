import { useRef, useState, useCallback, useEffect } from "react";
import type { StreamEvent } from "../types";

/**
 * Hook: WebSocket connection for streaming agent responses.
 *
 * Manages the lifecycle of a WebSocket connection to the backend's
 * /ws/{session_id} endpoint. Provides real-time streaming of LLM tokens,
 * tool call notifications, and the final report.
 *
 * TODO:
 *   - Implement automatic reconnection with exponential backoff.
 *   - Add heartbeat/ping to detect stale connections.
 *   - Buffer tokens for efficient batching in UI updates.
 */
export function useWebSocket(sessionId: string) {
  const wsRef = useRef<WebSocket | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [tokens, setTokens] = useState<string[]>([]);
  const [reportMd, setReportMd] = useState<string | null>(null);

  const connect = useCallback(() => {
    const wsBase = import.meta.env.VITE_WS_BASE_URL || `ws://localhost:8000`;
    const url = `${wsBase}/ws/${sessionId}`;
    const ws = new WebSocket(url);

    ws.onopen = () => {
      setIsConnected(true);
      setTokens([]);
      setReportMd(null);
    };

    ws.onmessage = (event: MessageEvent) => {
      const msg: StreamEvent = JSON.parse(event.data);
      switch (msg.event) {
        case "token":
          setTokens((prev) => [...prev, (msg.data.token as string)]);
          break;
        case "tool_call":
          // TODO: Display tool invocation in the UI
          break;
        case "tool_result":
          // TODO: Show tool results inline
          break;
        case "done":
          setReportMd((msg.data.report_md as string) || null);
          break;
        case "error":
          console.error("WebSocket error:", msg.data);
          break;
      }
    };

    ws.onclose = () => setIsConnected(false);
    ws.onerror = (err) => console.error("WebSocket error:", err);

    wsRef.current = ws;
  }, [sessionId]);

  const send = useCallback((content: string, stationId?: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ content, station_id: stationId }));
    }
  }, []);

  const disconnect = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      wsRef.current?.close();
    };
  }, []);

  return { isConnected, tokens, reportMd, connect, send, disconnect };
}
