//! The Anthropic Messages API, streamed.
//!
//! Request shape: https://docs.anthropic.com/en/api/messages
//! Stream events: https://docs.anthropic.com/en/api/messages-streaming
//! Tool use: https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview
//! Prompt caching: https://platform.claude.com/docs/en/build-with-claude/prompt-caching
//!
//! Mapping from the runtime's messages to the request:
//!
//! | runtime                      | request                                          |
//! |------------------------------|--------------------------------------------------|
//! | `system`                     | top-level `system`, one text block, cache marked |
//! | `tools`                      | `tools` with `input_schema`; last one cache marked |
//! | `Message::User`              | `user` turn of `text` and `image` blocks         |
//! | `Message::Assistant`         | `assistant` turn of `thinking`, `text`, and `tool_use` blocks |
//! | `Message::Tool`              | `tool_result` block in a `user` turn             |
//!
//! Consecutive tool results share one `user` turn, which is how the API
//! expects the results of parallel tool calls. An assistant turn with no
//! blocks at all is omitted because the API rejects empty content.
//!
//! Reasoning blocks are replayed ahead of the turn's other blocks, each as
//! `{"type": "thinking", "thinking": text, "signature": sig}`, or as
//! `{"type": "redacted_thinking", "data": ...}` when the signature carries
//! the [`REDACTED_MARKER`]. The API accepts a replayed block only for the
//! model route that produced it. The runtime fixes `model` for the whole
//! episode in configuration version 1, so every block in the history came
//! from this transport's route and is replayed whenever present. A block
//! without a signature is skipped because the API rejects it.
//!
//! Mapping from stream events to chunks:
//!
//! | event                                        | chunk                     |
//! |----------------------------------------------|---------------------------|
//! | `content_block_delta` / `text_delta`         | `Text`                    |
//! | `content_block_delta` / `thinking_delta`     | `Thinking`                |
//! | `content_block_stop` of a `thinking` block   | `ThinkingSignature`       |
//! | `content_block_stop` of `redacted_thinking`  | `ThinkingSignature`       |
//! | `content_block_start` of a `tool_use` block  | `ToolCallStart`           |
//! | `content_block_delta` / `input_json_delta`   | `ToolCallDelta`           |
//! | `content_block_stop` of a `tool_use` block   | `ToolCallEnd`             |
//! | `message_delta` with `stop_reason`           | `Done`                    |
//! | `error`                                      | `Error`                   |
//!
//! `end_turn`, `stop_sequence`, and `pause_turn` map to `End`; `tool_use`
//! to `Tool`; `max_tokens` to `Length`. `refusal` has no chunk equivalent
//! and is reported as a non-retryable `Error`. Empty deltas are dropped.
//! `signature_delta` fragments accumulate and are emitted once when their
//! block stops. `ping`, `message_stop`, and unknown events are ignored.

use std::collections::BTreeMap;

use foe_core::{Chunk, ContentBlock, Message, ModelRequestBody, StopReason, ToolSchema, Transport, Usage};
use foe_log::ThinkingBlock;
use serde_json::{json, Value};

use crate::{http::Url, sse, Decoder, Exchange, TransportError};

const PROVIDER: &str = "anthropic";
const DEFAULT_BASE_URL: &str = "https://api.anthropic.com";
const API_VERSION: &str = "2023-06-01";
/// `max_tokens` is required by the API. Every current model accepts this
/// value, and a smaller limit truncates a long tool-calling turn, which
/// costs the whole step.
const DEFAULT_MAX_TOKENS: u32 = 64_000;

/// Prefix of the signature recorded for a `redacted_thinking` block. The
/// block has no text and its `data` field is the only thing the API needs
/// back, so the data travels in the signature slot of a `ThinkingBlock`
/// with empty text. The prefix tells a redacted block apart from an
/// ordinary block whose text is empty, which current models produce when
/// reasoning display is omitted. A real signature is base64 and cannot
/// start with this prefix because the prefix contains a colon.
pub const REDACTED_MARKER: &str = "redacted_thinking:";

pub struct Anthropic {
    model: String,
    api_key: String,
    url: Url,
    max_tokens: u32,
}

