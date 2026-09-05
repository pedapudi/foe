//! Built-in model clients, used when a configuration has a `model` block.
//!
//! A client is a wire format paired with a credential source and a URL.
//! The wire formats live in [`format`], and the credential sources live in
//! [`auth`]. The provider table in [`providers`] names each pairing and its
//! defaults. [`build`] resolves a `model` block against the table.
//!
//! What every client guarantees:
//!
//! - Credentials come from the file the `model` block names, or from the
//!   convention path `~/.config/foe/credentials/<provider>.json` when it
//!   names none. A compatible HTTP endpoint reads a key only when its block
//!   names one, and otherwise receives no authentication header. No
//!   environment variable is read, including `HOME` and proxy variables;
//!   there is no proxy support.
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
//! HTTP and credential refresh use asynchronous I/O. Decoded chunks reach
//! the caller's sink directly, with no intermediate chunk queue.

#![forbid(unsafe_code)]

use futures_util::FutureExt;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use tokio::io::AsyncReadExt;

use foe_contract::ModelConfig;
use foe_core::{Chunk, ModelRequestBody, Transport};

pub mod auth;
pub mod format;
mod http;
pub mod paths;
pub mod providers;
mod sse;

use auth::{Auth, AuthKind};
use format::{Decoder, Format};
use http::Url;
use providers::{Provider, Verify, WireFormat};

/// Why a `model` block could not become a transport. Every variant names the
/// configuration key involved and says what to do.
#[derive(Debug, thiserror::Error)]
pub enum TransportError {
    #[error("model.provider: `{name}` is unknown; known providers: {}", known.join(", "))]
    UnknownProvider { name: String, known: Vec<&'static str> },
    #[error("model.{key}: required by provider {provider}; {hint}")]
    MissingOption { provider: &'static str, key: &'static str, hint: String },
    #[error("model.{key}: {path}: {reason}; run `foe login {provider}` or name the file in the model block")]
    Credential { provider: &'static str, key: &'static str, path: PathBuf, reason: String },
    #[error("model.base_url: {url}: {reason}")]
    BaseUrl { url: String, reason: String },
    #[error("home directory: {0}; name the credential file in the model block instead")]
    Home(String),
}

/// A `model` block resolved against the provider table, before any
/// credential is read. `foe plan` prints it; [`build`] consumes it.
#[derive(Debug, Clone)]
pub struct Plan {
    pub provider: &'static Provider,
    /// The block with every defaulted option filled in, including any
    /// resolved credential path. What `episode/start.contract.model` records.
    pub model: ModelConfig,
    /// The file the transport reads for its credential, when it reads one.
    pub credential_path: Option<PathBuf>,
}

impl Plan {
    /// One line for `foe plan`.
    pub fn describe(&self) -> String {
        let mut text = format!("{}/{}: {} format", self.provider.name, self.model.model, self.format_name());
        match &self.credential_path {
            Some(path) => text.push_str(&format!(", {} from {}", self.provider.auth.name(), path.display())),
            None => text.push_str(", no credential read by foe"),
        }
        text
    }

