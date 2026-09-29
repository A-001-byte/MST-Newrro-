"""MissionApp: fusion, custody, handoff truth table, member score, determinism (B3 pure logic)."""
from __future__ import annotations

import copy
import json

import pytest

from swarm import wire
from swarm.app import MissionApp
from swarm.tests.fixtures import dev_mission

M = dev_mission()
C1 = [m["address"] for m in M.cluster(1).members if not m["flags"] & 1]      # 4 full validators
C2 = [m["address"] for m in M.cluster(2).members]
MASTER1 = M.member_by_name("c1-d1")["address"]


class FakeView:
    """Just the LedgerView attributes MissionApp is allowed to read."""

    def __init__(self, members, master=None):
        self.committee = {a: {"weight": 3, "flags": 0} for a in members}
        self.master = master or members[0]
        self.contingency: dict = {}
        self.pending_sends: dict = {}
        self.received: set = set()
        self.height = 0

    def is_member(self, a):
        return a in self.committee

    def weight_of(self, a):
        return self.committee.get(a, {}).get("weight", 0)


def obs(member, x, y=5000, c=90, obj="OBJ-1"):
    return {"type": "OBS", "mission": M.mission_id, "cluster": 1, "from": member, "seq": 1, "t_ms": 0, "obj": obj,
            "x_cm": x, "y_cm": y, "conf_pct": c}


def commit(app, view, height, txs=()):
    view.height = height
    for tx in txs:
        app.deliver_tx(tx, view, height)
    app.end_block(height, view)


def app1(events=None):
    return MissionApp(1, M, emit=(lambda e, d: events.append((e, d))) if events is not None else None)


# --------------------------------------------------------------------- fusion ----
def test_lower_median_over_odd_and_even_counts():
    a, v = app1(), FakeView(C1)
    commit(a, v, 1, [obs(C1[0], 1000, 4000, 70), obs(C1[1], 3000, 6000, 90), obs(C1[2], 2000, 5000, 80)])
    assert a.accepted_track("OBJ-1") == {"x_cm": 2000, "y_cm": 5000, "conf_pct": 80, "members": 3, "height": 1}
    a2, v2 = app1(), FakeView(C1)
    commit(a2, v2, 1, [obs(C1[0], 100), obs(C1[1], 400), obs(C1[2], 200), obs(C1[3], 300)])
    assert a2.accepted_track("OBJ-1")["x_cm"] == 200, "lower median: sorted[(n-1)//2]"
    a3, v3 = app1(), FakeView(C1)
    commit(a3, v3, 1, [obs(C1[0], 100, c=10), obs(C1[1], 500, c=99)])
    t = a3.accepted_track("OBJ-1")
    assert t["x_cm"] == 100 and t["conf_pct"] == 10 and t["members"] == 2


def test_median_is_independent_of_delivery_order():
    txs = [obs(C1[i], x) for i, x in enumerate([900, 100, 700, 300])]
    results = set()
    for order in ([0, 1, 2, 3], [3, 2, 1, 0], [1, 3, 0, 2]):
        a, v = app1(), FakeView(C1)
        commit(a, v, 1, [txs[i] for i in order])
        results.add(json.dumps(a.accepted_track("OBJ-1"), sort_keys=True))
    assert len(results) == 1


def test_needs_at_least_two_distinct_members():
    a, v = app1(), FakeView(C1)
    commit(a, v, 1, [obs(C1[0], 1000)])
    assert a.accepted_track("OBJ-1") is None
    commit(a, v, 2, [obs(C1[0], 1100)])           # same member again is still one member
    assert a.accepted_track("OBJ-1") is None
    commit(a, v, 3, [obs(C1[1], 1300)])
    assert a.accepted_track("OBJ-1")["members"] == 2


def test_only_latest_obs_per_member_counts():
    a, v = app1(), FakeView(C1)
    commit(a, v, 1, [obs(C1[0], 1000), obs(C1[1], 1000)])
    commit(a, v, 2, [obs(C1[0], 2000)])
    assert a.accepted_track("OBJ-1")["x_cm"] == 1000       # lower median of [1000, 2000]


def test_fusion_window_is_strictly_the_last_five_heights():
    a, v = app1(), FakeView(C1)
    commit(a, v, 5, [obs(C1[0], 1000), obs(C1[1], 1000)])
    for h in (6, 7, 8, 9):
        commit(a, v, h)
        assert a.accepted_track("OBJ-1") is not None, h    # obs.height 5 > H-5 holds for H <= 9
    commit(a, v, 10)
    assert a.accepted_track("OBJ-1") is None, "obs.height 5 is not > 10 - 5"
    assert a.last_track["OBJ-1"]["height"] == 9, "last accepted track is remembered"


