//! Head and tail truncation shared by `read` and `bash`.
//!
//! Both cuts keep whole lines: a line is shown entirely or omitted, so a cut
//! never splits a UTF-8 sequence. The byte measure counts each line plus one
//! byte for its terminating newline. The first line that would exceed either
//! limit ends the cut, so a single line longer than the byte limit yields an
//! empty cut.

/// The half-open index range of the lines that survive a cut.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Cut {
    pub start: usize,
    pub end: usize,
}

impl Cut {
    pub fn len(&self) -> usize {
        self.end - self.start
    }
}

/// Splits text into lines without their terminators. A trailing newline does
/// not produce a final empty line, and a carriage return before a newline is
/// dropped.
pub fn lines(text: &str) -> Vec<&str> {
    let mut v: Vec<&str> = text.split('\n').map(|l| l.strip_suffix('\r').unwrap_or(l)).collect();
    if v.last() == Some(&"") {
        v.pop();
    }
    v
}

/// How many of `lines`, taken in the order given, fit within both limits.
/// A line counts its own length and the newline after it.
fn fits<'a>(lines: impl Iterator<Item = &'a &'a str>, max_lines: usize, max_bytes: usize) -> usize {
    let mut bytes = 0;
    let mut kept = 0;
    for line in lines.take(max_lines) {
        if bytes + line.len() + 1 > max_bytes {
            break;
        }
        bytes += line.len() + 1;
        kept += 1;
    }
    kept
}

/// The longest prefix of `lines` within both limits.
pub fn head(lines: &[&str], max_lines: usize, max_bytes: usize) -> Cut {
    Cut { start: 0, end: fits(lines.iter(), max_lines, max_bytes) }
}

/// The longest suffix of `lines` within both limits. Only `bash` keeps a tail.
#[cfg(any(feature = "exec", test))]
pub fn tail(lines: &[&str], max_lines: usize, max_bytes: usize) -> Cut {
    Cut { start: lines.len() - fits(lines.iter().rev(), max_lines, max_bytes), end: lines.len() }
}

#[cfg(test)]
#[path = "truncate_test.rs"]
mod tests;
