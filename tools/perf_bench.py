"""Performance benchmark for the ROUT III client (change ``total-performance``).

Runs the reference-measurement workloads on the declared reference machine
(see README) and writes a JSON report.  Modes:

  --mode convert   compare framebuffer conversion paths (task 1.2)
  --mode tile      tile-mode map frame budget  (task 5.1)
  --mode sector    sector-mode map frame budget (task 5.1)

Usage::

  py -3.12 tools/perf_bench.py --map maps/demo.json --seconds 30 --out results.json
  py -3.12 tools/perf_bench.py --mode convert --seconds 2 --out convert.json

The benchmark keeps its own measurement overhead out of the numbers
(metrics/logging are muted inside the runs).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pygame  # noqa: E402

from engine.framebuffer import Frame  # noqa: E402
from engine.constants import W, H  # noqa: E402


class Bench:
    """Runs ``duration`` seconds of a workload and collects frame times."""

    def __init__(self, duration: float) -> None:
        self.duration = duration
        self.frames = []

    def run(self, fn):
        end = time.perf_counter() + self.duration
        while True:
            t0 = time.perf_counter()
            fn()
            self.frames.append(time.perf_counter() - t0)
            if time.perf_counter() >= end:
                break
        return self

    def report(self) -> dict:
        frames = [f * 1000.0 for f in self.frames]  # ms
        frames.sort()
        n = len(frames)
        p95 = frames[int(n * 0.95) - 1] if n else 0.0
        avg = sum(frames) / n if n else 0.0
        return {
            "frames": n,
            "avg_ms": round(avg, 4),
            "p95_ms": round(p95, 4),
            "min_ms": round(frames[0], 4) if n else 0.0,
            "max_ms": round(frames[-1], 4) if n else 0.0,
            "effective_fps": round(1000.0 / avg, 2) if avg else 0.0,
        }


def _scene_frame(iw: int, ih: int) -> Frame:
    """A synthetic frame with movement across the buffer (walls + noise)."""
    f = Frame(iw, ih)
    f.clear((14, 12, 20))
    f.fill_rect(iw // 4, ih // 4, iw // 2, ih * 3 // 4, (120, 90, 40))
    f.fill_rect(0, 0, iw, ih // 3, (60, 60, 70))
    step = max(1, ih // 24)
    for i in range(0, ih, step):
        f.vspan_bytes(min(iw - 1, i), 0, ih, ((i * 13) % 256, (i * 7) % 256,
                                              (i * 5) % 256))
    return f


def run_convert(duration: float, out: str) -> dict:
    """Compare frombuffer (Path A) vs persistent-pixels (Path B) conversion
    + single upscale onto a 1000x700 target, at tier ``high`` resolution."""
    pygame.init()
    pygame.display.set_mode((64, 48))
    target = pygame.Surface((W, H))
    iw, ih = 320, 240
    f = _scene_frame(iw, ih)
    # warm up
    for _ in range(10):
        f.draw_to(target, direct=True)
        f.draw_to(target, direct=False)
    a = Bench(duration).run(lambda: f.draw_to(target, direct=False))
    b = Bench(duration).run(lambda: f.draw_to(target, direct=True))
    rep_a, rep_b = a.report(), b.report()
    result = {
        "mode": "convert",
        "target": (W, H),
        "internal": (iw, ih),
        "pathA_frombuffer": rep_a,
        "pathB_persistent_pixels": rep_b,
        "chosen_path": "B" if rep_b["avg_ms"] <= rep_a["avg_ms"] else "A",
    }
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
    return result


def _load_map(path: str):
    import json as _json

    if not path:
        raise SystemExit("--map required for tile/sector modes")
    with open(path, "r", encoding="utf-8") as fh:
        return _json.load(fh)


def run_map(mode: str, map_path: str, duration: float, out: str,
            tier: str) -> dict:
    """Map-driven frame budget (implemented in task 5.1)."""
    _load_map(map_path)
    from tools import bench_map as _bm

    result = _bm.run(mode, map_path, duration, tier)
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--map", default=None, help="map file (tile/sector modes)")
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--out", default=None, help="JSON report path")
    ap.add_argument("--mode", choices=["convert", "tile", "sector"],
                    default="sector")
    ap.add_argument("--tier", choices=["high", "medium", "fast", "auto"],
                    default="high")
    args = ap.parse_args()

    if args.mode == "convert":
        res = run_convert(args.seconds, args.out)
    else:
        res = run_map(args.mode, args.map, args.seconds, args.out, args.tier)
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()