def test_inactive_or_revoked_members_are_ignored():
    a, v = app1(), FakeView(C1)
    commit(a, v, 1, [obs(C1[0], 1000), obs(C1[1], 1200), obs(C1[2], 9000)])
    assert a.accepted_track("OBJ-1")["x_cm"] == 1200
    del v.committee[C1[2]]                                  # revoked -> not in the active committee
    assert a.accepted_track("OBJ-1")["x_cm"] == 1000 and a.accepted_track("OBJ-1")["members"] == 2
    assert not a.deliver_tx(obs("0x" + "ee" * 20, 5), v, 2) and ("0x" + "ee" * 20, "OBJ-1") not in a.obs


def test_track_event_emitted_on_commit():
    ev = []
    a, v = app1(ev), FakeView(C1)
    commit(a, v, 3, [obs(C1[0], 1000), obs(C1[1], 1200)])
    assert ("track", {"obj": "OBJ-1", "x_cm": 1000, "y_cm": 5000, "conf_pct": 90, "members": 2, "height": 3}) in ev


# -------------------------------------------------------------------- check_tx ----
def test_check_tx_obs_and_zone():
    a, v = app1(), FakeView(C1)
    ok = lambda tx: a.check_tx(tx, v)[0]  # noqa: E731
    assert ok(obs(C1[0], 1000))
    assert not ok(obs(C1[0], -1)) and not ok(obs(C1[0], 20001)) and not ok(obs(C1[0], 1000, y=10001))
    assert not ok(obs(C1[0], 1000, c=101)) and not ok(obs(C1[0], 1000, c=-1))
    assert not ok({**obs(C1[0], 1000), "x_cm": 10.5}) and not ok(obs(C1[0], 1000, obj="OBJ-9"))
    assert not ok({**obs(C1[0], 1000), "x_cm": True})
    assert ok({"type": "ZONE", "zone": {"xMin_cm": 0, "xMax_cm": 9000, "yMin_cm": 0, "yMax_cm": 10000}})
    assert not ok({"type": "ZONE", "zone": {"xMin_cm": 9000, "xMax_cm": 0, "yMin_cm": 0, "yMax_cm": 10000}})
    assert a.check_tx({"type": "OBS"}, v)[0] is False        # malformed never raises


def send_tx(x=9500, obj="OBJ-1", dst=2, seq=1, frm=MASTER1):
    return {"type": "XC_SEND", "mission": M.mission_id, "cluster": 1, "from": frm, "seq": 5, "t_ms": 1, "dst": dst,
            "xcSeq": seq, "timeoutMs": 8000,
            "payload": {"kind": "HANDOFF", "obj": obj, "track": {"x_cm": x, "y_cm": 5000, "vx_cmps": 0,
                                                                "vy_cmps": 0, "t_ms": 1, "conf_pct": 90}}}


def near_line(a, v, height=3, x=9500):
    commit(a, v, height, [obs(C1[0], x), obs(C1[1], x + 100), obs(C1[2], x - 100)])


def test_check_tx_xc_send_rules():
    a, v = app1(), FakeView(C1)
    assert a.check_tx(send_tx(), v) == (False, "no_track")
    near_line(a, v)
    assert a.check_tx(send_tx(), v) == (True, "")
    assert a.check_tx(send_tx(dst=3), v) == (False, "wrong_dst")
    assert a.check_tx(send_tx(obj="OBJ-9"), v) == (False, "bad_payload")
    b, vb = app1(), FakeView(C1)
    near_line(b, vb, x=5000)
    assert b.check_tx(send_tx(), vb) == (False, "before_handoff_line")
    a.custody["OBJ-1"] = 2
    assert a.check_tx(send_tx(), v) == (False, "no_custody")


# ---------------------------------------------------------------------- custody ---
def recv_tx(send, src=1):
    return {"type": "XC_RECV", "mission": M.mission_id, "cluster": 2, "from": C2[1], "seq": 1, "t_ms": 1, "src": src,
            "xcSeq": send["xcSeq"], "packet": {"kind": "PACKET", "src": src, "dst": 2, "seq": send["xcSeq"], "tx": send}}


def ack_tx(recv, dst=2):
    return {"type": "XC_ACK", "mission": M.mission_id, "cluster": 1, "from": C1[1], "seq": 1, "t_ms": 1, "dst": dst,
            "xcSeq": recv["xcSeq"], "ackPacket": {"kind": "ACK", "src": 2, "dst": 1, "seq": recv["xcSeq"], "tx": recv}}


