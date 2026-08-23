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
//! The flows are one function per credential source, all driven through a
//! [`Session`] so that a test can script the input, capture the output,
//! and point the verification and token endpoints at a local server.

use foe_core::ModelConfig;
use foe_transport::auth::{self, AuthKind};
use foe_transport::paths;
use foe_transport::providers::{Provider, PROVIDERS};
use std::io::{BufRead, BufReader, Write};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

#[derive(Debug, Default)]
pub struct Options {
    pub provider: Option<String>,
    pub model: Option<String>,
    pub status: bool,
}

/// The command the person runs next, printed after every successful login.
pub const NEXT_COMMAND: &str = "foe \"describe what this repository does\"";

/// Where `gcloud auth application-default login` writes its file, offered
/// as the default for Vertex AI.
const GCLOUD_DEFAULT: &str = ".config/gcloud/application_default_credentials.json";

/// Endpoints and ports that tests redirect to local servers.
pub struct Endpoints {
    /// Replaces a provider's default base URL for verification.
    pub base_url: Option<String>,
    pub authorize_url: String,
    pub token_url: String,
    /// The loopback port for the OAuth callback; 0 takes an ephemeral port.
    pub callback_port: u16,
    pub open_browser: bool,
}

impl Default for Endpoints {
    fn default() -> Self {
        Endpoints {
            base_url: None,
            authorize_url: auth::token_file::codex::AUTHORIZE_URL.to_string(),
            token_url: auth::token_file::codex::TOKEN_URL.to_string(),
            callback_port: auth::token_file::codex::REDIRECT_PORT,
            open_browser: true,
        }
    }
}

/// One login conversation: where files go, where prompts and answers flow.
pub struct Session<'a> {
    pub home: PathBuf,
    pub input: &'a mut dyn BufRead,
    pub output: &'a mut dyn Write,
    /// Whether standard input is a terminal whose echo can be turned off
    /// while a secret is typed.
    pub terminal: bool,
    pub endpoints: Endpoints,
}

