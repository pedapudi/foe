// The derived-messages rule from docs/log-format.md, "Derived messages".
// The runtime, this viewer, and the Python package compute the same list.

import { arr, num, obj, str } from "./types.js";
import type { ContentBlock, LogEvent, ToolCall } from "./types.js";

export type DerivedMessage =
  | { role: "user"; content: ContentBlock[] }
  | { role: "assistant"; text: string; tool_calls: ToolCall[] }
  | { role: "tool"; call_id: string; name: string; rendered: string; is_error: boolean };

/**
 * Computes the message list for the `model/request` event at `requestSeq`.
 *
 * An inbox item enters the list at the position of the request that
 * consumed it. This keeps a steering message that arrived while a tool was
 * running after that tool's result, and places the items consumed by the
 * request being built at the end of the list.
 */
export function deriveMessages(events: LogEvent[], requestSeq: number): DerivedMessage[] {
  const inbox = new Map<number, LogEvent>();
  const out: DerivedMessage[] = [];
  for (const ev of events) {
    if (ev.seq > requestSeq) break;
    switch (ev.type) {
      case "inbox/item":
        inbox.set(ev.seq, ev);
        break;
      case "model/request": {
        const consumed = arr(ev.data.consumed).map((s) => num(s, -1));
        const blocks: ContentBlock[] = [];
        for (const seq of consumed) {
          const item = inbox.get(seq);
          if (item) blocks.push(...(arr(item.data.content) as ContentBlock[]));
        }
        if (consumed.length > 0) mergeUser(out, blocks);
        break;
      }
      case "assistant/message":
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

/** Consecutive user messages merge into one with concatenated blocks. */
function mergeUser(out: DerivedMessage[], blocks: ContentBlock[]): void {
  const last = out[out.length - 1];
  if (last && last.role === "user") {
    last.content.push(...blocks);
  } else {
    out.push({ role: "user", content: blocks });
  }
}

/** Every `model/request` in the log paired with its recomputed messages. */
export function deriveAllRequests(
  events: LogEvent[],
): { seq: number; recorded: unknown[]; derived: DerivedMessage[] }[] {
  return events
    .filter((ev) => ev.type === "model/request")
    .map((ev) => ({
      seq: ev.seq,
      recorded: arr(ev.data.messages),
      derived: deriveMessages(events, ev.seq),
    }));
}
