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
"""Check board scheduling, peer messages, and nested delegation."""

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
task_events = [event for event in lead if event["type"] == "team/task"]
messages = data_of(lead, "team/message")
delivered = data_of(lead, "team/delivered")
reports = [item for item in data_of(lead, "inbox/item") if item["source"] == "child"]

require(len(starts) == 3, f"the lead started {len(starts)} members rather than three")
members = {start["contract"]: start["child_id"] for start in starts}
require(
    sorted(members) == ["integration", "reviewer", "tester"],
    f"the members are {sorted(members)}",
)
for name, member_id in members.items():
    phases = [entry["phase"] for entry in roster if entry["member_id"] == member_id]
    require(phases[:2] == ["provisioning", "active"], f"{name} entered the roster as {phases}")
    require(phases[-1] == "active", f"{name} has unexpected roster phases {phases}")

# The root task is derived. The two independent tasks start together. The
# integration task remains queued until both dependencies complete.
require(
    all(event["data"]["task_id"] != "task_root" for event in task_events),
    "the derived root task was written as a team/task event",
)


def task_states(task_id):
    return [
        event["data"]["status"]
        for event in task_events
        if event["data"]["task_id"] == task_id
    ]


for task_id in ("task_01", "task_02", "task_03"):
    require(
        task_states(task_id) == ["queued", "running", "completed"],
        f"{task_id} moved through {task_states(task_id)}",
    )
integration_added = next(
    event for event in task_events
    if event["data"]["task_id"] == "task_03" and event["data"]["status"] == "queued"
)
require(
    integration_added["data"]["blocked_by"] == ["task_01", "task_02"],
    "the integration task does not name both dependencies",
)
dependency_ends = [
    event["seq"] for event in task_events
    if event["data"]["task_id"] in ("task_01", "task_02")
    and event["data"]["status"] == "completed"
]
integration_start = next(
    event["seq"] for event in task_events
    if event["data"]["task_id"] == "task_03" and event["data"]["status"] == "running"
)
require(max(dependency_ends) < integration_start, "integration started before both dependencies completed")

first_two = {members["reviewer"], members["tester"]}
first_two_starts = [
    event["seq"] for event in lead
    if event["type"] == "spawn/start" and event["data"]["child_id"] in first_two
]
first_two_ends = [
    event["seq"] for event in lead
    if event["type"] == "spawn/end" and event["data"]["child_id"] in first_two
]
require(max(first_two_starts) < min(first_two_ends), "review and test did not overlap")

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

# The lead's one `wait` call returned after all root-board tasks settled.
waited = [e["seq"] for e in lead if e["type"] == "tool/result" and e["data"]["name"] == "wait"]
settled_seqs = [e["seq"] for e in lead if e["type"] in ("spawn/end", "budget/release")]
require(len(waited) == 1, f"the lead called wait {len(waited)} times rather than once")
require(len(settled_seqs) == 6, f"{len(settled_seqs)} settlement events rather than six")
require(max(settled_seqs) < waited[0], "wait returned before the root board settled")

require(len(reports) == 3 * 2, f"the lead received {len(reports)} member messages rather than six")
said = [item["content"][0]["text"] for item in reports]
require(any(text.startswith("review of ") for text in said), "the reviewer sent no report")
require(any(text.startswith("tests passed") for text in said), "the tester reported no passing run")
require(any(text.startswith("integration passed") for text in said), "integration reported no passing run")

for name in ("reviewer", "tester"):
    member_id = members[name]
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

integration_id = members["integration"]
integration_log = events(root / "children" / integration_id)
parent_board = next(
    event["data"]["value"] for event in integration_log
    if event["type"] == "tool/result" and event["data"]["name"] == "team"
)
require(parent_board["lead_id"] == lead_id, "integration did not inspect its parent-led board")
require(len(parent_board["tasks"]) == 4, "the parent board did not include all root tasks")
nested_starts = data_of(integration_log, "spawn/start")
require(len(nested_starts) == 1, "integration did not delegate exactly one nested task")
auditor_id = nested_starts[0]["child_id"]
nested_states = [
    event["data"]["status"] for event in integration_log
    if event["type"] == "team/task" and event["data"]["task_id"] == "task_01"
]
require(
    nested_states == ["queued", "running", "completed"],
    f"the nested task moved through {nested_states}",
)
require(
    not any(
        event["type"] == "team/task" and event["data"]["task_id"] == "task_root"
        for event in integration_log
    ),
    "the nested board wrote its derived root task",
)
auditor = events(root / "children" / integration_id / "children" / auditor_id)
auditor_start = data_of(auditor, "episode/start")[0]
require(auditor_start["parent_id"] == integration_id, "the auditor names another parent")
require(auditor_start["team_id"] == integration_id, "the auditor names another team lead")
require(
    data_of(auditor, "episode/end")[0]["outcome"]["kind"] == "completed",
    "the nested auditor did not complete",
)

print("Concurrent review and test work unlocked integration, which led a nested audit team.")
PY

echo "Team demo passed. Inspect it with:"
if [ -n "${BUILD_WORKSPACE_DIRECTORY:-}" ]; then
  echo "  bazel run //:foe -- view $log_dir --serve"
else
  echo "  $binary view $log_dir --serve"
fi
