//! The OpenAI Responses API, streamed. Serves OpenAI's platform and the
//! ChatGPT Codex backend, which speak the same request and event shapes.
//!
//! Request shape: https://platform.openai.com/docs/api-reference/responses/create
//! Stream events: https://platform.openai.com/docs/api-reference/responses-streaming
//! Reasoning replay: https://platform.openai.com/docs/guides/reasoning#keeping-reasoning-items-in-context
//! Codex backend: https://chatgpt.com/backend-api/codex/responses, the
//! endpoint the Codex command-line tool calls with a ChatGPT login.
//!
//! Mapping from the runtime's messages to the request:
//!
//! | runtime              | request                                                        |
//! |----------------------|----------------------------------------------------------------|
//! | `system`             | `instructions`                                                 |
//! | `tools`              | `tools` of type `function`, `strict` off                       |
//! | `Message::User`      | `user` input item of `input_text` and `input_image` parts      |
//! | `Message::Assistant` | a `reasoning` item per signed thinking block, an `assistant` message for the text, a `function_call` item per call |
//! | `Message::Tool`      | a `function_call_output` item                                  |
//!
//! Every request sends `store: false`, so the provider keeps nothing
//! between requests and the log is the only history. Reasoning is then
//! replayed through `include: ["reasoning.encrypted_content"]`: each
//! reasoning item arrives with an `encrypted_content` token, which travels
//! in the signature slot of a `ThinkingBlock` as `<item id> <token>` and
//! returns as a `reasoning` item ahead of the turn's other items. A block
//! without a signature is not replayed. The API has no field for a failed
//! tool result, so `is_error` is not transmitted; the rendered text carries
//! it.
//!
//! Mapping from stream events to chunks:
//!
//! | event                                           | chunk               |
//! |-------------------------------------------------|---------------------|
//! | `response.output_text.delta`                    | `Text`              |
//! | `response.reasoning_summary_text.delta`, `response.reasoning_text.delta` | `Thinking` |
//! | `response.output_item.done` of a `reasoning` item | `ThinkingSignature` |
//! | `response.output_item.added` of a `function_call` | `ToolCallStart`   |
//! | `response.function_call_arguments.delta`        | `ToolCallDelta`     |
//! | `response.output_item.done` of a `function_call` | `ToolCallEnd`      |
//! | `response.completed`                            | `Done`              |
//! | `response.incomplete`                           | `Done` with `Length`, or `Error` |
//! | `response.failed`, `error`                      | `Error`             |
//!
//! A completed response with at least one function call stops with `Tool`
//! and with `End` otherwise. An incomplete response stops with `Length`
//! when its reason is `max_output_tokens`; `content_filter` has no chunk
//! equivalent and is a non-retryable `Error`, as is a `refusal` part.
//! Usage is read from the final response object: `input_tokens` already
//! counts cached tokens, and `input_tokens_details.cached_tokens` is the
//! cache read.

use std::collections::BTreeMap;

use foe_core::{Chunk, ContentBlock, Message, ModelRequestBody, StopReason, ToolSchema, Usage};
use foe_log::ThinkingBlock;
use serde_json::{json, Value};

use super::{fail, Decoder, Format};
use crate::sse;

pub struct Responses {
    provider: &'static str,
    model: String,
    max_tokens: Option<u32>,
    /// `reasoning.effort`, sent only when configured, because models
    /// without reasoning reject the field.
    reasoning_effort: Option<String>,
    service_tier: Option<String>,
}

impl Responses {
    pub fn new(
        provider: &'static str,
        model: String,
        max_output_tokens: Option<u32>,
        reasoning_effort: Option<String>,
        service_tier: Option<String>,
    ) -> Responses {
        Responses { provider, model, max_tokens: max_output_tokens, reasoning_effort, service_tier }
    }
}

impl Format for Responses {
    fn body(&self, req: &ModelRequestBody) -> Value {
        // The ChatGPT Codex backend shares the Responses event format but
        // rejects the public API's per-request output cap.
        let max_tokens = (self.provider != "openai-codex").then(|| req.max_output_tokens.or(self.max_tokens)).flatten();
        request_body(&self.model, max_tokens, self.reasoning_effort.as_deref(), self.service_tier.as_deref(), req)
    }

