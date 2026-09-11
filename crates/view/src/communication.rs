//! Communication evidence joined across sender, lead, and recipient logs.

use foe_log::{ContentBlock, EventData, InboxSource};
use std::collections::{hash_map::DefaultHasher, BTreeMap};
use std::hash::{Hash, Hasher};

const QUEUED: u8 = 1;
const RECEIVED: u8 = 2;
const ACKNOWLEDGED: u8 = 4;
const CONFIRMED: u8 = 8;

#[derive(Default)]
pub(crate) struct Messages {
    pub names: BTreeMap<String, String>,
    parents: BTreeMap<String, String>,
    calls: BTreeMap<(String, String), Message>,
    records: Vec<Message>,
}

#[derive(Default)]
pub(crate) struct Message {
    pub from: String,
    pub to: String,
    pub kind: &'static str,
    pub body: Option<String>,
    pub delivered: bool,
    pub shown: bool,
    owner: String,
    id: String,
    digest: u64,
    stages: u8,
}

fn content(blocks: &[ContentBlock], id: &str) -> String {
    let guidance = format!("Answer this with send, reply_to {id}.");
    let skip =
        usize::from(!id.is_empty() && matches!(blocks.last(), Some(ContentBlock::Text { text }) if text == &guidance));
    blocks[..blocks.len() - skip]
        .iter()
        .map(|block| match block {
            ContentBlock::Text { text } => text.as_str(),
            ContentBlock::Image { .. } => "[image]",
        })
        .collect::<Vec<_>>()
        .join("\n\n")
}

impl Messages {
    pub fn name(&self, id: &str) -> String {
        self.names.get(id).cloned().unwrap_or_else(|| id.into())
    }

    pub fn read(&mut self, episode: &str, event: &EventData) -> Option<usize> {
        let mut message = Message { owner: episode.into(), ..Message::default() };
        match event {
            EventData::EpisodeStart(start) => {
                self.names
                    .entry(episode.into())
                    .or_insert_with(|| start.contract["name"].as_str().unwrap_or(episode).into());
                self.parents.insert(episode.into(), start.parent_id.clone().unwrap_or_default());
                return None;
            }
            EventData::TeamRoster { member_id, name, .. } => {
                self.names.insert(member_id.clone(), name.clone());
                return None;
            }
            EventData::AssistantMessage(answer) => {
                for call in &answer.tool_calls {
                    let kind = match call.name.as_str() {
                        "ask" => "ask",
                        "notify" => "notify",
                        "send" if call.args.get("reply_to").is_some() => "reply",
                        "send" => "send",
                        _ => continue,
                    };
                    let Some(body) = call.args["content"].as_str() else { continue };
                    self.calls.insert(
                        (episode.into(), call.id.clone()),
                        Message {
                            from: episode.into(),
                            kind,
                            body: Some(body.into()),
                            to: if kind == "notify" {
                                self.parents.get(episode).cloned().unwrap_or_default()
                            } else {
                                call.args["to"].as_str().unwrap_or_default().into()
                            },
                            ..Message::default()
                        },
                    );
                }
                return None;
            }
            EventData::ToolResult(result) => {
                let call = self.calls.remove(&(episode.into(), result.call_id.clone()))?;
                if result.is_error || result.synthetic {
                    return None;
                }
                message = call;
                message.id = result.value["message_id"].as_str().unwrap_or_default().into();
                if message.kind != "notify" && message.id.is_empty() {
                    return None;
                }
                message.stages = ACKNOWLEDGED;
            }
            EventData::TeamMessage { message_id, from, to, content: blocks } => {
                message.id = message_id.clone();
                message.from = from.clone();
                message.to = to.clone();
                message.body = Some(content(blocks, message_id));
                message.kind = "message";
                message.stages = QUEUED;
            }
            EventData::TeamDelivered { message_id, to } => {
                message.id = message_id.clone();
                message.to = to.clone();
                message.stages = CONFIRMED;
            }
            EventData::InboxItem(item) => {
                message.kind = if item.synthetic {
                    "default"
                } else {
                    match item.source {
                        InboxSource::Peer => "send",
                        InboxSource::Request => "ask",
                        InboxSource::Response => "reply",
                        InboxSource::Parent => "steer",
                        InboxSource::Child => "notify",
                        _ => return None,
                    }
                };
                message.from = item.from.clone().unwrap_or_default();
                message.to = episode.into();
                message.id = item.message_id.clone().unwrap_or_default();
                message.body = Some(content(&item.content, &message.id));
                message.stages = RECEIVED;
            }
            _ => return None,
        }
        let mut hash = DefaultHasher::new();
        message.body.hash(&mut hash);
        message.digest = hash.finish();
        message.delivered = message.stages & (RECEIVED | CONFIRMED) != 0;
        let known = self.records.iter().position(|held| {
            message.kind != "default"
                && held.kind != "default"
                && held.stages & message.stages == 0
                && held.id == message.id
                && (message.from.is_empty() || held.from == message.from)
                && (message.stages == ACKNOWLEDGED || held.stages == ACKNOWLEDGED || held.to == message.to)
                && if message.stages == CONFIRMED {
                    held.stages & QUEUED != 0 && held.owner == message.owner
                } else {
                    held.digest == message.digest
                        && (held.kind == "message" || message.kind == "message" || held.kind == message.kind)
                }
        });
        if let Some(index) = known {
            let held = &mut self.records[index];
            let changed = message.kind != "message" && !message.kind.is_empty() && message.kind != held.kind;
            if held.shown && (changed || message.delivered && !held.delivered) {
                held.body = Some(String::new());
            }
            if message.stages == QUEUED {
                held.owner = message.owner;
            }
            held.stages |= message.stages;
            if message.kind != "message" && !message.kind.is_empty() {
                held.kind = message.kind;
            }
            if message.stages != ACKNOWLEDGED {
                held.to = message.to;
            }
            held.delivered |= message.delivered;
            Some(index)
        } else if message.stages != CONFIRMED {
            self.records.push(message);
            Some(self.records.len() - 1)
        } else {
            None
        }
    }

    pub fn take(&mut self, index: usize) -> Option<Message> {
        let record = &mut self.records[index];
        if record.kind == "notify" && record.stages & ACKNOWLEDGED == 0 {
            return None;
        }
        let body = record.body.take()?;
        let shown = record.shown;
        record.shown = true;
        Some(Message {
            from: record.from.clone(),
            to: record.to.clone(),
            kind: record.kind,
            body: Some(body),
            delivered: record.delivered,
            shown,
            ..Message::default()
        })
    }
}
