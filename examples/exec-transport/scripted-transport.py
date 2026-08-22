#!/usr/bin/python3
"""The exec transport this example runs: fixed chunks in place of a provider.

The `exec` provider starts this program once per model request with the
model name as its single argument, writes one `model/request` line to its
standard input, and reads `model/chunk` lines from its standard output.
`docs/models.md` specifies that exchange. `litellm-transport` beside this
file is the same program written against a real provider.

The program answers two requests: a `read` call on the file named by the
`readme` option, then one sentence of assistant text.

The program runs under the episode's sandbox, which permits it to read the
episode's read roots and this file. `run.sh` copies this file and
`chunks.py` into the project directory so that both lie under a read root.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chunks import call, done, read_request, step, text

SUMMARY = "The README states what the project does and how to build it."

request = read_request()
if step(request) == 0:
    call(request, "tc_1", "read", {"path": request["options"]["readme"]})
    done(request, "tool")
else:
    text(request, SUMMARY)
    done(request, "end")
