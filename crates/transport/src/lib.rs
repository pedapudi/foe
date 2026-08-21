//! Built-in model clients, used when a configuration has a `model` block.
//!
//! This crate holds the pieces every client shares: a minimal HTTP/1.1
//! client that streams a response body, and a reader for server-sent
//! events. Both are specified in their modules.
//!
//! What the crate guarantees:
//!
//! - No environment variable is read, including proxy variables; there is
//!   no proxy support.
//! - TLS trusts a compiled-in copy of Mozilla's root certificates; the
//!   system certificate store is never opened.

#![forbid(unsafe_code)]

pub mod http;
pub mod sse;
#[cfg(test)]
mod testserver;
