//! Built-in model clients, used when a configuration has a `model` block.
//!
//! A client is a wire format paired with a credential source and a URL.
//! The wire formats live in [`format`]: Anthropic Messages, OpenAI Chat
//! Completions, OpenAI Responses, and Gemini. The credential sources live
//! in [`auth`]: an API key file, an OAuth token file, and Google
//! credentials. The provider table in [`providers`] names each pairing and
//! its defaults; [`build`] resolves a `model` block against the table. A
//! `model` block whose provider is `exec` names a program instead, which
//! [`exec`] drives over standard input and output.
//!
//! What every client guarantees:
//!
//! - Credentials come from the file the `model` block names, or from the
//!   convention path `~/.config/foe/credentials/<provider>.json` when it
//!   names none. No environment variable is read, including `HOME` and
//!   proxy variables; there is no proxy support.
//! - TLS trusts a compiled-in copy of Mozilla's root certificates; the
//!   system certificate store is never opened.
//! - Every call to `stream` ends with exactly one `Chunk::Done` or
//!   `Chunk::Error`. An HTTP 429 or 5xx status, a refused connection, and a
//!   connection that drops mid-stream are reported as retryable; any other
//!   4xx status is not. A `Retry-After` header is carried in the error
//!   message as `retry_after_ms=N`.
//!
//! `model.base_url` follows each provider's own convention. For Anthropic
//! it is the origin, `https://api.anthropic.com`, and the client appends
//! `/v1/messages`. For OpenAI-shaped servers it includes the version
//! prefix, `https://api.openai.com/v1` or `http://127.0.0.1:11434/v1`, and
//! the client appends `/responses` or `/chat/completions`. For Vertex AI it
//! is the regional origin, which is derived from `location` when absent.
//!
//! The HTTP work runs on a blocking thread; `stream` forwards chunks to the
//! caller's sink as they arrive.

#![forbid(unsafe_code)]

#[cfg(feature = "http")]
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::Arc;

#[cfg(feature = "http")]
use foe_core::{Chunk, ModelRequestBody};
use foe_core::{Executor, Transport};
use foe_program::ModelConfig;

/// Longest silence tolerated between bytes of a response. Providers keep a
/// stream alive with periodic events, and a model that is thinking with its
/// reasoning hidden may send nothing for minutes, so the limit is generous.
/// The episode's wall-clock budget, enforced by the runtime, bounds the
/// whole request.
///
/// Here rather than in `http`, because the `exec` transport honours the
/// same limit and builds without the HTTP client.
#[cfg(any(feature = "http", feature = "exec"))]
pub(crate) const READ_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(600);

pub mod auth;
#[cfg(feature = "exec")]
pub mod exec;
pub mod format;
#[cfg(feature = "http")]
mod http;
pub mod paths;
pub mod providers;
mod sse;
#[cfg(test)]
mod testserver;

use auth::{Auth, AuthKind};
#[cfg(feature = "http")]
use format::{Decoder, Format};
#[cfg(feature = "http")]
use http::Url;
#[allow(unused_imports)]
use providers::{Provider, Verify, WireFormat};

/// Why a `model` block could not become a transport. Every variant names the
/// configuration key involved and says what to do.
#[derive(Debug, thiserror::Error)]
pub enum TransportError {
    #[error("model.provider: `{name}` is unknown to this build; known providers: {}", known.join(", "))]
    UnknownProvider { name: String, known: Vec<&'static str> },
    #[error("model.{key}: required by provider {provider}; {hint}")]
    MissingOption { provider: &'static str, key: &'static str, hint: String },
    #[error("model.{key}: {path}: {reason}; run `foe login {provider}` or name the file in the model block")]
    Credential { provider: &'static str, key: &'static str, path: PathBuf, reason: String },
    #[error("model.base_url: {url}: {reason}")]
    BaseUrl { url: String, reason: String },
    #[error("model.exec: {path}: {reason}")]
    Exec { path: PathBuf, reason: String },
    #[error("home directory: {0}; name the credential file in the model block instead")]
    Home(String),
    #[error("model.provider: exec needs an executor, which only the foe binary supplies")]
    NoExecutor,
}

/// A `model` block resolved against the provider table, before any
/// credential is read. `foe plan` prints it; [`build`] consumes it.
#[derive(Debug, Clone)]
pub struct Plan {
    pub provider: &'static Provider,
    /// The block with every defaulted option filled in, including the
    /// credential path. What `episode/start.program.model` records.
    pub model: ModelConfig,
    /// The file the transport reads for its credential, when it reads one.
    pub credential_path: Option<PathBuf>,
    /// The program an `exec` provider runs.
    pub exec: Option<PathBuf>,
}

impl Plan {
    /// One line for `foe plan`.
    pub fn describe(&self) -> String {
        let mut text = format!("{}/{}: {} format", self.provider.name, self.model.model, self.format_name());
        match &self.credential_path {
            Some(path) => text.push_str(&format!(", {} from {}", self.provider.auth.name(), path.display())),
            None => text.push_str(", no credential read by foe"),
        }
        if let Some(exec) = &self.exec {
            text.push_str(&format!(", program {}", exec.display()));
        }
        text
    }

