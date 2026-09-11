//! The running form: load the contract document, restrict the process, compose
//! the runtime's parts, run one episode, and report the outcome.
//!
//! Order matters in [`run`]. Everything that reads a file outside the
//! grants, opens a listening socket, or starts a browser happens before the
//! process restricts itself. `foe_core::confine` carries that order: the
//! policy is assembled through an `Unconfined`, and entering confinement
//! consumes it, so no later line can add to the policy. The restriction is
//! applied on the main thread before the asynchronous runtime starts, so
//! every thread of the episode inherits it.

use foe_contract::document::{resolve, resolve_with_executables, ResolvedContract};
use foe_contract::fingerprint::{compute, Fingerprint};
use foe_contract::{Budget, ContractDocument, ModelConfig, ToolSpec};
use foe_core::budget::Pool;
use foe_core::captured_executable::{process_fd_path, CapturedExecutableTree, InheritedExecutables};
use foe_core::confine::{Confined, Unconfined};
use foe_core::context::ContextPolicy;
use foe_core::exec::LocalExecutor;
use foe_core::fingerprint::runtime_info;
use foe_core::grants::RootReader;
use foe_core::loop_::{self, Log, Params};
use foe_core::process_boundary::ProcessOwnership;
use foe_core::protocol::{Channel, Host};
use foe_core::registry::{Handles, Registry};
use foe_core::sandbox::{Policy, Sandbox};
use foe_core::session::LocalSessions;
use foe_core::spawn::{ChildLaunch, ProcessConnections, ProcessSpawner, Router, Uplink};
use foe_core::wiring::{BudgetedSpawner, NoHostUplink};
use foe_core::{Spawner, Tool, Transport, Writer};
use foe_log::seed::{SeedContract, SeedHeader};
use foe_log::{ContentBlock, EpisodeStart, EventData, InboxItem, InboxSource, LogError, Outcome};
use foe_team::{self as team, Team};
use foe_workflow::WorkflowParams;
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

/// How long the viewer stays reachable after the episode ends and the
/// outcome is displayed, so that an open page receives the final events.
const VIEWER_GRACE: Duration = Duration::from_secs(3);

const BUILTIN_IMPLEMENTATION_CALLS: u64 = 60;
const BUILTIN_ASSESSMENT_CALLS: u64 = 60;
const BUILTIN_REPAIR_CALLS: u64 = 60;
const BUILTIN_VERIFIER_RETRIES: u32 = 12;

/// Static behavior of the built-in coding workflow. Dynamic permissions,
/// environment, model, verifier, and task values are filled below.
const BUILTIN_CONTRACT_DOCUMENT: &str = include_str!("builtin-coding.json");
const BUILTIN_TEAM_DOCUMENT: &str = include_str!("builtin-team.json");
const BUILTIN_EXECUTABLE_PROBES: &[(&str, &str)] = &[
    ("sh", "/bin/sh"),
    ("bash", "/bin/bash"),
    ("git", "/usr/bin/git"),
    ("python3", "/usr/bin/python3"),
    ("file", "/usr/bin/file"),
    ("xxd", "/usr/bin/xxd"),
    ("od", "/usr/bin/od"),
    ("awk", "/usr/bin/awk"),
    ("strings", "/usr/bin/strings"),
    ("gcc", "/usr/bin/gcc"),
    ("clang", "/usr/bin/clang"),
    ("make", "/usr/bin/make"),
    ("cmake", "/usr/bin/cmake"),
    ("cargo", "/usr/bin/cargo"),
    ("node", "/usr/bin/node"),
    ("go", "/usr/bin/go"),
];

/// The built-in coding workflow declares these standard command roots for
/// its shell-based tools. Configured contracts choose their own execute roots.
pub(crate) const BUILTIN_EXECUTE_ROOTS: &[&str] = &["/bin", "/usr/bin", "/usr/local/bin"];

fn builtin_environment(cwd: &Path, present: impl Fn(&Path) -> bool) -> String {
    let probe = |(name, path): &(&str, &str)| match present(Path::new(path)) {
        true => format!("{name}={path}"),
        false => format!("{name}=not found at {path}"),
    };
    let availability = BUILTIN_EXECUTABLE_PROBES.iter().map(probe).collect::<Vec<_>>().join(", ");
    format!(
        "Working directory: {}. Fixed-path executable probe: {availability}. A not-found result covers only the \
         listed standard locations; project-local tools may still exist.",
        cwd.display()
    )
}

#[derive(Debug, Default)]
pub struct Options {
    pub task: Option<String>,
    /// The `--config` value: a built-in document name or a file path.
    pub config: Option<String>,
    pub model: Option<String>,
    /// Provider service tier of the `model` block the command line supplies.
    pub service_tier: Option<String>,
    /// An executable verifier for a built-in document.
    pub verify: Option<PathBuf>,
    /// Kernel confinement mode for a built-in document.
    pub sandbox: Option<String>,
    /// Whether a built-in document grants the whole filesystem and runs
    /// with kernel confinement off.
    pub permit_everything: bool,
    pub log_dir: Option<PathBuf>,
    /// The source log directory of `--from DIR[@SEQ]`.
    pub from: Option<PathBuf>,
    /// The boundary `--from DIR@SEQ` names: source events with `seq` in
    /// `[1, at)` are copied. Absent when the value carried no boundary.
    pub at: Option<u64>,
    pub viewer: Viewer,
    pub conversation: bool,
    /// The descriptors of `--protocol-fds READ,WRITE`: the host's answers
    /// are read from the first and every log event is written to the second.
    pub protocol_fds: Option<(i32, i32)>,
}

/// The built-in tools implemented outside the registry: the coding tools
/// and the lead's team tools. Fingerprint and the registry receive this same
/// list, so `foe plan` and a run agree.
pub fn extra_builtin_specs() -> Vec<ToolSpec> {
    foe_code::all()
        .iter()
        .map(|t| t.spec().clone())
        .chain(std::iter::once(foe_core::retrieval::spec()))
        .chain(team::builtin_specs())
        .collect()
}

/// Adds exact interpreter access for every selected built-in executable
/// in the reachable contract tree.
pub fn add_builtin_runtime_access(policy: &mut Policy, contract: &ResolvedContract) -> Result<(), String> {
    for (contract_key, descendant) in
        contract.contract_tree(foe_contract::document::ContractTreeSelection::ExecutableReachable)
    {
        for (path, purpose) in foe_code::required_executables(&descendant.tools) {
            policy.add_executable(Path::new(path), format!("{purpose} selected by {contract_key}.tools"))?;
        }
    }
    Ok(())
}

/// Adds credential-file access for every reachable built-in model
/// transport. A descendant inherits the enclosing Landlock domain before
/// it opens its own credential.
pub fn add_transport_runtime_access(policy: &mut Policy, contract: &ResolvedContract) -> Result<(), String> {
    for (contract_key, descendant) in
        contract.contract_tree(foe_contract::document::ContractTreeSelection::ExecutableReachable)
    {
        let Some(model) = &descendant.model else { continue };
        let plan = foe_transport::plan(model).map_err(|e| format!("{contract_key}.model: {e}"))?;
        if let Some(path) = plan.credential_path {
            let home = if plan.home_from_environment {
                "; home directory resolved from HOME because the user has no passwd entry"
            } else {
                ""
            };
            policy.add_read_file(path, format!("credential for configured model endpoint in {contract_key}{home}"));
        }
    }
    Ok(())
}

