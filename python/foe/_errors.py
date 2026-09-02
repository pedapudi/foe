"""Exceptions raised by the package."""

from __future__ import annotations


class ConfigError(ValueError):
    """A contract was declared in a way the configuration format rejects.

    The message names the key and the rule, as docs/config.md requires of
    every construction error.
    """


class BinaryError(RuntimeError):
    """The foe binary could not be run or exited before completing a command."""


class ProtocolError(RuntimeError):
    """A line from the binary did not conform to docs/protocol.md."""


class CompatibilityError(RuntimeError):
    """The binary does not pair with this package.

    The message names the version that disagrees: the log format the binary
    writes, or the runtime version whose protocol it speaks. The episode is
    cancelled before its first request, as docs/protocol.md "Versioning"
    requires of a host that does not recognize the version.
    """


class CapabilityError(PermissionError):
    """A path or executable lies outside every granted root."""