    /// The wire format this plan speaks, with Vertex resolved by model name.
    pub fn format_name(&self) -> &'static str {
        match self.provider.format {
            #[cfg(feature = "google")]
            WireFormat::VertexByModel => {
                if self.model.model.starts_with("claude") {
                    "messages"
                } else {
                    "gemini"
                }
            }
            #[allow(unreachable_patterns)]
            other => other.name(),
        }
    }
}

/// Every provider name this build knows, in table order.
pub fn known_providers() -> Vec<&'static str> {
    providers::names()
}

/// The table row for a provider name, for `foe plan` and `foe login`.
pub fn provider_info(name: &str) -> Option<&'static Provider> {
    providers::find(name)
}

/// The context window in tokens of the model a `model` block names, when
/// the provider table knows it. Context compaction uses it when the
/// configuration gives no `context.window_tokens`.
pub fn context_window(config: &ModelConfig) -> Option<u64> {
    provider_info(&config.provider)?.context_window(&config.model)
}

/// Resolves a `model` block: looks the provider up, fills the credential
/// path from the convention directory when the block names none, and
/// checks that every required option is present. Reads the Vertex
/// convention file when it exists; reads no secret.
pub fn plan(config: &ModelConfig) -> Result<Plan, TransportError> {
    let home = paths::home_dir().map_err(TransportError::Home)?;
    plan_with_home(config, &home)
}

/// [`plan`] with the home directory given, for tests and for callers that
/// already know it.
pub fn plan_with_home(config: &ModelConfig, home: &Path) -> Result<Plan, TransportError> {
    let provider = providers::find(&config.provider)
        .ok_or_else(|| TransportError::UnknownProvider { name: config.provider.clone(), known: providers::names() })?;
    let mut model = config.clone();
    let mut credential_path = None;
    if let Some(key) = provider.auth.option_key() {
        let default = paths::credentials_path(home, provider.name);
        #[cfg(feature = "google")]
        if provider.auth == AuthKind::Google && model.option(key).is_none() {
            // The Vertex convention file names the Google credentials file and
            // the project and location, which the block may still override.
            if let Ok(text) = std::fs::read_to_string(&default) {
                let value: serde_json::Value = serde_json::from_str(&text).map_err(|e| TransportError::Credential {
                    provider: provider.name,
                    key,
                    path: default.clone(),
                    reason: format!("not JSON: {e}"),
                })?;
                for field in ["credentials_file", "project", "location"] {
                    if let Some(text) = value[field].as_str() {
                        model.options.entry(field.to_string()).or_insert_with(|| text.to_string());
                    }
                }
            }
        }
        let path = match model.option(key) {
            Some(text) => PathBuf::from(text),
            None => default,
        };
        if !path.is_absolute() {
            return Err(TransportError::Credential {
                provider: provider.name,
                key,
                path,
                reason: "is not an absolute path".into(),
            });
        }
        model.options.insert(key.to_string(), path.to_string_lossy().into_owned());
        credential_path = Some(path);
    }
    for (key, hint) in provider.required {
        if model.option(key).is_none_or(|v| v.trim().is_empty()) {
            return Err(TransportError::MissingOption { provider: provider.name, key, hint: hint.to_string() });
        }
    }
    let exec = match provider.format {
        #[cfg(feature = "exec")]
        WireFormat::Exec => {
            let path = PathBuf::from(model.option("exec").unwrap_or_default());
            if !path.is_absolute() {
                return Err(TransportError::Exec { path, reason: "is not an absolute path".into() });
            }
            Some(path)
        }
        #[allow(unreachable_patterns)]
        _ => None,
    };
    #[cfg(feature = "http")]
    if let Some(url) = model.option("base_url") {
        Url::parse(url).map_err(|reason| TransportError::BaseUrl { url: url.to_string(), reason })?;
    }
    Ok(Plan { provider, model, credential_path, exec })
}

