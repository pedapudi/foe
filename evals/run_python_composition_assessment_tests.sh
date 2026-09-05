#!/bin/sh
set -eu
exec /usr/bin/python3 "$(dirname "$0")/python_composition_assessment_test.py"