pub fn fingerprint(contract: &ResolvedContract) -> Result<Fingerprint, String> {
    compute(contract, &extra_builtin_specs(), &runtime_info()).map_err(|e| e.to_string())
}

pub fn runtime() -> Result<tokio::runtime::Runtime, String> {
    let mut runtime = tokio::runtime::Builder::new_multi_thread();
    runtime.worker_threads(2).enable_all().build().map_err(|e| format!("runtime: {e}"))
}

/// What a run does about the browser viewer.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub enum Viewer {
    /// Serve the viewer and open the browser on it.
    #[default]
    Open,
    /// Serve the viewer and leave opening it to the person.
    Serve,
    /// Serve no viewer.
    Off,
}

impl Viewer {
    /// Reads a `--viewer` value, naming the three it accepts on any other.
    pub fn parse(value: &str) -> Result<Self, String> {
        match value {
            "open" => Ok(Self::Open),
            "serve" => Ok(Self::Serve),
            "off" => Ok(Self::Off),
            other => Err(format!("--viewer {other}: expected open, serve, or off")),
        }
    }
}

/// Reads a `--protocol-fds READ,WRITE` value: the two descriptor numbers
/// the host left open, in the order foe reads from and writes to them.
pub fn protocol_fds(value: &str) -> Result<(i32, i32), String> {
    let pair = value.split_once(',').and_then(|(r, w)| Some((r.trim().parse().ok()?, w.trim().parse().ok()?)));
    pair.ok_or_else(|| format!("--protocol-fds {value}: expected READ,WRITE, two open descriptor numbers"))
}

/// The two halves of the protocol channel the descriptors name. Each is
/// reopened through `/proc/self/fd`, which requires a pipe and refuses a
/// socket, and each is opened before the process restricts itself.
fn protocol_channel(fds: (i32, i32)) -> Result<(std::fs::File, Channel), String> {
    let named = |fd: i32, e: std::io::Error| format!("--protocol-fds: descriptor {fd}: {e}");
    let read = std::fs::File::open(process_fd_path(fds.0)).map_err(|e| named(fds.0, e))?;
    let write = std::fs::OpenOptions::new().write(true).open(process_fd_path(fds.1)).map_err(|e| named(fds.1, e))?;
    Ok((read, Channel::new(write)))
}

/// The compaction policy a `context` block with `compact: true` resolves
/// to, with the window taken from the block or from the provider table for
/// the model named. `None` when the contract never compacts. An unknown
/// model with no `window_tokens` is a construction error.
pub fn context_policy(contract: &ResolvedContract) -> Result<Option<foe_context::Policy>, String> {
    let Some(cfg) = contract.context.clone().filter(|c| c.compact) else { return Ok(None) };
    let model = contract.model.as_ref();
    let window = cfg.window_tokens.or_else(|| model.and_then(known_window)).ok_or_else(|| {
        let named = model.map_or("the host's model".to_string(), |m| format!("{}/{}", m.provider, m.model));
        format!(
            "config: context.window_tokens: is required because {named} is not in the provider table; set it to \
             the model's context window in tokens"
        )
    })?;
    let max_output = model.and_then(|m| m.max_output_tokens).map_or(0, u64::from);
    Ok(Some(foe_context::Policy::new(cfg, window, max_output, contract.done_when.as_ref())))
}

fn known_window(model: &ModelConfig) -> Option<u64> {
    foe_transport::context_window(model)
}

/// The file a parent process writes beside a child's log, naming the child.
pub(crate) const CHILD_LAUNCH: &str = "child-launch.json";

/// Who this episode is. A child reads the launch metadata its parent wrote;
/// a root draws a fresh id.
fn read_child_launch(log_dir: Option<&Path>) -> Result<ChildLaunch, String> {
    let file = log_dir.map(|d| d.join(CHILD_LAUNCH)).filter(|f| f.is_file());
    let Some(file) = file else { return Ok(ChildLaunch { episode_id: fresh_id(), ..ChildLaunch::default() }) };
    let bytes = std::fs::read(&file).map_err(|e| format!("{}: {e}", file.display()))?;
    serde_json::from_slice(&bytes).map_err(|e| format!("{}: {e}", file.display()))
}

fn fresh_id() -> String {
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
    let digest = Sha256::digest(format!("{now}:{}", std::process::id()));
    format!("ep_{}", hex::encode(&digest[..4]))
}

/// Where the episode's log lives, who the episode is, and one line about how
/// the directory was reached, which the run prints under the directory.
type Placement = (PathBuf, ChildLaunch, Option<String>);

/// Where the episode's log lives and who the episode is: the episode
/// `--from DIR` continues, an episode forked from a prefix of that source, or
/// a fresh directory. docs/design.md "The command line" holds the table of
/// what each `--from` value selects, and docs/log-format.md "Seeding" holds
/// the seeding rules a fork obeys.
fn episode_directory(options: &Options, fingerprint: &str, task: &Task) -> Result<Placement, String> {
    let Some(source) = options.from.as_deref() else {
        let launch = read_child_launch(options.log_dir.as_deref())?;
        return Ok((fresh_directory(options.log_dir.as_deref(), &launch.episode_id), launch, None));
    };
    let dest = options.log_dir.as_deref();
    if let Some(at) = options.at {
        return fork(source, at, dest, task.directive());
    }
    let (start, ended, events) = source_state(source)?;
    let (named, earlier) = (source.display(), format!("--from {}@SEQ", source.display()));
    match (ended, options.task.is_some()) {
        (false, false) => resume(source, fingerprint),
        (false, true) => Err(format!(
            "{named}: episode {} has not ended, and a continued episode keeps the task it started with; give \
             {earlier} to fork it with a new task",
            start.id
        )),
        (true, false) => Err(format!(
            "{named}: episode {} ended; give a task to continue from its whole conversation, or {earlier} to fork \
             it earlier",
            start.id
        )),
        (true, true) => fork(source, events, dest, task.directive()),
    }
}

/// The task this run carries, and where it came from. A task the source log
/// of `--from` recorded is what that episode already ran, so a fork of it
/// reruns the copied conversation rather than receiving the task again.
struct Task {
    text: String,
    recorded: bool,
}

impl Task {
    /// The task a fork appends as a live directive, which is every task
    /// except one the source log recorded.
    fn directive(&self) -> Option<&str> {
        (!self.recorded).then_some(self.text.as_str())
    }
}

/// What the source log of `--from` records: its `episode/start`, whether the
/// episode ended, and how many events it holds, which is the boundary a fork
/// from the end of the conversation takes.
fn source_state(source: &Path) -> Result<(EpisodeStart, bool, u64), String> {
    source_log(source)?;
    let in_source = |e: LogError| format!("{}: {e}", source.display());
    let events = foe_log::fold::read_all(source).map_err(in_source)?;
    let state = foe_log::fold::fold(&events).map_err(in_source)?;
    let start = state.start.ok_or_else(|| format!("{}: the log has no episode/start", source.display()))?;
    Ok((start, state.outcome.is_some(), events.len() as u64))
}

/// Refuses a `--from` value under which no log exists, naming the file: a
/// directory `--log-dir` names holds episode directories rather than a log,
/// so it is the value most likely to be given by mistake.
fn source_log(source: &Path) -> Result<(), String> {
    let log = source.join(foe_log::fold::LOG_FILE);
    if log.is_file() {
        return Ok(());
    }
    let (source, log) = (source.display(), log.display());
    Err(format!("--from {source}: {log} does not exist; --from takes one episode's own directory, which a run names as `foe: log PATH`"))
}

