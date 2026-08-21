//! Parsing and validating the configuration document; resolving it into a program.
//!
//! Implements docs/config.md. Every rule there maps to one
//! `ConfigError::Invalid { key, rule }` naming the offending key in dotted
//! form, for example `programs.survey.grants.read[0]`.

use crate::{
    grants, Budget, ChildProgram, Config, ConfigError, DoneWhen, Grants, HostToolDef, ModelConfig, SandboxConfig,
    ToolDef,
};
use serde::Serialize;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

/// The configuration format version this crate accepts.
pub const CONFIG_VERSION: u32 = 1;

/// A configuration with `task` removed, every path canonical, and child
/// programs resolved recursively. What `episode/start.program` records.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Program {
    pub name: String,
    pub instructions: BTreeMap<String, String>,
    pub tools: Vec<String>,
    pub tool_defs: BTreeMap<String, ToolDef>,
    pub host_tools: BTreeMap<String, HostToolDef>,
    pub grants: Grants,
    pub budget: Budget,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub done_when: Option<DoneWhen>,
    /// Inherited by every child program.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub model: Option<ModelConfig>,
    /// Inherited by every child program.
    pub sandbox: SandboxConfig,
    pub programs: BTreeMap<String, Program>,
}

impl Program {
    /// The JSON recorded in `episode/start.program`.
    pub fn to_value(&self) -> serde_json::Value {
        serde_json::to_value(self).expect("a program serializes")
    }
}

/// The keys shared by a root document and a child program.
struct Section<'a> {
    name: &'a str,
    instructions: &'a BTreeMap<String, String>,
    tools: &'a [String],
    tool_defs: &'a BTreeMap<String, ToolDef>,
    host_tools: &'a BTreeMap<String, HostToolDef>,
    grants: &'a Grants,
    budget: &'a Budget,
    done_when: Option<&'a DoneWhen>,
    programs: &'a BTreeMap<String, ChildProgram>,
}

impl<'a> From<&'a Config> for Section<'a> {
    fn from(c: &'a Config) -> Self {
        Section {
            name: &c.name,
            instructions: &c.instructions,
            tools: &c.tools,
            tool_defs: &c.tool_defs,
            host_tools: &c.host_tools,
            grants: &c.grants,
            budget: &c.budget,
            done_when: c.done_when.as_ref(),
            programs: &c.programs,
        }
    }
}

impl<'a> From<&'a ChildProgram> for Section<'a> {
    fn from(c: &'a ChildProgram) -> Self {
        Section {
            name: &c.name,
            instructions: &c.instructions,
            tools: &c.tools,
            tool_defs: &c.tool_defs,
            host_tools: &c.host_tools,
            grants: &c.grants,
            budget: &c.budget,
            done_when: c.done_when.as_ref(),
            programs: &c.programs,
        }
    }
}

fn invalid(key: impl Into<String>, rule: impl Into<String>) -> ConfigError {
    ConfigError::Invalid { key: key.into(), rule: rule.into() }
}

/// Parses the document text. Unknown keys and wrong types are `Parse`
/// errors; the rules of docs/config.md are checked by [`validate`].
pub fn parse(text: &str) -> Result<Config, ConfigError> {
    Ok(serde_json::from_str(text)?)
}

/// Reads, parses, validates, and resolves a document from `path`.
pub fn load(path: &Path) -> Result<Program, ConfigError> {
    let config = parse(&std::fs::read_to_string(path)?)?;
    resolve(&config)
}

/// Checks every rule of docs/config.md that does not need the filesystem.
pub fn validate(config: &Config) -> Result<(), ConfigError> {
    if config.version != CONFIG_VERSION {
        return Err(invalid("version", format!("is {CONFIG_VERSION}")));
    }
    if config.task.trim().is_empty() {
        return Err(invalid("task", "is not empty"));
    }
    if let Some(model) = &config.model {
        if model.provider.trim().is_empty() {
            return Err(invalid("model.provider", "is not empty"));
        }
        if model.model.trim().is_empty() {
            return Err(invalid("model.model", "is not empty"));
        }
        if model.max_output_tokens == Some(0) {
            return Err(invalid("model.max_output_tokens", "is greater than 0"));
        }
    }
    validate_section("", &Section::from(config))
}

