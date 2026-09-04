#!/usr/bin/python3
"""Answer every request with the same retryable endpoint error.

The message represents failure to resolve the endpoint host name. The
retryable flag marks a condition that a later attempt could survive. It
makes the runtime retry with a growing delay rather than fail at once.
"""

from response_chunks import error

MESSAGE = "cannot reach the model endpoint: name resolution for api.example.invalid failed"


def respond(request: dict) -> list[dict]:
    """Return the retryable endpoint error for every request."""
    del request
    chunks: list[dict] = []
    error(chunks, MESSAGE, retryable=True)
    return chunks
