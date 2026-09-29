"""Tiny observer stand-in: listens on UDP 47002 and prints commit rates / notable events.

  ./.venv/Scripts/python -m swarm.tools.event_tail --seconds 15
Dedupes commits per (cluster, height) and reports the first-arrival rate, like the real observer does.
"""
from __future__ import annotations

import argparse
import json
import socket
import time

NOTABLE = {"xc_sent", "xc_verified", "xc_acked", "xc_timeout", "xc_rejected", "master_changed", "evidence", "revoked",
           "contingency", "ownership", "fault_injected", "node_up"}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=47002)
    ap.add_argument("--seconds", type=float, default=15.0)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", args.port))
    sock.settimeout(0.5)
    seen: dict[tuple, float] = {}
    nodes: set[str] = set()
    t0 = time.monotonic()
    last_report = t0
    print(f"[event_tail] listening on UDP {args.port} for {args.seconds:.0f}s", flush=True)
    while time.monotonic() - t0 < args.seconds:
        try:
            data, _ = sock.recvfrom(65535)
        except (socket.timeout, ConnectionResetError):
            data = None
        if data:
            ev = json.loads(data)
            name, d, cl = ev["event"], ev["data"], ev.get("cluster")
            if name == "node_up":
                nodes.add(ev["name"])
            if name == "commit":
                seen.setdefault(("commit", cl, d["height"]), time.monotonic())
            elif name in NOTABLE and not args.quiet:
                print(f"[event_tail] {name} {ev.get('name')} {json.dumps(d)[:140]}", flush=True)
        now = time.monotonic()
        if now - last_report >= 5:
            last_report = now
            counts: dict = {}
            for k in seen:
                counts[k[1]] = counts.get(k[1], 0) + 1
            span = now - t0
            print(f"[event_tail] t={span:4.0f}s nodes={len(nodes)} commits(first-arrival): "
                  + ", ".join(f"C{c}={n} ({n / span:.2f}/s)" for c, n in sorted(counts.items())), flush=True)
    counts = {}
    for k in seen:
        counts[k[1]] = counts.get(k[1], 0) + 1
    print(f"[event_tail] done nodes={len(nodes)} commits: {counts}", flush=True)


if __name__ == "__main__":
    main()
