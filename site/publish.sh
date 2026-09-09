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
  # The temporary branch and the worktree record outlive a failed run.
  git -C "$repo" worktree prune
  git -C "$repo" branch -q -D publish 2>/dev/null || true
}
trap cleanup EXIT

python3 "$here/build/build.py"

git -C "$repo" fetch -q origin gh-pages
rmdir "$work"
git -C "$repo" worktree add -q --detach "$work" origin/gh-pages
git -C "$work" switch -q -C publish

# The published tree is exactly what the build wrote. A worktree keeps its
# link to the repository in a .git file, so the sweep has to leave that file
# alone. The faces carry their own licence statement in their name table,
# which is how view/fonts distributes them; view/fonts/README.md records it.
# CNAME is the custom domain: GitHub reads it from the served tree and drops
# the domain when it is missing, so it is a source file here rather than
# state the sweep would carry away.
find "$work" -maxdepth 1 -type f ! -name README.md ! -name .git -exec rm -f {} +
cp "$here/public/index.html" "$here/public/favicon.svg" "$here/public/install.sh" "$work/"
cp "$here/public/CNAME" "$work/"
cp "$here"/public/*.woff2 "$work/"

git -C "$work" add -A
if git -C "$work" diff --cached --quiet; then
  echo "publish.sh: the served tree already matches the build"
  exit 0
fi
git -C "$work" commit -q -m "Publish the landing page"
git -C "$work" push -q origin publish:gh-pages
echo "publish.sh: pushed to gh-pages; https://foe.sh/ rebuilds in about a minute"