/// The task the source log recorded, which fills the place a built-in
/// document leaves empty when the command line names no task.
fn recorded_task(options: &Options) -> Result<Option<String>, String> {
    match options.from.as_deref().filter(|_| options.task.is_none()) {
        Some(source) => source_state(source).map(|(start, _, _)| Some(start.task)),
        None => Ok(None),
    }
}

/// The directory a fresh episode writes into: the episode id under the parent
/// directory, which is `--log-dir` when given and `.foe` otherwise. A
/// directory holding launch metadata is the episode's own directory instead,
/// because the parent process that wrote that file chose the directory and
/// the episode id together.
fn fresh_directory(log_dir: Option<&Path>, episode_id: &str) -> PathBuf {
    match log_dir {
        Some(dir) if dir.join(CHILD_LAUNCH).is_file() => dir.to_path_buf(),
        Some(parent) => parent.join(episode_id),
        None => PathBuf::from(".foe").join(episode_id),
    }
}

/// Seeds a fresh episode from a prefix of the log in `source` and appends a
/// `directive`, the task the command line gave, as a `system` inbox item: the
/// one `task` item per log is the copied one at seq 1, and `system` is the
/// runtime's channel for text the model must see. Without a directive the
/// fork reruns the copied conversation from the boundary.
fn fork(source: &Path, at: u64, dest: Option<&Path>, directive: Option<&str>) -> Result<Placement, String> {
    source_log(source)?;
    let launch = ChildLaunch { episode_id: fresh_id(), ..ChildLaunch::default() };
    let dest = fresh_directory(dest, &launch.episode_id);
    let in_dest = |e: LogError| format!("{}: {e}", dest.display());
    std::fs::create_dir_all(&dest).map_err(|e| format!("{}: {e}", dest.display()))?;
    let header = SeedHeader { new_id: launch.episode_id.clone(), parent_id: None, team_id: None, contract: None };
    foe_log::seed::seed(source, at, &dest, header).map_err(|e| format!("--from {}: {e}", source.display()))?;
    let mut writer = foe_log::append::Writer::open(&dest, None).map_err(in_dest)?;
    if let Some(task) = directive {
        let content = vec![ContentBlock::Text { text: task.to_string() }];
        let item = InboxItem::new(InboxSource::System, content, None, None);
        writer.append(EventData::InboxItem(item)).map_err(in_dest)?;
    }
    writer.sync().map_err(in_dest)?;
    Ok((dest, launch, Some(format!("fork of {} at seq {at}", source.display()))))
}

/// Continues the episode whose log is in `dir` under the same contract. A
/// log ending at an event boundary with every binding obligation closed is
/// continued in place; one cut short mid-line or with an obligation open
/// is seeded at its last clean boundary into a fresh directory beside it,
/// which the run then continues. A prepared seeded log, ending at
/// `seed/end`, is continued as it stands with no fingerprint comparison,
/// because a seeded `episode/start` records its source's contract.
fn resume(dir: &Path, contract_fingerprint: &str) -> Result<Placement, String> {
    let dir = dir.canonicalize().map_err(|e| format!("{}: {e}", dir.display()))?;
    let in_dir = |e: LogError| format!("{}: {e}", dir.display());
    let (events, consumed) = foe_log::fold::read_from(&dir, 0).map_err(in_dir)?;
    let state = foe_log::fold::fold(&events).map_err(in_dir)?;
    let start = state.start.ok_or_else(|| format!("{}: the log has no episode/start", dir.display()))?;
    if start.fork_origin.is_some() && state.seeded_through.is_none() {
        return Err(format!("{}: resuming a seeded log requires seed/end", dir.display()));
    }
    let spawned_start = start.parent_id.is_some() && start.effective_budget.is_some();
    let mut launch = read_child_launch(Some(&dir))?;
    launch.effective_budget = start.effective_budget.clone().or(launch.effective_budget);
    if spawned_start {
        launch.expected_contract_fingerprint = Some(start.contract_fingerprint.clone());
    }
    launch.episode_id = start.id.clone();
    launch.parent_id = start.parent_id.clone();
    launch.team_id = start.team_id.clone();
    let torn = std::fs::metadata(dir.join(foe_log::fold::LOG_FILE)).is_ok_and(|m| m.len() > consumed);
    let prepared = !torn && events.last().is_some_and(|e| matches!(e.data, EventData::SeedEnd {}));
    if start.contract_fingerprint != contract_fingerprint && (!prepared || spawned_start) {
        let (dir, recorded) = (dir.display(), &start.contract_fingerprint);
        return Err(format!("{dir}: resuming requires the contract that ran: the log records fingerprint {recorded}; the given contract document resolves to {contract_fingerprint}"));
    }
    let unanswered = events
        .iter()
        .rev()
        .take_while(|event| !matches!(event.data, EventData::SeedEnd {}))
        .filter_map(|event| match &event.data {
            EventData::ToolResult(result) if result.name == "ask" && !result.is_error && !result.synthetic => {
                result.value["message_id"].as_str()
            }
            _ => None,
        })
        .find(|id| {
            !state.inbox.values().any(|(item, _)| {
                item.source == foe_log::InboxSource::Response && item.message_id.as_deref() == Some(id)
            })
        });
    if let Some(id) = unanswered {
        return Err(format!("{}: cannot continue with unanswered ask {id}; use --from DIR@SEQ to start a separate episode from an explicit prefix", dir.display()));
    }
    if prepared || (!torn && foe_log::fold::open_obligations(&events).is_empty()) {
        let note = format!("continues episode {} in place", launch.episode_id);
        return Ok((dir, launch, Some(note)));
    }
    let new_id = fresh_id();
    let dest = dir.parent().unwrap_or(Path::new(".")).join(&new_id);
    std::fs::create_dir_all(&dest).map_err(|e| format!("{}: {e}", dest.display()))?;
    let header = SeedHeader {
        new_id: new_id.clone(),
        parent_id: launch.parent_id.clone(),
        team_id: launch.team_id.clone(),
        contract: None,
    };
    foe_log::seed::seed(&dir, events.len() as u64, &dest, header).map_err(in_dir)?;
    let note = format!(
        "continues episode {}, which stopped mid-line or mid-obligation, as episode {new_id}",
        launch.episode_id
    );
    Ok((dest, ChildLaunch { episode_id: new_id, ..launch }, Some(note)))
}

/// The name of the coding workflow the binary carries.
pub(crate) const BUILTIN_CODING: &str = "coding";

/// The name of the one implementation episode the binary carries, run alone.
pub(crate) const BUILTIN_ONESHOT: &str = "oneshot";

/// The name of the document that delegates to a team of workers.
pub(crate) const BUILTIN_TEAM: &str = "team";

/// Every document the binary carries, each selected as `builtin:NAME`.
pub(crate) const BUILTIN_DOCUMENTS: &[&str] = &[BUILTIN_CODING, BUILTIN_ONESHOT, BUILTIN_TEAM];

/// What marks a `--config` value as the name of a document the binary
/// carries rather than a file path.
pub(crate) const BUILTIN_PREFIX: &str = "builtin:";

