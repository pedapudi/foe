use super::*;
use crate::budget::Pool;
use crate::exec::tests::scratch;
use crate::loop_::Log;
use crate::spawn::tests::{parent_config, wait_for};
use foe_log::{EpisodeStart, EventData, Outcome, RuntimeInfo, SandboxInfo, SandboxMode};
use std::path::Path;

fn sessions(name: &str, limit: usize) -> (LocalSessions, PathBuf) {
    let dir = scratch("session", name);
    let sandbox = Arc::new(Sandbox::new(SandboxMode::BestEffort).unwrap());
    let policy = Policy { exec: vec!["/bin/bash".into()], ..Policy::default() };
    (LocalSessions::new(sandbox, policy, dir.join("spill"), limit, false), dir)
}

fn shell(cwd: &Path, command: &str) -> SessionRequest {
    SessionRequest {
        name: "bash".into(),
        program: "/bin/bash".into(),
        args: vec!["-c".into(), command.into()],
        env: BTreeMap::new(),
        cwd: cwd.to_path_buf(),
        lifetime: SessionLifetime::Episode,
    }
}

/// docs/tools.md "session": a session outlives the call that started it,
/// a poll returns only the output since the last poll, and the exit status
/// arrives once the process has ended.
#[test]
fn a_session_outlives_calls_and_polls_return_only_new_output() {
    let (s, dir) = sessions("polls", 4);
    let started = s.start(shell(&dir, r#"read line; echo "got $line"; read more; echo "got $more"; exit 7"#)).unwrap();
    assert_eq!(started.id, 1);
    assert!(started.alive);
    s.write_stdin(1, b"one\n").unwrap();
    let mut first = Vec::new();
    wait_for(|| {
        first.extend(s.take_output(1).unwrap().1.stdout);
        first.ends_with(b"got one\n").then_some(())
    });
    assert_eq!(first, b"got one\n");
    let (status, empty) = s.take_output(1).unwrap();
    assert!(status.alive);
    assert!(empty.stdout.is_empty(), "a poll returns only output since the last poll");
    s.write_stdin(1, b"two\n").unwrap();
    let mut rest = Vec::new();
    let status = wait_for(|| {
        let (status, output) = s.take_output(1).unwrap();
        rest.extend(output.stdout);
        (!status.alive).then_some(status)
    });
    assert_eq!(rest, b"got two\n", "the take that observes the end carries the final bytes");
    assert_eq!(status.exit_code, Some(7));
    assert_eq!(subject(&status), format!("session 1: exit 7 after {}s", status.seconds));
}

/// docs/tools.md `session`: when more than the poll bound arrived, the poll
/// returns the newest bounded bytes and the evidence file retains the stream.
#[test]
fn a_poll_of_large_output_keeps_the_newest_bytes() {
    let dir = scratch("session", "bounded-output");
    let path = dir.join("stream.stdout");
    let mut bytes = vec![b'a'; crate::exec::CAPTURE_LIMIT];
    bytes.extend_from_slice(b"newest");
    std::fs::write(&path, &bytes).unwrap();
    let output = Output { path: path.clone(), offset: Mutex::new(0) };

    let taken = String::from_utf8(output.take()).unwrap();
    assert!(taken.contains("newest\n[output beyond"), "the bounded poll includes the newest bytes: {taken:?}");
    assert_eq!(std::fs::read(path).unwrap(), bytes, "the evidence file remains complete");
}

/// docs/tools.md "session": a session may bind a TCP port the policy's
/// `bind_tcp` lists — filled from `grants.bind` — and the bound listener
/// answers across calls while the session stays alive. The sandbox tests
/// cover what each ABI tier denies; this holds at every tier.
#[test]
fn a_session_serves_a_granted_bind_port_across_calls() {
    if !Path::new("/usr/bin/python3").exists() {
        eprintln!("skipped: /usr/bin/python3 is absent");
        return;
    }
    let dir = scratch("session", "bind");
    let probe = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let port = probe.local_addr().unwrap().port();
    drop(probe);
    let server = format!(
        "import socket\ns = socket.socket()\ns.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n\
         s.bind((\"127.0.0.1\", {port}))\ns.listen()\nprint(\"ready\", flush=True)\n\
         while True:\n    c, _ = s.accept()\n    c.sendall(b\"pong\")\n    c.close()\n"
    );
    std::fs::write(dir.join("server.py"), server).unwrap();
    let sandbox = Arc::new(Sandbox::new(SandboxMode::BestEffort).unwrap());
    let policy =
        Policy { read: vec![dir.clone()], exec: vec!["/bin/bash".into()], bind_tcp: vec![port], ..Policy::default() };
    let s = LocalSessions::new(sandbox, policy, dir.join("spill"), 4, false);
    s.start(shell(&dir, "/usr/bin/python3 server.py")).unwrap();
    wait_for(|| {
        let (_, output) = s.take_output(1).unwrap();
        (!output.stdout.is_empty()).then_some(())
    });
    let ping = || {
        let mut c = std::net::TcpStream::connect(("127.0.0.1", port)).unwrap();
        let mut buf = Vec::new();
        c.read_to_end(&mut buf).unwrap();
        buf
    };
    assert_eq!(ping(), b"pong");
    let (status, _) = s.take_output(1).unwrap();
    assert!(status.alive, "the listener holds between calls");
    assert_eq!(ping(), b"pong", "the bound listener answers again after a further call");
    s.stop(1).unwrap();
}

/// docs/tools.md "session": at most the configured number of sessions are
/// alive at once, and a stopped session frees its place.
#[test]
fn the_alive_bound_refuses_a_further_start() {
    let (s, dir) = sessions("limit", 1);
    s.start(shell(&dir, "sleep 30")).unwrap();
    let refused = s.start(shell(&dir, "sleep 30")).err().map(|e| e.to_string()).unwrap_or_default();
    assert!(refused.contains("session limit"), "{refused}");
    assert!(!s.stop(1).unwrap().alive);
    assert_eq!(s.start(shell(&dir, "sleep 30")).unwrap().id, 2, "a stopped session frees its place");
    s.stop(2).unwrap();
}

/// docs/tools.md "session": a named signal reaches the whole process group.
#[test]
fn a_named_signal_reaches_the_process_group() {
    let (s, dir) = sessions("signal", 4);
    s.start(shell(&dir, "trap 'exit 3' USR1; echo ready; while true; do sleep 0.1; done")).unwrap();
    // The signal waits for the readiness line, so it cannot arrive before
    // the shell has installed its trap.
    wait_for(|| {
        let (_, output) = s.take_output(1).unwrap();
        (!output.stdout.is_empty()).then_some(())
    });
    s.signal(1, "SIGUSR1").unwrap();
    let status = wait_for(|| {
        let (status, _) = s.take_output(1).unwrap();
        (!status.alive).then_some(status)
    });
    assert_eq!(status.exit_code, Some(3));
    let unknown = s.signal(1, "SIGNOPE").err().map(|e| e.to_string()).unwrap_or_default();
    assert!(unknown.contains("SIGNOPE"), "{unknown}");
}

/// docs/tools.md "session": stop escalates from SIGTERM to SIGKILL after
/// the grace bound, so a process that ignores SIGTERM still ends.
#[test]
fn stop_escalates_to_kill_when_the_grace_is_ignored() {
    let (s, dir) = sessions("escalate", 4);
    s.start(shell(&dir, "trap '' TERM; echo ready; while true; do sleep 0.1; done")).unwrap();
    wait_for(|| {
        let (_, output) = s.take_output(1).unwrap();
        (!output.stdout.is_empty()).then_some(())
    });
    let status = s.stop(1).unwrap();
    assert!(!status.alive);
    assert_eq!(status.exit_code, None, "SIGKILL ended a process that ignored SIGTERM");
    assert_eq!(subject(&status), format!("session 1: killed after {}s", status.seconds));
}

/// docs/tools.md "session": settlement stops episode-lifetime sessions. The
/// pattern of the executable-teardown tests: the session backgrounds a
/// child, and killing the session's group ends that child too.
#[test]
fn no_episode_process_survives_settlement() {
    let (s, dir) = sessions("teardown", 4);
    s.start(shell(&dir, "sleep 30 & echo $!; wait")).unwrap();
    let bytes = wait_for(|| {
        let (_, output) = s.take_output(1).unwrap();
        (!output.stdout.is_empty()).then_some(output.stdout)
    });
    let pid: u32 = String::from_utf8(bytes).unwrap().trim().parse().unwrap();
    let stopped = s.settle();
    assert_eq!(stopped.len(), 1);
    assert!(!stopped[0].status.alive);
    assert!(!stopped[0].released_to_task);
    let gone = (0..200).any(|_| {
        std::thread::sleep(std::time::Duration::from_millis(10));
        !Path::new(&format!("/proc/{pid}")).exists()
    });
    assert!(gone, "the backgrounded sleep was killed with the group");
    assert!(s.settle().is_empty(), "a second settlement finds no survivor");
}

/// docs/tools.md "session": liveness and settlement cover the process
/// group even when its original leader has exited.
#[test]
fn a_session_group_outlives_its_leader_and_still_settles() {
    let (s, dir) = sessions("leader-exit", 4);
    s.start(shell(&dir, "sleep 30 & echo $!; exit 7")).unwrap();
    let bytes = wait_for(|| {
        let (_, output) = s.take_output(1).unwrap();
        (!output.stdout.is_empty()).then_some(output.stdout)
    });
    let child: u32 = String::from_utf8(bytes).unwrap().trim().parse().unwrap();
    let status = wait_for(|| {
        let status = s.session(1).unwrap().status(1);
        s.session(1).unwrap().ended.lock().unwrap().is_some().then_some(status)
    });
    assert!(status.alive, "the session remains alive while its process group has a member");
    assert_eq!(status.exit_code, None, "a live group has no final exit status");
    let settled = s.settle();
    assert_eq!(settled.len(), 1);
    assert!(!settled[0].status.alive);
    assert_eq!(settled[0].status.exit_code, Some(7));
    let gone = (0..200).any(|_| {
        std::thread::sleep(std::time::Duration::from_millis(10));
        !Path::new(&format!("/proc/{child}")).exists()
    });
    assert!(gone, "settlement killed the remaining process-group member");
}

/// docs/tools.md "session": task lifetime requires grants.task_session.
/// Settlement records one ownership transfer and leaves the group alive for
/// the enclosing task environment.
#[test]
fn a_task_session_requires_authority_and_survives_settlement() {
    let (denied, dir) = sessions("task-denied", 4);
    let mut req = shell(&dir, "sleep 30");
    req.lifetime = SessionLifetime::Task;
    let error = denied.start(req).unwrap_err().to_string();
    assert!(error.contains("grants.task_session"), "{error}");

    let dir = scratch("session", "task-release");
    let sandbox = Arc::new(Sandbox::new(SandboxMode::BestEffort).unwrap());
    let policy = Policy { exec: vec!["/bin/bash".into()], ..Policy::default() };
    let sessions = LocalSessions::new(sandbox, policy, dir.join("spill"), 4, true);
    let mut req = shell(&dir, "echo ready; sleep 30");
    req.lifetime = SessionLifetime::Task;
    sessions.start(req).unwrap();
    wait_for(|| {
        let (_, output) = sessions.take_output(1).unwrap();
        output.stdout.ends_with(b"ready\n").then_some(())
    });
    let released = sessions.settle();
    assert_eq!(released.len(), 1);
    assert!(released[0].released_to_task);
    assert!(released[0].status.alive);
    assert_eq!(released[0].pid as i32, released[0].process_group);
    assert!(Path::new(&format!("/proc/{}", released[0].pid)).exists());
    assert!(sessions.settle().is_empty(), "ownership transfers once");
    sessions.stop(1).unwrap();
}

/// `Sessions::take_exited` reports each session's end exactly once, whether
/// the process exited on its own or a stop ended it, and never a live one.
#[test]
fn take_exited_reports_each_end_once() {
    let (s, dir) = sessions("exited", 4);
    s.start(shell(&dir, "exit 5")).unwrap();
    let status = wait_for(|| s.take_exited().pop());
    assert_eq!((status.id, status.exit_code), (1, Some(5)));
    assert!(s.take_exited().is_empty(), "an exit is reported once per lifetime");
    s.start(shell(&dir, "sleep 30")).unwrap();
    assert!(s.take_exited().is_empty(), "a live session is not an exit");
    s.stop(2).unwrap();
    let stopped = s.take_exited();
    assert_eq!(stopped.len(), 1, "a stopped session is an exit");
    assert_eq!(stopped[0].id, 2);
    assert!(s.take_exited().is_empty());
}

fn start() -> EpisodeStart {
    EpisodeStart {
        id: "ep_root".into(),
        parent_id: None,
        fork_origin: None,
        team_id: None,
        program: serde_json::json!({}),
        identity: "sha256:0".into(),
        task: "t".into(),
        runtime: RuntimeInfo { version: "0".into(), build: "unknown".into() },
        sandbox: SandboxInfo { mode: SandboxMode::Off, landlock_abi: 0 },
    }
}

/// docs/log-format.md "Open obligations": the termination of a surviving
/// session at settlement is recorded as the ordinary result of the
/// implicit stop — synthetic, closing nothing — and the log stays valid.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn settlement_records_the_implicit_stop_and_the_log_stays_valid() {
    let (s, dir) = sessions("settle", 4);
    s.start(shell(&dir, "sleep 30")).unwrap();
    let log_dir = dir.join("log");
    std::fs::create_dir_all(&log_dir).unwrap();
    let log = Arc::new(Log::create_or_open(&log_dir, None).unwrap());
    log.append(EventData::EpisodeStart(start())).unwrap();
    let pool = Arc::new(Mutex::new(Pool::new(parent_config().budget)));
    let sessions: Arc<dyn Sessions> = Arc::new(s);
    crate::loop_::settle(&log, &pool, None, Some(sessions.clone())).await.unwrap();
    log.append(EventData::EpisodeEnd { outcome: Outcome::Completed { value: serde_json::Value::Null } }).unwrap();
    let events = log.events();
    let result = events
        .iter()
        .find_map(|e| match &e.data {
            EventData::ToolResult(r) => Some(r.clone()),
            _ => None,
        })
        .expect("settlement wrote the implicit stop's result");
    assert!(result.synthetic);
    assert_eq!(result.name, SESSION_TOOL);
    assert_eq!(result.call_id, "session-1-settle");
    assert!(result.value.get("lifetime").is_none(), "the default settlement value remains compatible");
    assert!(result.subject.as_deref().unwrap_or_default().starts_with("session 1: killed after"), "{result:?}");
    let item = events
        .iter()
        .find_map(|e| match &e.data {
            EventData::InboxItem(i) => Some(i.clone()),
            _ => None,
        })
        .expect("settlement posted the exit as a session inbox item");
    assert_eq!(item.source, foe_log::InboxSource::Session);
    assert_eq!(item.from.as_deref(), Some("1"));
    let foe_log::ContentBlock::Text { text } = &item.content[0] else { panic!() };
    assert!(text.starts_with("session 1: killed after"), "{text}");
    foe_log::fold::fold(&events).expect("the log is well-formed");
    assert!(sessions.settle().is_empty(), "settlement left nothing alive");
}

/// docs/log-format.md `tool/result`: settlement records a task-lifetime
/// session as released while alive, with the process identity and the new
/// owner. The release produces no exit inbox item.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn settlement_records_task_ownership_without_stopping_the_process() {
    let dir = scratch("session", "task-release-log");
    let sandbox = Arc::new(Sandbox::new(SandboxMode::BestEffort).unwrap());
    let policy = Policy { exec: vec!["/bin/bash".into()], ..Policy::default() };
    let sessions = Arc::new(LocalSessions::new(sandbox, policy, dir.join("spill"), 4, true));
    let mut request = shell(&dir, "sleep 30 & echo $!; exit 7");
    request.lifetime = SessionLifetime::Task;
    sessions.start(request).unwrap();
    let child: u32 = String::from_utf8(wait_for(|| {
        let (_, output) = sessions.take_output(1).unwrap();
        (!output.stdout.is_empty()).then_some(output.stdout)
    }))
    .unwrap()
    .trim()
    .parse()
    .unwrap();
    let status = wait_for(|| {
        let status = sessions.session(1).unwrap().status(1);
        sessions.session(1).unwrap().ended.lock().unwrap().is_some().then_some(status)
    });
    assert!(status.alive, "the released group remains alive after its leader exits");
    let log_dir = dir.join("log");
    std::fs::create_dir_all(&log_dir).unwrap();
    let log = Arc::new(Log::create_or_open(&log_dir, None).unwrap());
    log.append(EventData::EpisodeStart(start())).unwrap();
    let pool = Arc::new(Mutex::new(Pool::new(parent_config().budget)));
    crate::loop_::settle(&log, &pool, None, Some(sessions.clone())).await.unwrap();
    log.append(EventData::EpisodeEnd { outcome: Outcome::Completed { value: serde_json::Value::Null } }).unwrap();

    let events = log.events();
    let result = events
        .iter()
        .find_map(|event| match &event.data {
            EventData::ToolResult(result) => Some(result),
            _ => None,
        })
        .expect("settlement recorded the task ownership transfer");
    assert!(result.synthetic);
    assert_eq!(result.value["lifetime"], "task");
    assert_eq!(result.value["disposition"], "released_to_task_environment");
    assert_eq!(result.value["alive"], true, "the log states the observed release state");
    assert!(result.value["pid"].as_u64().is_some());
    assert!(result.value["process_group"].as_i64().is_some());
    assert!(Path::new(&format!("/proc/{child}")).exists(), "the released process-group member remains alive");
    assert!(result.rendered.contains("released to task environment"));
    assert!(!events.iter().any(|event| matches!(event.data, EventData::InboxItem(_))));
    foe_log::fold::fold(&events).expect("the ownership record replays without the process");
    assert_eq!(sessions.stop(1).unwrap().exit_code, Some(7));
    let gone = (0..200).any(|_| {
        std::thread::sleep(std::time::Duration::from_millis(10));
        !Path::new(&format!("/proc/{child}")).exists()
    });
    assert!(gone, "the enclosing task environment can stop a released group after its leader exits");
}
