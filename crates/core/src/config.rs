//! Parsing and validating the configuration document; resolving it into a program.
//!
//! Implements docs/config.md. Every rule there maps to one
//! `ConfigError::Invalid { key, rule }` naming the offending key in dotted
//! form, for example `programs.survey.grants.read[0]`.

use crate::workflow::{self, WorkflowConfig};
use crate::{
    grants, Budget, ChildProgram, Config, ConfigError, ContextConfig, DoneWhen, Grants, HostToolDef, ModelConfig,
    SandboxConfig, ToolDef,
};
use serde::Serialize;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

/// The configuration format version this crate accepts.
pub const CONFIG_VERSION: u32 = 2;

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
    #[serde(skip_serializing_if = "Option::is_none")]
    pub context: Option<ContextConfig>,
    /// Inherited by every child program.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub model: Option<ModelConfig>,
    /// Inherited by every child program.
    pub sandbox: SandboxConfig,
    pub programs: BTreeMap<String, Program>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub workflow: Option<WorkflowConfig>,
}

impl Program {
    /// The JSON recorded in `episode/start.program`.
    pub fn to_value(&self) -> serde_json::Value {
        serde_json::to_value(self).expect("a program serializes")
    }
}

/// A root document holds every key a child program holds, and the keys a
/// child inherits besides. The shared keys are validated and resolved
/// through one path, which takes the child form of either.
impl From<&Config> for ChildProgram {
    fn from(c: &Config) -> Self {
        ChildProgram {
            name: c.name.clone(),
            instructions: c.instructions.clone(),
            tools: c.tools.clone(),
            tool_defs: c.tool_defs.clone(),
            host_tools: c.host_tools.clone(),
            grants: c.grants.clone(),
            budget: c.budget.clone(),
            done_when: c.done_when.clone(),
            context: c.context.clone(),
            model: c.model.clone(),
            programs: c.programs.clone(),
            workflow: c.workflow.clone(),
        }
    }
}

fn invalid(key: impl Into<String>, rule: impl Into<String>) -> ConfigError {
    ConfigError::Invalid { key: key.into(), rule: rule.into() }
}

/// `Ok` when `holds`; otherwise the error naming `key` and `rule`.
fn require(holds: bool, key: impl Into<String>, rule: impl Into<String>) -> Result<(), ConfigError> {
    if holds {
        Ok(())
    } else {
        Err(invalid(key, rule))
    }
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
    require(config.version == CONFIG_VERSION, "version", format!("is {CONFIG_VERSION}"))?;
    require(!config.task.trim().is_empty(), "task", "is not empty")?;
    validate_section("", &ChildProgram::from(config))
}

/// `key` under `prefix` in dotted form; `key` alone at the root.
fn key_at(prefix: &str, key: &str) -> String {
    format!("{prefix}.{key}").trim_start_matches('.').to_string()
}

fn require_absolute(key: &str, path: &Path) -> Result<(), ConfigError> {
    require(path.is_absolute(), key, "is an absolute path")
}

