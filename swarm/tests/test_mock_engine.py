"""MockEngine behaviour on the deterministic in-memory mesh (no sockets)."""
from __future__ import annotations

import copy
import inspect

from swarm import wire
from swarm.mock_engine import MockEngine
from swarm.tests.fixtures import dev_mission
from swarm.tests.simnet import Sim

M = dev_mission()
XC_SEND = {"type": "XC_SEND", "dst": 2, "timeoutMs": 8000,
           "payload": {"kind": "HANDOFF", "obj": "OBJ-1",
                       "track": {"x_cm": 9100, "y_cm": 5000, "vx_cmps": 0, "vy_cmps": 0, "t_ms": 1, "conf_pct": 90}}}


def a(name: str) -> str:
    return M.member_by_name(name)["address"]


def heights(sim: Sim, cluster: int) -> dict[str, int]:
    """Full validators only: the light board keeps headers, not blocks."""
    return {n: sim.height(n) for n, e in sim.engines.items() if M.cluster_of_name(n) == cluster and not e.light}


def test_constructor_signature_matches_spec_section_13():
    params = list(inspect.signature(MockEngine.__init__).parameters)[1:]
    assert params[:10] == ["identity", "mission", "cluster_id", "transport", "clock", "app", "on_commit", "on_event",
                           "fault", "light"]
    for m in ("start", "tick", "on_envelope", "submit_tx", "state", "set_fault", "export_certified",
              "verify_foreign_packet"):
        assert callable(getattr(MockEngine, m))


def test_both_clusters_commit_about_once_per_second_and_agree():
    sim = Sim(M)
    sim.run(10_000)
    for cl in (1, 2):
        hs = heights(sim, cl)
        assert min(hs.values()) >= 6 and max(hs.values()) - min(hs.values()) <= 1, hs
        upto = min(hs.values())
        hashes = {tuple(b["blockHash"] for b in sim.engines[n].blocks[:upto]) for n in hs}
        assert len(hashes) == 1, "all nodes of a cluster must hold identical chains"
        blk = sim.engines[next(iter(hs))].blocks[3]
        assert blk["header"]["prevHash"] == sim.engines[next(iter(hs))].blocks[2]["blockHash"]
        assert wire.block_hash(blk["header"]) == blk["blockHash"]
    first = sim.engines["c1-d1"].blocks[0]["header"]["prevHash"]
    assert first == wire.genesis_hash(M.mission_id, 1, M.roster_hash)
    commits = [e for e in sim.ev("commit") if e[1] == "c1-d2"]
    assert all(e[4]["activeWeight"] == 13 and e[4]["signedWeight"] >= 9 for e in commits)


def test_every_engine_event_is_tagged_mock():
    sim = Sim(M)
    sim.run(4000)
    assert sim.events and all(e[4].get("mock") is True for e in sim.events)


def test_submitted_txs_are_committed_everywhere_and_reach_the_app():
    sim = Sim(M)
    sim.run(2000)
    h = sim.engines["c1-d2"].submit_tx({"type": "OBS", "obj": "OBJ-1", "x_cm": 3000, "y_cm": 5000, "conf_pct": 90})
    sim.run(4000)
    for n in ("c1-d1", "c1-d2", "c1-d3", "c1-d4"):
        hashes = [wire.tx_hash(i["tx"]) for b in sim.engines[n].blocks for i in b["txs"]]
        assert h in hashes
        assert any(t == "OBS" for t, _ in sim.apps[n].delivered), "deliver_tx sees every committed tx"
    assert h not in sim.engines["c1-d2"].mempool and sim.engines["c2-d1"].blocks
    tx = next(i["tx"] for b in sim.engines["c1-d1"].blocks for i in b["txs"] if wire.tx_hash(i["tx"]) == h)
    assert tx["from"] == a("c1-d2") and tx["mission"] == M.mission_id and tx["cluster"] == 1 and tx["seq"] >= 1


