//! Parsing and validating a program document; resolving it into a program.
//!
//! Implements docs/config.md. Every rule there maps to one
//! `ProgramError::Invalid { key, rule }` naming the offending key in dotted
//! form, for example `programs.survey.grants.read[0]`.

use crate::workflow::{self, WorkflowConfig};
use crate::{
    contains, Budget, ChildProgramDocument, ContextConfig, DoneWhen, Grants, HostToolDef, ModelConfig, ProgramDocument,
    ProgramError, SandboxConfig, ToolDef,
};
use serde::Serialize;
use std::collections::BTreeMap;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::sync::Arc;

/// The program-document format version this crate accepts.
pub const PROGRAM_FORMAT_VERSION: u32 = 3;

/// Executable bytes read through one open file during program construction.
/// Identity and execution use this same snapshot.
#[derive(Debug, Clone, PartialEq)]
pub struct ExecutableImage {
    pub path: PathBuf,
    pub basename: std::ffi::OsString,
    pub sha256: String,
    pub bytes: Arc<[u8]>,
}

/// Which declared programs a recursive program-tree walk includes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProgramTreeSelection {
    /// Every declaration that contributes to program identity.
    AllDeclared,
    /// The programs an episode can start through a spawn grant or workflow.
    ExecutableReachable,
}

/// Whether the completion schema makes the standard `learned` observation
/// channel an evidence requirement.
pub fn completion_evidence_required(done: Option<&DoneWhen>) -> bool {
    done.and_then(|d| d.returns.as_ref()?.get("required")?.as_array())
        .is_some_and(|required| required.iter().any(|field| field == "learned"))
}

/// A program document with `task` removed, every path canonical, and child
/// programs resolved recursively. What `episode/start.program` records.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ResolvedProgram {
    pub name: String,
    pub instructions: BTreeMap<String, String>,
    pub tools: Vec<String>,
    pub tool_defs: BTreeMap<String, ToolDef>,
    /// Executable snapshots read while the program is constructed.
    #[serde(skip)]
    pub executable_images: BTreeMap<String, ExecutableImage>,
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
    /// The executable transport snapshot when `model.provider` is `exec`.
    #[serde(skip)]
    pub transport_executable: Option<ExecutableImage>,
    /// Inherited by every child program.
    pub sandbox: SandboxConfig,
    /// The root document's ancestry claim, kept so that
    /// `episode/start.program` records it. Identity omits it.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub program_lineage: Option<crate::ProgramLineage>,
    pub programs: BTreeMap<String, ResolvedProgram>,
    /// Resolved programs of workflow model nodes, keyed by node path.
    /// Their declarations remain under `workflow` in the serialized program.
    #[serde(skip)]
    pub workflow_programs: BTreeMap<String, ResolvedProgram>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub workflow: Option<WorkflowConfig>,
}

impl ResolvedProgram {
    /// The JSON recorded in `episode/start.program`.
    pub fn to_value(&self) -> serde_json::Value {
        serde_json::to_value(self).expect("a program serializes")
    }

    /// A child program available to an explicit spawn or a workflow model
    /// firing. Workflow paths use `/` between nested node names.
    pub fn spawned_program(&self, name: &str) -> Option<&ResolvedProgram> {
        self.workflow_programs.get(name).or_else(|| self.programs.get(name))
    }

    /// Whether this episode may launch the named child program.
    pub fn permits_spawn(&self, name: &str) -> bool {
        self.grants.spawn.iter().any(|granted| granted == name) || self.workflow_programs.contains_key(name)
    }