    fn decoder(&self) -> Box<dyn Decoder> {
        Box::new(StreamDecoder { provider: self.provider, ..Default::default() })
    }
}

// ---- request ------------------------------------------------------------------

pub fn request_body(
    model: &str,
    max_tokens: Option<u32>,
    reasoning_effort: Option<&str>,
    service_tier: Option<&str>,
    req: &ModelRequestBody,
) -> Value {
    let mut body = json!({
        "model": model,
        "input": input_json(&req.messages),
        "stream": true,
        "store": false,
        "include": ["reasoning.encrypted_content"],
        "parallel_tool_calls": true,
    });
    if !req.system.trim().is_empty() {
        body["instructions"] = json!(req.system);
    }
    if !req.tools.is_empty() {
        body["tools"] = Value::Array(tools_json(&req.tools));
    }
    if let Some(n) = max_tokens {
        body["max_output_tokens"] = json!(n);
    }
    if let Some(effort) = reasoning_effort {
        body["reasoning"] = json!({ "effort": effort, "summary": "auto" });
    }
    if let Some(tier) = service_tier {
        body["service_tier"] = json!(tier);
    }
    body
}

fn tools_json(tools: &[ToolSchema]) -> Vec<Value> {
    tools
        .iter()
        .map(|t| {
            json!({
                "type": "function",
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
                "strict": false,
            })
        })
        .collect()
}

/// The `input` array. See the module documentation for the mapping.
pub fn input_json(messages: &[Message]) -> Vec<Value> {
    let mut out = Vec::new();
    for message in messages {
        match message {
            Message::User { content } => {
                let parts: Vec<Value> = content.iter().map(content_part).collect();
                out.push(json!({ "role": "user", "content": parts }));
            }
            Message::Assistant { text, tool_calls, thinking } => {
                out.extend(thinking.iter().filter_map(reasoning_item));
                if !text.trim().is_empty() {
                    out.push(json!({ "role": "assistant", "content": text }));
                }
                for call in tool_calls {
                    out.push(json!({
                        "type": "function_call",
                        "call_id": call.id,
                        "name": call.name,
                        "arguments": call.args.to_string(),
                    }));
                }
            }
            Message::Tool { call_id, name: _, rendered, is_error: _ } => {
                out.push(json!({ "type": "function_call_output", "call_id": call_id, "output": rendered }));
            }
        }
    }
    out
}

/// A reasoning item as the API expects it back: the item id and encrypted
/// content from the signature slot, and the summary text when there is any.
/// `None` when the block has no signature.
fn reasoning_item(block: &ThinkingBlock) -> Option<Value> {
    let signature = block.signature.as_deref()?;
    let (id, encrypted) = signature.split_once(' ')?;
    let mut item = json!({ "type": "reasoning", "id": id, "summary": [], "encrypted_content": encrypted });
    if !block.text.is_empty() {
        item["summary"] = json!([{ "type": "summary_text", "text": block.text }]);
    }
    Some(item)
}

fn content_part(block: &ContentBlock) -> Value {
    match block {
        ContentBlock::Text { text } => json!({ "type": "input_text", "text": text }),
        ContentBlock::Image { data, media_type } => {
            json!({ "type": "input_image", "image_url": format!("data:{media_type};base64,{data}"), "detail": "auto" })
        }
    }
}

// ---- stream -------------------------------------------------------------------

/// Per-request state of the event-to-chunk translation.
#[derive(Default)]
struct StreamDecoder {
    provider: &'static str,
    /// Item id to call id, for `function_call` items that have been added
    /// and not finished. Argument deltas name the item id; the runtime
    /// matches results by call id.
    calls: BTreeMap<String, String>,
    /// Whether any function call was produced, which decides `Tool` against
    /// `End` at completion.
    called: bool,
}