    /// The wire format this plan speaks, with Vertex resolved by model name.
    pub fn format_name(&self) -> &'static str {
        match self.provider.format {
            WireFormat::ManagedCloudByModel => {
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

/// Every provider name, in table order.
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

/// Resolves a `model` block: looks the provider up, fills a required or
/// existing convention credential path, and checks every required option.
/// Reads the managed-cloud convention file when it exists; reads no secret.
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
    let key = provider.auth.option_key();
    let default = paths::credentials_path(home, provider.name);
    if provider.auth == AuthKind::ManagedCloud && model.option(key).is_none() {
        // The managed-cloud convention file names the credentials file and
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
        Some(text) => Some(PathBuf::from(text)),
        None if provider.auth.credential_optional() => None,
        None => Some(default),
    };
    if let Some(path) = path {
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
    if let Some(url) = model.option("base_url") {
        Url::parse(url).map_err(|reason| TransportError::BaseUrl { url: url.to_string(), reason })?;
    }
    Ok(Plan { provider, model, credential_path })
}

/// Builds the transport a `model` block names. Reads the credential file
/// once, here, so that a construction error is reported before any
/// request.
pub fn build(config: &ModelConfig) -> Result<Arc<dyn Transport>, TransportError> {
    build_planned(&plan(config)?)
}

/// Builds the transport of a resolved plan.
pub fn build_planned(plan: &Plan) -> Result<Arc<dyn Transport>, TransportError> {
    build_http(plan)
}

/// Builds the HTTP client of a plan: the credential source, the wire
/// format, and the URL the provider row implies.
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
        WireFormat::Messages => (
            Box::new(format::messages::Messages::new(provider.name, Some(model.model.clone()), max, None)),
            base(provider.default_base_url)?.join(provider.path),
        ),
        WireFormat::Chat => (
            Box::new(format::chat::Chat::new(provider.name, model.model.clone(), max)),
            base(provider.default_base_url)?.join(provider.path),
        ),
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
        WireFormat::ManagedCloudByModel => vertex_route(provider, model, &base)?,
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
        let format =
            format::messages::Messages::new(provider.name, None, model.max_output_tokens, Some("vertex-2023-10-16"));
        let url = base(Some(&origin))?.join(&format!("{prefix}/anthropic/models/{name}:streamRawPredict"));
        return Ok((Box::new(format), url));
    }
    let include_thoughts = model.option("include_thoughts") != Some("false");
    let format = format::gemini::Gemini::new(provider.name, model.max_output_tokens, include_thoughts);
    let url = base(Some(&origin))?.join(&format!("{prefix}/google/models/{name}:streamGenerateContent?alt=sse"));
    Ok((Box::new(format), url))
}

/// Opens the credential source a plan names. The only place a secret is
/// read.
fn open_auth(plan: &Plan) -> Result<Arc<dyn Auth>, TransportError> {
    let provider = plan.provider;
    let key = provider.auth.option_key();
    let Some(path) = plan.credential_path.clone() else {
        debug_assert!(provider.auth.credential_optional());
        return Ok(Arc::new(auth::NoAuth));
    };
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
        AuthKind::ApiKey { header, .. } => Arc::new(auth::api_key::ApiKey::from_file(header, &path).map_err(fail)?),
        AuthKind::TokenFile { account_header, token_url, client_id } => {
            let client = auth::token_file::OAuthClient { token_url: token_url.into(), client_id: client_id.into() };
            Arc::new(auth::token_file::TokenFile::open(&path, client, account_header).map_err(fail)?)
        }
        AuthKind::ManagedCloud => Arc::new(auth::google::Google::open(&path).map_err(fail)?),
    })
}

