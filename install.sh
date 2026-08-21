#!/bin/sh
set -eu

repository="https://github.com/pedapudi/foe"
reference="main"
install_dir=""
temporary_dir=""
staged_binary=""
bazel_root=""
bazel_command=""

cleanup() {
  if [ -n "$staged_binary" ] && [ -f "$staged_binary" ]; then
    rm -f "$staged_binary"
  fi
  if [ -n "$bazel_command" ] && [ -n "$bazel_root" ] && [ -d "$bazel_root" ]; then
    "$bazel_command" --output_user_root="$bazel_root" shutdown >/dev/null 2>&1 || true
  fi
  if [ -n "$temporary_dir" ] && [ -d "$temporary_dir" ]; then
    chmod -R u+w "$temporary_dir" 2>/dev/null || true
    rm -rf "$temporary_dir"
  fi
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

run_bazel() {
  if [ -n "$bazel_root" ]; then
    "$bazel_command" --output_user_root="$bazel_root" "$@"
  else
    "$bazel_command" "$@"
  fi
}

usage() {
  cat <<'EOF'
usage: install.sh [--install-dir DIR] [--ref GIT-REFERENCE]

Builds foe from a local checkout or a downloaded source archive, then
installs the binary. The default destination is ~/.local/bin and the
default downloaded Git reference is main.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --install-dir)
      [ "$#" -ge 2 ] || { echo "install.sh: --install-dir requires a value" >&2; exit 1; }
      install_dir=$2
      shift
      ;;
    --ref)
      [ "$#" -ge 2 ] || { echo "install.sh: --ref requires a value" >&2; exit 1; }
      reference=$2
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "install.sh: unknown argument $1" >&2
      usage >&2
      exit 1
      ;;
  esac
  shift
done

case "$reference" in
  ""|/*|*/|*..*|*//*|*[!A-Za-z0-9._/-]*)
    echo "install.sh: --ref contains an unsupported value" >&2
    exit 1
    ;;
esac

if [ -z "$install_dir" ]; then
  if command -v getent >/dev/null 2>&1; then
    home_dir=$(getent passwd "$(id -u)" | awk -F: '{ print $6 }')
  else
    home_dir=$(CDPATH= cd -- && pwd)
  fi
  if [ -z "$home_dir" ]; then
    home_dir=$(CDPATH= cd -- && pwd)
  fi
  install_dir="$home_dir/.local/bin"
fi

case "$install_dir" in
  /*) ;;
  *) echo "install.sh: --install-dir must be an absolute path" >&2; exit 1 ;;
esac

if command -v bazel >/dev/null 2>&1; then
  bazel_command=bazel
elif command -v bazelisk >/dev/null 2>&1; then
  bazel_command=bazelisk
else
  echo "install.sh: Bazel or Bazelisk is required to build foe" >&2
  exit 1
fi

source_dir=""
case "$0" in
  */*)
    candidate=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
    if [ -f "$candidate/Cargo.toml" ] && [ -f "$candidate/crates/cli/Cargo.toml" ]; then
      source_dir=$candidate
    fi
    ;;
esac

temporary_dir=$(mktemp -d)
if [ -z "$source_dir" ]; then
  bazel_root="$temporary_dir/bazel-root"
  archive="$temporary_dir/source.tar.gz"
  url="$repository/archive/$reference.tar.gz"
  echo "Downloading foe source from $url"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$url" -o "$archive"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$archive" "$url"
  else
    echo "install.sh: curl or wget is required to download foe" >&2
    exit 1
  fi
  source_dir="$temporary_dir/source"
  mkdir -p "$source_dir"
  tar -xzf "$archive" --strip-components=1 -C "$source_dir"
fi

echo "Building foe with Bazel and the repository's pinned Rust toolchain"
(
  cd "$source_dir"
  run_bazel build \
    --lockfile_mode=error \
    --experimental_convenience_symlinks=ignore \
    //:foe
)

bazel_bin=$(
  cd "$source_dir"
  run_bazel info -c opt bazel-bin
)
built_binary="$bazel_bin/crates/cli/foe"
[ -x "$built_binary" ] || { echo "install.sh: the build produced no foe binary" >&2; exit 1; }
"$built_binary" schema >/dev/null

mkdir -p "$install_dir"
staged_binary="$install_dir/.foe-install.$$"
cp "$built_binary" "$staged_binary"
chmod 755 "$staged_binary"
mv "$staged_binary" "$install_dir/foe"
staged_binary=""

echo "Installed foe to $install_dir/foe"
echo "Run it with: $install_dir/foe \"describe this repository\""
echo "Add $install_dir to PATH to run it as foe."
