//! `session`: a process that survives the call that started it.
//!
//! One tool drives every session through the `Sessions` handle in
//! `CallCtx`; the tool never touches a process itself. `start` runs a
//! command line under exactly the policy and fixed environment a `bash`
//! call receives, in its own process group, and returns a session id.
//! `poll` returns the output since the last poll, bounded by the same
//! collection-and-spill rule as `bash` output. `write` sends bytes to
//! standard input, `signal` sends a named signal to the process group, and
//! `stop` ends the group. The runtime stops an episode-lifetime session at
//! settlement. With explicit authority, a task-lifetime session is released
//! to the environment that owns the foe invocation.

use crate::{parse_args, shell_environment, OUTPUT_MAX_CHARS, OUTPUT_MAX_LINES, SESSION_MAX_ALIVE, SHELL};

use foe_config::{Effect, ToolSpec};
use foe_core::exec::TERM_GRACE;
use foe_core::session::{subject, SESSION_TOOL};
use foe_core::{fitting, CallCtx, SessionLifetime, SessionOutput, SessionRequest, SessionStatus, Tool, ToolValue};
use serde::Deserialize;
use serde_json::json;
use std::fmt::Write;
use std::path::Path;

pub struct Session {
    spec: ToolSpec,
}

#[derive(Deserialize, Clone, Copy, PartialEq)]
#[serde(rename_all = "lowercase")]
enum Action {
    Start,
    Poll,
    Write,
    Signal,
    Stop,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Args {
    action: Action,
    command: Option<String>,
    session: Option<u64>,
    input: Option<String>,
    signal: Option<String>,
    lifetime: Option<SessionLifetime>,
}

impl Session {
    pub(crate) fn new() -> Self {
        let grace = TERM_GRACE.as_secs();
        Self {
            spec: ToolSpec {
                name: SESSION_TOOL.into(),
                description: format!(
                    "A process across calls. `start` runs `command` with {SHELL} -c under the `bash` \
                     environment, network closed, in its own process group; at most {SESSION_MAX_ALIVE} \
                     may be alive. `poll` returns new bounded output and final status. `write` sends \
                     input; `signal` signals the group; `stop` terminates, escalating after {grace}s. \
                     Default `episode` lifetime ends at settlement. `task` transfers ownership to the \
                     task environment and requires grants.task_session."
                ),
                instruction: Some(
                    "Use session for a process that must outlive one bash call, such as a server under \
                     test: start it, poll for its output, and stop it when it is no longer needed. \
                     Use task lifetime only when the process must remain after foe exits and the \
                     enclosing task environment owns cleanup. There is no terminal."
                        .into(),
                ),
                params: json!({
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["start", "poll", "write", "signal", "stop"]},
                        "command": {"type": "string", "description": "Shell command line; `start` only."},
                        "session": {"type": "integer", "minimum": 1, "description": "Session id from `start`; every action except `start`."},
                        "input": {"type": "string", "description": "Bytes for standard input; `write` only."},
                        "signal": {"type": "string", "description": "Signal name such as SIGINT; `signal` only."},
                        "lifetime": {"type": "string", "enum": ["episode", "task"], "description": "Process ownership for `start`; default `episode`. `task` requires grants.task_session."}
                    },
                    "required": ["action"],
                    "additionalProperties": false
                }),
                effect: Effect::Execs,
            },
        }
    }
}

#[async_trait::async_trait]
impl Tool for Session {
    fn spec(&self) -> &ToolSpec {
        &self.spec
    }

