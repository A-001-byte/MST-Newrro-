"""B1 hub tests. Real local UDP sockets; hub bound to 127.0.0.1 on ephemeral ports."""
from __future__ import annotations

import asyncio
import contextlib
import json

import aiohttp

from swarm.hub import MAX_LIGHT_DATAGRAM, Hub, light_env
from swarm.tests.udp_helpers import EventSink, FakeNode, settle, wait_for

A1, A2, A3, ALIGHT = ("0x" + c * 40 for c in "1234")
H = "0x" + "ab" * 32


def env(etype: str, cluster: int = 1, frm: str = A1, body: dict | None = None, to: str = "*", **kw) -> dict:
    return {"v": 1, "type": etype, "mission": H, "cluster": cluster, "from": frm, "to": to, "height": 5,
            "round": 0, "subject": None, "sig": None, "body": body or {}, **kw}


def header(cluster: int = 1, height: int = 5) -> dict:
    return {"mission": H, "cluster": cluster, "height": height, "prevHash": H, "txRoot": H,
            "proposer": "0x" + "cd" * 20, "timestampMs": 1790000000000}


def qc(n_sigs: int = 5, round_: int = 0) -> dict:
    sigs = [{"signer": "0x" + f"{i:02x}" * 20, "r": "0x" + "11" * 32, "s": "0x" + "22" * 32, "v": 27}
            for i in range(1, n_sigs + 1)]
    return {"round": round_, "sigs": sigs}


@contextlib.asynccontextmanager
async def rig(offline_ms: int = 6000):
    sink = await EventSink().start()
    hub = Hub(host="127.0.0.1", udp_port=0, http_port=0, observer=("127.0.0.1", sink.port), offline_ms=offline_ms,
              stats_interval_s=0, seed=1)
    await hub.start()
    nodes: list[FakeNode] = []
    session = aiohttp.ClientSession()

    class Rig:
        pass

    r = Rig()
    r.hub, r.sink, r.nodes = hub, sink, nodes
    r.base = f"http://127.0.0.1:{hub.http_port}"

    async def add(addr, name, cluster, light=False):
        n = await FakeNode(addr, name, cluster, light).start(("127.0.0.1", hub.udp_port))
        nodes.append(n)
        return n

    async def post(path, body=None):
        async with session.post(r.base + path, json=body or {}) as resp:
            return resp.status, await resp.json()

    async def get(path):
        async with session.get(r.base + path) as resp:
            return await resp.json()

    r.add, r.post, r.get = add, post, get
    try:
        yield r
    finally:
        await session.close()
        for n in nodes:
            n.close()
        await hub.close()
        sink.close()


def run(coro):
    asyncio.run(coro)


def test_register_and_topology():
    async def main():
        async with rig() as r:
            await r.add(A1, "c1-d1", 1)
            await r.add(A2, "c1-d2", 1)
            await r.add(A3, "c2-d1", 2)
            await settle()
            topo = await r.get("/topology")
            assert {n["name"] for n in topo["nodes"]} == {"c1-d1", "c1-d2", "c2-d1"}
            assert all(n["online"] and not n["killed"] and n["dropRate"] == 0 for n in topo["nodes"])
            assert topo["partition"] is None and topo["globalDropRate"] == 0
            assert any(e["data"]["nodes"] for e in r.sink.named("topology"))
    run(main())


def test_broadcast_never_echoes_to_sender():
    async def main():
        async with rig() as r:
            n1, n2, n3 = [await r.add(a, f"n{i}", 1) for i, a in enumerate((A1, A2, A3))]
            await settle()
            n1.send("*", env("PREVOTE"))
            assert await wait_for(lambda: n2.delivered("PREVOTE") and n3.delivered("PREVOTE"))
            await settle()
            assert n1.delivered() == []
            assert len(n2.delivered("PREVOTE")) == 1
    run(main())


