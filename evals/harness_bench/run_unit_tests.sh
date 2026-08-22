#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 -m unittest -v "$script_dir/run_foe_test.py"
