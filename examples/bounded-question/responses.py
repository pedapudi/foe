#!/usr/bin/python3
"""Deterministic responses for a lead and two recorders that ask it a question.

Both recorders ask the same question and reach the same answer by different
routes: the lead replies to one, and the other's deadline passes so the
default it named stands. Neither is left waiting.
"""

import re

from response_chunks import call, done, error, step, text, tool_names

DATE = "2026-09-08"
DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")

# How long each recorder's question stays open. The eastern deadline outlasts
# the episode that asks it: the recorder's own `seconds` budget is 60, so the
# runtime can never deliver that default, and the only answer the eastern
# recorder can receive is the lead's. A deadline merely longer than the run
# is not enough, because a loaded machine stretches the run and not the
# deadline. The western deadline is short enough that the example does not
# wait on a person's patience, and the lead never answers it.
DEADLINE_MS = {"east": 600000, "west": 1000}

FIRST_LINE = {"east": "sunrise 05:58", "west": "sunrise 06:31"}


def user_texts(request: dict) -> list[str]:
    """One string per task and inbox message this episode has received.

    A message's blocks are joined, because a question carries its own text
    in one block and the identifier to answer it in another, and the two are
    one message.
    """
    return [
        "\n".join(block.get("text", "") for block in message["content"])
        for message in request["messages"]
        if message["role"] == "user"
    ]


def calls(request: dict) -> list[dict]:
    """Every tool call this episode has made, in order."""
    return [
        tool_call
        for message in request["messages"]
        if message["role"] == "assistant"
        for tool_call in message["tool_calls"]
    ]


def called(request: dict, name: str) -> bool:
    """Whether this episode has called the named tool."""
    return any(tool_call["name"] == name for tool_call in calls(request))


def results(request: dict) -> list[str]:
    """The rendered results of this episode's tool calls, in order."""
    return [message["rendered"] for message in request["messages"] if message["role"] == "tool"]


def region_of(request: dict) -> str:
    """The region a recorder's task names."""
    return "east" if "east" in user_texts(request)[0] else "west"


def answered_question(request: dict, about: str) -> str | None:
    """The identifier of a question this episode was asked, or None.

    A question carries its identifier in its own text, because a model reads
    an inbox item's content and no other field.
    """
    for item in user_texts(request):
        if about in item and "reply_to " in item:
            return item.rsplit("reply_to ", 1)[1].strip().rstrip(".")
    return None


def own_question(request: dict) -> str | None:
    """The identifier `ask` returned to this episode, or None."""
    for rendered in results(request):
        if rendered.startswith("sent to ") and " as " in rendered:
            return rendered.rsplit(" as ", 1)[1].strip()
    return None


def answer_text(request: dict, asked: str) -> str | None:
    """The date the answer to this episode's question carries, or None.

    The lead's reply and the default the runtime delivers arrive under the
    same identifier and say the same date, which is the point: a recorder
    reads one answer and never learns which route it took.
    """
    for item in user_texts(request):
        found = DATE_PATTERN.search(item)
        if found is not None and asked not in item:
            return found.group(0)
    return None


def lead(request: dict) -> list[dict]:
    """Spawn both recorders, answer the eastern question, then settle."""
    chunks: list[dict] = []
    if step(request) == 0:
        for region in ("east", "west"):
            call(
                chunks,
                f"tc_spawn_{region}",
                "spawn",
                {
                    "contract": "recorder",
                    "task": f"Record the header in notes/{region}/notes.txt.",
                    "name": region,
                    "context": "fresh",
                    "write": [f"notes/{region}"],
                },
            )
        done(chunks, "tool")
        return chunks
    asked = answered_question(request, "east")
    if asked is None:
        # A wait on an arrival states how long it blocks. The western
        # question may arrive first; it is not the one this lead answers, so
        # the lead waits again.
        call(
            chunks,
            f"tc_await_question_{step(request)}",
            "wait",
            {"until": [{"inbox": "request"}], "timeout_seconds": 30},
        )
        done(chunks, "tool")
        return chunks
    if not called(request, "send"):
        call(chunks, "tc_answer_east", "send", {"to": "east", "content": DATE, "reply_to": asked})
        done(chunks, "tool")
        return chunks
    if not any(tool_call["name"] == "wait" and not tool_call["args"] for tool_call in calls(request)):
        call(chunks, "tc_settle", "wait", {})
        done(chunks, "tool")
        return chunks
    if sum(item.count(" ended: ") for item in user_texts(request)) < 2:
        error(chunks, "wait returned before both recorders had ended")
        return chunks
    text(chunks, f"Both regions recorded {DATE}: one from this lead's reply, one from its own default.")
    done(chunks, "end")
    return chunks


def recorder(request: dict) -> list[dict]:
    """Ask the lead for the date, wait for one answer, write the header."""
    chunks: list[dict] = []
    region = region_of(request)
    if not called(request, "ask"):
        call(
            chunks,
            "tc_ask",
            "ask",
            {
                "to": "notes-lead",
                "content": f"The {region} header records a date. Which date?",
                "deadline_ms": DEADLINE_MS[region],
                "default": DATE,
            },
        )
        done(chunks, "tool")
        return chunks
    asked = own_question(request)
    if asked is None:
        error(chunks, "ask returned no message identifier")
        return chunks
    date = answer_text(request, asked)
    if date is None:
        # The reply condition names this question, so the wait returns for
        # its answer and not for any arrival.
        call(
            chunks,
            f"tc_await_answer_{step(request)}",
            "wait",
            {"until": [{"reply": asked}], "timeout_seconds": 30},
        )
        done(chunks, "tool")
        return chunks
    if not called(request, "edit"):
        header = f"# recorded {date} {region}"
        call(
            chunks,
            "tc_header",
            "edit",
            {
                "path": f"notes/{region}/notes.txt",
                "edits": [{"old_text": FIRST_LINE[region], "new_text": f"{header}\n{FIRST_LINE[region]}"}],
            },
        )
        done(chunks, "tool")
        return chunks
    if not called(request, "notify"):
        call(chunks, "tc_report", "notify", {"content": f"recorded {date} in the {region} header"})
        done(chunks, "tool")
        return chunks
    text(chunks, f"Wrote the {region} header.")
    done(chunks, "end")
    return chunks


def respond(request: dict) -> list[dict]:
    """Select the lead or a recorder by the tools offered to the episode."""
    return lead(request) if "spawn" in tool_names(request) else recorder(request)
