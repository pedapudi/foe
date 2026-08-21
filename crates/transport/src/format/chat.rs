//! The OpenAI Chat Completions API, streamed. Also serves local servers
//! such as Ollama, vLLM, and llama.cpp, and proxies such as LiteLLM and
//! OpenRouter, which implement the same request and stream shapes.
//!
//! Request shape: https://platform.openai.com/docs/api-reference/chat/create
//! Stream chunks: https://platform.openai.com/docs/api-reference/chat/streaming
//! Tool calls in a stream: https://platform.openai.com/docs/guides/function-calling#streaming
//!
//! Mapping from the runtime's messages to the request:
//!
//! | runtime              | request                                               |
//! |----------------------|-------------------------------------------------------|
//! | `system`             | a leading `system` message                            |
//! | `tools`              | `tools` of type `function`                            |
//! | `Message::User`      | `user` message; a string when it is one text block    |
//! | `Message::Assistant` | `assistant` message with `content` and `tool_calls`; `thinking` dropped |
//! | `Message::Tool`      | `tool` message with `tool_call_id`                    |
//!
//! A user message that is a single text block is sent as a plain string
//! because some local servers accept only that form; anything else is sent
//! as an array of content parts. The API has no field for a failed tool
//! result, so `is_error` is not transmitted; the rendered text carries it.
//! The API has no way to replay reasoning blocks, so an assistant turn's
//! `thinking` is dropped.
//!
//! Mapping from stream chunks to chunks:
//!
//! | field                                            | chunk           |
//! |--------------------------------------------------|-----------------|
//! | `delta.content`                                  | `Text`          |
//! | `delta.reasoning_content` or `delta.reasoning`   | `Thinking`      |
//! | `delta.tool_calls[]` first seen `index`          | `ToolCallStart` |
//! | `delta.tool_calls[].function.arguments`          | `ToolCallDelta` |
//! | a tool call's last fragment                      | `ToolCallEnd`   |
//! | `finish_reason` then `[DONE]` or end of body     | `Done`          |
//!
//! `stop` maps to `End`, `tool_calls` and `function_call` to `Tool`,
//! `length` to `Length`. `content_filter` has no chunk equivalent and is a
//! non-retryable `Error`. `Done` is emitted when the stream ends, because
//! the usage chunk requested through `stream_options.include_usage` arrives
//! after the chunk carrying `finish_reason`. The reasoning fields are a
//! convention of DeepSeek, vLLM, and llama.cpp rather than part of the
//! OpenAI specification.

use std::collections::BTreeMap;

use foe_core::{Chunk, ContentBlock, Message, ModelRequestBody, StopReason, ToolSchema, Usage};
use serde_json::{json, Value};

use super::{fail, Decoder, Format};
use crate::sse;

pub struct Chat {
    provider: &'static str,
    model: String,
    max_tokens: Option<u32>,
}

impl Chat {
    pub fn new(provider: &'static str, model: String, max_output_tokens: Option<u32>) -> Chat {
        Chat { provider, model, max_tokens: max_output_tokens }
    }
}

impl Format for Chat {
    fn body(&self, req: &ModelRequestBody) -> Value {
        request_body(&self.model, req.max_output_tokens.or(self.max_tokens), req)
    }

    fn decoder(&self) -> Box<dyn Decoder> {
        Box::new(StreamDecoder { provider: self.provider, ..Default::default() })
    }
}

// ---- request ------------------------------------------------------------------

/// The JSON body for one request. `max_tokens` rather than the newer
/// `max_completion_tokens` because every compatible server accepts it;
/// https://platform.openai.com/docs/api-reference/chat/create marks it
/// deprecated but supported.
pub fn request_body(model: &str, max_tokens: Option<u32>, req: &ModelRequestBody) -> Value {
    let mut messages = Vec::new();
    if !req.system.trim().is_empty() {
        messages.push(json!({ "role": "system", "content": req.system }));
    }
    messages.extend(messages_json(&req.messages));
    let mut body = json!({
        "model": model,
        "messages": messages,
        "stream": true,
        "stream_options": { "include_usage": true },
    });
    if !req.tools.is_empty() {
        body["tools"] = Value::Array(tools_json(&req.tools));
    }
    if let Some(n) = max_tokens {
        body["max_tokens"] = json!(n);
    }
    body
}

