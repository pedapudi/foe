// Folds one episode log into the rows the conversation pane shows and the
// summary the tree pane shows. The fold is incremental: each pushed event
// yields patches naming only the rows it created or changed.

import { fmtInt } from "./dom.js";
import { renderContinuation } from "./messages.js";
import { headerDifference } from "./prompt.js";
import type { HeaderParts } from "./prompt.js";
import type { Mark, NodeDecision, NodeFiring } from "./trajectory.js";
import { arr, num, obj, str } from "./types.js";
import type {
  ContentBlock,
  ForkOrigin,
  LogEvent,
  Outcome,
  ToolSchema,
  Usage,
} from "./types.js";

interface RowBase {
  key: string;
  seq: number;
  time: number;
}

export interface HeaderRow extends RowBase {
  kind: "header";
  reason: string;
  system: string;
  tools: ToolSchema[];
  model: string;
  /**
   * The instruction sections `episode/start.contract` declares, by key. They
   * name the parts of the system prompt, which the log stores rendered.
   */
  instructions: Record<string, string>;
  /** What this header changed, empty for the first header of an episode. */
  changed: string[];
}

export interface UserRow extends RowBase {
  kind: "user";
  source: string;
  from: string;
  messageId: string;
  content: ContentBlock[];
}

export interface StreamedCall {
  id: string;
  name: string;
  /** Parsed arguments, or the raw text while the arguments are still streaming. */
  args: unknown;
  raw: string;
  done: boolean;
}

export interface AssistantRow extends RowBase {
  kind: "assistant";
  requestId: string;
  step: number;
  text: string;
  thinking: string;
  toolCalls: StreamedCall[];
  stop: string;
  usage: Usage | null;
  interrupted: boolean;
  /** True while only chunks have arrived and the assembled message has not. */
  streaming: boolean;
}

export interface ToolRow extends RowBase {
  kind: "tool";
  callId: string;
  name: string;
  /**
   * The one line the tool wrote naming what it acted on and what came of
   * it. Absent for a log written before tools stated it, and for a tool
   * that states none; `docs/log-format.md` gives the field.
   */
  subject: string;
  value: unknown;
  rendered: string;
  isError: boolean;
  synthetic: boolean;
  spill: string;
  durationMs: number;
}

/** A compact single-line row for every event that is not part of the dialogue. */
export interface NoteRow extends RowBase {
  kind: "note";
  type: string;
  label: string;
  detail: string;
  /** Drives color: "error" for refusals and failures, an outcome kind for outcomes. */
  level: "info" | "error" | "completed" | "blocked" | "exhausted" | "failed";
  data: unknown;
  /** Episode id this row refers to, when clicking it should select that episode. */
  link: string | null;
}

/**
 * A `compaction/summary`: from here on the model sees the task, the
 * continuation message, and the rows from `firstKeptSeq` onward in place of
 * everything before the cut. The conversation pane places it at the cut.
 */
export interface CompactionRow extends RowBase {
  kind: "compaction";
  step: number;
  firstKeptSeq: number;
  /** Dialogue rows the summary replaced: those the compaction covered. */
  summarized: number;
  summary: string;
  /** The continuation message as the model receives it. */
  continuation: string;
  state: unknown;
}

export type Row = HeaderRow | UserRow | AssistantRow | ToolRow | NoteRow | CompactionRow;

export interface Patch {
  op: "append" | "update";
  row: Row;
}

