//! Credential sources: where a provider's secret comes from and how it
//! becomes request headers.
//!
//! Three sources exist. An API key is a string in a file. A token file holds
//! an OAuth access token with the refresh token that renews it. Google
//! credentials are an application-default-credentials file or a service
//! account file, exchanged for a short-lived access token. Each source
//! implements [`Auth`], which the request loop calls once per request on a
//! blocking thread, so a source may refresh a token there.

use std::path::PathBuf;

pub mod api_key;
pub mod google;
pub mod login;
pub mod token_file;

/// Produces the headers that authenticate one request.
pub trait Auth: Send + Sync {
    fn headers(&self) -> Result<Vec<(String, String)>, AuthError>;
}

/// A source that adds nothing, for contracts that hold their own credentials.
pub struct NoAuth;

impl Auth for NoAuth {
    fn headers(&self) -> Result<Vec<(String, String)>, AuthError> {
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
    },
    /// An OAuth token file renewed at `token_url` as `client_id`.
    /// `account_header`, when set, is sent with the token's `account_id` as
    /// its value.
    TokenFile {
        account_header: Option<&'static str>,
        token_url: &'static str,
        client_id: &'static str,
    },
    Google,
    /// The contract holds its own credentials.
    None,
}

impl AuthKind {
    /// The `model` key that names the credential file explicitly.
    pub fn option_key(&self) -> Option<&'static str> {
        match self {
            AuthKind::ApiKey { .. } => Some("api_key_file"),
            AuthKind::TokenFile { .. } => Some("token_file"),
            AuthKind::Google => Some("credentials_file"),
            AuthKind::None => None,
        }
    }

    /// A short noun phrase for `foe plan` and `foe login`.
    pub fn name(&self) -> &'static str {
        match self {
            AuthKind::ApiKey { .. } => "api key",
            AuthKind::TokenFile { .. } => "token file",
            AuthKind::Google => "google credentials",
            AuthKind::None => "none",
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
pub(crate) fn post_form(endpoint: &str, fields: &[(&str, &str)]) -> Result<serde_json::Value, AuthError> {
    use std::io::Read;
    let fail =
        |reason: String, retryable: bool| AuthError::Endpoint { endpoint: endpoint.to_string(), reason, retryable };
    let url = crate::http::Url::parse(endpoint).map_err(|e| fail(e, false))?;
    let body = form_encode(fields);
    let headers = [("content-type", "application/x-www-form-urlencoded"), ("accept", "application/json")];
    let mut response =
        crate::http::post(&url, &headers, body.as_bytes()).map_err(|e| fail(e.to_string(), e.retryable()))?;
    let mut text = String::new();
    let _ = (&mut response.body).take(64 * 1024).read_to_string(&mut text);
    if !(200..300).contains(&response.status) {
        let retryable = response.status == 429 || (500..600).contains(&response.status);
        return Err(fail(format!("HTTP {}: {}", response.status, crate::describe_error_body(&text)), retryable));
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
}
