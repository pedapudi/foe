#!/usr/bin/python3
"""Answer every request with one more tool call, so the loop never ends.

Each answer reads a different module. Identical tool calls and assistant
turns therefore stay below `loop_threshold`. The configured `model_calls`
limit stops the episode.
"""

from response_chunks import call, done, step


def respond(request: dict) -> list[dict]:
    """Return one distinct read call for the next step."""
    chunks: list[dict] = []
    turn = step(request) + 1
    call(chunks, f"tc_{turn:02}", "read", {"path": f"src/module_{turn}.py"})
    done(chunks, "tool")
    return chunks