export interface Summary {
  id: string;
  name: string;
  parentId: string | null;
  forkOrigin: { episodeId: string; seq: number } | null;
  teamId: string | null;
  /**
   * `episode/start.contract_fingerprint`: the hash over everything that shapes what the
   * model sees, which docs/design.md "Execution contracts and fingerprints" defines. Two
   * episodes of one contract under one runtime build carry the same value,
   * so it is what tells runs of one contract apart from unrelated episodes.
   * Empty until `episode/start` has been read.
   */
  contractFingerprint: string;
  task: string;
  startTime: number;
  endTime: number | null;
  outcome: Outcome | null;
  modelCalls: number;
  retries: number;
  usage: { input: number; output: number; cacheRead: number };
  budget: { modelCalls: number | null; inputTokens: number | null; outputTokens: number | null };
  sandbox: {
    mode: string;
    landlockAbi: number | null;
    processBoundary: { kind: string; subtreeCleanup: string; reason: string } | null;
  };
  children: Map<string, { contract: string; context: string }>;
  roster: Map<string, { name: string; phase: string }>;
  seedEnd: number | null;
  /**
   * `episode/start.contract`: the resolved configuration with the task
   * removed. The workflow view reads its `workflow` key and the statistics
   * view its `budget`.
   */
  contract: Record<string, unknown>;
  lastSeq: number;
  /** Marks the trajectory pane draws, in seq order (src/trajectory.ts). */
  marks: Mark[];
  /**
   * Firings of the declared graph, in the order they started, and the
   * branch and recovery decisions between them. Both are empty for an
   * episode that runs the free loop rather than a graph.
   */
  firings: NodeFiring[];
  decisions: NodeDecision[];
  /** `verification/result` events, in seq order (docs/log-format.md). */
  verifications: Verification[];
}

/** One authoritative verifier invocation, as its event records it. */
export interface Verification {
  seq: number;
  tool: string;
  /** One of accepted, findings, and failed. */
  status: string;
  /** How many finding strings the run returned. */
  findings: number;
  durationMs: number;
}

export function emptySummary(id: string): Summary {
  return {
    id,
    name: id,
    parentId: null,
    forkOrigin: null,
    teamId: null,
    contractFingerprint: "",
    task: "",
    startTime: 0,
    endTime: null,
    outcome: null,
    modelCalls: 0,
    retries: 0,
    usage: { input: 0, output: 0, cacheRead: 0 },
    budget: { modelCalls: null, inputTokens: null, outputTokens: null },
    sandbox: { mode: "", landlockAbi: null, processBoundary: null },
    children: new Map(),
    roster: new Map(),
    seedEnd: null,
    contract: {},
    lastSeq: -1,
    marks: [],
    firings: [],
    decisions: [],
    verifications: [],
  };
}

export function outcomeLabel(outcome: Outcome | null): string {
  if (!outcome) return "running";
  const o = outcome as Record<string, unknown>;
  switch (outcome.kind) {
    case "completed":
      return "completed";
    case "blocked":
      return `blocked · ${str(o.code, "?")}`;
    case "exhausted":
      return `exhausted · ${str(o.limit, "?")}`;
    case "failed":
      return `failed · ${str(o.error, "?")}`;
    default:
      return str(outcome.kind, "unknown");
  }
}

export class EpisodeFold {
  readonly events: LogEvent[] = [];
  readonly rows: Row[] = [];
  readonly summary: Summary;
  private readonly byKey = new Map<string, Row>();
  /** One line of arguments per tool call id, for the trajectory hovercard. */
  private readonly callArgs = new Map<string, string>();
  /**
   * The mark of each request still in flight, by `request_id`. A request
   * spans its `model/request` to the `assistant/message` that answers it,
   * and the events between them close the span as they are read.
   */
  private readonly requests = new Map<string, Mark>();
  /** The header in effect, against which the next one states its change. */
  private header: HeaderParts | null = null;
  private readonly stream: boolean;

  /** `stream` makes `assistant/chunk` events build a row token by token. */
  constructor(id: string, opts: { stream: boolean }) {
    this.summary = emptySummary(id);
    this.stream = opts.stream;
  }

