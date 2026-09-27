#!/bin/sh
# Build the attempt image and the sink image of the cross-harness
# evaluation, warm one build volume with the dependencies of every task, and
# print how one attempt runs. environment.md states what the image holds.
#
#   build.sh [--repo DIR] [--tasks DIR] [--commit SHA]... [--codex PATH]
#            [--cargo PATH] [--state DIR] [--tag NAME] [--sink-tag NAME]
#            [--no-warm]
#
# --repo    the foe checkout; default the one holding this script
# --tasks   the directory of task directories whose task.json files name the
#           fixture commits; default evals/cross_harness/tasks/foe-tree under
#           the repository
# --commit  a fixture commit; may repeat; default every commit the tasks name;
#           the volume is warmed for the tasks that name a listed commit
# --codex   the Codex binary copied into the image; default the `codex`
#           command on the search path
# --cargo   the cargo that vendors the registry; default ~/.cargo/bin/cargo
#           of the calling user
# --state   where the build context, the fixture trees, and the printed
#           recipe are kept; default ~/.local/state/foe/cross-harness/environment
# --tag     the attempt image tag; default foe-cross-harness:local
# --no-warm build the images and skip the build volume
#
# The foe binary is bazel-bin/crates/cli/foe-portable when it exists, the
# static build, and target/debug/foe otherwise; the image records which
# under /opt/foe/origin and the digest under /opt/foe/sha256. The script
# reads no environment variable: the calling user's home comes from the
# account database, and every other value is an argument or a default named
# above. Every argument path is used as given.
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
home=$(getent passwd "$(id -u)" | cut -d: -f6)

# The versions the image pins beyond the Dockerfile: the Codex binary must
# report this version, and the Dockerfile refuses any other.
CODEX_VERSION=0.153.4

# The attempt network. The host never takes the gateway address, because
# the network is created with inhibit_ipv4; the sink holds it instead, so
# the default route of every attempt container ends at the sink.
NETWORK=foe-cross-harness
SUBNET=10.219.0.0/24
GATEWAY=10.219.0.254
SINK_ADDRESS=10.219.0.2

# The build volume. Every grade script of a task on foe's tree builds under
# ~/.local/state/foe/cross-harness/build/TASK/target of the account it runs
# as, which in the attempt image is the user `attempt`; the volume is
# mounted at that directory and warmed in that layout, so a grade in the
# container finds the dependencies of its task compiled.
BUILD_VOLUME=foe-cross-harness-build
BUILD_MOUNT=/home/attempt/.local/state/foe/cross-harness/build

repo=""
tasks=""
commits=""
codex=""
cargo="$home/.cargo/bin/cargo"
state="$home/.local/state/foe/cross-harness/environment"
tag=foe-cross-harness:local
sink_tag=foe-cross-harness-sink:local
warm=yes

fail() {
  printf 'build.sh: %s\n' "$*" >&2
  exit 1
}

usage() {
  sed -n '2,/^set -eu/p' "$0" | head -n -1 | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) repo=$2; shift 2 ;;
    --tasks) tasks=$2; shift 2 ;;
    --commit) commits="$commits $2"; shift 2 ;;
    --codex) codex=$2; shift 2 ;;
    --cargo) cargo=$2; shift 2 ;;
    --state) state=$2; shift 2 ;;
    --tag) tag=$2; shift 2 ;;
    --sink-tag) sink_tag=$2; shift 2 ;;
    --no-warm) warm=no; shift ;;
    -h|--help) usage; exit 0 ;;
    *) fail "unknown argument $1; run with --help for the arguments" ;;
  esac
done

[ -n "$repo" ] || repo=$(git -C "$here" rev-parse --show-toplevel)
[ -d "$repo/.git" ] || [ -f "$repo/.git" ] || fail "--repo names $repo, which is not a git checkout"
[ -n "$tasks" ] || tasks="$repo/evals/cross_harness/tasks/foe-tree"
[ -d "$tasks" ] || fail "--tasks names $tasks, which is not a directory"

# The foe binary: the static build first, the debug build otherwise.
if [ -f "$repo/bazel-bin/crates/cli/foe-portable" ]; then
  foe_origin=bazel-bin/crates/cli/foe-portable
elif [ -f "$repo/target/debug/foe" ]; then
  foe_origin=target/debug/foe
else
  fail "neither $repo/bazel-bin/crates/cli/foe-portable nor $repo/target/debug/foe exists; build one with \`bazel build //crates/cli:foe-portable\` or \`cargo build -p foe\`"
fi
foe="$repo/$foe_origin"

