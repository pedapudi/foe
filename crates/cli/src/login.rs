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
        Some(model) => say(session, &format!("default model  {}/{}", model.provider, model.model))?,
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
                    let hint = provider.hint("base_url");
                    let url = ask(session, &format!("Server base URL ({hint}):"))?;
                    if url.is_empty() {
                        return Err("a base URL is required for this provider".into());
                    }
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
            let project = ask(session, "Google Cloud project id:")?;
            if project.is_empty() {
                return Err("a project id is required".into());
            }
            let location = ask(session, "Location (for example us-east5 or global):")?;
            if location.is_empty() {
                return Err("a location is required".into());
            }
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
        open_browser(&url);
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

fn percent_decode(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'%' if i + 2 < bytes.len() => match u8::from_str_radix(&text[i + 1..i + 3], 16) {
                Ok(byte) => {
                    out.push(byte);
                    i += 3;
                }
                Err(_) => {
                    out.push(b'%');
                    i += 1;
                }
            },
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            byte => {
                out.push(byte);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

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

// ---- the default model ----------------------------------------------------------

fn choose_model(session: &mut Session, provider: &Provider) -> Result<String, String> {
    if provider.presets.is_empty() {
        let model = ask(session, "Model name:")?;
        return if model.is_empty() { Err("a model name is required".into()) } else { Ok(model) };
    }
    say(session, "Default model:")?;
    for (i, preset) in provider.presets.iter().enumerate() {
        say(session, &format!("  {}. {preset}", i + 1))?;
    }
    say(session, &format!("  {}. another name", provider.presets.len() + 1))?;
    let answer = ask(session, &format!("Choose [1-{}]:", provider.presets.len() + 1))?;
    match answer.parse::<usize>() {
        Ok(n) if n >= 1 && n <= provider.presets.len() => Ok(provider.presets[n - 1].to_string()),
        Ok(n) if n == provider.presets.len() + 1 => {
            let model = ask(session, "Model name:")?;
            if model.is_empty() {
                Err("a model name is required".into())
            } else {
                Ok(model)
            }
        }
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
mod tests {
    use super::*;
    use std::io::{Cursor, Read};
    use std::os::unix::fs::PermissionsExt;
    use std::sync::{Arc, Mutex};

    fn home(name: &str) -> PathBuf {
        let dir =
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../../target/foe-cli-tests").join(format!("login-{name}"));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    /// A loopback HTTP server answering each connection with one scripted
    /// body as JSON, recording the request heads.
    struct MockServer {
        base: String,
        seen: Arc<Mutex<Vec<String>>>,
    }

    impl MockServer {
        fn start(replies: Vec<(u16, String)>) -> MockServer {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            let base = format!("http://{}", listener.local_addr().unwrap());
            let seen = Arc::new(Mutex::new(Vec::new()));
            let recorded = seen.clone();
            std::thread::spawn(move || {
                for (status, body) in replies {
                    let Ok((mut stream, _)) = listener.accept() else { return };
                    let mut reader = BufReader::new(stream.try_clone().unwrap());
                    let mut head = String::new();
                    let mut length = 0;
                    loop {
                        let mut line = String::new();
                        reader.read_line(&mut line).unwrap();
                        if let Some(v) = line.to_ascii_lowercase().strip_prefix("content-length:") {
                            length = v.trim().parse().unwrap();
                        }
                        head.push_str(&line);
                        if line.trim().is_empty() {
                            break;
                        }
                    }
                    let mut body_bytes = vec![0; length];
                    reader.read_exact(&mut body_bytes).unwrap();
                    head.push_str(&String::from_utf8_lossy(&body_bytes));
                    recorded.lock().unwrap().push(head);
                    let _ = write!(stream, "HTTP/1.1 {status} X\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{body}", body.len());
                    let _ = stream.flush();
                }
            });
            MockServer { base, seen }
        }
    }

    /// A writer shared with the test, so output can be read while the
    /// session is still blocked on a callback.
    #[derive(Clone, Default)]
    struct Shared(Arc<Mutex<Vec<u8>>>);

    impl Write for Shared {
        fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
            self.0.lock().unwrap().extend_from_slice(buf);
            Ok(buf.len())
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }

    impl Shared {
        fn text(&self) -> String {
            String::from_utf8_lossy(&self.0.lock().unwrap()).into_owned()
        }
    }

    fn make_session<'a>(
        home: &Path,
        input: &'a mut dyn BufRead,
        output: &'a mut dyn Write,
        endpoints: Endpoints,
    ) -> Session<'a> {
        Session { home: home.to_path_buf(), input, output, terminal: false, endpoints }
    }

    /// docs/models.md "foe login": the api-key flow verifies the key, writes
    /// the credentials file with mode 0600, and sets the default model.
    #[test]
    fn api_key_login_end_to_end() {
        let home = home("api-key");
        let server = MockServer::start(vec![(200, r#"{"data":[]}"#.into())]);
        let mut input = Cursor::new(b"sk-ant-test-key\n1\n".to_vec());
        let mut output = Vec::new();
        let endpoints = Endpoints { base_url: Some(server.base.clone()), ..Endpoints::default() };
        let mut session = make_session(&home, &mut input, &mut output, endpoints);
        let code = run(&mut session, Options { provider: Some("anthropic".into()), ..Default::default() }).unwrap();
        assert_eq!(code, ExitCode::SUCCESS);
        let text = String::from_utf8(output).unwrap();
        assert!(text.contains("Paste your Anthropic API key:"), "{text}");
        assert!(!text.contains("sk-ant-test-key"), "the secret is never printed: {text}");
        let creds = home.join(".config/foe/credentials/anthropic.json");
        assert!(text.contains(&format!("wrote {}", creds.display())), "{text}");
        assert_eq!(std::fs::metadata(&creds).unwrap().permissions().mode() & 0o777, 0o600);
        assert_eq!(auth::api_key::read_api_key(&creds).unwrap(), "sk-ant-test-key");
        let seen = server.seen.lock().unwrap();
        assert!(seen[0].starts_with("GET /v1/models HTTP/1.1"), "{}", seen[0]);
        assert!(seen[0].contains("x-api-key: sk-ant-test-key"), "{}", seen[0]);
        let default = default_model_in(&home).unwrap().unwrap();
        assert_eq!((default.provider.as_str(), default.model.as_str()), ("anthropic", "claude-opus-5"));
        assert!(text.contains("1. claude-opus-5"), "{text}");
        assert!(text.trim_end().ends_with(&format!("next: {NEXT_COMMAND}")), "{text}");
    }

    #[test]
    fn a_rejected_key_says_what_to_do_and_writes_nothing() {
        let home = home("rejected");
        let body = r#"{"error":{"type":"authentication_error","message":"invalid x-api-key"}}"#;
        let server = MockServer::start(vec![(401, body.into())]);
        let mut input = Cursor::new(b"sk-bad\n".to_vec());
        let mut output = Vec::new();
        let endpoints = Endpoints { base_url: Some(server.base.clone()), ..Endpoints::default() };
        let mut session = make_session(&home, &mut input, &mut output, endpoints);
        let err = run(&mut session, Options { provider: Some("anthropic".into()), ..Default::default() }).unwrap_err();
        assert_eq!(err, "the provider rejected the key (HTTP 401: authentication_error: invalid x-api-key); check the key and run `foe login anthropic` again");
        assert!(!home.join(".config/foe/credentials/anthropic.json").exists());
        assert!(default_model_in(&home).unwrap().is_none());
    }

    #[test]
    fn an_explicit_model_skips_the_prompt_and_a_second_login_keeps_the_default() {
        let home = home("explicit-model");
        let server = MockServer::start(vec![(200, "{}".into()), (200, "{}".into())]);
        let mut input = Cursor::new(b"sk-one\n".to_vec());
        let mut output = Vec::new();
        let endpoints = Endpoints { base_url: Some(server.base.clone()), ..Endpoints::default() };
        let mut session = make_session(&home, &mut input, &mut output, endpoints);
        let options = Options { provider: Some("openai".into()), model: Some("gpt-5-mini".into()), status: false };
        run(&mut session, options).unwrap();
        assert_eq!(default_model_in(&home).unwrap().unwrap().model, "gpt-5-mini");
        // A second provider leaves the default alone and no model prompt appears.
        let mut input = Cursor::new(b"sk-two\n".to_vec());
        let mut output = Vec::new();
        let endpoints = Endpoints { base_url: Some(server.base.clone()), ..Endpoints::default() };
        let mut session = make_session(&home, &mut input, &mut output, endpoints);
        run(&mut session, Options { provider: Some("openrouter".into()), ..Default::default() }).unwrap();
        assert!(!String::from_utf8(output).unwrap().contains("Default model:"));
        assert_eq!(default_model_in(&home).unwrap().unwrap().provider, "openai");
        // Status names both credential paths and never a secret.
        let mut input = Cursor::new(Vec::new());
        let mut output = Vec::new();
        let mut session = make_session(&home, &mut input, &mut output, Endpoints::default());
        run(&mut session, Options { status: true, ..Default::default() }).unwrap();
        let text = String::from_utf8(output).unwrap();
        assert!(text.contains("default model  openai/gpt-5-mini"), "{text}");
        assert!(text.contains("credentials/openai.json") && text.contains("credentials/openrouter.json"), "{text}");
        assert!(!text.contains("sk-"), "{text}");
    }

    #[test]
    fn listing_shows_every_provider_with_its_state() {
        let home = home("list");
        let mut input = Cursor::new(Vec::new());
        let mut output = Vec::new();
        let mut session = make_session(&home, &mut input, &mut output, Endpoints::default());
        run(&mut session, Options::default()).unwrap();
        let text = String::from_utf8(output).unwrap();
        for provider in PROVIDERS {
            assert!(text.contains(provider.name), "{text}");
        }
        assert!(text.contains("anthropic          not configured"), "{text}");
        assert!(text.contains("exec               no login"), "{text}");
    }

    /// docs/models.md "foe login": the Codex flow opens an authorization URL
    /// with PKCE, receives the code on the loopback listener, exchanges it,
    /// and writes the token file.
    #[test]
    fn codex_login_up_to_the_loopback_callback() {
        let home = home("codex");
        let payload = serde_json::json!({ "https://api.openai.com/auth": { "chatgpt_account_id": "acct_12345678" } });
        let jwt = format!("eyJhbGciOiJub25lIn0.{}.sig", base64url(payload.to_string().as_bytes()));
        let token_body =
            serde_json::json!({ "access_token": jwt, "refresh_token": "rt", "expires_in": 3600 }).to_string();
        let server = MockServer::start(vec![(200, token_body)]);
        let output = Shared::default();
        let home_for_thread = home.clone();
        let token_url = format!("{}/oauth/token", server.base);
        let authorize_url = format!("{}/oauth/authorize", server.base);
        let mut out = output.clone();
        let worker = std::thread::spawn(move || {
            let mut input = Cursor::new(b"1\n".to_vec());
            let endpoints =
                Endpoints { base_url: None, authorize_url, token_url, callback_port: 0, open_browser: false };
            let mut session = make_session(&home_for_thread, &mut input, &mut out, endpoints);
            run(&mut session, Options { provider: Some("openai-codex".into()), ..Default::default() })
        });
        let url = loop {
            let text = output.text();
            if let Some(line) = text.lines().find(|l| l.contains("code_challenge=")) {
                break line.to_string();
            }
            std::thread::sleep(std::time::Duration::from_millis(10));
        };
        let query: Vec<(&str, &str)> =
            url.split('?').nth(1).unwrap().split('&').filter_map(|p| p.split_once('=')).collect();
        let get = |k: &str| query.iter().find(|(key, _)| *key == k).map(|(_, v)| percent_decode(v)).unwrap();
        assert_eq!(get("code_challenge_method"), "S256");
        assert_eq!(get("client_id"), auth::token_file::codex::CLIENT_ID);
        assert_eq!(get("originator"), "foe");
        let redirect = get("redirect_uri");
        let port: u16 = redirect.trim_start_matches("http://localhost:").split('/').next().unwrap().parse().unwrap();
        // A request with the wrong state is refused and the wait continues.
        let mut bad = std::net::TcpStream::connect(("127.0.0.1", port)).unwrap();
        write!(bad, "GET /auth/callback?code=x&state=wrong HTTP/1.1\r\nhost: localhost\r\n\r\n").unwrap();
        assert!(drain(bad).starts_with("HTTP/1.1 400"));
        let mut good = std::net::TcpStream::connect(("127.0.0.1", port)).unwrap();
        write!(good, "GET /auth/callback?code=c0de&state={} HTTP/1.1\r\nhost: localhost\r\n\r\n", get("state"))
            .unwrap();
        assert!(drain(good).starts_with("HTTP/1.1 200"));
        worker.join().unwrap().unwrap();
        let seen = server.seen.lock().unwrap();
        assert!(seen[0].starts_with("POST /oauth/token"), "{}", seen[0]);
        assert!(
            seen[0].contains("grant_type=authorization_code&client_id=app_")
                && seen[0].contains("&code=c0de&code_verifier="),
            "{}",
            seen[0]
        );
        let path = home.join(".config/foe/credentials/openai-codex.json");
        let token = auth::token_file::read_token(&path).unwrap();
        assert_eq!(token.account_id.as_deref(), Some("acct_12345678"));
        assert_eq!(std::fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
        let text = output.text();
        assert!(text.contains("(account ...5678)"), "{text}");
        assert!(!text.contains("acct_12345678") && !text.contains("rt\""), "{text}");
        assert_eq!(default_model_in(&home).unwrap().unwrap().model, "gpt-5.6-sol");
    }

    #[test]
    fn an_unknown_provider_and_exec_are_refused_with_directions() {
        let home = home("unknown");
        let mut input = Cursor::new(Vec::new());
        let mut output = Vec::new();
        let mut session = make_session(&home, &mut input, &mut output, Endpoints::default());
        let err = run(&mut session, Options { provider: Some("bedrock".into()), ..Default::default() }).unwrap_err();
        assert!(err.starts_with("provider `bedrock` is unknown to this build"), "{err}");
        let err = run(&mut session, Options { provider: Some("exec".into()), ..Default::default() }).unwrap_err();
        assert!(err.starts_with("exec needs no login"), "{err}");
    }

    fn base64url(bytes: &[u8]) -> String {
        const ALPHABET: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
        let mut out = String::new();
        for chunk in bytes.chunks(3) {
            let mut n = 0u32;
            for (i, b) in chunk.iter().enumerate() {
                n |= (*b as u32) << (16 - 8 * i);
            }
            for i in 0..=chunk.len() {
                out.push(ALPHABET[((n >> (18 - 6 * i)) & 63) as usize] as char);
            }
        }
        out
    }
}
