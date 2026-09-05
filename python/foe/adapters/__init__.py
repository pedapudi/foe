"""Model backend adapters.

An adapter turns a model client into the model backend callable
`foe.ExecutionContract.run` accepts. Each adapter imports its provider library only
when constructed, so the core package has no dependencies.
"""
