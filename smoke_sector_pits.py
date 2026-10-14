"""Smoke: sector pits — the lower face and the pit floor (sector-step-down-pit).

Scene from the proposal: room 512x512 with a sunken sector 128x128 inside,
the eye 92 units from the pit's near edge, looking into it.  Three
configurations — depth -64, step -16 and a flat control (depth 0) — plus an
up-step control (platform +48) and an occluded variant (one-sided pillar in
front of the pit lip).

Checks (tasks 2.1-2.6):

* the pit floor is pushed as flat spans for sector 1 and its texture gets
  pixels in the frame;
* the -64 and -16 frames differ (depth reads; they used to be identical);
* the ``lower`` lip band is drawn and lies between the two floor lines;
* the equal-floor control draws no lip bands and never evicts the pit floor
  (the inner sector's floor still reaches the frame bottom), and the up-step
  control keeps an explicit lip between its floor lines;
* a lip in a column already owned by a nearer solid wall draws nothing and
  does not move that column's floor clip.
"""
from __future__ import annotations

import math
import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pygame  # noqa: E402

from engine import constants as const  # noqa: E402
from engine import game_state as gs  # noqa: E402
from shared.sector_map import (  # noqa: E402
    LineDef, Sector, SectorMap, SideDef, Thing, Vertex,
)

SOUTH = 1.5707963
PIT_SECTOR = 1

COL = {
    "floor0": (200, 40, 40),
    "floor1": (40, 60, 220),
    "ceil0": (200, 200, 60),
    "ceil1": (60, 200, 220),
    "wall": (170, 170, 170),
    "lower": (250, 140, 20),
    "upper": (140, 250, 60),
    "mid": (255, 0, 255),
}


def _init():
    pygame.init()
    gs.screen = pygame.display.set_mode((const.W, const.H))
    gs.textures = {}
    gs.flats = {}
    for k, v in COL.items():
        s = pygame.Surface((64, 64))
        s.fill(v)
        gs.flats[k] = s
        gs.textures[k] = s


def classify(rgb):
    best, bd = "?", 1e18
    for name, c in COL.items():
        d = sum((a - b) ** 2 for a, b in zip(rgb, c))
        if d < bd:
            bd, best = d, name
    return best


def _vertex(sm, x, y):
    for i, v in enumerate(sm.verts):
        if v.x == x and v.y == y:
            return i
    sm.verts.append(Vertex(float(x), float(y)))
    return len(sm.verts) - 1


def _line(sm, ax, ay, bx, by, front=-1, back=-1, up="-", low="-", mid="-"):
    a, b = _vertex(sm, ax, ay), _vertex(sm, bx, by)
    f = -1
    if front >= 0:
        f = len(sm.sidedefs)
        sm.sidedefs.append(SideDef(sector=front, upper=up, lower=low, mid=mid))
    bk = -1
    if back >= 0:
        bk = len(sm.sidedefs)
        sm.sidedefs.append(SideDef(sector=back, upper=up, lower=low, mid=mid))
    sm.linedefs.append(LineDef(a, b, front=f, back=bk))
    return len(sm.linedefs) - 1


def _box(sm, x0, y0, x1, y1, front, back=-1, low="-", mid="-"):
    """Rect whose interior sits on the BACK side (the editor's convention)."""
    _line(sm, x0, y0, x1, y0, front, back, low=low, mid=mid)
    _line(sm, x1, y0, x1, y1, front, back, low=low, mid=mid)
    _line(sm, x1, y1, x0, y1, front, back, low=low, mid=mid)
    _line(sm, x0, y1, x0, y0, front, back, low=low, mid=mid)


