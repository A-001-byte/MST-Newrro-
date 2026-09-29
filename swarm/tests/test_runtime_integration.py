"""Real UDP: hub + 10 mock drones (two node groups) + stub observer. Fast timing profile so it runs in seconds."""
from __future__ import annotations

import asyncio
import contextlib
import json

from aiohttp import web

from swarm.common import EventSender
from swarm.hub import Hub
from swarm.node import Node
from swarm.tests.fixtures import dev_mission
from swarm.tests.udp_helpers import EventSink, FakeNode, wait_for

FAST = {"COMMIT_INTERVAL_MS": 250, "TIMEOUT_PROPOSE_MS": 700, "TIMEOUT_PREVOTE_MS": 500, "TIMEOUT_PRECOMMIT_MS": 500,
        "REGISTER_INTERVAL_MS": 500, "HELLO_INTERVAL_MS": 300, "ROLE_SILENCE_MS": 1200, "PEER_SILENCE_MS": 1500,
        "XC_TIMEOUT_MS": 4000, "XC_RETRY_MS": 600, "OBS_INTERVAL_MS": 250}


@contextlib.asynccontextmanager
async def cluster(tmp_path, groups=(("A", "B", "board"), ("C",))):
    mission = dev_mission(FAST)
    sink = await EventSink().start()
    uploads: list[tuple[str, dict]] = []

    async def upload(request):
        uploads.append((request.match_info["run"] + "/" + request.match_info["name"], await request.json()))
        return web.json_response({"ok": True})

    app = web.Application(client_max_size=64 * 1024 * 1024)
    app.add_routes([web.post("/runs/{run}/export/{name}", upload)])
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    obs_http = ("127.0.0.1", runner.addresses[0][1])
    hub = Hub(host="127.0.0.1", udp_port=0, http_port=0, observer=("127.0.0.1", sink.port), stats_interval_s=0)
    await hub.start()
    sender = EventSender(("127.0.0.1", sink.port))
    nodes = []
    for g in groups:
        for cl, m in mission.members_of_host(",".join(g)):
            nodes.append(Node(mission, cl, m, ("127.0.0.1", hub.udp_port), sender, "mock", "itest", tmp_path / "runs",
                              obs_http))
    for n in nodes:
        await n.start()

    class R:
        pass
    r = R()
    r.mission, r.sink, r.hub, r.nodes, r.uploads, r.by = mission, sink, hub, nodes, uploads, {n.name: n for n in nodes}
    try:
        yield r
    finally:
        for n in nodes:
            await n.stop(export=False)
        await hub.close()
        await runner.cleanup()
        sender.close()
        sink.close()


def dedup(sink, event, key):
    seen = {}
    for e, _ in sink.events:
        if e["event"] == event:
            seen.setdefault(key(e), e)
    return seen


def test_ten_mock_drones_commit_about_once_per_interval_and_export(tmp_path):
    async def main():
        async with cluster(tmp_path) as r:
            assert len(r.nodes) == 10
            assert await wait_for(lambda: len(dedup(r.sink, "node_up", lambda e: e["name"])) == 10, 5)
            assert await wait_for(lambda: len(dedup(r.sink, "commit", lambda e: (e["cluster"], e["data"]["height"]))) >= 16, 12)
            commits = dedup(r.sink, "commit", lambda e: (e["cluster"], e["data"]["height"]))
            per = {c: sorted(h for (cc, h) in commits if cc == c) for c in (1, 2)}
            assert all(len(v) >= 5 and v == list(range(1, len(v) + 1)) for v in per.values()), per
            assert all(e["data"]["mock"] is True for e in commits.values())
            states = {e["name"]: e["data"] for e, _ in r.sink.events if e["event"] == "node_state"}
            assert len(states) == 10 and all(s["mock"] for s in states.values())
            topo = r.hub.topology()
            assert sum(1 for n in topo["nodes"] if n["light"]) == 1 and len(topo["nodes"]) == 10
            # export: CONTROL over the hub -> files on disk AND uploaded to the observer
            import aiohttp
            async with aiohttp.ClientSession() as s:
                async with s.post(f"http://127.0.0.1:{r.hub.http_port}/export", json={"runId": "itest-export"}) as resp:
                    assert (await resp.json())["ok"]
            assert await wait_for(lambda: len(r.uploads) >= 9, 5)
            f = tmp_path / "runs" / "itest-export" / "c1-d2.ledger.json"
            assert f.exists()
            data = json.loads(f.read_text(encoding="utf-8"))
            assert data["cluster"] == 1 and len(data["blocks"]) >= 3 and data["mock"] is True
            assert {name.split("/")[1] for name, _ in r.uploads} >= {"c1-d1", "c1-d2", "c2-d1", "c2-d5"}
    asyncio.run(main())


