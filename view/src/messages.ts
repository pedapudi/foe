// The derived-messages rule from docs/log-format.md, "Derived messages".
// The runtime, this viewer, and the Python package compute the same list.

import { arr, num, obj, str } from "./types.js";
import type { ContentBlock, LogEvent, ToolCall } from "./types.js";

export type DerivedMessage =
  | { role: "user"; content: ContentBlock[] }
  | { role: "assistant"; text: string; tool_calls: ToolCall[] }
  | { role: "tool"; call_id: string; name: string; rendered: string; is_error: boolean };

/** Request ids of summarization requests, whose events contribute nothing. */
export const SUMMARY_REQUEST_PREFIX = "cmp_";

export function isSummaryRequest(requestId: unknown): boolean {
  return str(requestId).startsWith(SUMMARY_REQUEST_PREFIX);
}

/**
 * Computes the message list for the `model/request` event at `requestSeq`.
 *
 * After the latest `compaction/summary` before the request, the list opens
 * with the task verbatim and the continuation message, and only events
 * from the summary's `first_kept_seq` on contribute. An inbox item enters
 * the list at the position of the request that consumed it, wherever the
 * item itself lies. This keeps a steering message that arrived while a
 * tool was running after that tool's result, and places the items consumed
 * by the request being built at the end of the list.
 */
export function deriveMessages(events: LogEvent[], requestSeq: number): DerivedMessage[] {
  const inbox = new Map<number, LogEvent>();
  const out: DerivedMessage[] = [];
  const summary = latestSummary(events, requestSeq);
  let from = 0;
  if (summary) {
    out.push(userText(str(obj(summary.data.state).task)));
    out.push(userText(renderContinuation(summary.data)));
    from = num(summary.data.first_kept_seq);
  }
  for (const ev of events) {
    if (ev.seq > requestSeq) break;
    if (ev.type === "inbox/item") inbox.set(ev.seq, ev);
    if (ev.seq < from) continue;
    switch (ev.type) {
      case "model/request": {
        if (isSummaryRequest(ev.data.request_id)) break;
        const blocks: ContentBlock[] = [];
        for (const seq of arr(ev.data.consumed).map((s) => num(s, -1))) {
          const item = inbox.get(seq);
          if (item) blocks.push(...(arr(item.data.content) as ContentBlock[]));
        }
        if (blocks.length > 0) out.push({ role: "user", content: blocks });
        break;
      }
      case "assistant/message":
        if (isSummaryRequest(ev.data.request_id)) break;
        out.push({
          role: "assistant",
          text: str(ev.data.text),
          tool_calls: arr(ev.data.tool_calls).map((c) => obj(c) as ToolCall),
        });
        break;
      case "tool/result":
        out.push({
          role: "tool",
          call_id: str(ev.data.call_id),
          name: str(ev.data.name),
          rendered: str(ev.data.rendered),
          is_error: ev.data.is_error === true,
        });
        break;
      default:
        break;
    }
  }
  return out;
}

function userText(text: string): DerivedMessage {
  return { role: "user", content: [{ type: "text", text }] };
}

/** The latest `compaction/summary` strictly before `seq`, if any. */
export function latestSummary(events: LogEvent[], seq: number): LogEvent | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i]!;
    if (ev.seq < seq && ev.type === "compaction/summary") return ev;
  }
  return null;
}

const STATE_NONE = "(none)";

/** A list value: one item per line below the label, or `(none)` after it. */
function list(items: unknown[]): string {
  return items.length === 0 ? ` ${STATE_NONE}` : items.map((item) => `\n- ${String(item)}`).join("");
}

function amount(n: unknown): string {
  return typeof n === "number" ? String(n) : "unlimited";
}

/**
 * The continuation message a `compaction/summary` contributes: the state
 * as labeled lines, then the model's summary. Byte for byte what the
 * runtime renders, so a recorded request can be checked against it.
 */
export function renderContinuation(data: Record<string, unknown>): string {
  const s = obj(data.state);
  const files = obj(s.files);
  const covered = obj(s.covered);
  const budget = obj(s.budget_remaining);
  const children = arr(s.children).map((child) => {
    const c = obj(child);
    const o = obj(c.outcome);
    const detail = str(o.code) || str(o.limit);
    return `${str(c.id)} (${str(c.program)}): ${str(o.kind)}${detail ? ` ${detail}` : ""}`;
  });
  const lines = [
    `covered: seq ${num(covered.first_seq)} to ${num(covered.last_seq)}`,
    `done_when: ${str(s.done_when)}`,
    `outstanding_findings:${list(arr(s.outstanding_findings))}`,
    `files_read:${list(arr(files.read))}`,
    `files_written:${list(arr(files.written))}`,
    `files_edited:${list(arr(files.edited))}`,
    `children:${list(children)}`,
    `budget_remaining: model_calls ${amount(budget.model_calls)}, tokens ${amount(budget.tokens)}, seconds ${amount(
      budget.seconds,
    )}`,
  ];
  return `## Continuation state\n\n${lines.join("\n")}\n\n## Summary\n\n${str(data.summary)}`;
}

/**
 * Every ordinary `model/request` in the log paired with its recomputed
 * messages. A summarization request records its prompt rather than a
 * derived list, so it is left out.
 */
export function deriveAllRequests(
  events: LogEvent[],
): { seq: number; recorded: unknown[]; derived: DerivedMessage[] }[] {
  return events
    .filter((ev) => ev.type === "model/request" && !isSummaryRequest(ev.data.request_id))
    .map((ev) => ({
      seq: ev.seq,
      recorded: arr(ev.data.messages),
      derived: deriveMessages(events, ev.seq),
    }));
}
