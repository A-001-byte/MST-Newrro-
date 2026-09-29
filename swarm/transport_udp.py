"""UdpHubTransport: the Transport protocol (00_CONTEXT 13) over the UDP hub (11.2). One UDP socket per node."""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Callable

from .common import MAX_DATAGRAM, dumps, resolve_ipv4


class UdpHubTransport(asyncio.DatagramProtocol):
    def __init__(self, hub_addr: tuple[str, int], node_addr: str, name: str, cluster: int, light: bool = False,
                 register_interval_ms: int = 2000, bind: tuple[str, int] = ("0.0.0.0", 0)):
        self.hub_addr = (resolve_ipv4(hub_addr[0]), hub_addr[1])
        self.node_addr = node_addr.lower()
        self.name, self.cluster, self.light = name, cluster, light
        self.register_interval_ms = register_interval_ms
        self.bind = bind
        self._receiver: Callable[[dict], None] | None = None
        self._world: Callable[[dict], None] | None = None
        self._control: Callable[[dict], None] | None = None
        self._transport: asyncio.DatagramTransport | None = None
        self._task: asyncio.Task | None = None
        self.sent = 0
        self.received = 0
        self.oversize = 0
        self.errors = 0
        self._warned: set[str] = set()

    # ------------------------------------------------------------ Transport protocol --
    def send(self, to: str, env: dict) -> None:
        """to: '*' | 'cluster:N' | '0x<addr>' (wrapped into {"hub":"SEND"})."""
        self._sendto({"hub": "SEND", "to": to, "env": env})

    def set_receiver(self, handler: Callable[[dict], None]) -> None:
        self._receiver = handler

    # ------------------------------------------------------------------- extra hooks --
    def set_world_handler(self, handler: Callable[[dict], None]) -> None:
        self._world = handler

    def set_control_handler(self, handler: Callable[[dict], None]) -> None:
        self._control = handler

    # ---------------------------------------------------------------------- lifecycle --
    async def start(self) -> "UdpHubTransport":
        loop = asyncio.get_running_loop()
        await loop.create_datagram_endpoint(lambda: self, local_addr=self.bind)
        self.register()
        self._task = asyncio.create_task(self._register_loop())
        return self

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        if self._transport:
            self._transport.close()
            self._transport = None

    @property
    def local_port(self) -> int:
        return self._transport.get_extra_info("sockname")[1] if self._transport else 0

    def register(self) -> None:
        self._sendto({"hub": "REGISTER", "node": self.node_addr, "name": self.name, "cluster": self.cluster,
                      "light": self.light})

    async def _register_loop(self) -> None:
        while True:
            await asyncio.sleep(self.register_interval_ms / 1000)
            self.register()

    # -------------------------------------------------------------------- UDP plumbing --
    def connection_made(self, transport):
        self._transport = transport

    def error_received(self, exc):
        self.errors += 1  # Windows ICMP reset when the hub is not up yet; keep going

    def _sendto(self, msg: dict) -> None:
        if self._transport is None:
            return
        data = dumps(msg)
        if len(data) > MAX_DATAGRAM:
            self.oversize += 1
            return
        self._transport.sendto(data, self.hub_addr)
        self.sent += 1

    def datagram_received(self, data, addr):
        try:
            msg = json.loads(data)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        self.received += 1
        kind = msg.get("hub")
        try:
            if kind == "DELIVER" and self._receiver and isinstance(msg.get("env"), dict):
                self._receiver(msg["env"])
            elif kind == "WORLD" and self._world:
                self._world(msg)
            elif kind == "CONTROL" and self._control:
                self._control(msg)
        except Exception as e:  # noqa: BLE001 - a handler bug must not kill the socket
            key = f"{kind}:{type(e).__name__}"
            if key not in self._warned:
                self._warned.add(key)
                print(f"[{self.name}] handler error on {kind}: {e!r}", file=sys.stderr, flush=True)
