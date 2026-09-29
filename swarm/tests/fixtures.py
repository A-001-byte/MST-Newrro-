"""DEV / MOCK mission fixture: FAKE addresses (sha256 of the drone name), no private keys anywhere.

`python -m swarm.tests.fixtures` (re)writes config/mission.dev.json. Same shape as MST-Newrro-/config/mission.example.json.
The rosterHash is computed with the real 10.7 formula over the fake addresses, so swarmledger.load_mission accepts it.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from swarm import wire
from swarm.mission import MissionLite

ROOT = Path(__file__).resolve().parents[2]
DEV_PATH = ROOT / "config" / "mission.dev.json"

# (name, weight, flags, host, pos_cm)
C1 = [("c1-d1", 3, 0, "B", (5000, 5000)), ("c1-d2", 3, 0, "B", (3000, 3000)), ("c1-d3", 3, 0, "B", (3000, 7000)),
      ("c1-d4", 3, 0, "A", (7000, 3000)), ("c1-board", 1, 1, "board", (7000, 7000))]
C2 = [("c2-d1", 3, 0, "C", (15000, 5000)), ("c2-d2", 3, 0, "C", (13000, 3000)), ("c2-d3", 3, 0, "C", (13000, 7000)),
      ("c2-d4", 3, 0, "C", (17000, 3000)), ("c2-d5", 3, 0, "A", (17000, 7000))]

TIMING = {"TICK_MS": 50, "TIMEOUT_PROPOSE_MS": 1500, "TIMEOUT_PREVOTE_MS": 1000, "TIMEOUT_PRECOMMIT_MS": 1000,
          "TIMEOUT_DELTA_MS": 500, "COMMIT_INTERVAL_MS": 1000, "MAX_TXS_PER_BLOCK": 64, "ROLE_SILENCE_MS": 3000,
          "HELLO_INTERVAL_MS": 1000, "PEER_SILENCE_MS": 5000, "XC_TIMEOUT_MS": 8000, "XC_RETRY_MS": 2000,
          "OBS_INTERVAL_MS": 1000, "REGISTER_INTERVAL_MS": 2000}


def fake_address(name: str) -> str:
    return "0x" + hashlib.sha256(("DEV-MOCK-" + name).encode()).hexdigest()[:40]


def fake_pubkey(name: str) -> str:
    return "0x04" + (hashlib.sha256(("DEV-MOCK-PUB-" + name).encode()).hexdigest() * 2)


def _members(rows):
    return [{"name": n, "address": fake_address(n), "pubkey": fake_pubkey(n), "weight": w, "flags": f, "host": h,
             "pos_cm": list(p)} for n, w, f, h, p in rows]


def make_dev_mission(timing: dict | None = None) -> dict:
    mission_id = wire.hx(wire.sha256(wire.DOMAIN + b"MISSION" + b"swarmshield-dev-mock"))
    clusters = [
        {"clusterId": 1, "zone": {"xMin_cm": 0, "xMax_cm": 10000, "yMin_cm": 0, "yMax_cm": 10000},
         "initialMaster": "c1-d1", "members": _members(C1)},
        {"clusterId": 2, "zone": {"xMin_cm": 10000, "xMax_cm": 20000, "yMin_cm": 0, "yMax_cm": 10000},
         "initialMaster": "c2-d1", "members": _members(C2)},
    ]
    roster = [{"address": m["address"], "clusterId": c["clusterId"], "weight": m["weight"], "flags": m["flags"]}
              for c in clusters for m in c["members"]]
    return {
        "_comment": "DEV/MOCK fixture: FAKE addresses and pubkeys derived from names, NO private keys. Mock engine only. "
                    "Shape = MST-Newrro-/config/mission.example.json. Never use for anything real.",
        "version": 1,
        "missionName": "swarmshield-dev-mock",
        "missionId": mission_id,
        "rosterHash": wire.roster_hash(mission_id, roster),
        "hardwareProposes": False,
        "timing": {**TIMING, **(timing or {})},
        "fusion": {"FUSION_WINDOW_HEIGHTS": 5, "FUSION_MIN_MEMBERS": 2, "HANDOFF_MARGIN_CM": 1000},
        "world": {
            "width_cm": 20000, "height_cm": 10000, "SENSOR_RANGE_CM": 6000, "SENSOR_SIGMA_CM": 200,
            "objects": [
                {"id": "OBJ-1", "label": "drifting boat (search & rescue)", "start": [2000, 5000],
                 "end": [18000, 5000], "speed_cmps": 500, "initialCustody": 1,
                 "startOn": "director command start_object (Act 1)"},
                {"id": "OBJ-2", "label": "second boat (Act 4: handoff deferred during partition)",
                 "start": [2000, 3000], "end": [18000, 3000], "speed_cmps": 500, "initialCustody": 1,
                 "startOn": "scenario, during the partition (Act 4)"},
            ],
        },
        "clusters": clusters,
    }


def dev_mission(timing: dict | None = None) -> MissionLite:
    return MissionLite(make_dev_mission(timing))


def write_dev_mission(path: Path = DEV_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(make_dev_mission(), indent=2) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    p = write_dev_mission()
    print(f"wrote {p}")
    sys.exit(0)
