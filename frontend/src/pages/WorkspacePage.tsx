import { useState, useEffect, useCallback } from "react";
import {
  Bot, User, Brain, FileText, BookOpen, FolderOpen,
  Calendar, Save, RefreshCw, CheckCircle2, Loader2,
  FolderClosed, ChevronDown, ChevronRight, Info, PlusCircle,
} from "lucide-react";
import clsx from "clsx";
import {
  getWorkspaceInfo, getWorkspaceFile, updateWorkspaceFile,
  getDailyLog, listDailyLogs, listCanvas, getCanvasFile,
  appendWorkspaceMemory,
  type WorkspaceInfo, type CanvasFileMeta,
} from "../api";

// ── 文件元数据 ─────────────────────────────────────────────

interface WorkspaceEntry {
  id: string;           // 用于 API 调用的 key（"SOUL.md" etc）
  label: string;        // 显示名称
  icon: React.ComponentType<{ size?: number | string; className?: string }>;
  readOnly?: boolean;
  group: "core" | "memory" | "canvas";
  description: string;
}

const CORE_FILES: WorkspaceEntry[] = [
  { id: "AGENTS.md",   label: "Agents",   icon: Bot,      group: "core",   description: "Agent 定义与路由规则" },
  { id: "IDENTITY.md", label: "Identity", icon: FileText,  group: "core",   description: "Agent 身份与能力" },
  { id: "SOUL.md",     label: "Soul",     icon: Brain,     group: "core",   description: "性格、语气、价值观" },
  { id: "USER.md",     label: "User",     icon: User,      group: "core",   description: "用户画像与偏好" },
  { id: "MEMORY.md",   label: "Memory",   icon: BookOpen,  group: "core",   description: "持久化长期记忆" },
  { id: "CONTEXT.md",  label: "Context",  icon: Info,      group: "core",   description: "当前阶段补充背景" },
];

// ── 侧边栏文件项 ───────────────────────────────────────────

function SidebarItem({
  entry, active, onClick,
}: {
  entry: WorkspaceEntry;
  active: boolean;
  onClick: () => void;
}) {
  const Icon = entry.icon;
  return (
    <button
      onClick={onClick}
      className={clsx(
        "w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-left transition-colors text-sm",
        active
          ? "bg-nimo-100 text-nimo-700 font-medium"
          : "text-gray-500 hover:bg-gray-100 hover:text-gray-700",
      )}
    >
      <Icon size={15} className={active ? "text-nimo-500" : "text-gray-400"} />
      <div className="flex-1 min-w-0">
        <div className="truncate font-mono text-xs leading-tight">{entry.id}</div>
        <div className="truncate text-xs text-gray-400 leading-tight">{entry.label}</div>
      </div>
    </button>
  );
}

// ── 可折叠分组 ─────────────────────────────────────────────

function SidebarGroup({
  label, icon: Icon, open, onToggle, children,
}: {
  label: string;
  icon: React.ComponentType<{ size?: number | string; className?: string }>;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <div>
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-2 px-2 py-1.5 text-xs font-semibold text-gray-400 uppercase tracking-wider hover:text-gray-600 transition-colors"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <Icon size={12} />
        {label}
      </button>
      {open && <div className="space-y-0.5 pl-1">{children}</div>}
    </div>
  );
}

// ── 主页面 ─────────────────────────────────────────────────

type ActiveFile =
  | { type: "core"; id: string }
  | { type: "daily"; date: string }
  | { type: "canvas"; name: string };

