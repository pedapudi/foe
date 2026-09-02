"""foe: a host for the foe runtime.

The package builds the configuration document docs/config.md specifies,
runs the `foe` binary, answers the host protocol docs/protocol.md specifies
over the binary's standard input and output, and returns a typed outcome.
It executes no tool of its own: a host tool is routed to the callable the
embedding contract supplies. The model is called by the embedding
contract's transport, or by the binary's own transport when the document
carries a `model` block. Nothing in the package reads an environment
variable.
"""

from ._capabilities import Exec, ExecResult, ReadFS, WriteFS
from ._errors import BinaryError, CapabilityError, ConfigError, ProtocolError
from ._host import EventCallback, Handle, Transport, Viewer, run_config, serve, start_config
from ._outcome import Blocked, Completed, Event, Exhausted, Failed, Outcome, Runtime
from ._contract import BUILTIN_TOOLS, Budget, DoneWhen, Grants, ExecutionContract, Model, Returns, ToolDef, Verified
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
    "Model",
    "Outcome",
    "ExecutionContract",
    "ProtocolError",
    "ReadFS",
    "Returns",
    "Runtime",
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