/// Builds the transport a `model` block names. Reads the credential file
/// once, here, so that a construction error is reported before any
/// request. `executor` is needed only by the `exec` provider.
pub fn build(config: &ModelConfig, executor: Option<Arc<dyn Executor>>) -> Result<Arc<dyn Transport>, TransportError> {
    build_planned(&plan(config)?, executor)
}

/// [`build`] without the `exec` provider.
pub fn from_config(config: &ModelConfig) -> Result<Arc<dyn Transport>, TransportError> {
    build(config, None)
}

/// Builds the transport of a resolved plan.
pub fn build_planned(plan: &Plan, executor: Option<Arc<dyn Executor>>) -> Result<Arc<dyn Transport>, TransportError> {
    #[cfg(feature = "exec")]
    if plan.provider.format == WireFormat::Exec {
        let executor = executor.ok_or(TransportError::NoExecutor)?;
        return Ok(Arc::new(exec::ExecTransport::new(&plan.model, executor)?));
    }
    let _ = &executor;
    build_http(plan)
}

/// The table holds only `exec` rows in a build without a wire format.
#[cfg(not(feature = "http"))]
fn build_http(plan: &Plan) -> Result<Arc<dyn Transport>, TransportError> {
    unreachable!("provider {} has no wire format in this build", plan.provider.name)
}

/// Builds the HTTP client of a plan: the credential source, the wire
/// format, and the URL the provider row implies.
#[cfg(feature = "http")]
fn build_http(plan: &Plan) -> Result<Arc<dyn Transport>, TransportError> {
    let provider = plan.provider;
    let model = &plan.model;
    let auth = open_auth(plan)?;
    let base = |default: Option<&str>| -> Result<Url, TransportError> {
        let text = model.option("base_url").or(default).unwrap_or("");
        Url::parse(text).map_err(|reason| TransportError::BaseUrl { url: text.to_string(), reason })
    };
    let max = model.max_output_tokens;
    let (format, url): (Box<dyn Format>, Url) = match provider.format {
        #[cfg(feature = "messages")]
        WireFormat::Messages => (
            Box::new(format::messages::Messages::new(provider.name, Some(model.model.clone()), max, None)),
            base(provider.default_base_url)?.join(provider.path),
        ),
        #[cfg(feature = "chat")]
        WireFormat::Chat => (
            Box::new(format::chat::Chat::new(provider.name, model.model.clone(), max)),
            base(provider.default_base_url)?.join(provider.path),
        ),
        #[cfg(feature = "responses")]
        WireFormat::Responses => (
            Box::new(format::responses::Responses::new(
                provider.name,
                model.model.clone(),
                max,
                model.option("reasoning_effort").map(str::to_string),
                model.option("service_tier").map(str::to_string),
            )),
            base(provider.default_base_url)?.join(provider.path),
        ),
        #[cfg(feature = "google")]
        WireFormat::VertexByModel => vertex_route(provider, model, &base)?,
        #[cfg(feature = "exec")]
        WireFormat::Exec => unreachable!("handled by build_planned"),
    };
    let headers = provider.headers.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
    Ok(Arc::new(Client {
        route: foe_log::ModelRoute { provider: provider.name.to_string(), model: model.model.clone() },
        provider: provider.name,
        url,
        headers,
        auth,
        format,
    }))
}

/// Vertex AI publishes Anthropic models behind the Messages format and
/// Google models behind the Gemini format, under one project and location.
/// https://cloud.google.com/vertex-ai/generative-ai/docs/partner-models/use-claude
/// https://cloud.google.com/vertex-ai/generative-ai/docs/model-reference/inference
#[cfg(feature = "google")]
fn vertex_route(
    provider: &'static Provider,
    model: &ModelConfig,
    base: &dyn Fn(Option<&str>) -> Result<Url, TransportError>,
) -> Result<(Box<dyn Format>, Url), TransportError> {
    let project = model.option("project").unwrap_or_default();
    let location = model.option("location").unwrap_or_default();
    let origin = if location == "global" {
        "https://aiplatform.googleapis.com".to_string()
    } else {
        format!("https://{location}-aiplatform.googleapis.com")
    };
    let prefix = format!("/v1/projects/{project}/locations/{location}/publishers");
    let name = &model.model;
    if name.starts_with("claude") {
        #[cfg(feature = "messages")]
        {
            let format = format::messages::Messages::new(
                provider.name,
                None,
                model.max_output_tokens,
                Some("vertex-2023-10-16"),
            );
            let url = base(Some(&origin))?.join(&format!("{prefix}/anthropic/models/{name}:streamRawPredict"));
            return Ok((Box::new(format), url));
        }
        #[cfg(not(feature = "messages"))]
        return Err(TransportError::MissingOption {
            provider: provider.name,
            key: "model",
            hint: "names starting with `claude` need the messages feature, which this build lacks".into(),
        });
    }
    #[cfg(feature = "gemini")]
    {
        let include_thoughts = model.option("include_thoughts") != Some("false");
        let format = format::gemini::Gemini::new(provider.name, model.max_output_tokens, include_thoughts);
        let url = base(Some(&origin))?.join(&format!("{prefix}/google/models/{name}:streamGenerateContent?alt=sse"));
        Ok((Box::new(format), url))
    }
    #[cfg(not(feature = "gemini"))]
    Err(TransportError::MissingOption {
        provider: provider.name,
        key: "model",
        hint: "names other than `claude*` need the gemini feature, which this build lacks".into(),
    })
}

