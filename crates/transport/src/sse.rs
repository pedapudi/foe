//! Server-sent events, read one event at a time from a buffered body.
//!
//! The format is the WHATWG event stream: lines of `field: value`, an
//! event dispatched at each empty line, `:` lines as comments.
//! https://html.spec.whatwg.org/multipage/server-sent-events.html#parsing-an-event-stream
//!
//! Only the `event` and `data` fields are kept. `id` and `retry` have no
//! use here because a request is never resumed.

use std::io::{self, BufRead};

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
pub fn next_event<R: BufRead>(reader: &mut R) -> io::Result<Option<Event>> {
    let mut event = Event::default();
    let mut line = String::new();
    loop {
        line.clear();
        if reader.read_line(&mut line)? == 0 {
            return Ok(None);
        }
        let text = line.trim_end_matches(['\r', '\n']);
        if text.is_empty() {
            if event.data.is_empty() {
                event.name.clear();
                continue;
            }
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
            "event" => event.name = value.to_string(),
            "data" => {
                if !event.data.is_empty() {
                    event.data.push('\n');
                }
                event.data.push_str(value);
            }
            _ => {}
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn all(text: &str) -> Vec<Event> {
        let mut reader = io::Cursor::new(text.as_bytes());
        let mut out = Vec::new();
        while let Some(e) = next_event(&mut reader).unwrap() {
            out.push(e);
        }
        out
    }

    #[test]
    fn parses_named_and_unnamed_events() {
        let events = all("event: ping\ndata: {\"type\":\"ping\"}\n\ndata: a\ndata: b\n\n");
        assert_eq!(events.len(), 2);
        assert_eq!(
            events[0],
            Event {
                name: "ping".into(),
                data: "{\"type\":\"ping\"}".into()
            }
        );
        assert_eq!(
            events[1],
            Event {
                name: String::new(),
                data: "a\nb".into()
            }
        );
    }

    #[test]
    fn ignores_comments_crlf_and_events_without_data() {
        let events = all(": keep-alive\r\n\r\nevent: orphan\r\n\r\ndata:[DONE]\r\n\r\n");
        assert_eq!(
            events,
            vec![Event {
                name: String::new(),
                data: "[DONE]".into()
            }]
        );
    }

    #[test]
    fn drops_incomplete_trailing_event() {
        assert!(all("data: partial").is_empty());
        assert!(all("").is_empty());
    }
}
