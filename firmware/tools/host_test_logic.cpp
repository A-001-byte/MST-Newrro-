// host_test_logic.cpp: unit tests for swarm_logic.h (light validator L0-L2 logic) with no board.
// Committee mirrors the spec demo: 4 full validators (weight 3) + the board (weight 1) = active weight 13, quorum needs >= 9...
// (3w > 2*13 -> w >= 9). The board's own prevote counts, because the hub never echoes it back.
#define SS_HOST_TEST 1
#include <stdio.h>
#include <string.h>
#include "../swarm_member/test_vectors.h"
#include "host_sha256.h"
#include "../swarm_member/swarm_logic.h"

static int fails = 0;
#define CHECK(cond, msg) do { if (cond) printf("  ok   %s\n", msg); else { printf("  FAIL %s\n", msg); fails++; } } while (0)

static const char* A[5] = {
  "0x1000000000000000000000000000000000000001", "0x2000000000000000000000000000000000000002",
  "0x3000000000000000000000000000000000000003", "0x4000000000000000000000000000000000000004",
  "0x9000000000000000000000000000000000000009" };   // A[4] = the board
static const uint16_t W[5] = { 3, 3, 3, 3, 1 };

static void mk(char out[65], char c) { for (int i = 0; i < 64; i++) out[i] = c; out[64] = 0; }
static SsLight fresh() { SsLight l; l.init(A[4], A, W, 5); return l; }

