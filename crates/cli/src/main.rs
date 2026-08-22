//! The `foe` binary. docs/design.md "The command line" states the forms.
//! This file parses the command line and implements the forms that run
//! nothing: `view`, `plan`, `tools`, and `schema`. The running form is in
//! `run.rs` and `login` in `login.rs`.

#![forbid(unsafe_code)]

#[cfg(feature = "transport")]
mod login;
mod plan;
mod run;

use foe_core::registry::{block_spec, resolve_specs, Source};
use foe_core::ToolSpec;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

/// The JSON Schema of the configuration document, maintained by hand to
/// mirror docs/config.md.
const SCHEMA: &str = include_str!("schema.json");

const USAGE: &str = "usage:
  foe \"task\" [--config FILE] [--model PROVIDER/MODEL] [--key-file PATH] [--log-dir DIR] [--no-open] [--headless]
  foe --config FILE --host [--log-dir DIR]
  foe login [PROVIDER [--model MODEL]] [--status]
  foe view DIR [--serve [--port N]]
  foe plan --config FILE [--json]
  foe tools [--config FILE]
  foe schema";

/// Options that take a value.
const VALUED: &[&str] = &["--config", "--model", "--key-file", "--log-dir", "--port"];
/// Options that take none.
const SWITCHES: &[&str] = &["--no-open", "--headless", "--host", "--serve", "--json", "--status"];

#[derive(Default)]
struct Args {
    positional: Vec<String>,
    options: BTreeMap<String, String>,
}

impl Args {
    fn parse(args: &[String]) -> Result<Args, String> {
        let mut out = Args::default();
        let mut rest = args.iter();
        while let Some(arg) = rest.next() {
            let name = arg.as_str();
            if VALUED.contains(&name) {
                let value = rest.next().ok_or_else(|| format!("{name} takes a value\n{USAGE}"))?;
                out.options.insert(arg.clone(), value.clone());
            } else if SWITCHES.contains(&name) {
                out.options.insert(arg.clone(), String::new());
            } else if name.starts_with("--") {
                return Err(format!("unknown option {name}\n{USAGE}"));
            } else {
                out.positional.push(arg.clone());
            }
        }
        Ok(out)
    }

    fn value(&mut self, name: &str) -> Option<String> {
        self.options.remove(name)
    }

    fn switch(&mut self, name: &str) -> bool {
        self.options.remove(name).is_some()
    }

    /// Fails when an option the form does not accept was given.
    fn finish(self, form: &str) -> Result<(), String> {
        match self.options.keys().next() {
            Some(name) => Err(format!("{name} does not apply to `foe {form}`\n{USAGE}")),
            None => Ok(()),
        }
    }
}

enum Command {
    Run(run::Options),
    Login { provider: Option<String>, model: Option<String>, status: bool },
    View { dir: PathBuf, serve: bool, port: u16 },
    Plan { config: PathBuf, json: bool },
    Tools { config: Option<PathBuf> },
    Schema,
}

fn command(args: &[String]) -> Result<Command, String> {
    let mut args = Args::parse(args)?;
    let form = args.positional.first().cloned().unwrap_or_default();
    let command = match form.as_str() {
        "schema" => Command::Schema,
        "view" => {
            let dir =
                args.positional.get(1).cloned().ok_or_else(|| format!("`foe view` takes a directory\n{USAGE}"))?;
            let port = match args.value("--port") {
                Some(text) => text.parse().map_err(|_| format!("--port: {text} is not a port number"))?,
                None => 0,
            };
            Command::View { dir: PathBuf::from(dir), serve: args.switch("--serve"), port }
        }
        "plan" => {
            let config = args.value("--config").ok_or_else(|| format!("`foe plan` takes --config FILE\n{USAGE}"))?;
            Command::Plan { config: PathBuf::from(config), json: args.switch("--json") }
        }
        "tools" => Command::Tools { config: args.value("--config").map(PathBuf::from) },
        "login" => Command::Login {
            provider: args.positional.get(1).cloned(),
            model: args.value("--model"),
            status: args.switch("--status"),
        },
        _ => {
            if args.positional.len() > 1 {
                return Err(format!("one task at most\n{USAGE}"));
            }
            let options = run::Options {
                task: args.positional.pop(),
                config: args.value("--config").map(PathBuf::from),
                model: args.value("--model"),
                key_file: args.value("--key-file").map(PathBuf::from),
                log_dir: args.value("--log-dir").map(PathBuf::from),
                no_open: args.switch("--no-open"),
                headless: args.switch("--headless"),
                host: args.switch("--host"),
            };
            if options.task.is_none() && options.config.is_none() {
                return Err(USAGE.to_string());
            }
            if options.host && (options.task.is_some() || options.config.is_none()) {
                return Err(format!("--host takes the task from --config FILE\n{USAGE}"));
            }
            Command::Run(options)
        }
    };
    let leftover = match &command {
        Command::Run(_) => args.positional.is_empty(),
        Command::Schema | Command::Tools { .. } | Command::Plan { .. } => args.positional.len() == 1,
        Command::Login { .. } => args.positional.len() <= 2,
        Command::View { .. } => args.positional.len() == 2,
    };
    if !leftover {
        return Err(format!("unexpected argument\n{USAGE}"));
    }
    args.finish(&form)?;
    Ok(command)
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match command(&args).and_then(dispatch) {
        Ok(code) => code,
        Err(message) => {
            eprintln!("foe: {message}");
            ExitCode::from(1)
        }
    }
}

