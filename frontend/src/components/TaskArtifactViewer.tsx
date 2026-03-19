import { useState } from "react";
import { FileText, Code, FileOutput, ChevronDown, ChevronRight, Copy, Check } from "lucide-react";
import type { TaskArtifact } from "../api";

interface TaskArtifactViewerProps {
  artifacts: TaskArtifact[];
}

const ARTIFACT_TYPE_CONFIG: Record<
  string,
  { icon: typeof FileText; color: string; bg: string; label: string }
> = {
  code: { icon: Code, color: "text-blue-600", bg: "bg-blue-50", label: "代码" },
  doc: { icon: FileText, color: "text-green-600", bg: "bg-green-50", label: "文档" },
  analysis: { icon: FileOutput, color: "text-purple-600", bg: "bg-purple-50", label: "分析" },
  summary: { icon: FileText, color: "text-orange-600", bg: "bg-orange-50", label: "总结" },
  report: { icon: FileOutput, color: "text-indigo-600", bg: "bg-indigo-50", label: "报告" },
};

function ArtifactIcon({ type }: { type: string }) {
  const cfg = ARTIFACT_TYPE_CONFIG[type] ?? ARTIFACT_TYPE_CONFIG.doc;
  const Icon = cfg.icon;
  return (
    <div className={`p-1.5 rounded-lg ${cfg.bg}`}>
      <Icon size={14} className={cfg.color} />
    </div>
  );
}

function ArtifactTypeBadge({ type }: { type: string }) {
  const cfg = ARTIFACT_TYPE_CONFIG[type] ?? ARTIFACT_TYPE_CONFIG.doc;
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${cfg.bg} ${cfg.color}`}>
      {cfg.label}
    </span>
  );
}

function ArtifactContent({ artifact }: { artifact: TaskArtifact }) {
  const [copied, setCopied] = useState(false);
  const [expanded, setExpanded] = useState(true);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(artifact.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const isCode = artifact.artifact_type === "code";

  return (
    <div className="mt-3 border rounded-lg overflow-hidden bg-gray-50">
      <div className="flex items-center justify-between px-3 py-2 bg-gray-100 border-b">
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-1 text-xs text-gray-600 hover:text-gray-900"
        >
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          {expanded ? "收起" : "展开"}
        </button>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 px-2 py-1 text-xs text-gray-600 hover:text-gray-900 hover:bg-gray-200 rounded transition-colors"
        >
          {copied ? <Check size={12} className="text-green-600" /> : <Copy size={12} />}
          {copied ? "已复制" : "复制"}
        </button>
      </div>
      {expanded && (
        <pre
          className={`p-3 text-xs overflow-x-auto ${
            isCode ? "bg-gray-900 text-gray-100 font-mono" : "bg-white text-gray-800"
          }`}
          style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}
        >
          {artifact.content}
        </pre>
      )}
    </div>
  );
}

export function TaskArtifactViewer({ artifacts }: TaskArtifactViewerProps) {
  const [selectedArtifact, setSelectedArtifact] = useState<TaskArtifact | null>(null);

  if (artifacts.length === 0) {
    return (
      <div className="text-center py-8 text-gray-500 text-sm">
        暂无产出物
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-sm font-medium text-gray-700">
        <FileOutput size={16} />
        任务产出物 ({artifacts.length})
      </div>
      <div className="grid gap-2">
        {artifacts.map((artifact) => (
          <div
            key={artifact.id}
            className="p-3 bg-white border rounded-lg hover:border-blue-300 cursor-pointer transition-colors"
            onClick={() => setSelectedArtifact(selectedArtifact?.id === artifact.id ? null : artifact)}
          >
            <div className="flex items-center gap-2">
              <ArtifactIcon type={artifact.artifact_type} />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-gray-900 truncate">
                    {artifact.title}
                  </span>
                  <ArtifactTypeBadge type={artifact.artifact_type} />
                </div>
                <div className="text-xs text-gray-500 mt-1">
                  {artifact.content.length} 字符 · {new Date(artifact.created_at).toLocaleString("zh-CN")}
                </div>
              </div>
              {selectedArtifact?.id === artifact.id ? (
                <ChevronDown size={16} className="text-gray-400" />
              ) : (
                <ChevronRight size={16} className="text-gray-400" />
              )}
            </div>
            {selectedArtifact?.id === artifact.id && (
              <ArtifactContent artifact={artifact} />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
