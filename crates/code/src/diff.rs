//! Unified diff for a set of known replacements applied to one text.
//!
//! `edit` knows which byte spans it replaced, so no general diff
//! algorithm runs. Each replacement maps to the lines it touches; spans on
//! shared lines form one change, changes closer than twice the context form
//! one hunk, and common leading and trailing lines are trimmed from each
//! change so that the output shows what actually differs.

use std::fmt::Write;

/// Unchanged lines shown on either side of a change.
const CONTEXT: usize = 3;

/// A replacement of `original[start..end]` by `new_text`.
pub struct Span<'a> {
    pub start: usize,
    pub end: usize,
    pub new_text: &'a str,
}

pub struct Diff {
    pub text: String,
    pub added: usize,
    pub removed: usize,
}

/// One contiguous block of changed lines: `old` lines starting at 0-based
/// line `at` become `new` lines.
struct Change<'a> {
    at: usize,
    old: Vec<&'a str>,
    new: Vec<String>,
}

/// Renders the diff of `spans`, which must be sorted by `start` and must not
/// overlap. `name` labels both sides of the header.
pub fn unified(name: &str, original: &str, spans: &[Span]) -> Diff {
    if original.is_empty() && spans.len() == 1 && spans[0].start == 0 && spans[0].end == 0 {
        let body = spans[0].new_text.strip_suffix('\n').unwrap_or(spans[0].new_text);
        let new: Vec<&str> = if spans[0].new_text.is_empty() { Vec::new() } else { body.split('\n').collect() };
        let mut text = format!("--- a/{name}\n+++ b/{name}\n@@ -0,0 +1,{} @@\n", new.len());
        for line in &new {
            let _ = writeln!(text, "+{line}");
        }
        return Diff { text, added: new.len(), removed: 0 };
    }
    let lines: Vec<&str> = {
        let body = original.strip_suffix('\n').unwrap_or(original);
        if original.is_empty() {
            Vec::new()
        } else {
            body.split('\n').collect()
        }
    };
    let mut starts = Vec::with_capacity(lines.len());
    let mut pos = 0;
    for l in &lines {
        starts.push(pos);
        pos += l.len() + 1;
    }
    let line_of = |byte: usize| starts.partition_point(|&s| s <= byte).saturating_sub(1);
    let line_end = |i: usize| (starts[i] + lines[i].len() + 1).min(original.len());

    let mut changes: Vec<Change> = Vec::new();
    let mut i = 0;
    while i < spans.len() {
        let a = line_of(spans[i].start);
        let mut b = line_of(spans[i].end - 1);
        let mut j = i + 1;
        while j < spans.len() && line_of(spans[j].start) <= b {
            b = b.max(line_of(spans[j].end - 1));
            j += 1;
        }
        let mut new_text = String::new();
        let mut cursor = starts[a];
        for s in &spans[i..j] {
            new_text.push_str(&original[cursor..s.start]);
            new_text.push_str(s.new_text);
            cursor = s.end;
        }
        new_text.push_str(&original[cursor..line_end(b)]);
        let mut old: Vec<&str> = lines[a..=b].to_vec();
        let mut new: Vec<String> = match new_text.strip_suffix('\n') {
            _ if new_text.is_empty() => Vec::new(),
            Some(t) => t.split('\n').map(str::to_owned).collect(),
            None => new_text.split('\n').map(str::to_owned).collect(),
        };
        let lead = old.iter().zip(&new).take_while(|(o, n)| **o == n.as_str()).count();
        old.drain(..lead);
        new.drain(..lead);
        let trail = old.iter().rev().zip(new.iter().rev()).take_while(|(o, n)| **o == n.as_str()).count();
        old.truncate(old.len() - trail);
        new.truncate(new.len() - trail);
        if !old.is_empty() || !new.is_empty() {
            changes.push(Change { at: a + lead, old, new });
        }
        i = j;
    }

    let mut out = format!("--- a/{name}\n+++ b/{name}\n");
    let (mut added, mut removed, mut delta) = (0usize, 0usize, 0isize);
    let mut k = 0;
    while k < changes.len() {
        let hunk_start = changes[k].at.saturating_sub(CONTEXT);
        let mut end = k;
        while end + 1 < changes.len() && changes[end + 1].at <= changes[end].at + changes[end].old.len() + 2 * CONTEXT {
            end += 1;
        }
        let last = &changes[end];
        let hunk_end = (last.at + last.old.len() + CONTEXT).min(lines.len());
        let old_count = hunk_end - hunk_start;
        let (minus, plus): (usize, usize) =
            changes[k..=end].iter().map(|c| (c.old.len(), c.new.len())).fold((0, 0), |a, b| (a.0 + b.0, a.1 + b.1));
        let new_count = old_count - minus + plus;
        let new_start = (hunk_start as isize + delta) as usize;
        let shown = |start: usize, count: usize| if count == 0 { start } else { start + 1 };
        let _ = writeln!(
            out,
            "@@ -{},{old_count} +{},{new_count} @@",
            shown(hunk_start, old_count),
            shown(new_start, new_count)
        );
        let mut cursor = hunk_start;
        for c in &changes[k..=end] {
            for l in &lines[cursor..c.at] {
                let _ = writeln!(out, " {l}");
            }
            for l in &c.old {
                let _ = writeln!(out, "-{l}");
            }
            for l in &c.new {
                let _ = writeln!(out, "+{l}");
            }
            cursor = c.at + c.old.len();
        }
        for l in &lines[cursor..hunk_end] {
            let _ = writeln!(out, " {l}");
        }
        added += plus;
        removed += minus;
        delta += plus as isize - minus as isize;
        k = end + 1;
    }
    Diff { text: out, added, removed }
}

#[cfg(test)]
#[path = "diff_test.rs"]
mod tests;
