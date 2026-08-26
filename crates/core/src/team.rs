//! Team fold over the lead log: roster, message queue, delivery records.
//!
//! A team is the set of children one episode has spawned; that episode is
//! the lead. The lead's log holds the roster and the queue of messages
//! between members, and [`fold`] derives both from it. No other team state
//! exists. See docs/design.md "Subagents and teams".
//!
//! Six built-in tools belong here. `spawn`, `wait`, and `steer` act on this
//! episode's own team. `notify`, `send`, and `team` act on the team this
//! episode belongs to: in an episode with a parent they are host tool calls
//! that the parent answers, and in a root they act on its own roster, with
//! `notify` an error because no parent exists. When the lead answers a
//! member, a `notify` becomes an inbox item in the lead's log with source
//! `child`; a `send` becomes a `team/message` in the lead's log followed by
//! an inbox item with source `peer` written to the target; `team` returns
//! the roster. When the target records the peer item, the lead sees it and
//! writes `team/delivered`. See docs/protocol.md "Children".

use crate::budget::Pool;
use crate::loop_::{settled_children, SETTLE_POLL};
use crate::protocol::{Host, InboxSink};
use crate::spawn::{ChildObserver, Router};
use crate::{CallCtx, CapError, SpawnHandle, SpawnRequest, Spawner, Tool, ToolValue};
use foe_log::{
    BudgetAmount, ContentBlock, Event, EventData, InboxItem, InboxSource, MemberPhase, Outcome, SpawnContext,
};
use foe_program::{Effect, ToolSpec};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

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

/// The lead's side of a team: writes roster and queue events to the lead's
/// log, delivers messages to members, and answers members' host tool calls.
pub struct Team {
    lead_id: String,
    log: Arc<dyn LeadLog>,
    inbox: Arc<dyn InboxSink>,
    router: Arc<Router>,
    /// Read by `wait`, which returns once no reservation is outstanding.
    pool: Arc<Mutex<Pool>>,
    /// Held across a spawn and across every roster write, so that a member's
    /// `provisioning` entry precedes its `active` one.
    roster_lock: Mutex<()>,
    messages: AtomicU64,
}

impl Team {
    pub fn new(
        lead_id: String,
        log: Arc<dyn LeadLog>,
        inbox: Arc<dyn InboxSink>,
        router: Arc<Router>,
        pool: Arc<Mutex<Pool>>,
    ) -> Self {
        Team { lead_id, log, inbox, router, pool, roster_lock: Mutex::new(()), messages: AtomicU64::new(0) }
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
            EventData::InboxItem(item) if item.source == InboxSource::Peer => {
                if let Some(id) = &item.message_id {
                    self.log.append(EventData::TeamDelivered { message_id: id.clone(), to: child_id.to_string() });
                }
            }
            _ => {}
        }
    }

    /// A member that failed, or whose process ended without an outcome, is
    /// marked failed; every ending is reported to the lead as an inbox item.
    fn ended(&self, child_id: &str, outcome: &Outcome) {
        if matches!(outcome, Outcome::Failed { .. }) {
            self.set_phase(child_id, MemberPhase::Failed);
        }
        let name = self.state().member_by_id(child_id).map(|m| m.name.clone()).unwrap_or_default();
        let text = format!("{name} ({child_id}) ended: {}", render_outcome(outcome));
        self.inbox.append(child_item(child_id, text_content(&text)));
    }

    fn host_call(&self, child_id: &str, name: &str, args: &serde_json::Value) -> Option<ToolValue> {
        let kind: Kind = serde_json::from_value(serde_json::Value::String(name.to_string())).ok()?;
        let content = match kind {
            Kind::Spawn | Kind::Steer => return None,
            Kind::Team => Vec::new(),
            _ => match arg(args, "content") {
                Ok(text) => text_content(text),
                Err(e) => return Some(e),
            },
        };
        Some(match kind {
            Kind::Notify => {
                self.inbox.append(child_item(child_id, content));
                ToolValue::ok(serde_json::json!({ "sent": true }), "sent")
            }
            Kind::Send => arg(args, "to").map_or_else(|e| e, |to| self.send_value(child_id, to, content)),
            _ => self.roster(),
        })
    }
}

impl Team {
    /// The result of a `send` call: the message id, or the failure.
    fn send_value(&self, from: &str, to: &str, content: Vec<ContentBlock>) -> ToolValue {
        match self.send(from, to, content) {
            Ok(id) => ToolValue::ok(serde_json::json!({ "to": to, "message_id": id }), format!("sent to {to}")),
            Err(e) => ToolValue::error(format!("send: {e}")),
        }
    }

