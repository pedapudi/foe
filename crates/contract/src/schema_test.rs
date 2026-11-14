use super::{arguments_conform, check, conforms, DIALECT};
use crate::ContractError;
use serde_json::json;

fn rule(key: &str, schema: &serde_json::Value) -> String {
    match check(key.to_string(), schema) {
        Err(ContractError::Invalid { key: named, rule }) => {
            assert_eq!(named, key);
            rule
        }
        other => panic!("expected an invalid-schema error, got {other:?}"),
    }
}

/// docs/config.md "JSON Schema subset": every implemented assertion is
/// enforced, so a value outside any one of them is refused by name.
#[test]
fn every_implemented_assertion_is_enforced() {
    let schema = json!({
        "type": "object",
        "additionalProperties": false,
        "required": ["count", "label", "tags"],
        "properties": {
            "count": { "type": "integer", "minimum": 2, "maximum": 8 },
            "label": { "type": "string", "minLength": 2, "maxLength": 4 },
            "tags": { "type": "array", "minItems": 1, "maxItems": 2, "items": { "type": "string" } },
            "kind": { "enum": ["a", "b"] },
            "version": { "const": 1 }
        }
    });
    let valid = json!({ "count": 3, "label": "abc", "tags": ["x"], "kind": "a", "version": 1 });
    assert!(conforms(&schema, &valid).is_ok(), "{:?}", conforms(&schema, &valid));
    for (field, value, expected) in [
        ("count", json!(1), "minimum"),
        ("count", json!(9), "maximum"),
        ("label", json!("a"), "minLength"),
        ("label", json!("abcde"), "maxLength"),
        ("tags", json!([]), "minItems"),
        ("tags", json!(["a", "b", "c"]), "maxItems"),
        ("tags", json!([1]), "expected type string"),
        ("kind", json!("c"), "is not one of"),
        ("version", json!(2), "is not the constant"),
    ] {
        let mut invalid = valid.clone();
        invalid[field] = value;
        let error = conforms(&schema, &invalid).unwrap_err();
        assert!(error.contains(field) && error.contains(expected), "{field}: {error}");
    }
    assert!(conforms(&schema, &json!({ "count": 3, "label": "ab" })).unwrap_err().contains("`tags`"));
    let extra = json!({ "count": 3, "label": "ab", "tags": [], "surprise": 1 });
    let unexpected = conforms(&schema, &extra).unwrap_err();
    assert!(unexpected.contains("`surprise`"), "{unexpected}");
    // The error lists the accepted properties, so a caller that misnamed a
    // field sees the name it should have used.
    assert!(unexpected.contains("count, kind, label, tags, version"), "{unexpected}");
}

/// docs/config.md "JSON Schema subset": `additionalProperties` closes an
/// object when it is false and types the remaining properties when it is a
/// schema, which is what `dict[str, T]` derives in the Python package.
#[test]
fn additional_properties_closes_an_object_or_types_the_rest() {
    let closed = json!({ "type": "object", "additionalProperties": false });
    assert!(conforms(&closed, &json!({})).is_ok());
    assert!(conforms(&closed, &json!({ "a": 1 })).unwrap_err().contains("`a`"));
    let typed = json!({ "type": "object", "additionalProperties": { "type": "integer" } });
    assert!(conforms(&typed, &json!({ "a": 1, "b": 2 })).is_ok());
    assert!(conforms(&typed, &json!({ "a": "one" })).unwrap_err().contains("value.a"));
}

/// docs/config.md "JSON Schema subset": `anyOf` accepts a value that matches
/// one alternative, which is what `Optional[T]` derives in the Python package.
#[test]
fn any_of_accepts_a_value_matching_one_alternative() {
    let schema = json!({ "anyOf": [{ "type": "string" }, { "type": "null" }] });
    assert!(conforms(&schema, &json!("text")).is_ok());
    assert!(conforms(&schema, &json!(null)).is_ok());
    assert!(conforms(&schema, &json!(7)).unwrap_err().contains("2 alternatives"));
}

