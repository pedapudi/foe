//! `edit`: exact-text replacement in one file, applied atomically.
//!
//! Every `old_text` is located in the original content, so the edits are
//! independent of each other's results. A UTF-8 byte order mark and CRLF
//! line endings are removed before matching and restored on write, so the
//! model matches against the text it saw through `read`.

use crate::diff::{self, Span};
use crate::{display, file_version, parse_args, resolve, EDIT_DIFF_MAX_LINES};
use foe_core::{CallCtx, CapError, Tool, ToolValue};
use foe_program::{Effect, ToolSpec};
use serde::Deserialize;
use serde_json::json;
use std::io::Read as _;

const BOM: &str = "\u{feff}";

pub struct Edit {
    spec: ToolSpec,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Args {
    path: String,
    expected_version: Option<String>,
    edits: Vec<Replacement>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Replacement {
    old_text: String,
    new_text: String,
}

impl Edit {
    pub(crate) fn new() -> Self {
        Self {
            spec: ToolSpec {
                name: "edit".into(),
                description: "Replace exact text in one file, or create a file with one edit whose \
                              old_text is empty. Each nonempty old_text must occur exactly once in the \
                              current file; matches must not overlap; all edits are applied together \
                              in one atomic write. There is no fuzzy matching: copy old_text exactly, \
                              including indentation. Returns a unified diff."
                    .into(),
                instruction: Some(
                    "Use edit for every file change. Read an existing file first and include enough \
                     surrounding lines in old_text to make each match unique. To create a file, send \
                     one edit with empty old_text and the complete file in new_text. Pass the version \
                     returned by read as expected_version when the file must be unchanged."
                        .into(),
                ),
                params: json!({
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path, absolute or relative to the first read root."},
                        "expected_version": {"type": "string", "description": "Optional SHA-256 version returned by read; its sha256: prefix may be omitted. The edit is refused when the current bytes differ."},
                        "edits": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "old_text": {"type": "string", "description": "Text to find; must occur exactly once. Use an empty string only to create a missing or empty file with one edit."},
                                    "new_text": {"type": "string", "description": "Replacement text."}
                                },
                                "required": ["old_text", "new_text"],
                                "additionalProperties": false
                            }
                        }
                    },
                    "required": ["path", "edits"],
                    "additionalProperties": false
                }),
                effect: Effect::Writes,
            },
        }
    }
}

/// True when the text has at least one line break and every one is CRLF.
fn all_crlf(text: &str) -> bool {
    let lf = text.matches('\n').count();
    lf > 0 && text.matches("\r\n").count() == lf
}

/// The diff as the rendering shows it: whole up to [`EDIT_DIFF_MAX_LINES`]
/// lines, then one elision line counting the added and removed lines not
/// shown. The bound is applied here, where the rendering is produced; the
/// canonical value keeps the complete diff.
fn bounded_diff(text: &str) -> String {
    let lines: Vec<&str> = text.lines().collect();
    if lines.len() <= EDIT_DIFF_MAX_LINES {
        return text.to_owned();
    }
    let (added, removed) = lines[EDIT_DIFF_MAX_LINES..].iter().fold((0, 0), |(a, r), l| match l.as_bytes().first() {
        Some(b'+') => (a + 1, r),
        Some(b'-') => (a, r + 1),
        _ => (a, r),
    });
    let mut out = lines[..EDIT_DIFF_MAX_LINES].join("\n");
    out.push_str(&format!(
        "\n[Diff cut at {EDIT_DIFF_MAX_LINES} lines: {added} added and {removed} removed lines omitted. \
         Every edit was applied; read the file to see the result.]\n"
    ));
    out
}

#[async_trait::async_trait]
impl Tool for Edit {
    fn spec(&self) -> &ToolSpec {
        &self.spec
    }

