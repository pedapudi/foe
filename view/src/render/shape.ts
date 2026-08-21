// What kind of text a tool result holds, decided from the text alone, and
// the colouring language a file path implies. Both are pure so that the
// conversation pane can choose a renderer before building any element.

import { looksLikeUnifiedDiff } from "./unified-diff.js";

export type ResultShape = "json" | "diff" | "source" | "text";

/** Lines the `read` tool numbers, as a decimal count then a tab. */
const NUMBERED = /^\s*\d+\t/;

/**
 * The shape of a tool result's rendered text. A result that carries diff
 * hunks, parses as JSON, or numbers every line gets the matching
 * treatment; everything else stays preformatted.
 */
export function resultShape(rendered: string): ResultShape {
  const trimmed = rendered.trim();
  if (trimmed === "") return "text";
  if (looksLikeUnifiedDiff(rendered)) return "diff";
  if ((trimmed.startsWith("{") || trimmed.startsWith("[")) && parsesAsJson(trimmed)) return "json";
  const lines = rendered.split("\n").filter((line) => line !== "");
  // The last line of a truncated read carries a note rather than a number,
  // so one unnumbered line still counts as numbered source.
  if (lines.length > 0 && lines.filter((line) => NUMBERED.test(line)).length >= Math.max(1, lines.length - 1)) {
    return "source";
  }
  return "text";
}

function parsesAsJson(text: string): boolean {
  try {
    JSON.parse(text);
    return true;
  } catch {
    return false;
  }
}

const EXTENSIONS = new Map<string, string>([
  ["rs", "rust"], ["py", "python"], ["ts", "typescript"], ["tsx", "typescript"],
  ["js", "javascript"], ["mjs", "javascript"], ["jsx", "javascript"], ["json", "json"],
  ["sh", "shell"], ["bash", "shell"], ["go", "go"], ["c", "c"], ["h", "c"], ["cc", "cpp"],
  ["cpp", "cpp"], ["hpp", "cpp"], ["java", "java"], ["cs", "cs"], ["toml", "toml"],
  ["yaml", "yaml"], ["yml", "yaml"], ["md", "markdown"],
]);

/** The colouring language for a path, empty when the extension is unknown. */
export function languageForPath(path: string): string {
  const dot = path.lastIndexOf(".");
  if (dot < 0) return "";
  return EXTENSIONS.get(path.slice(dot + 1).toLowerCase()) ?? "";
}
