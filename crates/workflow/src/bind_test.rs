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
