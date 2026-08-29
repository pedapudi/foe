use super::{Produced, Scheduler};
use foe_program::workflow::WorkflowConfig;
use serde_json::json;

fn scheduler(nodes: serde_json::Value) -> Scheduler {
    let wf: WorkflowConfig = serde_json::from_value(json!({ "nodes": nodes })).unwrap();
    Scheduler::new(&wf, Produced { value: json!("run the graph"), rendered: "run the graph".into(), seq: 1 })
}

fn produced(seq: u64) -> Produced {
    Produced { value: json!({ "seq": seq }), rendered: seq.to_string(), seq }
}

/// docs/workflow.md "The graph": `task` holds the invocation task before
/// the first firing, is listed first among a node's inputs, and orders
/// nothing, so a node that follows only `task` fires at the start while a
/// node that also follows another node waits for it.
#[test]
fn the_task_source_is_present_first_and_orders_nothing() {
    let mut s = scheduler(json!({
        "intro": { "tool": "t", "follows": ["task"] },
        "join": { "tool": "t", "follows": ["intro", "task"], "terminal": true }
    }));
    assert_eq!(s.inputs["join"], vec!["task", "intro"]);
    assert_eq!(s.state["task"].value.as_ref().unwrap().rendered, "run the graph");
    assert_eq!(s.ready(), vec!["intro"], "join waits for intro; task is no ancestor of anything");
    s.begin("intro");
    s.finish("intro");
    s.produced("intro", produced(2), None);
    assert_eq!(s.ready(), vec!["join"]);
    assert_eq!(s.nearest_model("join"), None, "the walk over predecessors never reaches task");
}

/// docs/workflow.md "Firing": a node fires when its inputs are fresh; on a
/// cycle it fires again when they are fresh again, and `max_fires` is what
/// the executor checks before each firing.
#[test]
fn readiness_follows_freshness_around_a_cycle() {
    let mut s = scheduler(json!({
        "manifest": { "tool": "list" },
        "survey": { "tool": "grep", "follows": ["manifest"], "max_fires": 3 },
        "propose": { "tool": "decide", "follows": ["manifest", "survey"],
                     "branches": { "accept": ["derive"], "widen": ["survey"] }, "max_fires": 3 },
        "derive": { "tool": "derive", "follows": ["propose"], "terminal": true }
    }));
    assert_eq!(s.ready(), vec!["manifest"], "only the source is ready at the start");
    assert_eq!(s.begin("manifest").0, 1);
    assert!(s.ready().is_empty(), "nothing is ready while the source runs");
    s.finish("manifest");
    assert_eq!(s.produced("manifest", produced(1), None), vec!["propose", "survey"]);
    assert_eq!(s.ready(), vec!["survey"], "propose waits for its other input, whose source is ready");
    s.begin("survey");
    s.finish("survey");
    s.produced("survey", produced(2), None);
    assert_eq!(s.ready(), vec!["propose"]);
    s.begin("propose");
    s.finish("propose");
    assert_eq!(s.produced("propose", produced(3), Some("widen")), vec!["survey"], "only the listed successor");
    assert_eq!(s.ready(), vec!["survey"], "the cycle re-fires survey");
    assert_eq!(s.state["derive"].fresh.len(), 0, "derive was not refreshed");
    assert_eq!(s.begin("survey").0, 2, "the second firing of survey");
    s.finish("survey");
    s.produced("survey", produced(4), None);
    assert_eq!(s.ready(), vec!["propose"], "one fresh input re-fires propose even though manifest is stale");
    s.begin("propose");
    s.finish("propose");
    assert_eq!(s.produced("propose", produced(5), Some("accept")), vec!["derive"]);
    assert_eq!(s.ready(), vec!["derive"]);
    assert_eq!(s.state["propose"].fires, 2);
}

/// docs/workflow.md "Firing": after a branch source has produced a value,
/// an unchosen target cannot fire from freshness retained on another input.
/// A forced firing and a later chosen branch remain eligible.
#[test]
fn an_unchosen_branch_gates_stale_input_freshness() {
    let mut s = scheduler(json!({
        "implement": { "tool": "write" },
        "assess": { "tool": "check", "follows": ["implement"],
                    "branches": { "accept": [], "repair": ["repair"] }, "max_fires": 3 },
        "repair": { "tool": "write", "follows": ["implement", "assess"],
                    "branches": { "reassess": ["assess"] }, "max_fires": 2 }
    }));
    s.begin("implement");
    s.finish("implement");
    s.produced("implement", produced(1), None);
    assert_eq!(s.ready(), vec!["assess"]);

    s.begin("assess");
    s.finish("assess");
    s.produced("assess", produced(2), Some("accept"));
    assert!(s.ready().is_empty(), "the unchosen repair remains gated");

    s.force("assess");
    assert_eq!(s.ready(), vec!["assess"], "verification can force reassessment");
    s.begin("assess");
    s.finish("assess");
    s.produced("assess", produced(3), Some("repair"));
    assert_eq!(s.ready(), vec!["repair"]);

    s.begin("repair");
    s.finish("repair");
    s.produced("repair", produced(4), Some("reassess"));
    assert_eq!(s.ready(), vec!["assess"], "the chosen cycle edge refreshes assessment");
}

