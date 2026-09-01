//! Tool resolution from three sources, and the specification each source
//! produces.
//!
//! Implements docs/design.md (Tools). A name in `tools` resolves against
//! the built-in tools, then `tool_defs`, then `host_tools`; a name found in
//! two sources or in none is a construction error. The specification of the
//! built-in `block` tool lives here; the other built-ins arrive as
//! `extra_builtins` from the crates that implement them. The synthesized
//! `return` tool exists only when `done_when.returns` is set and is listed
//! after the named tools. A declared effect the grants do not cover is
//! refused here, before any tool is constructed.

use crate::document::ResolvedContract;
use crate::harness_text as text;
use crate::{ContractError, Effect, HostToolDef, ToolDef, ToolSpec};
use serde_json::{json, Value};

const BLOCK_CODES: [&str; 4] = ["goal-unreachable", "ambiguous-task", "missing-capability", "child-blocked"];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Source {
    Builtin,
    Configured,
    Host,
}

fn invalid(rule: String) -> ContractError {
    ContractError::Invalid { key: "tools".into(), rule }
}

/// Applies the resolution order to every name in `tools`.
pub fn resolve_sources(
    tools: &[String],
    builtins: &[&str],
    configured: &[&str],
    host: &[&str],
) -> Result<Vec<Source>, ContractError> {
    tools
        .iter()
        .map(|name| {
            let n = name.as_str();
            let found: Vec<Source> =
                [(Source::Builtin, builtins), (Source::Configured, configured), (Source::Host, host)]
                    .into_iter()
                    .filter(|(_, names)| names.contains(&n))
                    .map(|(source, _)| source)
                    .collect();
            match found.as_slice() {
                [one] => Ok(*one),
                [] => Err(invalid(format!(
                    "`{name}` resolves in no source: it is not built in, in tool_defs, or in host_tools"
                ))),
                _ => Err(invalid(format!("`{name}` resolves in more than one source: {found:?}"))),
            }
        })
        .collect()
}

pub fn block_spec(may_report_child_blocked: bool) -> ToolSpec {
    ToolSpec {
        name: text::BLOCK_NAME.into(),
        description: text::BLOCK_DESCRIPTION.into(),
        instruction: None,
        params: json!({
            "type": "object",
            "properties": {
                "code": { "type": "string", "enum": &BLOCK_CODES[..3 + usize::from(may_report_child_blocked)] },
                "message": { "type": "string" }
            },
            "required": ["code", "message"],
            "additionalProperties": false
        }),
        effect: Effect::Pure,
    }
}

pub fn return_spec(schema: &Value) -> ToolSpec {
    ToolSpec {
        name: text::RETURN_NAME.into(),
        description: text::RETURN_DESCRIPTION.into(),
        instruction: None,
        params: json!({ "type": "object", "properties": { "value": schema }, "required": ["value"] }),
        effect: Effect::Pure,
    }
}

pub fn exec_spec(name: &str, def: &ToolDef) -> ToolSpec {
    ToolSpec {
        name: name.into(),
        description: def.description.clone(),
        instruction: def.instruction.clone(),
        params: json!({
            "type": "object",
            "properties": { "args": { "type": "array", "items": { "type": "string" } } },
            "required": ["args"],
            "additionalProperties": false
        }),
        effect: Effect::Execs,
    }
}

pub fn host_spec(name: &str, def: &HostToolDef) -> ToolSpec {
    ToolSpec {
        name: name.into(),
        description: def.description.clone(),
        instruction: def.instruction.clone(),
        params: def.params.clone(),
        effect: def.effect,
    }
}

/// The specifications a contract's registry will hold, in the order the
/// model sees them, without constructing any tool. Fingerprint uses this.
/// `extra_builtins` are the specifications of built-in tools implemented
/// outside this crate.
pub fn resolve_specs(contract: &ResolvedContract, extra_builtins: &[ToolSpec]) -> Result<Vec<ToolSpec>, ContractError> {
    // `return` and `block` belong to the harness even when unlisted: a
    // configured definition would shadow the synthesized specification.
    for name in contract.tool_defs.keys().chain(contract.host_tools.keys()) {
        if name == text::RETURN_NAME || name == text::BLOCK_NAME {
            return Err(invalid(format!(
                "`{name}` is a harness tool name: tool_defs and host_tools may not define it"
            )));
        }
    }
    let block = block_spec(!contract.grants.spawn.is_empty() && contract.tools.iter().any(|name| name == "spawn"));
    let builtins: Vec<&ToolSpec> = std::iter::once(&block).chain(extra_builtins).collect();
    let builtin_names: Vec<&str> = builtins.iter().map(|s| s.name.as_str()).collect();
    let configured: Vec<&str> = contract.tool_defs.keys().map(String::as_str).collect();
    let host: Vec<&str> = contract.host_tools.keys().map(String::as_str).collect();
    let sources = resolve_sources(&contract.tools, &builtin_names, &configured, &host)?;
    let mut specs: Vec<ToolSpec> = contract
        .tools
        .iter()
        .zip(sources)
        .map(|(name, source)| match source {
            Source::Builtin => builtins.iter().find(|s| &s.name == name).map(|s| (*s).clone()).expect("resolved"),
            Source::Configured => exec_spec(name, &contract.tool_defs[name]),
            Source::Host => host_spec(name, &contract.host_tools[name]),
        })
        .collect();
    if let Some(schema) = contract.done_when.as_ref().and_then(|d| d.returns.as_ref()) {
        specs.push(return_spec(schema));
    }
    for spec in &specs {
        check_effect(contract, spec)?;
    }
    Ok(specs)
}

/// A tool whose declared effect exceeds the grants is refused. Execution
/// needs no grant: declaring a `tool_defs` entry is what permits it, and a
/// built-in executor is bounded by its own construction.
fn check_effect(contract: &ResolvedContract, spec: &ToolSpec) -> Result<(), ContractError> {
    let uncovered = match spec.effect {
        Effect::Reads => contract.grants.read.is_empty().then_some("grants.read"),
        Effect::Writes => contract.grants.write.is_empty().then_some("grants.write"),
        Effect::Spawns => contract.grants.spawn.is_empty().then_some("grants.spawn"),
        Effect::Pure | Effect::Execs => None,
    };
    match uncovered {
        Some(key) => Err(invalid(format!(
            "`{}` declares effect {:?}, which the empty {key} does not cover",
            spec.name, spec.effect
        ))),
        None => Ok(()),
    }
}

#[cfg(test)]
#[path = "tools_test.rs"]
mod tests;
