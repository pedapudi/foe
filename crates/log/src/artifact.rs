//! Validation and immutable retention of files referenced by episode events.

use crate::{LogError, RenderingArchive, ToolResult};
use std::fs;
use std::io::{self, Write};
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};

/// Reads a rendering archive whose path, byte length, and digest match its event.
pub fn read_rendering(spill_dir: &Path, seq: u64, archive: &RenderingArchive) -> Result<Vec<u8>, LogError> {
    if crate::digest::rendering_file(&archive.digest).as_deref() != Some(&archive.file) {
        return Err(LogError::Archive {
            seq,
            path: archive.file.clone(),
            rule: "file is not derived from digest".into(),
        });
    }
    read_verified(spill_dir, seq, &archive.file, archive.bytes, Some(&archive.digest))
}

/// Reads a canonical JSON spill and validates its locator. Inline results return `None`.
pub fn read_canonical(spill_dir: &Path, seq: u64, result: &ToolResult) -> Result<Option<Vec<u8>>, LogError> {
    let Some(file) = &result.spill else { return Ok(None) };
    let invalid = |rule: &str| LogError::Archive { seq, path: format!("spill/{file}"), rule: rule.into() };
    let value = &result.value;
    if Path::new(file).file_name().is_none_or(|name| name != std::ffi::OsStr::new(file))
        || value["spill"].as_str() != Some(file)
        || value["is_error"].as_bool() != Some(result.is_error)
    {
        return Err(invalid("tool/result spill locator requires a single-component path and matching is_error"));
    }
    let count =
        value["bytes"].as_u64().ok_or_else(|| invalid("tool/result spill bytes must be an unsigned integer"))?;
    let digest = value
        .get("digest")
        .map(|v| v.as_str().ok_or_else(|| invalid("tool/result spill digest must be a string")))
        .transpose()?;
    let bytes = read_verified(spill_dir, seq, file, count, digest)?;
    serde_json::from_slice::<serde_json::Value>(&bytes).map_err(|_| invalid("tool/result spill must contain JSON"))?;
    Ok(Some(bytes))
}

fn read_verified(dir: &Path, seq: u64, file: &str, count: u64, digest: Option<&str>) -> Result<Vec<u8>, LogError> {
    let invalid = |rule: String| LogError::Archive {
        seq,
        path: format!("spill/{file}"),
        rule: format!("{rule}; expected {count} bytes and digest {digest:?}"),
    };
    let path = dir.join(file);
    if !fs::symlink_metadata(&path).map_err(|e| invalid(e.to_string()))?.file_type().is_file() {
        return Err(invalid("archive must be a regular file".into()));
    }
    let bytes = fs::read(path).map_err(|e| invalid(format!("cannot read: {e}")))?;
    if bytes.len() as u64 != count
        || digest.is_some_and(|d| d != format!("sha256:{}", crate::digest::sha256_hex(&bytes)))
    {
        return Err(invalid("archive content does not match its locator".into()));
    }
    Ok(bytes)
}

/// Installs synchronized bytes without replacing an existing file. Existing content must match.
pub fn retain(path: &Path, bytes: &[u8]) -> io::Result<()> {
    static SERIAL: AtomicU64 = AtomicU64::new(0);
    let verify = || {
        if !fs::symlink_metadata(path)?.file_type().is_file() || fs::read(path)? != bytes {
            return Err(io::Error::other(format!("archive {}: existing content differs", path.display())));
        }
        Ok(())
    };
    if path.try_exists()? {
        return verify();
    }
    let parent =
        path.parent().ok_or_else(|| io::Error::other(format!("archive {}: parent is absent", path.display())))?;
    fs::create_dir_all(parent)?;
    let temporary =
        path.with_extension(format!("{}.{}.tmp", std::process::id(), SERIAL.fetch_add(1, Ordering::Relaxed)));
    let mut file = fs::OpenOptions::new().write(true).create_new(true).open(&temporary)?;
    let result = (|| {
        file.write_all(bytes)?;
        file.sync_all()?;
        match fs::hard_link(&temporary, path) {
            Err(e) if e.kind() != io::ErrorKind::AlreadyExists => return Err(e),
            _ => {}
        }
        verify()?;
        fs::File::open(parent)?.sync_all()
    })();
    // Only this invocation owns the temporary file; its bytes remain in the caller's buffer.
    let cleanup = fs::remove_file(temporary);
    result.and(cleanup)
}
