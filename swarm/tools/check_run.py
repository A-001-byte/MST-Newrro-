"""Run checker: verifies the demo acts from runs/<runId>/events.jsonl (00_CONTEXT 17, docs/02 B5).

  ./.venv/Scripts/python -m swarm.tools.check_run runs/<runId>

* Events are re-deduped with the 11.7 rule (first arrival wins) and ordered by the OBSERVER receive clock (`rx`).
  No wall clock of any drone is ever compared with another.
* Prints PASS / FAIL / SKIP per act, the measured latencies and which checks need --engine real.
* A run whose events carry data.mock = true can only prove the event SEQUENCE, never signatures or QCs.
Exit code: 0 when no act FAILs, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from swarm.observer import dedupe_key

FAILOVER_LIMIT_MS = 6000
REVOKE_LIMIT_HEIGHTS = 3
HANDOFF_LIMIT_MS = 40_000       # OBJ-1 reaches the 90 m line 14 s after start; the rest is fusion window + commit latency
DEFERRED_LIMIT_MS = 30_000      # after heal: contingency EXIT + fresh XC_SEND

REAL_ONLY = {
    1: ["QC signatures inside xc_verified (mock skips signature checks)", "certificate binds the SEND tx to a real quorum"],
    2: ["ROLE tx signed by a real full validator; engine-side silence timer"],
    3: ["evidence signatures verify with ecrecover; on-chain submitEquivocation (chain client)"],
    4: ["signed HISTORY merge after heal (history_merged), fresh certified packets"],
}


def load_events(run_dir: Path) -> tuple[list[dict], int]:
    """Deduped (first arrival) events in observer-receive order, and the raw count."""
    path = run_dir / "events.jsonl"
    if not path.exists():
        raise SystemExit(f"no events.jsonl in {run_dir}")
    raw = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                raw.append(json.loads(line))
            except ValueError:
                continue
    raw.sort(key=lambda e: e.get("rx", 0))
    seen, out = set(), []
    for e in raw:
        k = dedupe_key(e)
        if k is not None:
            if k in seen:
                continue
            seen.add(k)
        out.append(e)
    return out, len(raw)


class Run:
    def __init__(self, events: list[dict]):
        self.events = events
        self.mock = any((e.get("data") or {}).get("mock") for e in events)

    def find(self, event: str, after: int = -1, before: int | None = None, **match) -> list[dict]:
        out = []
        for e in self.events:
            if e.get("event") != event or e.get("rx", 0) < after:
                continue
            if before is not None and e.get("rx", 0) > before:
                continue
            d = e.get("data") or {}
            if all((d.get(k) if k != "cluster" else e.get("cluster")) == v for k, v in match.items()):
                out.append(e)
        return out

    def first(self, event: str, after: int = -1, before: int | None = None, **match):
        r = self.find(event, after, before, **match)
        return r[0] if r else None


def result(act: int, title: str, checks: list[tuple[str, bool, str]], measured: dict | None = None, skip: str | None = None) -> dict:
    if skip:
        return {"act": act, "title": title, "status": "SKIP", "checks": [], "measured": {}, "note": skip}
    ok = all(c[1] for c in checks)
    return {"act": act, "title": title, "status": "PASS" if ok else "FAIL", "checks": checks, "measured": measured or {}}


def act1(run: Run) -> dict:
    title = "certified handoff of OBJ-1"
    sent = run.first("xc_sent", obj="OBJ-1")
    if sent is None:
        if not run.find("xc_sent"):
            return result(1, title, [], skip="no xc_sent in this run (scenario without OBJ-1 handoff)")
        return result(1, title, [("xc_sent for OBJ-1", False, "xc_sent exists but not for OBJ-1")])
    d = sent["data"]
    src, dst, seq, t0 = sent["cluster"], d["dst"], d["seq"], sent["rx"]
    ver = run.first("xc_verified", after=t0, cluster=dst, src=src, seq=seq)
    own = run.first("ownership", after=t0, obj="OBJ-1", cluster=dst, via="XC_RECV")
    ack = run.first("xc_acked", after=t0, cluster=src, dst=dst, seq=seq)
    checks = [("xc_sent OBJ-1 (C%s -> C%s #%s)" % (src, dst, seq), True, ""),
              ("xc_verified by the destination cluster after xc_sent", ver is not None, ""),
              ("ownership(OBJ-1 -> C%s) via XC_RECV" % dst, own is not None, ""),
              ("xc_acked back at the source", ack is not None, "")]
    order = ver is not None and own is not None and ack is not None and sent["rx"] <= ver["rx"] <= ack["rx"] and own["rx"] <= ack["rx"] + 1000
    checks.append(("order xc_sent <= xc_verified <= ownership <= xc_acked (observer clock)", order, ""))
    rej = [e for e in run.find("xc_rejected", after=t0, seq=seq) if e["data"].get("reason") != "replay"]
    replays = len(run.find("xc_rejected", after=t0, seq=seq, reason="replay"))
    checks.append(("no xc_rejected (other than benign replay re-relays) for this handoff", not rej,
                   ", ".join(sorted({e["data"].get("reason", "?") for e in rej})) or ("%d benign replay" % replays if replays else "")))
    starts = [e for e in run.find("scenario", state="step", do="start_object") if (e["data"].get("args") or {}).get("obj") == "OBJ-1"]
    m = {}
    if starts:
        lag = t0 - starts[0]["rx"]
        m["start_to_xc_sent_ms"] = lag
        checks.append(("handoff not deferred: xc_sent within %d s of OBJ-1 start" % (HANDOFF_LIMIT_MS // 1000),
                       0 <= lag <= HANDOFF_LIMIT_MS, "%d ms" % lag))
    if own:
        m["handoff_send_to_recv_ms"] = own["rx"] - t0
    if ack:
        m["handoff_send_to_ack_ms"] = ack["rx"] - t0
    if ver and (ver["data"].get("activeWeight")):
        m["certificate_weight"] = "%s/%s" % (ver["data"].get("signedWeight"), ver["data"].get("activeWeight"))
    return result(1, title, checks, m)


def act2(run: Run) -> dict:
    title = "master killed, successor takes over"
    kills = run.find("fault_injected", mode="kill")
    if not kills:
        return result(2, title, [], skip="no kill fault in this run")
    best, fails = None, []
    for k in kills:
        node = k["data"].get("node")
        mc = run.first("master_changed", after=k["rx"], prev=node)
        if mc is not None:
            best = (k, mc)
            break
        fails.append(k["data"].get("name") or node)
    if best is None:
        return result(2, title, [("master_changed after a kill", False, "no master_changed for kill of %s (not a master, or no failover)" % ",".join(map(str, fails)))])
    k, mc = best
    ms = mc["rx"] - k["rx"]
    cl = mc["cluster"]
    cont = run.first("commit", after=mc["rx"] + 1, cluster=cl)
    checks = [("fault_injected{kill} of the master", True, k["data"].get("name") or ""),
              ("master_changed within %d s" % (FAILOVER_LIMIT_MS // 1000), ms <= FAILOVER_LIMIT_MS, "%d ms" % ms),
              ("commits continue in C%s after the change" % cl, cont is not None, "")]
    return result(2, title, checks, {"failover_ms": ms, "new_master": mc["data"].get("master")})


def act3(run: Run) -> dict:
    title = "equivocation -> evidence -> revoked"
    evs = run.find("evidence")
    if not evs:
        return result(3, title, [], skip="no evidence in this run")
    ev = evs[0]
    off, h = ev["data"]["offender"], ev["data"]["height"]
    rev = run.first("revoked", after=ev["rx"], member=off)
    checks = [("evidence(offender)", True, "kind %s" % ev["data"].get("kind")), ("revoked(offender) after evidence", rev is not None, "")]
    measured = {}
    if rev is not None:
        gap = rev["data"]["atHeight"] - h
        checks.append(("revoked within %d heights of the evidence" % REVOKE_LIMIT_HEIGHTS, 0 <= gap <= REVOKE_LIMIT_HEIGHTS, "%d heights" % gap))
        after = run.first("commit", after=rev["rx"] + 1, cluster=rev["cluster"])
        checks.append(("cluster keeps committing after revocation", after is not None, ""))
        measured = {"evidence_to_revoked_ms": rev["rx"] - ev["rx"], "heights": gap}
    return result(3, title, checks, measured)


def act4(run: Run) -> dict:
    title = "partition -> contingency -> heal -> deferred handoff"
    parts = run.find("fault_injected", mode="partition")
    if not parts:
        return result(4, title, [], skip="no partition fault in this run")
    p = parts[0]
    heal = run.first("fault_injected", after=p["rx"], mode="heal")
    checks = [("fault_injected{partition}", True, "")]
    if heal is None:
        checks.append(("fault_injected{heal} after the partition", False, "run ended while still partitioned"))
        return result(4, title, checks)
    enters = {e["cluster"] for e in run.find("contingency", after=p["rx"], before=heal["rx"], mode="ENTER")}
    checks.append(("contingency ENTER in both clusters while partitioned", enters >= {1, 2}, "clusters %s" % sorted(enters)))
    during = run.find("xc_sent", after=p["rx"], before=heal["rx"], obj="OBJ-2")
    checks.append(("no xc_sent for OBJ-2 while partitioned (deferred)", not during, ""))
    checks.append(("fault_injected{heal}", True, ""))
    exits = {e["cluster"] for e in run.find("contingency", after=heal["rx"], mode="EXIT")}
    checks.append(("contingency EXIT in both clusters after heal", exits >= {1, 2}, "clusters %s" % sorted(exits)))
    late = run.first("xc_sent", after=heal["rx"], obj="OBJ-2")
    own = run.first("ownership", after=heal["rx"], obj="OBJ-2", cluster=2, via="XC_RECV")
    checks.append(("xc_sent for OBJ-2 after heal", late is not None, ""))
    if late is not None:
        checks.append(("deferred handoff within %d s of heal" % (DEFERRED_LIMIT_MS // 1000),
                       late["rx"] - heal["rx"] <= DEFERRED_LIMIT_MS, "%d ms" % (late["rx"] - heal["rx"])))
    checks.append(("ownership(OBJ-2 -> C2) after heal", own is not None, ""))
    m = {}
    first_enter = run.first("contingency", after=p["rx"], mode="ENTER")
    if first_enter:
        m["cut_to_enter_ms"] = first_enter["rx"] - p["rx"]
    first_exit = run.first("contingency", after=heal["rx"], mode="EXIT")
    if first_exit:
        m["heal_to_exit_ms"] = first_exit["rx"] - heal["rx"]
    if late:
        m["heal_to_deferred_send_ms"] = late["rx"] - heal["rx"]
    return result(4, title, checks, m)


def chain_agreement(run_dir: Path) -> str | None:
    files = sorted(run_dir.glob("*.ledger.json"))
    if not files:
        return None
    per: dict[int, dict[str, list[str]]] = {}
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if d.get("blocks"):
            per.setdefault(d["cluster"], {})[f.stem.replace(".ledger", "")] = [b["blockHash"] for b in d["blocks"]]
    parts, ok = [], True
    for cl, nodes in sorted(per.items()):
        n = min(len(v) for v in nodes.values())
        agree = len({tuple(v[:n]) for v in nodes.values()}) == 1
        ok &= agree
        parts.append("C%s %d exports common prefix %d blocks %s" % (cl, len(nodes), n, "IDENTICAL" if agree else "DIFFER"))
    return ("OK  " if ok else "FAIL") + " " + "; ".join(parts)


def run_checks(run_dir: Path, acts: set[int] | None = None) -> tuple[list[dict], Run, int]:
    events, raw = load_events(run_dir)
    run = Run(events)
    fns = {1: act1, 2: act2, 3: act3, 4: act4}
    res = [fns[a](run) for a in sorted(fns) if not acts or a in acts]
    return res, run, raw


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Check the demo acts of a saved run")
    ap.add_argument("run_dir", help="runs/<runId>")
    ap.add_argument("--acts", default="", help="comma list, e.g. 1,2")
    ap.add_argument("--json", action="store_true", help="machine readable output")
    args = ap.parse_args(argv)
    run_dir = Path(args.run_dir)
    acts = {int(a) for a in args.acts.split(",") if a.strip()} or None
    res, run, raw = run_checks(run_dir, acts)
    if args.json:
        print(json.dumps({"mock": run.mock, "acts": [{k: v for k, v in r.items()} for r in res]}, indent=1, default=str))
        return 1 if any(r["status"] == "FAIL" for r in res) else 0
    print("run %s: %d raw events, %d after 11.7 dedupe, engine: %s" % (run_dir.name, raw, len(run.events), "MOCK" if run.mock else "real/unknown"))
    print("times below are the observer receive clock (never different laptops' clocks)")
    for r in res:
        tag = r["status"] + (" (mock)" if run.mock and r["status"] == "PASS" else "")
        print("\nAct %d [%s] %s" % (r["act"], tag, r["title"]))
        if r["status"] == "SKIP":
            print("   skipped: " + r["note"])
            continue
        for desc, ok, detail in r["checks"]:
            print("   %s %s%s" % ("ok  " if ok else "FAIL", desc, (" (" + detail + ")") if detail else ""))
        if r["measured"]:
            print("   measured: " + ", ".join("%s=%s" % kv for kv in r["measured"].items()))
    agree = chain_agreement(run_dir)
    if agree:
        print("\nledger exports: " + agree)
    if run.mock:
        print("\nNeeds --engine real (cannot be proven on the mock engine):")
        for r in res:
            for note in REAL_ONLY.get(r["act"], []):
                print("   act %d: %s" % (r["act"], note))
    else:
        print("\nReal-engine run: signatures, QCs and evidence were verified by the engine itself. Still outside this checker:")
        print("   history_merged after heal (A6+ feature), on-chain checks (chain_client audit), the hardware board (act 3b)")
    if run.mock:
        print("\nMOCK RUN: passing acts prove the event sequence only. Re-run with --engine real for E5-real.")
    failed = [r["act"] for r in res if r["status"] == "FAIL"]
    ran = [r for r in res if r["status"] != "SKIP"]
    print("\nSUMMARY: %s" % ("FAIL acts %s" % failed if failed else ("all %d checked acts PASS" % len(ran) if ran else "nothing to check")))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
