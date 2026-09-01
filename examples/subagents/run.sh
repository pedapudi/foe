#!/bin/sh
set -eu

launcher_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
launcher="$launcher_dir/$(basename -- "$0")"
if [ -n "${RUNFILES_DIR:-}" ] && [ -d "$RUNFILES_DIR/_main" ]; then
  repo_dir="$RUNFILES_DIR/_main"
  example_dir="$repo_dir/examples/subagents"
elif [ -d "$launcher.runfiles/_main" ]; then
  repo_dir="$launcher.runfiles/_main"
  example_dir="$repo_dir/examples/subagents"
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
  echo "subagents demo: $binary is not executable; run 'bazel run //examples/subagents'" >&2
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
run_dir=$(mktemp -d "$output_dir/foe-subagents-demo.XXXXXX")
project_dir="$run_dir/project"
log_dir="$run_dir/episode"
mkdir -p "$project_dir/src" "$project_dir/tools" "$project_dir/support"

# The model transport is a contract the episode starts, so it reads only
# inside the episode's read roots. Both it and the helper module it imports
# are copied into the project, which every episode in this tree may read.
cp "$example_dir/transport.py" "$project_dir/tools/transport.py"
cp "$repo_dir/examples/support/chunks.py" "$project_dir/support/chunks.py"
chmod +x "$project_dir/tools/transport.py"

cat > "$project_dir/src/config.py" <<'EOF'
"""Settings the client reads."""

DEFAULTS = {"timeout": 30, "retries": 3}


def load(overrides):
    """Returns the defaults with the caller's overrides applied."""
    settings = dict(DEFAULTS)
    settings.update(overrides)
    return settings


def deadline_of(settings):
    """Seconds to wait before a request is abandoned."""
    return settings["timeout"]
EOF

cat > "$project_dir/src/client.py" <<'EOF'
"""One request against the configured service."""

from config import load


def connect(overrides):
    """Returns the connection parameters for one request."""
    settings = load(overrides)
    return {"deadline": settings["timeout"], "retries": settings["retries"]}
EOF

/usr/bin/python3 "$repo_dir/examples/support/materialize.py" \
  "$example_dir/config.json" "$run_dir/config.json" \
  /home/user/project "$project_dir"

echo "Running the subagents demo in $run_dir"
"$binary" --config "$run_dir/config.json" --log-dir "$log_dir" --headless

grep -q '"timeout_seconds": 30' "$project_dir/src/config.py"
grep -q 'settings\["timeout_seconds"\]' "$project_dir/src/config.py"
grep -q 'settings\["timeout_seconds"\]' "$project_dir/src/client.py"
if grep -q '"timeout"' "$project_dir/src/config.py" "$project_dir/src/client.py"; then
  echo "subagents demo: the old key remains" >&2
  exit 1
fi

/usr/bin/python3 - "$log_dir" <<'PY'
"""Check the whole episode tree: the parent's reservations and both children."""

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
        raise SystemExit(f"subagents demo: {message}")


parent = events(root)
start = data_of(parent, "episode/start")[0]
contract = start["contract"]
reserved = data_of(parent, "budget/reserve")
released = data_of(parent, "budget/release")
starts = data_of(parent, "spawn/start")
ends = data_of(parent, "spawn/end")
reports = [item for item in data_of(parent, "inbox/item") if item["source"] == "child"]

require(len(starts) == 2, f"the parent started {len(starts)} children rather than two")
require(all(s["context"] == "fresh" for s in starts), "a child was started with a context other than fresh")
require(all(s["contract"] == "survey" for s in starts), "a child ran a contract other than survey")
require(len(reserved) == 2, f"the parent recorded {len(reserved)} reservations rather than two")
declared = contract["child_contracts"]["survey"]["budget"]
for reserve in reserved:
    require(
        reserve["reserved"]["model_calls"] == declared["model_calls"],
        f"a reservation took {reserve['reserved']['model_calls']} calls rather than the {declared['model_calls']} "
        "the survey contract declares",
    )
require(len(ends) == 2 and len(released) == 2, "a child settled without a spawn/end and a budget/release")
waited = [e["seq"] for e in parent if e["type"] == "tool/result" and e["data"]["name"] == "wait"]
settled = [e["seq"] for e in parent if e["type"] in ("spawn/end", "budget/release")]
require(len(waited) == 1, f"the parent called wait {len(waited)} times rather than once")
require(max(settled) < waited[0], "wait returned before every child had a spawn/end and a budget/release")
require(
    all(end["outcome"]["kind"] == "completed" for end in ends),
    "a child ended with an outcome other than completed",
)
by_child = {reserve["child_id"]: reserve["reserved"] for reserve in reserved}
for release in released:
    spent = release["spent"]["model_calls"]
    taken = by_child[release["child_id"]]["model_calls"]
    require(spent < taken, f"child {release['child_id']} spent {spent} of {taken} calls and returned nothing")
require(len(reports) == 2 * 2, f"the parent received {len(reports)} messages from children rather than four")
require(
    sum(1 for item in reports if "survey of " in item["content"][0]["text"]) == 2,
    "a survey child sent no report of its own",
)

children = sorted((root / "children").iterdir())
require(len(children) == 2, f"{len(children)} child logs were written rather than two")
for directory in children:
    child = events(directory)
    child_start = data_of(child, "episode/start")[0]
    child_contract = child_start["contract"]
    require(child_start["parent_id"] == start["id"], f"{directory.name}: the child names another parent")
    require(child_contract["grants"].get("write", []) == [], f"{directory.name}: the child holds a write grant")
    require(child_contract["grants"].get("spawn", []) == [], f"{directory.name}: the child may spawn")
    require(contract["grants"]["write"] != [], "the parent holds no write grant, so the child is no narrower")
    require(
        child_contract["budget"]["model_calls"] < contract["budget"]["model_calls"],
        f"{directory.name}: the child's call budget is not below the parent's",
    )
    require(
        "notify" in child_contract["tools"] and "edit" not in child_contract["tools"],
        f"{directory.name}: the child's tools are not the read-only set",
    )
    outcome = data_of(child, "episode/end")[0]["outcome"]
    require(outcome["kind"] == "completed", f"{directory.name}: the child ended {outcome['kind']}")

print(f"The parent reserved {declared['model_calls']} calls per child and both children returned what they saved.")
PY

echo "Subagents demo passed. Inspect it with:"
if [ -n "${BUILD_WORKSPACE_DIRECTORY:-}" ]; then
  echo "  bazel run //:foe -- view $log_dir --serve"
else
  echo "  $binary view $log_dir --serve"
fi
