//! The built binary end to end: episodes under `--host` driven by a scripted
//! host, `plan --json` over the examples, and the examples against the
//! schema the binary prints.

use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::ops::Deref;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

const FOE: &str = env!("CARGO_BIN_EXE_foe");
use foe_contract::SCHEMA;
const EXAMPLES: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../../examples");

struct ScratchDir {
    dir: Option<tempfile::TempDir>,
    path: PathBuf,
}

impl ScratchDir {
    fn new(name: &str) -> Self {
        assert_eq!(Path::new(name).file_name(), Some(name.as_ref()), "scratch name must be one path component");
        let dir =
            tempfile::Builder::new().prefix(&format!("cli-{name}-")).tempdir_in(env!("CARGO_TARGET_TMPDIR")).unwrap();
        let path = dir.path().canonicalize().unwrap();
        Self { dir: Some(dir), path }
    }
}

impl Deref for ScratchDir {
    type Target = Path;

    fn deref(&self) -> &Self::Target {
        &self.path
    }
}

impl AsRef<Path> for ScratchDir {
    fn as_ref(&self) -> &Path {
        &self.path
    }
}

impl serde::Serialize for ScratchDir {
    fn serialize<S: serde::Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serde::Serialize::serialize(&self.path, serializer)
    }
}

impl Drop for ScratchDir {
    fn drop(&mut self) {
        let Some(mut dir) = self.dir.take() else { return };
        if std::thread::panicking() {
            eprintln!("retained failed test directory: {}", self.path.display());
            dir.disable_cleanup(true);
            return;
        }
        dir.close().unwrap_or_else(|error| panic!("failed to remove test directory {}: {error}", self.path.display()));
    }
}

