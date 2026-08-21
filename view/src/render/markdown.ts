// A Markdown parser written for this viewer. It produces a tree, and
// markup.ts builds elements from that tree, so no model text is ever
// assigned as markup and the parser can be tested without a document.
//
// The grammar covered is the part assistant messages use: ATX headings,
// paragraphs, bullet and ordered lists with nesting, fenced code, block
// quotes, pipe tables, thematic breaks, and the inline set of emphasis,
// strong emphasis, code spans, links, hard line breaks, and mathematics.
// Anything outside it stays literal text.

export type Inline =
  | { kind: "text"; text: string }
  | { kind: "emphasis"; children: Inline[] }
  | { kind: "strong"; children: Inline[] }
  | { kind: "strike"; children: Inline[] }
  | { kind: "code"; text: string }
  | { kind: "link"; href: string; children: Inline[] }
  | { kind: "math"; tex: string; display: boolean }
  | { kind: "break" };

export type Align = "left" | "center" | "right" | null;

export type Block =
  | { kind: "heading"; level: number; children: Inline[] }
  | { kind: "paragraph"; children: Inline[] }
  | { kind: "code"; lang: string; text: string }
  | { kind: "quote"; blocks: Block[] }
  | { kind: "list"; ordered: boolean; start: number; items: Block[][] }
  | { kind: "table"; align: Align[]; header: Inline[][]; rows: Inline[][][] }
  | { kind: "rule" }
  | { kind: "math"; tex: string };

/** True when the text carries a mathematics delimiter, so Temml is needed. */
export function hasMath(text: string): boolean {
  return /\$\$?[^$]|\\\(|\\\[/.test(text);
}

const FENCE = /^(\s{0,3})(```+|~~~+)\s*([^\s`]*)\s*$/;
const HEADING = /^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const RULE = /^ {0,3}([-*_])\s*(?:\1\s*){2,}$/;
const BULLET = /^(\s*)([-*+])\s+(.*)$/;
const ORDERED = /^(\s*)(\d{1,9})[.)]\s+(.*)$/;
const QUOTE = /^ {0,3}> ?(.*)$/;
const DISPLAY_MATH = /^(?:\$\$([\s\S]*?)\$\$|\\\[([\s\S]*?)\\\])$/;

export function parseMarkdown(text: string): Block[] {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  // A text ending in a newline splits with a trailing empty element, which
  // would otherwise become a line of an unterminated fenced block.
  if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
  return parseBlocks(lines);
}

function parseBlocks(lines: string[]): Block[] {
  const out: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i]!;
    if (line.trim() === "") {
      i++;
      continue;
    }

    const fence = FENCE.exec(line);
    if (fence) {
      const marker = fence[2]!;
      const body: string[] = [];
      i++;
      while (i < lines.length && !new RegExp(`^\\s{0,3}${marker[0]}{${marker.length},}\\s*$`).test(lines[i]!)) {
        body.push(lines[i]!);
        i++;
      }
      if (i < lines.length) i++;
      out.push({ kind: "code", lang: fence[3] ?? "", text: body.join("\n") });
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      out.push({ kind: "heading", level: heading[1]!.length, children: parseInline(heading[2]!) });
      i++;
      continue;
    }

    if (RULE.test(line)) {
      out.push({ kind: "rule" });
      i++;
      continue;
    }

    if (QUOTE.test(line)) {
      const body: string[] = [];
      while (i < lines.length) {
        const q = QUOTE.exec(lines[i]!);
        if (q) body.push(q[1]!);
        else if (lines[i]!.trim() === "") break;
        else body.push(lines[i]!);
        i++;
      }
      out.push({ kind: "quote", blocks: parseBlocks(body) });
      continue;
    }

    const table = readTable(lines, i);
    if (table) {
      out.push(table.block);
      i = table.next;
      continue;
    }

    if (BULLET.test(line) || ORDERED.test(line)) {
      const list = readList(lines, i);
      out.push(list.block);
      i = list.next;
      continue;
    }

    // A paragraph runs to the next blank line or to the first line that
    // opens a block of another kind.
    const body: string[] = [];
    while (i < lines.length && lines[i]!.trim() !== "" && !opensBlock(lines[i]!)) {
      body.push(lines[i]!);
      i++;
    }
    if (body.length === 0) {
      body.push(lines[i]!);
      i++;
    }
    const joined = body.join("\n").trim();
    const math = DISPLAY_MATH.exec(joined);
    if (math) out.push({ kind: "math", tex: (math[1] ?? math[2] ?? "").trim() });
    else out.push({ kind: "paragraph", children: parseInline(joined) });
  }
  return out;
}

