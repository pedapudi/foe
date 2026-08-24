#!/usr/bin/env python3
"""Check the public Vim-macro contract without changing /app/input.csv."""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path


SCRIPT = Path("/app/apply_macros.vim")
INPUT = Path("/app/input.csv")
EXPECTED = Path("/app/expected.csv")
SETREG = re.compile(r"^call setreg\('([abc])', \"(.+)\"\)$")
EXECUTE = {f":%normal! @{register}" for register in "abc"}
SAMPLE_ROWS = 10_000


def copy_rows(source: Path, target: Path) -> None:
    with source.open("rb") as source_file, target.open("wb") as target_file:
        for index, line in enumerate(source_file):
            if index >= SAMPLE_ROWS:
                break
            target_file.write(line)


def inspect_script() -> list[str]:
    if not SCRIPT.is_file():
        return ["/app/apply_macros.vim does not exist"]
    try:
        raw_lines = SCRIPT.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        return [f"/app/apply_macros.vim cannot be read as UTF-8: {error}"]
    lines = [line.strip() for line in raw_lines if line.strip()]
    findings = []
    macros: dict[str, str] = {}
    executions = []
    exits = []
    for line in lines:
        match = SETREG.fullmatch(line)
        if match:
            register, content = match.groups()
            if register in macros:
                findings.append(f"register {register} is assigned more than once")
            macros[register] = content
        elif line in EXECUTE:
            executions.append(line)
        elif line in {":wq", ":x"}:
            exits.append(line)
        else:
            findings.append(f"apply_macros.vim contains a disallowed command: {line}")
    if set(macros) != set("abc"):
        findings.append("apply_macros.vim must define non-empty macros a, b, and c")
    if len(set(macros.values())) != 3:
        findings.append("macros a, b, and c must be distinct")
    if sum(len(value) for value in macros.values()) >= 200:
        findings.append("the three macro bodies contain 200 or more keystrokes")
    if executions != [":%normal! @a", ":%normal! @b", ":%normal! @c"]:
        findings.append("the script must execute macros a, b, and c once in that order")
    if len(exits) != 1 or lines[-1:] != exits:
        findings.append("the script must end with exactly one :wq or :x command")
    return findings


def check_result() -> list[str]:
    missing = [str(path) for path in (INPUT, EXPECTED) if not path.is_file()]
    if missing:
        return ["task input is missing: " + ", ".join(missing)]
    with tempfile.TemporaryDirectory(prefix="foe-vim-check-") as directory:
        candidate = Path(directory) / "input.csv"
        expected_sample = Path(directory) / "expected.csv"
        copy_rows(INPUT, candidate)
        copy_rows(EXPECTED, expected_sample)
        result = subprocess.run(
            [
                "/usr/bin/vim",
                "-Nu",
                "NONE",
                "-n",
                "-Es",
                str(candidate),
                "-S",
                str(SCRIPT),
            ],
            text=True,
            capture_output=True,
            timeout=240,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            return [
                f"Vim exited with status {result.returncode} on a copy of input.csv: {detail}"
            ]
        if candidate.read_bytes() != expected_sample.read_bytes():
            return [
                f"the macro script does not transform the first {SAMPLE_ROWS} rows "
                "to the corresponding visible expected rows"
            ]
    return []


def main() -> list[str]:
    findings = inspect_script()
    if findings:
        return findings
    try:
        return check_result()
    except subprocess.TimeoutExpired:
        return ["the macro script did not finish within 240 seconds"]
    except OSError as error:
        return [f"the macro result could not be checked: {error}"]


if __name__ == "__main__":
    for finding in main():
        print(finding)
