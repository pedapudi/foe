//! Reading an episode log into the typed facts the rest of the crate needs.
//!
//! Everything downstream reads [`Facts`] and never the log, so the set of
//! log fields telemetry can reach is exactly what this file names. Adding a
//! field here is the decision to consider emitting it.

use foe_log::{Event, EventData, Outcome, StopReason, Usage};
use std::collections::BTreeMap;

/// One model call and the response that closed it.
pub struct Step {
    pub step: u32,
    pub seq: u64,
    pub start_ms: i64,
    pub end_ms: i64,
    pub usage: Usage,
    pub stop: Option<StopReason>,
}

/// One tool call, as its result records it.
pub struct Call {
    pub seq: u64,
    pub name: String,
    pub start_ms: i64,
    pub end_ms: i64,
    pub duration_ms: u64,
    pub is_error: bool,
    pub subject: String,
}

/// Structural signals the classifier votes on. Counts, never text bodies.
#[derive(Default)]
pub struct Evidence {
    pub extensions: BTreeMap<String, u32>,
    pub heads: BTreeMap<String, u32>,
    pub tools: BTreeMap<String, u32>,
    pub spawns: u32,
    pub workflow_nodes: u32,
}

/// A value the scrubber must never let through, with the pseudonym type tag
/// it is replaced by: `p` for a path, `u` for a user name.
pub struct KnownValue {
    pub tag: char,
    pub value: String,
}

/// Everything telemetry knows about one episode.
#[derive(Default)]
pub struct Facts {
    pub id: String,
    pub identity: String,
    pub runtime_version: String,
    pub runtime_build: String,
    pub provider: String,
    pub model: String,
    pub start_ms: i64,
    pub end_ms: i64,
    pub usage: Usage,
    pub model_calls: u64,
    pub outcome: Option<Outcome>,
    pub steps: Vec<Step>,
    pub calls: Vec<Call>,
    pub evidence: Evidence,
    pub known: Vec<KnownValue>,
}

/// A known value shorter than this is not substituted: a one- or two-
/// character root such as `/` matches inside unrelated words, and
/// substituting it would destroy the text without protecting anything.
const MIN_KNOWN_LEN: usize = 3;

/// Tool argument keys whose value is a path the classifier reads an
/// extension from. `glob` is a grep filter rather than a path, and its
/// extension is the more direct signal of the two.
const PATH_ARGS: &[&str] = &["path", "glob"];

/// Reads `events` into facts. `log_dir` is the directory the log was read
/// from; it joins the known set because tool subjects quote it.
pub fn extract(events: &[Event], log_dir: &str) -> Facts {
    let time = |event: Option<&Event>| event.map(|e| e.time).unwrap_or_default();
    let mut facts = Facts { start_ms: time(events.first()), end_ms: time(events.last()), ..Facts::default() };
    push_known(&mut facts.known, 'p', log_dir);
    let mut open: BTreeMap<String, usize> = BTreeMap::new();
    for event in events {
        match &event.data {
            EventData::EpisodeStart(start) => {
                facts.id = start.id.clone();
                facts.identity = start.identity.clone();
                facts.runtime_version = start.runtime.version.clone();
                facts.runtime_build = start.runtime.build.clone();
                program_known(&start.program, &mut facts.known);
            }
            EventData::EpisodeEnd { outcome } => facts.outcome = Some(outcome.clone()),
            EventData::RequestHeader(header) => {
                facts.provider = header.model.provider.clone();
                facts.model = header.model.model.clone();
            }
            EventData::ModelRequest(request) => {
                facts.model_calls += 1;
                open.insert(request.request_id.clone(), facts.steps.len());
                let (step, seq, at) = (request.step, event.seq, event.time);
                facts.steps.push(Step { step, seq, start_ms: at, end_ms: at, usage: Usage::default(), stop: None });
            }
            EventData::AssistantMessage(message) => {
                facts.usage.input += message.usage.input;
                facts.usage.output += message.usage.output;
                facts.usage.cache_read += message.usage.cache_read;
                if let Some(step) = open.remove(&message.request_id).and_then(|i| facts.steps.get_mut(i)) {
                    step.end_ms = event.time;
                    step.usage = message.usage;
                    step.stop = Some(message.stop);
                }
                for call in &message.tool_calls {
                    *facts.evidence.tools.entry(call.name.clone()).or_default() += 1;
                    call_evidence(&call.name, &call.args, &mut facts.evidence);
                }
            }
            EventData::ToolResult(result) => facts.calls.push(Call {
                seq: event.seq,
                name: result.name.clone(),
                start_ms: event.time - result.duration_ms as i64,
                end_ms: event.time,
                duration_ms: result.duration_ms,
                is_error: result.is_error,
                subject: result.subject.clone().unwrap_or_default(),
            }),
            EventData::SpawnStart { .. } => facts.evidence.spawns += 1,
            EventData::WorkflowNodeStart(_) => facts.evidence.workflow_nodes += 1,
            _ => {}
        }
    }
    facts.known.sort_by(|a, b| b.value.len().cmp(&a.value.len()).then(a.value.cmp(&b.value)));
    facts.known.dedup_by(|a, b| a.value == b.value);
    facts
}

