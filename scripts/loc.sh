#!/usr/bin/env bash
# Counts Rust source lines in the budgeted crates, excluding tests and generated code.
# Eleven budgets: 6,250 over the kernel; 1,575 over contract; 1,900 over tools;
# 800 over team coordination; 1,050 over workflow; 500 over context; 600 over
# view; 1,650 over cli; 2,700 over transport; 1,000 over telemetry; and 500
# over evidence. Tools and team coordination stay under 2,700 together.
# The kernel combines log and core. Contract defines execution-contract
# documents, resolution, and fingerprints. Evidence verifies portable bundles.
# The kernel is budgeted apart from contract because it measures the machine.
# Contract measures the data model. A document that gains a key must not buy
# room in the loop.
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
      -exec awk '
          FNR == 1 { test_only = 0; test_attribute = 0 }
          test_only { next }
          /^#\[cfg\(test\)\]$/ { test_attribute = 1; next }
          test_attribute && /^mod tests \{$/ { test_only = 1; next }
          {
            if ($0 !~ /^[[:space:]]*$/ && $0 !~ /^[[:space:]]*\/\//) lines++
            test_attribute = 0
          }
          END { print lines + 0 }' {} +
}
kernel=0
for c in log core; do
  n=$(count "$c")
  printf '%-8s %6d\n' "$c" "$n"; kernel=$((kernel + n))
done
printf '%-8s %6d  (budget 6250)\n' kernel "$kernel"
contract=$(count contract)
printf '%-8s %6d  (budget 1575)\n' contract "$contract"
tools=$(count code)
printf '%-8s %6d  (budget 1900)\n' tools "$tools"
team=$(count team)
printf '%-8s %6d  (budget 800)\n' team "$team"
coordination=$((tools + team))
printf '%-8s %6d  (budget 2700)\n' tools+team "$coordination"
workflow=$(count workflow)
printf '%-8s %6d  (budget 1050)\n' workflow "$workflow"
context=$(count context)
printf '%-8s %6d  (budget 500)\n' context "$context"
view=$(count view)
printf '%-8s %6d  (budget 600)\n' view "$view"
cli=$(count cli)
printf '%-8s %6d  (budget 1650)\n' cli "$cli"
transport=$(count transport)
printf '%-8s %6d  (budget 2700)\n' transport "$transport"
telemetry=$(count telemetry)
printf '%-8s %6d  (budget 1000)\n' telemetry "$telemetry"
evidence=$(count evidence)
printf '%-8s %6d  (budget 500)\n' evidence "$evidence"
[ "$kernel" -le 6250 ] && [ "$contract" -le 1575 ] && [ "$tools" -le 1900 ] && [ "$team" -le 800 ] \
  && [ "$coordination" -le 2700 ] && [ "$workflow" -le 1050 ] \
  && [ "$context" -le 500 ] && [ "$view" -le 600 ] && [ "$cli" -le 1650 ] && [ "$transport" -le 2700 ] \
  && [ "$telemetry" -le 1000 ] && [ "$evidence" -le 500 ]
