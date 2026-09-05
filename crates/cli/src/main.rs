//! The `foe` binary. docs/design.md "The command line" states the forms.
//! This file parses the command line and implements the forms that run
//! nothing: `view` and `plan`. The running form is in `run.rs`, `login` in
//! `login.rs`, and `init` in `init.rs`.

#![forbid(unsafe_code)]

mod init;
mod login;
mod plan;
mod run;
mod telemetry;

use foe_contract::tools::{block_spec, resolve_specs, Source};
use foe_contract::SCHEMA;
use std::collections::BTreeMap;
use std::fmt::Write;
use std::path::{Path, PathBuf};
use std::process::{ExitCode, Stdio};

/// Every word of the table is fixed at compile time.
type Text = &'static str;

/// One option, and the form that accepts it: the command word of that form,
/// the flag, the value placeholder, empty for a switch, what applies when
/// the option is absent, and what it does. `absent` is the literal
/// `required` when the form refuses to run without the option, otherwise the
/// default in words, or empty when a switch that is simply off has nothing
/// to say.
struct Opt {
    command: Text,
    flag: Text,
    value: Text,
    absent: Text,
    meaning: Text,
}

const fn opt(command: Text, flag: Text, value: Text, absent: Text, meaning: Text) -> Opt {
    Opt { command, flag, value, absent, meaning }
}

/// One command form: the word that selects it, empty for the bare running
/// form; its positional arguments in usage shape, where a bracketed word is
/// optional and a trailing `...` repeats; and one line saying what it does.
struct Form {
    name: Text,
    args: Text,
    about: Text,
}

const fn form(name: Text, args: Text, about: Text) -> Form {
    Form { name, args, about }
}

/// The forms, the running one first: it is what `foe` does without a command
/// word, and what the top-level help describes.
const FORMS: &[Form] = &[
    form("", "[TASK]", "run one bounded episode: the task named here, or the task a contract document names"),
    form("login", "[PROVIDER]", "configure a provider's credential and the default model, or list the providers"),
    form("init", "", "write a repository's starting execution contract and placeholder verifier into its .foe directory"),
    form("view", "DIR", "write a log directory as one self-contained page, or serve it"),
    form("plan", "", "print a readiness summary, then the resolved contract with its fingerprint, model endpoint, reachable tools, and resolved permissions; bare, list the built-in tools"),
    form("telemetry", "LOG...", "print, for finished episode logs, the payload telemetry emission writes"),
];

/// Every option of every form, in the order help lists them. The parser and
/// both help screens read this and nothing else, so an option missing here
/// is not accepted, and one present here is documented. `--help` is accepted
/// by every form and so appears once, under no command.
const OPTS: &[Opt] = &[
    opt("", "--config", "FILE", "the built-in coding workflow", "the contract document to run"),
    opt("", "--model", "PROVIDER/MODEL", "the default model `foe login` wrote", "the model that answers"),
    opt("", "--service-tier", "TIER", "the model configuration's value", "request service tier: default or priority"),
    opt(
        "",
        "--key-file",
        "PATH",
        "the provider's file under ~/.config/foe/credentials/",
        "the provider credential file to use",
    ),
    opt(
        "",
        "--verify",
        "PATH",
        "the built-in workflow has no verifier gate",
        "an executable verifier whose acceptance completes the built-in workflow",
    ),
    opt("", "--sandbox", "MODE", "best-effort", "kernel confinement mode: best-effort, required, or off"),
    opt("", "--log-dir", "DIR", ".foe/<episode-id>", "where the episode log is written"),
    opt("", "--fork", "SOURCE_DIR", "", "seed the log from a prefix of SOURCE_DIR's log; the task is the fork's task"),
    opt("", "--at", "SEQ", "", "the --fork boundary: source events with seq below SEQ are copied"),
    opt("", "--no-open", "", "", "serve the viewer without opening a browser on it"),
    opt("", "--headless", "", "", "run without the browser viewer"),
    opt("", "--conversation", "", "", "show the conversation and execution tree in the terminal"),
    opt("", "--host", "", "", "answer model requests over the protocol on standard input; --config carries the task"),
    opt("login", "--model", "MODEL", "chosen from the provider's list", "the default model to record"),
    opt("init", "--repository", "PATH", "required", "the repository to write .foe/contract.json and .foe/verify for"),
    opt("login", "--status", "", "", "print the default model and every configured credential path"),
    opt("view", "--serve", "", "", "serve the directory instead of writing the page to standard output"),
    opt("view", "--port", "N", "an ephemeral port, printed as the first line", "the port to serve on"),
    opt("plan", "--config", "FILE", "the built-in tools alone", "the contract document to resolve"),
    opt("plan", "--json", "", "", "print one JSON object instead of the report"),
    opt("plan", "--schema", "", "", "print the JSON Schema of the contract document and nothing else"),
    opt("telemetry", "--json", "", "", "print that payload as JSON instead of a summary"),
];

