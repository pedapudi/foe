#!/bin/sh
# Runs every unit test of the cross-harness evaluation. None needs a model
# credential, the network, or a Codex login; a test that exercises the built
# foe binary skips with its reason when target/debug/foe is absent.
set -eu

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
  run_test.py \
  report_test.py
do
  echo "== $test"
  /usr/bin/python3 "$dir/$test" || status=1
done
exit "$status"
