//! The built binary end to end: episodes under `--host` driven by a scripted
//! host, `plan --json` over the examples, and the examples against the
//! schema the binary prints.

use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

const FOE: &str = env!("CARGO_BIN_EXE_foe");
const SCHEMA: &str = include_str!("../src/schema.json");
const EXAMPLES: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../../examples");

fn scratch(name: &str) -> PathBuf {
    let dir = Path::new(env!("CARGO_TARGET_TMPDIR")).join(format!("cli-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir.canonicalize().unwrap()
}

fn text(delta: &str) -> Value {
    json!({ "kind": "text", "delta": delta })
}

fn done(stop: &str) -> Value {
    json!({ "kind": "done", "stop": stop, "usage": { "input": 10, "output": 5, "cache_read": 0 } })
}

fn call(id: &str, name: &str, args: &str) -> Vec<Value> {
    vec![
        json!({ "kind": "tool_call_start", "id": id, "name": name }),
        json!({ "kind": "tool_call_delta", "id": id, "delta": args }),
        json!({ "kind": "tool_call_end", "id": id }),
    ]
}

fn config(dir: &Path, edit: impl FnOnce(&mut Value)) -> Value {
    let mut value = json!({
        "version": 1,
        "name": "test",
        "instructions": { "role": "You are under test." },
        "tools": ["read"],
        "grants": { "read": [dir] },
        "budget": { "model_calls": 4 },
        "task": "do the thing"
    });
    edit(&mut value);
    value
}

/// Runs the binary under `--host`, answering each `model/request` with the
/// next scripted response and each `host/tool-call` through `answer`.
/// Returns every event the binary wrote and its exit code.
fn host_run(
    dir: &Path,
    config: &Value,
    mut responses: Vec<Vec<Value>>,
    answer: impl Fn(&str, &Value) -> Value,
) -> (Vec<Value>, i32) {
    let config_path = dir.join("config.json");
    std::fs::write(&config_path, serde_json::to_vec_pretty(config).unwrap()).unwrap();
    let log_dir = dir.join("log");
    let mut child = Command::new(FOE)
        .arg("--config")
        .arg(&config_path)
        .arg("--host")
        .arg("--log-dir")
        .arg(&log_dir)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let mut stdin = child.stdin.take().unwrap();
    let stdout = BufReader::new(child.stdout.take().unwrap());
    let mut events = Vec::new();
    let mut send = |mut line: Value, tag: Option<&Value>| {
        if let Some(id) = tag {
            line["episode_id"] = id.clone();
        }
        writeln!(stdin, "{line}").unwrap()
    };
    for line in stdout.lines() {
        let event: Value = serde_json::from_str(&line.unwrap()).unwrap();
        // A line tagged with an episode id is a descendant's request passed
        // through this process; the answer carries the tag back.
        let tag = event.get("episode_id").cloned();
        match event["type"].as_str().unwrap() {
            "model/request" => {
                let id = event["data"]["request_id"].clone();
                let chunks = if responses.is_empty() {
                    vec![json!({ "kind": "error", "message": "script exhausted", "retryable": false })]
                } else {
                    responses.remove(0)
                };
                for chunk in chunks {
                    send(json!({ "type": "model/chunk", "request_id": id, "chunk": chunk }), tag.as_ref());
                }
            }
            "host/tool-call" => {
                let data = &event["data"];
                let value = answer(data["name"].as_str().unwrap(), &data["args"]);
                send(json!({ "type": "tool/result", "call_id": data["call_id"], "value": value }), tag.as_ref());
            }
            _ => {}
        }
        if tag.is_some() {
            continue;
        }
        let ended = event["type"] == "episode/end";
        events.push(event);
        if ended {
            break;
        }
    }
    drop(stdin);
    let code = child.wait().unwrap().code().unwrap();
    let file = std::fs::read_to_string(log_dir.join("episode.jsonl")).unwrap();
    let written: Vec<Value> = file.lines().map(|l| serde_json::from_str(l).unwrap()).collect();
    assert_eq!(written, events, "standard output is the log, line for line");
    (events, code)
}

fn types(events: &[Value]) -> Vec<&str> {
    events.iter().map(|e| e["type"].as_str().unwrap()).collect()
}

/// docs/protocol.md: the host supplies the transport and answers host tool
/// calls; the exit code follows the outcome.
#[test]
fn a_hosted_episode_with_a_host_tool_completes() {
    let dir = scratch("host-tool");
    let config = config(&dir, |c| {
        c["tools"] = json!(["mutation_usage"]);
        c["host_tools"] = json!({ "mutation_usage": {
            "description": "Find where a mutation point is referenced.",
            "params": { "type": "object", "properties": { "mutation_id": { "type": "string" } }, "required": ["mutation_id"] },
            "effect": "pure"
        }});
    });
    let mut first = vec![text("I will look.")];
    first.extend(call("tc_1", "mutation_usage", r#"{"mutation_id": "m_41"}"#));
    first.push(done("tool"));
    let second = vec![text("Done: 3 references."), done("end")];
    let (events, code) = host_run(&dir, &config, vec![first, second], |name, args| {
        assert_eq!((name, args["mutation_id"].as_str()), ("mutation_usage", Some("m_41")));
        json!({ "count": 3 })
    });
    assert_eq!(code, 0);
    assert_eq!(events[0]["type"], "episode/start");
    assert_eq!(events[0]["seq"], 0);
    assert_eq!(events[0]["data"]["parent_id"], Value::Null);
    let kinds = types(&events);
    assert_eq!(&kinds[..4], ["episode/start", "inbox/item", "request/header", "model/request"]);
    assert!(kinds.contains(&"assistant/message"));
    assert!(kinds.contains(&"host/tool-call"));
    let result = events.iter().find(|e| e["type"] == "tool/result").unwrap();
    assert_eq!(result["data"]["value"], json!({ "count": 3 }));
    assert_eq!(result["data"]["rendered"], r#"{"count":3}"#);
    let end = events.last().unwrap();
    assert_eq!(end["type"], "episode/end");
    assert_eq!(end["data"]["outcome"], json!({ "kind": "completed", "value": "Done: 3 references." }));
}

/// docs/config.md `done_when`: a configured verifier receives the candidate
/// on standard input; findings printed with exit status 0 return to the
/// model until it prints nothing, and any other exit status ends the
/// episode as failed with the exit code in the error.
#[test]
fn a_verifier_feeds_findings_back_until_it_prints_nothing() {
    let script_text = "#!/bin/sh\nread -r candidate\ncase \"$candidate\" in\n  *first*) echo \"one finding\" ;;\n  *crash*) exit 1 ;;\nesac\n";
    let verifying = |name: &str| {
        let dir = scratch(name);
        let script = dir.join("check");
        std::fs::write(&script, script_text).unwrap();
        std::fs::set_permissions(&script, std::os::unix::fs::PermissionsExt::from_mode(0o755)).unwrap();
        let config = config(&dir, |c| {
            c["tools"] = json!(["read", "check"]);
            c["tool_defs"] = json!({ "check": { "exec": script, "description": "Prints one finding per line." } });
            c["done_when"] = json!({ "verify": "check", "retries": 2 });
        });
        (dir, config)
    };
    let (dir, config) = verifying("verify");
    let responses = vec![vec![text("first draft"), done("end")], vec![text("second draft"), done("end")]];
    let (events, code) = host_run(&dir, &config, responses, |_, _| Value::Null);
    assert_eq!(code, 0);
    let kinds = types(&events);
    assert_eq!(kinds.iter().filter(|t| **t == "model/request").count(), 2);
    let verify = events.iter().find(|e| e["type"] == "inbox/item" && e["data"]["source"] == "verify").unwrap();
    assert!(verify["data"]["content"][0]["text"].as_str().unwrap().contains("one finding"));
    assert_eq!(events.last().unwrap()["data"]["outcome"]["kind"], "completed");

    let (dir, config) = verifying("verify-crash");
    let (events, code) = host_run(&dir, &config, vec![vec![text("crash"), done("end")]], |_, _| Value::Null);
    assert_eq!(code, 1);
    let outcome = &events.last().unwrap()["data"]["outcome"];
    assert_eq!(outcome["kind"], "failed");
    let error = outcome["error"].as_str().unwrap();
    assert!(error.contains("verifier `check` failed") && error.contains("[exit code 1]"), "{error}");
}

/// docs/design.md "The episode": a spent model-call budget with work
/// remaining ends the episode as exhausted, exit code 3.
#[test]
fn a_spent_model_call_budget_exhausts_the_episode() {
    let dir = scratch("exhausted");
    std::fs::write(dir.join("f.txt"), "line\n").unwrap();
    let config = config(&dir, |c| c["budget"] = json!({ "model_calls": 1 }));
    let mut first = vec![text("reading")];
    first.extend(call("tc_1", "read", r#"{"path": "f.txt"}"#));
    first.push(done("tool"));
    let (events, code) = host_run(&dir, &config, vec![first], |_, _| Value::Null);
    assert_eq!(code, 3);
    let result = events.iter().find(|e| e["type"] == "tool/result").unwrap();
    assert_eq!(result["data"]["is_error"], false, "the last permitted call still runs");
    assert_eq!(events.last().unwrap()["data"]["outcome"], json!({ "kind": "exhausted", "limit": "model_calls" }));
}

/// A child program for a workflow model node, reading `dir`.
fn node_program(name: &str, dir: &Path) -> Value {
    json!({
        "name": name, "instructions": { "role": "Decide." }, "tools": ["block"],
        "grants": { "read": [dir] }, "budget": { "model_calls": 2 }
    })
}

fn node_starts(events: &[Value]) -> Vec<(String, u64)> {
    events
        .iter()
        .filter(|e| e["type"] == "workflow/node-start")
        .map(|e| (e["data"]["node"].as_str().unwrap().to_string(), e["data"]["fire"].as_u64().unwrap()))
        .collect()
}

/// docs/workflow.md "The graph", "Firing", "Choice points", and "Model
/// nodes": the invocation task and a tool node's value reach a model node
/// as task sections, the task first; the model node's returned `branch`
/// fires only the listed successor; and a binding with a pointer reaches
/// the terminal tool node.
#[test]
fn a_workflow_fires_tool_model_and_tool_nodes_and_completes() {
    let dir = scratch("workflow-nodes");
    let config = config(&dir, |c| {
        c["tools"] = json!(["list", "derive"]);
        c["host_tools"] = json!({
            "list": { "description": "Lists targets.", "params": { "type": "object" }, "effect": "pure" },
            "derive": { "description": "Derives patches.", "params": { "type": "object" }, "effect": "pure" }
        });
        c["budget"] = json!({ "model_calls": 6 });
        c["workflow"] = json!({ "nodes": {
            "manifest": { "tool": "list" },
            "propose": { "model": node_program("propose", &dir), "follows": ["manifest", "task"],
                         "branches": { "accept": ["derive"], "stop": [] } },
            "derive": { "tool": "derive", "args": { "experiment": { "$node": "propose", "pointer": "/experiment" } },
                        "follows": ["propose"], "terminal": true }
        } });
    });
    let mut child = vec![text("proposing")];
    child.extend(call("tc_1", "return", r#"{"value": {"experiment": "swap", "branch": "accept"}}"#));
    child.push(done("tool"));
    let (events, code) = host_run(&dir, &config, vec![child], |name, args| match name {
        "list" => json!({ "targets": ["a", "b"] }),
        "derive" => {
            assert_eq!(args["experiment"], "swap", "the pointer binding resolved");
            json!({ "patches": 2 })
        }
        other => panic!("unexpected tool {other}"),
    });
    assert_eq!(code, 0);
    assert_eq!(events.last().unwrap()["data"]["outcome"], json!({ "kind": "completed", "value": { "patches": 2 } }));
    assert_eq!(node_starts(&events), [("manifest".into(), 1), ("propose".into(), 1), ("derive".into(), 1)]);
    assert_eq!(types(&events).iter().filter(|t| **t == "model/request").count(), 0, "no recovery was needed");
    let branch = events.iter().find(|e| e["type"] == "workflow/branch").unwrap();
    assert_eq!(branch["data"]["label"], "accept");
    assert_eq!(branch["data"]["successors"], json!(["derive"]));
    let spawn = events.iter().find(|e| e["type"] == "spawn/start").unwrap();
    assert_eq!(spawn["data"]["program"], "propose");
    let start = events.iter().find(|e| e["type"] == "workflow/node-start" && e["data"]["node"] == "propose").unwrap();
    let child_id = start["data"]["child_id"].as_str().unwrap();
    let child_log = dir.join("log/children").join(child_id).join("episode.jsonl");
    let child: Vec<Value> =
        std::fs::read_to_string(&child_log).unwrap().lines().map(|l| serde_json::from_str(l).unwrap()).collect();
    assert_eq!(child[1]["type"], "inbox/item");
    let task = child[1]["data"]["content"][0]["text"].as_str().unwrap();
    assert_eq!(task, "## task\n\ndo the thing\n\n## manifest\n\n{\"targets\":[\"a\",\"b\"]}", "one section per input");
    assert_eq!(start["data"]["inputs"][0], 1, "the task source's input is the task item at seq 1");
    let header = child.iter().find(|e| e["type"] == "request/header").unwrap();
    let returns = &header["data"]["tools"][1]["parameters"]["properties"]["value"];
    assert_eq!(returns["properties"]["branch"]["enum"], json!(["accept", "stop"]));
    assert_eq!(child.last().unwrap()["data"]["outcome"]["kind"], "completed");
    assert!(types(&events).contains(&"budget/release"));
}

/// docs/workflow.md "Recovery" and "Tool nodes": a configured executable
/// that exits non-zero fails its node; the host's `retry` re-fires it and
/// the workflow completes when it succeeds.
#[test]
fn a_failed_tool_node_is_retried_through_recovery() {
    let dir = scratch("workflow-recovery");
    let script = dir.join("flaky");
    std::fs::write(
        &script,
        "#!/bin/sh\nstate=\"$(dirname \"$0\")/state\"\nif [ ! -f \"$state\" ]; then touch \"$state\"; echo failing; exit 1; fi\necho fine\n",
    )
    .unwrap();
    std::fs::set_permissions(&script, std::os::unix::fs::PermissionsExt::from_mode(0o755)).unwrap();
    let config = config(&dir, |c| {
        c["tools"] = json!(["flaky"]);
        c["tool_defs"] = json!({ "flaky": { "exec": script, "description": "Fails once." } });
        c["grants"]["write"] = json!([dir]);
        c["workflow"] = json!({ "nodes": {
            "only": { "tool": "flaky", "args": { "args": [] }, "max_fires": 2, "terminal": true }
        } });
    });
    let mut decision = vec![text("retrying")];
    decision.extend(call("tc_r", "recover", r#"{"action": "retry", "node": "only"}"#));
    decision.push(done("tool"));
    let (events, code) = host_run(&dir, &config, vec![decision], |_, _| Value::Null);
    assert_eq!(code, 0);
    let outcome = &events.last().unwrap()["data"]["outcome"];
    assert_eq!((outcome["kind"].as_str(), outcome["value"]["exit_code"].as_i64()), (Some("completed"), Some(0)));
    let recovery = events.iter().find(|e| e["type"] == "workflow/recovery").unwrap();
    assert_eq!(recovery["data"]["action"], "retry");
    assert_eq!(recovery["data"]["target"], "only");
    assert_eq!(recovery["data"]["intervention"], 1);
    assert_eq!(recovery["data"]["cause"], "tool-error");
    assert_eq!(node_starts(&events), [("only".into(), 1), ("only".into(), 2)]);
    let first_end = events.iter().find(|e| e["type"] == "workflow/node-end").unwrap();
    assert!(first_end["data"]["error"].as_str().unwrap().contains("exit code 1"));
    let header = events.iter().find(|e| e["type"] == "request/header").unwrap();
    assert_eq!(header["data"]["tools"][0]["name"], "recover");
    let item = events.iter().find(|e| e["type"] == "inbox/item" && e["data"]["source"] == "system").unwrap();
    assert!(item["data"]["content"][0]["text"].as_str().unwrap().contains("Node `only` failed on firing 1"));
}

/// docs/workflow.md "What bounds it": a cycle that re-fires until a node
/// reaches its `max_fires` ends the episode as blocked with code
/// `recovery-exhausted`.
#[test]
fn a_cycle_that_reaches_max_fires_ends_as_recovery_exhausted() {
    let dir = scratch("workflow-cycle");
    let config = config(&dir, |c| {
        c["tools"] = json!(["list", "scan", "derive"]);
        c["host_tools"] = json!({
            "list": { "description": "Lists targets.", "params": { "type": "object" }, "effect": "pure" },
            "scan": { "description": "Scans a target.", "params": { "type": "object" }, "effect": "pure" },
            "derive": { "description": "Derives patches.", "params": { "type": "object" }, "effect": "pure" }
        });
        c["budget"] = json!({ "model_calls": 8 });
        c["workflow"] = json!({ "nodes": {
            "manifest": { "tool": "list" },
            "survey": { "tool": "scan", "args": { "about": { "$node": "manifest" } }, "follows": ["manifest"], "max_fires": 2 },
            "propose": { "model": node_program("propose", &dir), "follows": ["manifest", "survey"],
                         "branches": { "accept": ["derive"], "widen": ["survey"] }, "max_fires": 2 },
            "derive": { "tool": "derive", "follows": ["propose"], "terminal": true }
        } });
    });
    let widen = || {
        let mut chunks = vec![text("wider")];
        chunks.extend(call("tc_1", "return", r#"{"value": {"branch": "widen"}}"#));
        chunks.push(done("tool"));
        chunks
    };
    let (events, code) = host_run(&dir, &config, vec![widen(), widen()], |_, _| json!({ "ok": true }));
    assert_eq!(code, 2);
    let outcome = &events.last().unwrap()["data"]["outcome"];
    assert_eq!(outcome["code"], "recovery-exhausted");
    assert!(outcome["message"].as_str().unwrap().contains("max_fires"), "{outcome}");
    let fired: Vec<String> = node_starts(&events).iter().map(|(n, f)| format!("{n}#{f}")).collect();
    assert_eq!(fired, ["manifest#1", "survey#1", "propose#1", "survey#2", "propose#2"]);
    let labels: Vec<&str> = events
        .iter()
        .filter(|e| e["type"] == "workflow/branch")
        .map(|e| e["data"]["label"].as_str().unwrap())
        .collect();
    assert_eq!(labels, ["widen", "widen"]);
    assert_eq!(std::fs::read_dir(dir.join("log/children")).unwrap().count(), 2, "one child per model firing");
}

fn examples() -> Vec<(String, String)> {
    let mut found: Vec<(String, String)> = std::fs::read_dir(EXAMPLES)
        .unwrap()
        .flatten()
        .filter_map(|entry| {
            let text = std::fs::read_to_string(entry.path().join("config.json")).ok()?;
            Some((entry.file_name().to_string_lossy().into_owned(), text))
        })
        .collect();
    found.sort();
    assert!(found.len() >= 7, "every example has a config.json");
    found
}

/// Writes an example's configuration with its placeholder paths pointing
/// into `root`, and returns the path of the written document.
fn materialize(root: &Path, name: &str, text: &str, task: &str) -> PathBuf {
    let project = root.join("project");
    for sub in ["src", "tests", "tools"] {
        std::fs::create_dir_all(project.join(sub)).unwrap();
    }
    std::fs::write(root.join("anthropic.key"), "sk-test\n").unwrap();
    let ruff = project.join("tools/ruff-check");
    std::fs::copy(Path::new(EXAMPLES).join("wrap-a-binary/ruff-check"), &ruff).unwrap();
    let rewritten = text
        .replace("/home/user/.config/foe/anthropic.key", root.join("anthropic.key").to_str().unwrap())
        .replace("/home/user/project", project.to_str().unwrap());
    let mut value: Value = serde_json::from_str(&rewritten).unwrap();
    value["task"] = json!(task);
    let path = root.join(format!("{name}.json"));
    std::fs::write(&path, serde_json::to_vec_pretty(&value).unwrap()).unwrap();
    path
}

fn plan(config: &Path) -> Value {
    let output = Command::new(FOE).args(["plan", "--json", "--config"]).arg(config).output().unwrap();
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    let line = String::from_utf8(output.stdout).unwrap();
    assert_eq!(line.lines().count(), 1, "one JSON line");
    serde_json::from_str(&line).unwrap()
}

/// docs/design.md "Programs and identity": identity excludes the task and
/// the resolved paths, so the same program in another directory hashes the
/// same.
#[test]
fn plan_reports_an_identity_that_ignores_task_and_paths() {
    let a = scratch("plan-a");
    let b = scratch("plan-b");
    for (name, text) in examples() {
        let first = plan(&materialize(&a, &name, &text, "first task"));
        let second = plan(&materialize(&b, &name, &text, "second task"));
        let identity = first["identity"].as_str().unwrap();
        assert!(identity.starts_with("sha256:") && identity.len() == 71, "{name}: {identity}");
        assert_eq!(first["identity"], second["identity"], "{name}: identity ignores task and paths");
        assert_eq!(first["program"]["name"], json!(serde_json::from_str::<Value>(&text).unwrap()["name"]));
        assert!(first["program"].get("task").is_none(), "{name}: the program omits the task");
        if name == "workflow" {
            assert_eq!(first["workflow"]["terminal"], json!(["apply"]), "plan reports the workflow's terminal node");
            assert_eq!(first["workflow"]["cycles"], json!([]));
            assert!(first["program"]["workflow"]["nodes"]["propose"]["model"].is_object());
        } else {
            assert_eq!(first["workflow"], Value::Null);
        }
    }
}

/// Replaces every `$ref` into `$defs` by the definition it names, to the
/// given depth, so that the runtime's subset validator can check a document
/// against the whole schema.
fn inline(value: &Value, defs: &Value, depth: u32) -> Value {
    match value {
        Value::Object(map) => match map.get("$ref").and_then(Value::as_str) {
            Some(reference) => {
                let name = reference.strip_prefix("#/$defs/").unwrap();
                if depth == 0 {
                    return json!({});
                }
                inline(&defs[name], defs, depth - 1)
            }
            None => Value::Object(map.iter().map(|(k, v)| (k.clone(), inline(v, defs, depth))).collect()),
        },
        Value::Array(items) => Value::Array(items.iter().map(|v| inline(v, defs, depth)).collect()),
        other => other.clone(),
    }
}

/// docs/config.md: `foe schema` describes the document; every example
/// conforms to it and parses into the runtime's configuration type.
#[test]
fn every_example_conforms_to_the_schema_and_parses() {
    let schema: Value = serde_json::from_str(SCHEMA).unwrap();
    let printed = Command::new(FOE).arg("schema").output().unwrap();
    assert_eq!(serde_json::from_slice::<Value>(&printed.stdout).unwrap(), schema, "the binary prints the schema");
    let inlined = inline(&schema, &schema["$defs"], 4);
    for (name, text) in examples() {
        let document: Value = serde_json::from_str(&text).unwrap();
        foe_core::registry::conforms(&inlined, &document).unwrap_or_else(|e| panic!("{name}: {e}"));
        foe_core::config::parse(&text).unwrap_or_else(|e| panic!("{name}: {e}"));
    }
    let mut broken: Value = serde_json::from_str(&examples()[0].1).unwrap();
    broken["budget"]["model_calls"] = json!("many");
    assert!(foe_core::registry::conforms(&inlined, &broken).is_err(), "a wrong type is caught through a $ref");
}
