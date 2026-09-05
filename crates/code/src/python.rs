//! `compose_tools`: one model-written Python source through an isolated interpreter,
//! composing this episode's tools.
//!
//! The tool writes a foe-owned shim and the source to the interpreter's
//! standard input and serves the shim's dispatch socket: each `call_tool`
//! line becomes one inner dispatch through the episode's [`Composer`],
//! which records it. The interpreter runs under the executor with a policy
//! of its own — read on `/usr`, execute on the interpreter, write on
//! nothing, no network, an empty environment — so the source's only door
//! to the world is that socket. docs/tool-composition.md specifies the source.

use crate::{
    parse_args, BASH_DEFAULT_TIMEOUT_SECS, PYTHON_BIN, PYTHON_DIAGNOSTIC_MAX_CHARS, PYTHON_INNER_CALL_MAX,
    PYTHON_MEMORY_MAX_BYTES, PYTHON_SOURCE_MAX_BYTES,
};
use foe_contract::{Effect, ToolSpec};
use foe_core::sandbox::Policy;
use foe_core::{CallCtx, Composer, ExecRequest, ExecResult, Tool, ToolValue};
use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::fmt::Write;
use std::os::fd::OwnedFd;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::unix::OwnedWriteHalf;

const SHIM: &str = include_str!("python_shim.py");

