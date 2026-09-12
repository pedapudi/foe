#!/usr/bin/python3
"""Record every connection the attempt network sends toward its default route.

The sink holds the gateway address of the attempt network, so every packet
an attempt container sends to a destination outside the subnet arrives
here. Two netfilter rules redirect every TCP and every UDP flow, whatever
its destination port, to the two sockets this script serves. Each accepted
TCP connection is recorded with its source, the destination it was sent to,
and the host name its first bytes ask for, read from an HTTP Host header or
a TLS server name; a TLS record is read to the length its header declares,
since a ClientHello can span two segments; the connection is then closed,
so a harness meets a refused request rather than a hang. Each UDP datagram that is a DNS query is
recorded with the name it asks for and answered with one fixed address, so
that a harness resolving a model endpoint by name connects to this sink and
the connection is recorded with the name. Any other datagram is recorded
and dropped.

The log is one JSON object per line; `time` is UTC. The fourth command of
the recipe build.sh prints copies it out of the container when an attempt
ends.

    recorder.py --hold 10.219.0.254/24 --interface eth0 --log /var/log/sink/connections.jsonl

`--hold` adds the address and installs the redirect rules and needs
NET_ADMIN; without it the script serves the two sockets it is given and
configures nothing, which is how its tests run it.
"""

from __future__ import annotations

import argparse
import datetime
import json
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

# getsockopt name that returns the destination a redirected TCP connection
# was sent to. The IP_RECVORIGDSTADDR control message on a UDP socket carries
# the address a datagram was delivered to after the redirect, which is the
# sink's own; the name a DNS query asks for is what identifies the datagram.
SO_ORIGINAL_DST = 80
IP_RECVORIGDSTADDR = 20
IP_ORIGDSTADDR = 20

# Bytes read from a TCP connection to find the name it asks for: at most
# one TLS record of the largest size the protocol allows, with its five-byte
# header, and at most HEAD_SECONDS of waiting for them.
TLS_RECORD_HEADER = 5
HEAD_BYTES = TLS_RECORD_HEADER + 16384
HEAD_SECONDS = 2.0

DNS_TYPE_A = 1
DNS_CLASS_IN = 1


class Recorder:
    """Appends one JSON line per event to the log, from any thread."""

    def __init__(self, log: Path) -> None:
        self.log = log
        self._lock = threading.Lock()
        log.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: dict[str, Any]) -> None:
        line = json.dumps({"time": datetime.datetime.now(datetime.UTC).isoformat(timespec="milliseconds"), **event}, sort_keys=True)
        with self._lock, self.log.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def original_destination(connection: socket.socket) -> tuple[str, int] | None:
    """The address a redirected connection was sent to; None when the connection was not redirected."""
    try:
        raw = connection.getsockopt(socket.SOL_IP, SO_ORIGINAL_DST, 16)
    except OSError:
        return None
    port, packed = struct.unpack("!2xH4s8x", raw)
    return socket.inet_ntoa(packed), port


def tls_server_name(head: bytes) -> str | None:
    """The server name a TLS ClientHello asks for, or None when the bytes are not one that carries it."""

    def u8(at: int) -> int:
        return head[at]

    def u16(at: int) -> int:
        return struct.unpack("!H", head[at : at + 2])[0]

    try:
        if u8(0) != 0x16 or u8(5) != 0x01:
            return None
        at = 5 + 4 + 2 + 32
        at += 1 + u8(at)
        at += 2 + u16(at)
        at += 1 + u8(at)
        end = at + 2 + u16(at)
        at += 2
        while at + 4 <= end:
            kind, length = u16(at), u16(at + 2)
            at += 4
            if kind == 0:
                names_end = at + 2 + u16(at)
                at += 2
                while at + 3 <= names_end:
                    name_type, name_length = u8(at), u16(at + 1)
                    at += 3
                    if name_type == 0:
                        return head[at : at + name_length].decode("ascii", "replace")
                    at += name_length
                return None
            at += length
    except (IndexError, struct.error):
        return None
    return None


def http_host(head: bytes) -> str | None:
    """The Host header of an HTTP request, or None when the bytes are not one."""
    lines = head.split(b"\r\n")
    first = lines[0].split(b" ")
    if len(first) != 3 or not first[2].startswith(b"HTTP/"):
        return None
    for line in lines[1:]:
        if line.lower().startswith(b"host:"):
            return line[5:].strip().decode("ascii", "replace")
    return None


