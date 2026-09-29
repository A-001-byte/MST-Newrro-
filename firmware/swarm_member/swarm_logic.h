// swarm_logic.h: light-validator protocol logic for the Neurick board (no Arduino / JSON dependency, host-testable).
// Spec: docs/00_CONTEXT.md 7.9 (light validator), 11.2/11.4/11.5 (wire), doc 02 B6 L0-L2.
//  - height tracking from the light COMMIT (curHeight = header.height + 1, never goes backwards)
//  - PREVOTE once per (h,r) on a proposal whose recomputed blockHash equals its subject
//  - PRECOMMIT once per (h,r) when distinct claimed prevoters (incl. our own) exceed 2/3 of active weight
//  - revoked members are excluded from the weight; if WE are revoked we stop voting
#pragma once
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "swarm_crypto.h"

#define SS_MAX_COMMITTEE 16
#define SS_TALLY_SLOTS   4

static inline char ss_lc(char c) { return (c >= 'A' && c <= 'Z') ? (char)(c + 32) : c; }
static inline bool ss_addr_eq(const char* a, const char* b) {
  while (*a && *b) { if (ss_lc(*a) != ss_lc(*b)) return false; a++; b++; }
  return *a == *b;
}
// Normalise "0x<64 hex>" or bare 64 hex to lowercase 64 hex without prefix. out must hold 65 bytes.
static inline bool ss_norm_hash(const char* in, char out[65]) {
  if (!in) return false;
  if (in[0] == '0' && (in[1] == 'x' || in[1] == 'X')) in += 2;
  if (strlen(in) != 64) return false;
  for (int i = 0; i < 64; i++) {
    char c = ss_lc(in[i]);
    if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
    out[i] = c;
  }
  out[64] = 0;
  return true;
}
static inline bool ss_is_nil_hex(const char* h64) { for (int i = 0; i < 64; i++) if (h64[i] != '0') return false; return true; }

struct SsAction {
  bool prevote;
  bool precommit;
  uint32_t height;
  uint32_t round;
  char subject[65];   // 64 lowercase hex, no 0x
};

class SsLight {
 public:
  void init(const char* selfAddr, const char* const* addrs, const uint16_t* weights, int n) {
    n_ = n > SS_MAX_COMMITTEE ? SS_MAX_COMMITTEE : n;
    selfIdx_ = -1;
    for (int i = 0; i < n_; i++) {
      strncpy(addr_[i], addrs[i], 42); addr_[i][42] = 0;
      for (char* p = addr_[i]; *p; ++p) *p = ss_lc(*p);
      w_[i] = weights[i];
      if (ss_addr_eq(addrs[i], selfAddr)) selfIdx_ = i;
    }
    curHeight_ = 1; lastRound_ = 0; revokedMask_ = 0;
    pvValid_ = false; pcValid_ = false; pvH_ = pvR_ = pcH_ = pcR_ = 0; pvSubject_[0] = 0;
    tallyH_ = 0; tallyR_ = 0; clearTally();
    propH_ = 0; propSubject_[0] = 0; master_[0] = 0;
  }

  bool     ready() const { return selfIdx_ >= 0 && n_ > 0; }
  uint32_t height() const { return curHeight_; }
  uint32_t round() const { return lastRound_; }
  bool     selfRevoked() const { return selfIdx_ >= 0 && (revokedMask_ & (1u << selfIdx_)); }
  const char* master() const { return master_; }
  int      indexOf(const char* addr) const {
    if (!addr) return -1;
    for (int i = 0; i < n_; i++) if (ss_addr_eq(addr_[i], addr)) return i;
    return -1;
  }
  uint32_t activeWeight() const {
    uint32_t a = 0;
    for (int i = 0; i < n_; i++) if (!(revokedMask_ & (1u << i))) a += w_[i];
    return a;
  }
  // Blocks-of-the-current-height proposal subject, if we saw a valid one (used by the rogue double-vote).
  bool proposalSubject(uint32_t h, char out[65]) const {
    if (propH_ != h || !propSubject_[0]) return false;
    memcpy(out, propSubject_, 65); return true;
  }
  void markSelfRevoked() { if (selfIdx_ >= 0) revokedMask_ |= (1u << selfIdx_); }   // tamper: stop voting locally

