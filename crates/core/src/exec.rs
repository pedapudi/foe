//! Running configured executables: argv, constructed env, capture, reap, narrowed sandbox.
//!
//! [`LocalExecutor`] implements [`Executor`] for this machine. Each call
//! starts exactly the program and arguments it is given, with the
//! environment it is given and nothing inherited, in a fresh process group
//! under a sandbox narrowed from the episode's. When the call ends, by exit,
//! timeout, or cancellation, no process of that group survives it.
//!
//! Standard output and standard error are each kept up to [`CAPTURE_LIMIT`]
//! bytes. Beyond that the remainder is written to a file under the spill
//! directory and the captured bytes end with one line naming that file.

use crate::sandbox::{Policy, Sandbox};
use crate::{CapError, ExecRequest, ExecResult, Executor};
use nix::sys::signal::{killpg, Signal};
use nix::unistd::Pid;
use std::io::{Read, Write};
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
    pub fn new(
        sandbox: Arc<Sandbox>,
        policy: Policy,
        spill_dir: PathBuf,
        cancel: Arc<AtomicBool>,
    ) -> Self {
        LocalExecutor {
            sandbox,
            policy,
            spill_dir,
            cancel,
            calls: AtomicU64::new(0),
        }
    }
}

impl Executor for LocalExecutor {
    fn run(&self, req: ExecRequest) -> Result<ExecResult, CapError> {
        let start = Instant::now();
        let call = self.calls.fetch_add(1, Ordering::SeqCst);
        let mut cmd = Command::new(&req.program);
        cmd.args(&req.args)
            .current_dir(&req.cwd)
            .env_clear()
            .envs(&req.env)
            .process_group(0)
            .stdin(if req.stdin.is_some() {
                Stdio::piped()
            } else {
                Stdio::null()
            })
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let narrowed = self.policy.for_executable(&req.program, req.network);
        let mut child = self
            .sandbox
            .spawn_narrowed(&narrowed, cmd)
            .map_err(|e| CapError::Invalid(e.to_string()))?;
        let group = Pid::from_raw(child.id() as i32);
        if let (Some(bytes), Some(mut stdin)) = (req.stdin, child.stdin.take()) {
            std::thread::spawn(move || {
                let _ = stdin.write_all(&bytes);
            });
        }
        let name = req
            .program
            .file_name()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or_default();
        let stdout = Capture::start(
            child.stdout.take(),
            self.spill_dir.join(format!("exec-{call}-{name}.stdout")),
        );
        let stderr = Capture::start(
            child.stderr.take(),
            self.spill_dir.join(format!("exec-{call}-{name}.stderr")),
        );
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
fn end_group(group: Pid, child: &mut Child) -> std::io::Result<()> {
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
            return Capture {
                kept,
                spill,
                closed,
            };
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
        Capture {
            kept,
            spill,
            closed,
        }
    }

    /// Waits up to [`TERM_GRACE`] for the pipe to close. A process outside
    /// the group that inherited the pipe cannot hold the call open longer.
    fn finish(self) -> Vec<u8> {
        let _ = self.closed.recv_timeout(TERM_GRACE);
        let mut out = std::mem::take(&mut *self.kept.lock().unwrap());
        if let Some(path) = self.spill.lock().unwrap().as_ref() {
            let note = format!(
                "\n[output beyond {CAPTURE_LIMIT} bytes is in {}]\n",
                path.display()
            );
            out.extend_from_slice(note.as_bytes());
        }
        out
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use foe_log::SandboxMode;
    use std::collections::BTreeMap;
    use std::path::Path;

    /// A fresh directory under the build tree for one test.
    pub(crate) fn scratch(module: &str, name: &str) -> PathBuf {
        let dir = Path::new(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../target/test-scratch"
        ))
        .join(format!("{module}-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir.canonicalize().unwrap()
    }

    fn executor(
        name: &str,
        read: Vec<PathBuf>,
        exec: Vec<PathBuf>,
    ) -> (LocalExecutor, PathBuf, Arc<AtomicBool>) {
        let dir = scratch("exec", name);
        let cancel = Arc::new(AtomicBool::new(false));
        let sandbox = Arc::new(Sandbox::new(SandboxMode::BestEffort).unwrap());
        let policy = Policy {
            read,
            exec,
            ..Policy::default()
        };
        (
            LocalExecutor::new(sandbox, policy, dir.join("spill"), cancel.clone()),
            dir,
            cancel,
        )
    }

    fn request(program: &str, args: &[&str], cwd: &Path) -> ExecRequest {
        ExecRequest {
            program: program.into(),
            args: args.iter().map(|s| s.to_string()).collect(),
            cwd: cwd.to_path_buf(),
            env: BTreeMap::new(),
            timeout: Duration::from_secs(10),
            network: false,
            stdin: None,
        }
    }

    #[test]
    fn environment_is_exactly_the_request() {
        let (ex, dir, _) = executor("env", vec![], vec!["/usr/bin/env".into()]);
        let mut req = request("/usr/bin/env", &[], &dir);
        req.env.insert("FOE_TEST_KEY".into(), "value".into());
        let out = ex.run(req).unwrap();
        assert_eq!(out.exit_code, Some(0));
        assert_eq!(
            String::from_utf8(out.stdout).unwrap(),
            "FOE_TEST_KEY=value\n"
        );
    }

    #[test]
    fn stdin_is_null_or_the_supplied_bytes() {
        let (ex, dir, _) = executor("stdin", vec![], vec!["/bin/cat".into()]);
        let out = ex.run(request("/bin/cat", &[], &dir)).unwrap();
        assert_eq!(out.exit_code, Some(0));
        assert!(out.stdout.is_empty());
        let mut req = request("/bin/cat", &[], &dir);
        req.stdin = Some(b"hello".to_vec());
        assert_eq!(ex.run(req).unwrap().stdout, b"hello");
    }

    #[test]
    fn exit_code_and_cwd_are_reported() {
        let (ex, dir, _) = executor("cwd", vec![], vec!["/bin/sh".into(), "/bin/pwd".into()]);
        assert_eq!(
            ex.run(request("/bin/sh", &["-c", "exit 3"], &dir))
                .unwrap()
                .exit_code,
            Some(3)
        );
        let out = ex.run(request("/bin/pwd", &[], &dir)).unwrap();
        assert_eq!(
            String::from_utf8(out.stdout).unwrap().trim(),
            dir.display().to_string()
        );
    }

    #[test]
    fn timeout_kills_the_whole_group() {
        let (ex, dir, _) = executor("timeout", vec![], vec!["/bin/sh".into()]);
        let mut req = request("/bin/sh", &["-c", "sleep 30 & echo $!; wait"], &dir);
        req.timeout = Duration::from_millis(300);
        let out = ex.run(req).unwrap();
        assert!(out.timed_out);
        assert_eq!(out.exit_code, None);
        assert!(out.duration < Duration::from_secs(5));
        let pid: u32 = String::from_utf8(out.stdout)
            .unwrap()
            .trim()
            .parse()
            .unwrap();
        let gone = (0..200).any(|_| {
            std::thread::sleep(POLL);
            !Path::new(&format!("/proc/{pid}")).exists()
        });
        assert!(gone, "the backgrounded sleep was killed with the group");
    }

    #[test]
    fn cancellation_ends_a_running_process() {
        let (ex, dir, cancel) = executor("cancel", vec![], vec!["/bin/sleep".into()]);
        let flag = cancel.clone();
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(200));
            flag.store(true, Ordering::SeqCst);
        });
        let out = ex.run(request("/bin/sleep", &["30"], &dir)).unwrap();
        assert!(out.timed_out);
        assert!(out.duration < Duration::from_secs(5));
    }

