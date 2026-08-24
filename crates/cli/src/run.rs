//! The running form: load the configuration, restrict the process, compose
//! the runtime's parts, run one episode, and report the outcome.
//!
//! Order matters in [`run`]. Everything that reads a file outside the
//! grants, opens a listening socket, or starts a browser happens before the
//! process restricts itself. `foe_core::confine` carries that order: the
//! policy is assembled through an `Unconfined`, and entering confinement
//! consumes it, so no later line can add to the policy. The restriction is
//! applied on the main thread before the asynchronous runtime starts, so
//! every thread of the episode inherits it.

use foe_config::config::{resolve, Program};
use foe_config::identity::{compute, Identity};
use foe_config::{Config, ModelConfig, ToolSpec};
use foe_core::budget::Pool;
use foe_core::confine::{Confined, Unconfined};
use foe_core::context::ContextPolicy;
use foe_core::exec::LocalExecutor;
use foe_core::grants::{RootReader, RootWriter};
use foe_core::identity::runtime_info;
use foe_core::loop_::{self, Log, Params};
use foe_core::protocol::{stdout_mirror, Host};
use foe_core::registry::{Handles, Registry};
use foe_core::sandbox::{Policy, Sandbox};
use foe_core::spawn::{ProcessSpawner, Router, Uplink};
use foe_core::team::{self, Team};
use foe_core::wiring::{BudgetedSpawner, NoHostUplink, StdoutUplink};
use foe_core::{Spawner, Tool, Transport, Writer};
use foe_log::{EpisodeStart, Outcome};
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
const BUILTIN_AUDIT_CALLS: u64 = 60;

/// Runtime-owned instructions for the built-in coding workflow. They name no
/// path, so the program identity is the same in every directory.
const BUILTIN_INSTRUCTION: &str = "Run the declared coding workflow against the task.";
const BUILTIN_IMPLEMENTATION_INSTRUCTION: &str = "Implement the task in the current directory, which is the root \
of every relative path. Inspect the workspace, make the requested changes, and run relevant checks after the \
final change. In the completion value, report changed artifacts, commands and observed results, and unresolved \
risks for an independent audit.";
const BUILTIN_AUDIT_INSTRUCTION: &str = "Independently determine whether the shared workspace satisfies the \
original task. Treat the implementation episode's completion claim as unverified. Inspect the artifacts and run \
checks that distinguish plausible incorrect implementations. Repair every defect you find. After the final edit, \
run the strongest available task-relevant checks. Complete with the workspace in the state the task requires. \
Report every path changed by either episode, including valid implementation changes that required no audit edit.";
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

fn builtin_environment(cwd: &Path, present: impl Fn(&Path) -> bool) -> String {
    let availability =
        BUILTIN_EXECUTABLE_PROBES
            .iter()
            .map(|(name, path)| {
                if present(Path::new(path)) {
                    format!("{name}={path}")
                } else {
                    format!("{name}=not found at {path}")
                }
            })
            .collect::<Vec<_>>()
            .join(", ");
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
    pub key_file: Option<PathBuf>,
    /// An executable verifier for the built-in coding workflow.
    pub verify: Option<PathBuf>,
    pub log_dir: Option<PathBuf>,
    pub no_open: bool,
    pub headless: bool,
    pub host: bool,
}

/// The built-in tools implemented outside the registry: the coding tools
/// and the lead's team tools. Identity and the registry receive this same
/// list, so `foe plan` and a run agree.
pub fn extra_builtin_specs() -> Vec<ToolSpec> {
    foe_code::all()
        .iter()
        .map(|t| t.spec().clone())
        .chain(std::iter::once(foe_core::retrieval::spec()))
        .chain(team::builtin_specs())
        .collect()
}

pub fn identity(program: &Program) -> Result<Identity, String> {
    compute(program, &extra_builtin_specs(), &runtime_info()).map_err(|e| e.to_string())
}

pub fn runtime() -> Result<tokio::runtime::Runtime, String> {
    tokio::runtime::Builder::new_multi_thread().enable_all().build().map_err(|e| format!("runtime: {e}"))
}

