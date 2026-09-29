"""Real-socket test doubles: a fake mesh node and an observer event sink (asyncio UDP)."""
from __future__ import annotations

import asyncio
import json


class _Collect(asyncio.DatagramProtocol):
    def __init__(self, sink: list):
        self.sink = sink

    def datagram_received(self, data, addr):
        try:
            self.sink.append((json.loads(data), len(data)))
        except ValueError:
            pass

    def error_received(self, exc):  # Windows ICMP resets
        pass


class FakeNode:
    def __init__(self, addr: str, name: str, cluster: int, light: bool = False):
        self.addr, self.name, self.cluster, self.light = addr, name, cluster, light
        self.inbox: list[tuple[dict, int]] = []
        self.transport = None
        self.hub_addr = None

    async def start(self, hub_addr, register: bool = True) -> "FakeNode":
        loop = asyncio.get_running_loop()
        self.transport, _ = await loop.create_datagram_endpoint(lambda: _Collect(self.inbox),
                                                                local_addr=("127.0.0.1", 0))
        self.hub_addr = hub_addr
        if register:
            self.register()
        return self

    def register(self) -> None:
        self.raw({"hub": "REGISTER", "node": self.addr, "name": self.name, "cluster": self.cluster,
                  "light": self.light})

    def raw(self, msg: dict) -> None:
        self.transport.sendto(json.dumps(msg).encode(), self.hub_addr)

    def send(self, to: str, env: dict) -> None:
        self.raw({"hub": "SEND", "to": to, "env": env})

    def delivered(self, etype: str | None = None) -> list[dict]:
        out = [m["env"] for m, _ in self.inbox if m.get("hub") == "DELIVER"]
        return [e for e in out if etype is None or e.get("type") == etype]

    def worlds(self) -> list[dict]:
        return [m for m, _ in self.inbox if m.get("hub") == "WORLD"]

    def controls(self) -> list[dict]:
        return [m for m, _ in self.inbox if m.get("hub") == "CONTROL"]

    def close(self) -> None:
        if self.transport:
            self.transport.close()


class EventSink:
    """Stands in for the observer's UDP 47002."""

    def __init__(self):
        self.events: list[tuple[dict, int]] = []
        self.transport = None
        self.port = 0

    async def start(self) -> "EventSink":
        loop = asyncio.get_running_loop()
        self.transport, _ = await loop.create_datagram_endpoint(lambda: _Collect(self.events),
                                                                local_addr=("127.0.0.1", 0))
        self.port = self.transport.get_extra_info("sockname")[1]
        return self

    def named(self, event: str) -> list[dict]:
        return [e for e, _ in self.events if e.get("event") == event]

    def close(self) -> None:
        if self.transport:
            self.transport.close()


async def wait_for(pred, timeout: float = 2.0, step: float = 0.02) -> bool:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if pred():
            return True
        await asyncio.sleep(step)
    return pred()


async def settle(delay: float = 0.15) -> None:
    await asyncio.sleep(delay)
