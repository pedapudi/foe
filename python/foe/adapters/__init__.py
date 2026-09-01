"""Transport adapters.

An adapter turns a provider client into the transport callable
`foe.ExecutionContract.run` accepts. Each adapter imports its provider library only
when constructed, so the core package has no dependencies.
"""
