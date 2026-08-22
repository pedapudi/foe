#!/usr/bin/env bash
# Counts Rust source lines in the budgeted crates, excluding tests and generated code.
# Five budgets: 6000 over the runtime, which is log, core, and code together;
# 1000 over workflow; 500 over context; 600 over view; 1300 over cli. The
# viewer is budgeted apart from the runtime because it delivers a record of a
# run rather than running one, and its browser bundle is bounded by size
# instead, in view/. The command line is budgeted apart from the runtime
# because it serves a person at a terminal rather than an episode.
# See docs/design.md "Size".
set -euo pipefail
cd "$(dirname "$0")/.."
count() {
  find "crates/$1/src" -name '*.rs' ! -path '*/tests/*' ! -name '*_test.rs' ! -name 'generated*' \
      -exec cat {} + | grep -cvE '^\s*$|^\s*//' || true
}
total=0
for c in log core code; do
  n=$(count "$c")
  printf '%-8s %6d\n' "$c" "$n"; total=$((total + n))
done
printf '%-8s %6d  (budget 6000)\n' runtime "$total"
workflow=$(count workflow)
printf '%-8s %6d  (budget 1000)\n' workflow "$workflow"
context=$(count context)
printf '%-8s %6d  (budget 500)\n' context "$context"
view=$(count view)
printf '%-8s %6d  (budget 600)\n' view "$view"
cli=$(count cli)
printf '%-8s %6d  (budget 1300)\n' cli "$cli"
[ "$total" -le 6000 ] && [ "$workflow" -le 1000 ] && [ "$context" -le 500 ] && [ "$view" -le 600 ] \
  && [ "$cli" -le 1300 ]
