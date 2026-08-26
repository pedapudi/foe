//! The character budget the tool results of one model turn share.
//!
//! Every tool returns a canonical value, which the log keeps whole, and a
//! rendering, which is the only part the model reads. A rendering is
//! re-sent in every request after the step that produced it, so one large
//! rendering is paid for again on every later turn, and several large ones
//! in a single turn are paid for together. `read` and `bash` each bound one
//! call, so what is left unbounded is the turn: the renderings of one turn
//! therefore share one budget. Each call may show an equal part of it, a
//! call that needs less leaves the rest to the others, and no call is held
//! below a floor.
//!
//! The budget is a bound rather than a saving. It is set high enough that
//! ordinary work passes whole and one turn of many large calls cannot fill
//! the context.
//!
//! A rendering the budget cuts is returned beside the shortened value for
//! immutable archiving. A program with `retrieve` receives an opaque cursor
//! in the notice. Other programs receive the instruction to narrow and
//! repeat the original call. A numbered window from `read` is cut to its
//! head alone. Every other rendering keeps its head and its tail because a
//! command's output carries its verdict at both ends.
//!
//! The cut is applied before the result is appended to the log, so the
//! first request that carries the result and every request after it carry
//! the same text. No earlier turn is ever rewritten, which is what lets a
//! provider reuse the key-value cache of the prefix.

use crate::retrieval::ArchivedRendering;
use crate::{fitting, ToolCall, ToolValue};
use foe_program::harness_text as text;

/// Characters of tool-result text one model turn may show, divided between
/// the calls of that turn. One call is already bounded below this by the
/// limits of `read` and `bash`, so a turn of one large call passes almost
/// whole and only a turn of several is divided.
pub const TURN_BUDGET_CHARS: usize = 50_000;
/// Characters one result may show however small the turn's division makes
/// its share. A turn of many calls therefore costs more than the turn
/// budget, and never more than this floor times the number of calls.
pub const RESULT_FLOOR_CHARS: usize = 4_000;
/// Characters held back from a cut result's share for the notice naming
/// what was removed and the call that shows it.
const NOTICE_CHARS: usize = 200;
/// A retrieval notice carries a fixed-length source key and checksum in
/// addition to the ordinary shortening explanation.
const RETRIEVAL_NOTICE_CHARS: usize = 400;

/// The line number `read` writes before a tab at the start of every line of
/// its rendering. A rendering whose first line carries one is a numbered
/// window of a file, which is cut differently from any other output.
fn numbered(line: &str) -> Option<usize> {
    line.split_once('\t').and_then(|(n, _)| n.parse().ok())
}

/// The characters each rendering may show. Each may show an equal part of
/// the turn's budget; a rendering shorter than its part leaves the
/// remainder to be divided again, until every part is used. No rendering is
/// held below [`RESULT_FLOOR_CHARS`].
fn shares(lengths: &[usize]) -> Vec<usize> {
    let mut level = usize::MAX;
    let mut remaining = TURN_BUDGET_CHARS;
    let mut open: Vec<usize> = (0..lengths.len()).collect();
    while !open.is_empty() {
        let part = remaining / open.len();
        let (fits, rest): (Vec<usize>, Vec<usize>) = open.into_iter().partition(|&i| lengths[i] <= part);
        if fits.is_empty() {
            level = part;
            break;
        }
        remaining -= fits.iter().map(|&i| lengths[i]).sum::<usize>();
        open = rest;
    }
    let level = level.max(RESULT_FLOOR_CHARS);
    lengths.iter().map(|&n| n.min(level)).collect()
}

/// `body` shortened to `share` characters, ending in a notice of what was
/// removed. A numbered window keeps its head alone, so that the notice can
/// name the file line to resume at; any other rendering keeps its head and
/// its tail, two thirds of the room going to the head.
fn cut(body: &str, share: usize, cursor: Option<&str>) -> String {
    let lines: Vec<&str> = body.lines().collect();
    let notice_chars = if cursor.is_some() { RETRIEVAL_NOTICE_CHARS } else { NOTICE_CHARS };
    let room = share.saturating_sub(notice_chars);
    let window = lines.first().and_then(|l| numbered(l)).is_some();
    let (head, used) = fitting(lines.iter(), usize::MAX, if window { room } else { room * 2 / 3 });
    let tail = match window {
        true => 0,
        false => fitting(lines[head..].iter().rev(), usize::MAX, room - used).0,
    };
    let characters = body.chars().count().to_string();
    let omitted = (lines.len() - head - tail).to_string();
    // Only a numbered window resumes at a line: a line of any other output
    // that happens to open with a number and a tab names nothing readable.
    let resume = window.then(|| head.checked_sub(1).and_then(|last| numbered(lines[last]))).flatten();
    let notice = match (resume, cursor) {
        (Some(last), None) => text::fill(
            text::CUT_WINDOW,
            &[("omitted", &omitted), ("characters", &characters), ("next", &(last + 1).to_string())],
        ),
        (None, None) => text::fill(
            text::CUT_OUTPUT,
            &[("omitted", &omitted), ("characters", &characters), ("total", &lines.len().to_string())],
        ),
        (Some(_), Some(cursor)) => text::fill(
            text::CUT_RETRIEVABLE_WINDOW,
            &[("omitted", &omitted), ("characters", &characters), ("cursor", cursor)],
        ),
        (None, Some(cursor)) => text::fill(
            text::CUT_RETRIEVABLE,
            &[
                ("omitted", &omitted),
                ("characters", &characters),
                ("total", &lines.len().to_string()),
                ("cursor", cursor),
            ],
        ),
    };
    let mut out: Vec<&str> = lines[..head].to_vec();
    out.push(&notice);
    out.extend_from_slice(&lines[lines.len() - tail..]);
    out.join("\n")
}

/// Divides the turn's budget between the results of one turn and shortens
/// each rendering that exceeds its share. A result with no rendering of its
/// own is rendered from its canonical value first, so that every result of
/// the turn is counted.
pub fn bound(
    values: &mut [ToolValue],
    calls: &[ToolCall],
    step: u32,
    retrievable: bool,
) -> Vec<Option<ArchivedRendering>> {
    assert_eq!(values.len(), calls.len(), "one result per tool call");
    for value in values.iter_mut() {
        if value.rendered.is_none() {
            value.rendered = Some(value.value.to_string());
        }
    }
    let lengths: Vec<usize> =
        values.iter().map(|v| v.rendered.as_deref().unwrap_or_default().chars().count()).collect();
    let mut archives = Vec::with_capacity(values.len());
    for ((value, call), share) in values.iter_mut().zip(calls).zip(shares(&lengths)) {
        let Some(body) = value.rendered.as_deref().filter(|b| b.chars().count() > share) else {
            archives.push(None);
            continue;
        };
        let archived =
            ArchivedRendering { complete: body.to_string(), digest: crate::retrieval::digest(body.as_bytes()) };
        let cursor = retrievable.then(|| crate::retrieval::cursor(step, &call.id, body, 0));
        value.rendered = Some(cut(body, share, cursor.as_deref()));
        archives.push(Some(archived));
    }
    archives
}

#[cfg(test)]
#[path = "result_budget_test.rs"]
mod tests;
