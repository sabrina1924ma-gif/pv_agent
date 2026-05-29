/**
 * Shared TypeScript type definitions for the PV Agent frontend.
 */

/** A single chat message in the conversation */
export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: string;
  timestamp: string;
  intent?: string;
  metadata?: Record<string, unknown>;
}

/** Request body for POST /api/v1/chat */
export interface ChatRequest {
  message: string;
  station_id?: string;
}

/** Response from POST /api/v1/chat */
export interface ChatResponse {
  session_id: string;
  message: string;
  intent?: string;
  report_md?: string;
  tool_results?: Array<Record<string, unknown>>;
}

/** Single event in the WebSocket stream */
export interface StreamEvent {
  event: "token" | "tool_call" | "tool_result" | "done" | "error";
  data: Record<string, unknown>;
}

/** Token event payload */
export interface TokenEvent extends StreamEvent {
  event: "token";
  data: { token: string };
}

/** Tool call started */
export interface ToolCallEvent extends StreamEvent {
  event: "tool_call";
  data: {
    tool_name: string;
    input: Record<string, unknown>;
  };
}

/** Tool execution finished */
export interface ToolResultEvent extends StreamEvent {
  event: "tool_result";
  data: {
    tool_name: string;
    status: "ok" | "error";
    result: Record<string, unknown>;
    elapsed_ms: number;
  };
}

/** Agent finished, includes final report */
export interface DoneEvent extends StreamEvent {
  event: "done";
  data: {
    report_md?: string;
    intent?: string;
  };
}