def pit_map(depth=-64, pit_ceil=128, x0=192, x1=320, y0=192, y1=448,
            occluder=None):
    """Room 0 (floor 0) around a sunken sector 1 (floor ``depth``).

    ``occluder`` adds a one-sided pillar (x0, y0)-(x1, y1) in front of the
    pit lip: its columns are owned by a NEARER solid wall than the lip.
    """
    sm = SectorMap()
    sm.sectors.append(Sector(floor_h=0, ceil_h=128, light=255,
                             floor_tex="floor0", ceil_tex="ceil0"))
    sm.sectors.append(Sector(floor_h=depth, ceil_h=pit_ceil, light=255,
                             floor_tex="floor1", ceil_tex="ceil1"))
    _box(sm, 0, 0, 512, 512, 0, mid="wall")
    _box(sm, x0, y0, x1, y1, 0, 1, low="lower")
    if occluder is not None:
        _box(sm, *occluder, 0, mid="wall")
    sm.things.append(Thing(256, 100))
    errs = sm.validate()
    assert not errs, errs
    return sm


def plat_map(plat_h=48):
    """Room 0 with a raised sector 1 — the step-UP regression control."""
    sm = SectorMap()
    sm.sectors.append(Sector(floor_h=0, ceil_h=160, light=255,
                             floor_tex="floor0", ceil_tex="ceil0"))
    sm.sectors.append(Sector(floor_h=plat_h, ceil_h=160, light=255,
                             floor_tex="floor1", ceil_tex="ceil1"))
    _box(sm, 0, 0, 512, 512, 0, mid="wall")
    _box(sm, 192, 192, 320, 448, 0, 1, low="lower")
    sm.things.append(Thing(256, 100))
    errs = sm.validate()
    assert not errs, errs
    return sm


def _render(sm, px=256.0, py=100.0, pa=SOUTH, mod=None,
            record_bands=False, record_pushes=False):
    """One frame; returns z-buffer, band list, flat pushes, pixels, census."""
    from engine import sector_render as SR

    if mod is None:
        mod = SR
    gs.sec_scene = SR.SectorScene(sm)
    gs.sector_mode = True
    gs.render_scale = 2
    gs.vs_scale = 2
    gs.view_dist = 2000
    gs.flat_textured = True
    gs.px, gs.py, gs.pa = px, py, pa
    gs.pz = 0.0
    gs.vh720 = False
    bands_cap, push_cap = [], []
    real_b, real_p = mod._draw_wall_bands, mod._push_flat
    if record_bands:
        def spy_b(frame, bands, *rest):
            bands_cap.extend(bands)
            return real_b(frame, bands, *rest)
        mod._draw_wall_bands = spy_b
    if record_pushes:
        def spy_p(spans, *rest):
            push_cap.append(rest[:5])
            return real_p(spans, *rest)
        mod._push_flat = spy_p
    try:
        z = mod.render_scene(const.H, 0)
    finally:
        mod._draw_wall_bands = real_b
        mod._push_flat = real_p
    frame = mod._fb_ensure()
    surf = pygame.transform.scale(frame.to_surface(), (const.W, const.H))
    census = {}
    for y in range(0, const.H, 2):
        for x in range(0, const.W, 2):
            n = classify(surf.get_at((x, y))[:3])
            census[n] = census.get(n, 0) + 1
    return {"z": z, "bands": bands_cap, "pushes": push_cap,
            "bytes": pygame.image.tobytes(surf, "RGB"), "census": census}


