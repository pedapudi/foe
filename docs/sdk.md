# The Python package

`foe` is a Python package that embeds the runtime. The package is a host in
the sense of [protocol.md](protocol.md): it builds the configuration
document that [config.md](config.md) specifies, runs the `foe` binary,
answers the protocol over the binary's standard input and standard output,
and returns a typed outcome. The binary is the runtime; it owns the episode
loop, the log, the grants, and the sandbox. The package performs no model
call and executes no tool of its own. Both are routed to callables the
embedding program supplies.

The package lives in `python/foe`, targets Python 3.11 and later, and
depends on the standard library alone. It ships `py.typed` and passes
`mypy --strict`, so an editor sees every type from the source. Nothing in
the package reads an environment variable; credentials live in the
embedding program's transport.

## A complete example

```python
import asyncio
from dataclasses import dataclass
from pathlib import Path

import foe


@foe.tool
def mutation_usage(mutation_id: str, fs: foe.ReadFS) -> dict:
    """Find where a mutation point's value or symbol is referenced."""
    hits = [p for p in fs.walk("/gen/v37/snapshot") if mutation_id in fs.read_text(p)]
    return {"count": len(hits), "paths": [str(p) for p in hits]}


@mutation_usage.render
def mutation_usage_summary(value: dict) -> str:
    return f"{value['count']} references; first: {value['paths'][:5]}"


@foe.tool
def validate_patches(candidate: str) -> list[str]:
    """Report a finding for every section the proposal lacks."""
    return [s for s in ("Hypothesis", "Method") if s not in candidate]


program = foe.Program(
    name="zicato-proposer",
    instructions={"10-charter": "You propose experiments.", "20-grounding": "Ground every claim."},
    tools=["read", "grep", mutation_usage],
    grants=foe.Grants(read=["/gen/v37/snapshot"], write=["/tmp/scratch"]),
    budget=foe.Budget(model_calls=12, input_tokens=160_000, output_tokens=40_000, seconds=600),
    done_when=foe.Verified(verify=validate_patches, retries=2),
)


async def main() -> None:
    from foe.adapters.litellm import litellm_transport

    transport = litellm_transport("anthropic/claude-opus-5", api_key=Path("~/.config/foe/key").expanduser().read_text().strip())
    outcome = await program.run(
        task="Propose the next experiment.",
        transport=transport,
        binary="/usr/local/bin/foe",
        log_dir=Path("/tmp/episodes/proposer-01"),
        on_event=lambda e: print(e.seq, e.type),
    )
    match outcome:
        case foe.Completed(value):
            print("completed", value)
        case foe.Blocked(code, message):
            print("blocked", code, message)
        case foe.Exhausted(limit):
            print("exhausted", limit)
        case foe.Failed(error):
            print("failed", error)


asyncio.run(main())
```

Strings in `tools` name built-in tools or `tool_defs` entries. Callables
decorated with `@foe.tool` are host tools; the package writes their
specifications into the document under `host_tools` and runs their bodies
when the binary asks.

## The surface

| name | role |
|---|---|
| `foe.Program(...)` | a configuration document without a task |
| `program.to_dict()`, `program.to_json()` | the document, without `task` and without `model` |
| `program.identity(binary)` | the program's identity hash, computed by `foe plan` |
| `await program.run(task, ...)` | run one episode to its outcome |
| `await program.start(task, ...)` | run one episode and return a `Handle` |
| `handle.steer(text)`, `handle.cancel()`, `handle.wait()` | steer, stop, or await a running episode |
| `foe.run_config(doc, ...)`, `foe.start_config(doc, ...)` | the same two operations on a complete document written by hand |
| `foe.serve(log_dir, binary=...)` | serve a log directory through the binary's viewer |
| `@foe.tool`, `@tool.render` | declare a host tool and its rendering |
| `foe.ReadFS`, `foe.WriteFS`, `foe.Exec` | capability handles a host tool may request |
| `foe.Grants`, `foe.Budget`, `foe.ToolDef` | the `grants`, `budget`, and `tool_defs` keys |
| `foe.Verified`, `foe.Returns` | the `done_when` key |
| `foe.Completed`, `foe.Blocked`, `foe.Exhausted`, `foe.Failed` | the outcome union |
| `foe.Event` | one log event, as delivered to `on_event` |
| `foe.adapters.litellm.litellm_transport` | the reference transport adapter |

