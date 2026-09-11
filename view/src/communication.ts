import type { Row, Summary } from "./fold.js";
import { arr, obj, str } from "./types.js";
import type { ContentBlock } from "./types.js";

export type MessageKind = "ask" | "reply" | "send" | "notify" | "steer" | "default" | "message";
type Stage = "queued" | "received" | "acknowledged" | "confirmed";

export interface CommunicationEvidence {
  stage: Stage;
  episodeId: string;
  seq: number;
  time: number;
  from: string;
  to: string;
  toName?: string;
  messageId: string;
  kind: MessageKind;
  body: string;
}

export interface Communication extends CommunicationEvidence {
  id: string;
  delivered: boolean;
  stages: Set<Stage>;
}

function body(content: ContentBlock[], id: string): string {
  const blocks = [...content];
  const last = blocks.at(-1);
  if (id && last?.type === "text" && last.text === `Answer this with send, reply_to ${id}.`) blocks.pop();
  return blocks.map((block) => block.type === "text" ? str(block.text) : "[image]").join("\n\n");
}

/** Extract message evidence while keeping tool envelopes outside the conversation. */
export function readCommunication(summary: Summary, rows: Row[]): CommunicationEvidence[] {
  const evidence: CommunicationEvidence[] = [];
  const calls = new Map<string, { name: string; args: Record<string, unknown> }>();
  const put = (row: Row, value: Omit<CommunicationEvidence, "episodeId" | "seq" | "time">): void => {
    evidence.push({ episodeId: summary.id, seq: row.seq, time: row.time, ...value });
  };
  for (const row of rows) {
    if (row.kind === "assistant") {
      for (const call of row.toolCalls) calls.set(call.id, { name: call.name, args: obj(call.args) });
    } else if (row.kind === "user") {
      const kinds: Record<string, MessageKind> = { peer: "send", request: "ask", response: "reply", child: "notify", parent: "steer" };
      const kind = row.synthetic ? "default" : kinds[row.source];
      if (kind) put(row, {
        stage: "received", from: row.from, to: summary.id, messageId: row.messageId,
        kind, body: body(row.content, row.messageId),
      });
    } else if (row.kind === "note" && (row.type === "team/message" || row.type === "team/delivered")) {
      const data = obj(row.data);
      const id = str(data.message_id);
      put(row, {
        stage: row.type === "team/message" ? "queued" : "confirmed",
        from: str(data.from), to: str(data.to), messageId: id, kind: "message",
        body: body(arr(data.content) as ContentBlock[], id),
      });
    } else if (row.kind === "tool" && !row.isError && !row.synthetic) {
      const call = calls.get(row.callId);
      if (!call || !["ask", "send", "notify"].includes(call.name) || typeof call.args.content !== "string") continue;
      const id = str(obj(row.value).message_id);
      if (call.name !== "notify" && !id) continue;
      put(row, {
        stage: "acknowledged", from: summary.id,
        to: call.name === "notify" ? summary.parentId ?? "" : "", toName: str(call.args.to),
        messageId: id, kind: call.name === "send" ? (call.args.reply_to ? "reply" : "send") : call.name as MessageKind,
        body: call.args.content,
      });
    }
  }
  return evidence;
}

/** Join queue, receipt, and acknowledgement records without duplicating their bodies. */
export function communications(evidence: CommunicationEvidence[]): Communication[] {
  const records: Communication[] = [];
  for (const stage of ["queued", "received", "acknowledged", "confirmed"] as const) {
    for (const item of evidence.filter((entry) => entry.stage === stage)) {
      const known = item.kind === "default" ? undefined : records.find((record) => !record.stages.has(stage)
        && record.messageId === item.messageId
        && (!item.from || record.from === item.from)
        && (!item.to || record.to === item.to)
        && (stage !== "confirmed" || (record.stages.has("queued") && record.episodeId === item.episodeId && record.seq < item.seq))
        && (stage === "confirmed" || (record.body === item.body
          && (record.kind === "message" || record.kind === item.kind))));
      if (known) {
        known.stages.add(stage);
        known.delivered ||= stage === "received" || stage === "confirmed";
        if (item.kind !== "message") known.kind = item.kind;
        known.toName ||= item.toName;
      } else if (stage !== "confirmed") {
        records.push({ ...item, id: "", delivered: stage === "received", stages: new Set([stage]) });
      }
    }
  }
  const counts = new Map<string, number>();
  return records.filter((record) => record.kind !== "notify" || record.stages.has("acknowledged"))
    .map((record) => {
      const key = [record.from, record.to, record.messageId, record.kind === "default" ? "default" : "message"].map(encodeURIComponent).join("/");
      const ordinal = counts.get(key) ?? 0;
      counts.set(key, ordinal + 1);
      return { ...record, id: `communication/${key}/${ordinal}` };
    });
}
