import { useState, useRef } from "react";
import {
  Puzzle, Upload, Link as LinkIcon, Trash2,
  CheckCircle2, XCircle, Loader2, ChevronDown, ChevronRight,
  Package, AlertTriangle, ExternalLink,
} from "lucide-react";
import clsx from "clsx";
import {
  useSkills,
  useToggleSkill,
  useInstallSkillFromZip,
  useInstallSkillFromUrl,
  useUninstallSkill,
  type SkillInfo,
} from "../hooks/useSkills";

// ── Toggle Switch ─────────────────────────────────────────
function Toggle({ enabled, onChange, disabled }: {
  enabled: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={(e) => { e.stopPropagation(); !disabled && onChange(!enabled); }}
      disabled={disabled}
      className={clsx(
        "relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none",
        enabled ? "bg-nimo-500" : "bg-gray-200",
        disabled && "opacity-50 cursor-not-allowed"
      )}
    >
      <span
        className={clsx(
          "inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform",
          enabled ? "translate-x-6" : "translate-x-1"
        )}
      />
    </button>
  );
}

// ── Source Badge ───────────────────────────────────────────
function SourceBadge({ source }: { source: string }) {
  const colors: Record<string, string> = {
    local: "bg-gray-100 text-gray-600",
    upload: "bg-blue-100 text-blue-600",
    url: "bg-green-100 text-green-600",
    clawhub: "bg-purple-100 text-purple-600",
    "skills.sh": "bg-amber-100 text-amber-600",
  };

  return (
    <span className={clsx("text-xs px-1.5 py-0.5 rounded font-medium", colors[source] || "bg-gray-100 text-gray-600")}>
      {source}
    </span>
  );
}

