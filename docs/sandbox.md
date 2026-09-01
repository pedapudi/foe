# Sandbox

An episode's `grants` object declares permissions to read, write, execute,
start child contracts, and bind TCP ports. The runtime adds the exact support
permissions required to exercise those grants. The resulting set is called
the resolved permissions.

On Linux, the runtime compiles the resolved permissions into a Landlock
ruleset. It applies that ruleset to the episode and every process the episode
starts. The runtime also places each invocation in a delegated cgroup v2
hierarchy when the host provides one. Landlock restricts access. The cgroup
owns process subtrees so a new session or process group cannot escape cleanup.

The sandbox mode, Landlock ABI, and process-boundary record state what the
host actually enforced. This document specifies the permission resolution and
those enforcement mechanisms.

The implementations are `crates/core/src/sandbox.rs` and
`crates/core/src/process_boundary.rs`. The executable runner in
`crates/core/src/exec.rs` applies the narrowing described under
[Executables](#executables).

## What is compiled

A policy is the resolved permission set for one process. An episode policy is
derived from its execution contract, log directory, and runtime-owned cgroup
paths. The cgroup paths are launch metadata rather than configured grants.

During execution-contract construction, Foe captures each configured
executable's bytes, digest, source path, and invocation name. Every later
invocation uses the captured executable. Replacing, modifying, or deleting the
source cannot change the run.

| source | access granted |
|---|---|
| each `grants.read` directory | read files, list directories |
| each `grants.write` directory | write, truncate, create, remove, rename, and link files and directories; no read |
| each `grants.execute` file or directory in the contract and its reachable descendants | read and execute the file or every file below the directory, including from a tool subprocess |
| each selected configured tool and executable model transport in the contract and its reachable descendants | execute and read the captured executable |
| the running `foe` binary, when a child contract is reachable | execute and read that file, so the episode can start children |
| the shell or Python interpreter required by a selected built-in tool | execute and read that exact file |
| a captured executable's absolute shebang interpreter or ELF dynamic loader | execute and read that exact file |
| each credential file resolved by a reachable `model` block | read that file before confinement and reserve it for a descendant that inherits this domain |
| the episode's own log directory | read and write |
| the library directories `/lib`, `/lib64`, `/usr/lib`, `/usr/lib64`, `/usr/libexec`, `/usr/local/lib` | read |
| the runtime-owned cgroup hierarchy | runtime-only read and write for process ownership and cleanup; configured executables receive no access |
| the parent cgroup's `cgroup.procs`, for a root episode | runtime-only write so the runtime can leave its episode boundary before cleanup |
| `/bin/sh`, when cgroup v2 is delegated | execute and read the exact shell used to place a child or task-lifetime session in its boundary |
| the system directories `/etc`, `/usr/share`, `/proc`, `/sys` | read |
| the resolved target of `/etc/resolv.conf`, when the process may connect | read that file |
| the device files `/dev/null`, `/dev/zero`, `/dev/random`, `/dev/urandom`, `/dev/tty` | read and write |
| each `grants.bind` port | bind TCP on that port, in the episode and in every process it starts |
| TCP | bind: listed ports only; connect: all ports or none |

The runtime reads each selected configured executable and each exact support
executable before confinement. An ELF executable names its dynamic loader in
a `PT_INTERP` program header. A script names its interpreter in the first
line. The policy grants execute access to that exact loader or interpreter.
Library directories remain readable because the loader searches them for
shared objects. They carry no execute permission.

An execute grant on a directory permits executable files below that
directory. The runtime does not enumerate a mutable directory to infer
support files. A script below such a directory must receive its interpreter
through an exact execute grant, a selected configured executable, or a
selected built-in tool.

A shebang must name an absolute interpreter directly. A shebang that names
`/usr/bin/env` is rejected because `env` selects another executable through a
path search. The error asks the contract author to name that interpreter in
the shebang. This rule keeps the executable surface derivable before the
episode starts.

Configured tools and executable model transports run from the captured bytes.
The runtime stores each copy outside every declared write root and keeps its
file descriptor open. Replacing or deleting the configured source path cannot
change the bytes that run. A permission record names the construction-time
source path and content digest so a reader can identify the captured bytes.

A file under a read root is readable and is never executable through that
grant alone. A project script runs when a `tool_defs` entry names the file or
when an explicit execute grant covers it.

The resolver configuration may be a symbolic link into `/run`, which the
system read directories do not cover. Before applying a policy, foe resolves
`/etc/resolv.conf` and grants read access to the target file, so that a
process able to open a connection can turn a provider host name into an
address without gaining the target file's surrounding runtime directory. A
process that may not connect receives no such grant: an episode that
declares no `model` block, and an executable whose tool definition does not
ask for the network, read no resolver file.

A bind grant is inbound only. Outbound TCP is not a grant: it follows the
model transport and each tool definition's `network` field, as stated under
[What is not enforced](#what-is-not-enforced), and widening it is a
separate design.

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

Because a ruleset only narrows, an episode reserves the permissions required
by the reachable contracts below it before it restricts itself. A child's
read, write, and execute roots lie inside its parent's corresponding roots.
Its bind ports lie among its parent's bind ports. Contract resolution checks
that containment.

The ancestor also reserves captured configured executables, executable model
transports, built-in interpreters, and outbound TCP required by reachable
descendants. A declaration is reachable through a `grants.spawn` entry or a
workflow model node. Each descendant applies a narrower policy for its own
reachable subtree.

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
- execute on every explicit execute grant in its reachable contract tree;
- execute on every selected captured executable and required exact
  interpreter in that tree;
- execute on its own binary when a child contract is reachable;
- read on the credential files resolved by its reachable model transports;
- read and write on its own log directory, which holds its children's
  directories and its spill files;
- the loader, system, and device paths;
- outbound TCP when the episode calls a model transport or a reachable
  configured tool declares `network: true`;
- inbound TCP on the ports `grants.bind` lists and, when the episode serves
  a viewer, on the viewer's port, which the command line adds to the policy
  before applying it;
- no outbound TCP when a host process holds the transport.

## Process ownership

On a host with a delegated cgroup v2 hierarchy, one Foe invocation owns two
boundaries. The episode boundary contains the root episode and every child
episode. The task boundary contains only sessions that hold the
`task_session` permission and request task lifetime.

Each episode runs in a process leaf below its episode boundary. A parent
creates a nested boundary for each child. The child enters its process leaf
through a runtime-owned wrapper before the child runtime executes.

A task-lifetime session enters the invocation's task boundary before its
command executes. It never enters the child episode boundary. Child
settlement can therefore remove the child boundary while the task session
continues under the task environment's ownership.

When a direct child exits, the parent writes `1` to the child's
`cgroup.kill`. The kernel kills every descendant, including processes that
created another process group or session. The parent waits for recursive
`populated 0`, removes the boundary, and publishes the child's settlement.

The boundary path is runtime launch metadata in the child's
`child-launch.json`.
It does not participate in the contract fingerprint or enter a model
request. The log records the selected mechanism without recording the host
path.

When no delegated cgroup is available, process groups remain the portable
cleanup mechanism. The runtime reports this fallback as observational
subtree cleanup because a descendant can create a new group or session.

## Executables

A configured executable runs under the episode's ruleset narrowed once
more. The narrowed policy keeps the read roots, write roots, explicit execute
grants, library paths, system paths, and device paths. It also keeps execute on
the captured inode and its exact interpreter. It drops the log directory, key
file, and execute access to other configured tools. It keeps the episode's
bind ports. A server started by a shell or held by a session can listen on a
granted port. Outbound TCP remains available when the tool definition sets
`network: true`.

Foe stores the captured bytes in a private file under a runtime directory
outside every declared write root. Captured executables with the same digest
and invocation name share one
held inode. Construction tries the parent of the episode log directory, `/tmp`, and
`/var/tmp`, in that order. Each attempt combines directory creation with the
filesystem checks for that directory. A filesystem mounted with `noexec` is
skipped. Construction fails when no writable, executable location can be
separated from the declared write roots.

The captured executable has no write bits, and the runtime retains a read-only descriptor
for its inode. Construction checks its mode, mount flags, and stored bytes.
Child-episode initialization repeats the mode and byte checks before accepting
an inherited descriptor. Invocation maps the descriptor to a collision-free
child descriptor and executes it through `/proc/self/fd`. The stored file and
argument zero use the basename from the configured absolute path. This
preserves dispatch by BusyBox, coreutils, and other multicall executables. The
source pathname is never reopened.

The episode process receives internal read and write access to the storage
parent so it can remove the private directory after confinement. Tool and
session policies omit this access. A configured write root that contains the
episode log therefore does not expose a captured executable stored elsewhere.

A script receives its descriptor path as `$0`. A script that needs adjacent
resources uses its declared working directory or an absolute configured path.

Captured executables exist for the lifetime of their runtime owners.
The last owner removes the private directory. Task-lifetime sessions use the
session launcher and do not retain a configured tool or transport executable.

A parent passes captured executables for the selected child's full declared
contract tree. The sealed manifest associates every configuration key with an
executable digest and invocation name. Repeated references to one captured
executable share one descriptor. The child reads each descriptor once and
constructs its contract fingerprint from those retained bytes. Its sandbox
grants execute access only to captured executables
reachable through spawn grants and workflow nodes. Descriptor remapping
preserves standard input, standard output, standard error, and every source
descriptor when source and target numbers overlap.

This crate forbids unsafe code, so the narrowing is applied by a short-lived
thread rather than by a hook between fork and exec. The thread applies the
narrowed ruleset to itself, starts the process, hands the process handle
back, and ends. The process inherits the thread's ruleset before it
executes anything, which is the same point at which a hook would act.

A session released with task lifetime keeps the ruleset it inherited. The
restrictions remain attached to every process-group member after foe exits.
The enclosing task environment owns process cleanup, as
[tools.md](tools.md#session) specifies.

One request may replace the derived narrowing with a policy of its own.
The built-in `python` tool is the one caller: its interpreter runs with
read on `/usr` alone, execute on the interpreter, write on nothing, and no
network, in place of the episode's roots. [code-mode.md](code-mode.md)
specifies that confinement.

Each executable also runs in its own process group. When its timeout
elapses, or the episode is cancelled, the whole group receives `SIGTERM`
and, two seconds later, `SIGKILL`. When the executable exits on its own,
whatever remains of its group receives `SIGKILL`. A process that moved
itself into a new process group or session is outside this process-group
rule. An enforced episode cgroup still owns and kills that process at
episode settlement. The Landlock ruleset also continues to restrict it.

## Children

A child episode is a further `foe` process. The parent starts it without
narrowing, because the child applies its own policy to itself at startup.
Two independent rules keep that policy inside the parent's policy.

Resolving the configuration checks containment before any process starts:
each child contract's read, write, and execute roots must lie within its
parent contract's corresponding roots, and its bind ports among its parent's.
The rule applies at every level of
`child_contracts`. A document that fails the check is refused with the dotted key of
the offending root, and no episode begins.

The kernel enforces the same containment whatever the document says. The
parent restricts its main thread before it starts any other thread, and a
Landlock domain passes to every thread and process created afterwards, so
the child inherits the parent's domain before it executes. The child's own
ruleset nests inside that one, and the child's reach is the intersection.

## Resolved permissions and enforcement

`foe plan` reports the resolved permissions for the root and each reachable
descendant contract. Every read, write, or execute path includes its reason.
A captured executable includes its content digest. The report also names bind
ports and every reason outbound TCP remains available.

For a captured executable, the path is its construction-time source. The
digest identifies the captured bytes without reopening that source. For a
sandbox mode other than `off`, the plan includes the exact cgroup entry shell
with a conditional reason. The host decides whether cgroup delegation is
available when an episode starts.

`episode/start.sandbox.resolved_permissions` records the permission set
compiled for that episode. It includes runtime paths unavailable to a
configuration-only plan, such as the episode log directory and delegated
cgroup paths.

The resolved permission set states what the runtime attempted to permit.
`sandbox.mode`, `landlock_abi`, and `process_boundary` state what the host
enforced.

## Modes

`sandbox.mode` in the configuration decides what happens at startup.

| mode | behavior |
|---|---|
| `best-effort` | applies every Landlock feature the kernel offers up to version 7 and attempts to create a delegated cgroup v2 boundary; records the available enforcement and the reason for a process-group fallback |
| `required` | requires Landlock and attempts to create a delegated cgroup v2 boundary; records an observational process-group fallback when delegation is unavailable |
| `off` | applies no Landlock restriction, uses process-group cleanup, and records observational subtree cleanup |

`best-effort` records a number rather than a list of features, because the
list is a function of the number, given in the table above. A reader of the
log who sees `landlock_abi: 4` knows that the filesystem and TCP were
enforced and that signals and audit logging were not.

`required` requires Landlock. Process ownership has a separate recorded
guarantee because the execution contract has no key that requires cgroup
delegation.

`episode/start.sandbox.process_boundary` records `cgroup-v2` with enforced
subtree cleanup or `process-group` with observational cleanup. The optional
reason explains why cgroup ownership was unavailable.

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
- Another process running as the same operating-system user. Such a process
  can alter files owned by that user, including captured executable files.
  Processes that run with the same user identity are inside the host trust
  boundary.
- The network of the episode process when it holds the transport. An
  episode with a `model` block connects to the provider itself, and
  Landlock has no rule that names a remote host, so outbound TCP is open
  for that process and for nothing it starts without `network: true`.
- Anything on a kernel below the tier that enforces it, as listed in the
  table. A log records the tier, so a reader knows what held.
- Resources other than files, TCP, signals, and abstract sockets. Memory,
  processor time, and process count are bounded by the budget alone.
