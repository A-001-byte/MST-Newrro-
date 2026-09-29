#!/usr/bin/env python3
"""swarm_ref.py: dependency-free reference for the SwarmShield v2 byte formats (spec 10) used by the HW lane.

Pure Python (stdlib only): keccak-256, secp256k1 public-key recovery, statement digest, block hash.
Used by fake_hub.py (to check the board's signatures) and check_sig.py (recover a logged signature).
`python swarm_ref.py --vectors <vectors.json>` validates everything against ledger/test_vectors/vectors.json.
"""
import hashlib
import json
import struct
import sys

# ------------------------------------------------------------------ keccak-256 (NOT sha3-256)
_M64 = (1 << 64) - 1
_RC = [0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000, 0x000000000000808B,
       0x0000000080000001, 0x8000000080008081, 0x8000000000008009, 0x000000000000008A, 0x0000000000000088,
       0x0000000080008009, 0x000000008000000A, 0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
       0x8000000000008003, 0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
       0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008]
_ROT = [[0, 36, 3, 41, 18], [1, 44, 10, 45, 2], [62, 6, 43, 15, 61], [28, 55, 25, 21, 56], [27, 20, 39, 8, 14]]


def _rol(x, n):
    n %= 64
    return ((x << n) | (x >> (64 - n))) & _M64 if n else x


