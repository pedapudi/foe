# The attempt image of the cross-harness evaluation. environment.md states
# what the image holds and how an attempt runs in it; build.sh assembles the
# build context this file expects: `foe`, `codex`, `rust-toolchain.toml`,
# `cargo-config.toml`, and `vendor/`.
#
# Every version is pinned here or in a file the context carries: the base
# image by digest, the rustup installer by version and checksum, the Rust
# toolchain by rust-toolchain.toml, the foe and Codex binaries by the bytes
# build.sh copied in. No ENV instruction appears in this file: the tools in
# the image are found through the search path the base image already
# carries, and every path the runtime needs is a fixed absolute path.

ARG BASE_IMAGE=ubuntu:24.04@sha256:224a1869083a311ef3f13648a154ba79832fbef6364d31493642ca03082da254

# ---------------------------------------------------------------------------
# Stage one installs the pinned Rust toolchain with rustup and lays it out as
# a prefix. The rustup installer and the rustup proxies locate their files
# through RUSTUP_HOME and CARGO_HOME alone, which is why the two variables
# appear on the commands that run them and nowhere else in this file.
FROM ${BASE_IMAGE} AS toolchain
ARG RUSTUP_VERSION=1.29.1
ARG RUSTUP_INIT_SHA256=dda7234360b7f578ca8b0ddcb80145646fa61a67c1720a5abc7051b35c9fcb71
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*
COPY rust-toolchain.toml /build/rust-toolchain.toml
RUN curl -fsSLo /build/rustup-init "https://static.rust-lang.org/rustup/archive/${RUSTUP_VERSION}/x86_64-unknown-linux-gnu/rustup-init" \
 && echo "${RUSTUP_INIT_SHA256}  /build/rustup-init" | sha256sum -c - \
 && chmod 755 /build/rustup-init \
 && RUSTUP_HOME=/build/rustup CARGO_HOME=/build/cargo /build/rustup-init -y --no-modify-path --profile minimal --default-toolchain none \
 && cd /build \
 && RUSTUP_HOME=/build/rustup CARGO_HOME=/build/cargo /build/cargo/bin/rustup toolchain install \
 && rustc_path=$(RUSTUP_HOME=/build/rustup CARGO_HOME=/build/cargo /build/cargo/bin/rustup which rustc) \
 && toolchain=${rustc_path%/bin/rustc} \
 && mkdir -p /build/prefix \
 && cp -a "${toolchain}/bin" "${toolchain}/lib" "${toolchain}/libexec" /build/prefix/ \
 && "${toolchain}/bin/rustc" --version > /build/prefix/rust-version \
 && "${toolchain}/bin/cargo" --version >> /build/prefix/rust-version \
 && "${toolchain}/bin/cargo-clippy" --version >> /build/prefix/rust-version \
 && "${toolchain}/bin/rustfmt" --version >> /build/prefix/rust-version

# ---------------------------------------------------------------------------
# Stage two is the attempt image.
FROM ${BASE_IMAGE}
ARG FOE_ORIGIN
ARG CODEX_VERSION=0.153.4

# gcc and libc6-dev provide the linker rustc calls; bubblewrap is the
# sandbox Codex uses on Linux; git regenerates a task workspace from a
# recorded commit; ca-certificates lets a harness verify a model endpoint's
# certificate. The Python check pins the interpreter the runner and every
# grade script name as /usr/bin/python3.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 git bubblewrap gcc libc6-dev ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && /usr/bin/python3 --version | grep -q '^Python 3\.12\.'

# The toolchain lives under /usr/local so that the execute roots the foe
# documents carry, /bin, /usr/bin, and /usr/local/bin, cover cargo, rustc,
# and clippy without tool roots. rustc finds its libraries and its sysroot
# through RUNPATH $ORIGIN/../lib, so lib/ and libexec/ sit beside bin/.
COPY --from=toolchain /build/prefix/bin/ /usr/local/bin/
COPY --from=toolchain /build/prefix/lib/ /usr/local/lib/
COPY --from=toolchain /build/prefix/libexec/ /usr/local/libexec/
COPY --from=toolchain /build/prefix/rust-version /opt/rust/version

# The vendored registry and the cargo configuration that selects it live
# under /usr/share, which every foe sandbox reads. Cargo reads
# /.cargo/config.toml for a workspace anywhere in the filesystem, so the
# symlink makes every cargo invocation offline without an environment
# variable or a per-workspace file.
COPY vendor/ /usr/share/cargo/vendor/
COPY cargo-config.toml /usr/share/cargo/home/config.toml
RUN ln -s /usr/share/cargo/home /.cargo

# The foe binary, its digest, and which build it came from. The name under
# /usr/local/bin is a hard link, because a foe sandbox resolves a symbolic
# link at access time and denies a target under /opt, which no built-in
# root covers.
COPY foe /opt/foe/foe
RUN chmod 755 /opt/foe/foe \
 && sha256sum /opt/foe/foe | cut -d' ' -f1 > /opt/foe/sha256 \
 && printf '%s\n' "${FOE_ORIGIN}" > /opt/foe/origin \
 && ln /opt/foe/foe /usr/local/bin/foe

# The Codex binary and the version it reports, which must be the pinned one;
# its name under /usr/local/bin is a hard link for the same reason.
COPY codex /opt/codex/codex
RUN chmod 755 /opt/codex/codex \
 && /opt/codex/codex --version > /opt/codex/version \
 && grep -qx "codex-cli ${CODEX_VERSION}" /opt/codex/version \
 && ln /opt/codex/codex /usr/local/bin/codex

# An attempt runs as an unprivileged user so that bubblewrap uses an
# unprivileged user namespace and Landlock confines an ordinary process. The
# user takes uid 1000 so that a host directory owned by the first host user is
# writable when mounted; the base image's own uid 1000 account is removed to
# free the number. The build directory is where every grade script of a
# task on foe's tree builds, ~/.local/state/foe/cross-harness/build of the
# account it runs as; build.sh mounts the warm build volume there, and a
# volume mounted at a directory the image holds inherits its ownership.
RUN userdel -r ubuntu \
 && useradd --uid 1000 --user-group --create-home --shell /bin/bash attempt \
 && mkdir -p /home/attempt/.local/state/foe/cross-harness/build /work \
 && chown -R attempt:attempt /home/attempt/.local /work

# A smoke check of every tool the attempt and the graders call.
RUN /usr/local/bin/cargo --version \
 && /usr/local/bin/rustc --version \
 && /usr/local/bin/cargo clippy --version \
 && /usr/local/bin/rustfmt --version \
 && /usr/bin/git --version \
 && /usr/bin/bwrap --version \
 && /usr/bin/python3 -c 'import sys; assert sys.version_info[:2] == (3, 12), sys.version'

USER attempt
WORKDIR /work
