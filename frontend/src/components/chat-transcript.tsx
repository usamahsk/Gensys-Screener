"use client";

import { useEffect, useRef } from "react";

export interface ChatTurn {
  role: string;
  content: string;
  time?: string;
}

const AGENT_ROLES = new Set(["assistant", "agent", "bot", "ai"]);

// Matches a "[2026-10-02T12:42:43.006+00:00] user:" turn marker anywhere,
// so transcripts stored as one blob (markers inline, no newlines) still split.
const MARKER_RE = /\[\d{4}-\d{2}-\d{2}T[^\]]*\]\s*(assistant|user|customer|bot|agent|ai)\s*:/i;
const MARKER_SPLIT_RE =
  /(?=\[\d{4}-\d{2}-\d{2}T[^\]]*\]\s*(?:assistant|user|customer|bot|agent|ai)\s*:)/gi;
const LEADING_MARKER_RE =
  /^\s*\[\d{4}-\d{2}-\d{2}T[^\]]*\]\s*(?:assistant|user|customer|bot|agent|ai)\s*:/i;

/**
 * Parse raw transcript text like:
 *   [2026-09-08T08:01:49.755+00:00] assistant: Hello...
 *   [2026-09-08T08:01:56.186+00:00] user: ...
 * Markers may also sit inline without newlines — each one starts a new turn.
 * Continuation lines are appended to the previous turn.
 */
export function parseRawTranscript(text: string): ChatTurn[] {
  const turns: ChatTurn[] = [];
  for (const chunk of text.split(MARKER_SPLIT_RE)) {
    for (const line of chunk.split("\n")) {
      const m = line.match(/^\[(.*?)\]\s*(assistant|user|customer|bot|agent|ai)\s*:\s*(.*)$/i);
      if (m) {
        turns.push({ time: m[1], role: m[2].toLowerCase(), content: m[3] });
      } else if (line.trim() && turns.length > 0) {
        turns[turns.length - 1].content += "\n" + line;
      } else if (line.trim()) {
        turns.push({ role: "assistant", content: line });
      }
    }
  }
  return turns.filter((t) => t.content.trim());
}

/**
 * Expand blob turns that carry inline "[ts] role:" markers into real turns.
 * Saved transcript rows are sometimes a single turn holding the whole raw
 * log — without this the UI renders one giant bubble ("1 turns").
 */
export function normalizeTurns(turns: ChatTurn[]): ChatTurn[] {
  const out: ChatTurn[] = [];
  for (const t of turns) {
    const idx = t.content.search(MARKER_RE);
    if (idx > 0) {
      const head = t.content.slice(0, idx).trim();
      if (head) out.push({ role: t.role, content: head, time: t.time });
      out.push(...parseRawTranscript(t.content.slice(idx)));
    } else if (idx === 0) {
      const parsed = parseRawTranscript(t.content);
      if (parsed.length > 1) out.push(...parsed);
      else out.push(t);
    } else {
      out.push(t);
    }
  }
  return out;
}

function fmtTime(t?: string) {
  if (!t) return "";
  const d = new Date(t);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export function ChatTranscript({ turns }: { turns: ChatTurn[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const norm = normalizeTurns(turns);

  useEffect(() => {
    const el = ref.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns]);

  if (norm.length === 0) {
    return <div className="text-sm text-muted-foreground">No transcript available.</div>;
  }

  return (
    <div ref={ref} className="max-h-72 w-full min-w-0 space-y-2 overflow-y-auto overflow-x-hidden rounded-lg bg-[#E7E0D3] p-3">
      {norm.map((t, i) => {
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
