#!/usr/bin/env bash
# Counts Rust source lines in the budgeted crates, excluding tests and
# generated code, and fails when a surface exceeds its ceiling. The reasons
# each surface is budgeted apart are in docs/design.md "Size".
#
# The two tables below are the only places a ceiling is written in this
# script. The first holds one ceiling per surface. The second holds a ceiling
# over a group of surfaces, so that splitting a surface into two crates does
# not raise the total allowance. AGENTS.md, README.md, and docs/design.md
# quote the same ceilings, and the second half of this script fails when any
# of them quotes a different number, so a ceiling changes in one commit that
# touches all four files.
set -euo pipefail
cd "$(dirname "$0")/.."

# surface | ceiling | README table row label | crates whose lines are summed
budgets='
kernel    | 6250 | kernel               | log core
contract  | 1575 | execution contracts  | contract
tools     | 1900 | coding tools         | code
team      |  800 | team coordination    | team
workflow  | 1050 | workflows            | workflow
context   |  500 | compaction           | context
view      |  600 | viewer server        | view
cli       | 1650 | command line         | cli
transport | 2700 | model transports     | transport
telemetry | 1000 | telemetry            | telemetry
evidence  |  500 | evidence             | evidence
'

# group | ceiling | surfaces whose totals are summed | phrase every document
# uses to name the group. A group ceiling is stated in prose rather than in
# the README table, so the same phrase locates it in all three documents.
groups='
tools+team | 2700 | tools team | Coding tools and team coordination together
'

# rows TABLE: prints TABLE with each field trimmed, one row per line, fields
# separated by a tab.
rows() {
  printf '%s\n' "$1" | sed -e '/^[[:space:]]*$/d' -e 's/[[:space:]]*|[[:space:]]*/\t/g' -e 's/^[[:space:]]*//;s/[[:space:]]*$//'
}

count() {
  find "crates/$1/src" -name '*.rs' ! -path '*/tests/*' ! -name '*_test.rs' ! -name 'generated*' \
      -exec awk '
          FNR == 1 { test_only = 0; test_attribute = 0 }
          test_only { next }
          /^#\[cfg\(test\)\]$/ { test_attribute = 1; next }
          test_attribute && /^mod tests \{$/ { test_only = 1; next }
          {
            if ($0 !~ /^[[:space:]]*$/ && $0 !~ /^[[:space:]]*\/\//) lines++
            test_attribute = 0
          }
          END { print lines + 0 }' {} +
}

status=0
declare -A measured

while IFS=$'\t' read -r surface ceiling _label crates; do
  total=0
  for crate in $crates; do
    n=$(count "$crate")
    total=$((total + n))
    case $crates in *' '*) printf '%-10s %6d\n' "$crate" "$n" ;; esac
  done
  measured[$surface]=$total
  printf '%-10s %6d  (budget %d)\n' "$surface" "$total" "$ceiling"
  if [ "$total" -gt "$ceiling" ]; then
    echo "scripts/loc.sh: $surface has $total lines; the ceiling is $ceiling" >&2
    status=1
  fi
done < <(rows "$budgets")

while IFS=$'\t' read -r group ceiling surfaces _phrase; do
  total=0
  for surface in $surfaces; do
    total=$((total + measured[$surface]))
  done
  printf '%-10s %6d  (budget %d)\n' "$group" "$total" "$ceiling"
  if [ "$total" -gt "$ceiling" ]; then
    echo "scripts/loc.sh: $group has $total lines; the ceiling is $ceiling" >&2
    status=1
  fi
done < <(rows "$groups")

# quote FILE ANCHOR WINDOW: prints every ceiling FILE states for one surface
# or group, one per line and without thousands separators. ANCHOR is the text
# that names it and WINDOW is what may stand between that text and the
# number. In the README table the ceiling is the first number after the row
# label. In prose it is the first number after the anchor and the word
# "under" within one sentence. The file is folded to a single line with runs
# of whitespace collapsed first, so a sentence wrapped across lines, with or
# without indentation, still matches.
prose='[^.0-9]*under '
quote() {
  tr '\n' ' ' < "$1" | tr -s ' ' | grep -oE "$2$3[0-9][0-9,]*" | grep -oE '[0-9][0-9,]*$' | tr -d , || true
}

# agrees FILE NAME CEILING ANCHOR WINDOW: reports FILE when it states no
# ceiling for NAME or states one other than CEILING.
agrees() {
  local quoted n
  quoted=$(quote "$1" "$4" "$5")
  if [ -z "$quoted" ]; then
    echo "$1: does not state the $2 ceiling; scripts/loc.sh holds $3" >&2
    status=1
  fi
  for n in $quoted; do
    if [ "$n" != "$3" ]; then
      echo "$1: states $n for $2; scripts/loc.sh holds $3" >&2
      status=1
    fi
  done
}

for file in AGENTS.md README.md docs/design.md; do
  while IFS=$'\t' read -r surface ceiling label crates; do
    if [ "$file" = README.md ]; then
      agrees "$file" "$surface" "$ceiling" "\\| $label " '[^0-9]*'
      continue
    fi
    if [ "$surface" = kernel ]; then
      anchor='`log` and `core`'
    else
      anchor="\`(crates/)?$crates\`"
    fi
    agrees "$file" "$surface" "$ceiling" "$anchor" "$prose"
  done < <(rows "$budgets")
  while IFS=$'\t' read -r group ceiling surfaces phrase; do
    agrees "$file" "$group" "$ceiling" "$phrase" "$prose"
  done < <(rows "$groups")
done

exit "$status"
