# Sandbox

An episode's grants name what it may reach: directories to read, directories
to write, executables to run, and child programs to start. On Linux, the
runtime compiles those grants into a Landlock ruleset and applies it to the
episode process and to every process the episode starts. Landlock is a kernel
security module that lets an unprivileged process restrict itself; once
applied, a restriction cannot be lifted, and every process created afterwards
inherits it. This document specifies what the runtime compiles, what each
kernel version enforces, and what is outside the sandbox.

The implementation is `crates/core/src/sandbox.rs`. The executable runner in
`crates/core/src/exec.rs` applies the narrowing described under
[Executables](#executables).

## What is compiled

A policy is the list of what one process may reach. The policy of an episode
is derived from its configuration and its log directory; nothing else is
declared.

| source | access granted |
|---|---|
| each `grants.read` directory | read files, list directories |
| each `grants.write` directory | write, truncate, create, remove, rename, and link files and directories; no read |
| each `tool_defs` entry's `exec` file | execute and read that file |
| the running `foe` binary, when `grants.spawn` is not empty | execute and read that file, so the episode can start children |
| the credential file the `model` block resolves to, when present | read that file, so a child episode can read the credential after inheriting this domain |
| the episode's own log directory | read and write |
| the loader directories `/lib`, `/lib64`, `/usr/lib`, `/usr/lib64`, `/usr/libexec`, `/usr/local/lib`, `/bin`, `/usr/bin`, `/usr/local/bin` | read and execute |
| the system directories `/etc`, `/usr/share`, `/proc`, `/sys` | read |
| the resolved target of `/etc/resolv.conf`, when the process may connect | read that file |
| the device files `/dev/null`, `/dev/zero`, `/dev/random`, `/dev/urandom`, `/dev/tty` | read and write |
| TCP | bind: listed ports only; connect: all ports or none |

The loader directories let a process start: the kernel executes the dynamic
loader from `/lib64`, the loader maps shared libraries from `/usr/lib`, and a
script names its interpreter in `/usr/bin`.
A file under a read root is readable and is never executable by that grant
alone. A file outside every listed path, such as a script inside the
project, runs only when a `tool_defs` entry names it.

The resolver configuration may be a symbolic link into `/run`, which the
system read directories do not cover. Before applying a policy, foe resolves
`/etc/resolv.conf` and grants read access to the target file, so that a
process able to open a connection can turn a provider host name into an
address without gaining the target file's surrounding runtime directory. A
process that may not connect receives no such grant: an episode that
declares no `model` block, and an executable whose tool definition does not
ask for the network, read no resolver file.

A write root grants no read access. A configuration that writes to a
directory it also reads lists that directory under both grants, or lists a
parent under `read`.

Paths that do not exist when the ruleset is compiled are skipped. A path
that cannot be opened cannot be reached, so skipping it changes nothing.
Symbolic links are resolved by the kernel at access time, so a link inside a
root that points outside every root is denied.

## Kernel tiers

The kernel reports a Landlock version, called the ABI, at startup. Each
version adds restrictions; the runtime uses every feature up to version 7
and records the version it used. A newer kernel is used at version 7 and
recorded as 7. Version 0 means Landlock is absent or disabled.

| ABI | Linux | what the runtime enforces from this version |
|---|---|---|
| 1 | 5.13 | filesystem access by path: read, write, create, remove, and execute, as listed above |
| 2 | 5.19 | renaming and linking across directories is part of write access |
| 3 | 6.2 | truncation is part of write access |
| 4 | 6.7 | TCP: an executable with `network: false` can neither bind nor connect; an episode that does not hold the model transport cannot connect |
| 5 | 6.10 | device control calls (`ioctl`) on device files are part of write access |
| 6 | 6.12 | a sandboxed process cannot signal a process outside its sandbox and cannot connect to abstract Unix sockets created outside it |
| 7 | 6.15 | the kernel logs each denied access to the audit subsystem, including denials inside executables the episode starts |

Below version 4 the network is open for every process. Below version 6 a
tool can signal the episode process. Below version 7 a denial leaves no
kernel record; the denied process sees a permission error and nothing else.

Rulesets nest. Every process the episode starts keeps the episode's
ruleset and receives a further one, so an executable or child can reach
less than the episode and never more. The kernel allows sixteen nested
rulesets; an episode tree of depth sixteen is the practical limit.

## The episode process

The episode process applies its own policy to itself at startup, after it
has read its configuration and before it starts any other thread. Landlock
restricts the calling thread and every thread or process created
afterwards; a thread created before the ruleset is applied stays
unrestricted. The runtime therefore applies the ruleset from the main
thread before the asynchronous runtime exists.

Part of the policy is known only after work that the policy itself would
forbid: the port the viewer listens on, and the credential file the
resolved model transport reads. `crates/core/src/confine.rs` holds that
assembly. A value of type `Unconfined` owns the policy while the process is
still unrestricted and is the only source of a mutable reference to it.
Entering confinement consumes that value and returns a `Confined`, which
lends the policy for reading alone. A change to the policy after
enforcement therefore does not compile, and a second enforcement of the
same policy does not compile.

Those two properties concern the policy rather than the process. A file
read, a socket bind, or a process start written after confinement is
entered still compiles and still runs; the kernel refuses it when the
policy does not allow it, which is the reason for enforcing at all. The
types settle that the policy the kernel receives is final and that the
kernel receives it once. They do not settle that confinement is entered
from the main thread before any other thread exists, which the runtime does
and no type expresses.

The episode keeps:

- read on its read roots, write on its write roots;
- execute on every `tool_defs` executable, and on its own binary when it
  may spawn children;
- read on the key file named by its `model` block, which its children need;
- read and write on its own log directory, which holds its children's
  directories and its spill files;
- the loader, system, and device paths;
- outbound TCP when the configuration has a `model` block, because the
  episode then calls the provider itself;
- inbound TCP on the viewer's port alone, when the episode serves a viewer;
  the command line adds that one port to the policy before applying it;
- no outbound TCP when a host process holds the transport.

## Executables

A configured executable runs under the episode's ruleset narrowed once
more. The narrowed policy keeps the read roots, the write roots, the
loader, system, and device paths, and execute on the one file named by the
tool definition. It drops the log directory, the key file, execute on every
other executable, and TCP unless the tool definition sets `network: true`.

This crate forbids unsafe code, so the narrowing is applied by a short-lived
thread rather than by a hook between fork and exec. The thread applies the
narrowed ruleset to itself, starts the process, hands the process handle
back, and ends. The process inherits the thread's ruleset before it
executes anything, which is the same point at which a hook would act.

Each executable also runs in its own process group. When its timeout
elapses, or the episode is cancelled, the whole group receives `SIGTERM`
and, two seconds later, `SIGKILL`. When the executable exits on its own,
whatever remains of its group receives `SIGKILL`. A process that moved
itself into a new process group or session is outside this rule; the
kernel ruleset still binds it.

## Children

A child episode is a further `foe` process. The parent starts it without
narrowing, because the child applies its own policy to itself at startup.
Two independent rules keep that policy inside the parent's policy.

Resolving the configuration checks containment before any process starts:
each child program's read roots must lie within its parent program's read
roots and its write roots within its parent's write roots, applied at every
level of `programs`. A document that fails the check is refused with the
dotted key of the offending root, and no episode begins.

The kernel enforces the same containment whatever the document says. The
parent restricts its main thread before it starts any other thread, and a
Landlock domain passes to every thread and process created afterwards, so
the child inherits the parent's domain before it executes. The child's own
ruleset nests inside that one, and the child's reach is the intersection.

## Modes

`sandbox.mode` in the configuration decides what happens at startup.

| mode | behavior |
|---|---|
| `best-effort` | applies every feature the kernel offers up to version 7 and records the version in `episode/start.sandbox.landlock_abi`; records 0 and applies nothing when Landlock is absent |
| `required` | refuses to start when the kernel offers no Landlock; otherwise as `best-effort` |
| `off` | applies nothing and records 0 |

`best-effort` records a number rather than a list of features, because the
list is a function of the number, given in the table above. A reader of the
log who sees `landlock_abi: 4` knows that the filesystem and TCP were
enforced and that signals and audit logging were not.

## Denied accesses

The log format defines a `sandbox/denied` event for an access the kernel
refused. The runtime does not yet emit it. From version 7 the kernel writes
one audit record per denial, but an unprivileged process cannot read them:
the audit socket requires the `CAP_AUDIT_READ` capability, and the audit
daemon's log file is readable by the superuser alone. The runtime enables
the kernel's logging of denials inside executables, so a machine that runs
the audit daemon has the records; the runtime itself records nothing. The
condition for emitting the event is a kernel interface that reports
denials to the restricting process without privilege and without a daemon.

## What is not enforced

- The host process. The process that launched `foe` holds the model
  credentials and the host tools, and the runtime never restricts it.
- The network of the episode process when it holds the transport. An
  episode with a `model` block connects to the provider itself, and
  Landlock has no rule that names a remote host, so outbound TCP is open
  for that process and for nothing it starts without `network: true`.
- Anything on a kernel below the tier that enforces it, as listed in the
  table. A log records the tier, so a reader knows what held.
- Resources other than files, TCP, signals, and abstract sockets. Memory,
  processor time, and process count are bounded by the budget alone.
