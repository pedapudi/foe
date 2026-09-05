//! Team coordination derived from the lead episode's log.
//!
//! Every episode leads a team that initially contains its root task and the
//! episode itself. Spawning adds a task to that team's board. The runtime
//! starts one child episode for the task when its dependencies and capacity
//! permit. The lead's log holds every added task, roster change, and durable
//! peer message. [`fold`] derives the complete team from those events and the
//! episode lifecycle. See docs/design.md "Agent teams".
//!
//! Six built-in tools belong here. `spawn`, `wait`, and `steer` act on the
//! team this episode leads. `notify` and `send` act on the team this episode
//! belongs to. `team` can inspect either team when they differ. When the lead
//! answers a member, a `notify` becomes an inbox item in the lead's log with
//! source `child`. A `send` becomes a `team/message` in the lead's log followed
//! by an inbox item with source `peer` written to the target. `team` returns
//! the selected board and its members. When the target records the peer item,
//! the lead sees it and writes `team/delivered`. See docs/protocol.md "Children".

use foe_contract::{Effect, ToolSpec};
use foe_core::budget::Pool;
use foe_core::log::{
    BlockedCode, BudgetAmount, ContentBlock, Event, EventData, ExhaustedLimit, InboxItem, InboxSource, MemberPhase,
    Outcome, SpawnContext, TaskStatus, TeamTask,
};
use foe_core::loop_::SETTLE_POLL;
use foe_core::protocol::{Host, InboxSink};
use foe_core::spawn::{ChildObserver, Router};
use foe_core::{CallCtx, CapError, LeadLog, SpawnRequest, Spawner, Tool, ToolFailureCode, ToolValue};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct Member {
    pub member_id: String,
    pub name: String,
    pub description: String,
    pub phase: MemberPhase,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub task_status: Option<TaskStatus>,
}

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct Queued {
    pub message_id: String,
    pub from: String,
    pub to: String,
    pub content: Vec<ContentBlock>,
}

/// Team state folded from a lead's log.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct TeamState {
    pub lead_id: String,
    /// Members in order of first appearance, with startup phase and task status.
    pub roster: Vec<Member>,
    /// The root task followed by added tasks, each at its latest revision.
    pub tasks: Vec<TeamTask>,
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

    pub fn task(&self, task_id: &str) -> Option<&TeamTask> {
        self.tasks.iter().find(|task| task.task_id == task_id)
    }

    /// Messages queued and never delivered; redelivered when the target
    /// restarts.
    pub fn undelivered(&self) -> impl Iterator<Item = &Queued> {
        self.queue.iter().filter(|m| !self.delivered.contains(&m.message_id))
    }

    fn value(&self) -> serde_json::Value {
        serde_json::json!({ "lead_id": self.lead_id, "members": self.roster, "tasks": self.tasks })
    }

    fn roster_text(&self) -> String {
        if self.roster.is_empty() {
            return "members: none\ntasks: none".to_string();
        }
        let members: Vec<String> = self
            .roster
            .iter()
            .map(|m| {
                let status = m.task_status.as_ref().map(kebab).unwrap_or_else(|| kebab(&m.phase));
                format!("{}\t{}\t{status}", m.name, m.member_id)
            })
            .collect();
        let tasks: Vec<String> =
            self.tasks.iter().map(|task| format!("{}\t{}\t{}", task.task_id, task.name, kebab(&task.status))).collect();
        format!("members:\n{}\ntasks:\n{}", members.join("\n"), tasks.join("\n"))
    }
}