/// Opens the credential source a plan names. The only place a secret is
/// read.
#[cfg(feature = "http")]
fn open_auth(plan: &Plan) -> Result<Arc<dyn Auth>, TransportError> {
    let provider = plan.provider;
    let Some(key) = provider.auth.option_key() else {
        return Ok(Arc::new(auth::NoAuth));
    };
    let path = plan.credential_path.clone().expect("a credentialed plan has a path");
    let fail = |e: auth::AuthError| TransportError::Credential {
        provider: provider.name,
        key,
        path: path.clone(),
        reason: match e {
            auth::AuthError::Credential { reason, .. } => reason,
            other => other.to_string(),
        },
    };
    Ok(match provider.auth {
        #[cfg(feature = "api-key")]
        AuthKind::ApiKey { header } => Arc::new(auth::api_key::ApiKey::from_file(header, &path).map_err(fail)?),
        #[cfg(feature = "token-file")]
        AuthKind::TokenFile { account_header, token_url, client_id } => {
            let client = auth::token_file::OAuthClient { token_url: token_url.into(), client_id: client_id.into() };
            Arc::new(auth::token_file::TokenFile::open(&path, client, account_header).map_err(fail)?)
        }
        #[cfg(feature = "google")]
        AuthKind::Google => Arc::new(auth::google::Google::open(&path).map_err(fail)?),
        AuthKind::None => Arc::new(auth::NoAuth),
    })
}

/// One cheap authenticated request that proves a credential works, for
/// `foe login`. Returns a sentence for the person on failure.
#[cfg(feature = "http")]
pub fn verify_credential(provider: &Provider, base_url: Option<&str>, auth: &dyn Auth) -> Result<(), String> {
    let headers = auth.headers().map_err(|e| e.to_string())?;
    match provider.verify {
        Verify::None | Verify::MintToken => Ok(()),
        Verify::GetJson(path) => {
            let text = base_url.or(provider.default_base_url).unwrap_or("");
            let url = Url::parse(text).map_err(|reason| format!("base_url {text}: {reason}"))?.join(path);
            let mut all: Vec<(&str, &str)> = provider.headers.to_vec();
            all.extend(headers.iter().map(|(k, v)| (k.as_str(), v.as_str())));
            all.push(("accept", "application/json"));
            let mut response = http::request("GET", &url, &all, &[]).map_err(|e| format!("{text}: {e}"))?;
            if (200..300).contains(&response.status) {
                return Ok(());
            }
            let mut body = String::new();
            let _ = (&mut response.body).take(MAX_ERROR_BODY).read_to_string(&mut body);
            let detail = describe_error_body(&body);
            Err(match response.status {
                401 | 403 => format!("the provider rejected the key (HTTP {}: {detail})", response.status),
                status => format!("HTTP {status} from {}: {detail}", url.host),
            })
        }
    }
}

// ---- the shared request loop -------------------------------------------------

/// A wire format, a credential source, and a URL: one provider as the
/// runtime drives it.
#[cfg(feature = "http")]
pub struct Client {
    route: foe_log::ModelRoute,
    provider: &'static str,
    url: Url,
    headers: Vec<(String, String)>,
    auth: Arc<dyn Auth>,
    format: Box<dyn Format>,
}

#[cfg(feature = "http")]
impl Client {
    /// `provider` prefixes every error message and is the route's provider.
    pub fn new(
        provider: &'static str,
        model: &str,
        url: Url,
        headers: Vec<(String, String)>,
        auth: Arc<dyn Auth>,
        format: Box<dyn Format>,
    ) -> Client {
        let route = foe_log::ModelRoute { provider: provider.to_string(), model: model.to_string() };
        Client { route, provider, url, headers, auth, format }
    }

