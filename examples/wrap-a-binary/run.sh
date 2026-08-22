#!/bin/sh
set -eu

launcher_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
launcher="$launcher_dir/$(basename -- "$0")"
if [ -n "${RUNFILES_DIR:-}" ] && [ -d "$RUNFILES_DIR/_main" ]; then
  repo_dir="$RUNFILES_DIR/_main"
  example_dir="$repo_dir/examples/wrap-a-binary"
elif [ -d "$launcher.runfiles/_main" ]; then
  repo_dir="$launcher.runfiles/_main"
  example_dir="$repo_dir/examples/wrap-a-binary"
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
  echo "wrap-a-binary demo: $binary is not executable; run 'bazel run //examples/wrap-a-binary'" >&2
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
run_dir=$(mktemp -d "$output_dir/foe-wrap-a-binary-demo.XXXXXX")
project_dir="$run_dir/project"
log_dir="$run_dir/episode"
mkdir -p "$project_dir/src" "$project_dir/tools" "$project_dir/support"

cat > "$project_dir/src/report.py" <<'EOF'
"""Renders a summary of a run as JSON."""

import json
import math


def summarize(rows):
    """Returns the row count, the total, the mean, and the widest name."""
    total = sum(row["count"] for row in rows)
    return json.dumps({"rows": len(rows), "total": total, "mean": total / len(rows) if rows else 0.0, "widest": max((len(row["name"]) for row in rows), default=0)})
EOF

# The transport reads chunks.py, so both files sit under the episode's read
# root; a file outside every read root is unreadable to the transport process.
cp "$example_dir/style-check" "$project_dir/tools/style-check"
cp "$example_dir/transport.py" "$project_dir/tools/transport.py"
cp "$repo_dir/examples/support/chunks.py" "$project_dir/support/chunks.py"
chmod +x "$project_dir/tools/style-check" "$project_dir/tools/transport.py"

before=$(cd "$project_dir" && ./tools/style-check)
echo "$before" | grep -q "src/report.py:4:1: unused-import math"
echo "$before" | grep -q "src/report.py:10:89: line-too-long"
if [ "$(echo "$before" | wc -l)" -ne 2 ]; then
  echo "wrap-a-binary demo: the checker reports $(echo "$before" | wc -l) findings rather than two" >&2
  exit 1
fi

/usr/bin/python3 "$repo_dir/examples/support/materialize.py" \
  "$example_dir/config.json" "$run_dir/config.json" \
  /home/user/project "$project_dir"

echo "Running the wrap-a-binary demo in $run_dir"
"$binary" --config "$run_dir/config.json" --log-dir "$log_dir" --headless

after=$(cd "$project_dir" && ./tools/style-check)
if [ -n "$after" ]; then
  echo "wrap-a-binary demo: the checker still reports findings: $after" >&2
  exit 1
fi

/usr/bin/python3 - "$log_dir/episode.jsonl" <<'EOF'
import json
import sys

events = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8")]


def fail(message):
    raise SystemExit(f"wrap-a-binary demo: {message}")


def only(kind):
    return [event for event in events if event["type"] == kind]


header = only("request/header")[0]["data"]
if "style" not in {tool["name"] for tool in header["tools"]}:
    fail("the request/header does not carry the schema of the configured executable")
if "Run style after every edit." not in header["system"]:
    fail("the tool_defs instruction is absent from the system prompt")
if header["system"].index("Run style after every edit.") < header["system"].index("You are a coding agent"):
    fail("the tool_defs instruction precedes the instruction sections")

checks = [event for event in only("tool/result") if event["data"]["name"] == "style"]
edits = [event for event in only("tool/result") if event["data"]["name"] == "edit"]
if len(checks) != 2 or len(edits) != 2:
    fail(f"{len(checks)} checker calls and {len(edits)} edits; the transport makes two of each")
if checks[0]["data"]["value"]["exit_code"] != 0:
    fail("the checker exited non-zero while reporting findings, which a verifier may not do")
if "unused-import" not in checks[0]["data"]["value"]["stdout"]:
    fail("the first checker call reported no unused import")
if checks[1]["seq"] < edits[1]["seq"]:
    fail("the model ran the checker before its last edit rather than after it")
if checks[1]["data"]["value"]["stdout"] != "":
    fail(f"the checker after the last edit printed {checks[1]['data']['value']['stdout']!r}")

finished = [event for event in only("assistant/message") if event["data"]["stop"] == "end"]
if len(finished) != 1:
    fail(f"the model finished {len(finished)} times rather than once")
verifications = [event for event in only("inbox/item") if event["data"]["source"] == "verify"]
if len(verifications) != 2:
    fail(f"{len(verifications)} verify inbox items rather than two")
first_findings = verifications[0]["data"]["content"][0]["text"]
if "unused-import" not in first_findings or "line-too-long" not in first_findings:
    fail(f"the first fed-back findings are {first_findings!r}")
second_findings = verifications[1]["data"]["content"][0]["text"]
if "unused-import" in second_findings or "line-too-long" not in second_findings:
    fail(f"the second fed-back findings are {second_findings!r}")
if not checks[0]["seq"] < verifications[0]["seq"] < edits[0]["seq"]:
    fail("the first verify item does not follow the model's checker call")
if not edits[0]["seq"] < finished[0]["seq"] < verifications[1]["seq"] < edits[1]["seq"]:
    fail("the second verify item does not follow the model's finish")
if checks[1]["seq"] < edits[1]["seq"]:
    fail("the successful checker call precedes the last edit")

end = events[-1]
if end["type"] != "episode/end":
    fail(f"the last event is {end['type']}")
if end["data"]["outcome"]["kind"] != "completed":
    fail(f"the outcome is {end['data']['outcome']}")
EOF

echo "Wrap-a-binary demo passed. Inspect it with:"
if [ -n "${BUILD_WORKSPACE_DIRECTORY:-}" ]; then
  echo "  bazel run //:foe -- view $log_dir --serve"
else
  echo "  $binary view $log_dir --serve"
fi
