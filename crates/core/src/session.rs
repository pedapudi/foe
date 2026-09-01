//! Process sessions: supervised processes that outlive the call that started them.
//!
//! [`LocalSessions`] implements [`Sessions`] for this machine. A session is
//! one process group started under the same narrowing an executable
//! receives, with standard input piped and both output streams written to
//! evidence files. A stop sends SIGTERM to the group, waits
//! [`TERM_GRACE`], and sends SIGKILL, so nothing the session started
//! survives it. Settlement stops episode-lifetime sessions. It releases a
//! task-lifetime session to the enclosing task environment when the contract
//! holds that permission. See docs/tools.md "session".

use crate::exec::{end_group, CAPTURE_LIMIT};
use crate::process_boundary::{command_in, ProcessBoundary, PROCESS_BOUNDARY_LAUNCHER};
use crate::sandbox::{Policy, Sandbox};
use crate::{CapError, SessionLifetime, SessionOutput, SessionRequest, SessionSettlement, SessionStatus, Sessions};
use nix::errno::Errno;
use nix::sys::signal::{killpg, Signal};
use nix::unistd::Pid;
use std::collections::BTreeMap;
use std::io::{Read, Seek, SeekFrom, Write};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::str::FromStr;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Instant;

/// The built-in tool that drives sessions. Settlement writes this as the
/// `name` of the synthetic result that records an implicit stop.
pub const SESSION_TOOL: &str = "session";

/// The session half of every session subject, shared by the tool and by
/// the settlement that stops survivors, so the two never drift:
/// `session 2: postgres · alive`, `session 2: exit 0 after 84s`, or
/// `session 2: killed after 84s` when a signal ended the process.
pub fn subject(status: &SessionStatus) -> String {
    match (status.alive, status.exit_code) {
        (true, _) => format!("session {}: {} \u{b7} alive", status.id, status.name),
        (false, Some(code)) => format!("session {}: exit {code} after {}s", status.id, status.seconds),
        (false, None) => format!("session {}: killed after {}s", status.id, status.seconds),
    }
}

/// One process output file. Polling advances through the file and bounds the
/// returned bytes while leaving the complete stream available as evidence.
struct Output {
    path: PathBuf,
    offset: Mutex<u64>,
}

impl Output {
    fn take(&self) -> Vec<u8> {
        let mut offset = self.offset.lock().unwrap();
        let Ok(mut file) = std::fs::File::open(&self.path) else {
            return Vec::new();
        };
        let Ok(end) = file.metadata().map(|m| m.len()) else {
            return Vec::new();
        };
        if end <= *offset || file.seek(SeekFrom::Start(*offset)).is_err() {
            return Vec::new();
        }
        let available = end - *offset;
        let kept = available.min(CAPTURE_LIMIT as u64);
        let start = end - kept;
        if file.seek(SeekFrom::Start(start)).is_err() {
            return Vec::new();
        }
        let mut out = Vec::with_capacity(kept as usize);
        if file.take(kept).read_to_end(&mut out).is_err() {
            return Vec::new();
        }
        *offset = end;
        if available > kept {
            let note = format!("\n[output beyond {CAPTURE_LIMIT} bytes is in {}]\n", self.path.display());
            out.extend_from_slice(note.as_bytes());
        }
        out
    }
}

struct Session {
    name: String,
    pid: u32,
    group: Pid,
    lifetime: SessionLifetime,
    child: Mutex<Child>,
    stdin: Mutex<Option<ChildStdin>>,
    stdout: Output,
    stderr: Output,
    started: Instant,
    /// The group leader's exit code and the observed group-end time. The
    /// group may remain alive after the leader is reaped.
    ended: Mutex<Option<(Option<i32>, Option<u64>)>>,
    /// Set when `take_exited` has reported the end, so each session's exit
    /// is reported once per lifetime.
    reported: AtomicBool,
    /// Set when settlement transfers task ownership, so the transfer is
    /// recorded once even if cleanup is retried.
    released: AtomicBool,
}

