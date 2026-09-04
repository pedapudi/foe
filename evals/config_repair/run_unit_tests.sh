#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir"
python3 -m py_compile \
  candidate_check.py \
  evaluate.py \
  operational_digest.py \
  prepared_candidate_responses.py \
  run_repair_loop.py \
  task_responses.py
python3 -m unittest -v \
  evaluate_test.py \
  operational_digest_test.py \
  run_repair_loop_test.py
