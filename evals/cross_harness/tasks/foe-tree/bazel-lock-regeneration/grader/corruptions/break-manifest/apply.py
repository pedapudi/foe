#!/usr/bin/python3
"""Leave Cargo.toml unparseable: a manifest change that `cargo check` rejects, which the grader must report."""

import pathlib
import sys

manifest = pathlib.Path(sys.argv[1]) / "Cargo.toml"
if not manifest.is_file():
    raise SystemExit(f"{manifest} is absent")
manifest.write_text(manifest.read_text(encoding="utf-8") + "\n[dependencies\n", encoding="utf-8")
