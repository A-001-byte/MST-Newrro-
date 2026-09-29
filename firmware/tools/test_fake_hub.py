#!/usr/bin/env python3
"""test_fake_hub.py: end-to-end test of fake_hub.py with an emulated board (public test key k5, vectors.json roster).

Proves the bench hub is trustworthy before the real board depends on it:
  REGISTER -> /topology light:true; PROPOSAL blockHash recomputes; board votes counted and put in the QC;
  a forged signature is rejected; double PREVOTE -> EQUIVOCATION -> revoked; /reset; SELF_REVOKE -> revoked;
  every datagram to the light node <= 1400 B.
  python tools/test_fake_hub.py [--vectors <vectors.json>]
"""
import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import swarm_ref as ref  # noqa: E402

DEFAULT_VECTORS = HERE.parents[1].parent / "MST-Newrro-" / "ledger" / "test_vectors" / "vectors.json"
UDP, HTTP = 47100, 47101


def http(method, path):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (HTTP, path), method=method, data=b"{}" if method == "POST" else None)
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.loads(r.read())


class Board:
    def __init__(self, v):
        c1 = sorted((m for m in v["roster"]["members"] if m["clusterId"] == 1), key=lambda m: m["address"])
        self.committee = {m["address"]: m["weight"] for m in c1}
        k5 = next(k for k in v["keys"] if k["name"] == "k5")
        self.addr, self.priv = k5["address"], ref.b32(k5["priv"])
        self.mission = v["missionId"]
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.05)
        self.cur_h, self.voted, self.tally, self.revoked = 1, set(), {}, set()
        self.in_qc, self.max_rx, self.proposals_ok, self.proposals_bad, self.last_subject = 0, 0, 0, 0, {}

    def send(self, obj):
        self.sock.sendto(json.dumps(obj).encode(), ("127.0.0.1", UDP))

    def register(self):
        self.send({"hub": "REGISTER", "node": self.addr, "name": "c1-board", "cluster": 1, "light": True})

    def signed(self, typ, mt, h, r, subject, body=None, forge=False):
        dg = ref.stmt_digest(mt, ref.b32(self.mission), 1, h, r, ref.b32(subject))
        rr, ss, _ = ref.sign_digest(self.priv, dg)
        if forge:
            ss = ss ^ 1
        self.send({"hub": "SEND", "to": "cluster:1", "env": {
            "v": 1, "type": typ, "mission": self.mission, "cluster": 1, "from": self.addr, "to": "*",
            "height": h, "round": r, "subject": subject, "sig": {"r": "0x%064x" % rr, "s": "0x%064x" % ss, "v": 0},
            "body": body or {}}})

    def pump(self, seconds):
        end = time.time() + seconds
        last_reg = 0
        while time.time() < end:
            if time.time() - last_reg > 1:
                self.register()
                last_reg = time.time()
            try:
                data, _ = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            self.max_rx = max(self.max_rx, len(data))
            m = json.loads(data)
            if m.get("hub") != "DELIVER":
                continue
            e = m["env"]
            if e["type"] == "PROPOSAL":
                ok = "0x" + ref.block_hash(e["body"]["header"]).hex() == e["subject"] and "txs" not in e["body"]
                self.proposals_ok += ok
                self.proposals_bad += not ok
                self.last_subject[e["height"]] = e["subject"]
                if ok and e["height"] == self.cur_h and self.addr not in self.revoked and ("PV", e["height"]) not in self.voted:
                    self.voted.add(("PV", e["height"]))
                    self.signed("PREVOTE", 2, e["height"], e["round"], e["subject"])
                    self.tally.setdefault((e["height"], e["subject"]), set()).add(self.addr)
                    self.maybe_precommit(e["height"], e["round"], e["subject"])
            elif e["type"] == "PREVOTE":
                self.tally.setdefault((e["height"], e["subject"]), set()).add(e["from"])
                self.maybe_precommit(e["height"], e["round"], e["subject"])
            elif e["type"] == "COMMIT":
                b = e["body"]
                assert "txHashes" not in b and "sigs" not in b["qc"], "light COMMIT must carry no sigs / txHashes"
                self.in_qc += self.addr in b["qc"]["signers"]
                self.revoked = set(b["revoked"])
                self.cur_h = max(self.cur_h, b["header"]["height"] + 1)

    def maybe_precommit(self, h, r, subj):
        if ("PV", h) not in self.voted or ("PC", h) in self.voted or self.addr in self.revoked:
            return
        active = sum(w for a, w in self.committee.items() if a not in self.revoked)
        w = sum(self.committee.get(a, 0) for a in self.tally.get((h, subj), ()) if a not in self.revoked)
        if 3 * w > 2 * active:
            self.voted.add(("PC", h))
            self.signed("PRECOMMIT", 3, h, r, subj)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vectors", default=str(DEFAULT_VECTORS))
    args = ap.parse_args()
    v = json.load(open(args.vectors, encoding="utf-8"))
    c1 = sorted((m for m in v["roster"]["members"] if m["clusterId"] == 1), key=lambda m: m["address"])
    k5 = next(k for k in v["keys"] if k["name"] == "k5")
    cfg = Path(tempfile.mkdtemp(prefix="fakehub_")) / "config.h"
    cfg.write_text(
        '#define NODE_NAME "c1-board"\n#define NODE_ADDRESS "%s"\n#define MISSION_ID_HEX "%s"\n#define CLUSTER_ID 1\n'
        "#define COMMITTEE_SIZE %d\nstatic const char* const COMMITTEE_ADDR[COMMITTEE_SIZE] = { %s };\n"
        "static const uint16_t COMMITTEE_WEIGHT[COMMITTEE_SIZE] = { %s };\n"
        "static const uint8_t  COMMITTEE_FLAGS[COMMITTEE_SIZE]  = { %s };\n" % (
            k5["address"], v["missionId"][2:], len(c1), ", ".join('"%s"' % m["address"] for m in c1),
            ", ".join(str(m["weight"]) for m in c1), ", ".join(str(m["flags"]) for m in c1)), encoding="utf-8")
    env = dict(os.environ, PYTHONUTF8="1")
    hub = subprocess.Popen([sys.executable, str(HERE / "fake_hub.py"), "--config", str(cfg), "--udp-port", str(UDP),
                            "--http-port", str(HTTP), "--interval-ms", "250", "--commit-after-ms", "450"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    fails = 0

    def check(cond, msg):
        nonlocal fails
        print(("  ok   " if cond else "  FAIL ") + msg, flush=True)
        fails += 0 if cond else 1

    try:
        time.sleep(1.0)
        b = Board(v)
        b.pump(4.0)
        topo = http("GET", "/topology")
        node = next((n for n in topo["nodes"] if n["node"] == k5["address"]), None)
        check(node is not None and node["light"] is True and node["online"], "H1: /topology lists the board, light:true, online")
        check(b.proposals_ok >= 4 and b.proposals_bad == 0, "light PROPOSALs arrive and their blockHash recomputes (%d ok, %d bad)" % (b.proposals_ok, b.proposals_bad))
        st = http("GET", "/stats")
        check(st["boardInQc"] >= 3 and b.in_qc >= 3, "H4: board precommit lands in COMMIT qc.signers (%d heights)" % st["boardInQc"])
        check(st["badSig"] == 0 and st["goodSig"] >= 6, "H2: all board signatures (v=0) verify at the hub (%d ok)" % st["goodSig"])
        check(b.max_rx <= 1400, "every datagram to the light node <= 1400 B (max %d)" % b.max_rx)

        b.signed("PREVOTE", 2, 999, 0, "0x" + "11" * 32, forge=True)
        b.pump(0.5)
        check(http("GET", "/stats")["badSig"] == 1, "a forged signature is rejected by the hub")

        h = b.cur_h
        b.signed("PREVOTE", 2, h, 0, "0x" + "aa" * 32)
        b.signed("PREVOTE", 2, h, 0, "0x" + "bb" * 32)
        b.pump(2.0)
        st = http("GET", "/stats")
        check(st["evidence"] == 1 and k5["address"] in st["revoked"], "H3: double PREVOTE -> EQUIVOCATION -> board revoked")
        check(k5["address"] in b.revoked, "H3: the light COMMIT carries the board in revoked (servo 30 trigger)")

        http("POST", "/reset")
        b.cur_h, b.voted, b.tally, b.revoked = 1, set(), {}, set()
        b.pump(1.0)
        b.signed("SELF_REVOKE", 7, b.cur_h - 1, 0, "0x" + hashlib.sha256(b"tamper").hexdigest(), {"reason": "tamper"})
        b.pump(2.0)
        st = http("GET", "/stats")
        check(st["selfRevoke"] == 1 and k5["address"] in st["revoked"], "H3: SELF_REVOKE(tamper) verified -> board revoked")
        o = http("POST", "/oversize")
        check(o["ok"] and o["bytes"] == 1500, "/oversize sends a 1500 B datagram (board must log + ignore it)")
    finally:
        hub.terminate()
        out = hub.communicate(timeout=5)[0]
    if fails:
        print("---- fake_hub output ----\n" + out[-3000:])
    print("FAKE HUB E2E PASS" if not fails else "FAKE HUB E2E FAIL (%d)" % fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
