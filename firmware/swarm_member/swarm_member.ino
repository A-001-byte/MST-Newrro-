// swarm_member.ino: SwarmShield v2 - Neurick board (ESP32-S3) as a hardware LIGHT VALIDATOR of cluster 1.
// Spec: docs/00_CONTEXT.md 7.9, 10, 11, 21 · Plan: docs/02 B6 (L0 network, L1 crypto+triggers, L2 voting).
//
//  H1 (L0)  WiFi STA -> UDP 47010 -> REGISTER {"light":true} every 2 s -> parse DELIVER (ArduinoJson v7)
//  H2 (L1)  secp256k1 (mbedtls, RFC6979, low-s) + boot SELFTEST against vectors.json
//  H3 (L1b) USER button -> two conflicting signed PREVOTEs (servo 150) · MPU shake -> signed SELF_REVOKE (servo 30)
//  H4 (L2)  recompute blockHash, PREVOTE once per (h,r), PRECOMMIT on > 2/3 prevote weight, follow light COMMIT
//
// Servo (all 3 channels): 90 = active · 150 = rogue (just double-signed) · 30 = revoked
// Arduino IDE: ESP32S3 Dev Module · USB CDC On Boot Enabled · Flash 16MB · PSRAM OPI · 115200 baud
// Libraries: Newrick (organisers), ArduinoJson v7 (Library Manager).
// config.h is LOCAL ONLY (board private key + WiFi password). Never commit or paste it.
//
// Board rules: nr.begin() first and NEVER Wire.begin(); battery switch ON; never burn eFuses / secure boot.

// header.timestampMs is a 64-bit wall-clock ms value. No trailing comment on the next line:
// ArduinoJson token-pastes this macro's value into its namespace name.
#define ARDUINOJSON_USE_LONG_LONG 1
#include <ArduinoJson.h>
#include <Newrick.h>
#include <Wire.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include "esp_random.h"

#include "config.h"
#include "test_vectors.h"
#define SS_SELFTEST_AVAILABLE 1
#include "swarm_crypto.h"
#include "swarm_logic.h"
#include "swarm_oled.h"

// ---------------------------------------------------------------------------------------------
// build options
// ---------------------------------------------------------------------------------------------
#define FW_VERSION        "v2-L2"
#define FW_VOTING         1        // 1 = L2 light validator; 0 = L1 only (scope-ladder cut: signs SELF_REVOKE/rogue, no votes)
#define MAX_LIGHT_DGRAM   1400     // spec 11.1: anything bigger is logged and ignored
#define RX_BUF_SIZE       1500
#define TX_BUF_SIZE       900

#define SERVO_ACTIVE      90
#define SERVO_ROGUE       150
#define SERVO_REVOKED     30

#define TAMPER_THRESHOLD_G   0.5f
#define TAMPER_CONSECUTIVE   2
#define TAMPER_RESET_MS      4000
#define SERVO_SETTLE_MS      1500    // ignore the MPU while the MG995 swings (its own jolt must not look like tamper)
#define SELF_REVOKE_RESENDS  5       // identical deterministic bytes; resend in case UDP drops one
#define ROGUE_DEBOUNCE_MS    3000
#define MPU_ADDR             0x68
#define I2C_CLOCK_HZ         100000
#define WIFI_TX_POWER        WIFI_POWER_8_5dBm   // hotspot is close (RSSI ~-55 dBm); lower TX current spikes = cleaner I2C

// ---------------------------------------------------------------------------------------------
// state
// ---------------------------------------------------------------------------------------------
Newrick   nr;
WiFiUDP   udp;
SsLight   light;
JsonDocument doc;

static uint8_t  g_priv[32];
static uint8_t  g_mission[32];
static char     g_missionHex[65];
static char     g_selfAddr[43];
static bool     g_cryptoOk = false;
static bool     g_udpUp = false;

enum BoardState { ST_ACTIVE, ST_ROGUE, ST_REVOKED };
static BoardState g_state = ST_ACTIVE;
static int        g_servoAngle = -1;
static uint32_t   g_servoMovedAt = 0;

static char    rxBuf[RX_BUF_SIZE + 1];
static char    txBuf[TX_BUF_SIZE];