def test_cluster_and_unicast_routing():
    async def main():
        async with rig() as r:
            n1 = await r.add(A1, "c1-d1", 1)
            n2 = await r.add(A2, "c1-d2", 1)
            n3 = await r.add(A3, "c2-d1", 2)
            await settle()
            n1.send("cluster:2", env("TX"))
            assert await wait_for(lambda: n3.delivered("TX"))
            await settle()
            assert n2.delivered("TX") == [] and n1.delivered() == []
            n1.send("cluster:1", env("HELLO"))
            assert await wait_for(lambda: n2.delivered("HELLO"))
            await settle()
            assert n3.delivered("HELLO") == [] and n1.delivered() == []
            n3.send(A2, env("SYNC_REQ", cluster=2, frm=A3))
            assert await wait_for(lambda: n2.delivered("SYNC_REQ"))
            await settle()
            assert n1.delivered("SYNC_REQ") == []
    run(main())


def test_partition_blocks_cross_group_and_heal_restores():
    async def main():
        async with rig() as r:
            n1 = await r.add(A1, "c1-d1", 1)
            n2 = await r.add(A2, "c1-d2", 1)
            n3 = await r.add(A3, "c2-d1", 2)
            await settle()
            st, body = await r.post("/partition", {"groups": [["cluster:1"], ["cluster:2"]]})
            assert st == 200 and body["ok"] is True
            n1.send("*", env("HELLO"))
            assert await wait_for(lambda: n2.delivered("HELLO"))
            await settle()
            assert n3.delivered("HELLO") == []
            assert r.hub.dropped["partition"] >= 1
            assert (await r.get("/topology"))["partition"] == [["cluster:1"], ["cluster:2"]]
            await r.post("/heal")
            n1.send("*", env("HELLO", frm=A1))
            assert await wait_for(lambda: n3.delivered("HELLO"))
            assert (await r.get("/topology"))["partition"] is None
    run(main())


def test_partition_unlisted_nodes_form_one_extra_group():
    async def main():
        async with rig() as r:
            n1 = await r.add(A1, "c1-d1", 1)
            n2 = await r.add(A2, "c1-d2", 1)
            n3 = await r.add(A3, "c2-d1", 2)
            n4 = await r.add(ALIGHT, "c3-d1", 3)
            await settle()
            await r.post("/partition", {"groups": [[A1]]})
            n1.send("*", env("HELLO"))
            await settle(0.3)
            assert not (n2.delivered("HELLO") or n3.delivered("HELLO") or n4.delivered("HELLO"))
            n2.send("*", env("HELLO", frm=A2))
            assert await wait_for(lambda: n3.delivered("HELLO") and n4.delivered("HELLO"))
            await settle()
            assert n1.delivered("HELLO") == []
    run(main())


def test_kill_blocks_both_directions_and_revive():
    async def main():
        async with rig() as r:
            n1 = await r.add(A1, "c1-d1", 1)
            n2 = await r.add(A2, "c1-d2", 1)
            await settle()
            st, body = await r.post("/kill", {"node": A1})
            assert st == 200 and body["killed"] is True
            n2.send("*", env("HELLO", frm=A2))          # towards the dead node
            n1.send("*", env("HELLO", frm=A1))          # from the dead node
            await settle(0.3)
            assert n1.delivered() == [] and n2.delivered() == []
            assert r.hub.dropped["dst_killed"] >= 1 and r.hub.dropped["src_killed"] >= 1
            await r.post("/revive", {"node": "c1-d1"})  # by name
            n2.send("*", env("HELLO", frm=A2))
            assert await wait_for(lambda: n1.delivered("HELLO"))
            n1.send("*", env("HELLO", frm=A1))
            assert await wait_for(lambda: n2.delivered("HELLO"))
    run(main())


