# Example support

Six helpers support the end-to-end examples.

| file | job |
|---|---|
| `loopback_http.py` | serves deterministic streaming responses over loopback HTTP and records every request |
| `materialize.py` | replaces absolute path markers with directories created by a runner |
| `chunks.py` | writes protocol lines for the executable-transport example |
| `response_chunks.py` | builds chunks for deterministic host responses |
| `responses.py` | defines shared responses selected by request state |
| `run_with_host.py` | runs a configuration against the built binary while the host supplies responses |

## Response ownership

Deterministic examples omit the root `model` block. `run_with_host.py` starts
the binary in host mode and answers each request with a response function.
The response files stay in the repository because the binary does not execute
them inside an episode.

Examples that exercise runtime-owned HTTP use `loopback_http.py`. The loopback
server exposes a compatible endpoint without external network access or
credentials.