function opensBlock(line: string): boolean {
  return (
    FENCE.test(line) ||
    HEADING.test(line) ||
    RULE.test(line) ||
    QUOTE.test(line) ||
    BULLET.test(line) ||
    ORDERED.test(line)
  );
}

function readList(lines: string[], start: number): { block: Block; next: number } {
  const first = BULLET.exec(lines[start]!) ?? ORDERED.exec(lines[start]!)!;
  const ordered = BULLET.exec(lines[start]!) === null;
  const indent = first[1]!.length;
  const items: Block[][] = [];
  let i = start;
  let current: string[] | null = null;
  const flush = () => {
    if (current) items.push(parseBlocks(current));
    current = null;
  };
  while (i < lines.length) {
    const line = lines[i]!;
    if (line.trim() === "") {
      // A blank line ends the list unless the next line continues an item.
      const next = lines[i + 1];
      if (next === undefined || next.trim() === "") break;
      const indented = /^(\s*)/.exec(next)![1]!.length > indent;
      if (!indented && !BULLET.test(next) && !ORDERED.test(next)) break;
      current?.push("");
      i++;
      continue;
    }
    const marker = BULLET.exec(line) ?? ORDERED.exec(line);
    const lead = /^(\s*)/.exec(line)![1]!.length;
    if (marker && marker[1]!.length === indent) {
      flush();
      current = [marker[3]!];
      i++;
      continue;
    }
    if (lead > indent) {
      current?.push(line.slice(indent + 2 <= lead ? indent + 2 : lead));
      i++;
      continue;
    }
    if (marker && marker[1]!.length < indent) break;
    if (current === null) break;
    current.push(line.trim());
    i++;
  }
  flush();
  const startNumber = ordered ? Number(first[2]) : 1;
  return { block: { kind: "list", ordered, start: startNumber, items }, next: i };
}

function readTable(lines: string[], start: number): { block: Block; next: number } | null {
  const head = lines[start]!;
  const rule = lines[start + 1];
  if (rule === undefined || !head.includes("|")) return null;
  if (!/^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/.test(rule)) return null;
  const header = splitRow(head);
  const align = splitRow(rule).map<Align>((cell) => {
    const left = cell.startsWith(":");
    const right = cell.endsWith(":");
    if (left && right) return "center";
    if (right) return "right";
    if (left) return "left";
    return null;
  });
  if (header.length !== align.length) return null;
  const rows: Inline[][][] = [];
  let i = start + 2;
  while (i < lines.length && lines[i]!.trim() !== "" && lines[i]!.includes("|")) {
    const cells = splitRow(lines[i]!);
    while (cells.length < header.length) cells.push("");
    rows.push(cells.slice(0, header.length).map(parseInline));
    i++;
  }
  return {
    block: { kind: "table", align, header: header.map(parseInline), rows },
    next: i,
  };
}

/** Splits one table row on unescaped pipes, dropping the optional outer pair. */
function splitRow(line: string): string[] {
  const cells: string[] = [];
  let cell = "";
  const body = line.trim();
  for (let i = 0; i < body.length; i++) {
    const ch = body[i]!;
    if (ch === "\\" && body[i + 1] === "|") {
      cell += "|";
      i++;
      continue;
    }
    if (ch === "|") {
      cells.push(cell.trim());
      cell = "";
      continue;
    }
    cell += ch;
  }
  cells.push(cell.trim());
  if (cells.length > 0 && cells[0] === "") cells.shift();
  if (cells.length > 0 && cells[cells.length - 1] === "") cells.pop();
  return cells;
}

// ---- inline ----

