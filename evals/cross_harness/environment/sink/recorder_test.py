#!/usr/bin/python3
"""Tests of the egress sink's recorder: name extraction, DNS answers, and the two servers over loopback."""

from __future__ import annotations

import json
import socket
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import recorder  # noqa: E402


def client_hello(name: str, key_share_bytes: int = 0) -> bytes:
    """A TLS ClientHello carrying a server name; with `key_share_bytes`, a key share extension of that size precedes it, as a client offering a post-quantum key share sends."""
    host = name.encode("ascii")
    sni = struct.pack("!HBH", len(host) + 3, 0, len(host)) + host
    extensions = struct.pack("!HH", 0, len(sni)) + sni
    if key_share_bytes:
        extensions = struct.pack("!HH", 51, key_share_bytes) + bytes(key_share_bytes) + extensions
    body = (
        b"\x03\x03"
        + bytes(32)
        + b"\x00"
        + struct.pack("!H", 2)
        + b"\x13\x01"
        + b"\x01\x00"
        + struct.pack("!H", len(extensions))
        + extensions
    )
    handshake = b"\x01" + len(body).to_bytes(3, "big") + body
    return b"\x16\x03\x01" + struct.pack("!H", len(handshake)) + handshake


def dns_query(name: str, query_type: int = 1, identifier: int = 0x1234, edns: bool = False) -> bytes:
    """A query with one question; with `edns`, an OPT record in the additional section, as a resolver sends."""
    labels = b"".join(bytes([len(label)]) + label.encode("ascii") for label in name.split("."))
    question = labels + b"\x00" + struct.pack("!HH", query_type, 1)
    additional = b"\x00" + struct.pack("!HHIH", 41, 4096, 0, 0) if edns else b""
    return struct.pack("!HHHHHH", identifier, 0x0100, 1, 0, 0, 1 if edns else 0) + question + additional


class NameExtraction(unittest.TestCase):
    def test_http_host_header(self) -> None:
        self.assertEqual(recorder.server_name(b"GET /v1 HTTP/1.1\r\nUser-Agent: x\r\nHost: api.example.test\r\n\r\n"), "api.example.test")

    def test_tls_server_name(self) -> None:
        self.assertEqual(recorder.server_name(client_hello("model.example.test")), "model.example.test")

    def test_tls_server_name_past_a_large_key_share(self) -> None:
        hello = client_hello("model.example.test", 1500)
        self.assertGreater(len(hello), 1460)
        self.assertEqual(recorder.server_name(hello), "model.example.test")
        self.assertIsNone(recorder.server_name(hello[:1460]))

    def test_tls_record_length_comes_from_the_header(self) -> None:
        hello = client_hello("model.example.test", 1500)
        self.assertEqual(recorder.tls_record_length(hello), len(hello))
        self.assertEqual(recorder.tls_record_length(hello[:1460]), len(hello))
        self.assertEqual(recorder.tls_record_length(hello[:3]), recorder.TLS_RECORD_HEADER)
        self.assertIsNone(recorder.tls_record_length(b""))
        self.assertIsNone(recorder.tls_record_length(b"GET / HTTP/1.1\r\n"))
        self.assertGreaterEqual(recorder.HEAD_BYTES, recorder.TLS_RECORD_HEADER + 16384)

    def test_bytes_without_a_name(self) -> None:
        self.assertIsNone(recorder.server_name(b""))
        self.assertIsNone(recorder.server_name(b"\x16\x03\x01\x00"))
        self.assertIsNone(recorder.server_name(b"SSH-2.0-client\r\n"))
        self.assertIsNone(recorder.server_name(b"GET / HTTP/1.1\r\n\r\n"))


class Dns(unittest.TestCase):
    def test_question_is_parsed(self) -> None:
        query = dns_query("a.example.test", 28, 7)
        self.assertEqual(recorder.dns_question(query), (7, "a.example.test", 28, 1, len(query)))
        self.assertEqual(recorder.dns_question(dns_query("a.example.test", edns=True)), (0x1234, "a.example.test", 1, 1, len(query)))

    def test_malformed_and_responses_are_refused(self) -> None:
        self.assertIsNone(recorder.dns_question(b"\x00" * 5))
        self.assertIsNone(recorder.dns_question(b"\x12\x34\x81\x80" + b"\x00" * 8))
        self.assertIsNone(recorder.dns_question(dns_query("x.test")[:-3]))

    def test_a_query_is_answered_with_the_address(self) -> None:
        plain = dns_query("api.example.test")
        for query in (plain, dns_query("api.example.test", edns=True)):
            question = recorder.dns_question(query)
            assert question is not None
            answer = recorder.dns_answer(query, question, "10.219.0.254")
            identifier, flags, questions, answers, authorities, additionals = struct.unpack("!HHHHHH", answer[:12])
            self.assertEqual((identifier, flags, questions, answers, authorities, additionals), (0x1234, 0x8180, 1, 1, 0, 0))
            record = struct.pack("!HHHIH", 0xC00C, 1, 1, 0, 4) + socket.inet_aton("10.219.0.254")
            self.assertEqual(answer, answer[:12] + plain[12:] + record)

    def test_other_queries_get_an_empty_answer(self) -> None:
        query = dns_query("api.example.test", 28)
        question = recorder.dns_question(query)
        assert question is not None
        answer = recorder.dns_answer(query, question, "10.219.0.254")
        self.assertEqual(struct.unpack("!HHHH", answer[:8])[3], 0)


