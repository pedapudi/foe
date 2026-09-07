//! Google credentials exchanged for a short-lived access token.
//!
//! Two file shapes are accepted, both as written by Google's tools.
//!
//! - Application default credentials, `"type": "authorized_user"`, as
//!   `gcloud auth application-default login` writes them: a client id,
//!   a client secret, and a refresh token. The refresh token is exchanged at
//!   the token endpoint for an access token.
//!   https://cloud.google.com/docs/authentication/application-default-credentials
//! - A service account key, `"type": "service_account"`: a client email
//!   and an RSA private key. A JWT signed with RS256 asserting the account
//!   is exchanged at the key's `token_uri` for an access token.
//!   https://developers.google.com/identity/protocols/oauth2/service-account
//!
//! The access token is cached in memory and minted again when within
//! [`REFRESH_MARGIN`] of expiry. Nothing is written back to the file.

use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};
use tokio::sync::Mutex;

use base64::Engine;

use super::{post_form, Auth, AuthError};

/// The scope every Vertex AI request needs.
pub const SCOPE: &str = "https://www.googleapis.com/auth/cloud-platform";
/// The token endpoint for application default credentials.
pub const TOKEN_URL: &str = "https://oauth2.googleapis.com/token";
/// A token this close to expiry is minted again before use.
pub const REFRESH_MARGIN: Duration = Duration::from_secs(60);
/// Lifetime claimed by a service-account assertion; the maximum Google allows.
const ASSERTION_LIFETIME: u64 = 3600;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Credentials {
    AuthorizedUser { client_id: String, client_secret: String, refresh_token: String, token_uri: String },
    ServiceAccount { client_email: String, private_key_pem: String, token_uri: String },
}

impl Credentials {
    pub fn kind(&self) -> &'static str {
        match self {
            Credentials::AuthorizedUser { .. } => "authorized_user",
            Credentials::ServiceAccount { .. } => "service_account",
        }
    }

    pub fn token_uri(&self) -> &str {
        match self {
            Credentials::AuthorizedUser { token_uri, .. } | Credentials::ServiceAccount { token_uri, .. } => token_uri,
        }
    }
}

pub fn read_credentials(path: &Path) -> Result<Credentials, AuthError> {
    let fail = |reason: String| AuthError::Credential { path: path.to_path_buf(), reason };
    let text = std::fs::read_to_string(path).map_err(|e| fail(e.to_string()))?;
    let json: serde_json::Value = serde_json::from_str(&text).map_err(|e| fail(format!("not JSON: {e}")))?;
    let field = |name: &str| {
        json[name]
            .as_str()
            .filter(|s| !s.is_empty())
            .map(str::to_string)
            .ok_or_else(|| fail(format!("`{name}` is missing or empty")))
    };
    let token_uri = json["token_uri"].as_str().unwrap_or(TOKEN_URL).to_string();
    match json["type"].as_str().unwrap_or("") {
        "authorized_user" => Ok(Credentials::AuthorizedUser {
            client_id: field("client_id")?,
            client_secret: field("client_secret")?,
            refresh_token: field("refresh_token")?,
            token_uri,
        }),
        "service_account" => Ok(Credentials::ServiceAccount {
            client_email: field("client_email")?,
            private_key_pem: field("private_key")?,
            token_uri,
        }),
        other => Err(fail(format!(
            "`type` is {other:?}; expected authorized_user (gcloud auth application-default login) or service_account"
        ))),
    }
}

/// Mints one access token. Returns the token and its lifetime in seconds.
pub async fn mint(credentials: &Credentials) -> Result<(String, u64), AuthError> {
    let endpoint = credentials.token_uri();
    let json = match credentials {
        Credentials::AuthorizedUser { client_id, client_secret, refresh_token, .. } => {
            post_form(
                endpoint,
                &[
                    ("grant_type", "refresh_token"),
                    ("client_id", client_id),
                    ("client_secret", client_secret),
                    ("refresh_token", refresh_token),
                ],
            )
            .await?
        }
        Credentials::ServiceAccount { client_email, private_key_pem, token_uri } => {
            let now = super::now_ms() / 1000;
            let assertion = service_account_assertion(client_email, private_key_pem, token_uri, now)
                .map_err(|reason| AuthError::Endpoint { endpoint: endpoint.to_string(), reason, retryable: false })?;
            post_form(
                endpoint,
                &[("grant_type", "urn:ietf:params:oauth:grant-type:jwt-bearer"), ("assertion", &assertion)],
            )
            .await?
        }
    };
    let fail = |reason: String| AuthError::Endpoint { endpoint: endpoint.to_string(), reason, retryable: false };
    let access = json["access_token"]
        .as_str()
        .filter(|s| !s.is_empty())
        .ok_or_else(|| fail("response has no access_token".into()))?;
    let expires_in = json["expires_in"].as_u64().ok_or_else(|| fail("response has no numeric expires_in".into()))?;
    Ok((access.to_string(), expires_in))
}