fn validate_section(prefix: &str, s: &ChildProgram) -> Result<(), ConfigError> {
    let key = |k: &str| key_at(prefix, k);
    if let Some(model) = &s.model {
        for (field, value) in [("provider", &model.provider), ("model", &model.model)] {
            require(!value.trim().is_empty(), key(&format!("model.{field}")), "is not empty")?;
        }
    }
    require(!s.name.trim().is_empty(), key("name"), "is not empty")?;
    require(!s.instructions.is_empty(), key("instructions"), "has at least one entry")?;
    for (section, text) in &s.instructions {
        require(!text.trim().is_empty(), key(&format!("instructions.{section}")), "is not empty")?;
    }
    require(!s.tools.is_empty(), key("tools"), "has at least one entry")?;
    for (i, name) in s.tools.iter().enumerate() {
        require(!s.tools[..i].contains(name), key(&format!("tools[{i}]")), format!("lists `{name}` once"))?;
    }
    for (tool, roots, field) in [("edit", s.grants.write.len(), "write"), ("spawn", s.grants.spawn.len(), "spawn")] {
        let needs = s.tools.iter().any(|t| t == tool);
        require(!needs || roots > 0, key("tools"), format!("`{tool}` requires a non-empty grants.{field}"))?;
    }
    for (name, def) in &s.tool_defs {
        let k = |field: &str| key(&format!("tool_defs.{name}.{field}"));
        require_absolute(&k("exec"), &def.exec)?;
        require(!def.description.trim().is_empty(), k("description"), "is not empty")?;
        require(def.timeout_seconds > 0, k("timeout_seconds"), "is greater than 0")?;
        if let Some(cwd) = &def.cwd {
            require_absolute(&k("cwd"), cwd)?;
        }
    }
    for (name, def) in &s.host_tools {
        let k = |field: &str| key(&format!("host_tools.{name}.{field}"));
        require(!def.description.trim().is_empty(), k("description"), "is not empty")?;
        require(def.params.is_object(), k("params"), "is a JSON Schema object")?;
        crate::schema::check(k("params"), &def.params)?;
    }
    require(!s.grants.read.is_empty(), key("grants.read"), "has at least one entry")?;
    for (i, path) in s.grants.read.iter().enumerate() {
        require_absolute(&key(&format!("grants.read[{i}]")), path)?;
    }
    for (i, path) in s.grants.write.iter().enumerate() {
        require_absolute(&key(&format!("grants.write[{i}]")), path)?;
    }
    for (i, name) in s.grants.spawn.iter().enumerate() {
        let rule = format!("names an entry in programs; `{name}` is absent");
        require(s.programs.contains_key(name), key(&format!("grants.spawn[{i}]")), rule)?;
    }
    let b = &s.budget;
    require(b.model_calls > 0, key("budget.model_calls"), "is greater than 0")?;
    require(b.input_tokens != Some(0), key("budget.input_tokens"), "is greater than 0")?;
    require(b.output_tokens != Some(0), key("budget.output_tokens"), "is greater than 0")?;
    require(b.seconds != Some(0), key("budget.seconds"), "is greater than 0")?;
    require(b.max_episodes > 0, key("budget.max_episodes"), "is at least 1, counting this episode")?;
    require(b.loop_threshold >= 2, key("budget.loop_threshold"), "is at least 2")?;
    if let Some(done) = &s.done_when {
        if let Some(verify) = &done.verify {
            let rule = format!("names a tool in tools; `{verify}` is absent");
            require(s.tools.contains(verify), key("done_when.verify"), rule)?;
        }
        require(
            done.returns.as_ref().is_none_or(|r| r.is_object()),
            key("done_when.returns"),
            "is a JSON Schema object",
        )?;
        if let Some(returns) = &done.returns {
            crate::schema::check(key("done_when.returns"), returns)?;
        }
    }
    if let Some(c) = s.context.as_ref().filter(|c| c.compact) {
        let fits = c.window_tokens.is_none_or(|w| w > c.reserve_tokens + c.keep_recent_tokens);
        require(fits, key("context.window_tokens"), "exceeds reserve_tokens plus keep_recent_tokens")?;
    }
    for (name, child) in &s.programs {
        validate_section(&key(&format!("programs.{name}")), child)?;
    }
    if let Some(wf) = &s.workflow {
        workflow::check(&key("workflow"), wf, &s.tools, &mut validate_section)?;
    }
    Ok(())
}

/// Validates, then canonicalizes every path, resolves `tool_defs` defaults,
/// and resolves child programs. A child's read roots must lie within the
/// parent's read roots and its write roots within the parent's write roots.
pub fn resolve(config: &Config) -> Result<Program, ConfigError> {
    validate(config)?;
    let inherited = (config.model.clone(), config.sandbox.clone());
    resolve_section("", &ChildProgram::from(config), &inherited, None)
}

fn resolve_section(
    prefix: &str,
    s: &ChildProgram,
    inherited: &(Option<ModelConfig>, SandboxConfig),
    parent: Option<&Grants>,
) -> Result<Program, ConfigError> {
    let key = |k: &str| key_at(prefix, k);
    let model = s.model.clone().or_else(|| inherited.0.clone());
    let descendants = (model.clone(), inherited.1.clone());
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
    for (name, def) in &s.tool_defs {
        let k = |field: &str| key(&format!("tool_defs.{name}.{field}"));
        let exec = canonical(k("exec"), &def.exec)?;
        require(exec.is_file(), k("exec"), "names a file")?;
        let cwd = match &def.cwd {
            Some(cwd) => canonical(k("cwd"), cwd)?,
            None => grants.read[0].clone(),
        };
        tool_defs.insert(name.clone(), ToolDef { exec, cwd: Some(cwd), ..def.clone() });
    }
    let mut programs = BTreeMap::new();
    for (name, child) in &s.programs {
        let program = resolve_section(&key(&format!("programs.{name}")), child, &descendants, Some(&grants))?;
        programs.insert(name.clone(), program);
    }
    let program = Program {
        name: s.name.clone(),
        instructions: s.instructions.clone(),
        tools: s.tools.clone(),
        tool_defs,
        host_tools: s.host_tools.clone(),
        grants,
        budget: s.budget.clone(),
        done_when: s.done_when.clone(),
        context: s.context.clone(),
        model,
        sandbox: inherited.1.clone(),
        programs,
        workflow: s.workflow.clone(),
    };
    if let Some(wf) = &s.workflow {
        let mut subset = |k: &str, p: &ChildProgram| {
            let node = resolve_section(k, p, &descendants, Some(&program.grants))?;
            within_ceiling(k, &node, &program)
        };
        workflow::check(&key("workflow"), wf, &s.tools, &mut subset)?;
    }
    Ok(program)
}

