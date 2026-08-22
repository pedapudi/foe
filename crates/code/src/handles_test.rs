//! Test doubles for the capability handles: a reader and writer bounded to
//! one temporary directory, an executor that returns a canned result, and an
//! executor that runs real processes in their own process group. The
//! executor doubles are unused when the `exec` feature is off.
#![allow(dead_code)]

use foe_core::{CallCtx, CapError, ExecRequest, ExecResult, Executor, Reader, Writer};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

pub struct Fixture {
    dir: tempfile::TempDir,
    root: PathBuf,
    writes: Arc<AtomicUsize>,
}

impl Fixture {
    pub fn new() -> Self {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().canonicalize().unwrap().join("root");
        std::fs::create_dir(&root).unwrap();
        Self { dir, root, writes: Arc::new(AtomicUsize::new(0)) }
    }
    pub fn root(&self) -> PathBuf {
        self.root.clone()
    }
    pub fn write(&self, rel: &str, text: &str) {
        self.write_bytes(rel, text.as_bytes());
    }
    pub fn write_bytes(&self, rel: &str, bytes: &[u8]) {
        let p = self.root.join(rel);
        std::fs::create_dir_all(p.parent().unwrap()).unwrap();
        std::fs::write(p, bytes).unwrap();
    }
    pub fn read(&self, rel: &str) -> String {
        std::fs::read_to_string(self.root.join(rel)).unwrap()
    }
    /// Number of writes made through the `Writer` handle.
    pub fn writes(&self) -> usize {
        self.writes.load(Ordering::SeqCst)
    }
}

struct Bounded {
    roots: Vec<PathBuf>,
    writes: Arc<AtomicUsize>,
}

impl Bounded {
    fn check(&self, path: &Path) -> Result<PathBuf, CapError> {
        let canon = match path.canonicalize() {
            Ok(c) => c,
            Err(_) => {
                let parent = path.parent().ok_or_else(|| CapError::Denied { path: path.into() })?;
                parent.canonicalize()?.join(path.file_name().unwrap_or_default())
            }
        };
        if self.roots.iter().any(|r| canon.starts_with(r)) {
            Ok(canon)
        } else {
            Err(CapError::Denied { path: path.into() })
        }
    }
}

impl Reader for Bounded {
    fn read(&self, path: &Path) -> Result<Vec<u8>, CapError> {
        Ok(std::fs::read(self.check(path)?)?)
    }
    fn metadata(&self, path: &Path) -> Result<std::fs::Metadata, CapError> {
        Ok(std::fs::metadata(self.check(path)?)?)
    }
    fn files(&self, path: &Path) -> Result<Vec<PathBuf>, CapError> {
        let root = self.check(path)?;
        if root.is_file() {
            return Ok(vec![root]);
        }
        Ok(ignore::WalkBuilder::new(root)
            .require_git(false)
            .build()
            .filter_map(Result::ok)
            .filter(|entry| entry.file_type().is_some_and(|kind| kind.is_file()))
            .map(|entry| entry.into_path())
            .collect())
    }
    fn roots(&self) -> &[PathBuf] {
        &self.roots
    }
}

impl Writer for Bounded {
    fn write(&self, path: &Path, bytes: &[u8]) -> Result<(), CapError> {
        let target = self.check(path)?;
        let staged = target.with_extension("staged");
        std::fs::write(&staged, bytes)?;
        std::fs::rename(&staged, &target)?;
        self.writes.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
    fn roots(&self) -> &[PathBuf] {
        &self.roots
    }
}

/// A context with a reader and writer bounded to the fixture root.
pub fn ctx(fx: &Fixture) -> CallCtx {
    let handle = Arc::new(Bounded { roots: vec![fx.root()], writes: fx.writes.clone() });
    CallCtx {
        call_id: "call-1".into(),
        step: 1,
        reader: Some(handle.clone()),
        writer: Some(handle),
        executor: None,
        spawner: None,
        spill_dir: fx.dir.path().join("spill"),
        deadline: None,
    }
}

pub fn ctx_with_executor(fx: &Fixture, executor: Arc<dyn Executor>) -> CallCtx {
    let mut c = ctx(fx);
    c.writer = None;
    c.executor = Some(executor);
    c
}

/// Returns one canned result and records every request.
pub struct FakeExecutor {
    result: ExecResult,
    requests: Mutex<Vec<ExecRequest>>,
}

impl FakeExecutor {
    pub fn new(result: ExecResult) -> Self {
        Self { result, requests: Mutex::new(Vec::new()) }
    }
    pub fn last(&self) -> Option<ExecRequest> {
        self.requests.lock().unwrap().last().cloned()
    }
}

impl Executor for FakeExecutor {
    fn run(&self, req: ExecRequest) -> Result<ExecResult, CapError> {
        self.requests.lock().unwrap().push(req);
        Ok(self.result.clone())
    }
}

/// Runs the request for real in a fresh process group and kills the whole
/// group on timeout. Stands in for the runtime's executor.
pub struct ProcessGroupExecutor;

impl Executor for ProcessGroupExecutor {
    fn run(&self, req: ExecRequest) -> Result<ExecResult, CapError> {
        use std::io::Read;
        use std::os::unix::process::CommandExt;
        let started = Instant::now();
        let mut child = Command::new(&req.program)
            .args(&req.args)
            .current_dir(&req.cwd)
            .env_clear()
            .envs(&req.env)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .process_group(0)
            .spawn()?;
        let mut out = child.stdout.take().unwrap();
        let mut err = child.stderr.take().unwrap();
        let reader = std::thread::spawn(move || {
            let (mut o, mut e) = (Vec::new(), Vec::new());
            let t = std::thread::spawn(move || {
                err.read_to_end(&mut e).ok();
                e
            });
            out.read_to_end(&mut o).ok();
            (o, t.join().unwrap())
        });
        let mut timed_out = false;
        let status = loop {
            if let Some(s) = child.try_wait()? {
                break Some(s);
            }
            if started.elapsed() >= req.timeout {
                timed_out = true;
                Command::new("kill").args(["-9", "--", &format!("-{}", child.id())]).status()?;
                child.wait()?;
                break None;
            }
            std::thread::sleep(Duration::from_millis(20));
        };
        let (stdout, stderr) = reader.join().unwrap();
        Ok(ExecResult {
            exit_code: status.and_then(|s| s.code()),
            stdout,
            stderr,
            timed_out,
            duration: started.elapsed(),
        })
    }
}