static uint32_t tLastRegister = 0, tLastWifiRetry = 0, tLastHeap = 0, tLastAccel = 0, tLastSensors = 0, tLastRogue = 0;
static float    g_baselineG = 1.0f;
static int      g_highCount = 0;
static uint32_t g_lastHighAt = 0;
static uint32_t g_tamperAt = 0;
static bool     g_selfRevokeSent = false;
static int      g_selfRevokeLeft = 0;
static uint32_t g_selfRevokeNext = 0;
static uint32_t g_selfRevokeHeight = 0;
static uint8_t  g_prevButton = 0;
static bool     g_peerRevokeLogged = false;
SsOled          oled;
static const char* g_reason = "";              // shown on the OLED under REVOKED
static bool     g_oledDirty = true;
static float    g_mpuPeakDelta = 0;            // diagnostics: peak |g - baseline| per 5 s window
static uint32_t g_mpuBadReads = 0, tLastMpuLog = 0;
static bool     g_mcOk = false;                // STM32 motion controller answering on I2C 0x08
static int      g_mpuDiagLeft = 20;            // print raw frames of the first 20 above-threshold samples

static struct {
  uint32_t rx, oversize, badJson, wrongMission, wrongCluster, proposals, badProposals, commits, inQc,
           prevotesSent, precommitsSent, signFail, sendFail;
} st;

// ---------------------------------------------------------------------------------------------
// servo + state
// ---------------------------------------------------------------------------------------------
static void setServo(int angle) {
  if (angle == g_servoAngle) return;
  nr.servo((uint8_t)angle, (uint8_t)angle, (uint8_t)angle);   // drive all 3 channels (MG995 is on 2/3)
  g_servoAngle = angle;
  g_servoMovedAt = millis();
}
static void enterState(BoardState s, const char* why) {
  if (g_state == ST_REVOKED) return;                          // revocation is permanent for this mission
  g_state = s;
  g_oledDirty = true;
  setServo(s == ST_ACTIVE ? SERVO_ACTIVE : s == ST_ROGUE ? SERVO_ROGUE : SERVO_REVOKED);
  Serial.printf("[state] %s (servo %d) - %s\n", s == ST_ACTIVE ? "ACTIVE" : s == ST_ROGUE ? "ROGUE" : "REVOKED", g_servoAngle, why);
}

// ---------------------------------------------------------------------------------------------
// MPU6050 (proven v1 recipe, 00_CONTEXT 21)
// ---------------------------------------------------------------------------------------------
static void wakeMPU() {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x6B);
  Wire.write(0);
  Wire.endTransmission(true);
}
// Last raw frame, for diagnostics of suspicious samples.
static int16_t g_rawAx, g_rawAy, g_rawAz, g_rawT;
// Burst-read ACCEL_XOUT_H..TEMP_OUT_L (0x3B..0x42, 8 bytes). The temperature word is a free integrity check:
// a corrupted or misaligned frame almost never decodes to a plausible die temperature.
static bool readAccelG(float& g) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(MPU_ADDR, 8, true) != 8) return false;
  g_rawAx = Wire.read() << 8 | Wire.read();
  g_rawAy = Wire.read() << 8 | Wire.read();
  g_rawAz = Wire.read() << 8 | Wire.read();
  g_rawT  = Wire.read() << 8 | Wire.read();
  float x = g_rawAx / 16384.0f, y = g_rawAy / 16384.0f, z = g_rawAz / 16384.0f;
  g = sqrtf(x * x + y * y + z * z);
  float tC = g_rawT / 340.0f + 36.53f;
  if (tC < 0.0f || tC > 70.0f) return false;                   // corrupted frame (measured: -23 C, 115 C, 118 C)
  return (g > 0.3f && g < 4.0f);
}
static float mpuTempC() { return g_rawT / 340.0f + 36.53f; }
// Two burst reads ~1 ms apart must agree. Measured on this board: under WiFi load ~20 reads/s are corrupted
// (e.g. az stuck at 0x75FF) and occasionally pass the range checks; a real shake changes < 0.15 g in 1 ms.
static bool readAccelChecked(float& g) {
  float g1, g2;
  if (!readAccelG(g1)) return false;
  delayMicroseconds(1000);
  if (!readAccelG(g2)) return false;
  if (fabsf(g1 - g2) > 0.15f) return false;
  g = g2;
  return true;
}
static void calibrateBaseline() {
  float sum = 0; int n = 0, attempts = 0;
  while (n < 30 && attempts < 200) {
    float g;
    if (readAccelG(g)) { sum += g; n++; }
    attempts++;
    delay(20);
  }
  g_baselineG = (n > 0) ? sum / n : 1.0f;
  Serial.printf("[mpu] baseline %.3f g (%d samples) - keep the board still during boot\n", g_baselineG, n);
}

