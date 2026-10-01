#!/usr/bin/python3
"""A metering proxy between a harness under evaluation and its model endpoint.

The cross-harness comparison needs one instrument that measures every
harness's model spend the same way and enforces the same aggregate budget on
each, whatever budget the harness itself keeps. This proxy is that
instrument. A harness is pointed at `http://HOST:PORT/attempt/<id>/v1` in
place of the provider's origin, and every request it makes under that prefix
is forwarded to the upstream origin at `/v1/<endpoint>`, whatever the
endpoint, with its body and headers intact except for the hop-by-hop
headers, `Host`, and `Accept-Encoding`. The response streams back as it
arrives, server-sent events included. When the upstream ends a response
early or stops sending for longer than the upstream timeout, the proxy
charges and records the bytes that arrived and the client receives those
same bytes followed by the end of the connection.

For each attempt the proxy keeps the sum of the usage the upstream reported:
input tokens, output tokens, and cache-read tokens. The usage of a JSON body
is its `usage` object; the usage of an event stream is the last event that
carries one, which is the final chunk of a chat completion stream or the
`response.completed` event of a responses stream. Once the input or output
sum has reached the attempt's budget for that dimension, every later request
of the attempt is refused with status 429 and a JSON error that names the
attempt and the exhausted dimension. A request already in flight is never
cut short.

Every request and every response is written under the record directory as
`<attempt>/<n>.request.json` and `<attempt>/<n>.response.txt`, numbered in
arrival order, refused requests included. The response record is the status
line, the headers, and the body bytes as the upstream sent them. The request
record keeps the body as parsed JSON when it is JSON and as text otherwise,
and replaces the value of a credential header with a marker, so that a
record tree can be shared without sharing the key that produced it.

The control endpoint `/control/attempt/<id>` sets an attempt's budget on
POST and returns its sums on GET. The sums come with the counts of requests
forwarded, requests refused, forwarded responses that reported usage
(`metered`), and successful responses that reported none (`unmetered`).
`Proxy` wraps both for a runner that starts the proxy in a thread of its own
process; a control document the server refuses raises `ControlError` with
the server's own message.
"""

from __future__ import annotations

import argparse
import http.client
import json
import re
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, TextIO
from urllib.parse import urlsplit

DIMENSIONS = ("input_tokens", "output_tokens")
REDACTED = "<redacted>"

_ATTEMPT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MODEL_PATH = re.compile(r"^/attempt/([^/]+)/v1/(.+)$")
_CONTROL_PATH = re.compile(r"^/control/attempt/([^/]+)$")

# Request headers the proxy does not forward: the ones that describe a single
# hop, `Host`, which names the upstream once the proxy connects, and
# `Accept-Encoding`, dropped so that the upstream connection asks for
# `identity` and the proxy can read the usage from the body it relays.
_UNFORWARDED = frozenset({"host", "connection", "keep-alive", "proxy-connection", "te", "upgrade", "transfer-encoding", "content-length", "accept-encoding"})
# Response headers the proxy does not relay, because it frames the body it
# sends by itself: with the upstream's content length when it stated one and
# by closing the connection otherwise.
_UNRELAYED = frozenset({"connection", "keep-alive", "transfer-encoding", "content-length"})
_CREDENTIAL_HEADERS = frozenset({"authorization", "x-api-key", "api-key"})


