"""One drone: transport + engine + MissionApp + sensor + handoff trigger + observer event forwarding.

  ./.venv/Scripts/python -m swarm.node --name c1-d2 --hub 127.0.0.1:47000 --engine mock

Engine kinds: `mock` (swarm/mock_engine.py) or `real` (swarmledger.ClusterEngine, lazy import; UNTESTED until
Track A ships engine.py; needs PYTHONPATH=../MST-Newrro-/ledger and --keys <dir with <name>.json>).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from pathlib import Path

import aiohttp

from . import wire
from .app import MissionApp
from .common import (HUB_UDP_PORT, OBSERVER_HTTP_PORT, OBSERVER_UDP_PORT, EventSender, parse_hostport)
from .mission import load_mission_file
from .sensors import Sensor
from .transport_udp import UdpHubTransport

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNS = ROOT / "runs"
XC_GUARD_MS = 4000          # local "one XC_SEND per object in flight" guard expires if the tx never commits


class MonoClock:
    def now_ms(self) -> int:
        return int(time.monotonic() * 1000)


def default_mission_path() -> Path:
    real = ROOT / "config" / "mission.json"
    return real if real.exists() else ROOT / "config" / "mission.dev.json"


def apply_obs_conflict_shim() -> bool:
    """OPT-IN workaround for a Track A engine bug (do not edit their file, report it instead).

    swarmledger.engine._build_block does `k = _conflict_key(tx); if k in keys: continue; keys.add(k)`. For OBS/ZONE
    `_conflict_key` returns None, so after the first OBS `None in keys` is true and every further OBS in the block is
    skipped: one OBS per block. The shim gives each OBS a unique conflict key. Harmless once Track A fixes it.
    """
    import swarmledger.engine as E  # type: ignore[import-not-found]
    if getattr(E, "_swarm_sw_shim", False):
        return False
    orig = E._conflict_key
    E._conflict_key = lambda tx: orig(tx) or ("UNIQUE", tx.get("type"), tx.get("from"), tx.get("seq"))
    E._swarm_sw_shim = True
    return True


def build_engine(kind: str, member: dict, mission, cluster_id: int, transport, clock, app, on_commit, on_event,
                 fault: str, light: bool, keys_dir: Path | None, seed: int | None):
    if kind == "mock":
        from .mock_engine import MockEngine, MockIdentity
        return MockEngine(MockIdentity(member["address"]), mission, cluster_id, transport, clock, app,
                          on_commit=on_commit, on_event=on_event, fault=fault, light=light, seed=seed)
    if kind == "real":   # UNTESTED seam: same constructor as 00_CONTEXT 13
        from swarmledger.crypto import Identity  # type: ignore[import-not-found]
        from swarmledger.engine import ClusterEngine  # type: ignore[import-not-found]
        if keys_dir is None:
            raise SystemExit("--engine real needs --keys <dir with <name>.json> (private channel, never git)")
        key = json.loads((keys_dir / f"{member['name']}.json").read_text(encoding="utf-8"))
        return ClusterEngine(Identity.from_hex(key["priv"]), mission, cluster_id, transport, clock, app,
                             on_commit=on_commit, on_event=on_event, fault=fault, light=light)
    raise SystemExit(f"unknown --engine {kind!r} (mock|real)")


class Node:
    def __init__(self, mission, cluster_id: int, member: dict, hub_addr: tuple[str, int], events: EventSender | None,
                 engine_kind: str = "mock", run_id: str = "run", runs_dir: Path = DEFAULT_RUNS,
                 observer_http: tuple[str, int] | None = None, host_label: str = "", keys_dir: Path | None = None,
                 fault: str = "honest", seed: int | None = None):
        self.mission, self.cluster, self.member = mission, cluster_id, member
        self.name, self.address = member["name"], member["address"].lower()
        self.light = bool(member["flags"] & 1)
        self.engine_kind = engine_kind
        self.mock = engine_kind == "mock"
        self.events = events
        self.run_id, self.runs_dir, self.observer_http = run_id, Path(runs_dir), observer_http
        self.host_label = host_label or member.get("host", "")
        self.T = mission.timing
        self.clock = MonoClock()
        self.rng = random.Random(seed if seed is not None else hash(self.name) & 0xFFFFFFFF)
        self.transport = UdpHubTransport(hub_addr, self.address, self.name, cluster_id, self.light,
                                         register_interval_ms=self.T.REGISTER_INTERVAL_MS)
        self.app = MissionApp(cluster_id, mission, emit=self._on_app_event)
        self.engine = build_engine(engine_kind, member, mission, cluster_id, self.transport, self.clock, self.app,
                                   self._on_commit, self._on_event, fault, self.light, keys_dir, seed)
        # spec 13: the runtime feeds every DELIVERed envelope to engine.on_envelope (MockEngine also self-registers)
        self.transport.set_receiver(self.engine.on_envelope)
        self.sensor: Sensor | None = None
        if not self.light:
            self.sensor = Sensor(tuple(member["pos_cm"]), mission.world, self.T.OBS_INTERVAL_MS, self._submit_obs,
                                 self.clock.now_ms, self.rng)
            self.transport.set_world_handler(self.sensor.on_world)
        self.transport.set_control_handler(self._on_control)
        self._xc_inflight: dict[str, dict] = {}
        self._tasks: list[asyncio.Task] = []
        self._stopped = False
        self.commits = 0
        self.handoffs_submitted = 0

    # ------------------------------------------------------------------- events ----
    def _emit(self, event: str, data: dict) -> None:
        if self.events is not None:
            self.events.emit(self.address, self.name, self.cluster, event, data)

    def _on_event(self, name: str, data: dict) -> None:
        self._emit(name, data)

    def _on_app_event(self, name: str, data: dict) -> None:
        if self.mock:
            data = {**data, "mock": True}
        self._emit(name, data)

    def _submit_obs(self, tx: dict) -> None:
        self.engine.submit_tx(tx)

    # --------------------------------------------------------- handoff trigger ----
    def _on_commit(self, info: dict) -> None:
        self.commits += 1
        if self.light:
            return
        try:
            self._maybe_handoff(info)
        except Exception as e:  # noqa: BLE001 - the trigger must never break the commit callback
            print(f"[{self.name}] handoff trigger error: {e!r}", file=sys.stderr, flush=True)

    def _maybe_handoff(self, info: dict) -> None:
        """12.5 rule 7 + docs/02 B3: master submits XC_SEND once per object until it commits or times out."""
        now = self.clock.now_ms()
        committed = {wire.tx_hash(i["tx"]) for i in info.get("txs", []) if i["tx"].get("type") == "XC_SEND"}
        for obj, g in list(self._xc_inflight.items()):
            if g["hash"] in committed or now - g["at"] > XC_GUARD_MS:
                del self._xc_inflight[obj]
        if self.engine.state()["master"] != self.address:
            return
        for obj in self.app.objects:
            if obj in self._xc_inflight:
                continue
            plan = self.app.handoff_plan(obj)
            if plan is None:
                continue
            tx = {"type": "XC_SEND", "dst": plan["dst"], "timeoutMs": int(self.T.XC_TIMEOUT_MS),
                  "payload": {"kind": "HANDOFF", "obj": obj, "track": {**plan["track"], "t_ms": now}}}
            h = self.engine.submit_tx(tx)        # xcSeq is filled by the engine
            self._xc_inflight[obj] = {"hash": h, "at": now}
            self.handoffs_submitted += 1

    # ----------------------------------------------------------------- CONTROL -----
    def _on_control(self, msg: dict) -> None:
        cmd = msg.get("cmd")
        if cmd == "fault":
            mode = msg.get("mode", "honest")
            self.engine.set_fault(mode)
            if self.sensor:
                self.sensor.set_fault(mode)
        elif cmd == "rogue":                 # a simulated drone "goes rogue" = starts double-signing
            self.engine.set_fault("equivocate")
        elif cmd == "export":
            run_id = str(msg.get("runId") or self.run_id)
            asyncio.get_running_loop().create_task(self.export(run_id))

    # ----------------------------------------------------------------- lifecycle ---
    async def start(self) -> None:
        await self.transport.start()
        self.engine.start()
        self.app.announce_initial()
        self._emit("node_up", {"name": self.name, "address": self.address, "cluster": self.cluster,
                               "light": self.light, "host": self.host_label, "engine": self.engine_kind,
                               **({"mock": True} if self.mock else {})})
        self._tasks = [asyncio.create_task(self._tick_loop()), asyncio.create_task(self._state_loop())]

    async def _tick_loop(self) -> None:
        period = self.T.TICK_MS / 1000
        while True:
            self.engine.tick()
            if self.sensor:
                self.sensor.tick()
            await asyncio.sleep(period)

    async def _state_loop(self) -> None:
        while True:
            st = self.engine.state()
            drops = getattr(self.engine, "drops", None) or getattr(self.engine, "dropped", None)
            if drops:                       # additive diagnostics: why the engine ignored envelopes
                st = {**st, "drops": dict(drops)}
            self._emit("node_state", st)
            await asyncio.sleep(1.0)

    async def stop(self, export: bool = True) -> None:
        if self._stopped:
            return
        self._stopped = True
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if export and not self.light:
            await self.export(self.run_id)
        await self.transport.close()

    # ------------------------------------------------------------------- export ----
    async def export(self, run_id: str) -> Path | None:
        """Write runs/<runId>/<name>.ledger.json AND upload it to the observer (11.8)."""
        try:
            data = self.engine.export_certified()
        except Exception as e:  # noqa: BLE001
            print(f"[{self.name}] export failed: {e!r}", file=sys.stderr, flush=True)
            return None
        path = self.runs_dir / run_id / f"{self.name}.ledger.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        if self.observer_http:
            url = f"http://{self.observer_http[0]}:{self.observer_http[1]}/runs/{run_id}/export/{self.name}"
            try:
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
                    async with s.post(url, json=data) as r:
                        if r.status != 200:
                            print(f"[{self.name}] export upload -> HTTP {r.status}", file=sys.stderr, flush=True)
            except Exception as e:  # noqa: BLE001 - central upload is best effort; the local file is the source
                print(f"[{self.name}] export upload failed: {type(e).__name__}", file=sys.stderr, flush=True)
        return path


def add_common_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--mission", default=None, help="mission json (default: config/mission.json, else mission.dev.json)")
    ap.add_argument("--hub", default=f"127.0.0.1:{HUB_UDP_PORT}", help="hub HOST:47000 (laptop A)")
    ap.add_argument("--observer", default=None, help="observer HOST:47002 (default: hub host)")
    ap.add_argument("--engine", choices=["mock", "real"], default="mock")
    ap.add_argument("--keys", default=None, help="dir with <name>.json private keys (real engine only, never git)")
    ap.add_argument("--shim-obs-conflict", action="store_true",
                    help="real engine only: work around Track A's one-OBS-per-block bug (see apply_obs_conflict_shim)")
    ap.add_argument("--run-id", default=None, help="run id for exports (default run-<timestamp>)")
    ap.add_argument("--runs-dir", default=str(DEFAULT_RUNS))


def resolve_common(args):
    mission_path = Path(args.mission) if args.mission else default_mission_path()
    mission = load_mission_file(mission_path, args.engine)
    if args.engine == "real" and getattr(args, "shim_obs_conflict", False):
        if apply_obs_conflict_shim():
            print("[node] NOTE: OBS conflict-key shim ACTIVE (workaround for a Track A engine bug)", flush=True)
    hub_addr = parse_hostport(args.hub, HUB_UDP_PORT)
    obs_host = args.observer.rsplit(":", 1)[0] if args.observer else hub_addr[0]
    obs_udp = parse_hostport(args.observer, OBSERVER_UDP_PORT) if args.observer else (hub_addr[0], OBSERVER_UDP_PORT)
    run_id = args.run_id or time.strftime("run-%Y%m%d-%H%M%S")
    return mission, mission_path, hub_addr, obs_udp, (obs_host, OBSERVER_HTTP_PORT), run_id


async def _run_forever(nodes: list[Node]) -> None:
    for n in nodes:
        await n.start()
    try:
        await asyncio.Event().wait()
    finally:
        await asyncio.gather(*(n.stop() for n in nodes))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Run one SwarmShield drone")
    add_common_args(ap)
    ap.add_argument("--name", required=True, help="member name, e.g. c1-d2")
    args = ap.parse_args(argv)
    mission, path, hub_addr, obs_udp, obs_http, run_id = resolve_common(args)
    cl = mission.cluster_of_name(args.name) if hasattr(mission, "cluster_of_name") else mission.cluster_of(
        mission.member_by_name(args.name)["address"])
    member = mission.member_by_name(args.name)
    events = EventSender(obs_udp)
    node = Node(mission, cl, member, hub_addr, events, args.engine, run_id, Path(args.runs_dir), obs_http,
                keys_dir=Path(args.keys) if args.keys else None)
    print(f"[node] {args.name} cluster {cl} engine {args.engine} hub {hub_addr[0]}:{hub_addr[1]} run {run_id}", flush=True)
    try:
        asyncio.run(_run_forever([node]))
    except KeyboardInterrupt:
        print("[node] stopped", flush=True)


if __name__ == "__main__":
    main()
