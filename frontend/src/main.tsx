import React, { type ReactNode } from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error?: Error;
}

class ErrorBoundary extends React.Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("ErrorBoundary caught:", error, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div
          style={{
            padding: "2rem",
            maxWidth: 600,
            margin: "4rem auto",
            textAlign: "center",
            fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
            background: "#060b14",
            minHeight: "100vh",
          }}
        >
          <h1 style={{ color: "#ff5252", fontSize: "1.4rem", marginBottom: "0.5rem" }}>
            应用出错了
          </h1>
          <p style={{ color: "#5a7290", fontSize: "0.9rem" }}>
            PV Agent 遇到一个意外错误，请刷新页面重试。
          </p>
          <pre
            style={{
              background: "#0d1525",
              border: "1px solid #1e3050",
              color: "#b0c4dc",
              padding: "1rem",
              borderRadius: 8,
              fontSize: "0.85rem",
              textAlign: "left",
              overflowX: "auto",
              marginTop: "1rem",
              fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
            }}
          >
            {this.state.error?.message}
          </pre>
          <button
            onClick={() => window.location.reload()}
            style={{
              marginTop: "1rem",
              padding: "0.6rem 1.8rem",
              background: "linear-gradient(135deg, #0077ff, #0055cc)",
              color: "#fff",
              border: "none",
              borderRadius: 8,
              cursor: "pointer",
              fontSize: "0.95rem",
              fontWeight: 600,
              fontFamily: "inherit",
            }}
          >
            刷新页面
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </BrowserRouter>
  </React.StrictMode>
);
