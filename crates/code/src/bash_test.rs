use super::*;
use crate::testing::{ctx, ctx_with_executor, FakeExecutor, Fixture, ProcessGroupExecutor};
use foe_core::ExecResult;
use std::sync::Arc;

fn result(code: i32, stdout: &str, stderr: &str) -> ExecResult {
    ExecResult {
        exit_code: Some(code),
        stdout: stdout.into(),
        stderr: stderr.into(),
        timed_out: false,
        duration: Duration::from_millis(1500),
    }
}

#[tokio::test]
async fn builds_the_request_and_reports_a_non_zero_exit_as_a_result() {
    let fx = Fixture::new();
    let exec = Arc::new(FakeExecutor::new(result(2, "out\n", "err\n")));
    let c = ctx_with_executor(&fx, exec.clone());
    let v = Bash::new().call(json!({"command": "false"}), &c).await;
    assert!(!v.is_error, "{v:?}");
    assert_eq!(v.value["exit_code"], 2);
    assert_eq!(v.value["timed_out"], false);
    assert_eq!(v.value["duration_ms"], 1500);
    assert_eq!(v.rendered.as_deref(), Some("[exit 2 in 1.50s]\nout\n--- stderr ---\nerr\n"));
    let req = exec.last().unwrap();
    assert_eq!(req.program, PathBuf::from("/bin/bash"));
    assert_eq!(req.args, ["-c", "false"]);
    assert_eq!(req.cwd, fx.root());
    assert_eq!(req.timeout, Duration::from_secs(BASH_DEFAULT_TIMEOUT_SECS));
    assert!(req.stdin.is_none());
    assert!(!req.network);
    assert_eq!(req.env["PATH"], "/usr/local/bin:/usr/bin:/bin");
    assert_eq!(req.env["HOME"], fx.root().display().to_string());
}

#[tokio::test]
async fn timeout_argument_and_deadline_bound_the_request() {
    let fx = Fixture::new();
    let exec = Arc::new(FakeExecutor::new(result(0, "", "")));
    let mut c = ctx_with_executor(&fx, exec.clone());
    Bash::new().call(json!({"command": "true", "timeout_seconds": 7}), &c).await;
    assert_eq!(exec.last().unwrap().timeout, Duration::from_secs(7));
    c.deadline = Some(Instant::now() + Duration::from_secs(2));
    Bash::new().call(json!({"command": "true", "timeout_seconds": 7}), &c).await;
    assert!(exec.last().unwrap().timeout <= Duration::from_secs(2));
}

#[tokio::test]
async fn long_output_is_tail_truncated_and_spilled() {
    let fx = Fixture::new();
    let full: String = (1..=5000).map(|i| format!("line {i}\n")).collect();
    let exec = Arc::new(FakeExecutor::new(result(0, &full, "")));
    let c = ctx_with_executor(&fx, exec);
    let v = Bash::new().call(json!({"command": "seq 5000"}), &c).await;
    let r = v.rendered.unwrap();
    let spill = c.spill_dir.join("call-1-bash.txt");
    assert!(
        r.starts_with(&format!(
            "[exit 0 in 1.50s]\n[Showing the last 2000 of 5000 lines. Full output saved to {}]\nline 3001\n",
            spill.display()
        )),
        "{r}"
    );
    assert!(r.ends_with("line 5000\n"), "the tail of the output ends the rendering");
    assert_eq!(std::fs::read_to_string(&spill).unwrap(), full);
    assert_eq!(v.value["truncated"], true);
    assert_eq!(v.value["spill"], spill.display().to_string());
    assert_eq!(v.value["stdout"], full);
}

#[tokio::test]
async fn missing_handles_are_errors() {
    let fx = Fixture::new();
    let v = Bash::new().call(json!({"command": "true"}), &ctx(&fx)).await;
    assert!(v.is_error);
    assert!(v.rendered.unwrap().contains("executor"));
}

#[tokio::test]
async fn a_real_non_zero_exit_is_a_result() {
    let fx = Fixture::new();
    let c = ctx_with_executor(&fx, Arc::new(ProcessGroupExecutor));
    let v = Bash::new().call(json!({"command": "echo out; echo err >&2; exit 3"}), &c).await;
    assert!(!v.is_error, "{v:?}");
    assert_eq!(v.value["exit_code"], 3);
    assert_eq!(v.value["stdout"], "out\n");
    assert_eq!(v.value["stderr"], "err\n");
}

/// A run the executor cut short is a result rather than an error: the model
/// is told the command timed out and what it printed first. Killing the
/// process group is the executor's own rule and is tested where the runtime
/// implements it, in `foe_core::exec`; this test double only stands in for
/// it.
#[tokio::test]
async fn a_timed_out_run_is_reported_as_a_result_with_the_output_it_produced() {
    let fx = Fixture::new();
    let timed_out = ExecResult {
        exit_code: None,
        stdout: "partial\n".into(),
        stderr: Vec::new(),
        timed_out: true,
        duration: Duration::from_millis(300),
    };
    let c = ctx_with_executor(&fx, Arc::new(FakeExecutor::new(timed_out)));
    let v = Bash::new().call(json!({"command": "sleep 30", "timeout_seconds": 1}), &c).await;
    assert!(!v.is_error, "{v:?}");
    assert_eq!(v.value["timed_out"], true);
    assert!(v.value["exit_code"].is_null(), "a killed command has no exit code");
    let rendered = v.rendered.unwrap();
    assert!(rendered.contains("[timed out after"), "{rendered}");
    assert!(rendered.contains("partial"), "the output before the kill reaches the model: {rendered}");
}

/// The command with what came of it. The arguments say what was asked for;
/// only the tool knows how it ended, which is the half a reader scanning a
/// list is looking for.
#[tokio::test]
async fn states_the_command_and_how_it_ended() {
    let fx = Fixture::new();
    let exec = Arc::new(FakeExecutor::new(result(0, "ok\n", "")));
    let c = ctx_with_executor(&fx, exec.clone());
    let v = Bash::new().call(json!({"command": "cargo test -p parser"}), &c).await;
    assert_eq!(v.subject.as_deref(), Some("cargo test -p parser \u{b7} exit 0 in 1.50s"));

    let exec = Arc::new(FakeExecutor::new(ExecResult {
        exit_code: None,
        stdout: Vec::new(),
        stderr: Vec::new(),
        timed_out: true,
        duration: Duration::from_secs(30),
    }));
    let c = ctx_with_executor(&fx, exec);
    let v = Bash::new().call(json!({"command": "sleep 99"}), &c).await;
    assert_eq!(v.subject.as_deref(), Some("sleep 99 \u{b7} timed out after 30.0s; the process group was killed"));
}
