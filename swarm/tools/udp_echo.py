"""UDP echo / sink for plumbing tests (docs/02 section 6: the HW lane can test WiFi+UDP before the hub exists).

  ./.venv/Scripts/python -m swarm.tools.udp_echo --port 47000
Prints one ASCII line per datagram and echoes the payload back to the sender (use --no-echo for a pure sink).
"""
from __future__ import annotations

import argparse
import socket


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=47000)
    ap.add_argument("--no-echo", action="store_true")
    args = ap.parse_args(argv)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.port))
    print(f"[udp_echo] listening on {args.host}:{args.port} echo={not args.no_echo}", flush=True)
    try:
        while True:
            try:
                data, addr = sock.recvfrom(65535)
            except ConnectionResetError:          # Windows ICMP reset from a vanished sender
                continue
            text = data[:120].decode("utf-8", "replace").replace("\n", " ")
            print(f"[udp_echo] {addr[0]}:{addr[1]} {len(data)}B {text}", flush=True)
            if not args.no_echo:
                sock.sendto(data, addr)
    except KeyboardInterrupt:
        print("[udp_echo] stopped", flush=True)


if __name__ == "__main__":
    main()