/// The signed JWT a service account presents: header `{"alg":"RS256",
/// "typ":"JWT"}`, claims `iss`, `scope`, `aud`, `iat`, `exp`, signature
/// RSASSA-PKCS1-v1_5 with SHA-256 over the base64url-encoded header and
/// claims.
pub fn service_account_assertion(email: &str, private_key_pem: &str, aud: &str, iat: u64) -> Result<String, String> {
    let url = base64::engine::general_purpose::URL_SAFE_NO_PAD;
    let header = url.encode(r#"{"alg":"RS256","typ":"JWT"}"#);
    let claims = serde_json::json!({
        "iss": email,
        "scope": SCOPE,
        "aud": aud,
        "iat": iat,
        "exp": iat + ASSERTION_LIFETIME,
    });
    let signing_input = format!("{header}.{}", url.encode(claims.to_string()));
    let der = pem_to_der(private_key_pem)?;
    let key = ring::signature::RsaKeyPair::from_pkcs8(&der).map_err(|e| format!("private_key: {e}"))?;
    let mut signature = vec![0u8; key.public().modulus_len()];
    key.sign(
        &ring::signature::RSA_PKCS1_SHA256,
        &ring::rand::SystemRandom::new(),
        signing_input.as_bytes(),
        &mut signature,
    )
    .map_err(|e| format!("signing: {e}"))?;
    Ok(format!("{signing_input}.{}", url.encode(signature)))
}

/// The DER inside a `-----BEGIN PRIVATE KEY-----` block. Service account
/// files escape the newlines as `\n` in JSON; serde has already unescaped
/// them by the time the text arrives here.
fn pem_to_der(pem: &str) -> Result<Vec<u8>, String> {
    let body: String =
        pem.lines().map(str::trim).filter(|line| !line.is_empty() && !line.starts_with("-----")).collect();
    if !pem.contains("BEGIN PRIVATE KEY") {
        return Err("private_key is not a PKCS#8 `BEGIN PRIVATE KEY` block".into());
    }
    base64::engine::general_purpose::STANDARD.decode(body).map_err(|e| format!("private_key is not base64: {e}"))
}

/// Google credentials with the minted token cached until it nears expiry.
pub struct Google {
    path: PathBuf,
    credentials: Credentials,
    cache: Mutex<Option<(String, Instant)>>,
}

impl Google {
    pub fn open(path: &Path) -> Result<Google, AuthError> {
        let credentials = read_credentials(path)?;
        Ok(Google { path: path.to_path_buf(), credentials, cache: Mutex::new(None) })
    }

    pub fn credentials(&self) -> &Credentials {
        &self.credentials
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    /// A token valid for at least [`REFRESH_MARGIN`] from now.
    pub async fn token(&self) -> Result<String, AuthError> {
        let mut cache = self.cache.lock().await;
        if let Some((token, expiry)) = cache.as_ref() {
            if expiry.saturating_duration_since(Instant::now()) > REFRESH_MARGIN {
                return Ok(token.clone());
            }
        }
        let (token, expires_in) = mint(&self.credentials).await?;
        let expiry =
            Instant::now().checked_add(Duration::from_secs(expires_in)).ok_or_else(|| AuthError::Endpoint {
                endpoint: self.credentials.token_uri().to_string(),
                reason: "response expires_in exceeds the supported lifetime".into(),
                retryable: false,
            })?;
        *cache = Some((token.clone(), expiry));
        Ok(token)
    }
}

#[async_trait::async_trait]
impl Auth for Google {
    async fn headers(&self) -> Result<Vec<(String, String)>, AuthError> {
        Ok(vec![("authorization".to_string(), format!("Bearer {}", self.token().await?))])
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_support::scratch;
    use crate::testserver::{Reply, Server};

    /// A 2048-bit RSA key generated for these tests and used nowhere else.
    const TEST_KEY: &str = "-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDnqY6JHGRekfhE
gbM5VmKhB/yhApecjLASzt6D8vp7OGvqMM4rMEwZzgKV3TCYxr9ygvYqhcmi3CtQ
rj34XMnZO20MBc9lkskvF4E7eztsudRKaK2wNDQZ1BGx99I2lIFFJCP800md59Py
SwhI4wO4UWE6Kxmmb25oPj2IY3QFtAVMf8JIXCMP1ZMVWyF3gONFfS6WPTkM0Kz8
ouqbX/RLO1FZBGAooJEFPP6YuiOkVcGb3JTo54iANPr7rDEC1q4Ek1XXxr2QB9NT
5yxyib8USQi5lyWzKlwGbL1j7J1VU2kngMsIFbscN9hlbzhdpqjyBq5tgEQlYIUE
90Bkm85VAgMBAAECggEABBmdCbH+C5E9M4bK7SVcjbZ0xkUjxmQHciKSKQPh/Gm1
Uz0PSqlj3c/Z5BAfN5Aw724AI4sgB49dy7BBxx8EfYeoa7fzzm7oiG2IkbM7jBzG
QPwVGUZbQIFfczOZJCX0G1ncsmSE6+2gWfkk/ORzrbPwnhU8Mq6YzCSDda7MIC7h
itjUqEPw/0DLR804G3YAusrEhvhNIfLb4Gtdp7w6pezP/OxjfYVIkyyiVS/hiNkH
3gPiIwd9areBhodpG+MAaNWDMnlfPwCxt/35WYxOL5MZo6X9m8prjH6wng3OYSjc
k4Op/9zQ8AiRze2Rnclv225pr1RO9iQHDu0SaUfXswKBgQD5wd9MgJExBI+7/Uvr
EHfh3I8+ymkKaNynRNJaI/1iMuxifQxFdi1zGYrzY/DWGN9fBdc8zrWJEPg6Hb9b
lsqdPvh2lwZvnoqPFCo7JFcDMDZpldHtA5Lq74ZOsRmpQHhtPRtVkTauqr7Au2FZ
OfeWeCKdyf+4tEt8w+7EcE9MMwKBgQDtc+ZV447mIIHG+7LMwO12kCwKNOyMQQiQ
khYSMtvD9pIXgYF+Dt2+Ao8U+prALKz8MGrhd/fw9F0fO/YNCo7Kxv2Wk5cHCQIK
Sz0Tqji1Keh2lbc3RwkZFu+Lg27EMfrYsiD4S2kJHyWn/HkEEuBNdN2HSEjiTUN4
NSymbdpzVwKBgQCvlM0f4i6wUC2gElVh3sT4wu7tTK0FxWyCJ07eUfjbJUOrhY+v
8YHILgfSTctNKFU4X0nOlN9oicaITMtvXxX38AIKlOfQZpuwNJPv2f9V3XoTRmE7
h8ysX1GDVtvccdd3rILf5+OSbbUGl3S7npXhcXmchhrBxfZfsvrTnMUSowKBgHiS
h31JZYBZNUzS9gGeXXX800ADi7HUPAMdCvQGuy0QgTJKYnSeG96l8f2XGwlGJjiQ
ZVVD07SYgMiha9lHaSZyUMYq/19lJZIQjlzz7IOhWhcNAtGg0m/ZA532CUK6lkN+
f9tUf2tQU5CvVMvKwfbSxsIw5EF1NjNN3PRNh8VVAoGAVzutGcsghkzF35v2E1Kj
qtQRSWJCsZjpx6UyRt2rFLelojV0LTt32MpKhJ+HsFlExPsY/d9qpKMYC+DyBZkS
M3ZVuWkXdUC2XpzArkIe3yCqX3RQVdsYoqK4XHED3SrZ4Qpa4sVDrXXsxVON9W/m
JJykaWpMBy01wxf52VpXYZY=
-----END PRIVATE KEY-----
";

    #[tokio::test]
    async fn authorized_user_credentials_are_exchanged_and_cached() {
        let server = Server::start(vec![Reply::full(
            200,
            r#"{"access_token":"ya29.x","expires_in":3599,"token_type":"Bearer"}"#,
        )]);
        let path = scratch("adc.json");
        let json = serde_json::json!({
            "type": "authorized_user", "client_id": "cid.apps", "client_secret": "sec", "refresh_token": "1//r",
            "token_uri": format!("{}/token", server.base()),
        });
        std::fs::write(&path, json.to_string()).unwrap();
        let google = Google::open(&path).unwrap();
        assert_eq!(google.credentials().kind(), "authorized_user");
        assert_eq!(google.headers().await.unwrap(), vec![("authorization".to_string(), "Bearer ya29.x".to_string())]);
        google.headers().await.unwrap();
        let seen = server.requests();
        assert_eq!(seen.len(), 1, "the token is cached until near expiry");
        assert_eq!(seen[0].path, "/token");
        assert_eq!(
            seen[0].body,
            "grant_type=refresh_token&client_id=cid.apps&client_secret=sec&refresh_token=1%2F%2Fr"
        );
    }

    #[test]
    fn service_account_assertion_verifies_against_its_own_public_key() {
        let jwt =
            service_account_assertion("sa@p.iam.gserviceaccount.com", TEST_KEY, TOKEN_URL, 1_700_000_000).unwrap();
        let parts: Vec<&str> = jwt.split('.').collect();
        assert_eq!(parts.len(), 3);
        let url = base64::engine::general_purpose::URL_SAFE_NO_PAD;
        let header: serde_json::Value = serde_json::from_slice(&url.decode(parts[0]).unwrap()).unwrap();
        assert_eq!(header, serde_json::json!({ "alg": "RS256", "typ": "JWT" }));
        let claims: serde_json::Value = serde_json::from_slice(&url.decode(parts[1]).unwrap()).unwrap();
        assert_eq!(
            claims,
            serde_json::json!({
                "iss": "sa@p.iam.gserviceaccount.com", "scope": SCOPE, "aud": TOKEN_URL,
                "iat": 1_700_000_000u64, "exp": 1_700_003_600u64,
            })
        );
        let key = ring::signature::RsaKeyPair::from_pkcs8(&pem_to_der(TEST_KEY).unwrap()).unwrap();
        let public = ring::signature::UnparsedPublicKey::new(
            &ring::signature::RSA_PKCS1_2048_8192_SHA256,
            key.public().as_ref(),
        );
        let signed = format!("{}.{}", parts[0], parts[1]);
        public.verify(signed.as_bytes(), &url.decode(parts[2]).unwrap()).expect("signature verifies");
    }

    #[tokio::test]
    async fn service_account_file_is_exchanged_with_a_jwt_bearer_grant() {
        let invalid = serde_json::json!({"access_token":"unusable", "expires_in":u64::MAX}).to_string();
        let server = Server::start(vec![
            Reply::full(200, &invalid),
            Reply::full(200, r#"{"access_token":"sa-token","expires_in":3600}"#),
        ]);
        let path = scratch("sa.json");
        let json = serde_json::json!({
            "type": "service_account", "client_email": "sa@p.iam.gserviceaccount.com",
            "private_key": TEST_KEY, "token_uri": format!("{}/token", server.base()),
        });
        std::fs::write(&path, json.to_string()).unwrap();
        let credential = Google::open(&path).unwrap();
        let error = credential.token().await.unwrap_err();
        assert!(!error.retryable(), "{error}");
        assert!(error.to_string().contains("expires_in"), "{error}");
        assert_eq!(credential.token().await.unwrap(), "sa-token");
        let body = server.requests()[1].body.clone();
        assert!(
            body.starts_with("grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer&assertion=eyJ"),
            "{body}"
        );
    }

    #[test]
    fn malformed_credentials_name_the_path_and_the_field() {
        let path = scratch("creds-bad.json");
        std::fs::write(&path, r#"{"type":"authorized_user","client_id":"x"}"#).unwrap();
        let err = Google::open(&path).err().unwrap().to_string();
        assert!(err.contains("creds-bad.json: `client_secret` is missing or empty"), "{err}");
        std::fs::write(&path, r#"{"type":"external_account"}"#).unwrap();
        let err = Google::open(&path).err().unwrap().to_string();
        assert!(err.contains("`type` is \"external_account\""), "{err}");
        assert!(pem_to_der("-----BEGIN RSA PRIVATE KEY-----\nAAAA\n-----END RSA PRIVATE KEY-----").is_err());
    }

    #[tokio::test]
    async fn a_refused_exchange_is_an_endpoint_error() {
        let server = Server::start(vec![Reply::full(
            400,
            r#"{"error":"invalid_grant","error_description":"Token has been expired or revoked."}"#,
        )]);
        let path = scratch("adc-revoked.json");
        let json = serde_json::json!({
            "type": "authorized_user", "client_id": "c", "client_secret": "s", "refresh_token": "r",
            "token_uri": format!("{}/token", server.base()),
        });
        std::fs::write(&path, json.to_string()).unwrap();
        let err = Google::open(&path).unwrap().headers().await.unwrap_err();
        assert!(!err.retryable());
        assert!(err.to_string().contains("HTTP 400: invalid_grant"), "{err}");
    }
}
