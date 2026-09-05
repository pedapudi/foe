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

/// How long the viewer stays reachable after the episode ends, so that an
/// open page receives the final events.
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
    pub config: Option<PathBuf>,
    pub model: Option<String>,
    /// Provider service tier for the built-in coding workflow.
    pub service_tier: Option<String>,
    pub key_file: Option<PathBuf>,
    /// An executable verifier for the built-in coding workflow.
    pub verify: Option<PathBuf>,
    /// Kernel confinement mode for the built-in coding workflow.
    pub sandbox: Option<String>,
    pub log_dir: Option<PathBuf>,
    pub no_open: bool,
    pub headless: bool,
    pub host: bool,
    /// Source log directory to seed the new episode from, with the
    /// boundary: source events with `seq` in `[1, at)` are copied.
    pub fork: Option<PathBuf>,
    pub at: Option<u64>,
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

/// Who this episode is. A child reads the launch metadata its parent wrote;
/// a root draws a fresh id.
fn read_child_launch(log_dir: Option<&Path>) -> Result<ChildLaunch, String> {
    let file = log_dir.map(|d| d.join("child-launch.json")).filter(|f| f.is_file());
    let Some(file) = file else { return Ok(ChildLaunch { episode_id: fresh_id(), ..ChildLaunch::default() }) };
    let bytes = std::fs::read(&file).map_err(|e| format!("{}: {e}", file.display()))?;
    serde_json::from_slice(&bytes).map_err(|e| format!("{}: {e}", file.display()))
}

fn fresh_id() -> String {
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
    let digest = Sha256::digest(format!("{now}:{}", std::process::id()));
    format!("ep_{}", hex::encode(&digest[..4]))
}

/// Where the episode's log lives and who the episode is: a fresh directory
/// seeded from `--fork`, an existing log continued or repaired, or a new
/// log. docs/log-format.md "Seeding" states the fork and resume flows.
fn episode_directory(
    options: &Options,
    contract_fingerprint: &str,
    task: &str,
) -> Result<(PathBuf, ChildLaunch), String> {
    if let Some(source) = &options.fork {
        let at = options.at.expect("the parser pairs --fork with --at");
        return fork(source, at, options.log_dir.clone(), task);
    }
    if let Some(dir) = options.log_dir.as_deref().filter(|dir| dir.join(foe_log::fold::LOG_FILE).is_file()) {
        return resume(dir, contract_fingerprint);
    }
    let launch = read_child_launch(options.log_dir.as_deref())?;
    let dir = options.log_dir.clone().unwrap_or_else(|| PathBuf::from(".foe").join(&launch.episode_id));
    Ok((dir, launch))
}

/// Seeds a fresh episode from a prefix of the log in `source` and appends
/// the running form's task as a `system` inbox item: the one `task` item
/// per log is the copied one at seq 1, and `system` is the runtime's
/// channel for text the model must see.
fn fork(source: &Path, at: u64, dest: Option<PathBuf>, task: &str) -> Result<(PathBuf, ChildLaunch), String> {
    let launch = ChildLaunch { episode_id: fresh_id(), ..ChildLaunch::default() };
    let dest = dest.unwrap_or_else(|| PathBuf::from(".foe").join(&launch.episode_id));
    let in_dest = |e: LogError| format!("{}: {e}", dest.display());
    if dest.join(foe_log::fold::LOG_FILE).is_file() {
        return Err(format!("{} already holds a log; a fork starts a fresh one", dest.display()));
    }
    std::fs::create_dir_all(&dest).map_err(|e| format!("{}: {e}", dest.display()))?;
    let header = SeedHeader { new_id: launch.episode_id.clone(), parent_id: None, team_id: None, contract: None };
    foe_log::seed::seed(source, at, &dest, header).map_err(|e| format!("--fork {}: {e}", source.display()))?;
    let mut writer = foe_log::append::Writer::open(&dest, None).map_err(in_dest)?;
    let content = vec![ContentBlock::Text { text: task.to_string() }];
    let item = InboxItem { source: InboxSource::System, content, from: None, message_id: None };
    writer.append(EventData::InboxItem(item)).map_err(in_dest)?;
    writer.sync().map_err(in_dest)?;
    Ok((dest, launch))
}

