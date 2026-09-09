#!/bin/sh
set -eu

exec /usr/bin/python3 "$(dirname "$0")/episode_cost_test.py"
