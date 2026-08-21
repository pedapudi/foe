#!/bin/sh
set -eu

launcher_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
launcher="$launcher_dir/$(basename -- "$0")"
if [ -n "${RUNFILES_DIR:-}" ] && [ -d "$RUNFILES_DIR/_main" ]; then
  repo_dir="$RUNFILES_DIR/_main"
  example_dir="$repo_dir/examples/budget-exhausted"
elif [ -d "$launcher.runfiles/_main" ]; then
  repo_dir="$launcher.runfiles/_main"
  example_dir="$repo_dir/examples/budget-exhausted"
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
  echo "budget-exhausted example: $binary is not executable; run 'cargo build --release --bin foe'" >&2
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
run_dir=$(mktemp -d "$output_dir/foe-budget-exhausted.XXXXXX")
project_dir="$run_dir/project"
log_dir="$run_dir/episode"
mkdir -p "$project_dir/src" "$project_dir/tools" "$project_dir/support"

# The project the configuration points at: more modules than the budget
# allows turns, so the survey the task asks for cannot finish.
for n in 1 2 3 4 5 6 7 8; do
  printf 'def module_%s() -> str:\n    return "module %s"\n' "$n" "$n" >"$project_dir/src/module_$n.py"
done

# The transport and the chunk helpers it imports are copied into the
# project, because an executable the episode starts may read the read roots
# and its own file and nothing else. `support` sits beside `tools` here as
# it does in the repository, so the import path is the same in both.
cp "$example_dir/never-finishing-transport" "$project_dir/tools/never-finishing-transport"
cp "$repo_dir/examples/support/chunks.py" "$project_dir/support/chunks.py"
chmod +x "$project_dir/tools/never-finishing-transport"

config="$run_dir/config.json"
python3 "$repo_dir/examples/support/materialize.py" \
  "$example_dir/config.json" "$config" \
  /home/user/project "$project_dir"

status=0
"$binary" --config "$config" --log-dir "$log_dir" --headless >"$run_dir/outcome.json" || status=$?
cat "$run_dir/outcome.json"

python3 - "$log_dir/episode.jsonl" "$status" <<'ASSERTIONS'
import json
import sys

events = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8")]
status = int(sys.argv[2])
of = lambda kind: [e["data"] for e in events if e["type"] == kind]


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"budget-exhausted example: {message}")


limit = of("episode/start")[0]["program"]["budget"]["model_calls"]
end = of("episode/end")[0]["outcome"]
check(events[-1]["type"] == "episode/end", "the log ends with episode/end")
check(end["kind"] == "exhausted", f"the outcome is exhausted, not {end['kind']}")
check(end["limit"] == "model_calls", f"the limit named is model_calls, not {end['limit']}")
check(status == 3, f"the exit code for an exhausted outcome is 3, not {status}")

requests = of("model/request")
check(len(requests) == limit, f"the episode made {limit} model requests, not {len(requests)}")
check([r["step"] for r in requests] == list(range(1, limit + 1)), "each request is a step of its own")
check(all(r["attempt"] == 1 for r in requests), "no request was retried, so no attempt is above 1")

messages = of("assistant/message")
check(len(messages) == limit, f"every request was answered; {len(messages)} answers for {limit} requests")
check(all(m["stop"] == "tool" for m in messages), "every answer ended in a tool call rather than a final turn")

results = of("tool/result")
paths = [r["value"]["path"] for r in results]
check(len(results) == limit, f"every tool call has a result; {len(results)} results for {limit} calls")
check(not any(r["is_error"] for r in results), "no tool call failed")
check(len(set(paths)) == len(paths), "the calls differ, so the loop detector is not what ended the episode")

print(f"budget-exhausted example: exhausted after {limit} model calls, {len(results)} files read")
ASSERTIONS

echo "view it with: $binary view $log_dir"
