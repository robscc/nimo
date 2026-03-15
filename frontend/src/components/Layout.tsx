import { Link, Outlet, useLocation } from "react-router-dom";
import { MessageCircle, ListTodo, Wrench, Puzzle, FolderClosed, MessagesSquare, BarChart3, CalendarClock } from "lucide-react";
import clsx from "clsx";
import NimoIcon from "./NimoIcon";

export default function Layout() {
  const { pathname } = useLocation();
  const nav = [
    { to: "/chat",      icon: MessageCircle,  label: "对话" },
    { to: "/sessions",  icon: MessagesSquare,  label: "会话" },
    { to: "/tools",     icon: Wrench,          label: "工具" },
    { to: "/skills",    icon: Puzzle,          label: "技能" },
    { to: "/cron",      icon: CalendarClock,   label: "定时任务" },
    { to: "/tasks",     icon: ListTodo,        label: "任务" },
    { to: "/workspace", icon: FolderClosed,    label: "工作空间" },
    { to: "/dashboard", icon: BarChart3,        label: "监控" },
  ];

  return (
    <div className="flex h-screen bg-gray-50">
      {/* Sidebar */}
      <aside className="w-16 bg-white border-r flex flex-col items-center py-4 gap-4">
        <div className="w-10 h-10 rounded-xl bg-nimo-500 flex items-center justify-center">
          <NimoIcon size={28} />
        </div>
        {nav.map(({ to, icon: Icon, label }) => (
          <Link
            key={to}
            to={to}
            title={label}
            className={clsx(
              "w-10 h-10 rounded-lg flex items-center justify-center transition-colors",
              pathname.startsWith(to)
                ? "bg-nimo-100 text-nimo-600"
                : "text-gray-400 hover:bg-gray-100"
            )}
          >
            <Icon size={20} />
          </Link>
        ))}
      </aside>

      {/* Main */}
      <main className="flex-1 overflow-hidden">
        <Outlet />
      </main>
    </div>
  );
}
