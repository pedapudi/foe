use super::*;
use foe_contract::ContractDocument;
use std::io::Write;

fn sandbox() -> Option<Sandbox> {
    let s = Sandbox::new(SandboxMode::BestEffort).unwrap();
    if s.abi() == 0 {
        eprintln!("skipped: the kernel offers no Landlock");
        return None;
    }
    Some(s)
}

fn temp_dir(name: &str) -> crate::test_util::ScratchDir {
    crate::exec::tests::scratch("sandbox", name)
}

fn policy(config: &ContractDocument, log_dir: &Path) -> Policy {
    let contract = foe_contract::document::resolve(config).unwrap();
    let executables = crate::captured_executable::CapturedExecutableTree::materialize(
        &contract,
        Path::new("/tmp/foe-sandbox-episode"),
    )
    .unwrap();
    Policy::for_episode(&contract, &executables, log_dir).unwrap()
}

#[test]
fn off_records_zero_and_required_needs_landlock() {
    assert_eq!(Sandbox::new(SandboxMode::Off).unwrap().abi(), 0);
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
    assert_eq!(s.abi(), probe_abi().min(MAX_ABI));
    eprintln!("landlock abi in use: {}", s.abi());
}

#[test]
fn read_roots_are_readable_and_other_paths_are_not() {
    let Some(s) = sandbox() else { return };
    let inside = temp_dir("inside");
    let outside = temp_dir("outside");
    std::fs::write(inside.join("a"), b"a").unwrap();
    std::fs::write(outside.join("b"), b"b").unwrap();
    let policy = Policy { read: vec![inside.to_path_buf()], ..Policy::default() };
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
    let policy = Policy { write: vec![dir.to_path_buf()], ..Policy::default() };
    let ok = s
        .run_narrowed(&policy, || {
            std::fs::File::create(dir.join("f")).and_then(|mut f| f.write_all(b"x")).is_ok()
                && std::fs::create_dir(dir.join("d")).is_ok()
                && std::fs::remove_file(dir.join("f")).is_ok()
        })
        .unwrap();
    assert!(ok);
}

/// docs/sandbox.md "Executables": a dynamically linked executable receives
/// its exact ELF interpreter while general executable directories remain
/// outside the policy.
#[test]
fn a_dynamic_executable_starts_without_general_binary_directories() {
    let Some(s) = sandbox() else { return };
    let policy = Policy::for_runtime_executable(Path::new("/bin/true"), Vec::new(), "test binary").unwrap();
    assert!(policy.exec.contains(&std::fs::canonicalize("/bin/true").unwrap()));
    assert!(policy.exec.iter().any(|path| path.file_name().is_some_and(|name| name.to_string_lossy().contains("ld-"))));
    assert!(!policy.exec.iter().any(|path| path == Path::new("/bin") || path == Path::new("/usr/bin")));
    let status = s.spawn_narrowed(&policy, Command::new("/bin/true")).unwrap().wait().unwrap();
    assert!(status.success());
}

/// docs/sandbox.md "Executables": the interpreter named by a direct
/// shebang can start, while a different binary used by the script remains
/// unavailable without an explicit execute grant.
#[test]
fn a_script_gets_its_interpreter_without_unrelated_binary_access() {
    let Some(s) = sandbox() else { return };
    let dir = temp_dir("script-interpreter");
    let script = dir.join("probe");
    std::fs::write(&script, "#!/bin/sh\n/usr/bin/true\n").unwrap();
    std::fs::set_permissions(&script, std::os::unix::fs::PermissionsExt::from_mode(0o755)).unwrap();
    let policy = Policy::for_runtime_executable(&script, vec![dir.to_path_buf()], "declared script").unwrap();
    assert!(policy.exec.contains(&std::fs::canonicalize("/bin/sh").unwrap()));
    assert!(!policy.exec.contains(&std::fs::canonicalize("/usr/bin/true").unwrap()));
    let status = s.spawn_narrowed(&policy, Command::new(&script)).unwrap().wait().unwrap();
    assert!(!status.success(), "the script started, but its unrelated subprocess must be denied");
}