fn scratch(name: &str) -> ScratchDir {
    ScratchDir::new(name)
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
        "version": 4,
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

/// docs/log-format.md "Lifecycle": episode/start records the complete
/// policy after runtime-only paths have joined the declared grants.
#[test]
fn episode_start_records_resolved_permissions() {
    let dir = scratch("resolved-permissions");
    let value = config(&dir, |config| {
        config["sandbox"] = json!({"mode": "required"});
    });
    let (events, code) = host_run(&dir, &value, vec![vec![done("end")]], |_, _| unreachable!());
    assert_eq!(code, 0);
    let start = events.iter().find(|event| event["type"] == "episode/start").unwrap();
    let permissions = &start["data"]["sandbox"]["resolved_permissions"];
    assert!(permissions["read"].as_array().unwrap().iter().any(|entry| {
        entry["path"] == dir.to_string_lossy().as_ref() && entry["reason"] == "declared by contract.grants.read"
    }));
    assert!(permissions["write"]
        .as_array()
        .unwrap()
        .iter()
        .any(|entry| { entry["reason"] == "episode log and spill directory" }));
    assert!(permissions["read"].as_array().unwrap().iter().any(|entry| entry["reason"] == "shared-library lookup"));
    assert!(permissions["execute"]
        .as_array()
        .is_none_or(|entries| entries.iter().all(|entry| entry["path"] != "/bin" && entry["path"] != "/usr/bin")));
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

/// docs/log-format.md `episode/start`: a spawned episode records and
/// enforces the effective allowance supplied beside its declared contract.
#[test]
fn a_child_records_and_enforces_its_effective_budget() {
    let dir = scratch("effective-child-budget");
    std::fs::write(dir.join("f.txt"), "line\n").unwrap();
    let config = config(&dir, |c| c["budget"] = json!({ "model_calls": 4 }));
    let log_dir = dir.join("log");
    std::fs::create_dir(&log_dir).unwrap();
    std::fs::write(
        log_dir.join("child-launch.json"),
        serde_json::to_vec(&json!({
            "episode_id": "ep_child",
            "parent_id": "ep_parent",
            "team_id": "ep_parent",
            "effective_budget": {"model_calls": 1}
        }))
        .unwrap(),
    )
    .unwrap();
    let mut first = vec![text("reading")];
    first.extend(call("tc_1", "read", r#"{"path": "f.txt"}"#));
    first.push(done("tool"));
    let (events, code) = host_run(&dir, &config, vec![first], |_, _| Value::Null);
    assert_eq!(code, 3);
    assert_eq!(events[0]["data"]["effective_budget"]["model_calls"], 1);
    assert_eq!(events.last().unwrap()["data"]["outcome"], json!({ "kind": "exhausted", "limit": "model_calls" }));
}

/// A model transport for a headless run: one shell script for the whole
/// tree, which tells the episodes apart by the marker each contract's
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
    0) tool_call tc_middle_spawn spawn '{\"contract\":\"leaf\",\"task\":\"ask the host\"}' ;;
    1) tool_call tc_middle_wait wait '{}' ;;
    *) finish "the middle episode is done" ;;
  esac
else
  case $step in
    0) tool_call tc_root_spawn spawn '{\"contract\":\"middle\",\"task\":\"start the leaf\"}' ;;
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
    let transport_helpers = json!(["/usr/bin/sed", "/usr/bin/grep", "/usr/bin/wc", "/usr/bin/tr"]);
    let leaf = json!({
        "name": "leaf",
        "instructions": { "role": "ROLE_LEAF: call ask_host once." },
        "tools": ["ask_host"],
        "host_tools": { "ask_host": {
            "description": "Ask the host. Nothing in this tree answers it.",
            "params": { "type": "object", "properties": {}, "additionalProperties": false },
            "effect": "pure"
        }},
        "grants": { "read": [dir], "execute": transport_helpers },
        "budget": { "model_calls": 4 }
    });
    let middle = json!({
        "name": "middle",
        "instructions": { "role": "ROLE_MIDDLE: spawn the leaf and wait." },
        "tools": ["spawn", "wait", "notify"],
        "grants": { "read": [dir], "execute": transport_helpers, "spawn": ["leaf"] },
        "budget": { "model_calls": 6, "max_depth": 1, "max_episodes": 2 },
        "child_contracts": { "leaf": leaf }
    });
    let config = config(&dir, |c| {
        c["tools"] = json!(["spawn", "wait"]);
        c["grants"] = json!({ "read": [dir], "execute": transport_helpers, "spawn": ["middle"] });
        c["budget"] = json!({ "model_calls": 12, "max_depth": 2, "max_episodes": 4 });
        c["model"] = json!({ "provider": "exec", "model": "tree", "exec": transport });
        c["child_contracts"] = json!({ "middle": middle });
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

/// A child contract for a workflow model node, reading `dir`.
fn node_contract(name: &str, dir: &Path) -> Value {
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
            "propose": { "model": node_contract("propose", &dir), "follows": ["manifest", "task"],
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
    assert_eq!(spawn["data"]["contract"], "propose");
    let start = events.iter().find(|e| e["type"] == "workflow/node-start" && e["data"]["node"] == "propose").unwrap();
    let child_id = start["data"]["child_id"].as_str().unwrap();
    let child_log = dir.join("log/children").join(child_id).join("episode.jsonl");
    let child: Vec<Value> =
        std::fs::read_to_string(&child_log).unwrap().lines().map(|l| serde_json::from_str(l).unwrap()).collect();
    let launch: Value =
        serde_json::from_slice(&std::fs::read(child_log.parent().unwrap().join("child-launch.json")).unwrap()).unwrap();
    assert_eq!(child[0]["data"]["contract_fingerprint"], launch["expected_contract_fingerprint"]);
    assert_eq!(child[0]["data"]["effective_budget"], launch["effective_budget"]);
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
        let mut answer = node_contract("answer", &dir);
        answer["budget"] = json!({ "model_calls": 2, "max_depth": 0, "max_episodes": 1 });
        c["tools"] = json!(["spawn", "wait"]);
        c["grants"]["spawn"] = json!(["worker"]);
        c["budget"] = json!({ "model_calls": 6, "max_depth": 2, "max_episodes": 3 });
        c["child_contracts"] = json!({ "worker": {
            "name": "worker", "instructions": { "role": "Run the workflow." }, "tools": ["block"],
            "grants": { "read": [dir] },
            "budget": { "model_calls": 2, "max_depth": 1, "max_episodes": 2 },
            "workflow": { "nodes": {
                "answer": { "model": answer, "follows": ["task"], "terminal": true }
            } }
        } });
    });
    let mut delegate = vec![text("delegating")];
    delegate.extend(call("tc_spawn", "spawn", r#"{"contract":"worker","task":"answer"}"#));
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

/// docs/protocol.md "Children": a child replaces inherited executable
/// descriptors with close-on-exec copies before it starts any tool.
#[test]
fn a_child_tool_inherits_no_contract_tree_descriptor() {
    let dir = scratch("child-executable-descriptors");
    let probe = dir.join("probe");
    std::fs::write(
        &probe,
        "#!/bin/sh\nfor fd in 63 64 65 66; do [ ! -e /proc/self/fd/$fd ] || exit 9; done\nprintf 'clean\\n'\n",
    )
    .unwrap();
    std::fs::set_permissions(&probe, std::os::unix::fs::PermissionsExt::from_mode(0o755)).unwrap();
    let config = config(&dir, |c| {
        c["tools"] = json!(["spawn", "wait"]);
        c["grants"]["spawn"] = json!(["worker"]);
        c["budget"] = json!({ "model_calls": 4, "max_depth": 1, "max_episodes": 2 });
        c["child_contracts"] = json!({ "worker": {
            "name": "worker", "instructions": { "role": "Run the probe." }, "tools": ["probe"],
            "tool_defs": { "probe": { "exec": probe, "description": "Checks inherited descriptors." } },
            "grants": { "read": [dir] }, "budget": { "model_calls": 2, "max_depth": 0 }
        } });
    });
    let mut delegate = call("tc_spawn", "spawn", r#"{"contract":"worker","task":"run the probe"}"#);
    delegate.extend(call("tc_wait", "wait", "{}"));
    delegate.push(done("tool"));
    let mut run_probe = call("tc_probe", "probe", r#"{"args":[]}"#);
    run_probe.push(done("tool"));
    let responses =
        vec![delegate, run_probe, vec![text("probe finished"), done("end")], vec![text("done"), done("end")]];
    let (events, code) = host_run(&dir, &config, responses, |_, _| Value::Null);
    assert_eq!(code, 0, "{:?}", events.last());
    let child_id =
        events.iter().find(|event| event["type"] == "spawn/start").unwrap()["data"]["child_id"].as_str().unwrap();
    let child = child_events(&dir, child_id);
    let result = child.iter().find(|event| event["type"] == "tool/result" && event["data"]["name"] == "probe").unwrap();
    assert_eq!(result["data"]["value"]["exit_code"], 0, "{result}");
    assert!(
        std::fs::read_dir(&dir).unwrap().all(|entry| !entry
            .unwrap()
            .file_name()
            .to_string_lossy()
            .starts_with("foe-executables-")),
        "the confined root removes its private executable directory"
    );
}

/// docs/design.md "Contract construction": a child receives the executable
/// snapshots needed to reconstruct its full declared fingerprint, including an
/// ungranted descendant that the child cannot start.
#[test]
fn a_child_starts_after_an_ungranted_descendant_executable_source_is_deleted() {
    let dir = scratch("child-declared-executable-fingerprint");
    let latent = dir.join("latent-tool");
    std::fs::write(&latent, "#!/bin/sh\nprintf 'latent\\n'\n").unwrap();
    std::fs::set_permissions(&latent, std::os::unix::fs::PermissionsExt::from_mode(0o755)).unwrap();
    let config = config(&dir, |config| {
        config["tools"] = json!(["delete", "spawn", "wait"]);
        config["host_tools"] = json!({
            "delete": {"description": "Deletes the latent executable.", "params": {"type": "object"}, "effect": "writes"}
        });
        config["grants"] = json!({"read": [dir], "write": [dir], "spawn": ["worker"]});
        config["budget"] = json!({"model_calls": 6, "max_depth": 2, "max_episodes": 2});
        config["child_contracts"] = json!({"worker": {
            "name": "worker", "instructions": {"role": "Complete without spawning."}, "tools": ["block"],
            "grants": {"read": [dir]}, "budget": {"model_calls": 2, "max_depth": 1},
            "child_contracts": {"ungranted": {
                "name": "ungranted", "instructions": {"role": "Remain unreachable."}, "tools": ["latent"],
                "tool_defs": {"latent": {"exec": latent, "description": "A declared executable."}},
                "grants": {"read": [dir]}, "budget": {"model_calls": 1, "max_depth": 0}
            }}
        }});
    });
    let mut delete = call("tc_delete", "delete", "{}");
    delete.push(done("tool"));
    let mut delegate = call("tc_spawn", "spawn", r#"{"contract":"worker","task":"start"}"#);
    delegate.extend(call("tc_wait", "wait", "{}"));
    delegate.push(done("tool"));
    let responses =
        vec![delete, delegate, vec![text("child ready"), done("end")], vec![text("root ready"), done("end")]];
    let source = latent.clone();
    let (events, code) = host_run(&dir, &config, responses, move |name, _| {
        assert_eq!(name, "delete");
        std::fs::remove_file(&source).unwrap();
        json!({"removed": true})
    });
    assert_eq!(code, 0, "{:?}", events.last());
    assert!(!latent.exists());
    let child_id =
        events.iter().find(|event| event["type"] == "spawn/start").unwrap()["data"]["child_id"].as_str().unwrap();
    let child = child_events(&dir, child_id);
    assert_eq!(child.last().unwrap()["data"]["outcome"], json!({"kind": "completed", "value": "child ready"}));
    let launch: Value = serde_json::from_slice(
        &std::fs::read(dir.join("log/children").join(child_id).join("child-launch.json")).unwrap(),
    )
    .unwrap();
    assert_eq!(child[0]["data"]["contract_fingerprint"], launch["expected_contract_fingerprint"]);
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
        "#!/bin/sh\nstate=state\nif [ ! -f \"$state\" ]; then touch \"$state\"; echo failing; exit 1; fi\necho fine\n",
    )
    .unwrap();
    std::fs::set_permissions(&script, std::os::unix::fs::PermissionsExt::from_mode(0o755)).unwrap();
    let config = config(&dir, |c| {
        c["tools"] = json!(["flaky"]);
        c["tool_defs"] = json!({ "flaky": { "exec": script, "description": "Fails once." } });
        c["grants"]["write"] = json!([dir]);
        c["grants"]["execute"] = json!(["/usr/bin/touch"]);
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
    assert_eq!(recovery["data"]["cause"], "process-exit");
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
            "propose": { "model": node_contract("propose", &dir), "follows": ["manifest", "survey"],
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

/// The built-in coding workflow's shape, as `foe "task" --verify PATH`
/// composes it when `verifier` is set and as the bare form composes it
/// otherwise: implementation feeds an assessment choice, and its repair
/// branch feeds a fresh coding node. A verifier governs both branches at the
/// root. Every episode may call it while working.
/// The wiring itself is pinned by the unit tests over `builtin_contract_document`;
/// the bare form cannot run under a scripted transport, because the exec
/// provider needs a `model` option no flag sets, so these runs drive the
/// same document under `--host`.
fn coding_workflow(dir: &Path, verifier: Option<&Path>) -> Value {
    let node = |name: &str, tools: Value| {
        json!({
            "name": name, "instructions": { "role": "Work on the task." }, "tools": tools,
            "grants": { "read": [dir], "write": [dir] }, "budget": { "model_calls": 4 }
        })
    };
    let mut implement = node("implement-task", json!(["read"]));
    let completion = json!({ "returns": {
        "type": "object",
        "properties": { "summary": { "type": "string", "minLength": 1 } },
        "required": ["summary"],
        "additionalProperties": false
    } });
    let mut assessment = node("assess-task", json!(["read"]));
    assessment["done_when"] = completion.clone();
    let mut repair = node("repair-task", json!(["read"]));
    repair["done_when"] = completion;
    let mut value = config(dir, |c| {
        c["grants"]["write"] = json!([dir]);
        c["budget"] = json!({ "model_calls": 12, "max_episodes": 4, "max_concurrent": 1 });
    });
    if let Some(script) = verifier {
        let def = json!({ "check": { "exec": script, "description": "Prints one finding per line; silence is acceptance." } });
        implement["tools"] = json!(["read", "check"]);
        implement["tool_defs"] = def.clone();
        assessment["tools"] = json!(["read", "check"]);
        assessment["tool_defs"] = def.clone();
        repair["tools"] = json!(["read", "check"]);
        repair["tool_defs"] = def.clone();
        value["budget"]["max_episodes"] = json!(16);
        value["tools"] = json!(["read", "check"]);
        value["tool_defs"] = def;
        value["done_when"] = json!({ "verify": "check", "retries": 12 });
    }
    value["workflow"] = json!({
        "nodes": {
            "implement-task": { "model": implement, "follows": ["task"] },
            "assess-task": {
                "model": assessment,
                "follows": ["task", "implement-task"],
                "branches": { "accept": [], "repair": ["repair-task"] }
            },
            "repair-task": {
                "model": repair,
                "follows": ["task", "implement-task", "assess-task"],
                "terminal": true
            }
        },
        "recovery": { "enabled": false }
    });
    if verifier.is_some() {
        value["workflow"]["nodes"]["assess-task"]["max_fires"] = json!(13);
        value["workflow"]["nodes"]["repair-task"]["max_fires"] = json!(13);
    }
    value
}

fn workflow_return(value: Value) -> Vec<Value> {
    let mut chunks = vec![text("returning the assessed result")];
    chunks.extend(call("tc_return", "return", &json!({ "value": value }).to_string()));
    chunks.push(done("tool"));
    chunks
}

fn child_events(dir: &Path, child_id: &str) -> Vec<Value> {
    let file = dir.join("log/children").join(child_id).join("episode.jsonl");
    std::fs::read_to_string(file).unwrap().lines().map(|l| serde_json::from_str(l).unwrap()).collect()
}

/// docs/tools.md `session`: a workflow child may release an explicitly
/// authorized task-lifetime process to the enclosing task environment.
/// The process and its output survive both child settlement and the root
/// foe invocation.
#[test]
fn a_workflow_child_can_release_a_task_lifetime_session() {
    struct ProcessGroup(i64);
    impl Drop for ProcessGroup {
        fn drop(&mut self) {
            let _ = Command::new("/bin/kill").args(["-TERM", "--", &format!("-{}", self.0)]).status();
        }
    }

    let dir = scratch("workflow-task-session");
    let mut config = config(&dir, |c| {
        c["tools"] = json!(["session", "block"]);
        c["grants"] = json!({ "read": [dir], "write": [dir], "execute": ["/bin/sleep"], "task_session": true });
        c["budget"] = json!({ "model_calls": 4 });
    });
    config["workflow"] = json!({ "nodes": {
        "serve": {
            "model": {
                "name": "serve", "instructions": { "role": "Start the service." },
                "tools": ["session", "block"],
                "grants": {
                    "read": [dir], "write": [dir], "execute": ["/bin/sleep"], "task_session": true
                },
                "budget": { "model_calls": 2 }
            },
            "follows": ["task"], "terminal": true
        }
    }});
    let args = serde_json::to_string(&json!({
        "action": "start",
        "command": "printf 'before\\n'; /bin/sleep 0.3; printf 'after\\n'; printf 'warning\\n' >&2; exec /bin/sleep 30",
        "lifetime": "task"
    }))
    .unwrap();
    let mut start = vec![text("starting")];
    start.extend(call("tc_start", "session", &args));
    start.push(done("tool"));
    let finish = vec![text("service started"), done("end")];
    let (events, code) = host_run(&dir, &config, vec![start, finish], |_, _| Value::Null);
    assert_eq!(code, 0, "{:?}", events.last());

    let node = events.iter().find(|event| event["type"] == "workflow/node-start").unwrap();
    let child_id = node["data"]["child_id"].as_str().unwrap();
    let child = child_events(&dir, child_id);
    let release = child
        .iter()
        .find(|event| {
            event["type"] == "tool/result"
                && event["data"]["synthetic"] == true
                && event["data"]["value"]["disposition"] == "released_to_task_environment"
        })
        .expect("child settlement recorded the task-environment release");
    let pid = release["data"]["value"]["pid"].as_u64().unwrap();
    let process_group = release["data"]["value"]["process_group"].as_i64().unwrap();
    let _cleanup = ProcessGroup(process_group);
    assert_eq!(pid as i64, process_group);
    assert!(Path::new(&format!("/proc/{pid}")).exists(), "the process survived the root foe invocation");

    let spill = dir.join("log/children").join(child_id).join("spill");
    let outputs: Vec<_> = std::fs::read_dir(spill).unwrap().flatten().map(|entry| entry.path()).collect();
    let stdout = outputs.iter().find(|path| path.extension().is_some_and(|ext| ext == "stdout")).unwrap();
    let stderr = outputs.iter().find(|path| path.extension().is_some_and(|ext| ext == "stderr")).unwrap();
    let continued = (0..100).any(|_| {
        std::thread::sleep(std::time::Duration::from_millis(10));
        std::fs::read_to_string(stdout).is_ok_and(|text| text == "before\nafter\n")
            && std::fs::read_to_string(stderr).is_ok_and(|text| text == "warning\n")
    });
    assert!(continued, "stdout and stderr remained writable after child settlement and foe exit");
    assert_eq!(child.last().unwrap()["type"], "episode/end");
}

/// docs/design.md "The command line": an accepted assessment reaches the
/// root verifier. Its evidence remains in the root account, and repair does
/// not fire.
#[test]
fn accepted_assessment_is_verified_at_the_workflow_root() {
    let dir = scratch("verify-assessment");
    let script = executable(&dir, "check", "#!/bin/sh\nexit 0\n");
    let config = coding_workflow(&dir, Some(&script));
    let implement = vec![text("implemented the change"), done("end")];
    let assessment = workflow_return(json!({ "branch": "accept", "summary": "independently assessed" }));
    let (events, code) = host_run(&dir, &config, vec![implement, assessment], |_, _| Value::Null);
    assert_eq!(code, 0, "{:?}", events.last());
    assert_eq!(
        events.last().unwrap()["data"]["outcome"],
        json!({ "kind": "completed", "value": { "branch": "accept", "summary": "independently assessed" } })
    );
    assert_eq!(
        node_starts(&events),
        [("implement-task".into(), 1), ("assess-task".into(), 1)],
        "the accepted branch ends before repair"
    );
    let evidence =
        events.iter().find(|event| event["type"] == "verification/result").expect("root verification is logged");
    assert_eq!(evidence["data"]["status"], "accepted");
    assert_eq!(evidence["data"]["tool"], "check");
    assert_eq!(evidence["data"]["findings"], json!([]));
    let hashed = {
        use sha2::Digest;
        format!("sha256:{}", hex::encode(sha2::Sha256::digest(std::fs::read(&script).unwrap())))
    };
    assert_eq!(evidence["data"]["verifier_fingerprint"], json!(hashed));
    assert_eq!(std::fs::read_dir(dir.join("log/children")).unwrap().count(), 2);
}

/// The same accepted branch completes from the assessment's typed value when
/// no verifier is configured.
#[test]
fn without_a_verifier_the_assessment_runs() {
    let dir = scratch("verify-absent");
    let config = coding_workflow(&dir, None);
    let implement = vec![text("implemented"), done("end")];
    let assessment = workflow_return(json!({ "branch": "accept", "summary": "assessed" }));
    let (events, code) = host_run(&dir, &config, vec![implement, assessment], |_, _| Value::Null);
    assert_eq!(code, 0, "{:?}", events.last());
    assert_eq!(
        events.last().unwrap()["data"]["outcome"],
        json!({ "kind": "completed", "value": { "branch": "accept", "summary": "assessed" } })
    );
    assert_eq!(node_starts(&events), [("implement-task".into(), 1), ("assess-task".into(), 1)]);
    assert_eq!(std::fs::read_dir(dir.join("log/children")).unwrap().count(), 2);
}

/// docs/design.md "The command line": a repair branch carries the assessment
/// value into one fresh coding child, whose typed value completes the workflow.
#[test]
fn assessment_findings_activate_a_fresh_repair() {
    let dir = scratch("assessment-repair");
    let config = coding_workflow(&dir, None);
    let implement = vec![text("implemented"), done("end")];
    let assessment = workflow_return(json!({ "branch": "repair", "summary": "one defect" }));
    let repair = workflow_return(json!({ "summary": "repaired" }));
    let (events, code) = host_run(&dir, &config, vec![implement, assessment, repair], |_, _| Value::Null);
    assert_eq!(code, 0, "{:?}", events.last());
    assert_eq!(
        events.last().unwrap()["data"]["outcome"],
        json!({ "kind": "completed", "value": { "summary": "repaired" } })
    );
    assert_eq!(
        node_starts(&events),
        [("implement-task".into(), 1), ("assess-task".into(), 1), ("repair-task".into(), 1)]
    );
}

/// docs/design.md "The command line": root verifier findings re-fire the
/// assessment, which can activate repair before the verifier runs again.
#[test]
fn root_verifier_findings_can_turn_accept_into_repair() {
    let dir = scratch("verify-then-repair");
    let script = executable(
        &dir,
        "check",
        "#!/bin/sh\nstate=./.verification-attempted\nif [ ! -f \"$state\" ]; then : >\"$state\"; echo 'repair required'; fi\n",
    );
    let config = coding_workflow(&dir, Some(&script));
    let implement = vec![text("implemented"), done("end")];
    let first_assessment = workflow_return(json!({ "branch": "accept", "summary": "initial acceptance" }));
    let revised_assessment = workflow_return(json!({ "branch": "repair", "summary": "verifier finding" }));
    let repair = workflow_return(json!({ "summary": "repaired" }));
    let (events, code) =
        host_run(&dir, &config, vec![implement, first_assessment, revised_assessment, repair], |_, _| Value::Null);
    assert_eq!(code, 0, "{:?}", events.last());
    assert_eq!(
        events.last().unwrap()["data"]["outcome"],
        json!({ "kind": "completed", "value": { "summary": "repaired" } })
    );
    assert_eq!(
        node_starts(&events),
        [("implement-task".into(), 1), ("assess-task".into(), 1), ("assess-task".into(), 2), ("repair-task".into(), 1)]
    );
    let statuses: Vec<_> = events
        .iter()
        .filter(|event| event["type"] == "verification/result")
        .map(|event| event["data"]["status"].as_str().unwrap())
        .collect();
    assert_eq!(statuses, ["findings", "accepted"]);
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
        90 + 5 + 23 + 15,
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
    assert_eq!(summary_header["data"]["system"], foe_contract::harness_text::COMPACTION_INSTRUCTION);
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
        messages.iter().any(|message| message.to_string().contains(foe_contract::harness_text::FINAL_REQUEST)),
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
        messages.iter().any(|message| message.to_string().contains(foe_contract::harness_text::FINAL_REQUEST)),
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
    // contract, and each example's README says to install it there.
    const NON_EXECUTABLE_FILES: [&str; 5] = ["config.json", "README.md", "run.sh", "run.py", "BUILD.bazel"];
    for entry in std::fs::read_dir(Path::new(EXAMPLES).join(name)).unwrap().flatten() {
        let file = entry.file_name();
        if !NON_EXECUTABLE_FILES.contains(&file.to_string_lossy().as_ref()) {
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
/// and omits a contract no `grants.spawn` entry reaches.
#[test]
fn plan_reports_reachable_tools_and_resolved_permissions() {
    let dir = scratch("plan-permissions");
    let root_exec = dir.join("root-tool");
    let child_exec = dir.join("child-tool");
    let credential = dir.join("model.key");
    std::fs::write(&root_exec, "#!/bin/sh\nexit 0\n").unwrap();
    std::fs::write(&child_exec, "#!/bin/sh\nexit 0\n").unwrap();
    std::fs::set_permissions(&root_exec, std::os::unix::fs::PermissionsExt::from_mode(0o755)).unwrap();
    std::fs::set_permissions(&child_exec, std::os::unix::fs::PermissionsExt::from_mode(0o755)).unwrap();
    std::fs::write(&credential, "secret\n").unwrap();
    let grand = json!({
        "name": "grand", "instructions": { "role": "inspect" }, "tools": ["inspect"],
        "host_tools": { "inspect": { "description": "Inspect through the host", "params": {}, "effect": "reads" } },
        "model": {"provider": "openai", "model": "test", "api_key_file": credential},
        "grants": { "read": [dir] }, "budget": { "model_calls": 1 }
    });
    let unused = json!({
        "name": "unused", "instructions": { "role": "remain unreachable" }, "tools": ["hidden"],
        "host_tools": { "hidden": { "description": "Hidden declaration", "params": {}, "effect": "pure" } },
        "grants": { "read": [dir] }, "budget": { "model_calls": 1 }
    });
    let child = json!({
        "name": "child", "instructions": { "role": "delegate" }, "tools": ["inspect", "spawn"],
        "tool_defs": { "inspect": {
            "exec": child_exec, "description": "Inspect as the child", "network": true
        } },
        "grants": { "read": [dir], "spawn": ["grand"] }, "budget": { "model_calls": 2 },
        "child_contracts": { "grand": grand }
    });
    let value = config(&dir, |c| {
        c["tools"] = json!(["inspect", "spawn"]);
        c["tool_defs"] = json!({ "inspect": { "exec": root_exec, "description": "Inspect at the root" } });
        c["grants"]["spawn"] = json!(["child"]);
        c["child_contracts"] = json!({ "child": child, "unused": unused });
    });
    let path = dir.join("config.json");
    std::fs::write(&path, serde_json::to_vec_pretty(&value).unwrap()).unwrap();
    let report = plan(&path);
    let inspect: Vec<&Value> =
        report["reachable_tools"].as_array().unwrap().iter().filter(|row| row["name"] == "inspect").collect();
    assert_eq!(inspect.len(), 3, "same-name definitions remain distinct: {inspect:?}");
    assert!(inspect.iter().any(|row| row["contract_paths"] == json!(["contract"])));
    assert!(inspect.iter().any(|row| row["contract_paths"] == json!(["contract.child_contracts.child"])));
    assert!(inspect
        .iter()
        .any(|row| row["contract_paths"] == json!(["contract.child_contracts.child.child_contracts.grand"])));
    assert!(report["reachable_tools"].as_array().unwrap().iter().all(|row| row["name"] != "hidden"));
    let contracts = report["resolved_permissions"].as_array().unwrap();
    assert_eq!(contracts.len(), 3, "one permission set is reported for each reachable contract");
    let root = contracts.iter().find(|row| row["contract"] == "contract").unwrap();
    let execute = root["permissions"]["execute"].as_array().unwrap();
    assert!(execute.iter().any(|entry| {
        let digest = entry["sha256"].as_str().filter(|digest| digest.len() == 64);
        digest.is_some_and(|digest| entry["path"] == format!("captured:{digest}"))
            && entry["reason"].as_str().unwrap().contains("contract.child_contracts.child.tool_defs.inspect")
            && entry["reason"].as_str().unwrap().contains(child_exec.to_string_lossy().as_ref())
    }));
    assert!(
        execute.iter().all(|entry| entry["path"] != child_exec.to_string_lossy().as_ref()),
        "the captured tool's source path is not an executable object"
    );
    assert!(root["permissions"]["connect_tcp"]
        .as_array()
        .unwrap()
        .iter()
        .any(|reason| reason.as_str().unwrap().contains("contract.child_contracts.child.tool_defs.inspect.network")));
    assert!(root["permissions"]["read"].as_array().unwrap().iter().any(|entry| {
        entry["path"] == credential.to_string_lossy().as_ref()
            && entry["reason"].as_str().unwrap().contains("contract.child_contracts.child.child_contracts.grand")
    }));
}

/// docs/config.md grants.execute: plan warns before a kernel-enforced shell
/// contract spends a model request when it delegates no external command.
#[test]
fn plan_warns_when_shell_tools_cannot_start_external_commands() {
    let dir = scratch("plan-shell-permissions");
    let value = config(&dir, |contract| {
        contract["tools"] = json!(["bash"]);
        contract["grants"]["execute"] = json!([]);
    });
    let path = dir.join("config.json");
    std::fs::write(&path, serde_json::to_vec_pretty(&value).unwrap()).unwrap();
    let report = plan(&path);
    assert_eq!(
        report["warnings"],
        json!([{
            "contract": "contract",
            "code": "external-commands-unavailable",
            "configuration_key": "contract.grants.execute",
            "message": "contract.tools selects a shell tool while contract.grants.execute is empty. Shell built-ins remain available. Kernel-enforced runs require each external command's absolute file or directory in that key."
        }])
    );

    let mut value = value;
    value["grants"]["execute"] = json!(["/usr/bin/python3"]);
    std::fs::write(&path, serde_json::to_vec_pretty(&value).unwrap()).unwrap();
    assert_eq!(plan(&path)["warnings"], json!([]));
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

/// docs/design.md "Execution contracts and fingerprints": fingerprint excludes the task and
/// the resolved paths, so the same contract in another directory hashes the
/// same.
#[test]
fn plan_reports_an_fingerprint_that_ignores_task_and_paths() {
    let a = scratch("plan-a");
    let b = scratch("plan-b");
    for (name, text) in examples() {
        let first = plan(&materialize(&a, &name, &text, "first task"));
        let second = plan(&materialize(&b, &name, &text, "second task"));
        let fingerprint = first["contract_fingerprint"].as_str().unwrap();
        assert!(fingerprint.starts_with("sha256:") && fingerprint.len() == 71, "{name}: {fingerprint}");
        assert_eq!(
            first["contract_fingerprint"], second["contract_fingerprint"],
            "{name}: fingerprint ignores task and paths"
        );
        let canonical = foe_contract::fingerprint::canonical(&first["fingerprint_document"]);
        let rehashed = format!("sha256:{}", foe_contract::fingerprint::sha256_hex(canonical.as_bytes()));
        assert_eq!(
            rehashed, fingerprint,
            "{name}: the emitted fingerprint document rehashes to the reported fingerprint"
        );
        assert_eq!(first["contract"]["name"], json!(serde_json::from_str::<Value>(&text).unwrap()["name"]));
        assert!(first["contract"].get("task").is_none(), "{name}: the contract omits the task");
        if name == "workflow" {
            assert_eq!(first["workflow"]["terminal"], json!(["apply"]), "plan reports the workflow's terminal node");
            assert_eq!(first["workflow"]["cycles"], json!([]));
            assert_eq!(first["workflow"]["possible_firings"], json!(4), "survey once, propose once, apply twice");
            assert_eq!(first["workflow"]["max_possible_firings"], json!(4096));
            assert!(first["contract"]["workflow"]["nodes"]["propose"]["model"].is_object());
            assert!(first["reachable_tools"].as_array().unwrap().iter().any(|row| {
                row["contract_paths"]
                    .as_array()
                    .is_some_and(|paths| paths.iter().any(|path| path == "contract.workflow.nodes.propose.model"))
            }));
        } else {
            assert_eq!(first["workflow"], Value::Null);
        }
    }
}

/// The recorded fingerprint of every example contract, under the fixed runtime
/// [`recorded_runtime`] rather than the running binary. A retained trajectory
/// uses its fingerprint to identify the execution contract that produced it.
/// These values change only when the fingerprint inputs change.
#[rustfmt::skip]
const RECORDED_FINGERPRINTS: [(&str, &str); 12] = [
    ("budget-exhausted", "sha256:46fefc2dcaf73d8b9b258e068a2ef9621d9eabbbe1963eb1d32170a974f1faba"),
    ("exec-transport", "sha256:e831dbc44bbe1f6d666e3033a70049b3b44e5fb9e61e39425cb33d6f6098ae45"),
    ("host-transport", "sha256:6edc5655961a1532619a5cd9317905a4eff8d611907b9175b69874c8f77fab19"),
    ("minimal", "sha256:3749aba577f9a7014716d95b61d965b89b9f8c1c96fa185c9dc0ba114836ac0c"),
    ("recovery-exhausted", "sha256:f6e34210be5007468b1f9c67fa3f0acb104e5c4257a666fe18db976571866305"),
    ("sandbox", "sha256:d782d818af096573c8df312bace6be8ef32e0b80f781793e51bd6c13fef1bc91"),
    ("self-extension", "sha256:786075013e433f761e4b871d36c9c6a26bd3cc36aa7772209a230d01d5974e82"),
    ("subagents", "sha256:80d46911ef1aadc51635e89d95c1cf535b53fea15b71771414283f5015db17d3"),
    ("team", "sha256:938a3751b7c02a69ab081a01519f882de64397cd881b7f7d10219de274dc95b4"),
    ("verification-unsatisfiable", "sha256:6d05142cea4217d0c2a33d20f5dc7d066d42e6c6102f8a3ec2f3b7119b61fb60"),
    ("workflow", "sha256:15dae3560e510ebc03414debdd01a64c761360718ab072bf9d1d089bb0337f4e"),
    ("wrap-a-binary", "sha256:b006945bf76bc106eba9776e091d31685fb00d8f7ebe3f6f32c17b0a2a655fba"),
];

/// The runtime the recorded fingerprints were computed under. The real one
/// hashes the running binary, so it differs on every build; pinning it here
/// leaves the configuration and the harness text as the only things the
/// recorded hashes measure. Passing `runtime_info()` instead would make this
/// test fail on every rebuild, and would measure the build where the point is
/// to hold the contract still.
fn recorded_runtime() -> foe_log::RuntimeInfo {
    foe_log::RuntimeInfo { version: "0.2.0".into(), build: "sha256:recorded".into() }
}

/// The built-in tool specifications the binary composes, which fingerprint
/// hashes with the rest of the contract. Which packs a binary links is the
/// binary's own decision, taken in `foe::run::extra_builtin_specs`, and a
/// test binary cannot reach the command line's modules — so this names the
/// two packs itself, and the test below fails if the two lists ever part.
fn builtin_specs() -> Vec<foe_contract::ToolSpec> {
    foe_code::all()
        .iter()
        .map(|tool| tool.spec().clone())
        .chain(std::iter::once(foe_core::retrieval::spec()))
        .chain(foe_code::team::builtin_specs())
        .collect()
}

/// `foe plan` with no configuration prints every built-in the binary
/// carries. The recorded fingerprints are computed over [`builtin_specs`], so a
/// pack the binary gains or loses has to appear there too; without this check
/// the recorded hashes would go on describing contracts the binary no longer
/// builds.
#[test]
fn the_recorded_builtins_are_the_ones_the_binary_links() {
    let effect =
        |spec: &foe_contract::ToolSpec| serde_json::to_value(spec.effect).unwrap().as_str().unwrap().to_string();
    let mine: Vec<(String, String)> = std::iter::once(foe_contract::tools::block_spec(false))
        .chain(builtin_specs())
        .map(|spec| (spec.name.clone(), effect(&spec)))
        .collect();
    let printed = Command::new(FOE).arg("plan").output().unwrap();
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

/// docs/design.md "Execution contracts and fingerprints": every example contract hashes to
/// the fingerprint recorded for it.
#[test]
fn every_example_contract_hashes_to_its_recorded_fingerprint() {
    let dir = scratch("fingerprint-recorded");
    let specs = builtin_specs();
    let recorded: std::collections::BTreeMap<&str, &str> = RECORDED_FINGERPRINTS.into_iter().collect();
    let found = examples();
    assert_eq!(found.len(), recorded.len(), "every example has a recorded fingerprint");
    let mut changed = Vec::new();
    for (name, text) in found {
        let path = materialize(&dir, &name, &text, "the task the recording ignores");
        let contract = foe_contract::document::load(&path).unwrap_or_else(|e| panic!("{name}: {e}"));
        let fingerprint = foe_contract::fingerprint::compute(&contract, &specs, &recorded_runtime())
            .unwrap_or_else(|e| panic!("{name}: {e}"));
        if fingerprint.hash != recorded[name.as_str()] {
            changed.push(format!("({name:?}, {:?})", fingerprint.hash));
        }
    }
    assert!(changed.is_empty(), "example fingerprints changed:\n    {}", changed.join(",\n    "));
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

/// docs/config.md: `foe plan --schema` describes the document; every
/// example conforms to it and parses into the runtime's configuration type.
#[test]
fn every_example_conforms_to_the_schema_and_parses() {
    let schema: Value = serde_json::from_str(SCHEMA).unwrap();
    let printed = Command::new(FOE).args(["plan", "--schema"]).output().unwrap();
    assert_eq!(serde_json::from_slice::<Value>(&printed.stdout).unwrap(), schema, "the binary prints the schema");
    let inlined = inline(&schema, &schema["$defs"], 4);
    for (name, text) in examples() {
        let document: Value = serde_json::from_str(&text).unwrap();
        foe_contract::schema::conforms(&inlined, &document).unwrap_or_else(|e| panic!("{name}: {e}"));
        foe_contract::document::parse(&text).unwrap_or_else(|e| panic!("{name}: {e}"));
    }
    let mut broken: Value = serde_json::from_str(&examples()[0].1).unwrap();
    broken["budget"]["model_calls"] = json!("many");
    assert!(foe_contract::schema::conforms(&inlined, &broken).is_err(), "a wrong type is caught through a $ref");
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

/// Starts the binary under `--host` with the given running-form arguments
/// and drives the protocol: each `model/request` is answered with the next
/// scripted response. A launch over a seeded log first mirrors the seeded
/// prefix, whose copied requests want no answer, so answering begins after
/// the mirrored `seed/end` when `live_after_seed` is set. With `kill_at`
/// set, the process is killed the moment an event of that type appears,
/// leaving the log cut short. Returns the events read from standard
/// output, the exit code when the process ended itself, and everything it
/// wrote to standard error.
fn drive(
    config_path: &Path,
    extra: &[&str],
    mut responses: Vec<Vec<Value>>,
    kill_at: Option<&str>,
    live_after_seed: bool,
) -> (Vec<Value>, Option<i32>, String) {
    let mut child = Command::new(FOE)
        .arg("--config")
        .arg(config_path)
        .arg("--host")
        .args(extra)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let mut stdin = child.stdin.take().unwrap();
    let stdout = BufReader::new(child.stdout.take().unwrap());
    let mut stderr = child.stderr.take().unwrap();
    let (mut events, mut killed) = (Vec::new(), false);
    let mut live = !live_after_seed;
    for line in stdout.lines() {
        let Ok(line) = line else { break };
        let event: Value = serde_json::from_str(&line).unwrap();
        let kind = event["type"].as_str().unwrap().to_string();
        if kill_at == Some(kind.as_str()) {
            child.kill().unwrap();
            killed = true;
            break;
        }
        live = live || kind == "seed/end";
        if live && kind == "model/request" {
            let id = event["data"]["request_id"].clone();
            for chunk in responses.remove(0) {
                writeln!(stdin, "{}", json!({ "type": "model/chunk", "request_id": id, "chunk": chunk })).unwrap();
            }
        }
        events.push(event);
        if kind == "episode/end" {
            break;
        }
    }
    drop(stdin);
    let status = child.wait().unwrap();
    let mut err = String::new();
    std::io::Read::read_to_string(&mut stderr, &mut err).unwrap();
    (events, (!killed).then(|| status.code().unwrap()), err)
}

/// A configuration whose model calls one host tool, and a first launch
/// killed at that call: the log stops with the call open, the shape of an
/// interruption. The torn JSON fragment appended afterwards is what a
/// crash mid-write leaves.
fn interrupted_log(dir: &Path) -> (PathBuf, PathBuf, String, u64) {
    let config = config(dir, |c| {
        c["tools"] = json!(["ask_host"]);
        c["host_tools"] = json!({ "ask_host": {
            "description": "Ask the host.", "params": { "type": "object" }, "effect": "pure"
        }});
    });
    let config_path = dir.join("config.json");
    std::fs::write(&config_path, serde_json::to_vec_pretty(&config).unwrap()).unwrap();
    let log_dir = dir.join("log");
    let mut first = vec![text("I will ask.")];
    first.extend(call("tc_1", "ask_host", "{}"));
    first.push(done("tool"));
    let extra = [&["--log-dir"][..], &[log_dir.to_str().unwrap()][..]].concat();
    let (events, code, _) = drive(&config_path, &extra, vec![first], Some("host/tool-call"), false);
    assert_eq!(code, None, "the launch was killed, not ended");
    assert_eq!(types(&events).last(), Some(&"assistant/message"), "the tool call is open in the log");
    let written = std::fs::read_to_string(log_dir.join("episode.jsonl")).unwrap().lines().count() as u64;
    let mut file = std::fs::OpenOptions::new().append(true).open(log_dir.join("episode.jsonl")).unwrap();
    file.write_all(b"{\"seq\":99,\"time\":").unwrap();
    let episode_id = events[0]["data"]["id"].as_str().unwrap().to_string();
    (config_path, log_dir, episode_id, written)
}

/// docs/log-format.md "Seeding": launching over an interrupted log
/// continues the episode from a repaired copy seeded beside it at the last
/// clean boundary, with the torn tail line ignored, the open call closed
/// by a synthetic result, and the continuation's requests reconstructing
/// across the boundary.
#[test]
fn an_interrupted_episode_resumes_and_completes() {
    let dir = scratch("resume");
    let (config_path, log_dir, source_id, written) = interrupted_log(&dir);
    let responses = vec![vec![text("resumed and finished"), done("end")]];
    let extra = [&["--log-dir"][..], &[log_dir.to_str().unwrap()][..]].concat();
    let (events, code, err) = drive(&config_path, &extra, responses, None, true);
    assert_eq!(code, Some(0), "{err}");
    assert!(err.contains("continues it in"), "the continuation directory is announced: {err}");
    let start = &events[0]["data"];
    let new_id = start["id"].as_str().unwrap();
    assert_ne!(new_id, source_id, "the continuation is a fresh episode");
    assert_eq!(start["fork_origin"], json!({ "episode_id": source_id, "seq": written }));
    assert!(dir.join(new_id).join("episode.jsonl").is_file(), "the copy lives beside the interrupted log");
    let kinds = types(&events);
    assert!(kinds.contains(&"seed/end"), "the continuation is seeded: {kinds:?}");
    let orphan = events.iter().find(|e| e["type"] == "tool/result").unwrap()["data"].clone();
    assert_eq!((&orphan["synthetic"], &orphan["is_error"]), (&json!(true), &json!(true)));
    let request = events.iter().rev().find(|e| e["type"] == "model/request").unwrap();
    let messages = request["data"]["messages"].as_array().unwrap();
    let roles: Vec<&str> = messages.iter().map(|m| m["role"].as_str().unwrap()).collect();
    assert_eq!(roles, ["user", "assistant", "tool"], "the request reconstructs across the boundary");
    assert!(messages[2]["rendered"].as_str().unwrap().contains("was not recorded"));
    assert_eq!(
        events.last().unwrap()["data"]["outcome"],
        json!({ "kind": "completed", "value": "resumed and finished" })
    );
}

/// docs/design.md "The command line": an interrupted log resumes only
/// under the contract that ran it; a differing configuration is refused
/// with both fingerprints named.
#[test]
fn resuming_under_a_different_contract_is_refused_with_both_fingerprints() {
    let dir = scratch("resume-mismatch");
    let (_, log_dir, _, _) = interrupted_log(&dir);
    let differing = config(&dir, |c| {
        c["instructions"] = json!({ "role": "You are under different instructions." });
        c["tools"] = json!(["ask_host"]);
        c["host_tools"] = json!({ "ask_host": {
            "description": "Ask the host.", "params": { "type": "object" }, "effect": "pure"
        }});
    });
    let differing_path = dir.join("differing.json");
    std::fs::write(&differing_path, serde_json::to_vec_pretty(&differing).unwrap()).unwrap();
    let extra = [&["--log-dir"][..], &[log_dir.to_str().unwrap()][..]].concat();
    let (events, code, err) = drive(&differing_path, &extra, vec![], None, false);
    assert_eq!((events.len(), code), (0, Some(1)));
    assert!(err.contains("resuming requires the contract that ran"), "{err}");
    assert_eq!(err.matches("sha256:").count(), 2, "both fingerprints are named: {err}");
}

/// docs/log-format.md "Seeding": a log that stopped at an event boundary
/// with every binding obligation closed is continued in place, keeping its
/// episode id, with no seeded copy.
#[test]
fn a_cleanly_stopped_log_continues_in_place() {
    let dir = scratch("resume-in-place");
    let config = config(&dir, |_| {});
    let config_path = dir.join("config.json");
    std::fs::write(&config_path, serde_json::to_vec_pretty(&config).unwrap()).unwrap();
    let planned = plan(&config_path);
    let log_dir = dir.join("log");
    std::fs::create_dir_all(&log_dir).unwrap();
    let start = json!({ "seq": 0, "time": 1, "type": "episode/start", "data": {
        "id": "ep_prior", "parent_id": null, "fork_origin": null, "team_id": null,
        "contract": planned["contract"], "contract_fingerprint": planned["contract_fingerprint"], "task": "do the thing",
        "runtime": { "version": "0", "build": "unknown" },
        "sandbox": {
            "mode": "best-effort", "landlock_abi": 0, "resolved_permissions": {},
            "process_boundary": { "kind": "process-group", "subtree_cleanup": "observational" }
        } } });
    let item = json!({ "seq": 1, "time": 1, "type": "inbox/item", "data": {
        "source": "task", "content": [ { "type": "text", "text": "do the thing" } ],
        "from": null, "message_id": null } });
    std::fs::write(log_dir.join("episode.jsonl"), format!("{start}\n{item}\n")).unwrap();
    let responses = vec![vec![text("continued to the end"), done("end")]];
    let extra = [&["--log-dir"][..], &[log_dir.to_str().unwrap()][..]].concat();
    let (events, code, err) = drive(&config_path, &extra, responses, None, false);
    assert_eq!(code, Some(0), "{err}");
    assert_eq!(events[0]["data"]["id"], "ep_prior", "the same episode continues");
    assert!(!types(&events).contains(&"seed/end"), "no copy is seeded");
    let file = std::fs::read_to_string(log_dir.join("episode.jsonl")).unwrap();
    let last: Value = serde_json::from_str(file.lines().last().unwrap()).unwrap();
    assert_eq!(last["type"], "episode/end", "the episode ended in the original directory");
}

/// docs/design.md "The command line": `--fork SOURCE_DIR --at SEQ` seeds a
/// fresh episode from the source's prefix and runs it under the task the
/// launch carries, delivered as a `system` inbox item after `seed/end`.
#[test]
fn a_fork_runs_a_new_task_over_the_prior_context() {
    let dir = scratch("fork");
    let config = config(&dir, |_| {});
    let config_path = dir.join("config.json");
    std::fs::write(&config_path, serde_json::to_vec_pretty(&config).unwrap()).unwrap();
    let log_dir = dir.join("log");
    let extra = [&["--log-dir"][..], &[log_dir.to_str().unwrap()][..]].concat();
    let (events, code, _) =
        drive(&config_path, &extra, vec![vec![text("the first outcome"), done("end")]], None, false);
    assert_eq!(code, Some(0));
    let source_id = events[0]["data"]["id"].as_str().unwrap().to_string();
    let boundary = events.iter().find(|e| e["type"] == "assistant/message").unwrap()["seq"].as_u64().unwrap() + 1;
    let mut forked = config.clone();
    forked["task"] = json!("assess the first outcome");
    let forked_path = dir.join("forked.json");
    std::fs::write(&forked_path, serde_json::to_vec_pretty(&forked).unwrap()).unwrap();
    let fork_dir = dir.join("fork");
    let at = boundary.to_string();
    let extra = ["--fork", log_dir.to_str().unwrap(), "--at", &at, "--log-dir", fork_dir.to_str().unwrap()];
    let responses = vec![vec![text("the fork's outcome"), done("end")]];
    let (events, code, err) = drive(&forked_path, &extra, responses, None, true);
    assert_eq!(code, Some(0), "{err}");
    let start = &events[0]["data"];
    assert_ne!(start["id"].as_str().unwrap(), source_id, "the fork is a fresh episode");
    assert_eq!(start["fork_origin"], json!({ "episode_id": source_id, "seq": boundary }));
    let kinds = types(&events);
    let seeded = kinds.iter().position(|k| *k == "seed/end").expect("the fork is seeded");
    let task_item = &events[seeded + 1];
    assert_eq!(task_item["type"], "inbox/item", "the new task follows seed/end");
    assert_eq!(task_item["data"]["source"], "system");
    assert_eq!(task_item["data"]["content"][0]["text"], "assess the first outcome");
    let request = events.iter().rev().find(|e| e["type"] == "model/request").unwrap();
    let messages = request["data"]["messages"].as_array().unwrap();
    let text_of = |m: &Value| m["content"][0]["text"].as_str().unwrap_or_default().to_string();
    assert_eq!(messages.len(), 3, "prior context and the new task: {messages:?}");
    assert!(text_of(&messages[0]).contains("do the thing"), "the copied task opens the conversation");
    assert_eq!(messages[1]["role"], "assistant");
    assert_eq!(text_of(&messages[2]), "assess the first outcome");
    assert_eq!(
        events.last().unwrap()["data"]["outcome"],
        json!({ "kind": "completed", "value": "the fork's outcome" })
    );
}

/// docs/design.md "Subagents and teams": a spawned fork validates the
/// declared child fingerprint before it writes the seeded child start.
#[test]
fn a_spawned_fork_records_child_contract_evidence() {
    let dir = scratch("spawned-fork-contract");
    let config = config(&dir, |_| {});
    let config_path = dir.join("config.json");
    std::fs::write(&config_path, serde_json::to_vec_pretty(&config).unwrap()).unwrap();
    let planned = plan(&config_path);
    let source = dir.join("source");
    std::fs::create_dir(&source).unwrap();
    let source_start = json!({ "seq": 0, "time": 1, "type": "episode/start", "data": {
        "id": "ep_source", "parent_id": null, "fork_origin": null, "team_id": null,
        "contract": {}, "contract_fingerprint": "sha256:source", "task": "source task",
        "runtime": { "version": "0", "build": "unknown" },
        "sandbox": {
            "mode": "off", "landlock_abi": 0, "resolved_permissions": {},
            "process_boundary": { "kind": "process-group", "subtree_cleanup": "observational" }
        } } });
    let source_task = json!({ "seq": 1, "time": 1, "type": "inbox/item", "data": {
        "source": "task", "content": [{ "type": "text", "text": "source task" }],
        "from": null, "message_id": null } });
    std::fs::write(source.join("episode.jsonl"), format!("{source_start}\n{source_task}\n")).unwrap();
    let log_dir = dir.join("log");
    std::fs::create_dir(&log_dir).unwrap();
    std::fs::write(
        log_dir.join("child-launch.json"),
        serde_json::to_vec_pretty(&json!({
            "episode_id": "ep_child", "parent_id": "ep_parent", "team_id": "ep_parent",
            "expected_contract_fingerprint": planned["contract_fingerprint"], "effective_budget": {"model_calls": 2},
            "fork_source": source, "fork_at": 2
        }))
        .unwrap(),
    )
    .unwrap();
    let extra = [&["--log-dir"][..], &[log_dir.to_str().unwrap()][..]].concat();
    let responses = vec![vec![text("finished"), done("end")]];
    let (events, code, error) = drive(&config_path, &extra, responses, None, false);
    assert_eq!(code, Some(0), "{error}");
    assert_eq!(events[0]["data"]["contract_fingerprint"], planned["contract_fingerprint"]);
    assert_eq!(events[0]["data"]["contract"], planned["contract"]);
    assert_eq!(events[0]["data"]["effective_budget"]["model_calls"], 2);
    assert_eq!(events[0]["data"]["fork_origin"], json!({"episode_id": "ep_source", "seq": 2}));
}

/// The seeding API's boundary rule is surfaced verbatim when `--at` names
/// a cut outside the source log.
#[test]
fn a_fork_boundary_outside_the_source_log_is_refused() {
    let dir = scratch("fork-bad-seq");
    let config = config(&dir, |_| {});
    let config_path = dir.join("config.json");
    std::fs::write(&config_path, serde_json::to_vec_pretty(&config).unwrap()).unwrap();
    let log_dir = dir.join("log");
    let extra = [&["--log-dir"][..], &[log_dir.to_str().unwrap()][..]].concat();
    let (_, code, _) = drive(&config_path, &extra, vec![vec![text("done"), done("end")]], None, false);
    assert_eq!(code, Some(0));
    let fork_dir = dir.join("fork");
    let extra = ["--fork", log_dir.to_str().unwrap(), "--at", "999", "--log-dir", fork_dir.to_str().unwrap()];
    let (events, code, err) = drive(&config_path, &extra, vec![], None, false);
    assert_eq!((events.len(), code), (0, Some(1)));
    assert!(err.contains("seed boundary lies within the source log"), "{err}");
}
