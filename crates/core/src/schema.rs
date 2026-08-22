//! JSON Schema compilation and value validation under the one dialect foe accepts.

use crate::ConfigError;
use jsonschema::{Draft, Validator};
use serde_json::Value;

/// Every schema in a configuration is interpreted as this dialect.
pub const DIALECT: &str = "https://json-schema.org/draft/2020-12/schema";

/// Compiles one schema without network or filesystem retrieval. Schemas may
/// use references within their own document. The `format` keyword is enforced.
pub fn compile(schema: &Value) -> Result<Validator, String> {
    known_keywords(schema, "schema")?;
    jsonschema::options()
        .with_draft(Draft::Draft202012)
        .should_validate_formats(true)
        .should_ignore_unknown_formats(false)
        .build(schema)
        .map_err(schema_error)
}

/// Rejects an invalid schema as a construction error under its dotted key.
pub fn check(key: impl Into<String>, schema: &Value) -> Result<(), ConfigError> {
    compile(schema).map(drop).map_err(|rule| ConfigError::Invalid { key: key.into(), rule })
}

/// Validates a value against a schema and names the failing value field.
pub fn conforms(schema: &Value, value: &Value) -> Result<(), String> {
    compile(schema)?.validate(value).map_err(value_error)
}

pub fn value_error(error: jsonschema::ValidationError<'_>) -> String {
    format!("value{}: {error}", dotted(error.instance_path().as_str()))
}

fn schema_error(error: jsonschema::ValidationError<'_>) -> String {
    let path = error.instance_path().as_str();
    let keyword = path.rsplit('/').next().filter(|s| !s.is_empty()).unwrap_or(error.kind().keyword());
    format!("is not a valid JSON Schema at schema{} (`{keyword}`): {error}", dotted(path))
}

fn dotted(pointer: &str) -> String {
    pointer
        .split('/')
        .skip(1)
        .map(|part| match part.parse::<usize>() {
            Ok(index) => format!("[{index}]"),
            Err(_) => format!(".{}", part.replace("~1", "/").replace("~0", "~")),
        })
        .collect()
}

/// Foe treats extension keywords as construction errors so a misspelled
/// assertion cannot silently become an annotation.
fn known_keywords(schema: &Value, path: &str) -> Result<(), String> {
    const KNOWN: &[&str] = &[
        "$schema",
        "$id",
        "$vocabulary",
        "$ref",
        "$dynamicRef",
        "$dynamicAnchor",
        "$anchor",
        "$comment",
        "$defs",
        "type",
        "const",
        "enum",
        "multipleOf",
        "maximum",
        "exclusiveMaximum",
        "minimum",
        "exclusiveMinimum",
        "maxLength",
        "minLength",
        "pattern",
        "maxItems",
        "minItems",
        "uniqueItems",
        "maxContains",
        "minContains",
        "maxProperties",
        "minProperties",
        "required",
        "dependentRequired",
        "prefixItems",
        "items",
        "contains",
        "additionalProperties",
        "properties",
        "patternProperties",
        "dependentSchemas",
        "propertyNames",
        "if",
        "then",
        "else",
        "allOf",
        "anyOf",
        "oneOf",
        "not",
        "unevaluatedItems",
        "unevaluatedProperties",
        "title",
        "description",
        "default",
        "deprecated",
        "readOnly",
        "writeOnly",
        "examples",
        "format",
        "contentEncoding",
        "contentMediaType",
        "contentSchema",
    ];
    let Some(object) = schema.as_object() else { return Ok(()) };
    if let Some(declared) = object.get("$schema") {
        if declared != DIALECT {
            return Err(format!("{path} declares `$schema` as {declared}; the supported dialect is `{DIALECT}`"));
        }
    }
    if let Some(vocabularies) = object.get("$vocabulary").and_then(Value::as_object) {
        const SUPPORTED: &[&str] = &[
            "https://json-schema.org/draft/2020-12/vocab/core",
            "https://json-schema.org/draft/2020-12/vocab/applicator",
            "https://json-schema.org/draft/2020-12/vocab/unevaluated",
            "https://json-schema.org/draft/2020-12/vocab/validation",
            "https://json-schema.org/draft/2020-12/vocab/meta-data",
            "https://json-schema.org/draft/2020-12/vocab/format-annotation",
            "https://json-schema.org/draft/2020-12/vocab/format-assertion",
            "https://json-schema.org/draft/2020-12/vocab/content",
        ];
        if let Some((uri, _)) = vocabularies
            .iter()
            .find(|(uri, required)| required == &&Value::Bool(true) && !SUPPORTED.contains(&uri.as_str()))
        {
            return Err(format!("{path} requires unsupported `$vocabulary` `{uri}`"));
        }
    }
    if let Some(keyword) = object.keys().find(|key| !KNOWN.contains(&key.as_str())) {
        return Err(format!("is not a valid JSON Schema: {path}.{keyword} is an unsupported keyword"));
    }
    for keyword in [
        "items",
        "contains",
        "additionalProperties",
        "propertyNames",
        "if",
        "then",
        "else",
        "not",
        "unevaluatedItems",
        "unevaluatedProperties",
        "contentSchema",
    ] {
        object.get(keyword).into_iter().try_for_each(|value| known_keywords(value, &format!("{path}.{keyword}")))?;
    }
    for keyword in ["$defs", "properties", "patternProperties", "dependentSchemas"] {
        if let Some(map) = object.get(keyword).and_then(Value::as_object) {
            for (name, value) in map {
                known_keywords(value, &format!("{path}.{keyword}.{name}"))?;
            }
        }
    }
    for keyword in ["prefixItems", "allOf", "anyOf", "oneOf"] {
        if let Some(list) = object.get(keyword).and_then(Value::as_array) {
            for (index, value) in list.iter().enumerate() {
                known_keywords(value, &format!("{path}.{keyword}[{index}]"))?;
            }
        }
    }
    Ok(())
}

#[cfg(test)]
#[path = "schema_test.rs"]
mod tests;
