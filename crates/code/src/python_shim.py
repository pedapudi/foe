# The shim the `python` tool prepends to the model's program on the
# interpreter's standard input. It exposes call_tool and fail, speaks JSON
# lines over the inherited socket on file descriptor 3, and reports the
# value main returned or the failure that ended the program. python.rs
# fills __FOE_MEMORY__ before writing it. All other names carry a _foe
# prefix so the program cannot collide with them by accident.
import json as _foe_json
import os as _foe_os
import resource as _foe_resource

# The address-space cap. Both the soft and the hard limit are set, and a
# process without privilege cannot raise a hard limit, so the program
# cannot undo it.
_foe_resource.setrlimit(_foe_resource.RLIMIT_AS, (__FOE_MEMORY__, __FOE_MEMORY__))
_foe_pipe = _foe_os.fdopen(3, "r+b", buffering=0)
_foe_active = False


class _FoeFail(BaseException):
    pass


def _foe_send(message):
    # The descriptor is unbuffered, and one write(2) on a socket may be
    # partial, so the loop writes until the line is out.
    data = (_foe_json.dumps(message) + "\n").encode()
    while data:
        data = data[_foe_pipe.write(data) :]


def call_tool(name, args):
    if not _foe_active:
        raise _FoeFail("call_tool is available only while main runs")
    _foe_send({"call": {"name": name, "args": args}})
    line = _foe_pipe.readline()
    if not line:
        raise SystemExit(3)
    response = _foe_json.loads(line)
    if "fatal" in response:
        raise SystemExit(4)
    return {"value": response["value"], "is_error": response["is_error"]}


def fail(message):
    raise _FoeFail(str(message))


def _foe_run():
    global _foe_active
    _foe_active = True
    try:
        _foe_send({"done": main()})
    except _FoeFail as failure:
        _foe_send({"failed": str(failure)})
    except SystemExit:
        raise
    except BaseException:
        import traceback

        _foe_send({"failed": traceback.format_exc(limit=5)})
