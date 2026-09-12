//! Team coordination derived from the lead episode's log.
//!
//! Every episode leads a team that initially contains its root task and the
//! episode itself. Spawning adds a task to that team's board. The runtime
//! starts one child episode for the task when its dependencies and capacity
//! permit. The lead's log holds every added task, roster change, and durable
//! peer message. The coordinator maintains its roster and queue from appended
//! events and reads task revisions from the writer. See docs/design.md "Agent teams".
//!
//! Eight built-in tools belong here. `spawn`, `wait`, `steer`, and `cancel`
//! act on the team this episode leads. `notify`, `send`, and `ask` act on the
//! team this episode belongs to. `team` can inspect either team when they
//! differ. When the lead answers a member, a `notify` becomes an inbox item in
//! the lead's log with source `child`. A `send` becomes a `team/message` in the
//! lead's log followed by an inbox item with source `peer` written to the
//! target. `team` returns the selected board and its members. When the target
//! records the peer item, the lead sees it and writes `team/delivered`. A
//! message the lead is itself the target of reaches its inbox where it is
//! sent, so its delivery is recorded there. See docs/protocol.md "Children".
//!
//! `ask` sends a question and requires a deadline and a default answer with
//! it. The asking process holds the deadline and delivers the default to its
//! own inbox when the deadline passes unanswered, so no episode waits on
//! another without end. See docs/design.md "Agent teams".

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
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tokio::time::Instant;

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

/// The name that addresses the episode leading a team, whatever its
/// contract named it. A member that holds `ask` and not `team` cannot read
/// the roster, so the only name it can be sure of is one the runtime fixes.
/// [`unique_name`] keeps it clear of every member name.
pub const LEAD: &str = "lead";

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
    /// Messages whose target recorded them, as message id and target. An
    /// answer reuses the question's identity, so the target separates the
    /// two deliveries the pair produces.
    pub delivered: BTreeSet<(String, String)>,
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
        self.queue.iter().filter(|m| !self.delivered.contains(&(m.message_id.clone(), m.to.clone())))
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
fn refresh(state: &mut TeamState, folded: &foe_log::State, events: &[Event]) {
    let live_from = folded.seeded_through.map_or(0, |seq| seq + 1);
    state.tasks.clear();
    if let Some(start) = &folded.start {
        let outcome = folded.outcome.clone();
        let phase =
            if matches!(outcome, Some(Outcome::Failed { .. })) { MemberPhase::Failed } else { MemberPhase::Active };
        let status = outcome.as_ref().map_or(TaskStatus::Running, task_status);
        let name = start.contract["name"].as_str().unwrap_or("lead").to_string();
        state.lead_id = start.id.clone();
        if state.roster.is_empty() {
            state.roster.push(Member {
                member_id: start.id.clone(),
                name: name.clone(),
                description: start.task.clone(),
                phase,
                task_status: Some(status),
            });
        } else {
            state.roster[0].phase = phase;
        }
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
            write: Vec::new(),
            outcome,
            call_id: String::new(),
        });
    }
    state.tasks.extend(folded.tasks.iter().cloned());
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
            EventData::TeamDelivered { message_id, to } => {
                state.delivered.insert((message_id.clone(), to.clone()));
            }
            _ => {}
        }
    }
    for member in &mut state.roster {
        member.task_status =
            state.tasks.iter().find(|task| task.owner.as_deref() == Some(&member.member_id)).map(|task| task.status);
    }
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
    projection: Mutex<(usize, TeamState)>,
}

impl Team {
    pub fn new(
        lead_id: String,
        log: Arc<dyn LeadLog>,
        inbox: Arc<dyn InboxSink>,
        router: Arc<Router>,
        pool: Arc<Mutex<Pool>>,
    ) -> Self {
        Team { lead_id, log, inbox, router, pool, operations: Mutex::new(()), projection: Mutex::default() }
    }

    pub fn state(&self) -> TeamState {
        let mut projection = self.projection.lock().unwrap();
        self.log.with_state(&mut |folded, events| {
            let (scanned, state) = &mut *projection;
            if folded.seeded_through.is_some_and(|seq| *scanned as u64 <= seq) {
                *state = TeamState::default();
            }
            if *scanned < events.len() {
                refresh(state, folded, &events[*scanned..]);
                *scanned = events.len();
            }
        });
        projection.1.clone()
    }

