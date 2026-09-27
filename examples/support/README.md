# Example support

Five helpers support the end-to-end examples.

| file | job |
|---|---|
| `loopback_http.py` | serves deterministic streaming responses over loopback HTTP and records every request |
| `materialize.py` | replaces absolute path markers with directories created by a runner |
| `response_chunks.py` | builds chunks for deterministic host responses |
| `responses.py` | defines the responses the workflow, sandbox, and self-extension examples share, selected by request state |
| `run_with_host.py` | runs a configuration against the built binary while the host supplies responses |

## Response ownership

An example whose host supplies the responses omits the root `model` block.
`run_with_host.py` starts the binary as a host does, over the protocol
channel, and answers each request with a response function. The response
files stay outside the disposable project, because the host process runs
them and the episode never does.

The model-block embedding example exercises a configured endpoint through
`loopback_http.py`, and the binary tests in `python/tests/` use it too. The
loopback server exposes a compatible endpoint without external network access
or credentials.