/// One cheap authenticated request that proves a credential works, for
/// `foe login`. Returns a sentence for the person on failure.
pub async fn verify_credential(provider: &Provider, base_url: Option<&str>, auth: &dyn Auth) -> Result<(), String> {
    let headers = auth.headers().await.map_err(|e| e.to_string())?;
    match provider.verify {
        Verify::None | Verify::MintToken => Ok(()),
        Verify::GetJson(path) => {
            let text = base_url.or(provider.default_base_url).unwrap_or("");
            let url = Url::parse(text).map_err(|reason| format!("base_url {text}: {reason}"))?.join(path);
            let mut all: Vec<(&str, &str)> = provider.headers.to_vec();
            all.extend(headers.iter().map(|(k, v)| (k.as_str(), v.as_str())));
            all.push(("accept", "application/json"));
            let mut response = http::request("GET", &url, &all, &[]).await.map_err(|e| format!("{text}: {e}"))?;
            if (200..300).contains(&response.status) {
                return Ok(());
            }
            let mut body = String::new();
            let _ = (&mut response.body).take(MAX_ERROR_BODY).read_to_string(&mut body).await;
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
pub struct Client {
    route: foe_log::ModelRoute,
    provider: &'static str,
    url: Url,
    headers: Vec<(String, String)>,
    auth: Arc<dyn Auth>,
    format: Box<dyn Format>,
}

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
struct Exchange {
    /// The provider name used as the prefix of every error message.
    provider: &'static str,
    url: Url,
    headers: Vec<(String, String)>,
    body: Vec<u8>,
}

fn is_terminal(chunk: &Chunk) -> bool {
    matches!(chunk, Chunk::Done { .. } | Chunk::Error { .. })
}

/// Delivers decoded chunks directly to the sink. Cancellation drops the request and its socket owner.
async fn deliver(
    exchange: Exchange,
    auth: Arc<dyn Auth>,
    decoder: Box<dyn Decoder>,
    sink: &mut (dyn foe_core::ChunkSink + Send),
) {
    let provider = exchange.provider;
    let mut out = Outbox { sink, closed: false };
    let result = std::panic::AssertUnwindSafe(perform(exchange, auth, decoder, &mut out)).catch_unwind().await;
    match result {
        Ok(Err(error)) => out.push(error),
        _ if !out.closed => out.push(Chunk::Error {
            message: format!("{provider}: request ended without a final chunk"),
            retryable: true,
        }),
        _ => {}
    }
}

struct Outbox<'a> {
    sink: &'a mut (dyn foe_core::ChunkSink + Send),
    closed: bool,
}

impl Outbox<'_> {
    fn push(&mut self, chunk: Chunk) {
        if !self.closed {
            self.closed = is_terminal(&chunk);
            self.sink.push(chunk);
        }
    }
}

async fn perform(
    exchange: Exchange,
    auth: Arc<dyn Auth>,
    mut decoder: Box<dyn Decoder>,
    out: &mut Outbox<'_>,
) -> Result<(), Chunk> {
    let provider = exchange.provider;
    let credential = auth
        .headers()
        .await
        .map_err(|e| Chunk::Error { message: format!("{provider}: credential: {e}"), retryable: e.retryable() })?;
    let mut headers: Vec<(&str, &str)> = exchange.headers.iter().map(|(k, v)| (k.as_str(), v.as_str())).collect();
    headers.extend(credential.iter().map(|(k, v)| (k.as_str(), v.as_str())));
    let mut response = http::post(&exchange.url, &headers, &exchange.body)
        .await
        .map_err(|e| Chunk::Error { message: format!("{provider}: {e}"), retryable: e.retryable() })?;
    if !(200..300).contains(&response.status) {
        return Err(status_error(provider, &mut response).await);
    }
    while !out.closed {
        let event = sse::next_event(&mut response.body).await.map_err(|e| Chunk::Error {
            message: format!("{provider}: reading response body: {e}"),
            retryable: e.kind() != std::io::ErrorKind::InvalidData,
        })?;
        match event {
            Some(event) => decoder.event(&event, &mut |chunk| out.push(chunk)),
            None => out.push(decoder.end_of_stream()),
        }
    }
    Ok(())
}

/// Largest error body read for its message.
const MAX_ERROR_BODY: u64 = 64 * 1024;

/// Classifies a non-2xx response. The providers send a JSON body of the
/// form `{"error": {"type": ..., "message": ...}}`; its fields are quoted
/// when present and the raw body otherwise.
/// https://docs.anthropic.com/en/api/errors
/// https://platform.openai.com/docs/guides/error-codes
async fn status_error(provider: &str, response: &mut http::Response) -> Chunk {
    let status = response.status;
    // OpenAI sends `retry-after-ms` beside the standard `retry-after`;
    // only the delay-seconds form of the standard header is translated.
    let retry_after_ms = response
        .header("retry-after-ms")
        .and_then(|v| v.trim().parse::<u64>().ok())
        .or_else(|| response.header("retry-after").and_then(|v| v.trim().parse::<u64>().ok()).map(|s| s * 1000));
    let mut text = String::new();
    let _ = (&mut response.body).take(MAX_ERROR_BODY).read_to_string(&mut text).await;
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

#[cfg(test)]
#[path = "testserver_test.rs"]
mod testserver;

/// Shared helpers for the tests of this crate.
#[cfg(test)]
pub(crate) mod test_support {
    use std::ops::Deref;
    use std::path::{Path, PathBuf};

    pub struct ScratchDir(Option<tempfile::TempDir>);

    impl ScratchDir {
        fn path(&self) -> &Path {
            self.0.as_ref().unwrap().path()
        }
    }

    impl Deref for ScratchDir {
        type Target = Path;

        fn deref(&self) -> &Self::Target {
            self.path()
        }
    }

    impl AsRef<Path> for ScratchDir {
        fn as_ref(&self) -> &Path {
            self.path()
        }
    }

    impl Drop for ScratchDir {
        fn drop(&mut self) {
            let Some(mut dir) = self.0.take() else { return };
            if std::thread::panicking() {
                eprintln!("retained failed test directory: {}", dir.path().display());
                dir.disable_cleanup(true);
                return;
            }
            let path = dir.path().to_path_buf();
            dir.close().unwrap_or_else(|error| panic!("failed to remove test directory {}: {error}", path.display()));
        }
    }

    pub struct ScratchFile {
        path: PathBuf,
        _dir: ScratchDir,
    }

    impl Deref for ScratchFile {
        type Target = PathBuf;

        fn deref(&self) -> &Self::Target {
            &self.path
        }
    }

    impl AsRef<Path> for ScratchFile {
        fn as_ref(&self) -> &Path {
            &self.path
        }
    }

    /// A uniquely owned scratch directory that is removed after a successful
    /// test and retained when its owner is dropped during unwinding.
    pub fn scratch_dir(name: &str) -> ScratchDir {
        assert_eq!(Path::new(name).file_name(), Some(name.as_ref()), "scratch name must be one path component");
        ScratchDir(Some(tempfile::Builder::new().prefix(&format!("foe-transport-{name}-")).tempdir().unwrap()))
    }

    /// A scratch file path; the parent directory exists, the file may not.
    pub fn scratch(name: &str) -> ScratchFile {
        let dir = scratch_dir(&format!("file-{name}"));
        ScratchFile { path: dir.join(name), _dir: dir }
    }

    /// A fake home directory with nothing under it.
    pub fn fake_home(name: &str) -> ScratchDir {
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
    fn every_row_plans_with_its_credential_policy() {
        let home = fake_home("rows");
        for name in known_providers() {
            let mut config = model(name);
            match name {
                "compatible-http" => {
                    config.options.insert("base_url".into(), "http://127.0.0.1:11434/v1".into());
                }
                "vertex" => {
                    config.options.insert("project".into(), "p".into());
                    config.options.insert("location".into(), "us-east5".into());
                }
                _ => {}
            }
            let plan = plan_with_home(&config, &home).unwrap_or_else(|e| panic!("{name}: {e}"));
            assert_eq!(plan.provider.name, name);
            if plan.provider.auth.credential_optional() {
                assert!(plan.credential_path.is_none(), "{name}");
                assert!(plan.model.option(plan.provider.auth.option_key()).is_none(), "{name}");
            } else {
                let key = plan.provider.auth.option_key();
                let expected = home.join(format!(".config/foe/credentials/{name}.json"));
                assert_eq!(plan.credential_path.as_deref(), Some(expected.as_path()), "{name}");
                assert_eq!(plan.model.option(key), expected.to_str(), "{name}: the record names the path");
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
        let err = plan_with_home(&model("compatible-http"), &fake_home("required")).unwrap_err().to_string();
        assert!(err.starts_with("model.base_url: required by provider compatible-http; "), "{err}");
        let err = plan_with_home(&model("vertex"), &fake_home("required")).unwrap_err().to_string();
        assert!(err.starts_with("model.project: required by provider vertex; "), "{err}");
    }

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
        let err = build_planned(&plan_with_home(&model("anthropic"), &fake_home("missing")).unwrap())
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
            ("compatible-http", "127.0.0.1", "/v1/chat/completions"),
        ];
        for (name, host, path) in cases {
            let mut config = model(name);
            if name == "compatible-http" {
                config.options.insert("base_url".into(), "http://127.0.0.1:11434/v1".into());
            } else {
                config.options.insert("api_key_file".into(), key.to_string_lossy().into_owned());
            }
            let transport = build(&config).unwrap_or_else(|e| panic!("{name}: {e}"));
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
            WireFormat::ManagedCloudByModel => vertex_route(provider, model, &base).unwrap(),
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

    #[tokio::test]
    async fn verification_accepts_2xx_and_explains_401() {
        use crate::testserver::{Reply, Server};
        let server = Server::start(vec![
            Reply::full(200, r#"{"data":[]}"#),
            Reply::full(401, r#"{"error":{"type":"authentication_error","message":"invalid x-api-key"}}"#),
        ]);
        let provider = provider_info("anthropic").unwrap();
        let auth = auth::api_key::ApiKey::new(auth::KeyHeader::XApiKey, "sk-test".into());
        verify_credential(provider, Some(&server.base()), &auth).await.unwrap();
        let err = verify_credential(provider, Some(&server.base()), &auth).await.unwrap_err();
        assert_eq!(err, "the provider rejected the key (HTTP 401: authentication_error: invalid x-api-key)");
        let seen = server.requests();
        assert_eq!((seen[0].method.as_str(), seen[0].path.as_str()), ("GET", "/v1/models"));
        assert_eq!(seen[0].header("x-api-key"), Some("sk-test"));
        assert_eq!(seen[0].header("anthropic-version"), Some("2023-06-01"));
    }
}