/// The `--help` row, accepted by every form and listed in every help screen.
const HELP: Opt = opt("*", "--help", "", "", "print this help and exit");

/// How a message names a form: `foe` alone for the bare running form.
fn spelled(form: &Form) -> String {
    format!("foe {}", form.name).trim_end().to_string()
}

/// Every option the form accepts: its own rows, then `--help`.
fn accepted(form: &'static Form) -> impl Iterator<Item = &'static Opt> {
    OPTS.iter().filter(|o| o.command == form.name).chain(std::iter::once(&HELP))
}

/// The form an invocation selects: the first bare word naming one, or the
/// running form. A flag is skipped along with its value when any row gives
/// it one, because the form is not known yet; no flag takes a value in one
/// form and none in another.
fn form_of(argv: &[String]) -> &'static Form {
    let mut rest = argv.iter();
    while let Some(arg) = rest.next() {
        if !arg.starts_with("--") {
            return FORMS.iter().find(|form| form.name == arg.as_str()).unwrap_or(&FORMS[0]);
        }
        if OPTS.iter().any(|o| o.flag == arg.as_str() && !o.value.is_empty()) {
            rest.next();
        }
    }
    &FORMS[0]
}

/// What one invocation gave: its positional arguments without the command
/// word, and every option it set, keyed by the flag in the table.
#[derive(Default)]
struct Given {
    positional: Vec<String>,
    options: BTreeMap<&'static str, String>,
}

impl Given {
    fn value(&mut self, flag: &str) -> Option<String> {
        self.options.remove(flag)
    }

    fn switch(&mut self, flag: &str) -> bool {
        self.options.remove(flag).is_some()
    }
}

/// Reads the arguments against one form's row. Acceptance, value shape, and
/// both error messages are the row's, so nothing here knows any flag by name.
fn given(form: &'static Form, argv: &[String]) -> Result<Given, String> {
    let mut out = Given::default();
    let mut rest = argv.iter();
    while let Some(arg) = rest.next() {
        if !arg.starts_with("--") {
            if out.positional.is_empty() && !form.name.is_empty() && arg.as_str() == form.name {
                continue;
            }
            out.positional.push(arg.clone());
            continue;
        }
        let Some(o) = accepted(form).find(|o| o.flag == arg.as_str()) else {
            let it = spelled(form);
            return Err(format!("unknown option {arg} for `{it}`; run `{it} --help` for the options it takes"));
        };
        let value = match o.value.is_empty() {
            true => String::new(),
            false => rest.next().cloned().ok_or_else(|| format!("{arg} takes {}", o.value))?,
        };
        out.options.insert(o.flag, value);
    }
    Ok(out)
}

