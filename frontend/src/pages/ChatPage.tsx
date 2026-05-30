import { useEffect, useState, useCallback } from "react";
import ChatBox from "../components/ChatBox";
import {
  getSessionId,
  setActiveSessionId,
  createSession,
  listSessions,
} from "../api/client";
import type { SessionSummary, SessionCreateResponse } from "../types";

/** 电站列表（与后端 mock 对齐，含健康度基线） */
const STATION_LIST = [
  { id: "station-001", name: "1号电站", location: "甘肃敦煌", capacity: "5.0 MW", health: 0.90 },
  { id: "station-002", name: "2号电站", location: "江苏盐城", capacity: "3.0 MW", health: 0.94 },
  { id: "station-003", name: "3号电站", location: "青海海南州", capacity: "5.0 MW", health: 0.82 },
  { id: "station-004", name: "4号电站", location: "浙江宁波", capacity: "2.0 MW", health: 0.96 },
  { id: "station-005", name: "5号电站", location: "河北张家口", capacity: "4.0 MW", health: 0.78 },
] as const;

/** 健康度 → 状态标签 */
function healthLabel(h: number): { text: string; cls: string } {
  if (h >= 0.90) return { text: "优秀", cls: "good" };
  if (h >= 0.80) return { text: "关注", cls: "warn" };
  return { text: "预警", cls: "risk" };
}

/** 格式化时间戳为友好显示 */
function formatTime(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return "刚刚";
  if (diffMin < 60) return `${diffMin}分钟前`;
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return `${diffHour}小时前`;
  const diffDay = Math.floor(diffHour / 24);
  if (diffDay < 7) return `${diffDay}天前`;
  return d.toLocaleDateString("zh-CN");
}

/**
 * 主聊天页面。
 *
 * 左侧：暗色赛博朋克风侧边栏。
 *   - 顶部：新建会话按钮
 *   - 中部：历史会话列表
 *   - 底部：电站状态面板（可折叠）
 * 右侧：ChatBox 对话区（填满剩余空间）。
 */
