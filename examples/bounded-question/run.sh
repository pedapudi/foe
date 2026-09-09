#!/bin/sh
set -eu

launcher_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
example_dir=$launcher_dir
repo_dir=$(CDPATH= cd -- "$example_dir/../.." && pwd)

binary=${1:-"target/release/foe"}
case "$binary" in
  /*) ;;
  *) binary="$repo_dir/$binary" ;;
esac

if [ ! -x "$binary" ]; then
  echo "bounded-question demo: $binary is not executable; build it with 'cargo build -p foe'" >&2
  exit 1
fi

if [ -n "${TEST_TMPDIR:-}" ]; then
  output_dir=$TEST_TMPDIR
else
  output_dir="$repo_dir/target"
fi
mkdir -p "$output_dir"
run_dir=$(mktemp -d "$output_dir/foe-bounded-question.XXXXXX")
project_dir="$run_dir/project"
log_parent="$run_dir/episode"
mkdir -p "$project_dir/notes/east" "$project_dir/notes/west"

cat > "$project_dir/notes/east/notes.txt" <<'EOF'
sunrise 05:58
high tide 11:12
sunset 19:24
EOF

cat > "$project_dir/notes/west/notes.txt" <<'EOF'
sunrise 06:31
high tide 12:40
sunset 19:57
EOF

cat > "$project_dir/check.py" <<'EOF'
"""Checks that both notes files open with a header recording the same date."""

import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
HEADER = re.compile(r"^# recorded (\d{4}-\d{2}-\d{2}) (east|west)$")

dates = {}
failures = []
for region in ("east", "west"):
    path = root / "notes" / region / "notes.txt"
    first = path.read_text(encoding="utf-8").splitlines()[0]
    match = HEADER.match(first)
    if match is None:
        failures.append(f"notes/{region}/notes.txt opens with {first!r}, which is not a header")
        continue
    if match.group(2) != region:
        failures.append(f"notes/{region}/notes.txt names {match.group(2)}")
    dates[region] = match.group(1)
if len(dates) == 2 and dates["east"] != dates["west"]:
    failures.append(f"the headers disagree on the date: {dates['east']} and {dates['west']}")

for failure in failures:
    print(failure)
if failures:
    raise SystemExit(1)
print(f"both headers record {dates['east']}")
EOF

/usr/bin/python3 "$repo_dir/examples/support/materialize.py" \
  "$example_dir/config.json" "$run_dir/config.json" \
  /home/user/project "$project_dir"

echo "Running the bounded-question demo in $run_dir"
status=0
/usr/bin/python3 "$repo_dir/examples/support/run_with_host.py" \
  "$binary" "$run_dir/config.json" "$log_parent" "$example_dir/responses.py" 2>"$run_dir/foe.err" || status=$?
cat "$run_dir/foe.err" >&2
[ "$status" -eq 0 ] || exit "$status"
log_dir=$(sed -n 's/^foe: log //p' "$run_dir/foe.err" | head -n 1)

(cd "$project_dir" && /usr/bin/python3 -B check.py)

/usr/bin/python3 - "$log_dir" <<'PY'
"""Check that one question was answered and the other took its default."""

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
        raise SystemExit(f"bounded-question demo: {message}")


lead = events(root)
# The roster names each member; `spawn/start` names only the contract, and
# both recorders run the same one.
members = {entry["name"]: entry["member_id"] for entry in data_of(lead, "team/roster")}
require(sorted(members) == ["east", "west"], f"the members are {sorted(members)}")

# Each recorder writes only its own directory, which is what keeps two
# writers off one file.
granted = {task["name"]: task["write"] for task in data_of(lead, "team/task") if task.get("write")}
for region, roots in granted.items():
    require(
        len(roots) == 1 and roots[0].endswith(f"notes/{region}"),
        f"{region} was granted {roots} rather than its own directory",
    )

# The lead's log carries both questions and the one answer it sent. A
# question arrives under `request`; an answer carries the question's own
# identifier, which is what lets the asker wait for that answer.
lead_id = data_of(lead, "episode/start")[0]["id"]
messages = data_of(lead, "team/message")
questions = [item for item in data_of(lead, "inbox/item") if item["source"] == "request"]
require(len(questions) == 2, f"the lead was asked {len(questions)} questions rather than two")
# Every message the team carries is durable in the lead's log, questions
# included, so direction is what separates the one answer from the two
# questions that reached the lead.
sent = [message for message in messages if message["from"] == lead_id]
require(len(sent) == 1, f"the lead sent {len(sent)} messages rather than one")
answer = sent[0]
require(answer["to"] == members["east"], "the lead's answer did not go to the eastern recorder")
require(
    answer["message_id"] in {item["message_id"] for item in questions},
    "the answer carries an identifier no question raised",
)

for region, member_id in members.items():
    member = events(root / "children" / member_id)
    asked = next(
        event for event in member
        if event["type"] == "tool/result" and event["data"]["name"] == "ask"
    )
    answers = [
        event for event in member
        if event["type"] == "inbox/item" and event["data"]["source"] == "response"
    ]
    require(len(answers) == 1, f"{region}: {len(answers)} answers arrived rather than one")
    arrived = answers[0]
    require(
        arrived["data"]["message_id"] == asked["data"]["value"]["message_id"],
        f"{region}: the answer names another question",
    )
    if region == "east":
        require(not arrived["data"].get("synthetic"), "east: the lead's reply was recorded as the runtime's")
        require(arrived["data"]["from"] is not None, "east: the reply names no sender")
    else:
        require(arrived["data"].get("synthetic"), "west: the default was not marked as the runtime's")
        require(arrived["data"]["from"] is None, "west: the default names a sender")
        waited = arrived["time"] - asked["time"]
        require(waited >= 1000, f"west: the default arrived {waited} ms after the question, inside its deadline")
    outcome = data_of(member, "episode/end")[0]["outcome"]
    require(outcome["kind"] == "completed", f"{region}: the recorder ended {outcome['kind']}")

print("One question was answered by the lead; the other's deadline passed and its default stood.")
PY

echo "Bounded-question demo passed. Inspect it with:"
echo "  $binary view $log_dir --serve"
