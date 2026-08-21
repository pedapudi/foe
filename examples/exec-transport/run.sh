#!/bin/sh
set -eu

launcher_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
launcher="$launcher_dir/$(basename -- "$0")"
if [ -n "${RUNFILES_DIR:-}" ] && [ -d "$RUNFILES_DIR/_main" ]; then
  repo_dir="$RUNFILES_DIR/_main"
  example_dir="$repo_dir/examples/exec-transport"
elif [ -d "$launcher.runfiles/_main" ]; then
  repo_dir="$launcher.runfiles/_main"
  example_dir="$repo_dir/examples/exec-transport"
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
  echo "exec transport demo: $binary is not executable; run 'bazel run //examples/exec-transport'" >&2
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
run_dir=$(mktemp -d "$output_dir/foe-exec-transport-demo.XXXXXX")
project_dir="$run_dir/project"
log_dir="$run_dir/episode"
mkdir -p "$project_dir/tools"

cat > "$project_dir/README.md" <<'EOF'
# Calculator

A small Python package with one module. Build it with `python -m build`.
EOF

# The transport program runs under the episode's sandbox, which grants it
# the read roots and its own file. Both files it opens must therefore lie
# under a read root, so the runner copies them into the project.
cp "$example_dir/scripted-transport.py" "$project_dir/tools/scripted-transport.py"
cp "$repo_dir/examples/support/chunks.py" "$project_dir/tools/chunks.py"
chmod +x "$project_dir/tools/scripted-transport.py"

/usr/bin/python3 "$repo_dir/examples/support/materialize.py" \
  "$example_dir/config.json" "$run_dir/config.json" \
  /home/user/project "$project_dir"

echo "Running the exec transport demo in $run_dir"
"$binary" --config "$run_dir/config.json" --log-dir "$log_dir" --headless

grep -q '"model":{"provider":"exec","model":"exec-transport-demo"}' "$log_dir/episode.jsonl"
grep -q 'A small Python package with one module' "$log_dir/episode.jsonl"
grep -q '"kind":"completed","value":"The README states what the project does and how to build it."' \
  "$log_dir/episode.jsonl"

echo "Exec transport demo passed. Inspect it with:"
if [ -n "${BUILD_WORKSPACE_DIRECTORY:-}" ]; then
  echo "  bazel run //:foe -- view $log_dir --serve"
else
  echo "  $binary view $log_dir --serve"
fi
