use super::*;
use std::io::{Cursor, Read};
use std::os::unix::fs::PermissionsExt;
use std::sync::{Arc, Mutex};

fn home(name: &str) -> PathBuf {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../target/foe-cli-tests").join(format!("login-{name}"));
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
    // The error carries the status, the provider's own reason, and the
    // command that retries. Its wording is not the subject.
    for part in ["401", "authentication_error", "invalid x-api-key", "foe login anthropic"] {
        assert!(err.contains(part), "the rejection does not say {part}: {err}");
    }
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
    let token_body = serde_json::json!({ "access_token": jwt, "refresh_token": "rt", "expires_in": 3600 }).to_string();
    let server = MockServer::start(vec![(200, token_body)]);
    let output = Shared::default();
    let home_for_thread = home.clone();
    let token_url = format!("{}/oauth/token", server.base);
    let authorize_url = format!("{}/oauth/authorize", server.base);
    let mut out = output.clone();
    let worker = std::thread::spawn(move || {
        let mut input = Cursor::new(b"1\n".to_vec());
        let endpoints = Endpoints { base_url: None, authorize_url, token_url, callback_port: 0, open_browser: false };
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
    write!(good, "GET /auth/callback?code=c0de&state={} HTTP/1.1\r\nhost: localhost\r\n\r\n", get("state")).unwrap();
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
    let model = default_model_in(&home).unwrap().unwrap();
    assert_eq!(model.model, "gpt-5.6-sol");
    assert_eq!(model.option("reasoning_effort"), Some("low"));
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
