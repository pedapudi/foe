use super::*;

#[test]
fn lines_drop_the_phantom_after_a_trailing_newline() {
    assert_eq!(lines(""), Vec::<&str>::new());
    assert_eq!(lines("a"), ["a"]);
    assert_eq!(lines("a\n"), ["a"]);
    assert_eq!(lines("a\n\n"), ["a", ""]);
    assert_eq!(lines("a\r\nb"), ["a", "b"]);
}

#[test]
fn head_stops_at_the_line_limit_or_the_byte_limit() {
    let l = ["aa", "bb", "cc"];
    assert_eq!(head(&l, 2, 100), Cut { start: 0, end: 2 });
    assert_eq!(head(&l, 10, 6), Cut { start: 0, end: 2 });
    assert_eq!(head(&l, 10, 100), Cut { start: 0, end: 3 });
}

#[test]
fn tail_keeps_the_last_lines_and_never_splits_a_multibyte_line() {
    let l = ["aa", "bb", "ééé", "cc"];
    assert_eq!(tail(&l, 2, 100), Cut { start: 2, end: 4 });
    assert_eq!(tail(&l, 10, 10), Cut { start: 2, end: 4 });
    assert_eq!(tail(&l, 10, 9), Cut { start: 3, end: 4 });
}

#[test]
fn a_first_line_over_the_byte_limit_yields_an_empty_cut() {
    assert_eq!(head(&["x".repeat(10).as_str()], 5, 4).len(), 0);
}