def test_drop_rate_one_drops_everything():
    async def main():
        async with rig() as r:
            n1 = await r.add(A1, "c1-d1", 1)
            n2 = await r.add(A2, "c1-d2", 1)
            await settle()
            await r.post("/drop", {"node": "*", "rate": 1.0})
            for _ in range(20):
                n1.send("*", env("PREVOTE"))
            await settle(0.3)
            assert n2.delivered() == [] and r.hub.dropped["random_drop"] == 20
            await r.post("/drop", {"node": "*", "rate": 0.0})
            await r.post("/drop", {"node": A2, "rate": 1.0})     # per-node: traffic to/from A2
            n1.send("*", env("PREVOTE"))
            n2.send("*", env("PREVOTE", frm=A2))
            await settle(0.3)
            assert n2.delivered() == [] and n1.delivered() == []
            st, _ = await r.post("/drop", {"node": "*", "rate": 2})
            assert st == 400
    run(main())


def test_light_node_gets_stripped_proposal_and_never_tx_or_xc():
    async def main():
        async with rig() as r:
            n1 = await r.add(A1, "c1-d1", 1)
            light = await r.add(ALIGHT, "c1-board", 1, light=True)
            await settle()
            proposal = env("PROPOSAL", body={"header": header(), "txs": [{"tx": {"type": "OBS", "x": i}, "sig": None} for i in range(30)],
                                            "polRound": -1}, subject=H)
            n1.send("*", proposal)
            assert await wait_for(lambda: light.delivered("PROPOSAL"))
            got = light.delivered("PROPOSAL")[0]
            assert "txs" not in got["body"] and got["body"]["polRound"] == -1 and got["body"]["header"]["height"] == 5
            assert proposal["body"]["txs"]  # original untouched
            for t in ("TX", "XC_PACKET", "XC_ACK", "HELLO", "EVIDENCE", "HISTORY", "SYNC_REQ", "SYNC_RESP", "SELF_REVOKE"):
                n1.send("*", env(t))
            n1.send("*", env("PREVOTE", subject=H))
            n1.send("*", env("STATUS", body={"step": "propose", "lastCommit": 4}))
            assert await wait_for(lambda: light.delivered("STATUS") and light.delivered("PREVOTE"))
            await settle()
            types = {e["type"] for e in light.delivered()}
            assert types <= {"PROPOSAL", "PREVOTE", "PRECOMMIT", "COMMIT", "STATUS"}
            assert r.hub.stripped_for_light == 1
            assert r.hub.dropped["light_filter"] == 9
    run(main())


def test_light_node_ignores_other_cluster_consensus_traffic():
    async def main():
        async with rig() as r:
            n3 = await r.add(A3, "c2-d1", 2)
            light = await r.add(ALIGHT, "c1-board", 1, light=True)
            await settle()
            n3.send("*", env("PREVOTE", cluster=2, frm=A3))
            await settle(0.3)
            assert light.delivered() == []
    run(main())


def test_commit_with_five_signature_qc_becomes_light_commit_under_1400():
    async def main():
        async with rig() as r:
            n1 = await r.add(A1, "c1-d1", 1)
            light = await r.add(ALIGHT, "c1-board", 1, light=True)
            full = await r.add(A2, "c1-d2", 1)
            await settle()
            body = {"header": header(), "txHashes": [H] * 10, "qc": qc(5, round_=2),
                    "revoked": ["0x" + "77" * 20], "master": "0x" + "88" * 20}
            n1.send("*", env("COMMIT", body=body))
            assert await wait_for(lambda: light.delivered("COMMIT") and full.delivered("COMMIT"))
            lbody = light.delivered("COMMIT")[0]["body"]
            assert set(lbody) == {"header", "qc", "revoked", "master"}
            assert lbody["qc"] == {"round": 2, "signers": [s["signer"] for s in body["qc"]["sigs"]]}
            assert "txHashes" not in lbody and "sigs" not in lbody["qc"]
            size = next(sz for m, sz in light.inbox if m.get("hub") == "DELIVER")
            assert size <= MAX_LIGHT_DATAGRAM, size
            assert lbody["revoked"] == ["0x" + "77" * 20] and lbody["master"] == "0x" + "88" * 20
            assert "sigs" in full.delivered("COMMIT")[0]["body"]["qc"]  # full nodes get the full COMMIT
    run(main())