### `Program`

```python
foe.Program(
    *,
    name: str,
    instructions: Mapping[str, str],
    tools: Sequence[str | foe.HostTool],
    grants: foe.Grants,
    budget: foe.Budget,
    tool_defs: Mapping[str, foe.ToolDef] | None = None,
    done_when: foe.Verified | foe.Returns | None = None,
    programs: Mapping[str, foe.Program] | None = None,
    sandbox: str | None = None,
)
```

Each argument maps to the key of the same name in config.md. `programs`
holds child programs; a child is a `Program` whose `version` and `sandbox`
are omitted from the document because they are inherited. `sandbox` is
`best-effort`, `required`, or `off`; when None the key is omitted and the
runtime's default applies.

Construction validates what can be known without a process and raises
`foe.ConfigError` with a message that names the key and the rule. The
checks are:

- every name in `tools` resolves to a built-in tool, a `tool_defs` entry,
  or a host tool, and to one source only; a host tool named `read`
  collides with the built-in and is refused;
- no name appears twice;
- a host tool whose effect is `writes` needs a non-empty `grants.write`,
  and one whose effect is `execs` needs a non-empty `tool_defs`; the
  built-in `edit` needs `grants.write` and `spawn` needs `grants.spawn`;
- every path in `grants`, `ToolDef.exec`, and `ToolDef.cwd` is absolute;
- every name in `grants.spawn` is a key of `programs`;
- a `Verified` verifier given by name is a tool in `tools`.

The runtime repeats every check when it reads the document and performs
the checks the package cannot, such as whether an executable exists.

`Budget` fields left None are omitted from the document and take the
runtime's defaults, which config.md states. `Grants.write`, `Grants.execute`,
and `Grants.spawn` are omitted when empty.

### `to_json` and `to_dict`

`to_dict(task=None)` returns the document as a dict; `to_json(task=None)`
returns it as a string. Without a task the result is the program alone,
which is the input to identity. With a task the result is a complete
document except for the `model` block, which the package never writes: a
host that runs a program supplies the transport itself.

Instruction sections are written in lexicographic key order, and object
keys under `tool_defs`, `host_tools`, and `programs` are sorted, so the same
program produces the same bytes on every machine. The `tools` list keeps
the order given, because that order participates in identity.

### `identity`

`program.identity(binary)` writes the document to a temporary file with a
placeholder task and runs `foe plan --json --config FILE`. It returns the
`identity` string the binary prints, of the form `sha256:<hex>`. The task
does not participate in identity, so the placeholder has no effect on the
value.

What `foe plan` reads: the document, and the files it names by absolute
path, which it hashes. What it never does: open a socket, read a
credential, start a child process, or write a log. An evaluation harness can
therefore compute identity on a machine that cannot run the program.

### `run` and `start`

```python
await program.run(
    task: str,
    *,
    transport: Transport,
    binary: str | os.PathLike,
    log_dir: str | os.PathLike,
    on_event: Callable[[foe.Event], None] | None = None,
    max_output_tokens: int | None = None,
) -> foe.Outcome
```

`start` takes the same arguments and returns a `foe.Handle` as soon as the
binary is running. `run` is `start` followed by `await handle.wait()`.

The package writes the document to a temporary file, creates `log_dir`
when it does not exist, and launches
`foe --config FILE --host --log-dir DIR`. The temporary file is removed
when the binary exits. `on_event` receives every line the binary writes,
parsed into a `foe.Event` with `seq`, `time`, `type`, `data`, and
`episode_id`; the callback runs on the event loop and should return
quickly. `max_output_tokens` is passed through to the transport on every
request; the package has no opinion about its value.