@dataclass(frozen=True)
class Usage:
    """Token counts one response reported, or the sum over several."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
        )

    def to_dict(self) -> dict[str, int]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens, "cache_read_tokens": self.cache_read_tokens}


def _count(usage: dict[str, Any], *keys: str) -> int:
    """The value of the first of `keys` present in a usage object."""
    for key in keys:
        if key in usage and usage[key] is not None:
            value = usage[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"usage.{key}: expected a non-negative integer, found {value!r}")
            return value
    raise ValueError(f"usage: none of {', '.join(keys)} is present")


def _cache_read(usage: dict[str, Any]) -> int:
    for key in ("input_tokens_details", "prompt_tokens_details"):
        details = usage.get(key)
        if isinstance(details, dict) and details.get("cached_tokens") is not None:
            return _count(details, "cached_tokens")
    return 0


def usage_from_object(document: Any) -> Usage | None:
    """The usage a decoded JSON document reports, or None when it reports none.

    A chat completion and a response object carry `usage` at the top level;
    the `response.completed` event of a responses stream carries it under
    `response`. Input is `input_tokens` or `prompt_tokens`, output is
    `output_tokens` or `completion_tokens`, and cache reads are the
    `cached_tokens` of the input details when present.
    """
    if not isinstance(document, dict):
        return None
    usage = document.get("usage")
    if usage is None and isinstance(document.get("response"), dict):
        usage = document["response"].get("usage")
    if usage is None:
        return None
    if not isinstance(usage, dict):
        raise ValueError(f"usage: expected an object, found {type(usage).__name__}")
    return Usage(_count(usage, "input_tokens", "prompt_tokens"), _count(usage, "output_tokens", "completion_tokens"), _cache_read(usage))


def usage_from_sse(text: str) -> Usage | None:
    """The usage of the last event in a server-sent event stream that reports one.

    An event is the `data:` lines between blank lines, joined by newlines.
    The `[DONE]` sentinel and events whose data is not JSON carry no usage.
    """
    found: Usage | None = None
    for block in re.split(r"\r?\n\r?\n", text):
        data = "\n".join(line[5:].lstrip() for line in block.splitlines() if line.startswith("data:"))
        if not data or data.strip() == "[DONE]":
            continue
        try:
            document = json.loads(data)
        except json.JSONDecodeError:
            continue
        usage = usage_from_object(document)
        if usage is not None:
            found = usage
    return found


def usage_from_response(content_type: str, body: bytes) -> Usage | None:
    """The usage a response body reports, by its content type."""
    text = body.decode("utf-8", errors="replace")
    if content_type.split(";")[0].strip().lower() == "text/event-stream":
        return usage_from_sse(text)
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"response body with content type {content_type!r} is not JSON: {exc}") from exc
    return usage_from_object(document)


def check_attempt_name(name: str) -> str:
    """The name unchanged when it can be a directory name under the record root."""
    if not _ATTEMPT_NAME.match(name) or name in (".", ".."):
        raise ValueError(f"attempt {name!r}: a name is 1 to 128 characters of letters, digits, '.', '_', and '-', starting with a letter or digit")
    return name


def budget_from_document(document: Any, current: dict[str, int | None]) -> dict[str, int | None]:
    """The budget a control document sets: a dimension absent from it stays as it is, null lifts the limit."""
    if not isinstance(document, dict):
        raise ValueError("control document: expected an object with input_tokens and output_tokens")
    budget = dict(current)
    for key, value in document.items():
        if key not in DIMENSIONS:
            raise ValueError(f"control document: unknown key {key!r}; the keys are {', '.join(DIMENSIONS)}")
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise ValueError(f"control document: {key} must be a non-negative integer or null, found {value!r}")
        budget[key] = value
    return budget


@dataclass
class Attempt:
    """One attempt's budget and the sums the proxy has charged against it."""

    name: str
    budget: dict[str, int | None]
    used: Usage = Usage()
    # Requests forwarded to the upstream, requests refused for budget,
    # forwarded requests whose response reported usage, and forwarded
    # requests whose successful response reported none. A response that is
    # neither successful nor metered, such as a 401 or a 502, counts in
    # `requests` alone.
    requests: int = 0
    refused: int = 0
    metered: int = 0
    unmetered: int = 0

    def exhausted(self) -> str | None:
        """The first dimension whose sum has reached its budget, or None."""
        for dimension in DIMENSIONS:
            limit = self.budget[dimension]
            if limit is not None and getattr(self.used, dimension) >= limit:
                return dimension
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.name,
            **self.used.to_dict(),
            "budget": dict(self.budget),
            "requests": self.requests,
            "refused": self.refused,
            "metered": self.metered,
            "unmetered": self.unmetered,
            "exhausted": self.exhausted(),
        }


