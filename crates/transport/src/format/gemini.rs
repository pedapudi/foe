//! The Gemini API on Vertex AI, streamed through `streamGenerateContent`
//! with `alt=sse`.
//!
//! Request and response shape:
//! https://cloud.google.com/vertex-ai/generative-ai/docs/model-reference/inference
//! Function calling: https://cloud.google.com/vertex-ai/generative-ai/docs/multimodal/function-calling
//! Thinking and thought signatures:
//! https://cloud.google.com/vertex-ai/generative-ai/docs/thinking
//!
//! Mapping from the runtime's messages to the request:
//!
//! | runtime              | request                                                      |
//! |----------------------|--------------------------------------------------------------|
//! | `system`             | `systemInstruction`, one text part                           |
//! | `tools`              | one `tools` entry of `functionDeclarations`                  |
//! | `Message::User`      | `user` content of `text` and `inlineData` parts              |
//! | `Message::Assistant` | `model` content of thought parts, a `text` part, and `functionCall` parts |
//! | `Message::Tool`      | `functionResponse` part in a `user` content                  |
//!
//! Consecutive tool results share one `user` content, which is how the API
//! expects the results of parallel calls. A tool result is
//! `{"output": rendered}` and a failed one `{"error": rendered}`. Schema
//! keywords the API rejects, `additionalProperties` and every `$`-prefixed
//! keyword, are removed from function declarations.
//!
//! Function calls carry no id in this API, so the transport numbers them
//! `call_1`, `call_2`, ... per response. A `functionResponse` names the
//! function rather than the call, which `Message::Tool` carries as `name`.
//!
//! Thought signatures arrive on whichever part the model attaches them to:
//! a thought part, a `functionCall` part, or a text part. The model needs
//! each signature back on the same kind of part. The origin travels in the
//! signature slot: a bare token came from a thought part; `call:` prefixes
//! a token from a `functionCall` part and `text:` one from a text part.
//! On replay the prefixed tokens are reattached to the turn's function
//! call parts in order and to its text part. A token with no part left to
//! carry it is dropped.
//!
//! Mapping from stream chunks to chunks:
//!
//! | field                                          | chunk               |
//! |------------------------------------------------|---------------------|
//! | a part with `thought: true`                    | `Thinking`          |
//! | a part's `thoughtSignature`                    | `ThinkingSignature` |
//! | a part with `text`                             | `Text`              |
//! | a part with `functionCall`                     | `ToolCallStart`, `ToolCallDelta`, `ToolCallEnd` |
//! | `finishReason` then end of body                | `Done`              |
//!
//! `STOP` maps to `Tool` when a call was made and to `End` otherwise;
//! `MAX_TOKENS` to `Length`. `SAFETY`, `RECITATION`, `BLOCKLIST`,
//! `PROHIBITED_CONTENT`, `SPII`, and `MALFORMED_FUNCTION_CALL` have no
//! chunk equivalent and are non-retryable errors, as is a
//! `promptFeedback.blockReason`. Usage comes from `usageMetadata`: prompt
//! tokens as input, candidate plus thought tokens as output, and
//! `cachedContentTokenCount` as the cache read.

use foe_core::{Chunk, ContentBlock, Message, ModelRequestBody, StopReason, ToolSchema, Usage};
use foe_log::ThinkingBlock;
use serde_json::{json, Value};

use super::{fail, Decoder, Format};
use crate::sse;

/// Signature-slot prefix of a token that arrived on a `functionCall` part.
pub const CALL_MARKER: &str = "call:";
/// Signature-slot prefix of a token that arrived on a text part.
pub const TEXT_MARKER: &str = "text:";

pub struct Gemini {
    provider: &'static str,
    max_tokens: Option<u32>,
    /// Sends `thinkingConfig.includeThoughts`, which models without
    /// thinking reject.
    include_thoughts: bool,
}

impl Gemini {
    pub fn new(provider: &'static str, max_output_tokens: Option<u32>, include_thoughts: bool) -> Gemini {
        Gemini { provider, max_tokens: max_output_tokens, include_thoughts }
    }
}

impl Format for Gemini {
    fn body(&self, req: &ModelRequestBody) -> Value {
        request_body(req.max_output_tokens.or(self.max_tokens), self.include_thoughts, req)
    }

    fn decoder(&self) -> Box<dyn Decoder> {
        Box::new(StreamDecoder { provider: self.provider, ..Default::default() })
    }
}