// ---------------------------------------------------------------------------------------------
// network
// ---------------------------------------------------------------------------------------------
static void udpSend(const char* buf, int n) {
  if (n <= 0 || WiFi.status() != WL_CONNECTED || !g_udpUp) { st.sendFail++; return; }
  if (!udp.beginPacket(HUB_IP, HUB_PORT)) { st.sendFail++; return; }
  udp.write((const uint8_t*)buf, (size_t)n);
  if (!udp.endPacket()) st.sendFail++;
}
static void sendRegister() {
  int n = ss_build_register(txBuf, sizeof(txBuf), g_selfAddr, NODE_NAME, CLUSTER_ID);
  udpSend(txBuf, n);
}
static void startWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);                                        // modem sleep adds 100+ ms UDP latency
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  WiFi.setTxPower(WIFI_TX_POWER);
  Serial.printf("[wifi] connecting to \"%s\"", WIFI_SSID);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) { delay(500); Serial.print("."); }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("[wifi] connected, board IP %s, RSSI %d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
  } else {
    Serial.printf("[wifi] FAILED (status %d) - 1=SSID not found/5GHz, 4=wrong password, 6=WPA3 or bad password. Retrying.\n",
                  (int)WiFi.status());
  }
}
static void maintainNetwork(uint32_t now) {
  if (WiFi.status() == WL_CONNECTED) {
    if (!g_udpUp) {
      g_udpUp = udp.begin(LOCAL_UDP_PORT) == 1;
      Serial.printf("[hub] UDP %s on :%d -> hub %s:%d\n", g_udpUp ? "listening" : "BIND FAILED", LOCAL_UDP_PORT, HUB_IP, HUB_PORT);
      tLastRegister = 0;                                       // register immediately
    }
    if (g_udpUp && (tLastRegister == 0 || now - tLastRegister >= REGISTER_INTERVAL_MS)) {
      tLastRegister = now ? now : 1;
      sendRegister();
    }
    return;
  }
  if (g_udpUp) { udp.stop(); g_udpUp = false; Serial.println("[wifi] link lost"); }
  if (now - tLastWifiRetry > 10000) {
    tLastWifiRetry = now;
    Serial.printf("[wifi] not connected (status %d), retrying...\n", (int)WiFi.status());
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    WiFi.setTxPower(WIFI_TX_POWER);
  }
}

// ---------------------------------------------------------------------------------------------
// signing + sending
// ---------------------------------------------------------------------------------------------
// Signs statement (msgType, mission, cluster, h, r, subject) and sends the envelope to cluster:<id>. Returns sign ms or -1.
static int signAndSend(const char* type, uint8_t msgType, uint32_t h, uint32_t r, const char* subjHex64, const char* body) {
  if (!g_cryptoOk) { Serial.printf("[crypto] refusing to sign %s: SELFTEST did not pass\n", type); return -1; }
  uint8_t subj[32], dig[32], R[32], S[32];
  if (!ss_hex2bin(subjHex64, subj, 32)) return -1;
  ss_statement_digest(dig, msgType, g_mission, CLUSTER_ID, h, r, subj);
  uint32_t t0 = millis();
  int rc = ss_sign_digest(g_priv, dig, R, S);
  int dt = (int)(millis() - t0);
  if (rc != 0) { st.signFail++; Serial.printf("[crypto] sign FAILED rc=-0x%04x\n", (unsigned)-rc); return -1; }
  int n = ss_build_signed_send(txBuf, sizeof(txBuf), type, g_missionHex, CLUSTER_ID, g_selfAddr, h, r, subjHex64, R, S, body);
  if (n < 0) { Serial.println("[hub] envelope too large"); return -1; }
  udpSend(txBuf, n);
  return dt;
}
static void doVote(const SsAction& a) {
#if FW_VOTING
  if (a.prevote) {
    int ms = signAndSend("PREVOTE", SS_MSG_PREVOTE, a.height, a.round, a.subject, "{}");
    if (ms >= 0) { st.prevotesSent++; Serial.printf("[vote] PREVOTE h=%lu r=%lu %.8s.. (sign %d ms)\n", (unsigned long)a.height, (unsigned long)a.round, a.subject, ms); }
  }
  if (a.precommit) {
    int ms = signAndSend("PRECOMMIT", SS_MSG_PRECOMMIT, a.height, a.round, a.subject, "{}");
    if (ms >= 0) { st.precommitsSent++; Serial.printf("[vote] PRECOMMIT h=%lu r=%lu %.8s.. (sign %d ms)\n", (unsigned long)a.height, (unsigned long)a.round, a.subject, ms); }
  }
#else
  (void)a;
#endif
}

