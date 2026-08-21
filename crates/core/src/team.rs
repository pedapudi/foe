//! Team fold over the lead log: roster, message queue, delivery records.
//!
//! A team is the set of children one episode has spawned; that episode is
//! the lead. The lead's log holds the roster and the queue of messages
//! between members, and [`fold`] derives both from it. No other team state
//! exists. See docs/design.md "Subagents and teams".
//!
//! The lead holds the `spawn` and `steer` built-ins. A member's `notify`,
//! `send`, and `team` are host tools from the member's point of view, and
//! the lead is the host that answers them: a `notify` becomes an inbox item
//! in the lead's log with source `child`; a `send` becomes a `team/message`
//! in the lead's log followed by an inbox item with source `peer` written
//! to the target; `team` returns the roster. When the target records the
//! peer item, the lead sees it and writes `team/delivered`. See
//! docs/protocol.md "Children".

use crate::protocol::InboxSink;
use crate::spawn::{ChildObserver, Router};
use crate::{CallCtx, CapError, Effect, HostToolDef, SpawnHandle, SpawnRequest, Spawner, Tool, ToolSpec, ToolValue};
use foe_log::{
    BudgetAmount, ContentBlock, Event, EventData, InboxItem, InboxSource, MemberPhase, Outcome, SpawnContext,
};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

/// This episode's own log, as the lead of its team: appends team events and
/// reads everything written so far.
pub trait LeadLog: Send + Sync {
    fn append(&self, event: EventData);
    fn events(&self) -> Vec<Event>;
}

#[derive(Debug, Clone, PartialEq)]
pub struct Member {
    pub member_id: String,
    pub name: String,
    pub description: String,
    pub phase: MemberPhase,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Queued {
    pub message_id: String,
    pub from: String,
    pub to: String,
    pub content: Vec<ContentBlock>,
}

/// Team state folded from a lead's log.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct TeamState {
    /// Members in order of first appearance, each at its latest phase.
    pub roster: Vec<Member>,
    /// Every message ever queued, in order.
    pub queue: Vec<Queued>,
    /// Ids of messages whose target recorded them.
    pub delivered: BTreeSet<String>,
}

impl TeamState {
    pub fn member(&self, name: &str) -> Option<&Member> {
        self.roster.iter().find(|m| m.name == name)
    }

    pub fn member_by_id(&self, member_id: &str) -> Option<&Member> {
        self.roster.iter().find(|m| m.member_id == member_id)
    }

    /// Messages queued and never delivered; redelivered when the target
    /// restarts.
    pub fn undelivered(&self) -> impl Iterator<Item = &Queued> {
        self.queue.iter().filter(|m| !self.delivered.contains(&m.message_id))
    }

    fn roster_value(&self) -> serde_json::Value {
        let rows = self.roster.iter().map(|m| {
            serde_json::json!({ "member_id": m.member_id, "name": m.name, "description": m.description, "phase": m.phase })
        });
        serde_json::Value::Array(rows.collect())
    }

    fn roster_text(&self) -> String {
        if self.roster.is_empty() {
            return "no members".to_string();
        }
        let rows: Vec<String> =
            self.roster.iter().map(|m| format!("{}\t{}\t{}", m.name, m.member_id, kebab(&m.phase))).collect();
        rows.join("\n")
    }
}

/// Folds the team events of a lead's log. Events copied from another log by
/// seeding, which precede `seed/end`, belong to that log's episode and are
/// skipped.
pub fn fold(events: &[Event]) -> TeamState {
    let live_from = events.iter().rev().find(|e| matches!(e.data, EventData::SeedEnd {})).map_or(0, |e| e.seq + 1);
    let mut state = TeamState::default();
    for event in events.iter().filter(|e| e.seq >= live_from) {
        match &event.data {
            EventData::TeamRoster { member_id, name, description, phase } => {
                match state.roster.iter_mut().find(|m| m.member_id == *member_id) {
                    Some(m) => m.phase = *phase,
                    None => state.roster.push(Member {
                        member_id: member_id.clone(),
                        name: name.clone(),
                        description: description.clone(),
                        phase: *phase,
                    }),
                }
            }
            EventData::TeamMessage { message_id, from, to, content } => state.queue.push(Queued {
                message_id: message_id.clone(),
                from: from.clone(),
                to: to.clone(),
                content: content.clone(),
            }),
            EventData::TeamDelivered { message_id, .. } => {
                state.delivered.insert(message_id.clone());
            }
            _ => {}
        }
    }
    state
}

