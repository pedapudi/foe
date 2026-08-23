#!/usr/bin/env bash
# Counts Rust source lines in the budgeted crates, excluding tests and generated code.
# Seven budgets: 5400 over the kernel, which is log and core together — the
# log format, the loop, budgets, sandbox, and spawn, whose smallness is the
# product claim; 1600 over tools, which is code — the tool surface, which
# grows a tool at a time without touching the kernel; 1,100 over workflow; 500
# over context; 600 over view; 1,000 over cli; 800 over telemetry. The viewer
# is budgeted apart because it delivers a record of a run rather than running
# one, and its browser bundle is bounded by size instead, in view/. The
# command line is budgeted apart because it serves a person at a terminal
# rather than an episode. Telemetry is budgeted apart because it reads a
# finished log rather than producing one, and nothing in the runtime may
# depend on it. See docs/design.md "Size".
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
printf '%-8s %6d  (budget 1100)\n' workflow "$workflow"
context=$(count context)
printf '%-8s %6d  (budget 500)\n' context "$context"
view=$(count view)
printf '%-8s %6d  (budget 600)\n' view "$view"
cli=$(count cli)
printf '%-8s %6d  (budget 1000)\n' cli "$cli"
telemetry=$(count telemetry)
printf '%-8s %6d  (budget 900)\n' telemetry "$telemetry"
[ "$kernel" -le 5400 ] && [ "$tools" -le 1600 ] && [ "$workflow" -le 1100 ] && [ "$context" -le 500 ] \
  && [ "$view" -le 600 ] && [ "$cli" -le 1000 ] && [ "$telemetry" -le 900 ]