  void noteRound(uint32_t h, uint32_t r) { if (h == curHeight_ && r > lastRound_) lastRound_ = r; }

  // PROPOSAL of the board's cluster. hashOk = recomputed blockHash(body.header) == subject (caller computes it).
  SsAction onProposal(uint32_t h, uint32_t r, const char* subject64, bool hashOk) {
    SsAction a = none();
    if (!ready()) return a;
    noteRound(h, r);
    if (selfRevoked() || h != curHeight_ || !hashOk) return a;
    propH_ = h; memcpy(propSubject_, subject64, 65);
    if (pvValid_ && !newer(h, r, pvH_, pvR_)) return a;             // once per (h,r), never go backwards
    if (!prepareTally(h, r)) return a;
    pvValid_ = true; pvH_ = h; pvR_ = r; memcpy(pvSubject_, subject64, 65);
    Slot* s = slotFor(subject64);
    if (!s) return a;
    s->mask |= (1u << selfIdx_);                                     // the hub never echoes our own vote back
    a.prevote = true; a.height = h; a.round = r; memcpy(a.subject, subject64, 65);
    if (evaluate(h, r).precommit) a.precommit = true;                // prevotes may have arrived before the proposal
    return a;
  }

  // A PREVOTE from another member (claimed sender; no signature check at L2).
  SsAction onPrevote(uint32_t h, uint32_t r, const char* subject64, const char* from) {
    SsAction a = none();
    if (!ready()) return a;
    int idx = indexOf(from);
    if (idx < 0 || idx == selfIdx_ || (revokedMask_ & (1u << idx))) return a;
    noteRound(h, r);
    if (h != curHeight_ || ss_is_nil_hex(subject64)) return a;
    if (!prepareTally(h, r)) return a;
    Slot* s = slotFor(subject64);
    if (!s) return a;
    s->mask |= (1u << idx);                                          // a bitmask dedupes repeated senders
    return evaluate(h, r);
  }

  // Light COMMIT: header.height, revoked[] and master are the state AFTER the block.
  void onCommit(uint32_t headerHeight, const char* const* revoked, int nRevoked, const char* master) {
    uint32_t nh = headerHeight + 1;
    if (nh >= curHeight_) {                                          // ignore stale commits
      for (int i = 0; i < nRevoked; i++) { int ix = indexOf(revoked[i]); if (ix >= 0) revokedMask_ |= (1u << ix); }
      if (master) { strncpy(master_, master, 42); master_[42] = 0; }
    }
    if (nh > curHeight_) { curHeight_ = nh; lastRound_ = 0; tallyH_ = 0; clearTally(); }
  }

 private:
  struct Slot { char subject[65]; uint32_t mask; bool used; };
  int      n_ = 0, selfIdx_ = -1;
  char     addr_[SS_MAX_COMMITTEE][43];
  uint16_t w_[SS_MAX_COMMITTEE];
  uint32_t revokedMask_ = 0, curHeight_ = 1, lastRound_ = 0;
  bool     pvValid_ = false, pcValid_ = false;
  uint32_t pvH_ = 0, pvR_ = 0, pcH_ = 0, pcR_ = 0;
  char     pvSubject_[65];
  uint32_t tallyH_ = 0, tallyR_ = 0;
  Slot     slots_[SS_TALLY_SLOTS];
  uint32_t propH_ = 0;
  char     propSubject_[65];
  char     master_[43];