    pub fn url(&self) -> &Url {
        &self.url
    }
}

#[cfg(feature = "http")]
#[async_trait::async_trait]
impl Transport for Client {
    fn route(&self) -> foe_log::ModelRoute {
        self.route.clone()
    }

    async fn stream(&self, req: ModelRequestBody, sink: &mut (dyn foe_core::ChunkSink + Send)) {
        let exchange = Exchange {
            provider: self.provider,
            url: self.url.clone(),
            headers: self.headers.clone(),
            body: serde_json::to_vec(&self.format.body(&req)).expect("a serde_json::Value serializes"),
        };
        deliver(exchange, self.auth.clone(), self.format.decoder(), sink).await
    }
}

/// One HTTP request, ready to send once the credential headers are added.
#[cfg(feature = "http")]
struct Exchange {
    /// The provider name used as the prefix of every error message.
    provider: &'static str,
    url: Url,
    headers: Vec<(String, String)>,
    body: Vec<u8>,
}

#[cfg(feature = "http")]
fn is_terminal(chunk: &Chunk) -> bool {
    matches!(chunk, Chunk::Done { .. } | Chunk::Error { .. })
}

/// Forwards chunks from the blocking request to the caller's sink. Ensures
/// the sequence ends with exactly one terminal chunk even if the worker
/// fails.
#[cfg(feature = "http")]
async fn deliver(
    exchange: Exchange,
    auth: Arc<dyn Auth>,
    decoder: Box<dyn Decoder>,
    sink: &mut (dyn foe_core::ChunkSink + Send),
) {
    let provider = exchange.provider;
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
    let worker = tokio::task::spawn_blocking(move || perform(exchange, auth, decoder, Outbox { tx, closed: false }));
    let mut terminal = false;
    while let Some(chunk) = rx.recv().await {
        terminal |= is_terminal(&chunk);
        sink.push(chunk);
    }
    let joined = worker.await;
    if !terminal {
        let reason = match joined {
            Ok(()) => "request ended without a final chunk".to_string(),
            Err(e) => format!("request worker failed: {e}"),
        };
        sink.push(Chunk::Error { message: format!("{provider}: {reason}"), retryable: true });
    }
}

/// The sending side of the chunk channel. Drops everything after the first
/// terminal chunk and after the receiver has gone away.
#[cfg(feature = "http")]
struct Outbox {
    tx: tokio::sync::mpsc::UnboundedSender<Chunk>,
    closed: bool,
}

#[cfg(feature = "http")]
impl Outbox {
    fn push(&mut self, chunk: Chunk) {
        if self.closed {
            return;
        }
        let terminal = is_terminal(&chunk);
        if self.tx.send(chunk).is_err() || terminal {
            self.closed = true;
        }
    }
}

/// Adds the credential headers, sends the request, and drives the decoder
/// until a terminal chunk. Runs on a blocking thread, so a token refresh
/// may block here.
#[cfg(feature = "http")]
fn perform(exchange: Exchange, auth: Arc<dyn Auth>, mut decoder: Box<dyn Decoder>, mut out: Outbox) {
    let provider = exchange.provider;
    let credential = match auth.headers() {
        Ok(headers) => headers,
        Err(e) => {
            out.push(Chunk::Error { message: format!("{provider}: credential: {e}"), retryable: e.retryable() });
            return;
        }
    };
    let mut headers: Vec<(&str, &str)> = exchange.headers.iter().map(|(k, v)| (k.as_str(), v.as_str())).collect();
    headers.extend(credential.iter().map(|(k, v)| (k.as_str(), v.as_str())));
    let mut response = match http::post(&exchange.url, &headers, &exchange.body) {
        Ok(response) => response,
        Err(e) => {
            out.push(Chunk::Error { message: format!("{provider}: {e}"), retryable: e.retryable() });
            return;
        }
    };
    if !(200..300).contains(&response.status) {
        out.push(status_error(provider, &mut response));
        return;
    }
    loop {
        match sse::next_event(&mut response.body) {
            Ok(Some(event)) => {
                decoder.event(&event, &mut |chunk| out.push(chunk));
                if out.closed {
                    return;
                }
            }
            Ok(None) => {
                out.push(decoder.end_of_stream());
                return;
            }
            Err(e) => {
                // Invalid UTF-8 is a malformed stream; anything else is the
                // connection failing under us.
                let retryable = e.kind() != std::io::ErrorKind::InvalidData;
                out.push(Chunk::Error { message: format!("{provider}: reading response body: {e}"), retryable });
                return;
            }
        }
    }
}

