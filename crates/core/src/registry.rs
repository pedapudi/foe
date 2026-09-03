//! Constructing a contract's tools from their specifications, and dispatch
//! with capability handles.
//!
//! Implements docs/design.md (Tools). `foe_contract::tools` resolves a
//! contract's `tools` into the specifications the model sees; this module
//! builds the tool behind each specification — a built-in, a `tool_defs`
//! executable, or a host implementation — and runs a call with the handles
//! the tool's declared effect entitles it to. The built-in `block` tool and
//! the synthesized `return` tool are implemented here.

use crate::captured_executable::{CapturedExecutable, CapturedExecutableTree};
use crate::{CallCtx, ExecRequest, ExecResult, Executor, Tool, ToolValue};
use foe_contract::document::ResolvedContract;
use foe_contract::harness_text as text;
use foe_contract::tools::{resolve_specs, Source};
use foe_contract::{schema, ContractError, Effect, ToolDef, ToolSpec};
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
    pub sessions: Option<Arc<dyn crate::Sessions>>,
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

fn invalid(rule: String) -> ContractError {
    ContractError::Invalid { key: "tools".into(), rule }
}

impl Registry {
    /// Resolves `contract.tools`. `executables` holds the captured copy each
    /// `tool_defs` entry runs. `host_tools` are the implementations of the
    /// document's `host_tools` entries; their specifications come from the
    /// document. `extra_builtins` are built-in tools implemented elsewhere:
    /// the coding tools and the spawn and team tools.
    pub fn new(
        contract: &ResolvedContract,
        executables: &CapturedExecutableTree,
        host_tools: Vec<Box<dyn Tool>>,
        extra_builtins: Vec<Box<dyn Tool>>,
    ) -> Result<Self, ContractError> {
        let extra_specs: Vec<ToolSpec> = extra_builtins.iter().map(|t| t.spec().clone()).collect();
        let specs = resolve_specs(contract, &extra_specs)?;
        let mut builtins: BTreeMap<String, Arc<dyn Tool>> =
            extra_builtins.into_iter().map(|t| (t.spec().name.clone(), Arc::from(t))).collect();
        if let Some(spec) = specs.iter().find(|spec| spec.name == text::BLOCK_NAME) {
            builtins.insert(text::BLOCK_NAME.into(), Arc::new(BlockTool(spec.clone())));
        }
        let mut hosts: BTreeMap<String, Arc<dyn Tool>> =
            host_tools.into_iter().map(|t| (t.spec().name.clone(), Arc::from(t))).collect();
        let mut entries = Vec::new();
        for spec in specs {
            let name = spec.name.as_str();
            let mut exec = None;
            let (tool, source): (Arc<dyn Tool>, Source) = if let Some(def) = contract.tool_defs.get(name) {
                let executable = executables
                    .tools
                    .get(name)
                    .ok_or_else(|| invalid(format!("configured tool `{name}` has no captured executable")))?;
                let tool = Arc::new(ExecTool { spec: spec.clone(), def: def.clone(), executable: executable.clone() });
                exec = Some(tool.clone());
                (tool, Source::Configured)
            } else if contract.host_tools.contains_key(name) {
                let tool = hosts
                    .remove(name)
                    .ok_or_else(|| invalid(format!("host tool `{name}` has no implementation registered")))?;
                (tool, Source::Host)
            } else if name == text::RETURN_NAME && !contract.tools.iter().any(|t| t == name) {
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

    pub fn has_retrieve(&self) -> bool {
        self.entry(crate::retrieval::NAME).is_some()
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
        let (reader, writer, executor, spawner, sessions) = match effect {
            Effect::Pure => (None, None, None, None, None),
            Effect::Reads => (h.reader.clone(), None, None, None, None),
            Effect::Writes => (h.reader.clone(), h.writer.clone(), None, None, None),
            Effect::Execs => (h.reader.clone(), None, h.executor.clone(), None, h.sessions.clone()),
            Effect::Spawns => (h.reader.clone(), None, None, h.spawner.clone(), None),
        };
        CallCtx { call_id, step, reader, writer, executor, spawner, sessions, composer: None, spill_dir, deadline }
    }

    /// Runs one call with the handles its effect entitles it to. An unknown
    /// name, or arguments outside the tool's declared parameter schema, yield
    /// an error result before the tool receives any capability handle. This is
    /// the one place a tool call's arguments are checked, for built-in,
    /// configured, and host tools alike. `composer` is `Some` only when the
    /// agent loop dispatches the [`crate::COMPOSING_TOOL`].
    pub async fn dispatch(
        &self,
        handles: &Handles,
        call: &ToolCall,
        step: u32,
        spill_dir: PathBuf,
        deadline: Option<Instant>,
        composer: Option<Arc<dyn crate::Composer>>,
    ) -> ToolValue {
        let Some(entry) = self.entry(&call.name) else {
            return ToolValue::invalid(text::fill(text::UNKNOWN_TOOL, &[("name", &call.name)]));
        };
        let reason = match call.args.is_object() {
            false => Some("arguments must be one JSON object".to_string()),
            true => schema::arguments_conform(&entry.spec.params, &call.args).err(),
        };
        if let Some(reason) = reason {
            return ToolValue::invalid(text::fill(text::INVALID_ARGS, &[("name", &call.name), ("reason", &reason)]));
        }
        let mut ctx = self.ctx(entry.spec.effect, handles, call.id.clone(), step, spill_dir, deadline);
        ctx.composer = composer;
        entry.tool.call(call.args.clone(), &ctx).await
    }

    /// Runs the tool `name` on a candidate as a verifier, per docs/config.md
    /// `done_when`. A contract's `done_when.verify` and a workflow node's
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
        let value = entry.tool.call(bind_candidate(&entry.spec.params, candidate), &ctx).await;
        if value.is_error {
            return Err(format!("verifier `{name}` failed: {}", value.rendered.unwrap_or_default()));
        }
        value
            .value
            .as_array()
            .and_then(|items| items.iter().map(|i| i.as_str().map(str::to_string)).collect())
            .ok_or_else(|| format!("verifier `{name}` returned a value that is not a list of strings"))
    }

    /// The verifier fingerprint a `verification/result` records. Configured
    /// tools use the executable digest committed during construction.
    /// Built-in and host tools use the runtime build fingerprint.
    pub fn verifier_fingerprint(&self, name: &str, runtime_build: &str) -> String {
        match self.entry(name).and_then(|e| e.exec.as_ref()) {
            Some(exec) => format!("sha256:{}", exec.executable.sha256),
            None => runtime_build.to_string(),
        }
    }
}

/// Binds a verifier's candidate to the single parameter its schema declares.
///
/// A tool call's arguments are an OBJECT KEYED BY PARAMETER NAME -- that is
/// what [`Registry::dispatch`] validates against `spec.params`, and what a
/// host tool spreads over its function's parameters. A verifier's candidate
/// is a value, not an argument map, so passing it straight through silently
/// reinterprets the candidate's own fields as parameter names.
///
/// With an object-shaped `done_when.returns` that is never right. The
/// verifier is called with the returned document's keys, and a host tool
/// whose signature is the documented single parameter fails with an
/// unexpected-keyword error naming one of the document's fields -- a
/// different field each run, depending on which the model emitted first.
///
/// docs/sdk.md, "Verifiers": the runtime calls the verifier "with the
/// candidate result as its single argument", and the SDK writes the tool in
/// with "a one-parameter schema". So bind it: wrap the candidate under that
/// one declared parameter's name.
///
/// Left alone when the schema does not declare exactly one property. A
/// `tool_defs` executable takes the candidate on stdin and never reaches
/// here, and a verifier that really does declare the candidate's fields as
/// its own parameters keeps working.
fn bind_candidate(params: &Value, candidate: &Value) -> Value {
    let Some(properties) = params.get("properties").and_then(Value::as_object) else {
        return candidate.clone();
    };
    let mut names = properties.keys();
    let (Some(only), None) = (names.next(), names.next()) else {
        return candidate.clone();
    };
    // Already an argument map naming that parameter: pass it through, so a
    // caller that binds for itself is not double-wrapped.
    if candidate.get(only).is_some() {
        return candidate.clone();
    }
    let mut args = serde_json::Map::new();
    args.insert(only.clone(), candidate.clone());
    Value::Object(args)
}

/// A tool declared in `tool_defs`: the model's `args` become the argument
/// vector of the executable, standard output and standard error are
/// captured, and the exit code is data. A non-zero exit is a result.
pub struct ExecTool {
    spec: ToolSpec,
    def: ToolDef,
    executable: Arc<CapturedExecutable>,
}

impl ExecTool {
    fn request(&self, args: Vec<String>) -> ExecRequest {
        ExecRequest {
            command: self.def.exec.clone(),
            captured_executable: Some(self.executable.clone()),
            args,
            cwd: self.def.cwd.clone().unwrap_or_default(),
            env: BTreeMap::new(),
            timeout: Duration::from_secs(self.def.timeout_seconds),
            network: self.def.network,
            stdin: None,
            policy: None,
            pass_fds: Vec::new(),
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
            return ToolValue::invalid(text::fill(
                text::INVALID_ARGS,
                &[("name", &self.spec.name), ("reason", "`args` is a list of strings")],
            ));
        };
        let Some(executor) = ctx.executor.clone() else {
            return ToolValue::unavailable(format!("`{}` has no executor", self.spec.name));
        };
        match run_blocking(executor, self.request(argv)).await {
            Ok(result) => {
                let value = json!({
                    "exit_code": result.exit_code, "stdout": String::from_utf8_lossy(&result.stdout),
                    "stderr": String::from_utf8_lossy(&result.stderr), "timed_out": result.timed_out,
                    "duration_ms": result.duration.as_millis() as u64,
                });
                ToolValue { value, rendered: Some(self.render(&result)), is_error: false, failure: None, subject: None }
            }
            Err(e) => ToolValue::from_cap_error(&format!("tool `{}`", self.spec.name), e),
        }
    }
}

/// The built-in `block` tool. The loop ends the episode as `blocked` when
/// a call to it succeeds.
struct BlockTool(ToolSpec);

#[async_trait::async_trait]
impl Tool for BlockTool {
    fn spec(&self) -> &ToolSpec {
        &self.0
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
