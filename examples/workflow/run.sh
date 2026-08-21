#!/bin/sh
set -eu

launcher_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
launcher="$launcher_dir/$(basename -- "$0")"
if [ -n "${RUNFILES_DIR:-}" ] && [ -d "$RUNFILES_DIR/_main" ]; then
  repo_dir="$RUNFILES_DIR/_main"
  example_dir="$repo_dir/examples/workflow"
elif [ -d "$launcher.runfiles/_main" ]; then
  repo_dir="$launcher.runfiles/_main"
  example_dir="$repo_dir/examples/workflow"
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
  echo "workflow demo: $binary is not executable; run 'bazel run //examples/workflow'" >&2
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
run_dir=$(mktemp -d "$output_dir/foe-workflow-demo.XXXXXX")
project_dir="$run_dir/project"
log_dir="$run_dir/episode"
mkdir -p "$project_dir/src"

cat > "$project_dir/src/calculator.py" <<'EOF'
def add(left: int, right: int) -> int:
    # TODO: Implement add.
    raise NotImplementedError
EOF

/usr/bin/python3 "$repo_dir/examples/support/materialize.py" \
  "$example_dir/config.json" "$run_dir/config.json" \
  /home/user/project "$project_dir" \
  /home/user/foe "$repo_dir"

echo "Running the workflow demo in $run_dir"
"$binary" --config "$run_dir/config.json" --log-dir "$log_dir" --headless

grep -q "return left + right" "$project_dir/src/calculator.py"
if grep -q "TODO" "$project_dir/src/calculator.py"; then
  echo "workflow demo: the TODO comment remains" >&2
  exit 1
fi
grep -q '"type":"workflow/branch"' "$log_dir/episode.jsonl"
grep -q '"type":"workflow/node-end"' "$log_dir/episode.jsonl"

echo "Workflow demo passed. Inspect it with:"
if [ -n "${BUILD_WORKSPACE_DIRECTORY:-}" ]; then
  echo "  bazel run //:foe -- view $log_dir --serve"
else
  echo "  $binary view $log_dir --serve"
fi
