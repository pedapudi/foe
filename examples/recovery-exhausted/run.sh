#!/bin/sh
set -eu

launcher_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
launcher="$launcher_dir/$(basename -- "$0")"
if [ -n "${RUNFILES_DIR:-}" ] && [ -d "$RUNFILES_DIR/_main" ]; then
  repo_dir="$RUNFILES_DIR/_main"
  example_dir="$repo_dir/examples/recovery-exhausted"
elif [ -d "$launcher.runfiles/_main" ]; then
  repo_dir="$launcher.runfiles/_main"
  example_dir="$repo_dir/examples/recovery-exhausted"
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
  echo "recovery-exhausted example: $binary is not executable; run 'cargo build --release --bin foe'" >&2
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
run_dir=$(mktemp -d "$output_dir/foe-recovery-exhausted.XXXXXX")
project_dir="$run_dir/project"
log_dir="$run_dir/episode"
mkdir -p "$project_dir/src" "$project_dir/tools"

# The project the configuration points at. The episode never reads it,
# because no request of its own is ever answered.
cat >"$project_dir/src/calculator.py" <<'MODULE'
def add(left: int, right: int) -> int:
    return left + right
MODULE

# The transport and the chunk helpers it imports lie under the read root,
# where the episode's sandbox lets the interpreter open them.
cp "$example_dir/unreachable-provider-transport" "$project_dir/tools/unreachable-provider-transport"
cp "$repo_dir/examples/support/chunks.py" "$project_dir/tools/chunks.py"
chmod +x "$project_dir/tools/unreachable-provider-transport"

config="$run_dir/config.json"
python3 "$repo_dir/examples/support/materialize.py" \
  "$example_dir/config.json" "$config" \
  /home/user/project "$project_dir"

echo "recovery-exhausted example: five attempts with a growing delay take about 15 seconds"
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
        raise SystemExit(f"recovery-exhausted example: {message}")


end = of("episode/end")[0]["outcome"]
check(events[-1]["type"] == "episode/end", "the log ends with episode/end")
check(end["kind"] == "blocked", f"the outcome is blocked, not {end['kind']}")
check(end["code"] == "recovery-exhausted", f"the code is recovery-exhausted, not {end['code']}")
check(end["message"] == "5 attempts at step 1 failed", f"the message counts the attempts: {end['message']!r}")
check(status == 2, f"the exit code for a blocked outcome is 2, not {status}")

requests = of("model/request")
check(len(requests) == 5, f"the runtime made 5 attempts, not {len(requests)}")
check(all(r["step"] == 1 for r in requests), "every attempt belongs to step 1, which never produced an answer")
check([r["attempt"] for r in requests] == [1, 2, 3, 4, 5], "the attempts are numbered 1 through 5")
check(all(r["messages"] == requests[0]["messages"] for r in requests), "every attempt carried the same messages")
check([r["consumed"] for r in requests] == [[1], [], [], [], []], "the task was consumed once, by the first attempt")

chunks = [e["data"]["chunk"] for e in events if e["type"] == "assistant/chunk"]
check(len(chunks) == 5, f"one error chunk per attempt; there are {len(chunks)}")
check(all(c["kind"] == "error" and c["retryable"] for c in chunks), "every chunk is a retryable error")

retries = of("request/retry")
check(len(retries) == 5, f"one request/retry per failed attempt; there are {len(retries)}")
check([r["delay_ms"] for r in retries] == [500, 1000, 2000, 4000, 8000], "the delay doubles up to the 8 second cap")
check(all(r["cause"] == "provider" for r in retries), "the cause recorded is provider, which the program reported")

check(of("assistant/message") == [], "no answer was assembled, so no assistant/message was written")
check(of("tool/result") == [], "no tool ran")
check(len(of("inbox/item")) == 1, "the task is the only inbox item")

print(f"recovery-exhausted example: blocked after {len(requests)} attempts at step 1")
ASSERTIONS

echo "view it with: $binary view $log_dir"
