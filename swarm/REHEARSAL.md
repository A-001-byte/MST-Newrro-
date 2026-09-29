# Multi-laptop rehearsal (real engine)

| Laptop | Role | Runs |
|---|---|---|
| Dell Inspiron 15 (this one) | **A** | station (hub + observer + dashboard) + drones c1-d4, c2-d5 |
| LOQ | **B** | drones c1-d1, c1-d2, c1-d3 (c1-d1 is master of cluster 1) |
| HP | **C** | drones c2-d1..c2-d4 (c2-d1 is master of cluster 2) |

All three on the hotspot `swarmshield` (2.4 GHz, WPA2). Laptop A's Wi-Fi IPv4 was `172.20.10.4`
(re-check with `ipconfig`; it changes if the hotspot reconnects). Below it is written as `<A-IP>`.

## 0. Once per laptop
Clone the repo and check out the `track-b` branch (it needs Track A's `ledger/` and `config/mission.json` too: merge or copy them). Then:

```powershell
cd MST-Newrro-
py -3 -m venv .venv
./.venv/Scripts/python -m pip install -r swarm/requirements.txt eth-keys eth-utils pycryptodome
```

Keys: each laptop needs ONLY the key files of its own drones in `.keys/` (gitignored) (send them over a private
channel, never git/chat): B = `c1-d1.json c1-d2.json c1-d3.json`, C = `c2-d1.json .. c2-d4.json`, A = `c1-d4.json c2-d5.json`.

## 1. Laptop A: network profile and firewall (Administrator PowerShell)
```powershell
Set-NetConnectionProfile -InterfaceAlias 'Wi-Fi 2' -NetworkCategory Private
./scripts/firewall_rules.ps1        # prints the netsh commands; run them yourself
```
B and C: mark their Wi-Fi Private too, and click Allow (Private) when Windows asks about Python.

## 2. Start order
```powershell
# A, terminal 1
./scripts/run_station.ps1 -RunId demo-final
# A, terminal 2
./scripts/run_nodes.ps1 -HostName A -Hub 127.0.0.1 -Engine real -Keys .keys -ShimObsConflict -RunId demo-final
# B
./scripts/run_nodes.ps1 -HostName B -Hub <A-IP> -Engine real -Keys .keys -ShimObsConflict -RunId demo-final
# C
./scripts/run_nodes.ps1 -HostName C -Hub <A-IP> -Engine real -Keys .keys -ShimObsConflict -RunId demo-final
```
`-ShimObsConflict` is needed until Track A fixes `_build_block` (one OBS per block).

## 3. Check before the demo
- From B or C: `curl.exe http://<A-IP>:47001/topology` lists the drones (9 with the board absent).
- Dashboard `http://<A-IP>:8080` on any device: both chains ticking about once a second, no "MOCK" banner.
- If a laptop is missing, nothing appears for it: firewall (Private + Allow), wrong `<A-IP>`, hotspot 5 GHz.

## 4. The demo
Dashboard: **Start scenario** with `demo` (Acts 1 to 4, about 100 s), then **Export run**.
Afterwards on A: `./.venv/Scripts/python -m swarm.tools.check_run runs/demo-final`.
Ctrl+C on each drone laptop also writes its ledger to `runs/demo-final/` and uploads it to A.

Two laptops only: run host C's command on B as well (`-HostName B,C`), or on A (`-HostName A,C`).
