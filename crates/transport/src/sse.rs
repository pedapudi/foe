//! Server-sent events, read one event at a time from a buffered body.
//!
//! The format is the WHATWG event stream: lines of `field: value`, an
//! event dispatched at each empty line, `:` lines as comments.
//! https://html.spec.whatwg.org/multipage/server-sent-events.html#parsing-an-event-stream
//!
//! Only the `event` and `data` fields are kept. `id` and `retry` have no
//! use here because a request is never resumed.

use std::io;
use tokio::io::{AsyncBufRead, AsyncBufReadExt, AsyncReadExt};

pub const MAX_EVENT_BYTES: usize = 1024 * 1024;

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Event {
    /// The `event` field, or empty when the server sent none.
    pub name: String,
    /// Every `data` line joined with `\n`.
    pub data: String,
}

/// Reads the next event. Returns `Ok(None)` at the end of the body. An
/// event that has no `data` line is not dispatched, as the specification
/// requires; an incomplete event cut off by the end of the body is dropped.
pub async fn next_event<R: AsyncBufRead + Unpin>(reader: &mut R) -> io::Result<Option<Event>> {
    let mut event = Event::default();
    let mut line = String::new();
    loop {
        line.clear();
        if (&mut *reader).take(MAX_EVENT_BYTES as u64 + 1).read_line(&mut line).await? == 0 {
            return Ok(None);
        }
        if line.len() > MAX_EVENT_BYTES {
            return Err(too_large());
        }
        let text = line.trim_end_matches(['\r', '\n']);
        if text.is_empty() {
            if event.data.is_empty() {
                event.name.clear();
                continue;
            }
            event.data.pop();
            return Ok(Some(event));
        }
        if text.starts_with(':') {
            continue;
        }
        let (field, value) = match text.split_once(':') {
            Some((field, value)) => (field, value.strip_prefix(' ').unwrap_or(value)),
            None => (text, ""),
        };
        match field {
            "event" => {
                if value.len().saturating_add(event.data.len()) > MAX_EVENT_BYTES {
                    return Err(too_large());
                }
                event.name = value.to_string();
            }
            "data" => {
                if event.name.len().saturating_add(event.data.len()).saturating_add(value.len()) >= MAX_EVENT_BYTES {
                    return Err(too_large());
                }
                event.data.push_str(value);
                event.data.push('\n');
            }
            _ => {}
        }
    }
}

fn too_large() -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, format!("SSE line or event exceeds {MAX_EVENT_BYTES} bytes"))
}

#[cfg(test)]
mod tests {
    use super::*;

    async fn all(text: &str) -> Vec<Event> {
        let mut reader = io::Cursor::new(text.as_bytes());
        let mut out = Vec::new();
        while let Some(e) = next_event(&mut reader).await.unwrap() {
            out.push(e);
        }
        out
    }

    #[tokio::test]
    async fn parses_named_and_unnamed_events() {
        let events = all("event: ping\ndata: {\"type\":\"ping\"}\n\ndata: a\ndata: b\n\n").await;
        assert_eq!(events.len(), 2);
        assert_eq!(events[0], Event { name: "ping".into(), data: "{\"type\":\"ping\"}".into() });
        assert_eq!(events[1], Event { name: String::new(), data: "a\nb".into() });
    }

    #[tokio::test]
    async fn ignores_comments_crlf_and_events_without_data() {
        let events = all(": keep-alive\r\n\r\nevent: orphan\r\n\r\ndata:[DONE]\r\n\r\n").await;
        assert_eq!(events, vec![Event { name: String::new(), data: "[DONE]".into() }]);
    }

    #[tokio::test]
    async fn drops_incomplete_trailing_event() {
        assert!(all("data: partial").await.is_empty());
        assert!(all("").await.is_empty());
    }

    #[tokio::test]
    async fn empty_data_fields_preserve_event_and_line_boundaries() {
        // docs/models.md: event data follows the server-sent event format.
        let events = all("data:\n\ndata:\ndata: a\ndata:\n\n").await;
        assert_eq!(events.iter().map(|event| event.data.as_str()).collect::<Vec<_>>(), ["", "\na\n"]);
    }

    #[tokio::test]
    async fn line_and_event_limits_apply_before_the_next_delimiter() {
        // docs/models.md: every line and retained event is bounded at one MiB.
        for text in [
            format!("data: {}", "a".repeat(MAX_EVENT_BYTES)),
            format!(": {}", "a".repeat(MAX_EVENT_BYTES)),
            "data: abcdefghijklmnopqrstuvwxyz\n".repeat(MAX_EVENT_BYTES / 26 + 1),
            format!("event: {}\ndata: {}\n", "a".repeat(MAX_EVENT_BYTES / 2), "b".repeat(MAX_EVENT_BYTES / 2)),
        ] {
            let mut reader = io::Cursor::new(text.as_bytes());
            let error = next_event(&mut reader).await.unwrap_err();
            assert_eq!(error.kind(), io::ErrorKind::InvalidData);
            assert!(error.to_string().contains("1048576 bytes"));
        }
        let mut reader = io::Cursor::new(vec![b'x'; 2 * MAX_EVENT_BYTES]);
        next_event(&mut reader).await.unwrap_err();
        assert_eq!(reader.position(), MAX_EVENT_BYTES as u64 + 1);
    }

    #[tokio::test]
    async fn malformed_utf8_is_nonretryable() {
        // docs/models.md: malformed event text is a permanent response error.
        let mut reader = io::Cursor::new(b"data: \xff\n\n");
        assert_eq!(next_event(&mut reader).await.unwrap_err().kind(), io::ErrorKind::InvalidData);
    }
}
