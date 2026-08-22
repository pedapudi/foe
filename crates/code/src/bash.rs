//! `bash`: one shell command through the `Executor`.
//!
//! The tool never starts a process itself. It builds an `ExecRequest` for
//! `/bin/bash -c` and renders the result: the tail of the combined output,
//! the exit code, and the duration. The executor owns the timeout and the
//! kill, so a command that outlives its budget is reported as `timed_out`
//! rather than as a tool error.

use crate::{parse_args, truncate, BASH_DEFAULT_TIMEOUT_SECS, OUTPUT_MAX_BYTES, OUTPUT_MAX_LINES};
use foe_core::{CallCtx, Effect, ExecRequest, Tool, ToolSpec, ToolValue};
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
        let kib = OUTPUT_MAX_BYTES / 1024;
        Self {
            spec: ToolSpec {
                name: "bash".into(),
                description: format!(
                    "Run a command with {SHELL} -c in the first read root. Times out after \
                     timeout_seconds (default {BASH_DEFAULT_TIMEOUT_SECS}). Returns stdout, stderr, \
                     the exit code, and the duration; a non-zero exit is an ordinary result. Only \
                     the last {OUTPUT_MAX_LINES} lines or {kib} KiB of output are shown; the full \
                     output is then saved to a file named in the result."
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
            verified_program: None,
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
        let lines = truncate::lines(&combined);
        let cut = truncate::tail(&lines, OUTPUT_MAX_LINES, OUTPUT_MAX_BYTES);
        let truncated = cut.len() < lines.len();
        let mut spill = None;
        let mut out = String::new();
        if truncated {
            let file = ctx.spill_dir.join(format!("{}-bash.txt", ctx.call_id));
            let saved =
                std::fs::create_dir_all(&ctx.spill_dir).and_then(|()| std::fs::write(&file, combined.as_bytes()));
            let _ = match &saved {
                Ok(()) => writeln!(
                    out,
                    "[Showing the last {} of {} lines. Full output saved to {}]",
                    cut.len(),
                    lines.len(),
                    file.display()
                ),
                Err(e) => writeln!(
                    out,
                    "[Showing the last {} of {} lines. Saving the full output failed: {e}]",
                    cut.len(),
                    lines.len()
                ),
            };
            if saved.is_ok() {
                spill = Some(file.display().to_string());
            }
        }
        for l in &lines[cut.start..cut.end] {
            let _ = writeln!(out, "{l}");
        }
        let secs = res.duration.as_secs_f64();
        let _ = match (res.timed_out, res.exit_code) {
            (true, _) => write!(out, "[timed out after {secs:.1}s; the process group was killed]"),
            (false, Some(code)) => write!(out, "[exit {code} in {secs:.2}s]"),
            (false, None) => write!(out, "[killed by a signal after {secs:.2}s]"),
        };
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
    }
}

#[cfg(test)]
#[path = "bash_test.rs"]
mod tests;