  /** Folds one event. Events at or below the last seen seq are ignored. */
  push(ev: LogEvent): Patch[] {
    if (!Number.isFinite(ev.seq) || ev.seq <= this.summary.lastSeq) return [];
    this.summary.lastSeq = ev.seq;
    this.events.push(ev);
    const data = obj(ev.data);
    const s = this.summary;
    switch (ev.type) {
      case "episode/start":
        return this.start(ev, data);
      case "episode/end": {
        s.endTime = ev.time;
        s.outcome = obj(data.outcome) as Outcome;
        const kind = str(s.outcome.kind, "unknown");
        return [
          ...this.settleStreams(),
          ...this.note(ev, "outcome", outcomeLabel(s.outcome), levelFor(kind), data.outcome),
        ];
      }
      case "seed/end":
        s.seedEnd = ev.seq;
        return this.note(ev, "seed end", "events above were copied from the fork origin", "info", null);
      case "request/header": {
        const parts = {
          system: str(data.system),
          tools: arr(data.tools).map((t) => obj(t) as ToolSchema),
          model: modelLabel(data.model),
        };
        // A header other than the first was written because one of those
        // three parts differs from the header in effect, so the row says
        // which; `docs/log-format.md` states that rule.
        const changed = this.header ? headerDifference(this.header, parts, this.instructions()) : [];
        this.header = parts;
        return this.append({
          kind: "header",
          key: `header:${ev.seq}`,
          seq: ev.seq,
          time: ev.time,
          reason: str(data.reason, "initial"),
          instructions: this.instructions(),
          changed,
          ...parts,
        });
      }
      case "model/request": {
        s.modelCalls += 1;
        const consumed = arr(data.consumed).length;
        const detail = `step ${num(data.step)} · attempt ${num(data.attempt)} · header seq ${num(
          data.header_seq,
        )}${consumed ? ` · consumed ${consumed}` : ""}`;
        const mark = this.mark(ev, "request", `step ${num(data.step)}`, detail, 0);
        // The span opens with no length. The chunks the request produces
        // and the message that answers it give it one as they are read.
        mark.span = { endTime: ev.time, endSeq: ev.seq, firstTokenTime: null, firstTokenSeq: null };
        const requestId = str(data.request_id);
        if (requestId) this.requests.set(requestId, mark);
        return this.note(ev, "request", detail, "info", data);
      }
      case "request/retry":
        s.retries += 1;
        // The duration of a retry is the backoff it imposes, so the mark has
        // the length of the wait before the next attempt may start.
        this.mark(
          ev,
          "retry",
          str(data.cause, "?"),
          `step ${num(data.step)} · attempt ${num(data.attempt)} · backoff before the next attempt`,
          num(data.delay_ms),
        );
        return this.note(
          ev,
          "retry",
          `step ${num(data.step)} · attempt ${num(data.attempt)} · ${str(data.cause, "?")} · ${num(
            data.delay_ms,
          )} ms`,
          "info",
          data,
        );
      case "assistant/chunk":
        // The span is closed whether or not the pane assembles the chunk
        // into a row, because the trajectory draws it either way.
        this.extendRequest(ev, data);
        return this.stream ? this.chunk(ev, data) : [];
      case "assistant/message":
        this.closeRequest(ev, str(data.request_id));
        return this.message(ev, data);
      case "tool/result":
        this.mark(
          ev,
          "tool",
          str(data.name, "?"),
          this.callArgs.get(str(data.call_id)) ?? "",
          num(data.duration_ms),
        );
        return this.append({
          kind: "tool",
          key: `tool:${ev.seq}`,
          seq: ev.seq,
          time: ev.time,
          callId: str(data.call_id),
          name: str(data.name, "?"),
          subject: str(data.subject),
          value: data.value,
          rendered: str(data.rendered),
          isError: data.is_error === true,
          synthetic: data.synthetic === true,
          spill: str(data.spill),
          durationMs: num(data.duration_ms),
        });
      case "host/tool-call":
        return this.note(ev, "host call", `${str(data.name, "?")} · ${str(data.call_id)}`, "info", data.args);
      case "inbox/item":
        return this.append({
          kind: "user",
          key: `user:${ev.seq}`,
          seq: ev.seq,
          time: ev.time,
          source: str(data.source, "?"),
          from: str(data.from),
          messageId: str(data.message_id),
          content: arr(data.content).map((b) => obj(b) as ContentBlock),
        });
      case "budget/reserve": {
        const r = obj(data.reserved);
        return this.note(
          ev,
          "reserve",
          `${str(data.child_id)} · ${num(r.model_calls)} calls · ${num(r.input_tokens)} input · ${num(r.output_tokens)} output`,
          "info",
          data,
          str(data.child_id),
        );
      }
      case "budget/release": {
        const r = obj(data.spent);
        return this.note(
          ev,
          "release",
          `${str(data.child_id)} · spent ${num(r.model_calls)} calls · ${num(r.input_tokens)} input · ${num(r.output_tokens)} output`,
          "info",
          data,
          str(data.child_id),
        );
      }
      case "spawn/start": {
        const child = str(data.child_id);
        s.children.set(child, { contract: str(data.contract), context: str(data.context) });
        this.mark(ev, "spawn", child, `${str(data.contract, "?")} · ${str(data.context, "?")}`, 0);
        return this.note(
          ev,
          "spawn",
          `${child} · ${str(data.contract, "?")} · ${str(data.context, "?")} · ${str(data.call_id)}`,
          "info",
          data,
          child,
        );
      }
      case "spawn/end": {
        const outcome = obj(data.outcome) as Outcome;
        return this.note(
          ev,
          "spawn end",
          `${str(data.child_id)} · ${outcomeLabel(outcome)}`,
          levelFor(str(outcome.kind)),
          data,
          str(data.child_id),
        );
      }
      case "team/roster": {
        const member = str(data.member_id);
        s.roster.set(member, { name: str(data.name), phase: str(data.phase) });
        return this.note(
          ev,
          "roster",
          `${str(data.name, "?")} (${member}) · ${str(data.phase, "?")}`,
          str(data.phase) === "failed" ? "error" : "info",
          data,
          member,
        );
      }
      case "team/message":
        return this.note(
          ev,
          "message",
          `${str(data.message_id)} · ${str(data.from, "?")} → ${str(data.to, "?")}`,
          "info",
          data,
        );
      case "team/delivered":
        return this.note(ev, "delivered", `${str(data.message_id)} → ${str(data.to, "?")}`, "info", data);
      case "sandbox/denied":
        return this.note(
          ev,
          "denied",
          `${str(data.comm, "?")} (pid ${num(data.pid)}) · ${str(data.access, "?")} ${str(data.path, "?")}`,
          "error",
          data,
        );
      case "compaction/start": {
        const covered = obj(data.covered);
        const detail = `step ${num(data.step)} · projected ${num(data.projected_tokens)} tokens · covering seq ${num(
          covered.first_seq,
        )}–${num(covered.last_seq)}`;
        this.mark(ev, "compaction", str(data.trigger, "threshold"), detail, 0);
        return this.note(ev, "compaction", detail, "info", data);
      }
      case "compaction/summary": {
        const covered = obj(obj(data.state).covered);
        const first = num(covered.first_seq);
        const last = num(covered.last_seq);
        const dialogue = (r: Row) => r.kind === "user" || r.kind === "assistant" || r.kind === "tool";
        return this.append({
          kind: "compaction",
          key: `compaction:${ev.seq}`,
          seq: ev.seq,
          time: ev.time,
          step: num(data.step),
          firstKeptSeq: num(data.first_kept_seq),
          summarized: this.rows.filter((r) => dialogue(r) && r.seq >= first && r.seq <= last).length,
          summary: str(data.summary),
          continuation: renderContinuation(data),
          state: data.state,
        });
      }
      case "compaction/end": {
        const usage = obj(data.usage);
        const detail = data.ok === true
          ? `step ${num(data.step)} · ${fmtInt(num(usage.input))} in / ${fmtInt(num(usage.output))} out · next request about ${fmtInt(
              num(data.active_estimate),
            )} tokens`
          : `step ${num(data.step)} · failed: ${str(data.error, "?")} · context unchanged`;
        return this.note(ev, "compaction end", detail, data.ok === true ? "info" : "error", data);
      }
      case "verification/result": {
        const status = str(data.status, "?");
        const findings = arr(data.findings).length;
        const ms = num(data.duration_ms);
        s.verifications.push({ seq: ev.seq, tool: str(data.tool, "?"), status, findings, durationMs: ms });
        const detail = `${str(data.tool, "?")} · ${status} · ${findings} finding${findings === 1 ? "" : "s"} · ${fmtInt(
          ms,
        )} ms`;
        return this.note(ev, "verify", detail, status === "failed" ? "error" : "info", data);
      }
      case "workflow/node-start":
      case "workflow/node-end":
      case "workflow/branch":
      case "workflow/recovery":
        this.graph(ev, data);
        return this.note(ev, ev.type, summarize(data), "info", data);
      default:
        // Reserved types (team/task) and any type this bundle does not know
        // render as a generic row.
        return this.note(ev, ev.type, summarize(data), "info", data);
    }
  }

