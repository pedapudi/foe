//! The two convention paths foe has, and the private-file writer they share.
//!
//! The home directory is the one the passwd database records for the
//! process's real user id, so the same command resolves the same paths
//! whatever the environment holds. `HOME` is read in one case: when the
//! database has no entry for the user at all. A statically linked binary
//! cannot load the modules `nsswitch.conf` names, so on a host keeping
//! accounts in a directory service every lookup finds nothing, and refusing
//! to run leaves nothing to fall back to. Where an entry exists it still
//! decides, so the environment never overrides a database that answered.
//!
//! Below the home directory, `~/.config/foe/` holds the default model file
//! and one credentials file per provider. Nothing else is looked up by
//! convention; every other path arrives in a configuration document.

use std::io;
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};

/// Where a home directory came from, which the caller reports when it was
/// not the passwd database.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HomeSource {
    Passwd,
    Environment,
}

/// The home directory of the real user and where it came from.
pub fn home_source() -> Result<(PathBuf, HomeSource), String> {
    let uid = nix::unistd::getuid();
    match nix::unistd::User::from_uid(uid) {
        Ok(Some(user)) if user.dir.is_absolute() => Ok((user.dir, HomeSource::Passwd)),
        Ok(Some(user)) => Err(format!("passwd entry for uid {uid} has a relative home directory {:?}", user.dir)),
        Ok(None) => from_environment(uid),
        Err(e) => Err(format!("reading the passwd entry for uid {uid}: {e}")),
    }
}

/// The home directory of the real user.
pub fn home_dir() -> Result<PathBuf, String> {
    home_source().map(|(dir, _)| dir)
}

/// `HOME` when the passwd database holds no entry for the user.
fn from_environment(uid: nix::unistd::Uid) -> Result<(PathBuf, HomeSource), String> {
    resolve_environment(uid, std::env::var_os("HOME"))
}

/// The home directory `home` names, or why it cannot stand for the missing
/// passwd entry. It must be an absolute path to a directory that exists: a
/// wrong one would write a credential where no reader would look for it,
/// and the failure it replaces is one a reader has to be able to act on.
fn resolve_environment(
    uid: nix::unistd::Uid,
    home: Option<std::ffi::OsString>,
) -> Result<(PathBuf, HomeSource), String> {
    let dir = home.filter(|value| !value.is_empty()).map(PathBuf::from);
    let fault = match &dir {
        None => "HOME is unset",
        Some(dir) if !dir.is_absolute() => "HOME is not an absolute path",
        Some(dir) if !dir.is_dir() => "HOME does not name a directory that exists",
        Some(dir) => return Ok((dir.clone(), HomeSource::Environment)),
    };
    Err(format!(
        "uid {uid} has no passwd entry, and {fault}. A statically linked binary cannot load the modules \
nsswitch.conf names, so an account a directory service holds is invisible to it even where `getent passwd \
{uid}` reports one. Set HOME to that account's home directory"
    ))
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

    /// A statically linked binary cannot load the modules `nsswitch.conf`
    /// names, so on a host keeping accounts in a directory service the
    /// passwd lookup finds nothing and there is nothing to fall back to.
    /// `HOME` stands for the missing entry, and only for a missing one, so
    /// a database that answered is never overridden.
    #[test]
    fn home_falls_back_to_the_environment_only_where_the_database_has_no_entry() {
        let uid = nix::unistd::getuid();
        let dir = crate::test_support::scratch_dir("paths-home");
        let os = |text: &str| Some(std::ffi::OsString::from(text));

        let (found, source) = resolve_environment(uid, os(&dir.display().to_string())).unwrap();
        assert_eq!((found.as_path(), source), (dir.as_ref(), HomeSource::Environment));

        for absent in [None, os(""), os("relative/home"), os(&dir.join("gone").display().to_string())] {
            let refused = resolve_environment(uid, absent.clone()).unwrap_err();
            assert!(refused.contains("no passwd entry"), "{refused}");
            assert!(refused.contains("nsswitch.conf"), "it names the mechanism: {refused}");
            assert!(refused.contains("Set HOME"), "it names what to do: {refused}");
        }

        // This host records an entry, so the database decides and the
        // environment is not consulted at all.
        assert_eq!(home_source().unwrap().1, HomeSource::Passwd);
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