/// docs/design.md "Tools": the error a model receives names the property, so
/// an `anyOf` whose alternatives all fail reports what each one wanted. Every
/// alternative states the field it refused, at the depth it refused it.
#[test]
fn a_failing_any_of_reports_why_each_alternative_refused() {
    let object = json!({
        "type": "object",
        "required": ["title", "count"],
        "properties": { "title": { "type": "string" }, "count": { "type": "integer" } }
    });
    let optional = json!({ "anyOf": [object, { "type": "null" }] });
    assert_eq!(
        conforms(&optional, &json!({ "heading": "x" })).unwrap_err(),
        "value: matches none of the 2 alternatives in `anyOf`: \
         value: lacks required property `title`; value: expected type null, found object"
    );
    let nested = json!({ "type": "object", "properties": { "item": optional.clone() } });
    assert_eq!(
        conforms(&nested, &json!({ "item": { "heading": "x" } })).unwrap_err(),
        "value.item: matches none of the 2 alternatives in `anyOf`: value.item: lacks required \
         property `title`; value.item: expected type null, found object"
    );
    let arguments = arguments_conform(&optional, &json!({ "heading": "x" })).unwrap_err();
    assert!(arguments.contains("arguments: lacks required property `title`"), "{arguments}");
}

/// docs/design.md "Tools": an alternative that refuses a value below its own
/// root keeps the deeper path, so the property the model has to correct is
/// named at the depth it sits rather than at the root of the `anyOf`.
#[test]
fn an_alternative_refusing_a_nested_field_names_that_field() {
    let inner = json!({ "type": "object", "properties": { "count": { "type": "integer" } } });
    let optional = json!({ "anyOf": [inner, { "type": "null" }] });
    let reported = conforms(&optional, &json!({ "count": "seven" })).unwrap_err();
    assert!(reported.contains("value.count: expected type integer, found string"), "{reported}");
}

/// docs/config.md "JSON Schema subset": accepting one alternative still
/// enforces sibling assertions, and an empty list accepts no value.
#[test]
fn any_of_keeps_sibling_assertions_and_empty_list_refusals() {
    let schema = json!({ "anyOf": [{ "type": "null" }, { "type": "string" }], "minLength": 3 });
    assert_eq!(conforms(&schema, &json!("x")).unwrap_err(), "value: is 1 characters long, outside `minLength` 3");
    assert!(conforms(&schema, &json!("abc")).is_ok());
    assert_eq!(
        conforms(&json!({ "anyOf": [] }), &json!(null)).unwrap_err(),
        "value: matches none of the 0 alternatives in `anyOf`"
    );
}

/// docs/design.md "Tools": a failing argument reads as its own path, so the
/// error the model receives names the property rather than the call.
#[test]
fn argument_errors_name_the_property_and_the_root_names_the_arguments() {
    let schema = json!({ "type": "object", "required": ["value"], "properties": { "value": { "type": "integer" } } });
    assert_eq!(
        arguments_conform(&schema, &json!({ "value": "three" })).unwrap_err(),
        "value: expected type integer, found string"
    );
    assert!(arguments_conform(&schema, &json!({})).unwrap_err().starts_with("arguments: lacks required"));
}

/// docs/config.md "JSON Schema subset": a schema asking for an assertion foe
/// does not implement is refused at construction, naming key and keyword.
#[test]
fn an_unimplemented_keyword_is_a_construction_error_naming_it() {
    for (schema, expected) in [
        (json!({ "type": "string", "pattern": "^a" }), "`pattern`"),
        (json!({ "type": "string", "format": "email" }), "`format`"),
        (json!({ "oneOf": [{ "type": "string" }] }), "`oneOf`"),
        (json!({ "$ref": "#/$defs/other" }), "`$ref`"),
        (json!({ "type": "string", "minLenght": 3 }), "`minLenght`"),
        (json!({ "type": "object", "properties": { "n": { "type": "integer", "multipleOf": 2 } } }), "`multipleOf`"),
        (json!({ "type": "array", "items": { "type": "string", "pattern": "a" } }), "`pattern`"),
        (json!({ "additionalProperties": { "type": "string", "pattern": "a" } }), "`pattern`"),
        (json!({ "anyOf": [{ "type": "null" }, { "type": "string", "format": "uri" }] }), "`format`"),
    ] {
        let reported = rule("done_when.returns", &schema);
        assert!(reported.contains(expected), "{expected}: {reported}");
    }
    let nested = rule(
        "host_tools.lookup.params",
        &json!({ "properties": { "n": { "type": "integer", "minimum": 1, "exclusiveMaximum": 4 } } }),
    );
    assert!(nested.contains("schema.properties.n"), "{nested}");
}