class Servers(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="recorder-test-")
        self.log = Path(self.temporary.name) / "connections.jsonl"
        self.tcp, self.udp = recorder.bind("127.0.0.1", 0, 0)
        recorder.start(recorder.Recorder(self.log), self.tcp, self.udp, "10.219.0.254")

    def tearDown(self) -> None:
        self.tcp.close()
        self.udp.close()
        self.temporary.cleanup()

    def events(self, count: int) -> list[dict]:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if self.log.is_file():
                lines = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]
                if len(lines) >= count:
                    return lines
            time.sleep(0.02)
        raise AssertionError(f"{count} events did not appear in {self.log}")

    def test_tcp_connection_is_recorded_with_its_name_and_closed(self) -> None:
        port = self.tcp.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port)) as client:
            client.sendall(b"POST /v1/responses HTTP/1.1\r\nHost: model.example.test\r\n\r\n")
            client.settimeout(5)
            self.assertEqual(client.recv(1), b"")
        (event,) = self.events(1)
        self.assertEqual(event["protocol"], "tcp")
        self.assertEqual(event["name"], "model.example.test")
        self.assertEqual(event["destination"], f"127.0.0.1:{port}")
        self.assertTrue(event["source"].startswith("127.0.0.1:"))
        self.assertIn("time", event)

    def test_split_client_hello_is_read_to_the_record_length(self) -> None:
        # The hello arrives as a 1460-byte segment, a pause, and the rest,
        # as a 1500-byte MTU delivers it; the server name lies in the rest.
        port = self.tcp.getsockname()[1]
        hello = client_hello("api.example.test", 1500)
        with socket.create_connection(("127.0.0.1", port)) as client:
            client.sendall(hello[:1460])
            time.sleep(0.2)
            client.sendall(hello[1460:])
            client.settimeout(5)
            self.assertEqual(client.recv(1), b"")
        (event,) = self.events(1)
        self.assertEqual((event["name"], event["head_bytes"]), ("api.example.test", len(hello)))

    def test_http_request_is_recorded_from_its_first_segment_alone(self) -> None:
        # A stream that is not a TLS record is recorded once its first
        # segment is in, without waiting HEAD_SECONDS for more.
        port = self.tcp.getsockname()[1]
        started = time.monotonic()
        with socket.create_connection(("127.0.0.1", port)) as client:
            client.sendall(b"GET /v1 HTTP/1.1\r\nHost: api.example.test\r\n\r\n")
            client.settimeout(5)
            self.assertEqual(client.recv(1), b"")
        self.assertLess(time.monotonic() - started, recorder.HEAD_SECONDS)
        (event,) = self.events(1)
        self.assertEqual(event["name"], "api.example.test")

    def test_dns_query_is_answered_and_recorded(self) -> None:
        port = self.udp.getsockname()[1]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(5)
            client.sendto(dns_query("chat.example.test"), ("127.0.0.1", port))
            answer, _peer = client.recvfrom(512)
        self.assertTrue(answer.endswith(socket.inet_aton("10.219.0.254")))
        (event,) = self.events(1)
        self.assertEqual((event["protocol"], event["name"], event["answer"]), ("udp", "chat.example.test", "10.219.0.254"))

    def test_other_datagram_is_recorded_without_a_name(self) -> None:
        port = self.udp.getsockname()[1]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.sendto(b"\x00", ("127.0.0.1", port))
        (event,) = self.events(1)
        self.assertEqual(event["protocol"], "udp")
        self.assertNotIn("name", event)


class Arguments(unittest.TestCase):
    def test_answer_is_required_without_hold(self) -> None:
        with self.assertRaises(SystemExit):
            recorder.main(["--log", "/nonexistent/x"])


if __name__ == "__main__":
    unittest.main()
