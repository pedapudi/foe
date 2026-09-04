//! Landlock ruleset compiled from grants; ABI probe; narrowing of child processes.
//!
//! A [`Policy`] is the list of what a process may reach. [`Sandbox`] turns a
//! policy into a Landlock ruleset and applies it either to the calling
//! thread or to a process about to be started. `docs/sandbox.md` states what
//! each ABI tier enforces.
//!
//! Landlock restricts the calling thread and every thread or process that
//! thread creates afterwards. This crate forbids unsafe code, so a child is
//! narrowed by starting it from a short-lived thread that first restricts
//! itself rather than through a `pre_exec` hook. The effect is the same: the
//! child inherits the narrower domain before it executes anything.

use crate::captured_executable::{CapturedExecutable, CapturedExecutableTree};
use crate::executable_support;
use crate::RuntimeError;
use foe_contract::document::{ContractTreeSelection, ResolvedContract};
use foe_log::{ResolvedPathPermission, ResolvedPermissions, SandboxInfo, SandboxMode};
use landlock::{
    Access, AccessFs, AccessNet, BitFlags, CompatLevel, Compatible, LandlockStatus, NetPort, PathBeneath, PathFd,
    Ruleset, RulesetAttr, RulesetCreated, RulesetCreatedAttr, RulesetStatus, Scope, ABI,
};
use std::collections::BTreeSet;
use std::os::fd::AsRawFd;
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::sync::Arc;

/// The highest Landlock ABI this module makes use of. A newer kernel is
/// used at this level and recorded as this level.
pub const MAX_ABI: u32 = 7;

/// Directories a dynamic loader searches for shared objects. They remain
/// readable and carry no execute access.
pub const LIBRARY_DIRS: &[&str] = &["/lib", "/lib64", "/usr/lib", "/usr/lib64", "/usr/libexec", "/usr/local/lib"];

/// Read-only system paths that runtimes consult while starting: loader
/// configuration, locale data, and process metadata.
pub const SYSTEM_READ_DIRS: &[&str] = &["/etc", "/usr/share", "/proc", "/sys"];

/// Device files any process may read and write.
pub const DEVICE_FILES: &[&str] = &["/dev/null", "/dev/zero", "/dev/random", "/dev/urandom", "/dev/tty"];

/// What one process may reach. Compiled into a ruleset by [`Sandbox`].
#[derive(Debug, Clone, Default)]
pub struct Policy {
    /// Directories readable in full.
    pub read: Vec<PathBuf>,
    /// Directories where files may be written, created, and removed.
    pub write: Vec<PathBuf>,
    /// Files that may be executed.
    pub exec: Vec<PathBuf>,
    /// Captured executable inodes retained during contract construction.
    pub exec_files: Vec<Arc<CapturedExecutable>>,
    /// Runtime-owned directories only the episode process enumerates and
    /// removes during cleanup: read and removal, never creation or writing.
    pub cleanup: Vec<PathBuf>,
    /// Shared parents of `cleanup` directories. Landlock checks a directory
    /// removal against the victim's parent, so each parent carries directory
    /// removal alone and no other access.
    pub cleanup_parents: Vec<PathBuf>,
    /// Explicit contract grants that remain available to subprocesses after
    /// the executable that starts them receives its narrower policy.
    pub delegated_exec: Vec<PathBuf>,
    /// Single files readable in full. Resolver configuration is present
    /// only when the process may connect. Resolved model credential files
    /// are present only in episode policies. Narrowed executables never
    /// receive credentials.
    pub read_files: Vec<PathBuf>,
    /// The episode's own log directory, readable and writable. `None` for an
    /// executable, which has no log of its own.
    pub log_dir: Option<PathBuf>,
    /// TCP ports the process may bind. Enforced from ABI 4.
    pub bind_tcp: Vec<u16>,
    /// Whether the process may open outbound TCP connections. Enforced from
    /// ABI 4.
    pub connect_tcp: bool,
    /// Runtime-owned cgroup directories. The episode process manages them;
    /// configured executables do not receive this access.
    #[doc(hidden)]
    pub runtime_control: Vec<PathBuf>,
    /// Runtime control files. Executable policies drop this access unless
    /// the runtime adds back the one file a trusted wrapper must write.
    #[doc(hidden)]
    pub runtime_control_files: Vec<PathBuf>,
    #[doc(hidden)]
    pub permissions: ResolvedPermissions,
}

