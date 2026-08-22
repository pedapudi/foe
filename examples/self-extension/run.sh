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

binary=${1:-"target/release/foe"}
case "$binary" in
  /*) ;;
  *) binary="$repo_dir/$binary" ;;
esac

if [ ! -x "$binary" ]; then
  echo "self-extension demo: $binary is not executable; run 'bazel run //examples/self-extension'" >&2
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
run_dir=$(mktemp -d "$output_dir/foe-self-extension-demo.XXXXXX")
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

echo "Running the self-extension demo in $run_dir"
"$binary" --config "$run_dir/config.json" --log-dir "$log_dir" --headless

findings=$(CDPATH= cd -- "$project_dir" && "$example_dir/check")
if [ -n "$findings" ]; then
  echo "$findings" >&2
  exit 1
fi
grep -q '"name":"read"' "$log_dir/episode.jsonl"
grep -q '"name":"edit"' "$log_dir/episode.jsonl"

echo "Self-extension demo passed. Inspect the extended source under $project_dir."
echo "Inspect the episode with:"
if [ -n "${BUILD_WORKSPACE_DIRECTORY:-}" ]; then
  echo "  bazel run //:foe -- view $log_dir --serve"
else
  echo "  $binary view $log_dir --serve"
fi