// ---- request ------------------------------------------------------------------

pub fn request_body(max_tokens: Option<u32>, include_thoughts: bool, req: &ModelRequestBody) -> Value {
    let mut body = json!({ "contents": contents_json(&req.messages) });
    if !req.system.trim().is_empty() {
        body["systemInstruction"] = json!({ "parts": [{ "text": req.system }] });
    }
    if !req.tools.is_empty() {
        body["tools"] = json!([{ "functionDeclarations": declarations_json(&req.tools) }]);
    }
    let mut generation = json!({});
    if let Some(n) = max_tokens {
        generation["maxOutputTokens"] = json!(n);
    }
    if include_thoughts {
        generation["thinkingConfig"] = json!({ "includeThoughts": true });
    }
    if generation.as_object().is_some_and(|g| !g.is_empty()) {
        body["generationConfig"] = generation;
    }
    body
}

fn declarations_json(tools: &[ToolSchema]) -> Vec<Value> {
    tools
        .iter()
        .map(|t| json!({ "name": t.name, "description": t.description, "parameters": sanitize_schema(&t.parameters) }))
        .collect()
}

/// Removes the JSON Schema keywords the API rejects.
pub fn sanitize_schema(schema: &Value) -> Value {
    match schema {
        Value::Object(map) => Value::Object(
            map.iter()
                .filter(|(k, _)| k.as_str() != "additionalProperties" && !k.starts_with('$'))
                .map(|(k, v)| (k.clone(), sanitize_schema(v)))
                .collect(),
        ),
        Value::Array(items) => Value::Array(items.iter().map(sanitize_schema).collect()),
        other => other.clone(),
    }
}

/// The `contents` array. See the module documentation for the mapping.
pub fn contents_json(messages: &[Message]) -> Vec<Value> {
    let mut out: Vec<Value> = Vec::new();
    for message in messages {
        match message {
            Message::User { content } => push_user(&mut out, content.iter().map(content_part).collect()),
            Message::Assistant { text, tool_calls, thinking } => {
                let mut parts = Vec::new();
                let mut call_tokens = Vec::new();
                let mut text_token = None;
                for block in thinking {
                    thought_part(block, &mut parts, &mut call_tokens, &mut text_token);
                }
                if !text.trim().is_empty() {
                    let mut part = json!({ "text": text });
                    if let Some(token) = text_token {
                        part["thoughtSignature"] = json!(token);
                    }
                    parts.push(part);
                }
                let mut tokens = call_tokens.into_iter();
                for call in tool_calls {
                    let mut part = json!({ "functionCall": { "name": call.name, "args": call.args } });
                    if let Some(token) = tokens.next() {
                        part["thoughtSignature"] = json!(token);
                    }
                    parts.push(part);
                }
                if !parts.is_empty() {
                    out.push(json!({ "role": "model", "parts": parts }));
                }
            }
            Message::Tool { call_id: _, name, rendered, is_error } => {
                let response = if *is_error { json!({ "error": rendered }) } else { json!({ "output": rendered }) };
                push_user(&mut out, vec![json!({ "functionResponse": { "name": name, "response": response } })]);
            }
        }
    }
    out
}

/// Sorts one recorded thinking block into a thought part or a held token.
fn thought_part(
    block: &ThinkingBlock,
    parts: &mut Vec<Value>,
    call_tokens: &mut Vec<String>,
    text_token: &mut Option<String>,
) {
    let token = block.signature.as_deref();
    if let Some(call) = token.and_then(|t| t.strip_prefix(CALL_MARKER)) {
        call_tokens.push(call.to_string());
    } else if let Some(text) = token.and_then(|t| t.strip_prefix(TEXT_MARKER)) {
        *text_token = Some(text.to_string());
    } else if !block.text.is_empty() {
        let mut part = json!({ "text": block.text, "thought": true });
        if let Some(token) = token {
            part["thoughtSignature"] = json!(token);
        }
        parts.push(part);
    }
}

/// Appends parts to the previous `user` content when there is one.
fn push_user(out: &mut Vec<Value>, parts: Vec<Value>) {
    if let Some(last) = out.last_mut() {
        if last["role"] == "user" {
            if let Some(existing) = last["parts"].as_array_mut() {
                existing.extend(parts);
                return;
            }
        }
    }
    out.push(json!({ "role": "user", "parts": parts }));
}

