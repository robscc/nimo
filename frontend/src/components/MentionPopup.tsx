import { useEffect, useRef } from "react";

export interface MentionAgent {
  name: string;
  display_name: string;
  accepted_task_types: string[];
}

interface MentionPopupProps {
  agents: MentionAgent[];
  selectedIndex: number;
  onSelect: (agent: MentionAgent) => void;
}

export default function MentionPopup({ agents, selectedIndex, onSelect }: MentionPopupProps) {
  const listRef = useRef<HTMLDivElement>(null);

  // 滚动选中项到可视区域
  useEffect(() => {
    const container = listRef.current;
    if (!container) return;
    const item = container.children[selectedIndex] as HTMLElement | undefined;
    item?.scrollIntoView({ block: "nearest" });
  }, [selectedIndex]);

  if (agents.length === 0) return null;

  return (
    <div
      ref={listRef}
      className="absolute bottom-full left-0 mb-2 w-72 bg-white border border-gray-200 rounded-xl shadow-lg max-h-48 overflow-y-auto z-50"
    >
      <div className="py-1">
        {agents.map((agent, i) => (
          <div
            key={agent.name}
            onMouseDown={(e) => {
              e.preventDefault(); // 防止 input 失焦
              onSelect(agent);
            }}
            className={`px-3 py-2 cursor-pointer transition-colors ${
              i === selectedIndex ? "bg-nimo-50" : "hover:bg-gray-50"
            }`}
          >
            <div className="flex items-baseline gap-2">
              <span className="text-sm font-medium text-gray-800">
                {agent.display_name}
              </span>
              <span className="text-xs text-gray-400 font-mono">
                @{agent.name}
              </span>
            </div>
            {agent.accepted_task_types.length > 0 && (
              <div className="mt-0.5 flex flex-wrap gap-1">
                {agent.accepted_task_types.map((t) => (
                  <span
                    key={t}
                    className="text-[10px] text-gray-400 bg-gray-100 rounded px-1 py-0.5"
                  >
                    {t}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
