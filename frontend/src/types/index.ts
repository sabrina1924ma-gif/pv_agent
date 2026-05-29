/**
 * PV Agent 前端共享 TypeScript 类型定义。
 */

/** 会话中的单条聊天消息 */
export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  timestamp: string;
  intent?: string;
  metadata?: Record<string, unknown>;
}

/** POST /api/v1/chat 的请求体 */
export interface ChatRequest {
  message: string;
  station_id?: string;
}

/** POST /api/v1/chat 的响应 */
export interface ChatResponse {
  session_id: string;
  message: string;
  intent?: string;
  report_md?: string;
  tool_results?: Array<Record<string, unknown>>;
}

/** WebSocket 流中的单个事件 */
export interface StreamEvent {
  event: "token" | "tool_call" | "tool_result" | "done" | "error";
  data: Record<string, unknown>;
}

/** Token 事件载荷 */
export interface TokenEvent extends StreamEvent {
  event: "token";
  data: { token: string };
}

/** 工具调用开始 */
export interface ToolCallEvent extends StreamEvent {
  event: "tool_call";
  data: {
    tool_name: string;
    input: Record<string, unknown>;
  };
}

/** 工具执行完成 */
export interface ToolResultEvent extends StreamEvent {
  event: "tool_result";
  data: {
    tool_name: string;
    status: "ok" | "error";
    result: Record<string, unknown>;
    elapsed_ms: number;
  };
}

/** Agent 完成，包含最终报告 */
export interface DoneEvent extends StreamEvent {
  event: "done";
  data: {
    report_md?: string;
    intent?: string;
  };
}