/// Largest error body read for its message.
#[cfg(feature = "http")]
const MAX_ERROR_BODY: u64 = 64 * 1024;

/// Classifies a non-2xx response. The providers send a JSON body of the
/// form `{"error": {"type": ..., "message": ...}}`; its fields are quoted
/// when present and the raw body otherwise.
/// https://docs.anthropic.com/en/api/errors
/// https://platform.openai.com/docs/guides/error-codes
#[cfg(feature = "http")]
fn status_error(provider: &str, response: &mut http::Response) -> Chunk {
    let status = response.status;
    // OpenAI sends `retry-after-ms` beside the standard `retry-after`;
    // only the delay-seconds form of the standard header is translated.
    let retry_after_ms = response
        .header("retry-after-ms")
        .and_then(|v| v.trim().parse::<u64>().ok())
        .or_else(|| response.header("retry-after").and_then(|v| v.trim().parse::<u64>().ok()).map(|s| s * 1000));
    let mut text = String::new();
    let _ = (&mut response.body).take(MAX_ERROR_BODY).read_to_string(&mut text);
    let mut message = format!("{provider}: HTTP {status}: {}", describe_error_body(&text));
    if let Some(ms) = retry_after_ms {
        message.push_str(&format!(" retry_after_ms={ms}"));
    }
    let retryable = status == 429 || (500..600).contains(&status);
    Chunk::Error { message, retryable }
}

/// The human-readable part of an error body: the structured fields of the
/// provider conventions, an OAuth `error`/`error_description` pair, or a
/// whitespace-collapsed snippet of whatever arrived.
#[cfg(feature = "http")]
pub(crate) fn describe_error_body(text: &str) -> String {
    if let Ok(value) = serde_json::from_str::<serde_json::Value>(text) {
        let error = value.get("error").unwrap_or(&value);
        if let Some(code) = error.as_str() {
            return match value.get("error_description").and_then(|v| v.as_str()) {
                Some(detail) => format!("{code}: {detail}"),
                None => code.to_string(),
            };
        }
        let kind = ["type", "code", "status"].iter().find_map(|k| error.get(*k).and_then(|v| v.as_str()));
        let detail = error.get("message").and_then(|v| v.as_str());
        match (kind, detail) {
            (Some(kind), Some(detail)) => return format!("{kind}: {detail}"),
            (None, Some(detail)) => return detail.to_string(),
            (Some(kind), None) => return kind.to_string(),
            (None, None) => {}
        }
    }
    let snippet: String = text.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(200).collect();
    if snippet.is_empty() {
        "empty response body".to_string()
    } else {
        snippet
    }
}

/// Shared helpers for the tests of this crate.
#[cfg(test)]
pub(crate) mod test_support {
    use std::path::{Path, PathBuf};

