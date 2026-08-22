use super::{check, conforms, DIALECT};
use crate::ConfigError;
use serde_json::json;

/// docs/config.md "JSON Schema dialect": assertion keywords, local
/// references, alternatives, and formats use Draft 2020-12 semantics.
#[test]
fn draft_2020_12_constraints_are_enforced() {
    let schema = json!({
        "$schema": DIALECT,
        "$defs": { "identifier": { "type": "string", "pattern": "^[A-Z]{2}$" } },
        "type": "object",
        "properties": {
            "count": { "type": "integer", "minimum": 2 },
            "identifier": { "$ref": "#/$defs/identifier" },
            "choice": { "oneOf": [{ "const": "left" }, { "const": "right" }] },
            "contact": { "type": "string", "format": "email" }
        },
        "required": ["count", "identifier", "choice", "contact"]
    });
    let valid = json!({ "count": 2, "identifier": "AB", "choice": "left", "contact": "a@example.com" });
    assert!(conforms(&schema, &valid).is_ok());
    for (field, value) in
        [("count", json!(1)), ("identifier", json!("abc")), ("choice", json!("middle")), ("contact", json!("invalid"))]
    {
        let mut invalid = valid.clone();
        invalid[field] = value;
        let error = conforms(&schema, &invalid).unwrap_err();
        assert!(error.contains(field), "{error}");
    }
}

#[test]
fn invalid_or_different_dialect_schemas_name_the_configuration_key() {
    let error = check("host_tools.lookup.params", &json!({ "minimum": "zero" })).unwrap_err();
    assert!(matches!(
        error,
        ConfigError::Invalid { key, rule }
            if key == "host_tools.lookup.params" && rule.contains("minimum")
    ));
    let error =
        check("done_when.returns", &json!({ "$schema": "http://json-schema.org/draft-07/schema#", "type": "string" }))
            .unwrap_err();
    assert!(matches!(
        error,
        ConfigError::Invalid { key, rule }
            if key == "done_when.returns" && rule.contains("$schema") && rule.contains(DIALECT)
    ));
    let error = check(
        "done_when.returns",
        &json!({ "properties": { "name": { "$schema": "http://json-schema.org/draft-07/schema#" } } }),
    )
    .unwrap_err();
    assert!(matches!(
        error,
        ConfigError::Invalid { key, rule }
            if key == "done_when.returns" && rule.contains("schema.properties.name") && rule.contains(DIALECT)
    ));
    let error =
        check("done_when.returns", &json!({ "$vocabulary": { "https://example.com/required-vocabulary": true } }))
            .unwrap_err();
    assert!(matches!(
        error,
        ConfigError::Invalid { key, rule }
            if key == "done_when.returns" && rule.contains("$vocabulary") && rule.contains("required-vocabulary")
    ));
    let error = check("done_when.returns", &json!({ "type": "string", "minLenght": 3 })).unwrap_err();
    assert!(matches!(
        error,
        ConfigError::Invalid { key, rule }
            if key == "done_when.returns" && rule.contains("minLenght") && rule.contains("unsupported")
    ));
    let error = check("done_when.returns", &json!({ "type": "string", "format": "project-id" })).unwrap_err();
    assert!(matches!(
        error,
        ConfigError::Invalid { key, rule }
            if key == "done_when.returns" && rule.contains("project-id")
    ));
    let error = check("done_when.returns", &json!({ "$ref": "file:///tmp/external-schema.json" })).unwrap_err();
    assert!(matches!(
        error,
        ConfigError::Invalid { key, rule }
            if key == "done_when.returns" && rule.contains("external-schema.json")
    ));
}
