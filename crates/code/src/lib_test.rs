#[test]
fn all_lists_each_tool_once_and_readonly_lists_the_reads_tools() {
    let names: Vec<String> = super::all().iter().map(|t| t.spec().name.clone()).collect();
    let expected = vec!["read", "grep", "edit"];
    assert_eq!(names, expected);
    let ro: Vec<String> = super::readonly()
        .iter()
        .map(|t| t.spec().name.clone())
        .collect();
    assert_eq!(ro, ["read", "grep"]);
    for t in super::all() {
        let words = t.spec().description.split_whitespace().count();
        assert!(
            words < 80,
            "{} description has {words} words",
            t.spec().name
        );
        assert!(
            t.spec().instruction.is_some(),
            "{} lacks an instruction",
            t.spec().name
        );
    }
}