    /// A scratch directory under the workspace target directory, so that no
    /// environment variable decides where test files go.
    pub fn scratch_dir(name: &str) -> PathBuf {
        let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../target/foe-transport-tests").join(name);
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    /// A scratch file path; the parent directory exists, the file may not.
    pub fn scratch(name: &str) -> PathBuf {
        let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../target/foe-transport-tests");
        std::fs::create_dir_all(&dir).unwrap();
        dir.join(name)
    }

    /// A fake home directory with nothing under it.
    pub fn fake_home(name: &str) -> PathBuf {
        scratch_dir(&format!("home-{name}"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_support::fake_home;

    fn model(provider: &str) -> ModelConfig {
        ModelConfig::new(provider, "m")
    }

    #[test]
    fn every_row_plans_and_names_its_credential_path() {
        let home = fake_home("rows");
        for name in known_providers() {
            let mut config = model(name);
            match name {
                "openai-compatible" => {
                    config.options.insert("base_url".into(), "http://127.0.0.1:11434/v1".into());
                }
                "vertex" => {
                    config.options.insert("project".into(), "p".into());
                    config.options.insert("location".into(), "us-east5".into());
                }
                "exec" => {
                    config.options.insert("exec".into(), "/usr/bin/true".into());
                }
                _ => {}
            }
            let plan = plan_with_home(&config, &home).unwrap_or_else(|e| panic!("{name}: {e}"));
            assert_eq!(plan.provider.name, name);
            match plan.provider.auth.option_key() {
                Some(key) => {
                    let expected = home.join(format!(".config/foe/credentials/{name}.json"));
                    assert_eq!(plan.credential_path.as_deref(), Some(expected.as_path()), "{name}");
                    assert_eq!(plan.model.option(key), expected.to_str(), "{name}: the record names the path");
                }
                None => assert!(plan.credential_path.is_none(), "{name}"),
            }
            assert!(plan.describe().starts_with(&format!("{name}/m: ")), "{}", plan.describe());
        }
    }

    #[test]
    fn an_unknown_provider_lists_the_known_ones() {
        let err = plan_with_home(&model("bedrock"), &fake_home("unknown")).unwrap_err().to_string();
        assert!(err.contains("model.provider") && err.contains("`bedrock`"), "{err}");
        for name in known_providers() {
            assert!(err.contains(name), "the rejection does not list {name}: {err}");
        }
    }

    #[test]
    fn an_explicit_credential_file_overrides_the_convention_path() {
        let mut config = model("anthropic");
        config.options.insert("api_key_file".into(), "/keys/a".into());
        let plan = plan_with_home(&config, &fake_home("explicit")).unwrap();
        assert_eq!(plan.credential_path.as_deref(), Some(Path::new("/keys/a")));
        config.options.insert("api_key_file".into(), "relative.key".into());
        let err = plan_with_home(&config, &fake_home("explicit")).unwrap_err().to_string();
        assert!(err.starts_with("model.api_key_file: relative.key: is not an absolute path"), "{err}");
    }

    #[test]
    fn a_missing_required_option_names_the_key_and_the_hint() {
        let err = plan_with_home(&model("openai-compatible"), &fake_home("required")).unwrap_err().to_string();
        assert!(err.starts_with("model.base_url: required by provider openai-compatible; "), "{err}");
        let err = plan_with_home(&model("vertex"), &fake_home("required")).unwrap_err().to_string();
        assert!(err.starts_with("model.project: required by provider vertex; "), "{err}");
    }

    #[cfg(feature = "google")]
    #[test]
    fn the_vertex_convention_file_supplies_project_location_and_credentials() {
        let home = fake_home("vertex");
        let path = paths::credentials_path(&home, "vertex");
        let json = serde_json::json!({ "credentials_file": "/g/adc.json", "project": "proj", "location": "us-east5" });
        paths::write_private(&path, json.to_string().as_bytes()).unwrap();
        let mut config = model("vertex");
        config.model = "gemini-2.5-pro".into();
        let plan = plan_with_home(&config, &home).unwrap();
        assert_eq!(plan.credential_path.as_deref(), Some(Path::new("/g/adc.json")));
        assert_eq!(plan.model.option("project"), Some("proj"));
        assert_eq!(plan.model.option("location"), Some("us-east5"));
        assert_eq!(plan.format_name(), "gemini");
        config.model = "claude-opus-5".into();
        config.options.insert("location".into(), "europe-west1".into());
        let plan = plan_with_home(&config, &home).unwrap();
        assert_eq!(plan.format_name(), "messages");
        assert_eq!(plan.model.option("location"), Some("europe-west1"), "the block overrides the file");
    }

    #[test]
    fn a_missing_credential_file_says_how_to_log_in() {
        let err = build_planned(&plan_with_home(&model("anthropic"), &fake_home("missing")).unwrap(), None)
            .err()
            .expect("a missing key file fails")
            .to_string();
        assert!(err.starts_with("model.api_key_file: "), "{err}");
        assert!(err.ends_with("run `foe login anthropic` or name the file in the model block"), "{err}");
    }

    #[test]
    fn built_clients_carry_the_provider_route_and_url() {
        let home = fake_home("routes");
        let key = home.join("k.json");
        auth::api_key::write_api_key(&key, "k").unwrap();
        let cases = [
            ("anthropic", "api.anthropic.com", "/v1/messages"),
            ("openai", "api.openai.com", "/v1/responses"),
            ("openrouter", "openrouter.ai", "/api/v1/chat/completions"),
            ("openai-compatible", "127.0.0.1", "/v1/chat/completions"),
        ];
        for (name, host, path) in cases {
            let mut config = model(name);
            config.options.insert("api_key_file".into(), key.to_string_lossy().into_owned());
            if name == "openai-compatible" {
                config.options.insert("base_url".into(), "http://127.0.0.1:11434/v1".into());
            }
            let transport = build(&config, None).unwrap_or_else(|e| panic!("{name}: {e}"));
            assert_eq!(transport.route().provider, name);
            assert_eq!(transport.route().model, "m");
            let plan = plan_with_home(&config, &home).unwrap();
            let client = build_client(&plan);
            assert_eq!((client.url().host.as_str(), client.url().path.as_str()), (host, path), "{name}");
        }
        let mut config = model("anthropic");
        config.options.insert("api_key_file".into(), key.to_string_lossy().into_owned());
        config.options.insert("base_url".into(), "localhost:11434".into());
        let err = plan_with_home(&config, &home).unwrap_err().to_string();
        assert_eq!(err, "model.base_url: localhost:11434: scheme must be http or https");
    }

    #[cfg(feature = "google")]
    #[test]
    fn vertex_urls_name_the_publisher_by_model_name() {
        let home = fake_home("vertex-urls");
        let adc = home.join("adc.json");
        let json = serde_json::json!({ "type": "authorized_user", "client_id": "c", "client_secret": "s", "refresh_token": "r" });
        std::fs::write(&adc, json.to_string()).unwrap();
        let mut config = model("vertex");
        config.options.insert("credentials_file".into(), adc.to_string_lossy().into_owned());
        config.options.insert("project".into(), "proj".into());
        config.options.insert("location".into(), "us-east5".into());
        config.model = "claude-opus-5".into();
        let client = build_client(&plan_with_home(&config, &home).unwrap());
        assert_eq!(client.url().host, "us-east5-aiplatform.googleapis.com");
        assert_eq!(
            client.url().path,
            "/v1/projects/proj/locations/us-east5/publishers/anthropic/models/claude-opus-5:streamRawPredict"
        );
        config.model = "gemini-2.5-pro".into();
        config.options.insert("location".into(), "global".into());
        let client = build_client(&plan_with_home(&config, &home).unwrap());
        assert_eq!(client.url().host, "aiplatform.googleapis.com");
        assert_eq!(
            client.url().path,
            "/v1/projects/proj/locations/global/publishers/google/models/gemini-2.5-pro:streamGenerateContent?alt=sse"
        );
    }

    /// Builds the plan as a concrete [`Client`] to inspect its URL.
    fn build_client(plan: &Plan) -> Client {
        let provider = plan.provider;
        let auth = open_auth(plan).unwrap();
        let model = &plan.model;
        let base = |default: Option<&str>| -> Result<Url, TransportError> {
            let text = model.option("base_url").or(default).unwrap_or("");
            Url::parse(text).map_err(|reason| TransportError::BaseUrl { url: text.to_string(), reason })
        };
        let (format, url): (Box<dyn Format>, Url) = match provider.format {
            #[cfg(feature = "google")]
            WireFormat::VertexByModel => vertex_route(provider, model, &base).unwrap(),
            _ => (
                Box::new(format::chat::Chat::new(provider.name, model.model.clone(), None)),
                base(provider.default_base_url).unwrap().join(provider.path),
            ),
        };
        Client::new(provider.name, &model.model, url, Vec::new(), auth, format)
    }

    #[test]
    fn error_body_description_prefers_structured_fields() {
        assert_eq!(
            describe_error_body(r#"{"type":"error","error":{"type":"rate_limit_error","message":"slow down"}}"#),
            "rate_limit_error: slow down"
        );
        assert_eq!(
            describe_error_body(r#"{"error":{"message":"bad","code":"invalid_api_key"}}"#),
            "invalid_api_key: bad"
        );
        assert_eq!(describe_error_body(r#"{"error":{"message":"bad"}}"#), "bad");
        assert_eq!(
            describe_error_body(r#"{"error":"invalid_grant","error_description":"revoked"}"#),
            "invalid_grant: revoked"
        );
        assert_eq!(describe_error_body("<html>\n  502 Bad Gateway\n</html>"), "<html> 502 Bad Gateway </html>");
        assert_eq!(describe_error_body(""), "empty response body");
    }

    #[test]
    fn verification_accepts_2xx_and_explains_401() {
        use crate::testserver::{Reply, Server};
        let server = Server::start(vec![
            Reply::full(200, r#"{"data":[]}"#),
            Reply::full(401, r#"{"error":{"type":"authentication_error","message":"invalid x-api-key"}}"#),
        ]);
        let provider = provider_info("anthropic").unwrap();
        let auth = auth::api_key::ApiKey::new(auth::KeyHeader::XApiKey, "sk-test".into());
        verify_credential(provider, Some(&server.base()), &auth).unwrap();
        let err = verify_credential(provider, Some(&server.base()), &auth).unwrap_err();
        assert_eq!(err, "the provider rejected the key (HTTP 401: authentication_error: invalid x-api-key)");
        let seen = server.requests();
        assert_eq!((seen[0].method.as_str(), seen[0].path.as_str()), ("GET", "/v1/models"));
        assert_eq!(seen[0].header("x-api-key"), Some("sk-test"));
        assert_eq!(seen[0].header("anthropic-version"), Some("2023-06-01"));
    }
}
