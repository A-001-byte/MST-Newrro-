// swarm_crypto.h: SwarmShield v2 byte formats + secp256k1 signing for the Neurick board.
// Spec: docs/00_CONTEXT.md section 10 (crypto). Ground truth: ledger/test_vectors/vectors.json.
//
// Two layers:
//   1. LAYOUT (pure C++, host-testable): statement (111 B), block hash (167 B), hex helpers, low-s check.
//   2. SIGN/VERIFY (mbedtls 3.x, board only; compiled out when SS_HOST_TEST is defined).
// Rules: big-endian everywhere; no Arduino String; every mbedtls context is freed on every path.
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <string.h>

#if defined(SS_HOST_TEST)
void ss_sha256(const uint8_t* data, size_t len, uint8_t out[32]);   // provided by the host test
#else
#include "mbedtls/sha256.h"
static inline void ss_sha256(const uint8_t* data, size_t len, uint8_t out[32]) {
  mbedtls_sha256(data, len, out, 0);
}
#endif

// ---------------------------------------------------------------------------------------------
// constants (spec 10.2, 10.3, 10.5, 10.10)
// ---------------------------------------------------------------------------------------------
enum {
  SS_MSG_PROPOSAL = 1, SS_MSG_PREVOTE = 2, SS_MSG_PRECOMMIT = 3,
  SS_MSG_XC_PACKET = 4, SS_MSG_XC_ACK = 5, SS_MSG_TX = 6, SS_MSG_SELF_REVOKE = 7
};
#define SS_STMT_LEN   111
#define SS_BLOCK_LEN  167
#define SS_BLOCK_TAG  0x10

static const uint8_t SS_DOMAIN[32] = {   // "SWARMSHIELD_V1" right-padded with 0x00
  'S','W','A','R','M','S','H','I','E','L','D','_','V','1', 0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0 };
static const uint8_t SS_NIL[32] = { 0 };
static const uint8_t SS_SECP256K1_N[32] = {
  0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xfe,
  0xba,0xae,0xdc,0xe6,0xaf,0x48,0xa0,0x3b,0xbf,0xd2,0x5e,0x8c,0xd0,0x36,0x41,0x41 };
static const uint8_t SS_SECP256K1_HALF_N[32] = {
  0x7f,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,0xff,
  0x5d,0x57,0x6e,0x73,0x57,0xa4,0x50,0x1d,0xdf,0xe9,0x2f,0x46,0x68,0x1b,0x20,0xa0 };

// ---------------------------------------------------------------------------------------------
// big-endian writers + hex
// ---------------------------------------------------------------------------------------------
static inline void ss_put_u16(uint8_t* p, uint16_t v) { p[0] = (uint8_t)(v >> 8); p[1] = (uint8_t)v; }
static inline void ss_put_u32(uint8_t* p, uint32_t v) {
  p[0] = (uint8_t)(v >> 24); p[1] = (uint8_t)(v >> 16); p[2] = (uint8_t)(v >> 8); p[3] = (uint8_t)v;
}
static inline void ss_put_u64(uint8_t* p, uint64_t v) {
  ss_put_u32(p, (uint32_t)(v >> 32)); ss_put_u32(p + 4, (uint32_t)v);
}

static inline int ss_hexval(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}
// Decode exactly n bytes from hex (optional 0x prefix). Returns false on bad length/char.
static inline bool ss_hex2bin(const char* hex, uint8_t* out, size_t n) {
  if (!hex) return false;
  if (hex[0] == '0' && (hex[1] == 'x' || hex[1] == 'X')) hex += 2;
  for (size_t i = 0; i < n; i++) {
    int a = ss_hexval(hex[2 * i]);
    if (a < 0) return false;
    int b = ss_hexval(hex[2 * i + 1]);
    if (b < 0) return false;
    out[i] = (uint8_t)((a << 4) | b);
  }
  return hex[2 * n] == '\0';
}
// Lowercase hex, NUL-terminated; out must hold 2n+1 chars.
static inline void ss_bin2hex(const uint8_t* in, size_t n, char* out) {
  static const char H[] = "0123456789abcdef";
  for (size_t i = 0; i < n; i++) { out[2 * i] = H[in[i] >> 4]; out[2 * i + 1] = H[in[i] & 15]; }
  out[2 * n] = '\0';
}

