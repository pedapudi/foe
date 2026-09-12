#!/usr/bin/python3
"""Every rule sentence of docs/config.md whose key no test cites, by the operational definition the task text states.

A key is a heading of docs/config.md of the form `### `key``; its section
runs to the next heading. A rule sentence is a sentence of the section's
prose: the lines outside fenced code blocks and outside table rows, grouped
into paragraphs at blank lines, headings, and the start of a list item, and
each paragraph split into sentences at a period, question mark, or
exclamation mark that is followed by whitespace or the end of the paragraph
and lies outside a backtick span. A test cites a key when a `///` doc
comment line in a file named `*_test.rs` under crates/ contains
`docs/config.md` and the first backtick span after it on the same line is
a backtick-quoted identifier; that identifier is the key.

    survey.py WORKSPACE        the uncited sentences as a JSON list of objects with `key` and `sentence`
    survey.py WORKSPACE --all  every key with its sentences and whether a test cites it

An absent docs/config.md or a test file the survey cannot read ends the
script with exit status 1 and a message naming the file.
"""

import json
import re
import sys
from pathlib import Path

DOCUMENT = "docs/config.md"
HEADING = re.compile(r"^#{1,6} ")
KEY_HEADING = re.compile(r"^### `([A-Za-z_][A-Za-z0-9_]*)`\s*$")
CITATION = re.compile(r"^\s*///.*docs/config\.md[^`]*`([A-Za-z_][A-Za-z0-9_]*)`")
CODE_SPAN = re.compile(r"`[^`]*`")
SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
LIST_ITEM = re.compile(r"^\s*(?:[-*]|\d+\.)\s")


def sentences(paragraph):
    text = " ".join(line.strip() for line in paragraph)
    masked = CODE_SPAN.sub(lambda match: "\0" * len(match.group(0)), text)
    found, start = [], 0
    for match in SENTENCE_END.finditer(masked):
        found.append(text[start : match.end()].strip())
        start = match.end()
    rest = text[start:].strip()
    if rest:
        found.append(rest)
    return found


def sections(document):
    """Each key's rule sentences, in document order."""
    found = {}
    key, fenced, paragraph = None, False, []

    def flush():
        if key is not None and paragraph:
            found.setdefault(key, []).extend(sentences(paragraph))
        paragraph.clear()

    for line in document.splitlines():
        if line.startswith("```"):
            flush()
            fenced = not fenced
            continue
        if fenced:
            continue
        if HEADING.match(line):
            flush()
            match = KEY_HEADING.match(line)
            key = match.group(1) if match else None
            continue
        if not line.strip() or line.lstrip().startswith("|"):
            flush()
            continue
        if LIST_ITEM.match(line):
            flush()
        paragraph.append(line)
    flush()
    return found


def read_text(root, relative):
    file = root / relative
    if not file.is_file():
        raise SystemExit(f"{relative}: absent from {root}, so the survey cannot read it")
    try:
        return file.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise SystemExit(f"{relative}: not UTF-8 at byte {error.start}, so the survey cannot read it") from error


def cited_keys(root):
    cited = set()
    for file in sorted((root / "crates").rglob("*_test.rs")):
        for line in read_text(root, file.relative_to(root).as_posix()).splitlines():
            match = CITATION.match(line)
            if match:
                cited.add(match.group(1))
    return cited


def survey(root):
    document = read_text(root, DOCUMENT)
    cited = cited_keys(root)
    return [{"key": key, "sentence": sentence} for key, found in sections(document).items() if key not in cited for sentence in found]


def everything(root):
    document = read_text(root, DOCUMENT)
    cited = cited_keys(root)
    return [{"key": key, "cited": key in cited, "sentences": found} for key, found in sections(document).items()]


if __name__ == "__main__":
    root = Path(sys.argv[1])
    print(json.dumps(everything(root) if "--all" in sys.argv[2:] else survey(root), indent=2))