    /// The result of a `team` call: the roster as rows and as text.
    fn roster(&self) -> ToolValue {
        let state = self.state();
        ToolValue::ok(state.roster_value(), state.roster_text())
    }
}

fn text_content(text: &str) -> Vec<ContentBlock> {
    vec![ContentBlock::Text { text: text.to_string() }]
}

/// An inbox item a child sends its parent.
fn child_item(child_id: &str, content: Vec<ContentBlock>) -> InboxItem {
    InboxItem { source: InboxSource::Child, content, from: Some(child_id.to_string()), message_id: None }
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

/// The six team tools; the serialized name is the tool name.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "lowercase")]
enum Kind {
    Spawn,
    Wait,
    Steer,
    Notify,
    Send,
    Team,
}

impl Kind {
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
            Kind::Wait => (
                "Wait until every child episode this one started has ended. Their reports are in the request that \
follows. Returns at once when no child is running. Use it before acting on work delegated to children; an episode \
that ends while a child runs ends that child. With `until`, wait instead until an arrival matches one of its \
conditions; the result names the condition met, or `timeout`, and the arrival itself is in the request that follows.",
                object(
                    serde_json::json!({
                        "until": {
                            "type": "array",
                            "description": "conditions, of which the first met ends the wait: {child, outcome?} for a child episode (id, or \"any\") reaching any outcome or the named kind; {session} for a process session (id, or \"any\") exiting; {inbox} for an inbox arrival by source",
                            "items": { "anyOf": [
                                { "type": "object", "properties": { "child": string("child episode id, or \"any\""), "outcome": { "type": "string", "enum": ["completed", "blocked", "exhausted", "failed"] } }, "required": ["child"], "additionalProperties": false },
                                { "type": "object", "properties": { "session": { "type": ["integer", "string"], "description": "session id, or \"any\"" } }, "required": ["session"], "additionalProperties": false },
                                { "type": "object", "properties": { "inbox": { "type": "string", "enum": ["task", "parent", "child", "peer", "verify", "system", "session"] } }, "required": ["inbox"], "additionalProperties": false }
                            ] }
                        },
                        "timeout_seconds": { "type": "integer", "minimum": 1, "description": "return after this long even if nothing matched" }
                    }),
                    &[],
                ),
                Effect::Pure,
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
        ToolSpec { name: kebab(&self), description: description.to_string(), instruction: None, params, effect }
    }
}

const KINDS: [Kind; 6] = [Kind::Spawn, Kind::Wait, Kind::Steer, Kind::Notify, Kind::Send, Kind::Team];

/// The specifications of the six team tools, in the order [`tools`] lists
/// them. Identity and `foe plan` use this without a running team.
pub fn builtin_specs() -> Vec<ToolSpec> {
    KINDS.into_iter().map(Kind::spec).collect()
}

/// The six team tools. `parent` is the link to the process hosting this
/// episode, when it has one; `notify`, `send`, and `team` then go to the
/// parent as host tool calls. `spawn` takes its [`Spawner`] from the call
/// context.
pub fn tools(team: Arc<Team>, parent: Option<&Host>) -> Vec<Box<dyn Tool>> {
    KINDS
        .into_iter()
        .map(|kind| match (parent, kind) {
            (Some(host), Kind::Notify | Kind::Send | Kind::Team) => host.tool(kind.spec()),
            _ => Box::new(TeamTool { spec: kind.spec(), kind, team: team.clone() }) as Box<dyn Tool>,
        })
        .collect()
}

/// Arguments of `wait`. Bare, the tool blocks until every child has ended;
/// with `until`, until an arrival matches one of the conditions.
#[derive(serde::Deserialize)]
struct WaitArgs {
    #[serde(default)]
    until: Vec<Condition>,
    timeout_seconds: Option<u64>,
}

/// One `until` condition, in outcome vocabulary. Each names what counts as
/// news: a child reaching an outcome, a session exiting, or an inbox
/// arrival by source.
#[derive(serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
enum Condition {
    Child {
        child: String,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        outcome: Option<OutcomeKind>,
    },
    Session {
        /// A session id, or the string `any`; validated before the wait.
        session: serde_json::Value,
    },
    Inbox {
        inbox: InboxSource,
    },
}

#[derive(serde::Serialize, serde::Deserialize, Clone, Copy, PartialEq)]
#[serde(rename_all = "lowercase")]
enum OutcomeKind {
    Completed,
    Blocked,
    Exhausted,
    Failed,
}

fn kind_of(outcome: &Outcome) -> OutcomeKind {
    match outcome {
        Outcome::Completed { .. } => OutcomeKind::Completed,
        Outcome::Blocked { .. } => OutcomeKind::Blocked,
        Outcome::Exhausted { .. } => OutcomeKind::Exhausted,
        Outcome::Failed { .. } => OutcomeKind::Failed,
    }
}

/// The first condition an unconsumed inbox item satisfies: `wait` blocks
/// until an arrival matches, and the arrival reaches the model through the
/// ordinary inbox drain of the next request. A child condition is met by
/// the child's ended report once its `spawn/end` records a matching
/// outcome; a session condition by the `session`-source item whose `from`
/// is the session id.
fn matched(events: &[Event], until: &[Condition]) -> Option<usize> {
    let mut consumed: BTreeSet<u64> = BTreeSet::new();
    let mut ended: BTreeMap<&str, OutcomeKind> = BTreeMap::new();
    for event in events {
        match &event.data {
            EventData::ModelRequest(r) => consumed.extend(r.consumed.iter().copied()),
            EventData::SpawnEnd { child_id, outcome } => {
                ended.insert(child_id, kind_of(outcome));
            }
            _ => {}
        }
    }
    for event in events.iter().filter(|e| !consumed.contains(&e.seq)) {
        let EventData::InboxItem(item) = &event.data else { continue };
        let from = item.from.as_deref().unwrap_or_default();
        let met = |condition: &Condition| match condition {
            Condition::Inbox { inbox } => item.source == *inbox,
            Condition::Session { session } => {
                item.source == InboxSource::Session && session.as_u64().is_none_or(|id| from == id.to_string())
            }
            Condition::Child { child, outcome } => {
                item.source == InboxSource::Child
                    && (child == "any" || child == from)
                    && ended.get(from).is_some_and(|kind| outcome.is_none_or(|wanted| wanted == *kind))
            }
        };
        if let Some(index) = until.iter().position(met) {
            return Some(index);
        }
    }
    None
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
            Kind::Steer | Kind::Send => {
                let (to, content) = match (arg(&args, "to"), arg(&args, "content")) {
                    (Ok(t), Ok(c)) => (t, text_content(c)),
                    (Err(e), _) | (_, Err(e)) => return e,
                };
                if self.kind == Kind::Send {
                    return self.team.send_value(&self.team.lead_id, to, content);
                }
                match self.team.steer(to, content) {
                    Ok(()) => ToolValue::ok(serde_json::json!({ "to": to }), format!("sent to {to}")),
                    Err(e) => ToolValue::error(format!("steer: {e}")),
                }
            }
            Kind::Wait => {
                let parsed: WaitArgs = match serde_json::from_value(args) {
                    Ok(parsed) => parsed,
                    Err(e) => return ToolValue::error(format!("wait: {e}")),
                };
                if parsed.until.iter().any(
                    |c| matches!(c, Condition::Session { session } if session.as_u64().is_none() && session != "any"),
                ) {
                    return ToolValue::error("wait: `session` names a session id or \"any\"");
                }
                let timeout = parsed.timeout_seconds.map(|s| Instant::now() + Duration::from_secs(s));
                let deadline = match (ctx.deadline, timeout) {
                    (Some(budget), Some(asked)) => Some(budget.min(asked)),
                    (budget, asked) => budget.or(asked),
                };
                let timed_out = || ToolValue::ok(serde_json::json!({ "matched": "timeout" }), "timeout");
                if parsed.until.is_empty() {
                    return match settled_children(&self.team.pool, deadline).await {
                        0 => ToolValue::ok(serde_json::json!({ "running": 0 }), "every child has ended"),
                        _ if timeout.is_some_and(|t| Instant::now() >= t) => timed_out(),
                        running => ToolValue::error(format!(
                            "wait: {running} child episode(s) were still running when the seconds budget ran out"
                        )),
                    };
                }
                loop {
                    if let Some(index) = matched(&self.team.log.events(), &parsed.until) {
                        let met = serde_json::to_value(&parsed.until[index]).unwrap_or_default();
                        return ToolValue::ok(serde_json::json!({ "matched": met }), format!("matched: {met}"));
                    }
                    if deadline.is_some_and(|d| Instant::now() >= d) {
                        return timed_out();
                    }
                    tokio::time::sleep(SETTLE_POLL).await;
                }
            }
            Kind::Notify => ToolValue::error("notify: this episode has no parent to notify"),
            Kind::Team => self.team.roster(),
        }
    }
}

#[cfg(test)]
#[path = "team_test.rs"]
mod tests;
