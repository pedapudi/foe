#!/bin/sh
set -eu

repository=/app/personal-site
commit=$(/usr/bin/git -C "$repository" reflog --all --format='%H%x09%gs' \
    | /usr/bin/awk -F '\t' '$2 ~ /Move to Stanford/ { print $1; exit }')
test -n "$commit"
/usr/bin/git -C "$repository" checkout master
/usr/bin/git -C "$repository" merge --no-edit -X theirs "$commit"