class Meter:
    """The per-attempt sums, budgets, and record numbering, shared by the handler threads."""

    def __init__(self, record: Path, default_budget: dict[str, int | None]) -> None:
        self.record = record
        self.default_budget = {dimension: default_budget.get(dimension) for dimension in DIMENSIONS}
        self._attempts: dict[str, Attempt] = {}
        self._lock = threading.Lock()

    def _attempt(self, name: str) -> Attempt:
        if name not in self._attempts:
            self._attempts[name] = Attempt(name, dict(self.default_budget))
        return self._attempts[name]

    def set_budget(self, name: str, document: Any) -> dict[str, Any]:
        with self._lock:
            attempt = self._attempt(name)
            attempt.budget = budget_from_document(document, attempt.budget)
            return attempt.to_dict()

    def usage(self, name: str) -> dict[str, Any]:
        with self._lock:
            return self._attempt(name).to_dict()

    def open_request(self, name: str) -> tuple[int, str | None]:
        """The record number of a request that has arrived, and the exhausted dimension when it is refused."""
        with self._lock:
            attempt = self._attempt(name)
            exhausted = attempt.exhausted()
            if exhausted is None:
                attempt.requests += 1
            else:
                attempt.refused += 1
            number = attempt.requests + attempt.refused
        directory = self.record / name
        directory.mkdir(parents=True, exist_ok=True)
        return number, exhausted

    def charge(self, name: str, usage: Usage | None, succeeded: bool) -> None:
        """Adds a forwarded response's usage; a successful response without usage counts as unmetered."""
        with self._lock:
            attempt = self._attempt(name)
            if usage is not None:
                attempt.used = attempt.used + usage
                attempt.metered += 1
            elif succeeded:
                attempt.unmetered += 1

    def record_path(self, name: str, number: int, kind: str) -> Path:
        return self.record / name / f"{number:04d}.{kind}"