def test_custody_initial_recv_and_ack():
    ev1, ev2 = [], []
    a1, v1 = app1(ev1), FakeView(C1)
    a2, v2 = MissionApp(2, M, emit=lambda e, d: ev2.append((e, d))), FakeView(C2)
    a1.announce_initial()
    assert a1.custody == {"OBJ-1": 1, "OBJ-2": 1} and ("ownership", {"obj": "OBJ-1", "cluster": 1, "height": 0, "via": "initial"}) in ev1
    send = send_tx()
    v1.pending_sends[(2, 1)] = {"status": "SENT", "height": 4, "sendTxHash": wire.tx_hash(send)}
    commit(a1, v1, 4, [send])
    assert a1.custody["OBJ-1"] == 1 and a1.pending_handoff("OBJ-1"), "both clusters track between SEND and ACK"
    recv = recv_tx(send)
    v2.received.add((1, 1))
    commit(a2, v2, 5, [recv])
    assert a2.custody["OBJ-1"] == 2 and ("ownership", {"obj": "OBJ-1", "cluster": 2, "height": 5, "via": "XC_RECV"}) in ev2
    v1.pending_sends[(2, 1)]["status"] = "ACKED"
    commit(a1, v1, 6, [ack_tx(recv)])
    assert a1.custody["OBJ-1"] == 2 and ("ownership", {"obj": "OBJ-1", "cluster": 2, "height": 6, "via": "XC_ACK"}) in ev1
    assert not a1.pending_handoff("OBJ-1")
    assert a1.custody["OBJ-2"] == 1, "other objects unaffected"


def test_timeout_keeps_custody_and_late_ack_still_releases():
    ev = []
    a, v = app1(ev), FakeView(C1)
    send = send_tx()
    v.pending_sends[(2, 1)] = {"status": "SENT", "height": 4, "sendTxHash": wire.tx_hash(send)}
    commit(a, v, 4, [send])
    v.pending_sends[(2, 1)]["status"] = "TIMED_OUT"
    commit(a, v, 13, [{"type": "XC_TIMEOUT", "dst": 2, "xcSeq": 1, "from": MASTER1}])
    assert a.custody["OBJ-1"] == 1 and not a.pending_handoff("OBJ-1")
    assert ("ownership", {"obj": "OBJ-1", "cluster": 1, "height": 13, "via": "XC_TIMEOUT"}) in ev
    v.pending_sends[(2, 1)]["status"] = "ACKED"
    commit(a, v, 20, [ack_tx(recv_tx(send))])
    assert a.custody["OBJ-1"] == 2 and ev[-1][1]["via"] == "XC_ACK"


def test_skipped_protocol_txs_do_not_change_custody():
    a, v = app1(), FakeView(C1)
    send = send_tx()
    v.pending_sends[(2, 1)] = {"status": "SENT", "height": 4, "sendTxHash": wire.tx_hash(send)}
    commit(a, v, 4, [send])
    commit(a, v, 5, [ack_tx(recv_tx(send))])     # ledger did not mark it ACKED (skipped): custody must not move
    assert a.custody["OBJ-1"] == 1
    stale = send_tx(seq=7)                        # skipped XC_SEND: not in pending_sends -> not remembered
    commit(a, v, 6, [stale])
    assert (2, 7) not in a.sent


# ---------------------------------------------------------------- handoff_ready ---
def test_handoff_ready_truth_table():
    a, v = app1(), FakeView(C1)
    assert not a.handoff_ready("OBJ-1"), "no track"
    near_line(a, v, x=8900)
    assert not a.handoff_ready("OBJ-1"), "x=8900 is before the 90 m line"
    commit(a, v, 4, [obs(C1[0], 9000), obs(C1[1], 9000), obs(C1[2], 9000)])
    assert not a.handoff_ready("OBJ-1"), "exactly on the line is not beyond it"
    commit(a, v, 5, [obs(C1[0], 9001), obs(C1[1], 9001), obs(C1[2], 9001)])
    assert a.handoff_ready("OBJ-1")
    plan = a.handoff_plan("OBJ-1")
    assert plan["dst"] == 2 and plan["obj"] == "OBJ-1" and plan["track"]["x_cm"] == 9001 and plan["track"]["conf_pct"] == 90
    v.contingency[2] = {"sinceHeight": 5, "reason": "peer_silent"}
    assert not a.handoff_ready("OBJ-1"), "no handoff while in contingency with dst"
    v.contingency.clear()
    assert a.handoff_ready("OBJ-1")
    send = send_tx(x=9001)
    v.pending_sends[(2, 1)] = {"status": "SENT", "height": 6, "sendTxHash": wire.tx_hash(send)}
    commit(a, v, 6, [send])
    assert not a.handoff_ready("OBJ-1"), "pending handoff blocks a second one"
    v.pending_sends[(2, 1)]["status"] = "TIMED_OUT"
    assert a.handoff_ready("OBJ-1"), "a timed-out handoff may be retried"
    a.custody["OBJ-1"] = 2
    assert not a.handoff_ready("OBJ-1"), "custody moved away"


