use super::*;
use foe_program::ProgramDocument;
use std::io::Write;

fn sandbox() -> Option<Sandbox> {
    let s = Sandbox::new(SandboxMode::BestEffort).unwrap();
    if s.abi() == 0 {
        eprintln!("skipped: the kernel offers no Landlock");
        return None;
    }
    Some(s)
}

fn temp_dir(name: &str) -> PathBuf {
    let dir = crate::exec::tests::scratch("sandbox", name);
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

fn policy(config: &ProgramDocument, log_dir: &Path) -> Policy {
    let program = foe_program::document::resolve(config).unwrap();
    let executables =
        crate::executable::ExecutableTree::materialize(&program, Path::new("/tmp/foe-sandbox-episode")).unwrap();
    Policy::for_episode(&program, &executables, log_dir)
}

#[test]
fn off_records_zero_and_required_needs_landlock() {
    assert_eq!(
        Sandbox::new(SandboxMode::Off).unwrap().info(),
        SandboxInfo {
            mode: SandboxMode::Off,
            landlock_abi: 0,
            effective_access: None,
            process_boundary: None,
        }
    );
    let required = Sandbox::new(SandboxMode::Required);
    if probe_abi() == 0 {
        assert!(matches!(required, Err(RuntimeError::Sandbox(_))));
    } else {
        assert!(required.unwrap().abi() >= 1);
    }
}

#[test]
fn best_effort_records_the_abi_obtained() {
    let s = Sandbox::new(SandboxMode::BestEffort).unwrap();
    assert_eq!(s.info().landlock_abi, probe_abi().min(MAX_ABI));
    eprintln!("landlock abi in use: {}", s.abi());
}

#[test]
fn read_roots_are_readable_and_other_paths_are_not() {
    let Some(s) = sandbox() else { return };
    let inside = temp_dir("inside");
    let outside = temp_dir("outside");
    std::fs::write(inside.join("a"), b"a").unwrap();
    std::fs::write(outside.join("b"), b"b").unwrap();
    let policy = Policy { read: vec![inside.clone()], ..Policy::default() };
    let (a, b, w) = s
        .run_narrowed(&policy, || {
            (
                std::fs::read(inside.join("a")).is_ok(),
                std::fs::read(outside.join("b")).is_ok(),
                std::fs::write(inside.join("c"), b"c").is_ok(),
            )
        })
        .unwrap();
    assert!(a, "a read root is readable");
    assert!(!b, "a path outside every root is denied");
    assert!(!w, "a read root is not writable");
    // The calling thread is unaffected by the narrowed thread.
    assert!(std::fs::read(outside.join("b")).is_ok());
}

#[test]
fn write_roots_allow_create_and_remove() {
    let Some(s) = sandbox() else { return };
    let dir = temp_dir("write");
    let policy = Policy { write: vec![dir.clone()], ..Policy::default() };
    let ok = s
        .run_narrowed(&policy, || {
            std::fs::File::create(dir.join("f")).and_then(|mut f| f.write_all(b"x")).is_ok()
                && std::fs::create_dir(dir.join("d")).is_ok()
                && std::fs::remove_file(dir.join("f")).is_ok()
        })
        .unwrap();
    assert!(ok);
}

#[test]
fn executable_policy_keeps_only_its_own_file() {
    let Some(s) = sandbox() else { return };
    let dir = temp_dir("exec");
    let other = dir.join("true");
    std::fs::copy("/bin/true", &other).unwrap();
    let episode = Policy {
        read: vec![dir.clone()],
        exec: vec!["/bin/sh".into(), other.clone()],
        runtime_storage: vec![dir.join("runtime-only")],
        ..Policy::default()
    };
    let tool = episode.for_executable(Path::new("/bin/sh"), false);
    assert_eq!(tool.exec, vec![PathBuf::from("/bin/sh")]);
    assert!(tool.runtime_storage.is_empty());
    assert!(tool.log_dir.is_none());
    let run = |policy: &Policy| {
        let mut cmd = Command::new("/bin/sh");
        cmd.arg("-c").arg(other.display().to_string()).env_clear();
        s.spawn_narrowed(policy, cmd).unwrap().wait_with_output().unwrap().status.success()
    };
    assert!(run(&episode), "a file in the episode's exec list runs");
    assert!(!run(&tool), "the same file is denied under a policy naming only /bin/sh");
}

/// docs/sandbox.md "Executables": an explicit execute grant remains in the
/// narrowed policy so a shell or build tool can start a declared subprocess.
#[test]
fn executable_policy_keeps_explicit_subprocess_grants() {
    let Some(s) = sandbox() else { return };
    let dir = temp_dir("delegated-exec");
    let helper = dir.join("true");
    std::fs::copy("/bin/true", &helper).unwrap();
    let episode = Policy {
        exec: vec!["/bin/sh".into(), helper.clone()],
        delegated_exec: vec![helper.clone()],
        ..Policy::default()
    };
    let tool = episode.for_executable(Path::new("/bin/sh"), false);
    assert_eq!(tool.exec, vec![PathBuf::from("/bin/sh"), helper.clone()]);
    let mut cmd = Command::new("/bin/sh");
    cmd.arg("-c").arg(helper.display().to_string()).env_clear();
    let status = s.spawn_narrowed(&tool, cmd).unwrap().wait().unwrap();
    assert!(status.success(), "the explicit subprocess grant survives executable narrowing");
}

#[test]
fn tcp_connect_is_denied_from_abi_4() {
    let Some(s) = sandbox() else { return };
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = listener.local_addr().unwrap();
    let closed = Policy::default();
    let open = Policy { connect_tcp: true, ..Policy::default() };
    let denied = s.run_narrowed(&closed, || std::net::TcpStream::connect(addr).is_err()).unwrap();
    let allowed = s.run_narrowed(&open, || std::net::TcpStream::connect(addr).is_ok()).unwrap();
    if s.abi() >= 4 {
        assert!(denied, "connect is denied when the policy closes TCP");
    }
    assert!(allowed, "connect succeeds when the policy opens TCP");
}

/// docs/sandbox.md "What is compiled": a process with outbound network
/// access can read the resolver configuration even when `/etc/resolv.conf`
/// is a symbolic link whose target lies outside `/etc`.
#[test]
fn network_policy_can_read_resolver_configuration() {
    let Some(s) = sandbox() else { return };
    let config: ProgramDocument = serde_json::from_value(serde_json::json!({
        "version": 3, "name": "network", "instructions": {"role": "x"}, "tools": ["block"],
        "grants": {"read": ["/tmp"], "write": []}, "budget": {"model_calls": 1},
        "model": {"provider": "openai", "model": "m"}, "task": "t"
    }))
    .unwrap();
    let policy = policy(&config, Path::new("/logs/ep"));
    let read = s.run_narrowed(&policy, || std::fs::read_to_string("/etc/resolv.conf")).unwrap();
    assert!(read.is_ok(), "/etc/resolv.conf is readable under the network policy: {read:?}");
}

#[test]
fn tcp_bind_is_limited_to_listed_ports_from_abi_4() {
    let Some(s) = sandbox() else { return };
    if s.abi() < 4 {
        eprintln!("skipped: ABI {} has no TCP rules", s.abi());
        return;
    }
    let probe = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let port = probe.local_addr().unwrap().port();
    drop(probe);
    let policy = Policy { bind_tcp: vec![port], ..Policy::default() };
    let (listed, other) = s
        .run_narrowed(&policy, || {
            (
                std::net::TcpListener::bind(("127.0.0.1", port)).is_ok(),
                std::net::TcpListener::bind(("127.0.0.1", port.wrapping_add(1).max(1024))).is_ok(),
            )
        })
        .unwrap();
    assert!(listed);
    assert!(!other);
}

/// docs/sandbox.md "Executables": the ports of `grants.bind` survive
/// executable narrowing, so a server started by a shell binds a granted
/// port, and an ungranted port is refused where the ABI enforces TCP.
#[test]
fn a_bind_grant_reaches_a_narrowed_executable() {
    let Some(s) = sandbox() else { return };
    let probe = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let port = probe.local_addr().unwrap().port();
    drop(probe);
    let config: ProgramDocument = serde_json::from_value(serde_json::json!({
        "version": 3, "name": "server", "instructions": {"role": "x"}, "tools": ["block"],
        "grants": {"read": ["/tmp"], "bind": [port]}, "budget": {"model_calls": 1}, "task": "t"
    }))
    .unwrap();
    let tool = policy(&config, Path::new("/logs/ep")).for_executable(Path::new("/bin/sh"), false);
    assert_eq!(tool.bind_tcp, vec![port]);
    let (granted, other) = s
        .run_narrowed(&tool, || {
            (
                std::net::TcpListener::bind(("127.0.0.1", port)).is_ok(),
                std::net::TcpListener::bind(("127.0.0.1", port.wrapping_add(1).max(1024))).is_ok(),
            )
        })
        .unwrap();
    assert!(granted, "a granted port binds under the narrowed policy");
    if s.abi() >= 4 {
        assert!(!other, "an ungranted port is refused from ABI 4");
    }
}

#[test]
fn episode_policy_follows_grants_and_tool_defs() {
    let dir = temp_dir("episode-policy");
    let tool = dir.join("ruff");
    std::fs::copy("/bin/true", &tool).unwrap();
    let out = dir.join("out");
    std::fs::create_dir(&out).unwrap();
    let config: ProgramDocument = serde_json::from_value(serde_json::json!({
        "version": 3, "name": "p", "instructions": {"r": "x"}, "tools": ["ruff"],
        "tool_defs": {"ruff": {"exec": tool, "description": "d"}},
        "grants": {"read": [dir], "write": [out], "execute": ["/bin/sh"], "bind": [8080]},
        "budget": {"model_calls": 1}, "task": "t"
    }))
    .unwrap();
    let p = policy(&config, Path::new("/logs/ep"));
    assert_eq!(p.read, vec![dir.clone()]);
    assert_eq!(p.write, vec![out]);
    let shell = std::fs::canonicalize("/bin/sh").unwrap();
    assert_eq!(p.delegated_exec, vec![shell.clone()]);
    assert_eq!(p.exec, vec![shell], "configured executables are authorized by their committed inode");
    assert_eq!(p.exec_files.len(), 1);
    assert_eq!(p.log_dir, Some(PathBuf::from("/logs/ep")));
    assert_eq!(p.bind_tcp, vec![8080], "the episode may bind the granted port");
    assert!(!p.connect_tcp, "an episode without a model block holds no transport");
    let resolver: Vec<PathBuf> = std::fs::canonicalize("/etc/resolv.conf").into_iter().collect();
    assert!(p.read_files.is_empty(), "an episode that opens no connection reads no resolver file");
    let mut with_children = config.clone();
    with_children.grants.spawn = vec!["survey".into()];
    with_children.programs.insert(
        "survey".into(),
        serde_json::from_value(serde_json::json!({
            "name": "survey", "instructions": {"r": "x"}, "tools": ["block"],
            "grants": {"read": [dir]}, "budget": {"model_calls": 1}
        }))
        .unwrap(),
    );
    with_children.model = Some(foe_program::ModelConfig::new("anthropic", "m"));
    let mut p = policy(&with_children, Path::new("/logs/ep"));
    assert_eq!(p.read_files, resolver, "the credential file is appended by the binary after resolution");
    if let Ok(binary) = std::env::current_exe() {
        assert!(p.exec.contains(&binary), "the binary starts children");
    }
    assert!(p.connect_tcp);
    p.read_files.push(PathBuf::from("/keys/anthropic"));
    let offline = p.for_executable(Path::new("/bin/sh"), false);
    assert!(offline.read_files.is_empty(), "an executable without network reads no resolver file");
    let online = p.for_executable(Path::new("/bin/sh"), true);
    assert_eq!(online.read_files, resolver, "an executable with network keeps the resolver file");
    assert!(!online.read_files.contains(&PathBuf::from("/keys/anthropic")), "and never the credential file");
    assert_eq!(online.bind_tcp, vec![8080], "the episode's bind ports survive executable narrowing");
}

/// docs/sandbox.md "What is compiled": a ruleset only narrows, so an episode
/// reserves execute on the configured executable of every program below it.
/// Without the reservation a child, a grandchild, or a workflow model node
/// cannot run the tool its own configuration names.
#[test]
fn an_episode_reserves_the_configured_executables_of_every_program_below_it() {
    let dir = temp_dir("descendant-policies");
    let paths: Vec<PathBuf> = ["own", "childs", "grandchilds", "nodes"]
        .into_iter()
        .map(|name| {
            let path = dir.join(name);
            std::fs::copy("/bin/true", &path).unwrap();
            path
        })
        .collect();
    let config: ProgramDocument = serde_json::from_value(serde_json::json!({
        "version": 3, "name": "p", "instructions": {"r": "x"}, "tools": ["own", "nodes"],
        "tool_defs": {
            "own": {"exec": paths[0], "description": "d"},
            "nodes": {"exec": paths[3], "description": "d"}
        },
        "grants": {"read": [dir], "spawn": ["kid"]},
        "budget": {"model_calls": 1},
        "programs": {"kid": {
            "name": "kid", "instructions": {"r": "x"}, "tools": ["childs"],
            "tool_defs": {"childs": {"exec": paths[1], "description": "d"}},
            "grants": {"read": [dir], "spawn": ["grandkid"]}, "budget": {"model_calls": 1},
            "programs": {"grandkid": {
                "name": "grandkid", "instructions": {"r": "x"}, "tools": ["grandchilds"],
                "tool_defs": {"grandchilds": {"exec": paths[2], "description": "d"}},
                "grants": {"read": [dir]}, "budget": {"model_calls": 1}
            }}
        }},
        "workflow": {"nodes": {"draft": {"terminal": true, "model": {
            "name": "draft", "instructions": {"r": "x"}, "tools": ["nodes"],
            "tool_defs": {"nodes": {"exec": paths[3], "description": "d"}},
            "grants": {"read": [dir]}, "budget": {"model_calls": 1}
        }}}},
        "task": "t"
    }))
    .unwrap();
    let resolved = foe_program::document::resolve(&config).unwrap();
    let executables = crate::executable::ExecutableTree::materialize(&resolved, &dir).unwrap();
    let p = Policy::for_episode(&resolved, &executables, Path::new("/logs/ep"));
    assert_eq!(p.exec_files.len(), paths.len(), "the ancestor reserves every reachable committed executable");
    let keys: Vec<_> = executables.reachable_entries().into_iter().map(|(key, _)| key).collect();
    assert_eq!(
        keys,
        [
            "tool_defs.nodes.exec",
            "tool_defs.own.exec",
            "workflow.nodes.draft.model.tool_defs.nodes.exec",
            "programs.kid.tool_defs.childs.exec",
            "programs.kid.programs.grandkid.tool_defs.grandchilds.exec",
        ]
    );
    assert_eq!(executables.child("kid").unwrap().reachable().len(), 2);
}

/// docs/sandbox.md "Executables": the reservation is what lets a descendant
/// start its configured executable inside the domain it inherits.
#[test]
fn a_descendant_executable_starts_inside_the_domain_the_ancestor_reserved() {
    let Some(s) = sandbox() else { return };
    let dir = temp_dir("descendant-exec");
    let tool = dir.join("tool");
    std::fs::write(&tool, "#!/bin/sh\nexit 0\n").unwrap();
    std::fs::set_permissions(&tool, std::os::unix::fs::PermissionsExt::from_mode(0o755)).unwrap();
    let config: ProgramDocument = serde_json::from_value(serde_json::json!({
        "version": 3, "name": "p", "instructions": {"r": "x"}, "tools": ["block"],
        "grants": {"read": [dir], "spawn": ["kid"]}, "budget": {"model_calls": 1},
        "programs": {"kid": {
            "name": "kid", "instructions": {"r": "x"}, "tools": ["t"],
            "tool_defs": {"t": {"exec": tool, "description": "d"}},
            "grants": {"read": [dir]}, "budget": {"model_calls": 1}
        }},
        "task": "t"
    }))
    .unwrap();
    let program = foe_program::document::resolve(&config).unwrap();
    let executables = crate::executable::ExecutableTree::materialize(&program, &dir).unwrap();
    let ancestor = Policy::for_episode(&program, &executables, &dir);
    let executable = executables.child("kid").unwrap().tools["t"].clone();
    let outer = ancestor.clone();
    let result = s
        .run_narrowed(&ancestor, move || {
            let executor = crate::exec::LocalExecutor::new(
                std::sync::Arc::new(s),
                outer,
                dir.join("spill"),
                std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false)),
            );
            crate::Executor::run(
                &executor,
                crate::ExecRequest {
                    program: tool,
                    executable: Some(executable),
                    args: Vec::new(),
                    cwd: dir,
                    env: std::collections::BTreeMap::new(),
                    timeout: std::time::Duration::from_secs(5),
                    network: false,
                    stdin: None,
                    policy: None,
                    pass_fds: Vec::new(),
                },
            )
        })
        .unwrap()
        .unwrap();
    assert_eq!(result.exit_code, Some(0));
}