class ProxyServer(ThreadingHTTPServer):
    """The listening server; `meter` and the upstream location are what the handlers share."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, listen: tuple[str, int], upstream: str, meter: Meter, timeout: float, log: TextIO | None) -> None:
        parts = urlsplit(upstream)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError(f"upstream {upstream!r}: expected http://HOST[:PORT][/PATH] or https://HOST[:PORT][/PATH]")
        if parts.path.rstrip("/").endswith("/v1"):
            raise ValueError(f"upstream {upstream!r} ends with /v1; pass the origin, the proxy appends /v1/<endpoint> itself")
        if parts.query or parts.fragment:
            raise ValueError(f"upstream {upstream!r}: a query or fragment has no place in an origin")
        self.upstream = upstream
        self.upstream_scheme = parts.scheme
        self.upstream_netloc = parts.netloc
        self.upstream_path = parts.path.rstrip("/")
        self.meter = meter
        self.upstream_timeout = timeout
        self.log = log
        super().__init__(listen, Handler)

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}"

    def connect(self) -> http.client.HTTPConnection:
        if self.upstream_scheme == "https":
            return http.client.HTTPSConnection(self.upstream_netloc, timeout=self.upstream_timeout)
        return http.client.HTTPConnection(self.upstream_netloc, timeout=self.upstream_timeout)

    def note(self, line: str) -> None:
        if self.log is not None:
            print(line, file=self.log, flush=True)


class Handler(BaseHTTPRequestHandler):
    server: ProxyServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # The record directory is the log of what passed; the default
        # per-request line on stderr adds nothing to it.
        return

    def _send(self, status: int, body: bytes) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
        except OSError:
            # The client left before the reply; the record and the sums
            # already hold what the reply would have told it.
            return

    def _reply(self, status: int, payload: dict[str, Any]) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"))

    @staticmethod
    def _error_body(message: str, **fields: Any) -> bytes:
        return json.dumps({"error": {"message": message, "type": fields.pop("type", "proxy_error"), **fields}}).encode("utf-8")

    def _error(self, status: int, message: str, **fields: Any) -> None:
        self._send(status, self._error_body(message, **fields))

    def _read_body(self) -> bytes | None:
        length = self.headers.get("Content-Length")
        if length is None:
            if self.headers.get("Transfer-Encoding"):
                self._error(411, f"{self.path}: the proxy reads a request body by its Content-Length; a chunked request body is refused")
                return None
            return b""
        try:
            size = int(length)
        except ValueError:
            size = -1
        if size < 0:
            self._error(400, f"{self.path}: Content-Length {length!r} is not a non-negative integer")
            return None
        return self.rfile.read(size)

    def do_GET(self) -> None:  # noqa: N802
        control = _CONTROL_PATH.match(self.path)
        if control is None:
            self._error(404, f"{self.path}: no such path; GET serves /control/attempt/<id>")
            return
        try:
            name = check_attempt_name(control.group(1))
        except ValueError as exc:
            self._error(400, str(exc))
            return
        self._reply(200, self.server.meter.usage(name))

    def do_POST(self) -> None:  # noqa: N802
        control = _CONTROL_PATH.match(self.path)
        model = _MODEL_PATH.match(self.path)
        if control is not None:
            self._control(control.group(1))
        elif model is not None:
            self._forward(model.group(1), model.group(2))
        else:
            self._error(404, f"{self.path}: no such path; POST serves /control/attempt/<id> and /attempt/<id>/v1/<endpoint>")

    def _control(self, raw_name: str) -> None:
        body = self._read_body()
        if body is None:
            return
        try:
            name = check_attempt_name(raw_name)
            document = json.loads(body.decode("utf-8"))
            self._reply(200, self.server.meter.set_budget(name, document))
        except (ValueError, UnicodeDecodeError) as exc:
            self._error(400, f"{self.path}: {exc}")

    def _forward(self, raw_name: str, endpoint: str) -> None:
        try:
            name = check_attempt_name(raw_name)
        except ValueError as exc:
            self._error(400, str(exc))
            return
        body = self._read_body()
        if body is None:
            return
        meter = self.server.meter
        number, exhausted = meter.open_request(name)
        upstream_url = f"{self.server.upstream_scheme}://{self.server.upstream_netloc}{self.server.upstream_path}/v1/{endpoint}"
        headers = {key: value for key, value in self.headers.items() if key.lower() not in _UNFORWARDED}
        headers["Content-Length"] = str(len(body))
        self._record_request(name, number, upstream_url, headers, body, exhausted)

        if exhausted is not None:
            usage = meter.usage(name)
            refusal = self._error_body(
                f"attempt {name}: the {exhausted} budget of {usage['budget'][exhausted]} is exhausted, {usage[exhausted]} used",
                type="budget_exhausted",
                attempt=name,
                dimension=exhausted,
                budget=usage["budget"][exhausted],
                used=usage[exhausted],
            )
            self._record_response(name, number, 429, "Too Many Requests", [("Content-Type", "application/json")], refusal)
            self.server.note(f"{name} {number:04d} refused: {exhausted} exhausted")
            self._send(429, refusal)
            return

        try:
            connection = self.server.connect()
            connection.request("POST", f"{self.server.upstream_path}/v1/{endpoint}", body=body, headers=headers)
            response = connection.getresponse()
        except (OSError, http.client.HTTPException) as exc:
            failure = self._error_body(f"attempt {name}: the upstream {upstream_url} did not answer: {exc}", type="upstream_unreachable", attempt=name)
            self._record_response(name, number, 502, "Bad Gateway", [("Content-Type", "application/json")], failure)
            meter.charge(name, None, False)
            self.server.note(f"{name} {number:04d} upstream unreachable: {exc}")
            self._send(502, failure)
            return

        content_type = response.getheader("Content-Type", "")
        succeeded = 200 <= response.status < 300

        def settle(received: bytes, ended_early: str | None) -> None:
            usage: Usage | None = None
            if succeeded:
                try:
                    usage = usage_from_response(content_type, received)
                except ValueError as exc:
                    self.server.note(f"{name} {number:04d}: usage unreadable: {exc}")
            meter.charge(name, usage, succeeded)
            self._record_response(name, number, response.status, response.reason, response.getheaders(), received)
            line = f"{name} {number:04d} {response.status} " + ("no usage" if usage is None else json.dumps(usage.to_dict()))
            if ended_early is not None:
                line += f"; the upstream ended the response early: {ended_early}"
            self.server.note(line)

        try:
            self._relay(response, settle)
        finally:
            connection.close()

    def _relay(self, response: http.client.HTTPResponse, settle: Callable[[bytes, str | None], None]) -> None:
        """Sends the upstream response to the client as it arrives.

        `settle` receives the whole body before the final chunk is written, so
        that a client which sends its next request or reads the record the
        moment this response ends finds the sum and the record already
        updated. A client that closes early stops receiving; the upstream is
        still read to the end so that the usage it reports is charged and
        recorded. When the upstream closes before the body is complete or
        stops sending for longer than the upstream timeout, `settle` receives
        the bytes that arrived and the reason the body ended, and the client
        receives those bytes and then the end of the connection.
        """
        # `send_response_only` leaves the `Server` and `Date` headers to the
        # upstream, whose values are relayed below.
        self.send_response_only(response.status, response.reason)
        for key, value in response.getheaders():
            if key.lower() not in _UNRELAYED:
                self.send_header(key, value)
        declared: int | None = None
        length = response.getheader("Content-Length")
        if length is not None and response.getheader("Transfer-Encoding") is None:
            self.send_header("Content-Length", length)
            declared = int(length) if length.isdigit() else None
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.flush()
        parts: list[bytes] = []
        client_open = True

        def send(chunk: bytes) -> None:
            nonlocal client_open
            if not client_open:
                return
            try:
                self.wfile.write(chunk)
                self.wfile.flush()
            except OSError:
                client_open = False

        # One chunk stays held back until the next arrives or the stream ends.
        held: bytes | None = None
        ended_early: str | None = None
        try:
            while True:
                chunk = response.read1(65536)
                if not chunk:
                    break
                parts.append(chunk)
                if held is not None:
                    send(held)
                held = chunk
        except (OSError, http.client.HTTPException) as exc:
            ended_early = f"{type(exc).__name__}: {exc}"
        received = b"".join(parts)
        if ended_early is None and declared is not None and len(received) < declared:
            # A body cut short of its declared length reaches here as a plain
            # end of stream; the shortfall is the only sign of it.
            ended_early = f"{len(received)} of the declared {declared} bytes arrived"
        settle(received, ended_early)
        if held is not None:
            send(held)

    def _record_request(self, name: str, number: int, upstream_url: str, headers: dict[str, str], body: bytes, refused: str | None) -> None:
        text = body.decode("utf-8", errors="replace")
        try:
            content: Any = json.loads(text)
        except json.JSONDecodeError:
            content = text
        record = {
            "attempt": name,
            "number": number,
            "method": "POST",
            "path": self.path,
            "upstream": upstream_url,
            "headers": {key: (REDACTED if key.lower() in _CREDENTIAL_HEADERS else value) for key, value in headers.items()},
            "body": content,
            "refused": refused,
        }
        self.server.meter.record_path(name, number, "request.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    def _record_response(self, name: str, number: int, status: int, reason: str, headers: list[tuple[str, str]], body: bytes) -> None:
        head = f"HTTP/1.1 {status} {reason}\r\n" + "".join(f"{key}: {value}\r\n" for key, value in headers) + "\r\n"
        self.server.meter.record_path(name, number, "response.txt").write_bytes(head.encode("utf-8") + body)


def parse_listen(listen: str) -> tuple[str, int]:
    host, separator, port = listen.rpartition(":")
    if not separator or not host or not port.isdigit():
        raise ValueError(f"--listen {listen!r}: expected HOST:PORT")
    return host, int(port)


def serve(listen: str, upstream: str, record: Path, budget: dict[str, int | None], timeout: float = 600.0, log: TextIO | None = None) -> ProxyServer:
    """A bound server that has not started serving; `serve_forever` runs it."""
    record.mkdir(parents=True, exist_ok=True)
    return ProxyServer(parse_listen(listen), upstream, Meter(record, budget), timeout, log)


class ControlError(RuntimeError):
    """A control request the proxy refused; the message is the proxy's own and names the key or rule."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"metering proxy answered {status}: {message}")
        self.status = status
        self.message = message


