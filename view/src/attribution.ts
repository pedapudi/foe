// Where the input tokens of every model request came from. The module holds
// no element and reads no document, so the arithmetic is tested directly and
// render/attribution.ts draws what this returns.
//
// One request carries five kinds of text: the system prompt and the tool
// schemas from the `request/header` it points at, and the inbox items,
// assistant turns, and tool results its `messages` list holds. A
// summarization call carries a sixth, its own prompt. Nothing in the log
// states how many tokens each of those cost, so this module measures each
// one in characters and apportions the input count the provider reported
// across them in proportion to those characters.
//
// The consequence is stated wherever a figure shows one of these numbers.
// A request's character counts and its reported input are measurements. The
// tokens attributed to one part of that request are derived, and they are
// exact only to the degree that every part of a request encodes at the same
// characters per token, which no tokenizer guarantees. The parts of one
// request always sum to that request's reported input, because the
// apportionment divides that measurement rather than estimating it.
//
// A request whose answer reported no usage has characters and no tokens. A
// total that spans such a request is therefore a lower bound, and it is
// marked as one rather than reported as if the unanswered requests cost
// nothing.

import { arr, num, obj, str } from "./types.js";
import type { StatisticsEpisode } from "./statistics.js";

/** Where one piece of a request's input came from. */
export type PartKind = "system" | "schemas" | "inbox" | "assistant" | "tool" | "summary";

/** What each kind is called where a figure groups by it. */
export const KIND_NAMES: Record<PartKind, string> = {
  system: "system prompt",
  schemas: "tool schemas",
  inbox: "task and inbox items",
  assistant: "assistant turns",
  tool: "tool results",
  summary: "summarization prompts",
};

/** The kinds in the order a request carries them, which the legend keeps. */
export const KIND_ORDER: PartKind[] = ["system", "schemas", "inbox", "assistant", "tool", "summary"];

/**
 * One piece of text, identified so that the requests carrying the same text
 * name one part. A tool result is identified by its call id, which the log
 * gives it; everything else by its text, so that two turns of one wording
 * are one part and a rewritten system prompt is a different one.
 */
export interface Part {
  key: string;
  kind: PartKind;
  /** What to call it, taken from the first request that carried it. */
  label: string;
  episodeId: string;
  /** `seq` of the first `model/request` that carried it. */
  seq: number;
  /** Characters of the text the log records for it. Measured. */
  chars: number;
}

/** One part as one request carried it. */
export interface PartShare {
  part: Part;
  /**
   * True when an earlier request of the same episode, whose answer reported
   * an input count, already carried this text.
   */
  replayed: boolean;
  /**
   * Input tokens apportioned to this part, absent when the request's answer
   * reported no input count.
   */
  tokens: number | null;
}

/** One model request and the parts its input was made of. */
export interface RequestInput {
  episodeId: string;
  requestId: string;
  step: number;
  attempt: number;
  requestSeq: number;
  /** True for a compaction's own summarization call. */
  compaction: boolean;
  /** The parts in the order the request carried them. */
  shares: PartShare[];
  /** Characters over every part. Measured. */
  chars: number;
  /** `usage.input` of the answer, absent when no answer reported one. */
  input: number | null;
  /** `usage.cache_read` of the answer, absent when no answer reported one. */
  cacheRead: number | null;
  /**
   * Characters of this request divided by the input tokens it was billed,
   * which is the rate the apportionment uses. Absent with the input.
   */
  charsPerToken: number | null;
}

/** One part over every request that carried it. */
export interface PartTotal {
  part: Part;
  /** Times this text was sent, which is how often it was carried. */
  sends: number;
  /** Of those, the sends whose request reported an input count. */
  measuredSends: number;
  /**
   * Input tokens this part accounted for over every request that carried it
   * and reported usage, which is its replay cost. Absent when no such
   * request reported one.
   */
  tokens: number | null;
  /** Characters sent for this part, which is its size times its sends. */
  charCost: number;
  /** True when a request carried it and reported no usage, so `tokens` is a floor. */
  bounded: boolean;
}