    /// The root and its recursively declared programs, with stable paths.
    pub fn program_tree(&self, selection: ProgramTreeSelection) -> Vec<(String, &ResolvedProgram)> {
        fn walk<'a>(
            program: &'a ResolvedProgram,
            path: String,
            selection: ProgramTreeSelection,
            out: &mut Vec<(String, &'a ResolvedProgram)>,
        ) {
            out.push((path.clone(), program));
            for (name, child) in &program.programs {
                if selection == ProgramTreeSelection::AllDeclared || program.grants.spawn.contains(name) {
                    walk(child, format!("{path}.programs.{name}"), selection, out);
                }
            }
            for (node_path, child) in &program.workflow_programs {
                let nodes = node_path.replace('/', ".workflow.nodes.");
                walk(child, format!("{path}.workflow.nodes.{nodes}.model"), selection, out);
            }
        }
        let mut out = Vec::new();
        walk(self, "program".into(), selection, &mut out);
        out
    }
}

/// A root document holds every key a child program holds, and the keys a
/// child inherits besides. The shared keys are validated and resolved
/// through one path, which takes the child form of either.
impl From<&ProgramDocument> for ChildProgramDocument {
    fn from(c: &ProgramDocument) -> Self {
        ChildProgramDocument {
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

fn invalid(key: impl Into<String>, rule: impl Into<String>) -> ProgramError {
    ProgramError::Invalid { key: key.into(), rule: rule.into() }
}

/// `Ok` when `holds`; otherwise the error naming `key` and `rule`.
fn require(holds: bool, key: impl Into<String>, rule: impl Into<String>) -> Result<(), ProgramError> {
    if holds {
        Ok(())
    } else {
        Err(invalid(key, rule))
    }
}

/// Parses the document text. Unknown keys and wrong types are `Parse`
/// errors; the rules of docs/config.md are checked by [`validate`].
pub fn parse(text: &str) -> Result<ProgramDocument, ProgramError> {
    Ok(serde_json::from_str(text)?)
}

/// Reads, parses, validates, and resolves a document from `path`.
pub fn load(path: &Path) -> Result<ResolvedProgram, ProgramError> {
    let config = parse(&std::fs::read_to_string(path)?)?;
    resolve(&config)
}

/// Checks every rule of docs/config.md that does not need the filesystem.
pub fn validate(config: &ProgramDocument) -> Result<(), ProgramError> {
    require(config.version == PROGRAM_FORMAT_VERSION, "version", format!("is {PROGRAM_FORMAT_VERSION}"))?;
    require(!config.task.trim().is_empty(), "task", "is not empty")?;
    validate_section("", &ChildProgramDocument::from(config))
}

/// `key` under `prefix` in dotted form; `key` alone at the root.
fn key_at(prefix: &str, key: &str) -> String {
    format!("{prefix}.{key}").trim_start_matches('.').to_string()
}

fn require_absolute(key: &str, path: &Path) -> Result<(), ProgramError> {
    require(path.is_absolute(), key, "is an absolute path")
}

fn validate_section(prefix: &str, s: &ChildProgramDocument) -> Result<(), ProgramError> {
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
    for (field, paths) in [("read", &s.grants.read), ("write", &s.grants.write), ("execute", &s.grants.execute)] {
        for (i, path) in paths.iter().enumerate() {
            require_absolute(&key(&format!("grants.{field}[{i}]")), path)?;
        }
    }
    for (i, port) in s.grants.bind.iter().enumerate() {
        require(*port > 0, key(&format!("grants.bind[{i}]")), "is between 1 and 65535")?;
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
            if completion_evidence_required(Some(done)) {
                let learned = &returns["properties"]["learned"];
                let item = &learned["items"];
                let required = item["required"].as_array();
                let shape = learned["type"] == "array"
                    && learned["minItems"].as_u64().is_some_and(|n| n > 0)
                    && item["type"] == "object"
                    && item["properties"]["claim"]["type"] == "string"
                    && item["properties"]["seq"]["type"] == "integer"
                    && item["properties"]["seq"]["minimum"].as_i64() == Some(0)
                    && required.is_some_and(|r| r.iter().any(|f| f == "claim") && r.iter().any(|f| f == "seq"));
                require(
                    shape,
                    key("done_when.returns.properties.learned"),
                    "is the standard non-empty array of claim and seq objects",
                )?;
            }
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
pub fn resolve(config: &ProgramDocument) -> Result<ResolvedProgram, ProgramError> {
    resolve_with_executables(config, &BTreeMap::new())
}

/// Resolves a document using inherited executable bytes for the dotted keys
/// present in `inherited`. A spawned child receives these bytes through
/// inherited descriptors, so it never reopens those paths.
pub fn resolve_with_executables(
    config: &ProgramDocument,
    inherited: &BTreeMap<String, (Arc<[u8]>, std::ffi::OsString)>,
) -> Result<ResolvedProgram, ProgramError> {
    validate(config)?;
    let settings = (config.model.clone(), config.sandbox.clone());
    let lineage = config.program_lineage.clone();
    Ok(ResolvedProgram {
        program_lineage: lineage,
        ..resolve_section("", &ChildProgramDocument::from(config), &settings, None, inherited)?
    })
}

fn resolve_section(
    prefix: &str,
    s: &ChildProgramDocument,
    inherited: &(Option<ModelConfig>, SandboxConfig),
    parent: Option<&Grants>,
    inherited_executables: &BTreeMap<String, (Arc<[u8]>, std::ffi::OsString)>,
) -> Result<ResolvedProgram, ProgramError> {
    let key = |k: &str| key_at(prefix, k);
    let model = s.model.clone().or_else(|| inherited.0.clone());
    let descendants = (model.clone(), inherited.1.clone());
    let canonical = |k: String, path: &Path| -> Result<PathBuf, ProgramError> {
        std::fs::canonicalize(path).map_err(|e| invalid(k, format!("names an existing path: {}: {e}", path.display())))
    };
    let roots = |k: &str, paths: &[PathBuf]| -> Result<Vec<PathBuf>, ProgramError> {
        paths.iter().enumerate().map(|(i, p)| canonical(key(&format!("{k}[{i}]")), p)).collect()
    };
    let grants = Grants {
        read: roots("grants.read", &s.grants.read)?,
        write: roots("grants.write", &s.grants.write)?,
        execute: roots("grants.execute", &s.grants.execute)?,
        spawn: s.grants.spawn.clone(),
        bind: s.grants.bind.clone(),
        task_session: s.grants.task_session,
    };
    if let Some(parent) = parent {
        for (field, own, theirs) in [
            ("read", &grants.read, &parent.read),
            ("write", &grants.write, &parent.write),
            ("execute", &grants.execute, &parent.execute),
        ] {
            if let Some(i) = own.iter().position(|p| !contains(theirs, p)) {
                return Err(invalid(
                    key(&format!("grants.{field}[{i}]")),
                    format!("lies within a {field} root of the parent program"),
                ));
            }
        }
        if let Some(i) = grants.bind.iter().position(|port| !parent.bind.contains(port)) {
            return Err(invalid(key(&format!("grants.bind[{i}]")), "is a bind port of the parent program"));
        }
        require(!grants.task_session || parent.task_session, key("grants.task_session"), "is granted by the parent")?;
    }
    let image = |key: String, path: &Path, configured: &Path| -> Result<ExecutableImage, ProgramError> {
        let (bytes, basename): (Arc<[u8]>, std::ffi::OsString) = match inherited_executables.get(&key) {
            Some((bytes, basename)) => (bytes.clone(), basename.clone()),
            None => {
                let mut file = std::fs::File::open(path)
                    .map_err(|e| invalid(&key, format!("is readable for construction: {e}")))?;
                let metadata = file.metadata().map_err(|e| invalid(&key, format!("has readable metadata: {e}")))?;
                require(metadata.is_file(), &key, "names a file")?;
                require(metadata.permissions().mode() & 0o111 != 0, &key, "names an executable file")?;
                let mut bytes = Vec::new();
                std::io::Read::read_to_end(&mut file, &mut bytes)
                    .map_err(|e| invalid(&key, format!("is readable for construction: {e}")))?;
                let basename = configured.file_name().unwrap_or_else(|| std::ffi::OsStr::new("executable")).to_owned();
                (Arc::from(bytes), basename)
            }
        };
        Ok(ExecutableImage { path: path.to_path_buf(), basename, sha256: crate::identity::sha256_hex(&bytes), bytes })
    };
    let mut tool_defs = BTreeMap::new();
    for (name, def) in &s.tool_defs {
        let k = |field: &str| key(&format!("tool_defs.{name}.{field}"));
        let exec_key = k("exec");
        let exec = if inherited_executables.contains_key(&exec_key) {
            def.exec.clone()
        } else {
            canonical(exec_key, &def.exec)?
        };
        let cwd = match &def.cwd {
            Some(cwd) => canonical(k("cwd"), cwd)?,
            None => grants.read[0].clone(),
        };
        tool_defs.insert(name.clone(), ToolDef { exec, cwd: Some(cwd), ..def.clone() });
    }
    let executable_images = tool_defs
        .iter()
        .filter(|(name, _)| s.tools.contains(name))
        .map(|(name, def)| {
            let image = image(key(&format!("tool_defs.{name}.exec")), &def.exec, &s.tool_defs[name].exec)?;
            Ok((name.clone(), image))
        })
        .collect::<Result<_, ProgramError>>()?;
    let transport_executable = model
        .as_ref()
        .filter(|model| model.provider == "exec")
        .map(|model| {
            let key = key("model.exec");
            let path = PathBuf::from(model.option("exec").unwrap_or_default());
            require_absolute(&key, &path)?;
            let path = match inherited_executables.contains_key(&key) {
                true => path,
                false => canonical(key.clone(), &path)?,
            };
            image(key, &path, &PathBuf::from(model.option("exec").unwrap_or_default()))
        })
        .transpose()?;
    let mut programs = BTreeMap::new();
    for (name, child) in &s.programs {
        let program = resolve_section(
            &key(&format!("programs.{name}")),
            child,
            &descendants,
            Some(&grants),
            inherited_executables,
        )?;
        programs.insert(name.clone(), program);
    }
    let mut program = ResolvedProgram {
        name: s.name.clone(),
        instructions: s.instructions.clone(),
        tools: s.tools.clone(),
        tool_defs,
        executable_images,
        host_tools: s.host_tools.clone(),
        grants,
        budget: s.budget.clone(),
        done_when: s.done_when.clone(),
        context: s.context.clone(),
        model,
        transport_executable,
        sandbox: inherited.1.clone(),
        program_lineage: None,
        programs,
        workflow_programs: BTreeMap::new(),
        workflow: s.workflow.clone(),
    };
    if let Some(wf) = &s.workflow {
        let mut resolved = BTreeMap::new();
        for (path, node) in workflow::model_nodes(wf, "") {
            let dotted = path.replace('/', ".workflow.nodes.");
            let k = key(&format!("workflow.nodes.{dotted}.model"));
            let node = resolve_section(
                &k,
                &workflow::node_program(node),
                &descendants,
                Some(&program.grants),
                inherited_executables,
            )?;
            within_ceiling(&k, &node, &program)?;
            resolved.insert(path, node);
        }
        program.workflow_programs = resolved;
    }
    Ok(program)
}

/// Requires a workflow model node's program to name no authority the
/// program containing the workflow does not already hold, at every depth.
/// Grants are checked by `resolve_section`; this covers the rest of
/// docs/workflow.md "Model nodes".
fn within_ceiling(prefix: &str, node: &ResolvedProgram, ceiling: &ResolvedProgram) -> Result<(), ProgramError> {
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
    bounded(!node.grants.task_session || ceiling.grants.task_session, key("grants.task_session"))?;
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

#[cfg(test)]
#[path = "document_test.rs"]
mod tests;
