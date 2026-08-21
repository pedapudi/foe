# Example support

Two helpers shared by the examples.

| file | job |
|---|---|
| `materialize.py` | replaces a configuration's absolute path markers, such as `/home/user/project`, with directories a runner just created |
| `chunks.py` | writes model chunks, so an example's transport script holds only the responses that example demonstrates |
| `scripted-transport.py` | the responses for the sandbox and workflow demos, dispatched by the `model` name their configurations set |

An example that runs without a provider credential names the `exec` provider
and points it at a transport script. A new example places that script in its
own directory and imports `chunks.py`, so that adding an example never edits
a file another example depends on.