    async fn call(&self, args: serde_json::Value, ctx: &CallCtx) -> ToolValue {
        let a: Args = match parse_args(SESSION_TOOL, args) {
            Ok(a) => a,
            Err(e) => return e,
        };
        let Some(sessions) = ctx.sessions.as_ref() else {
            return ToolValue::error("session: dispatched without a sessions handle");
        };
        if a.action != Action::Start && a.lifetime.is_some() {
            return ToolValue::error("session: `lifetime` applies only to `start`");
        }
        let sid = match (a.action, a.session) {
            (Action::Start, _) => 0,
            (_, Some(id)) => id,
            (_, None) => return ToolValue::error("session: `session` names the session id"),
        };
        match a.action {
            Action::Start => {
                let Some(command) = a.command else {
                    return ToolValue::error("session: `start` requires `command`");
                };
                let Some(cwd) = ctx.reader.as_ref().and_then(|r| r.roots().first().cloned()) else {
                    return ToolValue::error("session: no read root to use as the working directory");
                };
                let lifetime = a.lifetime.unwrap_or(SessionLifetime::Episode);
                let req = SessionRequest {
                    name: display_name(&command),
                    program: SHELL.into(),
                    args: vec!["-c".into(), command.clone()],
                    env: shell_environment(&cwd),
                    cwd,
                    lifetime,
                };
                match sessions.start(req) {
                    Ok(status) => {
                        let lifetime_name = match lifetime {
                            SessionLifetime::Episode => "episode",
                            SessionLifetime::Task => "task",
                        };
                        let qualifier = if lifetime == SessionLifetime::Task { "task lifetime " } else { "" };
                        let line = format!("session {}: {} \u{b7} {qualifier}started", status.id, status.name);
                        ToolValue::ok(
                            json!({
                                "session": status.id, "name": status.name, "command": command,
                                "lifetime": lifetime_name,
                            }),
                            format!("[{line}]\n"),
                        )
                        .subject(line)
                    }
                    Err(e) => ToolValue::error(format!("session: {e}")),
                }
            }
            Action::Poll => match sessions.take_output(sid) {
                Ok((status, output)) => render_poll(ctx, &status, &output),
                Err(e) => ToolValue::error(format!("session: {e}")),
            },
            Action::Write => {
                let Some(input) = a.input else {
                    return ToolValue::error("session: `write` requires `input`");
                };
                match sessions.write_stdin(sid, input.as_bytes()) {
                    Ok(status) => {
                        let line =
                            format!("session {}: {} \u{b7} {} bytes to stdin", status.id, status.name, input.len());
                        ToolValue::ok(json!({ "session": sid, "bytes": input.len() }), format!("[{line}]\n"))
                            .subject(line)
                    }
                    Err(e) => ToolValue::error(format!("session: {e}")),
                }
            }
            Action::Signal => {
                let Some(signal) = a.signal else {
                    return ToolValue::error("session: `signal` requires `signal`");
                };
                let name = normalize(&signal);
                match sessions.signal(sid, &name) {
                    Ok(status) => {
                        let line = format!("session {}: {} \u{b7} {name} sent", status.id, status.name);
                        ToolValue::ok(json!({ "session": sid, "signal": name }), format!("[{line}]\n")).subject(line)
                    }
                    Err(e) => ToolValue::error(format!("session: {e}")),
                }
            }
            Action::Stop => match sessions.stop(sid) {
                Ok(status) => {
                    let line = subject(&status);
                    ToolValue::ok(
                        json!({
                            "session": sid, "name": status.name,
                            "exit_code": status.exit_code, "seconds": status.seconds,
                        }),
                        format!("[{line}]\n"),
                    )
                    .subject(line)
                }
                Err(e) => ToolValue::error(format!("session: {e}")),
            },
        }
    }
}

/// The status line first, then the tail of the new output: the `bash`
/// shape, so the status survives any later cut of the middle. On
/// truncation the whole text is saved and a notice names the file.
fn render_poll(ctx: &CallCtx, status: &SessionStatus, output: &SessionOutput) -> ToolValue {
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
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
    let mut out = format!("[{}]\n", subject(status));
    if truncated {
        let file = ctx.spill_dir.join(format!("{}-session.txt", ctx.call_id));
        let saved = std::fs::create_dir_all(&ctx.spill_dir).and_then(|()| std::fs::write(&file, combined.as_bytes()));
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
    let line_count = stdout.lines().count() + stderr.lines().count();
    ToolValue::ok(
        json!({
            "session": status.id, "name": status.name, "alive": status.alive,
            "exit_code": status.exit_code, "seconds": status.seconds,
            "stdout": stdout, "stderr": stderr, "truncated": truncated, "spill": spill,
        }),
        out,
    )
    .subject(match status.alive {
        true => format!("{}, {line_count} lines", subject(status)),
        false => subject(status),
    })
}

/// The first word of the command line, reduced to its file name: what the
/// subject calls the process, `postgres` for `postgres -D data`.
fn display_name(command: &str) -> String {
    command
        .split_whitespace()
        .next()
        .map(|token| {
            Path::new(token).file_name().map_or_else(|| token.to_string(), |n| n.to_string_lossy().into_owned())
        })
        .unwrap_or_else(|| "bash".into())
}

/// `int`, `INT`, and `SIGINT` all name SIGINT; the supervisor parses the
/// `SIG` form.
fn normalize(signal: &str) -> String {
    let upper = signal.trim().to_ascii_uppercase();
    match upper.starts_with("SIG") {
        true => upper,
        false => format!("SIG{upper}"),
    }
}

#[cfg(test)]
#[path = "session_test.rs"]
mod tests;
