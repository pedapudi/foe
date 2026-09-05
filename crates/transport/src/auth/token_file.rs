//! An OAuth token file: the access token, when it expires, and the refresh
//! token that renews an ordinary mutable file.
//!
//! The file is JSON: `{ "access": ..., "refresh": ..., "expires": N,
//! "account_id": ... }`, with `expires` in milliseconds since the Unix
//! epoch and `account_id` present when the provider's token carries one.
//! An access-only file omits `refresh`. Before each request the access token
//! is checked. A mutable token within [`REFRESH_MARGIN`] of expiry is
//! refreshed at the provider's token endpoint. The file is then rewritten
//! atomically with mode 0600, so a second process sees a whole token. An
//! access-only file fails locally when it reaches the same margin.
//!
//! The [`codex`] module holds the public OAuth parameters of the ChatGPT
//! Codex client, which `foe login openai-codex` drives through an
//! authorization-code flow with PKCE (RFC 7636).

use std::path::{Path, PathBuf};
use std::time::Duration;
use tokio::sync::Mutex;

use base64::Engine;
use serde::{Deserialize, Serialize};

use super::{now_ms, post_form, Auth, AuthError};

/// A token this close to expiry is refreshed before use.
pub const REFRESH_MARGIN: Duration = Duration::from_secs(60);

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Token {
    pub access: String,
    /// Empty when the file is access-only and cannot renew itself.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub refresh: String,
    /// Milliseconds since the Unix epoch.
    pub expires: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub account_id: Option<String>,
}

impl Token {
    pub fn needs_refresh(&self, now: u64) -> bool {
        self.expires <= now + REFRESH_MARGIN.as_millis() as u64
    }
}

/// The public parameters of an OAuth client: where tokens are issued and
/// which client id to present.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OAuthClient {
    pub token_url: String,
    pub client_id: String,
}

impl OAuthClient {
    /// Exchanges a refresh token for a new token pair.
    pub async fn refresh(&self, refresh_token: &str) -> Result<Token, AuthError> {
        let fields =
            [("grant_type", "refresh_token"), ("refresh_token", refresh_token), ("client_id", &self.client_id)];
        token_from_response(&self.token_url, post_form(&self.token_url, &fields).await?, refresh_token)
    }

    /// Exchanges an authorization code and its PKCE verifier for a token.
    pub async fn exchange_code(&self, code: &str, verifier: &str, redirect_uri: &str) -> Result<Token, AuthError> {
        let fields = [
            ("grant_type", "authorization_code"),
            ("client_id", &self.client_id),
            ("code", code),
            ("code_verifier", verifier),
            ("redirect_uri", redirect_uri),
        ];
        token_from_response(&self.token_url, post_form(&self.token_url, &fields).await?, "")
    }
}

/// Reads a token response. A response without a new refresh token keeps
/// the previous one, which some servers expect.
fn token_from_response(endpoint: &str, json: serde_json::Value, previous_refresh: &str) -> Result<Token, AuthError> {
    let fail = |reason: String| AuthError::Endpoint { endpoint: endpoint.to_string(), reason, retryable: false };
    let access = json["access_token"]
        .as_str()
        .filter(|s| !s.is_empty())
        .ok_or_else(|| fail("response has no access_token".into()))?;
    let refresh = json["refresh_token"].as_str().filter(|s| !s.is_empty()).unwrap_or(previous_refresh);
    if refresh.is_empty() {
        return Err(fail("response has no refresh_token".into()));
    }
    let expires_in = json["expires_in"].as_u64().ok_or_else(|| fail("response has no numeric expires_in".into()))?;
    Ok(Token {
        access: access.to_string(),
        refresh: refresh.to_string(),
        expires: now_ms() + expires_in * 1000,
        account_id: account_id_from_jwt(access),
    })
}

/// The ChatGPT account id carried in the access token's
/// `https://api.openai.com/auth` claim, when the token is a JWT with one.
pub fn account_id_from_jwt(access: &str) -> Option<String> {
    let payload = access.split('.').nth(1)?;
    let bytes = base64::engine::general_purpose::URL_SAFE_NO_PAD.decode(payload).ok()?;
    let claims: serde_json::Value = serde_json::from_slice(&bytes).ok()?;
    claims["https://api.openai.com/auth"]["chatgpt_account_id"].as_str().map(str::to_string)
}

pub fn read_token(path: &Path) -> Result<Token, AuthError> {
    let fail = |reason: String| AuthError::Credential { path: path.to_path_buf(), reason };
    let text = std::fs::read_to_string(path).map_err(|e| fail(e.to_string()))?;
    let token: Token = serde_json::from_str(&text).map_err(|e| fail(format!("not a token file: {e}")))?;
    if token.access.is_empty() {
        return Err(fail("access token is empty".into()));
    }
    Ok(token)
}