export default function WorkspacePage() {
  const [wsInfo, setWsInfo] = useState<WorkspaceInfo | null>(null);
  const [active, setActive] = useState<ActiveFile>({ type: "core", id: "SOUL.md" });
  const [content, setContent] = useState("");
  const [originalContent, setOriginalContent] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  // daily logs & canvas
  const [dailyOpen, setDailyOpen] = useState(true);
  const [canvasOpen, setCanvasOpen] = useState(true);
  const [dailyDates, setDailyDates] = useState<string[]>([]);
  const [canvasFiles, setCanvasFiles] = useState<CanvasFileMeta[]>([]);

  // memory append
  const [memoryInput, setMemoryInput] = useState("");
  const [appendingMemory, setAppendingMemory] = useState(false);

  // 加载 workspace info + 侧边数据
  useEffect(() => {
    getWorkspaceInfo().then(setWsInfo).catch(() => {});
    listDailyLogs().then(setDailyDates).catch(() => {});
    listCanvas().then(setCanvasFiles).catch(() => {});
  }, []);

  // 加载选中文件
  const loadFile = useCallback(async (file: ActiveFile) => {
    setLoading(true);
    setSaved(false);
    try {
      let result = "";
      if (file.type === "core") {
        const f = await getWorkspaceFile(file.id);
        result = f.content;
      } else if (file.type === "daily") {
        const f = await getDailyLog(file.date);
        result = f.content;
      } else if (file.type === "canvas") {
        const f = await getCanvasFile(file.name);
        result = f.content;
      }
      setContent(result);
      setOriginalContent(result);
    } catch {
      setContent("（读取失败）");
      setOriginalContent("");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadFile(active);
  }, [active, loadFile]);

  // 保存
  const handleSave = async () => {
    if (active.type !== "core") return;
    setSaving(true);
    try {
      await updateWorkspaceFile(active.id, content);
      setOriginalContent(content);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch {
      alert("保存失败，请检查后端日志");
    } finally {
      setSaving(false);
    }
  };

  // 追加记忆
  const handleAppendMemory = async () => {
    if (!memoryInput.trim()) return;
    setAppendingMemory(true);
    try {
      await appendWorkspaceMemory(memoryInput.trim());
      setMemoryInput("");
      // 如果当前正在查看 MEMORY.md，刷新
      if (active.type === "core" && active.id === "MEMORY.md") {
        await loadFile(active);
      }
    } catch {
      alert("追加失败");
    } finally {
      setAppendingMemory(false);
    }
  };

  const isDirty = active.type === "core" && content !== originalContent;
  const isReadOnly = active.type === "daily";
  const activeEntry = CORE_FILES.find(
    (f) => active.type === "core" && f.id === active.id,
  );

  const activeLabel =
    active.type === "core"
      ? active.id
      : active.type === "daily"
      ? `memory/${active.date}.md`
      : `canvas/${active.name}`;

  return (
    <div className="flex h-full overflow-hidden bg-gray-50">
      {/* ── 左侧：文件树 ── */}
      <aside className="w-56 bg-white border-r flex flex-col shrink-0 overflow-y-auto">
        {/* Header */}
        <div className="px-4 py-3 border-b">
          <h1 className="font-semibold text-gray-800 text-sm flex items-center gap-2">
            <FolderClosed size={16} className="text-nimo-500" />
            Workspace
          </h1>
          {wsInfo && (
            <p className="text-xs text-gray-400 mt-0.5 truncate font-mono" title={wsInfo.workspace_dir}>
              {wsInfo.workspace_dir.replace(/^.*\/([^/]+\/[^/]+)$/, "~/$1")}
            </p>
          )}
        </div>

        <div className="flex-1 p-2 space-y-3">
          {/* Core files */}
          <SidebarGroup
            label="Files"
            icon={FileText}
            open={true}
            onToggle={() => {}}
          >
            {CORE_FILES.map((entry) => (
              <SidebarItem
                key={entry.id}
                entry={entry}
                active={active.type === "core" && active.id === entry.id}
                onClick={() => setActive({ type: "core", id: entry.id })}
              />
            ))}
          </SidebarGroup>

          {/* Daily logs */}
          <SidebarGroup
            label="Memory Logs"
            icon={Calendar}
            open={dailyOpen}
            onToggle={() => setDailyOpen((v) => !v)}
          >
            {dailyDates.length === 0 ? (
              <p className="text-xs text-gray-400 px-3 py-1">暂无日志</p>
            ) : (
              dailyDates.slice(0, 14).map((date) => (
                <button
                  key={date}
                  onClick={() => setActive({ type: "daily", date })}
                  className={clsx(
                    "w-full text-left px-3 py-1.5 rounded-lg text-xs font-mono transition-colors",
                    active.type === "daily" && active.date === date
                      ? "bg-nimo-100 text-nimo-700"
                      : "text-gray-500 hover:bg-gray-100",
                  )}
                >
                  {date}
                </button>
              ))
            )}
          </SidebarGroup>

          {/* Canvas */}
          <SidebarGroup
            label="Canvas"
            icon={FolderOpen}
            open={canvasOpen}
            onToggle={() => setCanvasOpen((v) => !v)}
          >
            {canvasFiles.length === 0 ? (
              <p className="text-xs text-gray-400 px-3 py-1">暂无文件</p>
            ) : (
              canvasFiles.map((f) => (
                <button
                  key={f.name}
                  onClick={() => setActive({ type: "canvas", name: f.name })}
                  className={clsx(
                    "w-full text-left px-3 py-1.5 rounded-lg text-xs font-mono transition-colors truncate",
                    active.type === "canvas" && active.name === f.name
                      ? "bg-nimo-100 text-nimo-700"
                      : "text-gray-500 hover:bg-gray-100",
                  )}
                >
                  {f.name}
                </button>
              ))
            )}
          </SidebarGroup>
        </div>
      </aside>

      {/* ── 右侧：编辑区 ── */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Toolbar */}
        <div className="px-5 py-3 bg-white border-b flex items-center justify-between gap-4 shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            <span className="font-mono text-sm text-nimo-600 font-medium truncate">
              {activeLabel}
            </span>
            {activeEntry && (
              <span className="text-xs text-gray-400 hidden sm:inline">
                {activeEntry.description}
              </span>
            )}
            {isReadOnly && (
              <span className="text-xs bg-gray-100 text-gray-500 px-2 py-0.5 rounded-full">
                只读
              </span>
            )}
            {isDirty && (
              <span className="text-xs bg-amber-100 text-amber-600 px-2 py-0.5 rounded-full">
                未保存
              </span>
            )}
          </div>

          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={() => loadFile(active)}
              disabled={loading}
              className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-gray-700 px-2.5 py-1.5 rounded-lg hover:bg-gray-100 transition-colors"
            >
              <RefreshCw size={13} className={loading ? "animate-spin" : ""} />
              刷新
            </button>

            {active.type === "core" && (
              <button
                onClick={handleSave}
                disabled={saving || !isDirty}
                className={clsx(
                  "flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg transition-colors",
                  saved
                    ? "bg-green-50 text-green-600"
                    : isDirty
                    ? "bg-nimo-500 text-white hover:bg-nimo-600"
                    : "bg-gray-100 text-gray-400 cursor-not-allowed",
                )}
              >
                {saving ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : saved ? (
                  <CheckCircle2 size={13} />
                ) : (
                  <Save size={13} />
                )}
                {saved ? "已保存" : "保存"}
              </button>
            )}
          </div>
        </div>

        {/* Editor */}
        <div className="flex-1 overflow-hidden flex flex-col">
          {loading ? (
            <div className="flex items-center justify-center h-full">
              <Loader2 size={24} className="animate-spin text-gray-300" />
            </div>
          ) : (
            <textarea
              value={content}
              onChange={(e) => !isReadOnly && setContent(e.target.value)}
              readOnly={isReadOnly}
              spellCheck={false}
              className={clsx(
                "flex-1 w-full resize-none font-mono text-sm leading-relaxed p-5 outline-none",
                "bg-white text-gray-800",
                isReadOnly ? "cursor-default text-gray-600" : "focus:bg-white",
              )}
              placeholder={isReadOnly ? "（暂无内容）" : "在此编辑内容…"}
            />
          )}
        </div>

        {/* Memory.md 快速追加面板 */}
        {active.type === "core" && active.id === "MEMORY.md" && (
          <div className="px-5 py-3 bg-amber-50 border-t border-amber-100 flex items-center gap-3 shrink-0">
            <PlusCircle size={15} className="text-amber-500 shrink-0" />
            <input
              value={memoryInput}
              onChange={(e) => setMemoryInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && handleAppendMemory()}
              placeholder="快速追加一条长期记忆（Enter 确认）…"
              className="flex-1 text-sm bg-transparent outline-none text-gray-700 placeholder-amber-400"
            />
            <button
              onClick={handleAppendMemory}
              disabled={appendingMemory || !memoryInput.trim()}
              className="flex items-center gap-1.5 text-xs bg-amber-500 text-white px-3 py-1.5 rounded-lg hover:bg-amber-600 disabled:opacity-40 transition-colors"
            >
              {appendingMemory ? <Loader2 size={12} className="animate-spin" /> : <PlusCircle size={12} />}
              追加
            </button>
          </div>
        )}

        {/* Workspace info footer */}
        {wsInfo && (
          <div className="px-5 py-2 bg-gray-50 border-t text-xs text-gray-400 flex items-center gap-4 shrink-0">
            <span className="font-mono truncate">{wsInfo.workspace_dir}</span>
            <span className={wsInfo.bootstrapped ? "text-green-500" : "text-amber-500"}>
              {wsInfo.bootstrapped ? "✓ 已初始化" : "⚠ 未初始化"}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
