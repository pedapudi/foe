use super::*;
use foe_log::SandboxMode;
use std::collections::BTreeMap;
use std::path::Path;

/// A fresh directory under the build tree for one test.
pub(crate) fn scratch(module: &str, name: &str) -> PathBuf {
    let dir = Path::new(concat!(env!("CARGO_MANIFEST_DIR"), "/../../target/test-scratch"))
        .join(format!("{module}-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir.canonicalize().unwrap()
}

fn executor(name: &str, read: Vec<PathBuf>, exec: Vec<PathBuf>) -> (LocalExecutor, PathBuf, Arc<AtomicBool>) {
    let dir = scratch("exec", name);
    let cancel = Arc::new(AtomicBool::new(false));
    let sandbox = Arc::new(Sandbox::new(SandboxMode::BestEffort).unwrap());
    let policy = Policy { read, exec, ..Policy::default() };
    (LocalExecutor::new(sandbox, policy, dir.join("spill"), cancel.clone()), dir, cancel)
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
        policy: None,
        pass_fds: Vec::new(),
    }
}

#[test]
fn environment_is_exactly_the_request() {
    let (ex, dir, _) = executor("env", vec![], vec!["/usr/bin/env".into()]);
    let mut req = request("/usr/bin/env", &[], &dir);
    req.env.insert("FOE_TEST_KEY".into(), "value".into());
    let out = ex.run(req).unwrap();
    assert_eq!(out.exit_code, Some(0));
    assert_eq!(String::from_utf8(out.stdout).unwrap(), "FOE_TEST_KEY=value\n");
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
    assert_eq!(ex.run(request("/bin/sh", &["-c", "exit 3"], &dir)).unwrap().exit_code, Some(3));
    let out = ex.run(request("/bin/pwd", &[], &dir)).unwrap();
    assert_eq!(String::from_utf8(out.stdout).unwrap().trim(), dir.display().to_string());
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
    let pid: u32 = String::from_utf8(out.stdout).unwrap().trim().parse().unwrap();
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
    assert_eq!(std::fs::metadata(&spill).unwrap().len() as usize, total - CAPTURE_LIMIT);
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
    let a = ex.run(request("/bin/cat", &[inside.join("a").to_str().unwrap()], &dir)).unwrap();
    assert_eq!(a.exit_code, Some(0));
    let b = ex.run(request("/bin/cat", &[outside.join("b").to_str().unwrap()], &dir)).unwrap();
    assert_ne!(b.exit_code, Some(0));
    assert!(String::from_utf8_lossy(&b.stderr).contains("Permission denied"));
}

/// A descriptor in `pass_fds` reaches the child at the number given, and
/// the parent-held copy closes when the run ends, so a reader of the other
/// end sees end-of-file rather than a hang.
#[test]
fn a_passed_descriptor_reaches_the_child_at_its_number() {
    use std::io::Read;
    let (ex, dir, _) = executor("passfd", vec![], vec!["/bin/sh".into()]);
    let (mut parent, child) = std::os::unix::net::UnixStream::pair().unwrap();
    let mut req = request("/bin/sh", &["-c", "echo over-three >&3"], &dir);
    req.pass_fds = vec![(3, Arc::new(std::os::fd::OwnedFd::from(child)))];
    let out = ex.run(req).unwrap();
    assert_eq!(out.exit_code, Some(0), "{}", String::from_utf8_lossy(&out.stderr));
    let mut received = String::new();
    parent.read_to_string(&mut received).unwrap();
    assert_eq!(received, "over-three\n");
}

/// A request naming a policy of its own runs under that policy in place of
/// the per-executable narrowing.
#[test]
fn a_request_policy_replaces_the_derived_narrowing() {
    let (ex, dir, _) = executor("ownpolicy", vec![], vec![]);
    if ex.sandbox.abi() == 0 {
        eprintln!("skipped: the kernel offers no Landlock");
        return;
    }
    std::fs::write(dir.join("secret"), b"s").unwrap();
    let probe = format!("cat {}/secret", dir.display());
    let mut req = request("/bin/sh", &["-c", &probe], &dir);
    req.cwd = PathBuf::from("/");
    req.policy = Some(Policy { read: vec!["/usr".into()], exec: vec!["/bin/sh".into()], ..Policy::default() });
    let out = ex.run(req).unwrap();
    assert_ne!(out.exit_code, Some(0), "a path outside the request's policy is denied");
}
