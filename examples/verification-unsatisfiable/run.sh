#!/bin/sh
set -eu

launcher_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
launcher="$launcher_dir/$(basename -- "$0")"
if [ -n "${RUNFILES_DIR:-}" ] && [ -d "$RUNFILES_DIR/_main" ]; then
  repo_dir="$RUNFILES_DIR/_main"
  example_dir="$repo_dir/examples/verification-unsatisfiable"
elif [ -d "$launcher.runfiles/_main" ]; then
  repo_dir="$launcher.runfiles/_main"
  example_dir="$repo_dir/examples/verification-unsatisfiable"
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
  echo "verification-unsatisfiable example: $binary is not executable; run 'cargo build --release --bin foe'" >&2
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
run_dir=$(mktemp -d "$output_dir/foe-verification-unsatisfiable.XXXXXX")
project_dir="$run_dir/project"
log_parent="$run_dir/episode"
mkdir -p "$project_dir/src" "$project_dir/tools"

# The project the configuration points at: one module with the TODO comment
# the task names, which the episode may edit and never does.
cat >"$project_dir/src/calculator.py" <<'MODULE'
def add(left: int, right: int) -> int:
    # TODO: Implement add.
    raise NotImplementedError
MODULE

# The verifier carries the path it checks, so it is materialized like the
# configuration.
python3 "$repo_dir/examples/support/materialize.py" \
  "$example_dir/todo-check" "$project_dir/tools/todo-check" \
  /home/user/project "$project_dir"
chmod +x "$project_dir/tools/todo-check"

config="$run_dir/config.json"
python3 "$repo_dir/examples/support/materialize.py" \
  "$example_dir/config.json" "$config" \
  /home/user/project "$project_dir"

status=0
/usr/bin/python3 "$repo_dir/examples/support/run_with_host.py" \
  "$binary" "$config" "$log_parent" "$example_dir/responses.py" >"$run_dir/outcome.json" 2>"$run_dir/foe.err" || status=$?
cat "$run_dir/foe.err" >&2
# The run creates its own directory for the episode under the one named
# and prints it on standard error, which docs/design.md "The command line"
# fixes.
log_dir=$(sed -n 's/^foe: log //p' "$run_dir/foe.err" | head -n 1)
cat "$run_dir/outcome.json"

python3 - "$log_dir/episode.jsonl" "$status" "$project_dir/src/calculator.py" <<'ASSERTIONS'
import json
import sys

events = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8")]
status = int(sys.argv[2])
module = open(sys.argv[3], encoding="utf-8").read()
of = lambda kind: [e["data"] for e in events if e["type"] == kind]


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"verification-unsatisfiable example: {message}")


retries = of("episode/start")[0]["contract"]["done_when"]["retries"]
end = of("episode/end")[0]["outcome"]
check(events[-1]["type"] == "episode/end", "the log ends with episode/end")
check(end["kind"] == "blocked", f"the outcome is blocked, not {end['kind']}")
check(end["code"] == "verification-unsatisfiable", f"the code is verification-unsatisfiable, not {end['code']}")
check(end["message"] == f"`todo-check` still reports 1 finding(s) after {retries} retries", f"{end['message']!r}")
check(status == 2, f"the exit code for a blocked outcome is 2, not {status}")

messages = of("assistant/message")
check(len(messages) == retries + 1, f"the verifier judged {retries + 1} candidates, one per answer")
check(all(m["stop"] == "end" for m in messages), "every answer finished its turn, which is what makes it a candidate")
check(all(not m["tool_calls"] for m in messages), "no answer called a tool")
check(len({m["text"] for m in messages}) == len(messages), "the answers differ, so the loop detector did not end this")

findings = [i for i in of("inbox/item") if i["source"] == "verify"]
check(len(findings) == retries, f"findings were fed back {retries} times, once per retry")
check(all("TODO: Implement add." in i["content"][0]["text"] for i in findings), "each item carries the finding")
check(all("todo-check" in i["content"][0]["text"] for i in findings), "each item names the verifier that reported")

check(of("tool/result") == [], "the verifier is not a tool call and writes no tool/result")
check("TODO: Implement add." in module, "the module still holds the TODO the answers claimed to have removed")

print(f"verification-unsatisfiable example: blocked after {retries} retries with the finding still present")
ASSERTIONS

echo "view it with: $binary view $log_dir"
