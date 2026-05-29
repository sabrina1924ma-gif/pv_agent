import { useEffect, useState } from "react";
import ChatBox from "../components/ChatBox";
import { getSessionId } from "../api/client";

/** 电站列表（与后端 mock 对齐） */
const STATION_LIST = [
  { id: "station-001", name: "1号电站", location: "甘肃敦煌", capacity: "5.0 MW" },
  { id: "station-002", name: "2号电站", location: "江苏盐城", capacity: "3.0 MW" },
  { id: "station-003", name: "3号电站", location: "青海海南州", capacity: "5.0 MW" },
  { id: "station-004", name: "4号电站", location: "浙江宁波", capacity: "2.0 MW" },
  { id: "station-005", name: "5号电站", location: "河北张家口", capacity: "4.0 MW" },
] as const;

/**
 * 主聊天页面。
 *
 * 初始化会话并渲染带侧边栏的聊天界面。
 * 侧边栏展示电站列表概览。
 */
function ChatPage() {
  const [sessionId, setSessionId] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(true);

  useEffect(() => {
    setSessionId(getSessionId());
  }, []);

  return (
    <div className="page-layout">
      {/* 侧边栏 */}
      <aside className={`sidebar ${sidebarOpen ? "open" : "closed"}`}>
        <div className="sidebar-header">
          <h2>电站列表</h2>
          <button
            className="sidebar-toggle"
            onClick={() => setSidebarOpen(false)}
          >
            ×
          </button>
        </div>
        <ul className="station-list">
          {STATION_LIST.map((s) => (
            <li key={s.id} className="station-item">
              <strong>{s.name}</strong>
              <span className="station-meta">
                {s.location} · {s.capacity}
              </span>
            </li>
          ))}
        </ul>
        <div className="sidebar-footer">
          <p className="session-label">会话: {sessionId.slice(0, 8)}...</p>
        </div>
      </aside>

      {/* 主内容 */}
      <main className="main-content">
        {!sidebarOpen && (
          <button
            className="sidebar-open-btn"
            onClick={() => setSidebarOpen(true)}
          >
            ☰ 电站
          </button>
        )}
        <h1>PV Agent — 电力智能助手</h1>
        <p className="subtitle">
          自然语言查询光伏电站数据，生成分析报告
        </p>
        {sessionId && <ChatBox sessionId={sessionId} />}
      </main>

      <style>{`
        .page-layout {
          display: flex;
          height: 100vh;
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
          background: #f5f5f5;
        }

        .sidebar {
          width: 260px;
          background: white;
          border-right: 1px solid #e0e0e0;
          display: flex;
          flex-direction: column;
          flex-shrink: 0;
          transition: width 0.2s;
        }

        .sidebar.closed {
          width: 0;
          overflow: hidden;
          border-right: none;
        }

        .sidebar-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 1rem;
          border-bottom: 1px solid #e0e0e0;
        }

        .sidebar-header h2 {
          margin: 0;
          font-size: 1rem;
          color: #333;
        }

        .sidebar-toggle {
          background: none;
          border: none;
          font-size: 1.2rem;
          cursor: pointer;
          color: #999;
        }

        .station-list {
          list-style: none;
          padding: 0;
          margin: 0;
          flex: 1;
          overflow-y: auto;
        }

        .station-item {
          padding: 0.75rem 1rem;
          border-bottom: 1px solid #f0f0f0;
        }

        .station-item strong {
          display: block;
          font-size: 0.9rem;
          color: #333;
        }

        .station-meta {
          font-size: 0.78rem;
          color: #999;
        }

        .sidebar-footer {
          padding: 0.75rem 1rem;
          border-top: 1px solid #e0e0e0;
        }

        .session-label {
          font-size: 0.75rem;
          color: #aaa;
          margin: 0;
          font-family: monospace;
        }

        .main-content {
          flex: 1;
          overflow-y: auto;
          padding: 1.5rem;
        }

        .sidebar-open-btn {
          position: fixed;
          top: 1rem;
          left: 1rem;
          z-index: 10;
          background: white;
          border: 1px solid #e0e0e0;
          padding: 0.5rem 0.75rem;
          border-radius: 6px;
          cursor: pointer;
          font-size: 0.9rem;
          box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }

        .main-content h1 {
          margin: 0 0 0.25rem 0;
          font-size: 1.5rem;
          color: #222;
        }

        .subtitle {
          margin: 0 0 1rem 0;
          color: #888;
          font-size: 0.9rem;
        }

        @media (max-width: 768px) {
          .sidebar {
            position: fixed;
            left: 0;
            top: 0;
            bottom: 0;
            z-index: 20;
            box-shadow: 2px 0 8px rgba(0,0,0,0.15);
          }

          .sidebar.closed {
            width: 0;
          }

          .main-content {
            padding: 1rem;
          }

          .main-content h1 {
            font-size: 1.25rem;
          }
        }
      `}</style>
    </div>
  );
}

export default ChatPage;
