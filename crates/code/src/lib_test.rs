#[test]
fn all_lists_each_tool_once_and_readonly_lists_the_reads_tools() {
    let names: Vec<String> = super::all().iter().map(|t| t.spec().name.clone()).collect();
    let mut expected = vec!["read", "grep", "edit"];
    if cfg!(feature = "exec") {
        expected.push("bash");
    }
    assert_eq!(names, expected);
    let ro: Vec<String> = super::readonly().iter().map(|t| t.spec().name.clone()).collect();
    assert_eq!(ro, ["read", "grep"]);
    for t in super::all() {
        let words = t.spec().description.split_whitespace().count();
        assert!(words < 80, "{} description has {words} words", t.spec().name);
        assert!(t.spec().instruction.is_some(), "{} lacks an instruction", t.spec().name);
    }
}

/// docs/config.md "JSON Schema subset": dispatch checks a call against the
/// tool's parameter schema, so a schema this crate writes stays inside the
/// subset the runtime evaluates.
#[test]
fn every_coding_tool_schema_stays_inside_the_implemented_subset() {
    for tool in super::all() {
        let spec = tool.spec();
        foe_core::schema::check(format!("tools.{}.params", spec.name), &spec.params).unwrap();
    }
}
