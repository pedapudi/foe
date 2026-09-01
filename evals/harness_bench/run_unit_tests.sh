#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir"
python3 -m unittest -v \
  run_foe_test.py \
  collect_evidence_test.py \
  run_self_improvement_test.py
