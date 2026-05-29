import { Routes, Route } from "react-router-dom";
import ChatPage from "./pages/ChatPage";

/**
 * Root application component.
 *
 * TODO: Add route definitions for additional pages:
 *   - /reports        — saved report viewer
 *   - /stations       — station management dashboard
 *   - /admin          — admin panel (tenants, users, config)
 */
function App() {
  return (
    <Routes>
      <Route path="/" element={<ChatPage />} />
      {/* Additional routes go here */}
    </Routes>
  );
}

export default App;
