//! `grep`: regular-expression search over file contents, in process.
//!
//! The tree is walked with the `ignore` crate so that `.gitignore` and
//! `.ignore` files apply, and each file's bytes are obtained through the
//! `Reader`, which keeps containment in one place. Matches are sorted by
//! path and line so that the result is deterministic regardless of the
//! filesystem's directory order.

use crate::{display, parse_args, resolve, GREP_COLLECT_MAX, GREP_DEFAULT_LIMIT, GREP_LINE_MAX_CHARS};
use foe_core::{CallCtx, Effect, Tool, ToolSpec, ToolValue};
use grep_regex::RegexMatcherBuilder;
use grep_searcher::{BinaryDetection, Searcher, SearcherBuilder, Sink, SinkContext, SinkMatch};
use ignore::overrides::OverrideBuilder;
use ignore::WalkBuilder;
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::fmt::Write;
use std::path::PathBuf;

pub struct Grep {
    spec: ToolSpec,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Args {
    pattern: String,
    path: Option<String>,
    glob: Option<String>,
    #[serde(default)]
    ignore_case: bool,
    #[serde(default)]
    literal: bool,
    #[serde(default)]
    context: usize,
    limit: Option<usize>,
}

/// One line of output: a matching line, or a context line near one.
#[derive(Serialize)]
struct Hit {
    path: PathBuf,
    line: u64,
    text: String,
    context: bool,
}

struct Collected {
    hits: Vec<Hit>,
    matches: usize,
    /// False once `GREP_COLLECT_MAX` matches were collected and the search stopped.
    complete: bool,
}

struct Collect<'a> {
    path: &'a std::path::Path,
    into: &'a mut Collected,
}

impl Collect<'_> {
    fn push(&mut self, line: u64, bytes: &[u8], context: bool) -> Result<bool, std::io::Error> {
        if !context {
            if self.into.matches >= GREP_COLLECT_MAX {
                self.into.complete = false;
                return Ok(false);
            }
            self.into.matches += 1;
        }
        let text = String::from_utf8_lossy(bytes);
        let text = clamp(text.trim_end_matches(['\n', '\r']));
        self.into.hits.push(Hit { path: self.path.to_path_buf(), line, text, context });
        Ok(true)
    }
}

impl Sink for Collect<'_> {
    type Error = std::io::Error;

    fn matched(&mut self, _: &Searcher, m: &SinkMatch<'_>) -> Result<bool, Self::Error> {
        self.push(m.line_number().unwrap_or(0), m.bytes(), false)
    }

    fn context(&mut self, _: &Searcher, c: &SinkContext<'_>) -> Result<bool, Self::Error> {
        self.push(c.line_number().unwrap_or(0), c.bytes(), true)
    }
}

/// Cuts a line at `GREP_LINE_MAX_CHARS` characters and marks the cut.
fn clamp(text: &str) -> String {
    match text.char_indices().nth(GREP_LINE_MAX_CHARS) {
        Some((at, _)) => {
            let more = text[at..].chars().count();
            format!("{} [clamped at {GREP_LINE_MAX_CHARS} chars; {more} more]", &text[..at])
        }
        None => text.to_owned(),
    }
}

impl Grep {
    pub(crate) fn new() -> Self {
        Self {
            spec: ToolSpec {
                name: "grep".into(),
                description: format!(
                    "Search file contents with a regular expression under a directory or in one \
                     file, honoring .gitignore. Matching lines come back sorted by path and line, \
                     up to limit (default {GREP_DEFAULT_LIMIT}); each line is clamped at \
                     {GREP_LINE_MAX_CHARS} characters. The search stops after {GREP_COLLECT_MAX} \
                     matches."
                ),
                instruction: Some(
                    "Use grep to locate definitions, call sites, and strings across the tree before \
                     reading whole files. Narrow with glob (for example \"*.rs\") and set literal \
                     when the pattern contains regex metacharacters."
                        .into(),
                ),
                params: json!({
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Regular expression (Rust regex syntax), or a literal string when literal is true."},
                        "path": {"type": "string", "description": "Directory or file to search. Default: the first read root."},
                        "glob": {"type": "string", "description": "Only search files whose path matches this glob, for example \"*.py\" or \"src/**/*.ts\"."},
                        "ignore_case": {"type": "boolean", "description": "Case-insensitive matching. Default false."},
                        "literal": {"type": "boolean", "description": "Treat pattern as a fixed string. Default false."},
                        "context": {"type": "integer", "minimum": 0, "description": "Lines of context before and after each match. Default 0."},
                        "limit": {"type": "integer", "minimum": 1, "description": format!("Maximum matches to show. Default {GREP_DEFAULT_LIMIT}.")}
                    },
                    "required": ["pattern"],
                    "additionalProperties": false
                }),
                effect: Effect::Reads,
            },
        }
    }
}

