"""foe: a host for the foe runtime.

The package builds the configuration document docs/config.md specifies,
runs the `foe` binary, answers the host protocol docs/protocol.md specifies
over the binary's standard input and output, and returns a typed outcome.
It executes no tool of its own: a host tool is routed to the callable the
embedding contract supplies. The host supplies a model backend when the
document has no `model` block. When the document has a `model` block, the
binary calls the configured endpoint. The documents the binary carries reach
a host through `builtin`, which reads one from the binary; the package holds
no copy of any of them. Nothing in the package reads an environment variable.
"""

from ._builtin import builtin
from ._capabilities import Exec, ExecResult, ReadFS, WriteFS
from ._errors import BinaryError, CapabilityError, CompatibilityError, ConfigError, ProtocolError
from ._host import EventCallback, Handle, ModelBackend, Viewer, run_config, serve, start_config
from ._outcome import Blocked, Completed, Event, Exhausted, Failed, Outcome, Runtime
from ._contract import BUILTIN_TOOLS, Budget, DoneWhen, Grants, ExecutionContract, Model, Returns, ToolDef, Verified
from ._schema import schema_for
from ._tools import Capabilities, Effect, HostTool, ToolResult, ToolSpec, tool
from ._versions import CONFIG_VERSION, LOG_FORMAT_VERSION, PROTOCOL_VERSION

__all__ = [
    "BUILTIN_TOOLS",
    "CONFIG_VERSION",
    "LOG_FORMAT_VERSION",
    "PROTOCOL_VERSION",
    "BinaryError",
    "Blocked",
    "Budget",
    "Capabilities",
    "CapabilityError",
    "CompatibilityError",
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
    "ModelBackend",
    "Verified",
    "Viewer",
    "WriteFS",
    "builtin",
    "run_config",
    "schema_for",
    "serve",
    "start_config",
    "tool",
]

__version__ = "0.2.0"
