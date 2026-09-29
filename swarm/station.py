"""Laptop A: hub + director + observer in one asyncio process (00_CONTEXT 6.2).

  ./.venv/Scripts/python -m swarm.station                      # dashboard on http://localhost:8080
  ./.venv/Scripts/python -m swarm.station --mock-nodes         # + every mock drone in-process (single-laptop demo)
  ./.venv/Scripts/python -m swarm.station --no-director        # each part can be disabled and run on its own
"""
from __future__ import annotations

import argparse
import asyncio
import socket
import time
from pathlib import Path

from .common import (HUB_HTTP_PORT, HUB_UDP_PORT, OBSERVER_HTTP_PORT, OBSERVER_UDP_PORT, EventSender, parse_hostport)
from .director import Director
from .hub import Hub
from .mission import load_mission_file
from .node import DEFAULT_RUNS, Node, default_mission_path
from .observer import LocalEvents, Observer

ROOT = Path(__file__).resolve().parents[1]


def local_ipv4s() -> list[str]:
    try:
        return sorted({i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
                       if not i[4][0].startswith("127.")})
    except OSError:
        return []


async def run_station(args) -> None:
    mission_path = Path(args.mission) if args.mission else default_mission_path()
    mission = load_mission_file(mission_path, "mock")
    run_id = args.run_id or time.strftime("run-%Y%m%d-%H%M%S")
    holder: dict = {}
    if not args.deployments:
        for cand in (ROOT / "contracts" / "deployments" / "mst-testnet.json", ROOT / "config" / "mst-testnet.json"):
            if cand.exists():
                args.deployments = str(cand)
                break
    if not args.abi_dir and (ROOT / "contracts" / "abi").exists():
        args.abi_dir = str(ROOT / "contracts" / "abi")

    hub = None
    if not args.no_hub:
        sink = (lambda ev: holder["obs"].ingest(ev)) if not args.no_observer else None
        hub = Hub(host=args.host, udp_port=args.hub_udp_port, http_port=args.hub_http_port,
                  observer=None if not args.no_observer else parse_hostport(args.observer, OBSERVER_UDP_PORT),
                  event_sink=sink)
        # with an in-process observer the hub calls ingest() directly; otherwise it sends events over UDP
        await hub.start()
    hub_udp = ("127.0.0.1", hub.udp_port) if hub else parse_hostport(args.hub, HUB_UDP_PORT)
    hub_http = ("127.0.0.1", hub.http_port) if hub else (hub_udp[0], HUB_HTTP_PORT)

    observer = None
    events = EventSender(parse_hostport(args.observer, OBSERVER_UDP_PORT))
    if not args.no_observer:
        observer = Observer(args.host, args.observer_udp_port, args.http_port, DEFAULT_RUNS if not args.runs_dir else Path(args.runs_dir),
                            run_id, hub_http, mission_path,
                            deployments_path=Path(args.deployments) if args.deployments else None,
                            abi_dir=Path(args.abi_dir) if args.abi_dir else None)
        holder["obs"] = observer
        await observer.start()
        events = LocalEvents(observer)

    director = None
    if not args.no_director:
        director = Director(mission, hub_udp, hub_http, events, time_scale=args.time_scale, run_id=run_id)
        director.start()
        if observer:
            observer.director = director

    nodes: list[Node] = []
    if args.mock_nodes:
        for cl, m in mission.members_of_host(args.mock_nodes):
            nodes.append(Node(mission, cl, m, hub_udp, events, "mock", run_id, Path(args.runs_dir or DEFAULT_RUNS),
                              ("127.0.0.1", observer.http_port) if observer else None, host_label="station"))
        for n in nodes:
            await n.start()

    parts = [p for p, on in (("hub", hub), ("observer", observer), ("director", director)) if on]
    print(f"[station] running: {', '.join(parts)}   mission={mission_path.name}   run={run_id}", flush=True)
    if hub:
        print(f"[station] hub UDP {hub.udp_port}  control http://localhost:{hub.http_port}/topology", flush=True)
    if observer:
        print(f"[station] dashboard  http://localhost:{observer.http_port}", flush=True)
        for ip in local_ipv4s():
            print(f"[station]   from other devices: http://{ip}:{observer.http_port}  (hub for drones: {ip}:{hub.udp_port if hub else HUB_UDP_PORT})", flush=True)
    if nodes:
        print(f"[station] {len(nodes)} MOCK drones in-process (no other laptop needed)", flush=True)
    if mission.is_dev_mock if hasattr(mission, "is_dev_mock") else False:
        print("[station] NOTE: DEV/MOCK mission fixture (fake addresses, mock engine)", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        for n in nodes:
            await n.stop()
        if director:
            await director.close()
        if observer:
            await observer.close()
        if hub:
            await hub.close()


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="SwarmShield station: hub + director + observer")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--mission", default=None)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--runs-dir", default=None)
    ap.add_argument("--hub-udp-port", type=int, default=HUB_UDP_PORT)
    ap.add_argument("--hub-http-port", type=int, default=HUB_HTTP_PORT)
    ap.add_argument("--observer-udp-port", type=int, default=OBSERVER_UDP_PORT)
    ap.add_argument("--http-port", type=int, default=OBSERVER_HTTP_PORT)
    ap.add_argument("--no-hub", action="store_true")
    ap.add_argument("--no-director", action="store_true")
    ap.add_argument("--no-observer", action="store_true")
    ap.add_argument("--hub", default=f"127.0.0.1:{HUB_UDP_PORT}", help="hub UDP address when --no-hub")
    ap.add_argument("--observer", default=f"127.0.0.1:{OBSERVER_UDP_PORT}", help="observer UDP address when --no-observer")
    ap.add_argument("--deployments", default=None,
                    help="MST deployment record (default: contracts/deployments/mst-testnet.json, else config/mst-testnet.json)")
    ap.add_argument("--abi-dir", default=None, help="default: contracts/abi when it exists")
    ap.add_argument("--time-scale", type=float, default=1.0, help="speed up scenario time (tests)")
    ap.add_argument("--mock-nodes", nargs="?", const="all", default=None,
                    help="also run mock drones in this process; optional host list (default all)")
    args = ap.parse_args(argv)
    try:
        asyncio.run(run_station(args))
    except KeyboardInterrupt:
        print("[station] stopped", flush=True)


if __name__ == "__main__":
    main()