// ---------------------------------------------------------------------------------------------
// H3 triggers
// ---------------------------------------------------------------------------------------------
static void randomHex64(char out[65]) {
  uint8_t b[32];
  esp_fill_random(b, 32);
  ss_bin2hex(b, 32, out);
}
// Rogue: two conflicting signed PREVOTEs for the same (curHeight, lastRound). Honest full validators turn this into EVIDENCE.
static void triggerRogue(const char* source) {
  uint32_t now = millis();
  if (g_state == ST_REVOKED || light.selfRevoked()) { Serial.printf("[rogue] ignored (%s): already revoked\n", source); return; }
  if (tLastRogue && now - tLastRogue < ROGUE_DEBOUNCE_MS) return;
  tLastRogue = now;
  uint32_t h = light.height(), r = light.round();
  char a[65], b[65];
  if (!light.proposalSubject(h, a)) randomHex64(a);
  do { randomHex64(b); } while (strcmp(a, b) == 0);
  enterState(ST_ROGUE, source);
  int m1 = signAndSend("PREVOTE", SS_MSG_PREVOTE, h, r, a, "{}");
  int m2 = signAndSend("PREVOTE", SS_MSG_PREVOTE, h, r, b, "{}");
  Serial.printf("[rogue] double PREVOTE at h=%lu r=%lu: %.8s.. and %.8s.. (sign %d/%d ms) - expect evidence -> revoked\n",
                (unsigned long)h, (unsigned long)r, a, b, m1, m2);
}
// Tamper: signed SELF_REVOKE (msgType 7, height = curHeight-1, round 0, subject = sha256("tamper")).
static void sendSelfRevoke() {
  uint8_t rh[32]; char rhHex[65];
  ss_sha256((const uint8_t*)"tamper", 6, rh);
  ss_bin2hex(rh, 32, rhHex);
  int ms = signAndSend("SELF_REVOKE", SS_MSG_SELF_REVOKE, g_selfRevokeHeight, 0, rhHex, "{\"reason\":\"tamper\"}");
  Serial.printf("[mpu] SELF_REVOKE sent h=%lu subject=sha256(\"tamper\") (sign %d ms, %d resends left)\n",
                (unsigned long)g_selfRevokeHeight, ms, g_selfRevokeLeft - 1);
}
static void onTamper() {
  if (g_selfRevokeSent) return;
  g_selfRevokeSent = true;
  g_selfRevokeHeight = light.height() - 1;                     // curHeight starts at 1, so this never underflows
  g_selfRevokeLeft = SELF_REVOKE_RESENDS;
  g_selfRevokeNext = 0;
  Serial.println("[mpu] TAMPER DETECTED - signing SELF_REVOKE");
  light.markSelfRevoked();                                      // a tampered key must not keep voting
  g_reason = "SELF_REVOKE: tamper";
  enterState(ST_REVOKED, "self-revoked on tamper");
}
static void pollTriggers(uint32_t now) {
  if (now - tLastAccel >= 30) {                                 // ~33 Hz
    tLastAccel = now;
    float g;
    bool okRead = readAccelChecked(g);
    // A failed/rejected read is SKIPPED (neither counts nor resets): on this board most reads fail under WiFi load, so
    // resetting on failure made a real shake undetectable. "Consecutive" = consecutive VALID (double-read-checked) samples;
    // a stale high sample expires after 300 ms so two far-apart spikes never combine.
    if (!okRead) g_mpuBadReads++;
    if (g_highCount > 0 && now - g_lastHighAt > 300) g_highCount = 0;
    if (okRead) {
      bool settling = now - g_servoMovedAt < SERVO_SETTLE_MS;
      float delta = fabsf(g - g_baselineG);
      if (delta > g_mpuPeakDelta) g_mpuPeakDelta = delta;
      if (delta > TAMPER_THRESHOLD_G && g_mpuDiagLeft > 0) {
        g_mpuDiagLeft--;
        Serial.printf("[mpu] diag delta %.2f g raw ax=%d ay=%d az=%d temp=%.1fC settling=%d\n",
                      delta, g_rawAx, g_rawAy, g_rawAz, mpuTempC(), (int)settling);
      }
      if (!settling && delta > TAMPER_THRESHOLD_G) { g_highCount++; g_lastHighAt = now; } else g_highCount = 0;
      if (g_highCount >= TAMPER_CONSECUTIVE && (g_tamperAt == 0 || now - g_tamperAt > TAMPER_RESET_MS)) {
        g_tamperAt = now; g_highCount = 0;
        Serial.printf("[mpu] shock %.2f g (baseline %.2f, delta %.2f > %.2f on %d consecutive samples)\n",
                      g, g_baselineG, delta, TAMPER_THRESHOLD_G, TAMPER_CONSECUTIVE);
        onTamper();
      }
    }
  }
  if (now - tLastMpuLog >= 5000) {
    tLastMpuLog = now;
    Serial.printf("[mpu] peak delta %.3f g (5 s), bad reads %lu\n", g_mpuPeakDelta, (unsigned long)g_mpuBadReads);
    g_mpuPeakDelta = 0;
  }
  if (g_selfRevokeLeft > 0 && now >= g_selfRevokeNext) {
    sendSelfRevoke();
    g_selfRevokeLeft--;
    g_selfRevokeNext = now + 2000;
  }
  // Button at 10 Hz (rising edge). If the STM32 (0x08) does not answer, failed I2C transactions to it were measured to
  // corrupt MPU reads on the shared bus -> back off to one probe every 5 s until it answers again.
  if (now - tLastSensors >= (g_mcOk ? 100u : 5000u)) {
    tLastSensors = now;
    bool ok = nr.updateSensors();
    if (ok != g_mcOk) Serial.printf("[boot] motion controller %s\n", ok ? "answering (button active)" : "NOT answering - battery switch ON? (button polled every 5 s)");
    if (ok && !g_mcOk) {                                        // STM32 came back: re-send the servo angle for the current state
      int a = g_servoAngle; g_servoAngle = -1; setServo(a < 0 ? SERVO_ACTIVE : a);
    }
    g_mcOk = ok;
    if (ok) {
      uint8_t b = nr.buttonState;
      if (b != 0 && g_prevButton == 0) triggerRogue("USER button");
      g_prevButton = b;
    }
  }
}