#[async_trait::async_trait]
impl Tool for Grep {
    fn spec(&self) -> &ToolSpec {
        &self.spec
    }

    async fn call(&self, args: serde_json::Value, ctx: &CallCtx) -> ToolValue {
        let a: Args = match parse_args("grep", args) {
            Ok(a) => a,
            Err(e) => return e,
        };
        let Some(reader) = ctx.reader.as_ref() else {
            return ToolValue::error("grep: dispatched without a reader handle");
        };
        let roots = reader.roots();
        let root = match (&a.path, roots.first()) {
            (Some(p), _) => resolve(roots, p),
            (None, Some(r)) => r.clone(),
            (None, None) => return ToolValue::error("grep: no read root to search"),
        };
        let root_shown = display(roots, &root);
        if let Err(e) = reader.metadata(&root) {
            return ToolValue::error(format!("grep: {root_shown}: {e}"));
        }
        let matcher =
            match RegexMatcherBuilder::new().case_insensitive(a.ignore_case).fixed_strings(a.literal).build(&a.pattern)
            {
                Ok(m) => m,
                Err(e) => return ToolValue::error(format!("grep: invalid pattern: {e}")),
            };
        let mut walk = WalkBuilder::new(&root);
        walk.require_git(false);
        if let Some(glob) = &a.glob {
            let mut ov = OverrideBuilder::new(&root);
            let built = ov.add(glob).and_then(|b| b.build());
            match built {
                Ok(ov) => walk.overrides(ov),
                Err(e) => return ToolValue::error(format!("grep: invalid glob {glob:?}: {e}")),
            };
        }
        let mut searcher = SearcherBuilder::new()
            .line_number(true)
            .before_context(a.context)
            .after_context(a.context)
            .binary_detection(BinaryDetection::quit(0))
            .build();
        let mut collected = Collected { hits: Vec::new(), matches: 0, complete: true };
        let mut searched = 0usize;
        for entry in walk.build() {
            let Ok(entry) = entry else { continue };
            if !entry.file_type().is_some_and(|t| t.is_file()) {
                continue;
            }
            let Ok(bytes) = reader.read(entry.path()) else {
                continue;
            };
            searched += 1;
            let sink = Collect { path: entry.path(), into: &mut collected };
            let _ = searcher.search_slice(&matcher, &bytes, sink);
            if !collected.complete {
                break;
            }
        }
        collected.hits.sort_by(|x, y| x.path.cmp(&y.path).then(x.line.cmp(&y.line)));

        let limit = a.limit.unwrap_or(GREP_DEFAULT_LIMIT).max(1);
        let files = {
            let mut paths: Vec<&PathBuf> = collected.hits.iter().filter(|h| !h.context).map(|h| &h.path).collect();
            paths.dedup();
            paths.len()
        };
        let mut out = format!("{} matches in {files} files under {root_shown}", collected.matches);
        if !collected.complete {
            let _ = write!(out, " (search stopped at {GREP_COLLECT_MAX} matches)");
        }
        if collected.matches > limit {
            let _ = write!(out, "; showing the first {limit}. Refine the pattern or raise limit.");
        }
        out.push('\n');
        let mut shown = 0;
        for h in &collected.hits {
            if !h.context {
                if shown == limit {
                    break;
                }
                shown += 1;
            }
            let sep = if h.context { '-' } else { ':' };
            let _ = writeln!(out, "{}:{}{sep}{}", display(roots, &h.path), h.line, h.text);
        }
        ToolValue::ok(
            json!({
                "pattern": a.pattern,
                "root": root.display().to_string(),
                "matches": collected.matches,
                "files": files,
                "searched_files": searched,
                "complete": collected.complete,
                "hits": collected.hits,
            }),
            out.trim_end_matches('\n'),
        )
        .subject(format!("{} matches in {files} files under {root_shown}", collected.matches))
    }
}

#[cfg(test)]
#[path = "grep_test.rs"]
mod tests;
