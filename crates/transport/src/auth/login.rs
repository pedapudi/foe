//! Acquiring a credential, the half of each provider's authentication
//! protocol that runs once rather than per request.
//!
//! The rest of this module tree spends credentials: it reads a credential
//! file and turns it into request headers. This file produces those files.
//! An API key is verified against the provider and written. An OAuth token
//! is obtained through the authorization-code flow with PKCE, whose
//! browser half returns to a loopback listener here. Google credentials
//! are recorded by the convention file that [`crate::plan_with_home`]
//! reads back, so the writer and the reader of that shape sit together.
//!
//! Beside the credentials this module owns the default model file, which
//! the same operation writes and a bare `foe "task"` reads.
//!
//! What is not here is the conversation: which questions to ask, in what
//! order, and where the answers come from. A caller drives the steps and
//! owns its terminal.

use std::io::{BufRead, BufReader, Write};
use std::net::TcpListener;
use std::path::{Path, PathBuf};

use foe_contract::ModelConfig;

use super::AuthKind;
use crate::paths;
use crate::providers::Provider;

/// Where `gcloud auth application-default login` writes its file, relative
/// to the home directory. Offered as the default for Vertex AI.
pub const GCLOUD_DEFAULT: &str = ".config/gcloud/application_default_credentials.json";

/// The endpoints one login flow talks to. Outside tests these are the
/// provider's own; a test points them at a local server.
pub struct Endpoints {
    /// Replaces a provider's default base URL for verification.
    pub base_url: Option<String>,
    pub authorize_url: String,
    pub token_url: String,
    /// The loopback port for the OAuth callback; 0 takes an ephemeral port.
    pub callback_port: u16,
}

impl Default for Endpoints {
    fn default() -> Self {
        #[cfg(feature = "token-file")]
        {
            use super::token_file::codex;
            let (authorize_url, token_url) = (codex::AUTHORIZE_URL.to_string(), codex::TOKEN_URL.to_string());
            Endpoints { base_url: None, authorize_url, token_url, callback_port: codex::REDIRECT_PORT }
        }
        #[cfg(not(feature = "token-file"))]
        Endpoints { base_url: None, authorize_url: String::new(), token_url: String::new(), callback_port: 0 }
    }
}

/// Verifies an API key by making one request to the provider with it.
/// `base_url` replaces the provider's default, for a provider that has
/// none and for tests.
#[cfg(feature = "api-key")]
pub fn verify_api_key(provider: &Provider, base_url: Option<&str>, key: &str) -> Result<(), String> {
    let AuthKind::ApiKey { header } = provider.auth else {
        return Err(format!("{} does not authenticate with an API key", provider.name));
    };
    crate::verify_credential(provider, base_url, &super::api_key::ApiKey::new(header, key.to_string()))
}

/// Writes an API key as the provider's credential file and returns where it
/// went.
#[cfg(feature = "api-key")]
pub fn save_api_key(home: &Path, provider: &Provider, key: &str) -> Result<PathBuf, String> {
    let path = paths::credentials_path(home, provider.name);
    super::api_key::write_api_key(&path, key).map_err(|e| format!("{}: {e}", path.display()))?;
    Ok(path)
}

/// Writes an OAuth token as the provider's credential file and returns
/// where it went.
#[cfg(feature = "token-file")]
pub fn save_token(home: &Path, provider: &Provider, token: &super::token_file::Token) -> Result<PathBuf, String> {
    let path = paths::credentials_path(home, provider.name);
    super::token_file::write_token(&path, token).map_err(|e| format!("{}: {e}", path.display()))?;
    Ok(path)
}

/// Writes the Vertex convention file: the Google credentials file to use
/// with the project and location to send requests to. `plan_with_home`
/// reads exactly these three fields back into the `model` block.
#[cfg(feature = "google")]
pub fn save_google(
    home: &Path,
    provider: &Provider,
    credentials_file: &Path,
    project: &str,
    location: &str,
) -> Result<PathBuf, String> {
    let path = paths::credentials_path(home, provider.name);
    let json = serde_json::json!({ "credentials_file": credentials_file, "project": project, "location": location });
    let text = format!("{}\n", serde_json::to_string_pretty(&json).expect("three strings serialize"));
    paths::write_private(&path, text.as_bytes()).map_err(|e| format!("{}: {e}", path.display()))?;
    Ok(path)
}

/// The authorization-code flow with PKCE for the ChatGPT Codex client,
/// split where the person has to act: [`begin`](BrowserLogin::begin) binds
/// the loopback listener and mints the URL to visit, and
/// [`finish`](BrowserLogin::finish) waits for the browser to come back and
/// exchanges the code it carries. The caller shows the URL and opens a
/// browser between the two, in whatever way suits it.
#[cfg(feature = "token-file")]
pub struct BrowserLogin {
    /// The URL the person opens to sign in.
    pub url: String,
    /// Where the authorization server sends the browser back to.
    pub redirect_uri: String,
    listener: TcpListener,
    verifier: String,
    state: String,
    token_url: String,
}