/// Requires a workflow model node's program to name no authority the
/// program containing the workflow does not already hold, at every depth.
/// Grants are checked by `resolve_section`; this covers the rest of
/// docs/workflow.md "Model nodes".
fn within_ceiling(prefix: &str, node: &Program, ceiling: &Program) -> Result<(), ConfigError> {
    let key = |k: &str| key_at(prefix, k);
    let bounded = |holds: bool, k: String| require(holds, k, "does not exceed the workflow ceiling");
    for (i, name) in node.tools.iter().enumerate() {
        bounded(ceiling.tools.contains(name), key(&format!("tools[{i}]")))?;
    }
    for (name, own) in &node.tool_defs {
        let Some(cap) = ceiling.tool_defs.get(name) else {
            return Err(invalid(key(&format!("tool_defs.{name}")), "names a configured tool in the workflow ceiling"));
        };
        // A description and an instruction change what the model reads
        // rather than what the process may do, so a node may reword them.
        // The four fields below are the process authority itself.
        for (field, holds) in [
            ("exec", own.exec == cap.exec),
            ("cwd", own.cwd.as_ref().is_some_and(|p| cap.cwd.as_ref().is_some_and(|root| p.starts_with(root)))),
            ("network", !own.network || cap.network),
            ("timeout_seconds", own.timeout_seconds <= cap.timeout_seconds),
        ] {
            bounded(holds, key(&format!("tool_defs.{name}.{field}")))?;
        }
    }
    for (name, own) in &node.host_tools {
        bounded(ceiling.host_tools.get(name) == Some(own), key(&format!("host_tools.{name}")))?;
    }
    for (i, name) in node.grants.spawn.iter().enumerate() {
        bounded(ceiling.grants.spawn.contains(name), key(&format!("grants.spawn[{i}]")))?;
    }
    // `max_episodes` and `max_concurrent` are absent here. The pool clamps
    // a child's episode share when it reserves, and `max_concurrent` counts
    // one episode's own direct children rather than the tree's, so neither
    // is a ceiling the containing program imposes at construction.
    let (own, cap) = (&node.budget, &ceiling.budget);
    let within = |value: Option<u64>, limit: Option<u64>| value.is_none_or(|v| limit.is_none_or(|l| v <= l));
    for (field, holds) in [
        ("model_calls", own.model_calls <= cap.model_calls),
        ("input_tokens", within(own.input_tokens, cap.input_tokens)),
        ("output_tokens", within(own.output_tokens, cap.output_tokens)),
        ("seconds", within(own.seconds, cap.seconds)),
        ("max_depth", own.max_depth <= cap.max_depth),
        ("loop_threshold", own.loop_threshold <= cap.loop_threshold),
    ] {
        bounded(holds, key(&format!("budget.{field}")))?;
    }
    for (name, child) in &node.programs {
        let cap = ceiling.programs.get(name).ok_or_else(|| {
            invalid(key(&format!("programs.{name}")), "names a descendant program in the workflow ceiling")
        })?;
        within_ceiling(&key(&format!("programs.{name}")), child, cap)?;
    }
    Ok(())
}

/// Resolves a workflow model node's program against the resolved program
/// that declares it, for identity. Keyed as `validate` keys it.
pub fn resolve_node_program(key: &str, parent: &Program, child: &ChildProgram) -> Result<Program, ConfigError> {
    let inherited = (parent.model.clone(), parent.sandbox.clone());
    resolve_section(key, child, &inherited, Some(&parent.grants))
}

#[cfg(test)]
#[path = "config_test.rs"]
mod tests;
