# Tool mistake audit

A deterministic suite that submits the common malformed calls to every
built-in coding tool and asserts the typed failure each one produces. Every
case runs through a real foe episode, so the failure code, retryability,
and message come from the actual validation and dispatch path rather than
from a reimplementation. The expectations pin the complete message text: a
wording or code regression fails the suite.

## Running it

```
cargo build
/usr/bin/python3 evals/tool_audit/run_tool_audit.py --foe target/debug/foe
```

`--include-kernel-sandbox` adds a probe that requires Landlock: it runs an
external command outside `grants.execute` under `sandbox.mode: required`
and checks the denial shape, which is exit 126, `permission_denial` set to
`possible`, and a rendering that names `grants.execute`. `--keep DIR`
retains the configurations, fixtures, and episode logs. Exit status 0 means
every case matched, 1 means at least one expectation failed, and 2 means
the suite could not run. Unit tests of the case table and checks run with
`/usr/bin/python3 evals/tool_audit/tool_audit_test.py`.

## What the suite covers

Forty-two cases across `read`, `grep`, `edit`, `bash`, `session`, `compose_tools`,
and `retrieve`, keyed by tool, failure code, and the field at fault. The
mistake kinds are: a wrong field name, a missing required field, a wrong
type, an out-of-range value, a value the schema cannot judge (an absent
path, a stale version, a malformed cursor, an oversize source), and a call
that needs a capability the contract does not grant. Thirty cases produce
`invalid-call`, eight `operation-failed`, three `capability-denied`, and
one `limit-exceeded`. Each case asserts that the message names the invalid
field or its value.

## Traps the audit removed

| Trap | Correction | Where |
|---|---|---|
| A wrong field name reported only the rejected name, so a caller had to guess the accepted one. | The error lists the accepted properties: ``has unexpected property `file`; the properties are limit, offset, path``. | `crates/contract/src/schema.rs` |
| Non-object arguments produced "arguments are a JSON object", which states the rule as if the call satisfied it. | The message reads "arguments must be one JSON object". | `crates/core/src/registry.rs` |
| A syntactically invalid `retrieve` cursor carried `operation-failed`, blurring a malformed argument into a runtime fault. | The failure code is `invalid-call`; a well-formed cursor naming an ineligible source keeps `operation-failed`. | `crates/core/src/retrieval.rs` |
| The `retrieve` cursor error named no corrective action. | The message adds "copy the whole cursor from its notice unchanged". | `crates/contract/src/harness_text.rs` |
| A `session` action without its `session` id got "`session` names the session id", which names no fix and no source for the id. | The message reads "this action requires `session`, the id `start` returned". | `crates/code/src/session.rs` |
| An invalid `grep` pattern offered no alternative for text containing regex metacharacters. | The message adds "set literal to true to match it as a fixed string". | `crates/code/src/grep.rs` |
| `read` kept an `offset` guard that dispatch already enforces through the schema's `minimum`, an unreachable second message for the same mistake. | A clamp keeps the window arithmetic safe for callers outside dispatch; the schema error is the single message. | `crates/code/src/read.rs` |

The message changes move every contract fingerprint, because the
fingerprint hashes the harness text. The recorded example fingerprints in
`crates/cli/tests/integration.rs` carry the new values. No parameter
schema changed, and `harness_text::VERSION` is unchanged because no
constant changed meaning.

## Behavior the audit confirmed and left alone

- Missing optional fields already take the uniquely determined value the
  runtime knows: `offset` 1, the grep `path` from the first read root,
  the `episode` session lifetime, and the default timeouts. No further
  field has a uniquely determined value, so nothing new is inferred.
- Capability denials are already distinct from malformed input and name
  their configuration keys: `grants.read` and `grants.write` for a path
  outside the roots, `grants.task_session` for a task-lifetime session,
  and `grants.execute` in the exit-126 guidance.
- "offset N is past the end" keeps `operation-failed`: the declared schema
  cannot know a file's length, and the message states the exact line count.
- An unknown session id keeps `operation-failed` and lists no live ids,
  because the id set is runtime state held by the kernel supervisor.
- An unknown tool name lists no alternatives, because every request already
  carries the declared tool roster.
- The regex library renders a failing pattern wrapped as `(?:PATTERN)`, so
  its caret column is approximate; the appended `literal` hint gives the
  fix without re-deriving the column.
- `edit` errors already name the failing edit index, the occurrence count,
  and a corrective hint, and nothing there changed.
