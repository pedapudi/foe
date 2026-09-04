#!/usr/bin/python3
"""Answer every request with a turn that reports the work as done.

No answer calls a tool, so each one is a candidate for the `done_when`
verifier, and no answer changes the file the verifier looks at. The text
differs from turn to turn, because an identical assistant turn repeated
`loop_threshold` times ends the episode as `blocked` with the code
`looping-reasoning`, which is a different outcome from the one this example
demonstrates.
"""

from response_chunks import done, step, text

CLAIMS = [
    "I implemented add and removed the TODO comment above it.",
    "I looked at the module again and the change I described is in place.",
    "The function is implemented and no TODO comment remains in it.",
    "There is nothing further for me to change in this module.",
]


def respond(request: dict) -> list[dict]:
    """Return a distinct completion claim for the next verification attempt."""
    chunks: list[dict] = []
    turn = step(request)
    text(chunks, CLAIMS[min(turn, len(CLAIMS) - 1)])
    done(chunks, "end")
    return chunks
