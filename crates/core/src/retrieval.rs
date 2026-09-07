//! Immutable tool-rendering archives and bounded retrieval from episode evidence.

use crate::loop_::Log;
use crate::{CallCtx, RuntimeError, Tool, ToolValue};
use foe_contract::{fingerprint::sha256_hex, harness_text as text, Effect, ToolSpec};
use foe_log::{Event, EventData, RenderingArchive};
use serde_json::{json, Value};
use std::path::Path;
use std::sync::Arc;

pub const NAME: &str = "retrieve";
/// Maximum bytes in the model-facing rendering of one retrieval response.
pub const RENDERED_BYTES: usize = 16_000;

pub fn spec() -> ToolSpec {
    ToolSpec {
        name: NAME.into(),
        description: text::RETRIEVE_DESCRIPTION.into(),
        instruction: None,
        params: json!({
            "type": "object",
            "properties": { "cursor": { "type": "string", "minLength": 1 } },
            "required": ["cursor"],
            "additionalProperties": false
        }),
        effect: Effect::Pure,
    }
}

pub fn tool(log: Arc<Log>) -> Box<dyn Tool> {
    Box::new(RetrieveTool { spec: spec(), log })
}

/// A cursor for `offset` in the complete rendering of one result.
pub fn cursor(step: u32, call_id: &str, rendering: &str, offset: usize) -> String {
    cursor_for_digest(step, call_id, &digest(rendering.as_bytes()), offset)
}

/// A cursor built from the complete rendering digest recorded in the log.
/// Context policies use this form so archived bytes remain outside their
/// projection boundary.
pub fn cursor_for_digest(step: u32, call_id: &str, digest: &str, offset: usize) -> String {
    let key = source_key(step, call_id, digest);
    let body = format!("r1.{key}.{offset:x}");
    format!("{body}.{}", sha256_hex(body.as_bytes()))
}

pub fn digest(bytes: &[u8]) -> String {
    format!("sha256:{}", sha256_hex(bytes))
}

fn source_key(step: u32, call_id: &str, digest: &str) -> String {
    sha256_hex(format!("{step}\n{}\n{call_id}\n{digest}", call_id.len()).as_bytes())
}

struct ParsedCursor {
    key: String,
    offset: usize,
}

fn parse_cursor(value: &str) -> Result<ParsedCursor, String> {
    let fields: Vec<&str> = value.split('.').collect();
    let ["r1", key, offset, checksum] = fields.as_slice() else {
        return Err(text::RETRIEVE_INVALID.into());
    };
    if key.len() != 64 || checksum.len() != 64 || !key.bytes().chain(checksum.bytes()).all(|b| b.is_ascii_hexdigit()) {
        return Err(text::RETRIEVE_INVALID.into());
    }
    let parsed = usize::from_str_radix(offset, 16).map_err(|_| text::RETRIEVE_INVALID.to_string())?;
    let body = format!("r1.{}.{parsed:x}", key.to_ascii_lowercase());
    if value != format!("{body}.{}", sha256_hex(body.as_bytes())) {
        return Err(text::RETRIEVE_INVALID.into());
    }
    Ok(ParsedCursor { key: key.to_string(), offset: parsed })
}

struct Source {
    seq: u64,
    step: u32,
    call_id: String,
    digest: String,
    bytes: Vec<u8>,
}

