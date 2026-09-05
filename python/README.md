# foe, the Python package

A host for the foe runtime. The package builds the configuration document,
runs the `foe` binary, answers the host protocol over the binary's standard
input and standard output, and returns a typed outcome. When a contract omits
the `model` block, the host supplies a model backend callback. A `model` block
directs the binary to call the configured endpoint. Host tool calls are routed
to callables the embedding contract supplies.

`docs/sdk.md` at the repository root documents the package. Tests run with
`uv run pytest` from this directory.