`Handle` exposes:

- `await handle.wait()`, which returns the outcome;
- `await handle.steer(text)`, which writes an `inbox/item` line with source
  `parent`, a single text block, and `from` and `message_id` null; the
  runtime records it and includes it in the next request;
- `await handle.cancel()`, which writes a `cancel` line and returns the
  outcome the runtime records, `Failed("cancelled")`;
- `handle.episode_id`, `handle.runtime_version`, `handle.log_dir`,
  `handle.outcome`, and `handle.done`.

A handle is not an episode. The host process is never sandboxed, and
closing the Python process without cancelling leaves the binary to notice
the closed pipe.

### `run_config` and `start_config`

```python
await foe.run_config(
    config: Mapping[str, Any] | str | os.PathLike,
    *,
    transport, binary, log_dir,
    tools: Iterable[foe.HostTool] = (),
    on_event=None, max_output_tokens=None,
) -> foe.Outcome
```

These take a complete document, as a dict or as the path of a JSON file,
and run it the way `Program.run` does. They exist for a document written
by hand or produced by another program. The document must carry `task` and
must not carry `model`. `tools` supplies the implementation of every name
in the document's `host_tools`; a missing implementation is an error before
launch.

### `serve`

`await foe.serve(log_dir, binary=...)` runs `foe view DIR --serve`, reads
the URL the binary prints as its first line of standard output, and returns
a `foe.Viewer` whose `url` attribute holds it and whose `close()` stops the
viewer process. `str(viewer)` is the URL.

## Host tools

`@foe.tool` turns a function into a `foe.HostTool`. The decorator derives
the tool's specification from the function.

| specification field | derived from |
|---|---|
| `name` | the function name, or `name=` on the decorator |
| `description` | the first line of the docstring, or `description=` on the decorator |
| `instruction` | `instruction=` on the decorator; absent otherwise |
| `params` | the annotated parameters, excluding capability parameters |
| `effect` | the capability parameters, as described under "Capabilities become effects" |

A function with neither a docstring nor an explicit description is refused
at decoration, as is a parameter without an annotation.

### Parameter schemas

`params` is a JSON Schema object with one property per parameter,
`required` listing the parameters without defaults, and
`additionalProperties` false. Annotations map as follows.

| annotation | schema |
|---|---|
| `str`, `int`, `float`, `bool` | `string`, `integer`, `number`, `boolean` |
| `list[T]` | `array` with `items` from `T`; bare `list` has no `items` |
| `dict[str, T]` | `object` with `additionalProperties` from `T`; bare `dict` has none |
| `Optional[T]`, `T \| None` | `anyOf` of `T` and `null` |
| `Literal[...]` | `enum` |
| `Any` | the empty schema |
| a dataclass | `object` with a property per field, `required` for fields without defaults, and `description` from the class docstring when the author wrote one |
| a `TypedDict` | `object` with a property per key and `required` from the class's required keys |

Any other annotation raises `TypeError` naming it. `foe.schema_for` exposes
the derivation for a single annotation.

### Results and rendering

The function's return value is the canonical result. Dataclass instances
become objects, paths become strings, and tuples and sets become lists.
The package sends it as `value` in the `tool/result` line, so it must be
representable as JSON after that conversion.

`@name.render` registers a function from the canonical value to the text
the model sees. Without one, `rendered` is omitted and the runtime renders
the value compactly. A function may also return a `foe.ToolResult(value,
rendered, is_error)` to set all three fields itself.

The decorator returns the function unchanged, so the name it is bound to
still refers to it. `HostTool` holds the renderer privately and offers no
accessor for it, so binding the decorated function to `_` leaves the
program no way to call it again. A program that needs the text the model
saw reads the `rendered` field of the `tool/result` event from the log.