class Proxy:
    """A client of one metering proxy, over the control endpoint.

    `Proxy.start` runs a server in a thread of the calling process and
    returns the client bound to it; `Proxy(url)` binds to a proxy that runs
    elsewhere. `stop` shuts down a server the client started and does
    nothing otherwise.
    """

    def __init__(self, url: str, server: ProxyServer | None = None) -> None:
        self.url = url.rstrip("/")
        parts = urlsplit(self.url)
        if parts.scheme != "http" or not parts.netloc:
            raise ValueError(f"proxy url {url!r}: expected http://HOST:PORT, the address the server prints")
        self._netloc = parts.netloc
        self._path = parts.path
        self._server = server
        self._thread: threading.Thread | None = None

    @classmethod
    def start(
        cls,
        upstream: str,
        record: Path,
        listen: str = "127.0.0.1:0",
        *,
        budget_input: int | None = None,
        budget_output: int | None = None,
        timeout: float = 600.0,
        log: TextIO | None = None,
    ) -> Proxy:
        server = serve(listen, upstream, record, {"input_tokens": budget_input, "output_tokens": budget_output}, timeout, log)
        proxy = cls(server.url, server)
        proxy._thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, name="metering-proxy", daemon=True)
        proxy._thread.start()
        return proxy

    def base_url(self, attempt: str) -> str:
        """The URL a harness uses in place of the provider origin plus `/v1`."""
        return f"{self.url}/attempt/{check_attempt_name(attempt)}/v1"

    def _control(self, attempt: str, document: dict[str, Any] | None) -> dict[str, Any]:
        # `http.client` connects to the address it is given; `urllib`'s
        # default opener consults proxy environment variables, which would
        # make the control client depend on the environment.
        path = f"{self._path}/control/attempt/{check_attempt_name(attempt)}"
        connection = http.client.HTTPConnection(self._netloc, timeout=30)
        try:
            if document is None:
                connection.request("GET", path)
            else:
                connection.request("POST", path, body=json.dumps(document).encode("utf-8"), headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            body = response.read()
        finally:
            connection.close()
        text = body.decode("utf-8", errors="replace")
        if response.status != 200:
            try:
                message = json.loads(text)["error"]["message"]
            except (ValueError, KeyError, TypeError):
                message = text
            raise ControlError(response.status, message)
        return json.loads(text)

    def set_budget(self, attempt: str, *, input_tokens: int | None, output_tokens: int | None) -> dict[str, Any]:
        """Sets both budgets of an attempt; None lifts the limit of that dimension."""
        return self._control(attempt, {"input_tokens": input_tokens, "output_tokens": output_tokens})

    def usage(self, attempt: str) -> dict[str, Any]:
        """The attempt's sums, budget, request counts, and exhausted dimension."""
        return self._control(attempt, None)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            if self._thread is not None:
                self._thread.join(timeout=10)
            self._server = None

    def __enter__(self) -> Proxy:
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--listen", required=True, help="HOST:PORT to bind; port 0 picks a free one, which the first output line names")
    parser.add_argument("--upstream", required=True, help="the provider origin, such as https://api.example.com; the proxy appends /v1/<endpoint>")
    parser.add_argument("--record", required=True, type=Path, help="where <attempt>/<n>.request.json and <n>.response.txt are written")
    parser.add_argument("--budget-input", type=int, default=None, help="input-token budget of every attempt the control endpoint has not set")
    parser.add_argument("--budget-output", type=int, default=None, help="output-token budget of every attempt the control endpoint has not set")
    parser.add_argument("--upstream-timeout", type=float, default=600.0, help="seconds to wait for the upstream to connect or to send more")
    args = parser.parse_args(argv)
    for name, value in (("--budget-input", args.budget_input), ("--budget-output", args.budget_output)):
        if value is not None and value < 0:
            print(f"metering proxy: {name} must be a non-negative integer, found {value}", file=sys.stderr)
            return 2
    try:
        server = serve(
            args.listen,
            args.upstream,
            args.record.resolve(),
            {"input_tokens": args.budget_input, "output_tokens": args.budget_output},
            args.upstream_timeout,
            sys.stderr,
        )
    except (ValueError, OSError) as exc:
        print(f"metering proxy: {exc}", file=sys.stderr)
        return 2
    print(f"listening {server.url}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