impl Session {
    /// Reaps the process when it has exited and returns the session's state.
    fn status(&self, id: u64) -> SessionStatus {
        let elapsed = self.started.elapsed().as_secs();
        let mut ended = self.ended.lock().unwrap();
        if ended.is_none() {
            if let Ok(Some(status)) = self.child.lock().unwrap().try_wait() {
                *ended = Some((status.code(), None));
            }
        }
        let alive = ended.as_ref().is_none_or(|(_, seconds)| seconds.is_none())
            && !matches!(killpg(self.group, None), Err(Errno::ESRCH));
        if !alive {
            ended.get_or_insert((None, None)).1.get_or_insert(elapsed);
        }
        let (exit_code, seconds) = match *ended {
            Some((code, Some(seconds))) => (code, seconds),
            _ => (None, elapsed),
        };
        SessionStatus { id, name: self.name.clone(), alive, exit_code, seconds }
    }

    /// SIGTERM to the group, [`TERM_GRACE`] for the leader to exit, SIGKILL,
    /// reap. Ending the whole group is what keeps a process the session
    /// started from surviving it.
    fn stop(&self, id: u64) -> Result<SessionStatus, CapError> {
        if self.status(id).alive {
            let mut ended = self.ended.lock().unwrap();
            let mut child = self.child.lock().unwrap();
            end_group(self.group, &mut child)?;
            let status = child.wait()?;
            *ended = Some((status.code(), Some(self.started.elapsed().as_secs())));
        }
        Ok(self.status(id))
    }
}

pub struct LocalSessions {
    sandbox: Arc<Sandbox>,
    policy: Policy,
    spill_dir: PathBuf,
    /// Sessions that may be alive at once; a further start is refused.
    limit: usize,
    task_session: bool,
    boundary: Option<Arc<ProcessBoundary>>,
    inner: Mutex<BTreeMap<u64, Arc<Session>>>,
    next: AtomicU64,
}

impl LocalSessions {
    /// `policy` is the episode's own policy; every session runs under a
    /// narrowing of it, exactly as an executable does.
    pub fn new(sandbox: Arc<Sandbox>, policy: Policy, spill_dir: PathBuf, limit: usize, task_session: bool) -> Self {
        LocalSessions {
            sandbox,
            policy,
            spill_dir,
            limit,
            task_session,
            boundary: None,
            inner: Mutex::new(BTreeMap::new()),
            next: AtomicU64::new(0),
        }
    }

    /// Places authorized task-lifetime sessions in the invocation-owned
    /// task cgroup before their user code starts.
    pub fn with_boundary(mut self, boundary: Option<Arc<ProcessBoundary>>) -> Self {
        self.boundary = boundary;
        self
    }

    fn session(&self, id: u64) -> Result<Arc<Session>, CapError> {
        self.inner
            .lock()
            .unwrap()
            .get(&id)
            .cloned()
            .ok_or_else(|| CapError::Invalid(format!("session {id}: no session has this id")))
    }

    fn snapshot(&self) -> Vec<(u64, Arc<Session>)> {
        self.inner.lock().unwrap().iter().map(|(id, s)| (*id, s.clone())).collect()
    }
}