impl StreamDecoder {
    fn usage(response: &Value) -> Usage {
        let usage = &response["usage"];
        let n = |v: &Value| v.as_u64().unwrap_or(0);
        Usage {
            input: n(&usage["input_tokens"]),
            output: n(&usage["output_tokens"]),
            cache_read: n(&usage["input_tokens_details"]["cached_tokens"]),
        }
    }

    fn message_code(message: &str) -> Option<&str> {
        let code = message.split_once(':')?.0;
        (!code.is_empty() && code.bytes().all(|byte| byte.is_ascii_lowercase() || byte == b'_')).then_some(code)
    }
}

impl Decoder for StreamDecoder {
    fn event(&mut self, event: &sse::Event, out: &mut dyn FnMut(Chunk)) {
        let provider = self.provider;
        let data: Value = match serde_json::from_str(&event.data) {
            Ok(v) => v,
            Err(e) => return out(fail(provider, format!("event {}: data is not JSON: {e}", event.name))),
        };
        let kind = data["type"].as_str().unwrap_or(event.name.as_str());
        match kind {
            "response.output_text.delta" => {
                if let Some(text) = data["delta"].as_str().filter(|t| !t.is_empty()) {
                    out(Chunk::Text { delta: text.to_string() });
                }
            }
            "response.reasoning_summary_text.delta" | "response.reasoning_text.delta" => {
                if let Some(text) = data["delta"].as_str().filter(|t| !t.is_empty()) {
                    out(Chunk::Thinking { delta: text.to_string() });
                }
            }
            "response.output_item.added" => {
                let item = &data["item"];
                if item["type"] == "function_call" {
                    let item_id = item["id"].as_str().unwrap_or("").to_string();
                    let call_id = item["call_id"].as_str().unwrap_or("").to_string();
                    let name = item["name"].as_str().unwrap_or("").to_string();
                    if call_id.is_empty() || name.is_empty() {
                        return out(fail(
                            provider,
                            "event response.output_item.added: function_call without call_id or name",
                        ));
                    }
                    self.calls.insert(item_id, call_id.clone());
                    self.called = true;
                    out(Chunk::ToolCallStart { id: call_id, name });
                    // Arguments present at creation, as some servers send whole calls.
                    if let Some(args) = item["arguments"].as_str().filter(|a| !a.is_empty()) {
                        let id = self.calls.values().last().cloned().unwrap_or_default();
                        out(Chunk::ToolCallDelta { id, delta: args.to_string() });
                    }
                }
            }
            "response.function_call_arguments.delta" => {
                let item_id = data["item_id"].as_str().unwrap_or("");
                let Some(id) = self.calls.get(item_id) else {
                    return out(fail(
                        provider,
                        format!("event {kind}: arguments for item {item_id:?}, which is not an open function_call"),
                    ));
                };
                if let Some(text) = data["delta"].as_str().filter(|t| !t.is_empty()) {
                    out(Chunk::ToolCallDelta { id: id.clone(), delta: text.to_string() });
                }
            }
            "response.output_item.done" => {
                let item = &data["item"];
                match item["type"].as_str().unwrap_or("") {
                    "function_call" => {
                        let item_id = item["id"].as_str().unwrap_or("");
                        if let Some(id) = self.calls.remove(item_id) {
                            out(Chunk::ToolCallEnd { id });
                        }
                    }
                    "reasoning" => {
                        if let (Some(id), Some(encrypted)) = (item["id"].as_str(), item["encrypted_content"].as_str()) {
                            out(Chunk::ThinkingSignature { signature: format!("{id} {encrypted}") });
                        }
                    }
                    _ => {}
                }
            }
            "response.refusal.done" => {
                let text = data["refusal"].as_str().unwrap_or("");
                out(fail(provider, format!("refusal: {text}")));
            }
            "response.completed" => {
                let stop = if self.called { StopReason::Tool } else { StopReason::End };
                out(Chunk::Done { stop, usage: Self::usage(&data["response"]) });
            }
            "response.incomplete" => {
                let response = &data["response"];
                match response["incomplete_details"]["reason"].as_str().unwrap_or("") {
                    "max_output_tokens" => out(Chunk::Done { stop: StopReason::Length, usage: Self::usage(response) }),
                    "content_filter" => {
                        out(fail(provider, "incomplete_details content_filter: the output was withheld"))
                    }
                    other => out(fail(provider, format!("response incomplete: {other:?}"))),
                }
            }
            "response.failed" | "error" => {
                let error = if kind == "error" { &data } else { &data["response"]["error"] };
                let detail =
                    error["message"].as_str().filter(|value| !value.is_empty()).map(str::to_string).unwrap_or_else(
                        || {
                            let value = if error.is_null() { &data["response"] } else { error };
                            crate::describe_error_body(&serde_json::to_string(value).unwrap_or_default())
                        },
                    );
                let explicit_code = error["code"].as_str().filter(|value| !value.is_empty());
                let inferred_code = Self::message_code(&detail);
                let code = explicit_code.or(inferred_code).unwrap_or("unknown");
                let display_detail = if explicit_code.is_none() && inferred_code.is_some() {
                    detail.strip_prefix(code).and_then(|value| value.strip_prefix(':')).unwrap_or(&detail).trim()
                } else {
                    &detail
                };
                let retryable = matches!(code, "unknown" | "server_error" | "rate_limit_exceeded");
                out(Chunk::Error {
                    message: format!("{provider}: response failed {code}: {display_detail}"),
                    retryable,
                });
            }
            _ => {}
        }
    }