pub struct Python {
    spec: ToolSpec,
    /// The interpreter's absolute path; [`PYTHON_BIN`] outside tests.
    bin: PathBuf,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Args {
    source: String,
    timeout_seconds: Option<u64>,
}

/// One line the shim sends over the dispatch socket.
#[derive(Deserialize)]
#[serde(untagged)]
enum ShimLine {
    Call { call: InnerCall },
    Done { done: Value },
    Failed { failed: String },
}

#[derive(Deserialize)]
struct InnerCall {
    name: String,
    args: Value,
}

impl Python {
    pub(crate) fn new() -> Self {
        let source_kib = PYTHON_SOURCE_MAX_BYTES / 1024;
        let memory_mib = PYTHON_MEMORY_MAX_BYTES >> 20;
        Self {
            spec: ToolSpec {
                name: foe_core::COMPOSING_TOOL.into(),
                description: format!(
                    "Run Python source in an isolated interpreter whose only capability is \
                     calling this episode's tools. The source defines a zero-argument main() \
                     returning a JSON-serializable value. call_tool(name, args) performs one tool \
                     call and returns {{\"value\": ..., \"is_error\": bool}}; fail(message) ends the \
                     call as an error; compose_tools, block, and return are excluded. Environment, \
                     workspace, and network are absent. Bounds: {memory_mib} MiB memory, \
                     {source_kib} KiB source, {PYTHON_INNER_CALL_MAX} inner calls, timeout_seconds \
                     (default {BASH_DEFAULT_TIMEOUT_SECS})."
                ),
                instruction: Some(
                    "Use compose_tools when later tool calls depend on earlier results, or when \
                     several large results can be reduced before returning to the model. Issue \
                     independent small calls at the top level so they can run concurrently."
                        .into(),
                ),
                params: json!({
                    "type": "object",
                    "properties": {
                        "source": {"type": "string", "description": "Python source defining a zero-argument main()."},
                        "timeout_seconds": {"type": "integer", "minimum": 1, "description": format!("Wall-clock limit. Default {BASH_DEFAULT_TIMEOUT_SECS}.")}
                    },
                    "required": ["source"],
                    "additionalProperties": false
                }),
                effect: Effect::Execs,
            },
            bin: PathBuf::from(PYTHON_BIN),
        }
    }
}

/// The confinement of the interpreter process: read on the interpreter's
/// installation prefix alone, execute on the interpreter, write on
/// nothing, no network. The sandbox's baseline loader, system, and device
/// paths apply as they do to every process; docs/sandbox.md states them
/// and the best-effort caveat.
fn interpreter_policy(bin: &std::path::Path) -> Result<Policy, String> {
    Policy::for_runtime_executable(bin, vec![PathBuf::from("/usr")], "built-in Python interpreter")
}

/// The last [`PYTHON_DIAGNOSTIC_MAX_CHARS`] characters of one output stream.
fn tail(bytes: &[u8]) -> String {
    let text = String::from_utf8_lossy(bytes);
    let over = text.chars().count().saturating_sub(PYTHON_DIAGNOSTIC_MAX_CHARS);
    match over {
        0 => text.into_owned(),
        _ => format!("[{over} characters removed]…{}", text.chars().skip(over).collect::<String>()),
    }
}

async fn respond(write: &mut OwnedWriteHalf, line: Value) -> bool {
    let mut bytes = line.to_string().into_bytes();
    bytes.push(b'\n');
    write.write_all(&bytes).await.is_ok()
}

/// What the serve loop learned: the value `main` returned, or the failure
/// that ended the source, and the derivation tally.
#[derive(Default)]
struct Served {
    done: Option<Value>,
    failed: Option<String>,
    calls: u32,
    errors: u32,
    by_tool: BTreeMap<String, u32>,
}

/// Serves the parent end of the dispatch socket until the shim reports an
/// outcome or the socket closes, which happens when the process ends.
async fn serve(stream: tokio::net::UnixStream, composer: Arc<dyn Composer>) -> Served {
    let (read, mut write) = stream.into_split();
    let mut lines = BufReader::new(read).lines();
    let mut served = Served::default();
    while let Ok(Some(line)) = lines.next_line().await {
        match serde_json::from_str::<ShimLine>(&line) {
            Ok(ShimLine::Call { call }) => {
                if served.calls >= PYTHON_INNER_CALL_MAX {
                    served.failed =
                        Some(format!("compose_tools: the bound of {PYTHON_INNER_CALL_MAX} inner calls was reached"));
                    let _ = respond(&mut write, json!({"fatal": "inner-call bound"})).await;
                    break;
                }
                served.calls += 1;
                *served.by_tool.entry(call.name.clone()).or_insert(0) += 1;
                match composer.call(&call.name, call.args).await {
                    Ok((value, is_error)) => {
                        served.errors += is_error as u32;
                        if !respond(&mut write, json!({"value": value, "is_error": is_error})).await {
                            served.failed =
                                Some("compose_tools: the interpreter closed its dispatch socket mid-call".into());
                            break;
                        }
                    }
                    Err(e) => {
                        served.failed = Some(format!("compose_tools: recording an inner call failed: {e}"));
                        let _ = respond(&mut write, json!({"fatal": "recording failed"})).await;
                        break;
                    }
                }
            }
            Ok(ShimLine::Done { done }) => {
                served.done = Some(done);
                break;
            }
            Ok(ShimLine::Failed { failed }) => {
                served.failed = Some(failed);
                break;
            }
            Err(_) => {
                served.failed = Some("compose_tools: a dispatch line from the interpreter did not parse".into());
                break;
            }
        }
    }
    served
}

/// The outer result from what the serve loop learned and how the process
/// ended. The derivation reports every inner call that completed, whether
/// or not the source did.
fn outcome(served: Served, res: ExecResult) -> ToolValue {
    let (stdout, stderr) = (tail(&res.stdout), tail(&res.stderr));
    let derivation = |complete: bool| json!({ "complete": complete, "inner_calls": served.calls, "errors": served.errors, "by_tool": served.by_tool });
    let diagnostics = |out: &mut String| {
        for (label, text) in [("stdout", &stdout), ("stderr", &stderr)] {
            if !text.is_empty() {
                let _ = write!(out, "\n--- {label} ---\n{text}");
            }
        }
    };
    let (calls, errors) = (served.calls, served.errors);
    if let Some(returned) = served.done {
        let bytes = serde_json::to_vec(&returned).map(|b| b.len()).unwrap_or(0);
        let mut out = format!("[compose_tools: {calls} inner call(s), {errors} error(s)]\n");
        out.push_str(&serde_json::to_string_pretty(&returned).unwrap_or_default());
        diagnostics(&mut out);
        let value = json!({ "returned": returned, "derivation": derivation(true), "stdout": stdout, "stderr": stderr });
        return ToolValue::ok(value, out)
            .subject(format!("compose_tools: {calls} call(s), {errors} error(s), {bytes} bytes returned"));
    }
    let timed_out = res.timed_out;
    let exit_code = res.exit_code;
    let message = served.failed.unwrap_or_else(|| match (timed_out, exit_code) {
        (true, _) => {
            format!("compose_tools: timed out after {:.1}s; the process group was killed", res.duration.as_secs_f64())
        }
        (false, Some(code)) => {
            format!("compose_tools: the interpreter exited with status {code} before main returned")
        }
        (false, None) => "compose_tools: a signal ended the interpreter before main returned".into(),
    });
    let mut out = format!("[compose_tools: failed after {calls} inner call(s)]\n{message}");
    diagnostics(&mut out);
    let value = json!({
        "error": { "message": message, "derivation": derivation(false), "stdout": stdout, "stderr": stderr }
    });
    let subject = message.lines().next().unwrap_or_default().to_string();
    let code = match (timed_out, exit_code) {
        (true, _) => foe_core::ToolFailureCode::TimedOut,
        (false, Some(0)) => foe_core::ToolFailureCode::OperationFailed,
        (false, _) => foe_core::ToolFailureCode::ProcessExit,
    };
    let failure = foe_core::ToolFailure {
        code,
        message,
        retryable: true,
        details: json!({ "exit_code": exit_code, "timed_out": timed_out }),
    };
    ToolValue { value, rendered: Some(out), is_error: true, failure: Some(Box::new(failure)), subject: None }
        .subject(format!("compose_tools: {calls} call(s) · {subject}"))
}

#[async_trait::async_trait]
impl Tool for Python {
    fn spec(&self) -> &ToolSpec {
        &self.spec
    }

