use super::*;
use std::os::unix::process::CommandExt;
use std::process::Stdio;

pub(crate) fn test_boundary(name: &str) -> Result<(ProcessBoundary, PathBuf), RuntimeError> {
    let origin = current_cgroup()?;
    let invocation = origin.join(format!("foe-test-{}-{}", safe_name(name), std::process::id()));
    let task = invocation.join("task");
    let episode = invocation.join("episode");
    let process = episode.join("process");
    for path in [&invocation, &task, &episode, &process] {
        std::fs::create_dir(path).map_err(|e| cgroup_error("create", path, e))?;
    }
    require_boundary_files(&episode)?;
    Ok((ProcessBoundary { paths: BoundaryPaths { episode, task } }, invocation))
}

pub(crate) fn remove_test_boundary(boundary: &ProcessBoundary, invocation: &Path) {
    let _ = kill_and_remove(&boundary.paths.episode);
    let _ = kill_and_remove(&boundary.paths.task);
    let _ = std::fs::remove_dir(invocation);
}

/// docs/sandbox.md "Process ownership": cgroup cleanup observes and kills
/// descendants after their direct child exits, including a process that
/// created a new session and process group.
#[test]
fn a_child_boundary_owns_a_grandchild_and_a_detached_process() {
    let Ok((boundary, invocation)) = test_boundary("detached") else {
        return;
    };
    let child = boundary.child("ep_child").unwrap();
    let script = "sleep 30 >/dev/null 2>&1 & a=$!; setsid /bin/sh -c 'sleep 30' >/dev/null 2>&1 & b=$!; printf '%s %s' \"$a\" \"$b\"";
    let argv = [OsString::from("/bin/sh"), OsString::from("-c"), OsString::from(script)];
    let output = command_in(&child.process_procs(), &argv).process_group(0).stdin(Stdio::null()).output().unwrap();
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    let pids: Vec<u32> =
        String::from_utf8(output.stdout).unwrap().split_whitespace().map(|pid| pid.parse().unwrap()).collect();
    assert_eq!(pids.len(), 2);
    assert!(populated(&child.paths.episode).unwrap(), "both descendants outlive their direct child");
    child.terminate().unwrap();
    assert!(pids.iter().all(|pid| exited(*pid)), "both descendants were killed before the boundary was removed");
    remove_tree(&boundary.paths.episode).unwrap();
    remove_tree(&boundary.paths.task).unwrap();
    std::fs::remove_dir(invocation).unwrap();
}

fn exited(pid: u32) -> bool {
    let Ok(stat) = std::fs::read_to_string(format!("/proc/{pid}/stat")) else {
        return true;
    };
    stat.rsplit_once(") ").is_some_and(|(_, fields)| fields.starts_with('Z'))
}

/// docs/sandbox.md "Process ownership": off mode reports that process
/// groups provide observational cleanup without claiming subtree coverage.
#[test]
fn off_mode_reports_the_process_group_fallback() {
    let ownership = ProcessOwnership::enter(SandboxMode::Off, "ep_test", None).unwrap();
    assert_eq!(ownership.info().kind, ProcessBoundaryKind::ProcessGroup);
    assert_eq!(ownership.info().subtree_cleanup, SubtreeCleanup::Observational);
    assert!(ownership.boundary().is_none());
}

/// docs/sandbox.md "Modes": cgroup ownership is reported independently of
/// the required Landlock guarantee, so a missing delegation selects the
/// observational process-group fallback.
#[test]
fn a_missing_delegation_selects_observational_process_groups() {
    let unavailable = Err(RuntimeError::Sandbox("test host has no delegated cgroup".into()));
    let ownership = ProcessOwnership::from_root(unavailable);
    assert_eq!(ownership.info().kind, ProcessBoundaryKind::ProcessGroup);
    assert_eq!(ownership.info().subtree_cleanup, SubtreeCleanup::Observational);
    assert!(ownership.info().reason.is_some());
}

/// docs/sandbox.md "Process ownership": the exact shell used by the cgroup
/// entry wrapper and the runtime-owned control paths join the reported
/// episode policy before confinement.
#[test]
fn cgroup_ownership_reports_its_launcher_and_control_paths() {
    let paths = BoundaryPaths { episode: PathBuf::from("/cgroup/episode"), task: PathBuf::from("/cgroup/task") };
    let ownership = ProcessOwnership::enforced(ProcessBoundary { paths: paths.clone() }, None);
    let mut policy = Policy::default();
    ownership.authorize(&mut policy).unwrap();
    let access = policy.resolved_permissions();
    assert!(access.execute.iter().any(|entry| {
        entry.path == std::fs::canonicalize(PROCESS_BOUNDARY_LAUNCHER).unwrap().to_string_lossy()
            && entry.reason == "cgroup process-boundary launcher"
    }));
    assert!(access.read.iter().any(|entry| entry.path == paths.episode.to_string_lossy()));
    assert!(access.write.iter().any(|entry| entry.path == paths.task.to_string_lossy()));
}

/// docs/protocol.md "Children": a child accepts only the boundary that its
/// parent placed it in before launch.
#[test]
fn inherited_metadata_cannot_move_a_process_to_another_boundary() {
    let Ok((boundary, invocation)) = test_boundary("child-boundary") else {
        return;
    };
    let error = ProcessBoundary::enter(boundary.paths.clone()).err().unwrap().to_string();
    assert!(error.contains("current cgroup"), "{error}");
    remove_test_boundary(&boundary, &invocation);
}
