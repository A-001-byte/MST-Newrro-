"""Track A's real ClusterEngine + my MissionApp on the deterministic in-memory mesh (real signatures, real QCs).

Skipped automatically when swarmledger, its crypto dependencies, config/mission.json or .keys/ are missing.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
swl = pytest.importorskip("swarmledger.engine", reason="Track A package not importable (ledger/ + eth-keys)")
pytest.importorskip("eth_keys")
MISSION = ROOT / "config" / "mission.json"
KEYS = ROOT / ".keys"
pytestmark = pytest.mark.skipif(not MISSION.exists() or not KEYS.exists(), reason="needs config/mission.json and .keys/")

from swarmledger.config import load_mission  # noqa: E402
from swarmledger.crypto import Identity  # noqa: E402

from swarm import wire  # noqa: E402
from swarm.app import MissionApp  # noqa: E402
from swarm.node import apply_obs_conflict_shim  # noqa: E402
from swarm.tests.simnet import SimClock, SimNet  # noqa: E402


class RealSim:
    def __init__(self, clusters=(1,)):
        self.m = load_mission(str(MISSION))
        self.clock = SimClock()
        self.net = SimNet(self.clock, seed=3)
        self.engines, self.apps, self.events = {}, {}, []
        for cl in self.m.clusters:
            if cl.cluster_id not in clusters:
                continue
            for mem in cl.members:
                if mem["flags"] & 1:
                    continue          # the hardware board is a real device
                key = json.loads((KEYS / f"{mem['name']}.json").read_text(encoding="utf-8"))
                ep = self.net.endpoint(mem["address"], cl.cluster_id, False)
                app = MissionApp(cl.cluster_id, self.m)
                eng = swl.ClusterEngine(Identity.from_hex(key["priv"]), self.m, cl.cluster_id, ep, self.clock, app,
                                        on_event=lambda n, d, name=mem["name"], c=cl.cluster_id: self.events.append((name, c, n, d)))
                ep.set_receiver(eng.on_envelope)          # spec 13: the runtime feeds the engine
                self.engines[mem["name"]], self.apps[mem["name"]] = eng, app
        for e in self.engines.values():
            e.start()

    def run(self, ms, step=50):
        for _ in range(ms // step):
            self.clock.advance(step)
            while self.net.deliver_due():
                pass
            for e in self.engines.values():
                e.tick()
            while self.net.deliver_due():
                pass


def test_real_engines_agree_and_my_app_tracks_state():
    s = RealSim(clusters=(1, 2))
    s.run(9000)
    for cl, names in ((1, ["c1-d1", "c1-d2", "c1-d3", "c1-d4"]), (2, ["c2-d1", "c2-d2", "c2-d3", "c2-d4", "c2-d5"])):
        hs = [s.engines[n].st.height for n in names]
        assert min(hs) >= 5 and max(hs) - min(hs) <= 1, hs
        n = min(hs)
        assert len({tuple(b["blockHash"] for b in s.engines[x].blocks[:n]) for x in names}) == 1
    st = s.engines["c1-d2"].state()
    assert st["master"] == s.m.member_by_name("c1-d1")["address"] and st["fault"] == "honest"
    assert st["app"]["custody"] == {"OBJ-1": 1, "OBJ-2": 1}


def _obs_per_block(shim: bool):
    if shim:
        apply_obs_conflict_shim()
    s = RealSim()
    s.run(2000)
    for _ in range(8):                                   # every drone reports every second, like the sensors do
        for n, e in s.engines.items():
            e.submit_tx({"type": "OBS", "obj": "OBJ-1", "x_cm": 3000, "y_cm": 5000, "conf_pct": 80})
        s.run(1000)
    return max((sum(1 for i in b["txs"] if i["tx"]["type"] == "OBS") for b in s.engines["c1-d1"].blocks), default=0)


def test_track_a_one_obs_per_block_bug_is_reproduced_and_the_shim_fixes_it():
    import swarmledger.engine as E
    fixed_upstream = False
    if not getattr(E, "_swarm_sw_shim", False):
        without = _obs_per_block(shim=False)
        fixed_upstream = without > 1
        if not fixed_upstream:
            assert without == 1, "BUG (Track A): engine._build_block keeps only one OBS per block (None conflict key)"
    with_shim = _obs_per_block(shim=True)
    assert with_shim >= 2, "with the shim a block must carry several OBS"
    if fixed_upstream:
        pytest.skip("Track A fixed _conflict_key upstream; the shim is no longer needed")


def test_real_handoff_moves_custody_through_my_missionapp():
    apply_obs_conflict_shim()
    s = RealSim(clusters=(1, 2))
    s.run(2000)
    for _ in range(4):
        for n in ("c1-d1", "c1-d2", "c1-d3"):
            s.engines[n].submit_tx({"type": "OBS", "obj": "OBJ-1", "x_cm": 9400, "y_cm": 5000, "conf_pct": 80})
        s.run(1000)
    plan = s.apps["c1-d1"].handoff_plan("OBJ-1")
    assert plan and plan["dst"] == 2, "3 members at x=94 m beyond the 90 m line"
    s.engines["c1-d1"].submit_tx({"type": "XC_SEND", "dst": plan["dst"], "timeoutMs": 8000,
                                  "payload": {"kind": "HANDOFF", "obj": "OBJ-1", "track": {**plan["track"], "t_ms": 1}}})
    s.run(9000)
    names = [e[2] for e in s.events]
    for ev in ("xc_sent", "xc_verified", "xc_acked"):
        assert ev in names, ev
    assert all(s.apps[n].custody["OBJ-1"] == 2 for n in ("c1-d1", "c1-d4", "c2-d1", "c2-d5"))
    verified = next(d for n, c, ev, d in s.events if ev == "xc_verified")
    assert verified["signedWeight"] >= 9 and verified["activeWeight"] == 13, "certificate carries a real QC"
    assert not [d for _, _, ev, d in s.events if ev == "xc_rejected" and d["reason"] != "replay"]