/// docs/workflow.md "Firing": when alternative branch sources control one
/// target, a fresh choice from either source admits it. An earlier choice by
/// another source does not suppress that edge.
#[test]
fn alternative_branch_sources_admit_one_shared_target() {
    let mut s = scheduler(json!({
        "implement": { "tool": "write" },
        "assess": { "tool": "check", "follows": ["implement"],
                    "branches": { "accept": ["confirm"], "repair": ["repair"] } },
        "repair": { "tool": "write", "follows": ["assess"],
                    "branches": { "confirm": ["confirm"] } },
        "confirm": { "tool": "check", "branches": { "accept": [], "repair": ["confirm-repair"] },
                     "max_fires": 2 },
        "confirm-repair": { "tool": "write", "follows": ["confirm"],
                            "branches": { "reassess": ["confirm"] } }
    }));
    s.begin("implement");
    s.finish("implement");
    s.produced("implement", produced(1), None);
    s.begin("assess");
    s.finish("assess");
    s.produced("assess", produced(2), Some("repair"));
    assert_eq!(s.ready(), vec!["repair"]);

    s.begin("repair");
    s.finish("repair");
    s.produced("repair", produced(3), Some("confirm"));
    assert_eq!(s.ready(), vec!["confirm"], "repair admits the target that assessment did not choose");
    s.begin("confirm");
    s.finish("confirm");
    s.produced("confirm", produced(4), Some("repair"));
    assert_eq!(s.ready(), vec!["confirm-repair"]);

    s.begin("confirm-repair");
    s.finish("confirm-repair");
    s.produced("confirm-repair", produced(5), Some("reassess"));
    assert_eq!(s.ready(), vec!["confirm"], "the correction admits the shared confirmation target again");
}

/// docs/workflow.md "Firing": nodes with no pending dependency between
/// them are ready together, and a join waits for every input.
#[test]
fn independent_nodes_are_ready_together_and_a_join_waits() {
    let mut s = scheduler(json!({
        "a": { "tool": "t" },
        "b": { "tool": "t", "follows": ["a"] },
        "c": { "tool": "t", "follows": ["a"] },
        "d": { "tool": "t", "follows": ["b", "c"] }
    }));
    s.begin("a");
    s.finish("a");
    s.produced("a", produced(1), None);
    assert_eq!(s.ready(), vec!["b", "c"]);
    s.begin("b");
    s.begin("c");
    s.finish("b");
    s.produced("b", produced(2), None);
    assert!(s.ready().is_empty(), "d waits while c runs");
    s.finish("c");
    s.produced("c", produced(3), None);
    assert_eq!(s.ready(), vec!["d"]);
    s.force("a");
    assert_eq!(s.ready(), vec!["a"], "a forced ancestor fires first; d waits for the re-fire to reach it");
}

/// docs/workflow.md "Choice points": a successor under no label fires
/// regardless, and `nearest_model` walks upward to a model node.
#[test]
fn unlabeled_successors_fire_regardless_and_nearest_model_walks_up() {
    let mut s = scheduler(json!({
        "plan": { "model": { "name": "p", "instructions": { "r": "x" }, "tools": ["block"],
                             "grants": { "read": ["/src"] }, "budget": { "model_calls": 1 } } },
        "gate": { "tool": "t", "follows": ["plan"], "branches": { "go": ["build"], "stop": [] } },
        "build": { "tool": "t", "follows": ["gate"] },
        "log": { "tool": "t", "follows": ["gate"], "terminal": true }
    }));
    s.begin("plan");
    s.finish("plan");
    s.produced("plan", produced(1), None);
    s.begin("gate");
    s.finish("gate");
    assert_eq!(s.produced("gate", produced(2), Some("stop")), vec!["log"]);
    assert_eq!(s.nearest_model("log").as_deref(), Some("plan"));
    assert_eq!(s.nearest_model("plan").as_deref(), Some("plan"));
    let no_model = scheduler(json!({ "a": { "tool": "t" } }));
    assert_eq!(no_model.nearest_model("a"), None);
}