An exception inside the function becomes an error result: `value` is
`{"error": "<type>: <message>"}`, `rendered` is the same message, and
`is_error` is true. The model receives it as data and the episode
continues. The package never lets a host tool's exception end the episode.

Synchronous functions run in a worker thread so that calls whose effects
permit concurrency do not block the protocol loop. Coroutine functions are
awaited directly. A host tool has no timeout; the function is responsible
for bounding its own work.

### Verifiers

`foe.Verified(verify=fn)` accepts a host tool. The runtime calls it with
the candidate result as its single argument, and the function returns a
list of finding strings, where an empty list means no findings. The
package writes the tool into `host_tools` with effect `pure` and a
one-parameter schema, appends it to `tools` when it is not already listed,
and writes `done_when.verify` as its name. `Verified(verify="check")`
names a tool already in `tools`, such as a `tool_defs` entry. `Verified`
also accepts `returns=` so that the verifier checks a typed return.

`foe.Returns(Experiment)` derives the `done_when.returns` schema from a
dataclass or a `TypedDict`; a JSON Schema object is accepted as given.

### The shape of the `return` call

A `done_when.returns` schema makes the runtime synthesize a built-in tool
named `return`. The tool's parameters are an object with one required
property, `value`, whose schema is the declared one. The call the model
makes is therefore `{"value": {…}}`, with the declared object nested one
level down. A call that passes the declared object at the top level fails
the tool, and the episode continues until the budget is spent:

```
The arguments for `return` are invalid: value: expected type object, found null
```

The wrapper exists only on the call. `foe.Completed.value` holds the
declared object itself, so a program reading the outcome never sees the
`value` key. A transport, a test double, or an evaluation harness that
produces the `return` call on the model's behalf must write the wrapper,
because nothing between the model and the runtime adds it.

## Capabilities become effects

A host tool asks for access by annotating a parameter with a capability
class. The package constructs the handle per call, bounded to the grants in
the document, and passes it in that parameter. The model never sees the
parameter.

| annotation | handle | effect |
|---|---|---|
| `foe.ReadFS` | reads bounded to `grants.read` | `reads` |
| `foe.WriteFS` | writes bounded to `grants.write` | `writes` |
| `foe.Exec` | process starts bounded to the `exec` paths in `tool_defs` | `execs` |
| none | no handle | `pure` |

A tool with several capability parameters takes the strongest effect, in
the order `execs` over `writes` over `reads`. A tool that writes also
receives a `ReadFS` when it asks for one. No Python capability produces the
`spawns` effect; child episodes are started by the built-in `spawn` tool.

`ReadFS` offers `read_bytes`, `read_text`, `exists`, `walk`, and `resolve`.
`WriteFS` offers `write_bytes`, `write_text`, and `mkdir`; `write_bytes`
stages beside the target and renames, so a reader never sees a partial
file. `Exec.run(program, args, cwd=, env=, timeout=, stdin=)` starts a
declared executable with a fixed argument vector and no shell, and returns
an `ExecResult` with `exit_code`, `stdout`, `stderr`, `timed_out`, and
`duration_ms`.

Every handle resolves a path through symbolic links and checks it against
the granted roots before use, which is the prefix rule config.md states
for `grants`. A path outside every root raises `foe.CapabilityError`. This
check is a convenience: it lets a host tool behave like a built-in tool and
fail the same way. The guarantee is the runtime's own check, which refuses
a tool whose declared effect the grants do not cover and confines every
process the episode starts. The host process is never sandboxed, because
it holds the credentials, so a host tool that reaches the filesystem
without its handle is outside both checks.

## The transport adapter contract

A transport is an async callable that receives one request dict and yields
chunk dicts.

```python
async def transport(request: dict) -> AsyncIterator[dict]: ...
```

The request dict has five keys.