def _keccak_f(st):
    for rnd in range(24):
        c = [st[x] ^ st[x + 5] ^ st[x + 10] ^ st[x + 15] ^ st[x + 20] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        for i in range(25):
            st[i] ^= d[i % 5]
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rol(st[x + 5 * y], _ROT[x][y])
        for x in range(5):
            for y in range(5):
                st[x + 5 * y] = b[x + 5 * y] ^ ((~b[(x + 1) % 5 + 5 * y]) & b[(x + 2) % 5 + 5 * y])
        st[0] ^= _RC[rnd]


def keccak256(data):
    rate = 136
    msg = bytearray(data) + b"\x01"
    msg += b"\x00" * ((-len(msg)) % rate)
    msg[-1] |= 0x80
    st = [0] * 25
    for off in range(0, len(msg), rate):
        for i in range(rate // 8):
            st[i] ^= struct.unpack_from("<Q", msg, off + 8 * i)[0]
        _keccak_f(st)
    return b"".join(struct.pack("<Q", st[i]) for i in range(4))


# ------------------------------------------------------------------ secp256k1
P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
HALF_N = N // 2
G = (0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
     0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8)


def _add(a, b):
    if a is None:
        return b
    if b is None:
        return a
    if a[0] == b[0]:
        if (a[1] + b[1]) % P == 0:
            return None
        lam = (3 * a[0] * a[0]) * pow(2 * a[1], -1, P) % P
    else:
        lam = (b[1] - a[1]) * pow(b[0] - a[0], -1, P) % P
    x = (lam * lam - a[0] - b[0]) % P
    return (x, (lam * (a[0] - x) - a[1]) % P)


def _mul(k, pt):
    r = None
    while k:
        if k & 1:
            r = _add(r, pt)
        pt = _add(pt, pt)
        k >>= 1
    return r


def _neg(pt):
    return (pt[0], (-pt[1]) % P)


def pub_from_priv(priv32):
    x, y = _mul(int.from_bytes(priv32, "big"), G)
    return b"\x04" + x.to_bytes(32, "big") + y.to_bytes(32, "big")


def address_from_pub(pub65):
    return "0x" + keccak256(pub65[1:])[12:].hex()


def recover_pub(digest32, r, s, recid):
    """recid in {0,1}. Returns 65-byte uncompressed pubkey or None."""
    if not (1 <= r < N and 1 <= s < N):
        return None
    y2 = (pow(r, 3, P) + 7) % P
    y = pow(y2, (P + 1) // 4, P)
    if y * y % P != y2:
        return None
    if (y & 1) != recid:
        y = P - y
    z = int.from_bytes(digest32, "big")
    rinv = pow(r, -1, N)
    q = _mul(rinv, _add(_mul(s, (r, y)), _neg(_mul(z, G))))
    if q is None:
        return None
    return b"\x04" + q[0].to_bytes(32, "big") + q[1].to_bytes(32, "big")


def recover_address(digest32, r, s, v):
    """v in {0 (unknown), 27, 28}. v=0 tries both. Returns list of candidate addresses (lowercase)."""
    recids = [0, 1] if v == 0 else [v - 27]
    out = []
    for rid in recids:
        pub = recover_pub(digest32, r, s, rid)
        if pub:
            out.append((rid + 27, address_from_pub(pub)))
    return out


def sign_digest(priv32, digest32):
    """RFC 6979 deterministic ECDSA (HMAC-SHA256) + low-s, like eth_keys / the firmware. Returns (r, s, v 27|28)."""
    import hmac
    x = int.from_bytes(priv32, "big")
    h1 = int.from_bytes(digest32, "big") % N
    bx = x.to_bytes(32, "big") + h1.to_bytes(32, "big")
    V, K = b"\x01" * 32, b"\x00" * 32
    K = hmac.new(K, V + b"\x00" + bx, hashlib.sha256).digest()
    V = hmac.new(K, V, hashlib.sha256).digest()
    K = hmac.new(K, V + b"\x01" + bx, hashlib.sha256).digest()
    V = hmac.new(K, V, hashlib.sha256).digest()
    while True:
        V = hmac.new(K, V, hashlib.sha256).digest()
        k = int.from_bytes(V, "big")
        if 1 <= k < N:
            R = _mul(k, G)
            r = R[0] % N
            if r:
                s = pow(k, -1, N) * (int.from_bytes(digest32, "big") + r * x) % N
                if s:
                    recid = (R[1] & 1) | (2 if R[0] >= N else 0)
                    if s > HALF_N:
                        s, recid = N - s, recid ^ 1
                    return r, s, 27 + (recid & 1)
        K = hmac.new(K, V + b"\x00", hashlib.sha256).digest()
        V = hmac.new(K, V, hashlib.sha256).digest()


def verify_from(digest32, r, s, v, claimed_addr):
    """Spec 10.10: reject high-s, recover, compare. Returns recovered v (27/28) or None."""
    if s > HALF_N:
        return None
    for vv, addr in recover_address(digest32, r, s, v):
        if addr == claimed_addr.lower():
            return vv
    return None


# ------------------------------------------------------------------ statement + block hash (spec 10.3, 10.5)
DOMAIN = b"SWARMSHIELD_V1".ljust(32, b"\x00")
NIL = b"\x00" * 32


def b32(h):
    h = h[2:] if h.startswith("0x") else h
    b = bytes.fromhex(h)
    if len(b) != 32:
        raise ValueError("expected 32 bytes")
    return b


def statement(msg_type, mission, cluster, height, rnd, subject):
    return DOMAIN + struct.pack(">B", msg_type) + mission + struct.pack(">HQI", cluster, height, rnd) + subject


def stmt_digest(msg_type, mission, cluster, height, rnd, subject):
    return hashlib.sha256(statement(msg_type, mission, cluster, height, rnd, subject)).digest()


def block_hash(header):
    """header: dict with mission, cluster, height, prevHash, txRoot, proposer, timestampMs (0x-hex strings/ints)."""
    pre = (DOMAIN + b"\x10" + b32(header["mission"]) + struct.pack(">HQ", header["cluster"], header["height"]) +
           b32(header["prevHash"]) + b32(header["txRoot"]) + bytes.fromhex(header["proposer"][2:]) +
           struct.pack(">Q", header["timestampMs"]))
    assert len(pre) == 167
    return hashlib.sha256(pre).digest()


# ------------------------------------------------------------------ self-validation against vectors.json
def selftest(vectors_path):
    v = json.load(open(vectors_path, encoding="utf-8"))
    fails = 0

    def check(cond, msg):
        nonlocal fails
        print(("  ok   " if cond else "  FAIL ") + msg)
        fails += 0 if cond else 1

    check(keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470", "keccak256('') KAT")
    check(keccak256(b"abc").hex() == "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45", "keccak256('abc') KAT")
    for k in v["keys"]:
        pub = pub_from_priv(b32(k["priv"]))
        check(pub.hex() == k["pub_uncompressed"][2:] and address_from_pub(pub) == k["address"],
              "%s: priv -> pub -> address" % k["name"])
    mission = b32(v["missionId"])
    privs = {k["name"]: b32(k["priv"]) for k in v["keys"]}
    for s in v["statements"] + [v["equivocation"]["a"], v["equivocation"]["b"]]:
        dg = stmt_digest(s["msgType"], mission, s["clusterId"], s["height"], s["round"], b32(s["subject"]))
        check("0x" + dg.hex() == s["digest"], "%s: digest" % s["name"])
        r_, s_, v_ = sign_digest(privs[s["signer"]], dg)
        check(("0x%064x" % r_, "0x%064x" % s_, v_) == (s["sig"]["r"], s["sig"]["s"], s["sig"]["v"]),
              "%s: RFC6979 low-s signature byte-identical to vector" % s["name"])
        got = verify_from(dg, int(s["sig"]["r"], 16), int(s["sig"]["s"], 16), s["sig"]["v"], s["signerAddress"])
        check(got == s["sig"]["v"], "%s: recovers %s (v=%d)" % (s["name"], s["signer"], s["sig"]["v"]))
        check(verify_from(dg, int(s["sig"]["r"], 16), int(s["sig"]["s"], 16), 0, s["signerAddress"]) == s["sig"]["v"],
              "%s: v=0 (hardware) normalises to %d" % (s["name"], s["sig"]["v"]))
    h = v["block"]["header"]
    check("0x" + block_hash(h).hex() == v["block"]["blockHash"], "block hash (167 B preimage)")
    hs = v["negative"]["high_s"]
    r, s_ = int(hs["sig"]["r"], 16), int(hs["sig"]["s"], 16)
    dg = b32(hs["digest"])
    check(verify_from(dg, r, s_, hs["sig"]["v"], v["negative"]["same_subject_pair"]["a"]["signerAddress"]) is None,
          "high-s vector is rejected")
    check(len(recover_address(dg, r, s_, hs["sig"]["v"])) == 1, "high-s copy still recovers an address (so low-s check is essential)")
    print("SWARM_REF PASS" if not fails else "SWARM_REF FAIL (%d)" % fails)
    return fails


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--vectors":
        sys.exit(1 if selftest(sys.argv[2]) else 0)
    print(__doc__)
