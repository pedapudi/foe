use super::*;
use crate::testing::{ctx, ctx_with_sessions, Fixture};
use foe_core::{CapError, SessionLifetime, SessionSettlement, Sessions};
use std::sync::{Arc, Mutex};

/// Answers from a scripted status and records every call.
struct FakeSessions {
    status: SessionStatus,
    started: Mutex<Vec<SessionRequest>>,
    calls: Mutex<Vec<String>>,
    output: Mutex<SessionOutput>,
}

impl FakeSessions {
    fn new(status: SessionStatus) -> Self {
        Self {
            status,
            started: Mutex::new(Vec::new()),
            calls: Mutex::new(Vec::new()),
            output: Mutex::new(SessionOutput::default()),
        }
    }
    fn with_output(self, stdout: &str, stderr: &str) -> Self {
        *self.output.lock().unwrap() = SessionOutput { stdout: stdout.into(), stderr: stderr.into() };
        self
    }
}

impl Sessions for FakeSessions {
    fn start(&self, req: SessionRequest) -> Result<SessionStatus, CapError> {
        self.started.lock().unwrap().push(req);
        Ok(self.status.clone())
    }
    fn take_output(&self, _id: u64) -> Result<(SessionStatus, SessionOutput), CapError> {
        Ok((self.status.clone(), std::mem::take(&mut *self.output.lock().unwrap())))
    }
    fn write_stdin(&self, id: u64, bytes: &[u8]) -> Result<SessionStatus, CapError> {
        self.calls.lock().unwrap().push(format!("write {id} {}", bytes.len()));
        Ok(self.status.clone())
    }
    fn signal(&self, id: u64, signal: &str) -> Result<SessionStatus, CapError> {
        self.calls.lock().unwrap().push(format!("signal {id} {signal}"));
        Ok(self.status.clone())
    }
    fn stop(&self, id: u64) -> Result<SessionStatus, CapError> {
        self.calls.lock().unwrap().push(format!("stop {id}"));
        Ok(self.status.clone())
    }
    fn settle(&self) -> Vec<SessionSettlement> {
        Vec::new()
    }
}

fn alive(id: u64, name: &str) -> SessionStatus {
    SessionStatus { id, name: name.into(), alive: true, exit_code: None, seconds: 3 }
}

fn dead(id: u64, name: &str, exit_code: Option<i32>, seconds: u64) -> SessionStatus {
    SessionStatus { id, name: name.into(), alive: false, exit_code, seconds }
}

#[tokio::test]
async fn start_runs_the_command_under_the_bash_contract() {
    let fx = Fixture::new();
    let fake = Arc::new(FakeSessions::new(alive(1, "postgres")));
    let c = ctx_with_sessions(&fx, fake.clone());
    let v = Session::new().call(json!({"action": "start", "command": "postgres -D data"}), &c).await;
    assert!(!v.is_error, "{v:?}");
    assert_eq!(v.value["session"], 1);
    assert_eq!(v.value["command"], "postgres -D data");
    assert_eq!(v.rendered.as_deref(), Some("[session 1: postgres \u{b7} started]\n"));
    assert_eq!(v.subject.as_deref(), Some("session 1: postgres \u{b7} started"));
    let req = fake.started.lock().unwrap().pop().unwrap();
    assert_eq!(req.command, std::path::PathBuf::from("/bin/bash"));
    assert_eq!(req.args, ["-c", "postgres -D data"]);
    assert_eq!(req.name, "postgres");
    assert_eq!(req.lifetime, SessionLifetime::Episode);
    assert_eq!(req.cwd, fx.root());
    assert_eq!(req.env["PATH"], "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin");
    assert_eq!(req.env["HOME"], fx.root().display().to_string());
    assert_eq!(req.env["LANG"], "C.UTF-8");
}

#[tokio::test]
async fn start_rejects_a_literal_nul_before_opening_a_session() {
    let fx = Fixture::new();
    let fake = Arc::new(FakeSessions::new(alive(1, "server")));
    let c = ctx_with_sessions(&fx, fake.clone());
    let v = Session::new().call(json!({"action": "start", "command": "server\0arg"}), &c).await;
    assert!(v.is_error);
    assert!(v.rendered.unwrap().contains("printf '\\0'"));
    assert!(fake.started.lock().unwrap().is_empty());
}

#[tokio::test]
async fn start_passes_the_explicit_task_lifetime_to_the_capability() {
    let fx = Fixture::new();
    let fake = Arc::new(FakeSessions::new(alive(1, "server")));
    let c = ctx_with_sessions(&fx, fake.clone());
    let value = Session::new().call(json!({"action": "start", "command": "server", "lifetime": "task"}), &c).await;
    assert!(!value.is_error, "{value:?}");
    assert_eq!(value.value["lifetime"], "task");
    assert_eq!(value.rendered.as_deref(), Some("[session 1: server · task lifetime started]\n"));
    assert_eq!(fake.started.lock().unwrap()[0].lifetime, SessionLifetime::Task);

    let invalid = Session::new().call(json!({"action": "poll", "session": 1, "lifetime": "task"}), &c).await;
    assert!(invalid.is_error);
    assert!(invalid.rendered.unwrap().contains("applies only to `start`"));
}

