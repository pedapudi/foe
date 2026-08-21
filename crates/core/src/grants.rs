//! Path canonicalization and prefix containment; the Reader and Writer handles.
//!
//! Implements docs/config.md (grants). A grant is a directory prefix.
//! Symbolic links are resolved before every check, so a link that points
//! outside every granted root is denied even when the link itself lies
//! inside one.

use crate::{CapError, Reader, Writer};
use std::path::{Component, Path, PathBuf};

/// Resolves symbolic links in `path`. When the final component does not
/// exist, its parent is resolved and the file name appended, so that a
/// file about to be created can be checked against the roots.
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

/// True when `path` equals one of `roots` or lies below it, compared by
/// components so that `/src-other` is outside `/src`.
pub fn contains(roots: &[PathBuf], path: &Path) -> bool {
    roots.iter().any(|root| path.starts_with(root))
}

/// `path` as given when absolute, or under the first root.
fn absolute(roots: &[PathBuf], path: &Path) -> Result<PathBuf, CapError> {
    match (path.is_absolute(), roots.first()) {
        (true, _) => Ok(path.to_path_buf()),
        (false, Some(root)) => Ok(root.join(path)),
        (false, None) => Err(CapError::Denied { path: path.to_path_buf() }),
    }
}

/// Canonicalizes `path` and checks it against `roots`. A relative path is
/// taken against the first root.
pub fn resolve(roots: &[PathBuf], path: &Path) -> Result<PathBuf, CapError> {
    let resolved = canonicalize(&absolute(roots, path)?)?;
    if contains(roots, &resolved) {
        Ok(resolved)
    } else {
        Err(CapError::Denied { path: path.to_path_buf() })
    }
}

/// Reads bounded to a set of canonical roots.
pub struct RootReader {
    roots: Vec<PathBuf>,
}

impl RootReader {
    /// `roots` must already be canonical, as the resolved program holds them.
    pub fn new(roots: Vec<PathBuf>) -> Self {
        Self { roots }
    }
}

impl Reader for RootReader {
    fn read(&self, path: &Path) -> Result<Vec<u8>, CapError> {
        Ok(std::fs::read(resolve(&self.roots, path)?)?)
    }

    fn metadata(&self, path: &Path) -> Result<std::fs::Metadata, CapError> {
        Ok(std::fs::metadata(resolve(&self.roots, path)?)?)
    }

    /// Honors `.gitignore` and `.ignore` files whether or not `root` is
    /// inside a git checkout. Entries that resolve outside the roots
    /// through a link are omitted. Unreadable entries are skipped.
    fn walk(&self, root: &Path) -> Result<Box<dyn Iterator<Item = PathBuf> + Send>, CapError> {
        let root = resolve(&self.roots, root)?;
        let roots = self.roots.clone();
        let paths: Vec<PathBuf> = ignore::WalkBuilder::new(&root)
            .require_git(false)
            .build()
            .filter_map(Result::ok)
            .filter_map(|entry| std::fs::canonicalize(entry.path()).ok())
            .filter(|path| contains(&roots, path))
            .collect();
        Ok(Box::new(paths.into_iter()))
    }

    fn roots(&self) -> &[PathBuf] {
        &self.roots
    }
}

/// Writes bounded to a set of canonical roots.
pub struct RootWriter {
    roots: Vec<PathBuf>,
}

impl RootWriter {
    /// `roots` must already be canonical, as the resolved program holds them.
    pub fn new(roots: Vec<PathBuf>) -> Self {
        Self { roots }
    }
}

impl Writer for RootWriter {
    /// Stages the bytes in a sibling file and renames it over the target, so
    /// a reader never observes a partial file.
    fn write(&self, path: &Path, bytes: &[u8]) -> Result<(), CapError> {
        let target = resolve(&self.roots, path)?;
        let name = target.file_name().and_then(|n| n.to_str()).unwrap_or("file");
        let stage = target.with_file_name(format!(".{name}.{}.staged", std::process::id()));
        std::fs::write(&stage, bytes)?;
        std::fs::rename(&stage, &target).inspect_err(|_| {
            let _ = std::fs::remove_file(&stage);
        })?;
        Ok(())
    }

    /// Checks the deepest existing ancestor, so that no directory is created
    /// outside the roots through a link on the way down.
    fn create_dir_all(&self, path: &Path) -> Result<(), CapError> {
        let absolute = absolute(&self.roots, path)?;
        let mut existing = absolute.as_path();
        let mut rest = Vec::new();
        while !existing.exists() {
            match (existing.parent(), existing.file_name()) {
                (Some(parent), Some(name)) => {
                    rest.push(name.to_owned());
                    existing = parent;
                }
                _ => return Err(CapError::Denied { path: path.to_path_buf() }),
            }
        }
        let mut resolved = canonicalize(existing)?;
        for name in rest.iter().rev() {
            resolved.push(name);
        }
        if resolved.components().any(|c| c == Component::ParentDir) || !contains(&self.roots, &resolved) {
            return Err(CapError::Denied { path: path.to_path_buf() });
        }
        Ok(std::fs::create_dir_all(&resolved)?)
    }

    fn roots(&self) -> &[PathBuf] {
        &self.roots
    }
}

#[cfg(test)]
#[path = "grants_test.rs"]
mod tests;
