/**
 * PV Agent 后端的 HTTP API 客户端。
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api/v1";
const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 1000;

// --- 请求拦截器 ---

function buildHeaders(): Record<string, string> {
  return {
    "Content-Type": "application/json",
    "X-Session-Id": getSessionId(),
  };
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
