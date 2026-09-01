"""foe: a host for the foe runtime.

The package builds the configuration document docs/config.md specifies,
runs the `foe` binary, answers the host protocol docs/protocol.md specifies
over the binary's standard input and output, and returns a typed outcome.
It performs no model calls and executes no tools of its own; both are
routed to callables the embedding contract supplies. Nothing in the package
reads an environment variable.
"""

from ._capabilities import Exec, ExecResult, ReadFS, WriteFS
from ._errors import BinaryError, CapabilityError, ConfigError, ProtocolError
from ._host import EventCallback, Handle, Transport, Viewer, run_config, serve, start_config
from ._outcome import Blocked, Completed, Event, Exhausted, Failed, Outcome
from ._contract import BUILTIN_TOOLS, Budget, DoneWhen, Grants, ExecutionContract, Returns, ToolDef, Verified
from ._schema import schema_for
from ._tools import Capabilities, Effect, HostTool, ToolResult, ToolSpec, tool

__all__ = [
    "BUILTIN_TOOLS",
    "BinaryError",
    "Blocked",
    "Budget",
    "Capabilities",
    "CapabilityError",
    "Completed",
    "ConfigError",
    "DoneWhen",
    "Effect",
    "Event",
    "EventCallback",
    "Exec",
    "ExecResult",
    "Exhausted",
    "Failed",
    "Grants",
    "Handle",
    "HostTool",
    "Outcome",
    "ExecutionContract",
    "ProtocolError",
    "ReadFS",
    "Returns",
    "ToolDef",
    "ToolResult",
    "ToolSpec",
    "Transport",
    "Verified",
    "Viewer",
    "WriteFS",
    "run_config",
    "schema_for",
    "serve",
    "start_config",
    "tool",
]

__version__ = "0.2.0"
