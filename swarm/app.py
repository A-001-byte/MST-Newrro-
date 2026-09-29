"""MissionApp: the ABCI-like application on top of a cluster ledger (00_CONTEXT 8.4, 12.2, 12.5, 13).

Determinism rules (docs/02 pitfall 8): integers only, sort before any median, no wall clock, no randomness in
check_tx / deliver_tx / end_block, deliver_tx never raises. Committed state is read ONLY through the LedgerView
attributes handed in as `state` (committee, contingency, pending_sends, received, ...).

Custody (8.4): initial custody from mission.world.objects[].initialCustody; the destination takes custody when
XC_RECV commits, the source releases when XC_ACK commits; XC_TIMEOUT keeps custody at the source and a late valid
XC_ACK still releases it.
"""
from __future__ import annotations

import math
from typing import Callable

from . import wire

DEVIATION_FLAG_CM = 1500
SCORE_EMIT_EVERY = 5          # heights between periodic member_score events (plus every flag transition)


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


class MissionApp:
    def __init__(self, cluster_id: int, mission, emit: Callable[[str, dict], None] | None = None):
        self.cluster_id = cluster_id
        self.mission = mission
        self.emit = emit
        self.zone = dict(mission.zone_of(cluster_id))
        self.window = int(mission.fusion.FUSION_WINDOW_HEIGHTS)
        self.min_members = int(mission.fusion.FUSION_MIN_MEMBERS)
        self.margin = int(mission.fusion.HANDOFF_MARGIN_CM)
        self.world_w = int(mission.world.get("width_cm", 20000))
        self.world_h = int(mission.world.get("height_cm", 10000))
        self.objects = [o["id"] for o in mission.world["objects"]]
        self.custody: dict[str, int] = {o["id"]: int(o["initialCustody"]) for o in mission.world["objects"]}
        self.obs: dict[tuple[str, str], dict] = {}
        self.last_track: dict[str, dict] = {}
        self.scores: dict[str, dict] = {}
        self.sent: dict[tuple[int, int], str] = {}      # (dst, xcSeq) -> obj for handoffs this cluster initiated
        self.height = 0
        self._view = None
        self._fresh: list[tuple[str, str]] = []          # (member, obj) observed in the block being applied

    # ------------------------------------------------------------------ events ----
    def _emit(self, event: str, data: dict) -> None:
        if self.emit is not None:
            try:
                self.emit(event, data)
            except Exception:  # noqa: BLE001 - a sink bug must never touch consensus state
                pass

    def announce_initial(self) -> None:
        for obj in self.objects:
            self._emit("ownership", {"obj": obj, "cluster": self.custody[obj], "height": 0, "via": "initial"})

    # -------------------------------------------------------------- pure queries ----
    def accepted_track(self, obj: str, state=None) -> dict | None:
        """12.5: latest OBS per active member inside the window, >= FUSION_MIN_MEMBERS, lower median of x, y, conf."""
        view = state if state is not None else self._view
        active = view.committee if view is not None else None
        rows = [o for (m, ob), o in self.obs.items()
                if ob == obj and o["height"] > self.height - self.window and (active is None or m in active)]
        n = len(rows)
        if n < self.min_members:
            return None
        i = (n - 1) // 2
        xs = sorted(o["x_cm"] for o in rows)
        ys = sorted(o["y_cm"] for o in rows)
        cs = sorted(o["conf_pct"] for o in rows)
        return {"x_cm": xs[i], "y_cm": ys[i], "conf_pct": cs[i], "members": n, "height": self.height}

    def _track_for_handoff(self, obj: str, state=None) -> dict | None:
        return self.accepted_track(obj, state) or self.last_track.get(obj)

    def destination(self) -> int | None:
        """12.5 rule 6: the cluster whose zone contains x = xMax + 1 (the right-hand neighbour)."""
        probe = self.zone["xMax_cm"] + 1
        for cl in self.mission.clusters:
            if cl.cluster_id != self.cluster_id and cl.zone["xMin_cm"] <= probe <= cl.zone["xMax_cm"]:
                return cl.cluster_id
        return None

    def pending_handoff(self, obj: str, state=None) -> bool:
        view = state if state is not None else self._view
        if view is None:
            return False
        pend = view.pending_sends
        return any(o == obj and pend.get(key, {}).get("status") == "SENT" for key, o in self.sent.items())

    def handoff_plan(self, obj: str, state=None) -> dict | None:
        """{dst, obj, track} when this cluster should hand `obj` off now (handoff_ready), else None."""
        view = state if state is not None else self._view
        if self.custody.get(obj) != self.cluster_id or self.pending_handoff(obj, view):
            return None
        track = self._track_for_handoff(obj, view)
        if track is None or track["x_cm"] <= self.zone["xMax_cm"] - self.margin:
            return None
        dst = self.destination()
        if dst is None or (view is not None and dst in view.contingency):
            return None
        return {"dst": dst, "obj": obj,
                "track": {"x_cm": track["x_cm"], "y_cm": track["y_cm"], "vx_cmps": 0, "vy_cmps": 0,
                          "conf_pct": track["conf_pct"]}}

    def handoff_ready(self, obj: str, state=None) -> bool:
        return self.handoff_plan(obj, state) is not None

    # -------------------------------------------------------------------- App API ---
    def check_tx(self, tx: dict, state) -> tuple[bool, str]:
        try:
            return self._check(tx, state)
        except Exception as e:  # noqa: BLE001 - malformed txs are simply invalid
            return False, f"malformed:{type(e).__name__}"

    def _check(self, tx: dict, state) -> tuple[bool, str]:
        t = tx.get("type")
        if t == "OBS":
            if tx.get("obj") not in self.objects:
                return False, "unknown_obj"
            x, y, c = tx.get("x_cm"), tx.get("y_cm"), tx.get("conf_pct")
            if not (_is_int(x) and _is_int(y) and _is_int(c)):
                return False, "not_int"
            if not (0 <= c <= 100 and 0 <= x <= self.world_w and 0 <= y <= self.world_h):
                return False, "out_of_range"
            return True, ""
        if t == "ZONE":
            z = tx.get("zone") or {}
            vals = [z.get(k) for k in ("xMin_cm", "xMax_cm", "yMin_cm", "yMax_cm")]
            if not all(_is_int(v) for v in vals) or vals[0] >= vals[1] or vals[2] >= vals[3]:
                return False, "bad_zone"
            return True, ""
        if t == "XC_SEND":
            payload = tx.get("payload") or {}
            obj = payload.get("obj")
            if payload.get("kind") != "HANDOFF" or obj not in self.objects:
                return False, "bad_payload"
            if self.custody.get(obj) != self.cluster_id:
                return False, "no_custody"
            if self.pending_handoff(obj, state):
                return False, "handoff_pending"
            track = self._track_for_handoff(obj, state)
            if track is None:
                return False, "no_track"
            if track["x_cm"] <= self.zone["xMax_cm"] - self.margin:
                return False, "before_handoff_line"
            if self.destination() != tx.get("dst"):
                return False, "wrong_dst"
            return True, ""
        if t == "XC_RECV":
            return self._payload_ok(((tx.get("packet") or {}).get("tx") or {}).get("payload"))
        if t == "XC_ACK":
            return self._payload_ok((((tx.get("ackPacket") or {}).get("tx") or {}).get("packet") or {})
                                    .get("tx", {}).get("payload"))
        return True, ""

    def _payload_ok(self, payload) -> tuple[bool, str]:
        if not isinstance(payload, dict) or payload.get("kind") != "HANDOFF" or payload.get("obj") not in self.objects:
            return False, "bad_payload"
        return True, ""

    def deliver_tx(self, tx: dict, state, height: int) -> None:
        """Called for EVERY committed tx (protocol + app) after protocol effects. Never raises."""
        try:
            self._view = state
            self._deliver(tx, state, height)
        except Exception:  # noqa: BLE001
            pass

    def _deliver(self, tx: dict, state, height: int) -> None:
        t = tx.get("type")
        if t == "OBS":
            ok, _ = self._check(tx, state)
            if ok and state.is_member(tx["from"]):
                self.obs[(tx["from"], tx["obj"])] = {"x_cm": tx["x_cm"], "y_cm": tx["y_cm"],
                                                     "conf_pct": tx["conf_pct"], "height": height}
                self._fresh.append((tx["from"], tx["obj"]))
        elif t == "ZONE":
            if self._check(tx, state)[0] and tx["from"] == state.master:
                self.zone = dict(tx["zone"])
        elif t == "XC_SEND":
            key = (tx["dst"], tx["xcSeq"])
            entry = state.pending_sends.get(key)
            if entry is not None and entry.get("sendTxHash") in (None, wire.tx_hash(tx)):
                self.sent[key] = tx["payload"]["obj"]
        elif t == "XC_RECV":
            if (tx["src"], tx["xcSeq"]) in state.received:
                self._set_custody(tx["packet"]["tx"]["payload"]["obj"], self.cluster_id, height, "XC_RECV")
        elif t == "XC_ACK":
            if state.pending_sends.get((tx["dst"], tx["xcSeq"]), {}).get("status") == "ACKED":
                obj = tx["ackPacket"]["tx"]["packet"]["tx"]["payload"]["obj"]
                self._set_custody(obj, tx["dst"], height, "XC_ACK")
        elif t == "XC_TIMEOUT":
            key = (tx["dst"], tx["xcSeq"])
            obj = self.sent.get(key)
            if obj is not None and state.pending_sends.get(key, {}).get("status") == "TIMED_OUT":
                self._emit("ownership", {"obj": obj, "cluster": self.custody[obj], "height": height, "via": "XC_TIMEOUT"})

    def _set_custody(self, obj: str, cluster: int, height: int, via: str) -> None:
        if obj not in self.custody or self.custody[obj] == cluster:
            return
        self.custody[obj] = cluster
        self._emit("ownership", {"obj": obj, "cluster": cluster, "height": height, "via": via})

    def end_block(self, height: int, state) -> None:
        try:
            self.height = height
            self._view = state
            self._end_block(height, state)
        except Exception:  # noqa: BLE001
            pass
        finally:
            self._fresh = []

    def _end_block(self, height: int, state) -> None:
        for obj in self.objects:
            track = self.accepted_track(obj, state)
            if track is None:
                continue
            self.last_track[obj] = dict(track)
            self._emit("track", {"obj": obj, "x_cm": track["x_cm"], "y_cm": track["y_cm"],
                                 "conf_pct": track["conf_pct"], "members": track["members"], "height": height})
            for member, ob in self._fresh:
                if ob != obj:
                    continue
                o = self.obs[(member, ob)]
                dev = math.isqrt((o["x_cm"] - track["x_cm"]) ** 2 + (o["y_cm"] - track["y_cm"]) ** 2)
                prev = self.scores.get(member)
                ewma = (3 * (prev["ewma"] if prev else 0) + dev) // 4   # alpha 1/4, starts at 0: one bad reading never flags
                flagged = ewma > DEVIATION_FLAG_CM
                changed = prev is None or prev["flagged"] != flagged
                self.scores[member] = {"ewma": ewma, "flagged": flagged}
                if changed or height % SCORE_EMIT_EVERY == 0:
                    self._emit("member_score", {"member": member, "deviation_cm": ewma, "flagged": flagged})

    # ----------------------------------------------------------------- snapshots ----
    def snapshot(self) -> dict:
        """JSON-serialisable and deterministic: identical committed txs give an identical snapshot."""
        tracks = {}
        for obj in self.objects:
            cur = self.accepted_track(obj)
            last = self.last_track.get(obj)
            if cur or last:
                tracks[obj] = {**(cur or last), "current": cur is not None}
        return {"cluster": self.cluster_id, "height": self.height, "custody": dict(sorted(self.custody.items())),
                "tracks": tracks, "scores": {m: dict(s) for m, s in sorted(self.scores.items())},
                "zone": dict(self.zone),
                "pendingHandoffs": {f"{d}:{s}": o for (d, s), o in sorted(self.sent.items())}}

    def debug_state(self) -> dict:
        """Everything that affects future decisions (used by the determinism test)."""
        return {"snapshot": self.snapshot(),
                "obs": {f"{m}|{o}": dict(v) for (m, o), v in sorted(self.obs.items())},
                "last_track": {k: dict(v) for k, v in sorted(self.last_track.items())}}