/// Folds the team events of a lead's log. Events copied from another log by
/// seeding, which precede `seed/end`, belong to that log's episode and are
/// skipped.
pub fn fold(events: &[Event]) -> TeamState {
    let live_from = events.iter().rev().find(|e| matches!(e.data, EventData::SeedEnd {})).map_or(0, |e| e.seq + 1);
    let mut state = TeamState::default();
    if let Some(start) = events.iter().find_map(|event| match &event.data {
        EventData::EpisodeStart(start) => Some(start),
        _ => None,
    }) {
        let outcome = events.iter().rev().find_map(|event| match &event.data {
            EventData::EpisodeEnd { outcome } => Some(outcome.clone()),
            _ => None,
        });
        let phase =
            if matches!(outcome, Some(Outcome::Failed { .. })) { MemberPhase::Failed } else { MemberPhase::Active };
        let status = outcome.as_ref().map_or(TaskStatus::Running, task_status);
        let name = start.contract["name"].as_str().unwrap_or("lead").to_string();
        state.lead_id = start.id.clone();
        state.roster.push(Member {
            member_id: start.id.clone(),
            name: name.clone(),
            description: start.task.clone(),
            phase,
            task_status: Some(status),
        });
        state.tasks.push(TeamTask {
            task_id: "task_root".into(),
            revision: u64::from(outcome.is_some()),
            name,
            contract: start.contract["name"].as_str().unwrap_or("root").to_string(),
            description: start.task.clone(),
            context: SpawnContext::Fresh,
            status,
            owner: Some(start.id.clone()),
            blocked_by: Vec::new(),
            scope: Vec::new(),
            outcome,
            call_id: String::new(),
        });
    }
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
                        task_status: None,
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
            EventData::TeamTask(task) => match state.tasks.iter_mut().find(|known| known.task_id == task.task_id) {
                Some(known) if task.revision > known.revision => *known = task.clone(),
                None => state.tasks.push(task.clone()),
                _ => {}
            },
            _ => {}
        }
    }
    for member in &mut state.roster {
        member.task_status =
            state.tasks.iter().find(|task| task.owner.as_deref() == Some(&member.member_id)).map(|task| task.status);
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
    /// Serializes task creation, assignment, roster changes, and message allocation.
    operations: Mutex<()>,
}

impl Team {
    pub fn new(
        lead_id: String,
        log: Arc<dyn LeadLog>,
        inbox: Arc<dyn InboxSink>,
        router: Arc<Router>,
        pool: Arc<Mutex<Pool>>,
    ) -> Self {
        Team { lead_id, log, inbox, router, pool, operations: Mutex::new(()) }
    }

    pub fn state(&self) -> TeamState {
        fold(&self.log.events())
    }

    /// Adds a task and starts every queued task whose dependencies and
    /// capacity permit. A concurrency refusal keeps the task queued.
    pub fn delegate(
        self: &Arc<Self>,
        spawner: Arc<dyn Spawner>,
        req: SpawnRequest,
        name: Option<&str>,
        blocked_by: Vec<String>,
        scope: Vec<String>,
    ) -> Result<TeamTask, CapError> {
        let task_id;
        {
            let _guard = self.operations.lock().unwrap();
            let state = self.state();
            if blocked_by.iter().any(|id| id == "task_root") {
                return Err(CapError::Invalid(
                    "blocked_by cannot name task_root because the root settles after its team".into(),
                ));
            }
            if let Some(missing) = blocked_by.iter().find(|id| state.task(id).is_none()) {
                return Err(CapError::Invalid(format!("blocked_by names unknown task {missing}")));
            }
            task_id = format!("task_{:02}", state.tasks.len());
            let task = TeamTask {
                task_id: task_id.clone(),
                revision: 0,
                name: unique_name(&state, name.unwrap_or(&req.contract), &task_id),
                contract: req.contract,
                description: req.task,
                context: req.context,
                status: TaskStatus::Queued,
                owner: None,
                blocked_by,
                scope,
                outcome: None,
                call_id: req.call_id,
            };
            self.log.append(EventData::TeamTask(task))?;
        }
        self.schedule(spawner)?;
        self.state()
            .task(&task_id)
            .cloned()
            .ok_or_else(|| CapError::Invalid(format!("team/task {task_id} was not recorded")))
    }

