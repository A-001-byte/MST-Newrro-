"""Deterministic in-memory mesh for engine/app tests: ManualClock, hub-faithful light filtering, kill/partition/drop."""
from __future__ import annotations

import heapq
import json
import random

from swarm.hub import LIGHT_TYPES, light_env
from swarm.mock_engine import MockEngine, MockIdentity


class SimClock:
    def __init__(self, start: int = 1_000_000):
        self.t = start

    def now_ms(self) -> int:
        return self.t

    def advance(self, ms: int) -> None:
        self.t += ms


class _EP:
    def __init__(self, net: "SimNet", addr: str, cluster: int, light: bool):
        self.net, self.addr, self.cluster, self.light = net, addr, cluster, light
        self.handler = None

    def send(self, to: str, env: dict) -> None:
        self.net.send(self, to, env)

    def set_receiver(self, handler) -> None:
        self.handler = handler


class SimNet:
    def __init__(self, clock: SimClock, seed: int = 0, delay_ms=(1, 15), drop: float = 0.0):
        self.clock, self.rng, self.delay_ms, self.drop = clock, random.Random(seed), delay_ms, drop
        self.eps: dict[str, _EP] = {}
        self.killed: set[str] = set()
        self.groups: list[list[str]] | None = None
        self.q: list = []
        self.n = 0
        self.delivered_types: dict[str, int] = {}
        self.sent_by: dict[str, int] = {}

    def endpoint(self, addr: str, cluster: int, light: bool = False) -> _EP:
        ep = self.eps[addr] = _EP(self, addr, cluster, light)
        return ep

    def partition(self, groups: list[list[str]]) -> None:
        self.groups = groups

    def heal(self) -> None:
        self.groups = None

    def _group(self, ep: _EP) -> int:
        if self.groups is None:
            return 0
        for i, g in enumerate(self.groups):
            if f"cluster:{ep.cluster}" in g or ep.addr in g:
                return i
        return len(self.groups)

    def send(self, src: _EP, to: str, env: dict) -> None:
        self.sent_by[src.addr] = self.sent_by.get(src.addr, 0) + 1
        if src.addr in self.killed:
            return
        if to == "*":
            tg = [e for e in self.eps.values() if e is not src]
        elif to.startswith("cluster:"):
            tg = [e for e in self.eps.values() if e.cluster == int(to[8:]) and e is not src]
        else:
            tg = [self.eps[to]] if to in self.eps and self.eps[to] is not src else []
        for dst in tg:
            if dst.addr in self.killed or self._group(src) != self._group(dst) or self.rng.random() < self.drop:
                continue
            e = env
            if dst.light:
                if env["type"] not in LIGHT_TYPES or env.get("cluster") != dst.cluster:
                    continue
                e = light_env(env)
            self.n += 1
            heapq.heappush(self.q, (self.clock.now_ms() + self.rng.randint(*self.delay_ms), self.n, dst.addr,
                                    json.dumps({"hub": "DELIVER", "env": e})))

    def deliver_due(self) -> int:
        k = 0
        while self.q and self.q[0][0] <= self.clock.now_ms():
            _, _, dst, blob = heapq.heappop(self.q)
            h = self.eps[dst].handler
            if h and dst not in self.killed:
                env = json.loads(blob)["env"]
                self.delivered_types[env["type"]] = self.delivered_types.get(env["type"], 0) + 1
                h(env)
                k += 1
        return k


class StubApp:
    """Just enough App for engine tests: OBS allowed, XC_SEND needs custody, custody flips on XC_RECV / XC_ACK."""

    def __init__(self, cluster_id: int, mission):
        self.cluster_id = cluster_id
        self.custody = {o["id"]: o["initialCustody"] for o in mission.world["objects"]}
        self.delivered: list[tuple[str, int]] = []

    def check_tx(self, tx, state):
        if tx["type"] == "XC_SEND" and self.custody.get(tx.get("payload", {}).get("obj")) != self.cluster_id:
            return False, "no_custody"
        return True, ""

    def deliver_tx(self, tx, state, height):
        self.delivered.append((tx["type"], height))
        if tx["type"] == "XC_RECV":
            self.custody[tx["packet"]["tx"]["payload"]["obj"]] = self.cluster_id
        elif tx["type"] == "XC_ACK":
            self.custody[tx["ackPacket"]["tx"]["packet"]["tx"]["payload"]["obj"]] = tx["dst"]

    def end_block(self, height, state):
        pass

    def snapshot(self):
        return {"custody": dict(self.custody)}


class Sim:
    """All members of a mission on one SimNet. events: list of (t_ms, node_name, cluster, event, data)."""

    def __init__(self, mission, app_factory=None, seed: int = 1, only: list[str] | None = None, drop: float = 0.0):
        self.mission = mission
        self.clock = SimClock()
        self.net = SimNet(self.clock, seed=seed, drop=drop)
        self.events: list[tuple[int, str, int, str, dict]] = []
        self.commits: dict[str, list[dict]] = {}
        self.engines: dict[str, MockEngine] = {}
        self.apps: dict = {}
        self.addr: dict[str, str] = {}
        for cl_id, m in mission.all_members():
            if only and m["name"] not in only:
                continue
            self.add(cl_id, m, app_factory)
        for e in self.engines.values():
            e.start()

    def add(self, cl_id: int, m: dict, app_factory=None) -> MockEngine:
        name = m["name"]
        light = bool(m["flags"] & 1)
        app = app_factory(cl_id, self.mission) if app_factory else StubApp(cl_id, self.mission)
        ep = self.net.endpoint(m["address"], cl_id, light)
        self.commits[name] = []
        eng = MockEngine(MockIdentity(m["address"]), self.mission, cl_id, ep, self.clock, app,
                         on_commit=lambda info, n=name: self.commits[n].append(info),
                         on_event=lambda ev, d, n=name, c=cl_id: self.events.append((self.clock.now_ms(), n, c, ev, d)),
                         light=light, seed=hash(name) & 0xFFFF)
        self.engines[name], self.apps[name], self.addr[name] = eng, app, m["address"]
        return eng

    def run(self, ms: int, step: int = 50) -> None:
        for _ in range(ms // step):
            self.clock.advance(step)
            while self.net.deliver_due():
                pass
            for e in self.engines.values():   # a "killed" node only loses its radio (like the hub); it keeps ticking
                e.tick()
            while self.net.deliver_due():
                pass

    def kill(self, name: str) -> None:
        self.net.killed.add(self.addr[name])

    def revive(self, name: str) -> None:
        self.net.killed.discard(self.addr[name])

    def ev(self, event: str, **match) -> list[tuple[int, str, int, str, dict]]:
        out = [e for e in self.events if e[3] == event]
        return [e for e in out if all(e[4].get(k) == v for k, v in match.items())]

    def height(self, name: str) -> int:
        return self.engines[name].ledger.height
