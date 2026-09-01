# foe, the Python package

A host for the foe runtime. The package builds the configuration document,
runs the `foe` binary, answers the host protocol over the binary's standard
input and standard output, and returns a typed outcome. Model calls and host
tool calls are routed to callables the embedding contract supplies; the
package performs neither itself.

`docs/sdk.md` at the repository root documents the package. Tests run with
`uv run pytest` from this directory.
