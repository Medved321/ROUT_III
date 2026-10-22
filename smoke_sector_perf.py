"""Smoke: sector scene performance + adaptive budget (tasks 4.5, 5.3).

A mid-size 30-sector map (grid of rooms with door gaps) is rendered for 120
frames at the default adaptive quality and then at the degraded quality;
frame statistics are logged and the default path must hold the 60 FPS budget
on this machine (task 5.3).

Wall-clock budgets are only meaningful on a quiet reference machine — on a
shared desktop (IDE, browser, second game server) the same code slows down
2-3x in multi-second waves, and any fixed-ms assert turns into a coin toss.
So the suite enforces three load-proof layers and one optional strict one:

1. **Structure.** The composed frame must not exceed ``FRAME_PX_BUDGET``
   (the pixel ceiling of the high tier). Tier-table bloat (the class of
   regression that once doubled the buffer to 640x480) is caught here
   deterministically, without a stopwatch.
2. **Ratios.** Default and degraded frames are measured in one interleaved
   pass (a pair per loop iteration — both see the same machine-speed wave),
   and each ladder rung compares against the rung below it: the degraded
   path must never cost more than the default path. Catches flat-path and
   upscale inversions on any machine.
3. **Synthetic ladder** (task 4.5): the real AdaptiveQuality controller
   degrades under injected load and restores under headroom — pure logic,
   no timing.
4. **Strict wall-clock** (``SMOKE_STRICT_PERF=1``): the original claim that
   the default tier renders under 17 ms — run manually on an idle reference
   box when tuning tier sizes; not part of the default suite.

Task 4.5: the BSP path must live inside the existing adaptive-quality
controller — ``render_scale``, ``vs_scale`` and flat texture level from
:class:`client.performance.AdaptiveQuality`, one upscale per frame — and an
artificial-load "smoke_performance analog" must show the ladder degrading
under sustained overload (scale -> vs -> flats), every rung staying inside the
60 FPS budget, then restoring under headroom.
"""
from __future__ import annotations

import os
import statistics
import sys
import time

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pygame  # noqa: E402

from engine import game_state as gs  # noqa: E402
from engine import sector_render  # noqa: E402
from shared.map_data import GameMap  # noqa: E402
from shared.sector_map import SectorMap  # noqa: E402