// ---------------------------------------------------------------------------------------------
// H1/H4 receive path
// ---------------------------------------------------------------------------------------------
static bool sameHex(const char* a, const char* b64) {           // a may carry 0x / uppercase; b64 is normalised
  char n[65];
  return ss_norm_hash(a, n) && strcmp(n, b64) == 0;
}
// Recompute blockHash from the light PROPOSAL header (167-byte preimage, spec 10.5) and compare with subject.
static bool proposalHashOk(JsonObjectConst env, JsonObjectConst hdr, uint32_t h, const char* subj64) {
  if (hdr.isNull()) return false;
  SsHeader H;
  const char* mission = hdr["mission"] | "";
  if (!sameHex(mission, g_missionHex)) return false;
  if ((hdr["cluster"] | -1) != CLUSTER_ID) return false;
  if (hdr["height"].as<uint64_t>() != (uint64_t)h) return false;
  const char* proposer = hdr["proposer"] | "";
  const char* from = env["from"] | "";
  if (!ss_addr_eq(proposer, from)) return false;               // an honest proposer signs its own header
  memcpy(H.mission, g_mission, 32);
  H.cluster = CLUSTER_ID;
  H.height = h;
  H.timestampMs = hdr["timestampMs"].as<uint64_t>();
  if (!ss_hex2bin(hdr["prevHash"] | "", H.prevHash, 32)) return false;
  if (!ss_hex2bin(hdr["txRoot"] | "", H.txRoot, 32)) return false;
  if (!ss_hex2bin(proposer, H.proposer, 20)) return false;
  uint8_t bh[32]; char bhHex[65];
  ss_block_hash(bh, H);
  ss_bin2hex(bh, 32, bhHex);
  return strcmp(bhHex, subj64) == 0;
}