def _proj_consts():
    """(F, hc, eye) of the current render settings — same maths as the code."""
    vh = const.H
    ih = max(2, vh // max(1, gs.vs_scale))
    F = 750.0 * ih / vh
    return F, ih / 2.0, const.CAM_Z


def _seg_cols(ax, ay, bx, by, px, py, pa, iw, tan_h):
    """Screen-column span a world segment projects into (mirrors the code)."""
    ca, sa = math.cos(pa), math.sin(pa)
    dx1, dy1 = ax - px, ay - py
    dx2, dy2 = bx - px, by - py
    t1 = dx1 * ca + dy1 * sa
    s1 = -dx1 * sa + dy1 * ca
    t2 = dx2 * ca + dy2 * sa
    s2 = -dx2 * sa + dy2 * ca
    if t1 <= 0.0 and t2 <= 0.0:
        return []
    if t1 <= 0.0:
        k = -t1 / (t2 - t1)
        s1 += (s2 - s1) * k
        t1 = 1e-6
    elif t2 <= 0.0:
        k = -t2 / (t1 - t2)
        s2 += (s1 - s2) * k
        t2 = 1e-6
    ca1 = 0.5 + 0.5 * s1 / (t1 * tan_h)
    ca2 = 0.5 + 0.5 * s2 / (t2 * tan_h)
    clo, chi = min(ca1, ca2), max(ca1, ca2)
    if chi < 0.0 or clo > 1.0:
        return []
    c0 = max(0, int(clo * iw))
    c1 = min(iw, int(chi * iw) + 1)
    return list(range(c0, c1))


def test_map_builders():
    """Task 2.1: all five scene maps are valid sector maps."""
    maps = [pit_map(-64), pit_map(-16), pit_map(0), plat_map(),
            pit_map(occluder=(280, 130, 340, 190))]
    for sm in maps:
        errs = sm.validate()
        assert not errs, errs
    print("  map builders      OK")


def test_pit_floor_visible():
    """Task 2.2: the pit floor pushes flat spans and paints its texture."""
    r = _render(pit_map(-64), record_bands=True, record_pushes=True)
    spans = [p for p in r["pushes"] if len(p) >= 5
             and p[3] == PIT_SECTOR and p[4]]
    assert spans, f"no floor spans pushed for pit sector: {r['pushes'][:8]}"
    px = r["census"].get("floor1", 0)
    assert px > 0, f"pit floor texture has no pixels: {r['census']}"
    print(f"  pit floor visible OK ({len(spans)} spans, {px} px)")


def test_depth_readable():
    """Task 2.3: -64 and -16 frames differ; both show the floor."""
    c = {}
    b = {}
    for depth in (0, -16, -64):
        r = _render(pit_map(depth))
        c[depth], b[depth] = r["census"], r["bytes"]
    assert b[-64] != b[-16], "-64 and -16 frames are identical (depth lost)"
    assert b[-64] != b[0], "-64 and flat-control frames are identical"
    assert c[-64].get("floor1", 0) > 0, "pit floor not visible at -64"
    assert c[-16].get("floor1", 0) > 0, "pit floor not visible at -16"
    print(f"  depth readable    OK (floor1 px: "
          f"-64={c[-64]['floor1']}, -16={c[-16]['floor1']})")


def test_lower_band_between_floors():
    """Task 2.4: the lower face band exists and lies between the floor lines."""
    r = _render(pit_map(-64), record_bands=True)
    low = [b for b in r["bands"] if b[3] == "lower"]
    assert low, "no lower band drawn for the pit edge"
    F, hc, eye = _proj_consts()
    for b in low:
        col, y0, y1, dist = b[0], b[1], b[2], b[10]
        yf = hc + (eye - 0.0) * F / dist
        yfb = hc + (eye - (-64.0)) * F / dist
        lo, hi = min(yf, yfb), max(yf, yfb)
        assert lo - 1.0 <= y0 and y1 <= hi + 1.0, \
            (col, y0, y1, round(lo, 1), round(hi, 1), dist)
    print(f"  lower band        OK ({len(low)} bands, all between floors)")


def test_control_matches_reference():
    """Task 2.5: equal floors draw no lip and never evict the pit floor.

    The equal-floor control (depth 0) must not draw a ``lower``/``upper`` lip
    band, and the pit floor of a same-level aperture must still push down to
    the frame bottom — the back-face cull (sector-step-down-pit) keeps a
    single twin per line, so the reversed face can no longer clamp ``clip_bot``
    to the aperture floor and evict the inner sector's floor.  The byte-vs-HEAD
    claim is gone from the contract: a same-level portal now legitimately
    paints the inner sector's floor, which HEAD never showed.  The up-step
    control keeps an explicit lip band whose span lies between the two floor
    lines.
    """
    _r = _render(pit_map(0), record_bands=True)
    stray = [b for b in _r["bands"] if b[3] in ("lower", "upper")]
    assert not stray, f"equal floors must draw no lip bands: {stray[:4]}"
    assert _r["census"].get("floor1", 0) > 0, \
        "same-level pit floor must still show its own texture"
    F, hc, eye = _proj_consts()
    new_p = _render(plat_map(), record_bands=True)
    low = [b for b in new_p["bands"] if b[3] == "lower"]
    assert low, "up-step lip band not drawn"
    assert new_p["census"].get("floor1", 0) > 0, \
        "up-step platform top must still be visible"
    for b in low:
        col, y0, y1, dist = b[0], b[1], b[2], b[10]
        yf = hc + (eye - 0.0) * F / dist
        yfb = hc + (eye - 48.0) * F / dist
        lo, hi = min(yf, yfb), max(yf, yfb)
        assert lo - 1.0 <= y0 and y1 <= hi + 1.0, \
            (col, y0, y1, round(lo, 1), round(hi, 1), dist)
    print(f"  control floors    OK (flat no-lip eviction-free, "
          f"step lip between floors)")


def test_lip_behind_near_wall():
    """Task 2.6: a lip behind a nearer solid draws nothing, clips nothing.

    One-sided pillar in front of the pit lip: its screen columns are owned by
    ``solid_depth[col] ~ 24..90`` while the lip sits at ~92.  For every band
    the renderer emits we check, per column, that a ``lower`` band never
    arrives at a depth >= the solid that already owns the column, and that no
    call touching an owned column moves its floor clip.
    """
    from engine import sector_render as SR

    occluder = (280.0, 130.0, 340.0, 190.0)
    sm = pit_map(occluder=occluder)
    iw = const.W // 2
    tan_h = math.tan(const.FOV * 0.5)
    lip_cols = set(_seg_cols(192, 192, 320, 192, 256.0, 100.0, SOUTH,
                             iw, tan_h))
    occ_cols = set()
    for e in ((280, 130, 340, 130), (340, 130, 340, 190),
              (340, 190, 280, 190), (280, 190, 280, 130)):
        occ_cols |= set(_seg_cols(*e, 256.0, 100.0, SOUTH, iw, tan_h))
    closed = lip_cols & occ_cols
    assert closed, "the pillar does not overlap the pit lip in screen columns"

    state = {"solid_t": [None] * iw, "violations": [], "lip_drawn": set()}
    real = SR._seg_wall_bands

    def spy(*args, **kw):
        bands = args[0]
        clip_bot = args[14] if len(args) > 14 else kw.get("clip_bot")
        before = len(bands)
        snap = list(clip_bot) if clip_bot is not None else None
        real(*args, **kw)
        st = state["solid_t"]
        for b in bands[before:]:
            col, tex, dist = b[0], b[3], b[10]
            if tex == "wall":
                if st[col] is None or dist < st[col]:
                    st[col] = dist
            elif tex == "lower":
                if st[col] is not None and dist >= st[col]:
                    state["violations"].append(("band", col, dist, st[col]))
                else:
                    state["lip_drawn"].add(col)
        if snap is not None:
            for c in closed:
                if st[c] is not None and clip_bot[c] != snap[c]:
                    state["violations"].append(
                        ("clip", c, snap[c], clip_bot[c]))

    SR._seg_wall_bands = spy
    try:
        r = _render(sm, record_bands=True)
    finally:
        SR._seg_wall_bands = real

    wall_cols = {b[0] for b in r["bands"] if b[3] == "wall"}
    assert wall_cols & closed, "the occluding wall was not drawn in closed columns"
    assert state["lip_drawn"] - closed, \
        "the pit lip must still be visible in the open columns"
    assert not state["violations"], state["violations"][:6]
    print(f"  lip behind wall   OK ({len(closed)} closed cols, "
          f"{len(state['lip_drawn'])} lip cols)")


def main():
    _init()
    test_map_builders()
    test_pit_floor_visible()
    test_depth_readable()
    test_lower_band_between_floors()
    test_control_matches_reference()
    test_lip_behind_near_wall()
    print("smoke_sector_pits: ALL OK")


if __name__ == "__main__":
    main()