    async fn call(&self, args: serde_json::Value, ctx: &CallCtx) -> ToolValue {
        let a: Args = match parse_args("edit", args) {
            Ok(a) => a,
            Err(e) => return e,
        };
        let (Some(reader), Some(writer)) = (ctx.reader.as_ref(), ctx.writer.as_ref()) else {
            return ToolValue::error("edit: dispatched without both a reader and a writer handle");
        };
        if a.edits.is_empty() {
            return ToolValue::error("edit: edits must contain at least one entry");
        }
        let path = resolve(reader.roots(), &a.path);
        let shown = display(reader.roots(), &path);
        let creates_file = a.edits.len() == 1 && a.edits[0].old_text.is_empty();
        let mut raw_bytes = Vec::new();
        let read = reader.open(&path).and_then(|mut file| file.read_to_end(&mut raw_bytes).map_err(Into::into));
        match read {
            Ok(_) => {}
            Err(CapError::Io(e)) if creates_file && e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => return ToolValue::error(format!("edit: {shown}: {e}")),
        }
        let previous_version = file_version(&raw_bytes);
        let expected = a.expected_version.as_deref().map(|v| v.strip_prefix("sha256:").unwrap_or(v));
        if expected.is_some_and(|v| Some(v) != previous_version.strip_prefix("sha256:")) {
            return ToolValue::error(format!(
                "edit: {shown} has version {previous_version}, which differs from expected_version {}",
                a.expected_version.as_deref().unwrap_or_default()
            ));
        }
        let raw = match String::from_utf8(raw_bytes) {
            Ok(s) => s,
            Err(_) => {
                return ToolValue::error(format!("edit: {shown} is not valid UTF-8; edit changes text files only"))
            }
        };
        let (bom, body) = match raw.strip_prefix(BOM) {
            Some(rest) => (true, rest),
            None => (false, raw.as_str()),
        };
        let crlf = all_crlf(body);
        let normalize = |s: &str| {
            if crlf {
                s.replace("\r\n", "\n")
            } else {
                s.to_owned()
            }
        };
        let text = normalize(body);

        let mut located: Vec<(usize, usize, usize, String)> = Vec::with_capacity(a.edits.len());
        for (i, r) in a.edits.iter().enumerate() {
            let old = normalize(&r.old_text);
            if old.is_empty() {
                if a.edits.len() != 1 {
                    return ToolValue::error(format!(
                        "edits[{i}]: empty old_text requires exactly one edit because it creates the complete file"
                    ));
                }
                if !text.is_empty() {
                    return ToolValue::error(format!(
                        "edits[{i}]: empty old_text requires a missing or empty file; {shown} contains text"
                    ));
                }
                located.push((i, 0, 0, normalize(&r.new_text)));
                continue;
            }
            let count = text.matches(old.as_str()).count();
            if count != 1 {
                let hint = if count == 0 {
                    "the text must match the file exactly, including whitespace"
                } else {
                    "include more surrounding lines to make it unique"
                };
                return ToolValue::error(format!(
                    "edits[{i}]: old_text occurs {count} times in {shown}; it must occur exactly once ({hint})"
                ));
            }
            let start = text.find(old.as_str()).unwrap_or(0);
            located.push((i, start, start + old.len(), normalize(&r.new_text)));
        }
        located.sort_by_key(|e| e.1);
        for w in located.windows(2) {
            if w[1].1 < w[0].2 {
                return ToolValue::error(format!(
                    "edits[{}] and edits[{}] overlap in {shown}; merge them into one edit",
                    w[0].0, w[1].0
                ));
            }
        }
        let spans: Vec<Span> = located.iter().map(|(_, s, e, n)| Span { start: *s, end: *e, new_text: n }).collect();
        let mut result = String::with_capacity(text.len());
        let mut cursor = 0;
        for s in &spans {
            result.push_str(&text[cursor..s.start]);
            result.push_str(s.new_text);
            cursor = s.end;
        }
        result.push_str(&text[cursor..]);
        if result == text {
            return ToolValue::error(format!("edit: {shown} is unchanged; every new_text equals its old_text"));
        }
        let d = diff::unified(&shown, &text, &spans);

        let mut out = String::with_capacity(result.len() + 3);
        if bom {
            out.push_str(BOM);
        }
        if crlf {
            out.push_str(&result.replace('\n', "\r\n"));
        } else {
            out.push_str(&result);
        }
        if let Err(e) = writer.write(&path, out.as_bytes()) {
            return ToolValue::error(format!("edit: {shown}: {e}"));
        }
        let n = a.edits.len();
        // The head of the rendering is also the one line a reader wants,
        // so it is written once and used for both.
        let did = format!("{shown}: {n} edit(s), +{} -{} lines", d.added, d.removed);
        ToolValue::ok(
            json!({
                "path": path.display().to_string(),
                "edits": n,
                "added": d.added,
                "removed": d.removed,
                "diff": d.text,
                "previous_version": previous_version,
                "version": file_version(out.as_bytes()),
            }),
            format!("edited {did}\n{}", bounded_diff(&d.text)),
        )
        .subject(did)
    }
}

#[cfg(test)]
#[path = "edit_test.rs"]
mod tests;