#[test]
fn executable_policy_keeps_only_its_own_file() {
    let Some(s) = sandbox() else { return };
    let dir = temp_dir("exec");
    let other = dir.join("true");
    std::fs::copy("/bin/true", &other).unwrap();
    let mut episode =
        Policy { read: vec![dir.to_path_buf()], cleanup: vec![dir.join("runtime-only")], ..Policy::default() };
    episode.add_executable(Path::new("/bin/sh"), "test shell".into()).unwrap();
    episode.add_executable(&other, "other test executable".into()).unwrap();
    let tool = episode.for_executable(Path::new("/bin/sh"), false).unwrap();
    assert!(tool.exec.contains(&std::fs::canonicalize("/bin/sh").unwrap()));
    assert!(!tool.exec.contains(&other));
    assert!(tool.cleanup.is_empty());
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
    let tool = episode.for_executable(Path::new("/bin/sh"), false).unwrap();
    assert!(tool.exec.contains(&std::fs::canonicalize("/bin/sh").unwrap()));
    assert!(tool.exec.contains(&helper));
    let mut cmd = Command::new("/bin/sh");
    cmd.arg("-c").arg(helper.display().to_string()).env_clear();
    let status = s.spawn_narrowed(&tool, cmd).unwrap().wait().unwrap();
    assert!(status.success(), "the explicit subprocess grant survives executable narrowing");
}

