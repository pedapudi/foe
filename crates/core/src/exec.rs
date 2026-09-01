//! Running configured executables: argv, constructed env, capture, reap, narrowed sandbox.
//!
//! [`LocalExecutor`] implements [`Executor`] for this machine. Each call
//! starts exactly the command and arguments it is given, with the
//! environment it is given and nothing inherited, in a fresh process group
//! under a sandbox narrowed from the episode's. When the call ends, by exit,
//! timeout, or cancellation, no process of that group survives it.
//!
//! Standard output and standard error are each kept up to [`CAPTURE_LIMIT`]
//! bytes. Beyond that the remainder is written to a file under the spill
//! directory and the captured bytes end with one line naming that file.

use crate::captured_executable::{next_child_fd, process_fd_path};
use crate::sandbox::{Policy, Sandbox};
use crate::{CapError, ExecRequest, ExecResult, Executor};
use command_fds::{CommandFdExt, FdMapping};
use nix::sys::signal::{killpg, Signal};
use nix::unistd::Pid;
use std::io::{Read, Write};
use std::os::fd::AsFd;
use std::os::unix::process::CommandExt;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::time::{Duration, Instant};

/// Bytes of one output stream kept in the result.
pub const CAPTURE_LIMIT: usize = 1 << 20;

/// Time between SIGTERM and SIGKILL when a process group is ended, and the
/// longest wait for an output pipe to close after the group is gone.
pub const TERM_GRACE: Duration = Duration::from_secs(2);

const POLL: Duration = Duration::from_millis(10);

pub struct LocalExecutor {
    sandbox: Arc<Sandbox>,
    policy: Policy,
    spill_dir: PathBuf,
    cancel: Arc<AtomicBool>,
    calls: AtomicU64,
}

impl LocalExecutor {
    /// `policy` is the episode's own policy; every request runs under a
    /// narrowing of it. Setting `cancel` ends every running process group.
    pub fn new(sandbox: Arc<Sandbox>, policy: Policy, spill_dir: PathBuf, cancel: Arc<AtomicBool>) -> Self {
        LocalExecutor { sandbox, policy, spill_dir, cancel, calls: AtomicU64::new(0) }
    }
}

impl Executor for LocalExecutor {
    fn run(&self, req: ExecRequest) -> Result<ExecResult, CapError> {
        let start = Instant::now();
        let call = self.calls.fetch_add(1, Ordering::SeqCst);
        let executable_fd =
            req.captured_executable.as_ref().map(|_| next_child_fd(req.pass_fds.iter().map(|(fd, _)| *fd)));
        let invoked = executable_fd.map(process_fd_path).unwrap_or_else(|| req.command.clone());
        let mut cmd = Command::new(&invoked);
        if let Some(executable) = &req.captured_executable {
            cmd.arg0(executable.invocation_name());
        }
        cmd.args(&req.args)
            .current_dir(&req.cwd)
            .env_clear()
            .envs(&req.env)
            .process_group(0)
            .stdin(if req.stdin.is_some() { Stdio::piped() } else { Stdio::null() })
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        // Each mapped descriptor is duplicated here; the caller's copy in
        // the request closes when the request drops, at the end of the run.
        let mut mappings = req
            .pass_fds
            .iter()
            .map(|(child_fd, fd)| {
                fd.as_fd()
                    .try_clone_to_owned()
                    .map(|parent_fd| FdMapping { parent_fd, child_fd: *child_fd })
                    .map_err(|e| CapError::ProcessStart(format!("fd {child_fd}: cannot duplicate descriptor: {e}")))
            })
            .collect::<Result<Vec<_>, _>>()?;
        if let (Some(executable), Some(child_fd)) = (&req.captured_executable, executable_fd) {
            let parent_fd = executable.fd().as_fd().try_clone_to_owned().map_err(|e| {
                CapError::ProcessStart(format!("captured executable: cannot duplicate descriptor: {e}"))
            })?;
            mappings.push(FdMapping { parent_fd, child_fd });
        }
        cmd.fd_mappings(mappings).map_err(|e| CapError::ProcessStart(format!("fd mapping: {e:?}")))?;
        let narrowed = match req.policy.clone() {
            Some(policy) => policy,
            None => match &req.captured_executable {
                Some(executable) => self.policy.for_immutable_executable(executable.clone(), req.network),
                None => self.policy.for_executable(&req.command, req.network),
            }
            .map_err(CapError::ProcessStart)?,
        };
        let mut child =
            self.sandbox.spawn_narrowed(&narrowed, cmd).map_err(|e| CapError::ProcessStart(e.to_string()))?;
        let group = Pid::from_raw(child.id() as i32);
        if let (Some(bytes), Some(mut stdin)) = (req.stdin, child.stdin.take()) {
            std::thread::spawn(move || {
                let _ = stdin.write_all(&bytes);
            });
        }
        let name = req.command.file_name().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default();
        let stdout = Capture::start(child.stdout.take(), self.spill_dir.join(format!("exec-{call}-{name}.stdout")));
        let stderr = Capture::start(child.stderr.take(), self.spill_dir.join(format!("exec-{call}-{name}.stderr")));
        let deadline = start + req.timeout;
        let mut timed_out = false;
        let status = loop {
            if let Some(status) = child.try_wait()? {
                break status;
            }
            if Instant::now() >= deadline || self.cancel.load(Ordering::SeqCst) {
                timed_out = true;
                end_group(group, &mut child)?;
                break child.wait()?;
            }
            std::thread::sleep(POLL);
        };
        // Nothing the executable started outlives the call.
        let _ = killpg(group, Signal::SIGKILL);
        Ok(ExecResult {
            exit_code: if timed_out { None } else { status.code() },
            stdout: stdout.finish(),
            stderr: stderr.finish(),
            timed_out,
            duration: start.elapsed(),
        })
    }
}