#[cfg(feature = "token-file")]
impl BrowserLogin {
    pub fn begin(endpoints: &Endpoints) -> Result<BrowserLogin, String> {
        use super::token_file::codex;
        let port = endpoints.callback_port;
        let listener = TcpListener::bind(("127.0.0.1", port)).map_err(|e| {
            format!("cannot listen on 127.0.0.1:{port} for the login callback: {e}; stop the contract using that port")
        })?;
        let port = listener.local_addr().map_err(|e| e.to_string())?.port();
        let redirect_uri = codex::redirect_uri(port);
        let pkce = codex::pkce().map_err(|e| format!("/dev/urandom: {e}"))?;
        let state = codex::state().map_err(|e| format!("/dev/urandom: {e}"))?;
        let url = codex::authorization_url(&endpoints.authorize_url, &pkce.challenge, &state, &redirect_uri);
        Ok(BrowserLogin {
            url,
            redirect_uri,
            listener,
            verifier: pkce.verifier,
            state,
            token_url: endpoints.token_url.clone(),
        })
    }

    /// Waits for the callback carrying a code with the expected state, then
    /// exchanges it for a token.
    pub fn finish(self) -> Result<super::token_file::Token, String> {
        let code = wait_for_code(&self.listener, &self.state)?;
        let client = super::token_file::OAuthClient {
            token_url: self.token_url,
            client_id: super::token_file::codex::CLIENT_ID.into(),
        };
        client
            .exchange_code(&code, &self.verifier, &self.redirect_uri)
            .map_err(|e| format!("token exchange failed: {e}; run `foe login openai-codex` again"))
    }
}

/// Serves callback requests until one carries a code with the expected
/// state. Any other request receives an error page and the wait goes on.
#[cfg(feature = "token-file")]
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
        let outcome = if path != super::token_file::codex::REDIRECT_PATH {
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

/// Decodes one form-encoded query value, the inverse of the encoding
/// [`authorization_url`](super::token_file::codex::authorization_url)
/// applies: `%` and two hexadecimal digits become that byte, `+` becomes a
/// space. A `%` that no pair of hexadecimal digits follows stands for
/// itself.
pub fn percent_decode(text: &str) -> String {
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

/// The `model` block a bare `foe "task"` runs, from the home directory of
/// the passwd database.
pub fn default_model() -> Result<Option<ModelConfig>, String> {
    default_model_in(&paths::home_dir()?)
}

/// The same, under an explicit home. `None` when no default has been set.
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::providers::find;

    /// The reason the Vertex convention file is written here: `save_google`
    /// and `plan_with_home` are the two halves of one format, and a change
    /// to either that the other does not follow breaks a configured login.
    #[cfg(feature = "google")]
    #[test]
    fn the_vertex_convention_file_is_read_back_into_the_model_block() {
        let home = crate::test_support::scratch_dir("login-google");
        let credentials = home.join("adc.json");
        std::fs::write(&credentials, "{}").unwrap();
        let provider = find("vertex").expect("vertex is a known provider");
        let path = save_google(&home, provider, &credentials, "proj-1", "us-east5").unwrap();
        assert_eq!(path, paths::credentials_path(&home, "vertex"));
        let plan =
            crate::plan_with_home(&ModelConfig::new("vertex", "gemini-2.5-pro"), &home).expect("the plan resolves");
        assert_eq!(plan.model.option("project"), Some("proj-1"));
        assert_eq!(plan.model.option("location"), Some("us-east5"));
        assert_eq!(plan.model.option("credentials_file"), Some(credentials.to_string_lossy().as_ref()));
    }

    /// The default model file survives a round trip, including the options a
    /// login records beside the provider and the model.
    #[test]
    fn the_default_model_file_round_trips() {
        let home = crate::test_support::scratch_dir("login-default-model");
        assert!(default_model_in(&home).unwrap().is_none(), "no default before one is written");
        let mut model = ModelConfig::new("openai", "gpt-5-mini");
        model.options.insert("reasoning_effort".to_string(), "low".to_string());
        write_default_model(&home, &model).unwrap();
        let read = default_model_in(&home).unwrap().unwrap();
        assert_eq!((read.provider.as_str(), read.model.as_str()), ("openai", "gpt-5-mini"));
        assert_eq!(read.option("reasoning_effort"), Some("low"));
        write_default_model(&home, &ModelConfig::new(" ", "m")).unwrap();
        assert!(default_model_in(&home).unwrap_err().contains("provider and model are both required"));
    }

    /// The inverse of the encoding the authorization URL is built with.
    #[test]
    fn query_values_decode_back_to_what_the_url_encoded() {
        let encoded = super::super::form_encode(&[("redirect_uri", "http://localhost:1455/auth/callback")]);
        let value = encoded.split_once('=').unwrap().1;
        assert_eq!(percent_decode(value), "http://localhost:1455/auth/callback");
        assert_eq!(percent_decode("a+b%2Bc"), "a b+c");
        assert_eq!(percent_decode("100%"), "100%", "a stray percent stands for itself");
    }
}