impl Policy {
    /// A standalone internal runtime with fixed read roots and one exact
    /// executable. Used before an episode policy exists.
    pub fn for_runtime_executable(executable: &Path, read: Vec<PathBuf>, reason: &str) -> Result<Policy, String> {
        let mut policy = Policy { read, ..Policy::default() };
        for path in policy.read.clone() {
            policy.record_read(path, reason, None);
        }
        policy.record_implicit_reads();
        policy.add_executable(executable, reason.into())?;
        Ok(policy)
    }

    /// The complete descendant envelope an episode must reserve before
    /// Landlock can narrow it. Planning and execution share this walk.
    pub fn for_plan(config: &ResolvedContract) -> Result<Policy, String> {
        let mut policy = Policy {
            read: config.grants.read.clone(),
            write: config.grants.write.clone(),
            delegated_exec: config.grants.execute.clone(),
            bind_tcp: config.grants.bind.clone(),
            ..Policy::default()
        };
        policy.record_declared(config);
        policy.record_implicit_reads();
        let contracts = config.contract_tree(ContractTreeSelection::ExecutableReachable);
        for (contract_key, contract) in &contracts {
            for path in &contract.grants.execute {
                policy.add_executable(path, format!("declared by {contract_key}.grants.execute"))?;
            }
            for (name, executable) in &contract.captured_executables {
                policy.add_image(
                    &executable.source_path,
                    &executable.sha256,
                    &executable.bytes,
                    format!("selected configured tool {contract_key}.tool_defs.{name}"),
                )?;
                if contract.tool_defs[name].network {
                    policy.reserve_network(format!("{contract_key}.tool_defs.{name}.network is true"));
                }
            }
            if contract.model.is_some() {
                policy.reserve_network(format!("model transport in {contract_key}"));
            }
        }
        if contracts.len() > 1 {
            let executable = std::env::current_exe().map_err(|e| format!("running foe executable: {e}"))?;
            policy.add_executable(&executable, "child episode launcher".into())?;
        }
        Ok(policy)
    }

    /// The planned envelope plus captured inodes and paths that exist only
    /// after the episode directory has been chosen.
    pub fn for_episode(
        config: &ResolvedContract,
        executables: &CapturedExecutableTree,
        log_dir: &Path,
    ) -> Result<Policy, String> {
        let mut policy = Self::for_plan(config)?;
        policy.exec_files = executables.reachable();
        for path in executables.cleanup_roots() {
            policy.add_cleanup(path, "private captured-executable store");
        }
        policy.log_dir = Some(log_dir.to_path_buf());
        policy.record_read_write(log_dir.to_path_buf(), "episode log and spill directory", None);
        Ok(policy)
    }

    /// Narrows one pathname executable to itself and the current contract's
    /// explicit subprocess grants.
    pub fn for_executable(&self, executable: &Path, network: bool) -> Result<Policy, String> {
        let mut policy = self.narrowed();
        for path in &self.delegated_exec {
            policy.add_executable(path, "delegated by this contract's grants.execute".into())?;
        }
        policy.add_executable(executable, "selected executable".into())?;
        if network {
            policy.reserve_network("selected executable declares network access".into());
        }
        Ok(policy)
    }

    /// Narrows one configured executable to its captured inode and the
    /// current contract's explicit subprocess grants.
    pub fn for_immutable_executable(
        &self,
        executable: Arc<CapturedExecutable>,
        network: bool,
    ) -> Result<Policy, String> {
        let mut policy = self.narrowed();
        for path in &self.delegated_exec {
            policy.add_executable(path, "delegated by this contract's grants.execute".into())?;
        }
        policy.add_image(
            &executable.source_path,
            &executable.sha256,
            executable.bytes(),
            "selected configured executable".into(),
        )?;
        policy.exec_files.push(executable);
        if network {
            policy.reserve_network("selected configured executable declares network access".into());
        }
        Ok(policy)
    }