/// `foe <command> --help`: the usage line, what the form does, and every
/// option with its meaning and what applies when it is absent. For the
/// running form this is `foe --help`, which adds the other command words,
/// because a bare `foe` is the running form.
fn help(form: &'static Form) -> String {
    let mut line = format!("usage: foe {} {}", form.name, form.args);
    for o in OPTS.iter().filter(|o| o.command == form.name) {
        let flag = format!("{} {}", o.flag, o.value);
        let flag = flag.trim_end();
        write!(line, " {}", if o.absent == "required" { flag.to_string() } else { format!("[{flag}]") }).ok();
    }
    let line = line.split_whitespace().collect::<Vec<&str>>().join(" ");
    let mut out = format!("{line}\n\n{}\n\noptions:\n", form.about);
    for o in accepted(form) {
        let flag = format!("{} {}", o.flag, o.value);
        let absent = match o.absent {
            "" => String::new(),
            "required" => " (required)".to_string(),
            text => format!(" (default: {text})"),
        };
        writeln!(out, "  {:<24}{}{}", flag.trim_end(), o.meaning, absent).ok();
    }
    if form.name.is_empty() {
        out.push_str("\ncommands:\n");
        for other in FORMS.iter().filter(|f| !f.name.is_empty()) {
            writeln!(out, "  {:<11}{}", other.name, other.about).ok();
        }
        out.push_str("\nrun `foe <command> --help` for what one command takes.\n");
    }
    out
}

