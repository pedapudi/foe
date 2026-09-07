//! Credential sources: where a provider's secret comes from and how it
//! becomes request headers.
//!
//! Three sources exist. An API key is a string in a file. A token file holds
//! an OAuth access token with the refresh token that renews it. Google
//! credentials are an application-default-credentials file or a service
//! account file, exchanged for a short-lived access token. Each source
//! implements [`Auth`], which authorizes each request asynchronously.
//! Cancellation interrupts token-endpoint I/O and waiting for the cache lock.

use std::path::PathBuf;

pub mod api_key;
pub mod google;
pub mod login;
pub mod token_file;

/// Produces the headers that authenticate one request.
#[async_trait::async_trait]
pub trait Auth: Send + Sync {
    async fn headers(&self) -> Result<Vec<(String, String)>, AuthError>;
}

/// A source that adds no authentication header.
pub struct NoAuth;

#[async_trait::async_trait]
impl Auth for NoAuth {
    async fn headers(&self) -> Result<Vec<(String, String)>, AuthError> {
        Ok(Vec::new())
    }
}

#[derive(Debug, thiserror::Error)]
pub enum AuthError {
    /// The credential file is missing, unreadable, or malformed.
    #[error("{path}: {reason}")]
    Credential { path: PathBuf, reason: String },
    /// A token endpoint refused or could not be reached.
    #[error("{endpoint}: {reason}")]
    Endpoint { endpoint: String, reason: String, retryable: bool },
}

impl AuthError {
    pub fn retryable(&self) -> bool {
        matches!(self, AuthError::Endpoint { retryable: true, .. })
    }
}

/// How the key travels when the source is an API key.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum KeyHeader {
    /// `x-api-key: KEY`.
    XApiKey,
    /// `authorization: Bearer KEY`.
    Bearer,
}

/// The credential source a provider row names.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AuthKind {
    ApiKey {
        header: KeyHeader,
        /// A missing `api_key_file` sends no authentication header.
        optional: bool,
    },
    /// An OAuth token file renewed at `token_url` as `client_id`.
    /// `account_header`, when set, is sent with the token's `account_id` as
    /// its value.
    TokenFile {
        account_header: Option<&'static str>,
        token_url: &'static str,
        client_id: &'static str,
    },
    ManagedCloud,
}

impl AuthKind {
    /// The `model` key that names the credential file explicitly.
    pub fn option_key(&self) -> &'static str {
        match self {
            AuthKind::ApiKey { .. } => "api_key_file",
            AuthKind::TokenFile { .. } => "token_file",
            AuthKind::ManagedCloud => "credentials_file",
        }
    }

    /// Whether requests may omit this credential source.
    pub fn credential_optional(&self) -> bool {
        matches!(self, AuthKind::ApiKey { optional: true, .. })
    }

    /// A short noun phrase for `foe plan` and `foe login`.
    pub fn name(&self) -> &'static str {
        match self {
            AuthKind::ApiKey { .. } => "api key",
            AuthKind::TokenFile { .. } => "token file",
            AuthKind::ManagedCloud => "managed-cloud credentials",
        }
    }
}

/// Milliseconds since the Unix epoch.
pub(crate) fn now_ms() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_millis() as u64).unwrap_or(0)
}

/// `application/x-www-form-urlencoded` encoding of a field list.
pub(crate) fn form_encode(fields: &[(&str, &str)]) -> String {
    fn escape(text: &str, out: &mut String) {
        for byte in text.bytes() {
            match byte {
                b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => out.push(byte as char),
                b' ' => out.push('+'),
                other => out.push_str(&format!("%{other:02X}")),
            }
        }
    }
    let mut out = String::new();
    for (i, (name, value)) in fields.iter().enumerate() {
        if i > 0 {
            out.push('&');
        }
        escape(name, &mut out);
        out.push('=');
        escape(value, &mut out);
    }
    out
}