static void handleEnvelope(JsonObjectConst env) {
  const char* type = env["type"] | "";
  uint32_t h = env["height"].as<uint32_t>();
  uint32_t r = env["round"].as<uint32_t>();
  if ((env["v"] | 0) != 1) return;
  if (!sameHex(env["mission"] | "", g_missionHex)) { st.wrongMission++; return; }
  if ((env["cluster"] | -1) != CLUSTER_ID) { st.wrongCluster++; return; }

  if (strcmp(type, "PROPOSAL") == 0) {
    st.proposals++;
    char subj[65];
    bool ok = ss_norm_hash(env["subject"] | "", subj) &&
              proposalHashOk(env, env["body"]["header"].as<JsonObjectConst>(), h, subj);
    Serial.printf("[rx] PROPOSAL h=%lu r=%lu from %.10s.. hash %s\n", (unsigned long)h, (unsigned long)r, (const char*)(env["from"] | "?"), ok ? "OK" : "MISMATCH");
    if (!ok) { st.badProposals++; light.noteRound(h, r); return; }
    doVote(light.onProposal(h, r, subj, true));
  } else if (strcmp(type, "PREVOTE") == 0) {
    char subj[65];
    if (!ss_norm_hash(env["subject"] | "", subj)) return;
    Serial.printf("[rx] PREVOTE h=%lu r=%lu from %.10s..\n", (unsigned long)h, (unsigned long)r, (const char*)(env["from"] | "?"));
    doVote(light.onPrevote(h, r, subj, env["from"] | ""));
  } else if (strcmp(type, "PRECOMMIT") == 0) {
    Serial.printf("[rx] PRECOMMIT h=%lu r=%lu from %.10s..\n", (unsigned long)h, (unsigned long)r, (const char*)(env["from"] | "?"));
    light.noteRound(h, r);
  } else if (strcmp(type, "COMMIT") == 0) {
    st.commits++;
    JsonObjectConst body = env["body"].as<JsonObjectConst>();
    uint32_t hh = body["header"]["height"].as<uint32_t>();
    const char* rv[SS_MAX_COMMITTEE]; int nrv = 0;
    bool selfListed = false;
    for (JsonVariantConst x : body["revoked"].as<JsonArrayConst>()) {
      const char* a = x.as<const char*>();
      if (!a || nrv >= SS_MAX_COMMITTEE) continue;
      rv[nrv++] = a;
      if (ss_addr_eq(a, g_selfAddr)) selfListed = true;
    }
    bool inQc = false; int nSigners = 0;
    for (JsonVariantConst x : body["qc"]["signers"].as<JsonArrayConst>()) {
      const char* a = x.as<const char*>();
      nSigners++;
      if (a && ss_addr_eq(a, g_selfAddr)) inQc = true;
    }
    if (inQc) st.inQc++;
    light.onCommit(hh, rv, nrv, body["master"].as<const char*>());   // null master is ignored
    Serial.printf("[rx] COMMIT h=%lu signers=%d board-in-QC=%s revoked=%d -> curHeight=%lu\n",
                  (unsigned long)hh, nSigners, inQc ? "YES" : "no", nrv, (unsigned long)light.height());
    if (selfListed) {
      g_selfRevokeLeft = 0;                                     // peers have committed it; stop resending
      if (g_state != ST_REVOKED) {
        Serial.println("[state] REVOKED by peers: own address in COMMIT.revoked - voting stopped, still REGISTERing");
        g_reason = "revoked by peers";
        enterState(ST_REVOKED, "own address in COMMIT.revoked");
      } else if (!g_peerRevokeLogged) {
        Serial.println("[state] REVOKED confirmed by peers (COMMIT.revoked)");
      }
      g_peerRevokeLogged = true;
    }
  } else if (strcmp(type, "STATUS") == 0) {
    light.noteRound(h, r);
  }
}