    async fn call(&self, args: Value, ctx: &CallCtx) -> ToolValue {
        let a: Args = match parse_args(foe_core::COMPOSING_TOOL, args) {
            Ok(a) => a,
            Err(e) => return e,
        };
        let Some(executor) = ctx.executor.clone() else {
            return ToolValue::unavailable("compose_tools: dispatched without an executor handle");
        };
        let Some(composer) = ctx.composer.clone() else {
            return ToolValue::unavailable(
                "compose_tools: dispatched without a composer; the tool runs only in the agent loop",
            );
        };
        if a.source.len() > PYTHON_SOURCE_MAX_BYTES {
            return ToolValue::failed(
                foe_core::ToolFailureCode::LimitExceeded,
                format!(
                    "compose_tools: the source is {} bytes; the bound is {PYTHON_SOURCE_MAX_BYTES}",
                    a.source.len()
                ),
                true,
                json!({ "limit": "source_bytes", "actual": a.source.len(), "maximum": PYTHON_SOURCE_MAX_BYTES }),
            );
        }
        if !self.bin.is_file() {
            return ToolValue::unavailable(format!("compose_tools: no interpreter at {}", self.bin.display()));
        }
        let mut timeout = Duration::from_secs(a.timeout_seconds.unwrap_or(BASH_DEFAULT_TIMEOUT_SECS));
        if let Some(deadline) = ctx.deadline {
            timeout = timeout.min(deadline.saturating_duration_since(Instant::now()));
        }
        let (parent, child) = match std::os::unix::net::UnixStream::pair() {
            Ok(pair) => pair,
            Err(e) => return ToolValue::error(format!("compose_tools: dispatch socket: {e}")),
        };
        let parent = match parent.set_nonblocking(true).and_then(|()| tokio::net::UnixStream::from_std(parent)) {
            Ok(stream) => stream,
            Err(e) => return ToolValue::error(format!("compose_tools: dispatch socket: {e}")),
        };
        let script = format!(
            "{}\n{}\n\n_foe_run()\n",
            SHIM.replace("__FOE_MEMORY__", &PYTHON_MEMORY_MAX_BYTES.to_string()),
            a.source
        );
        let req = ExecRequest {
            command: self.bin.clone(),
            captured_executable: None,
            args: vec!["-I".into(), "-".into()],
            cwd: PathBuf::from("/"),
            env: BTreeMap::new(),
            timeout,
            network: false,
            stdin: Some(script.into_bytes()),
            policy: match interpreter_policy(&self.bin) {
                Ok(policy) => Some(policy),
                Err(error) => return ToolValue::error(format!("compose_tools: sandbox access: {error}")),
            },
            pass_fds: vec![(3, Arc::new(OwnedFd::from(child)))],
        };
        let run = tokio::task::spawn_blocking(move || executor.run(req));
        // The serve loop ends at the shim's outcome line, or at the
        // socket's close, which follows the process's end: the executor
        // drops the request, and with it the child end, when the run is
        // over. The run therefore always settles after the loop.
        let served = serve(parent, composer).await;
        let res = match run.await {
            Ok(Ok(res)) => res,
            Ok(Err(e)) => return ToolValue::error(format!("compose_tools: {e}")),
            Err(e) => return ToolValue::error(format!("compose_tools: executor task failed: {e}")),
        };
        outcome(served, res)
    }
}

#[cfg(test)]
#[path = "python_test.rs"]
mod tests;