    fn end_of_stream(&mut self) -> Chunk {
        Chunk::Error { message: format!("{}: stream ended before response.completed", self.provider), retryable: true }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::auth::api_key::ApiKey;
    use crate::auth::KeyHeader;
    use crate::testserver::{Reply, Server};
    use crate::{http::Url, Client};
    use foe_core::{ToolCall, Transport};
    use std::sync::Arc;

    const TEXT_ONLY: &str = r#"event: response.created
data: {"type":"response.created","sequence_number":0,"response":{"id":"resp_01","object":"response","status":"in_progress","output":[]}}

event: response.output_item.added
data: {"type":"response.output_item.added","sequence_number":1,"output_index":0,"item":{"id":"msg_01","type":"message","status":"in_progress","role":"assistant","content":[]}}

event: response.content_part.added
data: {"type":"response.content_part.added","sequence_number":2,"item_id":"msg_01","output_index":0,"content_index":0,"part":{"type":"output_text","text":""}}

event: response.output_text.delta
data: {"type":"response.output_text.delta","sequence_number":3,"item_id":"msg_01","output_index":0,"content_index":0,"delta":"Hello"}

event: response.output_text.delta
data: {"type":"response.output_text.delta","sequence_number":4,"item_id":"msg_01","output_index":0,"content_index":0,"delta":"!"}

event: response.output_text.done
data: {"type":"response.output_text.done","sequence_number":5,"item_id":"msg_01","output_index":0,"content_index":0,"text":"Hello!"}

event: response.output_item.done
data: {"type":"response.output_item.done","sequence_number":6,"output_index":0,"item":{"id":"msg_01","type":"message","status":"completed","role":"assistant","content":[{"type":"output_text","text":"Hello!"}]}}

event: response.completed
data: {"type":"response.completed","sequence_number":7,"response":{"id":"resp_01","object":"response","status":"completed","output":[],"usage":{"input_tokens":1250,"input_tokens_details":{"cached_tokens":1024},"output_tokens":2,"output_tokens_details":{"reasoning_tokens":0},"total_tokens":1252}}}

"#;

    const TOOL_CALL: &str = r#"event: response.created
data: {"type":"response.created","sequence_number":0,"response":{"id":"resp_02","status":"in_progress","output":[]}}

event: response.output_item.added
data: {"type":"response.output_item.added","sequence_number":1,"output_index":0,"item":{"id":"rs_01","type":"reasoning","summary":[]}}

event: response.reasoning_summary_part.added
data: {"type":"response.reasoning_summary_part.added","sequence_number":2,"item_id":"rs_01","output_index":0,"summary_index":0,"part":{"type":"summary_text","text":""}}

event: response.reasoning_summary_text.delta
data: {"type":"response.reasoning_summary_text.delta","sequence_number":3,"item_id":"rs_01","output_index":0,"summary_index":0,"delta":"I should read the file first."}

event: response.output_item.done
data: {"type":"response.output_item.done","sequence_number":4,"output_index":0,"item":{"id":"rs_01","type":"reasoning","summary":[{"type":"summary_text","text":"I should read the file first."}],"encrypted_content":"gAAAAABo3q9vZ2N5cHRlZA=="}}

event: response.output_item.added
data: {"type":"response.output_item.added","sequence_number":5,"output_index":1,"item":{"id":"fc_01","type":"function_call","status":"in_progress","arguments":"","call_id":"call_abc123","name":"read"}}

event: response.function_call_arguments.delta
data: {"type":"response.function_call_arguments.delta","sequence_number":6,"item_id":"fc_01","output_index":1,"delta":"{\"pa"}

event: response.function_call_arguments.delta
data: {"type":"response.function_call_arguments.delta","sequence_number":7,"item_id":"fc_01","output_index":1,"delta":"th\": \"/src/lib.rs\"}"}

event: response.function_call_arguments.done
data: {"type":"response.function_call_arguments.done","sequence_number":8,"item_id":"fc_01","output_index":1,"arguments":"{\"path\": \"/src/lib.rs\"}"}

event: response.output_item.done
data: {"type":"response.output_item.done","sequence_number":9,"output_index":1,"item":{"id":"fc_01","type":"function_call","status":"completed","arguments":"{\"path\": \"/src/lib.rs\"}","call_id":"call_abc123","name":"read"}}

event: response.completed
data: {"type":"response.completed","sequence_number":10,"response":{"id":"resp_02","status":"completed","output":[],"usage":{"input_tokens":300,"input_tokens_details":{"cached_tokens":0},"output_tokens":40,"total_tokens":340}}}

"#;

    const LENGTH: &str = r#"event: response.created
data: {"type":"response.created","sequence_number":0,"response":{"id":"resp_03","status":"in_progress","output":[]}}

event: response.output_text.delta
data: {"type":"response.output_text.delta","sequence_number":1,"item_id":"msg_03","output_index":0,"content_index":0,"delta":"Once upon a"}

event: response.incomplete
data: {"type":"response.incomplete","sequence_number":2,"response":{"id":"resp_03","status":"incomplete","incomplete_details":{"reason":"max_output_tokens"},"output":[],"usage":{"input_tokens":12,"input_tokens_details":{"cached_tokens":0},"output_tokens":4,"total_tokens":16}}}

"#;

    fn request() -> ModelRequestBody {
        ModelRequestBody {
            request_id: "rq_01".into(),
            system: "You are a coding agent.".into(),
            tools: vec![ToolSchema {
                name: "read".into(),
                description: "Read a file.".into(),
                parameters: json!({ "type": "object", "properties": { "path": { "type": "string" } } }),
            }],
            messages: vec![Message::User { content: vec![ContentBlock::Text { text: "Fix the test.".into() }] }],
            max_output_tokens: None,
        }
    }

    /// A client as the `openai` provider row builds it.
    fn client(base: &str) -> Client {
        Client::new(
            "openai",
            "gpt-5",
            Url::parse(base).unwrap().join("/responses"),
            Vec::new(),
            Arc::new(ApiKey::new(KeyHeader::Bearer, "sk-test".into())),
            Box::new(Responses::new("openai", "gpt-5".into(), Some(2048), None, None)),
        )
    }

    async fn run(reply: Reply) -> (Vec<Chunk>, Server) {
        let server = Server::start(vec![reply]);
        let mut chunks = Vec::new();
        client(&format!("{}/v1", server.base())).stream(request(), &mut chunks).await;
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
                Chunk::Done { stop: StopReason::End, usage: Usage { input: 1250, output: 2, cache_read: 1024 } },
            ]
        );
        let seen = server.requests();
        assert_eq!(seen.len(), 1);
        assert_eq!(seen[0].path, "/v1/responses");
        assert_eq!(seen[0].header("authorization"), Some("Bearer sk-test"));
        let body = seen[0].json();
        assert_eq!(body["model"], "gpt-5");
        assert_eq!(body["stream"], true);
        assert_eq!(body["store"], false);
        assert_eq!(body["include"], json!(["reasoning.encrypted_content"]));
        assert_eq!(body["max_output_tokens"], 2048);
        assert_eq!(body["instructions"], "You are a coding agent.");
        assert!(body.get("reasoning").is_none());
        assert_eq!(
            body["input"],
            json!([{ "role": "user", "content": [{ "type": "input_text", "text": "Fix the test." }] }])
        );
        assert_eq!(body["tools"][0]["type"], "function");
        assert_eq!(body["tools"][0]["name"], "read");
        assert_eq!(body["tools"][0]["strict"], false);
    }

    #[tokio::test]
    async fn tool_call_across_deltas_with_a_reasoning_item() {
        let (chunks, _server) = run(Reply::sse(TOOL_CALL)).await;
        assert_eq!(
            chunks,
            vec![
                Chunk::Thinking { delta: "I should read the file first.".into() },
                Chunk::ThinkingSignature { signature: "rs_01 gAAAAABo3q9vZ2N5cHRlZA==".into() },
                Chunk::ToolCallStart { id: "call_abc123".into(), name: "read".into() },
                Chunk::ToolCallDelta { id: "call_abc123".into(), delta: "{\"pa".into() },
                Chunk::ToolCallDelta { id: "call_abc123".into(), delta: "th\": \"/src/lib.rs\"}".into() },
                Chunk::ToolCallEnd { id: "call_abc123".into() },
                Chunk::Done { stop: StopReason::Tool, usage: Usage { input: 300, output: 40, cache_read: 0 } },
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
                Chunk::Done { stop: StopReason::Length, usage: Usage { input: 12, output: 4, cache_read: 0 } },
            ]
        );
    }

    #[tokio::test]
    async fn rate_limited_with_retry_after() {
        let body =
            r#"{"error":{"message":"Rate limit reached","type":"rate_limit_error","code":"rate_limit_exceeded"}}"#;
        let (chunks, _server) = run(Reply::full(429, body).with_header("retry-after", "3")).await;
        assert_eq!(
            chunks,
            vec![Chunk::Error {
                message: "openai: HTTP 429: rate_limit_error: Rate limit reached retry_after_ms=3000".into(),
                retryable: true,
            }]
        );
    }

    #[tokio::test]
    async fn failed_and_filtered_responses_and_early_ends() {
        let transcript = "event: response.failed\ndata: {\"type\":\"response.failed\",\"response\":{\"status\":\"failed\",\"error\":{\"code\":\"server_error\",\"message\":\"overloaded\"}}}\n\n";
        let (chunks, _server) = run(Reply::sse(transcript)).await;
        assert_eq!(
            chunks,
            vec![Chunk::Error { message: "openai: response failed server_error: overloaded".into(), retryable: true }]
        );
        let transcript = "event: response.failed\ndata: {\"type\":\"response.failed\",\"response\":{\"status\":\"failed\",\"error\":null}}\n\n";
        let (chunks, _server) = run(Reply::sse(transcript)).await;
        assert_eq!(
            chunks,
            vec![Chunk::Error {
                message: "openai: response failed unknown: {\"error\":null,\"status\":\"failed\"}".into(),
                retryable: true,
            }]
        );
        let transcript =
            "event: error\ndata: {\"type\":\"error\",\"message\":\"invalid_request: content rejected\"}\n\n";
        let (chunks, _server) = run(Reply::sse(transcript)).await;
        assert_eq!(
            chunks,
            vec![Chunk::Error {
                message: "openai: response failed invalid_request: content rejected".into(),
                retryable: false,
            }]
        );
        let transcript = "event: response.incomplete\ndata: {\"type\":\"response.incomplete\",\"response\":{\"status\":\"incomplete\",\"incomplete_details\":{\"reason\":\"content_filter\"}}}\n\n";
        let (chunks, _server) = run(Reply::sse(transcript)).await;
        assert_eq!(
            chunks,
            vec![Chunk::Error {
                message: "openai: incomplete_details content_filter: the output was withheld".into(),
                retryable: false,
            }]
        );
        // Seven events: through the first argument delta.
        let (chunks, _server) = run(Reply::sse_cut_after(TOOL_CALL, 7)).await;
        assert_eq!(chunks.len(), 5);
        match chunks.last().unwrap() {
            Chunk::Error { message, retryable } => {
                assert!(retryable);
                assert!(message.starts_with("openai: reading response body: "), "{message}");
            }
            other => panic!("{other:?}"),
        }
        let (chunks, _server) = run(Reply::sse(TEXT_ONLY.split("event: response.completed").next().unwrap())).await;
        assert_eq!(
            chunks.last().unwrap(),
            &Chunk::Error { message: "openai: stream ended before response.completed".into(), retryable: true }
        );
    }

    #[test]
    fn input_maps_every_role_and_replays_signed_reasoning() {
        let messages = vec![
            Message::User {
                content: vec![
                    ContentBlock::Text { text: "Look.".into() },
                    ContentBlock::Image { data: "aGk=".into(), media_type: "image/png".into() },
                ],
            },
            Message::Assistant {
                text: "Reading.".into(),
                thinking: vec![
                    ThinkingBlock { text: "Plan first.".into(), signature: Some("rs_01 ENC".into()) },
                    ThinkingBlock { text: String::new(), signature: Some("rs_02 ENC2".into()) },
                    ThinkingBlock { text: "unsigned, skipped".into(), signature: None },
                ],
                tool_calls: vec![ToolCall { id: "call_1".into(), name: "read".into(), args: json!({ "path": "/a" }) }],
            },
            Message::Tool {
                call_id: "call_1".into(),
                name: "read".into(),
                rendered: "contents".into(),
                is_error: true,
            },
            Message::Assistant { text: "Done.".into(), tool_calls: vec![], thinking: vec![] },
        ];
        assert_eq!(
            input_json(&messages),
            json!([
                { "role": "user", "content": [
                    { "type": "input_text", "text": "Look." },
                    { "type": "input_image", "image_url": "data:image/png;base64,aGk=", "detail": "auto" },
                ]},
                { "type": "reasoning", "id": "rs_01", "summary": [{ "type": "summary_text", "text": "Plan first." }], "encrypted_content": "ENC" },
                { "type": "reasoning", "id": "rs_02", "summary": [], "encrypted_content": "ENC2" },
                { "role": "assistant", "content": "Reading." },
                { "type": "function_call", "call_id": "call_1", "name": "read", "arguments": "{\"path\":\"/a\"}" },
                { "type": "function_call_output", "call_id": "call_1", "output": "contents" },
                { "role": "assistant", "content": "Done." },
            ])
            .as_array()
            .unwrap()
            .clone()
        );
    }

    #[test]
    fn reasoning_effort_and_request_max_tokens_are_honored() {
        let mut req = request();
        req.tools.clear();
        req.system = String::new();
        let body =
            Responses::new("openai", "gpt-5".into(), None, Some("high".into()), Some("priority".into())).body(&req);
        assert_eq!(body["reasoning"], json!({ "effort": "high", "summary": "auto" }));
        assert_eq!(body["service_tier"], "priority");
        assert!(body.get("instructions").is_none());
        assert!(body.get("tools").is_none());
        assert!(body.get("max_output_tokens").is_none());
        req.max_output_tokens = Some(9);
        assert_eq!(Responses::new("openai", "gpt-5".into(), Some(2048), None, None).body(&req)["max_output_tokens"], 9);
    }

    #[test]
    fn codex_backend_omits_the_output_cap_it_does_not_accept() {
        let mut req = request();
        req.max_output_tokens = Some(9);
        let body = Responses::new("openai-codex", "gpt-5.6-sol".into(), Some(2048), None, None).body(&req);
        assert!(body.get("max_output_tokens").is_none());
    }
}
