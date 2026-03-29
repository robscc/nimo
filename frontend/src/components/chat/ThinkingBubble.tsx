import { useState, useEffect } from "react";
import { Brain, Loader2, ChevronDown, ChevronRight } from "lucide-react";

export default function ThinkingBubble({ content, streaming }: { content: string; streaming?: boolean }) {
  const [collapsed, setCollapsed] = useState(false);

  // 思考完成后 1.5s 自动折叠
  useEffect(() => {
    if (!streaming && content) {
      const t = setTimeout(() => setCollapsed(true), 1500);
      return () => clearTimeout(t);
    }
  }, [streaming, content]);

  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50/80 text-xs overflow-hidden">
      <button
        onClick={() => setCollapsed((c) => !c)}
        className="w-full flex items-center gap-1.5 px-3 py-1.5 text-left hover:bg-gray-100 transition-colors"
      >
        {streaming
          ? <Loader2 size={11} className="text-gray-400 animate-spin shrink-0" />
          : <Brain size={11} className="text-gray-400 shrink-0" />}
        <span className="text-gray-500 font-medium">思考过程</span>
        {streaming && <span className="text-gray-400 animate-pulse ml-0.5">…</span>}
        <span className="ml-auto text-gray-400 shrink-0">
          {collapsed
            ? <ChevronRight size={11} />
            : <ChevronDown size={11} />}
        </span>
      </button>
      {!collapsed && (
        <div className="px-3 pt-2 pb-2.5 border-t border-gray-200 text-gray-500 leading-relaxed whitespace-pre-wrap max-h-52 overflow-y-auto">
          {content}
          {streaming && (
            <span className="inline-block w-0.5 h-[1em] bg-gray-400 ml-0.5 align-middle animate-pulse" />
          )}
        </div>
      )}
    </div>
  );
}
