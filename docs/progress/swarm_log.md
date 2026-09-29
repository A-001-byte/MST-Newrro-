# Track B (Swarm + Dashboard, LANE=SW): Progress Log (agent memory)
> Built in a side workspace (`SwarmShield/swarm_sw/`) and pushed to branch `track-b` at the end; paths below that say `swarm_sw/` mean the repo root here. Raw run folders (`runs/*`, 7-11 MB each) are not committed; `check_run` output for them is in `docs/progress/evidence/`.
> Never paste WiFi passwords or private keys here.

## STATUS
| Milestone | State | Evidence |
|---|---|---|
| B0 Setup | DONE | py 3.13.15, aiohttp 3.14.3, pyyaml 6.0.3, pytest 9.1.1 in `.venv` |
| B1 Hub + control API | DONE | `pytest swarm/tests/test_hub.py` 17 passed; `python -m swarm.hub`; `curl.exe http://localhost:47001/topology` returned JSON |
| B2 Runtime + MockEngine | DONE (mock) | 10 drones over 2 `node_host` processes: ~0.9 commits/s per cluster; exports written for all 10 |
| B3 MissionApp + sensors + director | DONE | `test_app.py` 19 tests, `test_director.py` 6; crossing -> xc_sent -> ownership{cluster:2} in real-UDP integration test |
| B4 Observer + station + dashboard | DONE (mock) | 9 observer tests; browser: all panels live, 0 console errors, controls work, 375 px width no horizontal scroll |
| B5 Scenarios + check_run | DONE (mock) | full `demo.yaml` run: 4/4 acts PASS in check_run; 12 check_run tests |
| B7 Real engine (S2-S3.5 equivalents) | DONE on 1 machine | Track A `ledger.zip` received; real engine over the UDP hub: all 4 acts PASS in `check_run` (with the OBS shim, see OPEN ISSUES); exports identical per cluster. Not run: multi-laptop, board, S4 live chain events with real chain_client |
| B8 Launchers/README | DONE (drafted) | `scripts/*.ps1` parse clean, README.md; real-run results table not possible yet |

Invariants: `./.venv/Scripts/python -m pytest swarm -q` -> **95 passed** (incl. 3 real-engine tests); `python -m swarm.station` starts cleanly; dashboard 0 console errors.

## NOW
- Lane: SW. Engine in use: mock. Spec read: v1.0.0.
- HARD_DEADLINE: NOT KNOWN (blank in docs/00_CONTEXT.md; asked the human, no answer yet). Phase treated as BUILD.
- Hosts / laptop A IPv4: NOT KNOWN (asked). Launchers take -HostName / -Hub parameters, nothing blocks.
- Next: wait for Track A (S2 engine.py, S1 mission.json + keys); then B7.

## DEFINITION OF DONE (evidence)
- [x] E1 Hub: `./.venv/Scripts/python -m pytest swarm/tests/test_hub.py -q` -> `17 passed`. Covers register, broadcast (no echo), cluster/unicast routing, partition (+unlisted group), kill both directions, drop 1.0, light stripped PROPOSAL, no TX/XC_* to light, 5-sig-QC COMMIT -> light COMMIT <= 1400 B, oversize dropped+counted, WORLD during partition, fault_injected on every fault POST, offline after silence, Windows ICMP-reset spike (hub survives a dead node socket).
- [x] E2 Runtime (mock): `hub` + `node_host --host A,B,board` + `node_host --host C` + `event_tail`: `t= 15s nodes=10 commits(first-arrival): C1=14 (0.91/s), C2=14 (0.91/s)`; `POST /export` -> 10 `*.ledger.json` in runs/. Real-UDP tests in `test_runtime_integration.py` (export upload to stub observer, failover over hub kill, shutdown exports).
- [x] E3 MissionApp: `test_app.py` (median/window/min-members, even lower median, custody initial->RECV->ACK, TIMEOUT keeps custody, late ACK releases, handoff_ready truth table, determinism, never raises).
- [x] E4 Station + dashboard: `python -m swarm.station --mock-nodes`; browser pane at http://localhost:8080: map, chains, timeline+filter, controls (kill/revive, partition/heal, drop, equivocate, false_obs, start scenario, export), MST panel ("Contracts not deployed yet" empty state), metrics. Console: no errors. Killing the master from the button worked (`kill c1-d4: ok`, it was the master after failover). NOT yet checked from a second physical device (needs the human + firewall).
- [x] E5 Scenarios + check_run (mock): `./.venv/Scripts/python -m swarm.tools.check_run runs/mock-demo-evidence` ->
  `Act 1 PASS (mock) handoff_send_to_recv_ms=1085 send_to_ack=2090 certificate_weight=9/13`,
  `Act 2 PASS failover_ms=2787`, `Act 3 PASS evidence_to_revoked_ms=1076 heights=1`,
  `Act 4 PASS cut_to_enter_ms=5149 heal_to_exit_ms=1051 heal_to_deferred_send_ms=2120`,
  `ledger exports: C1 4 exports common prefix 99 blocks IDENTICAL; C2 5 exports ... 104 IDENTICAL`, `SUMMARY: all 4 checked acts PASS`.
  These are MOCK numbers (simulator), never quote them as results.