fn tools_json(tools: &[ToolSchema]) -> Vec<Value> {
    tools
        .iter()
        .map(|t| {
            json!({
                "type": "function",
                "function": { "name": t.name, "description": t.description, "parameters": t.parameters },
            })
        })
        .collect()
}

/// The `messages` array without the system message. See the module
/// documentation for the mapping.
pub fn messages_json(messages: &[Message]) -> Vec<Value> {
    messages
        .iter()
        .map(|message| match message {
            Message::User { content } => {
                let value = match content.as_slice() {
                    [ContentBlock::Text { text }] => json!(text),
                    blocks => Value::Array(blocks.iter().map(content_part).collect()),
                };
                json!({ "role": "user", "content": value })
            }
            Message::Assistant { text, tool_calls, thinking: _ } => {
                let mut value =
                    json!({ "role": "assistant", "content": if text.is_empty() { Value::Null } else { json!(text) } });
                if !tool_calls.is_empty() {
                    value["tool_calls"] = tool_calls
                        .iter()
                        .map(|call| {
                            json!({
                                "id": call.id,
                                "type": "function",
                                "function": { "name": call.name, "arguments": call.args.to_string() },
                            })
                        })
                        .collect();
                }
                value
            }
            Message::Tool { call_id, name: _, rendered, is_error: _ } => {
                json!({ "role": "tool", "tool_call_id": call_id, "content": rendered })
            }
        })
        .collect()
}

fn content_part(block: &ContentBlock) -> Value {
    match block {
        ContentBlock::Text { text } => json!({ "type": "text", "text": text }),
        ContentBlock::Image { data, media_type } => {
            json!({ "type": "image_url", "image_url": { "url": format!("data:{media_type};base64,{data}") } })
        }
    }
}

// ---- stream -------------------------------------------------------------------

/// Per-request state of the chunk translation.
#[derive(Default)]
struct StreamDecoder {
    provider: &'static str,
    /// Tool call index, as the API numbers them, to the id announced with
    /// `ToolCallStart`.
    ids: BTreeMap<u64, String>,
    /// The index of the tool call whose arguments are still arriving.
    open: Option<u64>,
    stop: Option<StopReason>,
    usage: Usage,
}

impl StreamDecoder {
    fn close_open(&mut self, out: &mut dyn FnMut(Chunk)) {
        if let Some(index) = self.open.take() {
            if let Some(id) = self.ids.get(&index) {
                out(Chunk::ToolCallEnd { id: id.clone() });
            }
        }
    }

    fn tool_fragment(&mut self, call: &Value, out: &mut dyn FnMut(Chunk)) {
        // Fragments of one call share an index; fragments of the next call
        // start after the previous call's last fragment.
        let index = call["index"].as_u64().unwrap_or(0);
        if self.open != Some(index) {
            self.close_open(out);
        }
        let id = match self.ids.get(&index) {
            Some(id) => id.clone(),
            None => {
                let id = match call["id"].as_str().filter(|s| !s.is_empty()) {
                    Some(id) => id.to_string(),
                    // A server that sends no id still needs one the runtime
                    // can match results against.
                    None => format!("call_{index}"),
                };
                let name = call["function"]["name"].as_str().unwrap_or("").to_string();
                out(Chunk::ToolCallStart { id: id.clone(), name });
                self.ids.insert(index, id.clone());
                id
            }
        };
        self.open = Some(index);
        if let Some(args) = call["function"]["arguments"].as_str().filter(|s| !s.is_empty()) {
            out(Chunk::ToolCallDelta { id, delta: args.to_string() });
        }
    }

    fn finish(&mut self) -> Chunk {
        match self.stop {
            Some(stop) => Chunk::Done { stop, usage: self.usage },
            None => Chunk::Error {
                message: format!("{}: stream ended before finish_reason", self.provider),
                retryable: true,
            },
        }
    }
}

