use super::*;

#[test]
fn selected_process_tools_name_only_their_exact_runtime_executables() {
    assert!(required_executables(&["read".into(), "edit".into()]).is_empty());
    assert_eq!(
        required_executables(&["bash".into(), "session".into(), foe_core::COMPOSING_TOOL.into()]),
        vec![("/bin/bash", "built-in bash and session tools"), ("/usr/bin/python3", "built-in tool composer")]
    );
}