fn key_at(prefix: &str, key: &str) -> String {
    if prefix.is_empty() {
        key.to_string()
    } else {
        format!("{prefix}.{key}")
    }
}

fn require_absolute(key: &str, path: &Path) -> Result<(), ConfigError> {
    if path.is_absolute() {
        Ok(())
    } else {
        Err(invalid(key, "is an absolute path"))
    }
}

fn validate_section(prefix: &str, s: &Section) -> Result<(), ConfigError> {
    let key = |k: &str| key_at(prefix, k);
    if s.name.trim().is_empty() {
        return Err(invalid(key("name"), "is not empty"));
    }
    if s.instructions.is_empty() {
        return Err(invalid(key("instructions"), "has at least one entry"));
    }
    for (section, text) in s.instructions {
        if text.trim().is_empty() {
            return Err(invalid(key(&format!("instructions.{section}")), "is not empty"));
        }
    }
    if s.tools.is_empty() {
        return Err(invalid(key("tools"), "has at least one entry"));
    }
    for (i, name) in s.tools.iter().enumerate() {
        if s.tools[..i].contains(name) {
            return Err(invalid(key(&format!("tools[{i}]")), format!("lists `{name}` once")));
        }
    }
    if s.tools.iter().any(|t| t == "edit") && s.grants.write.is_empty() {
        return Err(invalid(key("tools"), "`edit` requires a non-empty grants.write"));
    }
    if s.tools.iter().any(|t| t == "spawn") && s.grants.spawn.is_empty() {
        return Err(invalid(key("tools"), "`spawn` requires a non-empty grants.spawn"));
    }
    for (name, def) in s.tool_defs {
        let k = |field: &str| key(&format!("tool_defs.{name}.{field}"));
        require_absolute(&k("exec"), &def.exec)?;
        if def.description.trim().is_empty() {
            return Err(invalid(k("description"), "is not empty"));
        }
        if def.timeout_seconds == 0 {
            return Err(invalid(k("timeout_seconds"), "is greater than 0"));
        }
        if let Some(cwd) = &def.cwd {
            require_absolute(&k("cwd"), cwd)?;
        }
    }
    for (name, def) in s.host_tools {
        let k = |field: &str| key(&format!("host_tools.{name}.{field}"));
        if def.description.trim().is_empty() {
            return Err(invalid(k("description"), "is not empty"));
        }
        if !def.params.is_object() {
            return Err(invalid(k("params"), "is a JSON Schema object"));
        }
    }
    if s.grants.read.is_empty() {
        return Err(invalid(key("grants.read"), "has at least one entry"));
    }
    for (i, path) in s.grants.read.iter().enumerate() {
        require_absolute(&key(&format!("grants.read[{i}]")), path)?;
    }
    for (i, path) in s.grants.write.iter().enumerate() {
        require_absolute(&key(&format!("grants.write[{i}]")), path)?;
    }
    for (i, name) in s.grants.spawn.iter().enumerate() {
        if !s.programs.contains_key(name) {
            return Err(invalid(
                key(&format!("grants.spawn[{i}]")),
                format!("names an entry in programs; `{name}` is absent"),
            ));
        }
    }
    let b = s.budget;
    if b.model_calls == 0 {
        return Err(invalid(key("budget.model_calls"), "is greater than 0"));
    }
    if b.tokens == Some(0) {
        return Err(invalid(key("budget.tokens"), "is greater than 0"));
    }
    if b.seconds == Some(0) {
        return Err(invalid(key("budget.seconds"), "is greater than 0"));
    }
    if b.max_episodes == 0 {
        return Err(invalid(key("budget.max_episodes"), "is at least 1, counting this episode"));
    }
    if b.loop_threshold < 2 {
        return Err(invalid(key("budget.loop_threshold"), "is at least 2"));
    }
    if let Some(done) = s.done_when {
        if let Some(verify) = &done.verify {
            if !s.tools.contains(verify) {
                return Err(invalid(key("done_when.verify"), format!("names a tool in tools; `{verify}` is absent")));
            }
        }
        if done.returns.as_ref().is_some_and(|r| !r.is_object()) {
            return Err(invalid(key("done_when.returns"), "is a JSON Schema object"));
        }
    }
    for (name, child) in s.programs {
        validate_section(&key(&format!("programs.{name}")), &Section::from(child))?;
    }
    Ok(())
}

