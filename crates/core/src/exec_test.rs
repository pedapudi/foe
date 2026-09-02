use super::*;
use crate::test_util::ScratchDir;
use foe_log::SandboxMode;
use std::collections::BTreeMap;
use std::os::unix::fs::PermissionsExt;
use std::path::Path;

/// A uniquely owned directory for one test.
pub(crate) fn scratch(module: &str, name: &str) -> ScratchDir {
    crate::test_util::tmp(&format!("{module}-{name}"))
}

fn executor(name: &str, read: Vec<PathBuf>, exec: Vec<PathBuf>) -> (LocalExecutor, ScratchDir, Arc<AtomicBool>) {
    let dir = scratch("exec", name);
    let cancel = Arc::new(AtomicBool::new(false));
    let sandbox = Arc::new(Sandbox::new(SandboxMode::BestEffort).unwrap());
    let policy = Policy { read, delegated_exec: exec, ..Policy::default() };
    (LocalExecutor::new(sandbox, policy, dir.join("spill"), cancel.clone()), dir, cancel)
}

fn request(contract: &str, args: &[&str], cwd: &Path) -> ExecRequest {
    ExecRequest {
        command: contract.into(),
        captured_executable: None,
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

fn configured_executor(
    name: &str,
    source: &Path,
    mode: SandboxMode,
    write: Vec<PathBuf>,
) -> (LocalExecutor, ExecRequest, crate::captured_executable::CapturedExecutableTree) {
    let dir = source.parent().unwrap();
    let config: foe_contract::ContractDocument = serde_json::from_value(serde_json::json!({
        "version": 4,
        "name": "immutable executable test",
        "instructions": {"role": "test"},
        "tools": ["configured"],
        "tool_defs": {"configured": {"exec": source, "description": "test executable"}},
        "grants": {"read": [dir], "write": write},
        "budget": {"model_calls": 1},
        "sandbox": {"mode": mode},
        "task": "test"
    }))
    .unwrap();
    let contract = foe_contract::document::resolve(&config).unwrap();
    let executables = crate::captured_executable::CapturedExecutableTree::materialize(&contract, dir).unwrap();
    let policy = Policy::for_episode(&contract, &executables, dir).unwrap();
    let sandbox = Arc::new(Sandbox::new(mode).unwrap());
    let executor =
        LocalExecutor::new(sandbox, policy, dir.join(format!("spill-{name}")), Arc::new(AtomicBool::new(false)));
    let mut req = request(source.to_str().unwrap(), &[], dir);
    req.captured_executable = Some(executables.tools["configured"].clone());
    (executor, req, executables)
}

#[test]
fn a_captured_script_survives_mutation_replacement_deletion_and_repeated_calls_under_landlock() {
    let dir = scratch("exec", "immutable-script");
    let source = dir.join("tool");
    std::fs::write(dir.join("resource"), "original\n").unwrap();
    std::fs::write(&source, "#!/bin/sh\nread value < resource\nprintf '%s\\n' \"$value\"\n").unwrap();
    std::fs::set_permissions(&source, std::fs::Permissions::from_mode(0o755)).unwrap();
    let (executor, req, executables) = configured_executor("script", &source, SandboxMode::Required, Vec::new());
    let captured_link =
        std::fs::read_link(crate::captured_executable::parent_fd_path(executables.tools["configured"].fd())).unwrap();
    assert_eq!(captured_link.file_name().unwrap(), "tool");
    assert_eq!(executor.run(req.clone()).unwrap().stdout, b"original\n");
    std::fs::write(&source, "#!/bin/sh\nprintf 'mutated\\n'\n").unwrap();
    assert_eq!(executor.run(req.clone()).unwrap().stdout, b"original\n");
    let replacement = dir.join("replacement");
    std::fs::write(&replacement, "#!/bin/sh\nprintf 'replacement\\n'\n").unwrap();
    std::fs::set_permissions(&replacement, std::fs::Permissions::from_mode(0o755)).unwrap();
    std::fs::rename(&replacement, &source).unwrap();
    assert_eq!(executor.run(req.clone()).unwrap().stdout, b"original\n");
    std::fs::remove_file(&source).unwrap();
    assert_eq!(executor.run(req.clone()).unwrap().stdout, b"original\n");
    let executable = &executables.tools["configured"];
    let write =
        std::fs::OpenOptions::new().write(true).open(crate::captured_executable::parent_fd_path(executable.fd()));
    assert!(write.is_err(), "the captured executable descriptor rejects writes");
    assert_eq!(executor.run(req).unwrap().stdout, b"original\n");
}

#[test]
fn a_captured_elf_runs_after_its_source_is_replaced_under_landlock() {
    let dir = scratch("exec", "immutable-elf");
    let source = dir.join("echo");
    std::fs::copy("/bin/echo", &source).unwrap();
    std::fs::set_permissions(&source, std::fs::Permissions::from_mode(0o755)).unwrap();
    let (executor, mut req, _) = configured_executor("elf", &source, SandboxMode::Required, Vec::new());
    req.args = vec!["committed".into()];
    std::fs::write(&source, b"not an executable anymore").unwrap();
    let result = executor.run(req).unwrap();
    assert_eq!(result.exit_code, Some(0), "{}", String::from_utf8_lossy(&result.stderr));
    assert_eq!(result.stdout, b"committed\n");
}

#[test]
fn a_configured_multicall_executable_keeps_its_configured_name() {
    let dir = scratch("exec", "multicall-name");
    let config: foe_contract::ContractDocument = serde_json::from_value(serde_json::json!({
        "version": 4,
        "name": "multicall executable test",
        "instructions": {"role": "test"},
        "tools": ["configured"],
        "tool_defs": {"configured": {"exec": "/bin/echo", "description": "test executable"}},
        "grants": {"read": [dir]},
        "budget": {"model_calls": 1},
        "sandbox": {"mode": "required"},
        "task": "test"
    }))
    .unwrap();
    let contract = foe_contract::document::resolve(&config).unwrap();
    let executables =
        crate::captured_executable::CapturedExecutableTree::materialize(&contract, &dir.join("episode")).unwrap();
    let executable = executables.tools["configured"].clone();
    assert_eq!(executable.invocation_name(), "echo");
    let policy = Policy::for_episode(&contract, &executables, &dir).unwrap();
    let executor = LocalExecutor::new(
        Arc::new(Sandbox::new(SandboxMode::Required).unwrap()),
        policy,
        dir.join("spill"),
        Arc::new(AtomicBool::new(false)),
    );
    let mut req = request(contract.tool_defs["configured"].exec.to_str().unwrap(), &["committed"], &dir);
    req.captured_executable = Some(executable);
    let result = executor.run(req).unwrap();
    assert_eq!(result.exit_code, Some(0), "{}", String::from_utf8_lossy(&result.stderr));
    assert_eq!(result.stdout, b"committed\n");
}

#[test]
fn construction_rejects_a_source_without_an_execute_bit() {
    let dir = scratch("exec", "non-executable");
    let source = dir.join("tool");
    std::fs::write(&source, "#!/bin/sh\nexit 0\n").unwrap();
    std::fs::set_permissions(&source, std::fs::Permissions::from_mode(0o644)).unwrap();
    let error = crate::captured_executable::CapturedExecutable::load(&source).unwrap_err();
    assert!(error.contains("executable file"), "{error}");
}

#[test]
fn a_declared_write_root_does_not_expose_private_executable_storage() {
    let dir = scratch("exec", "write-root-around-log");
    let source = dir.join("tool");
    std::fs::write(&source, "#!/bin/sh\nprintf 'safe\\n'\n").unwrap();
    std::fs::set_permissions(&source, std::fs::Permissions::from_mode(0o755)).unwrap();
    let (executor, req, executables) =
        configured_executor("write-root", &source, SandboxMode::Required, vec![dir.to_path_buf()]);
    assert!(!executables.tools["configured"].stored_path().starts_with(&dir));
    std::fs::write(&source, "#!/bin/sh\nprintf 'changed\\n'\n").unwrap();
    assert_eq!(executor.run(req).unwrap().stdout, b"safe\n");
}

#[test]
fn the_last_runtime_owner_removes_private_executable_storage() {
    let dir = scratch("exec", "runtime-storage-cleanup");
    let source = dir.join("tool");
    std::fs::write(&source, "#!/bin/sh\nexit 0\n").unwrap();
    std::fs::set_permissions(&source, std::fs::Permissions::from_mode(0o755)).unwrap();
    let executable = crate::captured_executable::CapturedExecutable::load(&source).unwrap();
    let stored_path = executable.stored_path().to_path_buf();
    assert!(stored_path.exists());
    drop(executable);
    assert!(!stored_path.exists());
}

#[test]
fn a_confined_episode_removes_private_executable_storage() {
    let dir = scratch("exec", "confined-storage-cleanup");
    let source = dir.join("tool");
    std::fs::write(&source, "#!/bin/sh\nexit 0\n").unwrap();
    std::fs::set_permissions(&source, std::fs::Permissions::from_mode(0o755)).unwrap();
    let (_, _, executables) =
        configured_executor("confined-cleanup", &source, SandboxMode::BestEffort, vec![dir.to_path_buf()]);
    let stored_path = executables.tools["configured"].stored_path().to_path_buf();
    let mut policy = Policy::default();
    for root in executables.cleanup_roots() {
        policy.add_cleanup(root, "private captured-executable store");
    }
    let sandbox = Sandbox::new(SandboxMode::BestEffort).unwrap();
    if sandbox.abi() == 0 {
        return;
    }
    let removed = sandbox.run_narrowed(&policy, move || {
        drop(executables);
        !stored_path.exists()
    });
    assert!(removed.unwrap(), "the episode cleanup permission removes the private store");
}

#[test]
fn executable_storage_skips_a_noexec_preferred_filesystem() {
    let noexec = Path::new("/dev/shm");
    let Ok(info) = nix::sys::statvfs::statvfs(noexec) else { return };
    if !info.flags().contains(nix::sys::statvfs::FsFlags::ST_NOEXEC) {
        return;
    }
    let preferred = noexec.join(format!("foe-executable-test-{}", std::process::id()));
    if std::fs::create_dir(&preferred).is_err() {
        return;
    }
    let dir = scratch("exec", "noexec-fallback");
    let source = dir.join("tool");
    std::fs::write(&source, "#!/bin/sh\nexit 0\n").unwrap();
    std::fs::set_permissions(&source, std::fs::Permissions::from_mode(0o755)).unwrap();
    let config: foe_contract::ContractDocument = serde_json::from_value(serde_json::json!({
        "version": 4, "name": "noexec fallback", "instructions": {"role": "test"},
        "tools": ["configured"],
        "tool_defs": {"configured": {"exec": source, "description": "test executable"}},
        "grants": {"read": [dir]}, "budget": {"model_calls": 1}, "task": "test"
    }))
    .unwrap();
    let contract = foe_contract::document::resolve(&config).unwrap();
    let executables =
        crate::captured_executable::CapturedExecutableTree::materialize(&contract, &preferred.join("episode")).unwrap();
    let link =
        std::fs::read_link(crate::captured_executable::parent_fd_path(executables.tools["configured"].fd())).unwrap();
    assert!(!link.starts_with(noexec), "the selected captured came from noexec storage: {link:?}");
    std::fs::remove_dir(preferred).unwrap();
}

#[test]
fn declared_tree_references_share_one_captured_executable() {
    let dir = scratch("exec", "declared-tree-dedup");
    let source = dir.join("tool");
    std::fs::write(&source, "#!/bin/sh\nexit 0\n").unwrap();
    std::fs::set_permissions(&source, std::fs::Permissions::from_mode(0o755)).unwrap();
    let declared = |name: &str| {
        serde_json::json!({
            "name": name, "instructions": {"role": "test"}, "tools": ["configured"],
            "tool_defs": {"configured": {"exec": source.clone(), "description": "same executable"}},
            "grants": {"read": [dir.to_path_buf()]}, "budget": {"model_calls": 1}
        })
    };
    let config: foe_contract::ContractDocument = serde_json::from_value(serde_json::json!({
        "version": 4, "name": "declared tree", "instructions": {"role": "test"},
        "tools": ["configured"],
        "tool_defs": {"configured": {"exec": source, "description": "same executable"}},
        "grants": {"read": [dir.to_path_buf()]}, "budget": {"model_calls": 1},
        "child_contracts": {"first": declared("first"), "second": declared("second")}, "task": "test"
    }))
    .unwrap();
    let contract = foe_contract::document::resolve(&config).unwrap();
    let executables = crate::captured_executable::CapturedExecutableTree::materialize(&contract, &dir).unwrap();
    assert_eq!(executables.reachable_entries().len(), 1, "ungranted contracts carry no execute permission");
    assert_eq!(executables.fingerprint_entries().len(), 3, "every declaration can be reconstructed");
    assert_eq!(executables.child_descriptors("ep_child").unwrap().len(), 2, "one image plus one manifest");
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
    let (ex, dir, _) = executor("timeout", vec![], vec!["/bin/sleep".into()]);
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
    let (ex, dir, _) = executor("spill", vec![], vec!["/usr/bin/head".into(), "/usr/bin/tr".into()]);
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
    let (ex, dir, _) = executor("landlock", vec![inside.to_path_buf()], vec!["/bin/cat".into()]);
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

#[test]
fn descriptor_remapping_preserves_crossed_sources_and_reserves_no_stdio_number() {
    use command_fds::{CommandFdExt, FdMapping};
    use std::os::fd::{AsRawFd, OwnedFd};
    let dir = scratch("exec", "fd-collision");
    let left_path = dir.join("left");
    let right_path = dir.join("right");
    std::fs::write(&left_path, "left").unwrap();
    std::fs::write(&right_path, "right").unwrap();
    let left = std::fs::File::open(&left_path).unwrap();
    let right = std::fs::File::open(&right_path).unwrap();
    let (left_fd, right_fd) = (left.as_raw_fd(), right.as_raw_fd());
    let mut command = std::process::Command::new("/bin/cat");
    command
        .arg(format!("/proc/self/fd/{left_fd}"))
        .arg(format!("/proc/self/fd/{right_fd}"))
        .fd_mappings(vec![
            FdMapping { parent_fd: OwnedFd::from(left), child_fd: right_fd },
            FdMapping { parent_fd: OwnedFd::from(right), child_fd: left_fd },
        ])
        .unwrap();
    let output = command.output().unwrap();
    assert_eq!(output.stdout, b"rightleft");

    let source = dir.join("tool");
    std::fs::write(&source, "#!/bin/sh\nexit 0\n").unwrap();
    std::fs::set_permissions(&source, std::fs::Permissions::from_mode(0o755)).unwrap();
    let (_, _, executables) = configured_executor("manifest-fds", &source, SandboxMode::Off, Vec::new());
    let mappings = executables.child_descriptors("ep_child").unwrap();
    let targets: Vec<i32> = mappings.iter().map(|(fd, _)| *fd).collect();
    assert!(targets.contains(&63) && targets.contains(&64), "{targets:?}");
    assert!(targets.iter().all(|fd| *fd > 2));
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
    let mut policy = Policy::for_runtime_executable(Path::new("/bin/sh"), vec!["/usr".into()], "test shell").unwrap();
    policy.add_executable(Path::new("/usr/bin/cat"), "test subprocess".into()).unwrap();
    req.policy = Some(policy);
    let out = ex.run(req).unwrap();
    assert_ne!(out.exit_code, Some(0), "a path outside the request's policy is denied");
}