static void handleDatagram(int len) {
  st.rx++;
  DeserializationError err = deserializeJson(doc, rxBuf, (size_t)len);   // zero-copy into rxBuf
  if (err) { st.badJson++; Serial.printf("[rx] bad JSON (%s), %d bytes\n", err.c_str(), len); return; }
  const char* hub = doc["hub"] | "";
  if (strcmp(hub, "DELIVER") == 0) {
    handleEnvelope(doc["env"].as<JsonObjectConst>());
  } else if (strcmp(hub, "CONTROL") == 0) {
    const char* cmd = doc["cmd"] | "";
    Serial.printf("[hub] CONTROL %s %s\n", cmd, (const char*)(doc["mode"] | ""));
    if (strcmp(cmd, "rogue") == 0) triggerRogue("hub CONTROL rogue");
  }
}

static void pollUdp() {
  if (!g_udpUp) return;
  for (int guard = 0; guard < 16; guard++) {                    // drain the socket, bounded per loop pass
    int size = udp.parsePacket();
    if (size <= 0) return;
    if (size > MAX_LIGHT_DGRAM) {
      st.oversize++;
      Serial.printf("[rx] oversize datagram %d B > %d, ignored\n", size, MAX_LIGHT_DGRAM);
      while (udp.read((uint8_t*)rxBuf, RX_BUF_SIZE) > 0) {}   // drain: parsePacket() returns 0 while a packet is buffered
      continue;
    }
    int n = udp.read((uint8_t*)rxBuf, RX_BUF_SIZE);
    if (n <= 0) continue;                                        // size <= 1400 < RX_BUF_SIZE, so one read drains it
    rxBuf[n] = 0;
    handleDatagram(n);
  }
}

// ---------------------------------------------------------------------------------------------
// setup / loop
// ---------------------------------------------------------------------------------------------
static void runSelfTest() {
  SsSelfTestResult r;
  bool ok = ss_selftest(r);
  if (ok) {
    Serial.printf("[crypto] SELFTEST PASS (sign %u ms)\n", (unsigned)r.signMs);
  } else if (r.layoutOk && r.verifyOk && !r.signOk) {
    Serial.printf("[crypto] SELFTEST VERIFY-ONLY: signatures valid but not byte-identical (%s)\n", r.failStep);
  } else {
    Serial.printf("[crypto] SELFTEST FAIL step=%s layout=%d sign=%d verify=%d highS=%d\n",
                  r.failStep, r.layoutOk, r.signOk, r.verifyOk, r.highSRejected);
  }
  g_cryptoOk = r.layoutOk && r.verifyOk;                        // valid signatures are required before we ever sign
}
static bool loadIdentity() {
  bool ok = ss_hex2bin(NODE_PRIVKEY_HEX, g_priv, 32) && ss_norm_hash(MISSION_ID_HEX, g_missionHex) &&
            ss_hex2bin(g_missionHex, g_mission, 32);
  strncpy(g_selfAddr, NODE_ADDRESS, 42); g_selfAddr[42] = 0;
  for (char* p = g_selfAddr; *p; ++p) *p = ss_lc(*p);
  light.init(g_selfAddr, COMMITTEE_ADDR, COMMITTEE_WEIGHT, COMMITTEE_SIZE);
  if (!light.ready()) { Serial.println("[boot] config.h: NODE_ADDRESS is not in COMMITTEE_ADDR"); ok = false; }
  return ok;
}