export interface Attribution {
  requests: RequestInput[];
  /** Every part, by replay cost, largest first. */
  parts: PartTotal[];
  /** Total `usage.input` over the requests that reported one. */
  input: number | null;
  /** Total `usage.cache_read`, reported beside the input and never inside it. */
  cacheRead: number | null;
  /** Input tokens carrying text no earlier request had sent. */
  unique: number | null;
  /** Input tokens carrying text an earlier request had already sent. */
  replayed: number | null;
  /**
   * Requests whose unique and replayed shares had to be apportioned because
   * the request before them carried text they do not. Zero means the whole
   * split is the difference of counts the provider reported.
   */
  originDerived: number;
  /** True when a request reported no usage, so every token total is a floor. */
  bounded: boolean;
  /** Requests whose answer reported no input count. */
  unmeasured: number;
  /** Characters over every request. Measured. */
  chars: number;
}

/** Separates the fields of a part key so that no two fields can run together. */
const SEP = "\u0000";

/** The two texts of one `request/header`, as the log records them. */
interface HeaderText {
  system: string;
  /** Every tool's name, description, and parameter schema, concatenated. */
  schemas: string;
}

function headerText(data: Record<string, unknown>): HeaderText {
  const schemas = arr(data.tools)
    .map(obj)
    .map((tool) => str(tool.name) + str(tool.description) + JSON.stringify(tool.parameters ?? null))
    .join("");
  return { system: str(data.system), schemas };
}

/** One part of one request before it is matched against the parts already seen. */
interface Piece {
  kind: PartKind;
  /** What distinguishes this text from any other of its kind. */
  id: string;
  label: string;
  chars: number;
}

/** The pieces one `model/request` carried, in the order it carried them. */
function piecesOf(data: Record<string, unknown>, header: HeaderText, compaction: boolean): Piece[] {
  const pieces: Piece[] = [];
  if (header.system !== "") {
    pieces.push({ kind: "system", id: header.system, label: "system prompt", chars: header.system.length });
  }
  if (header.schemas !== "") {
    pieces.push({ kind: "schemas", id: header.schemas, label: "tool schemas", chars: header.schemas.length });
  }
  let inbox = 0;
  let turn = 0;
  for (const message of arr(data.messages)) {
    const m = obj(message);
    const role = str(m.role);
    if (role === "assistant") {
      turn += 1;
      const text = str(m.text) + JSON.stringify(m.tool_calls ?? []);
      pieces.push({ kind: "assistant", id: text, label: `assistant turn ${turn}`, chars: text.length });
      continue;
    }
    if (role === "tool") {
      const callId = str(m.call_id, "?");
      const rendered = str(m.rendered);
      pieces.push({ kind: "tool", id: callId, label: `${str(m.name, "?")} · ${callId}`, chars: rendered.length });
      continue;
    }
    const text = arr(m.content)
      .map(obj)
      .map((block) => str(block.text))
      .join("");
    inbox += 1;
    // A summarization call's one user message is the transcript it was
    // asked to summarize, which is a prompt of its own rather than the
    // task or an item the episode received.
    if (compaction) {
      pieces.push({ kind: "summary", id: text, label: "summarization prompt", chars: text.length });
    } else {
      pieces.push({ kind: "inbox", id: text, label: inbox === 1 ? "task" : `inbox item ${inbox}`, chars: text.length });
    }
  }
  return pieces;
}

/** The input count and cache read each request's answer reported, by request id. */
function usageOf(episode: StatisticsEpisode): Map<string, { input: number | null; cacheRead: number | null }> {
  const out = new Map<string, { input: number | null; cacheRead: number | null }>();
  for (const event of episode.events) {
    if (event.type !== "assistant/message") continue;
    const data = obj(event.data);
    const usage = obj(data.usage);
    out.set(str(data.request_id), {
      input: typeof usage.input === "number" ? usage.input : null,
      cacheRead: typeof usage.cache_read === "number" ? usage.cache_read : null,
    });
  }
  return out;
}

/**
 * The input tokens of one request that carried text no earlier request had
 * sent.
 *
 * Where the request before it carried nothing this one lacks, the answer is
 * a measurement rather than an apportionment: the two requests differ by
 * the text new to the later one, so the difference between the two counts
 * the provider reported is what that text cost. The first request of an
 * episode is the same measurement with nothing before it, since all of its
 * text is new.
 *
 * A request that dropped text the one before it carried, which a compaction
 * does, has no such predecessor. Its shares are apportioned by characters
 * instead and `derived` is called, so that a figure can say how much of the
 * split was measured.
 */