/// The member's half of the deduplication rule: a peer message whose
/// `message_id` is already recorded in this log is dropped.
pub fn is_duplicate(events: &[Event], message_id: &str) -> bool {
    events.iter().any(|e| match &e.data {
        EventData::InboxItem(item) => item.message_id.as_deref() == Some(message_id),
        _ => false,
    })
}

/// The lead's side of a team: writes roster and queue events to the lead's
/// log, delivers messages to members, and answers members' host tool calls.
pub struct Team {
    lead_id: String,
    log: Arc<dyn LeadLog>,
    inbox: Arc<dyn InboxSink>,
    router: Arc<Router>,
    /// Held across a spawn and across every roster write, so that a member's
    /// `provisioning` entry precedes its `active` one.
    roster_lock: Mutex<()>,
    messages: AtomicU64,
}

impl Team {
    pub fn new(lead_id: String, log: Arc<dyn LeadLog>, inbox: Arc<dyn InboxSink>, router: Arc<Router>) -> Self {
        Team { lead_id, log, inbox, router, roster_lock: Mutex::new(()), messages: AtomicU64::new(0) }
    }

    pub fn state(&self) -> TeamState {
        fold(&self.log.events())
    }

    /// Starts a child and records it in the roster as provisioning.
    pub fn spawn(&self, spawner: &dyn Spawner, req: SpawnRequest, name: &str) -> Result<SpawnHandle, CapError> {
        let _guard = self.roster_lock.lock().unwrap();
        if self.state().member(name).is_some() {
            return Err(CapError::Invalid(format!("a member named {name} already exists")));
        }
        let description = req.task.clone();
        let handle = spawner.spawn(req)?;
        self.log.append(EventData::TeamRoster {
            member_id: handle.child_id.clone(),
            name: name.to_string(),
            description,
            phase: MemberPhase::Provisioning,
        });
        Ok(handle)
    }

    /// Writes an inbox item to a member, addressed by roster name.
    pub fn steer(&self, name: &str, content: Vec<ContentBlock>) -> Result<(), CapError> {
        let state = self.state();
        let member = state.member(name).ok_or_else(|| CapError::Invalid(format!("no member named {name}")))?;
        let item =
            InboxItem { source: InboxSource::Parent, content, from: Some(self.lead_id.clone()), message_id: None };
        self.router.send_inbox(&member.member_id, &item)
    }

    fn set_phase(&self, member_id: &str, phase: MemberPhase) {
        let _guard = self.roster_lock.lock().unwrap();
        let state = self.state();
        let (name, description) = state
            .member_by_id(member_id)
            .map(|m| (m.name.clone(), m.description.clone()))
            .unwrap_or_else(|| (member_id.to_string(), String::new()));
        self.log.append(EventData::TeamRoster { member_id: member_id.to_string(), name, description, phase });
    }

    /// Queues a message from one member to another and attempts delivery. A
    /// failed delivery leaves the message queued without a delivery record;
    /// the fold reports it as undelivered.
    fn send(&self, from: &str, to_name: &str, content: Vec<ContentBlock>) -> Result<String, CapError> {
        let state = self.state();
        let target = state.member(to_name).ok_or_else(|| CapError::Invalid(format!("no member named {to_name}")))?;
        let message_id = format!("tm_{:02}", self.messages.fetch_add(1, Ordering::SeqCst) + 1);
        self.log.append(EventData::TeamMessage {
            message_id: message_id.clone(),
            from: from.to_string(),
            to: target.member_id.clone(),
            content: content.clone(),
        });
        let item = InboxItem {
            source: InboxSource::Peer,
            content,
            from: Some(from.to_string()),
            message_id: Some(message_id.clone()),
        };
        let _ = self.router.send_inbox(&target.member_id, &item);
        Ok(message_id)
    }
}

impl ChildObserver for Team {
    fn observe(&self, child_id: &str, event: &Event) {
        match &event.data {
            EventData::EpisodeStart(_) => self.set_phase(child_id, MemberPhase::Active),
            EventData::EpisodeEnd { outcome } => {
                if matches!(outcome, Outcome::Failed { .. }) {
                    self.set_phase(child_id, MemberPhase::Failed);
                }
                let name = self.state().member_by_id(child_id).map(|m| m.name.clone()).unwrap_or_default();
                let text = format!("{name} ({child_id}) ended: {}", render_outcome(outcome));
                self.inbox.append(InboxItem {
                    source: InboxSource::Child,
                    content: text_content(&text),
                    from: Some(child_id.to_string()),
                    message_id: None,
                });
            }
            EventData::InboxItem(item) if item.source == InboxSource::Peer => {
                if let Some(id) = &item.message_id {
                    self.log.append(EventData::TeamDelivered { message_id: id.clone(), to: child_id.to_string() });
                }
            }
            _ => {}
        }
    }

