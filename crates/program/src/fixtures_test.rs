//! Fixtures shared by the tests of this crate: temporary directories, a
//! minimal document and the program it resolves to, and probe tool
//! specifications.

use crate::document::{resolve, ResolvedProgram};
use crate::{Effect, ProgramDocument, ToolSpec};
use serde_json::{json, Value};
use std::ops::Deref;
use std::path::Path;

pub struct ScratchDir(tempfile::TempDir);

impl ScratchDir {
    fn new(name: &str) -> Self {
        assert_eq!(Path::new(name).file_name(), Some(name.as_ref()), "scratch name must be one path component");
        Self(tempfile::Builder::new().prefix(&format!("foe-program-{name}-")).tempdir().unwrap())
    }
}

impl Deref for ScratchDir {
    type Target = Path;

    fn deref(&self) -> &Self::Target {
        self.0.path()
    }
}

impl AsRef<Path> for ScratchDir {
    fn as_ref(&self) -> &Path {
        self.0.path()
    }
}

impl serde::Serialize for ScratchDir {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serde::Serialize::serialize(self.0.path(), serializer)
    }
}

impl Drop for ScratchDir {
    fn drop(&mut self) {
        if std::thread::panicking() {
            eprintln!("retained failed test directory: {}", self.0.path().display());
            self.0.disable_cleanup(true);
        }
    }
}

pub fn tmp(name: &str) -> ScratchDir {
    ScratchDir::new(name)
}

/// A valid document granting read and write on `root`, with `block` as its
/// only tool and the host transport.
pub fn config_value(root: &Path) -> Value {
    json!({
        "version": 3,
        "name": "fixture",
        "instructions": { "10-role": "You are a test agent.", "05-first": "Be brief." },
        "tools": ["block"],
        "grants": { "read": [root], "write": [root] },
        "budget": { "model_calls": 10 },
        "task": "do the thing"
    })
}

pub fn config(root: &Path) -> ProgramDocument {
    serde_json::from_value(config_value(root)).unwrap()
}

pub fn program(root: &Path) -> ResolvedProgram {
    resolve(&config(root)).unwrap()
}

pub fn program_with(root: &Path, edit: impl FnOnce(&mut Value)) -> Result<ResolvedProgram, crate::ProgramError> {
    let mut value = config_value(root);
    edit(&mut value);
    let config: ProgramDocument = serde_json::from_value(value)?;
    resolve(&config)
}

pub fn spec(name: &str, effect: Effect) -> ToolSpec {
    ToolSpec {
        name: name.into(),
        description: format!("probe {name}"),
        instruction: None,
        params: json!({ "type": "object" }),
        effect,
    }
}
