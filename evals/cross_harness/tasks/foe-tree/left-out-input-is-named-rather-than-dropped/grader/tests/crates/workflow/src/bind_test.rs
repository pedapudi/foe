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
        "plain": { "path": "/x" }
    });
    let resolved = resolve(args.as_object().unwrap(), &inputs).unwrap();
    assert_eq!(
        resolved,
        json!({
            "pattern": "parse",
            "nested": { "list": [ "hits", 1 ], "whole": { "top_symbol": "parse", "count": 3 } },
            "plain": { "path": "/x" }
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

/// docs/design.md "What a derived value left out": an object holding
/// `pointer` and no `$node` is shaped like a binding and binds nothing, so
/// resolution names it by where it stands rather than passing it to the tool
/// as written.
#[test]
fn an_argument_that_binds_nothing_is_named_rather_than_passed_on() {
    let inputs = |name: &str| match name {
        "manifest" => Some(json!({ "top_symbol": "parse" })),
        _ => None,
    };
    let args = json!({
        "pattern": { "$node": "manifest", "pointer": "/top_symbol" },
        "plain": { "pointer": "/x" },
        "list": [ { "pointer": "/y" } ]
    });
    let refused = resolve(args.as_object().unwrap(), &inputs).unwrap_err();
    assert!(refused.contains("/plain"), "the argument that binds nothing is not named: {refused}");
    assert!(refused.contains("/list/0"), "the one inside an array is not named: {refused}");
    let sound = json!({ "pattern": { "$node": "manifest", "pointer": "/top_symbol" }, "plain": { "path": "/x" } });
    let resolved: Value = resolve(sound.as_object().unwrap(), &inputs).unwrap();
    assert_eq!(resolved, json!({ "pattern": "parse", "plain": { "path": "/x" } }));
}
