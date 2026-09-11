# Build and install

foe builds as one Rust binary with the browser viewer embedded. Building and
installing the binary requires neither Node.js nor a JavaScript package
manager. The compiled viewer assets are checked into `view/dist`, and the
font files the viewer serves itself are checked into `view/fonts`; the
build script of `crates/view` embeds both. TypeScript development commands
are documented in [`view/README.md`](../view/README.md).

## Install

The installer downloads the published x86-64 Linux binary. It needs `curl` or
`wget` and nothing else: the binary is statically linked, so it runs on any
x86-64 Linux rather than only where its builder's C library is new enough.

```sh
curl -fsSL foe.sh/install.sh | sh
```

The binary is checked against the SHA-256 published beside it, and refused
when the two differ. When `sha256sum` is absent the installer says the
download went unverified rather than pretending otherwise.

The installer reads what the machine is before it downloads anything. It
refuses a system that is not Linux, because foe's grants are Landlock rules
and its process boundary is a cgroup, and neither exists elsewhere. It
refuses an architecture the published binary is not built for, and names
`--from-source` as what builds one that is. It proceeds on a kernel older
than 5.13 and says what that costs: Landlock arrived there, so an older
kernel runs foe and confines nothing, `best-effort` records that it enforced
nothing, and `--sandbox required` refuses to start.

### Install from source

`--from-source` builds instead of downloading, and `--ref` implies it. The
source path requires Bazel or Bazelisk, and a downloaded archive also
requires `tar` and one client: an authenticated GitHub CLI reads a private
archive, and `curl` or `wget` reads a public one. It builds `//:foe` with the
pinned Rust toolchain.

```sh
./install.sh                      # the checked-out script builds its checkout
./install.sh --from-source        # any invocation, from the current source
curl -fsSL foe.sh/install.sh | sh -s -- --ref GIT-REFERENCE
```

`--ref` accepts a branch, tag, or commit understood by the GitHub source
archive endpoint. `--install-dir` must be absolute and applies to every
invocation. Either path runs `foe plan` against the new binary before
installing it, which resolves a document and builds the tool registry
without a credential, a network, or an episode. The installer copies the
binary through a temporary file and renames it into place, so an existing
installation stands until the new one has answered for itself.

### Cut a release

There is no release workflow. `scripts/release.sh` runs on a maintainer's
machine and uploads what it built, so a release costs no
continuous-integration minutes.

```sh
scripts/release.sh 0.2.0
```

`.cargo/config.toml` packs the binary's relative relocations into a bitmap
for this target, which is 300 KB of the published size and leaves the
executable position-independent.

It refuses a version that disagrees with `Cargo.toml`, a working tree with
changes, and a machine without the musl target or a musl C compiler
(`rustup target add x86_64-unknown-linux-musl` and `apt install musl-tools`).
It selects the checkout commit and requires that commit's most recent
`ci.yml` run to have succeeded. An archive of that commit supplies the
build source. The staged binary runs `foe plan`, the host protocol example,
and the team example before publication. Its checksum is checked again
after those executions.

An existing remote release tag must name the selected commit, including
when the tag is annotated. Otherwise the script creates the tag without
overwriting an existing tag. The release uses that verified tag and carries
`foe-x86_64-linux` and its SHA-256. `--draft` withholds publication.

## Pin the binary and Python package to one commit

