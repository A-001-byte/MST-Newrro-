"""Hash / encoding helpers duplicated from docs/00_CONTEXT.md section 10 (no crypto keys, hashes only).

Used by MockEngine, the dev fixture and tests so mock blocks carry the REAL byte layout. Verified against
ledger/test_vectors/vectors.json in swarm/tests/test_wire.py. Everything here is hex-string in / hex-string out.
"""
from __future__ import annotations

import hashlib
import json
import struct

DOMAIN = b"SWARMSHIELD_V1".ljust(32, b"\x00")
NIL = "0x" + "00" * 32
EMPTY_TX_ROOT = "0x" + hashlib.sha256(b"").hexdigest()
BLOCK_TAG = 0x10
GENESIS_TAG = 0x11
FLAG_HARDWARE = 1


def sha256(b: bytes) -> bytes:
    return hashlib.sha256(b).digest()


def unhex(s: str) -> bytes:
    return bytes.fromhex(s[2:] if s.startswith("0x") else s)


def hx(b: bytes) -> str:
    return "0x" + b.hex()


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def tx_hash(tx: dict) -> str:
    """sha256(canonical_json(tx without sig)) as 0x-hex (10.8)."""
    assert "sig" not in tx, "hash the tx without its signature"
    return hx(sha256(canonical_json(tx).encode("utf-8")))


def tx_root(hashes: list[str]) -> str:
    return hx(sha256(b"".join(unhex(h) for h in hashes)))


def block_preimage(header: dict) -> bytes:
    """167 bytes; the round is deliberately not part of a block (10.5)."""
    return (DOMAIN + struct.pack(">B", BLOCK_TAG) + unhex(header["mission"])
            + struct.pack(">HQ", header["cluster"], header["height"])
            + unhex(header["prevHash"]) + unhex(header["txRoot"]) + unhex(header["proposer"])
            + struct.pack(">Q", header["timestampMs"]))


def block_hash(header: dict) -> str:
    return hx(sha256(block_preimage(header)))


def genesis_hash(mission: str, cluster: int, roster_hash_: str) -> str:
    return hx(sha256(DOMAIN + struct.pack(">B", GENESIS_TAG) + unhex(mission) + struct.pack(">H", cluster)
                     + unhex(roster_hash_)))


def roster_hash(mission: str, members: list[dict]) -> str:
    """members: {address, clusterId, weight, flags}; sorted here by (clusterId, address) (10.7)."""
    acc = DOMAIN + unhex(mission)
    for m in sorted(members, key=lambda m: (m["clusterId"], m["address"].lower())):
        acc += unhex(m["address"]) + struct.pack(">HHB", m["clusterId"], m["weight"], m["flags"])
    return hx(sha256(acc))


def quorum(signed_weight: int, active_weight: int) -> bool:
    """3 * weight(S) > 2 * activeWeight, integers only (7.2)."""
    return 3 * signed_weight > 2 * active_weight
