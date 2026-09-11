#!/usr/bin/env bash
# Builds and exercises the release artifact from one commit with passing CI.
set -euo pipefail

target=x86_64-unknown-linux-musl
asset=foe-x86_64-linux
usage() {
  cat <<'EOF'
usage: scripts/release.sh VERSION [--draft]

Builds one clean commit with passing CI, exercises the artifact, and creates
vVERSION with the binary and its SHA-256. VERSION must match Cargo.toml.
EOF
}
draft=()
version=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --draft) draft=(--draft) ;;
    --help|-h) usage; exit 0 ;;
    -*) echo "release.sh: unknown argument $1" >&2; usage >&2; exit 1 ;;
    *) [ -z "$version" ] || { echo "release.sh: supply one version" >&2; exit 1; }; version=$1 ;;
  esac
  shift
done
[ -n "$version" ] || { usage >&2; exit 1; }
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(CDPATH= cd -- "$here/.." && pwd)
cd "$repo"
[ -z "$(git status --porcelain)" ] || { echo "release.sh: the working tree has changes" >&2; exit 1; }
commit=$(git rev-parse HEAD)
declared=$(git show "$commit:Cargo.toml" | sed -n 's/^version = "\(.*\)"$/\1/p' | head -1)
[ "$declared" = "$version" ] || { echo "release.sh: Cargo.toml declares $declared; requested $version" >&2; exit 1; }
tag="v$version"

# The most recent run for this commit must have completed successfully.
conclusion=$(gh run list --commit "$commit" --workflow ci.yml --limit 1 --json conclusion --jq '.[0].conclusion')
[ "$conclusion" = success ] || { echo "release.sh: ci.yml most recent run for $commit did not succeed" >&2; exit 1; }
rustup target list --installed | grep -qx "$target" || {
  echo "release.sh: install the $target toolchain target" >&2; exit 1;
}

staging=$(mktemp -d)
trap 'rm -rf "$staging"' EXIT
mkdir "$staging/source"
git archive "$commit" | tar -x -C "$staging/source"
(
  cd "$staging/source"
  cargo build --locked --release -p foe --target "$target" --target-dir "$repo/target/release-artifact"
)
built="$repo/target/release-artifact/$target/release/foe"
cp "$built" "$staging/$asset"
( cd "$staging" && sha256sum "$asset" > "$asset.sha256" )

# Both examples execute the exact staged asset. They exercise the host
# protocol, verification, child startup, and child task settlement.
"$staging/$asset" plan >/dev/null
python3 "$staging/source/examples/host-model-backend/run.py" "$staging/$asset"
bash "$staging/source/examples/team/run.sh" "$staging/$asset"
( cd "$staging" && sha256sum --check "$asset.sha256" )

# Prefer the peeled commit when the existing remote tag is annotated.
remote=$(git ls-remote --tags origin "refs/tags/$tag" "refs/tags/$tag^{}")
tag_commit=$(printf '%s\n' "$remote" | awk '/\^\{\}$/ {print $1; found=1} END {if (!found && NR) print first} NR==1 {first=$1}')
if [ -n "$tag_commit" ]; then
  [ "$tag_commit" = "$commit" ] || { echo "release.sh: $tag names $tag_commit; artifact was built from $commit" >&2; exit 1; }
else
  # Creating a tag refuses a competing tag without overwriting it.
  git push origin "$commit:refs/tags/$tag"
fi

gh release create "$tag" "${draft[@]}" --verify-tag --target "$commit" \
  --title "foe $version" \
  --notes "A statically linked x86-64 Linux binary built from commit $commit.

Install it with:

    curl -fsSL foe.sh/install.sh | sh

The installer verifies the SHA-256 published beside the binary." \
  "$staging/$asset" "$staging/$asset.sha256"
printf 'Released %s from %s (%s bytes)\n' "$tag" "$commit" "$(wc -c < "$staging/$asset")"