/// How an episode ended: its kind, the closed-vocabulary term qualifying
/// the kind, and its free text.
///
/// Only two of the four outcomes contribute text. A completed episode's
/// value is the report the model wrote, which is a result body and is never
/// emitted, and an exhausted episode names a limit and says nothing else.
pub fn outcome_terms(outcome: Option<&Outcome>) -> (&'static str, String, String) {
    let none = String::new();
    match outcome {
        Some(Outcome::Completed { .. }) => ("completed", "none".into(), none),
        Some(Outcome::Blocked { code, message }) => ("blocked", term(code), message.clone()),
        Some(Outcome::Exhausted { limit }) => ("exhausted", term(limit), none),
        Some(Outcome::Failed { error }) => ("failed", "none".into(), error.clone()),
        None => ("unfinished", "none".into(), none),
    }
}

/// The wire name of a closed-vocabulary enum, read back from its own
/// serialization so that the two can never disagree.
pub fn term<T: serde::Serialize>(value: &T) -> String {
    serde_json::to_value(value).ok().and_then(|v| v.as_str().map(str::to_string)).unwrap_or_default()
}

/// Absolute paths anywhere in the resolved configuration, plus the user
/// name component of any home directory among them.
fn program_known(program: &serde_json::Value, out: &mut Vec<KnownValue>) {
    let mut stack = vec![program];
    while let Some(value) = stack.pop() {
        match value {
            serde_json::Value::String(text) if text.starts_with('/') => {
                push_known(out, 'p', text);
                home_user(text).map(|user| push_known(out, 'u', user));
            }
            serde_json::Value::Array(items) => stack.extend(items.iter()),
            serde_json::Value::Object(fields) => stack.extend(fields.values()),
            _ => {}
        }
    }
}

/// The user name in `/home/<user>/…` or `/Users/<user>/…`, if any.
fn home_user(path: &str) -> Option<&str> {
    let rest = path.strip_prefix("/home/").or_else(|| path.strip_prefix("/Users/"))?;
    rest.split('/').next().filter(|user| !user.is_empty())
}

fn push_known(out: &mut Vec<KnownValue>, tag: char, value: &str) {
    let value = value.trim_end_matches('/');
    out.extend((value.len() >= MIN_KNOWN_LEN).then(|| KnownValue { tag, value: value.into() }));
}

/// Evidence carried by one tool call's arguments.
fn call_evidence(name: &str, args: &serde_json::Value, evidence: &mut Evidence) {
    if name == "bash" {
        let heads = command_heads(args["command"].as_str().unwrap_or_default());
        heads.into_iter().for_each(|head| *evidence.heads.entry(head).or_default() += 1);
        return;
    }
    let found: Vec<String> = PATH_ARGS.iter().filter_map(|key| args[key].as_str().and_then(extension)).collect();
    found.into_iter().for_each(|found| *evidence.extensions.entry(found).or_default() += 1);
}

/// The lower-cased extension of the last component of `path`, when it has
/// one short and alphanumeric enough to name a file type rather than being
/// a version number or the tail of a dotted name.
pub fn extension(path: &str) -> Option<String> {
    let (stem, extension) = path.rsplit('/').next()?.rsplit_once('.')?;
    let named = !stem.is_empty() && (1..=12).contains(&extension.len());
    (named && extension.chars().all(|c| c.is_ascii_alphabetic())).then(|| extension.to_ascii_lowercase())
}

/// Dispatchers whose subcommand carries the meaning: `cargo test` and
/// `cargo build` are different activities and the head alone says neither.
const DISPATCHERS: &[&str] = &["cargo", "go", "npm", "yarn", "pnpm", "bazel", "git", "dotnet", "mvn", "gradle"];

/// The head token of every segment of a shell command line.
///
/// Splitting is required rather than optional: a command line is a sequence
/// of segments joined by `&&`, `||`, `;`, `|`, and newlines, and treating
/// the whole line as one command classifies every line that begins with a
/// directory change as `cd`. A segment whose head is `cd` contributes
/// nothing and yields to the segment after it, and leading `VAR=value`
/// assignments are not the command.
pub fn command_heads(command: &str) -> Vec<String> {
    let mut heads = Vec::new();
    let breaks = regex::Regex::new(SEGMENT_BREAK).expect("the separator pattern is a constant");
    for segment in breaks.split(command) {
        let mut tokens = segment
            .split_whitespace()
            .skip_while(|t| is_assignment(t))
            .map(|t| t.trim_matches(|c| "()'\"`{}".contains(c)));
        let Some(head) = tokens.find(|t| !t.is_empty()) else { continue };
        let head = head.rsplit('/').next().unwrap_or(head).to_ascii_lowercase();
        if head == "cd" || head.starts_with('-') {
            continue;
        }
        if DISPATCHERS.contains(&head.as_str()) {
            if let Some(sub) = tokens.find(|t| !t.is_empty() && !t.starts_with('-')) {
                heads.push(format!("{head} {}", sub.to_ascii_lowercase()));
                continue;
            }
        }
        heads.push(head);
    }
    heads
}

/// Whether a token is a `VAR=value` environment assignment standing before
/// the command rather than the command itself.
fn is_assignment(token: &str) -> bool {
    let Some((name, _)) = token.split_once('=') else { return false };
    !name.is_empty() && name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
}

/// The shell operators that end one command and begin another. `&&` and
/// `||` are listed first so that neither is split twice as a single `&`
/// or `|`, which would leave an empty segment between the halves.
const SEGMENT_BREAK: &str = r"&&|\|\||[&|;\n]";

#[cfg(test)]
#[path = "extract_test.rs"]
mod tests;
