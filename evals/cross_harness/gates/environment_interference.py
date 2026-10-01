"""Report, per attempt, how often the environment refused an arm rather than the task defeating it.

Four defects in this evaluation degraded one harness without changing any
outcome: a toolchain that was granted and not on the search path, a home
directory that made the toolchain manager unusable, and two refusals to write
inside a granted write root. Each was found by reading logs after the fact.
None showed in a confusion cell, and three left attempts scored as correct
completions, because the arm worked around the obstacle and the grader runs
outside the sandbox.

This counts the signals those defects leave, so that a comparison can say
whether the two harnesses ran in the same environment instead of assuming it.
A run where one arm is refused and the other is not is not a comparison of
harnesses, whatever its cells say.
"""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

# What a degraded environment says, whichever harness is speaking.
SIGNALS = {
    "permission denied": re.compile(r"[Pp]ermission denied|os error 13"),
    "command not found": re.compile(r"not found|command not found"),
    "no network": re.compile(r"[Cc]ould not resolve host|[Ff]ailed to (?:download|connect)|Network is unreachable"),
}
COMMAND_NOT_FOUND_STATUS = 127


def foe_signals(attempt: Path) -> Counter:
    """Signals in the tool results of every episode of one foe attempt."""
    found: Counter = Counter()
    for log in attempt.glob("log/**/episode.jsonl"):
        for line in log.read_text(errors="replace").splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("type") != "tool/result":
                continue
            value = (record.get("data") or {}).get("value") or {}
            text = f"{value.get('stdout', '')}{value.get('stderr', '')}"
            if value.get("exit_code") == COMMAND_NOT_FOUND_STATUS:
                found["exited 127"] += 1
            for name, pattern in SIGNALS.items():
                if pattern.search(text):
                    found[name] += 1
    return found


def codex_signals(attempt: Path) -> Counter:
    """The same signals in the event stream of one attempt of the other harness."""
    found: Counter = Counter()
    events = attempt / "artifacts" / "events.jsonl"
    if not events.is_file():
        return found
    for line in events.read_text(errors="replace").splitlines():
        for name, pattern in SIGNALS.items():
            if pattern.search(line):
                found[name] += 1
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("attempts", type=Path, help="the attempts directory of a run")
    args = parser.parse_args(argv)

    rows = []
    for attempt in sorted(args.attempts.glob("*/*/[0-9]*")):
        if not attempt.is_dir():
            continue
        arm = attempt.parent.name
        signals = foe_signals(attempt) if arm.startswith("foe") else codex_signals(attempt)
        rows.append((attempt.parent.parent.name, arm, signals))
    if not rows:
        print(f"{args.attempts}: no attempts")
        return 1

    names = sorted({name for _, _, s in rows for name in s})
    print("| task | arm | " + " | ".join(names) + " |")
    print("|---|---|" + "---|" * len(names))
    for task, arm, signals in rows:
        cells = " | ".join(str(signals.get(name, 0)) for name in names)
        print(f"| `{task}` | `{arm}` | {cells} |")

    per_harness: dict[str, Counter] = {}
    for _, arm, signals in rows:
        per_harness.setdefault("foe" if arm.startswith("foe") else "the other harness", Counter()).update(signals)
    print("\n| harness | " + " | ".join(names) + " |")
    print("|---|" + "---|" * len(names))
    for harness, signals in sorted(per_harness.items()):
        print(f"| {harness} | " + " | ".join(str(signals.get(name, 0)) for name in names) + " |")
    print(
        "\nA signal is a line an arm's own tooling printed, not a judgement about the task. "
        "Read the two harness rows against each other: a run where one is refused and the other is not "
        "is not comparing harnesses."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
