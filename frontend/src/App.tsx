import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import ChatPage from "./pages/ChatPage";
import TasksPage from "./pages/TasksPage";
import ToolsPage from "./pages/ToolsPage";
import SkillsPage from "./pages/SkillsPage";
import CronPage from "./pages/CronPage";
import WorkspacePage from "./pages/WorkspacePage";
import SessionsPage from "./pages/SessionsPage";
import DashboardPage from "./pages/DashboardPage";
import Layout from "./components/Layout";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Navigate to="/chat" replace />} />
          <Route path="/chat" element={<ChatPage />} />
          <Route path="/tools" element={<ToolsPage />} />
          <Route path="/skills" element={<SkillsPage />} />
          <Route path="/cron" element={<CronPage />} />
          <Route path="/tasks" element={<TasksPage />} />
          <Route path="/sessions" element={<SessionsPage />} />
          <Route path="/workspace" element={<WorkspacePage />} />
          <Route path="/dashboard" element={<DashboardPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

