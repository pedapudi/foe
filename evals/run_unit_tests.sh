#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir"
/usr/bin/python3 -m unittest -v \
  foe_source_identity_test.py \
  trace_quality_test.py
