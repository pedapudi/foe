//! Built-in model clients, used when a configuration has a `model` block.
//!
//! [`Anthropic`] implements [`foe_core::Transport`] for the Anthropic
//! Messages API. It streams the response and translates provider events
//! into the chunk vocabulary of `docs/protocol.md`, which the runtime
//! records unchanged.
//!
//! What the client guarantees:
//!
//! - The API key comes from the file named by `model.api_key_file` and from
//!   nowhere else. No environment variable is read, including proxy
//!   variables; there is no proxy support.
//! - TLS trusts a compiled-in copy of Mozilla's root certificates; the
//!   system certificate store is never opened.
//! - Every call to `stream` ends with exactly one `Chunk::Done` or
//!   `Chunk::Error`. An HTTP 429 or 5xx status, a refused connection, and a
//!   connection that drops mid-stream are reported as retryable; any other
//!   4xx status is not. A `Retry-After` header is carried in the error
//!   message as `retry_after_ms=N`.
//!
//! `model.base_url` for Anthropic is the origin, `https://api.anthropic.com`,
//! and the client appends `/v1/messages`.
//!
//! The HTTP work runs on a blocking thread; `stream` forwards chunks to the
//! caller's sink as they arrive.

#![forbid(unsafe_code)]

use std::io::Read;
use std::path::{Path, PathBuf};

use foe_core::Chunk;

pub mod anthropic;
mod http;
mod sse;
#[cfg(test)]
mod testserver;

pub use anthropic::Anthropic;

/// Why a client could not be constructed. Every variant names the
/// configuration key involved.
#[derive(Debug, thiserror::Error)]
pub enum TransportError {
    #[error("model.api_key_file: {path}: {reason}")]
    ApiKeyFile { path: PathBuf, reason: String },
    #[error("model.base_url: {url}: {reason}")]
    BaseUrl { url: String, reason: String },
}

/// Reads the key file. Trailing whitespace, including the newline most
/// editors append, is removed. An empty key is an error because every
/// provider would reject it with a less specific message.
pub fn read_api_key(path: &Path) -> Result<String, TransportError> {
    let text = std::fs::read_to_string(path).map_err(|e| TransportError::ApiKeyFile {
        path: path.to_path_buf(),
        reason: e.to_string(),
    })?;
    let key = text.trim_end();
    if key.is_empty() {
        return Err(TransportError::ApiKeyFile {
            path: path.to_path_buf(),
            reason: "file is empty".into(),
        });
    }
    Ok(key.to_string())
}

fn parse_base_url(
    provider_default: &str,
    base_url: Option<&str>,
) -> Result<http::Url, TransportError> {
    let text = base_url.unwrap_or(provider_default);
    http::Url::parse(text).map_err(|reason| TransportError::BaseUrl {
        url: text.to_string(),
        reason,
    })
}

// ---- the shared request loop -------------------------------------------------

/// One HTTP request, ready to send.
struct Exchange {
    /// The provider name used as the prefix of every error message.
    provider: &'static str,
    url: http::Url,
    headers: Vec<(String, String)>,
    body: Vec<u8>,
}

/// Turns a provider's event stream into chunks. Implementations are
/// provider-specific state machines; the loop in [`perform`] feeds them.
trait Decoder: Send {
    /// Handles one event, pushing any chunks it yields to `out`. Pushing a
    /// `Done` or `Error` ends the request.
    fn event(&mut self, event: &sse::Event, out: &mut dyn FnMut(Chunk));
    /// The terminal chunk for a body that ended cleanly without the decoder
    /// having produced one.
    fn end_of_stream(&mut self) -> Chunk;
}

fn is_terminal(chunk: &Chunk) -> bool {
    matches!(chunk, Chunk::Done { .. } | Chunk::Error { .. })
}

/// Forwards chunks from the blocking request to the caller's sink. Ensures
/// the sequence ends with exactly one terminal chunk even if the worker
/// fails.
async fn deliver(
    exchange: Exchange,
    decoder: Box<dyn Decoder>,
    sink: &mut (dyn foe_core::ChunkSink + Send),
) {
    let provider = exchange.provider;
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
    let worker = tokio::task::spawn_blocking(move || {
        perform(exchange, decoder, Outbox { tx, closed: false })
    });
    let mut terminal = false;
    while let Some(chunk) = rx.recv().await {
        terminal |= is_terminal(&chunk);
        sink.push(chunk);
    }
    let joined = worker.await;
    if !terminal {
        let reason = match joined {
            Ok(()) => "request ended without a final chunk".to_string(),
            Err(e) => format!("request worker failed: {e}"),
        };
        sink.push(Chunk::Error {
            message: format!("{provider}: {reason}"),
            retryable: true,
        });
    }
}

/// The sending side of the chunk channel. Drops everything after the first
/// terminal chunk and after the receiver has gone away.
struct Outbox {
    tx: tokio::sync::mpsc::UnboundedSender<Chunk>,
    closed: bool,
}

impl Outbox {
    fn push(&mut self, chunk: Chunk) {
        if self.closed {
            return;
        }
        let terminal = is_terminal(&chunk);
        if self.tx.send(chunk).is_err() || terminal {
            self.closed = true;
        }
    }
}