pub fn write_token(path: &Path, token: &Token) -> std::io::Result<()> {
    let text = serde_json::to_string_pretty(token).expect("a token serializes");
    crate::paths::write_private(path, format!("{text}\n").as_bytes())
}

/// A token file kept fresh across requests.
pub struct TokenFile {
    path: PathBuf,
    client: OAuthClient,
    account_header: Option<&'static str>,
    state: Mutex<Token>,
}

impl TokenFile {
    pub fn open(
        path: &Path,
        client: OAuthClient,
        account_header: Option<&'static str>,
    ) -> Result<TokenFile, AuthError> {
        let token = read_token(path)?;
        Ok(TokenFile { path: path.to_path_buf(), client, account_header, state: Mutex::new(token) })
    }

    /// The current token, refreshed and rewritten when within the margin.
    pub async fn current(&self) -> Result<Token, AuthError> {
        let mut state = self.state.lock().await;
        if state.needs_refresh(now_ms()) {
            if state.refresh.is_empty() {
                return Err(AuthError::Credential {
                    path: self.path.clone(),
                    reason: "access token needs refresh, but this access-only token file has no refresh token".into(),
                });
            }
            let mut fresh = self.client.refresh(&state.refresh).await?;
            if fresh.account_id.is_none() {
                fresh.account_id = state.account_id.clone();
            }
            write_token(&self.path, &fresh)
                .map_err(|e| AuthError::Credential { path: self.path.clone(), reason: format!("rewriting: {e}") })?;
            *state = fresh;
        }
        Ok(state.clone())
    }
}

#[async_trait::async_trait]
impl Auth for TokenFile {
    async fn headers(&self) -> Result<Vec<(String, String)>, AuthError> {
        let token = self.current().await?;
        let mut headers = vec![("authorization".to_string(), format!("Bearer {}", token.access))];
        if let (Some(name), Some(id)) = (self.account_header, token.account_id) {
            headers.push((name.to_string(), id));
        }
        Ok(headers)
    }
}

/// The public OAuth parameters of the ChatGPT Codex client.
///
/// Authorization endpoint, token endpoint, client id, scopes, and redirect
/// URI are those the Codex command-line tool registers with
/// `https://auth.openai.com`; the redirect URI is fixed by that registration,
/// so the login listener binds port 1455. The flow is authorization code
/// with PKCE (RFC 7636, method S256).
pub mod codex {
    use base64::Engine;
    use sha2::{Digest, Sha256};

    pub const CLIENT_ID: &str = "app_EMoamEEZ73f0CkXaXp7hrann";
    pub const AUTHORIZE_URL: &str = "https://auth.openai.com/oauth/authorize";
    pub const TOKEN_URL: &str = "https://auth.openai.com/oauth/token";
    pub const SCOPE: &str = "openid profile email offline_access";
    pub const REDIRECT_PORT: u16 = 1455;
    pub const REDIRECT_PATH: &str = "/auth/callback";

    pub fn client() -> super::OAuthClient {
        super::OAuthClient { token_url: TOKEN_URL.to_string(), client_id: CLIENT_ID.to_string() }
    }

    pub fn redirect_uri(port: u16) -> String {
        format!("http://localhost:{port}{REDIRECT_PATH}")
    }

    /// A PKCE verifier and its S256 challenge.
    #[derive(Debug, Clone)]
    pub struct Pkce {
        pub verifier: String,
        pub challenge: String,
    }

    pub fn pkce() -> std::io::Result<Pkce> {
        let verifier = base64url(&random_bytes(32)?);
        let challenge = base64url(&Sha256::digest(verifier.as_bytes()));
        Ok(Pkce { verifier, challenge })
    }

    /// An opaque value that the callback must echo back.
    pub fn state() -> std::io::Result<String> {
        Ok(base64url(&random_bytes(16)?))
    }

    /// The URL the browser opens. `authorize_url` is [`AUTHORIZE_URL`]
    /// outside tests.
    pub fn authorization_url(authorize_url: &str, challenge: &str, state: &str, redirect_uri: &str) -> String {
        let query = super::super::form_encode(&[
            ("response_type", "code"),
            ("client_id", CLIENT_ID),
            ("redirect_uri", redirect_uri),
            ("scope", SCOPE),
            ("code_challenge", challenge),
            ("code_challenge_method", "S256"),
            ("state", state),
            ("id_token_add_organizations", "true"),
            ("codex_cli_simplified_flow", "true"),
            ("originator", "foe"),
        ]);
        format!("{authorize_url}?{query}")
    }

