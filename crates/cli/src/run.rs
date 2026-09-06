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
use foe_core::captured_executable::{CapturedExecutableTree, InheritedExecutables};
use foe_core::confine::{Confined, Unconfined};
use foe_core::context::ContextPolicy;
use foe_core::exec::LocalExecutor;
use foe_core::fingerprint::runtime_info;
use foe_core::grants::{RootReader, RootWriter};
use foe_core::loop_::{self, Log, Params};
use foe_core::process_boundary::ProcessOwnership;
use foe_core::protocol::{stdout_mirror, Host};
use foe_core::registry::{Handles, Registry};
use foe_core::sandbox::{Policy, Sandbox};
use foe_core::session::LocalSessions;
use foe_core::spawn::{ChildLaunch, ProcessConnections, ProcessSpawner, Router, Uplink};
use foe_core::wiring::{BudgetedSpawner, NoHostUplink, StdoutUplink};
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
    pub key_file: Option<PathBuf>,
    /// An executable verifier for the built-in coding workflow.
    pub verify: Option<PathBuf>,
    /// Kernel confinement mode for the built-in coding workflow.
    pub sandbox: Option<String>,
    pub log_dir: Option<PathBuf>,
    /// The source log directory of `--from DIR[@SEQ]`.
    pub from: Option<PathBuf>,
    /// The boundary `--from DIR@SEQ` names: source events with `seq` in
    /// `[1, at)` are copied. Absent when the value carried no boundary.
    pub at: Option<u64>,
    pub no_open: bool,
    pub headless: bool,
    pub conversation: bool,
    pub host: bool,
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
            policy.add_read_file(path, format!("credential for configured model endpoint in {contract_key}"));
        }
    }
    Ok(())
}

pub fn fingerprint(contract: &ResolvedContract) -> Result<Fingerprint, String> {
    compute(contract, &extra_builtin_specs(), &runtime_info()).map_err(|e| e.to_string())
}

pub fn runtime() -> Result<tokio::runtime::Runtime, String> {
    tokio::runtime::Builder::new_multi_thread().enable_all().build().map_err(|e| format!("runtime: {e}"))
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
        let item = InboxItem { source: InboxSource::System, content, from: None, message_id: None };
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

/// Every document the binary carries, each selected as `builtin:NAME`.
pub(crate) const BUILTIN_DOCUMENTS: &[&str] = &[BUILTIN_CODING];

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
            let document = builtin_contract_document(
                name,
                task,
                Some(model),
                options.key_file.as_deref(),
                options.verify.as_deref(),
                options.sandbox.as_deref(),
            )?;
            return Ok((document, from_log));
        }
        ContractSource::File(path) => path,
    };
    if options.verify.is_some() || options.sandbox.is_some() {
        let option = if options.verify.is_some() { "--verify" } else { "--sandbox" };
        return Err(format!(
            "{option} applies to the built-in coding workflow; {} declares its own behavior",
            path.display()
        ));
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
        let mut model = command_line_model(options)?;
        if let Some(key_file) = &options.key_file {
            name_credential_file(&mut model, key_file)?;
        }
        config.model = Some(model);
    }
    Ok((config, false))
}

/// The first model option the command line names, when it names one. The
/// three describe one `model` block between them, so one name is enough to
/// report which of them the document refuses.
fn model_option_given(options: &Options) -> Option<&'static str> {
    options
        .model
        .is_some()
        .then_some("--model")
        .or(options.key_file.is_some().then_some("--key-file"))
        .or(options.service_tier.is_some().then_some("--service-tier"))
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

