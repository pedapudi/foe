//! The two convention paths foe has, and the private-file writer they share.
//!
//! foe reads no environment variable, including `HOME`. The home directory
//! is the one the passwd database records for the process's real user id.
//! Below it, `~/.config/foe/` holds the default model file and one
//! credentials file per provider. Nothing else is looked up by convention;
//! every other path arrives in a configuration document.

use std::io;
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};

/// The home directory of the real user, from the passwd database.
pub fn home_dir() -> Result<PathBuf, String> {
    let uid = nix::unistd::getuid();
    match nix::unistd::User::from_uid(uid) {
        Ok(Some(user)) if user.dir.is_absolute() => Ok(user.dir),
        Ok(Some(user)) => Err(format!("passwd entry for uid {uid} has a relative home directory {:?}", user.dir)),
        Ok(None) => Err(format!("uid {uid} has no passwd entry")),
        Err(e) => Err(format!("reading the passwd entry for uid {uid}: {e}")),
    }
}

/// `~/.config/foe`.
pub fn config_dir(home: &Path) -> PathBuf {
    home.join(".config").join("foe")
}

/// `~/.config/foe/credentials/<provider>.json`: where a provider's
/// credential lives when the `model` block does not name one.
pub fn credentials_path(home: &Path, provider: &str) -> PathBuf {
    config_dir(home).join("credentials").join(format!("{provider}.json"))
}

/// `~/.config/foe/default-model.json`: the `{ "provider", "model" }` pair a
/// bare `foe "task"` runs when `--model` is absent.
pub fn default_model_path(home: &Path) -> PathBuf {
    config_dir(home).join("default-model.json")
}

/// Writes `bytes` to `path` with mode 0600, creating parent directories.
/// The file is staged beside the target and renamed into place, so a reader
/// sees either the old contents or the new ones and never a prefix.
pub fn write_private(path: &Path, bytes: &[u8]) -> io::Result<()> {
    let parent = path.parent().ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "path has no parent"))?;
    std::fs::create_dir_all(parent)?;
    let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("file");
    let staged = parent.join(format!(".{name}.{}.tmp", std::process::id()));
    let result = (|| {
        use io::Write;
        let mut file = std::fs::OpenOptions::new().write(true).create(true).truncate(true).mode(0o600).open(&staged)?;
        file.write_all(bytes)?;
        file.sync_all()?;
        std::fs::rename(&staged, path)
    })();
    if result.is_err() {
        let _ = std::fs::remove_file(&staged);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    #[test]
    fn home_comes_from_the_passwd_database() {
        let home = home_dir().unwrap();
        assert!(home.is_absolute());
        assert_eq!(credentials_path(&home, "anthropic"), home.join(".config/foe/credentials/anthropic.json"));
        assert_eq!(default_model_path(&home), home.join(".config/foe/default-model.json"));
    }

    #[test]
    fn private_write_is_mode_0600_and_replaces_whole() {
        let dir = crate::test_support::scratch_dir("paths");
        let path = dir.join("nested/creds.json");
        write_private(&path, b"one").unwrap();
        assert_eq!(std::fs::read(&path).unwrap(), b"one");
        assert_eq!(std::fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
        write_private(&path, b"two").unwrap();
        assert_eq!(std::fs::read(&path).unwrap(), b"two");
        let leftovers: Vec<_> = std::fs::read_dir(dir.join("nested")).unwrap().flatten().collect();
        assert_eq!(leftovers.len(), 1, "no staging file remains");
    }
}
