"""Director: world truth (00_CONTEXT 16) + scripted scenario runner.

* WORLD to the hub at 10 Hz (UDP), `world` events to the observer at 5 Hz. Objects exist only after `start_object`;
  they move linearly at speed_cmps from start to end, then stop (and keep being reported at the end point).
* Scenario runner: YAML timeline -> hub control calls (HTTP 47001). The hub itself emits `fault_injected`; the
  director additionally emits additive `scenario` events {name, state: started|step|finished|stopped}.

Non-kinetic: objects are drifting boats (search and rescue). Integers only (cm, ms).
"""
from __future__ import annotations

import asyncio
import json
import socket
import time
from pathlib import Path

import aiohttp
import yaml

from .common import HUB_HTTP_PORT, HUB_UDP_PORT, EventSender, dumps, resolve_ipv4

SCENARIO_DIR = Path(__file__).resolve().parent / "scenarios"
HUB_ACTIONS = {"kill", "revive", "partition", "heal", "drop", "fault", "rogue", "export"}
WORLD_ACTIONS = {"start_object", "stop_object", "reset_objects", "note"}
ACTIONS = HUB_ACTIONS | WORLD_ACTIONS
DEFAULT_PARTITION = [["cluster:1"], ["cluster:2"]]


class WorldObject:
    def __init__(self, cfg: dict):
        self.id = cfg["id"]
        self.label = cfg.get("label", self.id)
        self.start = (int(cfg["start"][0]), int(cfg["start"][1]))
        self.end = (int(cfg["end"][0]), int(cfg["end"][1]))
        self.speed = int(cfg["speed_cmps"])
        self.started_ms: int | None = None
        dx, dy = self.end[0] - self.start[0], self.end[1] - self.start[1]
        self.length = int((dx * dx + dy * dy) ** 0.5)

    def pos(self, now_ms: int) -> tuple[int, int]:
        if self.started_ms is None:
            return self.start
        travelled = min(self.length, self.speed * max(0, now_ms - self.started_ms) // 1000)
        if self.length == 0:
            return self.start
        return (self.start[0] + (self.end[0] - self.start[0]) * travelled // self.length,
                self.start[1] + (self.end[1] - self.start[1]) * travelled // self.length)

    def arrived(self, now_ms: int) -> bool:
        return self.started_ms is not None and self.speed * (now_ms - self.started_ms) // 1000 >= self.length


def load_scenario(name_or_path: str, directory: Path = SCENARIO_DIR) -> dict:
    p = Path(name_or_path)
    if not p.exists():
        p = directory / (name_or_path if name_or_path.endswith((".yaml", ".yml")) else name_or_path + ".yaml")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        raw = {"timeline": raw}
    if not isinstance(raw, dict) or not isinstance(raw.get("timeline"), list):
        raise ValueError(f"{p.name}: need a timeline list")
    steps = []
    for i, e in enumerate(raw["timeline"]):
        if not isinstance(e, dict) or "t" not in e or e.get("do") not in ACTIONS:
            raise ValueError(f"{p.name} step {i}: needs t and do in {sorted(ACTIONS)}: {e!r}")
        if not isinstance(e["t"], (int, float)) or e["t"] < 0:
            raise ValueError(f"{p.name} step {i}: t must be a number >= 0")
        steps.append(dict(e))
    steps.sort(key=lambda e: e["t"])
    return {"name": raw.get("name", p.stem), "description": raw.get("description", ""), "timeline": steps}


def list_scenarios(directory: Path = SCENARIO_DIR) -> list[str]:
    return sorted(p.stem for p in directory.glob("*.yaml"))


class Director:
    def __init__(self, mission, hub_udp: tuple[str, int] = ("127.0.0.1", HUB_UDP_PORT),
                 hub_http: tuple[str, int] = ("127.0.0.1", HUB_HTTP_PORT), events: EventSender | None = None,
                 time_scale: float = 1.0, run_id: str = "run", world_hz: int = 10, event_hz: int = 5):
        self.objects = {o["id"]: WorldObject(o) for o in mission.world["objects"]}
        self.hub_udp = (resolve_ipv4(hub_udp[0]), hub_udp[1])
        self.hub_http = hub_http
        self.events = events
        self.time_scale = time_scale
        self.run_id = run_id
        self.period = 1.0 / world_hz
        self.event_every = max(1, world_hz // event_hz)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.t0 = time.monotonic()
        self._task: asyncio.Task | None = None
        self._scenario_task: asyncio.Task | None = None
        self.scenario_name: str | None = None
        self.scenario_started: float | None = None
        self.log: list[dict] = []

    # ------------------------------------------------------------------- clock ----
    def now_ms(self) -> int:
        return int((time.monotonic() - self.t0) * 1000)

    # ------------------------------------------------------------------- world ----
    def start_object(self, obj_id: str) -> bool:
        o = self.objects.get(obj_id)
        if o is None or o.started_ms is not None:
            return False
        o.started_ms = self.now_ms()
        return True

    def stop_object(self, obj_id: str) -> bool:
        o = self.objects.get(obj_id)
        if o is None or o.started_ms is None:
            return False
        o.start = o.pos(self.now_ms())     # freeze in place
        o.end = o.start
        o.length = 0
        return True

    def reset_objects(self) -> None:
        for o in self.objects.values():
            o.started_ms = None

    def world_msg(self) -> dict:
        now = self.now_ms()
        objs = []
        for o in self.objects.values():
            if o.started_ms is not None:
                x, y = o.pos(now)
                objs.append({"id": o.id, "x_cm": x, "y_cm": y})
        return {"hub": "WORLD", "t_ms": now, "objects": objs}

    def _emit(self, event: str, data: dict) -> None:
        if self.events is not None:
            self.events.emit("director", None, None, event, data)

    async def run(self) -> None:
        n = 0
        while True:
            msg = self.world_msg()
            try:
                self.sock.sendto(dumps(msg), self.hub_udp)
            except OSError:
                pass
            n += 1
            if n % self.event_every == 0:
                self._emit("world", {"t_ms": msg["t_ms"], "objects": msg["objects"]})
            await asyncio.sleep(self.period)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self.run())

    async def close(self) -> None:
        await self.stop_scenario()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        self.sock.close()

    # --------------------------------------------------------------- hub control ---
    async def hub_post(self, path: str, body: dict) -> dict:
        url = f"http://{self.hub_http[0]}:{self.hub_http[1]}{path}"
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4)) as s:
                async with s.post(url, json=body) as r:
                    return await r.json()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": type(e).__name__}

    async def do(self, step: dict) -> dict:
        act = step["do"]
        if act == "start_object":
            return {"ok": self.start_object(step["obj"])}
        if act == "stop_object":
            return {"ok": self.stop_object(step["obj"])}
        if act == "reset_objects":
            self.reset_objects()
            return {"ok": True}
        if act == "note":
            return {"ok": True}
        if act in ("kill", "revive", "rogue"):
            return await self.hub_post("/" + act, {"node": step["node"]})
        if act == "partition":
            return await self.hub_post("/partition", {"groups": step.get("groups", DEFAULT_PARTITION)})
        if act == "heal":
            return await self.hub_post("/heal", {})
        if act == "drop":
            return await self.hub_post("/drop", {"node": step.get("node", "*"), "rate": step["rate"]})
        if act == "fault":
            return await self.hub_post("/fault", {"node": step["node"], "mode": step["mode"]})
        if act == "export":
            return await self.hub_post("/export", {"runId": step.get("runId", self.run_id)})
        return {"ok": False, "error": "unknown action"}

    # ----------------------------------------------------------------- scenarios ---
    def status(self) -> dict:
        running = self._scenario_task is not None and not self._scenario_task.done()
        return {"running": running, "name": self.scenario_name if running else None,
                "elapsed_s": round(time.monotonic() - self.scenario_started, 1) if running and self.scenario_started else None,
                "available": list_scenarios()}

    async def start_scenario(self, name: str) -> dict:
        if self._scenario_task is not None and not self._scenario_task.done():
            return {"ok": False, "error": f"scenario {self.scenario_name!r} already running"}
        try:
            sc = load_scenario(name)
        except (OSError, ValueError, yaml.YAMLError) as e:
            return {"ok": False, "error": str(e)}
        self.reset_objects()
        self.scenario_name = sc["name"]
        self._scenario_task = asyncio.get_running_loop().create_task(self._run_scenario(sc))
        return {"ok": True, "name": sc["name"], "steps": len(sc["timeline"])}

    async def stop_scenario(self) -> None:
        t = self._scenario_task
        if t is not None and not t.done():
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._emit("scenario", {"name": self.scenario_name, "state": "stopped"})
        self._scenario_task = None

    async def _run_scenario(self, sc: dict) -> None:
        self.scenario_started = time.monotonic()
        self._emit("scenario", {"name": sc["name"], "state": "started", "steps": len(sc["timeline"]),
                                "description": sc["description"]})
        for step in sc["timeline"]:
            delay = step["t"] / self.time_scale - (time.monotonic() - self.scenario_started)
            if delay > 0:
                await asyncio.sleep(delay)
            res = await self.do(step)
            entry = {"name": sc["name"], "state": "step", "t": step["t"], "do": step["do"],
                     "args": {k: v for k, v in step.items() if k not in ("t", "do")}, "ok": bool(res.get("ok"))}
            if step["do"] == "note":
                entry["message"] = step.get("message", "")
            self.log.append(entry)
            self._emit("scenario", entry)
        self._emit("scenario", {"name": sc["name"], "state": "finished"})
