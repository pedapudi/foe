#!/bin/sh
set -eu

exec /usr/bin/python3 "$(dirname "$0")/containment_matrix_test.py"
