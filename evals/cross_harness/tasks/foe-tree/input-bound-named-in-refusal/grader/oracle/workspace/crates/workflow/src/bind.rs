//! Argument binding for tool nodes: `{ "$node": NAME, "pointer": P }` is
//! replaced by the named input's value, or by the value at JSON Pointer `P`
//! within it. See docs/workflow.md "Tool nodes".

use serde_json::{Map, Value};

/// The bounds this crate enforces on the values entering a tool node: one
/// bound value, and the node's resolved arguments together. A value whose
/// serialization fails is past every bound rather than unbounded, so it
/// measures the largest addressable count and is refused.
pub const BOUND_VALUE: &str = "workflow.bound_value";
pub const NODE_ARGUMENTS: &str = "workflow.node_arguments";

/// The bytes one value occupies once serialized.
fn measured(value: &Value) -> usize {
    serde_json::to_string(value).map_or(usize::MAX, |text| text.len())
}

/// The value at `pointer` within `value`, by RFC 6901: `/a/0/b` walks
/// object keys and array indexes, `~1` stands for `/` and `~0` for `~`, and
/// the empty pointer names the whole value.
pub fn pointer<'a>(value: &'a Value, pointer: &str) -> Option<&'a Value> {
    if pointer.is_empty() {
        return Some(value);
    }
    let rest = pointer.strip_prefix('/')?;
    rest.split('/').try_fold(value, |current, token| {
        let token = token.replace("~1", "/").replace("~0", "~");
        match current {
            Value::Object(map) => map.get(&token),
            Value::Array(items) => token.parse::<usize>().ok().and_then(|i| items.get(i)),
            _ => None,
        }
    })
}

/// `args` with every binding replaced. `input` yields the current value of
/// a named input node. A binding whose node has no value, or whose pointer
/// names nothing within it, is an error naming both.
pub fn resolve(args: &Map<String, Value>, input: &dyn Fn(&str) -> Option<Value>) -> Result<Value, String> {
    fn walk(value: &Value, input: &dyn Fn(&str) -> Option<Value>) -> Result<Value, String> {
        let Some(object) = value.as_object() else {
            return Ok(match value {
                Value::Array(items) => Value::Array(items.iter().map(|v| walk(v, input)).collect::<Result<_, _>>()?),
                other => other.clone(),
            });
        };
        let Some(name) = object.get("$node").and_then(Value::as_str) else {
            let fields = object.iter().map(|(k, v)| Ok((k.clone(), walk(v, input)?)));
            return Ok(Value::Object(fields.collect::<Result<_, String>>()?));
        };
        let whole = input(name).ok_or_else(|| format!("binds `{name}`, which has produced no value"))?;
        let path = object.get("pointer").and_then(Value::as_str).unwrap_or("");
        let bound = pointer(&whole, path)
            .cloned()
            .ok_or_else(|| format!("binds `{name}` at pointer `{path}`, which is absent from its value"))?;
        foe_contract::within_input_bound(BOUND_VALUE, measured(&bound))
            .map_err(|rule| format!("binds `{name}` at pointer `{path}`, whose value {rule}"))?;
        Ok(bound)
    }
    let resolved = walk(&Value::Object(args.clone()), input)?;
    foe_contract::within_input_bound(NODE_ARGUMENTS, measured(&resolved))
        .map_err(|rule| format!("resolves to arguments whose serialization {rule}"))?;
    Ok(resolved)
}

#[cfg(test)]
#[path = "bind_test.rs"]
mod tests;