/// The document a run reads when the command line names none, relative to
/// the working directory. No ancestor directory is examined. Any entry at
/// the path selects it, a dangling symbolic link or a directory included,
/// so a broken file is reported and never replaced by the built-in
/// workflow in silence.
pub(crate) const REPOSITORY_CONTRACT: &str = ".foe/contract.json";

/// The document a run or a plan resolves.
#[derive(Debug)]
pub(crate) enum ContractSource {
    /// A document the binary carries, by its name after `builtin:`.
    Builtin(&'static str),
    /// A document in a file.
    File(PathBuf),
}

impl ContractSource {
    /// What a message calls the source: the `--config` value naming it.
    pub(crate) fn describe(&self) -> String {
        match self {
            Self::Builtin(name) => format!("builtin:{name}"),
            Self::File(path) => path.display().to_string(),
        }
    }
}

/// Reads a `--config` value. A `builtin:` prefix names a document the binary
/// carries, and every other value is a file path.
pub(crate) fn contract_source(value: &str) -> Result<ContractSource, String> {
    let Some(name) = value.strip_prefix(BUILTIN_PREFIX) else { return Ok(ContractSource::File(value.into())) };
    match BUILTIN_DOCUMENTS.iter().find(|carried| **carried == name) {
        Some(name) => Ok(ContractSource::Builtin(name)),
        None => Err(format!(
            "--config builtin:{name}: no built-in document has that name; the built-in documents are {}",
            BUILTIN_DOCUMENTS.iter().map(|carried| format!("builtin:{carried}")).collect::<Vec<_>>().join(", ")
        )),
    }
}

/// The contract document to run: the document `--config` names, else the
/// repository document in the working directory, else the built-in coding
/// workflow. A task on the command line replaces the document's own. A
/// built-in document carries no task, and under `--from` the task the source
/// log recorded fills that place. Reading the repository document is
/// announced on standard error, because the command line did not name it. A
/// document in a file that declares no `model` block takes one from the
/// model options, which a document declaring a block refuses.
fn load_contract_document(options: &Options) -> Result<(ContractDocument, bool), String> {
    let discovered = options.config.is_none() && Path::new(REPOSITORY_CONTRACT).symlink_metadata().is_ok();
    let source = match &options.config {
        Some(value) => contract_source(value)?,
        None if discovered => ContractSource::File(REPOSITORY_CONTRACT.into()),
        None => ContractSource::Builtin(BUILTIN_CODING),
    };
    let path = match source {
        ContractSource::Builtin(name) => {
            let recorded = recorded_task(options)?;
            let from_log = recorded.is_some();
            let task = options.task.clone().or(recorded).ok_or(match options.config.is_some() {
                true => USAGE_BUILTIN,
                false => USAGE_BARE,
            })?;
            let model = command_line_model(options)?;
            let sandbox = options.permit_everything.then_some("off").or(options.sandbox.as_deref());
            let mut document = builtin_contract_document(name, task, Some(model), options.verify.as_deref(), sandbox)?;
            // Everything the kernel would have held back, and the grants with
            // it. The episode still records what it was given, so a run made
            // this way is as readable afterwards as any other.
            if options.permit_everything {
                let whole = || vec![PathBuf::from("/")];
                (document.grants.read, document.grants.write, document.grants.execute) = (whole(), whole(), whole());
            }
            return Ok((document, from_log));
        }
        ContractSource::File(path) => path,
    };
    if options.verify.is_some() || options.sandbox.is_some() || options.permit_everything {
        let option = match (&options.verify, &options.sandbox) {
            (Some(_), _) => "--verify",
            (_, Some(_)) => "--sandbox",
            _ => "--dangerously-permit-everything-no-sandbox",
        };
        return Err(format!("{option} applies to a built-in document; {} declares its own behavior", path.display()));
    }
    let text = std::fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
    let mut config = foe_contract::document::parse(&text).map_err(|e| format!("{}: {e}", path.display()))?;
    if discovered {
        eprintln!("foe: using {REPOSITORY_CONTRACT}, workflow {}", config.name);
    }
    if let Some(task) = &options.task {
        config.task = task.clone();
    }
    if let Some(option) = model_option_given(options) {
        if config.model.is_some() {
            return Err(format!("{option}: the contract document declares its own `model` block"));
        }
        config.model = Some(command_line_model(options)?);
    }
    Ok((config, false))
}

/// The first model option the command line names, when it names one. The
/// two describe one `model` block between them, so one name is enough to
/// report which of them the document refuses.
fn model_option_given(options: &Options) -> Option<&'static str> {
    options.model.is_some().then_some("--model").or(options.service_tier.is_some().then_some("--service-tier"))
}

/// The `model` block the command line describes: `--model PROVIDER/MODEL`
/// or the default model `foe login` wrote, carrying `--service-tier` when
/// given. The provider table holds the accepted tier values, so resolution
/// rejects a value the provider does not accept and names the provider.
fn command_line_model(options: &Options) -> Result<ModelConfig, String> {
    let mut model = match &options.model {
        Some(spec) => {
            let (provider, model) =
                spec.split_once('/').ok_or("--model takes PROVIDER/MODEL, for example anthropic/claude-opus-5")?;
            ModelConfig::new(provider, model)
        }
        None => default_model()?.ok_or(NO_DEFAULT_MODEL)?,
    };
    if let Some(tier) = &options.service_tier {
        model.options.insert("service_tier".into(), tier.clone());
    }
    Ok(model)
}

/// Applies implementation model settings measured for the built-in coding
/// workflow. A value in the default model file remains authoritative here.
pub(crate) fn apply_builtin_model_defaults(model: &mut ModelConfig) {
    if matches!(model.provider.as_str(), "openai" | "openai-codex") && model.model == "gpt-5.6-sol" {
        model.options.entry("reasoning_effort".into()).or_insert_with(|| "low".into());
    }
}

const USAGE_BARE: &str = "a task or --config FILE is required";
const USAGE_BUILTIN: &str = "a task is required: a built-in document takes the task from the command line";
const NO_DEFAULT_MODEL: &str =
    "no model: run `foe login <provider>` once to set a default, or give --model PROVIDER/MODEL";

pub(crate) fn default_model() -> Result<Option<ModelConfig>, String> {
    foe_transport::auth::login::default_model()
}

/// What the model is told about the `--verify` executable, which it may
/// also run as an ordinary tool. The authoritative contract is the
/// verifier contract of docs/config.md `done_when`.
const BUILTIN_VERIFIER_DESCRIPTION: &str = "The task's verifier. It runs in the working directory, prints one \
finding per line, and exits 0 whether or not it found any; printing nothing is acceptance. An ordinary call takes \
{\"args\": []}; the authoritative run after completion receives the completion value as JSON on standard input.";

/// The document the binary carries under `name`, over the working directory.
/// Every name in `BUILTIN_DOCUMENTS` has an arm here, and `contract_source`
/// admits no other. A model block reads the credential file its own options
/// name, and the provider's convention path otherwise. `verify` names an
/// executable verifier: it becomes a `tool_defs` entry named `check` available to every
/// episode. In the coding workflow the root completion gate applies to both
/// the assessment's accept branch and the repair branch, and without a
/// verifier the assessment's typed choice governs completion. In the one-shot
/// document the gate applies to the one episode it runs.
pub(crate) fn builtin_contract_document(
    name: &str,
    task: String,
    model: Option<ModelConfig>,
    verify: Option<&Path>,
    sandbox: Option<&str>,
) -> Result<ContractDocument, String> {
    let cwd = std::env::current_dir().and_then(|d| d.canonicalize()).map_err(|e| format!("current directory: {e}"))?;
    match name {
        BUILTIN_CODING => coding_contract_document(&cwd, task, model, verify, sandbox),
        BUILTIN_ONESHOT => oneshot_contract_document(&cwd, task, model, verify, sandbox),
        BUILTIN_TEAM => team_contract_document(&cwd, task, model, verify, sandbox),
        other => Err(format!("builtin:{other}: no built-in document has that name")),
    }
}

