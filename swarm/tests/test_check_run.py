"""check_run on synthetic events.jsonl files: it must PASS a good run and FAIL each broken act for the right reason."""
from __future__ import annotations

import json
from pathlib import Path

from swarm.tools.check_run import load_events, main, run_checks

KILLED = "0x" + "d1" * 20
NEW = "0x" + "d2" * 20
BAD = "0x" + "d3" * 20


class RunBuilder:
    def __init__(self, mock=True):
        self.lines: list[dict] = []
        self.mock = mock

    def add(self, rx, event, data, cluster=None, name="n1", dup_of_first=False):
        d = dict(data)
        if self.mock:
            d["mock"] = True
        self.lines.append({"v": 1, "src": "0x1", "name": name, "cluster": cluster, "ts": 999_999_999 - rx, "event": event,
                           "data": d, "rx": rx, "dup": False})
        return self

    def write(self, tmp: Path) -> Path:
        run = tmp / "run"
        run.mkdir(exist_ok=True)
        (run / "events.jsonl").write_text("\n".join(json.dumps(x) for x in self.lines) + "\n", encoding="utf-8")
        return run


def good_run(tmp_path, tweak=None, mock=True):
    b = RunBuilder(mock)
    t = 1000
    for h in range(1, 60):
        for c in (1, 2):
            b.add(t + h * 1000 + c, "commit", {"height": h, "round": 0, "latencyMs": 20}, cluster=c)
    # act 1
    b.add(10_000, "xc_sent", {"dst": 2, "seq": 1, "height": 9, "obj": "OBJ-1", "sendTxHash": "0x1"}, cluster=1)
    b.add(10_010, "xc_sent", {"dst": 2, "seq": 1, "height": 9, "obj": "OBJ-1", "sendTxHash": "0x1"}, cluster=1, name="n2")   # per-node duplicate
    b.add(10_300, "xc_verified", {"src": 1, "seq": 1, "signers": ["a"], "signedWeight": 12, "activeWeight": 13, "relayer": "r"}, cluster=2)
    b.add(11_200, "ownership", {"obj": "OBJ-1", "cluster": 2, "height": 10, "via": "XC_RECV"}, cluster=2)
    b.add(12_100, "xc_acked", {"dst": 2, "seq": 1, "height": 11}, cluster=1)
    # act 2
    b.add(20_000, "fault_injected", {"node": KILLED, "mode": "kill", "name": "c1-d1"})
    b.add(23_500, "master_changed", {"prev": KILLED, "master": NEW, "height": 22, "reason": "silent"}, cluster=1)
    # act 3
    b.add(30_000, "evidence", {"offender": BAD, "kind": "EQUIVOCATION", "height": 30, "round": 0, "msgType": 2}, cluster=1)
    b.add(31_000, "revoked", {"member": BAD, "reason": "EQUIVOCATION", "atHeight": 31, "effectiveHeight": 32}, cluster=1)
    # act 4
    b.add(40_000, "fault_injected", {"node": "*", "mode": "partition"})
    b.add(45_100, "contingency", {"mode": "ENTER", "peer": 2, "reason": "peer_silent", "height": 44}, cluster=1)
    b.add(45_300, "contingency", {"mode": "ENTER", "peer": 1, "reason": "peer_silent", "height": 44}, cluster=2)
    b.add(50_000, "fault_injected", {"node": "*", "mode": "heal"})
    b.add(51_000, "contingency", {"mode": "EXIT", "peer": 2, "reason": "peer_heard", "height": 50}, cluster=1)
    b.add(51_100, "contingency", {"mode": "EXIT", "peer": 1, "reason": "peer_heard", "height": 50}, cluster=2)
    b.add(52_500, "xc_sent", {"dst": 2, "seq": 2, "height": 51, "obj": "OBJ-2", "sendTxHash": "0x2"}, cluster=1)
    b.add(53_400, "ownership", {"obj": "OBJ-2", "cluster": 2, "height": 52, "via": "XC_RECV"}, cluster=2)
    if tweak:
        tweak(b)
    return b.write(tmp_path)


def statuses(run):
    res, _, _ = run_checks(run)
    return {r["act"]: r["status"] for r in res}, {r["act"]: r for r in res}


def test_good_run_passes_all_four_acts_and_measures_latencies(tmp_path):
    st, full = statuses(good_run(tmp_path))
    assert st == {1: "PASS", 2: "PASS", 3: "PASS", 4: "PASS"}
    assert full[1]["measured"]["handoff_send_to_recv_ms"] == 1200 and full[1]["measured"]["handoff_send_to_ack_ms"] == 2100
    assert full[2]["measured"]["failover_ms"] == 3500 and full[3]["measured"]["heights"] == 1
    assert full[4]["measured"]["cut_to_enter_ms"] == 5100 and full[4]["measured"]["heal_to_exit_ms"] == 1000


def test_events_are_deduped_first_arrival_wins(tmp_path):
    events, raw = load_events(good_run(tmp_path))
    assert raw - len(events) == 1 and sum(1 for e in events if e["event"] == "xc_sent" and e["data"]["obj"] == "OBJ-1") == 1


def test_cli_exit_code_and_output_is_ascii(tmp_path, capsys):
    run = good_run(tmp_path)
    assert main([str(run)]) == 0
    out = capsys.readouterr().out
    assert out.isascii() and "PASS (mock)" in out and "Needs --engine real" in out and "MOCK RUN" in out
    assert main([str(run), "--acts", "1,2", "--json"]) == 0