    fn base64url(bytes: &[u8]) -> String {
        base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(bytes)
    }

    /// Bytes from the kernel's random source. No environment is consulted.
    fn random_bytes(n: usize) -> std::io::Result<Vec<u8>> {
        use std::io::Read;
        let mut bytes = vec![0u8; n];
        std::fs::File::open("/dev/urandom")?.read_exact(&mut bytes)?;
        Ok(bytes)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_support::scratch;
    use crate::testserver::{Reply, Server};
    use std::os::unix::fs::PermissionsExt;

    /// A JWT whose payload carries a ChatGPT account id; the signature is
    /// not checked by anything here.
    fn jwt(account: &str) -> String {
        let payload = serde_json::json!({ "https://api.openai.com/auth": { "chatgpt_account_id": account } });
        let body = base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(payload.to_string());
        format!("eyJhbGciOiJub25lIn0.{body}.sig")
    }

    #[tokio::test]
    async fn a_token_near_expiry_is_refreshed_and_rewritten_atomically() {
        let server = Server::start(vec![Reply::full(
            200,
            &serde_json::json!({ "access_token": jwt("acct_42"), "refresh_token": "r2", "expires_in": 3600 })
                .to_string(),
        )]);
        let path = scratch("token-refresh.json");
        let stale = Token { access: "old".into(), refresh: "r1".into(), expires: now_ms() + 10_000, account_id: None };
        write_token(&path, &stale).unwrap();
        let client = OAuthClient { token_url: format!("{}/oauth/token", server.base()), client_id: "cid".into() };
        let file = TokenFile::open(&path, client, Some("chatgpt-account-id")).unwrap();
        let headers = file.headers().await.unwrap();
        assert!(headers[0].1.starts_with("Bearer eyJ"), "{headers:?}");
        assert_eq!(headers[1], ("chatgpt-account-id".to_string(), "acct_42".to_string()));
        let seen = server.requests();
        assert_eq!(seen[0].path, "/oauth/token");
        assert_eq!(seen[0].header("content-type"), Some("application/x-www-form-urlencoded"));
        assert_eq!(seen[0].body, "grant_type=refresh_token&refresh_token=r1&client_id=cid");
        let written = read_token(&path).unwrap();
        assert_eq!(written.refresh, "r2");
        assert_eq!(written.account_id.as_deref(), Some("acct_42"));
        assert!(written.expires > now_ms() + 3_500_000);
        assert_eq!(std::fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
        // A second request within the new lifetime does not touch the endpoint.
        file.headers().await.unwrap();
        assert_eq!(server.requests().len(), 1);
    }

    #[tokio::test]
    async fn a_fresh_token_is_used_as_is_and_a_refused_refresh_names_the_endpoint() {
        let path = scratch("token-fresh.json");
        let fresh = Token {
            access: "a".into(),
            refresh: "r".into(),
            expires: now_ms() + 3_600_000,
            account_id: Some("acct".into()),
        };
        write_token(&path, &fresh).unwrap();
        let client = OAuthClient { token_url: "http://127.0.0.1:9/oauth/token".into(), client_id: "cid".into() };
        let file = TokenFile::open(&path, client, None).unwrap();
        assert_eq!(file.headers().await.unwrap(), vec![("authorization".to_string(), "Bearer a".to_string())]);

        let server =
            Server::start(vec![Reply::full(401, r#"{"error":"invalid_grant","error_description":"revoked"}"#)]);
        let path = scratch("token-revoked.json");
        write_token(&path, &Token { expires: 0, ..fresh }).unwrap();
        let client = OAuthClient { token_url: format!("{}/oauth/token", server.base()), client_id: "cid".into() };
        let err = TokenFile::open(&path, client, None).unwrap().headers().await.unwrap_err();
        assert!(!err.retryable());
        assert!(err.to_string().contains("/oauth/token: HTTP 401"), "{err}");
    }

    #[tokio::test]
    async fn an_access_only_token_works_until_refresh_is_required() {
        let path = scratch("token-access-only.json");
        let fresh = Token {
            access: "leased-access".into(),
            refresh: String::new(),
            expires: now_ms() + 3_600_000,
            account_id: Some("acct".into()),
        };
        write_token(&path, &fresh).unwrap();
        let text = std::fs::read_to_string(&path).unwrap();
        assert!(!text.contains("refresh"), "{text}");

        let client = OAuthClient { token_url: "http://127.0.0.1:9/oauth/token".into(), client_id: "cid".into() };
        let file = TokenFile::open(&path, client.clone(), None).unwrap();
        assert_eq!(
            file.headers().await.unwrap(),
            vec![("authorization".to_string(), "Bearer leased-access".to_string())]
        );

        write_token(&path, &Token { expires: 0, ..fresh }).unwrap();
        let error = TokenFile::open(&path, client, None).unwrap().headers().await.unwrap_err();
        assert!(matches!(&error, AuthError::Credential { .. }), "{error}");
        assert!(error.to_string().contains("access-only token file has no refresh token"), "{error}");
        assert!(!error.retryable());
    }

    #[tokio::test]
    async fn code_exchange_and_authorization_url_follow_pkce() {
        let server =
            Server::start(vec![Reply::full(200, r#"{"access_token":"opaque","refresh_token":"r","expires_in":60}"#)]);
        let client = OAuthClient { token_url: format!("{}/oauth/token", server.base()), client_id: "cid".into() };
        let token = client.exchange_code("c0de", "v3rifier", "http://localhost:1455/auth/callback").await.unwrap();
        assert_eq!((token.access.as_str(), token.refresh.as_str(), token.account_id), ("opaque", "r", None));
        assert_eq!(
            server.requests()[0].body,
            "grant_type=authorization_code&client_id=cid&code=c0de&code_verifier=v3rifier&redirect_uri=http%3A%2F%2Flocalhost%3A1455%2Fauth%2Fcallback"
        );
        let pkce = codex::pkce().unwrap();
        assert_eq!(pkce.verifier.len(), 43);
        let expected =
            base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(sha2::Sha256::digest(pkce.verifier.as_bytes()));
        assert_eq!(pkce.challenge, expected);
        let url = codex::authorization_url(codex::AUTHORIZE_URL, &pkce.challenge, "st8", &codex::redirect_uri(1455));
        assert!(url.starts_with("https://auth.openai.com/oauth/authorize?response_type=code&client_id=app_"));
        assert!(url.contains("&code_challenge_method=S256&state=st8&"));
        assert!(url.ends_with("&originator=foe"));
    }

    #[test]
    fn malformed_token_files_name_the_path() {
        let path = scratch("token-bad.json");
        std::fs::write(&path, "{\"access\": \"\"}").unwrap();
        let err = read_token(&path).unwrap_err().to_string();
        assert!(err.contains("token-bad.json: not a token file"), "{err}");
        std::fs::write(&path, "{\"access\": \"\", \"refresh\": \"\", \"expires\": 1}").unwrap();
        let err = read_token(&path).unwrap_err().to_string();
        assert!(err.ends_with("access token is empty"), "{err}");
    }

    #[tokio::test]
    async fn cancelled_refresh_closes_its_socket_and_releases_waiting_requests() {
        // docs/models.md: cancellation interrupts refresh I/O and releases the credential cache lock.
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let client = OAuthClient {
            token_url: format!("http://{}/token", listener.local_addr().unwrap()),
            client_id: "fixture".into(),
        };
        let path = scratch("cancelled-refresh.json");
        let stale = Token { access: "expired".into(), refresh: "refresh".into(), expires: 0, account_id: None };
        write_token(&path, &stale).unwrap();
        let original = std::fs::read(&path).unwrap();
        let file = TokenFile::open(&path, client, None).unwrap();
        let mut first = Box::pin(file.headers());
        let mut peer = tokio::select! {
            result = &mut first => panic!("refresh completed without a response: {result:?}"),
            stream = crate::testserver::accept_request(&listener) => stream,
        };
        let mut waiting = Box::pin(file.headers());
        assert!(futures_util::poll!(&mut waiting).is_pending());
        drop(first);
        assert_eq!(peer.read(&mut [0; 1]).await.unwrap(), 0);
        assert_eq!(std::fs::read(&path).unwrap(), original);
        let mut peer = tokio::select! {
            result = &mut waiting => panic!("refresh completed without a response: {result:?}"),
            stream = crate::testserver::accept_request(&listener) => stream,
        };
        let body = r#"{"access_token":"fresh","refresh_token":"renewed","expires_in":3600}"#;
        let response = format!("HTTP/1.1 200 OK\r\ncontent-length: {}\r\n\r\n{body}", body.len());
        peer.write_all(response.as_bytes()).await.unwrap();
        assert_eq!(waiting.await.unwrap(), vec![("authorization".into(), "Bearer fresh".into())]);
        assert_eq!(read_token(&path).unwrap().refresh, "renewed");
    }

    use sha2::Digest;
}