fn content_part(block: &ContentBlock) -> Value {
    match block {
        ContentBlock::Text { text } => json!({ "text": text }),
        ContentBlock::Image { data, media_type } => json!({ "inlineData": { "mimeType": media_type, "data": data } }),
    }
}

// ---- stream -------------------------------------------------------------------

#[derive(Default)]
struct StreamDecoder {
    provider: &'static str,
    /// Calls numbered so far in this response.
    calls: u32,
    stop: Option<StopReason>,
    usage: Usage,
}

impl Decoder for StreamDecoder {
    fn event(&mut self, event: &sse::Event, out: &mut dyn FnMut(Chunk)) {
        let provider = self.provider;
        let data: Value = match serde_json::from_str(&event.data) {
            Ok(v) => v,
            Err(e) => return out(fail(provider, format!("stream chunk is not JSON: {e}"))),
        };
        if let Some(error) = data.get("error") {
            let code = error["code"].as_u64().unwrap_or(0);
            let status = error["status"].as_str().unwrap_or("");
            let detail = error["message"].as_str().unwrap_or("");
            let retryable = code == 429 || (500..600).contains(&code);
            return out(Chunk::Error { message: format!("{provider}: stream error {status}: {detail}"), retryable });
        }
        if let Some(reason) = data["promptFeedback"]["blockReason"].as_str() {
            return out(fail(provider, format!("prompt blocked: {reason}")));
        }
        if let Some(usage) = data.get("usageMetadata").filter(|u| u.is_object()) {
            let n = |key: &str| usage[key].as_u64().unwrap_or(0);
            self.usage = Usage {
                input: n("promptTokenCount"),
                output: n("candidatesTokenCount") + n("thoughtsTokenCount"),
                cache_read: n("cachedContentTokenCount"),
            };
        }
        let Some(candidate) = data["candidates"].as_array().and_then(|c| c.first()) else {
            return;
        };
        for part in candidate["content"]["parts"].as_array().map(Vec::as_slice).unwrap_or(&[]) {
            let thought = part["thought"].as_bool().unwrap_or(false);
            let call = part.get("functionCall").filter(|c| c.is_object());
            if let Some(token) = part["thoughtSignature"].as_str().filter(|t| !t.is_empty()) {
                let signature = if call.is_some() {
                    format!("{CALL_MARKER}{token}")
                } else if thought {
                    token.to_string()
                } else {
                    format!("{TEXT_MARKER}{token}")
                };
                out(Chunk::ThinkingSignature { signature });
            }
            if let Some(text) = part["text"].as_str().filter(|t| !t.is_empty()) {
                if thought {
                    out(Chunk::Thinking { delta: text.to_string() });
                } else {
                    out(Chunk::Text { delta: text.to_string() });
                }
            }
            if let Some(call) = call {
                let name = call["name"].as_str().unwrap_or("").to_string();
                if name.is_empty() {
                    return out(fail(provider, "functionCall part without a name"));
                }
                self.calls += 1;
                let id = format!("call_{}", self.calls);
                let args = call.get("args").cloned().unwrap_or_else(|| json!({}));
                out(Chunk::ToolCallStart { id: id.clone(), name });
                out(Chunk::ToolCallDelta { id: id.clone(), delta: args.to_string() });
                out(Chunk::ToolCallEnd { id });
            }
        }
        if let Some(reason) = candidate["finishReason"].as_str() {
            self.stop = Some(match reason {
                "STOP" if self.calls > 0 => StopReason::Tool,
                "STOP" => StopReason::End,
                "MAX_TOKENS" => StopReason::Length,
                "SAFETY" | "RECITATION" | "BLOCKLIST" | "PROHIBITED_CONTENT" | "SPII" => {
                    return out(fail(provider, format!("finishReason {reason}: the output was withheld")));
                }
                "MALFORMED_FUNCTION_CALL" => {
                    return out(fail(
                        provider,
                        "finishReason MALFORMED_FUNCTION_CALL: the model produced an unparseable call",
                    ));
                }
                _ => StopReason::End,
            });
        }
    }

