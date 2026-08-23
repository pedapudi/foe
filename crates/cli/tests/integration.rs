//! The built binary end to end: episodes under `--host` driven by a scripted
//! host, `plan --json` over the examples, and the examples against the
//! schema the binary prints.

use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

const FOE: &str = env!("CARGO_BIN_EXE_foe");
use foe_config::SCHEMA;
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
        "version": 2,
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

/// A model transport for a headless run: one shell script for the whole
/// tree, which tells the episodes apart by the marker each program's
/// instructions carry. The root spawns the middle episode and waits, the
/// middle spawns the leaf and waits, and the leaf calls the host tool
/// `ask_host` once.
const TREE_TRANSPORT: &str = r#"#!/bin/sh
read -r request
id=$(printf '%s' "$request" | sed 's/.*"request_id":"\([^"]*\)".*/\1/')
emit() { printf '{"type":"model/chunk","request_id":"%s","chunk":%s}\n' "$id" "$1"; }
tool_call() {
  emit "{\"kind\":\"tool_call_start\",\"id\":\"$1\",\"name\":\"$2\"}"
  emit "{\"kind\":\"tool_call_delta\",\"id\":\"$1\",\"delta\":\"$3\"}"
  emit "{\"kind\":\"tool_call_end\",\"id\":\"$1\"}"
  emit '{"kind":"done","stop":"tool","usage":{"input":1,"output":1,"cache_read":0}}'
}
finish() {
  emit "{\"kind\":\"text\",\"delta\":\"$1\"}"
  emit '{"kind":"done","stop":"end","usage":{"input":1,"output":1,"cache_read":0}}'
}
step=$(printf '%s' "$request" | grep -o '"role":"assistant"' | wc -l | tr -d ' ')
marked() { printf '%s' "$request" | grep -q "$1"; }
if marked ROLE_LEAF; then
  case $step in
    0) tool_call tc_leaf ask_host '{}' ;;
    *) finish "the leaf is done" ;;
  esac
elif marked ROLE_MIDDLE; then
  case $step in
    0) tool_call tc_middle_spawn spawn '{\"program\":\"leaf\",\"task\":\"ask the host\"}' ;;
    1) tool_call tc_middle_wait wait '{}' ;;
    *) finish "the middle episode is done" ;;
  esac
else
  case $step in
    0) tool_call tc_root_spawn spawn '{\"program\":\"middle\",\"task\":\"start the leaf\"}' ;;
    1) tool_call tc_root_wait wait '{}' ;;
    *) finish "the root is done" ;;
  esac
fi
"#;

/// Runs the binary headless over the tree the configuration declares, and
/// returns the log of the root and of every descendant, roots first.
fn headless_run(dir: &Path, config: &Value) -> (Vec<Vec<Value>>, i32) {
    let config_path = dir.join("config.json");
    std::fs::write(&config_path, serde_json::to_vec_pretty(config).unwrap()).unwrap();
    let log_dir = dir.join("log");
    let code = Command::new(FOE)
        .arg("--config")
        .arg(&config_path)
        .arg("--log-dir")
        .arg(&log_dir)
        .arg("--headless")
        .status()
        .unwrap()
        .code()
        .unwrap();
    fn read(dir: &Path, logs: &mut Vec<Vec<Value>>) {
        let file = std::fs::read_to_string(dir.join("episode.jsonl")).unwrap();
        logs.push(file.lines().map(|l| serde_json::from_str(l).unwrap()).collect());
        let children = dir.join("children");
        for child in std::fs::read_dir(children).into_iter().flatten().flatten() {
            read(&child.path(), logs);
        }
    }
    let mut logs = Vec::new();
    read(&log_dir, &mut logs);
    (logs, code)
}

/// Writes `body` to `dir/name` as an executable script and returns its path.
fn executable(dir: &Path, name: &str, body: &str) -> PathBuf {
    use std::os::unix::fs::PermissionsExt;
    let path = dir.join(name);
    std::fs::write(&path, body).unwrap();
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
    path
}

