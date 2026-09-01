//! Fixtures shared by the tests of this crate: temporary directories, a
//! minimal document and the contract it resolves to, and probe tool
//! specifications.

use crate::document::{resolve, ResolvedContract};
use crate::{ContractDocument, Effect, ToolSpec};
use serde_json::{json, Value};
use std::ops::Deref;
use std::path::Path;

pub struct ScratchDir(Option<tempfile::TempDir>);

impl ScratchDir {
    fn new(name: &str) -> Self {
        assert_eq!(Path::new(name).file_name(), Some(name.as_ref()), "scratch name must be one path component");
        Self(Some(tempfile::Builder::new().prefix(&format!("foe-contract-{name}-")).tempdir().unwrap()))
    }

    fn path(&self) -> &Path {
        self.0.as_ref().unwrap().path()
    }
}

impl Deref for ScratchDir {
    type Target = Path;

    fn deref(&self) -> &Self::Target {
        self.path()
    }
}

impl AsRef<Path> for ScratchDir {
    fn as_ref(&self) -> &Path {
        self.path()
    }
}

impl serde::Serialize for ScratchDir {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serde::Serialize::serialize(self.path(), serializer)
    }
}

impl Drop for ScratchDir {
    fn drop(&mut self) {
        let Some(mut dir) = self.0.take() else { return };
        if std::thread::panicking() {
            eprintln!("retained failed test directory: {}", dir.path().display());
            dir.disable_cleanup(true);
            return;
        }
        let path = dir.path().to_path_buf();
        dir.close().unwrap_or_else(|error| panic!("failed to remove test directory {}: {error}", path.display()));
    }
}

pub fn tmp(name: &str) -> ScratchDir {
    ScratchDir::new(name)
}

/// A valid document granting read and write on `root`, with `block` as its
/// only tool and the host transport.
pub fn config_value(root: &Path) -> Value {
    json!({
        "version": 4,
        "name": "fixture",
        "instructions": { "10-role": "You are a test agent.", "05-first": "Be brief." },
        "tools": ["block"],
        "grants": { "read": [root], "write": [root] },
        "budget": { "model_calls": 10 },
        "task": "do the thing"
    })
}

pub fn config(root: &Path) -> ContractDocument {
    serde_json::from_value(config_value(root)).unwrap()
}

pub fn contract(root: &Path) -> ResolvedContract {
    resolve(&config(root)).unwrap()
}

pub fn contract_with(root: &Path, edit: impl FnOnce(&mut Value)) -> Result<ResolvedContract, crate::ContractError> {
    let mut value = config_value(root);
    edit(&mut value);
    let config: ContractDocument = serde_json::from_value(value)?;
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
