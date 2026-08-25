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
  instruction_candidate.py \
  run_self_improvement.py \
  tool_candidate.py \
  run_verifier_controls.py \
  verifier_control_agent.py \
  verifier_cases/cancel_async_tasks/check.py \
  verifier_cases/fix_git/check.py \
  verifier_cases/large_scale_text_editing/check.py \
  trajectory_diagnostics.py \
  workflow_candidate.py
python3 -m unittest -v \
  capability_probe_support_test.py \
  collect_diagnostics_test.py \
  foe_agent_support_test.py \
  instruction_candidate_test.py \
  run_self_improvement_test.py \
  tool_candidate_test.py \
  run_test.py \
  run_verifier_controls_test.py \
  task_derived_checker_test.py \
  trajectory_diagnostics_test.py \
  workflow_candidate_test.py
