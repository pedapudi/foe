//! Argument binding for tool nodes: `{ "$node": NAME, "pointer": P }` is
//! replaced by the named input's value, or by the value at JSON Pointer `P`
//! within it. See docs/workflow.md "Tool nodes".

use serde_json::{Map, Value};

/// The kind this crate reports what resolving a tool node's arguments left
/// out under. An object holding `pointer` and no `$node` is shaped like a
/// binding and binds nothing, so it was passed to the tool as written and
/// nobody could tell that the value it meant to carry never arrived.
pub const UNNAMED_BINDING: &str = "workflow.unnamed_binding";

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
    fn walk(
        value: &Value,
        input: &dyn Fn(&str) -> Option<Value>,
        at: &str,
        unnamed: &mut Vec<String>,
    ) -> Result<Value, String> {
        let Some(object) = value.as_object() else {
            return Ok(match value {
                Value::Array(items) => Value::Array(
                    items
                        .iter()
                        .enumerate()
                        .map(|(index, v)| walk(v, input, &format!("{at}/{index}"), unnamed))
                        .collect::<Result<_, _>>()?,
                ),
                other => other.clone(),
            });
        };
        let Some(name) = object.get("$node").and_then(Value::as_str) else {
            if object.contains_key("pointer") {
                unnamed.push(format!("`{at}`"));
            }
            let fields = object.iter().map(|(k, v)| Ok((k.clone(), walk(v, input, &format!("{at}/{k}"), unnamed)?)));
            return Ok(Value::Object(fields.collect::<Result<_, String>>()?));
        };
        let whole = input(name).ok_or_else(|| format!("binds `{name}`, which has produced no value"))?;
        let path = object.get("pointer").and_then(Value::as_str).unwrap_or("");
        pointer(&whole, path)
            .cloned()
            .ok_or_else(|| format!("binds `{name}` at pointer `{path}`, which is absent from its value"))
    }
    let mut unnamed = Vec::new();
    let resolved = walk(&Value::Object(args.clone()), input, "", &mut unnamed)?;
    match foe_log::left_out(UNNAMED_BINDING, unnamed) {
        Some(report) => Err(report.sentence()),
        None => Ok(resolved),
    }
}

#[cfg(test)]
#[path = "bind_test.rs"]
mod tests;