int main() {
  char H1[65], H2[65], NIL[65]; mk(H1, 'a'); mk(H2, 'b'); mk(NIL, '0');

  { SsLight l = fresh();
    CHECK(l.ready() && l.height() == 1 && l.activeWeight() == 13, "init: ready, curHeight=1, active weight 13");
    CHECK(l.indexOf("0x9000000000000000000000000000000000000009") == 4 && l.indexOf("0X4000000000000000000000000000000000000004") == 3,
          "indexOf is case-insensitive; board is index 4");
    SsAction a = l.onProposal(1, 0, H1, true);
    CHECK(a.prevote && !a.precommit && a.height == 1 && a.round == 0 && strcmp(a.subject, H1) == 0, "valid proposal at curHeight -> PREVOTE");
    a = l.onProposal(1, 0, H1, true);
    CHECK(!a.prevote, "PREVOTE once per (h,r): repeated proposal ignored");
  }
  { SsLight l = fresh();
    CHECK(!l.onProposal(1, 0, H1, false).prevote, "proposal with a wrong recomputed hash -> no vote");
    CHECK(!l.onProposal(2, 0, H1, true).prevote, "proposal for h != curHeight -> no vote");
  }
  { SsLight l = fresh();                      // quorum arithmetic: own 1 + 3 + 3 = 7 (21 > 26 false); + 3 = 10 (30 > 26 true)
    l.onProposal(1, 0, H1, true);
    SsAction a = l.onPrevote(1, 0, H1, A[0]); CHECK(!a.precommit, "own + 1 full (w=4): no precommit");
    a = l.onPrevote(1, 0, H1, A[1]);          CHECK(!a.precommit, "own + 2 full (w=7): 3*7=21 <= 26, no precommit");
    a = l.onPrevote(1, 0, H1, A[2]);
    CHECK(a.precommit && a.height == 1 && a.round == 0 && strcmp(a.subject, H1) == 0, "own + 3 full (w=10): 30 > 26 -> PRECOMMIT");
    a = l.onPrevote(1, 0, H1, A[3]);          CHECK(!a.precommit, "PRECOMMIT once per (h,r)");
  }
  { SsLight l = fresh();                      // duplicates from one sender count once
    l.onProposal(1, 0, H1, true);
    for (int i = 0; i < 6; i++) l.onPrevote(1, 0, H1, A[0]);
    SsAction a = l.onPrevote(1, 0, H1, A[1]);
    CHECK(!a.precommit, "same sender repeated 6x counts once (w = 1+3+3 = 7)");
  }
  { SsLight l = fresh();                      // things that must not count
    l.onProposal(1, 0, H1, true);
    l.onPrevote(1, 0, NIL, A[0]); l.onPrevote(1, 0, H2, A[1]);
    l.onPrevote(1, 0, H1, "0xdead000000000000000000000000000000000000");
    l.onPrevote(2, 0, H1, A[2]);
    SsAction a = l.onPrevote(1, 0, H1, A[3]);
    CHECK(!a.precommit, "nil, other-subject, unknown sender and wrong-height prevotes do not count (only own + 1 full)");
  }
  { SsLight l = fresh();                      // prevotes arrive before the proposal (UDP reordering)
    SsAction a = l.onPrevote(1, 0, H1, A[0]); a = l.onPrevote(1, 0, H1, A[1]); a = l.onPrevote(1, 0, H1, A[2]);
    CHECK(!a.precommit, "no PRECOMMIT before we have prevoted (no proposal yet)");
    a = l.onProposal(1, 0, H1, true);
    CHECK(a.prevote && a.precommit, "proposal after 3 prevotes -> PREVOTE and PRECOMMIT in one action (own vote completes quorum)");
  }
  { SsLight l = fresh();                      // revoked weight is excluded: active = 10, need 3w > 20 -> w >= 7
    const char* rv[1] = { A[0] };
    l.onCommit(0, rv, 1, A[1]);
    CHECK(l.activeWeight() == 10 && l.height() == 1, "COMMIT(header 0) revoking full validator #1 -> active weight 10");
    l.onProposal(1, 0, H1, true);
    SsAction a = l.onPrevote(1, 0, H1, A[0]); CHECK(!a.precommit, "prevote from a revoked member is ignored");
    a = l.onPrevote(1, 0, H1, A[1]);          CHECK(!a.precommit, "own + 1 full = 4: no");
    a = l.onPrevote(1, 0, H1, A[2]);          CHECK(a.precommit, "own + 2 full = 7 > 2/3 of 10 -> PRECOMMIT with reduced active weight");
    CHECK(strcmp(l.master(), A[1]) == 0, "master follows COMMIT");
  }
  { SsLight l = fresh();                      // height tracking
    l.onCommit(5, nullptr, 0, nullptr);
    CHECK(l.height() == 6, "COMMIT header.height=5 -> curHeight=6");
    l.onCommit(3, nullptr, 0, nullptr);
    CHECK(l.height() == 6, "stale COMMIT never lowers curHeight");
    CHECK(l.onProposal(6, 0, H1, true).prevote, "proposal at the new curHeight -> vote");
  }
  { SsLight l = fresh();                      // rounds
    l.onProposal(1, 0, H1, true);
    CHECK(l.onProposal(1, 1, H2, true).prevote, "round 1 proposal after round 0 -> new PREVOTE");
    CHECK(!l.onProposal(1, 0, H1, true).prevote, "older round proposal after round 1 -> ignored");
    CHECK(l.round() == 1, "lastRound tracks the highest round seen at curHeight");
    l.onPrevote(1, 0, H1, A[0]); l.onPrevote(1, 0, H1, A[1]);
    SsAction a = l.onPrevote(1, 1, H2, A[0]); a = l.onPrevote(1, 1, H2, A[1]); a = l.onPrevote(1, 1, H2, A[2]);
    CHECK(a.precommit && a.round == 1 && strcmp(a.subject, H2) == 0, "quorum is tallied per round on the subject we voted for");
  }
  { SsLight l = fresh();                      // self revoked (peers) -> stop voting
    const char* rv[1] = { A[4] };
    l.onCommit(3, rv, 1, A[0]);
    CHECK(l.selfRevoked() && l.height() == 4, "own address in COMMIT.revoked -> selfRevoked");
    CHECK(!l.onProposal(4, 0, H1, true).prevote, "revoked board no longer votes");
  }
  { SsLight l = fresh();                      // tamper: local self-revoke stops voting
    l.markSelfRevoked();
    CHECK(l.selfRevoked() && !l.onProposal(1, 0, H1, true).prevote, "markSelfRevoked stops voting");
  }
  { SsLight l = fresh();
    char sub[65];
    CHECK(!l.proposalSubject(1, sub), "no proposal seen yet -> proposalSubject false");
    l.onProposal(1, 0, H1, true);
    CHECK(l.proposalSubject(1, sub) && strcmp(sub, H1) == 0, "proposalSubject returns the valid proposal (rogue double-vote uses it)");
  }
  { char out[65];
    CHECK(ss_norm_hash("0xABCDEF0123456789abcdef0123456789ABCDEF0123456789abcdef0123456789", out) &&
          out[0] == 'a' && strlen(out) == 64, "ss_norm_hash lowercases and strips 0x");
    CHECK(!ss_norm_hash("0x1234", out) && !ss_norm_hash("0xzz34567890123456789012345678901234567890123456789012345678901234", out), "ss_norm_hash rejects bad input");
  }

  // ---- outgoing JSON: printed for python (run_tests.py) to parse and verify against the vectors ----
  { char reg[256], env[900];
    int n = ss_build_register(reg, sizeof(reg), SV_K5_ADDR, "c1-board", 1);
    CHECK(n > 0, "REGISTER built");
    printf("REGISTER %s\n", reg);
    const SsTestStmt& t = SV_K5_STMTS[0];             // prevote_block, signed by k5
    uint8_t r[32], s[32]; ss_hex2bin(t.r, r, 32); ss_hex2bin(t.s, s, 32);
    n = ss_build_signed_send(env, sizeof(env), "PREVOTE", SV_MISSION_HEX, t.cluster, SV_K5_ADDR, (uint32_t)t.height, t.round, t.subject, r, s, "{}");
    CHECK(n > 0 && n < 1400, "signed PREVOTE envelope fits well under 1400 bytes");
    printf("ENVELOPE %s\n", env);
    char tiny[20]; CHECK(ss_build_signed_send(tiny, sizeof(tiny), "PREVOTE", SV_MISSION_HEX, 1, SV_K5_ADDR, 1, 0, t.subject, r, s, "{}") == -1,
                        "builder reports overflow instead of truncating");
  }

  printf(fails ? "LOGIC TEST FAIL (%d)\n" : "LOGIC TEST PASS\n", fails);
  return fails ? 1 : 0;
}
