//! Linux process-subtree ownership through cgroup v2.
//!
//! Each episode runs inside one cgroup whose descendants contain its child
//! episodes. The invocation also owns a sibling cgroup for explicitly
//! authorized task-lifetime sessions. A child enters its cgroup through a
//! shell wrapper before user code executes. Process groups remain the
//! portable fallback when the host has not delegated a cgroup hierarchy.

use crate::sandbox::Policy;
use crate::RuntimeError;
use foe_log::SandboxMode;
use foe_log::{ProcessBoundaryInfo, ProcessBoundaryKind, SubtreeCleanup};
use serde::{Deserialize, Serialize};
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Arc;

const CGROUP_MOUNT: &str = "/sys/fs/cgroup";
const ENTER_SCRIPT: &str = "printf '%s\\n' \"$$\" > \"$1\" || exit 125; shift; exec \"$@\"";
/// Fixed executable used to enter a cgroup before the requested program.
pub const PROCESS_BOUNDARY_LAUNCHER: &str = "/bin/sh";

/// Runtime launch metadata that a parent writes for a child. These paths
/// describe host state and do not participate in program identity.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BoundaryPaths {
    pub episode: PathBuf,
    pub task: PathBuf,
}

/// The process ownership available to one episode.
pub struct ProcessOwnership {
    boundary: Option<Arc<ProcessBoundary>>,
    info: ProcessBoundaryInfo,
    root: Option<RootCleanup>,
}

impl ProcessOwnership {
    /// Enters the boundary a parent prepared, or creates an invocation
    /// boundary for a root episode. Off mode selects the process-group
    /// fallback without probing the host.
    pub fn enter(mode: SandboxMode, episode_id: &str, inherited: Option<BoundaryPaths>) -> Result<Self, RuntimeError> {
        if mode == SandboxMode::Off {
            return Ok(Self::fallback("sandbox.mode is off"));
        }
        if let Some(paths) = inherited {
            let boundary = ProcessBoundary::enter(paths)?;
            return Ok(Self::enforced(boundary, None));
        }
        Ok(Self::from_root(ProcessBoundary::root(episode_id)))
    }

    fn from_root(result: Result<(ProcessBoundary, RootCleanup), RuntimeError>) -> Self {
        match result {
            Ok((boundary, cleanup)) => Self::enforced(boundary, Some(cleanup)),
            Err(_) => Self::fallback("the host did not delegate a writable cgroup v2 hierarchy"),
        }
    }

    fn enforced(boundary: ProcessBoundary, root: Option<RootCleanup>) -> Self {
        Self {
            boundary: Some(Arc::new(boundary)),
            info: ProcessBoundaryInfo {
                kind: ProcessBoundaryKind::CgroupV2,
                subtree_cleanup: SubtreeCleanup::Enforced,
                reason: None,
            },
            root,
        }
    }

    fn fallback(reason: &str) -> Self {
        Self {
            boundary: None,
            info: ProcessBoundaryInfo {
                kind: ProcessBoundaryKind::ProcessGroup,
                subtree_cleanup: SubtreeCleanup::Observational,
                reason: Some(reason.to_string()),
            },
            root: None,
        }
    }

    pub fn boundary(&self) -> Option<Arc<ProcessBoundary>> {
        self.boundary.clone()
    }

    pub fn info(&self) -> ProcessBoundaryInfo {
        self.info.clone()
    }

    /// Adds the cgroup paths that the runtime must manage after Landlock
    /// narrows the episode. Executable policies drop these paths.
    pub fn authorize(&self, policy: &mut Policy) -> Result<(), RuntimeError> {
        if let Some(boundary) = &self.boundary {
            policy
                .add_executable(Path::new(PROCESS_BOUNDARY_LAUNCHER), "cgroup process-boundary launcher".into())
                .map_err(RuntimeError::Sandbox)?;
            policy.add_runtime_control(boundary.paths.episode.clone());
            policy.add_runtime_control(boundary.paths.task.clone());
        }
        if let Some(root) = &self.root {
            policy.add_runtime_control(root.invocation.parent().unwrap().to_path_buf());
            policy.add_runtime_control_file(root.origin.join("cgroup.procs"));
        }
        Ok(())
    }
}