impl Anthropic {
    /// `base_url` is the origin without a path; `/v1/messages` is appended.
    pub fn new(
        model: &str,
        api_key: String,
        base_url: Option<&str>,
        max_output_tokens: Option<u32>,
    ) -> Result<Self, TransportError> {
        let url = crate::parse_base_url(DEFAULT_BASE_URL, base_url)?.join("/v1/messages");
        Ok(Anthropic {
            model: model.to_string(),
            api_key,
            url,
            max_tokens: max_output_tokens.unwrap_or(DEFAULT_MAX_TOKENS),
        })
    }

    fn exchange(&self, req: &ModelRequestBody) -> Exchange {
        let body = request_body(&self.model, self.max_tokens, req);
        Exchange {
            provider: PROVIDER,
            url: self.url.clone(),
            headers: vec![
                ("x-api-key".to_string(), self.api_key.clone()),
                ("anthropic-version".to_string(), API_VERSION.to_string()),
            ],
            body: serde_json::to_vec(&body).expect("a serde_json::Value serializes"),
        }
    }
}

#[async_trait::async_trait]
impl Transport for Anthropic {
    fn route(&self) -> foe_log::ModelRoute {
        foe_log::ModelRoute { provider: PROVIDER.to_string(), model: self.model.clone() }
    }

    async fn stream(&self, req: ModelRequestBody, sink: &mut (dyn foe_core::ChunkSink + Send)) {
        crate::deliver(self.exchange(&req), Box::new(StreamDecoder::default()), sink).await
    }
}

// ---- request ------------------------------------------------------------------

/// The JSON body for one request. The system prompt and the last tool
/// definition carry `cache_control` so that the prefix the API renders
/// first, tools then system, is cached across steps.
pub fn request_body(model: &str, max_tokens: u32, req: &ModelRequestBody) -> Value {
    let mut body = json!({
        "model": model,
        "max_tokens": max_tokens,
        "stream": true,
        "messages": messages_json(&req.messages),
    });
    if !req.system.trim().is_empty() {
        body["system"] = json!([{ "type": "text", "text": req.system, "cache_control": { "type": "ephemeral" } }]);
    }
    if !req.tools.is_empty() {
        body["tools"] = Value::Array(tools_json(&req.tools));
    }
    body
}

fn tools_json(tools: &[ToolSchema]) -> Vec<Value> {
    let mut out: Vec<Value> = tools
        .iter()
        .map(|t| json!({ "name": t.name, "description": t.description, "input_schema": t.parameters }))
        .collect();
    if let Some(last) = out.last_mut() {
        last["cache_control"] = json!({ "type": "ephemeral" });
    }
    out
}

/// The `messages` array. See the module documentation for the mapping.
pub fn messages_json(messages: &[Message]) -> Vec<Value> {
    let mut out: Vec<Value> = Vec::new();
    for message in messages {
        match message {
            Message::User { content } => push_user(&mut out, content.iter().map(content_block).collect()),
            Message::Assistant { text, tool_calls, thinking } => {
                let mut blocks: Vec<Value> = thinking.iter().filter_map(thinking_block).collect();
                if !text.trim().is_empty() {
                    blocks.push(json!({ "type": "text", "text": text }));
                }
                for call in tool_calls {
                    blocks.push(json!({ "type": "tool_use", "id": call.id, "name": call.name, "input": call.args }));
                }
                if !blocks.is_empty() {
                    out.push(json!({ "role": "assistant", "content": blocks }));
                }
            }
            Message::Tool { call_id, name: _, rendered, is_error } => {
                let mut block = json!({ "type": "tool_result", "tool_use_id": call_id });
                if !rendered.is_empty() {
                    block["content"] = json!(rendered);
                }
                if *is_error {
                    block["is_error"] = json!(true);
                }
                push_user(&mut out, vec![block]);
            }
        }
    }
    out
}

/// Appends blocks to the previous `user` turn when there is one, so that
/// tool results and a following user message form a single turn.
fn push_user(out: &mut Vec<Value>, blocks: Vec<Value>) {
    if let Some(last) = out.last_mut() {
        if last["role"] == "user" {
            if let Some(content) = last["content"].as_array_mut() {
                content.extend(blocks);
                return;
            }
        }
    }
    out.push(json!({ "role": "user", "content": blocks }));
}

