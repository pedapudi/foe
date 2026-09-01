//! `foe login`: configure a provider's credential and the default model.
//!
//! ```text
//! foe login                      list providers, with whether each is configured
//! foe login <provider>           configure it, then set the default model if none is set
//! foe login <provider> --model M set the default model explicitly
//! foe login --status             show the default model and every configured credential path
//! ```
//!
//! Everything is written under `~/.config/foe/`: one credentials file per
//! provider and `default-model.json`, the `model` block a bare `foe "task"`
//! runs. Secrets are read with echo off and never printed. Every prompt is
//! plain standard input and standard error.
//!
//! What lives here is the conversation: which questions each credential
//! source needs, in what order, and how the answers are read from a
//! terminal. The protocol underneath — verifying a key, the
//! authorization-code flow with PKCE and its loopback callback, the
//! credential-file and default-model formats — is
//! [`foe_transport::auth::login`], beside the code that spends what it
//! writes. Everything runs through a [`Session`] so that a test can script
//! the input, capture the output, and point the endpoints at a local
//! server.

use foe_contract::ModelConfig;
use foe_transport::auth::login::{self, default_model_in, write_default_model, BrowserLogin, Endpoints};
use foe_transport::auth::AuthKind;
use foe_transport::paths;
use foe_transport::providers::{Provider, PROVIDERS};
use std::collections::BTreeMap;
use std::io::{BufRead, Write};
use std::path::PathBuf;
use std::process::ExitCode;

#[derive(Debug, Default)]
pub struct Options {
    pub provider: Option<String>,
    pub model: Option<String>,
    pub status: bool,
}

/// The command the person runs next, printed after every successful login.
pub const NEXT_COMMAND: &str = "foe \"describe what this repository does\"";

/// One login conversation: where files go, where prompts and answers flow.
pub struct Session<'a> {
    pub home: PathBuf,
    pub input: &'a mut dyn BufRead,
    pub output: &'a mut dyn Write,
    /// Whether standard input is a terminal whose echo can be turned off
    /// while a secret is typed.
    pub terminal: bool,
    pub endpoints: Endpoints,
    /// Whether the browser flow starts a browser, which a test must not.
    pub open_browser: bool,
}

pub fn login(options: Options) -> Result<ExitCode, String> {
    let home = paths::home_dir()?;
    let stdin = std::io::stdin();
    let mut input = stdin.lock();
    let mut output = std::io::stderr();
    let terminal = nix::unistd::isatty(std::io::stdin()).unwrap_or(false);
    let endpoints = Endpoints::default();
    let mut session = Session { home, input: &mut input, output: &mut output, terminal, endpoints, open_browser: true };
    run(&mut session, options)
}

pub fn run(session: &mut Session, options: Options) -> Result<ExitCode, String> {
    if options.status {
        status(session)?;
        return Ok(ExitCode::SUCCESS);
    }
    let Some(name) = options.provider else {
        list(session)?;
        return Ok(ExitCode::SUCCESS);
    };
    let provider = foe_transport::provider_info(&name).ok_or_else(|| {
        format!("provider `{name}` is unknown to this build; run `foe login` to list the known providers")
    })?;
    let extra = configure(session, provider)?;
    let path = paths::default_model_path(&session.home);
    if options.model.is_some() || !path.is_file() {
        let model = match options.model {
            Some(model) => model,
            None => choose_model(session, provider)?,
        };
        let mut block = ModelConfig::new(provider.name, model);
        block.options = extra;
        crate::run::apply_builtin_model_defaults(&mut block);
        write_default_model(&session.home, &block)?;
        say(session, &format!("default model: {}/{} ({})", block.provider, block.model, path.display()))?;
    }
    say(session, "")?;
    say(session, &format!("next: {NEXT_COMMAND}"))?;
    Ok(ExitCode::SUCCESS)
}

// ---- listing and status -------------------------------------------------------

