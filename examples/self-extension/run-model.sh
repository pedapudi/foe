#!/bin/sh
set -eu

usage() {
  echo "usage: run-model.sh [FOE_BINARY] [--workflow] [--model PROVIDER/MODEL] [--attempts N] [--confirm-spend]"
}

launcher_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
launcher="$launcher_dir/$(basename -- "$0")"
if [ -n "${RUNFILES_DIR:-}" ] && [ -d "$RUNFILES_DIR/_main" ]; then
  repo_dir="$RUNFILES_DIR/_main"
  example_dir="$repo_dir/examples/self-extension"
elif [ -d "$launcher.runfiles/_main" ]; then
  repo_dir="$launcher.runfiles/_main"
  example_dir="$repo_dir/examples/self-extension"
else
  example_dir=$launcher_dir
  repo_dir=$(CDPATH= cd -- "$example_dir/../.." && pwd)
fi

binary=target/release/foe
if [ "$#" -gt 0 ] && [ "${1#--}" = "$1" ]; then
  binary=$1
  shift
fi
case "$binary" in
  /*) ;;
  *) binary="$repo_dir/$binary" ;;
esac

model_route=openai-codex/gpt-5.6-sol
confirmed=false
workflow=false
attempts=1
while [ "$#" -gt 0 ]; do
  case "$1" in
    --workflow)
      workflow=true
      shift
      ;;
    --model)
      [ "$#" -ge 2 ] || { echo "--model takes PROVIDER/MODEL" >&2; usage >&2; exit 2; }
      model_route=$2
      shift 2
      ;;
    --attempts)
      [ "$#" -ge 2 ] || { echo "--attempts takes a positive integer" >&2; usage >&2; exit 2; }
      case "$2" in
        ''|*[!0-9]*) echo "--attempts takes a positive integer, received $2" >&2; exit 2 ;;
      esac
      [ "$2" -gt 0 ] || { echo "--attempts takes a positive integer, received $2" >&2; exit 2; }
      attempts=$2
      shift 2
      ;;
    --confirm-spend)
      confirmed=true
      shift
      ;;
    --help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

case "$model_route" in
  */*)
    provider=${model_route%%/*}
    model=${model_route#*/}
    ;;
  *)
    echo "--model takes PROVIDER/MODEL, received $model_route" >&2
    exit 2
    ;;
esac
if [ -z "$provider" ] || [ -z "$model" ]; then
  echo "--model takes nonempty PROVIDER/MODEL, received $model_route" >&2
  exit 2
fi

config_template=$example_dir/config.json
run_prefix=foe-self-extension-model
description="model-backed self-extension"
target=//examples/self-extension:self-extension-model
model_call_limit=40
seconds_limit=900
if [ "$workflow" = true ]; then
  config_template=$example_dir/workflow-config.json
  run_prefix=foe-self-improvement-workflow-model
  description="model-backed self-improvement workflow"
  target=//examples/self-extension:self-improvement-workflow-model
fi
if [ "$confirmed" != true ]; then
  echo "The $description uses $model_route."
  echo "Each attempt permits up to $model_call_limit model calls and $seconds_limit seconds. Token use is measured without a hard allowance."
  if [ "$attempts" -gt 1 ]; then
    echo "$attempts attempts permit up to $((attempts * model_call_limit)) model calls."
  fi
  echo "No episode was started. Add --confirm-spend to run it."
  exit 2
fi

if [ ! -x "$binary" ]; then
  echo "$description: $binary is not executable" >&2
  echo "run 'bazel run $target -- --confirm-spend'" >&2
  exit 1
fi

if [ "$attempts" -gt 1 ]; then
  attempt=1
  passed=0
  while [ "$attempt" -le "$attempts" ]; do
    echo "$description attempt $attempt of $attempts"
    if [ "$workflow" = true ]; then
      if "$launcher" "$binary" --workflow --model "$model_route" --confirm-spend; then
        passed=$((passed + 1))
      fi
    elif "$launcher" "$binary" --model "$model_route" --confirm-spend; then
      passed=$((passed + 1))
    fi
    attempt=$((attempt + 1))
  done
  echo "$passed of $attempts fresh $description attempts passed."
  [ "$passed" -eq "$attempts" ]
  exit
fi

if [ -n "${TEST_TMPDIR:-}" ]; then
  output_dir=$TEST_TMPDIR
elif [ -n "${BUILD_WORKSPACE_DIRECTORY:-}" ]; then
  output_dir="$BUILD_WORKSPACE_DIRECTORY/target"