impl Drop for ProcessOwnership {
    fn drop(&mut self) {
        if let Some(root) = self.root.take() {
            root.finish();
        }
    }
}

/// One episode cgroup and the invocation-owned task cgroup beside the root
/// episode. Child episode cgroups nest below `episode`.
pub struct ProcessBoundary {
    paths: BoundaryPaths,
}

impl ProcessBoundary {
    fn root(episode_id: &str) -> Result<(Self, RootCleanup), RuntimeError> {
        let origin = current_cgroup()?;
        let name = format!("foe-{}-{}", safe_name(episode_id), std::process::id());
        let manager = origin.join("foe-runtime");
        std::fs::create_dir_all(&manager).map_err(|e| cgroup_error("create", &manager, e))?;
        let invocation = manager.join(name);
        let task = invocation.join("task");
        let episode = invocation.join("episode");
        let process = episode.join("process");
        let setup = (|| {
            for path in [&invocation, &task, &episode, &process] {
                std::fs::create_dir(path).map_err(|e| cgroup_error("create", path, e))?;
            }
            require_boundary_files(&episode)?;
            join(&process)
        })();
        if let Err(error) = setup {
            let _ = remove_tree(&invocation);
            return Err(error);
        }
        let boundary = Self { paths: BoundaryPaths { episode, task } };
        let cleanup = RootCleanup {
            origin,
            invocation,
            episode: boundary.paths.episode.clone(),
            task: boundary.paths.task.clone(),
        };
        Ok((boundary, cleanup))
    }

    fn enter(paths: BoundaryPaths) -> Result<Self, RuntimeError> {
        let current = current_cgroup()?;
        if current != paths.episode.join("process") {
            return Err(RuntimeError::Sandbox(
                "cgroup v2: lineage.json process_boundary does not name the process's current cgroup".into(),
            ));
        }
        let invocation = paths
            .episode
            .ancestors()
            .find(|path| path.file_name().is_some_and(|name| name == "episode"))
            .and_then(Path::parent);
        if invocation.is_none_or(|path| paths.task != path.join("task")) {
            return Err(RuntimeError::Sandbox(
                "cgroup v2: lineage.json process_boundary does not name the invocation task cgroup".into(),
            ));
        }
        require_boundary_files(&paths.episode)?;
        Ok(Self { paths })
    }

    /// Creates the cgroup a child must enter before its runtime starts.
    pub fn child(&self, child_id: &str) -> Result<ChildBoundary, RuntimeError> {
        let episode = self.paths.episode.join(format!("child-{}", safe_name(child_id)));
        let process = episode.join("process");
        std::fs::create_dir(&episode).map_err(|e| cgroup_error("create", &episode, e))?;
        if let Err(error) = std::fs::create_dir(&process).map_err(|e| cgroup_error("create", &process, e)) {
            let _ = std::fs::remove_dir(&episode);
            return Err(error);
        }
        if let Err(error) = require_boundary_files(&episode) {
            let _ = remove_tree(&episode);
            return Err(error);
        }
        Ok(ChildBoundary { paths: BoundaryPaths { episode, task: self.paths.task.clone() } })
    }

    pub fn task_procs(&self) -> PathBuf {
        self.paths.task.join("cgroup.procs")
    }
}

/// A child boundary stays owned by the parent until cleanup has killed and
/// reaped every process in the subtree.
pub struct ChildBoundary {
    paths: BoundaryPaths,
}

impl ChildBoundary {
    pub fn paths(&self) -> BoundaryPaths {
        self.paths.clone()
    }

    pub fn process_procs(&self) -> PathBuf {
        self.paths.episode.join("process/cgroup.procs")
    }

    pub fn terminate(&self) -> Result<(), RuntimeError> {
        kill_and_remove(&self.paths.episode)
    }
}

