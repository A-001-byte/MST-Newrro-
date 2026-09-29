"""MockEngine: same API as swarmledger.ClusterEngine (00_CONTEXT 13) but NO cryptography.

A tiny Tendermint-shaped exchange (PROPOSAL -> PREVOTE -> PRECOMMIT -> local commit) with unsigned votes, so
quorum, kill, partition, master failover, equivocation evidence and cross-cluster packets behave the way the
demo needs while Track A's real engine is not available. Every emitted event carries data.mock = true.
Block/tx hashes use the real byte layouts (swarm/wire.py, verified against vectors.json).

MOCK ONLY: sig is always null, QC entries carry zeroed r/s, verify_foreign_packet skips signature checks.
"""
from __future__ import annotations

import random
from typing import Callable

from . import wire

TX_TYPES = ("OBS", "ROLE", "EVIDENCE", "SELF_REVOKE", "XC_SEND", "XC_RECV", "XC_ACK", "XC_TIMEOUT", "CONTINGENCY", "ZONE")
FAULTS = ("honest", "equivocate", "silent", "false_obs")
TRANSIENT = frozenset({"not_silent", "peer_not_silent", "peer_still_silent"})  # engine-local liveness hints
NIL = wire.NIL
ZERO32 = "0x" + "00" * 32


class MockIdentity:
    """Address only. There is no key and no signing in mock mode."""

    def __init__(self, address: str):
        self.address = address.lower()

    def sign_digest(self, digest: bytes) -> dict:  # never used by MockEngine; present for API parity
        return {"r": ZERO32, "s": ZERO32, "v": 27, "mock": True}


class MockView:
    """LedgerView-compatible read-only view of the committed state (00_CONTEXT 13)."""

    def __init__(self, st: "MockLedger"):
        self._st = st

    cluster_id = property(lambda s: s._st.cluster_id)
    mission = property(lambda s: s._st.mission)
    height = property(lambda s: s._st.height)
    committee = property(lambda s: {a: dict(m) for a, m in s._st.committee.items()})
    active_weight = property(lambda s: s._st.active_weight)
    revoked = property(lambda s: {a: dict(m) for a, m in s._st.revoked.items()})
    master = property(lambda s: s._st.master)
    contingency = property(lambda s: {c: dict(m) for c, m in s._st.contingency.items()})
    next_send_seq = property(lambda s: dict(s._st.next_send_seq))
    pending_sends = property(lambda s: {k: dict(v) for k, v in s._st.pending_sends.items()})
    received = property(lambda s: frozenset(s._st.received))

    def is_member(self, addr: str) -> bool:
        return addr in self._st.committee

    def weight_of(self, addr: str) -> int:
        return self._st.committee.get(addr, {}).get("weight", 0)