/// The compaction policy a `context` block with `compact: true` resolves
/// to, with the window taken from the block or from the provider table for
/// the model named. `None` when the program never compacts. An unknown
/// model with no `window_tokens` is a construction error.
pub fn context_policy(program: &Program) -> Result<Option<foe_context::Policy>, String> {
    let Some(cfg) = program.context.clone().filter(|c| c.compact) else { return Ok(None) };
    let model = program.model.as_ref();
    let window = cfg.window_tokens.or_else(|| model.and_then(known_window)).ok_or_else(|| {
        let named = model.map_or("the host's model".to_string(), |m| format!("{}/{}", m.provider, m.model));
        format!(
            "config: context.window_tokens: is required because {named} is not in the provider table; set it to \
             the model's context window in tokens"
        )
    })?;
    let max_output = model.and_then(|m| m.max_output_tokens).map_or(0, u64::from);
    Ok(Some(foe_context::Policy::new(cfg, window, max_output, program.done_when.as_ref())))
}

#[cfg(feature = "transport")]
fn known_window(model: &ModelConfig) -> Option<u64> {
    foe_transport::context_window(model)
}

#[cfg(not(feature = "transport"))]
fn known_window(_model: &ModelConfig) -> Option<u64> {
    None
}

/// Who this episode is. A child reads its ids from the `lineage.json` its
/// parent wrote beside the log; a root draws a fresh id.
#[derive(serde::Deserialize)]
struct Lineage {
    episode_id: String,
    #[serde(default)]
    parent_id: Option<String>,
    #[serde(default)]
    team_id: Option<String>,
}

impl Lineage {
    fn read(log_dir: Option<&Path>) -> Result<Lineage, String> {
        let file = log_dir.map(|d| d.join("lineage.json")).filter(|f| f.is_file());
        match file {
            Some(file) => {
                serde_json::from_slice(&std::fs::read(&file).map_err(|e| format!("{}: {e}", file.display()))?)
                    .map_err(|e| format!("{}: {e}", file.display()))
            }
            None => Ok(Lineage { episode_id: fresh_id(), parent_id: None, team_id: None }),
        }
    }
}

fn fresh_id() -> String {
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
    let digest = Sha256::digest(format!("{now}:{}", std::process::id()));
    format!("ep_{}", hex::encode(&digest[..4]))
}

/// The configuration to run: the document named by `--config`, with the
/// command-line task replacing its own, or the built-in coding workflow
/// for a bare task.
fn load_config(options: &Options) -> Result<Config, String> {
    let Some(path) = &options.config else {
        let task = options.task.clone().ok_or(USAGE_BARE)?;
        let model = match &options.model {
            Some(spec) => {
                let (provider, model) =
                    spec.split_once('/').ok_or("--model takes PROVIDER/MODEL, for example anthropic/claude-opus-5")?;
                ModelConfig::new(provider, model)
            }
            None => default_model()?.ok_or(NO_DEFAULT_MODEL)?,
        };
        return builtin_config(task, model, options.key_file.as_deref(), options.verify.as_deref());
    };
    if options.verify.is_some() {
        return Err("--verify applies to the built-in coding workflow; a configuration document declares its own \
                    done_when and skip_when_verified"
            .into());
    }
    let text = std::fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let mut config = foe_config::config::parse(&text).map_err(|e| format!("{}: {e}", path.display()))?;
    if let Some(task) = &options.task {
        config.task = task.clone();
    }
    Ok(config)
}

/// Applies model settings measured for the built-in coding workflow.
/// A value in the default model file remains authoritative.
pub(crate) fn apply_builtin_model_defaults(model: &mut ModelConfig) {
    if matches!(model.provider.as_str(), "openai" | "openai-codex") && model.model == "gpt-5.6-sol" {
        model.options.entry("reasoning_effort".into()).or_insert_with(|| "low".into());
    }
}

const USAGE_BARE: &str = "a task or --config FILE is required";
const NO_DEFAULT_MODEL: &str =
    "no model: run `foe login <provider>` once to set a default, or give --model PROVIDER/MODEL";

#[cfg(feature = "transport")]
fn default_model() -> Result<Option<ModelConfig>, String> {
    foe_transport::auth::login::default_model()
}