    #[test]
    fn output_beyond_the_limit_spills_to_a_file() {
        let (ex, dir, _) = executor("spill", vec![], vec!["/bin/sh".into()]);
        let total = CAPTURE_LIMIT + 4096;
        let script = format!("head -c {total} /dev/zero | tr '\\0' a");
        let out = ex.run(request("/bin/sh", &["-c", &script], &dir)).unwrap();
        assert_eq!(out.exit_code, Some(0));
        let text = String::from_utf8(out.stdout).unwrap();
        assert!(text.starts_with(&"a".repeat(CAPTURE_LIMIT)));
        let note = &text[CAPTURE_LIMIT..];
        assert!(note.starts_with("\n[output beyond"), "{note}");
        let spill = dir.join("spill").join("exec-0-sh.stdout");
        assert_eq!(
            std::fs::metadata(&spill).unwrap().len() as usize,
            total - CAPTURE_LIMIT
        );
        assert!(note.contains(&spill.display().to_string()));
    }

    #[test]
    fn reads_outside_the_roots_are_denied_under_landlock() {
        let inside = scratch("exec", "inside");
        let outside = scratch("exec", "outside");
        std::fs::write(inside.join("a"), "a").unwrap();
        std::fs::write(outside.join("b"), "b").unwrap();
        let (ex, dir, _) = executor("landlock", vec![inside.clone()], vec!["/bin/cat".into()]);
        if ex.sandbox.abi() == 0 {
            eprintln!("skipped: the kernel offers no Landlock");
            return;
        }
        let a = ex
            .run(request(
                "/bin/cat",
                &[inside.join("a").to_str().unwrap()],
                &dir,
            ))
            .unwrap();
        assert_eq!(a.exit_code, Some(0));
        let b = ex
            .run(request(
                "/bin/cat",
                &[outside.join("b").to_str().unwrap()],
                &dir,
            ))
            .unwrap();
        assert_ne!(b.exit_code, Some(0));
        assert!(String::from_utf8_lossy(&b.stderr).contains("Permission denied"));
    }
}
