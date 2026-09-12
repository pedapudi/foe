#!/usr/bin/python3
"""Leave the crate root unparseable: a source change that `cargo check` rejects, which the grader must report."""

import pathlib
import sys

source = pathlib.Path(sys.argv[1]) / "crates/workflow/src/lib.rs"
if not source.is_file():
    raise SystemExit(f"{source} is absent")
source.write_text(source.read_text(encoding="utf-8") + "\npub fn crate_version( {\n", encoding="utf-8")
