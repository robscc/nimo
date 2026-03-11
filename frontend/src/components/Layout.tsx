import { Link, Outlet, useLocation } from "react-router-dom";
import { MessageCircle, ListTodo } from "lucide-react";
import clsx from "clsx";

export default function Layout() {
  const { pathname } = useLocation();
  const nav = [
    { to: "/chat", icon: MessageCircle, label: "对话" },
    { to: "/tasks", icon: ListTodo, label: "任务" },
  ];

  return (
    <div className="flex h-screen bg-gray-50">
      {/* Sidebar */}
      <aside className="w-16 bg-white border-r flex flex-col items-center py-4 gap-4">
        <div className="w-10 h-10 rounded-xl bg-indigo-600 flex items-center justify-center">
          <span className="text-white font-bold text-lg">A</span>
        </div>
        {nav.map(({ to, icon: Icon, label }) => (
          <Link
            key={to}
            to={to}
            title={label}
            className={clsx(
              "w-10 h-10 rounded-lg flex items-center justify-center transition-colors",
              pathname.startsWith(to)
                ? "bg-indigo-100 text-indigo-700"
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
