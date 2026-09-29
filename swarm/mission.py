"""Mission loader with the same attribute shape as swarmledger.config.MissionConfig (00_CONTEXT 13 / 15).

Mock mode uses MissionLite (no swarmledger import, rosterHash is not enforced so DEV fixtures load).
Real mode uses swarmledger.config.load_mission (lazy import, read-only PYTHONPATH=../MST-Newrro-/ledger).
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace


class MissionLite:
    def __init__(self, raw: dict):
        self.raw = raw
        self.mission_id: str = raw["missionId"].lower()
        self.mission_name: str = raw["missionName"]
        self.roster_hash: str = raw["rosterHash"].lower()
        self.hardware_proposes: bool = bool(raw.get("hardwareProposes", False))
        self.timing = SimpleNamespace(**raw["timing"])
        self.fusion = SimpleNamespace(**raw["fusion"])
        self.world = raw["world"]
        self.clusters = []
        for c in raw["clusters"]:
            members = [dict(m, address=m["address"].lower()) for m in c["members"]]
            self.clusters.append(SimpleNamespace(cluster_id=c["clusterId"], zone=c["zone"],
                                                 initial_master=c["initialMaster"], members=members))
        self._by_addr = {m["address"]: (cl.cluster_id, m) for cl in self.clusters for m in cl.members}
        self._by_name = {m["name"]: (cl.cluster_id, m) for cl in self.clusters for m in cl.members}
        self.is_dev_mock = "DEV/MOCK" in str(raw.get("_comment", ""))

    def cluster(self, cluster_id: int):
        return next(c for c in self.clusters if c.cluster_id == cluster_id)

    def member_by_name(self, name: str) -> dict:
        return self._by_name[name][1]

    def member_by_address(self, addr: str) -> dict | None:
        hit = self._by_addr.get(addr.lower())
        return hit[1] if hit else None

    def cluster_of(self, addr: str) -> int | None:
        hit = self._by_addr.get(addr.lower())
        return hit[0] if hit else None

    def cluster_of_name(self, name: str) -> int:
        return self._by_name[name][0]

    def zone_of(self, cluster_id: int) -> dict:
        return self.cluster(cluster_id).zone

    def members_of_host(self, host: str) -> list[tuple[int, dict]]:
        """[(cluster_id, member)] whose `host` matches (comma list or 'all' accepted)."""
        wanted = {h.strip() for h in host.split(",")}
        return [(cl.cluster_id, m) for cl in self.clusters for m in cl.members
                if "all" in wanted or m.get("host") in wanted]

    def all_members(self) -> list[tuple[int, dict]]:
        return [(cl.cluster_id, m) for cl in self.clusters for m in cl.members]


def members_of_host(mission, host: str) -> list[tuple[int, dict]]:
    """Works for MissionLite and for swarmledger's MissionConfig: [(cluster_id, member)] for host labels (comma list / all)."""
    wanted = {h.strip() for h in host.split(",")}
    return [(cl.cluster_id, m) for cl in mission.clusters for m in cl.members
            if "all" in wanted or m.get("host") in wanted]


def load_mission_file(path: str | Path, engine: str = "mock"):
    """engine='real' goes through swarmledger (rosterHash is verified there). UNTESTED until Track A ships it."""
    if engine == "real":
        from swarmledger.config import load_mission  # lazy: needs PYTHONPATH=../MST-Newrro-/ledger
        return load_mission(str(path))
    with open(path, encoding="utf-8") as f:
        return MissionLite(json.load(f))