    fn narrowed(&self) -> Policy {
        let mut policy = Policy {
            read: self.read.clone(),
            write: self.write.clone(),
            delegated_exec: self.delegated_exec.clone(),
            bind_tcp: self.bind_tcp.clone(),
            ..Policy::default()
        };
        for path in policy.read.clone() {
            policy.record_read(path, "declared by this contract's grants.read", None);
        }
        for path in policy.write.clone() {
            policy.record_write(path, "declared by this contract's grants.write", None);
        }
        policy.permissions.bind_tcp = policy.bind_tcp.clone();
        policy.record_implicit_reads();
        policy
    }

    fn record_declared(&mut self, config: &ResolvedContract) {
        for path in &config.grants.read {
            self.record_read(path.clone(), "declared by contract.grants.read", None);
        }
        for path in &config.grants.write {
            self.record_write(path.clone(), "declared by contract.grants.write", None);
        }
        self.permissions.bind_tcp = config.grants.bind.clone();
    }

    fn record_implicit_reads(&mut self) {
        for path in LIBRARY_DIRS {
            self.record_existing_read(PathBuf::from(path), "shared-library lookup");
        }
        for path in SYSTEM_READ_DIRS {
            self.record_existing_read(PathBuf::from(path), "runtime system information");
        }
        for path in DEVICE_FILES {
            let path = PathBuf::from(path);
            if path.exists() {
                self.record_read_write(path, "standard runtime device", None);
            }
        }
    }

    fn record_existing_read(&mut self, path: PathBuf, reason: &str) {
        if path.exists() {
            self.record_read(path, reason, None);
        }
    }

    /// Records the captured image whose retained copy the compiled rule
    /// binds. The row names the image by digest because no pathname is the
    /// granted object; the construction-time source is provenance in the
    /// reason and receives no execute rule of its own.
    fn add_image(&mut self, source: &Path, sha256: &str, image: &[u8], reason: String) -> Result<(), String> {
        let reason = format!("{reason}; captured from {}", source.display());
        self.record_execute(PathBuf::from(format!("captured:{sha256}")), &reason, Some(sha256.to_string()));
        self.add_support(image, &reason, &mut BTreeSet::new())
    }

    /// Adds an exact runtime executable and every loader or interpreter the
    /// kernel executes while starting it.
    pub fn add_executable(&mut self, path: &Path, reason: String) -> Result<(), String> {
        let path = std::fs::canonicalize(path).map_err(|e| format!("{}: {e}", path.display()))?;
        push_unique(&mut self.exec, path.clone());
        self.record_execute(path.clone(), &reason, None);
        if path.is_file() {
            let image = std::fs::read(&path).map_err(|e| format!("{}: {e}", path.display()))?;
            self.add_support(&image, &reason, &mut BTreeSet::new())?;
        }
        Ok(())
    }

    fn add_support(&mut self, image: &[u8], owner: &str, seen: &mut BTreeSet<PathBuf>) -> Result<(), String> {
        let Some(path) = executable_support::interpreter(image).map_err(|e| format!("{owner}: {e}"))? else {
            return Ok(());
        };
        let path = std::fs::canonicalize(&path).map_err(|e| format!("{owner}: {}: {e}", path.display()))?;
        if !seen.insert(path.clone()) {
            return Ok(());
        }
        let kind = if image.starts_with(b"#!") { "shebang interpreter" } else { "ELF dynamic loader" };
        let reason = format!("{kind} for {owner}");
        push_unique(&mut self.exec, path.clone());
        self.record_execute(path.clone(), &reason, None);
        let support = std::fs::read(&path).map_err(|e| format!("{}: {e}", path.display()))?;
        self.add_support(&support, &reason, seen)
    }

    pub fn add_read_file(&mut self, path: PathBuf, reason: impl Into<String>) {
        push_unique(&mut self.read_files, path.clone());
        self.record_read(path, reason, None);
    }