void setup() {
  // USB CDC: with no reader attached, each print would block up to 100 ms and stall voting (measured: 8 s lag).
  // Bigger buffer + zero timeout = logs are dropped instead of blocking when nobody reads the port.
#if ARDUINO_USB_MODE && ARDUINO_USB_CDC_ON_BOOT                  // Serial is HWCDC (USB Serial/JTAG)
  Serial.setTxBufferSize(4096);
  Serial.setTxTimeoutMs(0);
#endif
  Serial.begin(115200);
  delay(2000);
  Serial.printf("\n[boot] SwarmShield swarm_member %s, %s (%s) cluster %d, light validator\n", FW_VERSION, NODE_NAME, NODE_ADDRESS, CLUSTER_ID);

  nr.begin();                                                   // shared I2C: once, first, never Wire.begin()
  Wire.setClock(I2C_CLOCK_HZ);                                  // 100 kHz: WiFi bursts corrupted 400 kHz reads (measured)
  wakeMPU();
  setServo(SERVO_ACTIVE);
  {                                                             // I2C scan: expect 0x08 STM32, 0x3C OLED, 0x68 MPU
    char found[96]; int len = 0; found[0] = 0;
    for (uint8_t a = 1; a < 127; a++) {
      Wire.beginTransmission(a);
      if (Wire.endTransmission(true) == 0 && len < 88) len += snprintf(found + len, sizeof(found) - len, " 0x%02X", a);
    }
    Serial.printf("[boot] I2C devices:%s\n", len ? found : " none");
  }
  if (oled.begin()) { Serial.println("[boot] OLED 0x3C ready"); oled.boot(NODE_NAME); }
  else              Serial.println("[boot] OLED not found at 0x3C - status screen disabled");
  bool mcOk = false;
  for (int i = 0; i < 10 && !mcOk; i++) { mcOk = nr.updateSensors(); if (!mcOk) delay(200); }   // STM32 may boot slower
  g_mcOk = mcOk;
  if (mcOk) Serial.printf("[boot] Neurick ready, battery %.2f V\n", nr.batteryVolts);
  else      Serial.println("[boot] No reply from motion controller after 2 s - is the battery switch ON?");
  g_servoAngle = -1; setServo(SERVO_ACTIVE);                   // resend now that the STM32 had time to boot

  bool idOk = loadIdentity();
  if (!idOk) Serial.println("[boot] config.h identity INVALID - will not sign");
  runSelfTest();
  if (!idOk) g_cryptoOk = false;
  Serial.printf("[boot] committee %d members, active weight %lu, voting %s\n", COMMITTEE_SIZE,
                (unsigned long)light.activeWeight(), FW_VOTING ? "ON (L2)" : "OFF (L1)");

  delay(500);                                                   // let the servo settle before the MPU baseline
  calibrateBaseline();
  startWiFi();
  Serial.println("[ready] USER button = rogue double-vote (servo 150) | shake = SELF_REVOKE (servo 30)");
}

void loop() {
  uint32_t now = millis();
  maintainNetwork(now);
  pollUdp();
  pollTriggers(now);
  // Hold ROGUE on screen >= 3 s: peers can revoke within one block (~1 s), too fast to see.
  int shown = (g_state == ST_REVOKED && tLastRogue && now - tLastRogue < 3000) ? (int)ST_ROGUE : (int)g_state;
  oled.status(shown, g_reason, light.height(), st.inQc, st.commits, WiFi.status() == WL_CONNECTED,
              g_cryptoOk, now, g_oledDirty);
  g_oledDirty = false;
  if (now - tLastHeap >= 10000) {
    tLastHeap = now;
    Serial.printf("[heap] free=%u min=%u | rx=%lu props=%lu bad=%lu commits=%lu inQC=%lu pv=%lu pc=%lu oversize=%lu badjson=%lu sendfail=%lu h=%lu state=%d\n",
                  (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getMinFreeHeap(), (unsigned long)st.rx, (unsigned long)st.proposals,
                  (unsigned long)st.badProposals, (unsigned long)st.commits, (unsigned long)st.inQc, (unsigned long)st.prevotesSent,
                  (unsigned long)st.precommitsSent, (unsigned long)st.oversize, (unsigned long)st.badJson, (unsigned long)st.sendFail,
                  (unsigned long)light.height(), (int)g_state);
  }
  delay(1);
}