impl Sessions for LocalSessions {
    fn start(&self, req: SessionRequest) -> Result<SessionStatus, CapError> {
        let mut inner = self.inner.lock().unwrap();
        if req.lifetime == SessionLifetime::Task && !self.task_session {
            return Err(CapError::CapabilityDenied(
                "grants.task_session does not authorize a task-lifetime session".into(),
            ));
        }
        let alive = inner.iter().filter(|(id, s)| s.status(**id).alive).count();
        if alive >= self.limit {
            return Err(CapError::Invalid(format!(
                "session limit: {alive} of {} sessions are alive; stop one before starting another",
                self.limit
            )));
        }
        let id = self.next.load(Ordering::SeqCst) + 1;
        let base = format!("session-{id}-{}", std::process::id());
        let stdout_path = self.spill_dir.join(format!("{base}.stdout"));
        let stderr_path = self.spill_dir.join(format!("{base}.stderr"));
        std::fs::create_dir_all(&self.spill_dir)?;
        let output = |path: PathBuf| -> Result<(Output, Stdio), CapError> {
            let file = std::fs::OpenOptions::new()
                .create(true)
                .truncate(true)
                .write(true)
                .open(&path)
                .map_err(|e| CapError::Invalid(format!("session output {}: {e}", path.display())))?;
            Ok((Output { path, offset: Mutex::new(0) }, Stdio::from(file)))
        };
        let (stdout, stdout_stdio) = output(stdout_path)?;
        let (stderr, stderr_stdio) = output(stderr_path)?;
        let task_procs = (req.lifetime == SessionLifetime::Task)
            .then(|| self.boundary.as_ref().map(|boundary| boundary.task_procs()))
            .flatten();
        let argv: Vec<_> =
            std::iter::once(req.command.as_os_str().to_owned()).chain(req.args.iter().map(Into::into)).collect();
        let mut cmd = match &task_procs {
            Some(procs) => command_in(procs, &argv),
            None => {
                let mut command = Command::new(&req.command);
                command.args(&req.args);
                command
            }
        };
        cmd.current_dir(&req.cwd)
            .env_clear()
            .envs(&req.env)
            .process_group(0)
            .stdin(Stdio::piped())
            .stdout(stdout_stdio)
            .stderr(stderr_stdio);
        let mut narrowed = self.policy.for_executable(&req.command, false).map_err(CapError::ProcessStart)?;
        if let Some(procs) = task_procs {
            narrowed
                .add_executable(Path::new(PROCESS_BOUNDARY_LAUNCHER), "cgroup process-boundary launcher".into())
                .map_err(CapError::ProcessStart)?;
            narrowed.add_runtime_control_file(procs);
        }
        let mut child =
            self.sandbox.spawn_narrowed(&narrowed, cmd).map_err(|e| CapError::ProcessStart(e.to_string()))?;
        self.next.store(id, Ordering::SeqCst);
        let pid = child.id();
        let group = Pid::from_raw(child.id() as i32);
        let stdin = child.stdin.take();
        let session = Arc::new(Session {
            name: req.name,
            pid,
            group,
            lifetime: req.lifetime,
            child: Mutex::new(child),
            stdin: Mutex::new(stdin),
            stdout,
            stderr,
            started: Instant::now(),
            ended: Mutex::new(None),
            reported: AtomicBool::new(false),
            released: AtomicBool::new(false),
        });
        let status = session.status(id);
        inner.insert(id, session);
        Ok(status)
    }

    fn take_output(&self, id: u64) -> Result<(SessionStatus, SessionOutput), CapError> {
        let session = self.session(id)?;
        let status = session.status(id);
        let output = SessionOutput { stdout: session.stdout.take(), stderr: session.stderr.take() };
        Ok((status, output))
    }

    fn write_stdin(&self, id: u64, bytes: &[u8]) -> Result<SessionStatus, CapError> {
        let session = self.session(id)?;
        {
            let mut stdin = session.stdin.lock().unwrap();
            let pipe =
                stdin.as_mut().ok_or_else(|| CapError::Invalid(format!("session {id}: standard input is closed")))?;
            pipe.write_all(bytes)?;
            pipe.flush()?;
        }
        Ok(session.status(id))
    }

    fn signal(&self, id: u64, signal: &str) -> Result<SessionStatus, CapError> {
        let session = self.session(id)?;
        let sig = Signal::from_str(signal)
            .map_err(|_| CapError::Invalid(format!("session {id}: {signal} is not a signal name")))?;
        if !session.status(id).alive {
            return Err(CapError::Invalid(format!("session {id}: the process has ended")));
        }
        nix::sys::signal::killpg(session.group, sig).map_err(|e| CapError::Invalid(format!("session {id}: {e}")))?;
        Ok(session.status(id))
    }

    fn stop(&self, id: u64) -> Result<SessionStatus, CapError> {
        self.session(id)?.stop(id)
    }

    fn settle(&self) -> Vec<SessionSettlement> {
        self.snapshot()
            .into_iter()
            .filter_map(|(id, session)| {
                let observed = session.status(id);
                if !observed.alive {
                    return None;
                }
                let released_to_task = session.lifetime == SessionLifetime::Task;
                if released_to_task && session.released.swap(true, Ordering::SeqCst) {
                    return None;
                }
                let status = match released_to_task {
                    true => observed,
                    false => session.stop(id).ok()?,
                };
                Some(SessionSettlement {
                    status,
                    pid: session.pid,
                    process_group: session.group.as_raw(),
                    released_to_task,
                })
            })
            .collect()
    }

    fn take_exited(&self) -> Vec<SessionStatus> {
        self.snapshot()
            .into_iter()
            .map(|(id, s)| (s.status(id), s))
            .filter(|(status, s)| !status.alive && !s.reported.swap(true, Ordering::SeqCst))
            .map(|(status, _)| status)
            .collect()
    }
}

#[cfg(test)]
#[path = "session_test.rs"]
mod tests;
