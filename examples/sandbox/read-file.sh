#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "usage: read-file.sh PATH" >&2
  exit 2
fi

while IFS= read -r line || [ -n "$line" ]; do
  printf '%s\n' "$line"
done < "$1"
