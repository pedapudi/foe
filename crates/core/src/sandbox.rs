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

use crate::{Config, RuntimeError};
use foe_log::{SandboxInfo, SandboxMode};
use landlock::{
    Access, AccessFs, AccessNet, BitFlags, CompatLevel, Compatible, LandlockStatus, NetPort, PathBeneath, PathFd,
    Ruleset, RulesetAttr, RulesetCreated, RulesetCreatedAttr, RulesetStatus, Scope, ABI,
};
use std::path::{Path, PathBuf};
use std::process::{Child, Command};

/// The highest Landlock ABI this module makes use of. A newer kernel is
/// used at this level and recorded as this level.
pub const MAX_ABI: u32 = 7;

/// Directories every process needs in order to start: the dynamic loader,
/// shared libraries, and interpreters named by shebang lines. Granted read
/// and execute. Paths absent on the machine are skipped.
pub const LOADER_DIRS: &[&str] = &[
    "/lib",
    "/lib64",
    "/usr/lib",
    "/usr/lib64",
    "/usr/libexec",
    "/usr/local/lib",
    "/bin",
    "/usr/bin",
    "/usr/local/bin",
];

/// Read-only system paths that runtimes consult while starting: loader
/// configuration, locale data, and process metadata.
pub const SYSTEM_READ_DIRS: &[&str] = &["/etc", "/usr/share", "/proc", "/sys"];

/// Device files any process may read and write.
pub const DEVICE_FILES: &[&str] = &["/dev/null", "/dev/zero", "/dev/random", "/dev/urandom", "/dev/tty"];

/// What one process may reach. Compiled into a ruleset by [`Sandbox`].
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Policy {
    /// Directories readable in full.
    pub read: Vec<PathBuf>,
    /// Directories where files may be written, created, and removed.
    pub write: Vec<PathBuf>,
    /// Files that may be executed.
    pub exec: Vec<PathBuf>,
    /// Single files readable in full: the model credential file, which the
    /// binary adds once the transport has resolved it, and which child
    /// episodes read after inheriting this domain. Executables never
    /// receive these.
    pub read_files: Vec<PathBuf>,
    /// The episode's own log directory, readable and writable. `None` for an
    /// executable, which has no log of its own.
    pub log_dir: Option<PathBuf>,
    /// TCP ports the process may bind. Enforced from ABI 4.
    pub bind_tcp: Vec<u16>,
    /// Whether the process may open outbound TCP connections. Enforced from
    /// ABI 4.
    pub connect_tcp: bool,
}

impl Policy {
    /// The policy of an episode process: its grants, every configured
    /// executable, the running binary when it may start children, its log
    /// directory, and outbound TCP only when the episode itself holds the
    /// model transport. The credential file the transport reads is not
    /// known here; the binary appends it to `read_files` after resolving
    /// the `model` block.
    pub fn for_episode(config: &Config, log_dir: &Path) -> Policy {
        let mut exec: Vec<PathBuf> = config.tool_defs.values().map(|d| d.exec.clone()).collect();
        if !config.grants.spawn.is_empty() {
            exec.extend(std::env::current_exe().ok());
        }
        Policy {
            read: config.grants.read.clone(),
            write: config.grants.write.clone(),
            exec,
            read_files: Vec::new(),
            log_dir: Some(log_dir.to_path_buf()),
            bind_tcp: Vec::new(),
            connect_tcp: config.model.is_some(),
        }
    }

    /// The policy of one executable started by this episode: the same read
    /// and write roots, execute access on that file alone, no log directory,
    /// and TCP only when the tool definition asks for it.
    pub fn for_executable(&self, exec: &Path, network: bool) -> Policy {
        Policy {
            read: self.read.clone(),
            write: self.write.clone(),
            exec: vec![exec.to_path_buf()],
            read_files: Vec::new(),
            log_dir: None,
            bind_tcp: Vec::new(),
            connect_tcp: network,
        }
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

    /// What `episode/start` records.
    pub fn info(&self) -> SandboxInfo {
        SandboxInfo { mode: self.mode, landlock_abi: self.abi }
    }

    /// The ABI in use; 0 when nothing is enforced.
    pub fn abi(&self) -> u32 {
        self.abi
    }

    /// Restricts the calling thread and everything it starts afterwards.
    /// Call from the main thread before any other thread exists, so that
    /// every thread of the episode inherits the domain.
    pub fn enforce_self(&self, policy: &Policy) -> Result<(), RuntimeError> {
        match self.compile(policy)? {
            Some(ruleset) => apply(ruleset).map(|_| ()),
            None => Ok(()),
        }
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
            (policy.read_files.clone(), BitFlags::from(AccessFs::ReadFile)),
            (policy.log_dir.iter().cloned().collect(), read | write),
            (paths(LOADER_DIRS), read | AccessFs::Execute),
            (paths(SYSTEM_READ_DIRS), read),
            (paths(DEVICE_FILES), AccessFs::ReadFile | AccessFs::WriteFile),
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

fn paths(list: &[&str]) -> Vec<PathBuf> {
    list.iter().map(PathBuf::from).collect()
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
        match status.landlock {
            LandlockStatus::Available { effective_abi, kernel_abi } => {
                Some(kernel_abi.map(|k| k as u32).unwrap_or(effective_abi as u32))
            }
            _ => Some(0),
        }
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