class MockLedger:
    """Deterministic cluster state; mirrors swarmledger.state.LedgerState semantics (apply order, skips, events)."""

    def __init__(self, mission, cluster_id: int, app):
        self.mission, self.cluster_id, self.app = mission, cluster_id, app
        cl = mission.cluster(cluster_id)
        self.enrolled = {m["address"]: {"weight": m["weight"], "flags": m["flags"]} for m in cl.members}
        self.committee = {a: dict(m) for a, m in self.enrolled.items()}
        self.revoked: dict[str, dict] = {}
        self.master = mission.member_by_name(cl.initial_master)["address"]
        self.height = 0
        self.genesis = wire.genesis_hash(mission.mission_id, cluster_id, mission.roster_hash)
        self.last_hash = self.genesis
        self.contingency: dict[int, dict] = {}
        self.peers = [c.cluster_id for c in mission.clusters if c.cluster_id != cluster_id]
        self.next_send_seq = {c: 1 for c in self.peers}
        self.pending_sends: dict[tuple[int, int], dict] = {}
        self.received: set[tuple[int, int]] = set()
        self.evidence: list[dict] = []
        self.committed: set[str] = set()
        self.view = MockView(self)

    @property
    def active_weight(self) -> int:
        return sum(m["weight"] for m in self.committee.values())

    def full_validators(self) -> list[str]:
        return sorted(a for a, m in self.committee.items() if not m["flags"] & 1)

    def proposers(self) -> list[str]:
        return sorted(self.committee) if self.mission.hardware_proposes else self.full_validators()

    def proposer(self, height: int, rnd: int) -> str:
        p = self.proposers()
        return p[(height + rnd) % len(p)]

    def successor(self, prev: str) -> str:
        fv = self.full_validators()
        for a in fv:
            if a > prev:
                return a
        return fv[0]

    # ---------------------------------------------------------------- validity ------
    def validate_tx(self, tx: dict, local: dict | None = None) -> tuple[bool, str]:
        """Validity vs the state after the last committed height. `local` = engine-local liveness hints only."""
        t, frm = tx.get("type"), tx.get("from")
        if t not in TX_TYPES:
            return False, "unknown_type"
        if tx.get("mission") != self.mission.mission_id or tx.get("cluster") != self.cluster_id:
            return False, "wrong_mission"
        if frm not in self.committee:
            return False, "not_member"
        if wire.tx_hash(tx) in self.committed:
            return False, "duplicate"
        light = bool(self.committee[frm]["flags"] & 1)
        ok, why = True, ""
        if t == "OBS":
            if light:
                return False, "light_cannot_obs"
            return self._app_check(tx)
        if t == "ROLE":
            if light or tx.get("prev") != self.master or tx.get("master") != self.successor(self.master):
                return False, "bad_role"
            if local is not None and local["now"] - local["last_heard"].get(tx["prev"], local["now"]) < local["role_silence"]:
                return False, "not_silent"
        elif t == "EVIDENCE":
            a, b = tx.get("a"), tx.get("b")
            if tx.get("offender") not in self.committee or not isinstance(a, dict) or not isinstance(b, dict):
                return False, "bad_evidence"
            if a.get("msgType") != b.get("msgType") or a.get("subject") == b.get("subject"):
                return False, "not_equivocation"
        elif t == "SELF_REVOKE":
            if tx.get("offender") not in self.committee:
                return False, "bad_evidence"
        elif t == "XC_SEND":
            dst = tx.get("dst")
            if frm != self.master or dst not in self.peers or tx.get("xcSeq") != self.next_send_seq.get(dst):
                return False, "bad_xc_send"
            if dst in self.contingency:
                return False, "contingency"
            ok, why = self._app_check(tx)
        elif t == "XC_RECV":
            if tx.get("src") not in self.peers or (tx.get("src"), tx.get("xcSeq")) in self.received:
                return False, "replay"
            ok, why = self._app_check(tx)
        elif t == "XC_ACK":
            p = self.pending_sends.get((tx.get("dst"), tx.get("xcSeq")))
            if not p or p["status"] not in ("SENT", "TIMED_OUT"):
                return False, "replay"
            ok, why = self._app_check(tx)
        elif t == "XC_TIMEOUT":
            p = self.pending_sends.get((tx.get("dst"), tx.get("xcSeq")))
            if frm != self.master or not p or p["status"] != "SENT":
                return False, "bad_timeout"
        elif t == "CONTINGENCY":
            peer, mode = tx.get("peer"), tx.get("mode")
            if light or peer not in self.peers or mode not in ("ENTER", "EXIT"):
                return False, "bad_contingency"
            if (mode == "ENTER") == (peer in self.contingency):
                return False, "stale_contingency"
            if local is not None:
                silent = local["now"] - local["last_heard_peer"].get(peer, local["now"])
                if mode == "ENTER" and silent < local["peer_silence"] and tx.get("reason") != "xc_timeout":
                    return False, "peer_not_silent"
                if mode == "EXIT" and silent > 2 * local["hello"]:
                    return False, "peer_still_silent"
        elif t == "ZONE":
            ok, why = (True, "") if frm == self.master else (False, "not_master")
            if ok:
                ok, why = self._app_check(tx)
        return ok, why

    def _app_check(self, tx: dict) -> tuple[bool, str]:
        try:
            return self.app.check_tx(tx, self.view)
        except Exception as e:  # noqa: BLE001
            return False, f"app_error:{type(e).__name__}"

    # ------------------------------------------------------------------- apply ------
    def apply_block(self, header: dict, blk_hash: str, txs: list[dict], height: int) -> list[tuple[str, dict]]:
        """Apply a committed block. Returns events; names starting with '_' are engine-internal. Never raises."""
        events: list[tuple[str, dict]] = []
        for item in txs:
            tx = item["tx"]
            h = wire.tx_hash(tx)
            self.committed.add(h)
            reason = self._apply_tx(tx, h, height, events)
            if reason:
                events.append(("tx_skipped", {"txHash": h, "type": tx["type"], "reason": reason}))
            try:
                self.app.deliver_tx(tx, self.view, height)
            except Exception as e:  # noqa: BLE001 - deliver_tx must never break consensus
                events.append(("tx_skipped", {"txHash": h, "type": tx["type"], "reason": f"app_error:{type(e).__name__}"}))
        self.height, self.last_hash = height, blk_hash
        try:
            self.app.end_block(height, self.view)
        except Exception:  # noqa: BLE001
            pass
        return events

    def _revoke(self, offender: str, reason: str, height: int, ev: dict, events: list) -> str:
        if offender not in self.committee:
            return "stale_revoke"
        del self.committee[offender]
        self.revoked[offender] = {"reason": reason, "atHeight": height}
        self.evidence.append({**ev, "offender": offender, "atHeight": height})
        events.append(("revoked", {"member": offender, "reason": reason, "atHeight": height,
                                   "effectiveHeight": height + 1}))
        return ""

    def _apply_tx(self, tx: dict, h: str, height: int, events: list) -> str:
        t = tx["type"]
        if t == "ROLE":
            if tx["prev"] != self.master or tx["master"] not in self.committee:
                return "stale_role"
            events.append(("master_changed", {"prev": self.master, "master": tx["master"], "height": height,
                                              "reason": tx.get("reason", "silent")}))
            self.master = tx["master"]
        elif t == "EVIDENCE":
            return self._revoke(tx["offender"], "EQUIVOCATION", height,
                                {"kind": "EQUIVOCATION", "a": tx["a"], "b": tx["b"]}, events)
        elif t == "SELF_REVOKE":
            return self._revoke(tx["offender"], "SELF_REVOKE", height,
                                {"kind": "SELF_REVOKE", "stmt": tx["stmt"]}, events)
        elif t == "XC_SEND":
            dst, seq = tx["dst"], tx["xcSeq"]
            if self.next_send_seq.get(dst) != seq:
                return "stale_xc_send"
            self.pending_sends[(dst, seq)] = {"status": "SENT", "height": height, "sendTxHash": h}
            self.next_send_seq[dst] = seq + 1
            events.append(("xc_sent", {"dst": dst, "seq": seq, "height": height, "sendTxHash": h,
                                       "obj": tx.get("payload", {}).get("obj")}))
            events.append(("_xc_send", {"tx": tx, "key": (dst, seq)}))
        elif t == "XC_RECV":
            key = (tx["src"], tx["xcSeq"])
            if key in self.received:
                return "replay"
            self.received.add(key)
            events.append(("_xc_recv", {"tx": tx}))
        elif t == "XC_ACK":
            p = self.pending_sends.get((tx["dst"], tx["xcSeq"]))
            if not p or p["status"] == "ACKED":
                return "replay"
            p["status"] = "ACKED"
            events.append(("xc_acked", {"dst": tx["dst"], "seq": tx["xcSeq"], "height": height}))
        elif t == "XC_TIMEOUT":
            p = self.pending_sends.get((tx["dst"], tx["xcSeq"]))
            if not p or p["status"] != "SENT":
                return "stale_timeout"
            p["status"] = "TIMED_OUT"
            events.append(("xc_timeout", {"dst": tx["dst"], "seq": tx["xcSeq"]}))
        elif t == "CONTINGENCY":
            peer = tx["peer"]
            if tx["mode"] == "ENTER" and peer not in self.contingency:
                self.contingency[peer] = {"sinceHeight": height, "reason": tx.get("reason", "")}
            elif tx["mode"] == "EXIT" and peer in self.contingency:
                del self.contingency[peer]
            else:
                return "stale_contingency"
            events.append(("contingency", {"mode": tx["mode"], "peer": peer, "reason": tx.get("reason", ""),
                                           "height": height, **({"peerTip": tx["peerTip"]} if "peerTip" in tx else {})}))
        return ""