def test_object_crossing_the_line_hands_off_end_to_end(tmp_path):
    async def main():
        async with cluster(tmp_path) as r:
            director = await FakeNode("0xdd", "director", 0).start(("127.0.0.1", r.hub.udp_port), register=False)
            try:
                async def world():
                    while True:
                        director.raw({"hub": "WORLD", "t_ms": 0, "objects": [{"id": "OBJ-1", "x_cm": 9500, "y_cm": 5000}]})
                        await asyncio.sleep(0.1)
                task = asyncio.create_task(world())
                names = ("xc_sent", "xc_verified", "ownership", "xc_acked")

                def have(n):
                    return any(e["event"] == n and (n != "ownership" or (e["data"]["cluster"] == 2 and e["data"]["obj"] == "OBJ-1"))
                               for e, _ in r.sink.events)
                ok = await wait_for(lambda: all(have(n) for n in names), 25)
                task.cancel()
                assert ok, {n: have(n) for n in names}
                first = {n: min(e["ts"] for e, _ in r.sink.events if e["event"] == n and
                                (n != "ownership" or e["data"]["cluster"] == 2)) for n in names}
                assert first["xc_sent"] <= first["xc_verified"] <= first["ownership"] <= first["xc_acked"] + 1000
                assert any(e["event"] == "track" and e["data"]["obj"] == "OBJ-1" for e, _ in r.sink.events)
                # every node applies the block on its own schedule: wait for custody instead of assuming it
                assert await wait_for(lambda: all(r.by[n].app.custody["OBJ-1"] == 2 for n in ("c1-d2", "c1-d4", "c2-d3", "c2-d5")), 5)
                sent = [e for e, _ in r.sink.events if e["event"] == "xc_sent"]
                assert len({(e["data"]["dst"], e["data"]["seq"]) for e in sent}) == 1, "exactly one handoff for OBJ-1"
                assert not [e for e, _ in r.sink.events if e["event"] == "xc_rejected"]
            finally:
                director.close()
    asyncio.run(main())


def test_hub_kill_of_master_causes_failover_over_real_udp(tmp_path):
    async def main():
        async with cluster(tmp_path) as r:
            assert await wait_for(lambda: r.by["c1-d2"].engine.ledger.height >= 4, 8)
            import aiohttp
            async with aiohttp.ClientSession() as s:
                async with s.post(f"http://127.0.0.1:{r.hub.http_port}/kill", json={"node": "c1-d1"}) as resp:
                    assert (await resp.json())["ok"]
            assert await wait_for(lambda: any(e["event"] == "master_changed" and e["cluster"] == 1
                                              for e, _ in r.sink.events), 10)
            h = r.by["c1-d2"].engine.ledger.height
            assert await wait_for(lambda: r.by["c1-d2"].engine.ledger.height >= h + 3, 8)
            assert any(e["event"] == "fault_injected" and e["data"]["mode"] == "kill" for e, _ in r.sink.events)
    asyncio.run(main())


def test_shutdown_of_node_host_exports_every_ledger(tmp_path):
    """Ctrl+C in node_host cancels its main task; the finally block must write runs/<runId>/<name>.ledger.json."""
    from swarm import node_host

    async def main():
        async with cluster(tmp_path, groups=(("board",),)) as r:      # r.nodes = just the board; add real ones below
            mission = r.mission
            sender = EventSender(("127.0.0.1", r.sink.port))
            nodes = [Node(mission, cl, m, ("127.0.0.1", r.hub.udp_port), sender, "mock", "itest-stop", tmp_path / "runs")
                     for cl, m in mission.members_of_host("C")]
            task = asyncio.create_task(node_host._amain(nodes))
            await asyncio.sleep(2.0)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            files = sorted(p.name for p in (tmp_path / "runs" / "itest-stop").glob("*.ledger.json"))
            assert files == ["c2-d1.ledger.json", "c2-d2.ledger.json", "c2-d3.ledger.json", "c2-d4.ledger.json"], files
            sender.close()
    asyncio.run(main())