    fn host_call(&self, child_id: &str, name: &str, args: &serde_json::Value) -> Option<ToolValue> {
        let kind = match name {
            "notify" => Kind::Notify,
            "send" => Kind::Send,
            "team" => Kind::Team,
            _ => return None,
        };
        let content = match kind {
            Kind::Team => Vec::new(),
            _ => match arg(args, "content") {
                Ok(text) => text_content(text),
                Err(e) => return Some(e),
            },
        };
        Some(match kind {
            Kind::Notify => {
                let item = InboxItem {
                    source: InboxSource::Child,
                    content,
                    from: Some(child_id.to_string()),
                    message_id: None,
                };
                self.inbox.append(item);
                ToolValue::ok(serde_json::json!({ "sent": true }), "sent")
            }
            Kind::Send => match arg(args, "to").map(|to| (to, self.send(child_id, to, content))) {
                Ok((to, Ok(message_id))) => {
                    ToolValue::ok(serde_json::json!({ "to": to, "message_id": message_id }), format!("sent to {to}"))
                }
                Ok((_, Err(e))) => ToolValue::error(format!("send: {e}")),
                Err(e) => e,
            },
            Kind::Team => {
                let state = self.state();
                ToolValue::ok(state.roster_value(), state.roster_text())
            }
            Kind::Spawn | Kind::Steer => unreachable!("resolved above"),
        })
    }
}

fn text_content(text: &str) -> Vec<ContentBlock> {
    vec![ContentBlock::Text { text: text.to_string() }]
}

fn render_outcome(outcome: &Outcome) -> String {
    match outcome {
        Outcome::Completed { value } => format!("completed with {value}"),
        Outcome::Blocked { code, message } => format!("blocked ({}): {message}", kebab(code)),
        Outcome::Exhausted { limit } => format!("exhausted its {} budget", kebab(limit)),
        Outcome::Failed { error } => format!("failed: {error}"),
    }
}

fn kebab(value: &impl serde::Serialize) -> String {
    serde_json::to_value(value).ok().and_then(|v| v.as_str().map(str::to_string)).unwrap_or_default()
}

fn arg<'a>(args: &'a serde_json::Value, key: &str) -> Result<&'a str, ToolValue> {
    args.get(key).and_then(|v| v.as_str()).ok_or_else(|| ToolValue::error(format!("{key}: a string is required")))
}

// ---- tools --------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Kind {
    Spawn,
    Steer,
    Notify,
    Send,
    Team,
}

impl Kind {
    fn name(self) -> &'static str {
        match self {
            Kind::Spawn => "spawn",
            Kind::Steer => "steer",
            Kind::Notify => "notify",
            Kind::Send => "send",
            Kind::Team => "team",
        }
    }

    fn spec(self) -> ToolSpec {
        let string = |description: &str| serde_json::json!({ "type": "string", "description": description });
        let object = |props: serde_json::Value, required: &[&str]| serde_json::json!({ "type": "object", "properties": props, "required": required, "additionalProperties": false });
        let (description, params, effect) = match self {
            Kind::Spawn => (
                "Start a child episode running one of the declared programs. Returns at once; the child's result arrives later as a message from it.",
                object(
                    serde_json::json!({
                        "program": string("name of a program listed in grants.spawn"),
                        "task": string("what the child is to do"),
                        "context": { "type": "string", "enum": ["fresh", "fork"], "description": "fresh starts the child with only its task; fork seeds it with this episode's conversation so far" },
                        "name": string("roster name for the child; defaults to the program name and must be unique"),
                    }),
                    &["program", "task"],
                ),
                Effect::Spawns,
            ),
            Kind::Steer => (
                "Send a message to a running child, addressed by roster name. It arrives in the child's next request.",
                object(serde_json::json!({ "to": string("roster name"), "content": string("the message") }), &["to", "content"]),
                Effect::Pure,
            ),
            Kind::Notify => (
                "Send a message to the episode that started this one.",
                object(serde_json::json!({ "content": string("the message") }), &["content"]),
                Effect::Pure,
            ),
            Kind::Send => (
                "Send a message to a teammate, addressed by roster name, through the lead.",
                object(
                    serde_json::json!({ "to": string("roster name of the teammate"), "content": string("the message") }),
                    &["to", "content"],
                ),
                Effect::Pure,
            ),
            Kind::Team => ("List the roster of the team this episode belongs to.", object(serde_json::json!({}), &[]), Effect::Pure),
        };
        ToolSpec {
            name: self.name().to_string(),
            description: description.to_string(),
            instruction: None,
            params,
            effect,
        }
    }
}