def test_handoff_uses_last_track_when_the_object_left_the_sensors():
    a, v = app1(), FakeView(C1)
    commit(a, v, 3, [obs(C1[0], 9500), obs(C1[1], 9500)])
    for h in range(4, 12):
        commit(a, v, h)
    assert a.accepted_track("OBJ-1") is None and a.handoff_ready("OBJ-1"), "deferred handoff uses lastTrack"
    assert a.handoff_plan("OBJ-1")["track"]["x_cm"] == 9500


def test_cluster_2_never_hands_off_at_the_end_of_the_world():
    a, v = MissionApp(2, M), FakeView(C2)
    a.custody["OBJ-1"] = 2
    commit(a, v, 3, [obs(C2[0], 19500), obs(C2[1], 19500)])
    assert a.accepted_track("OBJ-1")["x_cm"] == 19500 and a.destination() is None and not a.handoff_ready("OBJ-1")
    assert app1().destination() == 2


# ----------------------------------------------------------------- member score ---
def test_false_observer_is_flagged_by_ewma_deviation():
    ev = []
    a, v = app1(ev), FakeView(C1)
    liar = C1[3]
    for h in range(1, 7):
        commit(a, v, h, [obs(C1[0], 5000), obs(C1[1], 5000), obs(C1[2], 5000), obs(liar, 8000)])
    assert a.accepted_track("OBJ-1")["x_cm"] == 5000, "median bounds the liar's influence"
    sc = [d for e, d in ev if e == "member_score" and d["member"] == liar]
    assert sc and sc[-1]["flagged"] is True and sc[-1]["deviation_cm"] > 1500
    assert not any(d["flagged"] for e, d in ev if e == "member_score" and d["member"] == C1[0])
    assert a.scores[liar]["ewma"] <= 3000 and isinstance(a.scores[liar]["ewma"], int)


# ------------------------------------------------------------------ determinism ---
def replay(txs_by_height, events=None):
    a, v = app1(events), FakeView(C1)
    for h, txs in txs_by_height:
        commit(a, v, h, copy.deepcopy(txs))
    return a


def test_same_committed_txs_give_identical_snapshot_and_state():
    seq = [(1, [obs(C1[0], 7000), obs(C1[1], 7100), obs(C1[3], 9900)]), (2, [obs(C1[2], 7300)]), (3, []),
           (4, [obs(C1[0], 9400), obs(C1[1], 9500), obs(C1[2], 9450)]), (5, [])]
    e1, e2 = [], []
    a, b = replay(seq, e1), replay(seq, e2)
    assert json.dumps(a.snapshot(), sort_keys=True) == json.dumps(b.snapshot(), sort_keys=True)
    assert a.debug_state() == b.debug_state() and e1 == e2
    json.dumps(a.snapshot())
    assert replay(seq[:3]).debug_state() != a.debug_state()


def test_deliver_tx_never_raises_on_garbage():
    a, v = app1(), FakeView(C1)
    for junk in ({}, {"type": None}, {"type": "OBS"}, {"type": "XC_RECV"}, {"type": "XC_ACK", "dst": 2, "xcSeq": 1},
                 {"type": "XC_SEND", "dst": 2}, {"type": "XC_TIMEOUT"}, {"type": "ZONE", "zone": 3}, "not a dict", None):
        a.deliver_tx(junk, v, 1)          # must not raise
        assert a.check_tx(junk, v)[0] in (True, False)
    a.end_block(1, v)
    assert a.snapshot()["height"] == 1


@pytest.mark.parametrize("emit_raises", [True])
def test_emit_failures_do_not_touch_state(emit_raises):
    def boom(e, d):
        raise RuntimeError("sink down")
    a, v = MissionApp(1, M, emit=boom), FakeView(C1)
    commit(a, v, 1, [obs(C1[0], 9500), obs(C1[1], 9500)])
    assert a.accepted_track("OBJ-1")["x_cm"] == 9500