/// Names the provider credential file the block reads, in place of the
/// convention path under `~/.config/foe/credentials/`.
fn name_credential_file(model: &mut ModelConfig, key_file: &Path) -> Result<(), String> {
    let key_file = key_file.canonicalize().map_err(|e| format!("--key-file {}: {e}", key_file.display()))?;
    let option = credential_option(&model.provider);
    model.options.insert(option.to_string(), key_file.to_string_lossy().into_owned());
    Ok(())
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
/// admits no other. `--key-file` names the API key file explicitly; without
/// it the provider's convention path is read. `verify` names an executable
/// verifier: it becomes a `tool_defs` entry named `check` available to every
/// episode. The root completion gate applies to both the assessment's accept
/// branch and the repair branch. Without a verifier, the assessment's typed
/// choice governs completion.
pub(crate) fn builtin_contract_document(
    name: &str,
    task: String,
    model: Option<ModelConfig>,
    key_file: Option<&Path>,
    verify: Option<&Path>,
    sandbox: Option<&str>,
) -> Result<ContractDocument, String> {
    let cwd = std::env::current_dir().and_then(|d| d.canonicalize()).map_err(|e| format!("current directory: {e}"))?;
    match name {
        BUILTIN_CODING => coding_contract_document(&cwd, task, model, key_file, verify, sandbox),
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
    builtin_contract_document(name, BUILTIN_PLAN_TASK.into(), default_model()?, None, None, None)
}

/// The same coding workflow over an explicit root directory, which becomes
/// every episode's read, write, and working root. `foe init` reuses this
/// with the repository as the root. Without a `model` the document omits
/// the block, so a host must answer its model requests.
pub(crate) fn coding_contract_document(
    root: &Path,
    task: String,
    mut model: Option<ModelConfig>,
    key_file: Option<&Path>,
    verify: Option<&Path>,
    sandbox: Option<&str>,
) -> Result<ContractDocument, String> {
    let explicit_reasoning = model.as_ref().is_some_and(|m| m.option("reasoning_effort").is_some());
    if let Some(model) = &mut model {
        apply_builtin_model_defaults(model);
        if let Some(key_file) = key_file {
            name_credential_file(model, key_file)?;
        }
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
        if !matches!(mode, "best-effort" | "required" | "off") {
            return Err(format!("--sandbox {mode}: expected best-effort, required, or off"));
        }
        document["sandbox"] = serde_json::json!({ "mode": mode });
    }
    if let Some(check) = verify {
        let check = check.canonicalize().map_err(|e| format!("--verify {}: {e}", check.display()))?;
        let def = serde_json::json!({ "exec": check, "description": BUILTIN_VERIFIER_DESCRIPTION, "cwd": root });
        document["budget"]["max_episodes"] = serde_json::json!(BUILTIN_VERIFIER_RETRIES + 4);
        document["tools"].as_array_mut().expect("a tool list").push(serde_json::json!("check"));
        document["tool_defs"] = serde_json::json!({ "check": def });
        for node in ["implement-task", "assess-task", "repair-task"] {
            let contract = &mut document["workflow"]["nodes"][node]["model"];
            contract["tools"].as_array_mut().expect("a tool list").push(serde_json::json!("check"));
            contract["tool_defs"] = serde_json::json!({ "check": def });
        }
        for node in ["assess-task", "repair-task"] {
            document["workflow"]["nodes"][node]["max_fires"] = serde_json::json!(BUILTIN_VERIFIER_RETRIES + 1);
        }
        document["done_when"] = serde_json::json!({ "verify": "check", "retries": BUILTIN_VERIFIER_RETRIES });
        return serde_json::from_value(document).map_err(|e| format!("built-in contract document: {e}"));
    }
    serde_json::from_value(document).map_err(|e| format!("built-in contract document: {e}"))
}

fn credential_option(provider: &str) -> &'static str {
    foe_transport::provider_info(provider).map(|value| value.auth.option_key()).unwrap_or("api_key_file")
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
    let contract = match &inherited {
        Some(executables) => resolve_with_executables(&config, &executables.captured_bytes()),
        None => resolve(&config),
    }
    .map_err(|e| format!("config: {e}"))?;
    let fingerprint = fingerprint(&contract)?;
    let (log_dir, launch, note) = episode_directory(&options, &fingerprint.hash, &task)?;
    let task = task.text;
    if let Some(expected) = &launch.expected_contract_fingerprint {
        if expected != &fingerprint.hash {
            return Err(format!(
                "child-launch.json: expected contract fingerprint {expected}, but the child document resolves to {}",
                fingerprint.hash
            ));
        }
    }
    let limits = launch.effective_budget.clone().unwrap_or_else(|| contract.budget.clone());
    std::fs::create_dir_all(&log_dir).map_err(|e| format!("{}: {e}", log_dir.display()))?;
    announce_log_directory(&log_dir);
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
    let viewer = match serves_viewer(&options) {
        true => Some(foe_view::Bound::bind(0).map_err(|e| e.to_string())?),
        false => None,
    };
    let viewer_url = viewer.as_ref().map(foe_view::Bound::url);
    if let Some(bound) = &viewer {
        unconfined.policy_mut().add_bind_port(bound.addr.port());
        if !options.no_open {
            crate::open_browser(&bound.url());
        }
    }
    let context = context_policy(&contract)?.map(|p| Arc::new(p) as Arc<dyn ContextPolicy>);
    let runtime_info = runtime_info();
    let transport = match &contract.model {
        Some(model) => Some(built_in_transport(model)?),
        None if options.host => None,
        None => return Err("no model: give --model and --key-file, add a `model` block, or run under --host".into()),
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
    let setup = Setup {
        contract,
        executables,
        limits,
        log_dir,
        confined,
        viewer,
        transport,
        context,
        start,
        host: options.host,
        process,
    };
    let executor = runtime()?;
    let outcome = executor.block_on(async {
        let outcome = match options.conversation {
            true => foe_view::conversation(&telemetry_log_dir, viewer_url.clone(), episode(setup)).await,
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
    if !options.host && !options.conversation {
        println!("{}", serde_json::to_string(&outcome).map_err(|e| e.to_string())?);
    }
    Ok(ExitCode::from(match outcome {
        Outcome::Completed { .. } => 0,
        Outcome::Failed { .. } => 1,
        Outcome::Blocked { .. } => 2,
        Outcome::Exhausted { .. } => 3,
    }))
}

/// Whether a run serves the browser viewer: every running form does except
/// under `--host`, whose standard output is the log, and `--headless`.
fn serves_viewer(options: &Options) -> bool {
    !(options.host || options.headless)
}

/// The fixed prefix of the line a run writes on standard error to name the
/// directory it created for this episode's log. A caller reads the directory
/// from that line rather than reconstructing the episode id.
const LOG_DIRECTORY_LINE: &str = "foe: log ";

/// Names the created log directory, before the episode starts and before
/// anything the episode itself reports.
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
    host: bool,
    process: ProcessOwnership,
}

async fn episode(setup: Setup) -> Result<Outcome, String> {
    let Setup { contract, executables, limits, log_dir, confined, viewer, transport, host, context, start, process } =
        setup;
    let id = start.id.clone();
    let log = Arc::new(
        Log::create_or_open(&log_dir, host.then(stdout_mirror)).map_err(|e| format!("{}: {e}", log_dir.display()))?,
    );
    let router = Arc::new(Router::new());
    let (protocol, stop) = Host::new(id.clone(), log.clone(), Some(router.clone()));
    let cancel = Arc::new(AtomicBool::new(false));
    let transport = transport.unwrap_or_else(|| protocol.transport());
    let pool = Arc::new(Mutex::new(Pool::new(limits.clone())));
    let team = Arc::new(Team::new(id.clone(), log.clone(), Arc::new(protocol.clone()), router.clone(), pool.clone()));
    let uplink: Arc<dyn Uplink> = if host { Arc::new(StdoutUplink) } else { Arc::new(NoHostUplink) };
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
    let host_tools = if host { protocol.tools(&contract) } else { Vec::new() };
    let parent = start.parent_id.is_some().then_some(&protocol);
    let mut builtins: Vec<Box<dyn Tool>> = foe_code::all();
    builtins.push(foe_core::retrieval::tool(log.clone()));
    builtins.extend(team::tools(team.clone(), parent));
    let registry = Registry::new(&contract, &executables, host_tools, builtins).map_err(|e| format!("config: {e}"))?;
    let write = contract.grants.write.clone();
    let reader = RootReader::new(contract.grants.read.clone()).map_err(|e| format!("grants.read: {e}"))?;
    let writer = match write.is_empty() {
        true => None,
        false => Some(Arc::new(RootWriter::new(write).map_err(|e| format!("grants.write: {e}"))?) as Arc<dyn Writer>),
    };
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
    if host {
        protocol.spawn_reader(tokio::io::stdin());
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
