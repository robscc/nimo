import clsx from "clsx";
import { parseShellOutput } from "../../utils/chatHelpers";

export default function ShellOutput({ command, raw }: { command: string; raw: string }) {
  const parsed = parseShellOutput(raw);

  if (parsed.type === "error") {
    return (
      <div className="rounded-lg bg-gray-950 text-xs font-mono overflow-hidden">
        <div className="flex items-center gap-2 px-3 py-2 bg-gray-800">
          <span className="text-gray-500">$</span>
          <span className="text-gray-300 flex-1 truncate">{command}</span>
        </div>
        <p className="px-3 py-2 text-red-400 whitespace-pre-wrap">{parsed.message}</p>
      </div>
    );
  }

  const ok = parsed.returncode === 0;

  return (
    <div className="rounded-lg bg-gray-950 text-xs font-mono overflow-hidden">
      {/* header */}
      <div className="flex items-center gap-2 px-3 py-2 bg-gray-800">
        <span className="text-gray-500">$</span>
        <span className="text-gray-300 flex-1 truncate">{command}</span>
        {parsed.returncode !== null && (
          <span className={clsx(
            "px-1.5 py-0.5 rounded text-[10px] font-semibold shrink-0",
            ok ? "bg-green-900/50 text-green-400" : "bg-red-900/50 text-red-400",
          )}>
            {ok ? "exit 0" : `exit ${parsed.returncode}`}
          </span>
        )}
      </div>
      {/* stdout */}
      {parsed.stdout && (
        <pre className="px-3 py-2.5 text-gray-300 overflow-x-auto max-h-72 whitespace-pre-wrap leading-relaxed">
          {parsed.stdout}
        </pre>
      )}
      {/* stderr */}
      {parsed.stderr && (
        <pre className={clsx(
          "px-3 py-2.5 overflow-x-auto max-h-40 whitespace-pre-wrap leading-relaxed",
          parsed.stdout && "border-t border-gray-800",
          ok ? "text-amber-400" : "text-red-400",
        )}>
          {parsed.stderr}
        </pre>
      )}
    </div>
  );
}
