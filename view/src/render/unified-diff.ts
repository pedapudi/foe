// Reads the unified diff the `edit` tool returns. The parser is pure so
// that the line numbering it computes can be tested without a document;
// `renderUnifiedDiff` in markup.ts turns the result into elements.

export type DiffLineKind = "file" | "hunk" | "context" | "add" | "remove" | "meta";

export interface DiffLine {
  kind: DiffLineKind;
  /** The line without its leading `+`, `-`, or space marker. */
  text: string;
  /** Line number in the file before the edit, absent for an added line. */
  oldNumber: number | null;
  /** Line number in the file after the edit, absent for a removed line. */
  newNumber: number | null;
}

export interface UnifiedDiff {
  /** Lines above the first `---` marker, such as the tool's summary line. */
  preamble: string[];
  lines: DiffLine[];
  added: number;
  removed: number;
}

const HUNK = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/;

/** True when the text carries the markers of a unified diff. */
export function looksLikeUnifiedDiff(text: string): boolean {
  const lines = text.split("\n");
  let sawHunk = false;
  let sawFile = false;
  for (const line of lines) {
    if (HUNK.test(line)) sawHunk = true;
    if (line.startsWith("--- ") || line.startsWith("+++ ")) sawFile = true;
    if (sawHunk && sawFile) return true;
  }
  return sawHunk;
}

/**
 * Splits a unified diff into numbered lines. Text that carries no hunk
 * header yields null, so the caller can fall back to preformatted text.
 */
export function parseUnifiedDiff(text: string): UnifiedDiff | null {
  if (!looksLikeUnifiedDiff(text)) return null;
  const source = text.endsWith("\n") ? text.slice(0, -1) : text;
  const preamble: string[] = [];
  const lines: DiffLine[] = [];
  let started = false;
  let oldLine = 0;
  let newLine = 0;
  let added = 0;
  let removed = 0;
  for (const raw of source.split("\n")) {
    const hunk = HUNK.exec(raw);
    if (hunk) {
      started = true;
      oldLine = Number(hunk[1]);
      newLine = Number(hunk[3]);
      lines.push({ kind: "hunk", text: raw, oldNumber: null, newNumber: null });
      continue;
    }
    if (raw.startsWith("--- ") || raw.startsWith("+++ ")) {
      started = true;
      lines.push({ kind: "file", text: raw, oldNumber: null, newNumber: null });
      continue;
    }
    if (!started) {
      preamble.push(raw);
      continue;
    }
    if (raw.startsWith("+")) {
      lines.push({ kind: "add", text: raw.slice(1), oldNumber: null, newNumber: newLine++ });
      added++;
    } else if (raw.startsWith("-")) {
      lines.push({ kind: "remove", text: raw.slice(1), oldNumber: oldLine++, newNumber: null });
      removed++;
    } else if (raw.startsWith(" ")) {
      lines.push({ kind: "context", text: raw.slice(1), oldNumber: oldLine++, newNumber: newLine++ });
    } else {
      // `\ No newline at end of file` and anything else a producer adds.
      lines.push({ kind: "meta", text: raw, oldNumber: null, newNumber: null });
    }
  }
  return { preamble, lines, added, removed };
}
