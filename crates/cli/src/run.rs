//! The running form: load the configuration, restrict the process, compose
//! the runtime's parts, run one episode, and report the outcome.
//!
//! Order matters in [`run`]. Everything that reads a file outside the
//! grants, opens a listening socket, or starts a browser happens before the
//! process restricts itself, and the restriction is applied on the main
//! thread before the asynchronous runtime starts, so every thread of the
//! episode inherits it.

use foe_core::budget::Pool;
use foe_core::config::{resolve, Program};
use foe_core::exec::LocalExecutor;
use foe_core::grants::{RootReader, RootWriter};
use foe_core::identity::{self, Identity};
use foe_core::loop_::{self, Log, Params};
use foe_core::protocol::{stdout_mirror, Host};
use foe_core::registry::{Handles, Registry};
use foe_core::sandbox::{Policy, Sandbox};
use foe_core::spawn::{ProcessSpawner, Router, Uplink};
use foe_core::team::{self, Team};
use foe_core::wiring::{BudgetedSpawner, NoHostUplink, StdoutUplink};
use foe_core::{Config, ModelConfig, Provider, Spawner, Tool, ToolSpec, Transport, Writer};
use foe_log::{EpisodeStart, Outcome};
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

/// How long the viewer stays reachable after the episode ends, so that an
/// open page receives the final events.
const VIEWER_GRACE: Duration = Duration::from_secs(3);

/// The one instruction section of the built-in coding configuration. It
/// names no path, so the identity is the same in every directory.
const BUILTIN_INSTRUCTION: &str = "You are a coding agent working in the current directory, which is the root \
of every relative path. Make the requested change, then verify it by running the relevant build or tests before \
you finish.";

#[derive(Debug, Default)]
pub struct Options {
    pub task: Option<String>,
    pub config: Option<PathBuf>,
    pub model: Option<String>,
    pub key_file: Option<PathBuf>,
    pub log_dir: Option<PathBuf>,
    pub no_open: bool,
    pub headless: bool,
    pub host: bool,
}

/// The built-in tools implemented outside the registry: the coding tools
/// and the lead's team tools. Identity and the registry receive this same
/// list, so `foe plan` and a run agree.
pub fn extra_builtin_specs() -> Vec<ToolSpec> {
    foe_code::all().iter().map(|t| t.spec().clone()).chain(team::builtin_specs()).collect()
}

pub fn identity(program: &Program) -> Result<Identity, String> {
    identity::compute(program, &extra_builtin_specs(), &identity::runtime_info()).map_err(|e| e.to_string())
}

