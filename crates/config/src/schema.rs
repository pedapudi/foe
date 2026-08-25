//! The JSON Schema subset foe implements, and the check that a configuration
//! asks for nothing outside it.
//!
//! Implements docs/config.md (JSON Schema subset). Every schema embedded in a
//! configuration is read as JSON Schema Draft 2020-12. Foe implements the
//! assertions listed in [`IMPLEMENTED`] and no others. A schema that declares
//! another dialect, or that uses an assertion outside that list, is a
//! construction error naming the configuration key and the keyword.
//!
//! The list exists so that a declared constraint is either enforced at every
//! value boundary or refused before the episode starts. An unchecked keyword
//! would make `done_when.returns` a promise the log cannot evidence: the
//! runtime would report a value as conforming while ignoring the part of the
//! schema it does not read.

use crate::ConfigError;
use serde_json::{Map, Value};

/// The dialect every embedded schema is read under. `foe plan --schema`
/// declares it for the configuration document itself.
pub const DIALECT: &str = "https://json-schema.org/draft/2020-12/schema";

/// The assertions [`conforms`] evaluates. The set covers what the Python
/// package derives from a type annotation, so a tool declared there always
/// produces a schema this runtime enforces in full.
#[rustfmt::skip]
const IMPLEMENTED: &[&str] = &[
    "type", "enum", "const", "anyOf", "required", "properties", "additionalProperties", "items",
    "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems",
];

/// Keywords that carry no assertion. A validator that ignores them still
/// enforces the whole schema, so they need no implementation.
const ANNOTATIONS: &[&str] =
    &["$schema", "$comment", "title", "description", "default", "examples", "deprecated", "readOnly", "writeOnly"];

/// Rejects a schema outside the implemented subset as a construction error
/// under the dotted configuration `key`.
pub fn check(key: String, schema: &Value) -> Result<(), ConfigError> {
    supported(schema, "").map_err(|rule| ConfigError::Invalid { key, rule })
}

/// Reports the first part of `schema` foe does not implement. `at` is the
/// keyword path from the schema's root, so the error names the subschema.
fn supported(schema: &Value, at: &str) -> Result<(), String> {
    let Some(object) = schema.as_object() else {
        return Err(format!("is a JSON Schema object at schema{at}; a boolean schema is not implemented"));
    };
    if let Some(declared) = object.get("$schema").filter(|d| *d != DIALECT) {
        return Err(format!("declares the dialect {declared} at schema{at}; foe reads every schema as {DIALECT}"));
    }
    let unknown = object.keys().find(|k| !IMPLEMENTED.contains(&k.as_str()) && !ANNOTATIONS.contains(&k.as_str()));
    if let Some(keyword) = unknown {
        return Err(format!("uses `{keyword}` at schema{at}, which foe does not implement"));
    }
    for keyword in ["items", "additionalProperties"] {
        match object.get(keyword) {
            Some(Value::Bool(_)) | None => {}
            Some(sub) => supported(sub, &format!("{at}.{keyword}"))?,
        }
    }
    if let Some(properties) = object.get("properties") {
        let named = properties.as_object().ok_or_else(|| format!("has an object at schema{at}.properties"))?;
        for (name, sub) in named {
            supported(sub, &format!("{at}.properties.{name}"))?;
        }
    }
    if let Some(options) = object.get("anyOf") {
        let list = options.as_array().ok_or_else(|| format!("has a list at schema{at}.anyOf"))?;
        for (i, sub) in list.iter().enumerate() {
            supported(sub, &format!("{at}.anyOf[{i}]"))?;
        }
    }
    Ok(())
}

/// Checks `value` against `schema`, naming the failing field from the value's
/// root. A schema outside the implemented subset never reaches this function,
/// because [`check`] refused it at construction.
pub fn conforms(schema: &Value, value: &Value) -> Result<(), String> {
    check_value(schema, value, "value".to_string())
}

/// Checks a tool call's argument object, naming the failing field by its own
/// path so that a bad property reads as `path` rather than as `value.path`.
pub fn arguments_conform(schema: &Value, args: &Value) -> Result<(), String> {
    check_value(schema, args, String::new())
}

