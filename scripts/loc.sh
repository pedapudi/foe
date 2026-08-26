#!/usr/bin/env bash
# Counts Rust source lines in the budgeted crates, excluding tests and generated code.
# Ten budgets: 5,250 over the kernel, which is log and core together — the
# log format, the loop, budgets, sandbox, and spawn, whose smallness is the
# product claim; 1,500 over program, the other contract, which is the
# program document, its resolution, and identity; 1,700
# over tools, which is code — the tool surface, which grows a tool at a time
# without touching the kernel; 1,000 over workflow; 500 over context; 600
# over view; 1,300 over cli; 1,000
# over telemetry; 500 over lineage, which reads finished evidence about how
# program states relate and is part of neither contract; and 850 over the
# source-adoption evaluator used by external campaigns. The kernel is budgeted apart
# from program because the kernel measures the machine and program measures the
# data model; a document that gains a key must not buy room in the loop. The
# viewer
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
printf '%-8s %6d  (budget 5250)\n' kernel "$kernel"
program=$(count program)
printf '%-8s %6d  (budget 1500)\n' program "$program"
tools=$(count code)
printf '%-8s %6d  (budget 1700)\n' tools "$tools"
workflow=$(count workflow)
printf '%-8s %6d  (budget 1000)\n' workflow "$workflow"
context=$(count context)
printf '%-8s %6d  (budget 500)\n' context "$context"
view=$(count view)
printf '%-8s %6d  (budget 600)\n' view "$view"
cli=$(count cli)
printf '%-8s %6d  (budget 1300)\n' cli "$cli"
telemetry=$(count telemetry)
printf '%-8s %6d  (budget 1000)\n' telemetry "$telemetry"
lineage=$(count lineage)
printf '%-8s %6d  (budget 500)\n' lineage "$lineage"
source_adoption=$(grep -cvE '^\s*$|^\s*//' crates/lineage/examples/source_adoption.rs || true)
printf '%-15s %6d  (budget 850)\n' source_adoption "$source_adoption"
[ "$kernel" -le 5250 ] && [ "$program" -le 1500 ] && [ "$tools" -le 1700 ] && [ "$workflow" -le 1000 ] \
  && [ "$context" -le 500 ] && [ "$view" -le 600 ] && [ "$cli" -le 1300 ] && [ "$telemetry" -le 1000 ] \
  && [ "$lineage" -le 500 ] && [ "$source_adoption" -le 850 ]
