//! Wire formats: how one model request is serialized for a provider and how
//! the provider's event stream is turned into the chunk vocabulary of
//! `docs/protocol.md`.
//!
//! A format knows nothing about credentials or hosts. It is paired with a
//! credential source and a URL by the provider table in `providers.rs`.

use foe_core::{Chunk, ModelRequestBody};
use serde_json::Value;

use crate::sse;

pub mod chat;
pub mod gemini;
pub mod messages;
pub mod responses;

/// One wire format.
pub trait Format: Send + Sync {
    /// The JSON body of one request.
    fn body(&self, req: &ModelRequestBody) -> Value;
    /// A fresh translator for one response stream.
    fn decoder(&self) -> Box<dyn Decoder>;
}

/// Turns a provider's event stream into chunks. Implementations are
/// provider-specific state machines; the request loop in the crate root
/// feeds them one event at a time.
pub trait Decoder: Send {
    /// Handles one event, pushing any chunks it yields to `out`. Pushing a
    /// `Done` or `Error` ends the request.
    fn event(&mut self, event: &sse::Event, out: &mut dyn FnMut(Chunk));
    /// The terminal chunk for a body that ended cleanly without the decoder
    /// having produced one.
    fn end_of_stream(&mut self) -> Chunk;
}

/// A non-retryable error chunk prefixed with the provider name.
pub(crate) fn fail(provider: &str, message: impl std::fmt::Display) -> Chunk {
    Chunk::Error { message: format!("{provider}: {message}"), retryable: false }
}