function ChatPage() {
  const [sessionId, setSessionId] = useState("");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [stationsOpen, setStationsOpen] = useState(false);
  const [loadingSessions, setLoadingSessions] = useState(false);
  const [sessionVersion, setSessionVersion] = useState(0); // 用于强制 ChatBox 刷新

  // --- 加载会话列表 ---
  const loadSessions = useCallback(async () => {
    setLoadingSessions(true);
    try {
      const resp = await listSessions();
      if (resp.ok) {
        const data: SessionSummary[] = await resp.json();
        setSessions(data);
      }
    } catch {
      // 后端不可用时静默失败，不影响聊天功能
    } finally {
      setLoadingSessions(false);
    }
  }, []);

  // 初始化
  useEffect(() => {
    const currentId = getSessionId();
    setSessionId(currentId);
    loadSessions();
  }, [loadSessions]);

  // --- 新建会话 ---
  const handleNewSession = useCallback(async () => {
    try {
      const resp = await createSession();
      if (resp.ok) {
        const data: SessionCreateResponse = await resp.json();
        setActiveSessionId(data.session_id);
        setSessionId(data.session_id);
        setSessionVersion((v) => v + 1);
        // 重新加载会话列表
        loadSessions();
      }
    } catch {
      // 降级：前端直接生成
      const newId = crypto.randomUUID();
      setActiveSessionId(newId);
      setSessionId(newId);
      setSessionVersion((v) => v + 1);
    }
  }, [loadSessions]);

  // --- 切换会话 ---
  const handleSwitchSession = useCallback((id: string) => {
    setActiveSessionId(id);
    setSessionId(id);
    setSessionVersion((v) => v + 1);
  }, []);

  // --- 会话被清除后：创建新会话 + 刷新列表 ---
  const handleSessionCleared = useCallback(async () => {
    try {
      const resp = await createSession();
      if (resp.ok) {
        const data: SessionCreateResponse = await resp.json();
        setActiveSessionId(data.session_id);
        setSessionId(data.session_id);
        setSessionVersion((v) => v + 1);
      }
    } catch {
      const newId = crypto.randomUUID();
      setActiveSessionId(newId);
      setSessionId(newId);
      setSessionVersion((v) => v + 1);
    }
    loadSessions();
  }, [loadSessions]);

  return (
    <div className="page-layout">
      {/* ================================================================== */}
      {/*  侧边栏                                                            */}
      {/* ================================================================== */}
      <aside className={`sidebar ${sidebarOpen ? "open" : "closed"}`}>
        <div
          className="sidebar-overlay"
          onClick={() => setSidebarOpen(false)}
        />

        <div className="sidebar-inner">
          {/* 头部 */}
          <div className="sidebar-header">
            <div className="sidebar-brand">
              <span className="brand-icon">☀</span>
              <h2>PV Agent</h2>
            </div>
            <button
              className="sidebar-close"
              onClick={() => setSidebarOpen(false)}
              aria-label="关闭侧边栏"
            >
              ×
            </button>
          </div>

          <div className="sidebar-accent" />

          {/* ---- 新建会话按钮 ---- */}
          <div className="sidebar-section-header">
            <button
              className="btn-new-session"
              onClick={handleNewSession}
            >
              <span className="btn-new-session-icon">+</span>
              新建会话
            </button>
          </div>

          {/* ---- 历史会话列表 ---- */}
          <div className="session-list-wrapper">
            <div className="sidebar-section-label">历史会话</div>
            {loadingSessions && sessions.length === 0 && (
              <div className="session-list-empty">加载中…</div>
            )}
            {!loadingSessions && sessions.length === 0 && (
              <div className="session-list-empty">暂无历史会话</div>
            )}
            <ul className="session-list">
              {sessions.map((s) => (
                <li
                  key={s.session_id}
                  className={`session-item ${s.session_id === sessionId ? "active" : ""}`}
                  onClick={() => handleSwitchSession(s.session_id)}
                >
                  <div className="session-item-title">
                    {s.title || "新会话"}
                  </div>
                  <div className="session-item-meta">
                    <span>{s.message_count} 条消息</span>
                    <span>{formatTime(s.last_active)}</span>
                  </div>
                </li>
              ))}
            </ul>
          </div>

          <div className="sidebar-accent" />

          {/* ---- 电站面板（可折叠） ---- */}
          <div className="stations-section">
            <button
              className="stations-toggle"
              onClick={() => setStationsOpen(!stationsOpen)}
            >
              <span className={`stations-toggle-arrow ${stationsOpen ? "open" : ""}`}>
                ▸
              </span>
              <span>电站列表</span>
              <span className="stations-toggle-count">{STATION_LIST.length}</span>
            </button>

            {stationsOpen && (
              <ul className="station-list">
                {STATION_LIST.map((s) => {
                  const hl = healthLabel(s.health);
                  return (
                    <li key={s.id} className="station-card">
                      <div className="station-card-row">
                        <div className="station-card-left">
                          <strong className="station-name">{s.name}</strong>
                          <span className="station-loc">{s.location}</span>
                        </div>
                        <div className="station-card-right">
                          <span className={`health-badge ${hl.cls}`}>
                            {hl.text}
                          </span>
                        </div>
                      </div>
                      <div className="station-card-meta">
                        <span className="station-cap">{s.capacity}</span>
                        <span className="station-health-bar">
                          <span
                            className="health-fill"
                            style={{ width: `${s.health * 100}%` }}
                          />
                        </span>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          <div className="sidebar-accent" />

          {/* 底部会话信息 */}
          <div className="sidebar-footer">
            <span className="session-dot" />
            <p className="session-label">
              当前会话 <code>{sessionId.slice(0, 8)}…</code>
            </p>
          </div>
        </div>
      </aside>

      {/* ================================================================== */}
      {/*  主内容区                                                          */}
      {/* ================================================================== */}
      <main className="main-content">
        {!sidebarOpen && (
          <button
            className="sidebar-open-btn"
            onClick={() => setSidebarOpen(true)}
          >
            ☰ 会话
          </button>
        )}

        <header className="page-header">
          <h1 className="page-title">
            PV Agent
            <span className="page-title-accent">光伏电站智能运维</span>
          </h1>
          <p className="subtitle">
            自然语言查询电站运行状态、故障记录、发电数据，生成分析报告
          </p>
        </header>

        <div className="chat-wrapper">
          {sessionId && (
            <ChatBox
              key={`${sessionId}-${sessionVersion}`}
              sessionId={sessionId}
              onSessionCleared={handleSessionCleared}
            />
          )}
        </div>
      </main>

      {/* ================================================================== */}
      {/*  样式                                                              */}
      {/* ================================================================== */}
      <style>{`
        /* ===== 页面级布局 ===== */
        .page-layout {
          display: flex;
          height: 100vh;
          background: #060b14;
          overflow: hidden;
        }

        /* ===== 侧边栏容器 ===== */
        .sidebar {
          position: relative;
          width: 280px;
          flex-shrink: 0;
          transition: width 0.28s cubic-bezier(0.4, 0, 0.2, 1),
                      opacity 0.28s cubic-bezier(0.4, 0, 0.2, 1);
          z-index: 20;
        }
        .sidebar.closed {
          width: 0;
          overflow: hidden;
        }

        .sidebar-overlay {
          display: none;
        }

        .sidebar-inner {
          display: flex;
          flex-direction: column;
          height: 100%;
          background: #0b1120;
          border-right: 1px solid #1a2740;
        }

        /* ---- 头部 ---- */
        .sidebar-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 18px 20px 12px;
          flex-shrink: 0;
        }
        .sidebar-brand {
          display: flex;
          align-items: center;
          gap: 10px;
        }
        .brand-icon {
          font-size: 18px;
          filter: drop-shadow(0 0 6px #ffb30044);
        }
        .sidebar-brand h2 {
          margin: 0;
          font-size: 15px;
          font-weight: 700;
          color: #e8f0fe;
          letter-spacing: 0.3px;
        }
        .sidebar-close {
          background: none;
          border: none;
          color: #4a6080;
          font-size: 20px;
          cursor: pointer;
          padding: 0 4px;
          line-height: 1;
          transition: color 0.2s;
        }
        .sidebar-close:hover {
          color: #ff6b6b;
        }

        .sidebar-accent {
          height: 1px;
          margin: 0 20px;
          background: linear-gradient(90deg, #00e5ff44, #00e5ff08, transparent);
          flex-shrink: 0;
        }

        /* ---- 新建会话按钮 ---- */
        .sidebar-section-header {
          padding: 12px 20px 8px;
          flex-shrink: 0;
        }
        .btn-new-session {
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 6px;
          width: 100%;
          padding: 10px 16px;
          background: linear-gradient(135deg, #0077ff, #0055cc);
          color: #fff;
          border: none;
          border-radius: 8px;
          font-size: 13px;
          font-weight: 600;
          cursor: pointer;
          font-family: inherit;
          transition: all 0.2s;
        }
        .btn-new-session:hover {
          background: linear-gradient(135deg, #0088ff, #0066ee);
          box-shadow: 0 4px 20px #0077ff33;
        }
        .btn-new-session-icon {
          font-size: 16px;
          font-weight: 400;
          line-height: 1;
        }

        /* ---- 历史会话列表 ---- */
        .session-list-wrapper {
          flex: 1;
          overflow-y: auto;
          padding: 4px 0 8px;
          min-height: 0;
        }
        .sidebar-section-label {
          padding: 4px 20px 6px;
          font-size: 10px;
          font-weight: 600;
          color: #4a6080;
          text-transform: uppercase;
          letter-spacing: 1px;
        }
        .session-list-empty {
          padding: 12px 20px;
          font-size: 12px;
          color: #3a5070;
          font-style: italic;
        }
        .session-list {
          list-style: none;
          margin: 0;
          padding: 0 10px;
        }
        .session-item {
          padding: 10px 14px;
          margin: 2px 0;
          border-radius: 8px;
          cursor: pointer;
          transition: background 0.15s, border-color 0.15s;
          border: 1px solid transparent;
        }
        .session-item:hover {
          background: #0d1525;
          border-color: #1a2a45;
        }
        .session-item.active {
          background: #0d1f35;
          border-color: #00e5ff33;
        }
        .session-item-title {
          font-size: 13px;
          color: #c8d6e5;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
          margin-bottom: 3px;
        }
        .session-item.active .session-item-title {
          color: #e8f0fe;
        }
        .session-item-meta {
          display: flex;
          justify-content: space-between;
          font-size: 11px;
          color: #4a6080;
          font-variant-numeric: tabular-nums;
        }
        .session-item.active .session-item-meta {
          color: #5a7290;
        }

        /* ---- 电站面板 ---- */
        .stations-section {
          flex-shrink: 0;
          display: flex;
          flex-direction: column;
        }
        .stations-toggle {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 10px 20px;
          background: none;
          border: none;
          color: #6b7f9e;
          font-family: inherit;
          font-size: 12px;
          font-weight: 600;
          cursor: pointer;
          transition: color 0.2s;
          text-transform: uppercase;
          letter-spacing: 0.5px;
        }
        .stations-toggle:hover {
          color: #a0b8d8;
        }
        .stations-toggle-arrow {
          font-size: 10px;
          transition: transform 0.2s;
          display: inline-block;
        }
        .stations-toggle-arrow.open {
          transform: rotate(90deg);
        }
        .stations-toggle-count {
          font-size: 10px;
          color: #3a5070;
          background: #111b2e;
          padding: 2px 7px;
          border-radius: 4px;
          margin-left: auto;
        }

        .station-list {
          list-style: none;
          margin: 0;
          padding: 4px 14px 8px;
          display: flex;
          flex-direction: column;
          gap: 6px;
          max-height: 240px;
          overflow-y: auto;
        }
        .station-card {
          background: #0d1525;
          border: 1px solid #162040;
          border-radius: 10px;
          padding: 10px 12px;
          transition: border-color 0.2s, background 0.2s;
          cursor: default;
        }
        .station-card:hover {
          border-color: #1e3a60;
          background: #101a2e;
        }
        .station-card-row {
          display: flex;
          align-items: center;
          justify-content: space-between;
          margin-bottom: 8px;
        }
        .station-card-left {
          display: flex;
          flex-direction: column;
          gap: 2px;
        }
        .station-name {
          font-size: 12px;
          font-weight: 600;
          color: #d0ddf0;
        }
        .station-loc {
          font-size: 10px;
          color: #4a6080;
        }
        .station-card-right {
          flex-shrink: 0;
        }
        .station-card-meta {
          display: flex;
          align-items: center;
          gap: 10px;
        }
        .station-cap {
          font-size: 10px;
          font-family: "JetBrains Mono", "Fira Code", monospace;
          color: #5a7290;
          font-variant-numeric: tabular-nums;
        }

        /* ---- 健康度徽章 ---- */
        .health-badge {
          font-size: 10px;
          font-weight: 600;
          padding: 2px 8px;
          border-radius: 5px;
          text-transform: uppercase;
          letter-spacing: 0.5px;
        }
        .health-badge.good {
          background: #00e67615;
          color: #00e676;
          border: 1px solid #00e67622;
        }
        .health-badge.warn {
          background: #ffab0015;
          color: #ffab00;
          border: 1px solid #ffab0022;
        }
        .health-badge.risk {
          background: #ff525215;
          color: #ff5252;
          border: 1px solid #ff525222;
        }

        /* ---- 健康度进度条 ---- */
        .station-health-bar {
          flex: 1;
          height: 3px;
          background: #162040;
          border-radius: 2px;
          overflow: hidden;
        }
        .health-fill {
          display: block;
          height: 100%;
          border-radius: 2px;
          background: linear-gradient(90deg, #00e5ff, #00e676);
        }
        .station-card:hover .health-fill {
          background: linear-gradient(90deg, #00e5ff, #00e676);
          box-shadow: 0 0 6px #00e5ff44;
        }

        /* ---- 底部 ---- */
        .sidebar-footer {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 10px 20px 12px;
          flex-shrink: 0;
        }
        .session-dot {
          width: 7px;
          height: 7px;
          border-radius: 50%;
          background: #00e5ff;
          box-shadow: 0 0 6px #00e5ff44;
          flex-shrink: 0;
        }
        .session-label {
          margin: 0;
          font-size: 11px;
          color: #4a6080;
        }
        .session-label code {
          font-family: "JetBrains Mono", "Fira Code", monospace;
          font-size: 10px;
          color: #5a7290;
        }

        /* ===== 主内容区 ===== */
        .main-content {
          flex: 1;
          display: flex;
          flex-direction: column;
          min-width: 0;
          overflow: hidden;
        }

        .sidebar-open-btn {
          position: fixed;
          top: 14px;
          left: 14px;
          z-index: 15;
          background: #0b1120;
          color: #a0b4d0;
          border: 1px solid #1e3050;
          padding: 8px 14px;
          border-radius: 8px;
          cursor: pointer;
          font-size: 13px;
          font-family: inherit;
          transition: all 0.2s;
        }
        .sidebar-open-btn:hover {
          border-color: #00e5ff;
          color: #00e5ff;
          box-shadow: 0 0 16px #00e5ff14;
        }

        /* ---- 页头 ---- */
        .page-header {
          flex-shrink: 0;
          padding: 20px 28px 0;
        }
        .page-title {
          margin: 0 0 4px;
          font-size: 22px;
          font-weight: 700;
          color: #e8f0fe;
          letter-spacing: 1px;
          display: flex;
          align-items: baseline;
          gap: 14px;
        }
        .page-title-accent {
          font-size: 12px;
          font-weight: 500;
          color: #00e5ff;
          letter-spacing: 2px;
          text-transform: uppercase;
        }
        .subtitle {
          margin: 0 0 16px;
          color: #4a6080;
          font-size: 13px;
          line-height: 1.5;
        }

        /* ---- 聊天容器 ---- */
        .chat-wrapper {
          flex: 1;
          min-height: 0;
          padding: 0 20px 16px;
        }

        /* ===== 响应式 ===== */
        @media (max-width: 768px) {
          .sidebar {
            position: fixed;
            left: 0;
            top: 0;
            bottom: 0;
            width: 280px;
          }
          .sidebar.closed {
            width: 0;
          }
          .sidebar-overlay {
            display: block;
            position: fixed;
            inset: 0;
            background: #00000066;
            z-index: -1;
          }
          .sidebar.closed .sidebar-overlay {
            display: none;
          }
          .page-header {
            padding: 16px 16px 0;
          }
          .page-title {
            font-size: 18px;
            gap: 8px;
          }
          .page-title-accent {
            font-size: 10px;
          }
          .subtitle {
            font-size: 12px;
          }
          .chat-wrapper {
            padding: 0 8px 8px;
          }
        }
      `}</style>
    </div>
  );
}

export default ChatPage;
