//! The out-of-tree transport seam: a program that answers model requests.
//!
//! A `model` block of the form `{ "provider": "exec", "exec": "/abs/path",
//! "model": "name" }` names a program. For every model request the program
//! is started once through the episode's [`Executor`], with the network
//! allowed and the model name as its single argument. It reads one JSON
//! object from standard input and writes `model/chunk` lines to standard
//! output in the shape of `docs/protocol.md`:
//!
//! ```text
//! stdin:  {"type":"model/request","request_id":"rq_01","model":"name",
//!          "system":"...","tools":[...],"messages":[...],
//!          "max_output_tokens":null,"options":{...}}
//! stdout: {"type":"model/chunk","request_id":"rq_01","chunk":{"kind":"text","delta":"Hi"}}
//!         {"type":"model/chunk","request_id":"rq_01","chunk":{"kind":"done","stop":"end","usage":{...}}}
//! ```
//!
//! `tools` and `messages` are the `request/header` and `model/request`
//! fields of the log format. `options` is every key of the `model` block
//! other than `provider`, `model`, `max_output_tokens`, and `exec`, so a
//! program can be told where its own credential lives. The program runs
//! under the episode's sandbox narrowed as for a configured executable: it
//! may read the read roots, and it may open TCP connections. Its standard
//! error is quoted in the error when it exits without a final chunk.
//!
//! The chunks arrive after the program exits rather than as it writes
//! them, because the executor captures output whole. Nothing in the record
//! differs; only the latency of the first chunk does.

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::Arc;

use foe_config::ModelConfig;
use foe_core::{Chunk, ExecRequest, Executor, ModelRequestBody, Transport};
use serde::Deserialize;

use crate::TransportError;

const PROVIDER: &str = "exec";

/// Longest run of one request, matching the HTTP client's read timeout.
pub const TIMEOUT: std::time::Duration = crate::http::READ_TIMEOUT;

/// Bytes of standard error quoted in an error message.
const STDERR_QUOTE: usize = 2000;

pub struct ExecTransport {
    program: PathBuf,
    model: String,
    max_output_tokens: Option<u32>,
    options: BTreeMap<String, String>,
    executor: Arc<dyn Executor>,
}

impl ExecTransport {
    pub fn new(config: &ModelConfig, executor: Arc<dyn Executor>) -> Result<ExecTransport, TransportError> {
        let program = PathBuf::from(config.option("exec").unwrap_or_default());
        if !program.is_absolute() {
            return Err(TransportError::Exec { path: program, reason: "is not an absolute path".into() });
        }
        let options =
            config.options.iter().filter(|(k, _)| k.as_str() != "exec").map(|(k, v)| (k.clone(), v.clone())).collect();
        Ok(ExecTransport {
            program,
            model: config.model.clone(),
            max_output_tokens: config.max_output_tokens,
            options,
            executor,
        })
    }

    /// The line written to the program's standard input.
    pub fn request_line(&self, req: &ModelRequestBody) -> String {
        let line = serde_json::json!({
            "type": "model/request",
            "request_id": req.request_id,
            "model": self.model,
            "system": req.system,
            "tools": req.tools,
            "messages": req.messages,
            "max_output_tokens": req.max_output_tokens.or(self.max_output_tokens),
            "options": self.options,
        });
        format!("{line}\n")
    }
}

#[derive(Deserialize)]
struct ChunkLine {
    #[serde(rename = "type")]
    kind: String,
    #[serde(default)]
    request_id: Option<String>,
    chunk: Chunk,
}

#[async_trait::async_trait]
impl Transport for ExecTransport {
    fn route(&self) -> foe_log::ModelRoute {
        foe_log::ModelRoute { provider: PROVIDER.to_string(), model: self.model.clone() }
    }

