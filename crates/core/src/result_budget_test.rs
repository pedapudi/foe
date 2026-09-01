use super::*;
use serde_json::json;

fn value(chars: usize) -> ToolValue {
    let line = "x".repeat(79);
    let mut body = String::new();
    while body.chars().count() < chars {
        body.push_str(&line);
        body.push('\n');
    }
    ToolValue::ok(json!({}), body)
}

/// A rendering shaped the way `read` numbers its window.
fn window(first: usize, lines: usize) -> ToolValue {
    let body: String = (first..first + lines).map(|n| format!("{n}\tsource line {n}\n")).collect();
    ToolValue::ok(json!({}), body)
}

fn shown(values: &[ToolValue]) -> usize {
    values.iter().map(|v| v.rendered.as_deref().unwrap_or_default().chars().count()).sum()
}

fn apply_bound(values: &mut [ToolValue]) -> Vec<Option<crate::retrieval::ArchivedRendering>> {
    let calls: Vec<ToolCall> = (0..values.len())
        .map(|index| ToolCall { id: format!("tc_{index}"), name: "probe".into(), args: json!({}) })
        .collect();
    bound(values, &calls, 1, false)
}

/// docs/tools.md: a turn whose results fit within the budget is unchanged.
#[test]
fn a_turn_within_the_budget_shows_every_result_whole() {
    let mut values = vec![value(300), value(40_000)];
    let before: Vec<String> = values.iter().map(|v| v.rendered.clone().unwrap()).collect();
    apply_bound(&mut values);
    assert_eq!(values.iter().map(|v| v.rendered.clone().unwrap()).collect::<Vec<_>>(), before);
}

/// docs/tools.md: one call may show a full-size result, because the limits
/// of `read` and `bash` already bound one call below the turn budget.
#[test]
fn one_maximal_result_passes_whole() {
    let mut values = vec![value(crate::result_budget::TURN_BUDGET_CHARS - 1)];
    let before = values[0].rendered.clone().unwrap();
    apply_bound(&mut values);
    assert_eq!(values[0].rendered.clone().unwrap(), before);
}

/// docs/tools.md: the calls of one turn divide one budget, so parallel
/// calls cannot each take a limit of their own.
#[test]
fn parallel_calls_divide_one_budget() {
    let mut six: Vec<ToolValue> = (0..6).map(|_| value(60_000)).collect();
    let archives = apply_bound(&mut six);
    assert!(shown(&six) <= TURN_BUDGET_CHARS, "six calls showed {}", shown(&six));
    assert!(archives.iter().all(Option::is_some), "cut results remain archived without retrieve");
    assert!(six.iter().all(|value| value.rendered.as_deref().unwrap().contains("Issue the call again")));
}
/// docs/tools.md: a result shorter than its equal part leaves the remainder
/// to the others.
#[test]
fn a_short_result_leaves_its_remainder_to_the_others() {
    let mut values = vec![value(200), value(90_000)];
    let short = values[0].rendered.clone().unwrap();
    apply_bound(&mut values);
    assert_eq!(values[0].rendered.as_deref(), Some(short.as_str()), "the short result is untouched");
    let large = values[1].rendered.as_deref().unwrap().chars().count();
    assert!(large > TURN_BUDGET_CHARS / 2, "the large result received only {large} characters");
}

/// docs/tools.md "The turn budget": no result is held below a floor of
/// 4,000 characters, whatever the division leaves it, so a turn of many
/// calls costs more than the turn budget and never more than the floor
/// times the number of calls.
#[test]
fn no_result_is_held_below_the_floor() {
    const CALLS: usize = 40;
    let mut values: Vec<ToolValue> = (0..CALLS).map(|_| value(60_000)).collect();
    apply_bound(&mut values);
    for v in &values {
        // The share is the floor; the notice naming what was removed is
        // taken out of it, and the cut lands on a line boundary.
        let each = v.rendered.as_deref().unwrap().chars().count();
        let room = RESULT_FLOOR_CHARS - NOTICE_CHARS;
        assert!(each >= room, "a result showed {each} of the {RESULT_FLOOR_CHARS} floor");
        assert!(each <= RESULT_FLOOR_CHARS, "a result showed {each}, above the {RESULT_FLOOR_CHARS} floor");
    }
    let total = shown(&values);
    assert!(total > TURN_BUDGET_CHARS, "the floor lifted every result above an equal division: {total}");
    assert!(total <= RESULT_FLOOR_CHARS * CALLS, "a turn costs no more than the floor times its calls: {total}");
}