# The Codex binary, resolved through its symbolic links so the image holds
# the bytes rather than a link, and checked against the pinned version.
[ -n "$codex" ] || codex=$(command -v codex || true)
[ -n "$codex" ] || fail "--codex is absent and no codex command is on the search path"
codex=$(readlink -f "$codex")
[ -x "$codex" ] || fail "--codex names $codex, which is not an executable file"
codex_version=$("$codex" --version)
[ "$codex_version" = "codex-cli $CODEX_VERSION" ] || fail "$codex reports '$codex_version'; the image pins codex-cli $CODEX_VERSION (Dockerfile ARG CODEX_VERSION)"

[ -x "$cargo" ] || fail "--cargo names $cargo, which is not an executable file"
[ -f "$repo/rust-toolchain.toml" ] || fail "$repo/rust-toolchain.toml is absent; the image pins the toolchain it names"

# The tasks and the commits they build against, one "TASK COMMIT" line each,
# with every commit resolved to its full hash.
task_commits=$(/usr/bin/python3 - "$tasks" <<'PY'
import json, pathlib, sys
for task in sorted(pathlib.Path(sys.argv[1]).glob("*/task.json")):
    commit = (json.loads(task.read_text(encoding="utf-8")).get("metadata") or {}).get("source", {}).get("commit")
    if commit:
        print(task.parent.name, commit)
PY
)
resolved_task_commits=""
while read -r task commit; do
  [ -n "$task" ] || continue
  full=$(git -C "$repo" rev-parse --verify --quiet "$commit^{commit}") || fail "$tasks/$task/task.json names the commit $commit, which is not a commit of $repo"
  resolved_task_commits="$resolved_task_commits
$task $full"
done <<TASKS
$task_commits
TASKS

# The fixture commits, from the tasks unless given.
if [ -z "$commits" ]; then
  commits=$(printf '%s\n' "$resolved_task_commits" | awk 'NF == 2 && !seen[$2]++ { print $2 }' | tr '\n' ' ')
  [ -n "${commits% }" ] || fail "no task.json under $tasks names metadata.source.commit; pass --commit"
fi
resolved=""
for commit in $commits; do
  full=$(git -C "$repo" rev-parse --verify --quiet "$commit^{commit}") || fail "--commit $commit is not a commit of $repo"
  resolved="$resolved $full"
done
commits=$resolved

# The build context.
context="$state/context"
rm -rf "$context"
mkdir -p "$context" "$state/fixtures"
cp -L "$foe" "$context/foe"
cp -L "$codex" "$context/codex"
cp "$repo/rust-toolchain.toml" "$context/rust-toolchain.toml"
cp "$here/Dockerfile" "$context/Dockerfile"
cp "$here/cargo-config.toml" "$context/cargo-config.toml"

# One fixture tree per commit, from git archive, so the vendored registry
# covers every lockfile the tasks build against and each task's build
# directory is warmed from the tree the task regenerates.
sync=""
first=""
for commit in $commits; do
  short=$(printf '%s' "$commit" | cut -c1-12)
  fixture="$state/fixtures/$short"
  rm -rf "$fixture"
  mkdir -p "$fixture"
  git -C "$repo" archive "$commit" | tar -x -C "$fixture"
  [ -f "$fixture/Cargo.lock" ] || fail "commit $commit holds no Cargo.lock; the registry is vendored from the lockfile"
  if [ -z "$first" ]; then
    first=$fixture
  else
    sync="$sync --sync $fixture/Cargo.toml"
  fi
done

printf 'build.sh: vendoring the registry for %s\n' "$(printf '%s' "$commits" | wc -w) commit(s)" >&2
# cargo vendor prints a configuration snippet on standard output and one line
# per crate on standard error; the image carries cargo-config.toml instead,
# and the crate lines go to a log that a failure names.
# shellcheck disable=SC2086
(cd "$first" && "$cargo" vendor --locked --versioned-dirs $sync "$context/vendor" > /dev/null 2> "$state/vendor.log") \
  || fail "cargo vendor failed; its output is in $state/vendor.log"

printf 'build.sh: building %s from %s\n' "$tag" "$context" >&2
docker build --build-arg "FOE_ORIGIN=$foe_origin" --build-arg "CODEX_VERSION=$CODEX_VERSION" -t "$tag" "$context"
printf 'build.sh: building %s\n' "$sink_tag" >&2
docker build -t "$sink_tag" "$here/sink"

foe_sha256=$(docker run --rm --network none "$tag" cat /opt/foe/sha256)
image_size=$(docker image ls --format '{{.Size}}' "$tag")
rust_version=$(docker run --rm --network none "$tag" head -1 /opt/rust/version)

