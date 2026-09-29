# HW lane (LANE=HW) progress log - local, no git
> Work folder: `C:\Users\ADMIN\Downloads\SwarmShield\SwarmShield\hw_work\`. The repo `MST-Newrro-` is NOT modified (user instruction).
> `firmware/swarm_member/config.h` holds the board key + WiFi password: local only, never paste it anywhere.

## FINAL STATUS (06:10 IST)
| Task | State |
|---|---|
| H1 network + heartbeat | DONE on board |
| H2 crypto + self-test | DONE on board (byte-identical to vectors, mbedtls, 182 ms/sign) |
| H3 rogue double-vote | DONE on board via hub CONTROL rogue (same code as the button) |
| H3 shake -> SELF_REVOKE | DONE on board |
| H3 servo 150/30 + physical USER button | BLOCKED: STM32 (I2C 0x08) absent -> hardware/power fault, firmware already drives it (auto-resyncs if it returns) |
| OLED status screen (§8 #2, servo substitute) | DONE on board: ACTIVE -> ROGUE (3 s) -> REVOKED confirmed by user 06:30 (rogue via hub, EVID h=33, revoked h=34) |
| H4 light validator | DONE on board (in QC ~98% of heights on the fake hub) |
| Live with the real hub + Track A engine | NOT STARTED (needs SW lane hub/station; set HUB_IP to that laptop) |

## NOW
- Lane: HW · HARD_DEADLINE 07:00 IST 2026-09-29 · FREEZE 05:30 · FINAL 06:30
- Last ORIENT: 04:57 IST (~123 min left) · Phase: BUILD
- Hub host: this HP laptop, Wi-Fi 172.20.10.2 on `swarmshield` (2.4 GHz). Board on COM6. Laptops: HP, LOQ, Dell.
- Toolchain: `C:\Users\ADMIN\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe` 1.5.1, esp32 core 3.3.12 (mbedtls 3.6.6)
- Crypto backend: mbedtls (secp256k1 + deterministic ECDSA compiled in); micro-ecc fallback not needed so far
- Firmware level: code for L0+L1+L2 written; NOT yet compiled for the board (ArduinoJson missing) or flashed

## TASKS (tick = verified with evidence)
### H1 (L0) network + heartbeat
- [x] WiFi STA + status codes + reconnect; UDP :47010; REGISTER every 2 s light:true; DELIVER parse; >1400 B ignored; curHeight from light COMMIT; heap log 10 s  (code)
- [x] fake hub /topology lists board light:true  (emulated board, test_fake_hub.py)
- [ ] ON BOARD: serial `[rx] PROPOSAL h=..` ~1/s and board in fake_hub /topology
### H2 (L1) crypto + self-test
- [x] vectors_to_h.py -> test_vectors.h (--check up to date)
- [x] swarm_crypto.h: 111 B statement, 167 B block hash, low-s, mbedtls sign/verify, self-test  (host: HOST TEST PASS)
- [x] Python reference: RFC6979 signatures byte-identical to all 10 vectors; recovery with v=0
- [ ] ON BOARD: `[crypto] SELFTEST PASS (sign NN ms)`; leak check; fake hub reports `sig OK` for real board key
### H3 (L1b) triggers + servo
- [x] button rising edge -> 2 conflicting signed PREVOTEs, servo 150; MPU recipe -> SELF_REVOKE (h=curHeight-1, sha256("tamper")), servo 30; revoked-in-COMMIT -> servo 30; MPU ignored 1.5 s after servo moves  (code)
- [x] fake hub turns double PREVOTE into EQUIVOCATION and SELF_REVOKE into revocation  (emulated)
- [ ] ON BOARD: press USER -> 150 -> fake hub EVID -> COMMIT.revoked -> 30; shake -> SELF_REVOKE verified -> 30
### H4 (L2) light validator
- [x] swarm_logic.h: PREVOTE once/(h,r) on recomputed blockHash; tally distinct senders incl. own, exclude revoked; PRECOMMIT on 3w>2*active; follow COMMIT; stop if revoked  (LOGIC TEST PASS, 40 checks)
- [ ] ON BOARD: fake hub `inQC=YES` in most heights
- [ ] LIVE (later, needs SW hub + Track A engine): board address in commit.qcSigners

## EVIDENCE (on the real board, 05:10-06:02)
- H2: `[crypto] SELFTEST PASS (sign 182 ms)` every boot (serial_boot*.log); fake hub verifies every board signature (real key): `sig OK v=0->27/28`, bad=0
- H1: REGISTER c1-board 0x659a..ddaa from 172.20.10.3:47010 light=True cluster=1 OK; /topology online (fake_hub_run2.log)
- H4: board in COMMIT qc.signers 59/60 heights (05:54-05:56, untouched, no serial reader); prevote ~260-320 ms after PROPOSAL; heap stable ~254 KB
- H3 rogue (via hub CONTROL rogue = same code path as the button): `05:57:57 [EVID] EQUIVOCATION by board: two PREVOTE at h=130 r=0 ... both validly signed` -> `h=130 ... REVOKED 0x659a175e` (next COMMIT)
- H3 shake (user confirmed shaking at ~06:07:40): `06:07:42 [EVID] SELF_REVOKE by board at h=76 verified (sig OK v=0->27, 506 B)` -> `06:07:43 [commit] h=77 ... REVOKED 0x659a175e`
- False-trip check after the fix: 45 heights (06:05:52-06:07:13) untouched, no trip, board inQC 44/45
- 06:09 final state: host suite ALL HW CHECKS PASS; board rebooted into a clean voting state (inQC 4/5 in first 10 s, as expected after boot)
- BLOCKER (hardware): boot I2C scan `I2C devices: 0x3C 0x68` -> STM32 (0x08) absent even after battery OFF/ON. Servo + USER button run through the STM32, so both are dead. Answered once at 05:37 (battery 10.98 V).

## EVIDENCE (host)
- 04:57 `python tools/run_tests.py` -> ALL HW CHECKS PASS (11 steps)
- config preflight `tools/check_config.py` -> CONFIG PASS (priv derives NODE_ADDRESS; board weight-1 hardware; committee sorted; active weight 13)

## PLAYBOOK
- R0: nothing moves -> battery ON, nr.begin() first, never Wire.begin(), drive all 3 servo channels.
- R1: WiFi fail -> status 1 = SSID/5 GHz, 4 = wrong password, 6 = WPA3/bad password.
- R2: ESP32 WiFiUDP.parsePacket() returns 0 while a previous packet is still buffered -> always read the datagram fully (also oversize ones).
- R3: header.timestampMs is ~1.7e12 -> needs 64-bit JSON ints (ARDUINOJSON_USE_LONG_LONG 1).
- R4: servo swing can shake the board -> ignore the MPU for 1.5 s after any servo move, or a rogue press self-triggers tamper.
- R5: revocation is permanent per mission -> to rehearse again: reboot the board AND reset the hub (`POST /reset`).
- R6: hotspot profile must be Private + Python allowed, else inbound UDP 47000 is silently dropped.

- R7: USB-CDC Serial blocks ~100 ms per print with no reader -> Serial.setTxTimeoutMs(0) + 4 KB buffer (measured 8 s voting lag before).
- R8: ArduinoJson token-pastes ARDUINOJSON_USE_LONG_LONG into a namespace -> never put a comment on that #define line.
- R9: MPU reads get corrupted on this board (az stuck 0x75FF, 0x6868 frames) -> accept a sample only if 2 reads 1 ms apart agree (<0.15 g) and temp is 0-70 C. Untouched peak went 0.8 g -> 0.04 g.
- R10: hub reset FIRST, then reboot the board (else the board sees itself in COMMIT.revoked and stays revoked).
- R11: STM32 missing from the I2C scan = no servo, no button. Check with the boot `[boot] I2C devices:` line.

## DECISION RECORDS
- D1: fake_hub.py (bench-only) instead of waiting for SW lane B1 hub; emulates compact relay + verifies every board signature. Not the real hub.
- D2: proposal also requires header.proposer == env.from and header mission/cluster/height == envelope (cheap; honest proposers always satisfy it).
- D3: SELF_REVOKE resent up to 5x every 2 s (identical deterministic bytes) until own address appears in COMMIT.revoked.

## OPEN ISSUES / BLOCKERS
- ArduinoJson v7 not installed (needs user OK to download, or install from IDE Library Manager).
- Network `swarmshield` profile is Public -> must be Private (user action, security setting).

## JOURNAL (newest first)
### It-003 (04:57) fake hub + e2e test
- Result PASS. fake_hub.py + test_fake_hub.py; Python RFC6979 signer added to swarm_ref (byte-identical to vectors).
### It-002 (04:50) full firmware L0-L2 + logic tests
- Result PASS (host). swarm_logic.h, swarm_member.ino; 40 logic checks; reviewed: oversize drain, 64-bit timestamps, JSON const conversions.
### It-001 (04:35) H2 crypto layer
- Result PASS (host). Static spike: mbedtls secp256k1 + deterministic ECDSA enabled in core 3.3.12. Board run pending.
