#!/usr/bin/env python3
"""run_tests.py: run every HW-lane check that does not need the board. One command, ASCII output.

  python tools/run_tests.py [--vectors <path/to/vectors.json>]

1. test_vectors.h is up to date with vectors.json
2. swarm_ref.py (pure-Python reference) passes against the vectors
3. host_test.cpp   (byte layouts vs vectors)          -> compiled with g++
4. host_test_logic.cpp (voting logic, envelope build)  -> compiled with g++
5. the C++-built REGISTER / signed PREVOTE JSON parses and the signature recovers k5 (python verifier)
6. fake_hub.py end-to-end against an emulated board (test key k5)
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import swarm_ref as ref  # noqa: E402

DEFAULT_VECTORS = HERE.parents[1].parent / "MST-Newrro-" / "ledger" / "test_vectors" / "vectors.json"


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vectors", default=str(DEFAULT_VECTORS))
    args = ap.parse_args()
    vec = Path(args.vectors)
    fails = 0

    def step(name, ok, detail=""):
        nonlocal fails
        print("[%s] %s%s" % ("PASS" if ok else "FAIL", name, ("  " + detail) if detail and not ok else ""))
        fails += 0 if ok else 1

    r = run([sys.executable, str(HERE / "vectors_to_h.py"), "--vectors", str(vec),
             "--out", str(HERE.parent / "swarm_member" / "test_vectors.h"), "--check"])
    step("test_vectors.h up to date", r.returncode == 0, r.stdout + r.stderr)

    r = run([sys.executable, str(HERE / "swarm_ref.py"), "--vectors", str(vec)])
    step("swarm_ref.py vs vectors.json", r.returncode == 0, r.stdout[-600:] + r.stderr)

    tmp = Path(tempfile.mkdtemp(prefix="swarm_hw_"))
    outputs = {}
    for name in ("host_test", "host_test_logic"):
        exe = tmp / (name + ".exe")
        c = run(["g++", "-std=c++11", "-Wall", "-Wextra", "-o", str(exe), str(HERE / (name + ".cpp"))])
        step("compile %s.cpp (warnings count as failures)" % name, c.returncode == 0 and not c.stderr.strip(), c.stderr[-1500:])
        if c.returncode == 0:
            t = run([str(exe)])
            outputs[name] = t.stdout
            step("run %s" % name, t.returncode == 0, t.stdout[-1500:])

    out = outputs.get("host_test_logic", "")
    reg = next((l[9:] for l in out.splitlines() if l.startswith("REGISTER ")), None)
    env = next((l[9:] for l in out.splitlines() if l.startswith("ENVELOPE ")), None)
    step("C++ produced REGISTER + ENVELOPE lines", bool(reg and env))
    if reg and env:
        try:
            jr = json.loads(reg)
            step("REGISTER is valid JSON with the spec fields",
                 jr == {"hub": "REGISTER", "node": jr["node"], "name": "c1-board", "cluster": 1, "light": True} and jr["node"].startswith("0x"))
            je = json.loads(env)
            e = je["env"]
            v = json.load(open(vec, encoding="utf-8"))
            s = next(x for x in v["statements"] if x["name"] == "prevote_block")
            step("SEND wrapper: hub=SEND, to=cluster:1, env.type=PREVOTE, v=1, sig.v=0",
                 je["hub"] == "SEND" and je["to"] == "cluster:1" and e["type"] == "PREVOTE" and e["v"] == 1 and e["sig"]["v"] == 0)
            dg = ref.stmt_digest(2, ref.b32(e["mission"]), e["cluster"], e["height"], e["round"], ref.b32(e["subject"]))
            got = ref.verify_from(dg, int(e["sig"]["r"], 16), int(e["sig"]["s"], 16), e["sig"]["v"], e["from"])
            step("envelope signature (v=0) recovers the claimed sender %s" % e["from"][:10] + "...", got == s["sig"]["v"])
        except Exception as ex:  # noqa: BLE001
            step("envelope JSON checks", False, repr(ex))

    r = run([sys.executable, str(HERE / "test_fake_hub.py"), "--vectors", str(vec)], timeout=90)
    step("fake_hub end-to-end with emulated board (H1-H4 paths)", r.returncode == 0, r.stdout[-1500:] + r.stderr[-500:])

    print("ALL HW CHECKS PASS" if not fails else "HW CHECKS FAILED (%d)" % fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