/// The task `foe plan` gives a built-in document, whose own `task` key is
/// required. A run replaces it with the task on its command line.
const BUILTIN_PLAN_TASK: &str = "Placeholder task. A run of a built-in document takes its task from the command line.";

/// A built-in document as `foe plan` resolves it: over the working directory,
/// under the default model `foe login` recorded, and without the verifier and
/// sandbox mode that only a run selects. The resolved contract carries no
/// task, so the placeholder text reaches neither a model nor a fingerprint.
pub(crate) fn builtin_plan_document(name: &str) -> Result<ContractDocument, String> {
    builtin_contract_document(name, BUILTIN_PLAN_TASK.into(), default_model()?, None, None)
}

/// The same coding workflow over an explicit root directory, which becomes
/// every episode's read, write, and working root. `foe init` reuses this
/// with the repository as the root. Without a `model` the document omits
/// the block, so a host must answer its model requests.
pub(crate) fn coding_contract_document(
    root: &Path,
    task: String,
    mut model: Option<ModelConfig>,
    verify: Option<&Path>,
    sandbox: Option<&str>,
) -> Result<ContractDocument, String> {
    let explicit_reasoning = model.as_ref().is_some_and(|m| m.option("reasoning_effort").is_some());
    if let Some(model) = &mut model {
        apply_builtin_model_defaults(model);
    }
    let mut assessment_model = model.clone();
    if let Some(assessment) = &mut assessment_model {
        if !explicit_reasoning
            && matches!(assessment.provider.as_str(), "openai" | "openai-codex")
            && assessment.model == "gpt-5.6-sol"
        {
            assessment.options.insert("reasoning_effort".into(), "xhigh".into());
        }
    }
    let repair_model = assessment_model.clone();
    let environment = builtin_environment(root, Path::is_file);
    let grants = serde_json::json!({
        "read": [root], "write": [root], "execute": BUILTIN_EXECUTE_ROOTS
    });
    let mut document: serde_json::Value =
        serde_json::from_str(BUILTIN_CONTRACT_DOCUMENT).map_err(|e| format!("built-in contract template: {e}"))?;
    document["version"] = serde_json::json!(foe_contract::document::CONTRACT_FORMAT_VERSION);
    document["model"] = serde_json::json!(model);
    document["grants"] = grants.clone();
    document["budget"]["model_calls"] =
        serde_json::json!(BUILTIN_IMPLEMENTATION_CALLS + BUILTIN_ASSESSMENT_CALLS + BUILTIN_REPAIR_CALLS);
    document["task"] = serde_json::json!(task);
    for (node, calls) in [
        ("implement-task", BUILTIN_IMPLEMENTATION_CALLS),
        ("assess-task", BUILTIN_ASSESSMENT_CALLS),
        ("repair-task", BUILTIN_REPAIR_CALLS),
    ] {
        let contract = &mut document["workflow"]["nodes"][node]["model"];
        contract["instructions"]["environment"] = serde_json::json!(environment);
        contract["grants"] = grants.clone();
        contract["budget"]["model_calls"] = serde_json::json!(calls);
    }
    document["workflow"]["nodes"]["assess-task"]["model"]["model"] = serde_json::json!(assessment_model);
    document["workflow"]["nodes"]["repair-task"]["model"]["model"] = serde_json::json!(repair_model);
    if let Some(mode) = sandbox {
        document["sandbox"] = sandbox_block(mode)?;
    }
    if let Some(check) = verify {
        let def = verifier_def(check, root)?;
        document["budget"]["max_episodes"] = serde_json::json!(BUILTIN_VERIFIER_RETRIES + 4);
        add_verifier(&mut document, &def);
        for node in ["implement-task", "assess-task", "repair-task"] {
            add_verifier(&mut document["workflow"]["nodes"][node]["model"], &def);
        }
        for node in ["assess-task", "repair-task"] {
            document["workflow"]["nodes"][node]["max_fires"] = serde_json::json!(BUILTIN_VERIFIER_RETRIES + 1);
        }
        document["done_when"] = serde_json::json!({ "verify": "check", "retries": BUILTIN_VERIFIER_RETRIES });
        return serde_json::from_value(document).map_err(|e| format!("built-in contract document: {e}"));
    }
    serde_json::from_value(document).map_err(|e| format!("built-in contract document: {e}"))
}

/// The `tool_defs` entry `--verify PATH` adds to a built-in document under
/// the name `check`, running in `root`. The path is canonicalized so that
/// the document names the executable a run captures.
fn verifier_def(check: &Path, root: &Path) -> Result<serde_json::Value, String> {
    let check = check.canonicalize().map_err(|e| format!("--verify {}: {e}", check.display()))?;
    Ok(serde_json::json!({ "exec": check, "description": BUILTIN_VERIFIER_DESCRIPTION, "cwd": root }))
}

/// Gives one contract of a built-in document the verifier as a tool named
/// `check`, appended to its tool list and defined in its `tool_defs`.
fn add_verifier(contract: &mut serde_json::Value, def: &serde_json::Value) {
    contract["tools"].as_array_mut().expect("a tool list").push(serde_json::json!("check"));
    contract["tool_defs"] = serde_json::json!({ "check": def });
}

/// Model calls a worker of the team document may spend on its own unit, and
/// how many workers run at once under the lead and under one worker. A
/// worker does one unit of the work and reports it, which costs less than
/// the whole task and more than a survey; the lead surveys first, writes the
/// shared surfaces, delegates, and integrates.
const BUILTIN_WORKER_CALLS: u64 = 40;
const BUILTIN_TEAM_WORKERS: u32 = 6;
const BUILTIN_SUB_WORKERS: u32 = 3;

/// A worker that divides its unit spends its own calls and its sub-workers'
/// over two rounds, and its subtree holds itself and those sub-workers.
const DIVIDING_CALLS: u64 = BUILTIN_WORKER_CALLS * (1 + BUILTIN_SUB_WORKERS as u64 * 2);
const DIVIDING_EPISODES: u32 = BUILTIN_SUB_WORKERS * 2 + 1;

