"""Run the smoke tests (``smoke_*.py`` in the repo root) sequentially.

Each smoke test is a standalone script that prints ``... PASSED`` and exits 0
on success.  This runner discovers them, runs each in its own interpreter, and
prints a summary; it exits non-zero if any test failed.

Usage:
    py -3.12 tools/run_smoke.py            # all tests
    py -3.12 tools/run_smoke.py combat     # only names containing "combat"
    py -3.12 tools/run_smoke.py -- -k items   # substring filter (see below)

A positional substring filters by file name; every argument is treated as an
OR-ed substring, so ``run_smoke.py combat items`` runs both groups.
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def discover(needles: list[str]) -> list[str]:
    names = sorted(n for n in os.listdir(ROOT)
                   if n.startswith("smoke_") and n.endswith(".py"))
    if not needles:
        return names
    return [n for n in names if any(s.lower() in n.lower() for s in needles)]


def main(argv: list[str]) -> int:
    needles = [a for a in argv if not a.startswith("-")]
    tests = discover(needles)
    if not tests:
        print("no smoke tests matched")
        return 1

    passed: list[str] = []
    failed: list[str] = []
    for name in tests:
        proc = subprocess.run([sys.executable, os.path.join(ROOT, name)],
                              cwd=ROOT, capture_output=True, text=True)
        if proc.returncode == 0:
            passed.append(name)
            print(f"PASS {name}")
        else:
            failed.append(name)
            print(f"FAIL {name}")
            tail = (proc.stdout or "")[-2000:]
            print(tail)
            if proc.stderr:
                print(proc.stderr[-2000:])

    print(f"\n{len(passed)}/{len(tests)} passed")
    if failed:
        print("failed: " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