  private start(ev: LogEvent, data: Record<string, unknown>): Patch[] {
    const s = this.summary;
    const contract = obj(data.contract);
    const budget = obj(contract.budget);
    const sandbox = obj(data.sandbox);
    const processBoundary = obj(sandbox.process_boundary);
    const origin = obj(data.fork_origin) as ForkOrigin;
    if (typeof data.id === "string") s.id = data.id;
    s.contract = contract;
    s.name = str(contract.name, s.id);
    s.parentId = typeof data.parent_id === "string" ? data.parent_id : null;
    s.forkOrigin =
      typeof origin.episode_id === "string" ? { episodeId: origin.episode_id, seq: num(origin.seq) } : null;
    s.teamId = typeof data.team_id === "string" ? data.team_id : null;
    s.contractFingerprint = str(data.contract_fingerprint);
    s.task = str(data.task);
    s.startTime = ev.time;
    s.budget = {
      modelCalls: typeof budget.model_calls === "number" ? budget.model_calls : null,
      inputTokens: typeof budget.input_tokens === "number" ? budget.input_tokens : null,
      outputTokens: typeof budget.output_tokens === "number" ? budget.output_tokens : null,
    };
    s.sandbox = {
      mode: str(sandbox.mode),
      landlockAbi: typeof sandbox.landlock_abi === "number" ? sandbox.landlock_abi : null,
      processBoundary:
        Object.keys(processBoundary).length === 0
          ? null
          : {
              kind: str(processBoundary.kind),
              subtreeCleanup: str(processBoundary.subtree_cleanup),
              reason: str(processBoundary.reason),
            },
    };
    const parts = [s.name, s.id];
    if (s.forkOrigin) parts.push(`fork of ${s.forkOrigin.episodeId} at seq ${s.forkOrigin.seq}`);
    else if (s.parentId) parts.push(`spawned by ${s.parentId}`);
    if (s.teamId) parts.push(`team ${s.teamId}`);
    return this.note(ev, "episode", parts.join(" · "), "info", data);
  }

