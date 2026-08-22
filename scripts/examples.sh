#!/usr/bin/env bash
# Runs every example against one built binary and reports how long each took.
#
# This is the slow tier of the test suite. `cargo test --workspace` starts no
# process that waits on a real clock; the examples do, because they show an
# operator what a real run looks like. The recovery-exhausted example waits
# the whole retry backoff, which is most of this script's running time and is
# the behaviour it exists to demonstrate.
#
# The argument is the binary to run, relative to the repository root or
# absolute; it defaults to the debug build, which is what a local `cargo
# build` leaves behind.
set -euo pipefail
cd "$(dirname "$0")/.."
binary=${1:-target/debug/foe}
case "$binary" in /*) ;; *) binary="$PWD/$binary" ;; esac
if [ ! -x "$binary" ]; then
  echo "examples: $binary is not executable; build it first" >&2
  exit 1
fi

failed=()
for dir in examples/*/; do
  name=$(basename "$dir")
  if [ -f "$dir/run.sh" ]; then
    runner=(sh "$dir/run.sh" "$binary")
  elif [ -f "$dir/run.py" ]; then
    runner=(python3 "$dir/run.py" "$binary")
  else
    continue
  fi
  start=$SECONDS
  if output=$("${runner[@]}" 2>&1); then
    printf '%-28s ok   %3ds\n' "$name" "$((SECONDS - start))"
  else
    printf '%-28s FAIL %3ds\n' "$name" "$((SECONDS - start))"
    printf '%s\n' "$output"
    failed+=("$name")
  fi
done

if [ ${#failed[@]} -gt 0 ]; then
  echo "examples that did not succeed: ${failed[*]}" >&2
  exit 1
fi
