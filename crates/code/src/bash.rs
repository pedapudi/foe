//! `bash`: one shell command through the `Executor`.
//!
//! The tool never starts a process itself. It builds an `ExecRequest` for
//! `/bin/bash -c` and renders the result: the exit status, then the tail
//! of the combined output. The executor owns the timeout and the
//! kill, so a command that outlives its budget is reported as `timed_out`
//! rather than as a tool error.

use crate::{
    parse_args, process_output, shell_environment, BASH_DEFAULT_TIMEOUT_SECS, OUTPUT_MAX_CHARS, OUTPUT_MAX_LINES,
    SHELL, SHELL_COMMAND_NUL_ERROR,
};
use foe_contract::{Effect, ToolSpec};
use foe_core::{CallCtx, ExecRequest, Tool, ToolValue, SUBJECT_MAX};
use serde::Deserialize;
use serde_json::json;
use std::path::PathBuf;
use std::time::{Duration, Instant};

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
        if a.command.contains('\0') {
            return ToolValue::invalid(format!("bash: {SHELL_COMMAND_NUL_ERROR}"));
        }
        let Some(executor) = ctx.executor.as_ref() else {
            return ToolValue::unavailable("bash: dispatched without an executor handle");
        };
        let Some(cwd) = ctx.reader.as_ref().and_then(|r| r.roots().first().cloned()) else {
            return ToolValue::unavailable("bash: no read root to use as the working directory");
        };
        let mut timeout = Duration::from_secs(a.timeout_seconds.unwrap_or(BASH_DEFAULT_TIMEOUT_SECS));
        if let Some(deadline) = ctx.deadline {
            timeout = timeout.min(deadline.saturating_duration_since(Instant::now()));
        }
        let req = ExecRequest {
            command: PathBuf::from(SHELL),
            captured_executable: None,
            args: vec!["-c".into(), a.command.clone()],
            env: shell_environment(&cwd),
            cwd,
            timeout,
            network: false,
            stdin: None,
            policy: None,
            pass_fds: Vec::new(),
        };
        let res = match executor.run(req) {
            Ok(r) => r,
            Err(e) => return ToolValue::from_cap_error("bash", e),
        };
        let secs = res.duration.as_secs_f64();
        let status = match (res.timed_out, res.exit_code) {
            (true, _) => format!("timed out after {secs:.1}s; the process group was killed"),
            (false, Some(code)) => format!("exit {code} in {secs:.2}s"),
            (false, None) => format!("killed by a signal after {secs:.2}s"),
        };
        let output = process_output::render(ctx, &status, res.exit_code, &res.stdout, &res.stderr, "bash");
        ToolValue::ok(
            json!({
                "command": a.command,
                "exit_code": res.exit_code,
                "timed_out": res.timed_out,
                "duration_ms": res.duration.as_millis() as u64,
                "stdout": output.stdout,
                "stderr": output.stderr,
                "truncated": output.truncated,
                "spill": output.spill, "permission_denial": output.permission_denial.then_some("possible"),
            }),
            output.rendered,
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
