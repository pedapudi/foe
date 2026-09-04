#!/bin/sh
set -eu

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

workflow=false
while [ "$#" -gt 0 ]; do
  case "$1" in
    --workflow) workflow=true ;;
    *) echo "self-extension demo: unknown option $1" >&2; exit 2 ;;
  esac
  shift
done

config_template=$example_dir/config.json
run_prefix=foe-self-extension-demo
description="self-extension demo"
response=self_extension
if [ "$workflow" = true ]; then
  config_template=$example_dir/workflow-config.json
  run_prefix=foe-self-improvement-workflow-demo
  description="self-improvement workflow demo"
  response=self_improvement_retry
fi

if [ ! -x "$binary" ]; then
  echo "$description: $binary is not executable; run the matching Bazel target" >&2
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

echo "Running the $description in $run_dir"
/usr/bin/python3 "$repo_dir/examples/support/run_with_host.py" \
  "$binary" "$run_dir/config.json" "$log_dir" "$repo_dir/examples/support/responses.py" "$response"

findings=$(CDPATH= cd -- "$project_dir" && "$example_dir/check")
if [ -n "$findings" ]; then
  echo "$findings" >&2
  exit 1
fi
grep -Rq '"type":"tool/result".*"name":"read"' "$log_dir"
grep -Rq '"type":"tool/result".*"name":"edit"' "$log_dir"
if [ "$workflow" = true ]; then
  grep -q '"type":"workflow/node-end".*"node":"evaluate_read_tool"' "$log_dir/episode.jsonl"
  grep -q '"type":"workflow/node-end".*"node":"improve_read_tool"' "$log_dir/episode.jsonl"
  grep -Rq '"type":"tool/result".*"name":"check"' "$log_dir"
  grep -Rq '"type":"inbox/item".*"source":"verify"' "$log_dir"
fi
/usr/bin/python3 "$repo_dir/evals/trace_quality.py" --pretty "$log_dir"

echo "The $description passed. Inspect the extended source under $project_dir."
echo "Inspect the episode with:"
if [ -n "${BUILD_WORKSPACE_DIRECTORY:-}" ]; then
  echo "  bazel run //:foe -- view $log_dir --serve"
else
  echo "  $binary view $log_dir --serve"
fi
