#!/usr/bin/env python3
"""fake_hub.py: HW-lane bench hub. Stands in for the SW lane's swarm/hub.py so the board can be brought up alone.

It is NOT the real hub and NOT consensus. It emulates the compact-relay view a light node gets (spec 11.2) from a
simulated cluster whose full validators are the committee in config.h, and it CHECKS everything the board sends:

  * REGISTER (every 2 s, light:true) -> registry; GET http://localhost:47001/topology      (H1 acceptance)
  * per height: light PROPOSAL (no txs, real 167-byte blockHash) -> PREVOTEs from the simulated full validators
    -> light COMMIT {header, qc:{round, signers}, revoked, master}  (signers include the board only if it sent a
    valid PRECOMMIT for that block)                                                        (H4 acceptance)
  * every board envelope: statement rebuilt (spec 10.3), low-s, signature must recover the board's NODE_ADDRESS
    (v=0 -> tries 27/28, spec 10.10)                                                       (H2 acceptance)
  * two different subjects for the same (type,h,r) from the board -> EVIDENCE EQUIVOCATION -> board revoked in the
    next COMMIT; valid SELF_REVOKE (msgType 7, subject sha256("tamper")) -> revoked       (H3 acceptance)
  * every datagram to the board is checked <= 1400 bytes

Simulated validators' signatures are NOT real (zeros): the board does not verify signatures at L2 (spec 7.9).
Reads only PUBLIC fields of config.h (mission id, cluster, committee, board address); never prints secrets.

  python tools/fake_hub.py --config swarm_member/config.h [--interval-ms 1000] [--commit-after-ms 800] [--prevoters 4]
HTTP (port 47001): GET /topology  GET /stats  POST /rogue  POST /oversize  POST /reset
"""
import argparse
import hashlib
import json
import re
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import swarm_ref as ref  # noqa: E402

LIGHT_LIMIT = 1400
EMPTY_ROOT = "0x" + hashlib.sha256(b"").hexdigest()
TAMPER_HASH = "0x" + hashlib.sha256(b"tamper").hexdigest()
ZERO_SIG = {"r": "0x" + "00" * 32, "s": "0x" + "00" * 32, "v": 27}
MSGTYPE = {"PROPOSAL": 1, "PREVOTE": 2, "PRECOMMIT": 3, "SELF_REVOKE": 7}


def now_ms():
    return int(time.time() * 1000)


def log(tag, msg):
    print("%s [%s] %s" % (time.strftime("%H:%M:%S"), tag, msg), flush=True)


def read_public_config(path):
    t = Path(path).read_text(encoding="utf-8")

    def define(name):
        m = re.search(r"^\s*#define\s+" + name + r"\s+\"?([^\"\n]*?)\"?\s*(?://.*)?$", t, re.M)
        return m.group(1).strip() if m else None

    def arr(name):
        m = re.search(name + r"\s*\[[^\]]*\]\s*=\s*\{(.*?)\};", t, re.S)
        return m.group(1) if m else ""

    mission = define("MISSION_ID_HEX").lower()
    mission = mission[2:] if mission.startswith("0x") else mission
    addrs = [a.lower() for a in re.findall(r'"(0x[0-9a-fA-F]{40})"', arr("COMMITTEE_ADDR"))]
    weights = [int(x) for x in re.findall(r"\d+", arr("COMMITTEE_WEIGHT"))]
    flags = [int(x) for x in re.findall(r"\d+", arr("COMMITTEE_FLAGS"))]
    return {
        "mission": "0x" + mission,
        "cluster": int(define("CLUSTER_ID")),
        "board": define("NODE_ADDRESS").lower(),
        "name": define("NODE_NAME"),
        "committee": [{"addr": a, "weight": w, "flags": f} for a, w, f in zip(addrs, weights, flags)],
    }


