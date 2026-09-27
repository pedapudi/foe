#!/bin/sh
# Runs every unit test of the cross-harness evaluation. None needs a model
# credential, the network, or a Codex login. A test that exercises the built
# foe binary, cargo, or the repository's git history skips with its reason
# when that input is absent, as it is inside the Bazel sandbox.
#
# Usage: run_unit_tests.sh [--forbid-skips]
#
# --forbid-skips counts a skipped test as a failure and prints its test id and
# reason. Continuous integration passes it, so an absent binary or a shallow
# clone fails the suite rather than silently narrowing it.
set -eu

forbid_skips=0
case "${1-}" in
  "") ;;
  --forbid-skips) forbid_skips=1 ;;
  *)
    echo "run_unit_tests.sh: unknown argument '$1'; the one option is --forbid-skips" >&2
    exit 2
    ;;
esac

dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
status=0
for test in \
  trajectory_test.py \
  containment_matrix_test.py \
  normalize_foe_test.py \
  normalize_codex_test.py \
  metering_proxy_test.py \
  codex_budget_watcher_test.py \
  probe_test.py \
  arms/foe_arm_test.py \
  arms/codex_arm_test.py \
  contracts/graphs_test.py \
  tasks/protocol_test.py \
  tasks/policies_test.py \
  tasks/feature_removal_test.py \
  tasks/constructions_test.py \
  tasks/teams_test.py \
  gates/label_leakage_test.py \
  gates/isolation_test.py \
  environment/environment_test.py \
  environment/sink/recorder_test.py \
  run_test.py \
  report_test.py \
  admission_test.py \
  rescore_test.py \
  conditions_test.py \
  repairs_test.py \
  results/archive_test.py
do
  echo "== $test"
  if [ "$forbid_skips" -eq 0 ]; then
    /usr/bin/python3 "$dir/$test" || status=1
    continue
  fi
  # unittest in verbose mode reports a skip as "<test id> ... skipped '<reason>'"
  # on standard error. The output is kept so the skip lines can be listed.
  output=$(mktemp)
  /usr/bin/python3 "$dir/$test" -v >"$output" 2>&1 || status=1
  cat "$output"
  if grep -q "skipped '" "$output"; then
    echo "== $test: skipped tests are failures under --forbid-skips:"
    grep "skipped '" "$output" | sed 's/^/   /'
    status=1
  fi
  rm -f "$output"
done
exit "$status"