/// Validates, then canonicalizes every path, resolves `tool_defs` defaults,
/// and resolves child programs. A child's read roots must lie within the
/// parent's read roots and its write roots within the parent's write roots.
pub fn resolve(config: &Config) -> Result<Program, ConfigError> {
    validate(config)?;
    let inherited = (config.model.clone(), config.sandbox.clone());
    resolve_section("", &Section::from(config), &inherited, None)
}

fn resolve_section(
    prefix: &str,
    s: &Section,
    inherited: &(Option<ModelConfig>, SandboxConfig),
    parent: Option<&Grants>,
) -> Result<Program, ConfigError> {
    let key = |k: &str| key_at(prefix, k);
    let canonical = |k: String, path: &Path| -> Result<PathBuf, ConfigError> {
        std::fs::canonicalize(path).map_err(|e| invalid(k, format!("names an existing path: {}: {e}", path.display())))
    };
    let roots = |k: &str, paths: &[PathBuf]| -> Result<Vec<PathBuf>, ConfigError> {
        paths.iter().enumerate().map(|(i, p)| canonical(key(&format!("{k}[{i}]")), p)).collect()
    };
    let grants = Grants {
        read: roots("grants.read", &s.grants.read)?,
        write: roots("grants.write", &s.grants.write)?,
        spawn: s.grants.spawn.clone(),
    };
    if let Some(parent) = parent {
        for (field, own, theirs) in [("read", &grants.read, &parent.read), ("write", &grants.write, &parent.write)] {
            if let Some(i) = own.iter().position(|p| !grants::contains(theirs, p)) {
                return Err(invalid(
                    key(&format!("grants.{field}[{i}]")),
                    format!("lies within a {field} root of the parent program"),
                ));
            }
        }
    }
    let mut tool_defs = BTreeMap::new();
    for (name, def) in s.tool_defs {
        let k = |field: &str| key(&format!("tool_defs.{name}.{field}"));
        let exec = canonical(k("exec"), &def.exec)?;
        if !exec.is_file() {
            return Err(invalid(k("exec"), "names a file"));
        }
        let cwd = match &def.cwd {
            Some(cwd) => canonical(k("cwd"), cwd)?,
            None => grants.read[0].clone(),
        };
        tool_defs.insert(name.clone(), ToolDef { exec, cwd: Some(cwd), ..def.clone() });
    }
    let mut programs = BTreeMap::new();
    for (name, child) in s.programs {
        let program =
            resolve_section(&key(&format!("programs.{name}")), &Section::from(child), inherited, Some(&grants))?;
        programs.insert(name.clone(), program);
    }
    Ok(Program {
        name: s.name.to_string(),
        instructions: s.instructions.clone(),
        tools: s.tools.to_vec(),
        tool_defs,
        host_tools: s.host_tools.clone(),
        grants,
        budget: s.budget.clone(),
        done_when: s.done_when.cloned(),
        model: inherited.0.clone(),
        sandbox: inherited.1.clone(),
        programs,
    })
}

#[cfg(test)]
#[path = "config_test.rs"]
mod tests;
