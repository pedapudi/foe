#!/bin/sh
set -eu

checker=$1
case "$checker" in
  /*) ;;
  *) checker="$PWD/$checker" ;;
esac

candidate=$TEST_TMPDIR/candidate
mkdir -p "$candidate/crates/code/src" "$candidate/docs"
cat > "$candidate/crates/code/src/read.rs" <<'EOF'
let total_bytes = bytes.len();
json!({"total_bytes": total_bytes})
EOF
cat > "$candidate/crates/code/src/read_test.rs" <<'EOF'
assert_eq!(v.value["total_bytes"], text.len());
EOF
cat > "$candidate/docs/tools.md" <<'EOF'
| `read` | reads | `path` | limited | `path`, `total_bytes`, `content` |

`total_bytes` is the byte
count of the complete file.
EOF

findings=$(CDPATH= cd -- "$candidate" && "$checker")
[ -z "$findings" ] || {
  echo "the checker rejected an assertion against the fixture length: $findings" >&2
  exit 1
}

cat > "$candidate/crates/code/src/read_test.rs" <<'EOF'
assert_eq!(v.value["total_lines"], 3);
EOF
findings=$(CDPATH= cd -- "$candidate" && "$checker")
[ "$findings" = 'crates/code/src/read_test.rs does not assert the byte count' ] || {
  echo "the checker accepted a test with no total_bytes assertion: $findings" >&2
  exit 1
}