impl Decoder for StreamDecoder {
    fn event(&mut self, event: &sse::Event, out: &mut dyn FnMut(Chunk)) {
        if event.data.trim() == "[DONE]" {
            return out(self.finish());
        }
        let data: Value = match serde_json::from_str(&event.data) {
            Ok(v) => v,
            Err(e) => return out(fail(self.provider, format!("stream chunk is not JSON: {e}"))),
        };
        if let Some(error) = data.get("error") {
            // Proxies report an upstream failure mid-stream as a chunk; the
            // numeric code, when present, classifies it like a status.
            let code = error.get("code").or_else(|| error.get("status")).and_then(Value::as_u64).unwrap_or(0);
            let retryable = code == 429 || (500..600).contains(&code);
            let detail = error["message"].as_str().unwrap_or("");
            return out(Chunk::Error { message: format!("{}: stream error: {detail}", self.provider), retryable });
        }
        if let Some(usage) = data.get("usage").filter(|u| u.is_object()) {
            let n = |v: &Value| v.as_u64().unwrap_or(0);
            self.usage = Usage {
                input: n(&usage["prompt_tokens"]),
                output: n(&usage["completion_tokens"]),
                cache_read: n(&usage["prompt_tokens_details"]["cached_tokens"]),
            };
        }
        let Some(choice) = data["choices"].as_array().and_then(|c| c.first()) else {
            return;
        };
        let delta = &choice["delta"];
        for key in ["reasoning_content", "reasoning"] {
            if let Some(text) = delta[key].as_str().filter(|s| !s.is_empty()) {
                out(Chunk::Thinking { delta: text.to_string() });
            }
        }
        if let Some(text) = delta["content"].as_str().filter(|s| !s.is_empty()) {
            out(Chunk::Text { delta: text.to_string() });
        }
        if let Some(calls) = delta["tool_calls"].as_array() {
            for call in calls {
                self.tool_fragment(call, out);
            }
        }
        if let Some(reason) = choice["finish_reason"].as_str() {
            self.close_open(out);
            self.stop = Some(match reason {
                // Some local servers report `stop` after emitting tool calls.
                "stop" if !self.ids.is_empty() => StopReason::Tool,
                "tool_calls" | "function_call" => StopReason::Tool,
                "length" => StopReason::Length,
                "content_filter" => {
                    return out(fail(self.provider, "finish_reason content_filter: the output was withheld"));
                }
                _ => StopReason::End,
            });
        }
    }

    fn end_of_stream(&mut self) -> Chunk {
        self.finish()
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

    /// A client as the `openai-compatible` provider row builds it.
    fn client(base: &str, max_tokens: Option<u32>) -> Client {
        Client::new(
            "openai-compatible",
            "gpt-4o",
            Url::parse(base).unwrap().join("/chat/completions"),
            Vec::new(),
            Arc::new(ApiKey::new(KeyHeader::Bearer, "sk-test".into())),
            Box::new(Chat::new("openai-compatible", "gpt-4o".into(), max_tokens)),
        )
    }

    const TEXT_ONLY: &str = r#"data: {"id":"chatcmpl-9Y8e","object":"chat.completion.chunk","created":1716000000,"model":"gpt-4o-2024-08-06","system_fingerprint":"fp_1","choices":[{"index":0,"delta":{"role":"assistant","content":"","refusal":null},"logprobs":null,"finish_reason":null}],"usage":null}

data: {"id":"chatcmpl-9Y8e","object":"chat.completion.chunk","created":1716000000,"model":"gpt-4o-2024-08-06","system_fingerprint":"fp_1","choices":[{"index":0,"delta":{"content":"Hello"},"logprobs":null,"finish_reason":null}],"usage":null}

data: {"id":"chatcmpl-9Y8e","object":"chat.completion.chunk","created":1716000000,"model":"gpt-4o-2024-08-06","system_fingerprint":"fp_1","choices":[{"index":0,"delta":{"content":"!"},"logprobs":null,"finish_reason":null}],"usage":null}

data: {"id":"chatcmpl-9Y8e","object":"chat.completion.chunk","created":1716000000,"model":"gpt-4o-2024-08-06","system_fingerprint":"fp_1","choices":[{"index":0,"delta":{},"logprobs":null,"finish_reason":"stop"}],"usage":null}

data: {"id":"chatcmpl-9Y8e","object":"chat.completion.chunk","created":1716000000,"model":"gpt-4o-2024-08-06","system_fingerprint":"fp_1","choices":[],"usage":{"prompt_tokens":1250,"completion_tokens":2,"total_tokens":1252,"prompt_tokens_details":{"cached_tokens":1024,"audio_tokens":0},"completion_tokens_details":{"reasoning_tokens":0}}}

data: [DONE]

"#;

    const TOOL_CALL: &str = r#"data: {"id":"chatcmpl-9Y8f","object":"chat.completion.chunk","created":1716000001,"model":"gpt-4o-2024-08-06","choices":[{"index":0,"delta":{"role":"assistant","content":null,"tool_calls":[{"index":0,"id":"call_abc123","type":"function","function":{"name":"read","arguments":""}}]},"logprobs":null,"finish_reason":null}],"usage":null}

data: {"id":"chatcmpl-9Y8f","object":"chat.completion.chunk","created":1716000001,"model":"gpt-4o-2024-08-06","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\"pa"}}]},"logprobs":null,"finish_reason":null}],"usage":null}