Build both components from one clean checkout at a full commit hash. Branches
and tags can move. Package version strings do not identify the installed
source revision. The configuration, log format, and runtime protocol checks
are specified in [sdk.md](sdk.md#the-versions-a-pair-must-agree-on).

The following commands require Git, Bazel, and uv. Replace the commit
placeholder with a full forty-character commit hash. The source and install
directories must not already exist.

```sh
set -eu
foe_commit=FULL_FORTY_CHARACTER_COMMIT_HASH
foe_source_dir="$PWD/foe-source"
foe_pair_dir="$PWD/foe-pair"
test ! -e "$foe_source_dir"
test ! -e "$foe_pair_dir"
git clone https://github.com/pedapudi/foe.git "$foe_source_dir"
git -C "$foe_source_dir" checkout --detach "$foe_commit"
test "$(git -C "$foe_source_dir" rev-parse HEAD)" = "$foe_commit"
test -z "$(git -C "$foe_source_dir" status --porcelain)"
mkdir -p "$foe_pair_dir/bin"
cd "$foe_source_dir"
bazel build --lockfile_mode=error //:foe
install -m 755 bazel-bin/crates/cli/foe "$foe_pair_dir/bin/foe"
uv venv "$foe_pair_dir/venv"
uv pip install --python "$foe_pair_dir/venv/bin/python" ./python
printf '%s\n' "$foe_commit" > "$foe_pair_dir/source-commit"
"$foe_pair_dir/bin/foe" schema >/dev/null
"$foe_pair_dir/venv/bin/python" -c 'import foe; print(foe.__version__)'
```

Run the host application with that virtual environment and pass the absolute
path of `foe-pair/bin/foe` as its `binary` argument. Retain `source-commit`
with the installation. The episode log records the binary's content hash in
`episode/start.runtime.build`. That hash identifies the executable bytes;
the retained commit identifies the shared source revision.

Pinning the source does not guarantee byte-identical builds on different
platforms. Optional Python dependencies and host application dependencies
need their own lockfile. A local invocation of `install.sh` builds its local
checkout; its `--ref` option only selects a downloaded source archive.

## Build with Bazel

Bazel is the primary build interface. The repository declares external Bazel
dependencies through Bazel's module system (Bzlmod). The Rust dependency
generator (Crate Universe) reads the Cargo manifests and lockfile to construct
the external crate graph.

```sh
bazel build //:foe
```

The public target `//:foe` aliases the native Rust binary at
`//crates/cli:foe`. The optimized output is `bazel-bin/crates/cli/foe`.

`.bazelversion` pins Bazel 9.2.0. `MODULE.bazel` pins the Rust toolchain and
the Bazel rules used for Rust and shell targets. `.bazelrc` applies the
repository's size-oriented release settings to every Bazel build. The first
build downloads those dependencies and the crates named by `Cargo.lock`.
Later builds use the Bazel repository and action caches.

After changing a Cargo manifest or `Cargo.lock`, run
`bazel mod deps --lockfile_mode=update` and commit the resulting
`MODULE.bazel.lock`. Continuous integration uses `--lockfile_mode=error`
and refuses a module lockfile that does not describe those inputs.

## Run the end-to-end demos

Three executable targets demonstrate workflows, kernel-enforced sandboxing,
and foe extending a disposable copy of its own source:

```sh
bazel run //examples/workflow
bazel run //examples/sandbox
bazel run //examples/self-extension
```

Each target builds the foe binary, creates a disposable project under
`target/`, runs one complete episode, checks the result, and prints a viewer
command. The workflow and self-extension targets use a deterministic host
model backend. The sandbox target requires Linux with Landlock support. Every
target requires `/usr/bin/python3`. The demo episodes need no model credential
and make no network requests.

Bazel can run all three demonstrations as tests. Test runs place their
temporary projects under Bazel's test directory:

```sh
bazel test //examples/...
```

These three are the only examples with Bazel targets. `examples/` holds
thirteen runnable programs in all, each started by its own `run.sh` or
`run.py`; [`examples/README.md`](../examples/README.md) indexes them.

## Use Cargo for Rust development

The Cargo workspace remains available for Rust-specific development commands:

```sh
cargo build --locked --release -p foe
cargo test --workspace
cargo clippy --all-targets -- -D warnings
cargo fmt --all --check
```

The Cargo release binary is `target/release/foe`.

## The two tiers of the test suite

`cargo test --workspace` is the fast tier: about six seconds on a warm tree.
No test in it waits on a real clock. Where a rule is about elapsed time, the
test runs on tokio's virtual clock, which advances to each sleep's deadline
as soon as the episode is idle, so the delay is measured rather than served.
Two tests still start real child processes to prove that a timeout kills a
whole process group, and one drives a real loopback server; each costs about
a second.

`scripts/examples.sh` is the slow tier: about fifteen seconds, and it needs a
built binary. The examples show an operator what a real run looks like, so
they wait real time where the runtime does. Eight of the fifteen seconds are
the recovery-exhausted example waiting out the retry backoff.

```sh
cargo build -p foe
scripts/examples.sh target/debug/foe
```

Continuous integration runs both tiers, along with `scripts/loc.sh`, the
browser bundle build and test suite in `view/`, and the Python suite in
`python/`.

## Integrate a change

Work happens on a branch. A branch reaches `main` by being rebased onto
`main` or by being merged into `main` through a pull request, and squash
merge is the default for a branch an agent produced. Before a push, every
gate above runs on the tree that will become `main`: `cargo test --workspace`,
`scripts/loc.sh`, `scripts/examples.sh`, the Python suite, and the browser
bundle build and test suite. `main` requires the continuous-integration
checks to pass. A conflict is resolved in an ordinary commit on the branch
that states what the resolution changed, so a merge commit carries nothing
beyond the automatic merge. `AGENTS.md` "Commits" holds the full set of
rules.

Repository-owned Rust test helpers create a unique working directory for each
test invocation. A successful test removes its directory when its owner leaves
scope, and a cleanup error fails the test. A panicking test retains the
directory and prints its path so that its files can be inspected without a
second panic hiding the original failure. Runnable examples retain their
directories under `target/` because the final output gives the operator a
command for viewing the recorded episode.
