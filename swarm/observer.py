"""Observer: UDP 47002 events -> dedupe -> ring buffer + latest state + metrics + SSE + run files (00_CONTEXT 11.7-11.8, 19).

  UDP  0.0.0.0:47002   §11.7 event envelopes from every process
  HTTP 0.0.0.0:8080    dashboard, /events SSE, /state, /config/mission.json, /deployments, /abi/<Name>.json,
                       POST /event, POST /runs/<runId>/export/<name>, POST /control/*, GET /mst/setup.html (if present)

Dedupe (11.7): engine events arrive once PER NODE; the first arrival wins and its OBSERVER receive time (`rx`, ms on this
machine's monotonic clock) is the event time. Nothing here ever compares clocks of different laptops.
events.jsonl keeps EVERY raw event (duplicates flagged dup=true); the dashboard/SSE and metrics see the deduped stream.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from collections import deque
from pathlib import Path

from aiohttp import web

from .common import (HUB_HTTP_PORT, OBSERVER_HTTP_PORT, OBSERVER_UDP_PORT, dumps, make_event, mono_ms, parse_hostport)

ROOT = Path(__file__).resolve().parents[1]
RING = 5000
REPLAY = 500
HEARTBEAT_S = 15
VOLATILE = frozenset({"node_state", "world", "topology", "hub_stats", "metrics"})   # only the latest is replayed
HUB_PROXY = frozenset({"partition", "heal", "drop", "kill", "revive", "fault", "rogue", "export"})
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
NODE_NAME_KEYS = ("name",)


def dedupe_key(ev: dict):
    """11.7 dedupe rule. Returns None for events that are never deduped (single-source or per-node by design)."""
    e, d, cl = ev.get("event"), ev.get("data") or {}, ev.get("cluster")
    g = d.get
    table = {
        "proposal": (cl, g("height"), g("round")),
        "commit": (cl, g("height")),
        "round_timeout": (cl, g("height"), g("round"), g("step")),
        "evidence": (cl, g("offender"), g("height"), g("round"), g("msgType")),
        "revoked": (cl, g("member")),
        "master_changed": (cl, g("height"), g("master")),
        "xc_sent": (cl, g("dst"), g("seq")),
        "xc_verified": (cl, g("src"), g("seq")),
        "xc_rejected": (cl, g("src"), g("seq"), g("reason"), g("relayer")),
        "xc_acked": (cl, g("dst"), g("seq")),
        "xc_timeout": (cl, g("dst"), g("seq")),
        "contingency": (cl, g("mode"), g("peer"), g("height")),
        "history_merged": (cl, g("peer"), g("toHeight")),
        "tx_skipped": (cl, g("txHash"), g("reason")),
        "track": (cl, g("obj"), g("height")),
        "ownership": (cl, g("obj"), g("cluster"), g("via"), g("height")),
        "member_score": (cl, g("member"), g("deviation_cm"), g("flagged")),
        "vote": (cl, g("type"), g("height"), g("round"), g("voter"), g("subject")),
        "enrollment": (g("txHash"),),
        "onchain_revocation": (g("txHash"),),
        "anchor": (g("txHash"),),
    }
    k = table.get(e)
    return None if k is None else (e,) + k


def pct(values: list[int], p: int) -> int | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, max(0, -(-p * len(s) // 100) - 1))]


def stat(values: list[int]) -> dict:
    return {"n": len(values), "p50": pct(values, 50), "p95": pct(values, 95),
            "avg": (sum(values) // len(values)) if values else None, "max": max(values) if values else None}


class Metrics:
    """Section 19 from the deduped event stream. Cross-node durations use `rx` (observer clock) only."""

    def __init__(self):
        self.commits: dict[int, dict[int, tuple[int, int, int]]] = {}    # cluster -> height -> (rx, latencyMs, round)
        self.faults: list[tuple[int, str, str, str]] = []                # (rx, mode, node, name)
        self.xc_sent: dict[tuple, dict] = {}                             # (src, dst, seq) -> {rx, obj, recv, ack}
        self.recv_by_obj: list[tuple[int, str, int]] = []                # (rx, obj, cluster) ownership via XC_RECV
        self.master_changes: list[tuple[int, int, str]] = []             # (rx, cluster, prev)
        self.evidence_rx: dict[str, int] = {}
        self.revoked_rx: dict[str, int] = {}
        self.contingency: list[tuple[int, int, str]] = []                # (rx, cluster, mode)
        self.hub_stats: dict = {}
        self.mock = False

    def feed(self, ev: dict, rx: int) -> None:
        e, d, cl = ev["event"], ev.get("data") or {}, ev.get("cluster")
        if d.get("mock"):
            self.mock = True
        if e == "commit" and cl is not None:
            self.commits.setdefault(cl, {}).setdefault(d["height"], (rx, d.get("latencyMs", 0), d.get("round", 0)))
        elif e == "fault_injected":
            self.faults.append((rx, d.get("mode", ""), d.get("node", ""), d.get("name") or ""))
        elif e == "xc_sent":
            self.xc_sent.setdefault((cl, d["dst"], d["seq"]), {"rx": rx, "obj": d.get("obj")})
        elif e == "ownership" and d.get("via") == "XC_RECV":
            self.recv_by_obj.append((rx, d.get("obj"), d.get("cluster")))
        elif e == "xc_acked":
            s = self.xc_sent.get((cl, d["dst"], d["seq"]))
            if s and "ack" not in s:
                s["ack"] = rx
        elif e == "master_changed":
            self.master_changes.append((rx, cl, d.get("prev", "")))
        elif e == "evidence":
            self.evidence_rx.setdefault(d.get("offender", ""), rx)
        elif e == "revoked":
            self.revoked_rx.setdefault(d.get("member", ""), rx)
        elif e == "contingency" and cl is not None:
            self.contingency.append((rx, cl, d.get("mode", "")))
        elif e == "hub_stats":
            self.hub_stats = d

    def snapshot(self) -> dict:
        per_cluster, intervals_all = {}, []
        total_commits = 0
        for cl, hs in sorted(self.commits.items()):
            rows = [hs[h] for h in sorted(hs)]
            total_commits += len(rows)
            gaps = [rows[i][0] - rows[i - 1][0] for i in range(1, len(rows))]
            intervals_all += gaps
            per_cluster[str(cl)] = {"commits": len(rows), "height": max(hs) if hs else 0,
                                    "commit_latency_ms": stat([r[1] for r in rows]),
                                    "block_interval_ms": stat(gaps),
                                    "rounds": {"avg_x100": (sum(r[2] for r in rows) * 100 // len(rows)) if rows else None,
                                               "max": max((r[2] for r in rows), default=None)}}
        recv_ms, ack_ms = [], []
        for (src, dst, seq), s in self.xc_sent.items():
            r = next((rx for rx, obj, c in self.recv_by_obj if obj == s["obj"] and c == dst and rx >= s["rx"]), None)
            if r is not None:
                recv_ms.append(r - s["rx"])
            if "ack" in s:
                ack_ms.append(s["ack"] - s["rx"])
        failover = []
        for rx, cl, prev in self.master_changes:
            k = [f for f in self.faults if f[1] == "kill" and f[2] == prev and f[0] <= rx]
            if k:
                failover.append(rx - k[-1][0])
        revocation = [self.revoked_rx[o] - rx for o, rx in self.evidence_rx.items() if o in self.revoked_rx]
        cont_enter, cont_exit = [], []
        parts = [f[0] for f in self.faults if f[1] == "partition"]
        heals = [f[0] for f in self.faults if f[1] == "heal"]
        for cl in sorted({c for _, c, _ in self.contingency}):
            for t0 in parts:
                hit = next((rx for rx, c, m in self.contingency if c == cl and m == "ENTER" and rx >= t0), None)
                if hit is not None:
                    cont_enter.append(hit - t0)
            for t0 in heals:
                hit = next((rx for rx, c, m in self.contingency if c == cl and m == "EXIT" and rx >= t0), None)
                if hit is not None:
                    cont_exit.append(hit - t0)
        bt = self.hub_stats.get("byType", {})
        consensus = {k: v for k, v in bt.items() if k not in ("HELLO", "STATUS")}
        msgs = sum(v["count"] for v in consensus.values())
        byts = sum(v["bytes"] for v in consensus.values())
        return {"mock": self.mock, "clusters": per_cluster, "block_interval_ms": stat(intervals_all),
                "handoff_recv_ms": stat(recv_ms), "handoff_ack_ms": stat(ack_ms), "failover_ms": stat(failover),
                "revocation_ms": stat(revocation), "contingency_enter_ms": stat(cont_enter),
                "contingency_exit_ms": stat(cont_exit),
                "msgs_per_commit": (msgs // total_commits) if total_commits and msgs else None,
                "bytes_per_commit": (byts // total_commits) if total_commits and byts else None,
                "clock": "observer-receive"}


class LocalEvents:
    """EventSender-compatible sink for in-process producers (station: director, mock nodes)."""

    def __init__(self, observer: "Observer"):
        self.observer = observer

    def emit(self, src, name, cluster, event, data) -> None:
        self.observer.ingest(make_event(src, name, cluster, event, data))

    def send(self, ev: dict) -> None:
        self.observer.ingest(ev)

    def close(self) -> None:
        pass


class _UdpProto(asyncio.DatagramProtocol):
    def __init__(self, obs: "Observer"):
        self.obs = obs

    def datagram_received(self, data, addr):
        try:
            ev = json.loads(data)
        except ValueError:
            self.obs.bad += 1
            return
        self.obs.ingest(ev)

    def error_received(self, exc):
        pass


class Observer:
    def __init__(self, host: str = "0.0.0.0", udp_port: int = OBSERVER_UDP_PORT, http_port: int = OBSERVER_HTTP_PORT,
                 runs_dir: Path | None = None, run_id: str | None = None,
                 hub_http: tuple[str, int] = ("127.0.0.1", HUB_HTTP_PORT), mission_path: Path | None = None,
                 dashboard_dir: Path | None = None, deployments_path: Path | None = None, abi_dir: Path | None = None,
                 director=None, metrics_interval_s: float = 2.0):
        self.host, self.udp_port, self.http_port = host, udp_port, http_port
        self.runs_dir = Path(runs_dir or ROOT / "runs")
        self.run_id = run_id or time.strftime("run-%Y%m%d-%H%M%S")
        self.hub_http = hub_http
        self.mission_path = mission_path
        self.dashboard_dir = Path(dashboard_dir or ROOT / "dashboard")
        self.deployments_path, self.abi_dir = deployments_path, abi_dir
        self.director = director
        self.metrics_interval_s = metrics_interval_s
        self.ring: deque = deque(maxlen=RING)
        self.latest: dict[str, dict] = {}            # volatile events: latest per (event[, node])
        self.nodes: dict[str, dict] = {}
        self.seen: dict = {}
        self.metrics = Metrics()
        self.metrics_cache: dict = self.metrics.snapshot()
        self.subscribers: set[asyncio.Queue] = set()
        self.seq = 0
        self.raw_count = 0
        self.dup_count = 0
        self.bad = 0
        self.mock = False
        self._file = None
        self._transport = None
        self._runner: web.AppRunner | None = None
        self._tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------- ingest ----
    def ingest(self, ev: dict) -> None:
        if not isinstance(ev, dict) or ev.get("v") != 1 or not isinstance(ev.get("event"), str):
            self.bad += 1
            return
        ev.setdefault("data", {})
        if not isinstance(ev["data"], dict):
            self.bad += 1
            return
        rx = mono_ms()
        self.raw_count += 1
        key = dedupe_key(ev)
        dup = False
        if key is not None:
            if key in self.seen:
                dup = True
            else:
                self.seen[key] = rx
                if len(self.seen) > 60000:
                    for k in list(self.seen)[:20000]:
                        del self.seen[k]
        self._write_raw(ev, rx, dup)
        if dup:
            self.dup_count += 1
            return
        ev["rx"] = rx
        self.seq += 1
        ev["seq"] = self.seq
        if ev["data"].get("mock"):
            self.mock = True
        self.metrics.feed(ev, rx)
        name = ev["event"]
        if name == "node_state" and ev.get("name"):
            self.nodes[ev["name"]] = {"state": ev["data"], "rx": rx, "cluster": ev.get("cluster")}
        if name in VOLATILE:
            self.latest[name if name != "node_state" else "node_state:" + str(ev.get("name"))] = ev
        else:
            self.ring.append(ev)
        payload = dumps(ev).decode("utf-8")
        for q in list(self.subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                self.subscribers.discard(q)          # a stuck browser is dropped; EventSource reconnects and replays

    def _write_raw(self, ev: dict, rx: int, dup: bool) -> None:
        if self._file is None:
            return
        try:
            self._file.write(json.dumps({**ev, "rx": rx, "dup": dup}, separators=(",", ":"), ensure_ascii=False) + "\n")
            self._file.flush()
        except (OSError, ValueError):
            pass

    def replay_events(self) -> list[dict]:
        tail = list(self.ring)[-REPLAY:]
        volatile = sorted(self.latest.values(), key=lambda e: e["seq"])
        return sorted(tail + volatile, key=lambda e: e["seq"])

    def state(self) -> dict:
        return {"runId": self.run_id, "mock": self.mock,
                "nodes": {n: {**v["state"], "_rx": v["rx"]} for n, v in sorted(self.nodes.items())},
                "topology": (self.latest.get("topology") or {}).get("data"),
                "world": (self.latest.get("world") or {}).get("data"),
                "metrics": self.metrics_cache, "counts": {"raw": self.raw_count, "dup": self.dup_count, "bad": self.bad},
                "rxNow": mono_ms()}

    # ---------------------------------------------------------------- lifecycle -----
    async def start(self) -> None:
        run_dir = self.runs_dir / self.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        self._file = open(run_dir / "events.jsonl", "a", encoding="utf-8")
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(lambda: _UdpProto(self),
                                                                 local_addr=(self.host, self.udp_port))
        self.udp_port = self._transport.get_extra_info("sockname")[1]
        app = self.build_app()
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, self.host, self.http_port).start()
        self.http_port = self._runner.addresses[0][1]
        self._tasks = [asyncio.create_task(self._metrics_loop())]

    async def close(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self._transport:
            self._transport.close()
        for q in list(self.subscribers):
            q.put_nowait(None)
        if self._runner:
            await self._runner.cleanup()
        if self._file:
            self._file.close()
            self._file = None

    async def _metrics_loop(self) -> None:
        while True:
            await asyncio.sleep(self.metrics_interval_s)
            self.metrics_cache = self.metrics.snapshot()
            self.ingest(make_event("observer", None, None, "metrics", self.metrics_cache))

    # -------------------------------------------------------------------- HTTP ------
    def build_app(self) -> web.Application:
        @web.middleware
        async def cors(request, handler):
            def add(r):
                r.headers["Access-Control-Allow-Origin"] = "*"
                r.headers["Access-Control-Allow-Headers"] = "Content-Type"
                r.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
                return r
            if request.method == "OPTIONS":
                return add(web.Response(status=204))
            try:
                resp = await handler(request)
            except web.HTTPException as ex:
                add(ex)
                raise
            return resp if resp.prepared else add(resp)

        app = web.Application(middlewares=[cors], client_max_size=128 * 1024 * 1024)
        app.add_routes([
            web.get("/", self.h_index), web.get("/events", self.h_events), web.get("/state", self.h_state),
            web.get("/config/mission.json", self.h_mission), web.get("/deployments", self.h_deployments),
            web.get("/abi/{name}.json", self.h_abi), web.get("/scenarios", self.h_scenarios),
            web.post("/event", self.h_event), web.post("/runs/{run}/export/{name}", self.h_export),
            web.post("/control/scenario", self.h_scenario), web.post("/control/scenario/stop", self.h_scenario_stop),
            web.post("/control/object", self.h_object), web.post("/control/{cmd}", self.h_control),
            web.get("/mst/{tail:.*}", self.h_mst),
            web.get("/{file:app\\.js|style\\.css|favicon\\.svg}", self.h_static),
        ])
        return app

    async def h_index(self, request):
        p = self.dashboard_dir / "index.html"
        if not p.exists():
            return web.Response(text="dashboard/index.html not found", status=404)
        return web.FileResponse(p, headers={"Cache-Control": "no-cache"})

    async def h_static(self, request):
        p = self.dashboard_dir / request.match_info["file"]
        if not p.exists():
            raise web.HTTPNotFound()
        return web.FileResponse(p, headers={"Cache-Control": "no-cache"})

    async def h_mst(self, request):
        base = self.dashboard_dir / "mst"
        tail = request.match_info["tail"] or "setup.html"
        p = (base / tail).resolve()
        if not base.exists() or base.resolve() not in p.parents or not p.is_file():
            return web.Response(status=404, text="MST mission-setup page not installed in this workspace "
                                                 "(Track A provides dashboard/mst/). Copy it next to dashboard/.")
        return web.FileResponse(p, headers={"Cache-Control": "no-cache"})

    async def h_events(self, request):
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache",
                                                       "Connection": "keep-alive", "X-Accel-Buffering": "no",
                                                       "Access-Control-Allow-Origin": "*"})
        await resp.prepare(request)
        q: asyncio.Queue = asyncio.Queue(maxsize=5000)
        for ev in self.replay_events():
            await resp.write(f"id: {ev['seq']}\ndata: {json.dumps(ev, separators=(',', ':'))}\n\n".encode("utf-8"))
        self.subscribers.add(q)
        try:
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_S)
                except asyncio.TimeoutError:
                    await resp.write(b": heartbeat\n\n")
                    continue
                if item is None:
                    break
                await resp.write(f"data: {item}\n\n".encode("utf-8"))
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self.subscribers.discard(q)
        return resp

    async def h_state(self, request):
        return web.json_response(self.state())

    async def h_mission(self, request):
        if not self.mission_path or not Path(self.mission_path).exists():
            return web.json_response({"error": "no mission file"}, status=404)
        raw = json.loads(Path(self.mission_path).read_text(encoding="utf-8"))

        def scrub(o):
            if isinstance(o, dict):
                return {k: scrub(v) for k, v in o.items() if k.lower() not in ("priv", "private", "privkey", "priv_key")}
            return [scrub(v) for v in o] if isinstance(o, list) else o
        return web.json_response(scrub(raw))

    async def h_deployments(self, request):
        p = self.deployments_path
        if p and Path(p).exists():
            try:
                return web.json_response(json.loads(Path(p).read_text(encoding="utf-8")))
            except ValueError:
                pass
        return web.json_response({})

    async def h_abi(self, request):
        name = request.match_info["name"]
        if not SAFE_NAME.match(name) or not self.abi_dir or not (Path(self.abi_dir) / f"{name}.json").exists():
            return web.json_response({"error": "abi not available"}, status=404)
        return web.FileResponse(Path(self.abi_dir) / f"{name}.json")

    async def h_scenarios(self, request):
        from .director import list_scenarios
        return web.json_response({"scenarios": list_scenarios(),
                                  "status": self.director.status() if self.director else None})

    async def h_event(self, request):
        try:
            ev = await request.json()
        except ValueError:
            return web.json_response({"ok": False, "error": "bad json"}, status=400)
        before = self.bad
        self.ingest(ev)
        ok = self.bad == before
        return web.json_response({"ok": ok}, status=200 if ok else 400)

    async def h_export(self, request):
        run, name = request.match_info["run"], request.match_info["name"]
        if not SAFE_NAME.match(run) or not SAFE_NAME.match(name):
            return web.json_response({"ok": False, "error": "bad name"}, status=400)
        try:
            data = await request.json()
        except ValueError:
            return web.json_response({"ok": False, "error": "bad json"}, status=400)
        path = self.runs_dir / run / f"{name}.ledger.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        return web.json_response({"ok": True, "saved": f"runs/{run}/{name}.ledger.json"})

    async def h_control(self, request):
        import aiohttp
        cmd = request.match_info["cmd"]
        if cmd not in HUB_PROXY:
            return web.json_response({"ok": False, "error": f"unknown control {cmd!r}"}, status=404)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if cmd == "export" and not body.get("runId"):
            body["runId"] = self.run_id
        url = f"http://{self.hub_http[0]}:{self.hub_http[1]}/{cmd}"
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4)) as s:
                async with s.post(url, json=body) as r:
                    return web.json_response(await r.json(), status=r.status)
        except Exception as e:  # noqa: BLE001
            return web.json_response({"ok": False, "error": f"hub unreachable: {type(e).__name__}"}, status=502)

    async def h_scenario(self, request):
        if self.director is None:
            return web.json_response({"ok": False, "error": "no director in this process"}, status=503)
        body = await request.json() if request.can_read_body else {}
        res = await self.director.start_scenario(str(body.get("name", "demo")))
        return web.json_response(res, status=200 if res.get("ok") else 400)

    async def h_scenario_stop(self, request):
        if self.director is None:
            return web.json_response({"ok": False, "error": "no director in this process"}, status=503)
        await self.director.stop_scenario()
        return web.json_response({"ok": True})

    async def h_object(self, request):
        if self.director is None:
            return web.json_response({"ok": False, "error": "no director in this process"}, status=503)
        body = await request.json()
        obj, act = body.get("obj"), body.get("action", "start")
        ok = self.director.start_object(obj) if act == "start" else self.director.stop_object(obj) if act == "stop" else False
        return web.json_response({"ok": bool(ok)})


async def _amain(args) -> None:
    obs = Observer(args.host, args.udp_port, args.http_port, Path(args.runs_dir), args.run_id,
                   parse_hostport(args.hub_http, HUB_HTTP_PORT),
                   Path(args.mission) if args.mission else None)
    await obs.start()
    print(f"[observer] UDP {args.host}:{obs.udp_port}  HTTP http://localhost:{obs.http_port}  run {obs.run_id}", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        await obs.close()


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="SwarmShield observer (events, SSE, dashboard)")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--udp-port", type=int, default=OBSERVER_UDP_PORT)
    ap.add_argument("--http-port", type=int, default=OBSERVER_HTTP_PORT)
    ap.add_argument("--runs-dir", default=str(ROOT / "runs"))
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--hub-http", default=f"127.0.0.1:{HUB_HTTP_PORT}")
    ap.add_argument("--mission", default=None)
    args = ap.parse_args(argv)
    try:
        asyncio.run(_amain(args))
    except KeyboardInterrupt:
        print("[observer] stopped", flush=True)


if __name__ == "__main__":
    main()
