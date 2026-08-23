use super::*;

#[test]
fn a_trace_id_is_sixteen_derived_bytes_and_a_span_id_is_eight() {
    let trace = trace_id("ep_5fad07ce");
    let span = span_id("ep_5fad07ce", "tool", 41);
    assert_eq!(trace.len(), 32);
    assert_eq!(span.len(), 16);
    assert!(trace.chars().all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()));
    assert!(span.chars().all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()));
}

#[test]
fn ids_are_a_function_of_their_inputs_alone() {
    assert_eq!(trace_id("ep_1"), trace_id("ep_1"));
    assert_ne!(trace_id("ep_1"), trace_id("ep_2"));
    assert_eq!(span_id("ep_1", "step", 4), span_id("ep_1", "step", 4));
    assert_ne!(span_id("ep_1", "step", 4), span_id("ep_1", "tool", 4));
    assert_ne!(span_id("ep_1", "step", 4), span_id("ep_1", "step", 5));
    assert_ne!(span_id("ep_1", "step", 4), span_id("ep_2", "step", 4));
}

#[test]
fn the_span_id_material_cannot_be_confused_across_its_three_parts() {
    // A separator that cannot occur in an episode id, a kind, or a number
    // keeps `("ep_1", "step", 12)` and `("ep_1step", "", 12)` apart.
    assert_ne!(span_id("ep_1", "step", 12), span_id("ep_1step", "", 12));
}

#[test]
fn times_are_the_logs_own_milliseconds_in_nanoseconds() {
    assert_eq!(nanos(1724200001010), "1724200001010000000");
    assert_eq!(nanos(0), "0");
    assert_eq!(nanos(-5), "0");
}

#[test]
fn an_attribute_encodes_as_the_protobuf_json_any_value() {
    let attributes =
        vec![text("a", "x"), number("b", 7), flag("c", true), list("d", ["p".to_string(), "q".to_string()])];
    let rendered = serde_json::to_string(&attributes).unwrap();
    assert_eq!(
        rendered,
        r#"[{"key":"a","value":{"stringValue":"x"}},{"key":"b","value":{"intValue":"7"}},"#.to_string()
            + r#"{"key":"c","value":{"boolValue":true}},"#
            + r#"{"key":"d","value":{"arrayValue":{"values":[{"stringValue":"p"},{"stringValue":"q"}]}}}]"#
    );
}

#[test]
fn a_root_span_omits_its_parent_and_a_child_names_it() {
    let trace = trace_id("ep_1");
    let root = span("ep", span_id("ep_1", "episode", 0), String::new(), "episode".into(), 10, 20);
    let child = span(&trace, span_id("ep_1", "step", 2), "abc".into(), "step 1".into(), 10, 20);
    assert!(!serde_json::to_string(&root).unwrap().contains("parentSpanId"));
    assert!(serde_json::to_string(&child).unwrap().contains(r#""parentSpanId":"abc""#));
}

#[test]
fn a_span_never_ends_before_it_starts() {
    // A tool result whose duration exceeds the gap to the previous event
    // would otherwise place its end before its start.
    let span = span("t", "s".into(), String::new(), "tool".into(), 100, 40);
    assert_eq!(span.end_time_unix_nano, span.start_time_unix_nano);
}

#[test]
fn a_status_is_the_numeric_enum_the_encoding_defines() {
    assert_eq!(serde_json::to_string(&status(true)).unwrap(), r#"{"code":1}"#);
    assert_eq!(serde_json::to_string(&status(false)).unwrap(), r#"{"code":2}"#);
}