class FakeHub:
    def __init__(self, cfg, args):
        self.cfg, self.args = cfg, args
        self.lock = threading.RLock()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", args.udp_port))
        self.nodes = {}              # addr -> {name, cluster, light, ip, port, lastSeen}
        self.stats = {"rxByType": {}, "txBytesMax": 0, "oversizeRefused": 0, "badSig": 0, "goodSig": 0,
                      "heights": 0, "boardPrevoteOk": 0, "boardPrecommitOk": 0, "boardInQc": 0, "evidence": 0,
                      "selfRevoke": 0, "prevoteLatencyMs": []}
        self.reset_chain()

    # ------------------------------------------------------------------ chain simulation state
    def reset_chain(self):
        with self.lock:
            self.height = 1
            self.prev_hash = "0x" + hashlib.sha256(b"fake-genesis:" + self.cfg["mission"].encode()).hexdigest()
            self.revoked = []
            self.pending_revoke = {}  # addr -> reason
            self.master = None
            self.first_vote = {}      # (type, h, r) -> subject (board)
            self.cur = None           # {h, r, blockHash, t0, boardPrevote, boardPrecommit}

    def full_validators(self):
        return sorted(m["addr"] for m in self.cfg["committee"] if not (m["flags"] & 1) and m["addr"] not in self.revoked)

    # ------------------------------------------------------------------ network
    def board_endpoint(self):
        n = self.nodes.get(self.cfg["board"])
        if not n or now_ms() - n["lastSeen"] > 6000:
            return None
        return (n["ip"], n["port"])

    def deliver(self, env, force_size=None):
        ep = self.board_endpoint()
        if not ep:
            return False
        data = json.dumps({"hub": "DELIVER", "env": env}, separators=(",", ":")).encode()
        if force_size:
            data = data + b" " * max(0, force_size - len(data))
        elif len(data) > LIGHT_LIMIT:
            self.stats["oversizeRefused"] += 1
            log("hub", "REFUSED %s of %d B to light node (> %d)" % (env.get("type"), len(data), LIGHT_LIMIT))
            return False
        self.stats["txBytesMax"] = max(self.stats["txBytesMax"], len(data) if not force_size else 0)
        self.sock.sendto(data, ep)
        return True

    def envelope(self, typ, frm, h, r, subject, body, signed=True):
        return {"v": 1, "type": typ, "mission": self.cfg["mission"], "cluster": self.cfg["cluster"], "from": frm,
                "to": "*", "height": h, "round": r, "subject": subject, "sig": ZERO_SIG if signed else None, "body": body}

    # ------------------------------------------------------------------ receive path
    def rx_loop(self):
        while True:
            try:
                data, (ip, port) = self.sock.recvfrom(65535)
            except OSError:
                continue
            try:
                msg = json.loads(data.decode("utf-8"))
            except Exception:  # noqa: BLE001
                log("rx", "non-JSON datagram from %s:%d (%d B)" % (ip, port, len(data)))
                continue
            with self.lock:
                self.handle(msg, ip, port, len(data))

    def handle(self, msg, ip, port, size):
        kind = msg.get("hub")
        if kind == "REGISTER":
            addr = str(msg.get("node", "")).lower()
            new = addr not in self.nodes or now_ms() - self.nodes[addr]["lastSeen"] > 6000
            self.nodes[addr] = {"node": addr, "name": msg.get("name"), "cluster": msg.get("cluster"),
                                "light": msg.get("light"), "ip": ip, "port": port, "lastSeen": now_ms()}
            if new:
                ok = addr == self.cfg["board"] and msg.get("light") is True and msg.get("cluster") == self.cfg["cluster"]
                log("hub", "REGISTER %s %s from %s:%d light=%s cluster=%s %s" % (
                    msg.get("name"), addr, ip, port, msg.get("light"), msg.get("cluster"),
                    "OK (matches config.h)" if ok else "MISMATCH vs config.h"))
            return
        if kind != "SEND":
            log("rx", "unexpected hub kind %r" % kind)
            return
        env = msg.get("env") or {}
        typ = env.get("type")
        self.stats["rxByType"][typ] = self.stats["rxByType"].get(typ, 0) + 1
        if msg.get("to") != "cluster:%d" % self.cfg["cluster"]:
            log("rx", "board SEND to %r (expected cluster:%d)" % (msg.get("to"), self.cfg["cluster"]))
        self.check_board_envelope(env, size)

    def check_board_envelope(self, env, size):
        typ = env.get("type")
        h, r, subj = env.get("height"), env.get("round"), env.get("subject")
        problems = []
        if env.get("v") != 1:
            problems.append("v!=1")
        if env.get("mission") != self.cfg["mission"]:
            problems.append("mission mismatch")
        if env.get("cluster") != self.cfg["cluster"]:
            problems.append("cluster mismatch")
        if str(env.get("from", "")).lower() != self.cfg["board"]:
            problems.append("from != board address")
        if typ not in MSGTYPE:
            problems.append("unexpected type %s" % typ)
        sig = env.get("sig") or {}
        recovered_v = None
        if not problems:
            try:
                dg = ref.stmt_digest(MSGTYPE[typ], ref.b32(env["mission"]), env["cluster"], h, r, ref.b32(subj))
                recovered_v = ref.verify_from(dg, int(sig["r"], 16), int(sig["s"], 16), int(sig["v"]), self.cfg["board"])
            except Exception as ex:  # noqa: BLE001
                problems.append("bad fields: %r" % ex)
        if recovered_v is None and not problems:
            problems.append("SIGNATURE does not recover the board address (or high-s)")
        if problems:
            self.stats["badSig"] += 1
            log("CHECK", "FAIL board %s h=%s r=%s: %s" % (typ, h, r, "; ".join(problems)))
            return
        self.stats["goodSig"] += 1
        tag = "sig OK v=0->%d, %d B" % (recovered_v, size)

        if typ == "SELF_REVOKE":
            body_ok = (env.get("body") or {}).get("reason") == "tamper" and subj == TAMPER_HASH and r == 0
            if body_ok and self.cfg["board"] not in self.revoked and self.cfg["board"] not in self.pending_revoke:
                self.stats["selfRevoke"] += 1
                self.pending_revoke[self.cfg["board"]] = "SELF_REVOKE"
                log("EVID", "SELF_REVOKE by board at h=%s verified (%s) -> revoked in next COMMIT" % (h, tag))
            elif not body_ok:
                log("CHECK", "FAIL SELF_REVOKE fields (reason/subject/round) wrong")
            return

        key = (typ, h, r)
        first = self.first_vote.get(key)
        if first is None:
            self.first_vote[key] = subj
        elif first != subj:
            if self.cfg["board"] not in self.revoked and self.cfg["board"] not in self.pending_revoke:
                self.stats["evidence"] += 1
                self.pending_revoke[self.cfg["board"]] = "EQUIVOCATION"
                log("EVID", "EQUIVOCATION by board: two %s at h=%s r=%s (%s.. vs %s..) both validly signed -> revoked in next COMMIT"
                    % (typ, h, r, first[:12], subj[:12]))
            return
        cur = self.cur
        if cur and h == cur["h"] and r == cur["r"] and subj == cur["blockHash"]:
            if typ == "PREVOTE" and not cur["boardPrevote"]:
                cur["boardPrevote"] = True
                lat = now_ms() - cur["t0"]
                self.stats["prevoteLatencyMs"].append(lat)
                log("vote", "board PREVOTE h=%d for the block (%s, %d ms after PROPOSAL)" % (h, tag, lat))
            elif typ == "PRECOMMIT" and not cur["boardPrecommit"]:
                cur["boardPrecommit"] = True
                log("vote", "board PRECOMMIT h=%d for the block (%s)" % (h, tag))
        else:
            log("vote", "board %s h=%s r=%s subject %s.. (%s) [not the current block]" % (typ, h, r, str(subj)[:12], tag))

    # ------------------------------------------------------------------ height loop
    def sim_loop(self):
        while True:
            try:
                self.one_height()
            except Exception as ex:  # noqa: BLE001  (a bench tool must never die silently)
                log("hub", "sim error %r - continuing" % ex)
                time.sleep(0.5)

    def one_height(self):
        a = self.args
        if not self.board_endpoint():
            time.sleep(0.5)
            return
        with self.lock:
            fulls = self.full_validators()
            h, r = self.height, 0
            proposer = fulls[(h + r) % len(fulls)]
            header = {"mission": self.cfg["mission"], "cluster": self.cfg["cluster"], "height": h,
                      "prevHash": self.prev_hash, "txRoot": EMPTY_ROOT, "proposer": proposer, "timestampMs": now_ms()}
            bh = "0x" + ref.block_hash(header).hex()
            mine = {"h": h, "r": r, "blockHash": bh, "t0": now_ms(), "boardPrevote": False, "boardPrecommit": False}
            self.cur = mine
            self.deliver(self.envelope("PROPOSAL", proposer, h, r, bh, {"header": header, "polRound": -1}))
        time.sleep(0.1)
        for v in fulls[: a.prevoters]:
            with self.lock:
                self.deliver(self.envelope("PREVOTE", v, h, r, bh, {}))
            time.sleep(0.02)
        time.sleep(max(0, a.commit_after_ms / 1000.0 - 0.1 - 0.02 * a.prevoters))
        with self.lock:
            cur = self.cur
            if cur is not mine:                          # POST /reset happened during this height: drop it
                return
            board = self.cfg["board"]
            signers = list(fulls)
            board_in = cur["boardPrecommit"] and board not in self.revoked
            if board_in:
                signers.append(board)
            signers.sort()
            newly = [m for m in self.pending_revoke if m not in self.revoked]
            self.revoked.extend(newly)
            self.pending_revoke.clear()
            if self.master is None:
                self.master = fulls[0]
            body = {"header": header, "qc": {"round": r, "signers": signers}, "revoked": sorted(self.revoked), "master": self.master}
            self.deliver(self.envelope("COMMIT", proposer, h, r, None, body, signed=False))
            st = self.stats
            st["heights"] += 1
            st["boardPrevoteOk"] += cur["boardPrevote"]
            st["boardPrecommitOk"] += cur["boardPrecommit"]
            st["boardInQc"] += board_in
            log("commit", "h=%d %s.. signers=%d board: prevote=%s precommit=%s inQC=%s%s" % (
                h, bh[:12], len(signers), "Y" if cur["boardPrevote"] else "-", "Y" if cur["boardPrecommit"] else "-",
                "YES" if board_in else "no", ("  REVOKED " + ",".join(x[:10] for x in newly)) if newly else ""))
            if st["heights"] % 10 == 0:
                self.summary()
            self.prev_hash = bh
            self.height += 1
        time.sleep(a.interval_ms / 1000.0)

    def summary(self):
        s = self.stats
        n = max(1, s["heights"])
        lat = sorted(s["prevoteLatencyMs"][-50:])
        p50 = lat[len(lat) // 2] if lat else None
        log("SUMMARY", "heights=%d board prevote %d/%d precommit %d/%d inQC %d/%d | sigs ok=%d bad=%d | "
            "prevote latency p50=%s ms | max light datagram %d B (limit %d) | evidence=%d selfRevoke=%d" % (
                s["heights"], s["boardPrevoteOk"], n, s["boardPrecommitOk"], n, s["boardInQc"], n, s["goodSig"],
                s["badSig"], p50, s["txBytesMax"], LIGHT_LIMIT, s["evidence"], s["selfRevoke"]))

    # ------------------------------------------------------------------ control API
    def topology(self):
        t = now_ms()
        return {"nodes": [{"node": a, "name": n["name"], "cluster": n["cluster"], "light": n["light"], "ip": n["ip"],
                           "port": n["port"], "lastSeenMs": t - n["lastSeen"], "online": t - n["lastSeen"] <= 6000,
                           "killed": False, "dropRate": 0.0} for a, n in self.nodes.items()],
                "partition": None, "globalDropRate": 0.0, "fake": True}

    def control(self, cmd, **extra):
        ep = self.board_endpoint()
        if ep:
            self.sock.sendto(json.dumps(dict({"hub": "CONTROL", "cmd": cmd}, **extra)).encode(), ep)
        return bool(ep)


def make_http(hub):
    class H(BaseHTTPRequestHandler):
        def _send(self, obj, code=200):
            data = json.dumps(obj, indent=1).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            with hub.lock:
                if self.path == "/topology":
                    return self._send(hub.topology())
                if self.path == "/stats":
                    s = dict(hub.stats)
                    s["prevoteLatencyMs"] = s["prevoteLatencyMs"][-20:]
                    return self._send(dict(s, height=hub.height, revoked=hub.revoked))
            self._send({"error": "not found"}, 404)

        def do_POST(self):  # noqa: N802
            with hub.lock:
                if self.path == "/rogue":
                    return self._send({"ok": hub.control("rogue")})
                if self.path == "/oversize":
                    env = hub.envelope("STATUS", None, hub.height, 0, None, {"step": "propose", "lastCommit": hub.height - 1}, False)
                    return self._send({"ok": hub.deliver(env, force_size=1500), "bytes": 1500})
                if self.path == "/reset":
                    hub.reset_chain()
                    return self._send({"ok": True, "note": "chain reset; reboot the board too (its revocation is permanent)"})
            self._send({"error": "not found"}, 404)

        def log_message(self, *a):
            pass

    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(Path(__file__).resolve().parents[1] / "swarm_member" / "config.h"))
    ap.add_argument("--udp-port", type=int, default=47000)
    ap.add_argument("--http-port", type=int, default=47001)
    ap.add_argument("--interval-ms", type=int, default=1000, help="COMMIT_INTERVAL_MS after each commit")
    ap.add_argument("--commit-after-ms", type=int, default=800, help="PROPOSAL -> COMMIT time (board must precommit before)")
    ap.add_argument("--prevoters", type=int, default=4, help="how many simulated full validators prevote (0..4)")
    args = ap.parse_args()
    cfg = read_public_config(args.config)
    fulls = [m for m in cfg["committee"] if not (m["flags"] & 1)]
    log("hub", "FAKE HUB (bench only) mission %s.. cluster %d, board %s (%s), %d full validators, active weight %d" % (
        cfg["mission"][:12], cfg["cluster"], cfg["name"], cfg["board"], len(fulls), sum(m["weight"] for m in cfg["committee"])))
    hub = FakeHub(cfg, args)
    threading.Thread(target=hub.rx_loop, daemon=True).start()
    threading.Thread(target=hub.sim_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", args.http_port), make_http(hub))
    log("hub", "UDP 0.0.0.0:%d  HTTP http://localhost:%d/topology  - waiting for the board's REGISTER" % (args.udp_port, args.http_port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        hub.summary()


if __name__ == "__main__":
    main()