    fn end_of_stream(&mut self) -> Chunk {
        match self.stop {
            Some(stop) => Chunk::Done { stop, usage: self.usage },
            None => Chunk::Error {
                message: format!("{}: stream ended before finishReason", self.provider),
                retryable: true,
            },
        }
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

    const PATH: &str =
        "/v1/projects/p/locations/us-east5/publishers/google/models/gemini-2.5-pro:streamGenerateContent?alt=sse";

    const TEXT_ONLY: &str = r#"data: {"candidates":[{"content":{"role":"model","parts":[{"text":"Hello"}]}}],"usageMetadata":{"promptTokenCount":25,"totalTokenCount":25},"modelVersion":"gemini-2.5-pro","responseId":"r1"}

data: {"candidates":[{"content":{"role":"model","parts":[{"text":"!"}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":25,"candidatesTokenCount":2,"totalTokenCount":27,"cachedContentTokenCount":20},"modelVersion":"gemini-2.5-pro","responseId":"r1"}

"#;

    const FUNCTION_CALL: &str = r#"data: {"candidates":[{"content":{"role":"model","parts":[{"text":"I should read the file first.","thought":true}]}}],"usageMetadata":{"promptTokenCount":300},"responseId":"r2"}

data: {"candidates":[{"content":{"role":"model","parts":[{"functionCall":{"name":"read","args":{"path":"/src/lib.rs"}},"thoughtSignature":"CiQBsig"}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":300,"candidatesTokenCount":10,"thoughtsTokenCount":30,"totalTokenCount":340},"responseId":"r2"}

"#;

    const LENGTH: &str = r#"data: {"candidates":[{"content":{"role":"model","parts":[{"text":"Once upon a"}]},"finishReason":"MAX_TOKENS"}],"usageMetadata":{"promptTokenCount":12,"candidatesTokenCount":4,"totalTokenCount":16},"responseId":"r3"}

"#;

    fn request() -> ModelRequestBody {
        ModelRequestBody {
            request_id: "rq_01".into(),
            system: "You are a coding agent.".into(),
            tools: vec![ToolSchema {
                name: "read".into(),
                description: "Read a file.".into(),
                parameters: json!({ "$schema": "x", "type": "object", "properties": { "path": { "type": "string" } }, "additionalProperties": false }),
            }],
            messages: vec![Message::User { content: vec![ContentBlock::Text { text: "Fix the test.".into() }] }],
            max_output_tokens: None,
        }
    }

    /// A client as the `vertex` provider row builds it for a Gemini model,
    /// with a static bearer standing in for a minted token.
    fn client(base: &str) -> Client {
        Client::new(
            "vertex",
            "gemini-2.5-pro",
            Url::parse(base).unwrap().join(PATH),
            Vec::new(),
            Arc::new(ApiKey::new(KeyHeader::Bearer, "ya29.token".into())),
            Box::new(Gemini::new("vertex", Some(1024), true)),
        )
    }

    async fn run(reply: Reply) -> (Vec<Chunk>, Server) {
        let server = Server::start(vec![reply]);
        let mut chunks = Vec::new();
        client(&server.base()).stream(request(), &mut chunks).await;
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
                Chunk::Done { stop: StopReason::End, usage: Usage { input: 25, output: 2, cache_read: 20 } },
            ]
        );
        let seen = server.requests();
        assert_eq!(seen[0].path, PATH);
        assert_eq!(seen[0].header("authorization"), Some("Bearer ya29.token"));
        let body = seen[0].json();
        assert_eq!(body["systemInstruction"], json!({ "parts": [{ "text": "You are a coding agent." }] }));
        assert_eq!(body["contents"], json!([{ "role": "user", "parts": [{ "text": "Fix the test." }] }]));
        assert_eq!(
            body["tools"][0]["functionDeclarations"][0],
            json!({ "name": "read", "description": "Read a file.", "parameters": { "type": "object", "properties": { "path": { "type": "string" } } } })
        );
        assert_eq!(
            body["generationConfig"],
            json!({ "maxOutputTokens": 1024, "thinkingConfig": { "includeThoughts": true } })
        );
    }

    #[tokio::test]
    async fn function_call_with_a_thought_part_and_a_signature() {
        let (chunks, _server) = run(Reply::sse(FUNCTION_CALL)).await;
        assert_eq!(
            chunks,
            vec![
                Chunk::Thinking { delta: "I should read the file first.".into() },
                Chunk::ThinkingSignature { signature: "call:CiQBsig".into() },
                Chunk::ToolCallStart { id: "call_1".into(), name: "read".into() },
                Chunk::ToolCallDelta { id: "call_1".into(), delta: "{\"path\":\"/src/lib.rs\"}".into() },
                Chunk::ToolCallEnd { id: "call_1".into() },
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
    async fn rate_limited_and_withheld() {
        let body = r#"{"error":{"code":429,"message":"Resource exhausted","status":"RESOURCE_EXHAUSTED"}}"#;
        let (chunks, _server) = run(Reply::full(429, body).with_header("retry-after", "5")).await;
        assert_eq!(
            chunks,
            vec![Chunk::Error {
                message: "vertex: HTTP 429: RESOURCE_EXHAUSTED: Resource exhausted retry_after_ms=5000".into(),
                retryable: true,
            }]
        );
        let transcript = "data: {\"candidates\":[{\"finishReason\":\"SAFETY\",\"safetyRatings\":[]}]}\n\n";
        let (chunks, _server) = run(Reply::sse(transcript)).await;
        assert_eq!(
            chunks,
            vec![Chunk::Error {
                message: "vertex: finishReason SAFETY: the output was withheld".into(),
                retryable: false
            }]
        );
        let transcript = "data: {\"promptFeedback\":{\"blockReason\":\"PROHIBITED_CONTENT\"}}\n\n";
        let (chunks, _server) = run(Reply::sse(transcript)).await;
        assert_eq!(
            chunks,
            vec![Chunk::Error { message: "vertex: prompt blocked: PROHIBITED_CONTENT".into(), retryable: false }]
        );
        let (chunks, _server) =
            run(Reply::sse(TEXT_ONLY.split("\n\n").next().map(|s| format!("{s}\n\n")).unwrap().as_str())).await;
        assert_eq!(
            chunks,
            vec![
                text("Hello"),
                Chunk::Error { message: "vertex: stream ended before finishReason".into(), retryable: true }
            ]
        );
    }

    #[test]
    fn contents_map_every_role_and_reattach_signatures() {
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
                    ThinkingBlock { text: "Plan first.".into(), signature: Some("THOUGHT".into()) },
                    ThinkingBlock { text: String::new(), signature: Some("call:C1".into()) },
                    ThinkingBlock { text: String::new(), signature: Some("text:T1".into()) },
                    ThinkingBlock { text: String::new(), signature: None },
                ],
                tool_calls: vec![
                    ToolCall { id: "call_1".into(), name: "read".into(), args: json!({ "path": "/a" }) },
                    ToolCall { id: "call_2".into(), name: "read".into(), args: json!({ "path": "/b" }) },
                ],
            },
            Message::Tool {
                call_id: "call_1".into(),
                name: "read".into(),
                rendered: "contents of a".into(),
                is_error: false,
            },
            Message::Tool {
                call_id: "call_2".into(),
                name: "read".into(),
                rendered: "no such file".into(),
                is_error: true,
            },
            Message::User { content: vec![ContentBlock::Text { text: "Hurry.".into() }] },
            Message::Assistant { text: String::new(), tool_calls: vec![], thinking: vec![] },
        ];
        assert_eq!(
            contents_json(&messages),
            json!([
                { "role": "user", "parts": [
                    { "text": "Look." },
                    { "inlineData": { "mimeType": "image/png", "data": "aGk=" } },
                ]},
                { "role": "model", "parts": [
                    { "text": "Plan first.", "thought": true, "thoughtSignature": "THOUGHT" },
                    { "text": "Reading.", "thoughtSignature": "T1" },
                    { "functionCall": { "name": "read", "args": { "path": "/a" } }, "thoughtSignature": "C1" },
                    { "functionCall": { "name": "read", "args": { "path": "/b" } } },
                ]},
                { "role": "user", "parts": [
                    { "functionResponse": { "name": "read", "response": { "output": "contents of a" } } },
                    { "functionResponse": { "name": "read", "response": { "error": "no such file" } } },
                    { "text": "Hurry." },
                ]},
            ])
            .as_array()
            .unwrap()
            .clone()
        );
    }

    #[test]
    fn thoughts_can_be_left_out_and_request_max_tokens_wins() {
        let mut req = request();
        req.system = String::new();
        req.tools.clear();
        let body = Gemini::new("vertex", None, false).body(&req);
        assert!(body.get("systemInstruction").is_none());
        assert!(body.get("tools").is_none());
        assert!(body.get("generationConfig").is_none());
        req.max_output_tokens = Some(3);
        assert_eq!(
            Gemini::new("vertex", Some(1024), false).body(&req)["generationConfig"],
            json!({ "maxOutputTokens": 3 })
        );
    }
}
