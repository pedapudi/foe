// A small hand-written tokenizer for the fenced code blocks the
// conversation shows. It recognizes five roles, and a language it does not
// know yields one plain token, so unknown code still renders as code.
//
// The tokenizer is deliberately approximate: it reads one file at a time
// with no parser state beyond the current line, because its only consumer
// is a colour decision. A construct it misreads costs a wrong colour and
// never a wrong character; every token's text is preserved exactly, so
// concatenating the tokens reproduces the input byte for byte.

export type Role = "plain" | "comment" | "string" | "number" | "keyword" | "punct";

export interface Token {
  role: Role;
  text: string;
}

interface Language {
  /** Prefixes that start a comment running to the end of the line. */
  lineComment: string[];
  /** Opening and closing pairs of a comment that spans lines. */
  blockComment: [string, string][];
  /** True when a block comment may contain a nested block comment. */
  nestedBlockComment: boolean;
  /** Quote characters that open a string. */
  quotes: string[];
  /** Quotes inside which a backslash escapes the next character. */
  escaped: string[];
  /** Triple-quote runs, which span lines. */
  tripleQuotes: string[];
  keywords: Set<string>;
  /** True when a bare word followed by a colon or an equals sign is a key. */
  keysAreKeywords: boolean;
  /** True when `r"…"` and `r#"…"#` open a raw string, as in Rust. */
  rawStrings: boolean;
}

type LanguageSpec = Partial<Omit<Language, "keywords">> & { keywords?: string[] };

function language(spec: LanguageSpec): Language {
  return {
    lineComment: spec.lineComment ?? [],
    blockComment: spec.blockComment ?? [],
    nestedBlockComment: spec.nestedBlockComment ?? false,
    quotes: spec.quotes ?? [],
    escaped: spec.escaped ?? spec.quotes ?? [],
    tripleQuotes: spec.tripleQuotes ?? [],
    keywords: new Set(spec.keywords ?? []),
    keysAreKeywords: spec.keysAreKeywords ?? false,
    rawStrings: spec.rawStrings ?? false,
  };
}

const C_LIKE_COMMENTS = { lineComment: ["//"], blockComment: [["/*", "*/"] as [string, string]] };

const RUST = language({
  ...C_LIKE_COMMENTS,
  nestedBlockComment: true,
  quotes: ['"', "'"],
  rawStrings: true,
  keywords: [
    "as", "async", "await", "break", "const", "continue", "crate", "dyn", "else", "enum", "extern",
    "false", "fn", "for", "if", "impl", "in", "let", "loop", "match", "mod", "move", "mut", "pub",
    "ref", "return", "self", "Self", "static", "struct", "super", "trait", "true", "type", "unsafe",
    "use", "where", "while", "yield",
  ],
});

const PYTHON = language({
  lineComment: ["#"],
  quotes: ['"', "'"],
  tripleQuotes: ['"""', "'''"],
  keywords: [
    "and", "as", "assert", "async", "await", "break", "class", "continue", "def", "del", "elif",
    "else", "except", "False", "finally", "for", "from", "global", "if", "import", "in", "is",
    "lambda", "None", "nonlocal", "not", "or", "pass", "raise", "return", "True", "try", "while",
    "with", "yield",
  ],
});

const TYPESCRIPT = language({
  ...C_LIKE_COMMENTS,
  quotes: ['"', "'", "`"],
  keywords: [
    "abstract", "any", "as", "async", "await", "boolean", "break", "case", "catch", "class",
    "const", "continue", "declare", "default", "delete", "do", "else", "enum", "export", "extends",
    "false", "finally", "for", "from", "function", "get", "if", "implements", "import", "in",
    "instanceof", "interface", "keyof", "let", "new", "null", "number", "of", "private",
    "protected", "public", "readonly", "return", "satisfies", "set", "static", "string", "super",
    "switch", "this", "throw", "true", "try", "type", "typeof", "undefined", "unknown", "var",
    "void", "while", "yield",
  ],
});

const JSON_LANG = language({ quotes: ['"'], keywords: ["true", "false", "null"] });

const SHELL = language({
  lineComment: ["#"],
  quotes: ['"', "'", "`"],
  escaped: ['"', "`"],
  keywords: [
    "case", "do", "done", "elif", "else", "esac", "fi", "for", "function", "if", "in", "local",
    "return", "select", "then", "until", "while", "export", "readonly", "set", "unset", "shift",
    "trap", "source",
  ],
});

const GO = language({
  ...C_LIKE_COMMENTS,
  quotes: ['"', "'", "`"],
  escaped: ['"', "'"],
  keywords: [
    "break", "case", "chan", "const", "continue", "default", "defer", "else", "fallthrough", "for",
    "func", "go", "goto", "if", "import", "interface", "map", "package", "range", "return",
    "select", "struct", "switch", "type", "var", "nil", "true", "false",
  ],
});

