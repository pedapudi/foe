#!/usr/bin/env bash
# Counts Rust source lines in the budgeted crates, excluding tests and generated code.
# Six budgets: 5400 over the kernel, which is log and core together — the log
# format, the loop, budgets, sandbox, and spawn, whose smallness is the
# product claim; 1600 over tools, which is code — the tool surface, which
# grows a tool at a time without touching the kernel; 1000 over workflow; 500
# over context; 600 over view; 1300 over cli. The viewer is budgeted apart
# because it delivers a record of a run rather than running one, and its
# browser bundle is bounded by size instead, in view/. The command line is
# budgeted apart because it serves a person at a terminal rather than an
# episode. See docs/design.md "Size".
set -euo pipefail
cd "$(dirname "$0")/.."
count() {
  find "crates/$1/src" -name '*.rs' ! -path '*/tests/*' ! -name '*_test.rs' ! -name 'generated*' \
      -exec cat {} + | grep -cvE '^\s*$|^\s*//' || true
}
kernel=0
for c in log core; do
  n=$(count "$c")
  printf '%-8s %6d\n' "$c" "$n"; kernel=$((kernel + n))
done
printf '%-8s %6d  (budget 5400)\n' kernel "$kernel"
tools=$(count code)
printf '%-8s %6d  (budget 1600)\n' tools "$tools"
workflow=$(count workflow)
printf '%-8s %6d  (budget 1000)\n' workflow "$workflow"
context=$(count context)
printf '%-8s %6d  (budget 500)\n' context "$context"
view=$(count view)
printf '%-8s %6d  (budget 600)\n' view "$view"
cli=$(count cli)
printf '%-8s %6d  (budget 1300)\n' cli "$cli"
[ "$kernel" -le 5400 ] && [ "$tools" -le 1600 ] && [ "$workflow" -le 1000 ] && [ "$context" -le 500 ] \
  && [ "$view" -le 600 ] && [ "$cli" -le 1300 ]
