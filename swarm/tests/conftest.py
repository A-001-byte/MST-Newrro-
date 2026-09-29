"""Make Track A's package (ledger/ at the repo root; or a read-only copy in a side workspace) importable for the real-engine tests."""
import sys
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[2] / "ledger"
if (LEDGER / "swarmledger").exists() and str(LEDGER) not in sys.path:
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(LEDGER))