    pub fn add_write_root(&mut self, path: PathBuf, reason: impl Into<String>) {
        push_unique(&mut self.write, path.clone());
        self.record_write(path, reason, None);
    }

    pub fn add_bind_port(&mut self, port: u16) {
        push_unique(&mut self.bind_tcp, port);
        self.permissions.bind_tcp = self.bind_tcp.clone();
    }

    fn reserve_network(&mut self, reason: String) {
        self.connect_tcp = true;
        self.permissions.connect_tcp.push(reason.clone());
        if let Ok(path) = std::fs::canonicalize("/etc/resolv.conf") {
            self.add_read_file(path, format!("DNS resolver configuration for {reason}"));
        }
    }

    pub fn resolved_permissions(&self) -> ResolvedPermissions {
        let mut permissions = self.permissions.clone();
        permissions.read.sort_by(path_order);
        permissions.read.dedup();
        permissions.write.sort_by(path_order);
        permissions.write.dedup();
        permissions.execute.sort_by(path_order);
        permissions.execute.dedup();
        permissions.bind_tcp.sort_unstable();
        permissions.bind_tcp.dedup();
        permissions.connect_tcp.sort();
        permissions.connect_tcp.dedup();
        permissions
    }

    /// Gives only the episode process the right to enumerate and remove a
    /// runtime-owned directory, plus removal of the directory itself out of
    /// its shared parent, which carries no other access.
    pub fn add_cleanup(&mut self, dir: PathBuf, what: &str) {
        if let Some(parent) = dir.parent() {
            self.cleanup_parents.push(parent.to_path_buf());
            self.record_write(parent.to_path_buf(), format!("{what}: directory removal alone"), None);
        }
        self.record_read(dir.clone(), format!("{what}: read and enumerate for cleanup"), None);
        self.record_write(dir.clone(), format!("{what}: cleanup removal only, no creation or writing"), None);
        self.cleanup.push(dir);
    }

    /// Gives only the runtime control of a cgroup directory after the
    /// episode enters Landlock.
    pub fn add_runtime_control(&mut self, path: PathBuf) {
        self.runtime_control.push(path.clone());
        self.record_read_write(path, "cgroup process-boundary ownership and cleanup", None);
    }

    /// Gives the runtime or one trusted wrapper access to a cgroup file. A
    /// file that cannot be opened would compile no rule, so it is dropped
    /// here and reported as absent rather than granted.
    pub fn add_runtime_control_file(&mut self, path: PathBuf) {
        if PathFd::new(&path).is_err() {
            return;
        }
        self.runtime_control_files.push(path.clone());
        self.record_write(path, "cgroup process-boundary placement", None);
    }

    fn record_read(&mut self, path: PathBuf, reason: impl Into<String>, sha256: Option<String>) {
        self.permissions.read.push(permission_path(path, reason, sha256));
    }

    fn record_write(&mut self, path: PathBuf, reason: impl Into<String>, sha256: Option<String>) {
        self.permissions.write.push(permission_path(path, reason, sha256));
    }

    fn record_read_write(&mut self, path: PathBuf, reason: impl Into<String>, sha256: Option<String>) {
        let reason = reason.into();
        self.record_read(path.clone(), reason.clone(), sha256.clone());
        self.record_write(path, reason, sha256);
    }

    fn record_execute(&mut self, path: PathBuf, reason: &str, sha256: Option<String>) {
        self.permissions.execute.push(permission_path(path, reason, sha256));
    }
}

fn permission_path(path: PathBuf, reason: impl Into<String>, sha256: Option<String>) -> ResolvedPathPermission {
    ResolvedPathPermission { path: path.to_string_lossy().into_owned(), reason: reason.into(), sha256 }
}

fn path_order(a: &ResolvedPathPermission, b: &ResolvedPathPermission) -> std::cmp::Ordering {
    (&a.path, &a.reason, &a.sha256).cmp(&(&b.path, &b.reason, &b.sha256))
}

fn push_unique<T: PartialEq>(items: &mut Vec<T>, item: T) {
    if !items.contains(&item) {
        items.push(item);
    }
}

/// The sandbox mode decided at startup together with the ABI obtained.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Sandbox {
    mode: SandboxMode,
    abi: u32,
}

