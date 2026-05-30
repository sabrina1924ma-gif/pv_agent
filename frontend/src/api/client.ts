/**
 * PV Agent 后端的 HTTP API 客户端。
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api/v1";
const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 1000;

// --- 请求拦截器 ---

function buildHeaders(authRequired = true): Record<string, string> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-Session-Id": getSessionId(),
  };
  if (authRequired) {
    const token = getToken();
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
  }
  return headers;
}

// --- 响应拦截器 ---

async function handleResponse(resp: Response): Promise<Response> {
  if (!resp.ok) {
    const body = await resp.text().catch(() => "");
    throw new Error(`HTTP ${resp.status}: ${body || resp.statusText}`);
  }
  return resp;
}

// --- 重试逻辑 ---

async function fetchWithRetry(
  url: string,
  init: RequestInit,
  retries = MAX_RETRIES
): Promise<Response> {
  for (let attempt = 1; attempt <= retries; attempt++) {
    try {
      const resp = await fetch(url, init);
      // 仅对 5xx 和网络错误重试，4xx 直接抛
      if (resp.status >= 500 && attempt < retries) {
        await sleep(RETRY_DELAY_MS * attempt);
        continue;
      }
      return await handleResponse(resp);
    } catch (err) {
      if (attempt >= retries) throw err;
      await sleep(RETRY_DELAY_MS * attempt);
    }
  }
  throw new Error("请求失败：已达最大重试次数");
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// --- 公开 API ---

/** 获取或创建会话 ID，持久化存储在 localStorage 中 */
export function getSessionId(): string {
  let sessionId = localStorage.getItem("pv_session_id");
  if (!sessionId) {
    sessionId = crypto.randomUUID();
    localStorage.setItem("pv_session_id", sessionId);
  }
  return sessionId;
}

/** POST /api/v1/chat — 发送消息并获取完整响应 */
export async function sendChatMessage(
  message: string,
  stationId?: string
): Promise<Response> {
  return fetchWithRetry(`${BASE_URL}/chat`, {
    method: "POST",
    headers: buildHeaders(),
    body: JSON.stringify({ message, station_id: stationId }),
  });
}

/** GET /api/v1/sessions/{id} — 获取会话历史 */
export async function getSessionHistory(sessionId: string): Promise<Response> {
  return fetchWithRetry(`${BASE_URL}/sessions/${sessionId}`, {
    headers: buildHeaders(),
  });
}

/** DELETE /api/v1/sessions/{id} — 清除会话 */
export async function deleteSession(sessionId: string): Promise<Response> {
  return fetchWithRetry(`${BASE_URL}/sessions/${sessionId}`, {
    method: "DELETE",
    headers: buildHeaders(),
  });
}

/** POST /api/v1/sessions — 创建新会话 */
export async function createSession(): Promise<Response> {
  return fetchWithRetry(`${BASE_URL}/sessions`, {
    method: "POST",
    headers: buildHeaders(),
  });
}

/** GET /api/v1/sessions — 列出所有会话摘要 */
export async function listSessions(): Promise<Response> {
  return fetchWithRetry(`${BASE_URL}/sessions`, {
    headers: buildHeaders(),
  });
}

/** GET /api/v1/sessions/{id}/messages — 获取会话消息 */
export async function getSessionMessages(sessionId: string): Promise<Response> {
  return fetchWithRetry(`${BASE_URL}/sessions/${sessionId}/messages`, {
    headers: buildHeaders(),
  });
}

/**
 * 设置当前活跃的会话 ID（持久化到 localStorage）。
 * 也更新全局会话标识，方便 buildHeaders() 自动带上。
 */
export function setActiveSessionId(sessionId: string): void {
  localStorage.setItem("pv_session_id", sessionId);
}

// ============================================================================
// 认证 API
// ============================================================================

const TOKEN_KEY = "pv_token";
const USER_KEY = "pv_user";

/** 获取存储的 JWT token */
export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

/** 设置 JWT token 和用户信息 */
export function setAuth(token: string, user: Record<string, unknown>): void {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

/** 清除认证信息（登出） */
export function clearAuth(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  localStorage.removeItem("pv_session_id");
}

/** 获取存储的用户信息 */
export function getStoredUser(): Record<string, unknown> | null {
  try {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

/** 检查是否已登录（有 token） */
export function isAuthenticated(): boolean {
  return !!getToken();
}

/** POST /api/v1/auth/login — 用户名+密码登录 */
export async function login(
  username: string,
  password: string
): Promise<Response> {
  return fetchWithRetry(`${BASE_URL}/auth/login`, {
    method: "POST",
    headers: buildHeaders(false),
    body: JSON.stringify({ username, password }),
  });
}

/** POST /api/v1/auth/register — 注册新用户 */
export async function register(
  username: string,
  password: string
): Promise<Response> {
  return fetchWithRetry(`${BASE_URL}/auth/register`, {
    method: "POST",
    headers: buildHeaders(false),
    body: JSON.stringify({ username, password }),
  });
}

/** GET /api/v1/auth/me — 获取当前用户信息 */
export async function getCurrentUser(): Promise<Response> {
  return fetchWithRetry(`${BASE_URL}/auth/me`, {
    headers: buildHeaders(true),
  });
}

/** GET /api/v1/profile — 获取用户画像 */
export async function getUserProfile(): Promise<Response> {
  return fetchWithRetry(`${BASE_URL}/profile`, {
    headers: buildHeaders(true),
  });
}

/** POST /api/v1/profile/memory — 添加记忆笔记 */
export async function addMemoryNote(
  content: string,
  source = "user"
): Promise<Response> {
  return fetchWithRetry(`${BASE_URL}/profile/memory`, {
    method: "POST",
    headers: buildHeaders(true),
    body: JSON.stringify({ content, source }),
  });
}
