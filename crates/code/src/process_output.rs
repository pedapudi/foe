//! Shared rendering for output captured from a process.

use crate::{OUTPUT_MAX_CHARS, OUTPUT_MAX_LINES};
use foe_core::{fitting, CallCtx};
use std::fmt::Write;

pub(crate) struct ProcessOutput {
    pub(crate) stdout: String,
    pub(crate) stderr: String,
    pub(crate) rendered: String,
    pub(crate) truncated: bool,
    pub(crate) spill: Option<String>,
    pub(crate) line_count: usize,
}

/// Leads with `status` and keeps the tail, where process verdicts normally
/// appear. A cut rendering names an archive containing the complete output.
pub(crate) fn render(ctx: &CallCtx, status: &str, stdout: &[u8], stderr: &[u8], spill_name: &str) -> ProcessOutput {
    let stdout = String::from_utf8_lossy(stdout).into_owned();
    let stderr = String::from_utf8_lossy(stderr).into_owned();
    let mut combined = stdout.clone();
    if !stderr.is_empty() {
        if !combined.is_empty() && !combined.ends_with('\n') {
            combined.push('\n');
        }
        let _ = write!(combined, "--- stderr ---\n{stderr}");
    }
    let lines: Vec<&str> = combined.lines().collect();
    let (kept, _) = fitting(lines.iter().rev(), OUTPUT_MAX_LINES, OUTPUT_MAX_CHARS);
    let truncated = kept < lines.len();
    let mut spill = None;
    let mut rendered = format!("[{status}]\n");
    if truncated {
        let file = ctx.spill_dir.join(format!("{}-{spill_name}.txt", ctx.call_id));
        let saved = std::fs::create_dir_all(&ctx.spill_dir).and_then(|()| std::fs::write(&file, combined.as_bytes()));
        let _ = match &saved {
            Ok(()) => writeln!(
                rendered,
                "[Showing the last {} of {} lines. Full output saved to {}]",
                kept,
                lines.len(),
                file.display()
            ),
            Err(e) => writeln!(
                rendered,
                "[Showing the last {} of {} lines. Saving the full output failed: {e}]",
                kept,
                lines.len()
            ),
        };
        if saved.is_ok() {
            spill = Some(file.display().to_string());
        }
    }
    for line in &lines[lines.len() - kept..] {
        let _ = writeln!(rendered, "{line}");
    }
    let line_count = stdout.lines().count() + stderr.lines().count();
    ProcessOutput { stdout, stderr, rendered, truncated, spill, line_count }
}