/// A reasoning block as the API expects it back. `None` when the block has
/// no signature, which the API would reject.
fn thinking_block(block: &ThinkingBlock) -> Option<Value> {
    let signature = block.signature.as_deref()?;
    Some(match signature.strip_prefix(REDACTED_MARKER) {
        Some(data) => json!({ "type": "redacted_thinking", "data": data }),
        None => json!({ "type": "thinking", "thinking": block.text, "signature": signature }),
    })
}

fn content_block(block: &ContentBlock) -> Value {
    match block {
        ContentBlock::Text { text } => json!({ "type": "text", "text": text }),
        ContentBlock::Image { data, media_type } => json!({
            "type": "image",
            "source": { "type": "base64", "media_type": media_type, "data": data },
        }),
    }
}

// ---- stream -------------------------------------------------------------------

/// Per-request state of the event-to-chunk translation.
#[derive(Default)]
struct StreamDecoder {
    /// Content block index to tool call id, for `tool_use` blocks that have
    /// started and not stopped.
    tool_blocks: BTreeMap<u64, String>,
    /// Content block index to the signature accumulated so far, for
    /// `thinking` and `redacted_thinking` blocks that have started and not
    /// stopped. A redacted block's entry is its data behind
    /// [`REDACTED_MARKER`].
    thinking_blocks: BTreeMap<u64, String>,
    /// Usage counters as the API reports them, each overwritten whenever an
    /// event carries it. `message_start` carries the input side;
    /// `message_delta` carries cumulative output and, on current models,
    /// the input side again.
    input_tokens: u64,
    cache_creation_input_tokens: u64,
    cache_read_input_tokens: u64,
    output_tokens: u64,
}

impl StreamDecoder {
    /// `input` counts every prompt token the model read, cached or not, so
    /// that both providers report the same quantity. The API's
    /// `input_tokens` excludes tokens read from or written to the cache.
    /// https://platform.claude.com/docs/en/build-with-claude/prompt-caching
    fn usage(&self) -> Usage {
        Usage {
            input: self.input_tokens + self.cache_creation_input_tokens + self.cache_read_input_tokens,
            output: self.output_tokens,
            cache_read: self.cache_read_input_tokens,
        }
    }

    fn read_usage(&mut self, usage: &Value) {
        let take = |key: &str, slot: &mut u64| {
            if let Some(n) = usage.get(key).and_then(Value::as_u64) {
                *slot = n;
            }
        };
        take("input_tokens", &mut self.input_tokens);
        take("cache_creation_input_tokens", &mut self.cache_creation_input_tokens);
        take("cache_read_input_tokens", &mut self.cache_read_input_tokens);
        take("output_tokens", &mut self.output_tokens);
    }
}

fn fail(message: String) -> Chunk {
    Chunk::Error { message: format!("{PROVIDER}: {message}"), retryable: false }
}

fn index_of(event: &str, data: &Value) -> Result<u64, Chunk> {
    data.get("index").and_then(Value::as_u64).ok_or_else(|| fail(format!("event {event}: missing index")))
}