// ---------------------------------------------------------------------------------------------
// 1a. signed statement, 111 bytes (spec 10.3)
// ---------------------------------------------------------------------------------------------
static inline void ss_build_statement(uint8_t out[SS_STMT_LEN], uint8_t msgType, const uint8_t mission[32],
                                      uint16_t cluster, uint64_t height, uint32_t round,
                                      const uint8_t subject[32]) {
  memcpy(out, SS_DOMAIN, 32);                 //   0..31  DOMAIN
  out[32] = msgType;                          //  32      msgType
  memcpy(out + 33, mission, 32);              //  33..64  missionId
  ss_put_u16(out + 65, cluster);              //  65..66  clusterId
  ss_put_u64(out + 67, height);               //  67..74  height
  ss_put_u32(out + 75, round);                //  75..78  round
  memcpy(out + 79, subject, 32);              //  79..110 subject
}
static inline void ss_statement_digest(uint8_t digest[32], uint8_t msgType, const uint8_t mission[32],
                                       uint16_t cluster, uint64_t height, uint32_t round,
                                       const uint8_t subject[32]) {
  uint8_t buf[SS_STMT_LEN];
  ss_build_statement(buf, msgType, mission, cluster, height, round, subject);
  ss_sha256(buf, SS_STMT_LEN, digest);
}

// ---------------------------------------------------------------------------------------------
// 1b. block hash, 167 bytes, round NOT included (spec 10.5)
// ---------------------------------------------------------------------------------------------
struct SsHeader {
  uint8_t  mission[32];
  uint16_t cluster;
  uint64_t height;
  uint8_t  prevHash[32];
  uint8_t  txRoot[32];
  uint8_t  proposer[20];
  uint64_t timestampMs;
};
static inline void ss_block_preimage(uint8_t out[SS_BLOCK_LEN], const SsHeader& h) {
  memcpy(out, SS_DOMAIN, 32);                 //   0..31  DOMAIN
  out[32] = SS_BLOCK_TAG;                     //  32      0x10
  memcpy(out + 33, h.mission, 32);            //  33..64
  ss_put_u16(out + 65, h.cluster);            //  65..66
  ss_put_u64(out + 67, h.height);             //  67..74
  memcpy(out + 75, h.prevHash, 32);           //  75..106
  memcpy(out + 107, h.txRoot, 32);            // 107..138
  memcpy(out + 139, h.proposer, 20);          // 139..158
  ss_put_u64(out + 159, h.timestampMs);       // 159..166
}
static inline void ss_block_hash(uint8_t out[32], const SsHeader& h) {
  uint8_t buf[SS_BLOCK_LEN];
  ss_block_preimage(buf, h);
  ss_sha256(buf, SS_BLOCK_LEN, out);
}

// ---------------------------------------------------------------------------------------------
// 1c. low-s check on a 32-byte big-endian s (spec 10.10: reject s > n/2)
// ---------------------------------------------------------------------------------------------
static inline bool ss_is_low_s(const uint8_t s[32]) { return memcmp(s, SS_SECP256K1_HALF_N, 32) <= 0; }

// ---------------------------------------------------------------------------------------------
// 2. mbedtls secp256k1 (board only)
// ---------------------------------------------------------------------------------------------
#if !defined(SS_HOST_TEST)
#include <stdio.h>
#include <Arduino.h>
#include "mbedtls/ecp.h"
#include "mbedtls/ecdsa.h"
#include "mbedtls/bignum.h"
#include "mbedtls/md.h"
#include "esp_random.h"

// Blinding RNG for mbedtls 3.x (sign_det_ext requires a non-NULL RNG).
static int ss_rng(void*, unsigned char* buf, size_t len) { esp_fill_random(buf, len); return 0; }