enum Command {
    Run(run::Options),
    Init { repository: PathBuf },
    Login { provider: Option<String>, model: Option<String>, status: bool },
    View { dir: PathBuf, serve: bool, port: u16 },
    Plan { config: Option<PathBuf>, json: bool },
    Schema,
    Telemetry { logs: Vec<String>, json: bool },
    Help(&'static Form),
}

/// The table owns syntax and these arms own meaning: each turns the values
/// its row admitted into the command that runs.
fn command(argv: &[String]) -> Result<Command, String> {
    let asked = argv.first().is_some_and(|word| word == "help");
    let argv = if asked { &argv[1..] } else { argv };
    let form = form_of(argv);
    let mut args = given(form, argv)?;
    if asked || args.switch("--help") {
        return Ok(Command::Help(form));
    }
    // The positional bounds are what the form's shape states: a bracketed
    // word is optional, and a trailing `...` repeats the last one.
    let words: Vec<&str> = form.args.split_whitespace().collect();
    let least = words.iter().filter(|word| !word.starts_with('[')).count();
    let most = if form.args.ends_with("...") { usize::MAX } else { words.len() };
    if !(least..=most).contains(&args.positional.len()) {
        let (it, shape) = (spelled(form), if words.is_empty() { "no arguments" } else { form.args });
        return Err(format!("`{it}` takes {shape}; run `{it} --help`"));
    }
    Ok(match form.name {
        "telemetry" => Command::Telemetry { json: args.switch("--json"), logs: args.positional },
        "view" => Command::View {
            dir: PathBuf::from(&args.positional[0]),
            serve: args.switch("--serve"),
            port: match args.value("--port") {
                Some(text) => text.parse().map_err(|_| format!("--port: {text} is not a port number"))?,
                None => 0,
            },
        },
        "plan" => {
            let (schema, json) = (args.switch("--schema"), args.switch("--json"));
            let config = args.value("--config").map(PathBuf::from);
            if schema && (config.is_some() || json) {
                return Err("`foe plan --schema` prints the schema and takes no other option".into());
            }
            if config.is_none() && json {
                return Err("`foe plan` resolves no contract without --config FILE".into());
            }
            match schema {
                true => Command::Schema,
                false => Command::Plan { config, json },
            }
        }
        "init" => Command::Init {
            repository: args
                .value("--repository")
                .map(PathBuf::from)
                .ok_or("`foe init` takes --repository PATH; run `foe init --help`")?,
        },
        "login" => Command::Login {
            provider: args.positional.pop(),
            model: args.value("--model"),
            status: args.switch("--status"),
        },
        _ => {
            let options = run::Options {
                task: args.positional.pop(),
                config: args.value("--config").map(PathBuf::from),
                model: args.value("--model"),
                service_tier: args.value("--service-tier"),
                key_file: args.value("--key-file").map(PathBuf::from),
                verify: args.value("--verify").map(PathBuf::from),
                sandbox: args.value("--sandbox"),
                log_dir: args.value("--log-dir").map(PathBuf::from),
                no_open: args.switch("--no-open"),
                headless: args.switch("--headless"),
                conversation: args.switch("--conversation"),
                host: args.switch("--host"),
                fork: args.value("--fork").map(PathBuf::from),
                at: args.value("--at").map(|t| t.parse().map_err(|_| format!("--at: {t} is not a seq"))).transpose()?,
            };
            if options.host && options.conversation {
                return Err("--conversation cannot be combined with --host".into());
            }
            if options.fork.is_some() != options.at.is_some() {
                return Err("--fork SOURCE_DIR and --at SEQ come together; run `foe --help`".into());
            }
            if options.task.is_none() && options.config.is_none() {
                return Err("give a task or --config FILE; run `foe --help`".into());
            }
            if options.host && (options.task.is_some() || options.config.is_none()) {
                return Err("--host takes the task from --config FILE".into());
            }
            Command::Run(options)
        }
    })
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    command(&args).and_then(dispatch).unwrap_or_else(|message| {
        eprintln!("foe: {message}");
        ExitCode::from(1)
    })
}

/// Writes one page to standard output and succeeds, the shape of every form
/// whose whole result is what it printed.
fn printed(text: &str) -> Result<ExitCode, String> {
    print!("{text}");
    Ok(ExitCode::SUCCESS)
}

fn dispatch(command: Command) -> Result<ExitCode, String> {
    match command {
        Command::Help(form) => printed(&help(form)),
        Command::Schema => printed(SCHEMA),
        Command::Plan { config, json } => plan(config.as_deref(), json),
        Command::View { dir, serve, port } => view(&dir, serve, port),
        Command::Init { repository } => printed(&init::init(&repository)?),
        Command::Login { provider, model, status } => login(provider, model, status),
        Command::Telemetry { logs, json } => telemetry::preview(&logs, json).map(|()| ExitCode::SUCCESS),
        Command::Run(options) => run::run(options),
    }
}

fn login(provider: Option<String>, model: Option<String>, status: bool) -> Result<ExitCode, String> {
    login::login(login::Options { provider, model, status })
}

/// Starts the user's browser on a URL. A running form calls this before the
/// process restricts itself, because the browser would otherwise inherit
/// the restriction.
fn open_browser(url: &str) {
    let mut open = std::process::Command::new("/usr/bin/xdg-open");
    open.arg(url).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    if let Err(e) = open.spawn() {
        eprintln!("foe: /usr/bin/xdg-open: {e}; open the URL by hand");
    }
}

fn load(config: &Path) -> Result<foe_contract::document::ResolvedContract, String> {
    foe_contract::document::load(config).map_err(|e| format!("{}: {e}", config.display()))
}

/// Resolves the contract and prints a readiness summary — one line each
/// for the model, the granted read, write, and execute roots, the
/// completion mechanism, the limits, the sandbox mode, the workflow size
/// when one is declared, and the static warnings — then the detailed
/// report. Each summary line projects the same resolved objects the
/// detail prints. Without `--config`,
/// lists the built-in tools the binary carries instead, one row per tool.
/// `--json` prints one object with `contract_fingerprint`, `fingerprint_document` — the
/// canonical serialized form the fingerprint hash is computed over — and
/// `contract`, which the Python package parses, `context` with the
/// compaction policy in one line
/// when the contract compacts, and `workflow` when the contract declares one:
/// its cycles, the nodes sharing write roots, its terminal nodes, and its
/// declared edge references and firings against their fixed bounds. Both
/// forms report the resolved tools with the source each name resolved in
/// and the tools reachable from the root. Without `--json`, a
/// workflow is followed by the report docs/workflow.md "Firing" describes.
fn plan(config: Option<&Path>, json: bool) -> Result<ExitCode, String> {
    let Some(config) = config else {
        let builtins = std::iter::once(block_spec(false)).chain(run::extra_builtin_specs());
        return printed(&builtins.map(|spec| tool_row(&spec, "built-in")).collect::<String>());
    };
    let contract = load(config)?;
    let fingerprint = run::fingerprint(&contract)?;
    let value = contract.to_value();
    let model_endpoint = contract.model.as_ref().map(|_| run::describe_model_endpoint(&contract));
    let context = run::context_policy(&contract)?.map(|policy| policy.describe());
    let reachable_tools = plan::reachable_tools(&contract)?;
    let resolved_permissions = plan::resolved_permissions(&contract)?;
    let warnings = plan::configuration_warnings(&contract);
    if json {
        let overlaps = plan::write_overlaps(&contract)?;
        let workflow = contract.workflow.as_ref().map(|wf| {
            serde_json::json!({
                "cycles": plan::cycles(wf), "write_overlaps": overlaps,
                "terminal": wf.nodes.iter().filter(|(_, n)| n.terminal).map(|(k, _)| k).collect::<Vec<_>>(),
                "edge_references": wf.edge_references(),
                "max_edge_references": foe_contract::workflow::MAX_EDGE_REFERENCES,
                "possible_firings": wf.possible_firings(),
                "max_possible_firings": foe_contract::workflow::MAX_POSSIBLE_FIRINGS,
            })
        });
        let report = serde_json::json!({
            "contract_fingerprint": fingerprint.hash, "fingerprint_document": fingerprint.document, "contract": value,
            "model_endpoint": model_endpoint, "execution": plan::execution(&contract), "workflow": workflow, "context": context,
            "reachable_tools": reachable_tools, "resolved_permissions": resolved_permissions, "warnings": warnings,
        });
        println!("{report}");
    } else {
        print!("{}", plan::summary_report(&contract, model_endpoint.as_deref(), &warnings));
        println!();
        println!("fingerprint  {}", fingerprint.hash);
        println!("model     {}", model_endpoint.as_deref().unwrap_or("answered by the host over the protocol"));
        if let Some(context) = &context {
            println!("context   {context}");
        }
        println!("{}", serde_json::to_string_pretty(&value).map_err(|e| e.to_string())?);
        if contract.workflow.is_some() {
            print!("{}", plan::workflow_report(&contract)?);
        } else {
            print!("{}", plan::root_agent_report(&contract));
        }
        print!("tools\n{}", tool_rows(config, &contract)?);
        print!("{}", plan::reachable_tools_report(&reachable_tools));
        print!("{}", plan::permissions_report(&resolved_permissions));
        print!("{}", plan::warnings_report(&warnings));
    }
    Ok(ExitCode::SUCCESS)
}

/// One tool row: the name, the source its name resolved in, the effect, and
/// the first sentence of the description.
fn tool_row(spec: &foe_contract::ToolSpec, source: &str) -> String {
    let effect = serde_json::to_value(spec.effect).ok().and_then(|v| v.as_str().map(str::to_string));
    let text = &spec.description;
    let first = text[..text.find(". ").map_or(text.len(), |i| i + 1)].trim_end();
    format!("{:<10} {:<10} {:<7} {first}\n", spec.name, source, effect.unwrap_or_default())
}

/// The resolved tools of the root contract, one row each.
fn tool_rows(config: &Path, contract: &foe_contract::document::ResolvedContract) -> Result<String, String> {
    let extra = run::extra_builtin_specs();
    let specs = resolve_specs(contract, &extra).map_err(|e| format!("{}: {e}", config.display()))?;
    let sources = plan::tool_sources(contract, &extra)?;
    let named = |i: usize| match sources.get(i) {
        Some(Source::Builtin) => "built-in",
        Some(Source::Configured) => "tool_defs",
        Some(Source::Host) => "host_tools",
        None => "synthesized",
    };
    Ok(specs.iter().enumerate().map(|(i, spec)| tool_row(spec, named(i))).collect())
}

/// Writes the self-contained page to standard output, or serves the
/// directory until the process is ended. When serving, the URL is the first
/// line of standard output, which the Python package reads.
fn view(dir: &Path, serve: bool, port: u16) -> Result<ExitCode, String> {
    if !serve {
        return printed(&foe_view::export(dir).map_err(|e| e.to_string())?);
    }
    run::runtime()?.block_on(async {
        let bound = foe_view::Bound::bind(port).map_err(|e| e.to_string())?;
        println!("{}", bound.url());
        let server = bound.serve(dir).await.map_err(|e| e.to_string())?;
        server.wait().await;
        Ok(ExitCode::SUCCESS)
    })
}

#[cfg(test)]
#[path = "main_test.rs"]
mod tests;