    /// Adds a task and starts every queued task whose dependencies and
    /// capacity permit. A concurrency refusal keeps the task queued.
    pub fn delegate(
        self: &Arc<Self>,
        spawner: Arc<dyn Spawner>,
        req: SpawnRequest,
        name: Option<&str>,
        blocked_by: Vec<String>,
    ) -> Result<TeamTask, CapError> {
        let req = spawner.prepare(req)?;
        let write =
            req.write.as_deref().unwrap_or_default().iter().map(|path| path.display().to_string()).collect::<Vec<_>>();
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
            // Two workers writing under one root is the failure a partition
            // exists to prevent, and the board is where the partition is
            // visible. A task that has settled has stopped writing, and one
            // this task waits for cannot still be writing when it starts, so
            // a unit that integrates what two others wrote is not an overlap.
            let waits_for = awaited(&state, &blocked_by);
            if let Some((task, root)) = state
                .tasks
                .iter()
                .filter(|task| !settled(task.status) && !waits_for.contains(&task.task_id))
                .find_map(|task| overlap(&task.write, &write).map(|root| (task, root)))
            {
                return Err(CapError::Invalid(format!(
                    "write {root} is under or over {}, which {} is still writing",
                    task.write.join(", "),
                    task.name
                )));
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
                write,
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
                let ready = state
                    .tasks
                    .iter()
                    .filter(|task| task.status == TaskStatus::Queued)
                    .filter_map(|task| {
                        let blockers: Vec<_> = task
                            .blocked_by
                            .iter()
                            .filter_map(|id| state.task(id))
                            .filter(|blocker| blocker.status != TaskStatus::Completed)
                            .collect();
                        (blockers.is_empty() || blockers.iter().any(|blocker| settled(blocker.status)))
                            .then_some((task, blockers))
                    })
                    .min_by_key(|(_, blockers)| blockers.is_empty());
                let Some((task, blockers)) = ready else {
                    return Ok(());
                };
                let task = task.clone();
                if !blockers.is_empty() {
                    let names = blockers.iter().map(|task| task.task_id.as_str()).collect::<Vec<_>>().join(", ");
                    self.settle_task(
                        task,
                        Outcome::Blocked {
                            code: BlockedCode::ChildBlocked,
                            message: format!("dependency tasks did not complete: {names}"),
                        },
                    )?;
                    continue;
                }
                let child_id = spawner.allocate_id();
                let request = SpawnRequest {
                    contract: task.contract.clone(),
                    task: task.description.clone(),
                    context: task.context,
                    reserve: BudgetAmount::default(),
                    write: Some(task.write.iter().map(PathBuf::from).collect()),
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
        let item = InboxItem::new(InboxSource::Parent, content, Some(self.lead_id.clone()), None);
        self.router.send_inbox(&member.member_id, &item)
    }

    /// Stops a running member, addressed by roster name. The child ends its
    /// own episode as blocked with `cancelled`, which settles its board task
    /// and returns its reservation through the path every settlement takes.
    /// A member that has already settled is not an error: the lead asked for
    /// a state that already holds.
    pub fn cancel(&self, name: &str, reason: &str) -> Result<(), CapError> {
        self.log.check()?;
        let state = self.state();
        let member = state.member(name).ok_or_else(|| CapError::Invalid(format!("no member named {name}")))?;
        if !self.router.has_child(&member.member_id) {
            return Ok(());
        }
        self.log.append(EventData::TeamRoster {
            member_id: member.member_id.clone(),
            name: name.to_string(),
            description: format!("stopping: {reason}"),
            phase: MemberPhase::Active,
        })?;
        self.router.cancel(&member.member_id)
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
    fn send(&self, from: &str, to_name: &str, content: Vec<ContentBlock>, kind: Correlate) -> Result<String, CapError> {
        let _guard = self.operations.lock().unwrap();
        let state = self.state();
        let target = match to_name {
            LEAD => state.member_by_id(&state.lead_id),
            name => state.member(name),
        }
        .ok_or_else(|| CapError::Invalid(format!("no member named {to_name}")))?;
        let (source, answers) = kind;
        // An answer carries the identifier of the question it answers, which
        // is what lets the asker wait for this reply rather than any arrival.
        let message_id = answers.unwrap_or_else(|| format!("{}:tm_{:02}", self.lead_id, state.queue.len() + 1));
        // A model receives the rendered content of an inbox item and no
        // other field, so a question whose identifier stays in `message_id`
        // alone cannot be answered: the answerer has nothing to put in
        // `reply_to`. The question carries its identifier in its text.
        let mut content = content;
        if source == InboxSource::Request {
            content.push(ContentBlock::Text { text: format!("Answer this with send, reply_to {message_id}.") });
        }
        self.log.append(EventData::TeamMessage {
            message_id: message_id.clone(),
            from: from.to_string(),
            to: target.member_id.clone(),
            content: content.clone(),
        })?;
        let item = InboxItem::new(source, content, Some(from.to_string()), Some(message_id.clone()));
        if target.member_id == self.lead_id {
            self.inbox.append(item);
            // A message the lead is the target of reaches its inbox here
            // rather than through the router, so no child observation
            // follows to close the delivery this message opened. Without
            // this the board reports every question a member asked the lead
            // as undelivered for the life of the run.
            self.log.append(EventData::TeamDelivered { message_id: message_id.clone(), to: self.lead_id.clone() })?;
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
            EventData::InboxItem(item)
                if matches!(item.source, InboxSource::Peer | InboxSource::Request | InboxSource::Response) =>
            {
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
            Kind::Spawn | Kind::Steer | Kind::Cancel => return None,
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
            Kind::Send | Kind::Ask => {
                arg(args, "to").map_or_else(|e| e, |to| self.send_value(child_id, to, content, correlate(kind, args)))
            }
            _ => self.roster(),
        })
    }
}

impl Team {
    /// Delivers `default` to this episode under the question's identity once
    /// `after` has passed. The asking process holds the deadline, so the rule
    /// applies whatever answers the question, including a host application
    /// that runs no episode. The inbox drops an item whose `message_id` it
    /// already holds, so exactly one answer reaches the asker: the teammate's
    /// answer when it arrives before the deadline, and this default when it
    /// does not.
    fn default_answer(&self, message_id: String, after: Duration, default: String) {
        let inbox = self.inbox.clone();
        tokio::spawn(async move {
            tokio::time::sleep(after).await;
            let text = format!("no answer before the deadline; this default answer stands: {default}");
            let item = InboxItem::new(InboxSource::Response, text_content(&text), None, Some(message_id));
            inbox.append(InboxItem { synthetic: true, ..item });
        });
    }

    /// The result of a `send` call: the message id, or the failure.
    fn send_value(&self, from: &str, to: &str, content: Vec<ContentBlock>, kind: Correlate) -> ToolValue {
        match self.send(from, to, content, kind) {
            Ok(id) => {
                ToolValue::ok(serde_json::json!({ "to": to, "message_id": &id }), format!("sent to {to} as {id}"))
            }
            Err(e) => ToolValue::error(format!("send: {e}")),
        }
    }

    /// The result of a `team` call: its board and members as data and text.
    fn roster(&self) -> ToolValue {
        let state = self.state();
        ToolValue::ok(state.value(), state.roster_text())
    }
}

/// What one message is: the inbox source it arrives under and, when it
/// answers a question, the identity of that question. A statement expects
/// nothing back and arrives as `peer`. A question arrives as `request` under
/// an identity of its own. An answer arrives as `response` and carries its
/// question's identity, so the asker waits for that answer and not for any
/// arrival.
pub type Correlate = (InboxSource, Option<String>);

/// How long a question stays open and the answer that stands when that time
/// passes. `ask` requires both, and refuses the call without them, so no
/// episode can wait on another without end.
fn asked_bound(args: &serde_json::Value) -> Result<(Duration, String), ToolValue> {
    let held = args.get("deadline_ms").and_then(serde_json::Value::as_u64).filter(|ms| *ms > 0);
    let ms = held.ok_or_else(|| ToolValue::invalid("deadline_ms: whole milliseconds above zero are required"))?;
    Ok((Duration::from_millis(ms), arg(args, "default")?.to_string()))
}

/// What a `send` or an `ask` call makes of its message.
fn correlate(kind: Kind, args: &serde_json::Value) -> Correlate {
    match (kind, args.get("reply_to").and_then(serde_json::Value::as_str)) {
        (Kind::Ask, _) => (InboxSource::Request, None),
        (_, Some(asked)) => (InboxSource::Response, Some(asked.to_string())),
        _ => (InboxSource::Peer, None),
    }
}

fn text_content(text: &str) -> Vec<ContentBlock> {
    vec![ContentBlock::Text { text: text.to_string() }]
}

/// An inbox item a child sends its parent.
fn child_item(child_id: &str, content: Vec<ContentBlock>) -> InboxItem {
    InboxItem::new(InboxSource::Child, content, Some(child_id.to_string()), None)
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

/// Every task the given dependencies wait on, dependencies included. The
/// board is acyclic by construction, so the walk terminates.
fn awaited(state: &TeamState, blocked_by: &[String]) -> BTreeSet<String> {
    let mut seen = BTreeSet::new();
    let mut pending: Vec<String> = blocked_by.to_vec();
    while let Some(id) = pending.pop() {
        if !seen.insert(id.clone()) {
            continue;
        }
        if let Some(task) = state.task(&id) {
            pending.extend(task.blocked_by.iter().cloned());
        }
    }
    seen
}

/// The first root of `wanted` that is under or over one of `held`, if any.
/// Containment either way is an overlap: a worker given a directory and one
/// given a file inside it write the same bytes.
fn overlap(held: &[String], wanted: &[String]) -> Option<String> {
    let canonical = |path: &String| Path::new(path).canonicalize().unwrap_or_else(|_| PathBuf::from(path));
    let under = |a: &String, b: &String| canonical(a).starts_with(canonical(b));
    wanted.iter().find(|root| held.iter().any(|h| under(root, h) || under(h, root))).cloned()
}

fn unique_name(state: &TeamState, requested: &str, task_id: &str) -> String {
    if requested != LEAD
        && state.roster.iter().all(|member| member.name != requested)
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
    Cancel,
    Notify,
    Send,
    Ask,
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
                        "write": { "type": "array", "items": { "type": "string" }, "description": "the directories this worker may write in, each one that exists already, within your own and within what its contract allows, and not under or over a root another live task holds; naming a file, or a file the worker has yet to write, is refused; omitted, it writes nothing" },
                    }),
                    &["contract", "task"],
                ),
                Effect::Spawns,
            ),
            Kind::Wait => (
                "Wait until every task added to this episode's board has settled and no child reservation remains. Their reports are in the request that follows. Returns at once when the board has no added task. \
Use it before acting on delegated work. An episode that ends while a child runs ends that child. With `until`, wait instead until an arrival matches one condition. That form states how long it will block: name `timeout_seconds` unless this episode has a seconds budget. \
The result names the condition met, or `timeout`. The arrival itself is in the request that follows.",
                object(
                    serde_json::json!({
                        "until": {
                            "type": "array",
                            "description": "conditions, of which the first met ends the wait: {child, outcome?} for a child episode (id, or \"any\") reaching any outcome or the named kind; {session} for a process session (id, or \"any\") exiting; {inbox} for an inbox arrival by source; {reply} for the answer to the question whose message_id `ask` returned",
                            "items": { "anyOf": [
                                { "type": "object", "properties": { "child": string("child episode id, or \"any\""), "outcome": { "type": "string", "enum": ["completed", "blocked", "exhausted", "failed"] } }, "required": ["child"], "additionalProperties": false },
                                { "type": "object", "properties": { "session": { "type": ["integer", "string"], "description": "session id, or \"any\"" } }, "required": ["session"], "additionalProperties": false },
                                { "type": "object", "properties": { "inbox": { "type": "string", "enum": ["task", "parent", "child", "peer", "request", "response", "verify", "system", "session"] } }, "required": ["inbox"], "additionalProperties": false },
                                { "type": "object", "properties": { "reply": string("the message_id `ask` returned") }, "required": ["reply"], "additionalProperties": false }
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
            Kind::Cancel => (
                "Stop a running child, addressed by roster name. Its episode ends blocked with `cancelled`, its \
board task settles, and its unspent reservation returns. Use it to course-correct: a worker whose unit you no \
longer need, or that the answers already in hand have made wrong. What it wrote before it stopped stands.",
                object(
                    serde_json::json!({ "to": string("roster name"), "reason": string("why it is stopping, for the board") }),
                    &["to"],
                ),
                Effect::Pure,
            ),
            Kind::Notify => (
                "Send a message to the episode that started this one.",
                object(serde_json::json!({ "content": string("the message") }), &["content"]),
                Effect::Pure,
            ),
            Kind::Ask => (
                "Ask a teammate a question, addressed by roster name, through the lead. It returns the question's `message_id`, and `wait` with `{reply: that id}` blocks until that question is answered and not until any message arrives. \
A question is bounded: `deadline_ms` says how long it stays open, `default` says what answer stands when that time passes with no answer, and the runtime delivers that answer itself, so no episode waits on another without end. \
`scope` selects the team: the one this episode belongs to, or the one it leads. Use it when one answer from one teammate decides what you do next; `send` is for telling.",
                object(
                    serde_json::json!({
                        "to": string("roster name of the teammate, or `lead` for the episode leading the team"),
                        "content": string("the question"),
                        "deadline_ms": { "type": "integer", "minimum": 1, "description": "how long the question stays open, in milliseconds; when it passes with no answer the runtime delivers `default` as the answer" },
                        "default": string("the answer that stands when the deadline passes with no answer"),
                        "scope": { "type": "string", "enum": ["member", "led"], "description": "member selects the team this episode belongs to, the default; led selects the team it leads" }
                    }),
                    &["to", "content", "deadline_ms", "default"],
                ),
                Effect::Pure,
            ),
            Kind::Send => (
                "Send a message to a teammate, addressed by roster name, through the lead. With `reply_to`, it \
answers the question that identifier names, and the teammate waiting on that answer wakes. `scope` selects \
the team: the one this episode belongs to, or the one it leads, which is how a member answers a question its \
own child asked.",
                object(
                    serde_json::json!({
                        "to": string("roster name of the teammate, or `lead` for the episode leading the team"),
                        "content": string("the message"),
                        "reply_to": string("the message_id of a question this answers"),
                        "scope": { "type": "string", "enum": ["member", "led"], "description": "member selects the team this episode belongs to, the default; led selects the team it leads" }
                    }),
                    &["to", "content"],
                ),
                Effect::Pure,
            ),
            Kind::Team => (
                "List a team's lead, members, and task board. The default is the team this episode belongs to. `led` selects the team of children this episode leads.",
                object(
                    serde_json::json!({ "scope": { "type": "string", "enum": ["member", "led"], "description": "member selects the team this episode belongs to, the default; led selects the team it leads" } }),
                    &[],
                ),
                Effect::Pure,
            ),
        };
        ToolSpec { name: kebab(&self), description: description.to_string(), instruction: None, params, effect }
    }
}

/// Which team a call addresses: the one this episode belongs to, or the one
/// it leads. A root's two are the same team.
fn leads_scope(args: &serde_json::Value) -> Result<bool, ToolValue> {
    match args.get("scope").and_then(|value| value.as_str()) {
        None | Some("member") => Ok(false),
        Some("led") => Ok(true),
        Some(scope) => Err(ToolValue::invalid(format!("scope: {scope} is neither member nor led"))),
    }
}

const KINDS: [Kind; 8] =
    [Kind::Spawn, Kind::Wait, Kind::Steer, Kind::Cancel, Kind::Notify, Kind::Send, Kind::Ask, Kind::Team];

/// The specifications of the eight team tools, in the order [`tools`] lists
/// them. Fingerprint and `foe plan` use this without a running team.
pub fn builtin_specs() -> Vec<ToolSpec> {
    KINDS.into_iter().map(Kind::spec).collect()
}

/// The eight team tools. `parent` is the link to the process hosting this
/// episode, when it has one. `notify` goes to that parent. `send`, `ask`,
/// and `team` select the parent-led or locally led team from one schema, so
/// an episode in the middle of a tree can answer the team it leads as well
/// as the one it belongs to.
pub fn tools(team: Arc<Team>, parent: Option<&Host>) -> Vec<Box<dyn Tool>> {
    KINDS
        .into_iter()
        .map(|kind| match (parent, kind) {
            (Some(host), Kind::Notify) => host.tool(kind.spec()),
            (Some(host), Kind::Send | Kind::Ask | Kind::Team) => {
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
    /// The answer to one question, named by the `message_id` `ask` returned.
    Reply {
        reply: String,
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
fn matched(state: &foe_log::State, until: &[Condition]) -> Option<usize> {
    for (item, consumed) in state.inbox.values() {
        if *consumed {
            continue;
        }
        let from = item.from.as_deref().unwrap_or_default();
        let met = |condition: &Condition| match condition {
            Condition::Inbox { inbox } => item.source == *inbox,
            Condition::Reply { reply } => {
                item.source == InboxSource::Response && item.message_id.as_deref() == Some(reply.as_str())
            }
            Condition::Session { session } => {
                item.source == InboxSource::Session && session.as_u64().is_none_or(|id| from == id.to_string())
            }
            Condition::Child { child, outcome } => {
                item.source == InboxSource::Child
                    && (child == "any" || child == from)
                    && state
                        .children
                        .get(from)
                        .and_then(Option::as_ref)
                        .is_some_and(|ended| outcome.is_none_or(|wanted| wanted == kind_of(ended)))
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
                let write = match string_list(&args, "write") {
                    Ok(values) => values,
                    Err(error) => return error,
                };
                // The spawner reserves the child's whole share and records what it granted.
                let req = SpawnRequest {
                    contract: contract.clone(),
                    task,
                    context,
                    reserve: BudgetAmount::default(),
                    // The tool always states the grant, so a worker writes
                    // where its lead named and nowhere else. A declared grant
                    // is the ceiling, never the default: an omitted `write`
                    // is a worker that writes nothing.
                    write: Some(write.iter().map(PathBuf::from).collect()),
                    call_id: ctx.call_id.clone(),
                };
                match self.team.delegate(spawner.clone(), req, Some(&name), blocked_by) {
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
            Kind::Steer | Kind::Send | Kind::Ask => {
                let (to, content) = match (arg(&args, "to"), arg(&args, "content")) {
                    (Ok(t), Ok(c)) => (t, text_content(c)),
                    (Err(e), _) | (_, Err(e)) => return e,
                };
                if matches!(self.kind, Kind::Send | Kind::Ask) {
                    let started = Instant::now();
                    let bound = match (self.kind, asked_bound(&args)) {
                        (Kind::Ask, Err(invalid)) => return invalid,
                        (Kind::Ask, Ok(bound)) => Some(bound),
                        _ => None,
                    };
                    let sent = match (leads_scope(&args), &self.parent) {
                        (Err(invalid), _) => return invalid,
                        (Ok(false), Some(parent)) => {
                            let call = parent.call(args, ctx);
                            match &bound {
                                Some((after, default)) => tokio::time::timeout_at(started + *after, call).await
                                    .unwrap_or_else(|_| ToolValue::failed(ToolFailureCode::TimedOut,
                                        format!("ask: deadline_ms elapsed before delivery was acknowledged; default: {default}"),
                                        false, serde_json::json!({}))),
                                None => call.await,
                            }
                        }
                        _ => self.team.send_value(&self.team.lead_id, to, content, correlate(self.kind, &args)),
                    };
                    if let (Some((after, default)), Some(id)) = (bound, sent.value["message_id"].as_str()) {
                        self.team.default_answer(id.to_string(), after.saturating_sub(started.elapsed()), default);
                    }
                    return sent;
                }
                match self.team.steer(to, content) {
                    Ok(()) => ToolValue::ok(serde_json::json!({ "to": to }), format!("sent to {to}")),
                    Err(e) => ToolValue::error(format!("steer: {e}")),
                }
            }
            Kind::Cancel => {
                let to = match arg(&args, "to") {
                    Ok(to) => to,
                    Err(e) => return e,
                };
                let reason = args.get("reason").and_then(serde_json::Value::as_str).unwrap_or("no reason given");
                match self.team.cancel(to, reason) {
                    Ok(()) => ToolValue::ok(serde_json::json!({ "to": to }), format!("stopping {to}")),
                    Err(e) => ToolValue::error(format!("cancel: {e}")),
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
                let deadline = match (ctx.deadline.map(Instant::from_std), timeout) {
                    (Some(budget), Some(asked)) => Some(budget.min(asked)),
                    (budget, asked) => budget.or(asked),
                };
                // An `until` wait holds for something another episode does,
                // and nothing here makes that happen, so it states how long it
                // will block or it is refused. The bare form needs no such
                // statement: it holds for tasks this episode created, each
                // bounded by its own budget and by this rule in its turn.
                if deadline.is_none() && !parsed.until.is_empty() {
                    return ToolValue::invalid("timeout_seconds: an `until` wait needs a bound; this episode has none");
                }
                let timed_out = || ToolValue::ok(serde_json::json!({ "matched": "timeout" }), "timeout");
                if parsed.until.is_empty() {
                    loop {
                        let mut pending = 0;
                        self.team.log.with_state(&mut |state, _| {
                            pending = state.tasks.iter().filter(|task| !settled(task.status)).count();
                        });
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
                    let mut hit = None;
                    self.team.log.with_state(&mut |state, _| hit = matched(state, &parsed.until));
                    if let Some(index) = hit {
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
            Kind::Team => match (leads_scope(&args), &self.parent) {
                (Err(invalid), _) => invalid,
                (Ok(false), Some(parent)) => parent.call(serde_json::json!({}), ctx).await,
                _ => self.team.roster(),
            },
        }
    }
}

#[cfg(test)]
#[path = "lib_test.rs"]
mod tests;