def test_act1_fails_without_ack(tmp_path):
    def drop_ack(b):
        b.lines = [x for x in b.lines if x["event"] != "xc_acked"]
    st, full = statuses(good_run(tmp_path, drop_ack))
    assert st[1] == "FAIL" and any(c[0].startswith("xc_acked") and not c[1] for c in full[1]["checks"])
    assert main([str(tmp_path / "run")]) == 1


def test_act1_fails_when_receive_precedes_send(tmp_path):
    def reorder(b):
        for x in b.lines:
            if x["event"] == "xc_verified":
                x["rx"] = 9_000
    st, _ = statuses(good_run(tmp_path, reorder))
    assert st[1] == "FAIL"


def test_act2_fails_when_failover_is_slower_than_6s(tmp_path):
    def slow(b):
        for x in b.lines:
            if x["event"] == "master_changed":
                x["rx"] = 20_000 + 7_000
    st, full = statuses(good_run(tmp_path, slow))
    assert st[2] == "FAIL" and any("within 6 s" in c[0] and not c[1] for c in full[2]["checks"])


def test_act2_fails_when_the_killed_node_was_not_replaced(tmp_path):
    def none(b):
        b.lines = [x for x in b.lines if x["event"] != "master_changed"]
    st, _ = statuses(good_run(tmp_path, none))
    assert st[2] == "FAIL"


def test_act3_fails_when_revocation_takes_more_than_3_heights(tmp_path):
    def late(b):
        for x in b.lines:
            if x["event"] == "revoked":
                x["data"]["atHeight"] = 35
    st, full = statuses(good_run(tmp_path, late))
    assert st[3] == "FAIL" and any("3 heights" in c[0] and not c[1] for c in full[3]["checks"])


def test_act4_fails_if_handoff_is_not_deferred(tmp_path):
    def early(b):
        b.add(42_000, "xc_sent", {"dst": 2, "seq": 9, "height": 40, "obj": "OBJ-2", "sendTxHash": "0x9"}, cluster=1)
    st, full = statuses(good_run(tmp_path, early))
    assert st[4] == "FAIL" and any("deferred" in c[0] and not c[1] for c in full[4]["checks"])


def test_act4_needs_both_clusters_to_enter_and_exit(tmp_path):
    def one(b):
        b.lines = [x for x in b.lines if not (x["event"] == "contingency" and x["cluster"] == 2)]
    st, _ = statuses(good_run(tmp_path, one))
    assert st[4] == "FAIL"


def test_missing_acts_are_skipped_not_failed(tmp_path):
    def only_commits(b):
        b.lines = [x for x in b.lines if x["event"] == "commit"]
    st, _ = statuses(good_run(tmp_path, only_commits))
    assert set(st.values()) == {"SKIP"}
    assert main([str(tmp_path / "run")]) == 0


def test_non_mock_run_is_not_labelled_mock(tmp_path, capsys):
    run = good_run(tmp_path, mock=False)
    main([str(run)])
    out = capsys.readouterr().out
    assert "PASS (mock)" not in out and "MOCK RUN" not in out and "engine: real/unknown" in out


def test_benign_replay_rejection_is_not_a_failure_but_other_reasons_are(tmp_path):
    def replay(b):
        b.add(12_500, "xc_rejected", {"src": 2, "seq": 1, "reason": "replay", "relayer": "0x1"}, cluster=1)
    st, full = statuses(good_run(tmp_path, replay))
    assert st[1] == "PASS" and any("benign replay" in c[2] for c in full[1]["checks"])

    def real_reject(b):
        b.add(12_500, "xc_rejected", {"src": 2, "seq": 1, "reason": "no_quorum", "relayer": "0x1"}, cluster=1)
    st, _ = statuses(good_run(tmp_path, real_reject))
    assert st[1] == "FAIL"


def _scenario_start(b, rx):
    b.add(rx, "scenario", {"name": "demo", "state": "step", "t": 2, "do": "start_object", "args": {"obj": "OBJ-1"}, "ok": True})


def test_act1_fails_when_the_handoff_is_much_later_than_the_object_start(tmp_path):
    def late(b):
        _scenario_start(b, 100)
        for x in b.lines:                                 # the whole handoff happens ~60 s after the start: deferred
            if x["event"] in ("xc_sent", "xc_verified", "xc_acked") or (x["event"] == "ownership" and x["data"].get("obj") == "OBJ-1"):
                x["rx"] += 50_000
    st, full = statuses(good_run(tmp_path, late))
    assert st[1] == "FAIL" and any("not deferred" in c[0] and not c[1] for c in full[1]["checks"])

    def prompt(b):
        _scenario_start(b, 1_000)
    st, full = statuses(good_run(tmp_path, prompt))
    assert st[1] == "PASS" and full[1]["measured"]["start_to_xc_sent_ms"] == 9_000


def test_act4_fails_when_the_deferred_handoff_takes_too_long_after_heal(tmp_path):
    def slow(b):
        for x in b.lines:
            if x["event"] in ("xc_sent", "ownership") and (x["data"].get("obj") == "OBJ-2"):
                x["rx"] += 40_000
    st, full = statuses(good_run(tmp_path, slow))
    assert st[4] == "FAIL" and any("within 30 s of heal" in c[0] and not c[1] for c in full[4]["checks"])
