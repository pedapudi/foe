//! `grep`: regular-expression search over file contents, in process.
//!
//! The tree is walked with the `ignore` crate so that `.gitignore` and
//! `.ignore` files apply, and each file is streamed through the `Reader`,
//! which keeps containment in one place. Matches are sorted by path and line
//! so that the result is deterministic regardless of the filesystem's
//! directory order.

use crate::{
    display, parse_args, resolve, GREP_COLLECT_MAX, GREP_DEFAULT_LIMIT, GREP_HIT_COLLECT_MAX, GREP_LINE_MAX_CHARS,
    GREP_SEARCH_BUFFER_MAX_BYTES,
};
use foe_core::{CallCtx, Tool, ToolValue};
use foe_program::{Effect, ToolSpec};
use grep_regex::RegexMatcherBuilder;
use grep_searcher::{BinaryDetection, Searcher, SearcherBuilder, Sink, SinkContext, SinkMatch};
use ignore::gitignore::{Gitignore, GitignoreBuilder};
use ignore::overrides::OverrideBuilder;
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::fmt::Write;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};

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
    /// Names the collection bound that stopped the search.
    stopped_at: Option<&'static str>,
}

struct Collect<'a> {
    path: &'a std::path::Path,
    into: &'a mut Collected,
}

impl Collect<'_> {
    fn push(&mut self, line: u64, bytes: &[u8], context: bool) -> Result<bool, std::io::Error> {
        if self.into.hits.len() >= GREP_HIT_COLLECT_MAX {
            self.into.stopped_at = Some("result lines");
            return Ok(false);
        }
        if !context {
            if self.into.matches >= GREP_COLLECT_MAX {
                self.into.stopped_at = Some("matches");
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

fn ignore_rules(reader: &dyn foe_core::Reader, dir: &Path) -> Result<Gitignore, String> {
    let mut builder = GitignoreBuilder::new(dir);
    for name in [".gitignore", ".ignore"] {
        let path = dir.join(name);
        let file = match reader.open(&path) {
            Ok(file) => file,
            Err(foe_core::CapError::Io(error)) if error.kind() == std::io::ErrorKind::NotFound => continue,
            Err(error) => return Err(format!("{}: {error}", path.display())),
        };
        for (index, line) in BufReader::new(file).lines().enumerate() {
            let mut line = line.map_err(|error| format!("{}:{}: {error}", path.display(), index + 1))?;
            if index == 0 {
                line = line.trim_start_matches('\u{feff}').to_owned();
            }
            builder
                .add_line(Some(path.clone()), &line)
                .map_err(|error| format!("{}:{}: {error}", path.display(), index + 1))?;
        }
    }
    builder.build().map_err(|error| format!("{}: {error}", dir.display()))
}

fn hidden(path: &Path) -> bool {
    path.file_name().is_some_and(|name| name.to_string_lossy().starts_with('.'))
}

fn selected(path: &Path, is_dir: bool, rules: &[Gitignore], overrides: Option<&ignore::overrides::Override>) -> bool {
    let ignored = rules
        .iter()
        .rev()
        .map(|rule| rule.matched(path, is_dir))
        .find(|matched| !matched.is_none())
        .is_some_and(|matched| matched.is_ignore());
    match overrides.map(|matcher| matcher.matched(path, is_dir)) {
        Some(matched) if matched.is_ignore() => false,
        Some(matched) if matched.is_whitelist() => true,
        _ => !ignored,
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
                     {GREP_LINE_MAX_CHARS} characters. The line-search buffer uses at most {} MiB and \
                     stops after {GREP_COLLECT_MAX} matches or {GREP_HIT_COLLECT_MAX} result lines.",
                    GREP_SEARCH_BUFFER_MAX_BYTES / 1024 / 1024
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
        let override_matcher = if let Some(glob) = &a.glob {
            let mut ov = OverrideBuilder::new(&root);
            let built = ov.add(glob).and_then(|b| b.build());
            match built {
                Ok(ov) => Some(ov),
                Err(e) => return ToolValue::error(format!("grep: invalid glob {glob:?}: {e}")),
            }
        } else {
            None
        };
        let mut searcher = SearcherBuilder::new()
            .line_number(true)
            .before_context(a.context)
            .after_context(a.context)
            .binary_detection(BinaryDetection::quit(0))
            .heap_limit(Some(GREP_SEARCH_BUFFER_MAX_BYTES))
            .build();
        let mut collected = Collected { hits: Vec::new(), matches: 0, stopped_at: None };
        let mut searched = 0usize;
        let mut failed = 0usize;
        let mut traversal_failures = 0usize;
        let mut first_failure = None;
        let mut files = Vec::new();
        if reader.metadata(&root).is_ok_and(|metadata| metadata.is_file()) {
            if selected(&root, false, &[], override_matcher.as_ref()) {
                files.push(root.clone());
            }
        } else {
            let mut pending = vec![(root.clone(), Vec::<Gitignore>::new())];
            while let Some((dir, mut rules)) = pending.pop() {
                match ignore_rules(reader.as_ref(), &dir) {
                    Ok(rule) => rules.push(rule),
                    Err(error) => {
                        traversal_failures += 1;
                        first_failure.get_or_insert(error);
                    }
                }
                let mut entries = match reader.read_dir(&dir) {
                    Ok(entries) => entries,
                    Err(error) => {
                        traversal_failures += 1;
                        first_failure.get_or_insert_with(|| format!("{}: {error}", display(roots, &dir)));
                        continue;
                    }
                };
                entries.sort_by(|left, right| right.path.cmp(&left.path));
                for entry in entries {
                    if hidden(&entry.path) || !(entry.is_file || entry.is_dir) {
                        continue;
                    }
                    if !selected(&entry.path, entry.is_dir, &rules, override_matcher.as_ref()) {
                        continue;
                    }
                    if entry.is_dir {
                        pending.push((entry.path, rules.clone()));
                    } else {
                        files.push(entry.path);
                    }
                }
            }
        }
        files.sort();
        for path in files {
            let file = match reader.open(&path) {
                Ok(file) => file,
                Err(error) => {
                    failed += 1;
                    first_failure.get_or_insert_with(|| format!("{}: {error}", display(roots, &path)));
                    continue;
                }
            };
            let sink = Collect { path: &path, into: &mut collected };
            match searcher.search_reader(&matcher, file, sink) {
                Ok(()) => searched += 1,
                Err(error) => {
                    failed += 1;
                    first_failure.get_or_insert_with(|| format!("{}: {error}", display(roots, &path)));
                }
            }
            if collected.stopped_at.is_some() {
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
        if let Some(bound) = collected.stopped_at {
            let limit = if bound == "matches" { GREP_COLLECT_MAX } else { GREP_HIT_COLLECT_MAX };
            let _ = write!(out, " (search stopped at {limit} {bound})");
        }
        let failures = failed + traversal_failures;
        if failures > 0 {
            let _ = write!(out, "; {failures} path(s) could not be searched");
            if let Some(error) = &first_failure {
                let _ = write!(out, " (first: {})", clamp(error));
            }
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
        let complete = collected.stopped_at.is_none() && failures == 0;
        let subject = format!("{} match(es) in {files} file(s) under {root_shown}", collected.matches);
        let subject = if complete { subject } else { format!("{subject}; incomplete") };
        ToolValue::ok(
            json!({
                "pattern": a.pattern,
                "root": root.display().to_string(),
                "matches": collected.matches,
                "files": files,
                "searched_files": searched,
                "failed_files": failed,
                "traversal_failures": traversal_failures,
                "first_failure": first_failure,
                "complete": complete,
                "hits": collected.hits,
            }),
            out.trim_end_matches('\n'),
        )
        .subject(subject)
    }
}

#[cfg(test)]
#[path = "grep_test.rs"]
mod tests;