    /// Starts ready tasks in board order. The lead process performs every
    /// assignment, so two agents cannot receive the same task.
    pub fn schedule(self: &Arc<Self>, spawner: Arc<dyn Spawner>) -> Result<(), CapError> {
        loop {
            let mut watch = None;
            {
                let _guard = self.operations.lock().unwrap();
                self.log.check()?;
                let state = self.state();
                let queued: Vec<&TeamTask> =
                    state.tasks.iter().filter(|task| task.status == TaskStatus::Queued).collect();
                let Some(task) = queued
                    .iter()
                    .find(|task| {
                        task.blocked_by
                            .iter()
                            .filter_map(|id| state.task(id))
                            .any(|blocker| blocker.status != TaskStatus::Completed && settled(blocker.status))
                    })
                    .or_else(|| {
                        queued.iter().find(|task| {
                            task.blocked_by
                                .iter()
                                .filter_map(|id| state.task(id))
                                .all(|blocker| blocker.status == TaskStatus::Completed)
                        })
                    })
                    .map(|task| (*task).clone())
                else {
                    return Ok(());
                };
                let blockers: Vec<&TeamTask> = task.blocked_by.iter().filter_map(|id| state.task(id)).collect();
                if blockers.iter().any(|task| task.status != TaskStatus::Completed && settled(task.status)) {
                    let names = blockers
                        .iter()
                        .filter(|task| task.status != TaskStatus::Completed)
                        .map(|task| task.task_id.as_str())
                        .collect::<Vec<_>>()
                        .join(", ");
                    self.settle_task(
                        task,
                        Outcome::Blocked {
                            code: BlockedCode::ChildBlocked,
                            message: format!("dependency tasks did not complete: {names}"),
                        },
                    )?;
                    continue;
                }
                if blockers.iter().any(|task| !settled(task.status)) {
                    return Ok(());
                }
                let child_id = spawner.allocate_id();
                let request = SpawnRequest {
                    contract: task.contract.clone(),
                    task: task.description.clone(),
                    context: task.context,
                    reserve: BudgetAmount::default(),
                    call_id: task.call_id.clone(),
                };
                match spawner.launch(child_id.clone(), request) {
                    Ok(handle) => {
                        let mut running = task.clone();
                        running.revision += 1;
                        running.status = TaskStatus::Running;
                        running.owner = Some(child_id.clone());
                        self.log.append(EventData::TeamTask(running))?;
                        self.log.append(EventData::TeamRoster {
                            member_id: child_id,
                            name: task.name.clone(),
                            description: task.description.clone(),
                            phase: MemberPhase::Provisioning,
                        })?;
                        watch = Some(handle);
                    }
                    Err(CapError::Budget { limit: ExhaustedLimit::Concurrency, .. }) => return Ok(()),
                    Err(CapError::Budget { limit, .. }) => self.settle_task(task, Outcome::Exhausted { limit })?,
                    Err(error) => self.settle_task(task, Outcome::Failed { error: error.to_string() })?,
                }
            }
            if let Some(handle) = watch {
                let (team, scheduler) = (self.clone(), spawner.clone());
                tokio::spawn(async move {
                    let _ = handle.run.settle().await;
                    let _ = team.schedule(scheduler);
                });
            }
        }
    }

    fn settle_task(&self, mut task: TeamTask, outcome: Outcome) -> Result<(), CapError> {
        task.revision += 1;
        task.status = task_status(&outcome);
        task.outcome = Some(outcome);
        self.log.append(EventData::TeamTask(task))
    }

    /// Writes an inbox item to a member, addressed by roster name.
    pub fn steer(&self, name: &str, content: Vec<ContentBlock>) -> Result<(), CapError> {
        self.log.check()?;
        let state = self.state();
        let member = state.member(name).ok_or_else(|| CapError::Invalid(format!("no member named {name}")))?;
        let item =
            InboxItem { source: InboxSource::Parent, content, from: Some(self.lead_id.clone()), message_id: None };
        self.router.send_inbox(&member.member_id, &item)
    }

    fn set_phase(&self, member_id: &str, phase: MemberPhase) {
        let _guard = self.operations.lock().unwrap();
        let state = self.state();
        let (name, description) = state
            .member_by_id(member_id)
            .map(|m| (m.name.clone(), m.description.clone()))
            .unwrap_or_else(|| (member_id.to_string(), String::new()));
        let _ = self.log.append(EventData::TeamRoster { member_id: member_id.to_string(), name, description, phase });
    }

    /// Queues a message from one member to another and attempts delivery. A
    /// failed delivery leaves the message queued without a delivery record;
    /// the fold reports it as undelivered.
    fn send(&self, from: &str, to_name: &str, content: Vec<ContentBlock>) -> Result<String, CapError> {
        let _guard = self.operations.lock().unwrap();
        let state = self.state();
        let target = state.member(to_name).ok_or_else(|| CapError::Invalid(format!("no member named {to_name}")))?;
        let message_id = format!("{}:tm_{:02}", self.lead_id, state.queue.len() + 1);
        self.log.append(EventData::TeamMessage {
            message_id: message_id.clone(),
            from: from.to_string(),
            to: target.member_id.clone(),
            content: content.clone(),
        })?;
        let item = InboxItem {
            source: InboxSource::Peer,
            content,
            from: Some(from.to_string()),
            message_id: Some(message_id.clone()),
        };
        if target.member_id == self.lead_id {
            self.inbox.append(item);
        } else {
            let _ = self.router.send_inbox(&target.member_id, &item);
        }
        Ok(message_id)
    }
}

impl ChildObserver for Team {
    fn observe(&self, child_id: &str, event: &Event) {
        match &event.data {
            EventData::EpisodeStart(_) => self.set_phase(child_id, MemberPhase::Active),
            EventData::InboxItem(item) if item.source == InboxSource::Peer => {
                if let Some(id) = &item.message_id {
                    let _ =
                        self.log.append(EventData::TeamDelivered { message_id: id.clone(), to: child_id.to_string() });
                }
            }
            _ => {}
        }
    }