def server_name(head: bytes) -> str | None:
    """The host name the first bytes of a stream ask for, from an HTTP Host header or a TLS server name."""
    return http_host(head) or tls_server_name(head)


def dns_question(datagram: bytes) -> tuple[int, str, int, int, int] | None:
    """The id, name, type, class, and end offset of the question of a DNS query with one question; None for anything else."""
    if len(datagram) < 12:
        return None
    identifier, flags, questions = struct.unpack("!HHH", datagram[:6])
    if flags & 0x8000 or questions != 1:
        return None
    labels: list[str] = []
    at = 12
    while True:
        if at >= len(datagram):
            return None
        length = datagram[at]
        at += 1
        if length == 0:
            break
        if length >= 0xC0 or at + length > len(datagram):
            return None
        labels.append(datagram[at : at + length].decode("ascii", "replace"))
        at += length
    if at + 4 > len(datagram):
        return None
    query_type, query_class = struct.unpack("!HH", datagram[at : at + 4])
    return identifier, ".".join(labels), query_type, query_class, at + 4


def dns_answer(datagram: bytes, question: tuple[int, str, int, int, int], address: str) -> bytes:
    """A response to the query: one A record holding `address` for an A query, an empty answer otherwise.

    The response carries the question section alone; an OPT record the
    query appended is dropped, since a copied record would be read as the
    answer.
    """
    identifier, _name, query_type, query_class, question_end = question
    question_bytes = datagram[12:question_end]
    answers = b""
    count = 0
    if query_type == DNS_TYPE_A and query_class == DNS_CLASS_IN:
        answers = struct.pack("!HHHIH", 0xC00C, DNS_TYPE_A, DNS_CLASS_IN, 0, 4) + socket.inet_aton(address)
        count = 1
    header = struct.pack("!HHHHHH", identifier, 0x8180, 1, count, 0, 0)
    return header + question_bytes + answers


def tls_record_length(head: bytes) -> int | None:
    """The bytes the TLS record that `head` starts spans, header included; None when `head` starts anything other than a handshake record.

    A head shorter than the record header is reported as needing the header,
    since the length field is in it.
    """
    if not head or head[0] != 0x16:
        return None
    if len(head) < TLS_RECORD_HEADER:
        return TLS_RECORD_HEADER
    return TLS_RECORD_HEADER + struct.unpack("!H", head[3:TLS_RECORD_HEADER])[0]


def read_head(connection: socket.socket) -> bytes:
    """The first bytes of the stream, read until a TLS record is complete or the peer's first segment is in.

    A ClientHello with a large key share spans two segments at a 1500-byte
    MTU, and the server name extension can lie past the first, so a TLS
    record is read up to the length its header declares. Any other stream
    keeps its first segment alone. Reading stops at HEAD_BYTES, at the
    peer's close, and HEAD_SECONDS after the first receive began.
    """
    deadline = time.monotonic() + HEAD_SECONDS
    head = b""
    try:
        while len(head) < HEAD_BYTES:
            connection.settimeout(max(0.001, deadline - time.monotonic()))
            chunk = connection.recv(HEAD_BYTES - len(head))
            if not chunk:
                break
            head += chunk
            wanted = tls_record_length(head)
            if wanted is None or len(head) >= wanted:
                break
    except OSError:
        pass
    return head


def _serve_connection(connection: socket.socket, peer: tuple[str, int], recorder: Recorder) -> None:
    destination = original_destination(connection) or connection.getsockname()[:2]
    head = read_head(connection)
    recorder.record(
        {
            "protocol": "tcp",
            "source": f"{peer[0]}:{peer[1]}",
            "destination": f"{destination[0]}:{destination[1]}",
            "name": server_name(head),
            "head_bytes": len(head),
        }
    )
    connection.close()


def serve_tcp(listener: socket.socket, recorder: Recorder) -> None:
    """Accept connections on the listening socket until it is closed; each is recorded on its own thread."""
    while True:
        try:
            connection, peer = listener.accept()
        except OSError:
            return
        threading.Thread(target=_serve_connection, args=(connection, peer, recorder), daemon=True).start()


def _udp_destination(sock: socket.socket, ancillary: list[tuple[int, int, bytes]]) -> tuple[str, int]:
    for level, kind, data in ancillary:
        if level == socket.SOL_IP and kind == IP_ORIGDSTADDR and len(data) >= 8:
            port, packed = struct.unpack("!2xH4s", data[:8])
            return socket.inet_ntoa(packed), port
    return sock.getsockname()[:2]


