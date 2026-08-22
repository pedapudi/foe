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

#[tokio::test]
async fn a_timeout_kills_the_whole_process_group() {
    let fx = Fixture::new();
    let mut c = ctx_with_executor(&fx, Arc::new(ProcessGroupExecutor));
    // The step's deadline bounds the call below the argument's own minimum
    // of one second, so the wait is a fraction of a second rather than a
    // whole one. The deadline path is the one an episode near its `seconds`
    // budget takes.
    c.deadline = Some(Instant::now() + Duration::from_millis(300));
    let cmd = "sleep 30 & echo $!; wait";
    let v = Bash::new().call(json!({"command": cmd, "timeout_seconds": 30}), &c).await;
    assert!(!v.is_error, "{v:?}");
    assert_eq!(v.value["timed_out"], true);
    assert!(v.value["exit_code"].is_null());
    assert!(v.rendered.as_deref().unwrap().contains("[timed out after"));
    let grandchild = v.value["stdout"].as_str().unwrap().trim().to_owned();
    assert!(!grandchild.is_empty(), "bash printed the background pid before the kill");
    let state = std::fs::read_to_string(format!("/proc/{grandchild}/stat")).unwrap_or_default();
    let alive = !state.is_empty() && !state.contains(") Z ") && !state.contains(") X ");
    assert!(!alive, "sleep {grandchild} outlived the timeout: {state}");
}
