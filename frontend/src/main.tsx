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
            fontFamily: "sans-serif",
          }}
        >
          <h1 style={{ color: "#dc3545" }}>应用出错了</h1>
          <p style={{ color: "#666" }}>PV Agent 遇到一个意外错误，请刷新页面重试。</p>
          <pre
            style={{
              background: "#f8f9fa",
              padding: "1rem",
              borderRadius: 8,
              fontSize: "0.85rem",
              textAlign: "left",
              overflowX: "auto",
              marginTop: "1rem",
            }}
          >
            {this.state.error?.message}
          </pre>
          <button
            onClick={() => window.location.reload()}
            style={{
              marginTop: "1rem",
              padding: "0.5rem 1.5rem",
              background: "#007bff",
              color: "white",
              border: "none",
              borderRadius: 8,
              cursor: "pointer",
              fontSize: "1rem",
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