fn source(events: &[Event], spill_dir: &Path, current_step: u32, key: &str) -> Result<Source, String> {
    for (index, event) in events.iter().enumerate() {
        let EventData::ToolResult(result) = &event.data else { continue };
        if result.step >= current_step {
            continue;
        }
        let archive = index.checked_sub(1).and_then(|i| match &events[i].data {
            EventData::ToolRenderingArchive(archive)
                if archive.step == result.step && archive.call_id == result.call_id =>
            {
                Some((events[i].seq, archive))
            }
            _ => None,
        });
        let digest = archive.map_or_else(|| digest(result.rendered.as_bytes()), |(_, archive)| archive.digest.clone());
        if source_key(result.step, &result.call_id, &digest) != key {
            continue;
        }
        let bytes = match archive {
            Some((seq, archive)) => {
                foe_log::artifact::read_rendering(spill_dir, seq, archive).map_err(|e| format!("retrieve: {e}"))?
            }
            None => result.rendered.as_bytes().to_vec(),
        };
        std::str::from_utf8(&bytes).map_err(|e| format!("retrieve at event {}: {e}", event.seq))?;
        return Ok(Source { seq: event.seq, step: result.step, call_id: result.call_id.clone(), digest, bytes });
    }
    Err(text::RETRIEVE_UNAVAILABLE.into())
}

pub struct ArchivedRendering {
    pub complete: String,
    pub digest: String,
}

pub fn retain(
    spill_dir: &Path,
    step: u32,
    call_id: &str,
    archived: &ArchivedRendering,
) -> Result<RenderingArchive, RuntimeError> {
    let Some(file) = foe_log::digest::rendering_file(&archived.digest) else {
        return Err(RuntimeError::Protocol("tool rendering archive: digest is invalid".into()));
    };
    foe_log::artifact::retain(&spill_dir.join(&file), archived.complete.as_bytes()).map_err(foe_log::LogError::Io)?;
    Ok(RenderingArchive {
        step,
        call_id: call_id.into(),
        file,
        digest: archived.digest.clone(),
        bytes: archived.complete.len() as u64,
    })
}

struct RetrieveTool {
    spec: ToolSpec,
    log: Arc<Log>,
}

#[async_trait::async_trait]
impl Tool for RetrieveTool {
    fn spec(&self) -> &ToolSpec {
        &self.spec
    }

    async fn call(&self, args: Value, ctx: &CallCtx) -> ToolValue {
        let Some(raw) = args.get("cursor").and_then(Value::as_str) else {
            return ToolValue::invalid(text::RETRIEVE_INVALID);
        };
        let parsed = match parse_cursor(raw) {
            Ok(parsed) => parsed,
            Err(error) => return ToolValue::invalid(error),
        };
        let events = self.log.events();
        let source = match source(&events, &ctx.spill_dir, ctx.step, &parsed.key) {
            Ok(source) => source,
            Err(error) => return ToolValue::error(error),
        };
        let complete = std::str::from_utf8(&source.bytes).expect("source validation checked UTF-8");
        if parsed.offset > source.bytes.len() || !complete.is_char_boundary(parsed.offset) {
            return ToolValue::error(text::RETRIEVE_OFFSET);
        }
        let mut end = parsed.offset.saturating_add(RENDERED_BYTES).min(source.bytes.len());
        while !complete.is_char_boundary(end) {
            end -= 1;
        }
        let (content, next, rendered) = loop {
            let content = complete[parsed.offset..end].to_string();
            let next = (end < source.bytes.len())
                .then(|| cursor_for_digest(source.step, &source.call_id, &source.digest, end));
            let rendered = match &next {
                Some(cursor) => text::fill(text::RETRIEVE_MORE, &[("content", &content), ("cursor", cursor)]),
                None => content.clone(),
            };
            if rendered.len() <= RENDERED_BYTES {
                break (content, next, rendered);
            }
            end = end.saturating_sub(rendered.len() - RENDERED_BYTES);
            while !complete.is_char_boundary(end) {
                end -= 1;
            }
        };
        let remaining = next.is_some();
        ToolValue::ok(
            json!({
                "source_seq": source.seq,
                "digest": source.digest,
                "start_byte": parsed.offset,
                "end_byte": end,
                "total_bytes": source.bytes.len(),
                "remaining": remaining,
                "next_cursor": next,
                "content": content
            }),
            rendered,
        )
        .subject(format!(
            "result at seq {} bytes {}-{} of {}",
            source.seq,
            parsed.offset,
            end,
            source.bytes.len()
        ))
    }
}

#[cfg(test)]
#[path = "retrieval_test.rs"]
mod tests;