#[cfg(not(feature = "transport"))]
fn default_model() -> Result<Option<ModelConfig>, String> {
    Ok(None)
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
/// named `check` with execute authority on that file, the implementation
/// episode declares `done_when.verify` on it, and the audit node gains
/// `skip_when_verified`, so an accepted implementation skips the audit.
/// Without it the workflow is unchanged: always audited.
fn builtin_config(
    task: String,
    mut model: ModelConfig,
    key_file: Option<&Path>,
    verify: Option<&Path>,
) -> Result<Config, String> {
    let cwd = std::env::current_dir().and_then(|d| d.canonicalize()).map_err(|e| format!("current directory: {e}"))?;
    let explicit_reasoning = model.option("reasoning_effort").is_some();
    apply_builtin_model_defaults(&mut model);
    if let Some(key_file) = key_file {
        let key_file = key_file.canonicalize().map_err(|e| format!("--key-file {}: {e}", key_file.display()))?;
        model.options.insert("api_key_file".to_string(), key_file.to_string_lossy().into_owned());
    }
    let mut audit_model = model.clone();
    if !explicit_reasoning
        && matches!(audit_model.provider.as_str(), "openai" | "openai-codex")
        && audit_model.model == "gpt-5.6-sol"
    {
        audit_model.options.insert("reasoning_effort".into(), "high".into());
    }
    let environment = builtin_environment(&cwd, Path::is_file);
    let completion = serde_json::json!({
        "type": "object",
        "properties": {
            "summary": { "type": "string", "minLength": 1, "maxLength": 1000 },
            "changed_paths": {
                "type": "array", "items": { "type": "string", "maxLength": 512 }, "maxItems": 64
            },
            "validation": {
                "type": "array", "items": { "type": "string", "maxLength": 1000 },
                "minItems": 1, "maxItems": 32
            },
            "unresolved_risks": {
                "type": "array", "items": { "type": "string", "maxLength": 1000 }, "maxItems": 16
            }
        },
        "required": ["summary", "changed_paths", "validation", "unresolved_risks"],
        "additionalProperties": false
    });
    let grants = serde_json::json!({ "read": [cwd], "write": [cwd] });
    let tools = serde_json::json!(["read", "grep", "edit", "bash"]);
    let document = serde_json::json!({
        "version": foe_config::config::CONFIG_VERSION,
        "name": "coding",
        "instructions": { "role": BUILTIN_INSTRUCTION },
        "tools": tools,
        "grants": grants,
        "budget": {
            "model_calls": BUILTIN_IMPLEMENTATION_CALLS + BUILTIN_AUDIT_CALLS,
            "max_episodes": 3,
            "max_concurrent": 1
        },
        "model": model,
        "workflow": {
            "nodes": {
                "implement-task": {
                    "model": {
                        "name": "implement-task",
                        "instructions": {
                            "environment": environment,
                            "role": BUILTIN_IMPLEMENTATION_INSTRUCTION
                        },
                        "tools": tools,
                        "grants": grants,
                        "budget": { "model_calls": BUILTIN_IMPLEMENTATION_CALLS },
                        "done_when": { "returns": completion }
                    },
                    "follows": ["task"]
                },
                "audit-and-repair-task": {
                    "model": {
                        "name": "audit-and-repair-task",
                        "instructions": {
                            "environment": environment,
                            "role": BUILTIN_AUDIT_INSTRUCTION
                        },
                        "tools": tools,
                        "grants": grants,
                        "budget": { "model_calls": BUILTIN_AUDIT_CALLS },
                        "done_when": { "returns": completion },
                        "model": audit_model
                    },
                    "follows": ["task", "implement-task"],
                    "terminal": true
                }
            },
            "recovery": { "enabled": false }
        },
        "task": task,
    });
    if let Some(check) = verify {
        let check = check.canonicalize().map_err(|e| format!("--verify {}: {e}", check.display()))?;
        let def = serde_json::json!({ "exec": check, "description": BUILTIN_VERIFIER_DESCRIPTION, "cwd": cwd });
        let mut document = document;
        let implement = "implement-task";
        document["tools"].as_array_mut().expect("a tool list").push(serde_json::json!("check"));
        let node_tools = &mut document["workflow"]["nodes"][implement]["model"]["tools"];
        node_tools.as_array_mut().expect("a tool list").push(serde_json::json!("check"));
        document["tool_defs"] = serde_json::json!({ "check": def });
        document["workflow"]["nodes"][implement]["model"]["tool_defs"] = serde_json::json!({ "check": def });
        document["workflow"]["nodes"][implement]["model"]["done_when"]["verify"] = serde_json::json!("check");
        document["workflow"]["nodes"]["audit-and-repair-task"]["skip_when_verified"] = serde_json::json!(implement);
        return serde_json::from_value(document).map_err(|e| format!("built-in configuration: {e}"));
    }
    serde_json::from_value(document).map_err(|e| format!("built-in configuration: {e}"))
}

#[cfg(test)]
#[path = "run_test.rs"]
mod tests;

/// One line naming the transport a `model` block resolves to, for `foe plan`.
#[cfg(feature = "transport")]
pub fn describe_transport(model: &ModelConfig) -> String {
    foe_transport::plan(model).map(|plan| plan.describe()).unwrap_or_else(|e| e.to_string())
}

#[cfg(not(feature = "transport"))]
pub fn describe_transport(model: &ModelConfig) -> String {
    format!("{}/{}: this binary was built without the transport feature", model.provider, model.model)
}

/// Resolves the `model` block into a transport before the process restricts
/// itself. The block is rewritten with the credential path it resolved to,
/// so the record names the credential that ran; the path joins the sandbox
/// policy as a readable file; and an `exec` provider's program joins it as
/// an executable, run through the episode's own executor.
#[cfg(feature = "transport")]
fn built_in_transport(
    model: &mut ModelConfig,
    unconfined: &mut Unconfined,
    log_dir: &Path,
) -> Result<Arc<dyn Transport>, String> {
    let plan = foe_transport::plan(model).map_err(|e| e.to_string())?;
    let policy = unconfined.policy_mut();
    policy.read_files.extend(plan.credential_path.iter().cloned());
    policy.exec.extend(plan.exec.iter().cloned());
    let executor: Option<Arc<dyn foe_core::Executor>> = plan.exec.as_ref().map(|_| {
        let (sandbox, policy) = unconfined.parts();
        let cancel = Arc::new(AtomicBool::new(false));
        Arc::new(LocalExecutor::new(sandbox.clone(), policy.clone(), log_dir.join("spill"), cancel))
            as Arc<dyn foe_core::Executor>
    });
    *model = plan.model.clone();
    foe_transport::build_planned(&plan, executor).map_err(|e| e.to_string())
}

#[cfg(not(feature = "transport"))]
fn built_in_transport(
    _model: &mut ModelConfig,
    _unconfined: &mut Unconfined,
    _log_dir: &Path,
) -> Result<Arc<dyn Transport>, String> {
    Err("this binary was built without the transport feature; remove `model` and run under a host".into())
}

pub fn run(options: Options) -> Result<ExitCode, String> {
    let config = load_config(&options)?;
    let mut program = resolve(&config).map_err(|e| format!("config: {e}"))?;
    let lineage = Lineage::read(options.log_dir.as_deref())?;
    let log_dir = options.log_dir.clone().unwrap_or_else(|| PathBuf::from(".foe").join(&lineage.episode_id));
    std::fs::create_dir_all(&log_dir).map_err(|e| format!("{}: {e}", log_dir.display()))?;
    let log_dir = log_dir.canonicalize().map_err(|e| format!("{}: {e}", log_dir.display()))?;
    let sandbox = Arc::new(Sandbox::new(program.sandbox.mode).map_err(|e| e.to_string())?);
    // Every model node of a workflow is a child program of the spawner, so
    // the policy and the spawner see the same configuration.
    let config = foe_workflow::spawner_config(&config);
    let mut unconfined = Unconfined::new(sandbox, Policy::for_episode(&config, &log_dir));
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
        unconfined.policy_mut().write.push(dir);
    }
    let viewer = match options.host || options.headless {
        true => None,
        false => Some(foe_view::Bound::bind(0).map_err(|e| e.to_string())?),
    };
    if let Some(bound) = &viewer {
        unconfined.policy_mut().bind_tcp.push(bound.addr.port());
        if !options.no_open {
            crate::open_browser(&bound.url());
        }
    }
    let identity = identity(&program)?;
    let context = context_policy(&program)?.map(|p| Arc::new(p) as Arc<dyn ContextPolicy>);
    let runtime_info = runtime_info();
    let transport = match &mut program.model {
        Some(model) => Some(built_in_transport(model, &mut unconfined, &log_dir)?),
        None if options.host => None,
        None => return Err("no model: give --model and --key-file, add a `model` block, or run under --host".into()),
    };
    let confined = unconfined.enter().map_err(|e| e.to_string())?;
    let start = EpisodeStart {
        id: lineage.episode_id,
        parent_id: lineage.parent_id,
        fork_origin: None,
        team_id: lineage.team_id,
        program: program.to_value(),
        identity: identity.hash,
        task: config.task.clone(),
        runtime: runtime_info,
        sandbox: confined.parts().0.info(),
    };
    let telemetry_log_dir = log_dir.clone();
    let setup = Setup { config, program, log_dir, confined, viewer, transport, context, start, host: options.host };
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
    config: Config,
    program: Program,
    log_dir: PathBuf,
    confined: Confined,
    viewer: Option<foe_view::Bound>,
    transport: Option<Arc<dyn Transport>>,
    context: Option<Arc<dyn ContextPolicy>>,
    start: EpisodeStart,
    host: bool,
}