impl Drop for ChildBoundary {
    fn drop(&mut self) {
        let _ = self.terminate();
    }
}

/// Builds a command whose shell joins `cgroup.procs` before it executes the
/// fixed argument vector. The shell uses only built-ins before `exec`.
pub fn command_in(procs: &Path, argv: &[OsString]) -> Command {
    let mut command = Command::new(PROCESS_BOUNDARY_LAUNCHER);
    command.arg("-c").arg(ENTER_SCRIPT).arg("foe-cgroup-enter").arg(procs).args(argv);
    command
}

fn current_cgroup() -> Result<PathBuf, RuntimeError> {
    let text = std::fs::read_to_string("/proc/self/cgroup")
        .map_err(|e| RuntimeError::Sandbox(format!("cgroup v2: read /proc/self/cgroup: {e}")))?;
    let relative = text
        .lines()
        .find_map(|line| line.strip_prefix("0::"))
        .ok_or_else(|| RuntimeError::Sandbox("cgroup v2: /proc/self/cgroup has no unified hierarchy".into()))?;
    Ok(Path::new(CGROUP_MOUNT).join(relative.trim_start_matches('/')))
}

fn safe_name(name: &str) -> String {
    name.chars().map(|c| if c.is_ascii_alphanumeric() || matches!(c, '-' | '_') { c } else { '_' }).collect()
}

fn require_boundary_files(path: &Path) -> Result<(), RuntimeError> {
    for file in ["cgroup.kill", "cgroup.events", "cgroup.procs"] {
        let control = path.join(file);
        if !control.is_file() {
            return Err(RuntimeError::Sandbox(format!("cgroup v2: {} is unavailable", control.display())));
        }
    }
    Ok(())
}

fn join(path: &Path) -> Result<(), RuntimeError> {
    let procs = path.join("cgroup.procs");
    std::fs::write(&procs, std::process::id().to_string()).map_err(|e| cgroup_error("join", &procs, e))
}

fn populated(path: &Path) -> Result<bool, RuntimeError> {
    let events = path.join("cgroup.events");
    let text = std::fs::read_to_string(&events).map_err(|e| cgroup_error("read", &events, e))?;
    Ok(text.lines().any(|line| line == "populated 1"))
}

fn kill_and_remove(path: &Path) -> Result<(), RuntimeError> {
    if populated(path)? {
        let kill = path.join("cgroup.kill");
        std::fs::write(&kill, "1").map_err(|e| cgroup_error("kill", &kill, e))?;
        while populated(path)? {
            std::thread::yield_now();
        }
    }
    remove_tree(path)
}

fn remove_tree(path: &Path) -> Result<(), RuntimeError> {
    let mut children = Vec::new();
    for entry in std::fs::read_dir(path).map_err(|e| cgroup_error("list", path, e))? {
        let entry = entry.map_err(|e| cgroup_error("list", path, e))?;
        if entry.file_type().is_ok_and(|kind| kind.is_dir()) {
            children.push(entry.path());
        }
    }
    for child in children {
        remove_tree(&child)?;
    }
    std::fs::remove_dir(path).map_err(|e| cgroup_error("remove", path, e))
}

fn cgroup_error(action: &str, path: &Path, error: std::io::Error) -> RuntimeError {
    RuntimeError::Sandbox(format!("cgroup v2: {action} {}: {error}", path.display()))
}

struct RootCleanup {
    origin: PathBuf,
    invocation: PathBuf,
    episode: PathBuf,
    task: PathBuf,
}

impl RootCleanup {
    fn finish(self) {
        if join(&self.origin).is_err() {
            return;
        }
        let _ = kill_and_remove(&self.episode);
        if populated(&self.task).is_ok_and(|is_populated| !is_populated) {
            let _ = remove_tree(&self.task);
            let _ = std::fs::remove_dir(&self.invocation);
        }
    }
}

#[cfg(test)]
#[path = "process_boundary_test.rs"]
pub(crate) mod tests;