impl Decoder for StreamDecoder {
    fn event(&mut self, event: &sse::Event, out: &mut dyn FnMut(Chunk)) {
        let data: Value = match serde_json::from_str(&event.data) {
            Ok(v) => v,
            Err(e) => return out(fail(format!("event {}: data is not JSON: {e}", event.name))),
        };
        // The `event:` line and the `type` field agree; the field is the
        // fallback for a proxy that drops event names.
        let kind = if event.name.is_empty() { data["type"].as_str().unwrap_or("") } else { event.name.as_str() };
        match kind {
            "message_start" => self.read_usage(&data["message"]["usage"]),
            "content_block_start" => {
                let index = match index_of(kind, &data) {
                    Ok(i) => i,
                    Err(chunk) => return out(chunk),
                };
                let block = &data["content_block"];
                match block["type"].as_str().unwrap_or("") {
                    "tool_use" => {
                        let id = block["id"].as_str().unwrap_or("").to_string();
                        let name = block["name"].as_str().unwrap_or("").to_string();
                        if id.is_empty() || name.is_empty() {
                            return out(fail(format!("event {kind}: tool_use block without id or name")));
                        }
                        self.tool_blocks.insert(index, id.clone());
                        out(Chunk::ToolCallStart { id, name });
                    }
                    // A text block may open with content already present.
                    "text" => {
                        if let Some(text) = block["text"].as_str().filter(|t| !t.is_empty()) {
                            out(Chunk::Text { delta: text.to_string() });
                        }
                    }
                    "thinking" => {
                        self.thinking_blocks.insert(index, String::new());
                        if let Some(text) = block["thinking"].as_str().filter(|t| !t.is_empty()) {
                            out(Chunk::Thinking { delta: text.to_string() });
                        }
                    }
                    // The data arrives whole in the start event; nothing of
                    // the block is readable, so no `Thinking` chunk is sent.
                    "redacted_thinking" => {
                        let data = block["data"].as_str().unwrap_or("");
                        self.thinking_blocks.insert(index, format!("{REDACTED_MARKER}{data}"));
                    }
                    _ => {}
                }
            }
            "content_block_delta" => {
                let index = match index_of(kind, &data) {
                    Ok(i) => i,
                    Err(chunk) => return out(chunk),
                };
                let delta = &data["delta"];
                match delta["type"].as_str().unwrap_or("") {
                    "text_delta" => {
                        if let Some(text) = delta["text"].as_str().filter(|t| !t.is_empty()) {
                            out(Chunk::Text { delta: text.to_string() });
                        }
                    }
                    "thinking_delta" => {
                        if let Some(text) = delta["thinking"].as_str().filter(|t| !t.is_empty()) {
                            out(Chunk::Thinking { delta: text.to_string() });
                        }
                    }
                    "signature_delta" => {
                        if let Some(signature) = self.thinking_blocks.get_mut(&index) {
                            signature.push_str(delta["signature"].as_str().unwrap_or(""));
                        }
                    }
                    "input_json_delta" => {
                        let Some(id) = self.tool_blocks.get(&index) else {
                            return out(fail(format!(
                                "event {kind}: input_json_delta for block {index}, which is not an open tool_use block"
                            )));
                        };
                        if let Some(text) = delta["partial_json"].as_str().filter(|t| !t.is_empty()) {
                            out(Chunk::ToolCallDelta { id: id.clone(), delta: text.to_string() });
                        }
                    }
                    _ => {}
                }
            }
            "content_block_stop" => {
                let index = match index_of(kind, &data) {
                    Ok(i) => i,
                    Err(chunk) => return out(chunk),
                };
                if let Some(id) = self.tool_blocks.remove(&index) {
                    out(Chunk::ToolCallEnd { id });
                }
                if let Some(signature) = self.thinking_blocks.remove(&index) {
                    if !signature.is_empty() {
                        out(Chunk::ThinkingSignature { signature });
                    }
                }
            }
            "message_delta" => {
                self.read_usage(&data["usage"]);
                let Some(reason) = data["delta"]["stop_reason"].as_str() else {
                    return;
                };
                let stop = match reason {
                    "end_turn" | "stop_sequence" | "pause_turn" => StopReason::End,
                    "tool_use" => StopReason::Tool,
                    "max_tokens" => StopReason::Length,
                    "refusal" => return out(fail("stop_reason refusal: the model declined to respond".into())),
                    other => return out(fail(format!("stop_reason {other:?} has no chunk equivalent"))),
                };
                out(Chunk::Done { stop, usage: self.usage() });
            }
            "error" => {
                let error = &data["error"];
                let kind = error["type"].as_str().unwrap_or("unknown");
                let detail = error["message"].as_str().unwrap_or("");
                // https://docs.anthropic.com/en/api/errors: these three are
                // transient on the provider's side.
                let retryable = matches!(kind, "overloaded_error" | "api_error" | "rate_limit_error");
                out(Chunk::Error { message: format!("{PROVIDER}: stream error {kind}: {detail}"), retryable });
            }
            _ => {}
        }
    }