export function parseInline(text: string): Inline[] {
  const out: Inline[] = [];
  let plain = "";
  const flush = () => {
    if (plain !== "") out.push({ kind: "text", text: plain });
    plain = "";
  };
  let i = 0;
  while (i < text.length) {
    const ch = text[i]!;

    // Mathematics is read before the escape rule, because `\(` and `\[`
    // open an expression whenever the matching close follows and are an
    // escaped bracket otherwise.
    const math = readMath(text, i);
    if (math) {
      flush();
      out.push(math.node);
      i = math.next;
      continue;
    }

    if (ch === "\\" && i + 1 < text.length && /[\\`*_{}[\]()#+\-.!|~$]/.test(text[i + 1]!)) {
      plain += text[i + 1];
      i += 2;
      continue;
    }

    if (ch === "\n") {
      // Two trailing spaces make a hard break; every other newline inside
      // a paragraph is a space.
      if (plain.endsWith("  ")) {
        plain = plain.slice(0, -2);
        flush();
        out.push({ kind: "break" });
      } else {
        plain += " ";
      }
      i++;
      continue;
    }

    if (ch === "`") {
      const run = countRun(text, i, "`");
      const close = text.indexOf("`".repeat(run), i + run);
      if (close > 0) {
        flush();
        out.push({ kind: "code", text: text.slice(i + run, close).replace(/^ (.*) $/, "$1") });
        i = close + run;
        continue;
      }
    }

    if (ch === "[") {
      const link = readLink(text, i);
      if (link) {
        flush();
        out.push(link.node);
        i = link.next;
        continue;
      }
    }

    if (ch === "!" && text[i + 1] === "[") {
      // An image is shown as its link, because the page loads nothing
      // from the network.
      const link = readLink(text, i + 1);
      if (link) {
        flush();
        out.push(link.node);
        i = link.next;
        continue;
      }
    }

    if (ch === "~" && text[i + 1] === "~") {
      const close = text.indexOf("~~", i + 2);
      if (close > i + 2) {
        flush();
        out.push({ kind: "strike", children: parseInline(text.slice(i + 2, close)) });
        i = close + 2;
        continue;
      }
    }

    if (ch === "*" || ch === "_") {
      const span = readEmphasis(text, i, ch);
      if (span) {
        flush();
        out.push(span.node);
        i = span.next;
        continue;
      }
    }

    plain += ch;
    i++;
  }
  flush();
  return out;
}

function countRun(text: string, at: number, ch: string): number {
  let n = 0;
  while (text[at + n] === ch) n++;
  return n;
}

function readEmphasis(text: string, at: number, marker: string): { node: Inline; next: number } | null {
  const run = Math.min(countRun(text, at, marker), 2);
  const delim = marker.repeat(run);
  if (text[at + run] === undefined || /\s/.test(text[at + run]!)) return null;
  let i = at + run;
  while (i < text.length) {
    if (text[i] === "\\") {
      i += 2;
      continue;
    }
    if (text.startsWith(delim, i) && !/\s/.test(text[i - 1] ?? " ")) {
      // A three-or-more run closing a one-marker span belongs to the outer span.
      if (run === 1 && text[i + 1] === marker) {
        i++;
        continue;
      }
      const inner = text.slice(at + run, i);
      if (inner === "") return null;
      return {
        node: { kind: run === 2 ? "strong" : "emphasis", children: parseInline(inner) },
        next: i + run,
      };
    }
    i++;
  }
  return null;
}

function readLink(text: string, at: number): { node: Inline; next: number } | null {
  let depth = 0;
  let i = at;
  for (; i < text.length; i++) {
    if (text[i] === "\\") {
      i++;
      continue;
    }
    if (text[i] === "[") depth++;
    else if (text[i] === "]") {
      depth--;
      if (depth === 0) break;
    }
  }
  if (depth !== 0 || text[i + 1] !== "(") return null;
  const close = text.indexOf(")", i + 2);
  if (close < 0) return null;
  const label = text.slice(at + 1, i);
  const target = text.slice(i + 2, close).trim().split(/\s+/)[0] ?? "";
  return { node: { kind: "link", href: target, children: parseInline(label) }, next: close + 1 };
}

function readMath(text: string, at: number): { node: Inline; next: number } | null {
  const pairs: [string, string, boolean][] = [
    ["$$", "$$", true],
    ["\\[", "\\]", true],
    ["\\(", "\\)", false],
  ];
  for (const [open, close, display] of pairs) {
    if (!text.startsWith(open, at)) continue;
    const end = text.indexOf(close, at + open.length);
    if (end < 0) continue;
    return {
      node: { kind: "math", tex: text.slice(at + open.length, end).trim(), display },
      next: end + close.length,
    };
  }
  if (text[at] === "$" && text[at + 1] !== "$") {
    // A single dollar opens mathematics only when a closing dollar follows
    // on the same line with no space beside either delimiter, so that a
    // price or a shell variable stays literal text.
    if (/\s/.test(text[at + 1] ?? " ")) return null;
    const line = text.indexOf("\n", at);
    const limit = line < 0 ? text.length : line;
    for (let i = at + 1; i < limit; i++) {
      if (text[i] === "\\") {
        i++;
        continue;
      }
      if (text[i] === "$" && !/\s/.test(text[i - 1] ?? " ")) {
        return { node: { kind: "math", tex: text.slice(at + 1, i), display: false }, next: i + 1 };
      }
    }
  }
  return null;
}
