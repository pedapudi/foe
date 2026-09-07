#!/bin/sh
# Builds the page and updates the branch GitHub Pages serves.
#
# The served tree is the gh-pages branch at its root, because Pages serves a
# branch's root or its docs directory and this page lives under site/public.
# The build runs first, so what is published is what the sources produce.
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo=$(CDPATH= cd -- "$here/.." && pwd)
work=$(mktemp -d)

cleanup() {
  git -C "$repo" worktree remove --force "$work" 2>/dev/null || true
  rm -rf "$work"
}
trap cleanup EXIT

python3 "$here/build/build.py"

git -C "$repo" fetch -q origin gh-pages
git -C "$repo" worktree add -q "$work" origin/gh-pages --detach
git -C "$work" switch -q -c publish

# The published tree is exactly what the build wrote, plus the font licence.
find "$work" -maxdepth 1 -type f ! -name README.md -exec rm -f {} +
cp "$here/public/index.html" "$here/public/favicon.svg" "$here/public/install.sh" "$work/"
cp "$here"/public/*.woff2 "$work/"
cp "$here/fonts/UFL-Ubuntu.txt" "$work/"

git -C "$work" add -A
if git -C "$work" diff --cached --quiet; then
  echo "publish.sh: the served tree already matches the build"
  exit 0
fi
git -C "$work" commit -q -m "Publish the landing page"
git -C "$work" push -q origin publish:gh-pages
echo "publish.sh: pushed to gh-pages; https://pedapudi.github.io/foe/ rebuilds in about a minute"
