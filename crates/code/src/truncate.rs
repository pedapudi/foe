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
    let mut v: Vec<&str> = text
        .split('\n')
        .map(|l| l.strip_suffix('\r').unwrap_or(l))
        .collect();
    if v.last() == Some(&"") {
        v.pop();
    }
    v
}

/// The longest prefix of `lines` within both limits.
pub fn head(lines: &[&str], max_lines: usize, max_bytes: usize) -> Cut {
    let mut bytes = 0;
    let mut end = 0;
    for line in lines.iter().take(max_lines) {
        if bytes + line.len() + 1 > max_bytes {
            break;
        }
        bytes += line.len() + 1;
        end += 1;
    }
    Cut { start: 0, end }
}

/// The longest suffix of `lines` within both limits. Only `bash` keeps a tail.
#[cfg(any(feature = "exec", test))]
pub fn tail(lines: &[&str], max_lines: usize, max_bytes: usize) -> Cut {
    let mut bytes = 0;
    let mut start = lines.len();
    for line in lines.iter().rev().take(max_lines) {
        if bytes + line.len() + 1 > max_bytes {
            break;
        }
        bytes += line.len() + 1;
        start -= 1;
    }
    Cut {
        start,
        end: lines.len(),
    }
}

#[cfg(test)]
#[path = "truncate_test.rs"]
mod tests;