#[tokio::test]
async fn poll_leads_with_the_status_and_counts_the_new_lines() {
    let fx = Fixture::new();
    let fake = Arc::new(FakeSessions::new(alive(2, "postgres")).with_output("ready\n", "warn\n"));
    let c = ctx_with_sessions(&fx, fake);
    let v = Session::new().call(json!({"action": "poll", "session": 2}), &c).await;
    assert!(!v.is_error, "{v:?}");
    assert_eq!(v.rendered.as_deref(), Some("[session 2: postgres \u{b7} alive]\nready\n--- stderr ---\nwarn\n"));
    assert_eq!(v.subject.as_deref(), Some("session 2: postgres \u{b7} alive, 2 lines"));
    assert_eq!(v.value["alive"], true);
    assert_eq!(v.value["stdout"], "ready\n");
    assert_eq!(v.value["stderr"], "warn\n");
    assert_eq!(v.value["truncated"], false);
}

#[tokio::test]
async fn poll_reports_the_exit_status_once_the_process_ended() {
    let fx = Fixture::new();
    let fake = Arc::new(FakeSessions::new(dead(2, "postgres", Some(0), 84)));
    let c = ctx_with_sessions(&fx, fake);
    let v = Session::new().call(json!({"action": "poll", "session": 2}), &c).await;
    assert_eq!(v.rendered.as_deref(), Some("[session 2: exit 0 after 84s]\n"));
    assert_eq!(v.subject.as_deref(), Some("session 2: exit 0 after 84s"));
    assert_eq!(v.value["alive"], false);
    assert_eq!(v.value["exit_code"], 0);
}

#[tokio::test]
async fn long_poll_output_is_tail_truncated_and_spilled() {
    let fx = Fixture::new();
    let full: String = (1..=5000).map(|i| format!("line {i}\n")).collect();
    let fake = Arc::new(FakeSessions::new(alive(1, "seq")).with_output(&full, ""));
    let c = ctx_with_sessions(&fx, fake);
    let v = Session::new().call(json!({"action": "poll", "session": 1}), &c).await;
    let r = v.rendered.unwrap();
    let spill = c.spill_dir.join("call-1-session.txt");
    assert!(
        r.starts_with(&format!(
            "[session 1: seq \u{b7} alive]\n[Showing the last 2000 of 5000 lines. Full output saved to {}]\nline 3001\n",
            spill.display()
        )),
        "{r}"
    );
    assert!(r.ends_with("line 5000\n"), "the tail of the output ends the rendering");
    assert_eq!(std::fs::read_to_string(&spill).unwrap(), full);
    assert_eq!(v.value["truncated"], true);
    assert_eq!(v.value["spill"], spill.display().to_string());
    assert_eq!(v.subject.as_deref(), Some("session 1: seq \u{b7} alive, 5000 lines"));
}

#[tokio::test]
async fn write_signal_and_stop_report_their_action() {
    let fx = Fixture::new();
    let fake = Arc::new(FakeSessions::new(alive(2, "postgres")));
    let c = ctx_with_sessions(&fx, fake.clone());
    let tool = Session::new();

    let v = tool.call(json!({"action": "write", "session": 2, "input": "y\n"}), &c).await;
    assert_eq!(v.value["bytes"], 2);
    assert_eq!(v.subject.as_deref(), Some("session 2: postgres \u{b7} 2 bytes to stdin"));

    let v = tool.call(json!({"action": "signal", "session": 2, "signal": "int"}), &c).await;
    assert_eq!(v.value["signal"], "SIGINT", "a bare name gains the SIG prefix");
    assert_eq!(v.subject.as_deref(), Some("session 2: postgres \u{b7} SIGINT sent"));

    let fake_dead = Arc::new(FakeSessions::new(dead(2, "postgres", Some(0), 84)));
    let c = ctx_with_sessions(&fx, fake_dead);
    let v = tool.call(json!({"action": "stop", "session": 2}), &c).await;
    assert_eq!(v.value["exit_code"], 0);
    assert_eq!(v.subject.as_deref(), Some("session 2: exit 0 after 84s"));

    assert_eq!(*fake.calls.lock().unwrap(), ["write 2 2", "signal 2 SIGINT"]);
}

#[tokio::test]
async fn missing_arguments_and_handles_are_errors() {
    let fx = Fixture::new();
    let tool = Session::new();
    let v = tool.call(json!({"action": "poll", "session": 1}), &ctx(&fx)).await;
    assert!(v.is_error);
    assert!(v.rendered.unwrap().contains("sessions handle"));

    let fake = Arc::new(FakeSessions::new(alive(1, "x")));
    let c = ctx_with_sessions(&fx, fake);
    let v = tool.call(json!({"action": "start"}), &c).await;
    assert!(v.is_error && v.rendered.unwrap().contains("`command`"));
    let v = tool.call(json!({"action": "poll"}), &c).await;
    assert!(v.is_error && v.rendered.unwrap().contains("`session`"));
    let v = tool.call(json!({"action": "write", "session": 1}), &c).await;
    assert!(v.is_error && v.rendered.unwrap().contains("`input`"));
    let v = tool.call(json!({"action": "restart"}), &c).await;
    assert!(v.is_error && v.rendered.unwrap().contains("invalid arguments"));
}
