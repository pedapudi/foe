#!/bin/sh
set -eu

launcher_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
launcher="$launcher_dir/$(basename -- "$0")"
if [ -n "${RUNFILES_DIR:-}" ] && [ -d "$RUNFILES_DIR/_main" ]; then
  repo_dir="$RUNFILES_DIR/_main"
  example_dir="$repo_dir/examples/minimal"
elif [ -d "$launcher.runfiles/_main" ]; then
  repo_dir="$launcher.runfiles/_main"
  example_dir="$repo_dir/examples/minimal"
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
  echo "minimal demo: $binary is not executable; run 'bazel run //examples/minimal'" >&2
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
run_dir=$(mktemp -d "$output_dir/foe-minimal-demo.XXXXXX")
project_dir="$run_dir/project"
log_dir="$run_dir/episode"
mkdir -p "$project_dir/tools" "$project_dir/support"

cat > "$project_dir/brackets.py" <<'EOF'
"""Square-bracket nesting."""


def bracket_depth(text: str) -> int:
    """The greatest depth of square-bracket nesting reached anywhere in `text`."""
    depth = 0
    for character in text:
        if character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
    return depth
EOF

cat > "$project_dir/test_brackets.py" <<'EOF'
import unittest

from brackets import bracket_depth


class BracketDepth(unittest.TestCase):
    def test_no_brackets(self):
        self.assertEqual(bracket_depth("plain text"), 0)

    def test_unclosed_bracket(self):
        self.assertEqual(bracket_depth("[a"), 1)

    def test_nested_brackets(self):
        self.assertEqual(bracket_depth("[[a]]"), 2)


if __name__ == "__main__":
    unittest.main()
EOF

# The transport reads chunks.py, so both files sit under the episode's read
# root; a file outside every read root is unreadable to the transport process.
cp "$example_dir/transport.py" "$project_dir/tools/transport.py"
cp "$repo_dir/examples/support/chunks.py" "$project_dir/support/chunks.py"
chmod +x "$project_dir/tools/transport.py"

if (cd "$project_dir" && /usr/bin/python3 -m unittest test_brackets >/dev/null 2>&1); then
  echo "minimal demo: test_brackets passes before the episode, so the episode proves nothing" >&2
  exit 1
fi

/usr/bin/python3 "$repo_dir/examples/support/materialize.py" \
  "$example_dir/config.json" "$run_dir/config.json" \
  /home/user/project "$project_dir"

echo "Running the minimal demo in $run_dir"
"$binary" --config "$run_dir/config.json" --log-dir "$log_dir" --headless

(cd "$project_dir" && /usr/bin/python3 -m unittest test_brackets >/dev/null 2>&1)

/usr/bin/python3 - "$log_dir/episode.jsonl" <<'EOF'
import json
import sys

events = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8")]


def fail(message):
    raise SystemExit(f"minimal demo: {message}")


def only(kind):
    return [event for event in events if event["type"] == kind]


for index, event in enumerate(events):
    if event["seq"] != index:
        fail(f"seq {event['seq']} appears at position {index}")

if events[0]["type"] != "episode/start" or events[0]["seq"] != 0:
    fail(f"seq 0 is {events[0]['type']} rather than episode/start")
if events[1]["type"] != "inbox/item" or events[1]["data"]["source"] != "task":
    fail("seq 1 is not the inbox item with source task")

headers = only("request/header")
if len(headers) != 1:
    fail(f"{len(headers)} request/header events; the prompt and the tool schemas do not change")
if headers[0]["data"]["reason"] != "initial":
    fail(f"the only request/header has reason {headers[0]['data']['reason']}")
if headers[0]["data"]["model"]["model"] != "minimal-demo":
    fail("the request/header does not name the model the configuration set")

requests = only("model/request")
if headers[0]["seq"] > requests[0]["seq"]:
    fail("the request/header follows the first model/request")
if any(request["data"]["header_seq"] != headers[0]["seq"] for request in requests):
    fail("a model/request points at a header other than the only one written")
if len(requests) != 6:
    fail(f"{len(requests)} model calls; the transport answers six")
if len(requests) > 20:
    fail(f"{len(requests)} model calls exceeds the budget of 20")

calls = [call for message in only("assistant/message") for call in message["data"]["tool_calls"]]
results = {result["data"]["call_id"]: result["data"] for result in only("tool/result")}
if len(results) != len(calls):
    fail(f"{len(calls)} tool calls and {len(results)} results")
for call in calls:
    if call["id"] not in results:
        fail(f"tool call {call['id']} has no result")
    if results[call["id"]]["name"] != call["name"]:
        fail(f"the result of {call['id']} names another tool")
if [call["name"] for call in calls] != ["grep", "bash", "read", "edit", "bash"]:
    fail(f"the tools called were {[call['name'] for call in calls]}")

first_run, last_run = [result for result in results.values() if result["name"] == "bash"]
if first_run["value"]["exit_code"] == 0:
    fail("the first test run passed, so the edit repaired nothing")
if last_run["value"]["exit_code"] != 0:
    fail("the test run after the edit still failed")

end = events[-1]
if end["type"] != "episode/end":
    fail(f"the last event is {end['type']}")
if end["data"]["outcome"]["kind"] != "completed":
    fail(f"the outcome is {end['data']['outcome']}")
if "greatest depth" not in end["data"]["outcome"]["value"]:
    fail("the outcome value is not the text of the final turn")
EOF

grep -q "return greatest" "$project_dir/brackets.py"

echo "Minimal demo passed. Inspect it with:"
if [ -n "${BUILD_WORKSPACE_DIRECTORY:-}" ]; then
  echo "  bazel run //:foe -- view $log_dir --serve"
else
  echo "  $binary view $log_dir --serve"
fi
