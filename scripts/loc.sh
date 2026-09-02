#!/usr/bin/env bash
# Counts Rust source lines in the budgeted crates, excluding tests and generated code.
# Nine budgets: 6,720 over the kernel, which is log and core together; 1,575
# over contract, which defines execution-contract documents, resolution, and
# fingerprints; 1,825 over tools, which is code; 1,050 over workflow; 500 over
# context; 600 over view; 1,650 over cli; 1,000 over telemetry; and 500 over
# adoption, which verifies portable evidence. The kernel is budgeted apart
# from contract because the kernel measures the machine and contract measures
# the data model. A document that gains a key must not buy room in the loop.
# The viewer is budgeted apart because it delivers a record of a run rather
# than running one, and its browser bundle is bounded by size instead, in view/.
# The command line is budgeted apart because it serves a person at a terminal
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
printf '%-8s %6d  (budget 6720)\n' kernel "$kernel"
contract=$(count contract)
printf '%-8s %6d  (budget 1575)\n' contract "$contract"
tools=$(count code)
printf '%-8s %6d  (budget 1825)\n' tools "$tools"
workflow=$(count workflow)
printf '%-8s %6d  (budget 1050)\n' workflow "$workflow"
context=$(count context)
printf '%-8s %6d  (budget 500)\n' context "$context"
view=$(count view)
printf '%-8s %6d  (budget 600)\n' view "$view"
cli=$(count cli)
printf '%-8s %6d  (budget 1650)\n' cli "$cli"
telemetry=$(count telemetry)
printf '%-8s %6d  (budget 1000)\n' telemetry "$telemetry"
adoption=$(count adoption)
printf '%-8s %6d  (budget 500)\n' adoption "$adoption"
[ "$kernel" -le 6720 ] && [ "$contract" -le 1575 ] && [ "$tools" -le 1825 ] && [ "$workflow" -le 1050 ] \
  && [ "$context" -le 500 ] && [ "$view" -le 600 ] && [ "$cli" -le 1650 ] && [ "$telemetry" -le 1000 ] && [ "$adoption" -le 500 ]