  private chunk(ev: LogEvent, data: Record<string, unknown>): Patch[] {
    const requestId = str(data.request_id);
    const key = `assistant:${requestId || ev.seq}`;
    let row = this.byKey.get(key) as AssistantRow | undefined;
    const fresh = !row;
    if (!row) {
      row = {
        kind: "assistant",
        key,
        seq: ev.seq,
        time: ev.time,
        requestId,
        step: num(data.step),
        text: "",
        thinking: "",
        toolCalls: [],
        stop: "",
        usage: null,
        interrupted: false,
        streaming: true,
      };
    }
    const chunk = obj(data.chunk);
    const id = str(chunk.id);
    switch (str(chunk.kind)) {
      case "text":
        row.text += str(chunk.delta);
        break;
      case "thinking":
        row.thinking += str(chunk.delta);
        break;
      case "tool_call_start":
        row.toolCalls.push({ id, name: str(chunk.name, "?"), args: "", raw: "", done: false });
        break;
      case "tool_call_delta": {
        const call = row.toolCalls.find((c) => c.id === id);
        if (call) {
          call.raw += str(chunk.delta);
          call.args = call.raw;
        }
        break;
      }
      case "tool_call_end": {
        const call = row.toolCalls.find((c) => c.id === id);
        if (call) {
          call.done = true;
          call.args = parseLenient(call.raw);
        }
        break;
      }
      default:
        break;
    }
    return fresh ? this.append(row) : this.update(row);
  }