const C_FAMILY = language({
  ...C_LIKE_COMMENTS,
  quotes: ['"', "'"],
  keywords: [
    "auto", "bool", "break", "case", "catch", "char", "class", "const", "constexpr", "continue",
    "default", "delete", "do", "double", "else", "enum", "extern", "false", "float", "for",
    "goto", "if", "inline", "int", "long", "namespace", "new", "nullptr", "operator", "private",
    "protected", "public", "return", "short", "signed", "sizeof", "static", "struct", "switch",
    "template", "this", "throw", "true", "try", "typedef", "typename", "union", "unsigned",
    "using", "virtual", "void", "volatile", "while",
  ],
});

const TOML = language({
  lineComment: ["#"],
  quotes: ['"', "'"],
  escaped: ['"'],
  tripleQuotes: ['"""', "'''"],
  keysAreKeywords: true,
  keywords: ["true", "false"],
});

const YAML = language({
  lineComment: ["#"],
  quotes: ['"', "'"],
  escaped: ['"'],
  keysAreKeywords: true,
  keywords: ["true", "false", "null", "yes", "no", "on", "off"],
});

const LANGUAGES = new Map<string, Language>([
  ["rust", RUST], ["rs", RUST],
  ["python", PYTHON], ["py", PYTHON],
  ["typescript", TYPESCRIPT], ["ts", TYPESCRIPT], ["tsx", TYPESCRIPT],
  ["javascript", TYPESCRIPT], ["js", TYPESCRIPT], ["jsx", TYPESCRIPT], ["mjs", TYPESCRIPT],
  ["json", JSON_LANG], ["jsonc", JSON_LANG],
  ["shell", SHELL], ["sh", SHELL], ["bash", SHELL], ["zsh", SHELL], ["console", SHELL],
  ["go", GO], ["golang", GO],
  ["c", C_FAMILY], ["h", C_FAMILY], ["cpp", C_FAMILY], ["c++", C_FAMILY], ["cc", C_FAMILY],
  ["hpp", C_FAMILY], ["java", C_FAMILY], ["cs", C_FAMILY], ["csharp", C_FAMILY],
  ["toml", TOML],
  ["yaml", YAML], ["yml", YAML],
]);

const MARKDOWN_LANGUAGES = new Set(["markdown", "md"]);

const PUNCTUATION = new Set([..."{}[]()<>.,;:?!=+-*/%&|^~@#$\\"]);

/** True when this bundle colours the named language rather than leaving it plain. */
export function isKnownLanguage(lang: string): boolean {
  const key = lang.trim().toLowerCase();
  return LANGUAGES.has(key) || MARKDOWN_LANGUAGES.has(key);
}

/**
 * Splits `code` into coloured runs. Concatenating `text` over the result
 * returns `code` unchanged, whatever the language.
 */
export function tokenize(code: string, lang: string): Token[] {
  const key = lang.trim().toLowerCase();
  if (MARKDOWN_LANGUAGES.has(key)) return tokenizeMarkdown(code);
  const spec = LANGUAGES.get(key);
  if (!spec) return code === "" ? [] : [{ role: "plain", text: code }];
  return tokenizeCode(code, spec);
}

class Emitter {
  readonly tokens: Token[] = [];

  push(role: Role, text: string): void {
    if (text === "") return;
    const last = this.tokens[this.tokens.length - 1];
    if (last && last.role === role) last.text += text;
    else this.tokens.push({ role, text });
  }
}

function isWordStart(ch: string): boolean {
  return /[A-Za-z_$]/.test(ch);
}

function isWord(ch: string): boolean {
  return /[A-Za-z0-9_$]/.test(ch);
}

function isDigit(ch: string): boolean {
  return ch >= "0" && ch <= "9";
}

