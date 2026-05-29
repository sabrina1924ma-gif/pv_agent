/**
 * HTTP API client for the PV Agent backend.
 *
 * TODO:
 *   - Implement session_id header injection (from localStorage).
 *   - Add request/response interceptors for error handling.
 *   - Add retry logic for transient failures.
 */

const BASE_URL = import.meta.env.VITE_API_BASE_URL || "/api/v1";

/** Get or create a session ID, persisted in localStorage */
export function getSessionId(): string {
  let sessionId = localStorage.getItem("pv_session_id");
  if (!sessionId) {
    sessionId = crypto.randomUUID();
    localStorage.setItem("pv_session_id", sessionId);
  }
  return sessionId;
}

/** POST /api/v1/chat — send a message and get a complete response */
export async function sendChatMessage(
  message: string,
  stationId?: string
): Promise<Response> {
  return fetch(`${BASE_URL}/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Session-Id": getSessionId(),
    },
    body: JSON.stringify({ message, station_id: stationId }),
  });
}

/** GET /api/v1/sessions/{id} — retrieve session history */
export async function getSessionHistory(sessionId: string): Promise<Response> {
  return fetch(`${BASE_URL}/sessions/${sessionId}`, {
    headers: { "X-Session-Id": sessionId },
  });
}

/** DELETE /api/v1/sessions/{id} — clear a session */
export async function deleteSession(sessionId: string): Promise<Response> {
  return fetch(`${BASE_URL}/sessions/${sessionId}`, {
    method: "DELETE",
    headers: { "X-Session-Id": sessionId },
  });
}