/// docs/tools.md: a numbered window keeps its head alone, and the notice
/// names the file line to resume at. The live evidence for cutting this way
/// is that a model given a cut window asks for the lines after the head.
#[test]
fn a_numbered_window_keeps_its_head_and_names_the_line_to_resume_at() {
    let mut values = vec![window(1, 4_000), value(90_000)];
    apply_bound(&mut values);
    let out = values[0].rendered.clone().unwrap();
    assert!(out.starts_with("1\tsource line 1\n"), "{out:.60}");
    assert!(!out.contains("source line 4000"), "the tail of a window is not kept");
    let last: usize = out
        .lines()
        .rev()
        .nth(1)
        .and_then(|l| l.split_once('\t'))
        .map(|(n, _)| n.parse().unwrap())
        .expect("a numbered line before the notice");
    assert!(out.ends_with(&format!("offset={} to continue from here.]", last + 1)), "{}", &out[out.len() - 120..]);
}

/// docs/tools.md: a window that does not start at line 1 resumes at a line
/// of the file rather than of the rendering.
#[test]
fn a_window_resumes_at_a_line_of_the_file() {
    let mut values = vec![window(500, 4_000), value(90_000)];
    apply_bound(&mut values);
    let out = values[0].rendered.clone().unwrap();
    assert!(out.starts_with("500\tsource line 500\n"), "{out:.60}");
    assert!(!out.contains("offset=1 "), "{}", &out[out.len() - 120..]);
}

/// docs/tools.md: any other rendering keeps its head and its tail, because
/// a command's output carries its verdict at both ends.
#[test]
fn other_output_keeps_its_head_and_its_tail() {
    let body: String = std::iter::once("[exit 1 in 4.20s]\n".to_owned())
        .chain((1..=6_000).map(|n| format!("output line {n}\n")))
        .collect();
    let mut values = vec![ToolValue::ok(json!({}), body), value(90_000)];
    apply_bound(&mut values);
    let out = values[0].rendered.clone().unwrap();
    assert!(out.starts_with("[exit 1 in 4.20s]\n"), "the status leads");
    assert!(out.ends_with("output line 6000"), "the verdict at the end survives");
    assert!(out.contains("lines omitted here"), "{out:.300}");
}

/// docs/tools.md: output that only looks numbered part way through is not a
/// window, so its notice does not name an offset to read.
#[test]
fn output_whose_later_lines_look_numbered_is_not_a_window() {
    let body: String = std::iter::once("[exit 0 in 0.10s]\n".to_owned())
        .chain((1..=6_000).map(|n| format!("{n}\toutput column\n")))
        .collect();
    let mut values = vec![ToolValue::ok(json!({}), body), value(90_000)];
    apply_bound(&mut values);
    let out = values[0].rendered.clone().unwrap();
    assert!(!out.contains("offset="), "a command's output names no offset to read");
    assert!(out.contains("lines omitted here"), "{out:.200}");
    assert!(out.ends_with("6000\toutput column"), "the tail survives");
}

/// docs/tools.md: a result with no rendering of its own is counted and cut
/// through its canonical value.
#[test]
fn a_result_without_a_rendering_is_rendered_and_counted() {
    let mut values = vec![
        ToolValue {
            value: json!({ "text": "y".repeat(200_000) }),
            rendered: None,
            is_error: false,
            failure: None,
            subject: None,
        },
        value(90_000),
    ];
    apply_bound(&mut values);
    assert!(values[0].rendered.as_deref().unwrap().chars().count() <= TURN_BUDGET_CHARS);
}

/// docs/tools.md "What a cut keeps": every shortened rendering is retained,
/// and a contract with retrieve receives an opaque cursor in the notice.
#[test]
fn a_cut_retains_the_complete_rendering_and_offers_retrieval() {
    let mut values = vec![value(60_000), value(60_000)];
    let original = values[0].rendered.clone().unwrap();
    let calls = vec![
        ToolCall { id: "tc_a".into(), name: "probe".into(), args: json!({}) },
        ToolCall { id: "tc_b".into(), name: "probe".into(), args: json!({}) },
    ];
    let archives = bound(&mut values, &calls, 4, true);
    let archived = archives[0].as_ref().unwrap();
    assert_eq!(archived.complete, original);
    assert_eq!(archived.digest, crate::retrieval::digest(original.as_bytes()));
    let notice = values[0].rendered.as_ref().unwrap();
    assert!(notice.contains("Use retrieve with cursor \"r1."), "{}", &notice[notice.len() - 300..]);
    assert!(notice.chars().count() <= TURN_BUDGET_CHARS / 2);
}