    /// Every ending reaches the lead. An abnormal process end also marks
    /// the roster; ordinary settlement is carried by the task revision.
    fn ended(&self, child_id: &str, outcome: &Outcome) {
        let _guard = self.operations.lock().unwrap();
        let state = self.state();
        let member = state.member_by_id(child_id);
        if matches!(outcome, Outcome::Failed { .. }) {
            if let Some(member) = member {
                if self
                    .log
                    .append(EventData::TeamRoster {
                        member_id: child_id.to_string(),
                        name: member.name.clone(),
                        description: member.description.clone(),
                        phase: MemberPhase::Failed,
                    })
                    .is_err()
                {
                    return;
                }
            }
        }
        if let Some(task) = state
            .tasks
            .iter()
            .find(|task| task.owner.as_deref() == Some(child_id) && task.status == TaskStatus::Running)
            .cloned()
        {
            if self.settle_task(task, outcome.clone()).is_err() {
                return;
            }
        }
        let name = member.map(|member| member.name.clone()).unwrap_or_default();
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

    /// The result of a `team` call: its board and members as data and text.
    fn roster(&self) -> ToolValue {
        let state = self.state();
        ToolValue::ok(state.value(), state.roster_text())
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

fn task_status(outcome: &Outcome) -> TaskStatus {
    match outcome {
        Outcome::Completed { .. } => TaskStatus::Completed,
        Outcome::Blocked { .. } => TaskStatus::Blocked,
        Outcome::Exhausted { .. } => TaskStatus::Exhausted,
        Outcome::Failed { .. } => TaskStatus::Failed,
    }
}

fn settled(status: TaskStatus) -> bool {
    !matches!(status, TaskStatus::Queued | TaskStatus::Running)
}

fn unique_name(state: &TeamState, requested: &str, task_id: &str) -> String {
    if state.roster.iter().all(|member| member.name != requested)
        && state.tasks.iter().all(|task| task.name != requested)
    {
        requested.to_string()
    } else {
        format!("{requested}-{task_id}")
    }
}

fn kebab(value: &impl serde::Serialize) -> String {
    serde_json::to_value(value).ok().and_then(|v| v.as_str().map(str::to_string)).unwrap_or_default()
}

fn arg<'a>(args: &'a serde_json::Value, key: &str) -> Result<&'a str, ToolValue> {
    args.get(key).and_then(|v| v.as_str()).ok_or_else(|| ToolValue::invalid(format!("{key}: a string is required")))
}

fn string_list(args: &serde_json::Value, key: &str) -> Result<Vec<String>, ToolValue> {
    let Some(value) = args.get(key) else { return Ok(Vec::new()) };
    let Some(values) = value.as_array() else { return Err(ToolValue::invalid(format!("{key}: an array is required"))) };
    values
        .iter()
        .map(|value| {
            value
                .as_str()
                .map(str::to_string)
                .ok_or_else(|| ToolValue::invalid(format!("{key}: every item must be a string")))
        })
        .collect()
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
                "Add a task to this episode's team. The runtime starts one child episode when its dependencies and the team budget permit. Returns the durable task state; the child's result arrives later as a message.",
                object(
                    serde_json::json!({
                        "contract": string("name of a contract listed in grants.spawn"),
                        "task": string("what the child is to do"),
                        "context": { "type": "string", "enum": ["fresh", "fork"], "description": "fresh starts the child with only its task; fork seeds it with this episode's conversation so far" },
                        "name": string("roster name for the child; defaults to a unique form of the contract name"),
                        "blocked_by": { "type": "array", "items": { "type": "string" }, "description": "task ids that must complete before this task starts" },
                        "scope": { "type": "array", "items": { "type": "string" }, "description": "advisory paths this task intends to write" },
                    }),
                    &["contract", "task"],
                ),
                Effect::Spawns,
            ),
            Kind::Wait => (
                "Wait until every task added to this episode's board has settled and no child reservation remains. \
Their reports are in the request that follows. Returns at once when the board has no added task. Use it before \
acting on delegated work. An episode that ends while a child runs ends that child. With `until`, wait instead until \
an arrival matches one condition. The result names the condition met, or `timeout`. The arrival itself is in the \
request that follows.",
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
            Kind::Team => (
                "List a team's lead, members, and task board. The default is the team this episode belongs to. `led` selects the team of children this episode leads.",
                object(
                    serde_json::json!({ "scope": { "type": "string", "enum": ["member", "led"], "description": "member selects the parent-led team; led selects this episode's child team" } }),
                    &[],
                ),
                Effect::Pure,
            ),
        };
        ToolSpec { name: kebab(&self), description: description.to_string(), instruction: None, params, effect }
    }
}

