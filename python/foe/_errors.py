"""Exceptions raised by the package."""

from __future__ import annotations


class ConfigError(ValueError):
    """A program was declared in a way the configuration format rejects.

    The message names the key and the rule, as docs/config.md requires of
    every construction error.
    """


class BinaryError(RuntimeError):
    """The foe binary could not be run or exited before completing a command."""


class ProtocolError(RuntimeError):
    """A line from the binary did not conform to docs/protocol.md."""


class CapabilityError(PermissionError):
    """A path or executable lies outside every granted root."""
