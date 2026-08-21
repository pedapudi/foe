#!/usr/bin/env bash
# Counts Rust source lines in the budgeted crates, excluding tests and generated code.
# Two budgets: 6000 over log, core, code, and view together; 1000 over workflow.
# See docs/design.md "Size".
set -euo pipefail
cd "$(dirname "$0")/.."
count() {
  find "crates/$1/src" -name '*.rs' ! -path '*/tests/*' ! -name '*_test.rs' ! -name 'generated*' \
      -exec cat {} + | grep -cvE '^\s*$|^\s*//' || true
}
total=0
for c in log core code view; do
  n=$(count "$c")
  printf '%-8s %6d\n' "$c" "$n"; total=$((total + n))
done
printf '%-8s %6d  (budget 6000)\n' total "$total"
workflow=$(count workflow)
printf '%-8s %6d  (budget 1000)\n' workflow "$workflow"
[ "$total" -le 6000 ] && [ "$workflow" -le 1000 ]
