//! Process sessions: supervised processes that outlive the call that started them.
//!
//! [`LocalSessions`] implements [`Sessions`] for this machine. A session is
//! one process group started under the same narrowing an executable
//! receives, with standard input piped and both output streams read
//! continuously. A stop sends SIGTERM to the group, waits
//! [`TERM_GRACE`], and sends SIGKILL, so nothing the session started
//! survives it. Episode settlement stops every surviving session through
//! [`Sessions::stop_all`] and records each termination as the synthetic
//! result of the implicit stop; see docs/tools.md "session".

use crate::exec::{end_group, CAPTURE_LIMIT, TERM_GRACE};
use crate::sandbox::{Policy, Sandbox};
use crate::{CapError, SessionOutput, SessionRequest, SessionStatus, Sessions};
use nix::sys::signal::Signal;
use nix::unistd::Pid;
use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::os::unix::process::CommandExt;
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::str::FromStr;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

/// The built-in tool that drives sessions. Settlement writes this as the
/// `name` of the synthetic result that records an implicit stop.
pub const SESSION_TOOL: &str = "session";

const POLL: Duration = Duration::from_millis(10);

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

/// One output stream read on its own thread. Bytes up to [`CAPTURE_LIMIT`]
/// beyond what the last take drained stay in memory; further bytes go to a
/// file under the spill directory, and the take that first sees them ends
/// with a line naming that file.
struct Stream {
    state: Arc<Mutex<StreamState>>,
    path: PathBuf,
}

#[derive(Default)]
struct StreamState {
    kept: Vec<u8>,
    /// Bytes written to the spill file, and how many of them a take has
    /// reported, so the notice line appears once per spilled span.
    spilled: u64,
    reported: u64,
    /// Set when the pipe has closed, which is when no further byte can come.
    closed: bool,
}

impl Stream {
    fn start(pipe: Option<impl Read + Send + 'static>, path: PathBuf) -> Stream {
        let state = Arc::new(Mutex::new(StreamState::default()));
        let Some(mut pipe) = pipe else {
            state.lock().unwrap().closed = true;
            return Stream { state, path };
        };
        let writer = state.clone();
        let spill_path = path.clone();
        std::thread::spawn(move || {
            let mut chunk = [0u8; 8192];
            let mut file: Option<std::fs::File> = None;
            loop {
                let n = match pipe.read(&mut chunk) {
                    Ok(0) => break,
                    Ok(n) => n,
                    Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                    Err(_) => break,
                };
                let mut state = writer.lock().unwrap();
                let room = CAPTURE_LIMIT.saturating_sub(state.kept.len()).min(n);
                let (head, tail) = chunk[..n].split_at(room);
                state.kept.extend_from_slice(head);
                if tail.is_empty() {
                    continue;
                }
                if file.is_none() {
                    if let Some(parent) = spill_path.parent() {
                        let _ = std::fs::create_dir_all(parent);
                    }
                    file = std::fs::OpenOptions::new().create(true).append(true).open(&spill_path).ok();
                }
                if let Some(f) = file.as_mut() {
                    if f.write_all(tail).is_ok() {
                        state.spilled += tail.len() as u64;
                    }
                }
            }
            writer.lock().unwrap().closed = true;
        });
        Stream { state, path }
    }

    /// Every byte captured since the last take.
    fn take(&self) -> Vec<u8> {
        let mut state = self.state.lock().unwrap();
        let mut out = std::mem::take(&mut state.kept);
        if state.spilled > state.reported {
            state.reported = state.spilled;
            let note = format!("\n[output beyond {CAPTURE_LIMIT} buffered bytes is in {}]\n", self.path.display());
            out.extend_from_slice(note.as_bytes());
        }
        out
    }

    /// Waits up to [`TERM_GRACE`] for the pipe to close, so a take after
    /// the process ended returns its final bytes. A process outside the
    /// group that inherited the pipe cannot hold the take open longer.
    fn await_closed(&self) {
        let until = Instant::now() + TERM_GRACE;
        while Instant::now() < until && !self.state.lock().unwrap().closed {
            std::thread::sleep(POLL);
        }
    }
}

struct Session {
    name: String,
    group: Pid,
    child: Mutex<Child>,
    stdin: Mutex<Option<ChildStdin>>,
    stdout: Stream,
    stderr: Stream,
    started: Instant,
    /// The exit code and the elapsed whole seconds, recorded once when the
    /// process is reaped. The code is `None` for a process a signal ended.
    ended: Mutex<Option<(Option<i32>, u64)>>,
}