// Deterministic RFC 6979 ECDSA (HMAC-SHA256) over a 32-byte digest, low-s normalised.
// r and s are written as 32-byte big-endian. Returns 0 on success, else an mbedtls error code (never 0 on failure).
static inline int ss_sign_digest(const uint8_t priv[32], const uint8_t digest[32], uint8_t r_out[32], uint8_t s_out[32]) {
  mbedtls_ecp_group grp; mbedtls_mpi d, r, s, n, half;
  mbedtls_ecp_group_init(&grp);
  mbedtls_mpi_init(&d); mbedtls_mpi_init(&r); mbedtls_mpi_init(&s); mbedtls_mpi_init(&n); mbedtls_mpi_init(&half);
  int ret;
  if ((ret = mbedtls_ecp_group_load(&grp, MBEDTLS_ECP_DP_SECP256K1)) != 0) goto done;
  if ((ret = mbedtls_mpi_read_binary(&d, priv, 32)) != 0) goto done;
  if ((ret = mbedtls_ecdsa_sign_det_ext(&grp, &r, &s, &d, digest, 32, MBEDTLS_MD_SHA256, ss_rng, NULL)) != 0) goto done;
  if ((ret = mbedtls_mpi_read_binary(&n, SS_SECP256K1_N, 32)) != 0) goto done;
  if ((ret = mbedtls_mpi_read_binary(&half, SS_SECP256K1_HALF_N, 32)) != 0) goto done;
  if (mbedtls_mpi_cmp_mpi(&s, &half) > 0) {              // low-s: s = n - s
    if ((ret = mbedtls_mpi_sub_mpi(&s, &n, &s)) != 0) goto done;
  }
  if ((ret = mbedtls_mpi_write_binary(&r, r_out, 32)) != 0) goto done;
  if ((ret = mbedtls_mpi_write_binary(&s, s_out, 32)) != 0) goto done;
done:
  mbedtls_mpi_free(&d); mbedtls_mpi_free(&r); mbedtls_mpi_free(&s); mbedtls_mpi_free(&n); mbedtls_mpi_free(&half);
  mbedtls_ecp_group_free(&grp);
  return ret;
}

// Verify (r,s) over digest against a 65-byte uncompressed public key. Rejects high-s.
static inline bool ss_verify_digest(const uint8_t pub65[65], const uint8_t digest[32],
                                    const uint8_t r_in[32], const uint8_t s_in[32]) {
  if (!ss_is_low_s(s_in)) return false;
  mbedtls_ecp_group grp; mbedtls_ecp_point Q; mbedtls_mpi r, s;
  mbedtls_ecp_group_init(&grp); mbedtls_ecp_point_init(&Q); mbedtls_mpi_init(&r); mbedtls_mpi_init(&s);
  bool ok = false;
  if (mbedtls_ecp_group_load(&grp, MBEDTLS_ECP_DP_SECP256K1) == 0 &&
      mbedtls_ecp_point_read_binary(&grp, &Q, pub65, 65) == 0 &&
      mbedtls_mpi_read_binary(&r, r_in, 32) == 0 && mbedtls_mpi_read_binary(&s, s_in, 32) == 0 &&
      mbedtls_ecdsa_verify(&grp, digest, 32, &Q, &r, &s) == 0) ok = true;
  mbedtls_mpi_free(&r); mbedtls_mpi_free(&s); mbedtls_ecp_point_free(&Q); mbedtls_ecp_group_free(&grp);
  return ok;
}
#endif  // !SS_HOST_TEST

// ---------------------------------------------------------------------------------------------
// Self-test against vectors.json (test_vectors.h must be included first by the caller)
// ---------------------------------------------------------------------------------------------
#if defined(SS_SELFTEST_AVAILABLE)   // defined by test_vectors.h consumers: #define before including this header
struct SsSelfTestResult {
  bool layoutOk;      // statement bytes, digest, block hash all match the vectors
  bool signOk;        // every k5 signature is byte-identical to the vector (mbedtls RFC6979)
  bool verifyOk;      // every vector signature verifies against k5's public key (board only)
  bool highSRejected; // the negative high-s vector is rejected
  char failStep[48];  // first failing step, "" if none
  uint32_t signMs;    // time of the prevote_block signature (board only)
};