/// docs/config.md "JSON Schema subset": foe reads every embedded schema as
/// Draft 2020-12, so a schema declaring another dialect is refused.
#[test]
fn another_declared_dialect_is_refused() {
    let reported =
        rule("done_when.returns", &json!({ "$schema": "http://json-schema.org/draft-07/schema#", "type": "string" }));
    assert!(reported.contains("draft-07") && reported.contains(DIALECT), "{reported}");
    assert!(check("done_when.returns".into(), &json!({ "$schema": DIALECT, "type": "string" })).is_ok());
}

/// docs/config.md "JSON Schema subset": the table states the value each
/// keyword holds. A keyword holding another value is refused at construction,
/// naming the subschema and the keyword, so no declared constraint is ignored.
#[test]
fn a_keyword_value_of_the_wrong_shape_is_a_construction_error_naming_it() {
    let type_names = "has a type name or a non-empty list of type names at schema.type";
    let count = "has a non-negative integer at schema.";
    let boolean = "; a boolean schema is not implemented";
    for (schema, expected) in [
        (json!({ "type": 5 }), type_names.to_string()),
        (json!({ "type": "text" }), type_names.to_string()),
        (json!({ "type": [] }), type_names.to_string()),
        (json!({ "type": ["string", 5] }), type_names.to_string()),
        (json!({ "enum": "x" }), "has a list at schema.enum".to_string()),
        (json!({ "anyOf": { "type": "null" } }), "has a list at schema.anyOf".to_string()),
        (json!({ "anyOf": [true] }), format!("is a JSON Schema object at schema.anyOf[0]{boolean}")),
        (json!({ "required": "name" }), "has a list of property names at schema.required".to_string()),
        (json!({ "required": ["name", 5] }), "has a list of property names at schema.required".to_string()),
        (json!({ "properties": [] }), "has an object of subschemas at schema.properties".to_string()),
        (json!({ "properties": { "n": 5 } }), format!("is a JSON Schema object at schema.properties.n{boolean}")),
        (
            json!({ "additionalProperties": "no" }),
            format!("is a JSON Schema object at schema.additionalProperties{boolean}"),
        ),
        (json!({ "items": false }), format!("is a JSON Schema object at schema.items{boolean}")),
        (json!({ "items": [{ "type": "string" }] }), format!("is a JSON Schema object at schema.items{boolean}")),
        (json!({ "minimum": "3" }), "has a number at schema.minimum".to_string()),
        (json!({ "maximum": null }), "has a number at schema.maximum".to_string()),
        (json!({ "minLength": 1.5 }), format!("{count}minLength")),
        (json!({ "maxLength": "4" }), format!("{count}maxLength")),
        (json!({ "minItems": -1 }), format!("{count}minItems")),
        (json!({ "maxItems": true }), format!("{count}maxItems")),
        (
            json!({ "items": { "properties": { "n": { "minimum": "3" } } } }),
            "has a number at schema.items.properties.n.minimum".to_string(),
        ),
    ] {
        assert_eq!(rule("host_tools.lookup.params", &schema), expected, "{schema}");
    }
    let shaped = json!({
        "type": ["object", "null"], "required": ["n"], "additionalProperties": true,
        "properties": {
            "n": { "type": "number", "minimum": -1.5, "maximum": 3, "enum": [], "const": null },
            "s": { "type": "string", "minLength": 0, "maxLength": 4, "default": 5, "description": "d" },
            "a": { "type": "array", "minItems": 1, "maxItems": 2, "items": {}, "additionalProperties": false },
            "o": { "anyOf": [], "additionalProperties": { "type": "integer" }, "required": [] }
        }
    });
    assert!(check("done_when.returns".into(), &shaped).is_ok(), "{:?}", check("done_when.returns".into(), &shaped));
}