function tokenizeCode(code: string, spec: Language): Token[] {
  const out = new Emitter();
  let i = 0;
  let lineStart = true;
  while (i < code.length) {
    const ch = code[i]!;

    const block = spec.blockComment.find((pair) => code.startsWith(pair[0], i));
    if (block) {
      const end = blockCommentEnd(code, i, block, spec.nestedBlockComment);
      out.push("comment", code.slice(i, end));
      i = end;
      lineStart = false;
      continue;
    }

    const line = spec.lineComment.find((prefix) => code.startsWith(prefix, i));
    if (line) {
      const nl = code.indexOf("\n", i);
      const end = nl < 0 ? code.length : nl;
      out.push("comment", code.slice(i, end));
      i = end;
      continue;
    }

    const triple = spec.tripleQuotes.find((q) => code.startsWith(q, i));
    if (triple) {
      const end = closingIndex(code, i + triple.length, triple, true);
      out.push("string", code.slice(i, end));
      i = end;
      lineStart = false;
      continue;
    }

    if (spec.rawStrings && ch === "r") {
      const raw = rawStringEnd(code, i);
      if (raw > i) {
        out.push("string", code.slice(i, raw));
        i = raw;
        lineStart = false;
        continue;
      }
    }

    if (spec.quotes.includes(ch)) {
      const end = closingIndex(code, i + 1, ch, spec.escaped.includes(ch));
      out.push("string", code.slice(i, end));
      i = end;
      lineStart = false;
      continue;
    }

    if (isDigit(ch) || (ch === "." && isDigit(code[i + 1] ?? ""))) {
      let j = i;
      while (j < code.length && /[0-9a-fA-FxXoObB._]/.test(code[j]!)) j++;
      out.push("number", code.slice(i, j));
      i = j;
      lineStart = false;
      continue;
    }

    if (isWordStart(ch)) {
      let j = i;
      while (j < code.length && isWord(code[j]!)) j++;
      const word = code.slice(i, j);
      if (spec.keywords.has(word)) out.push("keyword", word);
      else if (spec.keysAreKeywords && isKey(code, i, j, lineStart)) out.push("keyword", word);
      else out.push("plain", word);
      i = j;
      lineStart = false;
      continue;
    }

    if (ch === "\n") {
      out.push("plain", ch);
      i++;
      lineStart = true;
      continue;
    }

    if (PUNCTUATION.has(ch)) {
      out.push("punct", ch);
      i++;
      lineStart = false;
      continue;
    }

    out.push("plain", ch);
    i++;
    if (ch !== " " && ch !== "\t") lineStart = false;
  }
  return out.tokens;
}

/** A bare word that opens its line and is followed by `:` or `=` names a key. */
function isKey(code: string, start: number, end: number, lineStart: boolean): boolean {
  if (!lineStart) return false;
  let j = end;
  while (j < code.length && (code[j] === " " || code[j] === "\t")) j++;
  void start;
  return code[j] === ":" || code[j] === "=";
}

function blockCommentEnd(code: string, at: number, pair: [string, string], nested: boolean): number {
  const [open, close] = pair;
  let depth = 0;
  let i = at;
  while (i < code.length) {
    if (code.startsWith(open, i)) {
      depth++;
      i += open.length;
      if (!nested && depth > 1) continue;
      continue;
    }
    if (code.startsWith(close, i)) {
      depth--;
      i += close.length;
      if (depth <= 0) return i;
      continue;
    }
    i++;
  }
  return code.length;
}

/** Index just past the closing delimiter, or the end of the input without one. */
function closingIndex(code: string, from: number, close: string, escapes: boolean): number {
  let i = from;
  while (i < code.length) {
    if (escapes && code[i] === "\\") {
      i += 2;
      continue;
    }
    if (code.startsWith(close, i)) return i + close.length;
    i++;
  }
  return code.length;
}

/** Index just past a Rust raw string opening at `at`, or `at` when there is none. */
function rawStringEnd(code: string, at: number): number {
  let i = at + 1;
  let hashes = 0;
  while (code[i] === "#") {
    hashes++;
    i++;
  }
  if (code[i] !== '"') return at;
  const close = `"${"#".repeat(hashes)}`;
  return closingIndex(code, i + 1, close, false);
}

/**
 * Markdown carries no keywords, so its roles are structural: a heading
 * marker and its text read as a keyword, a code span or fence as a string,
 * a list bullet or quote marker as punctuation.
 */
function tokenizeMarkdown(code: string): Token[] {
  const out = new Emitter();
  const lines = code.split("\n");
  let inFence = false;
  lines.forEach((line, index) => {
    if (index > 0) out.push("plain", "\n");
    if (/^\s*(```|~~~)/.test(line)) {
      inFence = !inFence;
      out.push("string", line);
      return;
    }
    if (inFence) {
      out.push("string", line);
      return;
    }
    const heading = /^(\s*#{1,6}\s.*)$/.exec(line);
    if (heading) {
      out.push("keyword", heading[1]!);
      return;
    }
    const marker = /^(\s*(?:[-*+]|\d+\.|>)\s)/.exec(line);
    let rest = line;
    if (marker) {
      out.push("punct", marker[1]!);
      rest = line.slice(marker[1]!.length);
    }
    for (const part of rest.split(/(`[^`]*`)/)) {
      if (part === "") continue;
      out.push(part.startsWith("`") && part.endsWith("`") && part.length > 1 ? "string" : "plain", part);
    }
  });
  return out.tokens;
}