/// Sends the request and drives the decoder until a terminal chunk. Runs on
/// a blocking thread.
fn perform(exchange: Exchange, mut decoder: Box<dyn Decoder>, mut out: Outbox) {
    let provider = exchange.provider;
    let headers: Vec<(&str, &str)> = exchange
        .headers
        .iter()
        .map(|(k, v)| (k.as_str(), v.as_str()))
        .collect();
    let mut response = match http::post(&exchange.url, &headers, &exchange.body) {
        Ok(response) => response,
        Err(e) => {
            out.push(Chunk::Error {
                message: format!("{provider}: {e}"),
                retryable: e.retryable(),
            });
            return;
        }
    };
    if !(200..300).contains(&response.status) {
        out.push(status_error(provider, &mut response));
        return;
    }
    loop {
        match sse::next_event(&mut response.body) {
            Ok(Some(event)) => {
                decoder.event(&event, &mut |chunk| out.push(chunk));
                if out.closed {
                    return;
                }
            }
            Ok(None) => {
                out.push(decoder.end_of_stream());
                return;
            }
            Err(e) => {
                // Invalid UTF-8 is a malformed stream; anything else is the
                // connection failing under us.
                let retryable = e.kind() != std::io::ErrorKind::InvalidData;
                out.push(Chunk::Error {
                    message: format!("{provider}: reading response body: {e}"),
                    retryable,
                });
                return;
            }
        }
    }
}

/// Largest error body read for its message.
const MAX_ERROR_BODY: u64 = 64 * 1024;

/// Classifies a non-2xx response. Both providers send a JSON body of the
/// form `{"error": {"type": ..., "message": ...}}`; its fields are quoted
/// when present and the raw body otherwise.
/// https://docs.anthropic.com/en/api/errors
/// https://platform.openai.com/docs/guides/error-codes
fn status_error(provider: &str, response: &mut http::Response) -> Chunk {
    let status = response.status;
    // OpenAI sends `retry-after-ms` beside the standard `retry-after`;
    // only the delay-seconds form of the standard header is translated.
    let retry_after_ms = response
        .header("retry-after-ms")
        .and_then(|v| v.trim().parse::<u64>().ok())
        .or_else(|| {
            response
                .header("retry-after")
                .and_then(|v| v.trim().parse::<u64>().ok())
                .map(|s| s * 1000)
        });
    let mut text = String::new();
    let _ = (&mut response.body)
        .take(MAX_ERROR_BODY)
        .read_to_string(&mut text);
    let mut message = format!("{provider}: HTTP {status}: {}", describe_error_body(&text));
    if let Some(ms) = retry_after_ms {
        message.push_str(&format!(" retry_after_ms={ms}"));
    }
    let retryable = status == 429 || (500..600).contains(&status);
    Chunk::Error { message, retryable }
}

fn describe_error_body(text: &str) -> String {
    if let Ok(value) = serde_json::from_str::<serde_json::Value>(text) {
        let error = value.get("error").unwrap_or(&value);
        let kind = error
            .get("type")
            .or_else(|| error.get("code"))
            .and_then(|v| v.as_str());
        let detail = error.get("message").and_then(|v| v.as_str());
        match (kind, detail) {
            (Some(kind), Some(detail)) => return format!("{kind}: {detail}"),
            (None, Some(detail)) => return detail.to_string(),
            (Some(kind), None) => return kind.to_string(),
            (None, None) => {}
        }
    }
    let snippet: String = text
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .take(200)
        .collect();
    if snippet.is_empty() {
        "empty response body".to_string()
    } else {
        snippet
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A scratch directory under the workspace target directory, so that no
    /// environment variable decides where test files go.
    fn scratch(name: &str) -> PathBuf {
        let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../target/foe-transport-tests");
        std::fs::create_dir_all(&dir).unwrap();
        dir.join(name)
    }

    #[test]
    fn key_file_trailing_newline_is_trimmed() {
        let path = scratch("key-with-newline");
        std::fs::write(&path, "sk-test-123\n").unwrap();
        assert_eq!(read_api_key(&path).unwrap(), "sk-test-123");
        std::fs::write(&path, "sk-test-123\r\n\n").unwrap();
        assert_eq!(read_api_key(&path).unwrap(), "sk-test-123");
    }

    #[test]
    fn empty_and_missing_key_files_name_the_key() {
        let path = scratch("key-empty");
        std::fs::write(&path, "\n").unwrap();
        let err = read_api_key(&path).unwrap_err().to_string();
        assert!(err.starts_with("model.api_key_file: "), "{err}");
        assert!(err.ends_with("file is empty"), "{err}");
        let missing = scratch("key-missing-does-not-exist");
        let err = read_api_key(&missing).unwrap_err().to_string();
        assert!(err.starts_with("model.api_key_file: "), "{err}");
        assert!(err.contains("key-missing-does-not-exist"), "{err}");
    }

    #[test]
    fn bad_base_url_names_the_key() {
        let err = parse_base_url("https://api.anthropic.com", Some("localhost:11434/v1"))
            .unwrap_err()
            .to_string();
        assert_eq!(
            err,
            "model.base_url: localhost:11434/v1: scheme must be http or https"
        );
    }

    #[test]
    fn error_body_description_prefers_structured_fields() {
        assert_eq!(
            describe_error_body(
                r#"{"type":"error","error":{"type":"rate_limit_error","message":"slow down"}}"#
            ),
            "rate_limit_error: slow down"
        );
        assert_eq!(
            describe_error_body(r#"{"error":{"message":"bad","code":"invalid_api_key"}}"#),
            "invalid_api_key: bad"
        );
        assert_eq!(describe_error_body(r#"{"error":{"message":"bad"}}"#), "bad");
        assert_eq!(
            describe_error_body("<html>\n  502 Bad Gateway\n</html>"),
            "<html> 502 Bad Gateway </html>"
        );
        assert_eq!(describe_error_body(""), "empty response body");
    }
}