/// The team document: a lead that divides the task into units, gives each
/// worker the paths that unit writes, and integrates what they return. A
/// worker may write, and only where its lead named: the declared grant is
/// the ceiling and every spawn states the actual roots, which the kernel
/// then holds. Two workers cannot touch one file, so there is no merge and
/// no lock, and what remains is the lead's job — write the shared surfaces
/// first, then partition what is left.
///
/// `verify` gates the lead and every worker, so a unit that broke its own
/// ground does not reach the integration.
///
/// A worker is not a coding workflow. Breadth and verification are separate
/// questions: this document answers "cover this", and `builtin:coding`
/// answers "change this and check it".
pub(crate) fn team_contract_document(
    root: &Path,
    task: String,
    mut model: Option<ModelConfig>,
    verify: Option<&Path>,
    sandbox: Option<&str>,
) -> Result<ContractDocument, String> {
    if let Some(model) = &mut model {
        apply_builtin_model_defaults(model);
    }
    let mut document: serde_json::Value =
        serde_json::from_str(BUILTIN_TEAM_DOCUMENT).map_err(|e| format!("built-in team document: {e}"))?;
    let environment = builtin_environment(root, Path::is_file);
    document["instructions"]["environment"] = serde_json::json!(environment);
    document["model"] = serde_json::json!(model);
    document["grants"] = serde_json::json!({
        "read": [root], "write": [root], "execute": BUILTIN_EXECUTE_ROOTS, "spawn": ["worker", "surveyor"]
    });
    // Both kinds read everything the lead reads and run the same commands.
    // A worker's write grant is the ceiling a spawn narrows, never what one
    // worker gets: the tool states the roots on every call. A surveyor
    // declares no write tool and is granted no write root, which is what
    // makes it the kind for a unit that answers rather than changes.
    for (child, write) in [("worker", vec![root]), ("surveyor", vec![])] {
        let entry = &mut document["child_contracts"][child];
        entry["instructions"]["environment"] = serde_json::json!(environment);
        entry["grants"] = serde_json::json!({ "read": [root], "write": write, "execute": BUILTIN_EXECUTE_ROOTS });
        entry["budget"] = serde_json::json!({ "model_calls": BUILTIN_WORKER_CALLS });
    }
    // A unit can divide again, so the worker the lead spawns holds the same
    // two kinds it does. `child_contracts` is a tree in the document and
    // cannot name itself, so the level that divides is built here from the
    // level that does not: the same contract with the delegating tools, the
    // spawn grant, and those two kinds under it. The kinds under it hold
    // neither, which is what ends the tree.
    let leaves = document["child_contracts"].clone();
    let worker = &mut document["child_contracts"]["worker"];
    worker["tools"] =
        serde_json::json!(["read", "grep", "edit", "bash", "spawn", "wait", "cancel", "send", "ask", "notify", "team"]);
    worker["grants"]["spawn"] = serde_json::json!(["worker", "surveyor"]);
    worker["child_contracts"] = leaves;
    worker["budget"] = serde_json::json!({
        "model_calls": DIVIDING_CALLS,
        "max_episodes": DIVIDING_EPISODES,
        "max_concurrent": BUILTIN_SUB_WORKERS,
    });
    // The lifetime count is the lead plus the subtree of every worker it may
    // open, which is twice what may run at once, so a second round is
    // affordable at each level.
    document["budget"] = serde_json::json!({
        "model_calls": BUILTIN_IMPLEMENTATION_CALLS
            + DIVIDING_CALLS * u64::from(BUILTIN_TEAM_WORKERS) * 2,
        "max_episodes": BUILTIN_TEAM_WORKERS * 2 * DIVIDING_EPISODES + 1,
        "max_concurrent": BUILTIN_TEAM_WORKERS,
        "max_depth": 2,
    });
    document["task"] = serde_json::json!(task);
    if let Some(mode) = sandbox {
        document["sandbox"] = sandbox_block(mode)?;
    }
    if let Some(check) = verify {
        let def = verifier_def(check, root)?;
        add_verifier(&mut document, &def);
        document["done_when"]["verify"] = serde_json::json!("check");
        document["done_when"]["retries"] = serde_json::json!(BUILTIN_VERIFIER_RETRIES);
        for child in ["worker", "surveyor"] {
            add_verifier(&mut document["child_contracts"][child], &def);
            let entry = &mut document["child_contracts"][child]["done_when"];
            entry["verify"] = serde_json::json!("check");
            entry["retries"] = serde_json::json!(BUILTIN_VERIFIER_RETRIES);
        }
    }
    serde_json::from_value(document).map_err(|e| format!("built-in team document: {e}"))
}

/// The `sandbox` block `--sandbox MODE` states. The three modes are the ones
/// docs/config.md `sandbox` defines, and any other value is refused before a
/// document is built.
fn sandbox_block(mode: &str) -> Result<serde_json::Value, String> {
    match mode {
        "best-effort" | "required" | "off" => Ok(serde_json::json!({ "mode": mode })),
        other => Err(format!("--sandbox {other}: expected best-effort, required, or off")),
    }
}

/// One implementation episode over `root` and no assessment: the coding
/// workflow's implementation node lifted out of the same embedded document
/// and made the whole contract, so the instructions, tools, and return
/// schema of the two forms cannot differ. The document declares no workflow,
/// and the resulting contract fingerprints apart from the coding workflow.
/// `verify` gates that one episode, with the retry allowance the coding
/// workflow receives; a finding re-fires inside the episode, so the lifetime
/// episode count stays at one.
pub(crate) fn oneshot_contract_document(
    root: &Path,
    task: String,
    mut model: Option<ModelConfig>,
    verify: Option<&Path>,
    sandbox: Option<&str>,
) -> Result<ContractDocument, String> {
    if let Some(model) = &mut model {
        apply_builtin_model_defaults(model);
    }
    let template: serde_json::Value =
        serde_json::from_str(BUILTIN_CONTRACT_DOCUMENT).map_err(|e| format!("built-in contract template: {e}"))?;
    let mut document = template["workflow"]["nodes"]["implement-task"]["model"].clone();
    document["version"] = serde_json::json!(foe_contract::document::CONTRACT_FORMAT_VERSION);
    document["name"] = serde_json::json!(BUILTIN_ONESHOT);
    document["instructions"]["environment"] = serde_json::json!(builtin_environment(root, Path::is_file));
    document["model"] = serde_json::json!(model);
    document["grants"] = serde_json::json!({ "read": [root], "write": [root], "execute": BUILTIN_EXECUTE_ROOTS });
    document["budget"] =
        serde_json::json!({ "model_calls": BUILTIN_IMPLEMENTATION_CALLS, "max_episodes": 1, "max_concurrent": 1 });
    document["task"] = serde_json::json!(task);
    if let Some(mode) = sandbox {
        document["sandbox"] = sandbox_block(mode)?;
    }
    if let Some(check) = verify {
        add_verifier(&mut document, &verifier_def(check, root)?);
        document["done_when"]["verify"] = serde_json::json!("check");
        document["done_when"]["retries"] = serde_json::json!(BUILTIN_VERIFIER_RETRIES);
    }
    serde_json::from_value(document).map_err(|e| format!("built-in contract document: {e}"))
}

#[cfg(test)]
#[path = "run_test.rs"]
mod tests;

/// One line naming the endpoint a `model` block resolves to, for `foe plan`.
pub fn describe_model_endpoint(contract: &ResolvedContract) -> String {
    let Some(model) = &contract.model else { return "no model".into() };
    foe_transport::plan(model).map(|plan| plan.describe()).unwrap_or_else(|e| e.to_string())
}

/// Fills provider defaults before contract construction, including the
/// credential path that `episode/start.contract` records.
fn prepare_model(config: &mut ContractDocument) -> Result<(), String> {
    if let Some(model) = &mut config.model {
        *model = foe_transport::plan(model).map_err(|e| e.to_string())?.model;
    }
    Ok(())
}

