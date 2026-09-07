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
/// the heading its help screen lists the option under, the flag, the value
/// placeholder, empty for a switch, what applies when the option is absent,
/// and what it does. `absent` is the literal `required` when the form
/// refuses to run without the option, otherwise the default in words, or
/// empty when a switch that is simply off has nothing to say. An empty
/// `group` lists the option above every heading.
struct Opt {
    command: Text,
    group: Text,
    flag: Text,
    value: Text,
    absent: Text,
    meaning: Text,
}

const fn opt(command: Text, group: Text, flag: Text, value: Text, absent: Text, meaning: Text) -> Opt {
    Opt { command, group, flag, value, absent, meaning }
}

/// The headings the running form's help lists its options under, in order.
/// Each says what the options beneath it decide, so a reader looking for one
/// decision reads one group. Every running option names exactly one, except
/// `--host`, which selects a different way to run rather than adjusting a
/// run and is listed above them all.
const WHAT_RUNS: Text = "what runs";
const BUILT_IN_ONLY: Text = "built-in documents only";
const THE_MODEL: Text = "the model, when the document names none";
const WATCHING: Text = "how you watch it";
const GROUPS: &[Text] = &[WHAT_RUNS, BUILT_IN_ONLY, THE_MODEL, WATCHING];

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
    opt(
        "",
        WHAT_RUNS,
        "--config",
        "FILE",
        ".foe/contract.json in the working directory, else builtin:coding",
        "the contract document to run: a file, builtin:coding, the careful default that implements and then \
         assesses, or builtin:single, the plain form of one implementation episode",
    ),
    opt("", WHAT_RUNS, "--log-dir", "DIR", ".foe", "the directory this episode's own directory is created under"),
    opt(
        "",
        WHAT_RUNS,
        "--from",
        "DIR[@SEQ]",
        "a fresh episode",
        "continue the episode logged in DIR, or with @SEQ fork it at that seq into a new episode",
    ),
    opt(
        "",
        BUILT_IN_ONLY,
        "--verify",
        "PATH",
        "a built-in document has no verifier gate",
        "an executable verifier whose acceptance completes the built-in document",
    ),
    opt(
        "",
        BUILT_IN_ONLY,
        "--sandbox",
        "MODE",
        "best-effort",
        "kernel confinement mode: best-effort, required, or off",
    ),
    opt("", THE_MODEL, "--model", "PROVIDER/MODEL", "the default model `foe login` wrote", "the model that answers"),
    opt(
        "",
        THE_MODEL,
        "--service-tier",
        "TIER",
        "the model configuration's value",
        "the provider's request service tier; docs/models.md lists the values each provider accepts",
    ),
    opt(
        "",
        WATCHING,
        "--viewer",
        "MODE",
        "open, and off under --host",
        "the browser viewer: open a browser on it, serve without opening one, or off",
    ),
    opt(
        "",
        WATCHING,
        "--conversation",
        "",
        "",
        "show the conversation and execution tree on standard output; --viewer applies as given",
    ),
    opt(
        "",
        "",
        "--host",
        "",
        "",
        "answer model requests over the protocol on standard input; --config carries the task",
    ),
    opt("login", "", "--model", "MODEL", "chosen from the provider's list", "the default model to record"),
    opt(
        "login",
        "",
        "--key-file",
        "PATH",
        "the key this command asks for, written under ~/.config/foe/credentials/",
        "record this file as the provider's credential, asking nothing",
    ),
    opt(
        "init",
        "",
        "--repository",
        "PATH",
        "required",
        "the repository to write .foe/contract.json and .foe/verify for",
    ),
    opt("login", "", "--status", "", "", "print the default model and every configured credential path"),
    opt("view", "", "--serve", "", "", "serve the directory instead of writing the page to standard output"),
    opt("view", "", "--port", "N", "an ephemeral port, printed as the first line", "the port to serve on"),
    opt(
        "plan",
        "",
        "--config",
        "FILE",
        "the built-in tools alone",
        "the contract document to resolve: a file, builtin:coding, or builtin:single",
    ),
    opt("plan", "", "--json", "", "", "print one JSON object instead of the report"),
    opt("plan", "", "--schema", "", "", "print the JSON Schema of the contract document and nothing else"),
    opt("telemetry", "", "--json", "", "", "print that payload as JSON instead of a summary"),
];

/// The `--help` row, accepted by every form and listed in every help screen.
const HELP: Opt = opt("*", "", "--help", "", "", "print this help and exit");

/// Spellings the running form does not accept, each with the option that
/// says the same thing. A command line using one is refused by its own name,
/// because what it asked for is still available under another spelling.
const RETIRED: &[(Text, Text)] = &[
    ("--fork", "write --from DIR@SEQ instead"),
    ("--at", "write --from DIR@SEQ instead"),
    ("--no-open", "write --viewer serve instead"),
    ("--headless", "write --viewer off instead"),
    (
        "--key-file",
        "record the credential once with `foe login PROVIDER --key-file PATH`, or name it in the document's \
         `model` block",
    ),
];

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
            let retired = RETIRED.iter().find(|(flag, _)| form.name.is_empty() && *flag == arg.as_str());
            if let Some((_, advice)) = retired {
                return Err(format!("`{it}` no longer takes {arg}; {advice}"));
            }
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