/// docs/sandbox.md "Executables": cleanup carries read and removal alone,
/// so an episode can enumerate and delete its own runtime-owned directory
/// while a sibling directory beneath the same shared parent stays outside
/// its read and write envelope.
#[test]
fn cleanup_removes_the_owned_directory_without_reaching_a_sibling() {
    let Some(s) = sandbox() else { return };
    let parent = temp_dir("cleanup-shared-parent");
    let store = parent.join("store");
    std::fs::create_dir_all(store.join("0")).unwrap();
    std::fs::write(store.join("0").join("captured"), b"x").unwrap();
    let sibling_log = parent.join("sibling-episode").join("log");
    std::fs::create_dir(parent.join("sibling-episode")).unwrap();
    std::fs::write(&sibling_log, b"evidence").unwrap();
    let mut policy = Policy::default();
    policy.add_cleanup(store.clone(), "test store");
    let (own, created, log) = (store.clone(), parent.join("created"), sibling_log.clone());
    let (write_denied, read_denied, create_denied, removed) = s
        .run_narrowed(&policy, move || {
            (
                std::fs::write(&log, b"tampered").is_err(),
                std::fs::read(&log).is_err(),
                std::fs::create_dir(&created).is_err(),
                std::fs::remove_dir_all(&own).is_ok(),
            )
        })
        .unwrap();
    assert!(write_denied, "a sibling's file cannot be written");
    assert!(read_denied, "a sibling's file cannot be read");
    assert!(create_denied, "nothing can be created beneath the shared parent");
    assert!(removed, "the owned directory is enumerated and removed");
    assert!(!store.exists());
    assert_eq!(std::fs::read(&sibling_log).unwrap(), b"evidence");
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
    let config: ContractDocument = serde_json::from_value(serde_json::json!({
        "version": 4, "name": "network", "instructions": {"role": "x"}, "tools": ["block"],
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
    let config: ContractDocument = serde_json::from_value(serde_json::json!({
        "version": 4, "name": "server", "instructions": {"role": "x"}, "tools": ["block"],
        "grants": {"read": ["/tmp"], "bind": [port]}, "budget": {"model_calls": 1}, "task": "t"
    }))
    .unwrap();
    let tool = policy(&config, Path::new("/logs/ep")).for_executable(Path::new("/bin/sh"), false).unwrap();
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
    let config: ContractDocument = serde_json::from_value(serde_json::json!({
        "version": 4, "name": "p", "instructions": {"r": "x"}, "tools": ["ruff"],
        "tool_defs": {"ruff": {"exec": tool, "description": "d"}},
        "grants": {"read": [dir], "write": [out], "execute": ["/bin/sh"], "bind": [8080]},
        "budget": {"model_calls": 1}, "task": "t"
    }))
    .unwrap();
    let p = policy(&config, Path::new("/logs/ep"));
    assert_eq!(p.read, vec![dir.to_path_buf()]);
    assert_eq!(p.write, vec![out]);
    let shell = std::fs::canonicalize("/bin/sh").unwrap();
    assert_eq!(p.delegated_exec, vec![shell.clone()]);
    assert!(p.exec.contains(&shell));
    assert!(!p.exec.contains(&tool), "configured executables are authorized by their committed inode");
    assert_eq!(p.exec_files.len(), 1);
    assert_eq!(p.cleanup.len(), 1, "the episode owns private executable cleanup");
    assert_eq!(p.cleanup_parents, vec![p.cleanup[0].parent().unwrap().to_path_buf()]);
    let rows = p.resolved_permissions();
    let (store, parent) = (p.cleanup[0].to_string_lossy(), p.cleanup_parents[0].to_string_lossy());
    assert!(rows.write.iter().any(|entry| entry.path == store && entry.reason.contains("removal only")));
    assert!(
        rows.write.iter().all(|entry| entry.path != parent || entry.reason.contains("directory removal alone")),
        "the report claims nothing on the shared parent beyond directory removal"
    );
    assert_eq!(p.log_dir, Some(PathBuf::from("/logs/ep")));
    assert_eq!(p.bind_tcp, vec![8080], "the episode may bind the granted port");
    assert!(!p.connect_tcp, "an episode without a model block holds no transport");
    let resolver: Vec<PathBuf> = std::fs::canonicalize("/etc/resolv.conf").into_iter().collect();
    assert!(p.read_files.is_empty(), "an episode that opens no connection reads no resolver file");
    let mut with_children = config.clone();
    with_children.grants.spawn = vec!["survey".into()];
    with_children.child_contracts.insert(
        "survey".into(),
        serde_json::from_value(serde_json::json!({
            "name": "survey", "instructions": {"r": "x"}, "tools": ["block"],
            "grants": {"read": [dir]}, "budget": {"model_calls": 1}
        }))
        .unwrap(),
    );
    with_children.model = Some(foe_contract::ModelConfig::new("anthropic", "m"));
    let mut p = policy(&with_children, Path::new("/logs/ep"));
    assert_eq!(p.read_files, resolver, "the credential file is appended by the binary after resolution");
    if let Ok(binary) = std::env::current_exe() {
        assert!(p.exec.contains(&binary), "the binary starts children");
    }
    assert!(p.connect_tcp);
    p.read_files.push(PathBuf::from("/keys/anthropic"));
    let offline = p.for_executable(Path::new("/bin/sh"), false).unwrap();
    assert!(offline.cleanup.is_empty(), "a configured executable cannot reach runtime storage");
    assert!(offline.read_files.is_empty(), "an executable without network reads no resolver file");
    let online = p.for_executable(Path::new("/bin/sh"), true).unwrap();
    assert_eq!(online.read_files, resolver, "an executable with network keeps the resolver file");
    assert!(!online.read_files.contains(&PathBuf::from("/keys/anthropic")), "and never the credential file");
    assert_eq!(online.bind_tcp, vec![8080], "the episode's bind ports survive executable narrowing");
}

/// docs/sandbox.md "Resolved permissions and enforcement": a captured
/// executable's row names the captured image by digest, because the
/// compiled rule binds the retained copy rather than any pathname; the
/// construction-time source appears only as provenance in the reason.
#[test]
fn a_captured_executable_row_names_the_image_not_its_source() {
    let dir = temp_dir("captured-row");
    let tool = dir.join("ruff");
    std::fs::copy("/bin/true", &tool).unwrap();
    let config: ContractDocument = serde_json::from_value(serde_json::json!({
        "version": 4, "name": "p", "instructions": {"r": "x"}, "tools": ["ruff"],
        "tool_defs": {"ruff": {"exec": tool, "description": "d"}},
        "grants": {"read": [dir]}, "budget": {"model_calls": 1}, "task": "t"
    }))
    .unwrap();
    let access = policy(&config, Path::new("/logs/ep")).resolved_permissions();
    let source = std::fs::canonicalize(&tool).unwrap();
    let row = access.execute.iter().find(|entry| entry.sha256.is_some()).unwrap();
    let digest = row.sha256.as_ref().unwrap();
    assert_eq!(row.path, format!("captured:{digest}"), "the row names the image the rule binds");
    assert!(row.reason.contains("tool_defs.ruff"), "{}", row.reason);
    assert!(row.reason.contains(&source.display().to_string()), "the source is provenance: {}", row.reason);
    assert!(access.execute.iter().all(|entry| Path::new(&entry.path) != source), "the source path is not executable");
}

/// A runtime control file that cannot be opened compiles no rule, so it is
/// reported as absent rather than granted; an openable one keeps its row.
#[test]
fn an_unopenable_runtime_control_file_is_reported_as_absent() {
    let dir = temp_dir("control-file");
    let mut policy = Policy::default();
    policy.add_runtime_control_file(dir.join("gone").join("cgroup.procs"));
    assert!(policy.runtime_control_files.is_empty());
    assert!(policy.resolved_permissions().write.is_empty());
    let procs = dir.join("cgroup.procs");
    std::fs::write(&procs, "").unwrap();
    policy.add_runtime_control_file(procs.clone());
    assert_eq!(policy.runtime_control_files, vec![procs.clone()]);
    let rows = policy.resolved_permissions();
    assert!(rows.write.iter().any(|entry| Path::new(&entry.path) == procs));
}

/// docs/sandbox.md "What is compiled": a ruleset only narrows, so an episode
/// reserves execute on the configured executable of every contract below it.
/// Without the reservation a child, a grandchild, or a workflow model node
/// cannot run the tool its own configuration names.
#[test]
fn an_episode_reserves_the_configured_executables_of_every_contract_below_it() {
    let dir = temp_dir("descendant-policies");
    let paths: Vec<PathBuf> = ["own", "childs", "grandchilds", "nodes"]
        .into_iter()
        .map(|name| {
            let path = dir.join(name);
            std::fs::copy("/bin/true", &path).unwrap();
            path
        })
        .collect();
    let config: ContractDocument = serde_json::from_value(serde_json::json!({
        "version": 4, "name": "p", "instructions": {"r": "x"}, "tools": ["own", "nodes"],
        "tool_defs": {
            "own": {"exec": paths[0], "description": "d"},
            "nodes": {"exec": paths[3], "description": "d"}
        },
        "grants": {"read": [dir], "spawn": ["kid"]},
        "budget": {"model_calls": 1},
        "child_contracts": {"kid": {
            "name": "kid", "instructions": {"r": "x"}, "tools": ["childs"],
            "tool_defs": {"childs": {"exec": paths[1], "description": "d"}},
            "grants": {"read": [dir], "spawn": ["grandkid"]}, "budget": {"model_calls": 1},
            "child_contracts": {"grandkid": {
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
    let resolved = foe_contract::document::resolve(&config).unwrap();
    let executables = crate::captured_executable::CapturedExecutableTree::materialize(&resolved, &dir).unwrap();
    let p = Policy::for_episode(&resolved, &executables, Path::new("/logs/ep")).unwrap();
    assert_eq!(p.exec_files.len(), paths.len(), "the ancestor reserves every reachable captured executable");
    let keys: Vec<_> = executables.reachable_entries().into_iter().map(|(key, _)| key).collect();
    assert_eq!(
        keys,
        [
            "contract.tool_defs.nodes.exec",
            "contract.tool_defs.own.exec",
            "contract.child_contracts.kid.tool_defs.childs.exec",
            "contract.child_contracts.kid.child_contracts.grandkid.tool_defs.grandchilds.exec",
            "contract.workflow.nodes.draft.model.tool_defs.nodes.exec",
        ]
    );
    assert_eq!(executables.child("kid").unwrap().reachable().len(), 2);
}

/// docs/sandbox.md "Children": the ancestor envelope uses the same
/// reachability rule as contract fingerprinting and planning. It reserves explicit
/// execute and outbound network access for reachable child and workflow
/// contracts, while an unspawned declaration contributes nothing.
#[test]
fn an_ancestor_reserves_only_reachable_descendant_execute_and_network_access() {
    let dir = temp_dir("descendant-access");
    let child_tool = dir.join("child-tool");
    std::fs::copy("/bin/true", &child_tool).unwrap();
    let child_exec = dir.join("child-exec");
    let workflow_exec = dir.join("workflow-exec");
    let unreachable_exec = dir.join("unreachable-exec");
    for path in [&child_exec, &workflow_exec, &unreachable_exec] {
        std::fs::copy("/bin/true", path).unwrap();
    }
    let config: ContractDocument = serde_json::from_value(serde_json::json!({
        "version": 4, "name": "root", "instructions": {"role": "host"}, "tools": ["block"],
        "grants": {"read": [dir], "execute": [dir], "spawn": ["child"]},
        "budget": {"model_calls": 4, "max_episodes": 4},
        "child_contracts": {
            "child": {
                "name": "child", "instructions": {"role": "call a network tool"}, "tools": ["remote"],
                "tool_defs": {"remote": {"exec": child_tool, "description": "remote", "network": true}},
                "grants": {"read": [dir], "execute": [child_exec]}, "budget": {"model_calls": 1}
            },
            "unreachable": {
                "name": "unreachable", "instructions": {"role": "unused"}, "tools": ["block"],
                "model": {"provider": "openai", "model": "unused"},
                "grants": {"read": [dir], "execute": [unreachable_exec]}, "budget": {"model_calls": 1}
            }
        },
        "workflow": {"nodes": {"review": {"terminal": true, "model": {
            "name": "review", "instructions": {"role": "review"}, "tools": ["block"],
            "model": {"provider": "openai", "model": "review"},
            "grants": {"read": [dir], "execute": [workflow_exec]}, "budget": {"model_calls": 1}
        }}}},
        "task": "test"
    }))
    .unwrap();
    let root = foe_contract::document::resolve(&config).unwrap();
    let access = Policy::for_plan(&root).unwrap().resolved_permissions();
    let executes = |path: &Path| access.execute.iter().any(|entry| Path::new(&entry.path) == path);
    assert!(executes(&dir.canonicalize().unwrap()), "a directory execute grant is reported as the directory");
    assert!(executes(&child_exec.canonicalize().unwrap()));
    assert!(executes(&workflow_exec.canonicalize().unwrap()));
    assert!(!access.execute.iter().any(|entry| entry.reason.contains("unreachable")));
    assert!(access.connect_tcp.iter().any(|reason| reason.contains("contract.child_contracts.child.tool_defs.remote")));
    assert!(access.connect_tcp.iter().any(|reason| reason.contains("contract.workflow.nodes.review.model")));
    assert!(!access.connect_tcp.iter().any(|reason| reason.contains("unreachable")));

    let child = &root.child_contracts["child"];
    let child_access = Policy::for_plan(child).unwrap().resolved_permissions();
    assert!(child_access.connect_tcp.iter().any(|reason| reason.contains("tool_defs.remote")));
    assert!(!child_access.execute.iter().any(|entry| Path::new(&entry.path) == workflow_exec));
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
    let config: ContractDocument = serde_json::from_value(serde_json::json!({
        "version": 4, "name": "p", "instructions": {"r": "x"}, "tools": ["block"],
        "grants": {"read": [dir], "spawn": ["kid"]}, "budget": {"model_calls": 1},
        "child_contracts": {"kid": {
            "name": "kid", "instructions": {"r": "x"}, "tools": ["t"],
            "tool_defs": {"t": {"exec": tool, "description": "d"}},
            "grants": {"read": [dir]}, "budget": {"model_calls": 1}
        }},
        "task": "t"
    }))
    .unwrap();
    let contract = foe_contract::document::resolve(&config).unwrap();
    let executables = crate::captured_executable::CapturedExecutableTree::materialize(&contract, &dir).unwrap();
    let ancestor = Policy::for_episode(&contract, &executables, &dir).unwrap();
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
                    command: tool,
                    captured_executable: Some(executable),
                    args: Vec::new(),
                    cwd: dir.to_path_buf(),
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
