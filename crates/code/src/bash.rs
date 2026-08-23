//! `bash`: one shell command through the `Executor`.
//!
//! The tool never starts a process itself. It builds an `ExecRequest` for
//! `/bin/bash -c` and renders the result: the exit status, then the tail
//! of the combined output. The executor owns the timeout and the
//! kill, so a command that outlives its budget is reported as `timed_out`
//! rather than as a tool error.

use crate::{parse_args, BASH_DEFAULT_TIMEOUT_SECS, OUTPUT_MAX_CHARS, OUTPUT_MAX_LINES};
use foe_config::{Effect, ToolSpec};
use foe_core::{fitting, CallCtx, ExecRequest, Tool, ToolValue, SUBJECT_MAX};
use serde::Deserialize;
use serde_json::json;
use std::collections::BTreeMap;
use std::fmt::Write;
use std::path::PathBuf;
use std::time::{Duration, Instant};

const SHELL: &str = "/bin/bash";

/// The complete environment of the shell. The executor sets exactly what it
/// is given and inherits nothing, so the shell needs a search path to find
/// programs; `HOME` is the working directory, since the tool has no other
/// writable location.
fn environment(cwd: &std::path::Path) -> BTreeMap<String, String> {
    BTreeMap::from([
        ("PATH".to_owned(), "/usr/local/bin:/usr/bin:/bin".to_owned()),
        ("HOME".to_owned(), cwd.display().to_string()),
        ("LANG".to_owned(), "C.UTF-8".to_owned()),
    ])
}

pub struct Bash {
    spec: ToolSpec,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Args {
    command: String,
    timeout_seconds: Option<u64>,
}

impl Bash {
    pub(crate) fn new() -> Self {
        let kib = OUTPUT_MAX_CHARS / 1024;
        Self {
            spec: ToolSpec {
                name: "bash".into(),
                description: format!(
                    "Run a command with {SHELL} -c in the first read root. Times out after \
                     timeout_seconds (default {BASH_DEFAULT_TIMEOUT_SECS}). Returns stdout, stderr, \
                     the exit code, and the duration; a non-zero exit is an ordinary result. The \
                     rendering opens with the exit status. At most the last {OUTPUT_MAX_LINES} lines \
                     or {kib} KiB of output are collected; the rest is saved to a file named in the \
                     result."
                ),
                instruction: Some(
                    "Use bash to build, test, and run programs. Prefer read, grep, and edit for \
                     inspecting and changing files."
                        .into(),
                ),
                params: json!({
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "Shell command line."},
                        "timeout_seconds": {"type": "integer", "minimum": 1, "description": format!("Wall-clock limit. Default {BASH_DEFAULT_TIMEOUT_SECS}.")}
                    },
                    "required": ["command"],
                    "additionalProperties": false
                }),
                effect: Effect::Execs,
            },
        }
    }
}

#[async_trait::async_trait]
impl Tool for Bash {
    fn spec(&self) -> &ToolSpec {
        &self.spec
    }

    async fn call(&self, args: serde_json::Value, ctx: &CallCtx) -> ToolValue {
        let a: Args = match parse_args("bash", args) {
            Ok(a) => a,
            Err(e) => return e,
        };
        let Some(executor) = ctx.executor.as_ref() else {
            return ToolValue::error("bash: dispatched without an executor handle");
        };
        let Some(cwd) = ctx.reader.as_ref().and_then(|r| r.roots().first().cloned()) else {
            return ToolValue::error("bash: no read root to use as the working directory");
        };
        let mut timeout = Duration::from_secs(a.timeout_seconds.unwrap_or(BASH_DEFAULT_TIMEOUT_SECS));
        if let Some(deadline) = ctx.deadline {
            timeout = timeout.min(deadline.saturating_duration_since(Instant::now()));
        }
        let req = ExecRequest {
            program: PathBuf::from(SHELL),
            args: vec!["-c".into(), a.command.clone()],
            env: environment(&cwd),
            cwd,
            timeout,
            network: false,
            stdin: None,
        };
        let res = match executor.run(req) {
            Ok(r) => r,
            Err(e) => return ToolValue::error(format!("bash: {e}")),
        };
        let stdout = String::from_utf8_lossy(&res.stdout);
        let stderr = String::from_utf8_lossy(&res.stderr);
        let mut combined = stdout.to_string();
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
        // The status leads the rendering so that it survives any later cut
        // of the middle, and the tail of the output, which carries a build
        // or test verdict, survives with it.
        let secs = res.duration.as_secs_f64();
        let mut out = match (res.timed_out, res.exit_code) {
            (true, _) => format!("[timed out after {secs:.1}s; the process group was killed]\n"),
            (false, Some(code)) => format!("[exit {code} in {secs:.2}s]\n"),
            (false, None) => format!("[killed by a signal after {secs:.2}s]\n"),
        };
        // The status the rendering leads with is also what a reader wants
        // beside the command, so it is taken from there rather than rebuilt.
        let status = out.trim().trim_matches(['[', ']']).to_string();
        if truncated {
            let file = ctx.spill_dir.join(format!("{}-bash.txt", ctx.call_id));
            let saved =
                std::fs::create_dir_all(&ctx.spill_dir).and_then(|()| std::fs::write(&file, combined.as_bytes()));
            let _ = match &saved {
                Ok(()) => writeln!(
                    out,
                    "[Showing the last {} of {} lines. Full output saved to {}]",
                    kept,
                    lines.len(),
                    file.display()
                ),
                Err(e) => writeln!(
                    out,
                    "[Showing the last {} of {} lines. Saving the full output failed: {e}]",
                    kept,
                    lines.len()
                ),
            };
            if saved.is_ok() {
                spill = Some(file.display().to_string());
            }
        }
        for l in &lines[lines.len() - kept..] {
            let _ = writeln!(out, "{l}");
        }
        ToolValue::ok(
            json!({
                "command": a.command,
                "exit_code": res.exit_code,
                "timed_out": res.timed_out,
                "duration_ms": res.duration.as_millis() as u64,
                "stdout": stdout,
                "stderr": stderr,
                "truncated": truncated,
                "spill": spill,
            }),
            out,
        )
        // The status must survive the subject's length cap: a long command is
        // cut to leave it room, since the outcome is what a reader scans for.
        .subject({
            let room = SUBJECT_MAX.saturating_sub(status.chars().count() + 4);
            let cut: String = a.command.chars().take(room - 1).collect();
            let cmd = if a.command.chars().count() > room { cut + "…" } else { a.command.clone() };
            format!("{cmd} \u{b7} {status}")
        })
    }
}

#[cfg(test)]
#[path = "bash_test.rs"]
mod tests;
