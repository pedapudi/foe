#!/bin/sh
# Builds the published binary and creates the release that carries it.
#
# There is no release workflow: this runs on a maintainer's machine and
# uploads what it built, so cutting a release costs no continuous-integration
# minutes. The binary is statically linked against musl, which is what lets
# one file run on any x86-64 Linux rather than only on a distribution whose
# glibc is new enough for the machine that built it.
set -eu

target=x86_64-unknown-linux-musl
asset=foe-x86_64-linux

usage() {
  cat <<'EOF'
usage: scripts/release.sh VERSION [--draft]

Builds the statically linked binary, checks it runs, and creates the release
tagged vVERSION with the binary and its SHA-256 attached. VERSION is the
version in Cargo.toml, without a leading v.
EOF
}

draft=""
version=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --draft) draft=--draft ;;
    --help|-h) usage; exit 0 ;;
    -*) echo "release.sh: unknown argument $1" >&2; usage >&2; exit 1 ;;
    *) [ -z "$version" ] || { echo "release.sh: one version, not two" >&2; exit 1; }; version=$1 ;;
  esac
  shift
done
[ -n "$version" ] || { usage >&2; exit 1; }

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(CDPATH= cd -- "$here/.." && pwd)
cd "$repo"

declared=$(sed -n 's/^version = "\(.*\)"$/\1/p' Cargo.toml | head -1)
if [ "$declared" != "$version" ]; then
  echo "release.sh: Cargo.toml declares $declared, not $version" >&2
  exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "release.sh: the working tree has changes; a release names a commit" >&2
  exit 1
fi

command -v gh >/dev/null 2>&1 || { echo "release.sh: gh is required to create a release" >&2; exit 1; }
rustup target list --installed | grep -qx "$target" || {
  echo "release.sh: rustup target add $target" >&2
  exit 1
}
command -v musl-gcc >/dev/null 2>&1 || command -v x86_64-linux-musl-gcc >/dev/null 2>&1 || {
  echo "release.sh: a musl C compiler is required: apt install musl-tools" >&2
  exit 1
}

echo "Building $asset for $target"
cargo build --locked --release -p foe --target "$target"
built="target/$target/release/foe"
[ -x "$built" ] || { echo "release.sh: the build produced no binary" >&2; exit 1; }

# The binary answers for itself before it is published. `plan` resolves a
# document and builds the tool registry, and needs neither a credential nor
# a network, so it fails on a binary that cannot do its own work.
"$built" plan >/dev/null

staging=$(mktemp -d)
trap 'rm -rf "$staging"' EXIT
cp "$built" "$staging/$asset"
( cd "$staging" && sha256sum "$asset" > "$asset.sha256" )

echo "Creating release v$version"
gh release create "v$version" $draft \
  --title "foe $version" \
  --notes "A statically linked x86-64 Linux binary. Install it with:

    curl -fsSL foe.sh/install.sh | sh

The installer verifies the SHA-256 published beside the binary." \
  "$staging/$asset" "$staging/$asset.sha256"

echo "Released v$version with $asset ($(wc -c < "$staging/$asset") bytes)"
