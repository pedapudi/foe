# The container environment

An attempt of the model-backed families runs in a container built from
`Dockerfile` in this directory, on a network whose only exit is a sink that
records every connection. `build.sh` builds the two images, warms one cargo
target volume per fixture commit, and prints the four commands that run one
attempt. `environment_test.py` checks the definition files without docker.

## What the attempt image holds

| path | content |
|---|---|
| base | `ubuntu:24.04`, pinned by digest in the `BASE_IMAGE` argument; its glibc 2.39 is the one the debug foe binary is linked against, and its `python3` is 3.12 |
| `/opt/foe/foe` | the foe binary `build.sh` copied in: `bazel-bin/crates/cli/foe-portable`, the static build, when it exists, and `target/debug/foe` otherwise; `/opt/foe/origin` names which and `/opt/foe/sha256` holds the digest; `/usr/local/bin/foe` is a hard link to the same file, so the name works inside a foe sandbox, which denies a symbolic link into `/opt` |
| `/opt/codex/codex` | the Codex binary copied from the host, whose `--version` output is in `/opt/codex/version` and must read `codex-cli 0.153.4`; `/usr/local/bin/codex` is a hard link to the same file |
| `/usr/local/bin`, `/usr/local/lib`, `/usr/local/libexec` | the Rust toolchain that the repository's `rust-toolchain.toml` names, with its rustfmt and clippy components, installed in a build stage by the rustup installer pinned by version and checksum and copied out as a prefix; `/opt/rust/version` records the versions |
| `/usr/share/cargo/vendor` | the crates.io packages that every fixture commit's `Cargo.lock` names, produced by `cargo vendor` in `build.sh` |
| `/usr/share/cargo/home/config.toml`, reached as `/.cargo/config.toml` | the cargo configuration that replaces crates.io with the vendored directory and sets cargo offline, so `cargo test` and `cargo clippy` open no connection |
| `/home/attempt/.local/state/foe/cross-harness/build` | the build directory of every grade script of a task on foe's tree, which builds under `~/.local/state/foe/cross-harness/build/TASK/target` of the account it runs as; owned by the attempt user, and the mount point of the warm build volume |
| packages | `git` regenerates a task workspace from its recorded commit; `bubblewrap` is the sandbox Codex uses on Linux; `gcc` and `libc6-dev` are the linker rustc calls; `ca-certificates` lets a harness verify an endpoint |

The toolchain lives under `/usr/local` because the built-in foe documents
grant execution under `/bin`, `/usr/bin`, and `/usr/local/bin` and nothing
else, so an attempt of the foe-as-shipped arm, which takes no tool roots,
can run cargo, rustc, and clippy. The vendored registry and its
configuration live under `/usr/share` because every foe sandbox reads the
system directories `/etc`, `/usr/share`, `/proc`, and `/sys`; a cargo
started inside the sandbox reads the configuration through the `/.cargo`
link, which the kernel resolves to a path under `/usr/share`.

The image sets no environment variable. The base image's search path
already covers `/usr/local/bin`, every path above is fixed, and the rustup
installer's two variables, `RUSTUP_HOME` and `CARGO_HOME`, are set on the
build-stage commands that run rustup and on nothing else. An attempt runs
as the unprivileged user `attempt`, uid 1000, so that bubblewrap creates an
unprivileged user namespace, Landlock confines an ordinary process, and a
host directory owned by the first host user is writable when mounted.

## The network and the sink

The network `foe-cross-harness` is a bridge with the subnet `10.219.0.0/24`
and the gateway address `10.219.0.254`, created with the driver option
`com.docker.network.bridge.inhibit_ipv4=true`. With that option the host
never takes the gateway address, so nothing on the host answers for it,
while every container on the network still receives a default route
through it. The sink container, started with `NET_ADMIN` and the fixed
address `10.219.0.2`, adds the gateway address to its own interface. From
then on the default route of every attempt container ends at the sink.

The sink runs `sink/recorder.py`. Two netfilter rules redirect every TCP
and every UDP flow that reaches the sink, whatever its destination port, to
the two sockets the recorder serves. Each TCP connection is recorded with
its source, the destination it was sent to, and the host name its first
bytes ask for, read from an HTTP `Host` header or a TLS server name, and
then closed, so a harness meets a refused request rather than a hang. The
attempt container names the sink as its DNS server; a DNS query the sink
receives is recorded with the name it asks for and answered with the
gateway address, so a harness resolving a model endpoint by name connects
to the sink, and that connection is recorded with the name. The log,
`/var/log/sink/connections.jsonl` in the sink container, holds one JSON
object per line with a UTC `time`; the fourth command of the printed
recipe copies it beside the attempt's output.

The attempt container runs with every capability dropped. Without
`NET_RAW` it cannot address a frame to any interface other than the one
its route names, and without `NET_ADMIN` it cannot change the route. The
two options `--security-opt seccomp=unconfined` and
`--security-opt apparmor=unconfined` are what bubblewrap needs to create
its user namespace under Docker's default profiles; Landlock needs neither.

A model endpoint is reachable only as a container attached to the same
network with `docker network connect`; the run document names it in
`model.base_url` on the compatible route. Every other destination, the
subscription route's endpoint included, ends at the sink and appears in
its log.

## Running one attempt

`build.sh` prints the four commands with the values it built: create the
network, start the sink, run the attempt, and copy the sink's log. The
attempt command mounts the foe checkout read-only at `/work/foe`, the run
document read-only at `/work/run.json`, an output directory at `/work/out`,
a Codex credential read-only at `/work/auth.json`, and one warm target
volume at `/var/cache/foe-cross-harness/target`, and runs
`/usr/bin/python3 /work/foe/evals/cross_harness/run.py /work/run.json --confirm-spend`.
The run document names container paths: `tasks` under
`/work/foe/evals/cross_harness/tasks`, `harnesses.foe` as `/opt/foe/foe`,
`harnesses.codex` as `/opt/codex/codex`, `harnesses.credential` as
`/work/auth.json`, and `out` as `/work/out`. It omits `tool_roots`, because
the toolchain sits under the built-in execute roots; the runner records
the foe-as-shipped arm as not applicable to a task when the run document
or the task names tool roots, so inside the container a document without
`tool_roots` runs that arm on every task. A document written for a host
run, such as `runs/autonomy-pilot.json`, names host tool roots under
`tool_roots`; the runner refuses a root that does not exist in the
container, so such a document is not usable there as it is.

The warm build volume is named `foe-cross-harness-build` and is mounted at
the build directory of the attempt user, where every grade script of a task
on foe's tree builds under `TASK/target`. `build.sh` fills it in that
layout: for every task whose `task.json` names a fixture commit, it runs
`cargo test --workspace --no-run` and `cargo clippy --workspace` on the
tree `git archive` produces for that commit, offline, in the attempt image,
with `--target-dir` set to the task's directory in the volume. The first
task of a commit compiles the dependencies; a later task of the same
commit starts from a copy of that directory, and cargo compiles whatever
the copy does not serve. A grade in the container then finds the
dependencies of its task compiled and builds the workspace crates alone.

## A host run without the container

On a host the toolchain lives under `~/.rustup`, outside every built-in
execute root, so a run document for the host names its installation under
`tool_roots` and the runner records the foe-as-shipped arm as not
applicable to every task; the document arms and the Codex arms run. The
built-in documents grant execution under `/bin`, `/usr/bin`, and
`/usr/local/bin` alone, so the foe-as-shipped arm runs a task that needs
the toolchain only where the toolchain sits under one of those roots, as
it does in the container.
