#!/usr/bin/env python3
"""check_config.py: preflight for firmware/swarm_member/config.h. Prints PASS/FAIL only, NEVER key or password values.

Checks: private key derives NODE_ADDRESS; board is in the committee as the weight-1 hardware member; committee is
sorted ascending and has sane weights; mission id / hub ip / password are not placeholders.
Usage: python check_config.py <path/to/config.h>
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import swarm_ref as ref  # noqa: E402


def main(path):
    t = Path(path).read_text(encoding="utf-8")
    fails = 0

    def check(cond, msg):
        nonlocal fails
        print(("  ok   " if cond else "  FAIL ") + msg)
        fails += 0 if cond else 1

    def define(name):
        m = re.search(r"^\s*#define\s+" + name + r"\s+\"?([^\"\n]*?)\"?\s*(?://.*)?$", t, re.M)
        return m.group(1).strip() if m else None

    def arr(name):
        m = re.search(name + r"\s*\[[^\]]*\]\s*=\s*\{(.*?)\};", t, re.S)
        return m.group(1) if m else ""

    priv = define("NODE_PRIVKEY_HEX") or ""
    addr = (define("NODE_ADDRESS") or "").lower()
    check(len(priv) == 64 and set(priv) != {"0"}, "NODE_PRIVKEY_HEX is set (64 hex, not the zero placeholder)")
    if len(priv) == 64 and set(priv) != {"0"}:
        derived = ref.address_from_pub(ref.pub_from_priv(bytes.fromhex(priv)))
        check(derived == addr, "private key derives NODE_ADDRESS (identity is consistent)")
    mid = define("MISSION_ID_HEX") or ""
    check(len(mid) == 64 and set(mid) != {"0"}, "MISSION_ID_HEX is set")
    addrs = [a.lower() for a in re.findall(r'"(0x[0-9a-fA-F]{40})"', arr("COMMITTEE_ADDR"))]
    weights = [int(x) for x in re.findall(r"\d+", arr("COMMITTEE_WEIGHT"))]
    flags = [int(x) for x in re.findall(r"\d+", arr("COMMITTEE_FLAGS"))]
    size = int(define("COMMITTEE_SIZE") or 0)
    check(len(addrs) == size == len(weights) == len(flags) and size > 0, "COMMITTEE_* arrays match COMMITTEE_SIZE (%d)" % size)
    check(addrs == sorted(addrs), "committee addresses sorted ascending (spec 10.7)")
    check(all(1 <= w <= 3 for w in weights), "weights within 1..3")
    if addr in addrs:
        i = addrs.index(addr)
        check(weights[i] == 1 and flags[i] & 1 == 1, "board is in the committee as the weight-1 hardware member")
    else:
        check(False, "board address is in COMMITTEE_ADDR")
    check(sum(weights) == 13, "cluster-1 active weight is 13 (4x3 + 1) as in the spec demo (got %d)" % sum(weights))
    check(define("WIFI_PASSWORD") not in (None, "", "CHANGE_ME"), "WIFI_PASSWORD is not the placeholder")
    ip = define("HUB_IP") or ""
    check(bool(re.fullmatch(r"\d+\.\d+\.\d+\.\d+", ip)) and ip != "0.0.0.0", "HUB_IP is set (not 0.0.0.0)")
    check(define("WIFI_SSID") == "swarmshield", "WIFI_SSID is swarmshield")
    print("CONFIG PASS" if not fails else "CONFIG FAIL (%d)" % fails)
    return fails


if __name__ == "__main__":
    sys.exit(1 if main(sys.argv[1]) else 0)
