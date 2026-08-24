//! Descriptor-bound filesystem containment; the Reader and Writer handles.
//!
//! Implements docs/config.md (grants). A grant is a directory prefix. Each
//! granted root is opened once when the episode starts, and every read and
//! write below it names a path relative to that open directory. Resolution
//! therefore happens inside the kernel against the directory the runtime
//! opened, rather than against a pathname that another process may have
//! repointed since the check. A symbolic link, a `..` component, or a
//! renamed directory component that would leave the root is refused by the
//! open itself.
//!
//! On Linux this is `openat2` with `RESOLVE_BENEATH`; on other Unix systems
//! `cap-std` enforces the same boundary through descriptor-relative opens.
//! The kernel sandbox in `sandbox.rs` enforces the same grants for every
//! process the episode starts. These handles are what bounds the episode
//! process itself where no Landlock is available, which `sandbox.mode`
//! `best-effort` permits and `off` requires.

use crate::{CapError, Reader, Writer};
use cap_std::ambient_authority;
use cap_std::fs::{Dir, OpenOptions};
use std::io::Write as _;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

/// The root that holds `path`, and the remainder of `path` below it. A
/// relative path belongs to the first root, as `resolve` treats it. An
/// absolute path takes the deepest root that is a prefix of it, so that a
/// root nested inside another is used with its own descriptor. The remainder
/// is never checked here: the open under the root's descriptor is what
/// refuses a remainder that climbs out.
fn locate(roots: &[PathBuf], path: &Path) -> Result<(usize, PathBuf), CapError> {
    let denied = || CapError::Denied { path: path.to_path_buf() };
    if !path.is_absolute() {
        return roots.first().map(|_| (0, path.to_path_buf())).ok_or_else(denied);
    }
    roots
        .iter()
        .enumerate()
        .filter_map(|(i, root)| path.strip_prefix(root).ok().map(|rest| (i, rest, root.components().count())))
        .max_by_key(|(_, _, depth)| *depth)
        .map(|(i, rest, _)| (i, if rest.as_os_str().is_empty() { PathBuf::from(".") } else { rest.to_path_buf() }))
        .ok_or_else(denied)
}

fn open_roots(roots: &[PathBuf]) -> Result<Vec<Dir>, CapError> {
    roots.iter().map(|path| Dir::open_ambient_dir(path, ambient_authority()).map_err(CapError::from)).collect()
}

/// Reads bounded to the directories the read roots named when the episode
/// started.
pub struct RootReader {
    roots: Vec<PathBuf>,
    dirs: Vec<Dir>,
}

impl RootReader {
    /// `roots` must already be canonical, as the resolved program holds them.
    /// Opening every root here is what binds later reads to these directories.
    pub fn new(roots: Vec<PathBuf>) -> Result<Self, CapError> {
        let dirs = open_roots(&roots)?;
        Ok(Self { roots, dirs })
    }
}

impl Reader for RootReader {
    fn open(&self, path: &Path) -> Result<Box<dyn std::io::Read + Send>, CapError> {
        let (i, rest) = locate(&self.roots, path)?;
        Ok(Box::new(self.dirs[i].open(rest)?.into_std()))
    }

    fn read(&self, path: &Path) -> Result<Vec<u8>, CapError> {
        let (i, rest) = locate(&self.roots, path)?;
        Ok(self.dirs[i].read(rest)?)
    }

    fn metadata(&self, path: &Path) -> Result<std::fs::Metadata, CapError> {
        let (i, rest) = locate(&self.roots, path)?;
        Ok(self.dirs[i].open(rest)?.into_std().metadata()?)
    }

    fn roots(&self) -> &[PathBuf] {
        &self.roots
    }
}

/// Writes bounded to the directories the write roots named when the episode
/// started.
pub struct RootWriter {
    roots: Vec<PathBuf>,
    dirs: Vec<Dir>,
    /// Distinguishes concurrent staged files of one process.
    stages: AtomicU64,
}

impl RootWriter {
    /// `roots` must already be canonical, as the resolved program holds them.
    pub fn new(roots: Vec<PathBuf>) -> Result<Self, CapError> {
        let dirs = open_roots(&roots)?;
        Ok(Self { roots, dirs, stages: AtomicU64::new(0) })
    }
}

impl Writer for RootWriter {
    /// Stages the bytes in a sibling file and renames it over the target, so
    /// a reader never observes a partial file. Both steps run against the
    /// target's own directory descriptor, so the file that is replaced is the
    /// one the containment check admitted.
    fn write(&self, path: &Path, bytes: &[u8]) -> Result<(), CapError> {
        let (i, rest) = locate(&self.roots, path)?;
        let name =
            rest.file_name().ok_or_else(|| CapError::Invalid(format!("{}: has no file name", path.display())))?;
        let parent_rest = rest.parent().filter(|p| !p.as_os_str().is_empty()).unwrap_or(Path::new("."));
        let parent = self.dirs[i].open_dir(parent_rest)?;
        let n = self.stages.fetch_add(1, Ordering::SeqCst);
        let stage = format!(".{}.{}.{n}.staged", name.to_string_lossy(), std::process::id());
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        let mut file = parent.open_with(&stage, &options)?;
        if let Err(e) = file.write_all(bytes).and_then(|()| parent.rename(&stage, &parent, name)) {
            let _ = parent.remove_file(&stage);
            return Err(e.into());
        }
        Ok(())
    }

    fn roots(&self) -> &[PathBuf] {
        &self.roots
    }
}

#[cfg(test)]
#[path = "grants_test.rs"]
mod tests;