// Layout part: pure, runs on the host too.
static inline bool ss_selftest_layout(SsSelfTestResult& res) {
  uint8_t mission[32];
  if (!ss_hex2bin(SV_MISSION_HEX, mission, 32)) { strcpy(res.failStep, "mission-hex"); return false; }
  for (int i = 0; i < SV_K5_STMTS_N; i++) {
    const SsTestStmt& t = SV_K5_STMTS[i];
    uint8_t subj[32], stmt[SS_STMT_LEN], enc[SS_STMT_LEN], dig[32], want[32];
    ss_hex2bin(t.subject, subj, 32);
    ss_build_statement(stmt, t.msgType, mission, t.cluster, t.height, t.round, subj);
    ss_hex2bin(t.encoded, enc, SS_STMT_LEN);
    if (memcmp(stmt, enc, SS_STMT_LEN) != 0) { strcpy(res.failStep, "statement-bytes:"); strncat(res.failStep, t.name, 24); return false; }
    ss_sha256(stmt, SS_STMT_LEN, dig);
    ss_hex2bin(t.digest, want, 32);
    if (memcmp(dig, want, 32) != 0) { strcpy(res.failStep, "digest:"); strncat(res.failStep, t.name, 38); return false; }
  }
  SsHeader h; memcpy(h.mission, mission, 32);
  h.cluster = SV_BLOCK.cluster; h.height = SV_BLOCK.height; h.timestampMs = SV_BLOCK.timestampMs;
  uint8_t pre[SS_BLOCK_LEN], wantPre[SS_BLOCK_LEN], bh[32], wantBh[32];
  if (!ss_hex2bin(SV_BLOCK.prevHash, h.prevHash, 32) || !ss_hex2bin(SV_BLOCK.txRoot, h.txRoot, 32) ||
      !ss_hex2bin(SV_BLOCK.proposer, h.proposer, 20)) { strcpy(res.failStep, "header-hex"); return false; }
  ss_block_preimage(pre, h);
  ss_hex2bin(SV_BLOCK.preimage, wantPre, SS_BLOCK_LEN);
  if (memcmp(pre, wantPre, SS_BLOCK_LEN) != 0) { strcpy(res.failStep, "block-preimage"); return false; }
  ss_block_hash(bh, h);
  ss_hex2bin(SV_BLOCK.blockHash, wantBh, 32);
  if (memcmp(bh, wantBh, 32) != 0) { strcpy(res.failStep, "block-hash"); return false; }
  uint8_t hs[32];
  ss_hex2bin(SV_HIGHS_S_HEX, hs, 32);
  res.highSRejected = !ss_is_low_s(hs);
  if (!res.highSRejected) { strcpy(res.failStep, "high-s-not-rejected"); return false; }
  for (int i = 0; i < SV_K5_STMTS_N; i++) {            // vector sigs themselves must be low-s
    uint8_t s[32]; ss_hex2bin(SV_K5_STMTS[i].s, s, 32);
    if (!ss_is_low_s(s)) { strcpy(res.failStep, "vector-not-low-s"); return false; }
  }
  return true;
}

#if !defined(SS_HOST_TEST)
// Full test: layout + sign + verify. millis() comes from Arduino.
static inline bool ss_selftest(SsSelfTestResult& res) {
  memset(&res, 0, sizeof(res));
  res.layoutOk = ss_selftest_layout(res);
  if (!res.layoutOk) return false;
  uint8_t mission[32], priv[32], pub[65];
  ss_hex2bin(SV_MISSION_HEX, mission, 32); ss_hex2bin(SV_K5_PRIV_HEX, priv, 32); ss_hex2bin(SV_K5_PUB_HEX, pub, 65);
  res.signOk = true; res.verifyOk = true;
  for (int i = 0; i < SV_K5_STMTS_N; i++) {
    const SsTestStmt& t = SV_K5_STMTS[i];
    uint8_t subj[32], dig[32], r[32], s[32], wr[32], ws[32];
    ss_hex2bin(t.subject, subj, 32);
    ss_statement_digest(dig, t.msgType, mission, t.cluster, t.height, t.round, subj);
    uint32_t t0 = millis();
    int rc = ss_sign_digest(priv, dig, r, s);
    uint32_t dt = millis() - t0;
    if (i == 0) res.signMs = dt;
    if (rc != 0) { snprintf(res.failStep, sizeof(res.failStep), "sign-rc=-0x%04x:%s", (unsigned)-rc, t.name); res.signOk = false; return false; }
    ss_hex2bin(t.r, wr, 32); ss_hex2bin(t.s, ws, 32);
    if (memcmp(r, wr, 32) != 0 || memcmp(s, ws, 32) != 0) {
      snprintf(res.failStep, sizeof(res.failStep), "sign-bytes:%s", t.name); res.signOk = false;   // fallback rule: verify still decides
    }
    if (!ss_verify_digest(pub, dig, r, s)) { snprintf(res.failStep, sizeof(res.failStep), "verify-own:%s", t.name); res.verifyOk = false; return false; }
    if (!ss_verify_digest(pub, dig, wr, ws)) { snprintf(res.failStep, sizeof(res.failStep), "verify-vector:%s", t.name); res.verifyOk = false; return false; }
  }
  return res.signOk && res.verifyOk;
}
#endif
#endif  // SS_SELFTEST_AVAILABLE