def test_master_kill_triggers_role_and_chain_continues():
    sim = Sim(M)
    sim.run(4000)
    prev = sim.engines["c1-d2"].ledger.master
    assert prev == a("c1-d1")
    t_kill = sim.clock.now_ms()
    sim.kill("c1-d1")
    h_at_kill = sim.height("c1-d2")
    sim.run(12_000)
    changed = [e for e in sim.ev("master_changed") if e[2] == 1]
    assert changed, "no master_changed"
    t_changed, data = changed[0][0], changed[0][4]
    assert data["prev"] == prev and data["master"] == sim.engines["c1-d2"].ledger.successor(prev) and data["master"] != prev
    assert 2500 <= t_changed - t_kill <= 7000, t_changed - t_kill
    assert sim.height("c1-d2") >= h_at_kill + 4, "commits must continue with the master dead"
    assert {sim.engines[n].ledger.master for n in ("c1-d2", "c1-d3", "c1-d4")} == {data["master"]}
    assert sim.engines["c2-d1"].ledger.master == a("c2-d1")


def test_losing_quorum_halts_safely_and_other_cluster_keeps_going():
    sim = Sim(M)
    sim.run(4000)
    sim.kill("c1-d2")
    sim.kill("c1-d3")           # alive weight 3+3+1 = 7 of 13, needs 9
    sim.run(500)
    h0, h2 = sim.height("c1-d1"), sim.height("c2-d1")
    sim.run(10_000)
    assert sim.height("c1-d1") == h0 and sim.height("c1-d4") <= h0 + 1
    assert sim.height("c2-d1") >= h2 + 6


def test_board_and_master_down_still_reaches_quorum_9_of_13():
    sim = Sim(M)
    sim.run(3000)
    sim.kill("c1-board")
    sim.kill("c1-d1")
    h0 = sim.height("c1-d2")
    sim.run(14_000)
    assert sim.height("c1-d2") >= h0 + 6
    last = sim.ev("commit", height=sim.height("c1-d2"))
    assert any(e[4]["signedWeight"] == 9 and e[4]["activeWeight"] == 13 for e in last if e[1] == "c1-d2")


def test_light_validator_follows_the_chain_and_appears_in_qcs():
    sim = Sim(M)
    sim.run(14_000)
    board = sim.engines["c1-board"]
    assert board.light and board.ledger.height >= sim.height("c1-d1") - 1 and board.ledger.height >= 8
    assert board.ledger.last_hash == sim.engines["c1-d1"].blocks[board.ledger.height - 1]["blockHash"]
    with_board = [e for e in sim.ev("commit") if e[1] == "c1-d1" and a("c1-board") in e[4]["qcSigners"]]
    assert with_board, "board should sign in at least some heights"
    assert sim.ev("vote", voter=a("c1-board"))


def test_equivocation_becomes_evidence_then_revocation_within_three_heights():
    sim = Sim(M)
    sim.run(3000)
    bad = a("c1-d3")
    sim.engines["c1-d3"].set_fault("equivocate")
    sim.run(9000)
    ev = [e for e in sim.ev("evidence", offender=bad)]
    rev = [e for e in sim.ev("revoked", member=bad)]
    assert ev and rev
    assert ev[0][4]["kind"] == "EQUIVOCATION" and ev[0][4]["msgType"] == 2
    assert rev[0][4]["reason"] == "EQUIVOCATION" and rev[0][4]["effectiveHeight"] == rev[0][4]["atHeight"] + 1
    assert rev[0][4]["atHeight"] - ev[0][4]["height"] <= 3
    for n in ("c1-d1", "c1-d2", "c1-d4", "c1-d3"):
        led = sim.engines[n].ledger
        assert bad in led.revoked and bad not in led.committee and led.active_weight == 10
    assert sim.height("c1-d1") >= 9
    exp = sim.engines["c1-d1"].export_certified()
    assert exp["revocations"][0]["member"] == bad and exp["evidence"][0]["kind"] == "EQUIVOCATION"
    assert len(sim.ev("revoked", member=bad)) >= 4 and sim.engines["c1-d1"].state()["revoked"][bad]["reason"] == "EQUIVOCATION"