function uniqueOf(
  shares: PartShare[],
  previous: { keys: Set<string>; input: number } | null,
  reported: number,
  derived: () => void,
): number {
  if (previous === null) return reported;
  const kept = [...previous.keys].every((key) => shares.some((share) => share.part.key === key));
  if (kept && reported >= previous.input) return reported - previous.input;
  derived();
  return shares.reduce((sum, share) => sum + (share.replayed ? 0 : (share.tokens ?? 0)), 0);
}

/**
 * Every request of the scope with the parts its input was made of, every
 * part with the input it accounted for over the whole scope, and the split
 * of that input into text sent once and text resent.
 *
 * Parts are never shared between episodes. Two episodes hold two
 * conversations, and one system prompt sent in both was sent twice.
 */
export function computeAttribution(scope: StatisticsEpisode[]): Attribution {
  const parts = new Map<string, Part>();
  const totals = new Map<string, PartTotal>();
  const requests: RequestInput[] = [];
  let input: number | null = null;
  let cacheRead: number | null = null;
  let unique: number | null = null;
  let chars = 0;
  let unmeasured = 0;
  let originDerived = 0;

  for (const episode of scope) {
    const usage = usageOf(episode);
    const headers = new Map<number, HeaderText>();
    const seen = new Set<string>();
    // The last request of this episode whose answer reported an input count,
    // against which the next such request's new text is measured.
    let previous: { keys: Set<string>; input: number } | null = null;
    // Labels are positional, so a compaction that restarts the message list
    // can produce a second part with the label of an earlier one. The step
    // that introduced the later part tells the two apart.
    const labels = new Set<string>();
    for (const event of episode.events) {
      const data = obj(event.data);
      if (event.type === "request/header") {
        headers.set(event.seq, headerText(data));
        continue;
      }
      if (event.type !== "model/request") continue;
      const requestId = str(data.request_id);
      const compaction = requestId.startsWith("cmp_");
      const header = headers.get(num(data.header_seq, -1)) ?? { system: "", schemas: "" };
      const answer = usage.get(requestId);
      const reported = answer?.input ?? null;
      const shares: PartShare[] = [];
      let requestChars = 0;
      for (const piece of piecesOf(data, header, compaction)) {
        const key = episode.id + SEP + piece.kind + SEP + piece.id;
        let part = parts.get(key);
        if (part === undefined) {
          const label = labels.has(piece.label) ? `${piece.label} · step ${num(data.step)}` : piece.label;
          labels.add(label);
          part = { key, kind: piece.kind, label, episodeId: episode.id, seq: event.seq, chars: piece.chars };
          parts.set(key, part);
        }
        requestChars += part.chars;
        shares.push({ part, replayed: seen.has(key), tokens: null });
        // Only a request whose answer reported an input count marks its
        // text as sent. An attempt nothing measured would otherwise move
        // the tokens of its retry into the replayed share on the strength
        // of a request whose own cost is unknown.
        if (reported !== null) seen.add(key);
      }
      // The apportionment divides the reported input rather than estimating
      // it, so the shares of one request sum to that measurement exactly.
      const rate = reported === null || requestChars === 0 ? null : reported / requestChars;
      if (rate !== null) for (const share of shares) share.tokens = share.part.chars * rate;
      for (const share of shares) {
        const total = totals.get(share.part.key) ?? {
          part: share.part,
          sends: 0,
          measuredSends: 0,
          tokens: null,
          charCost: 0,
          bounded: false,
        };
        total.sends += 1;
        total.charCost += share.part.chars;
        if (share.tokens === null) total.bounded = true;
        else {
          total.measuredSends += 1;
          total.tokens = (total.tokens ?? 0) + share.tokens;
        }
        totals.set(share.part.key, total);
      }
      if (reported === null) unmeasured += 1;
      else {
        input = (input ?? 0) + reported;
        unique = (unique ?? 0) + uniqueOf(shares, previous, reported, () => (originDerived += 1));
        previous = { keys: new Set(shares.map((share) => share.part.key)), input: reported };
      }
      if (answer?.cacheRead != null) cacheRead = (cacheRead ?? 0) + answer.cacheRead;
      chars += requestChars;
      requests.push({
        episodeId: episode.id,
        requestId,
        step: num(data.step),
        attempt: num(data.attempt, 1),
        requestSeq: event.seq,
        compaction,
        shares,
        chars: requestChars,
        input: reported,
        cacheRead: answer?.cacheRead ?? null,
        charsPerToken: reported === null || reported === 0 ? null : requestChars / reported,
      });
    }
  }

  // Replayed is the remainder rather than a second sum, so that the two
  // shares always add to the measured input.
  const replayed = input === null || unique === null ? null : input - unique;
  const ranked = [...totals.values()].sort(
    (a, b) => (b.tokens ?? -1) - (a.tokens ?? -1) || b.charCost - a.charCost || a.part.label.localeCompare(b.part.label),
  );
  return {
    requests,
    parts: ranked,
    input,
    cacheRead,
    unique,
    replayed,
    originDerived,
    bounded: unmeasured > 0,
    unmeasured,
    chars,
  };
}

