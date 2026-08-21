#!/usr/bin/env bash
# Counts Rust source lines in the budgeted crates, excluding tests and generated code.
# Budget: 6000. See docs/design.md "Size".
set -euo pipefail
cd "$(dirname "$0")/.."
total=0
for c in log core code view; do
  n=$(find "crates/$c/src" -name '*.rs' ! -path '*/tests/*' ! -name '*_test.rs' ! -name 'generated*' \
      -exec cat {} + | grep -cvE '^\s*$|^\s*//' || true)
  printf '%-6s %6d\n' "$c" "$n"; total=$((total + n))
done
printf '%-6s %6d  (budget 6000)\n' total "$total"
[ "$total" -le 6000 ]