// ── Skill Card ────────────────────────────────────────────
function SkillCard({
  skill,
  onToggle,
  onDelete,
  togglePending,
  deletePending,
}: {
  skill: SkillInfo;
  onToggle: (enabled: boolean) => void;
  onDelete: () => void;
  togglePending: boolean;
  deletePending: boolean;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className={clsx(
      "rounded-xl border transition-all",
      skill.enabled ? "bg-white border-gray-200" : "bg-gray-50 border-gray-100",
    )}>
      <div
        className="flex items-start gap-3 p-4 cursor-pointer hover:bg-black/[0.02] transition-colors"
        onClick={() => setOpen((o) => !o)}
      >
        {/* Icon */}
        <div className={clsx(
          "w-10 h-10 rounded-lg flex items-center justify-center shrink-0",
          skill.enabled ? "bg-nimo-100 text-nimo-500" : "bg-gray-100 text-gray-400"
        )}>
          <Puzzle size={20} />
        </div>

        {/* Info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className={clsx(
              "text-sm font-semibold",
              skill.enabled ? "text-gray-800" : "text-gray-400"
            )}>
              {skill.name}
            </span>
            <span className="text-xs text-gray-400">v{skill.version}</span>
            <SourceBadge source={skill.source} />
          </div>
          <p className="text-xs text-gray-400 mt-0.5">{skill.description || "暂无描述"}</p>
          {skill.tools.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1.5">
              {skill.tools.map((t) => (
                <span key={t} className="text-xs bg-nimo-50 text-nimo-500 px-1.5 py-0.5 rounded font-mono">
                  {t}
                </span>
              ))}
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-center gap-2 shrink-0">
          <Toggle
            enabled={skill.enabled}
            onChange={onToggle}
            disabled={togglePending}
          />
          {open
            ? <ChevronDown size={14} className="text-gray-400" />
            : <ChevronRight size={14} className="text-gray-400" />}
        </div>
      </div>

      {/* Expanded details */}
      {open && (
        <div className="px-4 pb-4 border-t border-gray-100 pt-3 space-y-2">
          <div className="grid grid-cols-2 gap-2 text-xs">
            {skill.author && (
              <div>
                <span className="text-gray-400">作者:</span>{" "}
                <span className="text-gray-700">{skill.author}</span>
              </div>
            )}
            {skill.source_url && (
              <div className="flex items-center gap-1">
                <span className="text-gray-400">来源:</span>{" "}
                <a
                  href={skill.source_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-nimo-500 hover:underline flex items-center gap-0.5"
                  onClick={(e) => e.stopPropagation()}
                >
                  链接 <ExternalLink size={10} />
                </a>
              </div>
            )}
            {skill.created_at && (
              <div>
                <span className="text-gray-400">安装于:</span>{" "}
                <span className="text-gray-700">
                  {new Date(skill.created_at).toLocaleDateString("zh-CN")}
                </span>
              </div>
            )}
          </div>
          <div className="flex justify-end pt-2">
            <button
              onClick={(e) => { e.stopPropagation(); onDelete(); }}
              disabled={deletePending}
              className="flex items-center gap-1.5 text-xs text-red-500 hover:text-red-700 hover:bg-red-50 px-3 py-1.5 rounded-lg transition-colors"
            >
              {deletePending ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
              卸载
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────
export default function SkillsPage() {
  const { data: skills = [], isLoading, refetch } = useSkills();
  const toggle = useToggleSkill();
  const installZip = useInstallSkillFromZip();
  const installUrl = useInstallSkillFromUrl();
  const uninstall = useUninstallSkill();

  const [urlInput, setUrlInput] = useState("");
  const [showUrlForm, setShowUrlForm] = useState(false);
  const [installMsg, setInstallMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const enabledCount = skills.filter((s) => s.enabled).length;

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setInstallMsg(null);
    try {
      const result = await installZip.mutateAsync(file);
      setInstallMsg({ ok: true, text: `已安装 ${result.name} v${result.version}（${result.tools.length} 个工具）` });
    } catch (err) {
      setInstallMsg({ ok: false, text: `安装失败: ${err instanceof Error ? err.message : String(err)}` });
    }
    // Reset file input
    if (fileRef.current) fileRef.current.value = "";
  };

  const handleUrlInstall = async () => {
    if (!urlInput.trim()) return;
    setInstallMsg(null);
    try {
      const result = await installUrl.mutateAsync(urlInput.trim());
      setInstallMsg({ ok: true, text: `已安装 ${result.name} v${result.version}（${result.tools.length} 个工具）` });
      setUrlInput("");
      setShowUrlForm(false);
    } catch (err) {
      setInstallMsg({ ok: false, text: `安装失败: ${err instanceof Error ? err.message : String(err)}` });
    }
  };

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="px-6 py-4 bg-white border-b">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="font-semibold text-gray-800 flex items-center gap-2">
              <Package size={20} className="text-nimo-500" />
              技能管理
            </h1>
            <p className="text-xs text-gray-400 mt-0.5">
              安装、管理和配置 Agent 技能包 · {enabledCount}/{skills.length} 已启用
            </p>
          </div>

          {/* Install buttons */}
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowUrlForm((o) => !o)}
              className="flex items-center gap-1.5 text-xs text-gray-600 hover:text-nimo-500 hover:bg-nimo-50 px-3 py-2 rounded-lg transition-colors border border-gray-200"
            >
              <LinkIcon size={14} />
              从 URL 安装
            </button>
            <label className="flex items-center gap-1.5 text-xs text-white bg-nimo-500 hover:bg-nimo-600 px-3 py-2 rounded-lg transition-colors cursor-pointer">
              <Upload size={14} />
              上传 ZIP
              <input
                ref={fileRef}
                type="file"
                accept=".zip"
                className="hidden"
                onChange={handleFileUpload}
                disabled={installZip.isPending}
              />
            </label>
          </div>
        </div>

        {/* URL install form */}
        {showUrlForm && (
          <div className="mt-3 flex gap-2">
            <input
              value={urlInput}
              onChange={(e) => setUrlInput(e.target.value)}
              placeholder="输入技能包 URL（支持 clawhub.ai / skills.sh / 直接 ZIP）"
              className="flex-1 rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-nimo-400 transition-colors"
              onKeyDown={(e) => e.key === "Enter" && handleUrlInstall()}
            />
            <button
              onClick={handleUrlInstall}
              disabled={installUrl.isPending || !urlInput.trim()}
              className="px-4 py-2 text-sm rounded-lg bg-nimo-500 text-white hover:bg-nimo-600 disabled:opacity-40 transition-colors flex items-center gap-1.5"
            >
              {installUrl.isPending ? <Loader2 size={14} className="animate-spin" /> : null}
              安装
            </button>
          </div>
        )}

        {/* Install message */}
        {installMsg && (
          <div className={clsx(
            "mt-2 flex items-center gap-2 text-xs px-3 py-2 rounded-lg",
            installMsg.ok ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700"
          )}>
            {installMsg.ok ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
            {installMsg.text}
            <button
              onClick={() => setInstallMsg(null)}
              className="ml-auto text-gray-400 hover:text-gray-600"
            >
              ×
            </button>
          </div>
        )}
      </div>

      {/* Skill list */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {isLoading ? (
          <div className="flex items-center justify-center py-24">
            <Loader2 size={24} className="animate-spin text-gray-300" />
          </div>
        ) : skills.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-24 text-gray-400 gap-4">
            <Package size={48} className="text-gray-200" />
            <div className="text-center">
              <p className="text-base font-medium">尚未安装技能</p>
              <p className="text-sm mt-1">
                上传 ZIP 包或从 URL 安装技能，让你的 Agent 获得新能力
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-3 max-w-3xl mx-auto">
            {skills.map((skill) => (
              <SkillCard
                key={skill.name}
                skill={skill}
                onToggle={(enabled) => toggle.mutate({ name: skill.name, enabled })}
                onDelete={() => {
                  if (confirm(`确定卸载技能 "${skill.name}" 吗？此操作不可撤销。`)) {
                    uninstall.mutate(skill.name);
                  }
                }}
                togglePending={toggle.isPending}
                deletePending={uninstall.isPending}
              />
            ))}
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="px-6 py-3 bg-white border-t">
        <p className="text-xs text-gray-400 flex items-center gap-1.5">
          <AlertTriangle size={12} />
          技能包中的代码会在服务端执行，请仅安装信任来源的技能
        </p>
      </div>
    </div>
  );
}