fn list(session: &mut Session) -> Result<(), String> {
    let width = PROVIDERS.iter().map(|p| p.name.len()).max().unwrap_or(0);
    for provider in PROVIDERS {
        let configured = paths::credentials_path(&session.home, provider.name).is_file();
        let state = match (provider.auth == AuthKind::None, configured) {
            (true, _) => "no login",
            (_, true) => "configured",
            _ => "not configured",
        };
        say(session, &format!("{:<width$}  {:<14}  {}", provider.name, state, provider.description))?;
    }
    say(session, "")?;
    say(session, "run `foe login <provider>` to configure one")?;
    Ok(())
}

fn status(session: &mut Session) -> Result<(), String> {
    match default_model_in(&session.home)? {
        Some(mut model) => {
            crate::run::apply_builtin_model_defaults(&mut model);
            let effort = model.option("reasoning_effort").map_or(String::new(), |v| format!(" reasoning_effort={v}"));
            say(session, &format!("default model  {}/{}{}", model.provider, model.model, effort))?;
        }
        None => say(session, "default model  none; run `foe login <provider>`")?,
    }
    for provider in PROVIDERS {
        let path = paths::credentials_path(&session.home, provider.name);
        if path.is_file() {
            say(session, &format!("{:<14} {}", provider.name, path.display()))?;
        }
    }
    Ok(())
}

// ---- configuring one provider ---------------------------------------------------

/// Configures the provider's credential and returns the options the
/// default model block needs beyond `provider` and `model`.
///
/// Every flow has one shape: gather the provider's answers, verify them
/// against the provider, and write one credential file. Only the gathering
/// and the verification differ, so the arms produce the note that names
/// what was written and share the report. The verifying and the writing
/// are `foe_transport::auth::login`; the asking is here.
fn configure(session: &mut Session, provider: &'static Provider) -> Result<BTreeMap<String, String>, String> {
    let mut extra = BTreeMap::new();
    let (path, note) = match provider.auth {
        AuthKind::ApiKey { .. } => {
            let base_url = match provider.default_base_url {
                Some(_) => session.endpoints.base_url.clone(),
                None => {
                    let prompt = format!("Server base URL ({}):", provider.hint("base_url"));
                    let url = ask_required(session, &prompt, "a base URL is required for this provider")?;
                    extra.insert("base_url".to_string(), url.clone());
                    Some(url)
                }
            };
            let key = ask_secret(session, &format!("Paste your {} API key:", provider.title))?;
            if key.is_empty() {
                return Err("no key was entered; paste the key and press return".into());
            }
            say(session, "verifying...")?;
            login::verify_api_key(provider, base_url.as_deref(), &key)
                .map_err(|e| format!("{e}; check the key and run `foe login {}` again", provider.name))?;
            (login::save_api_key(&session.home, provider, &key)?, String::new())
        }
        AuthKind::TokenFile { .. } => {
            let token = browser_login(session)?;
            let last4 = token.account_id.as_deref().map(|id| &id[id.len().saturating_sub(4)..]).unwrap_or("none");
            let note = format!(" (account ...{last4})");
            (login::save_token(&session.home, provider, &token)?, note)
        }
        AuthKind::Google => {
            let default = session.home.join(login::GCLOUD_DEFAULT);
            let answer = ask(session, &format!("Google credentials file [{}]:", default.display()))?;
            let file = if answer.is_empty() { default } else { PathBuf::from(answer) };
            if !file.is_absolute() {
                return Err(format!("{}: give an absolute path", file.display()));
            }
            let project = ask_required(session, "Google Cloud project id:", "a project id is required")?;
            let prompt = "Location (for example us-east5 or global):";
            let location = ask_required(session, prompt, "a location is required")?;
            say(session, "verifying...")?;
            let google = foe_transport::auth::google::Google::open(&file).map_err(|e| {
                format!("{e}; run `gcloud auth application-default login` or name a service account key file")
            })?;
            google.token().map_err(|e| format!("could not mint an access token: {e}"))?;
            let note = format!(" ({} credentials)", google.credentials().kind());
            (login::save_google(&session.home, provider, &file, &project, &location)?, note)
        }
        AuthKind::None => {
            return Err(format!(
                "{} needs no login; put `\"provider\": \"{}\"` and its options in the model block, see docs/models.md",
                provider.name, provider.name
            ));
        }
    };
    say(session, &format!("wrote {}{note}", path.display()))?;
    Ok(extra)
}

