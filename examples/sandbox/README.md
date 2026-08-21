# Sandbox

A configuration that requires kernel enforcement and then asks the model to
reach outside its grants, so that the refusal appears in the log.
`sandbox.mode` is `required`, which makes foe refuse to start on a kernel
without Landlock. The only grant is read access to `/home/user/project`. One
`tool_defs` entry wraps `/usr/bin/cat`; declaring it is what permits the
episode to execute that file. The task asks the model to print one file
inside the grant and one outside it.

## Paths to replace

- `/home/user/project`: a directory containing a `README.md`.
- `/home/user/.config/foe/anthropic.key`: a file whose whole contents are
  the API key.
- `/usr/bin/cat`: the absolute path of `cat` on the machine, when it
  differs.

## Run

```
foe --config examples/sandbox/config.json
```

On a kernel without Landlock the command exits with code 1 before writing
any log, and standard error names `sandbox.mode` and the rule it violated.

## What to look for

The `episode/start` event records the mode and the Landlock version the
kernel provided:

```json
{"seq": 0, "time": 1724200000000, "type": "episode/start", "data": { "sandbox": { "mode": "required", "landlock_abi": 7 } }}
```

The first `cat` call reads `/home/user/project/README.md` and returns its
contents with exit code 0. The second call targets `/etc/hostname`, which
no grant covers. The executable runs under a ruleset compiled from the
grants, the kernel refuses the open, and two events record the refusal. The
tool result carries the exit code and the message `cat` wrote:

```json
{"seq": 12, "time": 1724200004000, "type": "tool/result", "data": {
  "step": 2, "call_id": "tc_02", "name": "cat",
  "value": { "exit_code": 1, "stdout": "", "stderr": "/usr/bin/cat: /etc/hostname: Permission denied\n" },
  "rendered": "exit 1\n/usr/bin/cat: /etc/hostname: Permission denied",
  "is_error": false, "spill": null, "duration_ms": 3, "synthetic": false }}
```

A non-zero exit is a result rather than an error, so `is_error` is false and
the model reads the message. When the kernel exposes its audit log, foe also
writes the refusal it captured there:

```json
{"seq": 13, "time": 1724200004001, "type": "sandbox/denied", "data": { "pid": 4120, "comm": "cat", "path": "/etc/hostname", "access": "read" }}
```

`pid` is the process the kernel refused, `comm` is its command name, `path`
is the file it asked for, and `access` is the kind of access refused. The
exact field layout of `value` for a `tool_defs` result is specified in
`docs/tools.md`; the event envelope and the `sandbox/denied` payload are
specified in `docs/log-format.md`.

In the viewer, the sandbox line in the left pane shows `landlock 7`, and
the second `cat` call is listed with its exit code. The episode ends
`completed` with the model's report of both calls.
