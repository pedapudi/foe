"""The versions this package and a binary must agree on to run an episode.

Three versions form the compatibility surface between the two. The package
writes the configuration format the binary parses, the binary writes the log
format the package parses, and the binary states its runtime version, which
docs/protocol.md "Versioning" makes the version of the protocol they speak.
`_host` reads the last two from `episode/start` and refuses a binary that
does not pair with this package.
"""

from __future__ import annotations

# The `version` the package writes into every configuration document. It is
# `CONTRACT_FORMAT_VERSION` in crates/contract, and docs/config.md "version".
CONFIG_VERSION = 4

# The log format the package parses. It is `LOG_VERSION` in crates/log, and
# docs/log-format.md "The envelope".
LOG_FORMAT_VERSION = 3

# The log format of a log whose first event states no version. docs/log-format.md
# "The envelope": version 3 writers are the first to state the version, so
# absence identifies a version 3 log written before they did.
UNSTATED_LOG_FORMAT_VERSION = 3

# The runtime releases whose protocol this package speaks, as the leading
# fields of `episode/start.runtime.version`. docs/protocol.md "Versioning"
# makes that string the protocol version, and a runtime release changes it.
PROTOCOL_VERSION = "0.2"


def protocol_agrees(runtime_version: str) -> bool:
    """Whether a binary stating `runtime_version` speaks this package's protocol."""
    wanted = PROTOCOL_VERSION.split(".")
    return runtime_version.split(".")[: len(wanted)] == wanted