impl Session {
    /// Reaps the process when it has exited and returns the session's state.
    fn status(&self, id: u64) -> SessionStatus {
        let mut ended = self.ended.lock().unwrap();
        if ended.is_none() {
            if let Ok(Some(status)) = self.child.lock().unwrap().try_wait() {
                *ended = Some((status.code(), self.started.elapsed().as_secs()));
            }
        }
        let (alive, exit_code, seconds) = match *ended {
            Some((code, seconds)) => (false, code, seconds),
            None => (true, None, self.started.elapsed().as_secs()),
        };
        SessionStatus { id, name: self.name.clone(), alive, exit_code, seconds }
    }

    /// SIGTERM to the group, [`TERM_GRACE`] for the leader to exit, SIGKILL,
    /// reap. Ending the whole group is what keeps a process the session
    /// started from surviving it.
    fn stop(&self, id: u64) -> Result<SessionStatus, CapError> {
        {
            let mut ended = self.ended.lock().unwrap();
            if ended.is_none() {
                let mut child = self.child.lock().unwrap();
                end_group(self.group, &mut child)?;
                let status = child.wait()?;
                *ended = Some((status.code(), self.started.elapsed().as_secs()));
            }
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
    inner: Mutex<BTreeMap<u64, Arc<Session>>>,
    next: AtomicU64,
}

impl LocalSessions {
    /// `policy` is the episode's own policy; every session runs under a
    /// narrowing of it, exactly as an executable does.
    pub fn new(sandbox: Arc<Sandbox>, policy: Policy, spill_dir: PathBuf, limit: usize) -> Self {
        LocalSessions { sandbox, policy, spill_dir, limit, inner: Mutex::new(BTreeMap::new()), next: AtomicU64::new(0) }
    }

    fn session(&self, id: u64) -> Result<Arc<Session>, CapError> {
        self.inner
            .lock()
            .unwrap()
            .get(&id)
            .cloned()
            .ok_or_else(|| CapError::Invalid(format!("session {id}: no session has this id")))
    }
}

impl Sessions for LocalSessions {
    fn start(&self, req: SessionRequest) -> Result<SessionStatus, CapError> {
        let mut inner = self.inner.lock().unwrap();
        let alive = inner.iter().filter(|(id, s)| s.status(**id).alive).count();
        if alive >= self.limit {
            return Err(CapError::Invalid(format!(
                "session limit: {alive} of {} sessions are alive; stop one before starting another",
                self.limit
            )));
        }
        let mut cmd = Command::new(&req.program);
        cmd.args(&req.args)
            .current_dir(&req.cwd)
            .env_clear()
            .envs(&req.env)
            .process_group(0)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let narrowed = self.policy.for_executable(&req.program, false);
        let mut child = self.sandbox.spawn_narrowed(&narrowed, cmd).map_err(|e| CapError::Invalid(e.to_string()))?;
        let id = self.next.fetch_add(1, Ordering::SeqCst) + 1;
        let group = Pid::from_raw(child.id() as i32);
        let stdin = child.stdin.take();
        let stdout = Stream::start(child.stdout.take(), self.spill_dir.join(format!("session-{id}.stdout")));
        let stderr = Stream::start(child.stderr.take(), self.spill_dir.join(format!("session-{id}.stderr")));
        let session = Arc::new(Session {
            name: req.name,
            group,
            child: Mutex::new(child),
            stdin: Mutex::new(stdin),
            stdout,
            stderr,
            started: Instant::now(),
            ended: Mutex::new(None),
        });
        let status = session.status(id);
        inner.insert(id, session);
        Ok(status)
    }

    fn take_output(&self, id: u64) -> Result<(SessionStatus, SessionOutput), CapError> {
        let session = self.session(id)?;
        let status = session.status(id);
        if !status.alive {
            session.stdout.await_closed();
            session.stderr.await_closed();
        }
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

    fn stop_all(&self) -> Vec<SessionStatus> {
        let sessions: Vec<(u64, Arc<Session>)> =
            self.inner.lock().unwrap().iter().map(|(id, s)| (*id, s.clone())).collect();
        sessions.into_iter().filter(|(id, s)| s.status(*id).alive).filter_map(|(id, s)| s.stop(id).ok()).collect()
    }
}

#[cfg(test)]
#[path = "session_test.rs"]
mod tests;
