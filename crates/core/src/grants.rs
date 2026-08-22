//! Descriptor-relative filesystem containment and the Reader and Writer handles.
//!
//! Implements docs/config.md (grants). Each granted root is opened once.
//! Operations below it resolve through that directory handle, so replacing a
//! pathname after a check cannot redirect the operation outside the root.

use crate::{CapError, Reader, Writer};
use cap_std::ambient_authority;
use cap_std::fs::{Dir, OpenOptions};
use ignore::gitignore::{Gitignore, GitignoreBuilder};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

/// Resolves symbolic links in `path`. Configuration construction uses this
/// to store stable display paths; capability operations use open directories.
pub fn canonicalize(path: &Path) -> Result<PathBuf, CapError> {
    match std::fs::canonicalize(path) {
        Ok(resolved) => Ok(resolved),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            let name =
                path.file_name().ok_or_else(|| CapError::Invalid(format!("{}: has no file name", path.display())))?;
            let parent = path.parent().filter(|p| !p.as_os_str().is_empty()).unwrap_or(Path::new("."));
            Ok(std::fs::canonicalize(parent)?.join(name))
        }
        Err(e) => Err(e.into()),
    }
}

/// True when `path` equals one of `roots` or lies below it, compared by components.
pub fn contains(roots: &[PathBuf], path: &Path) -> bool {
    roots.iter().any(|root| path.starts_with(root))
}

/// Resolves a path for display and construction checks. Runtime access uses
/// the descriptor-relative operation on [`RootReader`] or [`RootWriter`].
pub fn resolve(roots: &[PathBuf], path: &Path) -> Result<PathBuf, CapError> {
    let absolute = match (path.is_absolute(), roots.first()) {
        (true, _) => path.to_path_buf(),
        (false, Some(root)) => root.join(path),
        (false, None) => return Err(CapError::Denied { path: path.to_path_buf() }),
    };
    let resolved = canonicalize(&absolute)?;
    contains(roots, &resolved).then_some(resolved).ok_or_else(|| CapError::Denied { path: path.to_path_buf() })
}

fn locate(roots: &[PathBuf], path: &Path) -> Result<(usize, PathBuf), CapError> {
    if !path.is_absolute() {
        return roots
            .first()
            .map(|_| (0, path.to_path_buf()))
            .ok_or_else(|| CapError::Denied { path: path.to_path_buf() });
    }
    roots
        .iter()
        .enumerate()
        .filter_map(|(i, root)| {
            path.strip_prefix(root).ok().map(|rel| (i, rel.to_path_buf(), root.components().count()))
        })
        .max_by_key(|(_, _, depth)| *depth)
        .map(|(i, rel, _)| (i, if rel.as_os_str().is_empty() { PathBuf::from(".") } else { rel }))
        .ok_or_else(|| CapError::Denied { path: path.to_path_buf() })
}

fn open_roots(roots: &[PathBuf]) -> Result<Vec<Dir>, CapError> {
    roots.iter().map(|path| Dir::open_ambient_dir(path, ambient_authority()).map_err(CapError::from)).collect()
}

/// Reads bounded to a set of directory handles opened at episode start.
pub struct RootReader {
    roots: Vec<PathBuf>,
    dirs: Vec<Dir>,
}

impl RootReader {
    /// `roots` must already be canonical, as the resolved program holds them.
    pub fn new(roots: Vec<PathBuf>) -> Result<Self, CapError> {
        let dirs = open_roots(&roots)?;
        Ok(Self { roots, dirs })
    }
}

impl Reader for RootReader {
    fn read(&self, path: &Path) -> Result<Vec<u8>, CapError> {
        let (i, rel) = locate(&self.roots, path)?;
        Ok(self.dirs[i].read(rel)?)
    }

    fn metadata(&self, path: &Path) -> Result<std::fs::Metadata, CapError> {
        let (i, rel) = locate(&self.roots, path)?;
        Ok(self.dirs[i].open(rel)?.into_std().metadata()?)
    }

    fn files(&self, path: &Path) -> Result<Vec<PathBuf>, CapError> {
        let (i, rel) = locate(&self.roots, path)?;
        let metadata = self.dirs[i].metadata(&rel)?;
        let absolute = if path.is_absolute() { path.to_path_buf() } else { self.roots[i].join(&rel) };
        if metadata.is_file() {
            return Ok(vec![absolute]);
        }
        if !metadata.is_dir() {
            return Ok(Vec::new());
        }
        let dir = self.dirs[i].open_dir(rel)?;
        let mut files = Vec::new();
        walk(&dir, &absolute, &[], &mut files)?;
        Ok(files)
    }

    fn roots(&self) -> &[PathBuf] {
        &self.roots
    }
}

fn walk(dir: &Dir, path: &Path, inherited: &[Gitignore], files: &mut Vec<PathBuf>) -> Result<(), CapError> {
    let mut rules = inherited.to_vec();
    for name in [".gitignore", ".ignore"] {
        let Ok(text) = dir.read_to_string(name) else { continue };
        let source = path.join(name);
        let mut builder = GitignoreBuilder::new(path);
        for line in text.lines() {
            let _ = builder.add_line(Some(source.clone()), line);
        }
        if let Ok(rule) = builder.build() {
            rules.push(rule);
        }
    }
    for entry in dir.entries()? {
        let entry = entry?;
        let name = entry.file_name();
        if name.to_string_lossy().starts_with('.') {
            continue;
        }
        let child = path.join(&name);
        let kind = entry.file_type()?;
        let ignored = rules.iter().rev().find_map(|rule| {
            let matched = rule.matched(&child, kind.is_dir());
            (!matched.is_none()).then(|| matched.is_ignore())
        });
        if ignored == Some(true) {
            continue;
        }
        if kind.is_dir() {
            walk(&entry.open_dir()?, &child, &rules, files)?;
        } else if kind.is_file() {
            files.push(child);
        }
    }
    Ok(())
}

/// Writes bounded to a set of directory handles opened at episode start.
pub struct RootWriter {
    roots: Vec<PathBuf>,
    dirs: Vec<Dir>,
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
    fn write(&self, path: &Path, bytes: &[u8]) -> Result<(), CapError> {
        let (i, rel) = locate(&self.roots, path)?;
        let name = rel.file_name().ok_or_else(|| CapError::Invalid(format!("{}: has no file name", path.display())))?;
        let parent_rel = rel.parent().filter(|p| !p.as_os_str().is_empty()).unwrap_or(Path::new("."));
        let parent = self.dirs[i].open_dir(parent_rel)?;
        let n = self.stages.fetch_add(1, Ordering::SeqCst);
        let stage = format!(".{}.{}.{n}.staged", name.to_string_lossy(), std::process::id());
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        let mut file = parent.open_with(&stage, &options)?;
        if let Err(error) = file.write_all(bytes).and_then(|()| parent.rename(&stage, &parent, name)) {
            let _ = parent.remove_file(&stage);
            return Err(error.into());
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