/// Posts a form to a token endpoint and returns the JSON body of a 2xx
/// response. Any other status is an `Endpoint` error quoting the body;
/// 429 and 5xx are retryable.
pub(crate) async fn post_form(endpoint: &str, fields: &[(&str, &str)]) -> Result<serde_json::Value, AuthError> {
    use tokio::io::AsyncReadExt;
    let fail =
        |reason: String, retryable: bool| AuthError::Endpoint { endpoint: endpoint.to_string(), reason, retryable };
    let url = crate::http::Url::parse(endpoint).map_err(|e| fail(e, false))?;
    let body = form_encode(fields);
    let headers = [("content-type", "application/x-www-form-urlencoded"), ("accept", "application/json")];
    let mut response =
        crate::http::post(&url, &headers, body.as_bytes()).await.map_err(|e| fail(e.to_string(), e.retryable()))?;
    let mut text = String::new();
    let success = (200..300).contains(&response.status);
    let read = (&mut response.body).take(64 * 1024 + u64::from(success)).read_to_string(&mut text).await;
    if !success {
        let retryable = response.status == 429 || (500..600).contains(&response.status);
        return Err(fail(format!("HTTP {}: {}", response.status, crate::describe_error_body(&text)), retryable));
    }
    read.map_err(|e| fail(format!("reading response body: {e}"), e.kind() != std::io::ErrorKind::InvalidData))?;
    if text.len() > 64 * 1024 {
        return Err(fail("response body exceeds 65536 bytes".into(), false));
    }
    serde_json::from_str(&text).map_err(|e| fail(format!("response is not JSON: {e}"), false))
}

#[cfg(test)]
mod tests {
    #[test]
    fn form_encoding_escapes_reserved_bytes() {
        let text = super::form_encode(&[("grant_type", "refresh_token"), ("refresh_token", "a+b/c=d e&f")]);
        assert_eq!(text, "grant_type=refresh_token&refresh_token=a%2Bb%2Fc%3Dd+e%26f");
    }

    async fn form_response(raw: Vec<u8>) -> Result<serde_json::Value, super::AuthError> {
        use tokio::io::AsyncWriteExt;
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let endpoint = format!("http://{}/token", listener.local_addr().unwrap());
        let server = tokio::spawn(async move {
            let mut socket = crate::testserver::accept_request(&listener).await;
            socket.write_all(&raw).await.unwrap();
            socket.shutdown().await.unwrap();
        });
        let result = super::post_form(&endpoint, &[]).await;
        server.await.unwrap();
        result
    }

    /// docs/models.md "HTTP requests and cancellation": credential JSON requires a complete body.
    #[tokio::test]
    async fn credential_success_rejects_incomplete_or_malformed_framing() {
        for (response, retryable) in [
            ("HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\n{}", true),
            ("HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n{}\r\n", true),
            ("HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\n{}\r\nQ\r\n", false),
        ] {
            let error = form_response(response.as_bytes().to_vec()).await.expect_err("framing must be complete");
            assert_eq!(error.retryable(), retryable, "{error}");
        }
    }

    /// docs/models.md "HTTP requests and cancellation": credential bodies have a 64 KiB limit.
    #[tokio::test]
    async fn credential_success_requires_bounded_utf8_json() {
        for bytes in [64 * 1024, 64 * 1024 + 1] {
            let body = format!("{{}}{}", " ".repeat(bytes - 2));
            let raw = format!("HTTP/1.1 200 OK\r\nContent-Length: {bytes}\r\n\r\n{body}");
            let result = form_response(raw.into_bytes()).await;
            if bytes == 64 * 1024 {
                assert_eq!(result.unwrap(), serde_json::json!({}));
            } else {
                let error = result.unwrap_err();
                assert!(!error.retryable(), "{error}");
                assert!(error.to_string().contains("65536"), "{error}");
            }
        }
        let error = form_response(b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\n{}\xff".to_vec()).await.unwrap_err();
        assert!(!error.retryable(), "{error}");
    }
}