/// Resolves the constructed `model` block into a client before the process
/// restricts itself. The credential path joins the sandbox policy as a
/// readable file.
fn built_in_transport(model: &ModelConfig) -> Result<Arc<dyn Transport>, String> {
    foe_transport::build(model).map_err(|e| e.to_string())
}

pub fn run(options: Options) -> Result<ExitCode, String> {
    let channel = options.protocol_fds.map(protocol_channel).transpose()?;
    let (mut config, recorded) = load_contract_document(&options)?;
    prepare_model(&mut config)?;
    let task = Task { text: config.task.clone(), recorded };
    let inherited = options
        .log_dir
        .as_deref()
        .filter(|dir| dir.join(CHILD_LAUNCH).is_file())
        .map(|dir| read_child_launch(Some(dir)))
        .transpose()?
        .filter(|launch| launch.parent_id.is_some())
        .map(|launch| InheritedExecutables::read(&launch.episode_id))
        .transpose()?
        .flatten();
    let mut contract = match &inherited {
        Some(executables) => resolve_with_executables(&config, &executables.captured_bytes()),
        None => resolve(&config),
    }
    .map_err(|e| format!("config: {e}"))?;
    let fingerprint = fingerprint(&contract)?;
    let (log_dir, launch, note) = episode_directory(&options, &fingerprint.hash, &task)?;
    let task = task.text;
    // A subordinate episode takes the task of its run from the document its
    // parent wrote. A task on the command line, and a built-in document that
    // carries none of its own, both state a run the caller meant to own.
    let own_task = options.task.is_some() || options.config.as_deref().is_some_and(|c| c.starts_with(BUILTIN_PREFIX));
    if own_task && launch.parent_id.is_some() {
        return Err("an episode a parent launched takes its task from the document that parent wrote".into());
    }
    if let Some(expected) = &launch.expected_contract_fingerprint {
        if expected != &fingerprint.hash {
            return Err(format!(
                "child-launch.json: expected contract fingerprint {expected}, but the child document resolves to {}",
                fingerprint.hash
            ));
        }
    }
    // The parent may grant narrower write roots than the contract declares,
    // which is how two children of one contract take disjoint parts of a
    // tree. It happens after the fingerprint check and before confinement:
    // paths are excluded from a fingerprint, and the policy below is built
    // from what stands here.
    if let Some(roots) = launch.effective_write.clone() {
        contract.grants.write = foe_core::spawn::write_roots(&contract, Some(&roots), &extra_builtin_specs())
            .map_err(|e| format!("child-launch.json effective_write: {e}"))?;
    }
    let limits = launch.effective_budget.clone().unwrap_or_else(|| contract.budget.clone());
    std::fs::create_dir_all(&log_dir).map_err(|e| format!("{}: {e}", log_dir.display()))?;
    // Only a run a person launched names its directory. A spawned child's
    // standard error is relayed to that person's terminal a line at a time,
    // where it lands wherever the cursor happens to be: on the progress line
    // of an opt-in conversation, which is redrawn in place and is not the
    // display's to erase once someone else has written past it. The child's
    // directory is under the parent's and the parent already named that.
    if launch.parent_id.is_none() {
        announce_log_directory(&log_dir);
    }
    if let Some(note) = note {
        eprintln!("foe: {note}");
    }
    let executables = match &inherited {
        Some(inherited) => CapturedExecutableTree::from_inherited(&contract, inherited),
        None => CapturedExecutableTree::materialize(&contract, &log_dir),
    }
    .map_err(|e| format!("configured executable: {e}"))?;
    if let Some(source) = launch.fork_source.as_ref().filter(|_| !log_dir.join(foe_log::fold::LOG_FILE).is_file()) {
        let at = launch.fork_at.ok_or("child-launch.json: fork_source requires fork_at")?;
        let header = SeedHeader {
            new_id: launch.episode_id.clone(),
            parent_id: launch.parent_id.clone(),
            team_id: launch.team_id.clone(),
            contract: Some(SeedContract {
                contract: contract.to_value(),
                contract_fingerprint: fingerprint.hash.clone(),
                effective_budget: limits.clone(),
            }),
        };
        foe_log::seed::seed(source, at, &log_dir, header)
            .map_err(|e| format!("child-launch.json fork_source {}: {e}", source.display()))?;
    }
    let log_dir = log_dir.canonicalize().map_err(|e| format!("{}: {e}", log_dir.display()))?;
    let sandbox = Arc::new(Sandbox::new(contract.sandbox.mode).map_err(|e| e.to_string())?);
    let process = ProcessOwnership::enter(contract.sandbox.mode, &launch.episode_id, launch.process_boundary.clone())
        .map_err(|e| e.to_string())?;
    let mut policy =
        Policy::for_episode(&contract, &executables, &log_dir).map_err(|e| format!("sandbox permissions: {e}"))?;
    add_builtin_runtime_access(&mut policy, &contract).map_err(|e| format!("sandbox permissions: {e}"))?;
    add_transport_runtime_access(&mut policy, &contract).map_err(|e| format!("sandbox permissions: {e}"))?;
    process.authorize(&mut policy, contract.grants.task_session).map_err(|e| e.to_string())?;
    let mut unconfined = Unconfined::new(sandbox, policy);
    // Telemetry is resolved before confinement: the capture directory must
    // be writable after the sandbox closes, so it is created now and
    // granted like any other write root. A broken enablement file warns
    // and disables rather than failing a run that would otherwise start.
    let telemetry = crate::telemetry::settings().unwrap_or_else(|warning| {
        eprintln!("telemetry: {warning}; telemetry is disabled for this run");
        None
    });
    if let Some(settings) = &telemetry {
        let dir = settings.capture.parent().unwrap_or(Path::new(".")).to_path_buf();
        std::fs::create_dir_all(&dir).map_err(|e| format!("{}: {e}", dir.display()))?;
        unconfined.policy_mut().add_write_root(dir, "telemetry capture directory");
    }
    let bind_viewer = || foe_view::Bound::bind(0).map_err(|e| e.to_string());
    let viewer = (options.viewer != Viewer::Off).then(bind_viewer).transpose()?;
    let viewer_url = viewer.as_ref().map(foe_view::Bound::url);
    if let Some(bound) = &viewer {
        unconfined.policy_mut().add_bind_port(bound.addr.port());
        if options.viewer == Viewer::Open {
            crate::open_browser(&bound.url());
        }
    }
    let context = context_policy(&contract)?.map(|p| Arc::new(p) as Arc<dyn ContextPolicy>);
    let runtime_info = runtime_info();
    let transport = match &contract.model {
        Some(model) => Some(built_in_transport(model)?),
        None if channel.is_some() => None,
        None => {
            return Err("no model: give --model, add a `model` block, or run under a host with --protocol-fds".into())
        }
    };
    let confined = unconfined.enter().map_err(|e| e.to_string())?;
    let start = EpisodeStart {
        id: launch.episode_id,
        parent_id: launch.parent_id,
        fork_origin: None,
        team_id: launch.team_id,
        contract: contract.to_value(),
        contract_fingerprint: fingerprint.hash,
        task,
        runtime: runtime_info,
        sandbox: confined.parts().0.info(process.info(), confined.parts().1),
        effective_budget: Some(limits.clone()),
    };
    let telemetry_log_dir = log_dir.clone();
    let setup =
        Setup { contract, executables, limits, log_dir, confined, viewer, transport, context, start, channel, process };
    let executor = runtime()?;
    let outcome = executor.block_on(async {
        let outcome = match options.conversation {
            true => foe_view::conversation(&telemetry_log_dir, episode(setup)).await,
            false => episode(setup).await,
        };
        // The viewer stays reachable after the display has written the
        // final block, so that an open page receives the final events.
        if viewer_url.is_some() && outcome.is_ok() {
            tokio::time::sleep(VIEWER_GRACE).await;
        }
        outcome
    });
    // Episode cleanup finishes before shutdown. An idle standard-input read
    // must not prevent the command from reporting an outcome or recording error.
    executor.shutdown_background();
    let outcome = outcome?;
    if let Some(settings) = &telemetry {
        crate::telemetry::after_run(settings, &telemetry_log_dir);
    }
    if !options.conversation {
        println!("{}", serde_json::to_string(&outcome).map_err(|e| e.to_string())?);
        // The live viewer leaves with the process; the command outlives it.
        eprintln!("foe: view the episode with foe view {}", telemetry_log_dir.display());
    }
    Ok(ExitCode::from(match outcome {
        Outcome::Completed { .. } => 0,
        Outcome::Failed { .. } => 1,
        Outcome::Blocked { .. } => 2,
        Outcome::Exhausted { .. } => 3,
    }))
}