data: {"id":"chatcmpl-9Y8f","object":"chat.completion.chunk","created":1716000001,"model":"gpt-4o-2024-08-06","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"th\": \"/src/lib.rs\"}"}}]},"logprobs":null,"finish_reason":null}],"usage":null}

data: {"id":"chatcmpl-9Y8f","object":"chat.completion.chunk","created":1716000001,"model":"gpt-4o-2024-08-06","choices":[{"index":0,"delta":{"tool_calls":[{"index":1,"id":"call_def456","type":"function","function":{"name":"grep","arguments":""}}]},"logprobs":null,"finish_reason":null}],"usage":null}

data: {"id":"chatcmpl-9Y8f","object":"chat.completion.chunk","created":1716000001,"model":"gpt-4o-2024-08-06","choices":[{"index":0,"delta":{"tool_calls":[{"index":1,"function":{"arguments":"{\"pattern\": \"fn main\"}"}}]},"logprobs":null,"finish_reason":null}],"usage":null}

data: {"id":"chatcmpl-9Y8f","object":"chat.completion.chunk","created":1716000001,"model":"gpt-4o-2024-08-06","choices":[{"index":0,"delta":{},"logprobs":null,"finish_reason":"tool_calls"}],"usage":null}

data: {"id":"chatcmpl-9Y8f","object":"chat.completion.chunk","created":1716000001,"model":"gpt-4o-2024-08-06","choices":[],"usage":{"prompt_tokens":300,"completion_tokens":40,"total_tokens":340,"prompt_tokens_details":{"cached_tokens":0}}}

data: [DONE]

"#;

    const LENGTH: &str = r#"data: {"id":"chatcmpl-9Y8g","object":"chat.completion.chunk","created":1716000002,"model":"llama3.1","choices":[{"index":0,"delta":{"role":"assistant","content":"Once"},"finish_reason":null}]}

data: {"id":"chatcmpl-9Y8g","object":"chat.completion.chunk","created":1716000002,"model":"llama3.1","choices":[{"index":0,"delta":{"content":" upon"},"finish_reason":null}]}

data: {"id":"chatcmpl-9Y8g","object":"chat.completion.chunk","created":1716000002,"model":"llama3.1","choices":[{"index":0,"delta":{},"finish_reason":"length"}]}

data: {"id":"chatcmpl-9Y8g","object":"chat.completion.chunk","created":1716000002,"model":"llama3.1","choices":[],"usage":{"prompt_tokens":12,"completion_tokens":2,"total_tokens":14}}

