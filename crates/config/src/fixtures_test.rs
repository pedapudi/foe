//! Fixtures shared by the tests of this crate: temporary directories, a
//! minimal document and the program it resolves to, and probe tool
//! specifications.

use crate::config::{resolve, Program};
use crate::{Config, Effect, ToolSpec};
use serde_json::{json, Value};
use std::path::{Path, PathBuf};

pub fn tmp(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("foe-config-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
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

pub fn config(root: &Path) -> Config {
    serde_json::from_value(config_value(root)).unwrap()
}

pub fn program(root: &Path) -> Program {
    resolve(&config(root)).unwrap()
}

pub fn program_with(root: &Path, edit: impl FnOnce(&mut Value)) -> Result<Program, crate::ConfigError> {
    let mut value = config_value(root);
    edit(&mut value);
    let config: Config = serde_json::from_value(value)?;
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
