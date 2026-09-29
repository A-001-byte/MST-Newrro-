"""Observer: dedupe, SSE, endpoints, metrics (synthetic events + real HTTP)."""
from __future__ import annotations

import asyncio
import contextlib
import json

import aiohttp
from aiohttp import web

import swarm.observer as obsmod
from swarm.common import make_event
from swarm.observer import Metrics, Observer, dedupe_key, pct
from swarm.tests.udp_helpers import wait_for

A = "0x" + "aa" * 20


def ev(event, data, cluster=1, name="n1", src=A, ts=1):
    return {"v": 1, "src": src, "name": name, "cluster": cluster, "ts": ts, "event": event, "data": data}


@contextlib.asynccontextmanager
async def running(tmp_path, **kw):
    kw.setdefault("metrics_interval_s", 0.2)
    obs = Observer("127.0.0.1", 0, 0, runs_dir=tmp_path / "runs", run_id="t1", **kw)
    await obs.start()
    session = aiohttp.ClientSession()
    base = f"http://127.0.0.1:{obs.http_port}"
    try:
        yield obs, session, base
    finally:
        await session.close()
        await obs.close()


def test_dedupe_first_arrival_wins_and_raw_file_keeps_everything(tmp_path):
    async def main():
        async with running(tmp_path) as (obs, s, base):
            first = ev("commit", {"height": 5, "round": 0, "latencyMs": 40}, name="n1")
            obs.ingest(first)
            obs.ingest(ev("commit", {"height": 5, "round": 0, "latencyMs": 90}, name="n2"))   # same (cluster, height)
            obs.ingest(ev("commit", {"height": 5, "round": 0, "latencyMs": 90}, cluster=2, name="n9"))  # other cluster
            obs.ingest(ev("commit", {"height": 6, "round": 0, "latencyMs": 50}, name="n2"))
            assert [e["data"]["height"] for e in obs.ring if e["event"] == "commit"] == [5, 5, 6]
            assert obs.dup_count == 1 and obs.raw_count == 4
            assert first["rx"] > 0 and first["seq"] == 1
            lines = [json.loads(x) for x in (tmp_path / "runs" / "t1" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
            assert len(lines) == 4 and [x["dup"] for x in lines] == [False, True, False, False]
    asyncio.run(main())


def test_dedupe_keys_follow_11_7():
    assert dedupe_key(ev("xc_sent", {"dst": 2, "seq": 1})) == ("xc_sent", 1, 2, 1)
    assert dedupe_key(ev("xc_verified", {"src": 1, "seq": 1}, cluster=2)) == ("xc_verified", 2, 1, 1)
    assert dedupe_key(ev("revoked", {"member": A})) == ("revoked", 1, A)
    assert dedupe_key(ev("node_state", {})) is None and dedupe_key(ev("fault_injected", {"node": "*"})) is None
    assert dedupe_key(ev("master_changed", {"height": 3, "master": A})) == ("master_changed", 1, 3, A)


def test_malformed_events_are_counted_not_fatal(tmp_path):
    async def main():
        async with running(tmp_path) as (obs, s, base):
            obs.ingest({"v": 2, "event": "x", "data": {}})
            obs.ingest({"v": 1, "event": 5})
            obs.ingest("nope")
            obs.ingest({"v": 1, "event": "x", "data": []})
            assert obs.bad == 4 and obs.raw_count == 0
            async with s.post(base + "/event", data="not json") as r:
                assert r.status == 400
            async with s.post(base + "/event", json={"v": 1, "event": "enrollment", "src": "chain", "name": None,
                                                    "cluster": None, "ts": 1, "data": {"txHash": "0x1"}}) as r:
                assert (await r.json())["ok"]
            assert any(e["event"] == "enrollment" for e in obs.ring)
    asyncio.run(main())


def test_sse_replays_tail_streams_live_and_heartbeats(tmp_path, monkeypatch):
    monkeypatch.setattr(obsmod, "HEARTBEAT_S", 0.4)
    async def main():
        async with running(tmp_path, metrics_interval_s=100) as (obs, s, base):
            for h in range(1, 6):
                obs.ingest(ev("commit", {"height": h, "round": 0, "latencyMs": 1}))
            obs.ingest(ev("node_state", {"name": "n1", "height": 5}))
            async with s.get(base + "/events") as r:
                assert r.status == 200
                assert r.headers["Content-Type"].startswith("text/event-stream") and r.headers["Cache-Control"] == "no-cache"
                got, live_seen, hb = [], False, False
                async def reader():
                    nonlocal live_seen, hb
                    async for raw in r.content:
                        line = raw.decode().rstrip("\n")
                        if line.startswith("data: "):
                            e = json.loads(line[6:])
                            got.append(e)
                            if e["event"] == "vote":
                                live_seen = True
                        elif line.startswith(": heartbeat"):
                            hb = True
                        if live_seen and hb:
                            return
                task = asyncio.create_task(reader())
                await asyncio.sleep(0.2)
                obs.ingest(ev("vote", {"type": "PREVOTE", "height": 6, "round": 0, "voter": A, "subject": "0x1"}))
                await asyncio.wait_for(task, 5)
                names = [e["event"] for e in got]
                assert names.count("commit") == 5 and "node_state" in names and live_seen and hb
                assert all("rx" in e and "seq" in e for e in got)
    asyncio.run(main())


def test_sse_replay_is_capped_at_500(tmp_path):
    async def main():
        async with running(tmp_path) as (obs, s, base):
            for h in range(1, 801):
                obs.ingest(ev("commit", {"height": h, "round": 0, "latencyMs": 1}))
            assert len(obs.replay_events()) == 500 and obs.replay_events()[-1]["data"]["height"] == 800
            assert len(obs.ring) == 800
    asyncio.run(main())


def test_http_endpoints(tmp_path):
    async def main():
        mission = tmp_path / "m.json"
        mission.write_text(json.dumps({"missionName": "x", "clusters": [{"members": [{"name": "a", "priv": "SECRET"}]}]}), encoding="utf-8")
        dash = tmp_path / "dashboard"
        dash.mkdir()
        (dash / "index.html").write_text("<html>hi</html>", encoding="utf-8")
        (dash / "app.js").write_text("//js", encoding="utf-8")
        abi = tmp_path / "abi"
        abi.mkdir()
        (abi / "SwarmRegistry.json").write_text('{"abi":[]}', encoding="utf-8")
        async with running(tmp_path, mission_path=mission, dashboard_dir=dash, abi_dir=abi) as (obs, s, base):
            async with s.get(base + "/") as r:
                assert r.status == 200 and "hi" in await r.text()
            async with s.get(base + "/app.js") as r:
                assert r.status == 200
            async with s.get(base + "/style.css") as r:
                assert r.status == 404
            async with s.get(base + "/config/mission.json") as r:
                body = await r.text()
                assert r.status == 200 and "SECRET" not in body and "missionName" in body
            async with s.get(base + "/deployments") as r:
                assert r.status == 200 and await r.json() == {}, "absent file -> empty object"
            async with s.get(base + "/abi/SwarmRegistry.json") as r:
                assert r.status == 200
            async with s.get(base + "/abi/Missing.json") as r:
                assert r.status == 404
            async with s.get(base + "/abi/..%2Fsecret.json") as r:
                assert r.status == 404
            async with s.get(base + "/mst/setup.html") as r:
                assert r.status == 404, "no dashboard/mst in this workspace: graceful 404, no crash"
            (dash / "mst").mkdir()
            (dash / "mst" / "setup.html").write_text("SETUP", encoding="utf-8")
            async with s.get(base + "/mst/setup.html") as r:
                assert r.status == 200 and await r.text() == "SETUP"
            async with s.get(base + "/mst/../index.html") as r:
                assert r.status in (404, 400)
            async with s.get(base + "/state") as r:
                st = await r.json()
                assert st["runId"] == "t1" and "metrics" in st and st["mock"] is False
            async with s.get(base + "/scenarios") as r:
                assert "demo" in (await r.json())["scenarios"]
            # export upload saved as runs/<runId>/<name>.ledger.json
            async with s.post(base + "/runs/rX/export/c1-d1", json={"blocks": [1, 2]}) as r:
                assert (await r.json())["ok"]
            assert json.loads((tmp_path / "runs" / "rX" / "c1-d1.ledger.json").read_text(encoding="utf-8")) == {"blocks": [1, 2]}
            async with s.post(base + "/runs/../export/x", json={}) as r:
                assert r.status in (400, 404)
            async with s.post(base + "/runs/rX/export/..%2Fevil", json={}) as r:
                assert r.status in (400, 404)
            assert not (tmp_path / "evil.ledger.json").exists()
    asyncio.run(main())


def test_control_is_proxied_to_the_hub_and_scenario_goes_to_director(tmp_path):
    async def main():
        calls = []

        async def hub(request):
            calls.append((request.path, await request.json()))
            return web.json_response({"ok": True, "echo": request.path})

        happ = web.Application()
        happ.add_routes([web.post("/{p}", hub)])
        runner = web.AppRunner(happ)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        hub_port = runner.addresses[0][1]

        class FakeDirector:
            started = []

            async def start_scenario(self, name):
                self.started.append(name)
                return {"ok": True, "name": name}

            async def stop_scenario(self):
                pass

            def start_object(self, o):
                self.started.append("obj:" + o)
                return True

            def status(self):
                return {"running": False}

        d = FakeDirector()
        async with running(tmp_path, hub_http=("127.0.0.1", hub_port), director=d) as (obs, s, base):
            async with s.post(base + "/control/kill", json={"node": "c1-d1"}) as r:
                assert r.status == 200 and (await r.json())["echo"] == "/kill"
            async with s.post(base + "/control/partition", json={"groups": [["cluster:1"], ["cluster:2"]]}) as r:
                assert (await r.json())["ok"]
            async with s.post(base + "/control/export", json={}) as r:
                assert (await r.json())["ok"]
            assert calls[0] == ("/kill", {"node": "c1-d1"}) and calls[2] == ("/export", {"runId": "t1"})
            async with s.post(base + "/control/rm-rf", json={}) as r:
                assert r.status == 404
            async with s.post(base + "/control/scenario", json={"name": "demo"}) as r:
                assert (await r.json()) == {"ok": True, "name": "demo"}
            async with s.post(base + "/control/object", json={"obj": "OBJ-1", "action": "start"}) as r:
                assert (await r.json())["ok"]
            assert d.started == ["demo", "obj:OBJ-1"]
        # hub down -> 502 with a message, never a crash
        async with running(tmp_path, hub_http=("127.0.0.1", 1)) as (obs, s, base):
            async with s.post(base + "/control/heal", json={}) as r:
                assert r.status == 502
            async with s.post(base + "/control/scenario", json={"name": "demo"}) as r:
                assert r.status == 503
        await runner.cleanup()
    asyncio.run(main())


def test_metrics_from_observer_receive_clock():
    m = Metrics()
    t = 10_000

    def feed(e, data, cl=1, rx=0):
        m.feed(ev(e, data, cluster=cl), rx)
    for h, rx in ((1, 0), (2, 1000), (3, 2100), (4, 3000)):
        feed("commit", {"height": h, "round": 0 if h != 3 else 1, "latencyMs": 100 + h * 10}, 1, rx)
    feed("commit", {"height": 1, "round": 0, "latencyMs": 5}, 2, 0)
    feed("xc_sent", {"dst": 2, "seq": 1, "obj": "OBJ-1"}, 1, t)
    feed("ownership", {"obj": "OBJ-1", "cluster": 2, "via": "XC_RECV"}, 2, t + 800)
    feed("xc_acked", {"dst": 2, "seq": 1}, 1, t + 1500)
    feed("fault_injected", {"node": A, "mode": "kill", "name": "c1-d1"}, None, 20_000)
    feed("master_changed", {"prev": A, "master": "0x" + "bb" * 20, "height": 9}, 1, 24_500)
    feed("evidence", {"offender": "0x1"}, 1, 30_000)
    feed("revoked", {"member": "0x1"}, 1, 32_000)
    feed("fault_injected", {"node": "*", "mode": "partition"}, None, 40_000)
    feed("contingency", {"mode": "ENTER", "peer": 2}, 1, 46_000)
    feed("contingency", {"mode": "ENTER", "peer": 1}, 2, 47_000)
    feed("fault_injected", {"node": "*", "mode": "heal"}, None, 60_000)
    feed("contingency", {"mode": "EXIT", "peer": 2}, 1, 63_000)
    m.feed(ev("hub_stats", {"byType": {"PROPOSAL": {"count": 10, "bytes": 1000}, "PREVOTE": {"count": 40, "bytes": 4000},
                                        "HELLO": {"count": 999, "bytes": 99999}}}), 1)
    s = m.snapshot()
    c1 = s["clusters"]["1"]
    assert c1["commits"] == 4 and c1["commit_latency_ms"]["p50"] == 120 and c1["commit_latency_ms"]["p95"] == 140
    assert c1["block_interval_ms"]["p50"] == 1000 and c1["rounds"]["max"] == 1
    assert s["handoff_recv_ms"]["p50"] == 800 and s["handoff_ack_ms"]["p50"] == 1500
    assert s["failover_ms"]["p50"] == 4500 and s["revocation_ms"]["p50"] == 2000
    assert s["contingency_enter_ms"]["n"] == 2 and s["contingency_enter_ms"]["p50"] in (6000, 7000)
    assert s["contingency_exit_ms"]["p50"] == 3000
    assert s["msgs_per_commit"] == 10 and s["bytes_per_commit"] == 1000, "HELLO/STATUS excluded, 5 commits"
    assert s["clock"] == "observer-receive" and s["mock"] is False
    assert pct([], 50) is None and pct([5], 95) == 5 and pct(list(range(1, 101)), 95) == 95


def test_udp_ingest_end_to_end(tmp_path):
    async def main():
        async with running(tmp_path) as (obs, s, base):
            from swarm.common import EventSender
            snd = EventSender(("127.0.0.1", obs.udp_port))
            snd.emit(A, "n1", 1, "commit", {"height": 1, "round": 0, "latencyMs": 1, "mock": True})
            snd.send(make_event("hub", None, None, "topology", {"nodes": []}))
            assert await wait_for(lambda: obs.raw_count == 2, 3)
            assert obs.mock is True and (await (await s.get(base + "/state")).json())["mock"] is True
            assert await wait_for(lambda: "metrics" in obs.latest, 3), "observer emits metrics periodically"
            snd.close()
    asyncio.run(main())