impl Sandbox {
    /// Probes the kernel and decides the tier. `required` fails when
    /// Landlock is absent; `off` records ABI 0 and enforces nothing.
    pub fn new(mode: SandboxMode) -> Result<Sandbox, RuntimeError> {
        let abi = match mode {
            SandboxMode::Off => 0,
            SandboxMode::BestEffort => probe_abi().min(MAX_ABI),
            SandboxMode::Required => match probe_abi().min(MAX_ABI) {
                0 => {
                    return Err(RuntimeError::Sandbox(
                        "sandbox.mode is required and the kernel offers no Landlock".into(),
                    ))
                }
                n => n,
            },
        };
        Ok(Sandbox { mode, abi })
    }

    /// What `episode/start` records after process-subtree ownership has
    /// been selected for this run.
    pub fn info(&self, process_boundary: foe_log::ProcessBoundaryInfo, policy: &Policy) -> SandboxInfo {
        SandboxInfo {
            mode: self.mode,
            landlock_abi: self.abi,
            resolved_permissions: policy.resolved_permissions(),
            process_boundary,
        }
    }

    /// The ABI in use; 0 when nothing is enforced.
    pub fn abi(&self) -> u32 {
        self.abi
    }

    /// Restricts the calling thread and everything it starts afterwards.
    /// Call from the main thread before any other thread exists, so that
    /// every thread of the episode inherits the domain.
    pub fn enforce_self(&self, policy: &Policy) -> Result<(), RuntimeError> {
        self.compile(policy)?.map_or(Ok(()), |ruleset| apply(ruleset).map(|_| ()))
    }

    /// Starts `cmd` under this sandbox narrowed to `policy`. The domain the
    /// caller already has is kept; the policy only removes access.
    pub fn spawn_narrowed(&self, policy: &Policy, mut cmd: Command) -> Result<Child, RuntimeError> {
        self.run_narrowed(policy, move || {
            cmd.spawn().map_err(|e| RuntimeError::Sandbox(format!("spawn {:?}: {e}", cmd.get_program())))
        })?
    }

    /// Runs `f` on a thread restricted to `policy`, for code that must work
    /// under a narrowed domain without starting a process.
    pub fn run_narrowed<T: Send>(&self, policy: &Policy, f: impl FnOnce() -> T + Send) -> Result<T, RuntimeError> {
        let ruleset = self.compile(policy)?;
        std::thread::scope(|scope| {
            scope
                .spawn(move || {
                    if let Some(r) = ruleset {
                        apply(r)?;
                    }
                    Ok(f())
                })
                .join()
                .map_err(|_| RuntimeError::Sandbox("the restricted thread panicked".into()))?
        })
    }