  private message(ev: LogEvent, data: Record<string, unknown>): Patch[] {
    const s = this.summary;
    const usage = obj(data.usage) as Usage;
    s.usage.input += num(usage.input);
    s.usage.output += num(usage.output);
    s.usage.cacheRead += num(usage.cache_read);
    const requestId = str(data.request_id);
    const key = `assistant:${requestId || ev.seq}`;
    const existing = this.byKey.get(key) as AssistantRow | undefined;
    const row: AssistantRow = {
      kind: "assistant",
      key,
      seq: existing ? existing.seq : ev.seq,
      time: existing ? existing.time : ev.time,
      requestId,
      step: num(data.step),
      text: str(data.text),
      thinking: existing ? existing.thinking : "",
      toolCalls: arr(data.tool_calls).map((c) => {
        const call = obj(c);
        const id = str(call.id);
        if (id) this.callArgs.set(id, argumentLine(call.args));
        return { id, name: str(call.name, "?"), args: call.args, raw: "", done: true };
      }),
      stop: str(data.stop),
      usage: data.usage === undefined ? null : usage,
      interrupted: data.interrupted === true,
      streaming: false,
    };
    return existing ? this.update(row) : this.append(row);
  }

  private note(
    ev: LogEvent,
    label: string,
    detail: string,
    level: NoteRow["level"],
    data: unknown,
    link: string | null = null,
  ): Patch[] {
    return this.append({
      kind: "note",
      key: `note:${ev.seq}`,
      seq: ev.seq,
      time: ev.time,
      type: ev.type,
      label,
      detail,
      level,
      data,
      link: link || null,
    });
  }

  /**
   * Closes every assistant row still assembling from chunks. A row is
   * assembling until its `assistant/message` arrives, so a row still
   * assembling when the episode ends is a stream that was cut off: the
   * request failed before the response was assembled. It is recorded as
   * interrupted, because that is what the log shows.
   */
  private settleStreams(): Patch[] {
    const patches: Patch[] = [];
    for (const row of this.rows) {
      if (row.kind !== "assistant" || !row.streaming) continue;
      row.streaming = false;
      row.interrupted = true;
      patches.push({ op: "update", row });
    }
    return patches;
  }

  /**
   * The instruction sections the contract declares, by key. `episode/start`
   * precedes every `request/header`, so the map is complete by the time a
   * header needs it; a log that carries no contract yields an empty map and
   * the prompt is then shown unsplit.
   */
  private instructions(): Record<string, string> {
    const declared = obj(this.summary.contract.instructions);
    const out: Record<string, string> = {};
    for (const [key, value] of Object.entries(declared)) {
      if (typeof value === "string") out[key] = value;
    }
    return out;
  }

