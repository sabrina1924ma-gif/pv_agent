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

// ============================================================================
// 会话管理类型
// ============================================================================

/** 会话摘要（列表用） */
export interface SessionSummary {
  session_id: string;
  title: string;
  message_count: number;
  created_at: string | null;
  last_active: string | null;
}

/** POST /api/v1/sessions 的响应 */
export interface SessionCreateResponse {
  session_id: string;
  created_at: string;
}

/** 单条历史消息（从后端加载） */
export interface MessageItem {
  id: string;
  role: string;
  content: string;
  intent: string | null;
  created_at: string;
}

/** GET /api/v1/sessions/{id}/messages 的响应 */
export interface SessionMessagesResponse {
  session_id: string;
  messages: MessageItem[];
}

// ============================================================================
// 认证类型
// ============================================================================

/** POST /api/v1/auth/register 请求 */
export interface RegisterRequest {
  username: string;
  password: string;
}

/** POST /api/v1/auth/login 请求 */
export interface LoginRequest {
  username: string;
  password: string;
}

/** 用户公开信息 */
export interface UserInfo {
  id: string;
  username: string;
  phone: string | null;
  name: string | null;
  avatar: string | null;
  created_at: string | null;
}

/** 登录/注册成功响应 */
export interface TokenResponse {
  access_token: string;
  token_type: string;
  user: UserInfo;
}

// ============================================================================
// 用户画像类型
// ============================================================================

/** GET /api/v1/profile 响应 */
export interface UserProfileResponse {
  user_id: string;
  preferences: Record<string, unknown>;
  frequent_stations: Record<string, number>;
  conversation_summary: string | null;
  memory_notes: Array<{ content: string; source: string; created_at: string }>;
  updated_at: string | null;
}