    /// Compiles `policy` for the ABI in use. `None` when nothing is enforced.
    fn compile(&self, policy: &Policy) -> Result<Option<RulesetCreated>, RuntimeError> {
        if self.abi == 0 {
            return Ok(None);
        }
        let abi = ABI::from(self.abi as i32);
        let err = |e: landlock::RulesetError| RuntimeError::Sandbox(e.to_string());
        let mut ruleset = Ruleset::default()
            .set_compatibility(CompatLevel::BestEffort)
            .handle_access(AccessFs::from_all(abi))
            .map_err(err)?;
        if self.abi >= 4 {
            // Connect is handled only when it is to be denied: a handled access
            // with no rule is denied everywhere, and connect has no wildcard rule.
            let net = if policy.connect_tcp { BitFlags::from(AccessNet::BindTcp) } else { AccessNet::from_all(abi) };
            ruleset = ruleset.handle_access(net).map_err(err)?;
        }
        if self.abi >= 6 {
            ruleset = ruleset.scope(Scope::from_all(abi)).map_err(err)?;
        }
        let mut created = ruleset.create().map_err(err)?;
        // The crate's read set includes execute; a read root grants reading alone.
        let read = AccessFs::from_read(abi) & !AccessFs::Execute;
        let write = AccessFs::from_write(abi);
        let rules: Vec<(Vec<PathBuf>, BitFlags<AccessFs>)> = vec![
            (policy.read.clone(), read),
            (policy.write.clone(), write),
            (policy.exec.clone(), AccessFs::Execute | AccessFs::ReadFile),
            (policy.cleanup.clone(), read | AccessFs::RemoveFile | AccessFs::RemoveDir),
            (policy.cleanup_parents.clone(), BitFlags::from(AccessFs::RemoveDir)),
            (policy.read_files.clone(), BitFlags::from(AccessFs::ReadFile)),
            (policy.log_dir.iter().cloned().collect(), read | write),
            (policy.runtime_control.clone(), read | write),
            (LIBRARY_DIRS.iter().map(PathBuf::from).collect(), read),
            (SYSTEM_READ_DIRS.iter().map(PathBuf::from).collect(), read),
            (DEVICE_FILES.iter().map(PathBuf::from).collect(), AccessFs::ReadFile | AccessFs::WriteFile),
        ];
        for (paths, access) in rules {
            for path in paths {
                // A granted path that does not exist cannot be opened, so it
                // cannot be reached either; skipping it is exact.
                if let Ok(fd) = PathFd::new(&path) {
                    created = created.add_rule(PathBeneath::new(fd, access)).map_err(err)?;
                }
            }
        }
        for executable in &policy.exec_files {
            let path = PathBuf::from(format!("/proc/self/fd/{}", executable.fd().as_raw_fd()));
            let fd = PathFd::new(&path).map_err(|e| RuntimeError::Sandbox(format!("{}: {e}", path.display())))?;
            created = created.add_rule(PathBeneath::new(fd, AccessFs::Execute | AccessFs::ReadFile)).map_err(err)?;
        }
        for path in &policy.runtime_control_files {
            if let Ok(fd) = PathFd::new(path) {
                created =
                    created.add_rule(PathBeneath::new(fd, AccessFs::WriteFile | AccessFs::Truncate)).map_err(err)?;
            }
        }
        if self.abi >= 4 {
            for port in &policy.bind_tcp {
                created = created.add_rule(NetPort::new(*port, AccessNet::BindTcp)).map_err(err)?;
            }
        }
        if self.abi >= 7 {
            // Denials inside executables are logged to the audit subsystem.
            created = created.log_new_exec(true).map_err(err)?;
        }
        Ok(Some(created))
    }
}

/// Restricts the calling thread with a compiled ruleset.
fn apply(ruleset: RulesetCreated) -> Result<RulesetStatus, RuntimeError> {
    let status = ruleset.restrict_self().map_err(|e| RuntimeError::Sandbox(e.to_string()))?;
    if status.ruleset == RulesetStatus::NotEnforced {
        return Err(RuntimeError::Sandbox("landlock_restrict_self enforced nothing".into()));
    }
    Ok(status.ruleset)
}

/// The Landlock ABI of the running kernel, or 0 when Landlock is absent or
/// disabled. A throwaway thread enforces a minimal ruleset on itself and
/// reports what it obtained; the calling thread stays unrestricted.
pub fn probe_abi() -> u32 {
    std::thread::spawn(|| {
        let status = Ruleset::default()
            .set_compatibility(CompatLevel::BestEffort)
            .handle_access(AccessFs::from_all(ABI::V1))
            .ok()?
            .create()
            .ok()?
            .restrict_self()
            .ok()?;
        let LandlockStatus::Available { effective_abi, kernel_abi } = status.landlock else { return Some(0) };
        Some(kernel_abi.map(|k| k as u32).unwrap_or(effective_abi as u32))
    })
    .join()
    .ok()
    .flatten()
    .unwrap_or(0)
}

// TODO(denial capture): `sandbox/denied` events are not produced. At ABI 7
// the kernel emits one audit record per denied access, but reading them
// requires either CAP_AUDIT_READ on an audit netlink socket or read access
// to the audit daemon's log file, and an unprivileged episode has neither.
// Remove this note when a capture path exists that needs no privilege and
// no daemon, for example a kernel interface that reports denials to the
// restricting process itself.

#[cfg(test)]
#[path = "sandbox_test.rs"]
mod tests;