fn dispatch(command: Command) -> Result<ExitCode, String> {
    match command {
        Command::Schema => {
            print!("{SCHEMA}");
            Ok(ExitCode::SUCCESS)
        }
        Command::Plan { config, json } => plan(&config, json),
        Command::Tools { config } => tools(config.as_deref()),
        Command::View { dir, serve, port } => view(&dir, serve, port),
        Command::Login { provider, model, status } => login(provider, model, status),
        Command::Run(options) => run::run(options),
    }
}

#[cfg(feature = "transport")]
fn login(provider: Option<String>, model: Option<String>, status: bool) -> Result<ExitCode, String> {
    login::login(login::Options { provider, model, status })
}

#[cfg(not(feature = "transport"))]
fn login(_provider: Option<String>, _model: Option<String>, _status: bool) -> Result<ExitCode, String> {
    Err("this binary was built without the transport feature; there is no provider to log in to".into())
}

/// Starts the user's browser on a URL. A running form calls this before the
/// process restricts itself, because the browser would otherwise inherit
/// the restriction.
fn open_browser(url: &str) {
    let started = std::process::Command::new("/usr/bin/xdg-open")
        .arg(url)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn();
    if let Err(e) = started {
        eprintln!("foe: /usr/bin/xdg-open: {e}; open the URL by hand");
    }
}

fn load(config: &Path) -> Result<foe_core::config::Program, String> {
    foe_core::config::load(config).map_err(|e| format!("{}: {e}", config.display()))
}

/// Resolves the program and prints it with its identity. `--json` prints one
/// object with `identity` and `program`, which the Python package parses,
/// `context` with the compaction policy in one line when the program
/// compacts, and `workflow` when the program declares one: its cycles, the
/// nodes sharing write roots, its terminal nodes, and how many firings it
/// can perform against the bound the runtime enforces. Both forms report the
/// tool authority reachable from the root. Without `--json`, a workflow is
/// followed by the report docs/workflow.md "Firing" describes.
fn plan(config: &Path, json: bool) -> Result<ExitCode, String> {
    let program = load(config)?;
    let identity = run::identity(&program)?;
    let value = program.to_value();
    let transport = program.model.as_ref().map(run::describe_transport);
    let context = run::context_policy(&program)?.map(|policy| policy.describe());
    let authority = plan::authority(&program)?;
    if json {
        let workflow = match &program.workflow {
            None => None,
            Some(wf) => {
                let terminal: Vec<&String> = wf.nodes.iter().filter(|(_, n)| n.terminal).map(|(k, _)| k).collect();
                let overlaps = plan::write_overlaps(&program)?;
                Some(serde_json::json!({
                    "cycles": plan::cycles(wf), "write_overlaps": overlaps, "terminal": terminal,
                    "possible_firings": wf.possible_firings(),
                    "max_possible_firings": foe_core::workflow::MAX_POSSIBLE_FIRINGS,
                }))
            }
        };
        let report = serde_json::json!({
            "identity": identity.hash, "program": value, "transport": transport, "workflow": workflow,
            "context": context, "authority": authority,
        });
        println!("{report}");
    } else {
        println!("identity  {}", identity.hash);
        println!("model     {}", transport.as_deref().unwrap_or("answered by the host over the protocol"));
        if let Some(context) = &context {
            println!("context   {context}");
        }
        println!("{}", serde_json::to_string_pretty(&value).map_err(|e| e.to_string())?);
        if program.workflow.is_some() {
            print!("{}", plan::workflow_report(&program)?);
        }
        print!("{}", plan::authority_report(&authority));
    }
    Ok(ExitCode::SUCCESS)
}

/// Lists the built-in tools, or the resolved tools of a document with the
/// source each name resolved in.
fn tools(config: Option<&Path>) -> Result<ExitCode, String> {
    let row = |spec: &ToolSpec, source: &str| {
        let effect = serde_json::to_value(spec.effect).ok().and_then(|v| v.as_str().map(str::to_string));
        println!(
            "{:<10} {:<10} {:<7} {}",
            spec.name,
            source,
            effect.unwrap_or_default(),
            first_sentence(&spec.description)
        );
    };
    let Some(config) = config else {
        for spec in std::iter::once(block_spec()).chain(run::extra_builtin_specs()) {
            row(&spec, "built-in");
        }
        return Ok(ExitCode::SUCCESS);
    };
    let program = load(config)?;
    let extra = run::extra_builtin_specs();
    let specs = resolve_specs(&program, &extra).map_err(|e| format!("{}: {e}", config.display()))?;
    let sources = plan::tool_sources(&program, &extra)?;
    for (i, spec) in specs.iter().enumerate() {
        let source = match sources.get(i) {
            Some(Source::Builtin) => "built-in",
            Some(Source::Configured) => "tool_defs",
            Some(Source::Host) => "host_tools",
            None => "synthesized",
        };
        row(spec, source);
    }
    Ok(ExitCode::SUCCESS)
}

fn first_sentence(text: &str) -> &str {
    let end = text.find(". ").map_or(text.len(), |i| i + 1);
    text[..end].trim_end()
}

/// Writes the self-contained page to standard output, or serves the
/// directory until the process is ended. When serving, the URL is the first
/// line of standard output, which the Python package reads.
fn view(dir: &Path, serve: bool, port: u16) -> Result<ExitCode, String> {
    if !serve {
        let html = foe_view::export(dir).map_err(|e| e.to_string())?;
        print!("{html}");
        return Ok(ExitCode::SUCCESS);
    }
    let runtime = run::runtime()?;
    runtime.block_on(async {
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