def test_partition_enters_contingency_in_both_clusters_and_exits_after_heal():
    sim = Sim(M)
    sim.run(4000)
    sim.net.partition([["cluster:1"], ["cluster:2"]])
    h1, h2 = sim.height("c1-d1"), sim.height("c2-d1")
    sim.run(14_000)
    enter = [e for e in sim.ev("contingency", mode="ENTER")]
    assert {e[2] for e in enter} == {1, 2}
    assert all(e[4]["peer"] in (1, 2) and e[4]["peer"] != e[2] for e in enter)
    assert sim.height("c1-d1") >= h1 + 8 and sim.height("c2-d1") >= h2 + 8, "local blocks continue in contingency"
    assert sim.engines["c1-d1"].state()["contingency"]["2"]["reason"] == "peer_silent"
    sim.net.heal()
    sim.run(9000)
    assert {e[2] for e in sim.ev("contingency", mode="EXIT")} == {1, 2}
    assert sim.engines["c1-d1"].state()["contingency"] == {} and sim.engines["c2-d3"].state()["contingency"] == {}
    assert sim.ev("master_changed") == [], "a partition between clusters must not change masters"


def test_certified_handoff_send_recv_ack_moves_custody():
    sim = Sim(M)
    sim.run(3000)
    sim.engines["c1-d1"].submit_tx(dict(XC_SEND))
    sim.run(9000)
    sent = sim.ev("xc_sent")
    assert sent and sent[0][4]["dst"] == 2 and sent[0][4]["seq"] == 1 and sent[0][4]["obj"] == "OBJ-1"
    ver = [e for e in sim.ev("xc_verified") if e[2] == 2]
    assert ver and ver[0][4]["src"] == 1 and ver[0][4]["signedWeight"] >= 9 and ver[0][4]["activeWeight"] == 13
    assert ver[0][4]["signers"] == sorted(ver[0][4]["signers"])
    acked = sim.ev("xc_acked")
    assert acked and acked[0][4]["dst"] == 2 and acked[0][4]["seq"] == 1
    assert sent[0][0] <= ver[0][0] <= acked[0][0]
    for n in ("c2-d1", "c2-d2", "c2-d5"):
        assert sim.apps[n].custody["OBJ-1"] == 2 and (1, 1) in sim.engines[n].ledger.received
    for n in ("c1-d1", "c1-d2", "c1-d4"):
        assert sim.apps[n].custody["OBJ-1"] == 2
        assert sim.engines[n].ledger.pending_sends[(2, 1)]["status"] == "ACKED"
    assert sim.engines["c1-d1"].ledger.next_send_seq[2] == 2
    assert not sim.ev("xc_rejected"), "benign re-relays must not look like rejections"
    assert not [e for e in sim.ev("xc_timeout")]


def test_xc_send_without_custody_is_never_committed():
    sim = Sim(M)
    sim.run(2000)
    tx = copy.deepcopy(XC_SEND)
    tx["payload"]["obj"] = "OBJ-9"
    sim.engines["c1-d1"].submit_tx(tx)
    sim.run(4000)
    assert not sim.ev("xc_sent")
    assert not sim.engines["c1-d1"].mempool or all(i["tx"]["type"] != "XC_SEND" for i in sim.engines["c1-d1"].mempool.values())


def test_xc_timeout_when_cluster_2_is_unreachable_keeps_custody():
    sim = Sim(M)
    sim.run(2000)
    sim.net.partition([["cluster:1"], ["cluster:2"]])
    sim.engines["c1-d1"].submit_tx(dict(XC_SEND))
    sim.run(4000)
    assert sim.ev("xc_sent")
    sim.run(8000)
    to = sim.ev("xc_timeout")
    assert to and to[0][4] == {"dst": 2, "seq": 1, "mock": True}
    assert sim.engines["c1-d2"].ledger.pending_sends[(2, 1)]["status"] == "TIMED_OUT"
    assert sim.apps["c1-d2"].custody["OBJ-1"] == 1, "TIMEOUT keeps custody at the source"
    assert not sim.ev("xc_verified")


def test_revived_node_catches_up_by_sync():
    sim = Sim(M)
    sim.run(2000)
    sim.kill("c1-d4")
    sim.run(9000)
    lag = sim.height("c1-d4")
    assert sim.height("c1-d1") >= lag + 5
    sim.revive("c1-d4")
    sim.run(6000)
    assert sim.height("c1-d1") - sim.height("c1-d4") <= 1
    n = sim.height("c1-d4")
    assert sim.engines["c1-d4"].blocks[n - 1]["blockHash"] == sim.engines["c1-d1"].blocks[n - 1]["blockHash"]