class MockEngine:
    """Drop-in for swarmledger.ClusterEngine (constructor/API of 00_CONTEXT 13). Single-threaded."""

    def __init__(self, identity, mission, cluster_id: int, transport, clock, app,
                 on_commit: Callable[[dict], None] | None = None,
                 on_event: Callable[[str, dict], None] | None = None,
                 fault: str = "honest", light: bool = False, seed: int | None = None):
        self.identity, self.mission, self.cluster_id = identity, mission, cluster_id
        self.transport, self.clock, self.app = transport, clock, app
        self.on_commit, self.on_event = on_commit, on_event
        self.light = light
        self.fault = fault if fault in FAULTS else "honest"
        self.addr = identity.address
        member = mission.member_by_address(self.addr) or {}
        self.name = member.get("name", self.addr[:10])
        self.T = mission.timing
        self.ledger = MockLedger(mission, cluster_id, app)
        self.rng = random.Random(seed)
        self.blocks: list[dict] = []
        self.mempool: dict[str, dict] = {}
        self._seq = 0
        self.started = False
        self.start_ms = 0
        self.round = 0
        self.step = "propose"
        self.h_start = 0
        self.round_start = 0
        self.last_commit_ms = 0
        self.proposals: dict[tuple[int, int], dict] = {}
        self.votes: dict[tuple[int, int, str], dict[str, str]] = {}
        self.my_votes: set[tuple[int, int, str]] = set()
        self.sent_proposal: set[tuple[int, int]] = set()
        self.locked: tuple[str, int] | None = None
        self.valid: str | None = None
        self.last_heard: dict[str, int] = {}
        self.last_heard_peer: dict[int, int] = {}
        self.known_tip = 0
        self._tip_peer: str | None = None
        self._last_sync_req = -10**9
        self._last_hello = -10**9
        self._role_sub: dict[str, int] = {}
        self._cont_sub: dict[tuple[int, str], int] = {}
        self._evidence_seen: set[tuple[str, str, int, int]] = set()
        self._recv_submitted: set[tuple[int, int]] = set()
        self._ack_submitted: set[tuple[int, int]] = set()
        self._timeout_sub: dict[tuple[int, int], int] = {}
        self._rejected_seen: set[tuple] = set()
        self.relays: dict[tuple[int, int], dict] = {}
        self.ack_relays: dict[tuple[int, int], dict] = {}
        self.commit_seen: dict[tuple[int, int], int] = {}
        self.send_timeout_ms: dict[tuple[int, int], int] = {}
        self.dropped: dict[str, int] = {}
        transport.set_receiver(self.on_envelope)

    # ------------------------------------------------------------------ helpers -----
    def _now(self) -> int:
        return self.clock.now_ms()

    def _emit(self, name: str, data: dict) -> None:
        data["mock"] = True
        if self.on_event:
            self.on_event(name, data)

    def _count(self, reason: str) -> None:
        self.dropped[reason] = self.dropped.get(reason, 0) + 1

    @property
    def cur_height(self) -> int:
        return self.ledger.height + 1

    def _in_committee(self) -> bool:
        return self.addr in self.ledger.committee

    def _is_full_validator(self) -> bool:
        m = self.ledger.committee.get(self.addr)
        return bool(m) and not m["flags"] & 1

    def _env(self, etype: str, height: int, rnd: int, subject, body: dict, to: str) -> dict:
        return {"v": 1, "type": etype, "mission": self.mission.mission_id, "cluster": self.cluster_id,
                "from": self.addr, "to": to, "height": height, "round": rnd, "subject": subject, "sig": None,
                "body": body}

    def _send(self, to: str, env: dict) -> None:
        if self.fault == "silent":
            return
        self.transport.send(to, env)

    def _local_hints(self) -> dict:
        return {"now": self._now(), "last_heard": self.last_heard, "last_heard_peer": self.last_heard_peer,
                "role_silence": max(0, self.T.ROLE_SILENCE_MS - 500), "peer_silence": max(0, self.T.PEER_SILENCE_MS - 500),
                "hello": self.T.HELLO_INTERVAL_MS}

    # -------------------------------------------------------------- public API -------
    def start(self) -> None:
        now = self._now()
        self.started = True
        self.start_ms = now
        self.last_commit_ms = now
        self.h_start = now + self.T.COMMIT_INTERVAL_MS
        self.round_start = self.h_start
        self._hello(now, force=True)

    def tick(self) -> None:
        if not self.started:
            return
        now = self._now()
        self._hello(now)
        if self.light:
            return
        self._maybe_sync(now)
        self._maybe_role(now)
        self._maybe_contingency(now)
        self._xc_tick(now)
        self._round_logic(now)

    def submit_tx(self, tx: dict) -> str:
        tx = dict(tx)
        self._seq += 1
        tx.setdefault("mission", self.mission.mission_id)
        tx.setdefault("cluster", self.cluster_id)
        tx.setdefault("from", self.addr)
        tx.setdefault("seq", self._seq)
        tx.setdefault("t_ms", self._now())
        if tx.get("type") == "XC_SEND" and "xcSeq" not in tx:
            tx["xcSeq"] = self.ledger.next_send_seq.get(tx.get("dst"), 1)
        h = wire.tx_hash(tx)
        if len(self.mempool) < 2000 and h not in self.ledger.committed:
            self.mempool[h] = {"tx": tx, "sig": None}
        if not self.light:
            self._send(f"cluster:{self.cluster_id}", self._env("TX", 0, 0, h, {"tx": tx}, f"cluster:{self.cluster_id}"))
        return h

    def set_fault(self, mode: str) -> None:
        if mode in FAULTS:
            self.fault = mode

    def state(self) -> dict:
        lg = self.ledger
        last = self.blocks[-1] if self.blocks else None
        return {
            "name": self.name, "address": self.addr, "cluster": self.cluster_id, "light": self.light,
            "height": lg.height, "round": self.round, "step": self.step,
            "lastCommit": {"height": lg.height, "blockHash": lg.last_hash if lg.height else None,
                           "qcSigners": [s["signer"] for s in last["qc"]["sigs"]] if last else []},
            "master": lg.master,
            "committee": [{"address": a, "weight": m["weight"], "flags": m["flags"]} for a, m in sorted(lg.committee.items())],
            "revoked": {a: dict(m) for a, m in lg.revoked.items()},
            "contingency": {str(c): dict(m) for c, m in lg.contingency.items()},
            "xc": {"nextSendSeq": {str(d): n for d, n in lg.next_send_seq.items()},
                   "pendingSends": [{"dst": d, "xcSeq": s, "status": p["status"], "height": p["height"]}
                                    for (d, s), p in sorted(lg.pending_sends.items())],
                   "received": [[s, q] for s, q in sorted(lg.received)]},
            "mempool": len(self.mempool), "fault": self.fault, "app": self.app.snapshot(), "mock": True,
        }

    def export_certified(self) -> dict:
        lg = self.ledger
        return {"mission": self.mission.mission_id, "cluster": self.cluster_id, "rosterHash": self.mission.roster_hash,
                "genesisHash": lg.genesis,
                "blocks": [{"header": b["header"], "blockHash": b["blockHash"], "txs": b["txs"], "qc": b["qc"]}
                           for b in self.blocks],
                "evidence": [dict(e) for e in lg.evidence],
                "revocations": [{"member": a, "reason": r["reason"], "atHeight": r["atHeight"],
                                 "evidence": next((e for e in lg.evidence if e.get("offender") == a), None)}
                                for a, r in lg.revoked.items()],
                "mock": True, "note": "MOCK ENGINE EXPORT: unsigned votes, QC entries are placeholders. Not verifiable."}

    def verify_foreign_packet(self, packet: dict) -> tuple[bool, str]:
        ok, reason, _ = self._verify_packet(packet)
        return ok, reason

    # -------------------------------------------------------------- HELLO / STATUS ---
    def _hello(self, now: int, force: bool = False) -> None:
        if not force and now - self._last_hello < self.T.HELLO_INTERVAL_MS:
            return
        self._last_hello = now
        role = "master" if self.ledger.master == self.addr else "member"
        self._send("*", self._env("HELLO", 0, 0, None, {"name": self.name, "role": role, "light": self.light}, "*"))
        if not self.light:
            self._send(f"cluster:{self.cluster_id}",
                       self._env("STATUS", self.cur_height, self.round, None,
                                 {"step": self.step, "lastCommit": self.ledger.height}, f"cluster:{self.cluster_id}"))

    # ------------------------------------------------------------------ ingress ------
    def on_envelope(self, env: dict) -> None:
        if not isinstance(env, dict) or env.get("mission") != self.mission.mission_id:
            self._count("wrong_mission")
            return
        frm = str(env.get("from") or "").lower()
        now = self._now()
        self.last_heard[frm] = now
        cl = self.mission.cluster_of(frm)
        if cl is None:
            self._count("not_member")
            return
        if cl != self.cluster_id:
            self.last_heard_peer[cl] = now
        t = env.get("type")
        try:
            if t in ("PROPOSAL", "PREVOTE", "PRECOMMIT", "COMMIT", "STATUS") and env.get("cluster") == self.cluster_id and cl == self.cluster_id:
                {"PROPOSAL": self._on_proposal, "PREVOTE": self._on_vote, "PRECOMMIT": self._on_vote,
                 "COMMIT": self._on_commit_msg, "STATUS": self._on_status}[t](env)
            elif self.light:
                return
            elif t == "TX" and cl == self.cluster_id:
                self._on_tx(env)
            elif t == "SYNC_REQ" and cl == self.cluster_id:
                self._on_sync_req(env)
            elif t == "SYNC_RESP" and cl == self.cluster_id:
                self._on_sync_resp(env)
            elif t in ("XC_PACKET", "XC_ACK") and cl != self.cluster_id:
                self._on_xc(env)
        except (KeyError, TypeError, ValueError, AttributeError):
            self._count("malformed")

    def _on_tx(self, env: dict) -> None:
        tx = (env.get("body") or {}).get("tx")
        if not isinstance(tx, dict) or tx.get("from") != env.get("from"):
            return
        h = wire.tx_hash(tx)
        if h not in self.ledger.committed and h not in self.mempool and len(self.mempool) < 2000:
            self.mempool[h] = {"tx": tx, "sig": None}

    def _on_status(self, env: dict) -> None:
        last = (env.get("body") or {}).get("lastCommit")
        if isinstance(last, int) and last > self.known_tip:
            self.known_tip = last
            self._tip_peer = env["from"]

    # -------------------------------------------------------------- consensus --------
    def _weight_of(self, voters) -> int:
        c = self.ledger.committee
        return sum(c[a]["weight"] for a in voters if a in c)

    def _round_logic(self, now: int) -> None:
        h, r = self.cur_height, self.round
        if now < self.h_start:
            return
        if (self.step == "propose" and (h, r) not in self.sent_proposal and self._is_full_validator()
                and self.ledger.proposer(h, r) == self.addr and self.fault != "silent"):
            self._propose(now)
        has_prop = (h, r) in self.proposals
        if not has_prop:
            limit = self.T.TIMEOUT_PROPOSE_MS + r * self.T.TIMEOUT_DELTA_MS
        else:
            limit = (self.T.TIMEOUT_PROPOSE_MS + self.T.TIMEOUT_PREVOTE_MS + self.T.TIMEOUT_PRECOMMIT_MS
                     + r * self.T.TIMEOUT_DELTA_MS)
        if now - self.round_start >= limit:
            step = "propose" if not has_prop else ("prevote" if (h, r, "PRECOMMIT") not in self.my_votes else "precommit")
            self._emit("round_timeout", {"height": h, "round": r, "step": step})
            self.round = r + 1
            self.round_start = now
            self.step = "propose"

    def _propose(self, now: int) -> None:
        h, r = self.cur_height, self.round
        self.sent_proposal.add((h, r))
        lg = self.ledger
        reuse = self._known_block(self.valid) if self.valid else None
        if reuse is not None:
            header, txs = dict(reuse["header"]), list(reuse["txs"])
        else:
            txs, hashes = [], []
            local = self._local_hints()
            for th, item in list(self.mempool.items()):
                if len(txs) >= self.T.MAX_TXS_PER_BLOCK:
                    break
                ok, why = lg.validate_tx(item["tx"], local)
                if ok:
                    txs.append(item)
                    hashes.append(th)
                elif why not in TRANSIENT:
                    self.mempool.pop(th, None)   # permanently invalid (or already committed): evict
            header = {"mission": self.mission.mission_id, "cluster": self.cluster_id, "height": h,
                      "prevHash": lg.last_hash, "txRoot": wire.tx_root(hashes), "proposer": self.addr, "timestampMs": now}
        bh = wire.block_hash(header)
        env = self._env("PROPOSAL", h, r, bh, {"header": header, "txs": txs, "polRound": -1}, f"cluster:{self.cluster_id}")
        self._send(f"cluster:{self.cluster_id}", env)
        self._on_proposal(env)

    def _known_block(self, bh: str) -> dict | None:
        for p in self.proposals.values():
            if p["blockHash"] == bh and p.get("txs") is not None:
                return p
        return None

    def _on_proposal(self, env: dict) -> None:
        h, r = env["height"], env["round"]
        body = env["body"]
        header = body["header"]
        lg = self.ledger
        if h > self.cur_height:
            self.known_tip = max(self.known_tip, h - 1)
            return
        if h < self.cur_height or (h, r) in self.proposals:
            return
        expected = lg.proposer(h, r)
        if env["from"] != expected or header.get("proposer") != expected:
            self._count("bad_proposer")
            return
        bh = wire.block_hash(header)
        if (bh != env.get("subject") or header.get("prevHash") != lg.last_hash or header.get("height") != h
                or header.get("cluster") != self.cluster_id or header.get("mission") != self.mission.mission_id):
            self._count("bad_proposal")
            return
        txs = None
        if not self.light:
            txs = body.get("txs") or []
            hashes = [wire.tx_hash(i["tx"]) for i in txs]
            if wire.tx_root(hashes) != header.get("txRoot"):
                self._count("bad_txroot")
                return
        now = self._now()
        self.proposals[(h, r)] = {"blockHash": bh, "header": header, "txs": txs, "seen_ms": now, "proposer": expected}
        self._emit("proposal", {"height": h, "round": r, "blockHash": bh, "proposer": expected,
                                "txCount": len(txs) if txs is not None else 0})
        if r > self.round:
            self.round, self.round_start = r, now
        self.step = "prevote"
        if self._in_committee() and self.fault != "silent" and (h, r, "PREVOTE") not in self.my_votes:
            if self.locked and self.locked[0] != bh:
                self._cast("PREVOTE", h, r, NIL)
            else:
                self._cast("PREVOTE", h, r, bh)
                if self.fault == "equivocate":
                    self._cast_raw("PREVOTE", h, r, wire.hx(self.rng.randbytes(32)))
        self._tally(h, r, "PREVOTE")
        self._tally(h, r, "PRECOMMIT")

    def _cast(self, vtype: str, h: int, r: int, subject: str) -> None:
        self.my_votes.add((h, r, vtype))
        self.votes.setdefault((h, r, vtype), {})[self.addr] = subject
        self._cast_raw(vtype, h, r, subject)
        self._emit("vote", {"type": vtype, "height": h, "round": r, "subject": subject, "voter": self.addr})
        if not self.light:
            self._tally(h, r, vtype)

    def _cast_raw(self, vtype: str, h: int, r: int, subject: str) -> None:
        self._send(f"cluster:{self.cluster_id}", self._env(vtype, h, r, subject, {}, f"cluster:{self.cluster_id}"))

    def _on_vote(self, env: dict) -> None:
        voter, h, r, sub, vtype = env["from"], env["height"], env["round"], env["subject"], env["type"]
        if h < self.ledger.height - 1 or voter not in self.ledger.enrolled:
            return
        d = self.votes.setdefault((h, r, vtype), {})
        if voter in d:
            if d[voter] != sub:
                self._evidence(voter, vtype, h, r, d[voter], sub)
            return
        d[voter] = sub
        if h == self.cur_height:
            self._tally(h, r, vtype)
            if vtype == "PREVOTE":
                self._tally(h, r, "PRECOMMIT")

    def _stmt(self, vtype: str, h: int, r: int, subject: str) -> dict:
        return {"msgType": 2 if vtype == "PREVOTE" else 3, "missionId": self.mission.mission_id,
                "clusterId": self.cluster_id, "height": h, "round": r, "subject": subject, "sig": None}

    def _evidence(self, offender: str, vtype: str, h: int, r: int, s1: str, s2: str) -> None:
        key = (offender, vtype, h, r)
        if key in self._evidence_seen or self.light:
            return
        self._evidence_seen.add(key)
        a, b = self._stmt(vtype, h, r, s1), self._stmt(vtype, h, r, s2)
        self._emit("evidence", {"offender": offender, "kind": "EQUIVOCATION", "height": h, "round": r, "msgType": a["msgType"]})
        if self._is_full_validator() and offender in self.ledger.committee:
            self.submit_tx({"type": "EVIDENCE", "kind": "EQUIVOCATION", "a": a, "b": b, "offender": offender})

    def _tally(self, h: int, r: int, vtype: str) -> None:
        if h != self.cur_height:
            return
        d = self.votes.get((h, r, vtype))
        if not d:
            return
        by_sub: dict[str, int] = {}
        for voter, sub in d.items():
            w = self.ledger.committee.get(voter, {}).get("weight", 0)
            if w:
                by_sub[sub] = by_sub.get(sub, 0) + w
        active = self.ledger.active_weight
        for sub, w in by_sub.items():
            if not wire.quorum(w, active) or sub == NIL:
                continue
            if vtype == "PREVOTE":
                if self._known_hash(h, r) == sub:
                    self.valid = sub
                    if (self._in_committee() and self.fault != "silent" and (h, r, "PRECOMMIT") not in self.my_votes
                            and r >= self.round):
                        self.locked = (sub, r)
                        self.step = "precommit"
                        self._cast("PRECOMMIT", h, r, sub)
            elif not self.light:
                self._commit(h, r, sub)
            return

    def _known_hash(self, h: int, r: int) -> str | None:
        p = self.proposals.get((h, r))
        return p["blockHash"] if p else None

    def _commit(self, h: int, r: int, bh: str) -> None:
        if h != self.cur_height:
            return
        prop = next((p for (ph, _), p in self.proposals.items() if ph == h and p["blockHash"] == bh and p["txs"] is not None), None)
        if prop is None:
            self.known_tip = max(self.known_tip, h)
            self._maybe_sync(self._now(), force=True)
            return
        signers = sorted(v for v, s in self.votes.get((h, r, "PRECOMMIT"), {}).items() if s == bh and v in self.ledger.committee)
        qc = {"round": r, "sigs": [{"signer": a, "r": ZERO32, "s": ZERO32, "v": 27} for a in signers], "mock": True}
        self._apply_committed(prop["header"], bh, prop["txs"], qc, prop["seen_ms"])

    def _apply_committed(self, header: dict, bh: str, txs: list[dict], qc: dict, seen_ms: int) -> None:
        lg = self.ledger
        h = header["height"]
        now = self._now()
        signed_w = self._weight_of(s["signer"] for s in qc["sigs"])
        active_w = lg.active_weight
        events = lg.apply_block(header, bh, txs, h)
        block = {"header": header, "blockHash": bh, "txs": txs, "qc": qc}
        self.blocks.append(block)
        hashes = [wire.tx_hash(i["tx"]) for i in txs]
        for th in hashes:
            self.mempool.pop(th, None)
        tx_types: dict[str, int] = {}
        for i in txs:
            tx_types[i["tx"]["type"]] = tx_types.get(i["tx"]["type"], 0) + 1
        latency = max(0, now - seen_ms)
        self._emit("commit", {"height": h, "round": qc["round"], "blockHash": bh, "prevHash": header["prevHash"],
                              "proposer": header["proposer"], "txCount": len(txs), "txTypes": tx_types,
                              "qcSigners": [s["signer"] for s in qc["sigs"]], "signedWeight": signed_w,
                              "activeWeight": active_w, "latencyMs": latency})
        for name, data in events:
            if name.startswith("_"):
                self._internal_event(name, data, block, hashes, now)
            else:
                self._emit(name, data)
        self.last_commit_ms = now
        self.h_start = now + self.T.COMMIT_INTERVAL_MS
        self.round, self.round_start, self.step = 0, self.h_start, "propose"
        self.locked = self.valid = None
        self._prune(h)
        if self.on_commit:
            self.on_commit({"cluster": self.cluster_id, "height": h, "round": qc["round"], "blockHash": bh,
                            "header": header, "txs": txs, "qc": qc, "latencyMs": latency})
        # every node broadcasts COMMIT after committing (mock adds body.txs so followers can apply the block)
        self._send(f"cluster:{self.cluster_id}",
                   self._env("COMMIT", h, qc["round"], None,
                             {"header": header, "txHashes": hashes, "qc": qc, "revoked": sorted(lg.revoked),
                              "master": lg.master, "txs": txs}, f"cluster:{self.cluster_id}"))

    def _prune(self, h: int) -> None:
        for k in [k for k in self.proposals if k[0] <= h]:
            del self.proposals[k]
        for k in [k for k in self.votes if k[0] < h - 1]:
            del self.votes[k]
        self.my_votes = {k for k in self.my_votes if k[0] > h}
        self.sent_proposal = {k for k in self.sent_proposal if k[0] > h}

    # ------------------------------------------------------------- COMMIT / sync -----
    def _verify_commit_qc(self, header: dict, qc: dict) -> bool:
        signers = qc.get("signers")
        if signers is None:
            signers = [s.get("signer") for s in qc.get("sigs", [])]
        if signers != sorted(set(signers)):
            return False
        return wire.quorum(self._weight_of(signers), self.ledger.active_weight)

    def _on_commit_msg(self, env: dict) -> None:
        body = env["body"]
        header = body["header"]
        h = header["height"]
        lg = self.ledger
        if h <= lg.height:
            return
        if self.light:
            if header.get("cluster") == self.cluster_id and self._verify_commit_qc(header, body["qc"]):
                lg.height, lg.last_hash = h, wire.block_hash(header)
                lg.master = body.get("master") or lg.master
                for a in body.get("revoked", []):
                    if a in lg.committee:
                        del lg.committee[a]
                        lg.revoked[a] = {"reason": "COMMIT", "atHeight": h}
                self.round, self.step = 0, "propose"
                self._prune(h)
            return
        self.known_tip = max(self.known_tip, h)
        if h != self.cur_height:
            self._maybe_sync(self._now(), peer=env["from"])
            return
        bh = wire.block_hash(header)
        txs = body.get("txs")
        if (header.get("prevHash") != lg.last_hash or header.get("cluster") != self.cluster_id
                or txs is None or wire.tx_root([wire.tx_hash(i["tx"]) for i in txs]) != header.get("txRoot")
                or not self._verify_commit_qc(header, body["qc"])):
            self._count("bad_commit")
            return
        seen = next((p["seen_ms"] for (ph, _), p in self.proposals.items() if ph == h and p["blockHash"] == bh),
                    self._now())
        self._apply_committed(header, bh, txs, body["qc"], seen)

    def _maybe_sync(self, now: int, peer: str | None = None, force: bool = False) -> None:
        if self.known_tip <= self.ledger.height or self.fault == "silent":
            return
        if not force and now - self._last_sync_req < 300:
            return
        self._last_sync_req = now
        to = peer or self._tip_peer or f"cluster:{self.cluster_id}"
        self._send(to, self._env("SYNC_REQ", self.ledger.height + 1, 0, None,
                                 {"fromHeight": self.ledger.height + 1, "toHeight": self.known_tip}, to))

    def _on_sync_req(self, env: dict) -> None:
        frm = max(1, int(env["body"]["fromHeight"]))
        to_h = int(env["body"]["toHeight"])
        chunk = self.blocks[frm - 1: min(to_h, frm + 7)]
        if chunk:
            self._send(env["from"], self._env("SYNC_RESP", frm, 0, None, {"blocks": [
                {"header": b["header"], "txs": b["txs"], "qc": b["qc"]} for b in chunk]}, env["from"]))

    def _on_sync_resp(self, env: dict) -> None:
        for b in env["body"].get("blocks", []):
            header = b["header"]
            if header["height"] != self.cur_height:
                continue
            if (header.get("prevHash") != self.ledger.last_hash or not self._verify_commit_qc(header, b["qc"])
                    or wire.tx_root([wire.tx_hash(i["tx"]) for i in b["txs"]]) != header.get("txRoot")):
                self._count("bad_sync")
                break
            self._apply_committed(header, wire.block_hash(header), b["txs"], b["qc"], self._now())
        if self.known_tip > self.ledger.height:
            self._maybe_sync(self._now(), peer=env["from"], force=True)

    # ----------------------------------------------------------- master / peers ------
    def _maybe_role(self, now: int) -> None:
        lg = self.ledger
        master = lg.master
        if not self._is_full_validator() or master == self.addr or self.fault == "silent":
            return
        heard = self.last_heard.get(master)
        if heard is None or now - heard < self.T.ROLE_SILENCE_MS:
            return
        if now - self._role_sub.get(master, -10**9) < 3000:
            return
        self._role_sub[master] = now
        self.submit_tx({"type": "ROLE", "prev": master, "master": lg.successor(master), "reason": "silent"})

    def _maybe_contingency(self, now: int) -> None:
        if not self._is_full_validator() or self.fault == "silent":
            return
        lg = self.ledger
        for cl in lg.peers:
            heard = self.last_heard_peer.get(cl)
            if heard is None:
                continue
            mode = None
            if cl not in lg.contingency and now - heard >= self.T.PEER_SILENCE_MS:
                mode, reason = "ENTER", "peer_silent"
            elif cl in lg.contingency and now - heard <= 2 * self.T.HELLO_INTERVAL_MS:
                mode, reason = "EXIT", "peer_heard"
            if mode and now - self._cont_sub.get((cl, mode), -10**9) >= 3000:
                self._cont_sub[(cl, mode)] = now
                self.submit_tx({"type": "CONTINGENCY", "mode": mode, "peer": cl, "reason": reason})

    # ----------------------------------------------------------- cross-cluster -------
    def _packet_for(self, kind: str, header: dict, qc: dict, txs: list[dict], tx_item: dict, dst: int, seq: int) -> dict:
        return {"kind": kind, "src": self.cluster_id, "dst": dst, "seq": seq, "header": header, "qc": qc,
                "txHashes": [wire.tx_hash(i["tx"]) for i in txs], "tx": tx_item["tx"], "txSig": tx_item["sig"],
                "evidence": []}

    def _internal_event(self, name: str, data: dict, block: dict, hashes: list[str], now: int) -> None:
        tx = data["tx"]
        item = next(i for i in block["txs"] if i["tx"] is tx or i["tx"] == tx)
        if name == "_xc_send":
            dst, seq = data["key"]
            self.commit_seen[(dst, seq)] = now
            self.send_timeout_ms[(dst, seq)] = int(tx.get("timeoutMs", self.T.XC_TIMEOUT_MS))
            packet = self._packet_for("PACKET", block["header"], block["qc"], block["txs"], item, dst, seq)
            is_master = self.ledger.master == self.addr
            self.relays[(dst, seq)] = {"packet": packet, "last": now if is_master else now}
            if is_master:
                self._relay("XC_PACKET", packet, dst)
        elif name == "_xc_recv":
            src, seq = tx["src"], tx["xcSeq"]
            packet = self._packet_for("ACK", block["header"], block["qc"], block["txs"], item, src, seq)
            self.ack_relays[(src, seq)] = {"packet": packet, "last": now,
                                           "until": now + self.T.XC_TIMEOUT_MS + 2 * self.T.XC_RETRY_MS}
            if self.ledger.master == self.addr:
                self._relay("XC_ACK", packet, src)

    def _relay(self, etype: str, packet: dict, dst_cluster: int) -> None:
        to = f"cluster:{dst_cluster}"
        self._send(to, self._env(etype, packet["header"]["height"], packet["qc"]["round"], None, {"packet": packet}, to))

    def _xc_tick(self, now: int) -> None:
        lg = self.ledger
        for key, rl in list(self.relays.items()):
            p = lg.pending_sends.get(key)
            if not p or p["status"] != "SENT":
                del self.relays[key]
                continue
            if now - rl["last"] >= self.T.XC_RETRY_MS:
                rl["last"] = now
                self._relay("XC_PACKET", rl["packet"], key[0])
        for key, rl in list(self.ack_relays.items()):
            if now > rl["until"]:
                del self.ack_relays[key]
            elif now - rl["last"] >= self.T.XC_RETRY_MS:
                rl["last"] = now
                self._relay("XC_ACK", rl["packet"], key[0])
        if lg.master == self.addr and self.fault != "silent":
            for key, p in lg.pending_sends.items():
                if p["status"] != "SENT" or key not in self.commit_seen:
                    continue
                if (now - self.commit_seen[key] >= self.send_timeout_ms.get(key, self.T.XC_TIMEOUT_MS)
                        and now - self._timeout_sub.get(key, -10**9) >= 3000):
                    self._timeout_sub[key] = now
                    self.submit_tx({"type": "XC_TIMEOUT", "dst": key[0], "xcSeq": key[1]})

    def _packet_summary(self, packet: dict) -> tuple[list[str], int, int]:
        src = packet["src"]
        cl = self.mission.cluster(src)
        weights = {m["address"]: m["weight"] for m in cl.members}
        signers = [s["signer"] for s in packet["qc"]["sigs"]]
        return signers, sum(weights.get(a, 0) for a in signers), sum(weights.values())

    def _verify_packet(self, packet: dict) -> tuple[bool, str, tuple]:
        """Section 8.3 minus signatures (MOCK): structure, freshness, txRoot, inclusion, QC weight."""
        lg = self.ledger
        try:
            kind, src, dst, seq = packet["kind"], packet["src"], packet["dst"], packet["seq"]
            header, qc, txh, tx = packet["header"], packet["qc"], packet["txHashes"], packet["tx"]
            if kind not in ("PACKET", "ACK") or not isinstance(tx, dict):
                return False, "bad_tx", ((), 0, 0)
            if dst != self.cluster_id or src == self.cluster_id or header.get("cluster") != src:
                return False, "wrong_dst", ((), 0, 0)
            if header.get("mission") != self.mission.mission_id or tx.get("mission") != self.mission.mission_id:
                return False, "wrong_mission", ((), 0, 0)
            if kind == "PACKET":
                if (src, seq) in lg.received:
                    return False, "replay", ((), 0, 0)
            else:
                p = lg.pending_sends.get((src, seq))
                if not p or p["status"] not in ("SENT", "TIMED_OUT"):
                    return False, "replay", ((), 0, 0)
            if wire.tx_root(txh) != header.get("txRoot"):
                return False, "bad_txroot", ((), 0, 0)
            if wire.tx_hash(tx) not in txh:
                return False, "not_included", ((), 0, 0)
            good = tx.get("cluster") == src and tx.get("xcSeq") == seq and (
                (kind == "PACKET" and tx.get("type") == "XC_SEND" and tx.get("dst") == dst)
                or (kind == "ACK" and tx.get("type") == "XC_RECV" and tx.get("src") == dst))
            if not good:
                return False, "bad_tx", ((), 0, 0)
            signers, signed_w, active_w = self._packet_summary(packet)
            if signers != sorted(set(signers)):
                return False, "unsorted", ((), 0, 0)
            weights = {m["address"] for m in self.mission.cluster(src).members}
            if any(a not in weights for a in signers):
                return False, "not_member", ((), 0, 0)
            if not wire.quorum(signed_w, active_w):
                return False, "no_quorum", ((), 0, 0)
            return True, "", (signers, signed_w, active_w)
        except (KeyError, TypeError, AttributeError, StopIteration):
            return False, "bad_tx", ((), 0, 0)

    def _benign_replay(self, packet: dict) -> bool:
        """A re-relay of a packet this cluster already processed is normal redundancy, not an attack."""
        lg = self.ledger
        try:
            key = (packet["src"], packet["seq"])
            if packet["kind"] == "PACKET":
                return key in lg.received
            return lg.pending_sends.get(key, {}).get("status") == "ACKED"
        except (KeyError, TypeError):
            return False

    def _on_xc(self, env: dict) -> None:
        packet = (env.get("body") or {}).get("packet") or {}
        ok, reason, (signers, signed_w, active_w) = self._verify_packet(packet)
        relayer = env.get("from")
        if not ok:
            if reason == "replay" and self._benign_replay(packet):
                self._count("replay_benign")
                return
            key = (packet.get("src"), packet.get("seq"), reason, relayer)
            if key not in self._rejected_seen and len(self._rejected_seen) < 5000:
                self._rejected_seen.add(key)
                self._emit("xc_rejected", {"src": packet.get("src"), "seq": packet.get("seq"), "reason": reason,
                                           "relayer": relayer})
            return
        src, seq = packet["src"], packet["seq"]
        vkey = ("v", src, seq, packet["kind"])
        if vkey not in self._rejected_seen:
            self._rejected_seen.add(vkey)
            self._emit("xc_verified", {"src": src, "seq": seq, "signers": signers, "signedWeight": signed_w,
                                       "activeWeight": active_w, "relayer": relayer})
        if not self._in_committee() or self.fault == "silent":
            return
        if packet["kind"] == "PACKET":
            if (src, seq) not in self._recv_submitted:
                self._recv_submitted.add((src, seq))
                self.submit_tx({"type": "XC_RECV", "src": src, "xcSeq": seq, "packet": packet})
        elif (src, seq) not in self._ack_submitted:
            self._ack_submitted.add((src, seq))
            self.submit_tx({"type": "XC_ACK", "dst": src, "xcSeq": seq, "ackPacket": packet})

