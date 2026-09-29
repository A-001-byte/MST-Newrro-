// host_test.cpp: checks the firmware byte layouts (statement 111 B, block hash 167 B, low-s) against
// vectors.json WITHOUT the board. Run everything with:  python tools/run_tests.py
#define SS_HOST_TEST 1
#define SS_SELFTEST_AVAILABLE 1
#include <stdio.h>
#include <string.h>
#include "../swarm_member/test_vectors.h"
#include "host_sha256.h"
#include "../swarm_member/swarm_crypto.h"

static int fails = 0;
#define CHECK(cond, msg) do { if (cond) printf("  ok   %s\n", msg); else { printf("  FAIL %s\n", msg); fails++; } } while (0)

int main() {
  uint8_t d[32], want[32];
  ss_sha256((const uint8_t*)"", 0, d);
  ss_hex2bin("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", want, 32);
  CHECK(memcmp(d, want, 32) == 0, "sha256(\"\") == spec empty txRoot");
  ss_sha256((const uint8_t*)"abc", 3, d);
  ss_hex2bin("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", want, 32);
  CHECK(memcmp(d, want, 32) == 0, "sha256(\"abc\") known answer");

  uint8_t b[4]; CHECK(ss_hex2bin("0xdeadBEEF", b, 4) && b[0] == 0xde && b[3] == 0xef, "hex2bin accepts 0x + mixed case");
  CHECK(!ss_hex2bin("0xdeadbee", b, 4), "hex2bin rejects short input");
  CHECK(!ss_hex2bin("0xdeadbeefaa", b, 4), "hex2bin rejects long input");
  char hx[9]; ss_bin2hex(b, 4, hx); CHECK(strcmp(hx, "deadbeef") == 0, "bin2hex lowercase");

  uint8_t dom[32]; ss_hex2bin("535741524d534849454c445f5631000000000000000000000000000000000000", dom, 32);
  CHECK(memcmp(dom, SS_DOMAIN, 32) == 0, "DOMAIN constant matches spec 10.2");
  uint8_t carry = 1, dbl[32];
  for (int i = 31; i >= 0; i--) { unsigned x = (unsigned)SS_SECP256K1_HALF_N[i] * 2 + carry; dbl[i] = (uint8_t)x; carry = (uint8_t)(x >> 8); }
  CHECK(memcmp(dbl, SS_SECP256K1_N, 32) == 0 && carry == 0, "HALF_N is floor(N/2)");

  SsSelfTestResult res; memset(&res, 0, sizeof(res));
  bool ok = ss_selftest_layout(res);
  printf("  layout self-test: %s%s%s\n", ok ? "PASS" : "FAIL ", ok ? "" : "step=", res.failStep);
  CHECK(ok, "statements (bytes+digest) x5, block preimage+hash, high-s rejected");
  CHECK(res.highSRejected, "negative vector: high-s rejected");

  uint8_t mission[32], subj[32], a1[SS_STMT_LEN], a2[SS_STMT_LEN];
  ss_hex2bin(SV_MISSION_HEX, mission, 32); ss_hex2bin(SV_K5_STMTS[0].subject, subj, 32);
  ss_build_statement(a1, 2, mission, 1, 1, 0, subj); ss_build_statement(a2, 2, mission, 1, 1, 1, subj);
  CHECK(memcmp(a1, a2, SS_STMT_LEN) != 0, "different round -> different statement");

  // SELF_REVOKE subject = sha256("tamper") (spec 10.4) must equal the vector's subject
  uint8_t rh[32]; ss_sha256((const uint8_t*)"tamper", 6, rh);
  for (int i = 0; i < SV_K5_STMTS_N; i++) {
    if (strcmp(SV_K5_STMTS[i].name, "self_revoke_tamper") == 0) {
      uint8_t vs[32]; ss_hex2bin(SV_K5_STMTS[i].subject, vs, 32);
      CHECK(memcmp(rh, vs, 32) == 0, "sha256(\"tamper\") == self_revoke_tamper subject");
    }
  }

  printf(fails ? "HOST TEST FAIL (%d)\n" : "HOST TEST PASS\n", fails);
  return fails ? 1 : 0;
}