/// Continues the episode whose log is in `dir` under the same contract. A
/// log ending at an event boundary with every binding obligation closed is
/// continued in place; one cut short mid-line or with an obligation open
/// is seeded at its last clean boundary into a fresh directory beside it,
/// which the run then continues. A prepared seeded log, ending at
/// `seed/end`, is continued as it stands with no fingerprint comparison,
/// because a seeded `episode/start` records its source's contract.
fn resume(dir: &Path, contract_fingerprint: &str) -> Result<(PathBuf, ChildLaunch), String> {
    let dir = dir.canonicalize().map_err(|e| format!("{}: {e}", dir.display()))?;
    let in_dir = |e: LogError| format!("{}: {e}", dir.display());
    let (events, consumed) = foe_log::fold::read_from(&dir, 0).map_err(in_dir)?;
    let state = foe_log::fold::fold(&events).map_err(in_dir)?;
    let start = state.start.ok_or_else(|| format!("{}: the log has no episode/start", dir.display()))?;
    if state.outcome.is_some() {
        let (dir, id) = (dir.display(), &start.id);
        return Err(format!("{dir}: episode {id} already ended; a finished log is forked, not resumed: foe \"task\" --fork {dir} --at SEQ"));
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
    if prepared {
        return Ok((dir, launch));
    }
    if !torn && foe_log::fold::open_obligations(&events).is_empty() {
        return Ok((dir, launch));
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
    eprintln!(
        "foe: {} stopped mid-line or mid-obligation; episode {new_id} continues it in {}",
        dir.display(),
        dest.display()
    );
    Ok((dest, ChildLaunch { episode_id: new_id, ..launch }))
}

/// The contract document to run: the document named by `--config`, with the
/// command-line task replacing its own, or the built-in coding workflow
/// for a bare task.
fn load_contract_document(options: &Options) -> Result<ContractDocument, String> {
    let Some(path) = &options.config else {
        let task = options.task.clone().ok_or(USAGE_BARE)?;
        let mut model = match &options.model {
            Some(spec) => {
                let (provider, model) =
                    spec.split_once('/').ok_or("--model takes PROVIDER/MODEL, for example anthropic/claude-opus-5")?;
                ModelConfig::new(provider, model)
            }
            None => default_model()?.ok_or(NO_DEFAULT_MODEL)?,
        };
        if let Some(tier) = &options.service_tier {
            if !matches!(tier.as_str(), "default" | "priority") {
                return Err(format!("--service-tier {tier}: expected default or priority"));
            }
            model.options.insert("service_tier".into(), tier.clone());
        }
        return builtin_contract_document(
            task,
            model,
            options.key_file.as_deref(),
            options.verify.as_deref(),
            options.sandbox.as_deref(),
        );
    };
    if options.verify.is_some() || options.sandbox.is_some() || options.service_tier.is_some() {
        let option = if options.verify.is_some() {
            "--verify"
        } else if options.sandbox.is_some() {
            "--sandbox"
        } else {
            "--service-tier"
        };
        return Err(format!(
            "{option} applies to the built-in coding workflow; a contract document declares its own behavior"
        ));
    }
    let text = std::fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let mut config = foe_contract::document::parse(&text).map_err(|e| format!("{}: {e}", path.display()))?;
    if let Some(task) = &options.task {
        config.task = task.clone();
    }
    Ok(config)
}

/// Applies implementation model settings measured for the built-in coding
/// workflow. A value in the default model file remains authoritative here.
pub(crate) fn apply_builtin_model_defaults(model: &mut ModelConfig) {
    if matches!(model.provider.as_str(), "openai" | "openai-codex") && model.model == "gpt-5.6-sol" {
        model.options.entry("reasoning_effort".into()).or_insert_with(|| "low".into());
    }
}

const USAGE_BARE: &str = "a task or --config FILE is required";
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

/// The built-in coding workflow. `--key-file` names the API key file
/// explicitly; without it the provider's convention path is read.
/// `verify` names an executable verifier: it becomes a `tool_defs` entry
/// named `check` available to every episode. The root completion gate applies
/// to both the assessment's accept branch and the repair branch. Without a
/// verifier, the assessment's typed choice governs completion.
fn builtin_contract_document(
    task: String,
    model: ModelConfig,
    key_file: Option<&Path>,
    verify: Option<&Path>,
    sandbox: Option<&str>,
) -> Result<ContractDocument, String> {
    let cwd = std::env::current_dir().and_then(|d| d.canonicalize()).map_err(|e| format!("current directory: {e}"))?;
    coding_contract_document(&cwd, task, Some(model), key_file, verify, sandbox)
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
            let key_file = key_file.canonicalize().map_err(|e| format!("--key-file {}: {e}", key_file.display()))?;
            let option = credential_option(&model.provider);
            model.options.insert(option.to_string(), key_file.to_string_lossy().into_owned());
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
    let mut config = load_contract_document(&options)?;
    prepare_model(&mut config)?;
    let task = config.task.clone();
    let inherited = options
        .log_dir
        .as_deref()
        .filter(|dir| dir.join("child-launch.json").is_file())
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
    let (log_dir, launch) = episode_directory(&options, &fingerprint.hash, &task)?;
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
    let viewer = match options.host || options.headless {
        true => None,
        false => Some(foe_view::Bound::bind(0).map_err(|e| e.to_string())?),
    };
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
    let outcome = runtime()?.block_on(episode(setup))?;
    if let Some(settings) = &telemetry {
        crate::telemetry::after_run(settings, &telemetry_log_dir);
    }
    if !options.host {
        println!("{}", serde_json::to_string(&outcome).map_err(|e| e.to_string())?);
    }
    Ok(ExitCode::from(match outcome {
        Outcome::Completed { .. } => 0,
        Outcome::Failed { .. } => 1,
        Outcome::Blocked { .. } => 2,
        Outcome::Exhausted { .. } => 3,
    }))
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
    let server = match viewer {
        Some(bound) => Some(bound.serve(&log_dir).await.map_err(|e| e.to_string())?),
        None => None,
    };
    let workflow = contract.workflow.clone();
    let registry = Arc::new(registry);
    let children = Some(router.clone());
    {
        let (protocol, router, cancel) = (protocol.clone(), router.clone(), cancel.clone());
        tokio::spawn(async move {
            if tokio::signal::ctrl_c().await.is_ok() {
                cancel.store(true, Ordering::SeqCst);
                router.cancel_all();
                protocol.stop("interrupted by SIGINT");
            }
        });
    }
    // A parent may have input queued already. Construction finishes before
    // the task takes seq 1, and the reader starts only after that append.
    loop_::initialize(&log, &start).map_err(|e| format!("{}: {e}", log_dir.display()))?;
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
    if server.is_some() {
        tokio::time::sleep(VIEWER_GRACE).await;
    }
    Ok(outcome)
}