  /**
   * Records one event of a declared graph on the summary the trajectory
   * draws. A `workflow/node-start` opens a firing, a `workflow/node-end`
   * closes the open firing of the same node and fire count, and the two
   * decision events mark where the run departed from the straight path
   * through the graph. docs/log-format.md specifies all four.
   */
  private graph(ev: LogEvent, data: Record<string, unknown>): void {
    const s = this.summary;
    const node = str(data.node, "?");
    const fire = num(data.fire, 1);
    if (ev.type === "workflow/node-start") {
      s.firings.push({
        node,
        fire,
        startSeq: ev.seq,
        startTime: ev.time,
        endSeq: null,
        endTime: null,
        durationMs: null,
        error: "",
        childId: typeof data.child_id === "string" ? data.child_id : null,
        inputs: arr(data.inputs).map((x) => num(x)),
      });
      return;
    }
    if (ev.type === "workflow/node-end") {
      for (let i = s.firings.length - 1; i >= 0; i -= 1) {
        const firing = s.firings[i]!;
        if (firing.node !== node || firing.fire !== fire || firing.endSeq !== null) continue;
        firing.endSeq = ev.seq;
        firing.endTime = ev.time;
        firing.durationMs = typeof data.duration_ms === "number" ? data.duration_ms : null;
        firing.error = str(data.error);
        return;
      }
      return;
    }
    if (ev.type === "workflow/branch") {
      const label = str(data.label, "?");
      const successors = arr(data.successors).map((x) => str(x)).filter((x) => x !== "");
      s.decisions.push({
        kind: "branch",
        node,
        fire,
        seq: ev.seq,
        time: ev.time,
        label,
        detail: successors.length > 0 ? `${label} leads to ${successors.join(", ")}` : `${label} ends the graph`,
      });
      return;
    }
    const action = str(data.action, "?");
    const target = typeof data.target === "string" ? data.target : "";
    s.decisions.push({
      kind: "recovery",
      node,
      fire,
      seq: ev.seq,
      time: ev.time,
      label: action,
      detail: `${str(data.cause, "?")} on firing ${fire}${target === "" ? "" : ` · re-fires ${target}`}`,
    });
  }

  /** Records one trajectory mark. Marks arrive in seq order and stay so. */
  private mark(ev: LogEvent, kind: Mark["kind"], label: string, detail: string, durationMs: number): Mark {
    const mark: Mark = { kind, seq: ev.seq, time: ev.time, durationMs, span: null, label, detail };
    this.summary.marks.push(mark);
    return mark;
  }

  /**
   * Extends the span of the request a chunk belongs to, and records where
   * its first token arrived. A chunk carrying an error produced no token,
   * so it moves the end of the span and leaves the first token unset. The
   * end moves with every chunk, so a request still streaming shows the
   * length it has reached rather than none.
   */
  private extendRequest(ev: LogEvent, data: Record<string, unknown>): void {
    const mark = this.requests.get(str(data.request_id));
    if (!mark || !mark.span) return;
    mark.span.endTime = ev.time;
    mark.span.endSeq = ev.seq;
    mark.durationMs = ev.time - mark.time;
    if (mark.span.firstTokenTime !== null) return;
    if (str(obj(data.chunk).kind) === "error") return;
    mark.span.firstTokenTime = ev.time;
    mark.span.firstTokenSeq = ev.seq;
  }

  /** Closes the span of the request an `assistant/message` answers. */
  private closeRequest(ev: LogEvent, requestId: string): void {
    const mark = this.requests.get(requestId);
    if (!mark || !mark.span) return;
    mark.span.endTime = ev.time;
    mark.span.endSeq = ev.seq;
    mark.durationMs = ev.time - mark.time;
    this.requests.delete(requestId);
  }

  private append(row: Row): Patch[] {
    this.rows.push(row);
    this.byKey.set(row.key, row);
    return [{ op: "append", row }];
  }

  private update(row: Row): Patch[] {
    const index = this.rows.findIndex((r) => r.key === row.key);
    if (index >= 0) this.rows[index] = row;
    this.byKey.set(row.key, row);
    return [{ op: "update", row }];
  }
}

/** How a completed episode's completion was established. */
export interface Provenance {
  kind: "verifier" | "reviewed" | "model-report";
  /** The verifier tool's name, empty when no verifier accepted. */
  verifier: string;
}

/**
 * Derives completion provenance from the fold, mirroring the derivation
 * documented in docs/telemetry.md "Completion provenance": `verifier` when
 * an authoritative `verification/result` accepted the completing value,
 * `reviewed` when the completing terminal node is a model node fed another
 * model node's completion value, and `model-report` otherwise. Null unless
 * the episode completed.
 */