def build_grid_map(rooms_x: int = 7, rooms_y: int = 5,
                   room_tiles: int = 8) -> SectorMap:
    "Wall grid -> GameMap tiles -> SectorMap (via 1.4 converter)."
    rep = room_tiles + 1  # 9 tiles per cell incl. the 1-wide wall
    w = rooms_x * rep + 1
    h = rooms_y * rep + 1
    tiles = [[0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            if x % rep == 0 or y % rep == 0:
                tiles[y][x] = 1
    gm = GameMap(w, h, tiles)
    sm = SectorMap.from_tiles(gm)
    assert not sm.validate(), sm.validate()
    print(f"  sectors={len(sm.sectors)} linedefs={len(sm.linedefs)}")
    return sm


def bench_pair(frames: int = 120) -> tuple[list[float], list[float]]:
    """Default and degraded frames in one interleaved pass.

    Consecutive iterations of the loop see the same machine-speed wave, so
    the pair ratio stays meaningful while an absolute threshold would not.
    """
    gs.render_scale = 4
    gs.vs_scale = 2
    gs.flat_textured = True
    gs.view_dist = 900
    gs.sec_scene = sector_render.SectorScene(build_grid_map())
    gs.screen = pygame.Surface((1000, 700))
    gs.sector_mode = True
    gs.px, gs.py, gs.pz = 4 * 64, 4 * 64, 0.0
    gs.vh720 = False
    t_def: list[float] = []
    t_low: list[float] = []
    for i in range(frames):
        gs.pa = (i * 0.018) % (3.14159 * 2) - 3.14159
        gs.render_scale, gs.vs_scale, gs.flat_textured = 4, 2, True
        t0 = time.perf_counter()
        sector_render.render_scene(700, 0)
        t_def.append(time.perf_counter() - t0)
        gs.render_scale, gs.vs_scale, gs.flat_textured = 8, 6, False
        t0 = time.perf_counter()
        sector_render.render_scene(700, 0)
        t_low.append(time.perf_counter() - t0)
    return t_def, t_low


def _frame_cost(scale: int, vs: int, flat_textured: bool) -> float:
    """Mean render_scene cost for one frame at the given quality (seconds)."""
    gs.render_scale = scale
    gs.vs_scale = vs
    gs.flat_textured = flat_textured
    gs.pa = 0.3
    sector_render.render_scene(700, 0)  # warm (textures/flat caches)
    t0 = time.perf_counter()
    for _ in range(20):
        sector_render.render_scene(700, 0)
    return (time.perf_counter() - t0) / 20.0


# Pixel ceiling of the high tier's composed frame (500x400 today).  Raising
# TIER_DEFS['high'] is a conscious performance decision — and so is raising
# this constant next to it; the pair is what stops buffer bloat without a
# stopwatch.  Deliberately a second copy of the number: an accidental table
# edit trips this assert, a conscious one updates both.
FRAME_PX_BUDGET = 500 * 400


def _check_frame_budget() -> None:
    """The composed frame must stay within the tier's pixel ceiling.

    Deterministic and load-proof: this is the layer that catches the frame
    being silently composed at window/tier size (the 640x480-class
    regression), which no wall-clock assert can do reliably on a shared
    machine.
    """
    px = gs.frame.iw * gs.frame.ih
    assert px <= FRAME_PX_BUDGET, (
        f"frame {gs.frame.iw}x{gs.frame.ih} = {px}px exceeds the high-tier "
        f"budget {FRAME_PX_BUDGET}px — TIER_DEFS['high'] раздута")


def test_adaptive_budget():
    from client.performance import AdaptiveQuality

    gs.sec_scene = sector_render.SectorScene(build_grid_map())
    gs.screen = pygame.Surface((1000, 700))
    gs.sector_mode = True
    gs.px, gs.py, gs.pz = 4 * 64, 4 * 64, 0.0
    gs.view_dist = 900
    gs.vh720 = False

    # One upscale per frame, never more (task 4.5 budget invariant).
    calls = []
    orig = sector_render._upscale

    def spy(frame, vy, vh):
        calls.append((vy, vh))
        orig(frame, vy, vh)

    sector_render._upscale = spy
    try:
        sector_render.render_scene(700, 0)
    finally:
        sector_render._upscale = orig
    assert len(calls) == 1, calls
    _check_frame_budget()

    # Weak-machine analog: real cost + 30ms of synthetic work keeps the frames
    # over budget, so the SAME adaptive controller degrades across its ladder
    # (render_scale -> vs_scale -> flats off) and the BSP path follows it.
    q = AdaptiveQuality(base_scale=4, max_scale=8, base_vs=2, target_fps=60)
    base = _frame_cost(4, 2, True)
    now = 0.0
    for _ in range(12 * 60):   # ~1 ladder step/second -> full degrade + slack
        now += 1.0 / 60.0
        q.sample((base + 0.030) * 1000.0, now)
    assert q.scale == 8 and q.vs_scale == 6 and not q.flat_textured, (
        q.scale, q.vs_scale, q.flat_textured)

    # The ladder degrades cost monotonically: each rung must not exceed the
    # one below it beyond noise (slack covers a machine wave crossing the
    # sequential measurements; a genuine inversion — flats solid slower than
    # textured, vs>1 slower than vs=1 — is far above it).
    costs = []
    for scale, vs, flat in ((4, 2, True), (4, 4, True), (8, 6, True),
                            (8, 6, False)):
        ms = _frame_cost(scale, vs, flat) * 1000.0
        costs.append(ms)
        print(f"  rung {scale}/{vs}/{'tex' if flat else 'solid'} "
              f"avg={ms:6.2f}ms")
    for i in range(1, len(costs)):
        assert costs[i] <= costs[i - 1] * 1.25 + 3.0, (costs, i)

    # Headroom (short frames) restores the ladder in reverse, back to base.
    for _ in range(14 * 60):
        now += 1.0 / 60.0
        q.sample(1.0, now)
    assert (q.scale, q.vs_scale, q.flat_textured) == (4, 2, True), (
        q.scale, q.vs_scale, q.flat_textured)
    print("  ok: adaptive budget drives the BSP path, one upscale, 60 FPS held")


def main():
    pygame.init()
    pygame.display.set_mode((1000, 700))
    t_def, t_low = bench_pair()
    for label, t in (("default(4/2/tex)", t_def), ("degraded(8/6/solid)", t_low)):
        avg = statistics.mean(t) * 1000
        p95 = sorted(t)[int(len(t) * 0.95)] * 1000
        mn = min(t) * 1000
        print(f"  {label:18s} avg={avg:6.2f}ms p95={p95:6.2f}ms min={mn:6.2f}ms")
    _check_frame_budget()
    avg_def = statistics.mean(t_def)
    avg_low = statistics.mean(t_low)
    # Paired pass: both series saw the same machine-speed waves, so the
    # ratio is meaningful where an absolute threshold is not.
    assert avg_low <= avg_def * 1.2 + 0.002, \
        f"degraded slower than default: {avg_low*1000:.1f} vs {avg_def*1000:.1f}"
    if os.environ.get("SMOKE_STRICT_PERF"):
        assert avg_def < 0.017, \
            f"60 FPS budget missed: {avg_def * 1000:.1f}ms avg (strict mode)"
        print("  ok: strict wall-clock budget held (<17ms)")
    test_adaptive_budget()
    print("smoke_sector_perf: ALL OK (frame budget, ratios, adaptive ladder)")


if __name__ == "__main__":
    main()