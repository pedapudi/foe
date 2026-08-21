#!/bin/sh
set -eu

launcher_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
launcher="$launcher_dir/$(basename -- "$0")"
if [ -n "${RUNFILES_DIR:-}" ] && [ -d "$RUNFILES_DIR/_main" ]; then
  repo_dir="$RUNFILES_DIR/_main"
  example_dir="$repo_dir/examples/sandbox"
elif [ -d "$launcher.runfiles/_main" ]; then
  repo_dir="$launcher.runfiles/_main"
  example_dir="$repo_dir/examples/sandbox"
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
  echo "sandbox demo: $binary is not executable; run 'bazel run //examples/sandbox'" >&2
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
run_dir=$(mktemp -d "$output_dir/foe-sandbox-demo.XXXXXX")
project_dir="$run_dir/project"
denied_dir="$run_dir/outside-grant"
log_dir="$run_dir/episode"
mkdir -p "$project_dir" "$denied_dir"
printf 'visible through the read grant\n' > "$project_dir/allowed.txt"
printf 'hidden outside the read grant\n' > "$denied_dir/denied.txt"

/usr/bin/python3 "$repo_dir/examples/support/materialize.py" \
  "$example_dir/config.json" "$run_dir/config.json" \
  /home/user/project "$project_dir" \
  /home/user/outside-grant "$denied_dir" \
  /home/user/foe "$repo_dir"

echo "Running the sandbox demo in $run_dir"
"$binary" --config "$run_dir/config.json" --log-dir "$log_dir" --headless

grep -q "visible through the read grant" "$log_dir/episode.jsonl"
grep -q "Permission denied" "$log_dir/episode.jsonl"
grep -q '"landlock_abi":[1-9]' "$log_dir/episode.jsonl"

echo "Sandbox demo passed. Inspect it with:"
if [ -n "${BUILD_WORKSPACE_DIRECTORY:-}" ]; then
  echo "  bazel run //:foe -- view $log_dir --serve"
else
  echo "  $binary view $log_dir --serve"
fi