async fn episode(setup: Setup) -> Result<Outcome, String> {
    let Setup { config, program, log_dir, confined, viewer, transport, host, context, start } = setup;
    let id = start.id.clone();
    let mirror = host.then(stdout_mirror);
    let log = Arc::new(Log::create_or_open(&log_dir, mirror).map_err(|e| format!("{}: {e}", log_dir.display()))?);
    let router = Arc::new(Router::new());
    let (protocol, stop) = Host::new(id.clone(), log.clone(), Some(router.clone()));
    if host {
        protocol.spawn_reader(tokio::io::stdin());
    }
    let cancel = Arc::new(AtomicBool::new(false));
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
    let transport = transport.unwrap_or_else(|| protocol.transport());
    let pool = Arc::new(Mutex::new(Pool::new(program.budget.clone())));
    let inbox = Arc::new(protocol.clone());
    let team = Arc::new(Team::new(id.clone(), log.clone(), inbox, router.clone(), pool.clone()));
    let uplink: Arc<dyn Uplink> = if host { Arc::new(StdoutUplink) } else { Arc::new(NoHostUplink) };
    let spawner =
        ProcessSpawner::new(id.clone(), log_dir.clone(), config.clone(), uplink, router.clone(), team.clone())
            .map_err(|e| format!("spawner: {e}"))?;
    let spawner: Arc<dyn Spawner> = Arc::new(BudgetedSpawner::new(Arc::new(spawner), log.clone(), pool.clone()));
    let (sandbox, policy) = confined.parts();
    let executor = LocalExecutor::new(sandbox.clone(), policy.clone(), log_dir.join("spill"), cancel);
    let host_tools = if host { protocol.tools(&program) } else { Vec::new() };
    let parent = start.parent_id.is_some().then_some(&protocol);
    let mut builtins: Vec<Box<dyn Tool>> = foe_code::all();
    builtins.push(foe_core::retrieval::tool(log.clone()));
    builtins.extend(team::tools(team.clone(), parent));
    let registry = Registry::new(&program, host_tools, builtins).map_err(|e| format!("config: {e}"))?;
    let write = program.grants.write.clone();
    let reader = RootReader::new(program.grants.read.clone()).map_err(|e| format!("grants.read: {e}"))?;
    let writer = match write.is_empty() {
        true => None,
        false => Some(Arc::new(RootWriter::new(write).map_err(|e| format!("grants.write: {e}"))?) as Arc<dyn Writer>),
    };
    let handles = Handles {
        reader: Some(Arc::new(reader)),
        writer,
        executor: Some(Arc::new(executor)),
        spawner: (!program.grants.spawn.is_empty()).then(|| spawner.clone()),
    };
    let server = match viewer {
        Some(bound) => Some(bound.serve(&log_dir).await.map_err(|e| e.to_string())?),
        None => None,
    };
    let workflow = program.workflow.clone();
    let registry = Arc::new(registry);
    let children = Some(router.clone());
    let params = Params { log, start, program, registry, handles, transport, pool, stop, children, context };
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
