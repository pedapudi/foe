use super::{pointer, resolve};
use serde_json::{json, Value};

#[test]
fn a_pointer_walks_objects_and_arrays_and_unescapes() {
    let value = json!({ "a": [ { "b/c": 1 }, { "~": 2 } ], "": 3 });
    assert_eq!(pointer(&value, ""), Some(&value));
    assert_eq!(pointer(&value, "/a/0/b~1c"), Some(&json!(1)));
    assert_eq!(pointer(&value, "/a/1/~0"), Some(&json!(2)));
    assert_eq!(pointer(&value, "/"), Some(&json!(3)));
    assert_eq!(pointer(&value, "/a/2"), None);
    assert_eq!(pointer(&value, "/a/x"), None);
    assert_eq!(pointer(&value, "a"), None, "a pointer starts with a slash");
}

/// docs/workflow.md "Tool nodes": a binding is replaced by the input's
/// value or the value at the pointer; nothing else is substituted.
#[test]
fn bindings_resolve_at_any_depth_and_misses_name_the_node() {
    let inputs = |name: &str| match name {
        "manifest" => Some(json!({ "top_symbol": "parse", "count": 3 })),
        "survey" => Some(json!("hits")),
        _ => None,
    };
    let args = json!({
        "pattern": { "$node": "manifest", "pointer": "/top_symbol" },
        "nested": { "list": [ { "$node": "survey" }, 1 ], "whole": { "$node": "manifest" } },
        "plain": { "pointer": "/x" }
    });
    let resolved = resolve(args.as_object().unwrap(), &inputs).unwrap();
    assert_eq!(
        resolved,
        json!({
            "pattern": "parse",
            "nested": { "list": [ "hits", 1 ], "whole": { "top_symbol": "parse", "count": 3 } },
            "plain": { "pointer": "/x" }
        })
    );
    let missing = json!({ "p": { "$node": "manifest", "pointer": "/absent" } });
    let error = resolve(missing.as_object().unwrap(), &inputs).unwrap_err();
    assert!(error.contains("`manifest`") && error.contains("/absent"), "{error}");
    let unproduced = json!({ "p": { "$node": "ghost" } });
    let error = resolve(unproduced.as_object().unwrap(), &inputs).unwrap_err();
    assert!(error.contains("`ghost`") && error.contains("no value"), "{error}");
    let none: Value = resolve(json!({}).as_object().unwrap(), &inputs).unwrap();
    assert_eq!(none, json!({}));
}

/// docs/design.md "Input bounds": a value bound into a tool node's arguments
/// is refused above its bound, and arguments that together exceed their own
/// bound are refused as well; each refusal names the bound it broke.
#[test]
fn a_value_above_its_input_bound_is_refused_naming_the_bound() {
    let bound =
        |name: &str| foe_contract::input_bound(name).unwrap_or_else(|| panic!("{name} stands in INPUT_BOUNDS")).maximum;
    // A JSON string of n - 2 characters serializes to n bytes with its quotes.
    let sized = |bytes: usize| Value::String("x".repeat(bytes - 2));
    let at = sized(bound(super::BOUND_VALUE));
    let over = sized(bound(super::BOUND_VALUE) + 1);
    let inputs = |name: &str| match name {
        "at" => Some(at.clone()),
        "over" => Some(over.clone()),
        _ => None,
    };
    let one = json!({ "p": { "$node": "at" } });
    resolve(one.as_object().unwrap(), &inputs).expect("a value at its bound resolves");
    let one = json!({ "p": { "$node": "over" } });
    let refused = resolve(one.as_object().unwrap(), &inputs).unwrap_err();
    assert!(refused.contains(super::BOUND_VALUE), "a value over its bound is refused without naming it: {refused}");

    let count = bound(super::NODE_ARGUMENTS) / bound(super::BOUND_VALUE) + 1;
    let args: serde_json::Map<String, Value> =
        (0..count).map(|index| (format!("k{index}"), json!({ "$node": "at" }))).collect();
    let refused = resolve(&args, &inputs).unwrap_err();
    assert!(
        refused.contains(super::NODE_ARGUMENTS),
        "arguments over their bound are refused without naming it: {refused}"
    );
}
