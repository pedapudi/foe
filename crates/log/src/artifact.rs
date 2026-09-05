//! Validation of files referenced by episode events.

use crate::{LogError, RenderingArchive};
use std::path::Path;

/// Reads a rendering archive whose path, byte length, and digest match its event.
pub fn read_rendering(spill_dir: &Path, seq: u64, archive: &RenderingArchive) -> Result<Vec<u8>, LogError> {
    let expected = crate::digest::rendering_file(&archive.digest);
    if expected.as_deref() != Some(&archive.file) {
        return Err(archive_error(seq, archive, "file is not derived from digest"));
    }
    let path = spill_dir.join(&archive.file);
    let bytes = std::fs::read(&path).map_err(|error| LogError::Archive {
        seq,
        path: format!("spill/{}", archive.file),
        rule: format!("cannot read: {error}"),
    })?;
    if bytes.len() as u64 != archive.bytes {
        return Err(archive_error(seq, archive, &format!("has {} bytes; expected {}", bytes.len(), archive.bytes)));
    }
    let actual = format!("sha256:{}", crate::digest::sha256_hex(&bytes));
    if actual != archive.digest {
        return Err(archive_error(seq, archive, &format!("has digest {actual}; expected {}", archive.digest)));
    }
    Ok(bytes)
}

pub(crate) fn archive_error(seq: u64, archive: &RenderingArchive, rule: &str) -> LogError {
    LogError::Archive {
        seq,
        path: format!("spill/{}", archive.file),
        rule: format!("{rule}; expected digest {}", archive.digest),
    }
}
