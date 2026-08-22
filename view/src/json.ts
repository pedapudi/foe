// Reading a JSON value for display: what kind it is, what a collapsed node
// says about what it holds, and whether it is small enough to stand open
// before a reader asks. The raw events tab draws payloads with these rules.
//
// Nothing here touches the DOM, so every rule a reader meets on the page is
// checked by a unit test rather than by looking at the page.

/** The six kinds a JSON value takes. A value of no other kind occurs. */
export type JsonKind = "object" | "array" | "string" | "number" | "boolean" | "null";

export function jsonKind(value: unknown): JsonKind {
  if (value === null || value === undefined) return "null";
  if (Array.isArray(value)) return "array";
  const type = typeof value;
  if (type === "string") return "string";
  if (type === "number" || type === "bigint") return "number";
  if (type === "boolean") return "boolean";
  return "object";
}

/** A string of at most this many characters, holding no line break, sets inline. */
export const INLINE_STRING_CHARS = 140;

/** Characters of the first line a collapsed string shows before its count. */
export const STRING_HEAD_CHARS = 96;

/** Keys a collapsed object names before it stops naming them. */
export const PREVIEW_KEYS = 4;

/** Lines a node may take open before it opens only when a reader asks. */
export const OPEN_LINES = 12;

/** Depth past which a node stays collapsed however little it holds. */
export const OPEN_DEPTH = 2;

/** One member of an object or an array, under the label the parent gives it. */
export interface JsonChild {
  readonly label: string;
  readonly value: unknown;
}

/**
 * The members of an object or an array, in the order they are stored. An
 * object keeps the order the log wrote its keys in, because that order is
 * what the runtime chose; nothing sorts them.
 */
export function children(value: unknown): JsonChild[] {
  if (Array.isArray(value)) return value.map((item, i) => ({ label: String(i), value: item }));
  if (jsonKind(value) !== "object") return [];
  const record = value as Record<string, unknown>;
  return Object.keys(record).map((label) => ({ label, value: record[label] }));
}

/** True when a string sets on the line its key is on. */
export function isInlineString(text: string): boolean {
  return text.length <= INLINE_STRING_CHARS && !text.includes("\n");
}

/** The first line of a string, cut with an ellipsis, for a collapsed summary. */
export function stringHead(text: string): string {
  const end = text.indexOf("\n");
  const line = end < 0 ? text : text.slice(0, end);
  if (line.length <= STRING_HEAD_CHARS) return end < 0 ? line : `${line}…`;
  return `${line.slice(0, STRING_HEAD_CHARS)}…`;
}

/** What a collapsed node states about its size, as a count with its noun. */
export function countLabel(value: unknown): string {
  const kind = jsonKind(value);
  const n = children(value).length;
  if (kind === "array") return n === 1 ? "1 item" : `${n} items`;
  if (kind === "object") return n === 1 ? "1 key" : `${n} keys`;
  return "";
}

/**
 * The key names a collapsed object shows beside its count, so that a reader
 * knows what is inside without opening it. An array shows none: its members
 * have no names, and its count already says how many there are.
 */
export function previewLabel(value: unknown): string {
  if (jsonKind(value) !== "object") return "";
  const names = children(value).map((child) => child.label);
  if (names.length === 0) return "";
  const shown = names.slice(0, PREVIEW_KEYS).join(", ");
  return names.length > PREVIEW_KEYS ? `${shown}, …` : shown;
}

/**
 * Lines the value takes when every node under it is open, counted no
 * further than `cap` so that a large payload costs the same as a small one
 * to measure. A scalar is one line; a string that sets as a block is two,
 * being its summary and its text.
 */
export function openLines(value: unknown, cap: number = OPEN_LINES + 1): number {
  const kind = jsonKind(value);
  if (kind === "string") return isInlineString(value as string) ? 1 : 2;
  if (kind !== "object" && kind !== "array") return 1;
  let total = 1;
  for (const child of children(value)) {
    total += openLines(child.value, cap);
    if (total >= cap) return cap;
  }
  return total;
}

/**
 * Whether a node stands open before a reader asks. A node opens when it is
 * near the surface and short enough that opening it costs no scrolling;
 * everything else waits, because the reader came to the tab for one field
 * and should not have to walk past the rest of the payload to reach it.
 */
export function opensByDefault(value: unknown, depth: number): boolean {
  const kind = jsonKind(value);
  if (kind !== "object" && kind !== "array") return false;
  if (children(value).length === 0) return false;
  if (depth > OPEN_DEPTH) return false;
  return openLines(value) <= OPEN_LINES;
}
