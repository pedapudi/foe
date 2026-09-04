use super::{canonical, compute};
use crate::harness_text;
use crate::test_util::{contract, contract_with, spec, tmp};
use crate::Effect;
use foe_log::RuntimeInfo;
use serde_json::json;

fn runtime() -> RuntimeInfo {
    RuntimeInfo { version: "0.2.0".into(), build: "sha256:test".into() }
}

#[test]
fn canonical_form_sorts_keys_and_drops_whitespace() {
    let value = json!({ "b": [1, { "z": 1, "a": 2 }], "a": "x y" });
    assert_eq!(canonical(&value), r#"{"a":"x y","b":[1,{"a":2,"z":1}]}"#);
}

#[test]
fn fingerprint_is_a_sha256_and_ignores_resolved_paths_model_and_task() {
    let a = tmp("fingerprint-a");
    let b = tmp("fingerprint-b");
    std::fs::write(a.join("k.key"), "k").unwrap();
    let first = compute(&contract(&a), &[], &runtime()).unwrap();
    assert!(first.hash.starts_with("sha256:") && first.hash.len() == 7 + 64);
    let second = contract_with(&b, |v| {
        v["task"] = json!("a different task");
        v["model"] = json!({ "provider": "anthropic", "model": "m", "api_key_file": a.join("k.key") });
    })
    .unwrap();
    assert_eq!(compute(&second, &[], &runtime()).unwrap().hash, first.hash);
}

#[test]
fn fingerprint_changes_with_what_the_model_sees() {
    let root = tmp("fingerprint-changes");
    let base = compute(&contract(&root), &[], &runtime()).unwrap().hash;
    let renamed = contract_with(&root, |v| {
        v["instructions"] = json!({ "20-role": "You are a test agent.", "05-first": "Be brief." });
    })
    .unwrap();
    assert_ne!(compute(&renamed, &[], &runtime()).unwrap().hash, base, "an instruction key changes rendering order");
    let probes = [spec("a", Effect::Pure), spec("b", Effect::Pure)];
    let ab = contract_with(&root, |v| v["tools"] = json!(["a", "b"])).unwrap();
    let ba = contract_with(&root, |v| v["tools"] = json!(["b", "a"])).unwrap();
    assert_ne!(compute(&ab, &probes, &runtime()).unwrap().hash, compute(&ba, &probes, &runtime()).unwrap().hash);
    let more_grants = contract_with(&root, |v| v["grants"]["write"] = json!([])).unwrap();
    assert_ne!(compute(&more_grants, &[], &runtime()).unwrap().hash, base, "grant counts participate");
    let execute_grant = contract_with(&root, |v| v["grants"]["execute"] = json!([root])).unwrap();
    assert_ne!(compute(&execute_grant, &[], &runtime()).unwrap().hash, base, "execute grant counts participate");
    let bind_grant = contract_with(&root, |v| v["grants"]["bind"] = json!([8080])).unwrap();
    assert_ne!(compute(&bind_grant, &[], &runtime()).unwrap().hash, base, "bind grant counts participate");
    let task_session = contract_with(&root, |v| v["grants"]["task_session"] = json!(true)).unwrap();
    assert_ne!(compute(&task_session, &[], &runtime()).unwrap().hash, base, "task-session permission participates");
    let other_runtime = RuntimeInfo { version: "0.2.1".into(), build: "sha256:test".into() };
    assert_ne!(compute(&contract(&root), &[], &other_runtime).unwrap().hash, base);
}

#[test]
fn fingerprint_hashes_harness_text_exec_content_and_children() {
    let root = tmp("fingerprint-hash");
    let exec = root.join("tool.sh");
    std::fs::write(&exec, "v1").unwrap();
    std::fs::set_permissions(&exec, std::os::unix::fs::PermissionsExt::from_mode(0o755)).unwrap();
    let with_tool = |v: &mut serde_json::Value| {
        v["tools"] = json!(["block", "t", "spawn"]);
        v["tool_defs"] = json!({ "t": { "exec": exec, "description": "d" } });
        v["grants"]["spawn"] = json!(["kid"]);
        v["child_contracts"] = json!({ "kid": { "name": "kid", "instructions": { "a": "b" }, "tools": ["block"], "grants": { "read": [root] }, "budget": { "model_calls": 1 } } });
    };
    let spawn = [spec("spawn", Effect::Spawns)];
    let resolved = contract_with(&root, with_tool).unwrap();
    let first = compute(&resolved, &spawn, &runtime()).unwrap();
    let texts = &first.document["harness_text"]["texts"];
    for (key, value) in harness_text::all() {
        assert_eq!(texts[key], json!(value));
    }
    assert_eq!(
        first.document["tools"][0]["params"]["properties"]["code"]["enum"],
        json!(["goal-unreachable", "ambiguous-task", "missing-capability", "child-blocked"]),
        "fingerprint records the block schema selected by child-contract permission"
    );
    assert_eq!(first.document["tools"][1]["exec_sha256"], json!(super::sha256_hex(b"v1")));
    assert_eq!(first.document["tools"][1]["exec_name"], json!("tool.sh"));
    assert!(first.document["child_contracts"]["kid"].as_str().unwrap().starts_with("sha256:"));
    std::fs::write(&exec, "v2").unwrap();
    assert_eq!(
        compute(&resolved, &spawn, &runtime()).unwrap().hash,
        first.hash,
        "fingerprint remains a function of the contract constructed before the executable changed"
    );
    let second = compute(&contract_with(&root, with_tool).unwrap(), &spawn, &runtime()).unwrap();
    assert_ne!(second.hash, first.hash, "replacing the executable changes fingerprint");
}

#[test]
fn fingerprint_distinguishes_configured_names_for_the_same_executable() {
    let root = tmp("fingerprint-executable-name");
    let target = root.join("multicall");
    std::fs::write(&target, "same bytes").unwrap();
    std::fs::set_permissions(&target, std::os::unix::fs::PermissionsExt::from_mode(0o755)).unwrap();
    std::os::unix::fs::symlink(&target, root.join("first")).unwrap();
    std::os::unix::fs::symlink(&target, root.join("second")).unwrap();
    let configured = |name: &str| {
        contract_with(&root, |value| {
            value["tools"] = json!(["tool"]);
            value["tool_defs"] = json!({"tool": {"exec": root.join(name), "description": "multicall executable"}});
        })
        .unwrap()
    };
    let first = compute(&configured("first"), &[], &runtime()).unwrap();
    let second = compute(&configured("second"), &[], &runtime()).unwrap();
    assert_ne!(first.hash, second.hash, "the configured invocation_name selects multicall behavior");
}