data: [DONE]

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

    async fn run(reply: Reply) -> (Vec<Chunk>, Server) {
        let server = Server::start(vec![reply]);
        let mut chunks = Vec::new();
        client(&format!("{}/v1", server.base()), Some(2048)).stream(request(), &mut chunks).await;
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
        assert_eq!(seen[0].path, "/v1/chat/completions");
        assert_eq!(seen[0].header("authorization"), Some("Bearer sk-test"));
        let body = seen[0].json();
        assert_eq!(body["model"], "gpt-4o");
        assert_eq!(body["stream"], true);
        assert_eq!(body["stream_options"]["include_usage"], true);
        assert_eq!(body["max_tokens"], 2048);
        assert_eq!(body["messages"][0], json!({ "role": "system", "content": "You are a coding agent." }));
        assert_eq!(body["messages"][1], json!({ "role": "user", "content": "Fix the test." }));
        assert_eq!(body["tools"][0]["type"], "function");
        assert_eq!(body["tools"][0]["function"]["name"], "read");
        assert_eq!(body["tools"][0]["function"]["parameters"]["type"], "object");
    }

    #[tokio::test]
    async fn tool_calls_split_across_deltas() {
        let (chunks, _server) = run(Reply::sse(TOOL_CALL)).await;
        assert_eq!(
            chunks,
            vec![
                Chunk::ToolCallStart { id: "call_abc123".into(), name: "read".into() },
                Chunk::ToolCallDelta { id: "call_abc123".into(), delta: "{\"pa".into() },
                Chunk::ToolCallDelta { id: "call_abc123".into(), delta: "th\": \"/src/lib.rs\"}".into() },
                Chunk::ToolCallEnd { id: "call_abc123".into() },
                Chunk::ToolCallStart { id: "call_def456".into(), name: "grep".into() },
                Chunk::ToolCallDelta { id: "call_def456".into(), delta: "{\"pattern\": \"fn main\"}".into() },
                Chunk::ToolCallEnd { id: "call_def456".into() },
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
                text("Once"),
                text(" upon"),
                Chunk::Done { stop: StopReason::Length, usage: Usage { input: 12, output: 2, cache_read: 0 } },
            ]
        );
    }

    #[tokio::test]
    async fn rate_limited_with_retry_after() {
        let body = r#"{"error":{"message":"Rate limit reached for gpt-4o","type":"tokens","param":null,"code":"rate_limit_exceeded"}}"#;
        let (chunks, _server) = run(Reply::full(429, body).with_header("Retry-After", "20")).await;
        assert_eq!(
            chunks,
            vec![Chunk::Error {
                message: "openai-compatible: HTTP 429: tokens: Rate limit reached for gpt-4o retry_after_ms=20000"
                    .into(),
                retryable: true,
            }]
        );
        // The millisecond header, when present, is more precise and wins.
        let (chunks, _server) =
            run(Reply::full(429, body).with_header("retry-after", "1").with_header("retry-after-ms", "350")).await;
        match &chunks[0] {
            Chunk::Error { message, .. } => {
                assert!(message.ends_with(" retry_after_ms=350"), "{message}")
            }
            other => panic!("{other:?}"),
        }
    }

    #[tokio::test]
    async fn server_errors_are_retryable_and_auth_errors_are_not() {
        let (chunks, _server) = run(Reply::full(502, "<html>Bad Gateway</html>")).await;
        assert_eq!(
            chunks,
            vec![Chunk::Error {
                message: "openai-compatible: HTTP 502: <html>Bad Gateway</html>".into(),
                retryable: true
            }]
        );
        let body = r#"{"error":{"message":"Incorrect API key provided","type":"invalid_request_error","code":"invalid_api_key"}}"#;
        let (chunks, _server) = run(Reply::full(401, body)).await;
        assert_eq!(
            chunks,
            vec![Chunk::Error {
                message: "openai-compatible: HTTP 401: invalid_request_error: Incorrect API key provided".into(),
                retryable: false,
            }]
        );
    }

    #[tokio::test]
    async fn disconnect_mid_stream() {
        let (chunks, _server) = run(Reply::sse_cut_after(TOOL_CALL, 2)).await;
        assert_eq!(
            chunks,
            vec![
                Chunk::ToolCallStart { id: "call_abc123".into(), name: "read".into() },
                Chunk::ToolCallDelta { id: "call_abc123".into(), delta: "{\"pa".into() },
                Chunk::Error {
                    message: "openai-compatible: reading response body: connection closed before the body was complete"
                        .into(),
                    retryable: true,
                },
            ]
        );
    }

    #[tokio::test]
    async fn clean_end_without_done_marker_still_completes() {
        // A server that omits `[DONE]` and the usage chunk.
        let transcript = r#"data: {"choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":null}]}

data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

"#;
        let (chunks, _server) = run(Reply::sse(transcript)).await;
        assert_eq!(chunks, vec![text("ok"), Chunk::Done { stop: StopReason::End, usage: Usage::default() }]);
        // A server that closes cleanly before any finish_reason.
        let transcript =
            "data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"ok\"},\"finish_reason\":null}]}\n\n";
        let (chunks, _server) = run(Reply::sse(transcript)).await;
        assert_eq!(
            chunks,
            vec![
                text("ok"),
                Chunk::Error {
                    message: "openai-compatible: stream ended before finish_reason".into(),
                    retryable: true
                }
            ]
        );
    }

    #[tokio::test]
    async fn reasoning_and_tool_call_without_id_and_stop_after_tools() {
        let transcript = r#"data: {"choices":[{"index":0,"delta":{"reasoning_content":"thinking..."},"finish_reason":null}]}

data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"name":"read","arguments":"{}"}}]},"finish_reason":null}]}

