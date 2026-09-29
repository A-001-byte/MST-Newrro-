"""UDP mesh hub = the radio emulator (docs/00_CONTEXT.md sections 11.1-11.3).

It relays datagrams, applies partitions / drops / kills and does compact relay for light nodes.
It never signs, validates or orders anything.

  UDP  0.0.0.0:47000   node traffic (REGISTER / SEND / WORLD) and DELIVER / CONTROL back to nodes
  HTTP 0.0.0.0:47001   control API (GET /topology /stats, POST /partition /heal /drop /kill /revive /fault /rogue /export)

Run:  ./.venv/Scripts/python -m swarm.hub
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from typing import Callable

from aiohttp import web

from .common import (HUB_HTTP_PORT, HUB_UDP_PORT, MAX_DATAGRAM, OBSERVER_UDP_PORT, EventSender, dumps,
                     make_event, mono_ms, parse_hostport)

LIGHT_TYPES = frozenset({"PROPOSAL", "PREVOTE", "PRECOMMIT", "COMMIT", "STATUS"})
MAX_LIGHT_DATAGRAM = 1400
FAULT_MODES = ("honest", "equivocate", "silent", "false_obs")


class NodeRec:
    __slots__ = ("node", "name", "cluster", "light", "ip", "port", "last_seen", "killed", "drop_rate", "was_online")

    def __init__(self, node: str, name: str, cluster: int, light: bool, ip: str, port: int, now: int):
        self.node, self.name, self.cluster, self.light = node, name, cluster, light
        self.ip, self.port, self.last_seen = ip, port, now
        self.killed = False
        self.drop_rate = 0.0
        self.was_online = True


def light_env(env: dict) -> dict:
    """Compact relay rewrite for light nodes (11.2). Returns a NEW envelope; the input is untouched."""
    t = env.get("type")
    body = env.get("body") or {}
    if t == "PROPOSAL":
        body = {k: v for k, v in body.items() if k != "txs"}
    elif t == "COMMIT":
        qc = body.get("qc") or {}
        signers = qc.get("signers")
        if signers is None:
            signers = [s.get("signer") for s in qc.get("sigs", []) if isinstance(s, dict)]
        body = {"header": body.get("header"), "qc": {"round": qc.get("round", 0), "signers": signers},
                "revoked": body.get("revoked", []), "master": body.get("master")}
    return {**env, "body": body}


class _Proto(asyncio.DatagramProtocol):
    def __init__(self, hub: "Hub"):
        self.hub = hub

    def connection_made(self, transport):
        self.hub.transport = transport

    def datagram_received(self, data, addr):
        try:
            self.hub.on_datagram(data, addr)
        except Exception as e:  # noqa: BLE001 - the radio must never die on a bad datagram
            self.hub._drop("handler_error")
            print(f"[hub] handler error: {e!r}", file=sys.stderr)

    def error_received(self, exc):
        # Windows reports ICMP "port unreachable" (a node process died) here. Not fatal.
        self.hub._drop("icmp_unreachable")


class Hub:
    def __init__(self, host: str = "0.0.0.0", udp_port: int = HUB_UDP_PORT, http_port: int = HUB_HTTP_PORT,
                 observer: tuple[str, int] | None = ("127.0.0.1", OBSERVER_UDP_PORT), offline_ms: int = 6000,
                 event_sink: Callable[[dict], None] | None = None, stats_interval_s: float = 2.0,
                 seed: int | None = None):
        self.host, self.udp_port, self.http_port = host, udp_port, http_port
        self.offline_ms = offline_ms
        self.stats_interval_s = stats_interval_s
        self.rng = random.Random(seed)
        self.nodes: dict[str, NodeRec] = {}
        self._by_addr: dict[tuple[str, int], str] = {}
        self._by_name: dict[str, str] = {}
        self.partition_spec: list[list[str]] | None = None
        self._partition_addr: list[set[str]] | None = None  # address entries per group (explicit)
        self._partition_cluster: list[set[int]] | None = None
        self.global_drop = 0.0
        self.by_type: dict[str, dict[str, int]] = {}
        self.dropped: dict[str, int] = {}
        self.stripped_for_light = 0
        self.oversize_dropped = 0
        self.sent_total = 0
        self.delivered_total = 0
        self.delivered_bytes = 0
        self.transport: asyncio.DatagramTransport | None = None
        self._runner: web.AppRunner | None = None
        self._tasks: list[asyncio.Task] = []
        self._sink = event_sink
        self._events = EventSender(observer) if (observer and event_sink is None) else None

    # ------------------------------------------------------------------ lifecycle --
    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.create_datagram_endpoint(lambda: _Proto(self), local_addr=(self.host, self.udp_port))
        self.udp_port = self.transport.get_extra_info("sockname")[1]
        app = web.Application()
        app.add_routes([
            web.get("/topology", self.h_topology), web.get("/stats", self.h_stats),
            web.post("/partition", self.h_partition), web.post("/heal", self.h_heal),
            web.post("/drop", self.h_drop), web.post("/kill", self.h_kill), web.post("/revive", self.h_revive),
            web.post("/fault", self.h_fault), web.post("/rogue", self.h_rogue), web.post("/export", self.h_export),
        ])
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.http_port)
        await site.start()
        self.http_port = self._runner.addresses[0][1]
        self._tasks = [asyncio.create_task(self._sweep_loop())]

    async def close(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self.transport:
            self.transport.close()
        if self._runner:
            await self._runner.cleanup()
        if self._events:
            self._events.close()

    # ---------------------------------------------------------------------- events --
    def emit(self, event: str, data: dict, name: str | None = None, cluster: int | None = None) -> None:
        ev = make_event("hub", name, cluster, event, data)
        if self._sink is not None:
            self._sink(ev)
        elif self._events is not None:
            self._events.send(ev)

    def _emit_topology(self) -> None:
        self.emit("topology", self.topology())

    def _emit_fault(self, node: str, mode: str, **extra) -> None:
        data = {"node": node, "mode": mode}
        data.update(extra)
        self.emit("fault_injected", data)

    # ------------------------------------------------------------------- bookkeeping --
    def _drop(self, reason: str, n: int = 1) -> None:
        self.dropped[reason] = self.dropped.get(reason, 0) + n

    def _online(self, rec: NodeRec, now: int | None = None) -> bool:
        return (mono_ms() if now is None else now) - rec.last_seen <= self.offline_ms

    def resolve(self, ref) -> str | None:
        """'0x..' address or drone name -> registered address (or None)."""
        if not isinstance(ref, str):
            return None
        r = ref.strip()
        if r.lower() in self.nodes:
            return r.lower()
        return self._by_name.get(r)

    async def _sweep_loop(self) -> None:
        last_stats = mono_ms()
        while True:
            await asyncio.sleep(0.5)
            now = mono_ms()
            changed = False
            for rec in self.nodes.values():
                on = self._online(rec, now)
                if on != rec.was_online:
                    rec.was_online = on
                    changed = True
            if changed:
                self._emit_topology()
            if self.stats_interval_s and now - last_stats >= self.stats_interval_s * 1000:
                last_stats = now
                self.emit("hub_stats", self.stats())

    # ------------------------------------------------------------------ UDP ingress --
    def on_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        if len(data) > MAX_DATAGRAM:
            self._drop("oversize_in")
            return
        try:
            msg = json.loads(data)
        except ValueError:
            self._drop("bad_json")
            return
        if not isinstance(msg, dict):
            self._drop("bad_json")
            return
        kind = msg.get("hub")
        if kind == "REGISTER":
            self._register(msg, addr)
        elif kind == "SEND":
            self._route(msg, addr, len(data))
        elif kind == "WORLD":
            self._world(data)
        else:
            self._drop("unknown_hub_msg")

    def _register(self, msg: dict, addr: tuple[str, int]) -> None:
        node = str(msg.get("node", "")).lower()
        if not node:
            self._drop("bad_register")
            return
        name = str(msg.get("name") or node[:10])
        try:
            cluster = int(msg.get("cluster", 0))
        except (TypeError, ValueError):
            cluster = 0
        light = bool(msg.get("light", False))
        now = mono_ms()
        rec = self.nodes.get(node)
        changed = False
        if rec is None:
            rec = self.nodes[node] = NodeRec(node, name, cluster, light, addr[0], addr[1], now)
            changed = True
        else:
            if (rec.ip, rec.port) != addr:
                self._by_addr.pop((rec.ip, rec.port), None)
                changed = True
            if (rec.name, rec.cluster, rec.light) != (name, cluster, light) or not rec.was_online:
                changed = True
            rec.name, rec.cluster, rec.light, rec.ip, rec.port, rec.last_seen = name, cluster, light, addr[0], addr[1], now
            rec.was_online = True
        self._by_addr[addr] = node
        self._by_name[name] = node
        if changed:
            self._emit_topology()

    # ---------------------------------------------------------------------- routing --
    def _group_of(self, rec: NodeRec) -> int:
        """Partition group index; nodes not listed anywhere form one extra group (index len(groups))."""
        n = len(self._partition_addr)
        for i, addrs in enumerate(self._partition_addr):
            if rec.node in addrs:
                return i
        for i, clusters in enumerate(self._partition_cluster):
            if rec.cluster in clusters:
                return i
        return n

    def _targets(self, src: NodeRec, to) -> list[NodeRec]:
        if to == "*":
            return [r for r in self.nodes.values() if r.node != src.node]
        if isinstance(to, str) and to.startswith("cluster:"):
            try:
                cl = int(to[8:])
            except ValueError:
                return []
            return [r for r in self.nodes.values() if r.cluster == cl and r.node != src.node]
        if isinstance(to, str):
            rec = self.nodes.get(to.lower())
            return [rec] if rec is not None and rec.node != src.node else []
        return []

    def _lossy(self, src: NodeRec, dst: NodeRec) -> bool:
        keep = (1.0 - self.global_drop) * (1.0 - src.drop_rate) * (1.0 - dst.drop_rate)
        return self.rng.random() >= keep

    def _route(self, msg: dict, addr: tuple[str, int], size: int) -> None:
        sender = self._by_addr.get(addr)
        env = msg.get("env")
        if sender is None or not isinstance(env, dict):
            self._drop("unregistered_sender" if sender is None else "bad_send")
            return
        src = self.nodes[sender]
        etype = str(env.get("type", "?"))
        st = self.by_type.setdefault(etype, {"count": 0, "bytes": 0})
        st["count"] += 1
        st["bytes"] += size
        self.sent_total += 1
        if src.killed:
            self._drop("src_killed")
            return
        targets = self._targets(src, msg.get("to"))
        if not targets:
            self._drop("no_target")
            return
        now = mono_ms()
        full_bytes: bytes | None = None
        for dst in targets:
            if not self._online(dst, now):
                self._drop("dst_offline")
                continue
            if dst.killed:
                self._drop("dst_killed")
                continue
            if self._partition_addr is not None and self._group_of(src) != self._group_of(dst):
                self._drop("partition")
                continue
            if self._lossy(src, dst):
                self._drop("random_drop")
                continue
            if dst.light:
                if etype not in LIGHT_TYPES or env.get("cluster") != dst.cluster:
                    self._drop("light_filter")
                    continue
                lenv = light_env(env)
                if lenv is not env and (lenv.get("body") != env.get("body")):
                    self.stripped_for_light += 1
                payload = dumps({"hub": "DELIVER", "env": lenv})
                if len(payload) > MAX_LIGHT_DATAGRAM:
                    self.oversize_dropped += 1
                    self._drop("oversize_light")
                    continue
            else:
                if full_bytes is None:
                    full_bytes = dumps({"hub": "DELIVER", "env": env})
                payload = full_bytes
                if len(payload) > MAX_DATAGRAM:
                    self._drop("oversize_full")
                    continue
            self._sendto(dst, payload)

    def _sendto(self, dst: NodeRec, payload: bytes) -> None:
        if self.transport is None:
            return
        self.transport.sendto(payload, (dst.ip, dst.port))
        self.delivered_total += 1
        self.delivered_bytes += len(payload)

    def _world(self, raw: bytes) -> None:
        """Physics, not radio: every full node except killed ones, regardless of partition/drop. Never light."""
        now = mono_ms()
        for rec in self.nodes.values():
            if rec.light or rec.killed or not self._online(rec, now):
                continue
            self._sendto(rec, raw)

    def _control(self, rec: NodeRec, payload: dict) -> None:
        """Out-of-band: delivered even to killed / partitioned nodes."""
        if self.transport is not None:
            self.transport.sendto(dumps({"hub": "CONTROL", **payload}), (rec.ip, rec.port))

    # ------------------------------------------------------------------------ views --
    def topology(self) -> dict:
        now = mono_ms()
        nodes = [{"node": r.node, "name": r.name, "cluster": r.cluster, "light": r.light, "ip": r.ip, "port": r.port,
                  "lastSeenMs": now - r.last_seen, "online": self._online(r, now), "killed": r.killed,
                  "dropRate": r.drop_rate}
                 for r in sorted(self.nodes.values(), key=lambda r: (r.cluster, r.node))]
        return {"nodes": nodes, "partition": self.partition_spec, "globalDropRate": self.global_drop}

    def stats(self) -> dict:
        return {"byType": {k: dict(v) for k, v in self.by_type.items()}, "dropped": dict(self.dropped),
                "strippedForLight": self.stripped_for_light, "oversizeDropped": self.oversize_dropped,
                "sentTotal": self.sent_total, "deliveredTotal": self.delivered_total,
                "deliveredBytes": self.delivered_bytes}

    # ------------------------------------------------------------------ HTTP handlers --
    @staticmethod
    async def _body(request: web.Request) -> dict:
        try:
            data = await request.json()
        except Exception:  # noqa: BLE001 - empty / non-JSON body is fine for heal etc.
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _bad(msg: str, status: int = 400) -> web.Response:
        return web.json_response({"ok": False, "error": msg}, status=status)

    async def h_topology(self, request):
        return web.json_response(self.topology())

    async def h_stats(self, request):
        return web.json_response(self.stats())

    async def h_partition(self, request):
        body = await self._body(request)
        groups = body.get("groups")
        if not isinstance(groups, list) or not groups or not all(isinstance(g, list) for g in groups):
            return self._bad("groups must be a non-empty list of lists")
        addr_sets: list[set[str]] = []
        cl_sets: list[set[int]] = []
        for g in groups:
            a: set[str] = set()
            c: set[int] = set()
            for entry in g:
                if isinstance(entry, str) and entry.startswith("cluster:"):
                    try:
                        c.add(int(entry[8:]))
                    except ValueError:
                        return self._bad(f"bad entry {entry!r}")
                else:
                    node = self.resolve(entry)
                    a.add(node if node else str(entry).lower())
            addr_sets.append(a)
            cl_sets.append(c)
        self.partition_spec = groups
        self._partition_addr, self._partition_cluster = addr_sets, cl_sets
        self._emit_fault("*", "partition", groups=groups)
        self._emit_topology()
        return web.json_response({"ok": True, "groups": groups})

    async def h_heal(self, request):
        self.partition_spec = self._partition_addr = self._partition_cluster = None
        self._emit_fault("*", "heal")
        self._emit_topology()
        return web.json_response({"ok": True})

    async def h_drop(self, request):
        body = await self._body(request)
        try:
            rate = float(body.get("rate"))
        except (TypeError, ValueError):
            return self._bad("rate must be a number in 0..1")
        if not 0.0 <= rate <= 1.0:
            return self._bad("rate must be in 0..1")
        ref = body.get("node", "*")
        if ref == "*":
            self.global_drop = rate
            self._emit_fault("*", "drop", rate=rate)
        else:
            addr = self.resolve(ref)
            if addr is None:
                return self._bad(f"unknown node {ref!r}")
            self.nodes[addr].drop_rate = rate
            self._emit_fault(addr, "drop", rate=rate, name=self.nodes[addr].name)
        self._emit_topology()
        return web.json_response({"ok": True, "rate": rate})

    async def _kill_revive(self, request, killed: bool):
        body = await self._body(request)
        addr = self.resolve(body.get("node"))
        if addr is None:
            return self._bad(f"unknown node {body.get('node')!r}")
        rec = self.nodes[addr]
        rec.killed = killed
        self._emit_fault(addr, "kill" if killed else "revive", name=rec.name)
        self._emit_topology()
        return web.json_response({"ok": True, "node": addr, "name": rec.name, "killed": killed})

    async def h_kill(self, request):
        return await self._kill_revive(request, True)

    async def h_revive(self, request):
        return await self._kill_revive(request, False)

    async def h_fault(self, request):
        body = await self._body(request)
        addr = self.resolve(body.get("node"))
        mode = body.get("mode")
        if addr is None:
            return self._bad(f"unknown node {body.get('node')!r}")
        if mode not in FAULT_MODES:
            return self._bad(f"mode must be one of {list(FAULT_MODES)}")
        rec = self.nodes[addr]
        self._control(rec, {"cmd": "fault", "mode": mode})
        self._emit_fault(addr, f"fault:{mode}", name=rec.name)
        self._emit_topology()
        return web.json_response({"ok": True, "node": addr, "name": rec.name, "mode": mode})

    async def h_rogue(self, request):
        body = await self._body(request)
        addr = self.resolve(body.get("node"))
        if addr is None:
            return self._bad(f"unknown node {body.get('node')!r}")
        rec = self.nodes[addr]
        self._control(rec, {"cmd": "rogue"})
        self._emit_fault(addr, "rogue", name=rec.name)
        self._emit_topology()
        return web.json_response({"ok": True, "node": addr, "name": rec.name})

    async def h_export(self, request):
        body = await self._body(request)
        run_id = body.get("runId")
        if not isinstance(run_id, str) or not run_id:
            return self._bad("runId required")
        for rec in self.nodes.values():
            self._control(rec, {"cmd": "export", "runId": run_id})
        self._emit_topology()
        return web.json_response({"ok": True, "runId": run_id, "sent": len(self.nodes)})


async def _amain(args) -> None:
    hub = Hub(host=args.host, udp_port=args.udp_port, http_port=args.http_port,
              observer=parse_hostport(args.observer, OBSERVER_UDP_PORT))
    await hub.start()
    print(f"[hub] UDP {args.host}:{hub.udp_port}  HTTP {args.host}:{hub.http_port}  observer {args.observer}", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await hub.close()


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="SwarmShield UDP mesh hub (radio emulator)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--udp-port", type=int, default=HUB_UDP_PORT)
    ap.add_argument("--http-port", type=int, default=HUB_HTTP_PORT)
    ap.add_argument("--observer", default=f"127.0.0.1:{OBSERVER_UDP_PORT}", help="observer UDP host:port for events")
    args = ap.parse_args(argv)
    try:
        asyncio.run(_amain(args))
    except KeyboardInterrupt:
        print("[hub] stopped", flush=True)


if __name__ == "__main__":
    main()