/// The specifications of the lead's built-in tools, in the order [`tools`]
/// lists them. Identity and `foe tools` use this without a running team.
pub fn builtin_specs() -> Vec<ToolSpec> {
    [Kind::Spawn, Kind::Steer].into_iter().map(Kind::spec).collect()
}

/// The built-in tools of a lead: `spawn` and `steer`. `spawn` takes its
/// [`Spawner`] from the call context.
pub fn tools(team: Arc<Team>) -> Vec<Box<dyn Tool>> {
    [Kind::Spawn, Kind::Steer]
        .into_iter()
        .map(|kind| Box::new(TeamTool { spec: kind.spec(), kind, team: team.clone() }) as Box<dyn Tool>)
        .collect()
}

/// The `host_tools` entries a child is launched with: `notify`, `send`, and
/// `team`, which its parent answers.
pub fn host_tool_defs() -> BTreeMap<String, HostToolDef> {
    [Kind::Notify, Kind::Send, Kind::Team]
        .into_iter()
        .map(|kind| {
            let spec = kind.spec();
            let def = HostToolDef {
                description: spec.description,
                instruction: spec.instruction,
                params: spec.params,
                effect: spec.effect,
            };
            (spec.name, def)
        })
        .collect()
}

struct TeamTool {
    spec: ToolSpec,
    kind: Kind,
    team: Arc<Team>,
}

#[async_trait::async_trait]
impl Tool for TeamTool {
    fn spec(&self) -> &ToolSpec {
        &self.spec
    }

    async fn call(&self, args: serde_json::Value, ctx: &CallCtx) -> ToolValue {
        match self.kind {
            Kind::Spawn => {
                let Some(spawner) = &ctx.spawner else {
                    return ToolValue::error("spawn: this episode holds no spawn capability");
                };
                let (program, task) = match (arg(&args, "program"), arg(&args, "task")) {
                    (Ok(p), Ok(t)) => (p.to_string(), t.to_string()),
                    (Err(e), _) | (_, Err(e)) => return e,
                };
                let context = match args.get("context").and_then(|v| v.as_str()) {
                    None | Some("fresh") => SpawnContext::Fresh,
                    Some("fork") => SpawnContext::Fork,
                    Some(other) => return ToolValue::error(format!("context: {other} is neither fresh nor fork")),
                };
                let name = args.get("name").and_then(|v| v.as_str()).unwrap_or(&program).to_string();
                // The spawner reserves the child's whole share and records what it granted.
                let req = SpawnRequest {
                    program: program.clone(),
                    task,
                    context,
                    reserve: BudgetAmount::default(),
                    call_id: ctx.call_id.clone(),
                };
                match self.team.spawn(spawner.as_ref(), req, &name) {
                    Ok(handle) => ToolValue::ok(
                        serde_json::json!({ "child_id": handle.child_id, "name": name, "program": program }),
                        format!(
                            "started {name} ({}) running {program}; its result will arrive as a message from it",
                            handle.child_id
                        ),
                    ),
                    Err(e) => ToolValue::error(format!("spawn: {e}")),
                }
            }
            Kind::Steer => {
                let (to, content) = match (arg(&args, "to"), arg(&args, "content")) {
                    (Ok(t), Ok(c)) => (t, c),
                    (Err(e), _) | (_, Err(e)) => return e,
                };
                match self.team.steer(to, text_content(content)) {
                    Ok(()) => ToolValue::ok(serde_json::json!({ "to": to }), format!("sent to {to}")),
                    Err(e) => ToolValue::error(format!("steer: {e}")),
                }
            }
            Kind::Notify | Kind::Send | Kind::Team => {
                unreachable!("answered as host tools by the lead")
            }
        }
    }
}

#[cfg(test)]
#[path = "team_test.rs"]
mod tests;
