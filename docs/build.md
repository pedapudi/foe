# Build and install

foe builds as one Rust binary with the browser viewer embedded. Building and
installing the binary requires neither Node.js nor a JavaScript package
manager. The compiled viewer assets are checked into `view/dist`, and the
font files the viewer serves itself are checked into `view/fonts`; the
build script of `crates/view` embeds both. TypeScript development commands
are documented in [`view/README.md`](../view/README.md).

## Install from source

The installer requires Bazel or Bazelisk. A downloaded installation also
requires `tar` and one source client. An authenticated GitHub CLI can read a
private source archive. `curl` and `wget` can read a publicly accessible
archive. The script builds `//:foe` with the pinned Rust toolchain, verifies
the resulting binary, and installs it under `~/.local/bin` by default.

```sh
gh api -H "Accept: application/vnd.github.raw+json" \
  repos/pedapudi/foe/contents/install.sh | sh
```

Running the checked-out script builds the current checkout, including local
changes:

```sh
./install.sh
```

The destination option applies to every invocation. The source-reference
option applies when the script downloads a source archive:

```sh
./install.sh --install-dir /absolute/directory
gh api -H "Accept: application/vnd.github.raw+json" \
  repos/pedapudi/foe/contents/install.sh | sh -s -- --ref GIT-REFERENCE
```

`--ref` accepts a branch, tag, or commit understood by the GitHub source
archive endpoint. `--install-dir` must be absolute. The installer copies the
new binary through a temporary file and renames it into place. An existing
installation remains intact until the build and verification succeed.

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
browser suite in `view/`, and the Python suite in `python/`.

Repository-owned Rust test helpers create a unique working directory for each
test invocation. A successful test removes its directory when its owner leaves
scope, and a cleanup error fails the test. A panicking test retains the
directory and prints its path so that its files can be inspected without a
second panic hiding the original failure. Runnable examples retain their
directories under `target/` because the final output gives the operator a
command for viewing the recorded episode.
