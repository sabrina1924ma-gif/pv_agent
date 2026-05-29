import { Routes, Route } from "react-router-dom";
import ChatPage from "./pages/ChatPage";

/**
 * 根应用组件。
 */
function App() {
  return (
    <Routes>
      <Route path="/" element={<ChatPage />} />
    </Routes>
  );
}

export default App;
