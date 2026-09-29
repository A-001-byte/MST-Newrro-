# SwarmShield v2 - Neurick board firmware (HW lane)

The Neurick board (ESP32-S3) is a **light validator** (weight 1) of cluster 1. It signs secp256k1 consensus votes
with its own key. Spec: `docs/00_CONTEXT.md` §7.9, §10, §11, §21 · Plan: `docs/02_AGENT_SWARM_HARDWARE.md` B6.

## Status (verified on the real board, 29 Sep 2026)
| Task | Status |
|---|---|
| H1 WiFi + UDP REGISTER (light:true) + DELIVER parsing + height tracking | DONE |
| H2 secp256k1 (mbedtls, RFC6979, low-s) + boot SELFTEST vs vectors.json | DONE - `SELFTEST PASS (sign 182 ms)`, byte-identical |
| H3 rogue: two conflicting signed PREVOTEs -> EQUIVOCATION -> revoked | DONE (triggered by hub CONTROL `rogue`) |
| H3 shake (MPU6050) -> signed SELF_REVOKE -> revoked | DONE |
| H4 light validator: PREVOTE/PRECOMMIT, follows COMMIT | DONE - board in the QC in ~98% of heights |
| OLED status screen (servo substitute) | DONE - ACTIVE / ROGUE (3 s) / REVOKED, big text |
| Servo 90/150/30 + USER button | BLOCKED by hardware: STM32 (I2C 0x08) never answers (ribbon cable seating?). Firmware drives it and resyncs automatically if it comes back |
| Live run with the real hub + Track A engine | NOT DONE (needs the SW lane hub) |

## Files
```
swarm_member/swarm_member.ino   main sketch (network, triggers, receive path, servo, OLED)
swarm_member/swarm_crypto.h     111 B statement, 167 B block hash, low-s, mbedtls sign/verify, self-test
swarm_member/swarm_logic.h      light-validator voting logic (host-tested)
swarm_member/swarm_oled.h       SSD1306 status screen
swarm_member/test_vectors.h     GENERATED from vectors.json (public test keys only)
swarm_member/config.example.h   template -> copy to config.h (LOCAL ONLY: board key + WiFi password, never commit)
tools/flash.sh                  compile + upload (arduino-cli bundled with Arduino IDE)
tools/run_tests.py              all checks that need no board (expects: ALL HW CHECKS PASS)
tools/fake_hub.py               bench hub: simulated cluster, verifies every board signature
tools/check_config.py           config.h preflight (prints PASS/FAIL only, never secrets)
tools/vectors_to_h.py           regenerates test_vectors.h
tools/swarm_ref.py              pure-Python reference (keccak, secp256k1 sign/recover, statement, block hash)
tools/serial_log.ps1            read the board's serial log (reset + capture)
```

## Use
1. `config.h`: copy `config.example.h` -> `config.h` (or take the one from Track A's `gen_keys.py`), then set
   `WIFI_PASSWORD` and `HUB_IP` (Wi-Fi IPv4 of the laptop running the hub). Check: `python tools/check_config.py swarm_member/config.h`
2. Libraries: Newrick, ArduinoJson 7.x, Adafruit SSD1306 (+ GFX, BusIO). Board: ESP32S3 Dev Module, USB CDC On Boot Enabled.
3. Flash: `bash tools/flash.sh COM6`
4. Bench test without the real hub: `python tools/fake_hub.py --config swarm_member/config.h` then `http://localhost:47001/topology`,
   `POST /rogue` (Act 3 trigger), `POST /reset`.
5. **Order before every run: reset the hub first, then reboot the board** (revocation is permanent for a mission).

## Demo (Act 3)
Trigger the double-vote with the hub's `POST /rogue {"node":"<board address>"}` (dashboard button). The OLED shows
ROGUE, then REVOKED once peers commit the evidence. Shake the board for the SELF_REVOKE variant (Act 3b).

## Board-specific lessons
- USB serial must not block: `Serial.setTxTimeoutMs(0)` (otherwise voting lags seconds when no one reads the port).
- I2C reads are corrupted on this board under WiFi load: 100 kHz bus, WiFi TX 8.5 dBm, and every MPU sample is read
  twice 1 ms apart and must agree (plus a temperature sanity check). Untouched noise went from 0.8 g to 0.04 g.
- ArduinoJson: never put a comment on the `#define ARDUINOJSON_USE_LONG_LONG 1` line.
- `nr.begin()` first, never `Wire.begin()`; the OLED uses `begin(..., periphBegin=false)`.
