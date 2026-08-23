#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir"
python3 -m py_compile \
  capability_probe_agent.py \
  capability_probe_support.py \
  collect_diagnostics.py \
  foe_agent.py \
  foe_agent_support.py \
  run.py \
  run_capability_probes.py \
  run_self_improvement.py \
  trajectory_diagnostics.py
python3 -m unittest -v \
  capability_probe_support_test.py \
  collect_diagnostics_test.py \
  foe_agent_support_test.py \
  run_self_improvement_test.py \
  run_test.py \
  trajectory_diagnostics_test.py
