#!/bin/sh
set -eu

launcher_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
launcher="$launcher_dir/$(basename -- "$0")"
if [ -n "${RUNFILES_DIR:-}" ] && [ -d "$RUNFILES_DIR/_main" ]; then
  repo_dir="$RUNFILES_DIR/_main"
  example_dir="$repo_dir/examples/team"
elif [ -d "$launcher.runfiles/_main" ]; then
  repo_dir="$launcher.runfiles/_main"
  example_dir="$repo_dir/examples/team"
else
  example_dir=$launcher_dir
  repo_dir=$(CDPATH= cd -- "$example_dir/../.." && pwd)
fi

binary=${1:-"target/release/foe"}
case "$binary" in
  /*) ;;
  *) binary="$repo_dir/$binary" ;;
esac

if [ ! -x "$binary" ]; then
  echo "team demo: $binary is not executable; run 'bazel run //examples/team'" >&2
  exit 1
fi

if [ -n "${TEST_TMPDIR:-}" ]; then
  output_dir=$TEST_TMPDIR
elif [ -n "${BUILD_WORKSPACE_DIRECTORY:-}" ]; then
  output_dir="$BUILD_WORKSPACE_DIRECTORY/target"
else
  output_dir="$repo_dir/target"
fi
mkdir -p "$output_dir"
run_dir=$(mktemp -d "$output_dir/foe-team-demo.XXXXXX")
project_dir="$run_dir/project"
log_dir="$run_dir/episode"
mkdir -p "$project_dir/src" "$project_dir/tests"

cat > "$project_dir/src/cli.py" <<'EOF'
"""Command line for the archive tool."""

import sys

ACTIONS = ("pack", "clean")


def perform(action, target):
    """Performs one action against the target directory."""
    return f"{action} {target}"


def main(argv):
    if len(argv) != 2:
        print("usage: cli.py <pack|clean> <target>")
        return 2
    action, target = argv
    if action not in ACTIONS:
        print(f"unknown action: {action}")
        return 2
    print(perform(action, target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
EOF

cat > "$project_dir/tests/check.py" <<'EOF'
"""Checks the command line: one case per action, and one for the dry run."""

import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import cli

CASES = [
    (["pack", "archive"], 0, "pack archive"),
    (["clean", "archive"], 0, "clean archive"),
    (["--dry-run", "pack", "archive"], 0, "would pack archive"),
]


def run(argv):
    """Returns the exit code and the printed text of one invocation."""
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        code = cli.main(argv)
    return code, printed.getvalue().strip()


failures = []
for argv, code, printed in CASES:
    got = run(argv)
    if got != (code, printed):
        failures.append(f"cli.main({argv}) returned {got} rather than {(code, printed)}")
for failure in failures:
    print(failure)
print(f"{len(CASES) - len(failures)} of {len(CASES)} checks passed")
raise SystemExit(1 if failures else 0)
EOF

/usr/bin/python3 "$repo_dir/examples/support/materialize.py" \
  "$example_dir/config.json" "$run_dir/config.json" \
  /home/user/project "$project_dir"

echo "Running the team demo in $run_dir"
/usr/bin/python3 "$repo_dir/examples/support/run_with_host.py" \
  "$binary" "$run_dir/config.json" "$log_dir" "$example_dir/responses.py"

grep -q 'usage: cli.py \[--dry-run\]' "$project_dir/src/cli.py"
(cd "$project_dir" && /usr/bin/python3 -B tests/check.py)

/usr/bin/python3 - "$log_dir" <<'PY'
"""Check the whole episode tree: the roster, the peer messages, the reports."""

import json
import sys
from pathlib import Path

root = Path(sys.argv[1])


def events(directory):
    lines = (directory / "episode.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def data_of(log, kind):
    return [event["data"] for event in log if event["type"] == kind]


def require(condition, message):
    if not condition:
        raise SystemExit(f"team demo: {message}")


lead = events(root)
lead_id = data_of(lead, "episode/start")[0]["id"]
starts = data_of(lead, "spawn/start")
roster = data_of(lead, "team/roster")
messages = data_of(lead, "team/message")
delivered = data_of(lead, "team/delivered")
reports = [item for item in data_of(lead, "inbox/item") if item["source"] == "child"]

require(len(starts) == 2, f"the lead started {len(starts)} members rather than two")
members = {start["contract"]: start["child_id"] for start in starts}
require(sorted(members) == ["reviewer", "tester"], f"the members are {sorted(members)}")
for name, member_id in members.items():
    phases = [entry["phase"] for entry in roster if entry["member_id"] == member_id]
    require(phases[:2] == ["provisioning", "active"], f"{name} entered the roster as {phases}")
    require("failed" not in phases, f"{name} is recorded as failed")

# One question from the reviewer to the tester, one answer back, each queued
# in the lead's log before delivery and settled by a matching record.
require(len(messages) == 2, f"the lead recorded {len(messages)} peer messages rather than two")
question, answer = messages
require(
    (question["from"], question["to"]) == (members["reviewer"], members["tester"]),
    "the first message did not run from the reviewer to the tester",
)
require(
    (answer["from"], answer["to"]) == (members["tester"], members["reviewer"]),
    "the second message did not run from the tester to the reviewer",
)
settled = {(record["message_id"], record["to"]) for record in delivered}
for message in messages:
    require(
        (message["message_id"], message["to"]) in settled,
        f"message {message['message_id']} was queued and never delivered",
    )

# The lead's one `wait` call returned only after both members were settled
# in its own log, so no reservation stands when the episode ends.
waited = [e["seq"] for e in lead if e["type"] == "tool/result" and e["data"]["name"] == "wait"]
settled_seqs = [e["seq"] for e in lead if e["type"] in ("spawn/end", "budget/release")]
require(len(waited) == 1, f"the lead called wait {len(waited)} times rather than once")
require(len(settled_seqs) == 4, f"{len(settled_seqs)} settlement events rather than four")
require(max(settled_seqs) < waited[0], "wait returned before both members were settled in the lead's log")

require(len(reports) == 2 * 2, f"the lead received {len(reports)} messages from members rather than four")
said = [item["content"][0]["text"] for item in reports]
require(any(text.startswith("review of ") for text in said), "the reviewer sent no report")
require(any(text.startswith("tests passed") for text in said), "the tester reported no passing run")

for name, member_id in members.items():
    member = events(root / "children" / member_id)
    start = data_of(member, "episode/start")[0]
    require(start["parent_id"] == lead_id, f"{name}: the member names another parent")
    require(start["team_id"] == lead_id, f"{name}: the member names another lead")
    peers = [item for item in data_of(member, "inbox/item") if item["source"] == "peer"]
    require(len(peers) == 1, f"{name}: {len(peers)} peer messages arrived rather than one")
    peer = peers[0]
    other = members["tester" if name == "reviewer" else "reviewer"]
    require(peer["from"] == other, f"{name}: the peer message came from {peer['from']} rather than {other}")
    require(
        peer["message_id"] in {message["message_id"] for message in messages},
        f"{name}: the peer message carries an id the lead never queued",
    )
    outcome = data_of(member, "episode/end")[0]["outcome"]
    require(outcome["kind"] == "completed", f"{name}: the member ended {outcome['kind']}")

print("Each member received the other's message through the lead, and both reported.")
PY

echo "Team demo passed. Inspect it with:"
if [ -n "${BUILD_WORKSPACE_DIRECTORY:-}" ]; then
  echo "  bazel run //:foe -- view $log_dir --serve"
else
  echo "  $binary view $log_dir --serve"
fi
