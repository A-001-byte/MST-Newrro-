"""Per-drone sensor: WORLD truth + Gaussian noise -> OBS txs (00_CONTEXT 16, docs/02 B3). Integers only.

Full drones only (light nodes have no sensor). The `false_obs` fault adds +3000 cm to x (lying about the world;
bounded by median fusion, not provable, never revoked).
"""
from __future__ import annotations

import math
import random
from typing import Callable

FALSE_OBS_OFFSET_CM = 3000
WORLD_STALE_MS = 2500      # stop observing when WORLD has not been heard for this long


class Sensor:
    def __init__(self, pos_cm: tuple[int, int], world_cfg: dict, obs_interval_ms: int,
                 submit: Callable[[dict], object], clock: Callable[[], int], rng: random.Random | None = None):
        self.pos = (int(pos_cm[0]), int(pos_cm[1]))
        self.range_cm = int(world_cfg["SENSOR_RANGE_CM"])
        self.sigma_cm = int(world_cfg["SENSOR_SIGMA_CM"])
        self.w = int(world_cfg.get("width_cm", 20000))
        self.h = int(world_cfg.get("height_cm", 10000))
        self.interval = int(obs_interval_ms)
        self.submit = submit
        self.clock = clock
        self.rng = rng or random.Random()
        self.fault = "honest"
        self.objects: dict[str, tuple[int, int]] = {}
        self.world_at = -10**9
        self.last_obs = -10**9
        self.sent = 0

    def set_fault(self, mode: str) -> None:
        self.fault = mode

    def on_world(self, msg: dict) -> None:
        """{"hub":"WORLD","t_ms":..,"objects":[{"id","x_cm","y_cm"}]}"""
        objs = {}
        for o in msg.get("objects", []):
            try:
                objs[str(o["id"])] = (int(o["x_cm"]), int(o["y_cm"]))
            except (KeyError, TypeError, ValueError):
                continue
        self.objects = objs
        self.world_at = self.clock()

    def tick(self) -> int:
        """Submit one OBS per visible object every OBS_INTERVAL_MS. Returns how many were submitted."""
        now = self.clock()
        if now - self.last_obs < self.interval or now - self.world_at > WORLD_STALE_MS:
            return 0
        self.last_obs = now
        n = 0
        for obj_id, (ox, oy) in sorted(self.objects.items()):
            dist = math.isqrt((ox - self.pos[0]) ** 2 + (oy - self.pos[1]) ** 2)
            if dist > self.range_cm:
                continue
            x = ox + int(round(self.rng.gauss(0, self.sigma_cm)))
            y = oy + int(round(self.rng.gauss(0, self.sigma_cm)))
            if self.fault == "false_obs":
                x += FALSE_OBS_OFFSET_CM
            x = min(max(x, 0), self.w)
            y = min(max(y, 0), self.h)
            conf = min(100, max(0, 100 - (dist * 60) // max(1, self.range_cm)))   # nearer = more confident
            self.submit({"type": "OBS", "obj": obj_id, "x_cm": x, "y_cm": y, "conf_pct": int(conf)})
            n += 1
        self.sent += n
        return n