/// The fixed prefix of the line a run writes on standard error to name the
/// directory it created for this episode's log. A caller reads the directory
/// from that line rather than reconstructing the episode id.
const LOG_DIRECTORY_LINE: &str = "foe: log ";

/// Names the created log directory, before the episode starts and before
/// anything the episode itself reports. A spawned child does not: see the
/// one call site.
fn announce_log_directory(dir: &Path) {
    eprintln!("{LOG_DIRECTORY_LINE}{}", dir.display());
}

/// What the episode needs from the work done before the process restricted
/// itself. It carries a [`Confined`] rather than a policy, so nothing
/// assembled here can widen what the kernel already holds.
struct Setup {
    contract: ResolvedContract,
    executables: CapturedExecutableTree,
    limits: Budget,
    log_dir: PathBuf,
    confined: Confined,
    viewer: Option<foe_view::Bound>,
    transport: Option<Arc<dyn Transport>>,
    context: Option<Arc<dyn ContextPolicy>>,
    start: EpisodeStart,
    /// The two halves of the host protocol channel, `None` without a host.
    channel: Option<(std::fs::File, Channel)>,
    process: ProcessOwnership,
}

async fn episode(setup: Setup) -> Result<Outcome, String> {
    let Setup { contract, executables, limits, log_dir, confined, viewer, transport, channel, context, start, process } =
        setup;
    let (answers, channel) = channel.unzip();
    let id = start.id.clone();
    let mirror = channel.clone().map(|c| Box::new(c) as Box<dyn std::io::Write + Send>);
    let log = Arc::new(Log::create_or_open(&log_dir, mirror).map_err(|e| format!("{}: {e}", log_dir.display()))?);
    let router = Arc::new(Router::new());
    let (protocol, stop) = Host::new(id.clone(), log.clone(), Some(router.clone()));
    let cancel = Arc::new(AtomicBool::new(false));
    let transport = transport.unwrap_or_else(|| protocol.transport());
    let pool = Arc::new(Mutex::new(Pool::new(limits.clone())));
    let team = Arc::new(Team::new(id.clone(), log.clone(), Arc::new(protocol.clone()), router.clone(), pool.clone()));
    let uplink = channel.clone().map_or(Arc::new(NoHostUplink) as Arc<dyn Uplink>, |c| Arc::new(c) as Arc<dyn Uplink>);
    let connections = ProcessConnections { uplink, router: router.clone(), observer: team.clone() };
    let spawner = ProcessSpawner::new(
        id,
        log_dir.clone(),
        contract.clone(),
        executables.clone(),
        limits,
        extra_builtin_specs(),
        connections,
    )
    .map_err(|e| format!("spawner: {e}"))?
    .with_boundary(process.boundary());
    let spawner: Arc<dyn Spawner> = Arc::new(BudgetedSpawner::new(Arc::new(spawner), log.clone(), pool.clone()));
    let (sandbox, policy) = confined.parts();
    let executor = LocalExecutor::new(sandbox.clone(), policy.clone(), log_dir.join("spill"), cancel.clone());
    let sessions = Arc::new(
        LocalSessions::new(
            sandbox.clone(),
            policy.clone(),
            log_dir.join("spill"),
            foe_code::SESSION_MAX_ALIVE,
            contract.grants.task_session,
        )
        .with_boundary(process.boundary()),
    );
    let host_tools = channel.as_ref().map_or_else(Vec::new, |_| protocol.tools(&contract));
    let parent = start.parent_id.is_some().then_some(&protocol);
    let mut builtins: Vec<Box<dyn Tool>> = foe_code::all();
    builtins.push(foe_core::retrieval::tool(log.clone()));
    builtins.extend(team::tools(team.clone(), parent));
    let registry = Registry::new(&contract, &executables, host_tools, builtins).map_err(|e| format!("config: {e}"))?;
    let reader = RootReader::new(contract.grants.read.clone()).map_err(|e| format!("grants.read: {e}"))?;
    let writer =
        policy.bound_write.clone().filter(|writer| !writer.roots().is_empty()).map(|writer| writer as Arc<dyn Writer>);
    let handles = Handles {
        reader: Some(Arc::new(reader)),
        writer,
        executor: Some(Arc::new(executor)),
        spawner: (!contract.grants.spawn.is_empty()).then(|| spawner.clone()),
        sessions: Some(sessions.clone()),
    };
    if let Some(bound) = viewer {
        bound.serve(&log_dir).await.map_err(|e| e.to_string())?;
    }
    let workflow = contract.workflow.clone();
    let registry = Arc::new(registry);
    let children = Some(router.clone());
    {
        let (protocol, router, cancel, log) = (protocol.clone(), router.clone(), cancel.clone(), log.clone());
        tokio::spawn(async move {
            let reason = tokio::select! {
                _ = tokio::signal::ctrl_c() => "interrupted by SIGINT".to_string(),
                error = log.failed() => error.to_string(),
            };
            cancel.store(true, Ordering::SeqCst);
            router.cancel_all();
            protocol.stop(&reason);
        });
    }
    // A parent may have input queued already. Construction finishes before
    // the task takes seq 1, and the reader starts only after that append.
    loop_::initialize(&log, &start).map_err(|e| format!("{}: {e}", log_dir.display()))?;
    // A cleanly resumable log can end between recording a queued task and
    // assigning it. Scheduling from the folded board continues that work.
    if workflow.is_some() {
        log.with_events(foe_workflow::validate_resume).map_err(|e| e.to_string())?;
    }
    log.with_events(|events| loop_::lock(&pool).restore(events, foe_log::append::now_millis()));
    let _ = team.schedule(spawner.clone());
    if let Some(answers) = answers {
        protocol.spawn_reader(tokio::fs::File::from_std(answers));
    }
    let params = Params {
        log,
        start,
        contract,
        registry,
        handles,
        transport,
        pool,
        stop,
        children,
        sessions: Some(sessions),
        context,
    };
    let outcome = match workflow {
        Some(workflow) => foe_workflow::run(WorkflowParams { episode: params, spawner, workflow }).await,
        None => loop_::run(params).await,
    }
    .map_err(|e| e.to_string())?;
    Ok(outcome)
}