/// docs/protocol.md "Children": a `host/tool-call` forwarded to a process
/// with no host is answered with an error naming the tool. The tree below
/// declares no `seconds`, so nothing else bounds the wait; every episode in
/// it still reaches `episode/end`.
#[test]
fn a_host_tool_call_no_host_can_answer_ends_every_episode_in_the_tree() {
    let dir = scratch("no-host-uplink");
    let transport = executable(&dir, "transport.sh", TREE_TRANSPORT);
    let leaf = json!({
        "name": "leaf",
        "instructions": { "role": "ROLE_LEAF: call ask_host once." },
        "tools": ["ask_host"],
        "host_tools": { "ask_host": {
            "description": "Ask the host. Nothing in this tree answers it.",
            "params": { "type": "object", "properties": {}, "additionalProperties": false },
            "effect": "pure"
        }},
        "grants": { "read": [dir] },
        "budget": { "model_calls": 4 }
    });
    let middle = json!({
        "name": "middle",
        "instructions": { "role": "ROLE_MIDDLE: spawn the leaf and wait." },
        "tools": ["spawn", "wait", "notify"],
        "grants": { "read": [dir], "spawn": ["leaf"] },
        "budget": { "model_calls": 6, "max_depth": 1, "max_episodes": 2 },
        "programs": { "leaf": leaf }
    });
    let config = config(&dir, |c| {
        c["tools"] = json!(["spawn", "wait"]);
        c["grants"] = json!({ "read": [dir], "spawn": ["middle"] });
        c["budget"] = json!({ "model_calls": 12, "max_depth": 2, "max_episodes": 4 });
        c["model"] = json!({ "provider": "exec", "model": "tree", "exec": transport });
        c["programs"] = json!({ "middle": middle });
    });
    let (logs, code) = headless_run(&dir, &config);
    assert_eq!(code, 0);
    assert_eq!(logs.len(), 3, "the root, the middle episode, and the leaf each wrote a log");
    for log in &logs {
        let end = log.last().unwrap();
        assert_eq!(end["type"], "episode/end", "every episode ends: {:?}", types(log));
        assert_eq!(end["data"]["outcome"]["kind"], "completed");
    }
    let refused = logs
        .iter()
        .flatten()
        .find(|e| e["type"] == "tool/result" && e["data"]["name"] == "ask_host")
        .expect("the leaf recorded a result for its host tool call");
    assert_eq!(refused["data"]["is_error"], true);
    let rendered = refused["data"]["rendered"].as_str().unwrap();
    assert!(rendered.contains("ask_host") && rendered.contains("no host"), "{rendered}");
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
        c["tools"] = json!(["block", "list", "derive"]);
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

/// docs/config.md `budget` and docs/workflow.md "Relationship to the rest of
/// foe": a spawned child whose only descendant is a workflow model node
/// receives episode capacity for that model node, and the release reports
/// the whole subtree count.
#[test]
fn a_spawned_child_can_run_its_workflow_model_node() {
    let dir = scratch("spawned-workflow");
    let config = config(&dir, |c| {
        let mut answer = node_program("answer", &dir);
        answer["budget"] = json!({ "model_calls": 2, "max_depth": 0, "max_episodes": 1 });
        c["tools"] = json!(["spawn", "wait"]);
        c["grants"]["spawn"] = json!(["worker"]);
        c["budget"] = json!({ "model_calls": 6, "max_depth": 2, "max_episodes": 3 });
        c["programs"] = json!({ "worker": {
            "name": "worker", "instructions": { "role": "Run the workflow." }, "tools": ["block"],
            "grants": { "read": [dir] },
            "budget": { "model_calls": 2, "max_depth": 1, "max_episodes": 2 },
            "workflow": { "nodes": {
                "answer": { "model": answer, "follows": ["task"], "terminal": true }
            } }
        } });
    });
    let mut delegate = vec![text("delegating")];
    delegate.extend(call("tc_spawn", "spawn", r#"{"program":"worker","task":"answer"}"#));
    delegate.extend(call("tc_wait", "wait", "{}"));
    delegate.push(done("tool"));
    let child = vec![text("workflow result"), done("end")];
    let finish = vec![text("finished"), done("end")];
    let (events, code) = host_run(&dir, &config, vec![delegate, child, finish], |_, _| Value::Null);
    assert_eq!(code, 0, "{:?}", events.last());
    assert_eq!(events.last().unwrap()["data"]["outcome"], json!({ "kind": "completed", "value": "finished" }));
    let reserved = events.iter().find(|e| e["type"] == "budget/reserve").unwrap();
    assert_eq!(reserved["data"]["reserved"]["episodes"], 2, "the child's subtree holds its model node");
    let spawn = events.iter().find(|e| e["type"] == "spawn/start").unwrap();
    let worker_log = dir.join("log/children").join(spawn["data"]["child_id"].as_str().unwrap()).join("episode.jsonl");
    let worker: Vec<Value> =
        std::fs::read_to_string(worker_log).unwrap().lines().map(|line| serde_json::from_str(line).unwrap()).collect();
    let node = worker.iter().find(|e| e["type"] == "workflow/node-start").unwrap();
    assert_eq!(node["data"]["node"], "answer");
    assert_eq!(worker.last().unwrap()["data"]["outcome"], json!({ "kind": "completed", "value": "workflow result" }));
    let released = events.iter().find(|e| e["type"] == "budget/release").unwrap();
    assert_eq!(released["data"]["spent"]["episodes"], 2, "the reconstructed subtree count includes the model node");
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
        c["budget"]["input_tokens"] = json!(1000);
        c["budget"]["output_tokens"] = json!(12);
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
    let request = events.iter().find(|e| e["type"] == "model/request").unwrap();
    assert_eq!(request["data"]["max_output_tokens"], 12, "workflow recovery uses the remaining output allowance");
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
        c["tools"] = json!(["block", "list", "scan", "derive"]);
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

fn done_with(stop: &str, input: u64) -> Value {
    json!({ "kind": "done", "stop": stop, "usage": { "input": input, "output": 5, "cache_read": 0 } })
}

/// docs/compaction.md: when the projection of the next request crosses
/// the threshold, the oldest steps are summarized through one recorded
/// `cmp_` request under its own header, the next request opens with the
/// task and the continuation message, and the summarization call counts
/// against the model-call budget.
#[test]
fn a_projected_request_over_the_threshold_is_compacted_through_one_recorded_call() {
    let context = json!({ "compact": true, "window_tokens": 120, "reserve_tokens": 40, "keep_recent_tokens": 1, "margin_tokens": 0 });
    let step = |id: &str, input: u64| {
        let mut chunks = vec![text("reading")];
        chunks.extend(call(id, "read", r#"{"path": "f.txt"}"#));
        chunks.push(done_with("tool", input));
        chunks
    };
    let narrative = "## Goal\nRead f.txt.\n\n## Progress\nRead it.\n\n## Decisions\nNone.\n\n## Open items\nNone.\n\n## Next step\nFinish.";
    let responses = || {
        vec![
            step("tc_1", 10),
            step("tc_2", 90),
            vec![text(narrative), done_with("end", 30)],
            vec![text("Done."), done_with("end", 20)],
        ]
    };
    let dir = scratch("compaction");
    std::fs::write(dir.join("f.txt"), "line\n").unwrap();
    let config = config(&dir, |c| {
        c["budget"] = json!({ "model_calls": 4, "input_tokens": 1000, "output_tokens": 25 });
        c["context"] = context.clone();
    });
    let (events, code) = host_run(&dir, &config, responses(), |_, _| Value::Null);
    assert_eq!(code, 0, "{:?}", events.last());
    let requests: Vec<&Value> = events.iter().filter(|e| e["type"] == "model/request").collect();
    let ids: Vec<&str> = requests.iter().map(|r| r["data"]["request_id"].as_str().unwrap()).collect();
    assert_eq!(ids, ["rq_0001", "rq_0002", "cmp_0003", "rq_0004"], "one counter numbers every request");
    let output_caps: Vec<u64> = requests.iter().map(|r| r["data"]["max_output_tokens"].as_u64().unwrap()).collect();
    assert_eq!(
        output_caps,
        [25, 20, 15, 10],
        "ordinary, compaction, and post-compaction requests share one output allowance"
    );
    let by_type = |t: &str| events.iter().find(|e| e["type"] == t).unwrap_or_else(|| panic!("no {t}"));
    let start = by_type("compaction/start");
    assert_eq!(start["data"]["step"], 3);
    assert_eq!(start["data"]["trigger"], "threshold");
    assert_eq!(
        start["data"]["projected_tokens"],
        90 + 5 + 2 + 15,
        "input, output, result estimate, and the remaining output cap"
    );
    assert_eq!(start["data"]["reserved"]["model_calls"], 2);
    let summary = by_type("compaction/summary");
    assert_eq!(summary["data"]["first_kept_seq"], requests[1]["seq"], "the cut is the second step's request");
    assert_eq!(summary["data"]["summary_request_seq"], requests[2]["seq"]);
    assert_eq!(summary["data"]["summary"], narrative);
    assert_eq!(summary["data"]["state"]["task"], "do the thing");
    assert_eq!(summary["data"]["state"]["files"]["read"], json!(["f.txt"]));
    assert_eq!(
        summary["data"]["state"]["covered"],
        json!({ "first_seq": 1, "last_seq": requests[1]["seq"].as_u64().unwrap() - 1 })
    );
    assert_eq!(start["data"]["covered"], summary["data"]["state"]["covered"]);
    let end = by_type("compaction/end");
    assert_eq!((end["data"]["ok"].as_bool(), end["data"]["usage"]["input"].as_u64()), (Some(true), Some(30)));
    assert!(end["data"].get("error").is_none());
    let header_of = |request: &Value| events.iter().find(|e| e["seq"] == request["data"]["header_seq"]).unwrap();
    let summary_header = header_of(requests[2]);
    assert_eq!(summary_header["data"]["system"], foe_config::harness_text::COMPACTION_INSTRUCTION);
    assert_eq!(summary_header["data"]["tools"], json!([]));
    assert_eq!(header_of(requests[3])["data"]["tools"][0]["name"], "read", "the ordinary header returns");
    let messages = requests[3]["data"]["messages"].as_array().unwrap();
    assert_eq!(messages[0], json!({ "role": "user", "content": [{ "type": "text", "text": "do the thing" }] }));
    let continuation = messages[1]["content"][0]["text"].as_str().unwrap();
    assert!(continuation.starts_with("## Continuation state\n\ncovered: seq 1 to "), "{continuation}");
    assert!(continuation.contains("\nfiles_read:\n- f.txt\nfiles_written: (none)\n"), "{continuation}");
    assert!(continuation.ends_with(&format!("## Summary\n\n{narrative}")), "{continuation}");
    assert_eq!(messages[2]["tool_calls"][0]["id"], "tc_2", "the kept suffix starts with the second step");
    assert_eq!(messages.len(), 5, "the final-request warning follows the compacted context");
    assert!(
        messages.iter().any(|message| message.to_string().contains(foe_config::harness_text::FINAL_REQUEST)),
        "the last ordinary request carries the recorded warning"
    );
    let prompt = requests[2]["data"]["messages"][0]["content"][0]["text"].as_str().unwrap();
    assert!(prompt.starts_with("# Transcript\n\n[user]\ndo the thing\n\n[assistant]\nreading\n[call read"), "{prompt}");
    assert!(!prompt.contains("tc_2") && prompt.contains("[result read]"), "the span ends at the cut: {prompt}");

    // The summarization call is the third of three permitted calls, so the
    // step after it finds the budget spent.
    let dir = scratch("compaction-budget");
    std::fs::write(dir.join("f.txt"), "line\n").unwrap();
    let mut tighter = config.clone();
    tighter["budget"] = json!({ "model_calls": 3 });
    let (events, code) = host_run(&dir, &tighter, responses(), |_, _| Value::Null);
    assert_eq!(code, 3);
    assert_eq!(events.last().unwrap()["data"]["outcome"], json!({ "kind": "exhausted", "limit": "model_calls" }));
    assert_eq!(types(&events).iter().filter(|t| **t == "model/request").count(), 3);
    assert!(types(&events).contains(&"compaction/end"));
}

/// docs/compaction.md "Failure keeps the previous context": a failed
/// summarization is recorded and the episode continues with the context
/// it had.
#[test]
fn a_failed_summarization_leaves_the_context_as_it_was() {
    let dir = scratch("compaction-failed");
    std::fs::write(dir.join("f.txt"), "line\n").unwrap();
    let config = config(&dir, |c| {
        c["context"] = json!({ "compact": true, "window_tokens": 120, "reserve_tokens": 40, "keep_recent_tokens": 1, "margin_tokens": 0 });
    });
    let mut first = vec![text("reading")];
    first.extend(call("tc_1", "read", r#"{"path": "f.txt"}"#));
    first.push(done_with("tool", 10));
    let mut second = vec![text("reading")];
    second.extend(call("tc_2", "read", r#"{"path": "f.txt"}"#));
    second.push(done_with("tool", 90));
    let refused = vec![json!({ "kind": "error", "message": "overloaded", "retryable": true })];
    let last = vec![text("Done."), done_with("end", 20)];
    let (events, code) = host_run(&dir, &config, vec![first, second, refused, last], |_, _| Value::Null);
    assert_eq!(code, 0, "{:?}", events.last());
    let kinds = types(&events);
    assert!(!kinds.contains(&"compaction/summary") && !kinds.contains(&"request/retry"), "{kinds:?}");
    let end = events.iter().find(|e| e["type"] == "compaction/end").unwrap();
    assert_eq!((end["data"]["ok"].as_bool(), end["data"]["error"].as_str()), (Some(false), Some("overloaded")));
    let last = events.iter().rev().find(|e| e["type"] == "model/request").unwrap();
    assert_eq!(last["data"]["request_id"], "rq_0004");
    assert_eq!(last["data"]["messages"][0]["content"][0]["text"], "do the thing");
    let messages = last["data"]["messages"].as_array().unwrap();
    assert_eq!(messages.len(), 6, "the prior context and final-request warning are present");
    assert!(
        messages.iter().any(|message| message.to_string().contains(foe_config::harness_text::FINAL_REQUEST)),
        "failed compaction preserves the warning"
    );
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
    let outside = root.join("outside-grant");
    for sub in ["src", "tests", "tools"] {
        std::fs::create_dir_all(project.join(sub)).unwrap();
    }
    std::fs::create_dir_all(&outside).unwrap();
    std::fs::write(root.join("anthropic.key"), "sk-test\n").unwrap();
    // `plan` resolves every executable an example names, so a file has to
    // exist at each path under `project/tools`. Every file an example ships
    // other than its configuration, its README, and its runner is such a
    // program, and each example's README says to install it there.
    const NOT_PROGRAMS: [&str; 5] = ["config.json", "README.md", "run.sh", "run.py", "BUILD.bazel"];
    for entry in std::fs::read_dir(Path::new(EXAMPLES).join(name)).unwrap().flatten() {
        let file = entry.file_name();
        if !NOT_PROGRAMS.contains(&file.to_string_lossy().as_ref()) {
            std::fs::copy(entry.path(), project.join("tools").join(&file)).unwrap();
        }
    }
    let rewritten = text
        .replace("/home/user/.config/foe/anthropic.key", root.join("anthropic.key").to_str().unwrap())
        .replace("/home/user/outside-grant", outside.to_str().unwrap())
        .replace("/home/user/foe", Path::new(EXAMPLES).parent().unwrap().to_str().unwrap())
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

/// docs/design.md "Subagents and teams": `foe plan` reports each distinct
/// tool definition throughout the reachable tree, even when names repeat,
/// and omits a program no `grants.spawn` entry reaches.
#[test]
fn plan_reports_effective_authority_across_nested_descendants() {
    let dir = scratch("plan-authority");
    let root_exec = dir.join("root-tool");
    let child_exec = dir.join("child-tool");
    std::fs::write(&root_exec, "").unwrap();
    std::fs::write(&child_exec, "").unwrap();
    let grand = json!({
        "name": "grand", "instructions": { "role": "inspect" }, "tools": ["inspect"],
        "host_tools": { "inspect": { "description": "Inspect through the host", "params": {}, "effect": "reads" } },
        "grants": { "read": [dir] }, "budget": { "model_calls": 1 }
    });
    let unused = json!({
        "name": "unused", "instructions": { "role": "remain unreachable" }, "tools": ["hidden"],
        "host_tools": { "hidden": { "description": "Hidden declaration", "params": {}, "effect": "pure" } },
        "grants": { "read": [dir] }, "budget": { "model_calls": 1 }
    });
    let child = json!({
        "name": "child", "instructions": { "role": "delegate" }, "tools": ["inspect", "spawn"],
        "tool_defs": { "inspect": { "exec": child_exec, "description": "Inspect as the child" } },
        "grants": { "read": [dir], "spawn": ["grand"] }, "budget": { "model_calls": 2 },
        "programs": { "grand": grand }
    });
    let value = config(&dir, |c| {
        c["tools"] = json!(["inspect", "spawn"]);
        c["tool_defs"] = json!({ "inspect": { "exec": root_exec, "description": "Inspect at the root" } });
        c["grants"]["spawn"] = json!(["child"]);
        c["programs"] = json!({ "child": child, "unused": unused });
    });
    let path = dir.join("config.json");
    std::fs::write(&path, serde_json::to_vec_pretty(&value).unwrap()).unwrap();
    let report = plan(&path);
    let inspect: Vec<&Value> =
        report["authority"].as_array().unwrap().iter().filter(|row| row["name"] == "inspect").collect();
    assert_eq!(inspect.len(), 3, "same-name definitions remain distinct: {inspect:?}");
    assert!(inspect.iter().any(|row| row["programs"] == json!(["program"])));
    assert!(inspect.iter().any(|row| row["programs"] == json!(["program.programs.child"])));
    assert!(inspect.iter().any(|row| row["programs"] == json!(["program.programs.child.programs.grand"])));
    assert!(report["authority"].as_array().unwrap().iter().all(|row| row["name"] != "hidden"));
}

/// docs/workflow.md "The flow guarantee, stated exactly": plan reports
/// overlaps for model nodes and effectful tool nodes at every graph depth.
#[test]
fn plan_reports_write_overlap_for_every_kind_of_writer() {
    let dir = scratch("plan-writers");
    let model = json!({
        "name": "model", "instructions": { "role": "write" }, "tools": ["block"],
        "grants": { "read": [dir], "write": [dir] }, "budget": { "model_calls": 1 }
    });
    let value = config(&dir, |c| {
        c["tools"] = json!(["block", "write"]);
        c["host_tools"] = json!({ "write": {
            "description": "Write a value", "params": {}, "effect": "writes"
        }});
        c["grants"]["write"] = json!([dir]);
        c["workflow"] = json!({ "nodes": {
            "direct": { "tool": "write" },
            "nested": { "workflow": { "nodes": { "inner": { "tool": "write", "terminal": true } } } },
            "model": { "model": model, "terminal": true }
        }});
    });
    let path = dir.join("config.json");
    std::fs::write(&path, serde_json::to_vec_pretty(&value).unwrap()).unwrap();
    let report = plan(&path);
    let pairs = report["workflow"]["write_overlaps"].as_array().unwrap();
    assert_eq!(pairs.len(), 3, "three writers produce every pair: {pairs:?}");
    assert!(pairs
        .iter()
        .any(|pair| pair.as_array().is_some_and(|values| values.iter().any(|value| value == "nested/inner"))));
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
            assert_eq!(first["workflow"]["possible_firings"], json!(4), "survey once, propose once, apply twice");
            assert_eq!(first["workflow"]["max_possible_firings"], json!(4096));
            assert!(first["program"]["workflow"]["nodes"]["propose"]["model"].is_object());
            assert!(first["authority"].as_array().unwrap().iter().any(|row| {
                row["programs"]
                    .as_array()
                    .is_some_and(|paths| paths.iter().any(|path| path == "program.workflow.nodes.propose.model"))
            }));
        } else {
            assert_eq!(first["workflow"], Value::Null);
        }
    }
}

/// The recorded identity of every example program, under the fixed runtime
/// [`RECORDED_RUNTIME`] rather than the running binary. A recorded identity
/// is what tells a reader of a retained trajectory which program produced
/// it, so the hash a document resolves to is a compatibility surface: a
/// hash that moves silently makes every stored identity name a program
/// nobody can reproduce. A change here is a deliberate change to what the
/// model sees, never a side effect of moving code between crates.
#[rustfmt::skip]
const RECORDED_IDENTITIES: [(&str, &str); 12] = [
    ("budget-exhausted", "sha256:af0078c894de927d1f01586f545fba8ba2fbec60ddc2c7c6cfc0032589b1a588"),
    ("exec-transport", "sha256:30bb9640e11454c364774d00b49d855a16e23e6cbcdc9d3f87fb17fbe7782a09"),
    ("host-transport", "sha256:9c9eb52dee8f1b3894be57e14e533574190e055cffacc0c73f5330724cf8e9d6"),
    ("minimal", "sha256:b7df6749a94871b7605a1f86f87d49388fffba0d0f6cbd2438790ea310230553"),
    ("recovery-exhausted", "sha256:cf99c643160498696921acde68915e2febeaf4001e6c1844451333b6548f3943"),
    ("sandbox", "sha256:26d4d2ea3c61d04f1753067178c6c6442b3c3378b7694aeab67b5fdc9d424d9c"),
    ("self-extension", "sha256:80b174d57c5ab581e37204196224af8523b4b03b276237e1a980a5bff308fccf"),
    ("subagents", "sha256:3545dd72f0d601fc8ebc35d3947526a5cea4811f2d6d324c645b173e9860400e"),
    ("team", "sha256:052ea32afb50b4f043341a88ba02510caae96355962d7e0b8a89ab7c255b9356"),
    ("verification-unsatisfiable", "sha256:0fd18afbd179fd756c4c4e8c1500e158ca0268564a90c2835989a40e32c0c832"),
    ("workflow", "sha256:ef8b0786020697d9e20f01a22605df347de28a052a30768bbd7aac7965122aca"),
    ("wrap-a-binary", "sha256:f3231514efb28cb97f728a472776028bcdeb5bde44fe328aab31e1a96a2f217f"),
];

/// The runtime the recorded identities were computed under. The real one
/// hashes the running binary, so it differs on every build; pinning it here
/// leaves the configuration and the harness text as the only things the
/// recorded hashes measure. Passing `runtime_info()` instead would make this
/// test fail on every rebuild, and would measure the build where the point is
/// to hold the contract still.
fn recorded_runtime() -> foe_log::RuntimeInfo {
    foe_log::RuntimeInfo { version: "0.1.0".into(), build: "sha256:recorded".into() }
}

/// The built-in tool specifications the binary composes, which identity
/// hashes with the rest of the program. Which packs a binary links is the
/// binary's own decision, taken in `foe::run::extra_builtin_specs`, and a
/// test binary cannot reach the command line's modules — so this names the
/// two packs itself, and the test below fails if the two lists ever part.
fn builtin_specs() -> Vec<foe_config::ToolSpec> {
    foe_code::all().iter().map(|t| t.spec().clone()).chain(foe_core::team::builtin_specs()).collect()
}

/// `foe tools` with no configuration prints every built-in the binary
/// carries. The recorded identities are computed over [`builtin_specs`], so a
/// pack the binary gains or loses has to appear there too; without this check
/// the recorded hashes would go on describing programs the binary no longer
/// builds.
#[test]
fn the_recorded_builtins_are_the_ones_the_binary_links() {
    let effect = |spec: &foe_config::ToolSpec| serde_json::to_value(spec.effect).unwrap().as_str().unwrap().to_string();
    let mine: Vec<(String, String)> = std::iter::once(foe_config::tools::block_spec())
        .chain(builtin_specs())
        .map(|spec| (spec.name.clone(), effect(&spec)))
        .collect();
    let printed = Command::new(FOE).arg("tools").output().unwrap();
    let rows: Vec<(String, String)> = String::from_utf8(printed.stdout)
        .unwrap()
        .lines()
        .map(|line| {
            let mut fields = line.split_whitespace();
            let name = fields.next().expect("a row names a tool").to_string();
            (name, fields.nth(1).expect("a row names an effect").to_string())
        })
        .collect();
    assert_eq!(rows, mine, "the built-in list this test records has parted from the one the binary links");
}

/// docs/design.md "Programs and identity": every example program hashes to
/// the identity recorded for it.
#[test]
fn every_example_program_hashes_to_its_recorded_identity() {
    let dir = scratch("identity-recorded");
    let specs = builtin_specs();
    let recorded: std::collections::BTreeMap<&str, &str> = RECORDED_IDENTITIES.into_iter().collect();
    let found = examples();
    assert_eq!(found.len(), recorded.len(), "every example has a recorded identity");
    for (name, text) in found {
        let path = materialize(&dir, &name, &text, "the task the recording ignores");
        let program = foe_config::config::load(&path).unwrap_or_else(|e| panic!("{name}: {e}"));
        let identity = foe_config::identity::compute(&program, &specs, &recorded_runtime())
            .unwrap_or_else(|e| panic!("{name}: {e}"));
        assert_eq!(identity.hash, recorded[name.as_str()], "{name}: the program hashes to another identity");
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
        foe_config::schema::conforms(&inlined, &document).unwrap_or_else(|e| panic!("{name}: {e}"));
        foe_config::config::parse(&text).unwrap_or_else(|e| panic!("{name}: {e}"));
    }
    let mut broken: Value = serde_json::from_str(&examples()[0].1).unwrap();
    broken["budget"]["model_calls"] = json!("many");
    assert!(foe_config::schema::conforms(&inlined, &broken).is_err(), "a wrong type is caught through a $ref");
}

/// The issue's acceptance, checked against the process a person runs:
/// every help form succeeds and prints its screen on standard output, and
/// an invocation that fails still fails on standard error.
#[test]
fn help_exits_zero_on_standard_output_for_every_form() {
    for args in [
        vec!["--help"],
        vec!["help"],
        vec!["login", "--help"],
        vec!["view", "--help"],
        vec!["plan", "--help"],
        vec!["tools", "--help"],
        vec!["schema", "--help"],
        vec!["telemetry", "--help"],
    ] {
        let printed = Command::new(FOE).args(&args).output().unwrap();
        let text = String::from_utf8(printed.stdout).unwrap();
        assert!(printed.status.success(), "`foe {}` exited {:?}", args.join(" "), printed.status.code());
        assert!(text.starts_with("usage: foe"), "`foe {}` printed no usage line: {text}", args.join(" "));
        assert!(text.contains("\noptions:\n"), "`foe {}` documented no options: {text}", args.join(" "));
        assert!(printed.stderr.is_empty(), "`foe {}` wrote to standard error", args.join(" "));
    }
    let refused = Command::new(FOE).arg("--nonesuch").output().unwrap();
    assert_eq!(refused.status.code(), Some(1));
    assert!(String::from_utf8(refused.stderr).unwrap().contains("run `foe --help`"), "the refusal points at help");
    let bare = Command::new(FOE).output().unwrap();
    assert_eq!(bare.status.code(), Some(1), "a bare `foe` still refuses");
}
