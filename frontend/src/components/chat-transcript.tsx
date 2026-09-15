"use client";

import { useEffect, useRef } from "react";

export interface ChatTurn {
  role: string;
  content: string;
  time?: string;
}

const AGENT_ROLES = new Set(["assistant", "agent", "bot", "ai"]);

/**
 * Parse raw transcript text like:
 *   [2026-09-08T08:01:49.755+00:00] assistant: Hello...
 *   [2026-09-08T08:01:56.186+00:00] user: ...
 * Continuation lines are appended to the previous turn.
 */
export function parseRawTranscript(text: string): ChatTurn[] {
  const turns: ChatTurn[] = [];
  for (const line of text.split("\n")) {
    const m = line.match(/^\[(.*?)\]\s*(assistant|user|customer|bot|agent|ai)\s*:\s*(.*)$/i);
    if (m) {
      turns.push({ time: m[1], role: m[2].toLowerCase(), content: m[3] });
    } else if (line.trim() && turns.length > 0) {
      turns[turns.length - 1].content += "\n" + line;
    } else if (line.trim()) {
      turns.push({ role: "assistant", content: line });
    }
  }
  return turns.filter((t) => t.content.trim());
}

function fmtTime(t?: string) {
  if (!t) return "";
  const d = new Date(t);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export function ChatTranscript({ turns }: { turns: ChatTurn[] }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns]);

  if (turns.length === 0) {
    return <div className="text-sm text-muted-foreground">No transcript available.</div>;
  }

  return (
    <div ref={ref} className="max-h-72 w-full min-w-0 space-y-2 overflow-y-auto overflow-x-hidden rounded-lg bg-[#E7E0D3] p-3">
      {turns.map((t, i) => {
        const isAgent = AGENT_ROLES.has(t.role.toLowerCase());
        return (
          <div key={i} className={isAgent ? "flex min-w-0 justify-start" : "flex min-w-0 justify-end"}>
            <div
              className={
                isAgent
                  ? "max-w-[85%] min-w-0 rounded-2xl rounded-tl-sm bg-white px-3 py-2 text-sm text-neutral-900 shadow-sm"
                  : "max-w-[85%] min-w-0 rounded-2xl rounded-tr-sm bg-neutral-900 px-3 py-2 text-sm text-white shadow-sm"
              }
            >
              <div
                className={
                  isAgent
                    ? "mb-0.5 text-[10px] font-semibold uppercase tracking-wide text-red-600"
                    : "mb-0.5 text-[10px] font-semibold uppercase tracking-wide text-neutral-300"
                }
              >
                {isAgent ? "Agent" : "Customer"}
              </div>
              <div className="min-w-0 overflow-hidden break-words whitespace-pre-wrap [overflow-wrap:anywhere]">{t.content}</div>
              {t.time && (
                <div className={isAgent ? "mt-1 text-right text-[10px] text-neutral-400" : "mt-1 text-right text-[10px] text-neutral-400"}>
                  {fmtTime(t.time)}
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
