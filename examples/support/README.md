# Example support

Three helpers shared by the examples.

| file | job |
|---|---|
| `materialize.py` | replaces a configuration's absolute path markers, such as `/home/user/project`, with directories a runner just created |
| `chunks.py` | writes model chunks, so an example's transport script holds only the responses that example demonstrates |
| `scripted-transport.py` | the responses for the demos whose configurations select a scenario by the `model` name they set |

## Where a transport script runs

An example that runs without a provider credential names the `exec` provider
and points it at a transport script. That script runs as a configured
executable, so `Policy::for_executable` grants it the episode's read roots,
plus execute and read on its own file, and nothing else. A script in this
directory therefore cannot import `chunks.py` from this directory: neither
file lies under a read root, and Landlock denies the read.

A runner copies both files into the disposable project it creates, and points
`model.exec` at the copy.

```
project/tools/transport.py     the example's own transport script
project/support/chunks.py      this directory's helper, beside it
```

That layout satisfies the `parent.parent / "support"` path `chunks.py`
documents, and both files sit under a read root the episode already grants.
Each example's README states why the copy exists, because a reader meeting
the runner without that sentence will read the copy as redundant.

A script that imports nothing has no such constraint and may run from
wherever the configuration names it.