else
  output_dir="$repo_dir/target"
fi
mkdir -p "$output_dir"
run_dir=$(mktemp -d "$output_dir/$run_prefix.XXXXXX")
project_dir="$run_dir/foe"
log_dir="$run_dir/episode"
mkdir -p "$project_dir/crates/code/src" "$project_dir/docs"
cp "$repo_dir/crates/code/src/read.rs" "$project_dir/crates/code/src/read.rs"
cp "$repo_dir/crates/code/src/read_test.rs" "$project_dir/crates/code/src/read_test.rs"
cp "$repo_dir/docs/tools.md" "$project_dir/docs/tools.md"

initial_findings=$(CDPATH= cd -- "$project_dir" && "$example_dir/check")
if [ -z "$initial_findings" ]; then
  echo "$description: the source already passes its evaluator" >&2
  exit 1
fi

/usr/bin/python3 "$repo_dir/examples/support/materialize.py" \
  "$config_template" "$run_dir/config.json" \
  /home/user/project "$project_dir" \
  /home/user/foe "$repo_dir"

/usr/bin/python3 - "$run_dir/config.json" "$provider" "$model" "$model_call_limit" "$seconds_limit" <<'PY'
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
provider, model = sys.argv[2:4]
config = json.loads(path.read_text(encoding="utf-8"))
if provider == "exec":
    config["model"]["model"] = model
else:
    config["model"] = {"provider": provider, "model": model}
root_budget = config["budget"]
root_budget.pop("input_tokens", None)
root_budget.pop("output_tokens", None)
root_budget["model_calls"] = int(sys.argv[4])
root_budget["seconds"] = int(sys.argv[5]) + (300 if "workflow" in config else 0)
for node in config.get("workflow", {}).get("nodes", {}).values():
    if "model" in node:
        budget = node["model"]["budget"]
        budget.pop("input_tokens", None)
        budget.pop("output_tokens", None)
        budget["model_calls"] = int(sys.argv[4])
        budget["seconds"] = int(sys.argv[5])
path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
PY

echo "Running the $description with $model_route in $run_dir"
set +e
"$binary" --config "$run_dir/config.json" --log-dir "$log_dir" --headless
foe_status=$?
set -e

result=0
if [ "$foe_status" -ne 0 ]; then
  echo "$description: foe exited with status $foe_status" >&2
  result=$foe_status
fi

findings=$(CDPATH= cd -- "$project_dir" && "$example_dir/check")
if [ -n "$findings" ]; then
  echo "$findings" >&2
  result=1
fi

if [ ! -f "$log_dir/episode.jsonl" ]; then
  echo "$description: $log_dir/episode.jsonl is absent" >&2
  result=1
else
  if ! grep -Rq '"type":"tool/result".*"name":"read"' "$log_dir"; then
    echo "$description: the episode tree has no read tool result" >&2
    result=1
  fi
  if ! grep -Rq '"type":"tool/result".*"name":"edit"' "$log_dir"; then
    echo "$description: the episode tree has no edit tool result" >&2
    result=1
  fi
  if [ "$workflow" = true ]; then
    if ! grep -q '"type":"workflow/node-end".*"node":"evaluate_read_tool"' "$log_dir/episode.jsonl"; then
      echo "$description: the evaluator node did not run" >&2
      result=1
    fi
    if ! grep -q '"type":"workflow/node-end".*"node":"improve_read_tool"' "$log_dir/episode.jsonl"; then
      echo "$description: the terminal improvement node did not run" >&2
      result=1
    fi
    if ! grep -Rq '"type":"tool/result".*"name":"check"' "$log_dir"; then
      echo "$description: the terminal improvement node did not call its verifier" >&2
      result=1
    fi
    if [ "$model_route" = exec/self-improvement-retry-demo ] \
      && ! grep -Rq '"type":"inbox/item".*"source":"verify"' "$log_dir"; then
      echo "$description: the deterministic route did not exercise verifier feedback" >&2
      result=1
    fi
  fi
  if ! /usr/bin/python3 "$repo_dir/evals/trace_quality.py" --pretty "$log_dir"; then
    result=1
  fi
fi

echo "Self-extension artifacts: $run_dir"
echo "Candidate source: $project_dir"
echo "Episode log: $log_dir"
if [ -n "${BUILD_WORKSPACE_DIRECTORY:-}" ]; then
  echo "Viewer: bazel run //:foe -- view $log_dir --serve"
else
  echo "Viewer: $binary view $log_dir --serve"
fi
exit "$result"
