"""Small shared helpers: ports, clocks, the 11.7 event envelope and a fire-and-forget UDP event sender."""
from __future__ import annotations

import json
import socket
import time

HUB_UDP_PORT = 47000
HUB_HTTP_PORT = 47001
OBSERVER_UDP_PORT = 47002
OBSERVER_HTTP_PORT = 8080
MAX_DATAGRAM = 60000


def wall_ms() -> int:
    return int(time.time() * 1000)


def mono_ms() -> int:
    return int(time.monotonic() * 1000)


def parse_hostport(text: str, default_port: int | None = None) -> tuple[str, int]:
    """'1.2.3.4:47000' -> ('1.2.3.4', 47000); 'host' + default_port also accepted."""
    if ":" in text:
        host, port = text.rsplit(":", 1)
        return host or "127.0.0.1", int(port)
    if default_port is None:
        raise ValueError(f"missing port in {text!r}")
    return text, default_port


def resolve_ipv4(host: str) -> str:
    return socket.gethostbyname(host)


def make_event(src: str, name: str | None, cluster: int | None, event: str, data: dict, ts: int | None = None) -> dict:
    """The 11.7 observer envelope."""
    return {"v": 1, "src": src, "name": name, "cluster": cluster,
            "ts": wall_ms() if ts is None else ts, "event": event, "data": data}


def dumps(obj) -> bytes:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class EventSender:
    """Fire-and-forget UDP sender towards the observer (47002). One socket, sync sendto, errors swallowed."""

    def __init__(self, addr: tuple[str, int]):
        self.addr = (resolve_ipv4(addr[0]), addr[1])
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.sent = 0
        self.errors = 0

    def send(self, ev: dict) -> None:
        data = dumps(ev)
        if len(data) > MAX_DATAGRAM:
            self.errors += 1
            return
        try:
            self.sock.sendto(data, self.addr)
            self.sent += 1
        except OSError:
            self.errors += 1

    def emit(self, src: str, name: str | None, cluster: int | None, event: str, data: dict) -> None:
        self.send(make_event(src, name, cluster, event, data))

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass
