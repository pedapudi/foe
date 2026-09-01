use super::resolve_specs;
use crate::harness_text as text;
use crate::test_util::{program_with, spec, tmp};
use crate::Effect;
use serde_json::json;

/// AGENTS.md: a specification rule that can be tested has a test. Every
/// parameter schema the runtime itself writes stays inside the subset
/// `crate::schema` implements, so no built-in tool advertises a constraint
/// dispatch would ignore.
#[test]
fn every_runtime_written_parameter_schema_stays_inside_the_implemented_subset() {
    let root = tmp("tools-own-schemas");
    let exec = root.join("t.sh");
    std::fs::write(&exec, "").unwrap();
    std::fs::set_permissions(&exec, std::os::unix::fs::PermissionsExt::from_mode(0o755)).unwrap();
    let program = program_with(&root, |v| {
        v["tools"] = json!(["t", "block"]);
        v["tool_defs"] = json!({ "t": { "exec": exec, "description": "configured" } });
        v["done_when"] = json!({ "returns": { "type": "object", "properties": { "n": { "type": "integer" } } } });
    })
    .unwrap();
    let specs = resolve_specs(&program, &[spec("p", Effect::Pure)]).unwrap();
    assert!(specs.iter().any(|s| s.name == text::RETURN_NAME), "the synthesized return tool is present");
    for s in specs {
        crate::schema::check(format!("tools.{}.params", s.name), &s.params).unwrap();
    }
}
