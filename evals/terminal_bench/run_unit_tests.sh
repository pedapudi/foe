#!/bin/sh
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$script_dir"
python3 -m py_compile foe_agent.py
python3 -m unittest -v foe_agent_support_test.py run_test.py