pub fn runtime() -> Result<tokio::runtime::Runtime, String> {
    tokio::runtime::Builder::new_multi_thread().enable_all().build().map_err(|e| format!("runtime: {e}"))
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
/// command-line task replacing its own, or the built-in coding configuration
/// for a bare task.
fn load_config(options: &Options) -> Result<Config, String> {
    let Some(path) = &options.config else {
        let task = options.task.clone().ok_or(USAGE_BARE)?;
        let (model, key_file) = match (&options.model, &options.key_file) {
            (Some(model), Some(key_file)) => (model, key_file),
            _ => return Err("a task without --config takes --model PROVIDER/MODEL and --key-file PATH".into()),
        };
        return builtin_config(task, model, key_file);
    };
    let text = std::fs::read_to_string(path).map_err(|e| format!("{}: {e}", path.display()))?;
    let mut config = foe_core::config::parse(&text).map_err(|e| format!("{}: {e}", path.display()))?;
    if let Some(task) = &options.task {
        config.task = task.clone();
    }
    Ok(config)
}

const USAGE_BARE: &str = "a task or --config FILE is required";

fn builtin_config(task: String, model: &str, key_file: &Path) -> Result<Config, String> {
    let (provider, model) =
        model.split_once('/').ok_or("--model takes PROVIDER/MODEL, for example anthropic/claude-opus-5")?;
    let provider = match provider {
        "anthropic" => Provider::Anthropic,
        "openai-compatible" => Provider::OpenaiCompatible,
        other => return Err(format!("--model: {other} is neither anthropic nor openai-compatible")),
    };
    let cwd = std::env::current_dir().and_then(|d| d.canonicalize()).map_err(|e| format!("current directory: {e}"))?;
    let key_file = key_file.canonicalize().map_err(|e| format!("--key-file {}: {e}", key_file.display()))?;
    let model = ModelConfig {
        provider,
        model: model.to_string(),
        api_key_file: key_file,
        base_url: None,
        max_output_tokens: None,
    };
    let document = serde_json::json!({
        "version": 1,
        "name": "coding",
        "instructions": { "role": BUILTIN_INSTRUCTION },
        "tools": ["read", "grep", "edit", "bash"],
        "grants": { "read": [cwd], "write": [cwd] },
        "budget": { "model_calls": 40 },
        "model": model,
        "task": task,
    });
    serde_json::from_value(document).map_err(|e| format!("built-in configuration: {e}"))
}

#[cfg(feature = "transport")]
fn built_in_transport(model: &ModelConfig) -> Result<Arc<dyn Transport>, String> {
    foe_transport::from_config(model).map(Arc::from).map_err(|e| e.to_string())
}

#[cfg(not(feature = "transport"))]
fn built_in_transport(_model: &ModelConfig) -> Result<Arc<dyn Transport>, String> {
    Err("this binary was built without the transport feature; remove `model` and run under a host".into())
}

/// Starts the user's browser on the viewer. Runs before the process is
/// restricted, because the browser would otherwise inherit the restriction.
fn open_browser(url: &str) {
    let started = std::process::Command::new("/usr/bin/xdg-open")
        .arg(url)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn();
    if let Err(e) = started {
        eprintln!("foe: /usr/bin/xdg-open: {e}");
    }
}

pub fn run(options: Options) -> Result<ExitCode, String> {
    let config = load_config(&options)?;
    let program = resolve(&config).map_err(|e| format!("config: {e}"))?;
    let lineage = Lineage::read(options.log_dir.as_deref())?;
    let log_dir = options.log_dir.clone().unwrap_or_else(|| PathBuf::from(".foe").join(&lineage.episode_id));
    std::fs::create_dir_all(&log_dir).map_err(|e| format!("{}: {e}", log_dir.display()))?;
    let log_dir = log_dir.canonicalize().map_err(|e| format!("{}: {e}", log_dir.display()))?;
    let sandbox = Arc::new(Sandbox::new(program.sandbox.mode).map_err(|e| e.to_string())?);
    let mut policy = Policy::for_episode(&config, &log_dir);
    let viewer = match options.host || options.headless {
        true => None,
        false => Some(foe_view::Bound::bind(0).map_err(|e| e.to_string())?),
    };
    if let Some(bound) = &viewer {
        policy.bind_tcp.push(bound.addr.port());
        if !options.no_open {
            open_browser(&bound.url());
        }
    }
    let identity = identity(&program)?;
    let runtime_info = identity::runtime_info();
    let transport = match &program.model {
        Some(model) => Some(built_in_transport(model)?),
        None if options.host => None,
        None => return Err("no model: give --model and --key-file, add a `model` block, or run under --host".into()),
    };
    sandbox.enforce_self(&policy).map_err(|e| e.to_string())?;
    let setup = Setup {
        config,
        program,
        lineage,
        log_dir,
        sandbox,
        policy,
        viewer,
        identity,
        runtime_info,
        transport,
        host: options.host,
    };
    let outcome = runtime()?.block_on(episode(setup))?;
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

/// Everything decided before the process restricted itself.
struct Setup {
    config: Config,
    program: Program,
    lineage: Lineage,
    log_dir: PathBuf,
    sandbox: Arc<Sandbox>,
    policy: Policy,
    viewer: Option<foe_view::Bound>,
    identity: Identity,
    runtime_info: foe_log::RuntimeInfo,
    transport: Option<Arc<dyn Transport>>,
    host: bool,
}

async fn episode(setup: Setup) -> Result<Outcome, String> {
    let Setup { config, program, lineage, log_dir, sandbox, policy, viewer, identity, runtime_info, transport, host } =
        setup;
    let id = lineage.episode_id.clone();
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
    let team = Arc::new(Team::new(id.clone(), log.clone(), Arc::new(protocol.clone()), router.clone()));
    let uplink: Arc<dyn Uplink> = if host { Arc::new(StdoutUplink) } else { Arc::new(NoHostUplink) };
    let spawner =
        ProcessSpawner::new(id.clone(), log_dir.clone(), config.clone(), uplink, router.clone(), team.clone())
            .map_err(|e| format!("spawner: {e}"))?;
    let spawner: Arc<dyn Spawner> = Arc::new(BudgetedSpawner::new(Arc::new(spawner), log.clone(), pool.clone()));
    let executor = LocalExecutor::new(sandbox.clone(), policy, log_dir.join("spill"), cancel);
    let host_tools = if host { protocol.tools(&program) } else { Vec::new() };
    let parent = lineage.parent_id.is_some().then_some(&protocol);
    let mut builtins: Vec<Box<dyn Tool>> = foe_code::all();
    builtins.extend(team::tools(team.clone(), parent));
    let registry = Registry::new(&program, host_tools, builtins).map_err(|e| format!("config: {e}"))?;
    let write = program.grants.write.clone();
    let handles = Handles {
        reader: Some(Arc::new(RootReader::new(program.grants.read.clone()))),
        writer: (!write.is_empty()).then(|| Arc::new(RootWriter::new(write)) as Arc<dyn Writer>),
        executor: Some(Arc::new(executor)),
        spawner: (!program.grants.spawn.is_empty()).then_some(spawner),
    };
    let start = EpisodeStart {
        id,
        parent_id: lineage.parent_id,
        fork_origin: None,
        team_id: lineage.team_id,
        program: program.to_value(),
        identity: identity.hash,
        task: config.task.clone(),
        runtime: runtime_info,
        sandbox: sandbox.info(),
    };
    let server = match viewer {
        Some(bound) => Some(bound.serve(&log_dir).await.map_err(|e| e.to_string())?),
        None => None,
    };
    let params = Params { log, start, program, registry: Arc::new(registry), handles, transport, pool, stop };
    let outcome = loop_::run(params).await.map_err(|e| e.to_string())?;
    if server.is_some() {
        tokio::time::sleep(VIEWER_GRACE).await;
    }
    Ok(outcome)
}