def serve_udp(sock: socket.socket, recorder: Recorder, answer: str) -> None:
    """Answer DNS queries on the socket with `answer` and record every datagram, until the socket is closed."""
    try:
        sock.setsockopt(socket.SOL_IP, IP_RECVORIGDSTADDR, 1)
    except OSError:
        pass
    while True:
        try:
            datagram, ancillary, _flags, peer = sock.recvmsg(4096, 512)
        except OSError:
            return
        destination = _udp_destination(sock, ancillary)
        question = dns_question(datagram)
        event: dict[str, Any] = {
            "protocol": "udp",
            "source": f"{peer[0]}:{peer[1]}",
            "destination": f"{destination[0]}:{destination[1]}",
            "bytes": len(datagram),
        }
        if question is not None:
            event.update({"name": question[1], "query_type": question[2], "answer": answer})
            try:
                sock.sendto(dns_answer(datagram, question, answer), peer)
            except OSError:
                pass
        recorder.record(event)


def configure(hold: str, interface: str, tcp_port: int, udp_port: int) -> None:
    """Take the gateway address and redirect every TCP and UDP flow to the two ports; each command names itself on failure."""
    commands = [
        ["/usr/sbin/ip", "addr", "replace", hold, "dev", interface],
        ["/usr/sbin/iptables", "-t", "nat", "-A", "PREROUTING", "-p", "tcp", "-j", "REDIRECT", "--to-ports", str(tcp_port)],
        ["/usr/sbin/iptables", "-t", "nat", "-A", "PREROUTING", "-p", "udp", "-j", "REDIRECT", "--to-ports", str(udp_port)],
    ]
    for command in commands:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise RuntimeError(f"`{' '.join(command)}` exited {completed.returncode}: {completed.stderr.strip()}")


def start(recorder: Recorder, tcp_listener: socket.socket, udp_socket: socket.socket, answer: str) -> list[threading.Thread]:
    """Serve both sockets on daemon threads; closing a socket ends its thread."""
    threads = [
        threading.Thread(target=serve_tcp, args=(tcp_listener, recorder), daemon=True),
        threading.Thread(target=serve_udp, args=(udp_socket, recorder, answer), daemon=True),
    ]
    for thread in threads:
        thread.start()
    return threads


def bind(bind_address: str, tcp_port: int, udp_port: int) -> tuple[socket.socket, socket.socket]:
    tcp_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    tcp_listener.bind((bind_address, tcp_port))
    tcp_listener.listen(128)
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socket.bind((bind_address, udp_port))
    return tcp_listener, udp_socket


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--log", required=True, type=Path, help="the file every event is appended to, one JSON object per line")
    parser.add_argument("--hold", help="the gateway address to take, as ADDRESS/PREFIX; installs the redirect rules")
    parser.add_argument("--interface", default="eth0", help="the interface --hold adds the address to")
    parser.add_argument("--bind", default="0.0.0.0", help="the address the two sockets bind")
    parser.add_argument("--tcp-port", type=int, default=9, help="the port every redirected TCP connection reaches")
    parser.add_argument("--udp-port", type=int, default=9, help="the port every redirected UDP datagram reaches")
    parser.add_argument("--answer", help="the address every A query is answered with; the held address by default")
    args = parser.parse_args(argv)
    answer = args.answer
    if answer is None:
        if args.hold is None:
            parser.error("--answer is required when --hold is absent, so that a DNS query has an address to be answered with")
        answer = args.hold.split("/", 1)[0]
    try:
        socket.inet_aton(answer)
    except OSError:
        parser.error(f"--answer names {answer!r}, which is not an IPv4 address")
    recorder = Recorder(args.log)
    tcp_listener, udp_socket = bind(args.bind, args.tcp_port, args.udp_port)
    if args.hold is not None:
        try:
            configure(args.hold, args.interface, args.tcp_port, args.udp_port)
        except RuntimeError as error:
            print(f"recorder: {error}", file=sys.stderr)
            return 1
    recorder.record({"protocol": "sink", "event": "started", "hold": args.hold, "answer": answer, "tcp_port": args.tcp_port, "udp_port": args.udp_port})
    print(f"recorder: holding {args.hold}, tcp on {args.tcp_port}, udp on {args.udp_port}, log {args.log}", file=sys.stderr, flush=True)
    threads = start(recorder, tcp_listener, udp_socket, answer)
    for thread in threads:
        thread.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