    async fn stream(&self, req: ModelRequestBody, sink: &mut (dyn foe_core::ChunkSink + Send)) {
        let request = ExecRequest {
            program: self.program.clone(),
            args: vec![self.model.clone()],
            cwd: self.program.parent().map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/")),
            env: BTreeMap::new(),
            timeout: TIMEOUT,
            network: true,
            stdin: Some(self.request_line(&req).into_bytes()),
        };
        let executor = self.executor.clone();
        let joined = tokio::task::spawn_blocking(move || executor.run(request)).await;
        let result = match joined {
            Ok(Ok(result)) => result,
            Ok(Err(e)) => return sink.push(fail(format!("starting {}: {e}", self.program.display()), false)),
            Err(e) => return sink.push(fail(format!("executor worker failed: {e}"), true)),
        };
        let stdout = String::from_utf8_lossy(&result.stdout);
        for line in stdout.lines().filter(|l| !l.trim().is_empty()) {
            let parsed: ChunkLine = match serde_json::from_str(line) {
                Ok(parsed) => parsed,
                Err(e) => return sink.push(fail(format!("output line is not a model/chunk: {e}: {line:.200}"), false)),
            };
            if parsed.kind != "model/chunk" {
                return sink.push(fail(format!("output line has type {:?}; expected model/chunk", parsed.kind), false));
            }
            if parsed.request_id.as_deref().is_some_and(|id| id != req.request_id) {
                return sink.push(fail(format!("output line answers request {:?}", parsed.request_id), false));
            }
            let terminal = matches!(parsed.chunk, Chunk::Done { .. } | Chunk::Error { .. });
            sink.push(parsed.chunk);
            if terminal {
                return;
            }
        }
        let stderr = String::from_utf8_lossy(&result.stderr);
        let tail: String = stderr.chars().rev().take(STDERR_QUOTE).collect::<Vec<_>>().into_iter().rev().collect();
        let tail = tail.trim();
        let suffix = if tail.is_empty() { String::new() } else { format!(": {tail}") };
        sink.push(match (result.timed_out, result.exit_code) {
            (true, _) => fail(format!("{} ran past {} s{suffix}", self.program.display(), TIMEOUT.as_secs()), true),
            (false, Some(0)) => fail(format!("{} exited without a final chunk{suffix}", self.program.display()), true),
            (false, Some(code)) => fail(format!("{} exited with code {code}{suffix}", self.program.display()), false),
            (false, None) => fail(format!("{} was killed{suffix}", self.program.display()), true),
        });
    }
}

