"""Director: world kinematics, scenario files, scripted run against a stub hub."""
from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import web

from swarm.common import EventSender
from swarm.director import Director, WorldObject, list_scenarios, load_scenario
from swarm.tests.fixtures import dev_mission
from swarm.tests.udp_helpers import EventSink, FakeNode, wait_for

M = dev_mission()


def test_object_moves_linearly_at_5_mps_then_stops():
    o = WorldObject(M.world["objects"][0])
    assert o.pos(10_000) == (2000, 5000), "not started -> at the start point"
    o.started_ms = 1000
    assert o.pos(1000) == (2000, 5000)
    assert o.pos(1000 + 14_000) == (9000, 5000), "5 m/s: 70 m after 14 s -> x = 90 m"
    assert o.pos(1000 + 32_000) == (18_000, 5000) and o.arrived(1000 + 32_000)
    assert o.pos(1000 + 99_000) == (18_000, 5000), "then stops"
    assert all(isinstance(v, int) for v in o.pos(1000 + 7777))
    o2 = WorldObject(M.world["objects"][1])
    o2.started_ms = 0
    assert o2.pos(4000) == (4000, 3000)


def test_world_message_only_lists_started_objects():
    d = Director(M)
    assert d.world_msg()["objects"] == []
    assert d.start_object("OBJ-1") and not d.start_object("OBJ-1") and not d.start_object("NOPE")
    msg = d.world_msg()
    assert msg["hub"] == "WORLD" and [o["id"] for o in msg["objects"]] == ["OBJ-1"]
    assert set(msg["objects"][0]) == {"id", "x_cm", "y_cm"}
    d.reset_objects()
    assert d.world_msg()["objects"] == []
    d.sock.close()


def test_stop_object_freezes_position():
    d = Director(M)
    d.start_object("OBJ-1")
    d.objects["OBJ-1"].started_ms -= 4000
    assert d.stop_object("OBJ-1")
    x = d.world_msg()["objects"][0]["x_cm"]
    d.objects["OBJ-1"].started_ms -= 10_000
    assert d.world_msg()["objects"][0]["x_cm"] == x
    d.sock.close()


def test_all_shipped_scenarios_load_and_are_valid():
    names = list_scenarios()
    assert {"demo", "handoff", "kill_master", "partition", "equivocate_sim", "false_obs"} <= set(names)
    node_names = {m["name"] for _, m in M.all_members()}
    for n in names:
        sc = load_scenario(n)
        ts = [s["t"] for s in sc["timeline"]]
        assert ts == sorted(ts) and sc["timeline"]
        for s in sc["timeline"]:
            if "node" in s:
                assert s["node"] in node_names or s["node"] == "*"
            if s["do"] == "start_object":
                assert s["obj"] in {o["id"] for o in M.world["objects"]}
    demo = {(s["do"], s.get("obj") or s.get("node")) for s in load_scenario("demo")["timeline"]}
    assert {("start_object", "OBJ-1"), ("kill", "c1-d1"), ("partition", None), ("heal", None), ("start_object", "OBJ-2")} <= demo


def test_scenario_validation_errors(tmp_path):
    (tmp_path / "bad.yaml").write_text("timeline:\n  - {t: 1, do: explode}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_scenario(str(tmp_path / "bad.yaml"))
    (tmp_path / "list.yaml").write_text("- {t: 5, do: start_object, obj: OBJ-1}\n- {t: 1, do: heal}\n", encoding="utf-8")
    sc = load_scenario(str(tmp_path / "list.yaml"))
    assert [s["t"] for s in sc["timeline"]] == [1, 5], "a bare list is accepted and sorted"
    assert "no words" not in json.dumps(sc)


def test_scenario_runner_calls_hub_and_emits_scenario_events():
    async def main():
        calls = []

        async def handler(request):
            calls.append((request.path, await request.json()))
            return web.json_response({"ok": True})

        app = web.Application()
        app.add_routes([web.post("/{p}", handler)])
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = runner.addresses[0][1]
        sink = await EventSink().start()
        hub_sock = await FakeNode("0xhub", "hub", 0).start(("127.0.0.1", 1), register=False)
        d = Director(M, hub_udp=("127.0.0.1", hub_sock.transport.get_extra_info("sockname")[1]),
                     hub_http=("127.0.0.1", port), events=EventSender(("127.0.0.1", sink.port)), time_scale=50.0,
                     run_id="rid")
        d.start()
        res = await d.start_scenario("demo")
        assert res["ok"] and res["name"] == "demo"
        assert (await d.start_scenario("demo"))["ok"] is False, "one scenario at a time"
        assert await wait_for(lambda: any(e["data"].get("state") == "finished" for e in sink.named("scenario")), 8)
        paths = [p for p, _ in calls]
        assert paths == ["/kill", "/revive", "/fault", "/partition", "/heal", "/export"], paths
        assert calls[0][1] == {"node": "c1-d1"} and calls[2][1] == {"node": "c1-d3", "mode": "equivocate"}
        assert calls[3][1] == {"groups": [["cluster:1"], ["cluster:2"]]} and calls[5][1] == {"runId": "rid"}
        steps = [e["data"] for e in sink.named("scenario") if e["data"]["state"] == "step"]
        assert steps[0]["do"] == "note" and any(s["do"] == "start_object" and s["ok"] for s in steps)
        assert sink.named("scenario")[0]["data"]["state"] == "started"
        assert set(d.world_msg()["objects"][0]) == {"id", "x_cm", "y_cm"} and len(d.world_msg()["objects"]) == 2
        # WORLD reached the hub socket at ~10 Hz and `world` events reached the observer
        assert hub_sock.worlds() and sink.named("world")
        assert (await d.start_scenario("nope.yaml"))["ok"] is False
        await d.close()
        hub_sock.close()
        sink.close()
        await runner.cleanup()
    asyncio.run(main())