fn check_value(schema: &Value, value: &Value, path: String) -> Result<(), String> {
    let Some(obj) = schema.as_object() else { return Ok(()) };
    let type_name = match value {
        Value::Null => "null",
        Value::Bool(_) => "boolean",
        Value::Number(n) if n.is_i64() || n.is_u64() => "integer",
        Value::Number(_) => "number",
        Value::String(_) => "string",
        Value::Array(_) => "array",
        Value::Object(_) => "object",
    };
    let allowed: Vec<&str> = match obj.get("type") {
        Some(Value::String(t)) => vec![t.as_str()],
        Some(Value::Array(ts)) => ts.iter().filter_map(Value::as_str).collect(),
        _ => vec![],
    };
    if !allowed.is_empty() && !allowed.iter().any(|t| *t == type_name || (*t == "number" && type_name == "integer")) {
        return fail(&path, format!("expected type {}, found {type_name}", allowed.join(" or ")));
    }
    if let Some(options) = obj.get("enum").and_then(Value::as_array) {
        if !options.contains(value) {
            return fail(&path, format!("is not one of {}", Value::Array(options.clone())));
        }
    }
    if obj.get("const").is_some_and(|c| c != value) {
        return fail(&path, format!("is not the constant {}", obj["const"]));
    }
    if let Some(options) = obj.get("anyOf").and_then(Value::as_array) {
        if options.iter().all(|option| check_value(option, value, path.clone()).is_err()) {
            return fail(&path, format!("matches none of the {} alternatives in `anyOf`", options.len()));
        }
    }
    bounded(obj, value, &path)?;
    if let (Some(fields), Some(required)) = (value.as_object(), obj.get("required").and_then(Value::as_array)) {
        if let Some(missing) = required.iter().filter_map(Value::as_str).find(|r| !fields.contains_key(*r)) {
            return fail(&path, format!("lacks required property `{missing}`"));
        }
    }
    if let Some(fields) = value.as_object() {
        let properties = obj.get("properties").and_then(Value::as_object);
        let extra = obj.get("additionalProperties");
        for (name, field) in fields {
            match properties.and_then(|p| p.get(name)).or_else(|| extra.filter(|e| e.is_object())) {
                Some(sub) => check_value(sub, field, format!("{path}.{name}"))?,
                None if extra == Some(&Value::Bool(false)) => {
                    return fail(&path, format!("has unexpected property `{name}`"))
                }
                None => {}
            }
        }
    }
    if let (Some(items), Some(sub)) = (value.as_array(), obj.get("items")) {
        for (i, item) in items.iter().enumerate() {
            check_value(sub, item, format!("{path}[{i}]"))?;
        }
    }
    Ok(())
}

/// `minimum` and `maximum` bound a number, `minLength` and `maxLength` a
/// string's characters, `minItems` and `maxItems` an array's elements. Each
/// pair applies to its own type and is ignored for every other type.
fn bounded(obj: &Map<String, Value>, value: &Value, path: &str) -> Result<(), String> {
    let measured = match value {
        Value::Number(n) => ("minimum", "maximum", n.as_f64().unwrap_or_default(), ""),
        Value::String(s) => ("minLength", "maxLength", s.chars().count() as f64, " characters long"),
        Value::Array(a) => ("minItems", "maxItems", a.len() as f64, " items long"),
        _ => return Ok(()),
    };
    let (low, high, actual, unit) = measured;
    let under = obj.get(low).and_then(Value::as_f64).filter(|limit| actual < *limit).map(|limit| (low, limit));
    let over = obj.get(high).and_then(Value::as_f64).filter(|limit| actual > *limit).map(|limit| (high, limit));
    match under.or(over) {
        Some((keyword, limit)) => fail(path, format!("is {actual}{unit}, outside `{keyword}` {limit}")),
        None => Ok(()),
    }
}

/// Names the failing field. The argument object itself has no name of its
/// own, so a failure at the root of a call's arguments says `arguments`.
fn fail(path: &str, what: String) -> Result<(), String> {
    let named = path.trim_start_matches('.');
    Err(format!("{}: {what}", if named.is_empty() { "arguments" } else { named }))
}

#[cfg(test)]
#[path = "schema_test.rs"]
mod tests;