  static SsAction none() { SsAction a; a.prevote = false; a.precommit = false; a.height = 0; a.round = 0; a.subject[0] = 0; return a; }
  static bool newer(uint32_t h, uint32_t r, uint32_t bh, uint32_t br) { return h > bh || (h == bh && r > br); }
  void clearTally() { for (int i = 0; i < SS_TALLY_SLOTS; i++) { slots_[i].used = false; slots_[i].mask = 0; slots_[i].subject[0] = 0; } }
  bool prepareTally(uint32_t h, uint32_t r) {
    if (tallyH_ != h || r > tallyR_) { tallyH_ = h; tallyR_ = r; clearTally(); return true; }
    return r == tallyR_;                                            // votes of an older round are ignored
  }
  Slot* slotFor(const char* subject64) {
    for (int i = 0; i < SS_TALLY_SLOTS; i++) if (slots_[i].used && strcmp(slots_[i].subject, subject64) == 0) return &slots_[i];
    for (int i = 0; i < SS_TALLY_SLOTS; i++) if (!slots_[i].used) {
      slots_[i].used = true; slots_[i].mask = 0; memcpy(slots_[i].subject, subject64, 65); return &slots_[i];
    }
    return nullptr;
  }
  SsAction evaluate(uint32_t h, uint32_t r) {
    SsAction a = none();
    if (selfRevoked() || !pvValid_ || pvH_ != h || pvR_ != r || h != curHeight_) return a;
    if (pcValid_ && !newer(h, r, pcH_, pcR_)) return a;             // PRECOMMIT once per (h,r)
    Slot* s = nullptr;
    for (int i = 0; i < SS_TALLY_SLOTS; i++) if (slots_[i].used && strcmp(slots_[i].subject, pvSubject_) == 0) s = &slots_[i];
    if (!s) return a;
    uint32_t w = 0, active = 0;
    for (int i = 0; i < n_; i++) {
      if (revokedMask_ & (1u << i)) continue;                        // revoked members carry no weight
      active += w_[i];
      if (s->mask & (1u << i)) w += w_[i];
    }
    if (3u * w > 2u * active) {                                      // spec 7.2: 3*weight > 2*activeWeight
      pcValid_ = true; pcH_ = h; pcR_ = r;
      a.precommit = true; a.height = h; a.round = r; memcpy(a.subject, pvSubject_, 65);
    }
    return a;
  }
};

// ---------------------------------------------------------------------------------------------
// outgoing JSON (fixed schema, snprintf into caller buffers; no heap)
// ---------------------------------------------------------------------------------------------
// {"hub":"REGISTER","node":"0x..","name":"c1-board","cluster":1,"light":true}
static inline int ss_build_register(char* out, size_t cap, const char* addr, const char* name, int cluster) {
  int n = snprintf(out, cap, "{\"hub\":\"REGISTER\",\"node\":\"%s\",\"name\":\"%s\",\"cluster\":%d,\"light\":true}", addr, name, cluster);
  return (n < 0 || (size_t)n >= cap) ? -1 : n;
}

// Signed envelope wrapped for the hub: {"hub":"SEND","to":"cluster:N","env":{...}} (spec 11.2, 11.4). sig.v is 0 (hardware).
// bodyJson is a JSON object literal, e.g. "{}" or "{\"reason\":\"tamper\"}".
static inline int ss_build_signed_send(char* out, size_t cap, const char* type, const char* missionHex64, int cluster,
                                       const char* fromAddr, uint32_t height, uint32_t round, const char* subjectHex64,
                                       const uint8_t r[32], const uint8_t s[32], const char* bodyJson) {
  char rh[65], sh[65];
  ss_bin2hex(r, 32, rh); ss_bin2hex(s, 32, sh);
  int n = snprintf(out, cap,
      "{\"hub\":\"SEND\",\"to\":\"cluster:%d\",\"env\":{\"v\":1,\"type\":\"%s\",\"mission\":\"0x%s\",\"cluster\":%d,"
      "\"from\":\"%s\",\"to\":\"*\",\"height\":%lu,\"round\":%lu,\"subject\":\"0x%s\","
      "\"sig\":{\"r\":\"0x%s\",\"s\":\"0x%s\",\"v\":0},\"body\":%s}}",
      cluster, type, missionHex64, cluster, fromAddr, (unsigned long)height, (unsigned long)round, subjectHex64, rh, sh, bodyJson);
  return (n < 0 || (size_t)n >= cap) ? -1 : n;
}