| key | value |
|---|---|
| `request_id` | the id from the `model/request` event |
| `system` | the system prompt from the `request/header` in effect |
| `tools` | the tool schemas from that header, each `{name, description, parameters}`, in `tools` order |
| `messages` | the derived message list from the `model/request` event, as log-format.md defines it |
| `max_output_tokens` | the value given to `run`, or None |

The transport yields `chunk` objects in the form protocol.md defines
under `model/chunk`: `text`, `thinking`, `tool_call_start`,
`tool_call_delta`, `tool_call_end`, then one `done` with `stop` and `usage`,
or one `error` with `message` and `retryable`. The package wraps each in a
`model/chunk` line and writes it to the binary. A transport that raises is
reported as an `error` chunk with `retryable` false and the exception's
type and message. A transport that ends without a terminal chunk is
reported the same way.

The package calls the transport once per `model/request`, and the runtime
has at most one outstanding request at a time, so the transport never runs
concurrently with itself within one episode. When the document has child
programs, requests from child episodes carry an `episode_id` on the event;
the package echoes it on every answer, as protocol.md requires.

### The reference adapter

`foe.adapters.litellm.litellm_transport(model, *, api_key=None,
api_base=None, **completion_kwargs)` returns a transport over
`litellm.acompletion(stream=True)` with tool calling. It maps the system
prompt, the derived messages, and the tool schemas to the request shape
that library expects. It maps streamed deltas back to chunks: content to
`text`, reasoning content to `thinking`, tool-call deltas to the three
tool-call kinds, the finish reason to `stop`, and the usage block to
`usage`. Exceptions whose class name indicates a rate limit, a timeout, a
connection failure, or a server error are reported with `retryable` true;
every other exception is reported with `retryable` false.

The library is imported when `litellm_transport` is called, so importing
`foe` does not require it. Install it with `pip install foe[litellm]`. Pass
`api_key` explicitly; the adapter reads no environment variable. No other
adapter ships with the package. A program that talks to a provider directly
writes its own transport against the contract above.

## The outcome union

```python
foe.Outcome = foe.Completed | foe.Blocked | foe.Exhausted | foe.Failed
```

Each member is a frozen dataclass that supports pattern matching.

| outcome | fields | meaning |
|---|---|---|
| `Completed` | `value` | the program's termination condition was met |
| `Blocked` | `code`, `message` | the agent recognized that it cannot proceed; `code` is from the closed vocabulary in log-format.md |
| `Exhausted` | `limit` | a resource limit was reached; one of `model_calls`, `input_tokens`, `output_tokens`, `context_window`, `seconds`, `depth`, `episodes`, `concurrency` |
| `Failed` | `error` | the runtime could not continue |

The outcome is parsed from the `episode/end` event. When the binary exits
without writing one, the package returns
`Failed("foe exited with code N before episode/end")`. When the binary
writes a line that is not a log event, the package sends `cancel` and
returns a `Failed` outcome naming the line.

## The package and the binary

The binary is the runtime. It validates the document, builds the system
prompt and the tool schemas, runs the step loop, enforces the budget,
detects looping, compiles the grants into kernel restrictions, writes the
log, and decides the outcome. Nothing the package does changes what the
model sees or what the episode may touch; the package can only decline to
launch a document it knows the binary would refuse.

The package is a host. It holds the credentials, performs the model calls
through the transport, runs the host tools, and reads the log as it is
written. Every exchange between the two passes through the binary's log,
so a log directory produced through the package replays and views the
same way as one produced by the binary alone.

A program that needs the binary's other commands invokes them directly;
the schema and tool listings of `foe plan` have no wrapper in the package.

## Testing without the binary

`python/tests/fake_foe.py` is a stand-in for the binary that speaks the
protocol over standard input and standard output and writes
`episode.jsonl`. It covers the episode shapes the package has to handle:
a text turn, a host tool call, a built-in tool call, a `block` call, a
`return` call with a verifier, a steer arriving mid-request, `cancel`, a
transport error, and a spent `model_calls` budget. `uv run pytest` from
`python/` runs the package's tests against it.
