"""wire.py must reproduce the ground-truth vectors (read-only file in the Track A reference checkout)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from swarm import wire

ROOT = Path(__file__).resolve().parents[2]
CANDIDATES = [ROOT / "ledger" / "test_vectors" / "vectors.json",                        # repo layout
              ROOT.parent / "MST-Newrro-" / "ledger" / "test_vectors" / "vectors.json"]  # side-by-side workspace
VECTORS = next((c for c in CANDIDATES if c.exists()), CANDIDATES[0])
pytestmark = pytest.mark.skipif(not VECTORS.exists(), reason="ledger/test_vectors/vectors.json not found")


@pytest.fixture(scope="module")
def v():
    return json.loads(VECTORS.read_text(encoding="utf-8"))


def test_domain_and_empty_root(v):
    assert "0x" + wire.DOMAIN.hex() == v["constants"]["domain_hex"]
    assert wire.EMPTY_TX_ROOT == v["constants"]["empty_tx_root"]
    assert wire.NIL == v["constants"]["nil_subject"]


def test_roster_and_genesis(v):
    members = v["roster"]["members"]
    assert wire.roster_hash(v["missionId"], members) == v["roster"]["rosterHash"]
    g = v["genesis"]
    assert wire.genesis_hash(v["missionId"], g["clusterId"], g["rosterHash"]) == g["genesisHash"]


def test_tx_hash_and_root(v):
    for key in ("obs", "role"):
        t = v["tx"][key]
        tx = t["object"]
        assert wire.canonical_json(tx) == t["canonical"]
        assert wire.tx_hash(tx) == t["txHash"]
    assert wire.tx_root(v["txRoot"]["txHashes"]) == v["txRoot"]["root"]


def test_block_hash(v):
    assert wire.hx(wire.block_preimage(v["block"]["header"])) == v["block"]["preimage"]
    assert wire.block_hash(v["block"]["header"]) == v["block"]["blockHash"]


def test_quorum_rule():
    assert wire.quorum(9, 13) and not wire.quorum(8, 13)      # cluster 1: 9 of 13
    assert wire.quorum(12, 15) and not wire.quorum(10, 15)    # cluster 2: 4 of 5 full validators
