#!/usr/bin/python3
"""Every error message under crates/ that names no subject, by the operational definition the task text states.

An error message is the string literal of an `#[error("...")]` attribute
whose literal is the attribute's only argument, written on one line whose
first non-blank characters are `#[error(`, in a `.rs` file under crates/
that is neither named `*_test.rs` nor held under a `tests/` directory;
`#[error(transparent)]` carries no message, and an attribute with arguments
after the literal, `#[error("...", expr)]`, is outside the survey. A
message names its subject when it contains a backtick-quoted identifier, a
key path, or the placeholder `{key}`. A key path is two or more segments
joined by single dots, each segment a name of two or more characters from
lowercase letters, digits, and underscores that starts with a letter or
underscore, or such a name in braces.

    survey.py WORKSPACE

prints the failing messages as a JSON list of objects with `path`, `line`,
and `message`, in path and line order. A file the survey cannot read ends
the script with exit status 1 and a message naming the file.
"""

import json
import re
import sys
from pathlib import Path

ATTRIBUTE = re.compile(r'^\s*#\[error\("((?:[^"\\]|\\.)*)"\)\]')
BACKTICK = re.compile(r"`[^`]+`")
SEGMENT = r"(?:[a-z_][a-z0-9_]+|\{[a-z_][a-z0-9_]+\})"
KEY_PATH = re.compile(r"(?<![A-Za-z0-9_{}])" + SEGMENT + r"(?:\." + SEGMENT + r")+(?![A-Za-z0-9_])")
KEY_PLACEHOLDER = "{key}"
TEST_FILE = re.compile(r"(^|/)tests/|_test\.rs$")


def read_lines(file, relative):
    try:
        return file.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise SystemExit(f"{relative}: not UTF-8 at byte {error.start}, so the survey cannot read it") from error


def names_subject(message):
    return bool(BACKTICK.search(message) or KEY_PATH.search(message) or KEY_PLACEHOLDER in message)


def survey(root):
    items = []
    for file in sorted((root / "crates").rglob("*.rs")):
        relative = file.relative_to(root).as_posix()
        if TEST_FILE.search(relative) or not file.is_file():
            continue
        for number, line in enumerate(read_lines(file, relative), start=1):
            match = ATTRIBUTE.match(line)
            if match and not names_subject(match.group(1)):
                items.append({"path": relative, "line": number, "message": match.group(1)})
    return items


if __name__ == "__main__":
    print(json.dumps(survey(Path(sys.argv[1])), indent=2))