/// SIGTERM to the group, [`TERM_GRACE`] for the leader to exit, then SIGKILL.
pub(crate) fn end_group(group: Pid, child: &mut Child) -> std::io::Result<()> {
    let _ = killpg(group, Signal::SIGTERM);
    let until = Instant::now() + TERM_GRACE;
    while Instant::now() < until && child.try_wait()?.is_none() {
        std::thread::sleep(POLL);
    }
    let _ = killpg(group, Signal::SIGKILL);
    Ok(())
}

/// One output stream being read on its own thread.
struct Capture {
    kept: Arc<Mutex<Vec<u8>>>,
    spill: Arc<Mutex<Option<PathBuf>>>,
    closed: mpsc::Receiver<()>,
}

impl Capture {
    fn start(pipe: Option<impl Read + Send + 'static>, spill_path: PathBuf) -> Capture {
        let kept = Arc::new(Mutex::new(Vec::new()));
        let spill = Arc::new(Mutex::new(None));
        let (tx, closed) = mpsc::channel();
        let Some(mut pipe) = pipe else {
            let _ = tx.send(());
            return Capture { kept, spill, closed };
        };
        let (kept_w, spill_w) = (kept.clone(), spill.clone());
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
                let mut kept = kept_w.lock().unwrap();
                let room = CAPTURE_LIMIT.saturating_sub(kept.len()).min(n);
                let (head, tail) = chunk[..n].split_at(room);
                kept.extend_from_slice(head);
                if tail.is_empty() {
                    continue;
                }
                if file.is_none() {
                    if let Some(parent) = spill_path.parent() {
                        let _ = std::fs::create_dir_all(parent);
                    }
                    file = std::fs::File::create(&spill_path).ok();
                    *spill_w.lock().unwrap() = Some(spill_path.clone());
                }
                if let Some(f) = file.as_mut() {
                    let _ = f.write_all(tail);
                }
            }
            let _ = tx.send(());
        });
        Capture { kept, spill, closed }
    }

    /// Waits up to [`TERM_GRACE`] for the pipe to close. A process outside
    /// the group that inherited the pipe cannot hold the call open longer.
    fn finish(self) -> Vec<u8> {
        let _ = self.closed.recv_timeout(TERM_GRACE);
        let mut out = std::mem::take(&mut *self.kept.lock().unwrap());
        if let Some(path) = self.spill.lock().unwrap().as_ref() {
            let note = format!("\n[output beyond {CAPTURE_LIMIT} bytes is in {}]\n", path.display());
            out.extend_from_slice(note.as_bytes());
        }
        out
    }
}

#[cfg(test)]
#[path = "exec_test.rs"]
pub(crate) mod tests;