def test_oversize_to_light_is_dropped_and_counted():
    async def main():
        async with rig() as r:
            n1 = await r.add(A1, "c1-d1", 1)
            light = await r.add(ALIGHT, "c1-board", 1, light=True)
            full = await r.add(A2, "c1-d2", 1)
            await settle()
            big = header()
            big["padding"] = "x" * 1500
            n1.send("*", env("PREVOTE", body=big))
            assert await wait_for(lambda: full.delivered("PREVOTE"))
            await settle()
            assert light.delivered() == []
            assert r.hub.oversize_dropped == 1 and (await r.get("/stats"))["oversizeDropped"] == 1
    run(main())


def test_world_reaches_full_nodes_during_partition_never_light():
    async def main():
        async with rig() as r:
            n1 = await r.add(A1, "c1-d1", 1)
            n3 = await r.add(A3, "c2-d1", 2)
            dead = await r.add(A2, "c1-d2", 1)
            light = await r.add(ALIGHT, "c1-board", 1, light=True)
            director = await FakeNode("0xdd", "director", 0).start(("127.0.0.1", r.hub.udp_port), register=False)
            r.nodes.append(director)
            await settle()
            await r.post("/partition", {"groups": [["cluster:1"], ["cluster:2"]]})
            await r.post("/kill", {"node": A2})
            director.raw({"hub": "WORLD", "t_ms": 100, "objects": [{"id": "OBJ-1", "x_cm": 2000, "y_cm": 5000}]})
            assert await wait_for(lambda: n1.worlds() and n3.worlds())
            await settle()
            assert n1.worlds()[0]["objects"][0]["id"] == "OBJ-1" and n1.worlds()[0]["t_ms"] == 100
            assert light.worlds() == [] and dead.worlds() == []
    run(main())


def test_fault_injecting_posts_emit_fault_injected_and_control_reaches_node():
    async def main():
        async with rig() as r:
            n1 = await r.add(A1, "c1-d1", 1)
            await r.add(A2, "c1-d2", 1)
            await settle()
            await r.post("/kill", {"node": A1})           # even a killed node still gets CONTROL (out of band)
            calls = [("/partition", {"groups": [["cluster:1"], ["cluster:2"]]}, "partition"),
                     ("/heal", {}, "heal"),
                     ("/drop", {"node": "*", "rate": 0.2}, "drop"),
                     ("/kill", {"node": A2}, "kill"),
                     ("/revive", {"node": A2}, "revive"),
                     ("/fault", {"node": A1, "mode": "equivocate"}, "fault:equivocate"),
                     ("/rogue", {"node": A1}, "rogue")]
            for path, body, mode in calls:
                st, resp = await r.post(path, body)
                assert st == 200 and resp["ok"] is True, (path, resp)
                await settle(0.05)
                got = [e["data"] for e in r.sink.named("fault_injected") if e["data"]["mode"] == mode]
                assert got, f"{path} did not emit fault_injected {mode}"
            kill_ev = [e["data"] for e in r.sink.named("fault_injected") if e["data"]["mode"] == "kill"]
            assert kill_ev[-1]["node"] == A2 and kill_ev[-1]["name"] == "c1-d2"
            assert next(e["data"] for e in r.sink.named("fault_injected") if e["data"]["mode"] == "heal")["node"] == "*"
            assert len(r.sink.named("topology")) >= len(calls)
            assert await wait_for(lambda: len(n1.controls()) >= 2)
            assert {"hub": "CONTROL", "cmd": "fault", "mode": "equivocate"} in n1.controls()
            assert {"hub": "CONTROL", "cmd": "rogue"} in n1.controls()
            # export: CONTROL to every node, topology event, but it injects no fault
            n_faults = len(r.sink.named("fault_injected"))
            st, resp = await r.post("/export", {"runId": "run-x"})
            assert st == 200 and resp["ok"] and resp["sent"] == 2
            assert await wait_for(lambda: {"hub": "CONTROL", "cmd": "export", "runId": "run-x"} in n1.controls())
            await settle(0.1)
            assert len(r.sink.named("fault_injected")) == n_faults
            st, _ = await r.post("/kill", {"node": "nobody"})
            assert st == 400
            st, _ = await r.post("/fault", {"node": A1, "mode": "explode"})
            assert st == 400
    run(main())


