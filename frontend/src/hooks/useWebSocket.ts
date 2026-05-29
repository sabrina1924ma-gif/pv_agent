import { useRef, useState, useCallback, useEffect } from "react";
import type { StreamEvent } from "../types";

/** 单条工具调用的状态 */
export interface ToolCallState {
  toolName: string;
  status?: "ok" | "error";
  elapsedMs?: number;
}

/** 重连配置 */
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;
const RECONNECT_JITTER_MS = 500;

/**
 * Hook: 用于流式传输 Agent 响应的 WebSocket 连接。
 *
 * 管理与后端 WebSocket 连接的生命周期，连接到
 * /ws/{session_id} 端点。提供 LLM token 的实时流式传输、
 * 工具调用通知以及最终报告。
 */
export function useWebSocket(sessionId: string) {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttemptRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const intentionalCloseRef = useRef(false);
  const wasEverConnectedRef = useRef(false);

  const [isConnected, setIsConnected] = useState(false);
  const [tokens, setTokens] = useState<string[]>([]);
  const [reportMd, setReportMd] = useState<string | null>(null);
  const [lastIntent, setLastIntent] = useState<string | null>(null);
  const [toolCalls, setToolCalls] = useState<ToolCallState[]>([]);
  const [lastError, setLastError] = useState<string | null>(null);

  // --- 自动重连（指数退避 + 抖动） ---
  const scheduleReconnect = useCallback(() => {
    if (reconnectTimerRef.current) return;

    const attempt = reconnectAttemptRef.current;
    const delay = Math.min(
      RECONNECT_BASE_MS * Math.pow(2, attempt),
      RECONNECT_MAX_MS
    ) + Math.random() * RECONNECT_JITTER_MS;

    reconnectTimerRef.current = setTimeout(() => {
      reconnectTimerRef.current = null;
      reconnectAttemptRef.current = attempt + 1;
      connect();
    }, delay);
  }, []);

  const connect = useCallback(() => {
    // 关闭已有连接（标记为主动关闭，防止 onclose 触发重连）
    if (wsRef.current) {
      intentionalCloseRef.current = true;
      wsRef.current.close();
    }
    // 取消所有待处理的重连
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }

    const wsBase = import.meta.env.VITE_WS_BASE_URL || `ws://localhost:8000`;
    const url = `${wsBase}/ws/${sessionId}`;
    const ws = new WebSocket(url);

    ws.onopen = () => {
      intentionalCloseRef.current = false;
      wasEverConnectedRef.current = true;
      setIsConnected(true);
      reconnectAttemptRef.current = 0;
      clearState();
    };

    ws.onmessage = (event: MessageEvent) => {
      // 忽略后台心跳的 ping 消息
      if (event.data === '{"event":"ping"}') return;

      const msg: StreamEvent = JSON.parse(event.data);
      switch (msg.event) {
        case "token":
          setTokens((prev) => [...prev, (msg.data.token as string)]);
          break;
        case "tool_call":
          setToolCalls((prev) => [
            ...prev,
            { toolName: msg.data.tool_name as string },
          ]);
          break;
        case "tool_result":
          setToolCalls((prev) => {
            const next = [...prev];
            for (let i = next.length - 1; i >= 0; i--) {
              if (!next[i].status) {
                next[i] = {
                  ...next[i],
                  status: msg.data.status as "ok" | "error",
                  elapsedMs: msg.data.elapsed_ms as number,
                };
                break;
              }
            }
            return next;
          });
          break;
        case "done":
          setReportMd((msg.data.report_md as string) || null);
          setLastIntent((msg.data.intent as string) || null);
          break;
        case "error":
          setLastError((msg.data.message as string) || "未知错误");
          break;
      }
    };

    ws.onclose = () => {
      setIsConnected(false);
      // 仅在非主动断开时尝试自动重连
      if (!intentionalCloseRef.current) {
        scheduleReconnect();
      }
    };

    ws.onerror = () => {
      // 仅在曾经连接成功后报错——页面首次加载时
      // 连接失败由状态指示器（○ 离线）体现，无需额外弹错误提示
      if (wasEverConnectedRef.current) {
        setLastError("WebSocket 连接失败");
      }
    };

    wsRef.current = ws;
  }, [sessionId, scheduleReconnect]);

  const send = useCallback((content: string, stationId?: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ content, station_id: stationId }));
    }
  }, []);

  const disconnect = useCallback(() => {
    // 标记为主动断开：取消重连，关闭连接
    intentionalCloseRef.current = true;
    wasEverConnectedRef.current = false;
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    reconnectAttemptRef.current = 0;
    wsRef.current?.close();
    wsRef.current = null;
    setIsConnected(false);
  }, []);

  const clearState = useCallback(() => {
    setTokens([]);
    setReportMd(null);
    setLastIntent(null);
    setToolCalls([]);
    setLastError(null);
  }, []);

  // 卸载时清理
  useEffect(() => {
    return () => {
      intentionalCloseRef.current = true;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close();
    };
  }, []);

  return {
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
  };
}