/// Shows the sign-in URL, starts a browser on it unless told not to, and
/// waits for the authorization server to come back to the loopback
/// listener the flow bound.
fn browser_login(session: &mut Session) -> Result<foe_transport::auth::token_file::Token, String> {
    let flow = BrowserLogin::begin(&session.endpoints)?;
    say(session, "Open this URL in your browser to sign in:")?;
    say(session, &flow.url)?;
    if session.open_browser {
        crate::open_browser(&flow.url);
    }
    say(session, &format!("waiting for the browser to return to {} ...", flow.redirect_uri))?;
    flow.finish()
}

// ---- the default model ----------------------------------------------------------

/// Refused when the model name is asked for and the answer is empty.
const MODEL_REQUIRED: &str = "a model name is required";

fn choose_model(session: &mut Session, provider: &Provider) -> Result<String, String> {
    if provider.presets.is_empty() {
        return ask_required(session, "Model name:", MODEL_REQUIRED);
    }
    say(session, "Default model:")?;
    for (i, preset) in provider.presets.iter().enumerate() {
        say(session, &format!("  {}. {preset}", i + 1))?;
    }
    say(session, &format!("  {}. another name", provider.presets.len() + 1))?;
    let answer = ask(session, &format!("Choose [1-{}]:", provider.presets.len() + 1))?;
    match answer.parse::<usize>() {
        Ok(n) if n >= 1 && n <= provider.presets.len() => Ok(provider.presets[n - 1].to_string()),
        Ok(n) if n == provider.presets.len() + 1 => ask_required(session, "Model name:", MODEL_REQUIRED),
        _ if !answer.is_empty() && answer.parse::<usize>().is_err() => Ok(answer),
        _ => Err(format!("choose a number from 1 to {}", provider.presets.len() + 1)),
    }
}

// ---- prompts ----------------------------------------------------------------------

fn say(session: &mut Session, text: &str) -> Result<(), String> {
    writeln!(session.output, "{text}").map_err(|e| format!("writing to standard error: {e}"))
}

fn ask(session: &mut Session, prompt: &str) -> Result<String, String> {
    write!(session.output, "{prompt} ").and_then(|_| session.output.flush()).map_err(|e| e.to_string())?;
    let mut line = String::new();
    let n = session.input.read_line(&mut line).map_err(|e| format!("reading standard input: {e}"))?;
    if n == 0 {
        return Err("standard input ended before the answer".into());
    }
    Ok(line.trim().to_string())
}

/// Asks for a value the flow cannot proceed without. An empty answer is
/// refused with `missing`, which says what the value is for.
fn ask_required(session: &mut Session, prompt: &str, missing: &str) -> Result<String, String> {
    match ask(session, prompt)? {
        answer if answer.is_empty() => Err(missing.to_string()),
        answer => Ok(answer),
    }
}

/// Reads one line with the terminal's echo off, so the secret is not shown
/// or left in scrollback.
fn ask_secret(session: &mut Session, prompt: &str) -> Result<String, String> {
    if !session.terminal {
        return ask(session, prompt);
    }
    let stdin = std::io::stdin();
    let saved = nix::sys::termios::tcgetattr(&stdin).map_err(|e| format!("terminal attributes: {e}"))?;
    let mut quiet = saved.clone();
    quiet.local_flags.remove(nix::sys::termios::LocalFlags::ECHO);
    nix::sys::termios::tcsetattr(&stdin, nix::sys::termios::SetArg::TCSANOW, &quiet)
        .map_err(|e| format!("terminal attributes: {e}"))?;
    let answer = ask(session, prompt);
    let _ = nix::sys::termios::tcsetattr(&stdin, nix::sys::termios::SetArg::TCSANOW, &saved);
    let _ = writeln!(session.output);
    answer
}

#[cfg(test)]
#[path = "login_test.rs"]
mod tests;
