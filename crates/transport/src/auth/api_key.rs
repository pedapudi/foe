//! An API key read from a file.
//!
//! Two file shapes are accepted. The convention file that `foe login`
//! writes is a JSON object `{ "api_key": "..." }`. A file named explicitly
//! by `model.api_key_file` may instead hold the bare key; trailing
//! whitespace, including the newline most editors append, is removed.

use std::path::Path;

use super::{Auth, AuthError, KeyHeader};

pub struct ApiKey {
    header: KeyHeader,
    key: String,
}

impl ApiKey {
    pub fn new(header: KeyHeader, key: String) -> ApiKey {
        ApiKey { header, key }
    }

    pub fn from_file(header: KeyHeader, path: &Path) -> Result<ApiKey, AuthError> {
        Ok(ApiKey { header, key: read_api_key(path)? })
    }
}

impl Auth for ApiKey {
    fn headers(&self) -> Result<Vec<(String, String)>, AuthError> {
        Ok(vec![match self.header {
            KeyHeader::XApiKey => ("x-api-key".to_string(), self.key.clone()),
            KeyHeader::Bearer => ("authorization".to_string(), format!("Bearer {}", self.key)),
        }])
    }
}

/// Reads the key. An empty key is an error because every provider would
/// reject it with a less specific message.
pub fn read_api_key(path: &Path) -> Result<String, AuthError> {
    let fail = |reason: String| AuthError::Credential { path: path.to_path_buf(), reason };
    let text = std::fs::read_to_string(path).map_err(|e| fail(e.to_string()))?;
    let key = match serde_json::from_str::<serde_json::Value>(&text) {
        Ok(value) if value.is_object() => value
            .get("api_key")
            .and_then(|v| v.as_str())
            .map(str::to_string)
            .ok_or_else(|| fail("JSON object without an `api_key` string".into()))?,
        _ => text.trim_end().to_string(),
    };
    if key.trim().is_empty() {
        return Err(fail("file is empty".into()));
    }
    Ok(key)
}

/// Writes the convention file with mode 0600.
pub fn write_api_key(path: &Path, key: &str) -> std::io::Result<()> {
    let text = serde_json::to_string_pretty(&serde_json::json!({ "api_key": key })).expect("a string serializes");
    crate::paths::write_private(path, format!("{text}\n").as_bytes())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::test_support::scratch;

    #[test]
    fn bare_key_is_trimmed_and_json_key_is_read() {
        let path = scratch("key-with-newline");
        std::fs::write(&path, "sk-test-123\n").unwrap();
        assert_eq!(read_api_key(&path).unwrap(), "sk-test-123");
        std::fs::write(&path, "sk-test-123\r\n\n").unwrap();
        assert_eq!(read_api_key(&path).unwrap(), "sk-test-123");
        write_api_key(&path, "sk-json").unwrap();
        assert_eq!(read_api_key(&path).unwrap(), "sk-json");
        let headers = ApiKey::from_file(KeyHeader::Bearer, &path).unwrap().headers().unwrap();
        assert_eq!(headers, vec![("authorization".to_string(), "Bearer sk-json".to_string())]);
        let headers = ApiKey::from_file(KeyHeader::XApiKey, &path).unwrap().headers().unwrap();
        assert_eq!(headers, vec![("x-api-key".to_string(), "sk-json".to_string())]);
    }

    #[test]
    fn empty_missing_and_malformed_files_name_the_path() {
        let path = scratch("key-empty");
        std::fs::write(&path, "\n").unwrap();
        let err = read_api_key(&path).unwrap_err().to_string();
        assert!(err.ends_with("file is empty"), "{err}");
        std::fs::write(&path, "{\"token\": \"x\"}").unwrap();
        let err = read_api_key(&path).unwrap_err().to_string();
        assert!(err.contains("without an `api_key` string"), "{err}");
        let missing = scratch("key-missing-does-not-exist");
        let err = read_api_key(&missing).unwrap_err().to_string();
        assert!(err.contains("key-missing-does-not-exist"), "{err}");
    }
}