def _partitioned_packet():
    sim = Sim(M)
    sim.run(2000)
    sim.net.partition([["cluster:1"], ["cluster:2"]])
    sim.engines["c1-d1"].submit_tx(dict(XC_SEND))
    sim.run(2500)
    eng = sim.engines["c1-d1"]
    blk = next(b for b in eng.blocks if any(i["tx"]["type"] == "XC_SEND" for i in b["txs"]))
    item = next(i for i in blk["txs"] if i["tx"]["type"] == "XC_SEND")
    packet = eng._packet_for("PACKET", blk["header"], blk["qc"], blk["txs"], item, 2, 1)
    return sim, packet


def test_verify_foreign_packet_rejection_reasons():
    sim, packet = _partitioned_packet()
    c2 = sim.engines["c2-d2"]
    assert c2.verify_foreign_packet(packet) == (True, "")

    def bad(mut) -> str:
        p = copy.deepcopy(packet)
        mut(p)
        ok, reason = c2.verify_foreign_packet(p)
        assert not ok
        return reason

    assert bad(lambda p: p.update(dst=1)) == "wrong_dst"
    assert bad(lambda p: p["header"].update(mission="0x" + "11" * 32)) == "wrong_mission"
    assert bad(lambda p: p["header"].update(txRoot="0x" + "22" * 32)) == "bad_txroot"

    def swap_tx(p):
        p["tx"]["payload"]["obj"] = "OBJ-2"
    assert bad(swap_tx) == "not_included"

    def wrong_type(p):
        p["tx"] = {**p["tx"], "type": "OBS"}
        p["txHashes"] = [wire.tx_hash(p["tx"])]
        p["header"]["txRoot"] = wire.tx_root(p["txHashes"])
    assert bad(wrong_type) == "bad_tx"
    assert bad(lambda p: p["qc"]["sigs"].reverse()) == "unsorted"
    assert bad(lambda p: p["qc"].update(sigs=p["qc"]["sigs"][:2])) == "no_quorum"

    def foreign_signer(p):
        p["qc"]["sigs"][-1]["signer"] = "0x" + "ff" * 20
    assert bad(foreign_signer) == "not_member"
    assert bad(lambda p: p.pop("qc")) == "bad_tx"
    # replay: once the packet has been received, the same packet is refused
    c2.ledger.received.add((1, 1))
    assert c2.verify_foreign_packet(packet) == (False, "replay")


def test_state_and_export_shapes_follow_section_13():
    sim = Sim(M)
    sim.run(5000)
    st = sim.engines["c1-d2"].state()
    for k in ("name", "address", "cluster", "height", "round", "step", "lastCommit", "master", "committee", "revoked",
              "contingency", "xc", "mempool", "fault", "app"):
        assert k in st, k
    assert set(st["lastCommit"]) == {"height", "blockHash", "qcSigners"} and set(st["xc"]) == {"nextSendSeq", "pendingSends", "received"}
    assert st["name"] == "c1-d2" and st["cluster"] == 1 and st["master"] == a("c1-d1") and st["fault"] == "honest"
    ex = sim.engines["c1-d2"].export_certified()
    for k in ("mission", "cluster", "rosterHash", "genesisHash", "blocks", "evidence", "revocations"):
        assert k in ex, k
    assert ex["blocks"] and set(ex["blocks"][0]) == {"header", "blockHash", "txs", "qc"} and ex["mock"] is True
    import json
    json.dumps(st), json.dumps(ex)   # JSON-serialisable


def test_silent_fault_sends_nothing_and_cluster_still_commits():
    sim = Sim(M)
    sim.run(3000)
    sim.engines["c1-d3"].set_fault("silent")
    sent_before = sim.net.sent_by[a("c1-d3")]
    h0 = sim.height("c1-d1")
    sim.run(6000)
    assert sim.height("c1-d1") >= h0 + 4, "3 of 4 full validators + board = 10 of 13 still commits"
    assert sim.net.sent_by[a("c1-d3")] == sent_before, "a silent node sends nothing at all"
    assert sim.engines["c1-d3"].state()["fault"] == "silent"
    assert sim.height("c1-d3") >= sim.height("c1-d1") - 1, "but it still receives and follows the chain"
