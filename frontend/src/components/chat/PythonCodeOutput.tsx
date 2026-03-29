import clsx from "clsx";
import { Code2, Terminal } from "lucide-react";
import { parseShellOutput } from "../../utils/chatHelpers";

export default function PythonCodeOutput({
  code,
  packages,
  raw,
}: {
  code: string;
  packages?: string[];
  raw: string;
}) {
  const parsed = parseShellOutput(raw);
  const lines = code.split("\n");

  if (parsed.type === "error") {
    return (
      <div className="rounded-lg text-xs font-mono overflow-hidden">
        <div className="flex items-center gap-2 px-3 py-2 bg-[#181825]">
          <Code2 size={11} className="text-blue-400 shrink-0" />
          <span className="text-[#7f849c]">python</span>
        </div>
        <pre className="bg-[#1e1e2e] px-3 py-2.5 text-red-400 whitespace-pre-wrap">{parsed.message}</pre>
      </div>
    );
  }

  const ok = parsed.returncode === 0;
  const hasOutput = !!(parsed.stdout || parsed.stderr);

  return (
    <div className="rounded-lg text-xs font-mono overflow-hidden shadow-sm border border-white/5">
      {/* ── header ── */}
      <div className="flex items-center gap-2 px-3 py-2 bg-[#181825]">
        <Code2 size={11} className="text-blue-400 shrink-0" />
        <span className="text-[#7f849c] text-[10px]">python</span>
        {packages && packages.length > 0 && (
          <>
            <span className="text-[#45475a] text-[10px] select-none">·</span>
            <span className="text-[#7f849c] text-[10px]">pip install {packages.join(" ")}</span>
          </>
        )}
        {parsed.returncode !== null && (
          <span className={clsx(
            "ml-auto px-1.5 py-0.5 rounded text-[10px] font-semibold shrink-0",
            ok ? "bg-green-900/40 text-green-400" : "bg-red-900/40 text-red-400",
          )}>
            {ok ? "✓ 成功" : `exit ${parsed.returncode}`}
          </span>
        )}
      </div>

      {/* ── code block ── */}
      <div className="bg-[#1e1e2e] flex overflow-x-auto">
        {/* line numbers */}
        <div className="select-none py-3 px-3 text-right text-[#45475a] shrink-0 leading-[1.65]">
          {lines.map((_, i) => (
            <div key={i}>{i + 1}</div>
          ))}
        </div>
        {/* code text */}
        <pre className="flex-1 py-3 pr-4 text-[#cdd6f4] whitespace-pre leading-[1.65] min-w-0 overflow-x-auto">
          {code}
        </pre>
      </div>

      {/* ── output section ── */}
      {hasOutput && (
        <>
          <div className="flex items-center gap-1.5 px-3 py-1.5 bg-[#13131a] border-t border-white/5">
            <Terminal size={9} className="text-[#45475a]" />
            <span className="text-[9px] text-[#45475a] uppercase tracking-widest select-none">输出</span>
          </div>
          <div className="bg-[#0d0d14]">
            {parsed.stdout && (
              <pre className="px-3 py-2.5 text-[#cdd6f4] overflow-x-auto max-h-64 whitespace-pre-wrap leading-[1.65]">
                {parsed.stdout}
              </pre>
            )}
            {parsed.stderr && (
              <pre className={clsx(
                "px-3 py-2.5 overflow-x-auto max-h-40 whitespace-pre-wrap leading-[1.65]",
                parsed.stdout ? "border-t border-white/5" : "",
                ok ? "text-amber-400" : "text-red-400",
              )}>
                {parsed.stderr}
              </pre>
            )}
          </div>
        </>
      )}
    </div>
  );
}
