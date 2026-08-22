#!/bin/sh
set -eu

exec /usr/bin/python3 "$(dirname "$0")/micro_tasks_test.py"