export function completionProvenance(s: Summary): Provenance | null {
  if (s.outcome?.kind !== "completed") return null;
  const nodes = obj(obj(s.contract.workflow).nodes);
  const declared = (name: string) => obj(nodes[name]);
  const accepted = s.verifications.filter((v) => v.status === "accepted");
  if (Object.keys(nodes).length === 0) {
    const last = s.verifications[s.verifications.length - 1];
    if (last && last.status === "accepted") return { kind: "verifier", verifier: last.tool };
    return { kind: "model-report", verifier: "" };
  }
  const terminal = (name: string) => declared(name).terminal === true;
  const isModel = (name: string) => declared(name).model !== undefined && declared(name).model !== null;
  // The verifier a node's acceptance came from is configured: the node's
  // own `verify`, or its contract's `done_when.verify`.
  const verifierOf = (name: string) => str(declared(name).verify) || str(obj(obj(declared(name).model).done_when).verify);
  // The completing firing: the last errorless end of a terminal node, or
  // of any node when the graph completed through a branch with no
  // successors and flags no terminal.
  let completing: NodeFiring | null = null;
  let lastEnd: NodeFiring | null = null;
  for (const f of s.firings) {
    if (f.endSeq === null || f.error !== "") continue;
    lastEnd = f;
    if (terminal(f.node)) completing = f;
  }
  const end = completing ?? lastEnd;
  if (end === null) return { kind: "model-report", verifier: "" };
  const judged =
    accepted.find((v) => v.seq > (end.endSeq ?? -1)) ??
    accepted.find((v) => v.seq > end.startSeq && v.seq < (end.endSeq ?? -1));
  if (judged) return { kind: "verifier", verifier: judged.tool };
  const fedByModel = end.inputs.some((seq) =>
    s.firings.some((f) => f.endSeq === seq && f.error === "" && isModel(f.node)),
  );
  if (isModel(end.node) && fedByModel) return { kind: "reviewed", verifier: "" };
  return { kind: "model-report", verifier: "" };
}

/** The faint text set after a completed outcome word. */
export function provenanceText(p: Provenance): string {
  if (p.kind === "verifier") return p.verifier === "" ? "verified" : `verified by ${p.verifier}`;
  return p.kind === "reviewed" ? "independently reviewed" : "model report";
}

function levelFor(kind: string): NoteRow["level"] {
  switch (kind) {
    case "completed":
    case "blocked":
    case "exhausted":
    case "failed":
      return kind;
    default:
      return "info";
  }
}

function modelLabel(model: unknown): string {
  const m = obj(model);
  const provider = str(m.provider);
  const name = str(m.model);
  return provider && name ? `${provider}/${name}` : name || provider;
}

/**
 * One line naming a tool call's arguments, for a hovercard that has room
 * for one line. A long value is cut so that the line stays scannable.
 */
export function argumentLine(args: unknown, limit = 90): string {
  const text = typeof args === "string" ? args : compactJson(args);
  const line = text.replace(/\s+/g, " ").trim();
  return line.length <= limit ? line : `${line.slice(0, limit - 1)}…`;
}

function compactJson(value: unknown): string {
  try {
    return JSON.stringify(value) ?? "";
  } catch {
    return String(value);
  }
}

/** Streamed arguments may be cut short; a prefix that fails to parse stays text. */
function parseLenient(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return raw;
  }
}

/** One line naming the payload's top-level keys, for rows of unknown type. */
function summarize(data: Record<string, unknown>): string {
  const keys = Object.keys(data);
  if (keys.length === 0) return "";
  return keys
    .slice(0, 6)
    .map((k) => {
      const v = data[k];
      return typeof v === "string" || typeof v === "number" || typeof v === "boolean" ? `${k}=${String(v)}` : k;
    })
    .join(" · ");
}
