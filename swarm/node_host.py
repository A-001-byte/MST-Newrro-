"""Run every member whose `host` matches, in one process (one UDP socket per drone).

  ./.venv/Scripts/python -m swarm.node_host --host B --hub 10.0.0.5:47000 --engine mock
  --host accepts a comma list (A,B,board) or `all`. The board (light validator) is only simulated in mock mode.
"""
from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path

from .common import EventSender
from .mission import members_of_host
from .node import Node, add_common_args, resolve_common


async def _amain(nodes: list[Node]) -> None:
    for n in nodes:
        await n.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError, ValueError):   # Windows Proactor loop: fall back to KeyboardInterrupt
            pass
    try:
        await stop.wait()
    finally:
        print("[node_host] stopping, exporting ledgers ...", flush=True)
        await asyncio.gather(*(n.stop() for n in nodes))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Run all SwarmShield drones of one host in a single process")
    add_common_args(ap)
    ap.add_argument("--host", required=True, help="host label from the mission (A|B|C|board), comma list, or all")
    args = ap.parse_args(argv)
    mission, path, hub_addr, obs_udp, obs_http, run_id = resolve_common(args)
    members = members_of_host(mission, args.host)
    if not members:
        raise SystemExit(f"no members with host={args.host!r} in {path}")
    if args.engine == "real":
        members = [(c, m) for c, m in members if not m["flags"] & 1]   # the hardware board is a real device
    events = EventSender(obs_udp)
    keys = Path(args.keys) if args.keys else None
    nodes = [Node(mission, c, m, hub_addr, events, args.engine, run_id, Path(args.runs_dir), obs_http,
                  host_label=args.host, keys_dir=keys) for c, m in members]
    print(f"[node_host] host={args.host} engine={args.engine} mission={path.name} hub={hub_addr[0]}:{hub_addr[1]} "
          f"run={run_id} drones={[n.name for n in nodes]}", flush=True)
    try:
        asyncio.run(_amain(nodes))
    except KeyboardInterrupt:
        print("[node_host] interrupted", flush=True)


if __name__ == "__main__":
    main()
    sys.exit(0)