/// One option's line in a help screen: the flag with its value placeholder,
/// what the option does, and what applies when it is absent.
fn row(o: &Opt) -> String {
    let flag = format!("{} {}", o.flag, o.value);
    let absent = match o.absent {
        "" => String::new(),
        "required" => " (required)".to_string(),
        text => format!(" (default: {text})"),
    };
    format!("  {:<24}{}{}\n", flag.trim_end(), o.meaning, absent)
}

/// `foe <command> --help`: the usage line, what the form does, and every
/// option with its meaning and what applies when it is absent, listed under
/// the heading its row names. For the running form this is `foe --help`,
/// which adds the other command words, because a bare `foe` is the running
/// form.
fn help(form: &'static Form) -> String {
    let mut line = format!("usage: foe {} {}", form.name, form.args);
    for o in OPTS.iter().filter(|o| o.command == form.name) {
        let flag = format!("{} {}", o.flag, o.value);
        let flag = flag.trim_end();
        write!(line, " {}", if o.absent == "required" { flag.to_string() } else { format!("[{flag}]") }).ok();
    }
    let line = line.split_whitespace().collect::<Vec<&str>>().join(" ");
    let mut out = format!("{line}\n\n{}\n\noptions:\n", form.about);
    for o in accepted(form).filter(|o| o.group.is_empty()) {
        out.push_str(&row(o));
    }
    for group in GROUPS.iter().filter(|group| accepted(form).any(|o| o.group == **group)) {
        writeln!(out, "\n{group}:").ok();
        for o in accepted(form).filter(|o| o.group == *group) {
            out.push_str(&row(o));
        }
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
    Login(login::Options),
    View { dir: PathBuf, serve: bool, port: u16 },
    Plan { config: Option<String>, json: bool },
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
            let config = args.value("--config");
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
        "login" => Command::Login(login::Options {
            provider: args.positional.pop(),
            model: args.value("--model"),
            key_file: args.value("--key-file").map(PathBuf::from),
            status: args.switch("--status"),
        }),
        _ => {
            // `--from DIR@SEQ` names a boundary; a value whose last `@` is
            // followed by anything else is a path in full.
            let boundary = |text: &str| text.rsplit_once('@').and_then(|(d, n)| Some((d.to_string(), n.parse().ok()?)));
            let (from, at) = match args.value("--from") {
                Some(text) => match boundary(&text) {
                    Some((dir, at)) => (Some(PathBuf::from(dir)), Some(at)),
                    None => (Some(PathBuf::from(text)), None),
                },
                None => (None, None),
            };
            // `--host` gives standard output to the log, so it runs with no
            // viewer whatever the command line named. A named value is still
            // read, so a value none of the three matches is reported.
            let host = args.switch("--host");
            let named_viewer = args.value("--viewer").map(|value| run::Viewer::parse(&value)).transpose()?;
            let options = run::Options {
                task: args.positional.pop(),
                config: args.value("--config"),
                model: args.value("--model"),
                service_tier: args.value("--service-tier"),
                verify: args.value("--verify").map(PathBuf::from),
                sandbox: args.value("--sandbox"),
                log_dir: args.value("--log-dir").map(PathBuf::from),
                viewer: match host {
                    true => run::Viewer::Off,
                    false => named_viewer.unwrap_or_default(),
                },
                conversation: args.switch("--conversation"),
                host,
                from,
                at,
            };
            if options.host && options.conversation {
                return Err("--conversation cannot be combined with --host".into());
            }
            if options.task.is_none() && options.config.is_none() && options.from.is_none() {
                return Err("give a task, --config FILE, or --from DIR; run `foe --help`".into());
            }
            if options.host && (options.task.is_some() || options.config.is_none()) {
                return Err("--host takes the task from --config FILE".into());
            }
            if options.host && options.config.as_deref().is_some_and(|c| c.starts_with(run::BUILTIN_PREFIX)) {
                return Err("--host takes the task from a document file; a built-in name carries no task".into());
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
        Command::Plan { config, json } => plan(config, json),
        Command::View { dir, serve, port } => view(&dir, serve, port),
        Command::Init { repository } => printed(&init::init(&repository)?),
        Command::Login(options) => login::login(options),
        Command::Telemetry { logs, json } => telemetry::preview(&logs, json).map(|()| ExitCode::SUCCESS),
        Command::Run(options) => run::run(options),
    }
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
/// `--config` names a file or a built-in document, and a built-in document
/// resolves here exactly as it resolves for a run of the same name.
fn plan(config: Option<String>, json: bool) -> Result<ExitCode, String> {
    let Some(config) = config else {
        let builtins = std::iter::once(block_spec(false)).chain(run::extra_builtin_specs());
        return printed(&builtins.map(|spec| tool_row(&spec, "built-in")).collect::<String>());
    };
    let source = run::contract_source(&config)?;
    let named = source.describe();
    let contract = match &source {
        run::ContractSource::Builtin(name) => {
            let document = run::builtin_plan_document(name)?;
            foe_contract::document::resolve(&document).map_err(|e| format!("{named}: {e}"))?
        }
        run::ContractSource::File(path) => load(path)?,
    };
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
        print!("tools\n{}", tool_rows(&named, &contract)?);
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

/// The resolved tools of the root contract, one row each. `source` is what
/// `--config` named, which an error repeats.
fn tool_rows(source: &str, contract: &foe_contract::document::ResolvedContract) -> Result<String, String> {
    let extra = run::extra_builtin_specs();
    let specs = resolve_specs(contract, &extra).map_err(|e| format!("{source}: {e}"))?;
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
