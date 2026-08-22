#!/bin/sh
set -eu

usage() {
  echo "usage: run-model.sh [FOE_BINARY] [--model PROVIDER/MODEL] [--confirm-spend]"
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
while [ "$#" -gt 0 ]; do
  case "$1" in
    --model)
      [ "$#" -ge 2 ] || { echo "--model takes PROVIDER/MODEL" >&2; usage >&2; exit 2; }
      model_route=$2
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

input_token_limit=20000
output_token_limit=4000
model_call_limit=8
if [ "$confirmed" != true ]; then
  echo "The self-extension run uses $model_route."
  echo "Its declared limits are $input_token_limit input tokens and $output_token_limit output tokens across $model_call_limit model calls."
  echo "No episode was started. Add --confirm-spend to run it."
  exit 2
fi

if [ ! -x "$binary" ]; then
  echo "self-extension model run: $binary is not executable" >&2
  echo "run 'bazel run //examples/self-extension:self-extension-model -- --confirm-spend'" >&2
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
run_dir=$(mktemp -d "$output_dir/foe-self-extension-model.XXXXXX")
project_dir="$run_dir/foe"
log_dir="$run_dir/episode"
mkdir -p "$project_dir/crates/code/src" "$project_dir/docs"
cp "$repo_dir/crates/code/src/read.rs" "$project_dir/crates/code/src/read.rs"
cp "$repo_dir/crates/code/src/read_test.rs" "$project_dir/crates/code/src/read_test.rs"
cp "$repo_dir/docs/tools.md" "$project_dir/docs/tools.md"

/usr/bin/python3 "$repo_dir/examples/support/materialize.py" \
  "$example_dir/config.json" "$run_dir/config.json" \
  /home/user/project "$project_dir" \
  /home/user/foe "$repo_dir"

/usr/bin/python3 - "$run_dir/config.json" "$provider" "$model" "$input_token_limit" "$output_token_limit" "$model_call_limit" <<'PY'
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
config["budget"]["input_tokens"] = int(sys.argv[4])
config["budget"]["output_tokens"] = int(sys.argv[5])
config["budget"]["model_calls"] = int(sys.argv[6])
path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
PY

echo "Running model-backed self-extension with $model_route in $run_dir"
set +e
"$binary" --config "$run_dir/config.json" --log-dir "$log_dir" --headless
foe_status=$?
set -e

result=0
if [ "$foe_status" -ne 0 ]; then
  echo "self-extension model run: foe exited with status $foe_status" >&2
  result=$foe_status
fi

findings=$(CDPATH= cd -- "$project_dir" && "$example_dir/check")
if [ -n "$findings" ]; then
  echo "$findings" >&2
  result=1
fi

if [ ! -f "$log_dir/episode.jsonl" ]; then
  echo "self-extension model run: $log_dir/episode.jsonl is absent" >&2
  result=1
else
  if ! grep -q '"name":"read"' "$log_dir/episode.jsonl"; then
    echo "self-extension model run: the episode has no read tool result" >&2
    result=1
  fi
  if ! grep -q '"name":"edit"' "$log_dir/episode.jsonl"; then
    echo "self-extension model run: the episode has no edit tool result" >&2
    result=1
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