data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]

"#;
        let (chunks, _server) = run(Reply::sse(transcript)).await;
        assert_eq!(
            chunks,
            vec![
                Chunk::Thinking { delta: "thinking...".into() },
                Chunk::ToolCallStart { id: "call_0".into(), name: "read".into() },
                Chunk::ToolCallDelta { id: "call_0".into(), delta: "{}".into() },
                Chunk::ToolCallEnd { id: "call_0".into() },
                Chunk::Done { stop: StopReason::Tool, usage: Usage::default() },
            ]
        );
    }

    #[tokio::test]
    async fn inline_error_chunk_and_content_filter() {
        let transcript =
            "data: {\"error\":{\"message\":\"upstream overloaded\",\"type\":\"server_error\",\"code\":503}}\n\n";
        let (chunks, _server) = run(Reply::sse(transcript)).await;
        assert_eq!(
            chunks,
            vec![Chunk::Error {
                message: "openai-compatible: stream error: upstream overloaded".into(),
                retryable: true
            }]
        );
        let transcript =
            "data: {\"choices\":[{\"index\":0,\"delta\":{},\"finish_reason\":\"content_filter\"}]}\n\ndata: [DONE]\n\n";
        let (chunks, _server) = run(Reply::sse(transcript)).await;
        assert_eq!(
            chunks,
            vec![Chunk::Error {
                message: "openai-compatible: finish_reason content_filter: the output was withheld".into(),
                retryable: false,
            }]
        );
    }

    #[test]
    fn messages_map_every_role() {
        let messages = vec![
            Message::User { content: vec![ContentBlock::Text { text: "Look.".into() }] },
            Message::User {
                content: vec![
                    ContentBlock::Text { text: "And this.".into() },
                    ContentBlock::Image { data: "aGk=".into(), media_type: "image/png".into() },
                ],
            },
            Message::Assistant {
                text: String::new(),
                thinking: vec![foe_log::ThinkingBlock { text: "dropped".into(), signature: Some("sig".into()) }],
                tool_calls: vec![ToolCall { id: "call_1".into(), name: "read".into(), args: json!({ "path": "/a" }) }],
            },
            Message::Tool {
                call_id: "call_1".into(),
                name: "read".into(),
                rendered: "error: no such file".into(),
                is_error: true,
            },
            Message::Assistant { text: "Done.".into(), tool_calls: vec![], thinking: vec![] },
        ];
        assert_eq!(
            messages_json(&messages),
            json!([
                { "role": "user", "content": "Look." },
                { "role": "user", "content": [
                    { "type": "text", "text": "And this." },
                    { "type": "image_url", "image_url": { "url": "data:image/png;base64,aGk=" } },
                ]},
                { "role": "assistant", "content": null, "tool_calls": [
                    { "id": "call_1", "type": "function", "function": { "name": "read", "arguments": "{\"path\":\"/a\"}" } },
                ]},
                { "role": "tool", "tool_call_id": "call_1", "content": "error: no such file" },
                { "role": "assistant", "content": "Done." },
            ])
            .as_array()
            .unwrap()
            .clone()
        );
    }

    #[test]
    fn request_omits_absent_max_tokens_and_empty_tools() {
        let mut req = request();
        req.tools.clear();
        let body = request_body("m", None, &req);
        assert!(body.get("max_tokens").is_none());
        assert!(body.get("tools").is_none());
        req.max_output_tokens = Some(7);
        assert_eq!(Chat::new("openai-compatible", "m".into(), Some(2048)).body(&req)["max_tokens"], 7);
    }
}
