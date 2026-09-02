#!/usr/bin/python3
"""Return a prepared candidate contract as the repair-proposal answer.

This transport stands in for the model child in the runner's
--repair-with-file mode: it reads the candidate document the model
block's `candidate_file` option names and returns it unchanged, so the
proposal episode, its verification event, and the evidence bundle are
exercised without a model request. The candidate file sits under the
proposal read root because the transport process is confined to it.
"""

import json
import sys


def main() -> None:
    request = json.loads(sys.stdin.readline())
    request_id = request["request_id"]

    def emit(chunk: dict) -> None:
        print(
            json.dumps(
                {"type": "model/chunk", "request_id": request_id, "chunk": chunk},
                separators=(",", ":"),
            )
        )

    with open(request["options"]["candidate_file"], encoding="utf-8") as source:
        candidate = json.load(source)
    emit({"kind": "tool_call_start", "id": "return-candidate", "name": "return"})
    emit({"kind": "tool_call_delta", "id": "return-candidate", "delta": json.dumps({"value": candidate})})
    emit({"kind": "tool_call_end", "id": "return-candidate"})
    emit({"kind": "done", "stop": "tool", "usage": {"input": 40, "output": 20, "cache_read": 0}})


if __name__ == "__main__":
    main()