- [x] E2-real (single machine): `PYTHONPATH=ledger python -m swarm.node_host --host A,B,C --engine real --keys .keys --mission config/mission.json --shim-obs-conflict`: 9 real drones, every node of a cluster holds the same block hash, ~1.1 s block interval, engine commit latency ~0.1 s, `mock` flag false. Track A's own suite in my venv: `60 passed`.
- [x] E5-real (single machine): `python -m swarm.tools.check_run runs/real-engine-shim` -> Act 1 PASS (start_to_xc_sent 22.3 s, send_to_recv 217 ms, send_to_ack 1121 ms, certificate 9/13 real QC), Act 2 PASS (failover 2922 ms), Act 3 PASS (evidence_to_revoked 1473 ms, 1 height), Act 4 PASS (cut_to_enter 4907 ms, heal_to_exit 922 ms, heal_to_deferred_send 2125 ms), `ledger exports: C1 4 exports common prefix 89 blocks IDENTICAL; C2 5 exports ... 92 IDENTICAL`, `SUMMARY: all 4 checked acts PASS`. WITHOUT the shim (`runs/real-engine-noshim`): Act 1 FAIL (handoff 80.7 s after OBJ-1 start) and Act 4 FAIL (deferred handoff 44.6 s after heal), Acts 2, 3 PASS.
- [ ] E6 (live on >= 2 laptops + board), E7 (results table from a real multi-laptop run, backup video): need the humans/hardware.

## PLAYBOOK
- R0: Nothing visible on another laptop -> Firewall (Private), bind 0.0.0.0, correct Wi-Fi IPv4 (not VMware), then hub /topology.
- R1: Event names/fields copied exactly from 00_CONTEXT 11.7; additive fields only (see INTERFACE NOTES).
- R2: Windows shell: `./.venv/Scripts/python -m ...`, no bare pip/python, PYTHONUTF8=1, ASCII console output, utf-8 file IO, pathlib. Bash `cd` is sticky between calls: always `cd swarm_sw` first.
- R3: Dedupe key (event, cluster, height[, dst/src, seq, member]); first arrival = event time, on the OBSERVER receive clock (`rx`).
- R4: Light nodes get only PROPOSAL/PREVOTE/PRECOMMIT/COMMIT/STATUS of their own cluster, <= 1400 B; hub never echoes.
- R5: When a real-time test flakes, first ask whether a periodic task (metrics tick, register loop) is keeping a stream busy or an offline window is shorter than the test (both happened).
- R6: Kill a Windows venv process tree with `taskkill /F /T /PID` (the venv python.exe is a launcher shim; `kill` alone leaves the child).
- R7: Mock needs "heard-before" guards (ROLE and CONTINGENCY only fire for peers heard at least once) or staggered process startup produces spurious master changes / contingency.
- R8: MockEngine: locked-value + valid-value re-proposal keep a round change from committing two blocks at one height.

