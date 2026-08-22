//! Tool resolution from three sources, effect checks against grants, dispatch with capability handles.
//!
//! Implements docs/design.md (Tools). A name in `tools` resolves against
//! the built-in tools, then `tool_defs`, then `host_tools`; a name found in
//! two sources or in none is a construction error. The built-in `block`
//! tool lives here; the other built-ins arrive as `extra_builtins` from the
//! crates that implement them. The synthesized `return` tool exists only
//! when `done_when.returns` is set and is listed after the named tools.

use crate::config::Program;
use crate::harness_text as text;
use crate::schema;
use crate::{
    CallCtx, ConfigError, Effect, ExecRequest, ExecResult, Executor, HostToolDef, Tool, ToolDef, ToolSpec, ToolValue,
};
use foe_log::ToolCall;
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::{Duration, Instant};

/// The capability handles an episode owns. Dispatch passes each tool the
/// subset its effect entitles it to.
#[derive(Clone, Default)]
pub struct Handles {
    pub reader: Option<Arc<dyn crate::Reader>>,
    pub writer: Option<Arc<dyn crate::Writer>>,
    pub executor: Option<Arc<dyn Executor>>,
    pub spawner: Option<Arc<dyn crate::Spawner>>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Source {
    Builtin,
    Configured,
    Host,
}

struct Entry {
    spec: ToolSpec,
    tool: Arc<dyn Tool>,
    source: Source,
    /// Set for a `tool_defs` entry, which verification runs differently.
    exec: Option<Arc<ExecTool>>,
}

pub struct Registry {
    /// In `tools` order, then `return` when synthesized.
    entries: Vec<Entry>,
}

fn invalid(rule: String) -> ConfigError {
    ConfigError::Invalid { key: "tools".into(), rule }
}

/// Applies the resolution order to every name in `tools`.
pub fn resolve_sources(
    tools: &[String],
    builtins: &[&str],
    configured: &[&str],
    host: &[&str],
) -> Result<Vec<Source>, ConfigError> {
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

pub fn block_spec() -> ToolSpec {
    ToolSpec {
        name: text::BLOCK_NAME.into(),
        description: text::BLOCK_DESCRIPTION.into(),
        instruction: None,
        params: json!({
            "type": "object",
            "properties": {
                "code": { "type": "string", "enum": ["goal-unreachable", "ambiguous-task", "missing-capability"] },
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

/// The specifications a program's registry will hold, in the order the
/// model sees them, without constructing any tool. Identity uses this.
/// `extra_builtins` are the specifications of built-in tools implemented
/// outside this crate.
pub fn resolve_specs(program: &Program, extra_builtins: &[ToolSpec]) -> Result<Vec<ToolSpec>, ConfigError> {
    let block = block_spec();
    let builtins: Vec<&ToolSpec> = std::iter::once(&block).chain(extra_builtins).collect();
    let builtin_names: Vec<&str> = builtins.iter().map(|s| s.name.as_str()).collect();
    let configured: Vec<&str> = program.tool_defs.keys().map(String::as_str).collect();
    let host: Vec<&str> = program.host_tools.keys().map(String::as_str).collect();
    let sources = resolve_sources(&program.tools, &builtin_names, &configured, &host)?;
    let mut specs: Vec<ToolSpec> = program
        .tools
        .iter()
        .zip(sources)
        .map(|(name, source)| match source {
            Source::Builtin => builtins.iter().find(|s| &s.name == name).map(|s| (*s).clone()).expect("resolved"),
            Source::Configured => exec_spec(name, &program.tool_defs[name]),
            Source::Host => host_spec(name, &program.host_tools[name]),
        })
        .collect();
    if let Some(schema) = program.done_when.as_ref().and_then(|d| d.returns.as_ref()) {
        specs.push(return_spec(schema));
    }
    for spec in &specs {
        check_effect(program, spec)?;
    }
    Ok(specs)
}

/// A tool whose declared effect exceeds the grants is refused. Execution
/// needs no grant: declaring a `tool_defs` entry is what permits it, and a
/// built-in executor is bounded by its own construction.
fn check_effect(program: &Program, spec: &ToolSpec) -> Result<(), ConfigError> {
    let uncovered = match spec.effect {
        Effect::Reads => program.grants.read.is_empty().then_some("grants.read"),
        Effect::Writes => program.grants.write.is_empty().then_some("grants.write"),
        Effect::Spawns => program.grants.spawn.is_empty().then_some("grants.spawn"),
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

impl Registry {
    /// Resolves `program.tools`. `host_tools` are the implementations of the
    /// document's `host_tools` entries; their specifications come from the
    /// document. `extra_builtins` are built-in tools implemented elsewhere:
    /// the coding tools and the spawn and team tools.
    pub fn new(
        program: &Program,
        host_tools: Vec<Box<dyn Tool>>,
        extra_builtins: Vec<Box<dyn Tool>>,
    ) -> Result<Self, ConfigError> {
        let extra_specs: Vec<ToolSpec> = extra_builtins.iter().map(|t| t.spec().clone()).collect();
        let specs = resolve_specs(program, &extra_specs)?;
        let mut builtins: BTreeMap<String, Arc<dyn Tool>> =
            extra_builtins.into_iter().map(|t| (t.spec().name.clone(), Arc::from(t))).collect();
        builtins.insert(text::BLOCK_NAME.into(), Arc::new(BlockTool));
        let mut hosts: BTreeMap<String, Arc<dyn Tool>> =
            host_tools.into_iter().map(|t| (t.spec().name.clone(), Arc::from(t))).collect();
        let mut entries = Vec::new();
        for spec in specs {
            let name = spec.name.as_str();
            let mut exec = None;
            let (tool, source): (Arc<dyn Tool>, Source) = if let Some(def) = program.tool_defs.get(name) {
                let tool = Arc::new(ExecTool { spec: spec.clone(), def: def.clone() });
                exec = Some(tool.clone());
                (tool, Source::Configured)
            } else if program.host_tools.contains_key(name) {
                let tool = hosts
                    .remove(name)
                    .ok_or_else(|| invalid(format!("host tool `{name}` has no implementation registered")))?;
                (tool, Source::Host)
            } else if name == text::RETURN_NAME && !program.tools.iter().any(|t| t == name) {
                (Arc::new(ReturnTool { spec: spec.clone() }), Source::Builtin)
            } else {
                (builtins.remove(name).expect("resolved as built in"), Source::Builtin)
            };
            entries.push(Entry { spec, tool, source, exec });
        }
        Ok(Self { entries })
    }

    /// Specifications in the order the model sees them.
    pub fn specs(&self) -> impl Iterator<Item = &ToolSpec> {
        self.entries.iter().map(|e| &e.spec)
    }

    pub fn schemas(&self) -> Vec<foe_log::ToolSchema> {
        self.specs().map(ToolSpec::schema).collect()
    }

    pub fn source(&self, name: &str) -> Option<Source> {
        self.entry(name).map(|e| e.source)
    }

    pub fn effect(&self, name: &str) -> Option<Effect> {
        self.entry(name).map(|e| e.spec.effect)
    }

    pub fn has_return(&self) -> bool {
        self.entry(text::RETURN_NAME).is_some()
    }

    fn entry(&self, name: &str) -> Option<&Entry> {
        self.entries.iter().find(|e| e.spec.name == name)
    }

    /// The system prompt: instruction sections in key order, then the
    /// instruction of every tool that has one, in `tools` order.
    pub fn system_prompt(&self, instructions: &BTreeMap<String, String>) -> String {
        let mut parts: Vec<String> = instructions.values().cloned().collect();
        let tool_parts: Vec<String> = self
            .specs()
            .filter_map(|s| {
                s.instruction
                    .as_ref()
                    .map(|i| text::fill(text::TOOL_INSTRUCTION_TEMPLATE, &[("name", &s.name), ("instruction", i)]))
            })
            .collect();
        if !tool_parts.is_empty() {
            parts.push(text::TOOL_INSTRUCTIONS_HEADING.into());
            parts.extend(tool_parts);
        }
        parts.join(text::SECTION_SEPARATOR)
    }

    fn ctx(
        &self,
        effect: Effect,
        handles: &Handles,
        call_id: String,
        step: u32,
        spill_dir: PathBuf,
        deadline: Option<Instant>,
    ) -> CallCtx {
        let h = handles;
        let (reader, writer, executor, spawner) = match effect {
            Effect::Pure => (None, None, None, None),
            Effect::Reads => (h.reader.clone(), None, None, None),
            Effect::Writes => (h.reader.clone(), h.writer.clone(), None, None),
            Effect::Execs => (h.reader.clone(), None, h.executor.clone(), None),
            Effect::Spawns => (h.reader.clone(), None, None, h.spawner.clone()),
        };
        CallCtx { call_id, step, reader, writer, executor, spawner, spill_dir, deadline }
    }

    /// Runs one call with the handles its effect entitles it to. An unknown
    /// name, or arguments outside the tool's declared parameter schema, yield
    /// an error result before the tool receives any capability handle. This is
    /// the one place a tool call's arguments are checked, for built-in,
    /// configured, and host tools alike.
    pub async fn dispatch(
        &self,
        handles: &Handles,
        call: &ToolCall,
        step: u32,
        spill_dir: PathBuf,
        deadline: Option<Instant>,
    ) -> ToolValue {
        let Some(entry) = self.entry(&call.name) else {
            return ToolValue::error(text::fill(text::UNKNOWN_TOOL, &[("name", &call.name)]));
        };
        let reason = match call.args.is_object() {
            false => Some("arguments are a JSON object".to_string()),
            true => schema::arguments_conform(&entry.spec.params, &call.args).err(),
        };
        if let Some(reason) = reason {
            return ToolValue::error(text::fill(text::INVALID_ARGS, &[("name", &call.name), ("reason", &reason)]));
        }
        let ctx = self.ctx(entry.spec.effect, handles, call.id.clone(), step, spill_dir, deadline);
        entry.tool.call(call.args.clone(), &ctx).await
    }

    /// Runs the tool `name` on a candidate as a verifier, per docs/config.md
    /// `done_when`. A program's `done_when.verify` and a workflow node's
    /// `verify` both name the tool this way. Returns the findings; an empty
    /// list means accepted. `Err` means the verifier failed rather than
    /// judged: it could not run, a `tool_defs` executable exited with a
    /// status other than zero, or a tool returned an error. The episode then
    /// ends as `failed`.
    pub async fn verify_with(
        &self,
        name: &str,
        handles: &Handles,
        candidate: &Value,
        step: u32,
        spill_dir: PathBuf,
        deadline: Option<Instant>,
    ) -> Result<Vec<String>, String> {
        let entry = self.entry(name).ok_or_else(|| format!("verifier `{name}` is not registered"))?;
        let ctx = self.ctx(entry.spec.effect, handles, format!("verify-{step}"), step, spill_dir, deadline);
        if let Some(exec) = &entry.exec {
            let executor = ctx.executor.clone().ok_or("verifier has no executor")?;
            let mut req = exec.request(vec![]);
            req.stdin = Some(serde_json::to_vec(candidate).map_err(|e| e.to_string())?);
            let result =
                run_blocking(executor, req).await.map_err(|e| format!("verifier `{name}` failed to run: {e}"))?;
            if result.exit_code != Some(0) {
                return Err(format!("verifier `{name}` failed: {}", exec.render(&result)));
            }
            return Ok(String::from_utf8_lossy(&result.stdout)
                .lines()
                .filter(|l| !l.trim().is_empty())
                .map(str::to_string)
                .collect());
        }
        let value = entry.tool.call(candidate.clone(), &ctx).await;
        if value.is_error {
            return Err(format!("verifier `{name}` failed: {}", value.rendered.unwrap_or_default()));
        }
        value
            .value
            .as_array()
            .and_then(|items| items.iter().map(|i| i.as_str().map(str::to_string)).collect())
            .ok_or_else(|| format!("verifier `{name}` returned a value that is not a list of strings"))
    }
}

/// A tool declared in `tool_defs`: the model's `args` become the argument
/// vector of the executable, standard output and standard error are
/// captured, and the exit code is data. A non-zero exit is a result.
pub struct ExecTool {
    spec: ToolSpec,
    def: ToolDef,
}

impl ExecTool {
    fn request(&self, args: Vec<String>) -> ExecRequest {
        ExecRequest {
            program: self.def.exec.clone(),
            args,
            cwd: self.def.cwd.clone().unwrap_or_default(),
            env: BTreeMap::new(),
            timeout: Duration::from_secs(self.def.timeout_seconds),
            network: self.def.network,
            stdin: None,
        }
    }

    /// Standard output, then standard error when present, the exit code,
    /// and a timeout notice: the text the model reads, and the diagnostic
    /// of a failed verification.
    fn render(&self, result: &ExecResult) -> String {
        let mut rendered = String::from_utf8_lossy(&result.stdout).into_owned();
        let stderr = String::from_utf8_lossy(&result.stderr);
        if !stderr.is_empty() {
            rendered.push_str(&text::fill(text::EXEC_STDERR, &[("stderr", &stderr)]));
        }
        let code = result.exit_code.map_or("none".to_string(), |c| c.to_string());
        rendered.push_str(&text::fill(text::EXEC_EXIT, &[("code", &code)]));
        if result.timed_out {
            let seconds = self.def.timeout_seconds.to_string();
            rendered.push_str(&text::fill(text::EXEC_TIMED_OUT, &[("seconds", &seconds)]));
        }
        rendered
    }
}

async fn run_blocking(executor: Arc<dyn Executor>, req: ExecRequest) -> Result<ExecResult, crate::CapError> {
    tokio::task::spawn_blocking(move || executor.run(req))
        .await
        .map_err(|e| crate::CapError::Invalid(format!("executor task failed: {e}")))?
}

#[async_trait::async_trait]
impl Tool for ExecTool {
    fn spec(&self) -> &ToolSpec {
        &self.spec
    }

    async fn call(&self, args: Value, ctx: &CallCtx) -> ToolValue {
        let Some(argv) = args
            .get("args")
            .and_then(Value::as_array)
            .and_then(|a| a.iter().map(|v| v.as_str().map(str::to_string)).collect::<Option<Vec<_>>>())
        else {
            return ToolValue::error(text::fill(
                text::INVALID_ARGS,
                &[("name", &self.spec.name), ("reason", "`args` is a list of strings")],
            ));
        };
        let Some(executor) = ctx.executor.clone() else {
            return ToolValue::error(format!("`{}` has no executor", self.spec.name));
        };
        match run_blocking(executor, self.request(argv)).await {
            Ok(result) => {
                let value = json!({
                    "exit_code": result.exit_code, "stdout": String::from_utf8_lossy(&result.stdout),
                    "stderr": String::from_utf8_lossy(&result.stderr), "timed_out": result.timed_out,
                    "duration_ms": result.duration.as_millis() as u64,
                });
                ToolValue { value, rendered: Some(self.render(&result)), is_error: false }
            }
            Err(e) => ToolValue::error(format!("`{}` could not start: {e}", self.spec.name)),
        }
    }
}

/// The built-in `block` tool. The loop ends the episode as `blocked` when
/// a call to it succeeds.
struct BlockTool;

#[async_trait::async_trait]
impl Tool for BlockTool {
    fn spec(&self) -> &ToolSpec {
        static SPEC: std::sync::OnceLock<ToolSpec> = std::sync::OnceLock::new();
        SPEC.get_or_init(block_spec)
    }

    async fn call(&self, args: Value, _ctx: &CallCtx) -> ToolValue {
        ToolValue::ok(args, "Blocked.")
    }
}

/// The synthesized `return` tool. Its parameter schema carries
/// `done_when.returns` under the `value` property, so dispatch has already
/// checked the value against it.
struct ReturnTool {
    spec: ToolSpec,
}

#[async_trait::async_trait]
impl Tool for ReturnTool {
    fn spec(&self) -> &ToolSpec {
        &self.spec
    }

    async fn call(&self, args: Value, _ctx: &CallCtx) -> ToolValue {
        let value = args.get("value").cloned().unwrap_or(Value::Null);
        ToolValue::ok(json!({ "value": value }), "Returned.")
    }
}

#[cfg(test)]
#[path = "registry_test.rs"]
mod tests;
