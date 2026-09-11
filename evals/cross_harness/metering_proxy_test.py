#!/usr/bin/python3
"""Unit tests for the metering proxy: a fake upstream in a thread, no model, no network beyond loopback."""

from __future__ import annotations

import http.client
import json
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

import metering_proxy as proxy_module  # noqa: E402

CHAT_COMPLETION = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "prompt_tokens_details": {"cached_tokens": 4}},
}

RESPONSE_OBJECT = {
    "id": "resp_1",
    "object": "response",
    "output": [{"type": "message", "content": [{"type": "output_text", "text": "hello"}]}],
    "usage": {"input_tokens": 30, "output_tokens": 9, "total_tokens": 39, "input_tokens_details": {"cached_tokens": 12}},
}

RESPONSE_STREAM = (
    "event: response.created\n"
    'data: {"type":"response.created","response":{"id":"resp_1","usage":null}}\n'
    "\n"
    "event: response.output_text.delta\n"
    'data: {"type":"response.output_text.delta","delta":"hel"}\n'
    "\n"
    "event: response.output_text.delta\n"
    'data: {"type":"response.output_text.delta","delta":"lo"}\n'
    "\n"
    "event: response.completed\n"
    'data: {"type":"response.completed","response":{"id":"resp_1","usage":{"input_tokens":20,"output_tokens":7,'
    '"input_tokens_details":{"cached_tokens":0},"output_tokens_details":{"reasoning_tokens":2},"total_tokens":27}}}\n'
    "\n"
)

CHAT_STREAM = (
    'data: {"id":"chatcmpl-2","object":"chat.completion.chunk","choices":[{"delta":{"content":"hi"}}],"usage":null}\n\n'
    'data: {"id":"chatcmpl-2","object":"chat.completion.chunk","choices":[],"usage":{"prompt_tokens":3,"completion_tokens":1}}\n\n'
    "data: [DONE]\n\n"
)