## DECISION RECORDS
- D1: Helper module `swarm/mission.py` (MissionLite: MissionConfig attribute shape; mock mode needs no swarmledger). `--engine real` uses `swarmledger.config.load_mission` lazily.
- D2: Dev fixture rosterHash uses the real 10.7 formula over FAKE addresses (sha256("DEV-MOCK-"+name)[:20]); no keys anywhere. `swarm/wire.py` (copied hash formulas) is verified against `vectors.json` (roster, genesis, tx hash, txRoot, block hash) in test_wire.py.
- D3: Hub accepts REGISTER from killed nodes (heartbeat = control plane); CONTROL is out-of-band (delivered even when killed/partitioned); WORLD skips killed nodes (dead drone does not sense) but ignores partition/drop.
- D4: "every control POST emits fault_injected" read as every fault-injecting POST (partition, heal, drop, kill, revive, fault, rogue). /export emits only `topology`.
- D5: MockEngine is a real (small) vote exchange, not a scripted commit: quorum, kill, partition, QC signer sets, equivocation evidence, round timeouts, SYNC catch-up behave realistically. Every event tagged `mock: true`; sig is null; QC entries are zeroed placeholders; verify_foreign_packet skips signatures.
- D6: MockEngine does not emit `xc_rejected{replay}` for benign re-relays of an already processed packet (would look like an attack on the dashboard); real replays of unknown packets still do.
- D7: Handoff guard: one XC_SEND per object in flight, released when it commits or after 4 s (`XC_GUARD_MS`); after XC_TIMEOUT the app allows a fresh XC_SEND (deferred handoff path for Act 4).
- D8: member_score EWMA alpha 1/4 starting at 0 (one bad reading never flags; a +3000 cm liar flags on the 3rd sample); deviation = isqrt(dx^2+dy^2).
- D10 (files from Track A): `config/mission.json` now exists, so `default_mission_path()` picks it over `mission.dev.json`; mock runs on the real mission work (10 nodes, 7+7 commits in 9 s) and still carry `mock: true`. Keys live only in `swarm_sw/.keys/` (`.gitignore` inside, mission identities, no funds). `config.h` (board firmware, contains the board's private key and WiFi placeholder) is HW-lane material; not used, not copied. `eth-keys/eth-utils/pycryptodome` were installed in my `.venv` only to verify key/address pairs (dev check, not a runtime dependency).
- D9: demo.yaml timing leaves margin per act; the killed master is revived before the equivocation act so quorum never drops below 10/10 alive weight.

## INTERFACE NOTES (ICP proposals / NOTICE TO TRACK A). All additive, no spec edit made.
- Hub POSTs also accept a drone NAME wherever `0x...` is accepted.
- `fault_injected.data` may carry extras: `name`, `rate` (drop), `groups` (partition).
- GET /topology nodes carry `online` (bool); `lastSeenMs` = age in ms since last REGISTER.
- GET /stats extras: `sentTotal`, `deliveredTotal`, `deliveredBytes`. `hub_stats` event = same JSON every 2 s.
- New additive events: `scenario {name, state: started|step|finished|stopped, ...}` (director), `metrics` (observer).
- Observer adds `rx` (observer receive ms) and `seq` to every forwarded event; events.jsonl keeps duplicates with `dup: true`.
- Observer extras: `GET /scenarios`, `POST /control/scenario/stop`, `POST /control/object {obj, action: start|stop}`.
- MockEngine COMMIT envelopes carry `body.txs` (mock only) so followers can apply blocks; the hub strips it for light nodes (light COMMIT rewrite).
- ASK TRACK A: engine.state()["committee"] element shape (mock emits {address, weight, flags}); does the real engine emit `evidence` at detection time and `revoked` at commit (check_run assumes that order); does `state()["height"]` mean last committed height (mock assumes yes).
- MissionApp extras beyond the spec API: `announce_initial()` (ownership via "initial"), `accepted_track(obj)`, `handoff_plan(obj)`, `handoff_ready(obj)`, `pending_handoff(obj)`, `debug_state()`.

## REQUESTS TO TRACK A   (status: needed | received | not needed)
Nothing blocks me now. Minimum first, in this order:
| # | File / thing | Why | Blocks | Status |
|---|---|---|---|---|
| 1 | Whole `ledger/` folder incl. non-empty `engine.py` | real ClusterEngine | `--engine real` | **received** (`ledger.zip`, extracted read-only to `swarm_sw/ledger/`; needs `eth-keys eth-utils pycryptodome`) |
| 1b | **BUG REPORT for Track A**: `engine._build_block` keeps only one OBS per block. `_conflict_key` returns None for OBS/ZONE; `if k in keys: continue; keys.add(k)` then treats every later None as a conflict. Fix: `if k is not None and k in keys: continue`. Effect: fused track lags, handoffs 60+ s late (see runs/real-engine-noshim). I work around it with `--shim-obs-conflict`, not by editing their file | correctness | live acts 1 and 4 | needed (tell teammate) |
| 1c | Optional: chain_client observer address from `STATION_IP` (currently hardcoded 127.0.0.1) | chain events from a laptop other than A | S4 | nice to have |
| 2 | `config/mission.json` (public addresses/pubkeys, missionId, rosterHash) | replaces my dev fixture; load_mission checks rosterHash | real engine boot (S1/S2) | **received** (mission swarmshield-demo-02; now `swarm_sw/config/mission.json`; rosterHash matches spec 10.7; `swarmledger.load_mission` accepts it) |
| 3 | `.keys/<name>.json` for drones on my laptop, PRIVATE channel only | signing on real engine | real engine | **received** as one JSON array; split into `swarm_sw/.keys/<name>.json` for the 9 drones (each priv verified to derive its address). Board key deliberately NOT copied (HW lane). Still needed from Track A if keys ever rotate |
| 4 | **answered**: chain_client `emit()` builds the 11.7 envelope (`name`/`cluster` null) and sends ONE UDP datagram to 127.0.0.1:47002 (hardcoded; must run on laptop A; teammate offered to use STATION_IP); events: enrollment, onchain_revocation, anchor, audit. Tested here with synthetic events in that exact format: timeline + MST panel render, `name: null` is fine. Old text: How chain events reach the observer (I assume UDP `<A-IP>:47002` or `POST /event` :8080, envelope 11.7, src "chain") and the answers to the INTERFACE NOTES questions above | MST panel + timeline | MST panel real data | needed (confirm) |
| 5 | **answered by teammate**: no sync point formally happened (nothing merged to main); engine/handoff/contingency/chain work exists and is tested in memory. One-line sync status: which of S1/S2/S3/S3.5/S4 happened | planning the switch | planning | needed |
| 6 | `contracts/deployments/mst-testnet.json` | MST panel | MST panel | **received** -> `swarm_sw/config/mst-testnet.json` (4 contracts incl. HandoffReceipts; mission 0x3d18... roster hash matches mission.json) |
| 7 | `dashboard/mst/` (copy to `swarm_sw/dashboard/mst/`; observer serves /mst/setup.html) | Act 0 link | Act 0 | needed later (graceful 404 until then) |
| 8 | `contracts/abi/*.json` (start with `--abi-dir`) | /abi/<Name>.json, only if setup.html needs it | Act 0 | needed later |
| - | chain_client.py (0 bytes), board key, firmware config.h | I only consume events / HW lane | nothing | not needed |

## OPEN ISSUES / BLOCKERS
- Track A bug: one OBS per block (see REQUESTS 1b). Shim `--shim-obs-conflict` is opt-in; without it Acts 1 and 4 fail `check_run` on the real engine.
- Not covered: multi-laptop run, the Neurick board (mission has `c1-board`, weight 1; real `node_host` skips light members so C1 runs on weight 12/13), chain_client running against my observer for real (only synthetic events in its exact format), `history_merged` (A6+).
- HARD_DEADLINE, host list, laptop A IPv4 unknown (asked; not blocking).
- Not yet verified from a second physical device / other laptop (needs the human; Windows Firewall "Allow Python on Private networks" once).
- Hub `/stats` msgs-per-commit counts hub ingress messages excluding HELLO/STATUS; fan-out per message is in `deliveredTotal`.
- Startup nuance in mock: a node started several seconds after its cluster catches up via SYNC (tested), but very late joins log a few `bad_proposer`/`bad_proposal` counters (harmless, counted in engine.dropped).

## ITERATION JOURNAL (newest first)
### It-007: real engine integration (B7)
- Result PASS on one machine. Lessons: (1) the real engine does NOT register itself with the transport; the runtime must call `transport.set_receiver(engine.on_envelope)` (my node now always does), without it every node was deaf and sat in prevote. (2) The first check_run "PASS" hid a 60 s deferred handoff; check_run now also checks handoff latency vs OBJ-1 start and vs heal. (3) `xc_rejected{replay}` on the real engine is benign re-relay traffic; check_run reports it, does not fail on it. (4) node_state now carries an additive `drops` counter dict for diagnosing dropped envelopes.
- Real engine facts: `state()["committee"]` is a sorted list of address strings (mock emits dicts; dashboard does not read it), `state()["height"]` = last committed, `evidence` is emitted at detection, `revoked` at commit.
### It-006: files from Track A (mission.json, keys)
- mission.json, keys, then ledger.zip + mst-testnet.json received; see REQUESTS. Keys stay in `.keys/` only.
### It-005: B5 + wrap-up
- Result PASS. check_run + scenarios + tests; full demo mock run 4/4; README, launchers; dashboard mobile check; MST-Newrro- confirmed untouched.
- Lesson: the mock's quality depends on real behaviours (round timeouts, locks, SYNC); scripted commits would not have exercised Acts 2 and 4 honestly.
### It-004: B4 observer + dashboard + station
- Result PASS. Observer dedupe/SSE/endpoints/metrics tested; station smoke: handoff 962 ms recv / 1023 ms ack on the observer clock (mock).
- Lesson: keep periodic emitters out of heartbeat tests (R5).
### It-003: B3 MissionApp/director/sensors + real-UDP integration
- Result PASS. End-to-end handoff over real sockets (`test_object_crossing_the_line_hands_off_end_to_end`).
### It-002: B1 hub + B2 runtime
- Result PASS. Windows asyncio UDP spike (ICMP reset) passed first try; light COMMIT rewrite stays <= 1400 B with 5-signer QC.
### It-001: B0 setup + spec read
- Result PASS. state.py (real LedgerState) showed the true event/field semantics that MockEngine mirrors (xc_sent on apply, ACK needs pendingSends, `packet`/`ackPacket` tx fields).