const KINDS: [Kind; 6] = [Kind::Spawn, Kind::Wait, Kind::Steer, Kind::Notify, Kind::Send, Kind::Team];

/// The specifications of the six team tools, in the order [`tools`] lists
/// them. Fingerprint and `foe plan` use this without a running team.
pub fn builtin_specs() -> Vec<ToolSpec> {
    KINDS.into_iter().map(Kind::spec).collect()
}

/// The six team tools. `parent` is the link to the process hosting this
/// episode, when it has one. `notify` and `send` go to that parent. `team`
/// selects the parent-led or locally led team from one schema.
pub fn tools(team: Arc<Team>, parent: Option<&Host>) -> Vec<Box<dyn Tool>> {
    KINDS
        .into_iter()
        .map(|kind| match (parent, kind) {
            (Some(host), Kind::Notify | Kind::Send) => host.tool(kind.spec()),
            (Some(host), Kind::Team) => {
                Box::new(TeamTool { spec: kind.spec(), kind, team: team.clone(), parent: Some(host.tool(kind.spec())) })
                    as Box<dyn Tool>
            }
            _ => Box::new(TeamTool { spec: kind.spec(), kind, team: team.clone(), parent: None }) as Box<dyn Tool>,
        })
        .collect()
}

/// Arguments of `wait`. Bare, the tool blocks until every added task and
/// child reservation has settled. `until` selects an arrival condition.
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
    parent: Option<Box<dyn Tool>>,
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
                    return ToolValue::failed(
                        ToolFailureCode::CapabilityDenied,
                        "spawn: this episode holds no spawn capability",
                        false,
                        serde_json::json!({ "capability": "spawn" }),
                    );
                };
                let (contract, task) = match (arg(&args, "contract"), arg(&args, "task")) {
                    (Ok(p), Ok(t)) => (p.to_string(), t.to_string()),
                    (Err(e), _) | (_, Err(e)) => return e,
                };
                let context = match args.get("context").and_then(|v| v.as_str()) {
                    None | Some("fresh") => SpawnContext::Fresh,
                    Some("fork") => SpawnContext::Fork,
                    Some(other) => return ToolValue::invalid(format!("context: {other} is neither fresh nor fork")),
                };
                let name = args.get("name").and_then(|v| v.as_str()).unwrap_or(&contract).to_string();
                let blocked_by = match string_list(&args, "blocked_by") {
                    Ok(values) => values,
                    Err(error) => return error,
                };
                let scope = match string_list(&args, "scope") {
                    Ok(values) => values,
                    Err(error) => return error,
                };
                // The spawner reserves the child's whole share and records what it granted.
                let req = SpawnRequest {
                    contract: contract.clone(),
                    task,
                    context,
                    reserve: BudgetAmount::default(),
                    call_id: ctx.call_id.clone(),
                };
                match self.team.delegate(spawner.clone(), req, Some(&name), blocked_by, scope) {
                    Ok(task) => {
                        let owner = task.owner.as_deref().unwrap_or("unassigned");
                        ToolValue::ok(
                            serde_json::to_value(&task).unwrap_or_default(),
                            format!("{} {} as {} for {owner}", task.task_id, kebab(&task.status), task.name),
                        )
                    }
                    Err(e) => ToolValue::from_cap_error("spawn", e),
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
                    loop {
                        let pending =
                            self.team.state().tasks.iter().skip(1).filter(|task| !settled(task.status)).count();
                        let running = self.team.pool.lock().unwrap().active_children();
                        if pending == 0 && running == 0 {
                            return ToolValue::ok(serde_json::json!({ "pending": 0 }), "every team task has settled");
                        }
                        if deadline.is_some_and(|d| Instant::now() >= d) {
                            return if timeout.is_some_and(|t| Instant::now() >= t) {
                                timed_out()
                            } else {
                                ToolValue::error(format!(
                                    "wait: {pending} team task(s) and {running} child reservation(s) remained when the seconds budget ran out"
                                ))
                            };
                        }
                        tokio::time::sleep(SETTLE_POLL).await;
                    }
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
            Kind::Team => match args.get("scope").and_then(|value| value.as_str()) {
                None | Some("member") if self.parent.is_some() => {
                    self.parent.as_ref().expect("checked").call(serde_json::json!({}), ctx).await
                }
                None | Some("member") | Some("led") => self.team.roster(),
                Some(scope) => ToolValue::invalid(format!("scope: {scope} is neither member nor led")),
            },
        }
    }
}

#[cfg(test)]
#[path = "lib_test.rs"]
mod tests;