fn fail(message: String, retryable: bool) -> Chunk {
    Chunk::Error { message: format!("{PROVIDER}: {message}"), retryable }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_support::scratch_dir;
    use foe_core::{CapError, ContentBlock, ExecResult, Message, StopReason, ToolSchema, Usage};
    use std::os::unix::fs::PermissionsExt;

    /// Runs the request as a plain process, the way the episode's executor
    /// does under its sandbox.
    struct PlainExecutor;

    impl Executor for PlainExecutor {
        fn run(&self, req: ExecRequest) -> Result<ExecResult, CapError> {
            use std::io::Write;
            use std::process::{Command, Stdio};
            let start = std::time::Instant::now();
            let mut child = Command::new(&req.program)
                .args(&req.args)
                .current_dir(&req.cwd)
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .spawn()?;
            if let Some(bytes) = req.stdin {
                // A child may exit before reading; production still settles it after the closed pipe.
                let _ = child.stdin.take().unwrap().write_all(&bytes);
            }
            let output = child.wait_with_output()?;
            Ok(ExecResult {
                exit_code: output.status.code(),
                stdout: output.stdout,
                stderr: output.stderr,
                timed_out: false,
                duration: start.elapsed(),
            })
        }
    }

    fn script(name: &str, body: &str) -> PathBuf {
        let dir = scratch_dir(&format!("exec-{name}"));
        let path = dir.join(name);
        std::fs::write(&path, format!("#!/bin/sh\n{body}\n")).unwrap();
        std::fs::set_permissions(&path, PermissionsExt::from_mode(0o755)).unwrap();
        path
    }

    fn transport(program: &std::path::Path) -> ExecTransport {
        let mut config = ModelConfig::new("exec", "local-model");
        config.options.insert("exec".into(), program.to_string_lossy().into_owned());
        config.options.insert("api_key_file".into(), "/keys/x".into());
        ExecTransport::new(&config, Arc::new(PlainExecutor)).unwrap()
    }

    fn request() -> ModelRequestBody {
        ModelRequestBody {
            request_id: "rq_01".into(),
            system: "Be brief.".into(),
            tools: vec![ToolSchema {
                name: "read".into(),
                description: "Read.".into(),
                parameters: serde_json::json!({ "type": "object" }),
            }],
            messages: vec![Message::User { content: vec![ContentBlock::Text { text: "Hi".into() }] }],
            max_output_tokens: Some(100),
        }
    }

    #[tokio::test]
    async fn the_program_receives_the_request_line_and_its_chunks_are_relayed() {
        // Echoes the request to standard error for the assertion, then answers.
        let program = script(
            "answer",
            r#"cat >&2
echo '{"type":"model/chunk","request_id":"rq_01","chunk":{"kind":"text","delta":"Hello "}}'
echo '{"type":"model/chunk","request_id":"rq_01","chunk":{"kind":"text","delta":"'"$1"'"}}'
echo '{"type":"model/chunk","request_id":"rq_01","chunk":{"kind":"done","stop":"end","usage":{"input":3,"output":2,"cache_read":0}}}'
echo ignored after the final chunk"#,
        );
        let transport = transport(&program);
        assert_eq!(transport.route().provider, "exec");
        let line = transport.request_line(&request());
        let value: serde_json::Value = serde_json::from_str(&line).unwrap();
        assert_eq!(value["type"], "model/request");
        assert_eq!(value["request_id"], "rq_01");
        assert_eq!(value["model"], "local-model");
        assert_eq!(value["system"], "Be brief.");
        assert_eq!(value["tools"][0]["name"], "read");
        assert_eq!(value["messages"][0]["role"], "user");
        assert_eq!(value["max_output_tokens"], 100);
        assert_eq!(value["options"], serde_json::json!({ "api_key_file": "/keys/x" }), "exec is not an option");
        let mut chunks = Vec::new();
        transport.stream(request(), &mut chunks).await;
        assert_eq!(
            chunks,
            vec![
                Chunk::Text { delta: "Hello ".into() },
                Chunk::Text { delta: "local-model".into() },
                Chunk::Done { stop: StopReason::End, usage: Usage { input: 3, output: 2, cache_read: 0 } },
            ]
        );
    }

    #[tokio::test]
    async fn exits_without_a_final_chunk_are_errors_quoting_standard_error() {
        let program = script("crash", "echo 'boom: no key' >&2; exit 3");
        let mut chunks = Vec::new();
        transport(&program).stream(request(), &mut chunks).await;
        assert_eq!(chunks.len(), 1);
        match &chunks[0] {
            Chunk::Error { message, retryable } => {
                assert!(!retryable);
                assert!(message.ends_with("exited with code 3: boom: no key"), "{message}");
            }
            other => panic!("{other:?}"),
        }
        let program = script("silent", "exit 0");
        let mut chunks = Vec::new();
        transport(&program).stream(request(), &mut chunks).await;
        match &chunks[0] {
            Chunk::Error { message, retryable } => {
                assert!(retryable);
                assert!(message.ends_with("exited without a final chunk"), "{message}");
            }
            other => panic!("{other:?}"),
        }
        let program = script("garbage", "echo not json");
        let mut chunks = Vec::new();
        transport(&program).stream(request(), &mut chunks).await;
        match &chunks[0] {
            Chunk::Error { message, retryable } => {
                assert!(!retryable);
                assert!(message.contains("output line is not a model/chunk"), "{message}");
            }
            other => panic!("{other:?}"),
        }
    }

    #[test]
    fn a_relative_program_path_is_refused() {
        let mut config = ModelConfig::new("exec", "m");
        config.options.insert("exec".into(), "bin/model".into());
        let err = ExecTransport::new(&config, Arc::new(PlainExecutor)).err().unwrap().to_string();
        assert_eq!(err, "model.exec: bin/model: is not an absolute path");
    }
}
