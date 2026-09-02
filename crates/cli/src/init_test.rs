use super::*;
use foe_contract::document::parse;
use std::process::Stdio;

fn repository(name: &str) -> (crate::tests::ScratchDir, PathBuf, PathBuf) {
    let dir = crate::tests::scratch("foe-cli-init", name);
    std::fs::write(dir.join("README.md"), "fixture\n").unwrap();
    let root = dir.canonicalize().unwrap();
    let contract_path = root.join(".foe").join("contract.json");
    let verify_path = root.join(".foe").join("verify");
    (dir, contract_path, verify_path)
}

/// The acceptance issue 34 states: init writes a document that resolves and
/// passes the readiness analysis `foe plan` prints with no warning, its
/// grants and budget backstops as decided, atomically.
#[test]
fn init_writes_a_resolvable_contract_with_no_configuration_warnings() {
    let (dir, contract_path, verify_path) = repository("fresh");
    let report = init(&dir).unwrap();
    let root = dir.canonicalize().unwrap();
    let document = parse(&std::fs::read_to_string(&contract_path).unwrap()).unwrap();
    let contract = resolve(&document).expect("the generated document resolves");
    assert_eq!(configuration_warnings(&contract), []);
    assert_eq!(document.grants.read, std::slice::from_ref(&root));
    assert_eq!(document.grants.write, std::slice::from_ref(&root));
    let mut execute: Vec<PathBuf> = run::BUILTIN_EXECUTE_ROOTS.iter().map(PathBuf::from).collect();
    execute.push(root.clone());
    assert_eq!(document.grants.execute, execute);
    for node in document.workflow.as_ref().unwrap().nodes.values() {
        assert_eq!(node.model.as_ref().unwrap().grants.execute, execute);
    }
    assert_eq!(document.budget.model_calls, INIT_MODEL_CALLS);
    assert_eq!(document.budget.seconds, Some(INIT_SECONDS));
    assert_eq!(document.budget.input_tokens, None, "token backstops stay unlimited");
    assert_eq!(document.budget.output_tokens, None, "token backstops stay unlimited");
    assert_eq!(document.budget.loop_threshold, 8, "the loop threshold keeps its default");
    assert_eq!(document.done_when.as_ref().unwrap().verify.as_deref(), Some("check"));
    assert_eq!(document.tool_defs["check"].exec, verify_path);
    assert!(!document.task.is_empty(), "`task` is required; the placeholder is replaced on the command line");
    let names: Vec<String> =
        std::fs::read_dir(root.join(".foe")).unwrap().map(|e| e.unwrap().file_name().into_string().unwrap()).collect();
    assert_eq!(names.len(), 2, "atomic writes leave no temporary beside the targets: {names:?}");
    for said in [
        ".git lies inside this write grant",
        "no exclusion syntax",
        "executing and reading every",
        "safety backstops",
        "judges candidates by the",
        "captured bytes, and a future run reads the file as it then exists",
    ] {
        assert!(report.contains(said), "the report does not say {said:?}:\n{report}");
    }
}

#[test]
fn init_refuses_to_replace_an_existing_configuration() {
    let (dir, contract_path, _) = repository("refuse");
    init(&dir).unwrap();
    let error = init(&dir).unwrap_err();
    assert!(error.contains("contract.json") && error.contains("already exists"), "{error}");
    std::fs::remove_file(&contract_path).unwrap();
    let error = init(&dir).unwrap_err();
    assert!(error.contains("verify") && error.contains("already exists"), "{error}");
}

/// docs/config.md `done_when`: a verifier reports findings by exiting zero
/// and printing one finding per line; a nonzero exit would end the episode
/// as failed instead of showing the model the finding.
#[test]
fn the_placeholder_verifier_rejects_every_candidate_with_one_finding() {
    let (dir, _, verify_path) = repository("verifier");
    init(&dir).unwrap();
    let mut child = std::process::Command::new(&verify_path)
        .current_dir(&dir)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    std::io::Write::write_all(child.stdin.as_mut().unwrap(), b"{\"summary\": \"a completion candidate\"}").unwrap();
    let output = child.wait_with_output().unwrap();
    assert!(output.status.success(), "findings are exit 0 with output; nonzero is a verifier failure: {output:?}");
    let stdout = String::from_utf8(output.stdout).unwrap();
    let findings: Vec<&str> = stdout.lines().collect();
    assert_eq!(findings.len(), 1, "one finding per candidate:\n{stdout}");
    for said in ["completion cannot be verified", "replace it with a real completion check", "report blocked"] {
        assert!(findings[0].contains(said), "the finding does not say {said:?}:\n{stdout}");
    }
    assert!(findings[0].contains(verify_path.to_str().unwrap()), "the finding names the file to replace");
}

/// The verifier lies inside the write grant. Construction captures its
/// bytes, so the active episode keeps judging by the captured verifier
/// while a later run reads the file as it then exists.
/// `crates/core/src/exec_test.rs`
/// `a_captured_script_survives_mutation_replacement_deletion_and_repeated_calls_under_landlock`
/// asserts the execution side: the captured copy runs unchanged after its
/// source is mutated.
#[test]
fn an_episode_judges_by_the_verifier_captured_at_construction() {
    let (dir, contract_path, verify_path) = repository("capture");
    init(&dir).unwrap();
    let document = parse(&std::fs::read_to_string(&contract_path).unwrap()).unwrap();
    let active = resolve(&document).unwrap();
    let captured = &active.captured_executables["check"];
    let original = std::fs::read(&verify_path).unwrap();
    assert_eq!(captured.bytes.as_ref(), original.as_slice());
    std::fs::write(&verify_path, "#!/bin/sh\nexit 0\n").unwrap();
    assert_eq!(captured.bytes.as_ref(), original.as_slice(), "the active contract holds construction-time bytes");
    let replaced = resolve(&document).unwrap();
    assert_ne!(
        replaced.captured_executables["check"].sha256, captured.sha256,
        "a future run reads the file as it then exists"
    );
}