    fn end_of_stream(&mut self) -> Chunk {
        Chunk::Error { message: format!("{PROVIDER}: connection closed before message_delta"), retryable: true }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testserver::{Reply, Server};
    use foe_core::ToolCall;

    const TEXT_ONLY: &str = r#"event: message_start
data: {"type":"message_start","message":{"id":"msg_01XFDUDYJgAACzvnptvVoYEL","type":"message","role":"assistant","model":"claude-opus-5","content":[],"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":25,"cache_creation_input_tokens":0,"cache_read_input_tokens":1200,"output_tokens":1}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: ping
data: {"type": "ping"}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"!"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":15}}

event: message_stop
data: {"type":"message_stop"}

"#;

    const TOOL_CALL: &str = r#"event: message_start
data: {"type":"message_start","message":{"id":"msg_014p7gG3wDgGV9EUtLvnow3U","type":"message","role":"assistant","model":"claude-opus-5","content":[],"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":472,"cache_creation_input_tokens":300,"cache_read_input_tokens":0,"output_tokens":2}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"I should read the file first."}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"signature_delta","signature":"EqQBCgIYAhIM1gbcDa9GJwZA2b3hGgxBdjrkzLoky3dl1pkiMOYds"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: content_block_start
data: {"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"I will read it."}}

event: content_block_stop
data: {"type":"content_block_stop","index":1}

event: content_block_start
data: {"type":"content_block_start","index":2,"content_block":{"type":"tool_use","id":"toolu_01T1x1fJ34qAmk2tNTrN7Up6","name":"read","input":{}}}

event: content_block_delta
data: {"type":"content_block_delta","index":2,"delta":{"type":"input_json_delta","partial_json":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":2,"delta":{"type":"input_json_delta","partial_json":"{\"path\": \"/src"}}

event: content_block_delta
data: {"type":"content_block_delta","index":2,"delta":{"type":"input_json_delta","partial_json":"/lib.rs\"}"}}

event: content_block_stop
data: {"type":"content_block_stop","index":2}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"tool_use","stop_sequence":null},"usage":{"input_tokens":472,"cache_creation_input_tokens":300,"cache_read_input_tokens":0,"output_tokens":89}}

event: message_stop
data: {"type":"message_stop"}

"#;

    const LENGTH: &str = r#"event: message_start
data: {"type":"message_start","message":{"id":"msg_01","type":"message","role":"assistant","model":"claude-opus-5","content":[],"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":10,"output_tokens":1}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Once upon a"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"max_tokens","stop_sequence":null},"usage":{"output_tokens":4}}

event: message_stop
data: {"type":"message_stop"}

"#;

    fn request() -> ModelRequestBody {
        ModelRequestBody {
            request_id: "rq_01".into(),
            system: "You are a coding agent.".into(),
            tools: vec![
                ToolSchema {
                    name: "read".into(),
                    description: "Read a file.".into(),
                    parameters: json!({ "type": "object", "properties": { "path": { "type": "string" } } }),
                },
                ToolSchema {
                    name: "grep".into(),
                    description: "Search.".into(),
                    parameters: json!({ "type": "object" }),
                },
            ],
            messages: vec![Message::User { content: vec![ContentBlock::Text { text: "Fix the test.".into() }] }],
            max_output_tokens: None,
        }
    }

    async fn run(reply: Reply) -> (Vec<Chunk>, Server) {
        let server = Server::start(vec![reply]);
        let transport = Anthropic::new("claude-opus-5", "sk-ant-test".into(), Some(&server.base()), None).unwrap();
        let mut chunks = Vec::new();
        transport.stream(request(), &mut chunks).await;
        (chunks, server)
    }

    fn text(s: &str) -> Chunk {
        Chunk::Text { delta: s.into() }
    }

    #[tokio::test]
    async fn text_only_response() {
        let (chunks, server) = run(Reply::sse(TEXT_ONLY)).await;
        assert_eq!(
            chunks,
            vec![
                text("Hello"),
                text("!"),
                Chunk::Done { stop: StopReason::End, usage: Usage { input: 1225, output: 15, cache_read: 1200 } },
            ]
        );
        let seen = server.requests();
        assert_eq!(seen.len(), 1);
        assert_eq!(seen[0].method, "POST");
        assert_eq!(seen[0].path, "/v1/messages");
        assert_eq!(seen[0].header("x-api-key"), Some("sk-ant-test"));
        assert_eq!(seen[0].header("anthropic-version"), Some("2023-06-01"));
        assert_eq!(seen[0].header("accept"), Some("text/event-stream"));
        let body = seen[0].json();
        assert_eq!(body["model"], "claude-opus-5");
        assert_eq!(body["stream"], true);
        assert_eq!(body["max_tokens"], 64_000);
        assert_eq!(body["system"][0]["text"], "You are a coding agent.");
        assert_eq!(body["system"][0]["cache_control"]["type"], "ephemeral");
        assert_eq!(body["tools"][0]["name"], "read");
        assert_eq!(body["tools"][0]["input_schema"]["type"], "object");
        assert!(body["tools"][0].get("cache_control").is_none());
        assert_eq!(body["tools"][1]["cache_control"]["type"], "ephemeral");
        assert_eq!(
            body["messages"],
            json!([{ "role": "user", "content": [{ "type": "text", "text": "Fix the test." }] }])
        );
    }

    #[tokio::test]
    async fn tool_call_split_across_deltas() {
        let (chunks, _server) = run(Reply::sse(TOOL_CALL)).await;
        let id = "toolu_01T1x1fJ34qAmk2tNTrN7Up6";
        assert_eq!(
            chunks,
            vec![
                Chunk::Thinking { delta: "I should read the file first.".into() },
                Chunk::ThinkingSignature { signature: "EqQBCgIYAhIM1gbcDa9GJwZA2b3hGgxBdjrkzLoky3dl1pkiMOYds".into() },
                text("I will read it."),
                Chunk::ToolCallStart { id: id.into(), name: "read".into() },
                Chunk::ToolCallDelta { id: id.into(), delta: "{\"path\": \"/src".into() },
                Chunk::ToolCallDelta { id: id.into(), delta: "/lib.rs\"}".into() },
                Chunk::ToolCallEnd { id: id.into() },
                Chunk::Done { stop: StopReason::Tool, usage: Usage { input: 772, output: 89, cache_read: 0 } },
            ]
        );
    }

    const REDACTED: &str = r#"event: message_start
data: {"type":"message_start","message":{"id":"msg_02","type":"message","role":"assistant","model":"claude-opus-5","content":[],"stop_reason":null,"stop_sequence":null,"usage":{"input_tokens":40,"output_tokens":1}}}

event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"redacted_thinking","data":"EmwKAhgBEgy3va3pzix/LafPsn4aDFIT2Xlxh0L5L8rLJhIw"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: content_block_start
data: {"type":"content_block_start","index":1,"content_block":{"type":"thinking","thinking":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{"type":"signature_delta","signature":"AAAA"}}

event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{"type":"signature_delta","signature":"BBBB"}}

event: content_block_stop
data: {"type":"content_block_stop","index":1}

event: content_block_start
data: {"type":"content_block_start","index":2,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":2,"delta":{"type":"text_delta","text":"Hidden."}}

event: content_block_stop
data: {"type":"content_block_stop","index":2}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":30}}

event: message_stop
data: {"type":"message_stop"}

"#;

    /// A redacted block yields its data behind the marker; a block whose
    /// reasoning text is omitted yields only its signature, assembled from
    /// every fragment.
    #[tokio::test]
    async fn redacted_and_text_free_thinking_blocks() {
        let (chunks, _server) = run(Reply::sse(REDACTED)).await;
        assert_eq!(
            chunks,
            vec![
                Chunk::ThinkingSignature {
                    signature: "redacted_thinking:EmwKAhgBEgy3va3pzix/LafPsn4aDFIT2Xlxh0L5L8rLJhIw".into()
                },
                Chunk::ThinkingSignature { signature: "AAAABBBB".into() },
                text("Hidden."),
                Chunk::Done { stop: StopReason::End, usage: Usage { input: 40, output: 30, cache_read: 0 } },
            ]
        );
    }

    #[tokio::test]
    async fn length_stop() {
        let (chunks, _server) = run(Reply::sse(LENGTH)).await;
        assert_eq!(
            chunks,
            vec![
                text("Once upon a"),
                Chunk::Done { stop: StopReason::Length, usage: Usage { input: 10, output: 4, cache_read: 0 } },
            ]
        );
    }

    #[tokio::test]
    async fn rate_limited_with_retry_after() {
        let body = r#"{"type":"error","error":{"type":"rate_limit_error","message":"This request would exceed the rate limit for your organization."}}"#;
        let (chunks, _server) = run(Reply::full(429, body).with_header("retry-after", "7")).await;
        assert_eq!(
            chunks,
            vec![Chunk::Error {
                message: "anthropic: HTTP 429: rate_limit_error: This request would exceed the rate limit for your organization. retry_after_ms=7000".into(),
                retryable: true,
            }]
        );
    }

    #[tokio::test]
    async fn overloaded_is_retryable_and_bad_request_is_not() {
        let body = r#"{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}"#;
        let (chunks, _server) = run(Reply::full(529, body)).await;
        assert_eq!(
            chunks,
            vec![Chunk::Error { message: "anthropic: HTTP 529: overloaded_error: Overloaded".into(), retryable: true }]
        );
        let body = r#"{"type":"error","error":{"type":"invalid_request_error","message":"max_tokens: too large"}}"#;
        let (chunks, _server) = run(Reply::full(400, body)).await;
        assert_eq!(
            chunks,
            vec![Chunk::Error {
                message: "anthropic: HTTP 400: invalid_request_error: max_tokens: too large".into(),
                retryable: false,
            }]
        );
    }

    #[tokio::test]
    async fn disconnect_mid_stream() {
        // Eleven events: through the first non-empty input_json_delta.
        let (chunks, _server) = run(Reply::sse_cut_after(TOOL_CALL, 11)).await;
        let id = "toolu_01T1x1fJ34qAmk2tNTrN7Up6";
        assert_eq!(
            chunks,
            vec![
                Chunk::Thinking { delta: "I should read the file first.".into() },
                Chunk::ThinkingSignature { signature: "EqQBCgIYAhIM1gbcDa9GJwZA2b3hGgxBdjrkzLoky3dl1pkiMOYds".into() },
                text("I will read it."),
                Chunk::ToolCallStart { id: id.into(), name: "read".into() },
                Chunk::ToolCallDelta { id: id.into(), delta: "{\"path\": \"/src".into() },
                Chunk::Error {
                    message: "anthropic: reading response body: connection closed before the body was complete".into(),
                    retryable: true,
                },
            ]
        );
    }

    #[tokio::test]
    async fn connection_closed_before_response_is_retryable() {
        let (chunks, _server) = run(Reply::close_immediately()).await;
        assert_eq!(chunks.len(), 1);
        match &chunks[0] {
            Chunk::Error { message, retryable } => {
                assert!(*retryable);
                assert!(message.starts_with("anthropic: io: "), "{message}");
            }
            other => panic!("{other:?}"),
        }
    }

    #[tokio::test]
    async fn stream_error_event_and_refusal() {
        let transcript = "event: message_start\ndata: {\"type\":\"message_start\",\"message\":{\"usage\":{\"input_tokens\":3}}}\n\nevent: error\ndata: {\"type\":\"error\",\"error\":{\"type\":\"overloaded_error\",\"message\":\"Overloaded\"}}\n\n";
        let (chunks, _server) = run(Reply::sse(transcript)).await;
        assert_eq!(
            chunks,
            vec![Chunk::Error {
                message: "anthropic: stream error overloaded_error: Overloaded".into(),
                retryable: true
            }]
        );
        let transcript = "event: message_delta\ndata: {\"type\":\"message_delta\",\"delta\":{\"stop_reason\":\"refusal\"},\"usage\":{\"output_tokens\":0}}\n\n";
        let (chunks, _server) = run(Reply::sse(transcript)).await;
        assert_eq!(
            chunks,
            vec![Chunk::Error {
                message: "anthropic: stop_reason refusal: the model declined to respond".into(),
                retryable: false,
            }]
        );
    }

    #[tokio::test]
    async fn clean_end_without_message_delta_is_retryable() {
        let transcript =
            "event: message_start\ndata: {\"type\":\"message_start\",\"message\":{\"usage\":{\"input_tokens\":3}}}\n\n";
        let (chunks, _server) = run(Reply::sse(transcript)).await;
        assert_eq!(
            chunks,
            vec![Chunk::Error { message: "anthropic: connection closed before message_delta".into(), retryable: true }]
        );
    }

    #[test]
    fn messages_map_every_role() {
        let messages = vec![
            Message::User {
                content: vec![
                    ContentBlock::Text { text: "Look at this.".into() },
                    ContentBlock::Image { data: "aGk=".into(), media_type: "image/png".into() },
                ],
            },
            Message::Assistant {
                text: "Reading.".into(),
                thinking: vec![
                    ThinkingBlock { text: "Plan first.".into(), signature: Some("c2ln".into()) },
                    ThinkingBlock { text: String::new(), signature: Some("redacted_thinking:RUJBRg==".into()) },
                    ThinkingBlock { text: "unsigned, skipped".into(), signature: None },
                ],
                tool_calls: vec![
                    ToolCall { id: "toolu_1".into(), name: "read".into(), args: json!({ "path": "/a" }) },
                    ToolCall { id: "toolu_2".into(), name: "read".into(), args: json!({ "path": "/b" }) },
                ],
            },
            Message::Tool {
                call_id: "toolu_1".into(),
                name: "read".into(),
                rendered: "contents of a".into(),
                is_error: false,
            },
            Message::Tool {
                call_id: "toolu_2".into(),
                name: "read".into(),
                rendered: "no such file".into(),
                is_error: true,
            },
            Message::User { content: vec![ContentBlock::Text { text: "Hurry.".into() }] },
            Message::Assistant { text: String::new(), tool_calls: vec![], thinking: vec![] },
            Message::Assistant { text: "Done.".into(), tool_calls: vec![], thinking: vec![] },
        ];
        assert_eq!(
            messages_json(&messages),
            json!([
                { "role": "user", "content": [
                    { "type": "text", "text": "Look at this." },
                    { "type": "image", "source": { "type": "base64", "media_type": "image/png", "data": "aGk=" } },
                ]},
                { "role": "assistant", "content": [
                    { "type": "thinking", "thinking": "Plan first.", "signature": "c2ln" },
                    { "type": "redacted_thinking", "data": "RUJBRg==" },
                    { "type": "text", "text": "Reading." },
                    { "type": "tool_use", "id": "toolu_1", "name": "read", "input": { "path": "/a" } },
                    { "type": "tool_use", "id": "toolu_2", "name": "read", "input": { "path": "/b" } },
                ]},
                { "role": "user", "content": [
                    { "type": "tool_result", "tool_use_id": "toolu_1", "content": "contents of a" },
                    { "type": "tool_result", "tool_use_id": "toolu_2", "content": "no such file", "is_error": true },
                    { "type": "text", "text": "Hurry." },
                ]},
                { "role": "assistant", "content": [{ "type": "text", "text": "Done." }] },
            ])
            .as_array()
            .unwrap()
            .clone()
        );
    }

    #[test]
    fn request_omits_empty_system_and_tools_and_honors_max_tokens() {
        let mut req = request();
        req.system = String::new();
        req.tools.clear();
        let body = request_body("claude-opus-5", 4096, &req);
        assert!(body.get("system").is_none());
        assert!(body.get("tools").is_none());
        assert_eq!(body["max_tokens"], 4096);
        let transport = Anthropic::new("m", "k".into(), Some("https://proxy.example/anthropic/"), Some(1000)).unwrap();
        assert_eq!(transport.url.path, "/anthropic/v1/messages");
        assert_eq!(transport.max_tokens, 1000);
        assert_eq!(Anthropic::new("m", "k".into(), None, None).unwrap().url.host, "api.anthropic.com");
    }

    #[test]
    fn decoder_reports_protocol_violations_as_non_retryable() {
        let mut decoder = StreamDecoder::default();
        let mut chunks = Vec::new();
        let event = sse::Event {
            name: "content_block_delta".into(),
            data: r#"{"type":"content_block_delta","index":4,"delta":{"type":"input_json_delta","partial_json":"{"}}"#
                .into(),
        };
        decoder.event(&event, &mut |c| chunks.push(c));
        let event = sse::Event { name: "message_start".into(), data: "not json".into() };
        decoder.event(&event, &mut |c| chunks.push(c));
        assert_eq!(chunks.len(), 2);
        for chunk in &chunks {
            match chunk {
                Chunk::Error { message, retryable } => {
                    assert!(!retryable);
                    assert!(message.starts_with("anthropic: event "), "{message}");
                }
                other => panic!("{other:?}"),
            }
        }
    }
}