pub fn login(options: Options) -> Result<ExitCode, String> {
    let home = paths::home_dir()?;
    let stdin = std::io::stdin();
    let mut input = stdin.lock();
    let mut output = std::io::stderr();
    let terminal = nix::unistd::isatty(std::io::stdin()).unwrap_or(false);
    let mut session =
        Session { home, input: &mut input, output: &mut output, terminal, endpoints: Endpoints::default() };
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
        let state = if provider.auth == AuthKind::None {
            "no login"
        } else if paths::credentials_path(&session.home, provider.name).is_file() {
            "configured"
        } else {
            "not configured"
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
fn configure(
    session: &mut Session,
    provider: &'static Provider,
) -> Result<std::collections::BTreeMap<String, String>, String> {
    let mut extra = std::collections::BTreeMap::new();
    match provider.auth {
        AuthKind::ApiKey { header } => {
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
            let credential = auth::api_key::ApiKey::new(header, key.clone());
            say(session, "verifying...")?;
            foe_transport::verify_credential(provider, base_url.as_deref(), &credential)
                .map_err(|e| format!("{e}; check the key and run `foe login {}` again", provider.name))?;
            let path = paths::credentials_path(&session.home, provider.name);
            auth::api_key::write_api_key(&path, &key).map_err(|e| format!("{}: {e}", path.display()))?;
            say(session, &format!("wrote {}", path.display()))?;
        }
        AuthKind::TokenFile { .. } => {
            let token = browser_login(session)?;
            let path = paths::credentials_path(&session.home, provider.name);
            auth::token_file::write_token(&path, &token).map_err(|e| format!("{}: {e}", path.display()))?;
            let last4 = token.account_id.as_deref().map(|id| &id[id.len().saturating_sub(4)..]).unwrap_or("none");
            say(session, &format!("wrote {} (account ...{last4})", path.display()))?;
        }
        AuthKind::Google => {
            let default = session.home.join(GCLOUD_DEFAULT);
            let answer = ask(session, &format!("Google credentials file [{}]:", default.display()))?;
            let file = if answer.is_empty() { default } else { PathBuf::from(answer) };
            if !file.is_absolute() {
                return Err(format!("{}: give an absolute path", file.display()));
            }
            let project = ask_required(session, "Google Cloud project id:", "a project id is required")?;
            let prompt = "Location (for example us-east5 or global):";
            let location = ask_required(session, prompt, "a location is required")?;
            say(session, "verifying...")?;
            let google = auth::google::Google::open(&file).map_err(|e| {
                format!("{e}; run `gcloud auth application-default login` or name a service account key file")
            })?;
            google.token().map_err(|e| format!("could not mint an access token: {e}"))?;
            let path = paths::credentials_path(&session.home, provider.name);
            let json = serde_json::json!({ "credentials_file": file, "project": project, "location": location });
            paths::write_private(&path, format!("{}\n", serde_json::to_string_pretty(&json).unwrap()).as_bytes())
                .map_err(|e| format!("{}: {e}", path.display()))?;
            say(session, &format!("wrote {} ({} credentials)", path.display(), google.credentials().kind()))?;
        }
        AuthKind::None => {
            return Err(format!(
                "{} needs no login; put `\"provider\": \"{}\"` and its options in the model block, see docs/models.md",
                provider.name, provider.name
            ));
        }
    }
    Ok(extra)
}

/// The authorization-code flow with PKCE for the ChatGPT Codex client:
/// a loopback listener receives the code, which is exchanged for a token.
fn browser_login(session: &mut Session) -> Result<auth::token_file::Token, String> {
    use auth::token_file::codex;
    let port = session.endpoints.callback_port;
    let listener = TcpListener::bind(("127.0.0.1", port)).map_err(|e| {
        format!("cannot listen on 127.0.0.1:{port} for the login callback: {e}; stop the program using that port")
    })?;
    let port = listener.local_addr().map_err(|e| e.to_string())?.port();
    let redirect_uri = codex::redirect_uri(port);
    let pkce = codex::pkce().map_err(|e| format!("/dev/urandom: {e}"))?;
    let state = codex::state().map_err(|e| format!("/dev/urandom: {e}"))?;
    let url = codex::authorization_url(&session.endpoints.authorize_url, &pkce.challenge, &state, &redirect_uri);
    say(session, "Open this URL in your browser to sign in:")?;
    say(session, &url)?;
    if session.endpoints.open_browser {
        crate::open_browser(&url);
    }
    say(session, &format!("waiting for the browser to return to {redirect_uri} ..."))?;
    let code = wait_for_code(&listener, &state)?;
    let client = auth::token_file::OAuthClient {
        token_url: session.endpoints.token_url.clone(),
        client_id: codex::CLIENT_ID.into(),
    };
    client
        .exchange_code(&code, &pkce.verifier, &redirect_uri)
        .map_err(|e| format!("token exchange failed: {e}; run `foe login openai-codex` again"))
}

/// Serves callback requests until one carries a code with the expected
/// state. Any other request receives an error page and the wait goes on.
fn wait_for_code(listener: &TcpListener, state: &str) -> Result<String, String> {
    loop {
        let (mut stream, _) = listener.accept().map_err(|e| format!("callback listener: {e}"))?;
        let mut reader = BufReader::new(stream.try_clone().map_err(|e| e.to_string())?);
        let mut line = String::new();
        if reader.read_line(&mut line).is_err() {
            continue;
        }
        let target = line.split_whitespace().nth(1).unwrap_or("");
        let (path, query) = target.split_once('?').unwrap_or((target, ""));
        let param = |name: &str| {
            query.split('&').find_map(|pair| {
                let (k, v) = pair.split_once('=')?;
                (k == name).then(|| percent_decode(v))
            })
        };
        let outcome = if path != auth::token_file::codex::REDIRECT_PATH {
            Err("not the callback path")
        } else if param("state").as_deref() != Some(state) {
            Err("state mismatch; start the login again")
        } else if let Some(error) = param("error") {
            return Err(format!("the authorization server returned {error}"));
        } else {
            param("code").filter(|c| !c.is_empty()).ok_or("no code in the callback")
        };
        let (status, text) = match &outcome {
            Ok(_) => ("200 OK", "foe is signed in. You can close this window."),
            Err(reason) => ("400 Bad Request", *reason),
        };
        let body = format!("<!doctype html><title>foe</title><p>{text}</p>");
        let _ = write!(stream, "HTTP/1.1 {status}\r\ncontent-type: text/html; charset=utf-8\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}", body.len());
        let _ = stream.flush();
        if let Ok(code) = outcome {
            return Ok(code);
        }
    }
}

/// Decodes one form-encoded query value: `%` and two hexadecimal digits
/// become that byte, `+` becomes a space. A `%` that no pair of hexadecimal
/// digits follows stands for itself.
fn percent_decode(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        let escape = (bytes[i] == b'%' && i + 2 < bytes.len())
            .then(|| u8::from_str_radix(&text[i + 1..i + 3], 16).ok())
            .flatten();
        out.push(escape.unwrap_or(if bytes[i] == b'+' { b' ' } else { bytes[i] }));
        i += if escape.is_some() { 3 } else { 1 };
    }
    String::from_utf8_lossy(&out).into_owned()
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

/// The `model` block a bare `foe "task"` runs, from the home directory of
/// the passwd database.
pub fn default_model() -> Result<Option<ModelConfig>, String> {
    default_model_in(&paths::home_dir()?)
}

pub fn default_model_in(home: &Path) -> Result<Option<ModelConfig>, String> {
    let path = paths::default_model_path(home);
    let text = match std::fs::read_to_string(&path) {
        Ok(text) => text,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(format!("{}: {e}", path.display())),
    };
    let model: ModelConfig = serde_json::from_str(&text).map_err(|e| format!("{}: {e}", path.display()))?;
    if model.provider.trim().is_empty() || model.model.trim().is_empty() {
        return Err(format!("{}: provider and model are both required", path.display()));
    }
    Ok(Some(model))
}

pub fn write_default_model(home: &Path, model: &ModelConfig) -> Result<(), String> {
    let path = paths::default_model_path(home);
    let text = serde_json::to_string_pretty(model).map_err(|e| e.to_string())?;
    paths::write_private(&path, format!("{text}\n").as_bytes()).map_err(|e| format!("{}: {e}", path.display()))
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

/// Reads the rest of a stream for tests that drive a callback by hand.
#[cfg(test)]
fn drain(mut stream: impl std::io::Read) -> String {
    let mut text = String::new();
    let _ = stream.read_to_string(&mut text);
    text
}

#[cfg(test)]
#[path = "login_test.rs"]
mod tests;