// ---- layout ----------------------------------------------------------------

export interface Segment {
  share: PartShare;
  x: number;
  w: number;
}

export interface RequestBar {
  request: RequestInput;
  segments: Segment[];
  /** Length of the whole bar, the request's characters against the largest. */
  w: number;
}

/**
 * One bar per request, its length the request's characters against the
 * largest request in the scope and its divisions that request's parts.
 * Characters set the length because every request has them; a request whose
 * answer reported no usage would otherwise draw nothing.
 */
export function layoutRequestInput(attribution: Attribution, width: number): RequestBar[] {
  const largest = Math.max(1, ...attribution.requests.map((r) => r.chars));
  return attribution.requests.map((request) => {
    const w = (request.chars / largest) * width;
    const segments: Segment[] = [];
    let x = 0;
    for (const share of request.shares) {
      const segment = (share.part.chars / largest) * width;
      segments.push({ share, x, w: segment });
      x += segment;
    }
    return { request, segments, w };
  });
}

/**
 * The parts by replay cost, largest first, as bars against the largest.
 * The measure is tokens where the scope reported any and characters
 * otherwise, because a scope whose answers reported no usage still knows
 * how much text it resent.
 */
export function layoutReplayCost(
  attribution: Attribution,
  width: number,
  rows: number,
): { total: PartTotal; w: number; measure: number }[] {
  const tokens = attribution.input !== null;
  const shown = attribution.parts.slice(0, rows);
  const measures = shown.map((total) => (tokens ? (total.tokens ?? 0) : total.charCost));
  const largest = Math.max(1, ...measures);
  return shown.map((total, i) => ({ total, w: (measures[i]! / largest) * width, measure: measures[i]! }));
}

/**
 * The input split into text sent for the first time and text resent, as one
 * bar. Absent when no answer in the scope reported an input count.
 */
export function layoutOrigin(
  attribution: Attribution,
  width: number,
): { name: string; tokens: number; fraction: number; x: number; w: number }[] | null {
  const { input, unique, replayed } = attribution;
  if (input === null || input === 0 || unique === null || replayed === null) return null;
  const parts: [string, number][] = [
    ["unique", unique],
    ["replayed", replayed],
  ];
  const out: { name: string; tokens: number; fraction: number; x: number; w: number }[] = [];
  let x = 0;
  for (const [name, tokens] of parts) {
    const fraction = tokens / input;
    const w = fraction * width;
    out.push({ name, tokens, fraction, x, w });
    x += w;
  }
  return out;
}

/** The scope's input grouped by where it came from, largest kind first. */
export function byKind(attribution: Attribution): { kind: PartKind; chars: number; tokens: number | null }[] {
  const chars = new Map<PartKind, number>();
  const tokens = new Map<PartKind, number | null>();
  for (const request of attribution.requests) {
    for (const share of request.shares) {
      const kind = share.part.kind;
      chars.set(kind, (chars.get(kind) ?? 0) + share.part.chars);
      if (share.tokens !== null) tokens.set(kind, (tokens.get(kind) ?? 0) + share.tokens);
    }
  }
  return KIND_ORDER.filter((kind) => chars.has(kind)).map((kind) => ({
    kind,
    chars: chars.get(kind)!,
    tokens: tokens.get(kind) ?? null,
  }));
}