# The build volume, warmed offline for every task that names a listed
# commit, from the fixture tree of that commit, with the toolchain and the
# registry the image holds, under BUILD_MOUNT/TASK/target as the task's
# grade script builds. The first task of a commit compiles its dependencies;
# a later task of the same commit starts from a copy of that directory, and
# cargo compiles whatever the copy does not serve, so the copy shortens the
# warm without being relied on.
warmed=""
if [ "$warm" = yes ]; then
  docker volume create "$BUILD_VOLUME" > /dev/null
  seeds=""
  while read -r task commit; do
    [ -n "$task" ] || continue
    case " $commits " in
      *" $commit "*) ;;
      *) continue ;;
    esac
    short=$(printf '%s' "$commit" | cut -c1-12)
    fixture="$state/fixtures/$short"
    seed=$(printf '%s\n' "$seeds" | awk -v commit="$commit" '$1 == commit { print $2 }')
    prepare=":"
    if [ -n "$seed" ]; then
      prepare="[ -d $BUILD_MOUNT/$task/target ] || { mkdir -p $BUILD_MOUNT/$task && cp -a $BUILD_MOUNT/$seed/target $BUILD_MOUNT/$task/target; }"
    fi
    printf 'build.sh: warming %s/%s/target from commit %s\n' "$BUILD_VOLUME" "$task" "$commit" >&2
    docker run --rm --network none \
      -v "$BUILD_VOLUME:$BUILD_MOUNT" \
      -v "$fixture:/work/fixture:ro" \
      "$tag" sh -c "$prepare && cd /work/fixture && cargo test --workspace --no-run --locked --target-dir $BUILD_MOUNT/$task/target && cargo clippy --workspace --locked --target-dir $BUILD_MOUNT/$task/target"
    [ -n "$seed" ] || seeds="$seeds
$commit $task"
    warmed="$warmed $task"
  done <<TASKS
$resolved_task_commits
TASKS
fi

cat <<RECIPE

image $tag
  size            $image_size
  foe             $foe_origin, sha256 $foe_sha256 (/opt/foe/sha256, /opt/foe/origin)
  codex           $codex_version (/opt/codex/version)
  rust            $rust_version (/opt/rust/version)
  registry        /usr/share/cargo/vendor, selected by /.cargo/config.toml, offline
  fixture commits$commits
  build volume    $BUILD_VOLUME at $BUILD_MOUNT, warm for${warmed:- no task (--no-warm)}
sink image $sink_tag

One attempt runs in four steps. The network is created once; the sink
runs while attempts run; the attempt container mounts the checkout, the
run document, and an output directory.

1. The network, whose gateway address the host never takes:

  docker network create --subnet $SUBNET --gateway $GATEWAY \\
    -o com.docker.network.bridge.inhibit_ipv4=true $NETWORK

2. The sink, which takes the gateway address and records every flow:

  docker run -d --name foe-cross-harness-sink --cap-add NET_ADMIN \\
    --network $NETWORK --ip $SINK_ADDRESS $sink_tag \\
    --hold $GATEWAY/${SUBNET#*/} --interface eth0 --log /var/log/sink/connections.jsonl

3. The attempt. The run document names container paths: tasks under
   /work/foe/evals/cross_harness/tasks, harnesses.foe /opt/foe/foe,
   harnesses.codex /opt/codex/codex, harnesses.credential /work/auth.json,
   and out /work/out. It omits tool_roots, because the toolchain sits under
   the built-in execute roots, and a document without tool_roots runs the
   foe-as-shipped arm on every task; a document written for a host run
   names host tool roots, which the runner refuses in the container. The
   seccomp and AppArmor options let bubblewrap create its user namespace;
   every capability is dropped; DNS goes to the sink.

  docker run --rm --name foe-cross-harness-attempt \\
    --security-opt seccomp=unconfined --security-opt apparmor=unconfined \\
    --cap-drop ALL --network $NETWORK --dns $SINK_ADDRESS \\
    -v $repo:/work/foe:ro \\
    -v RUN_DOCUMENT.json:/work/run.json:ro \\
    -v OUT_DIR:/work/out \\
    -v CREDENTIAL:/work/auth.json:ro \\
    -v $BUILD_VOLUME:$BUILD_MOUNT \\
    $tag /usr/bin/python3 /work/foe/evals/cross_harness/run.py /work/run.json --confirm-spend

   A model endpoint is reachable only as a container on the network: attach
   it with \`docker network connect $NETWORK CONTAINER\` and name it in the
   document's model.base_url. Every other destination ends at the sink.

4. The sink's log, collected after the attempt:

  docker cp foe-cross-harness-sink:/var/log/sink/connections.jsonl OUT_DIR/egress.jsonl
RECIPE