class FakeUpstreamHandler(BaseHTTPRequestHandler):
    """Answers the model endpoints with canned bodies and keeps what it received.

    A request body's `fake` key selects a scenario other than the happy path:
    `no_usage` answers 200 without a usage object, `unauthorized` answers
    401, `cut` sends one stream event and closes the socket before the
    chunked terminator, and `stall` sends one stream event and then waits
    for the server's `release` event before sending the rest.
    """

    protocol_version = "HTTP/1.1"
    server: FakeUpstream

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def do_POST(self) -> None:  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.received.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
        document = json.loads(body) if body else {}
        scenario = document.get("fake")
        if scenario == "no_usage":
            self._json(200, {"id": "resp_2", "object": "response", "output": []})
        elif scenario == "unauthorized":
            self._json(401, {"error": {"message": "invalid api key", "type": "invalid_request_error"}})
        elif scenario in ("cut", "stall"):
            self._stream_interrupted(RESPONSE_STREAM, stall=scenario == "stall")
        elif self.path == "/v1/chat/completions":
            self._json(200, CHAT_COMPLETION)
        elif self.path == "/v1/responses" and document.get("stream"):
            self._stream(RESPONSE_STREAM)
        elif self.path == "/v1/responses":
            self._json(200, RESPONSE_OBJECT)
        elif self.path == "/v1/responses/compact":
            self._json(200, {"object": "response.compaction", "usage": {"input_tokens": 100, "output_tokens": 2}})
        else:
            self._json(404, {"error": {"message": f"no such path {self.path}"}})

    def _stream_interrupted(self, text: str, *, stall: bool) -> None:
        events = [(event + "\n\n").encode("utf-8") for event in text.split("\n\n")[:-1]]
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        self.wfile.write(f"{len(events[0]):x}\r\n".encode("ascii") + events[0] + b"\r\n")
        self.wfile.flush()
        if not stall:
            self.connection.shutdown(socket.SHUT_RDWR)
            return
        self.server.release.wait(timeout=10)
        try:
            for chunk in events[1:]:
                self.wfile.write(f"{len(chunk):x}\r\n".encode("ascii") + chunk + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except OSError:
            # The proxy has given up on this response by the time it is released.
            return

    def _json(self, status: int, document: dict[str, Any]) -> None:
        payload = json.dumps(document).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Upstream-Marker", "fake")
        self.end_headers()
        self.wfile.write(payload)

    def _stream(self, text: str) -> None:
        # Chunked transfer, one chunk per event, as a provider's stream arrives.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for event in text.split("\n\n")[:-1]:
            chunk = (event + "\n\n").encode("utf-8")
            self.wfile.write(f"{len(chunk):x}\r\n".encode("ascii") + chunk + b"\r\n")
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


class FakeUpstream(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self.release = threading.Event()
        super().__init__(("127.0.0.1", 0), FakeUpstreamHandler)

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}"


def post(url: str, document: dict[str, Any], headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
    """Posts a JSON document straight to `url`; `http.client` consults no proxy environment variable."""
    parts = urlsplit(url)
    connection = http.client.HTTPConnection(parts.netloc, timeout=10)
    try:
        connection.request("POST", parts.path, body=json.dumps(document).encode("utf-8"), headers={"Content-Type": "application/json", **(headers or {})})
        response = connection.getresponse()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, response.read()
    finally:
        connection.close()


class UsageParsing(unittest.TestCase):
    def test_a_chat_completion_and_a_response_object_report_usage_at_the_top(self) -> None:
        self.assertEqual(proxy_module.usage_from_object(CHAT_COMPLETION), proxy_module.Usage(10, 5, 4))
        self.assertEqual(proxy_module.usage_from_object(RESPONSE_OBJECT), proxy_module.Usage(30, 9, 12))

    def test_a_responses_stream_reports_usage_on_its_last_event(self) -> None:
        self.assertEqual(proxy_module.usage_from_sse(RESPONSE_STREAM), proxy_module.Usage(20, 7, 0))

    def test_a_chat_stream_reports_usage_before_the_done_sentinel(self) -> None:
        self.assertEqual(proxy_module.usage_from_sse(CHAT_STREAM), proxy_module.Usage(3, 1, 0))
        self.assertEqual(proxy_module.usage_from_sse(CHAT_STREAM.replace("\n\n", "\r\n\r\n")), proxy_module.Usage(3, 1, 0))

    def test_a_document_without_usage_reports_none(self) -> None:
        self.assertIsNone(proxy_module.usage_from_object({"id": "x"}))
        self.assertIsNone(proxy_module.usage_from_object({"usage": None}))
        self.assertIsNone(proxy_module.usage_from_object([1, 2]))
        self.assertIsNone(proxy_module.usage_from_sse("data: [DONE]\n\n"))

    def test_an_unreadable_usage_names_the_key(self) -> None:
        with self.assertRaises(ValueError) as caught:
            proxy_module.usage_from_object({"usage": {"output_tokens": 1}})
        self.assertIn("input_tokens", str(caught.exception))
        self.assertIn("prompt_tokens", str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            proxy_module.usage_from_object({"usage": {"input_tokens": -1, "output_tokens": 1}})
        self.assertIn("usage.input_tokens", str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            proxy_module.usage_from_response("text/html", b"<html>")
        self.assertIn("text/html", str(caught.exception))

    def test_the_content_type_selects_the_reader(self) -> None:
        self.assertEqual(proxy_module.usage_from_response("text/event-stream; charset=utf-8", RESPONSE_STREAM.encode()), proxy_module.Usage(20, 7, 0))
        self.assertEqual(proxy_module.usage_from_response("application/json", json.dumps(CHAT_COMPLETION).encode()), proxy_module.Usage(10, 5, 4))


class Names(unittest.TestCase):
    def test_an_attempt_name_is_a_safe_directory_name(self) -> None:
        for name in ("a", "task-1.attempt_2", "0"):
            self.assertEqual(proxy_module.check_attempt_name(name), name)
        for name in ("", "..", ".", "-x", "a/b", "a b", "x" * 129):
            with self.assertRaises(ValueError, msg=name):
                proxy_module.check_attempt_name(name)

    def test_a_budget_document_names_its_keys_and_keeps_absent_dimensions(self) -> None:
        current = {"input_tokens": 5, "output_tokens": None}
        self.assertEqual(proxy_module.budget_from_document({"output_tokens": 7}, current), {"input_tokens": 5, "output_tokens": 7})
        self.assertEqual(proxy_module.budget_from_document({"input_tokens": None}, current), {"input_tokens": None, "output_tokens": None})
        with self.assertRaises(ValueError) as caught:
            proxy_module.budget_from_document({"tokens": 1}, current)
        self.assertIn("tokens", str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            proxy_module.budget_from_document({"input_tokens": True}, current)
        self.assertIn("input_tokens", str(caught.exception))

    def test_an_upstream_that_already_ends_in_v1_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for upstream in ("http://127.0.0.1:1/v1", "http://127.0.0.1:1/v1/", "ftp://x", "http://h/?q=1"):
                with self.assertRaises(ValueError, msg=upstream):
                    proxy_module.serve("127.0.0.1:0", upstream, Path(tmp), {})


class _Log:
    """A write target for the proxy's log lines that keeps them in a list."""

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines

    def write(self, text: str) -> int:
        if text.strip():
            self.lines.append(text.strip())
        return len(text)

    def flush(self) -> None:
        return


class ThroughTheProxy(unittest.TestCase):
    def setUp(self) -> None:
        self.upstream = FakeUpstream()
        threading.Thread(target=self.upstream.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.tmp = tempfile.TemporaryDirectory()
        self.record = Path(self.tmp.name) / "record"
        self.proxy = proxy_module.Proxy.start(self.upstream.url, self.record)

    def tearDown(self) -> None:
        self.proxy.stop()
        self.upstream.shutdown()
        self.upstream.server_close()
        self.tmp.cleanup()

    def test_a_chat_completion_is_forwarded_recorded_and_metered(self) -> None:
        url = self.proxy.base_url("t1") + "/chat/completions"
        status, headers, body = post(url, {"model": "m", "messages": []}, {"Authorization": "Bearer sk-secret", "X-Custom": "kept", "Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), CHAT_COMPLETION)
        self.assertEqual(headers["x-upstream-marker"], "fake")
        self.assertEqual(headers["content-length"], str(len(body)))

        seen = self.upstream.received[0]
        self.assertEqual(seen["path"], "/v1/chat/completions")
        self.assertEqual(json.loads(seen["body"]), {"model": "m", "messages": []})
        self.assertEqual(seen["headers"]["authorization"], "Bearer sk-secret")
        self.assertEqual(seen["headers"]["x-custom"], "kept")
        self.assertEqual(seen["headers"]["host"], self.upstream.url.removeprefix("http://"))
        self.assertEqual(seen["headers"]["accept-encoding"], "identity", "the client's gzip request is replaced so the relayed body stays readable")

        request_record = json.loads((self.record / "t1" / "0001.request.json").read_text(encoding="utf-8"))
        self.assertEqual(request_record["body"], {"model": "m", "messages": []})
        self.assertEqual(request_record["upstream"], self.upstream.url + "/v1/chat/completions")
        self.assertEqual(request_record["headers"]["Authorization"], proxy_module.REDACTED)
        self.assertEqual(request_record["headers"]["X-Custom"], "kept")
        self.assertIsNone(request_record["refused"])
        response_record = (self.record / "t1" / "0001.response.txt").read_bytes()
        head, _, recorded_body = response_record.partition(b"\r\n\r\n")
        self.assertTrue(head.startswith(b"HTTP/1.1 200 OK\r\n"))
        self.assertIn(b"X-Upstream-Marker: fake", head)
        self.assertEqual(recorded_body, body)

        usage = self.proxy.usage("t1")
        self.assertEqual((usage["input_tokens"], usage["output_tokens"], usage["cache_read_tokens"]), (10, 5, 4))
        self.assertEqual((usage["requests"], usage["refused"], usage["metered"], usage["unmetered"]), (1, 0, 1, 0))
        self.assertIsNone(usage["exhausted"])

    def test_the_relayed_response_carries_each_header_once(self) -> None:
        connection = http.client.HTTPConnection(self.proxy.url.removeprefix("http://"), timeout=10)
        try:
            connection.request("POST", "/attempt/h/v1/chat/completions", body=b"{}", headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            response.read()
            names = [key.lower() for key, _ in response.getheaders()]
        finally:
            connection.close()
        for name in ("server", "date", "content-type", "content-length", "x-upstream-marker"):
            self.assertEqual(names.count(name), 1, f"{name} appears {names.count(name)} times in {names}")

    def test_a_successful_response_without_usage_counts_as_unmetered(self) -> None:
        status, _, body = post(self.proxy.base_url("u1") + "/responses", {"fake": "no_usage"})
        self.assertEqual(status, 200)
        self.assertNotIn("usage", json.loads(body))
        usage = self.proxy.usage("u1")
        self.assertEqual((usage["requests"], usage["refused"], usage["metered"], usage["unmetered"]), (1, 0, 0, 1))
        self.assertEqual((usage["input_tokens"], usage["output_tokens"]), (0, 0))
        self.assertTrue((self.record / "u1" / "0001.response.txt").read_bytes().startswith(b"HTTP/1.1 200 OK\r\n"))

    def test_a_non_2xx_upstream_response_is_relayed_recorded_and_charges_nothing(self) -> None:
        status, headers, body = post(self.proxy.base_url("u2") + "/chat/completions", {"fake": "unauthorized"})
        self.assertEqual(status, 401)
        self.assertEqual(headers["content-type"], "application/json")
        self.assertEqual(json.loads(body)["error"]["message"], "invalid api key")
        usage = self.proxy.usage("u2")
        self.assertEqual((usage["requests"], usage["refused"], usage["metered"], usage["unmetered"]), (1, 0, 0, 0))
        self.assertEqual((usage["input_tokens"], usage["output_tokens"]), (0, 0))
        recorded = (self.record / "u2" / "0001.response.txt").read_bytes()
        self.assertTrue(recorded.startswith(b"HTTP/1.1 401 Unauthorized\r\n"))
        self.assertTrue(recorded.endswith(body))

    def test_an_upstream_that_closes_before_the_stream_ends_is_settled_and_recorded(self) -> None:
        log: list[str] = []
        proxy = proxy_module.Proxy.start(self.upstream.url, Path(self.tmp.name) / "cut", log=_Log(log))
        try:
            status, _, body = post(proxy.base_url("c") + "/responses", {"fake": "cut", "stream": True})
            self.assertEqual(status, 200)
            first_event = (RESPONSE_STREAM.split("\n\n")[0] + "\n\n").encode("utf-8")
            self.assertEqual(body, first_event)
            usage = proxy.usage("c")
            self.assertEqual((usage["requests"], usage["metered"], usage["unmetered"]), (1, 0, 1))
            self.assertEqual((usage["input_tokens"], usage["output_tokens"]), (0, 0))
            recorded = (Path(self.tmp.name) / "cut" / "c" / "0001.response.txt").read_bytes()
            self.assertTrue(recorded.startswith(b"HTTP/1.1 200 OK\r\n"))
            self.assertTrue(recorded.endswith(first_event))
            self.assertTrue(any("ended the response early" in line and "IncompleteRead" in line for line in log), log)
        finally:
            proxy.stop()

    def test_an_upstream_that_stalls_past_the_timeout_is_settled_and_recorded(self) -> None:
        log: list[str] = []
        proxy = proxy_module.Proxy.start(self.upstream.url, Path(self.tmp.name) / "stall", timeout=0.5, log=_Log(log))
        try:
            status, _, body = post(proxy.base_url("s") + "/responses", {"fake": "stall", "stream": True})
            self.assertEqual(status, 200)
            self.assertEqual(body, (RESPONSE_STREAM.split("\n\n")[0] + "\n\n").encode("utf-8"))
            usage = proxy.usage("s")
            self.assertEqual((usage["requests"], usage["metered"], usage["unmetered"]), (1, 0, 1))
            self.assertTrue((Path(self.tmp.name) / "stall" / "s" / "0001.response.txt").exists())
            self.assertTrue(any("ended the response early" in line and "timed out" in line for line in log), log)
        finally:
            self.upstream.release.set()
            proxy.stop()

    def test_the_sum_and_the_record_are_settled_when_the_body_ends(self) -> None:
        # The final chunk is written only after the sum and the record are
        # updated, so the moment the client's read returns both are there.
        for number in range(1, 6):
            connection = http.client.HTTPConnection(self.proxy.url.removeprefix("http://"), timeout=10)
            try:
                connection.request("POST", "/attempt/order/v1/responses", body=b'{"stream": true}', headers={"Content-Type": "application/json"})
                response = connection.getresponse()
                response.read()
            finally:
                connection.close()
            self.assertTrue((self.record / "order" / f"{number:04d}.response.txt").exists())
            self.assertEqual(self.proxy.usage("order")["input_tokens"], 20 * number)

    def test_an_event_stream_is_relayed_and_its_final_usage_charged(self) -> None:
        url = self.proxy.base_url("t2") + "/responses"
        status, headers, body = post(url, {"model": "m", "input": "x", "stream": True})
        self.assertEqual(status, 200)
        self.assertTrue(headers["content-type"].startswith("text/event-stream"))
        self.assertNotIn("content-length", headers)
        self.assertNotIn("transfer-encoding", headers)
        self.assertEqual(body.decode("utf-8"), RESPONSE_STREAM)
        recorded = (self.record / "t2" / "0001.response.txt").read_bytes()
        self.assertTrue(recorded.endswith(RESPONSE_STREAM.encode("utf-8")))
        usage = self.proxy.usage("t2")
        self.assertEqual((usage["input_tokens"], usage["output_tokens"], usage["cache_read_tokens"]), (20, 7, 0))

    def test_sums_accumulate_across_endpoints_and_attempts_stay_apart(self) -> None:
        post(self.proxy.base_url("a") + "/chat/completions", {})
        post(self.proxy.base_url("a") + "/responses", {"stream": True})
        post(self.proxy.base_url("a") + "/responses", {})
        post(self.proxy.base_url("b") + "/chat/completions", {})
        a, b = self.proxy.usage("a"), self.proxy.usage("b")
        self.assertEqual((a["input_tokens"], a["output_tokens"], a["cache_read_tokens"], a["requests"]), (60, 21, 16, 3))
        self.assertEqual((b["input_tokens"], b["output_tokens"], b["requests"]), (10, 5, 1))
        self.assertEqual(sorted(p.name for p in (self.record / "a").iterdir()), [f"{n:04d}.{k}" for n in (1, 2, 3) for k in ("request.json", "response.txt")])

    def test_a_request_after_the_input_budget_is_reached_is_refused(self) -> None:
        set_result = self.proxy.set_budget("t3", input_tokens=15, output_tokens=None)
        self.assertEqual(set_result["budget"], {"input_tokens": 15, "output_tokens": None})
        url = self.proxy.base_url("t3") + "/chat/completions"
        self.assertEqual(post(url, {})[0], 200)
        self.assertIsNone(self.proxy.usage("t3")["exhausted"])
        self.assertEqual(post(url, {})[0], 200, "a request that starts under the budget is served even when its usage crosses it")
        self.assertEqual(self.proxy.usage("t3")["exhausted"], "input_tokens")

        status, headers, body = post(url, {"model": "m"})
        self.assertEqual(status, 429)
        self.assertEqual(headers["content-type"], "application/json")
        error = json.loads(body)["error"]
        self.assertEqual((error["type"], error["attempt"], error["dimension"], error["budget"], error["used"]), ("budget_exhausted", "t3", "input_tokens", 15, 20))
        self.assertIn("t3", error["message"])
        self.assertIn("input_tokens", error["message"])
        self.assertEqual(len(self.upstream.received), 2, "the refused request never reached the upstream")

        usage = self.proxy.usage("t3")
        self.assertEqual((usage["requests"], usage["refused"], usage["input_tokens"]), (2, 1, 20))
        refused_record = json.loads((self.record / "t3" / "0003.request.json").read_text(encoding="utf-8"))
        self.assertEqual(refused_record["refused"], "input_tokens")
        self.assertEqual(refused_record["body"], {"model": "m"})
        self.assertTrue((self.record / "t3" / "0003.response.txt").read_bytes().startswith(b"HTTP/1.1 429 Too Many Requests\r\n"))

    def test_the_output_dimension_is_enforced_and_a_lifted_budget_admits_again(self) -> None:
        self.proxy.set_budget("t4", input_tokens=None, output_tokens=5)
        url = self.proxy.base_url("t4") + "/chat/completions"
        self.assertEqual(post(url, {})[0], 200)
        status, _, body = post(url, {})
        self.assertEqual(status, 429)
        self.assertEqual(json.loads(body)["error"]["dimension"], "output_tokens")
        self.proxy.set_budget("t4", input_tokens=None, output_tokens=None)
        self.assertEqual(post(url, {})[0], 200)
        self.assertEqual(self.proxy.usage("t4")["output_tokens"], 10)

    def test_a_default_budget_applies_to_every_attempt_the_control_endpoint_has_not_set(self) -> None:
        proxy = proxy_module.Proxy.start(self.upstream.url, Path(self.tmp.name) / "defaults", budget_input=10, budget_output=None)
        try:
            url = proxy.base_url("d") + "/chat/completions"
            self.assertEqual(post(url, {})[0], 200)
            self.assertEqual(post(url, {})[0], 429)
            proxy.set_budget("e", input_tokens=None, output_tokens=None)
            self.assertEqual(proxy.usage("e")["budget"], {"input_tokens": None, "output_tokens": None})
            self.assertEqual(proxy.usage("f")["budget"], {"input_tokens": 10, "output_tokens": None})
        finally:
            proxy.stop()

    def test_every_path_under_v1_is_forwarded_and_recorded(self) -> None:
        status, _, body = post(self.proxy.base_url("t5") + "/responses/compact", {"model": "m"})
        self.assertEqual(status, 200)
        self.assertEqual(self.upstream.received[-1]["path"], "/v1/responses/compact")
        self.assertEqual(json.loads(body)["object"], "response.compaction")
        status, _, body = post(self.proxy.base_url("t5") + "/models", {})
        self.assertEqual(status, 404, "the upstream's answer, relayed")
        self.assertEqual(self.upstream.received[-1]["path"], "/v1/models")
        self.assertIn("/v1/models", json.loads(body)["error"]["message"])
        usage = self.proxy.usage("t5")
        self.assertEqual((usage["requests"], usage["metered"], usage["unmetered"], usage["input_tokens"]), (2, 1, 0, 100))
        self.assertEqual(sorted(p.name for p in (self.record / "t5").iterdir()), [f"{n:04d}.{k}" for n in (1, 2) for k in ("request.json", "response.txt")])

    def test_a_negative_content_length_is_refused_and_names_the_header(self) -> None:
        host, port = self.proxy.url.removeprefix("http://").rsplit(":", 1)
        with socket.create_connection((host, int(port)), timeout=5) as client:
            client.sendall(b"POST /attempt/neg/v1/responses HTTP/1.1\r\nHost: x\r\nContent-Length: -1\r\n\r\n")
            reply = b""
            while not reply.endswith(b"}"):
                chunk = client.recv(65536)
                if not chunk:
                    break
                reply += chunk
        head, _, body = reply.partition(b"\r\n\r\n")
        self.assertEqual(head.split(b" ", 2)[1], b"400", head)
        self.assertIn("Content-Length '-1'", json.loads(body)["error"]["message"])
        self.assertEqual(self.upstream.received, [])
        self.assertFalse((self.record / "neg").exists())

    def test_an_unknown_path_and_a_bad_name_name_the_rule(self) -> None:
        status, _, body = post(self.proxy.url + "/v1/chat/completions", {})
        self.assertEqual(status, 404)
        self.assertIn("/attempt/<id>/v1/<endpoint>", json.loads(body)["error"]["message"])
        status, _, body = post(self.proxy.url + "/attempt/..%2Fx/v1/responses", {})
        self.assertEqual(status, 400)
        self.assertIn("attempt", json.loads(body)["error"]["message"])
        status, _, body = post(self.proxy.url + "/control/attempt/t5", {"tokens": 1})
        self.assertEqual(status, 400)
        self.assertIn("'tokens'", json.loads(body)["error"]["message"])

    def test_a_refused_control_document_reaches_the_client_with_the_server_message(self) -> None:
        with self.assertRaises(proxy_module.ControlError) as caught:
            self.proxy._control("bd", {"tokens": 1})
        self.assertEqual(caught.exception.status, 400)
        self.assertIn("'tokens'", str(caught.exception))
        self.assertIn("input_tokens, output_tokens", caught.exception.message)
        with self.assertRaises(ValueError):
            proxy_module.Proxy("https://127.0.0.1:1")

    def test_the_control_client_consults_no_proxy_environment_variable(self) -> None:
        # A child process with http_proxy pointing at a closed port must still
        # reach the loopback proxy, as the harness runner does on such a host.
        script = "import json, sys; sys.path.insert(0, sys.argv[1]); import metering_proxy; print(json.dumps(metering_proxy.Proxy(sys.argv[2]).usage('env')))"
        completed = subprocess.run(
            ["/usr/bin/python3", "-c", script, str(Path(__file__).resolve().parent), self.proxy.url],
            env={"http_proxy": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9"},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["attempt"], "env")

    def test_an_unreachable_upstream_answers_502_and_charges_nothing(self) -> None:
        self.upstream.shutdown()
        self.upstream.server_close()
        status, _, body = post(self.proxy.base_url("t6") + "/chat/completions", {})
        self.assertEqual(status, 502)
        error = json.loads(body)["error"]
        self.assertEqual(error["type"], "upstream_unreachable")
        self.assertIn(self.upstream.url, error["message"])
        usage = self.proxy.usage("t6")
        self.assertEqual((usage["requests"], usage["input_tokens"], usage["metered"], usage["unmetered"]), (1, 0, 0, 0))
        self.assertTrue((self.record / "t6" / "0001.response.txt").read_bytes().startswith(b"HTTP/1.1 502"))


class CommandLine(unittest.TestCase):
    def test_the_process_names_its_address_and_serves_the_control_endpoint(self) -> None:
        upstream = FakeUpstream()
        threading.Thread(target=upstream.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        with tempfile.TemporaryDirectory() as tmp:
            command = [
                "/usr/bin/python3",
                str(Path(__file__).resolve().parent / "metering_proxy.py"),
                "--listen",
                "127.0.0.1:0",
                "--upstream",
                upstream.url,
                "--record",
                str(Path(tmp) / "record"),
                "--budget-output",
                "5",
            ]
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                first = process.stdout.readline().strip()
                self.assertTrue(first.startswith("listening http://127.0.0.1:"), first)
                url = first.split(" ", 1)[1]
                client = proxy_module.Proxy(url)
                self.assertEqual(post(client.base_url("c") + "/chat/completions", {})[0], 200)
                self.assertEqual(post(client.base_url("c") + "/chat/completions", {})[0], 429)
                self.assertEqual(client.usage("c")["exhausted"], "output_tokens")
                client.set_budget("c", input_tokens=None, output_tokens=None)
                self.assertEqual(post(client.base_url("c") + "/chat/completions", {})[0], 200)
                self.assertTrue((Path(tmp) / "record" / "c" / "0003.response.txt").exists())
            finally:
                process.terminate()
                _, stderr = process.communicate(timeout=10)
                upstream.shutdown()
                upstream.server_close()
        self.assertIn("c 0002 refused: output_tokens exhausted", stderr)

    def test_a_bad_upstream_or_budget_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(proxy_module.main(["--listen", "127.0.0.1:0", "--upstream", "http://127.0.0.1:1/v1", "--record", tmp]), 2)
            self.assertEqual(proxy_module.main(["--listen", "nowhere", "--upstream", "http://127.0.0.1:1", "--record", tmp]), 2)
            self.assertEqual(proxy_module.main(["--listen", "127.0.0.1:0", "--upstream", "http://127.0.0.1:1", "--record", tmp, "--budget-input", "-1"]), 2)


if __name__ == "__main__":
    unittest.main()