def test_stats_counts_types_and_bytes():
    async def main():
        async with rig() as r:
            n1 = await r.add(A1, "c1-d1", 1)
            await r.add(A2, "c1-d2", 1)
            await settle()
            for _ in range(3):
                n1.send("*", env("PREVOTE"))
            n1.raw({"hub": "SEND", "to": "*", "env": "not-a-dict"})
            n1.transport.sendto(b"\xff not json", n1.hub_addr)
            await settle(0.3)
            s = await r.get("/stats")
            assert s["byType"]["PREVOTE"]["count"] == 3 and s["byType"]["PREVOTE"]["bytes"] > 0
            assert s["dropped"]["bad_json"] == 1 and s["dropped"]["bad_send"] == 1
            assert s["deliveredTotal"] == 3 and s["sentTotal"] == 3
    run(main())


def test_node_goes_offline_after_silence_and_back_on_register():
    async def main():
        async with rig(offline_ms=400) as r:
            n1 = await r.add(A1, "c1-d1", 1)
            n2 = await r.add(A2, "c1-d2", 1)
            await settle()
            assert all(n["online"] for n in (await r.get("/topology"))["nodes"])
            await asyncio.sleep(0.9)
            assert not any(n["online"] for n in (await r.get("/topology"))["nodes"])
            n1.register()
            await settle()
            n2.send("*", env("HELLO", frm=A2))          # n2 is offline: it is not delivered to, but n1 receives it
            assert await wait_for(lambda: n1.delivered("HELLO"))
            n1.send("*", env("HELLO", frm=A1))          # towards the offline node: dropped as dst_offline
            await settle(0.2)
            assert n2.delivered("HELLO") == [] and r.hub.dropped["dst_offline"] >= 1
            n1.register()                                # heartbeat again (offline window is only 400 ms here)
            await settle(0.05)
            topo ={n["name"]: n["online"] for n in (await r.get("/topology"))["nodes"]}
            assert topo == {"c1-d1": True, "c1-d2": False}
            assert r.sink.named("topology")
    run(main())


def test_hub_survives_a_dead_node_socket_windows_icmp_reset():
    """Spike: sending to a closed UDP port makes Windows raise WSAECONNRESET on the hub socket."""
    async def main():
        async with rig() as r:
            n1 = await r.add(A1, "c1-d1", 1)
            n2 = await r.add(A2, "c1-d2", 1)
            n3 = await r.add(A3, "c1-d3", 1)
            await settle()
            n2.close()                                    # the process died without unregistering
            for _ in range(5):
                n1.send("*", env("PREVOTE"))
                await settle(0.05)
            n1.send("*", env("PRECOMMIT"))
            assert await wait_for(lambda: n3.delivered("PRECOMMIT"))
            assert len(n3.delivered("PREVOTE")) == 5
    run(main())


def test_light_env_is_pure():
    e = env("COMMIT", body={"header": header(), "txHashes": [H], "qc": qc(3), "revoked": [], "master": A1})
    before = json.dumps(e, sort_keys=True)
    out = light_env(e)
    assert json.dumps(e, sort_keys=True) == before
    assert out["body"]["qc"]["signers"][0] == "0x" + "01" * 20
    already_light = env("COMMIT", body={"header": header(), "qc": {"round": 1, "signers": [A1]}, "revoked": [], "master": A1})
    assert light_env(already_light)["body"]["qc"] == {"round": 1, "signers": [A1]}
