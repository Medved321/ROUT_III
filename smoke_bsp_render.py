"""Smoke: BSP sector renderer (change ``bsp-renderer-mt1-format``, section 4).

The walk is DOOM near->far: near walls are drawn first and close their
columns, far walls draw only into the openings left by the clips (or are
discarded entirely, R_RenderSegLoop semantics).

* ``_walk_subsectors`` — near room's wall subsector is visited before the
  far room's; whole subtrees behind the camera / outside the frustum are
  culled (task 4.1, re-boxed near->far).
* ``bsp_clipped_frame`` — unit render with per-column clipping: the near
  wall covers the far wall, the portal opening still reveals the far wall.

Window/step frames, seg discard (4.2) and visplane flats (4.3) are asserted
as they land: near walls clip far walls and discard fully-closed segs; flat
spans are bounded by the per-column clips, textured via numpy or solid by
budget, and never leak into closed columns.  A door on an island's edge
(``maps/sector2.mt1``) renders whole from both sides — the within-subsector
near-to-far order (change ``sector-doors-and-keys``, D10).
"""
from __future__ import annotations

import math
import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pygame  # noqa: E402

from engine import game_state as gs  # noqa: E402
from shared.sector_map import (  # noqa: E402
    LineDef, Sector, SectorMap, SideDef, Thing, Vertex,
)


def _init_display():
    pygame.init()
    gs.screen = pygame.display.set_mode((1000, 700))


def _vertex(sm, x, y):
    for i, v in enumerate(sm.verts):
        if v.x == x and v.y == y:
            return i
    sm.verts.append(Vertex(float(x), float(y)))
    return len(sm.verts) - 1


def _line(sm, ax, ay, bx, by, front=-1, back=-1, mid="WALL",
          up="-", low="-"):
    f = -1
    if front >= 0:
        f = len(sm.sidedefs)
        sm.sidedefs.append(SideDef(sector=front, mid=mid, upper=up, lower=low))
    b = -1
    if back >= 0:
        b = len(sm.sidedefs)
        sm.sidedefs.append(SideDef(sector=back, mid=mid, upper=up, lower=low))
    sm.linedefs.append(LineDef(_vertex(sm, ax, ay), _vertex(sm, bx, by),
                               front=f, back=b))
    return len(sm.linedefs) - 1


def _make_two_room():
    """Room A (0..512, WALLA) and room B (512..1024, WALLB) divided by the
    wall x=512; a two-sided portal y 224..288 connects them."""
    sm = SectorMap()
    sm.sectors.append(Sector(floor_h=0, ceil_h=128, light=255))
    sm.sectors.append(Sector(floor_h=0, ceil_h=128, light=255))
    # Room A boundary.
    _line(sm, 0, 0, 512, 0, 0, mid="WALLA")
    _line(sm, 512, 0, 512, 224, 0, mid="WALLA")      # portal top jamb
    _line(sm, 512, 224, 512, 288, 0, 1, mid="-")     # two-sided portal
    _line(sm, 512, 288, 512, 512, 0, mid="WALLA")    # portal bottom jamb
    _line(sm, 512, 512, 0, 512, 0, mid="WALLA")
    _line(sm, 0, 512, 0, 0, 0, mid="WALLA")
    # Room B (far wall x=1024).
    _line(sm, 512, 0, 1024, 0, 1, mid="WALLB")
    _line(sm, 1024, 0, 1024, 512, 1, mid="WALLB")    # far wall
    _line(sm, 1024, 512, 512, 512, 1, mid="WALLB")
    sm.things.append(Thing(384, 256))
    assert not sm.validate(), sm.validate()
    return sm


def _textures():
    gs.textures = {}
    gs.flats = {}
    ta = pygame.Surface((64, 64))
    ta.fill((220, 30, 30))
    tb = pygame.Surface((64, 64))
    tb.fill((30, 30, 220))
    gs.textures["WALLA"] = ta
    gs.textures["WALLB"] = tb
    gf = pygame.Surface((64, 64))
    gf.fill((10, 200, 10))          # floor flats: green
    gc = pygame.Surface((64, 64))
    gc.fill((10, 100, 200))         # ceiling flats: blue
    gs.flats["floor"] = gf
    gs.flats["ceiling"] = gc


def _scene():
    from engine.sector_render import SectorScene

    _textures()
    return SectorScene(_make_two_room())


def _segs_of(tree, li):
    return [i for i, s in enumerate(tree.segs) if s.linedef == li]


def test_walk_order():
    from engine import sector_render

    scene = _scene()
    bsp = scene.ensure_bsp()
    lds = scene.sm.linedefs
    verts = scene.sm.verts
    li_jamb = next(i for i, ld in enumerate(lds)
                   if verts[ld.v1].x == 512 and verts[ld.v1].y == 0
                   and verts[ld.v2].x == 512 and verts[ld.v2].y == 224)
    li_far = next(i for i, ld in enumerate(lds)
                  if verts[ld.v1].x == 1024 and verts[ld.v1].y == 0
                  and verts[ld.v2].x == 1024 and verts[ld.v2].y == 512)
    si_jamb = _segs_of(bsp, li_jamb)[0]
    si_far = _segs_of(bsp, li_far)[0]
    sub_jamb = bsp.subsector_of_seg(si_jamb)
    sub_far = bsp.subsector_of_seg(si_far)
    assert sub_jamb >= 0 and sub_far >= 0 and sub_jamb != sub_far
    order = sector_render._walk_subsectors(bsp, 384, 256, 0.0)
    assert sub_jamb in order and sub_far in order, (sub_jamb, sub_far, order)
    assert order.index(sub_jamb) < order.index(sub_far), (
        "near room wall must come before the far room wall (near->far)",
        order.index(sub_jamb), order.index(sub_far))
    print("  walk order       OK")


def test_frustum_cull():
    from engine import sector_render

    scene = _scene()
    bsp = scene.ensure_bsp()
    lds = scene.sm.linedefs
    verts = scene.sm.verts
    li_far = next(i for i, ld in enumerate(lds)
                  if verts[ld.v1].x == 1024 and verts[ld.v1].y == 0
                  and verts[ld.v2].x == 1024 and verts[ld.v2].y == 512)
    si_far = _segs_of(bsp, li_far)[0]
    sub_far = bsp.subsector_of_seg(si_far)
    # Looking away (+x plan), the whole room B box sits behind the camera:
    # its subsector must be pruned entirely.
    order_away = sector_render._walk_subsectors(bsp, 384, 256, math.pi)
    assert sub_far not in order_away, (sub_far, order_away)
    # Looking straight at the portal opening: the far room is visible, so its
    # leaf survives culling.
    order_open = sector_render._walk_subsectors(bsp, 384, 256, 0.0)
    assert sub_far in order_open
    print("  frustum cull    OK")


def _pix_region(x, y, r):
    out = []
    for yy in range(y - r, y + r + 1):
        for xx in range(x - r, x + r + 1):
            out.append(gs.screen.get_at((xx, yy))[:3])
    return out


def _make_window_step():
    """Window + step frame: room A (0..512, floor 0, ceil 128, WALLA),
    room B (512..1024, floor 32, ceil 64, WALLB).  The wall x=512 has a
    two-sided window y 224..288; the B floor being higher turns the window
    sill into a step.  Player at (412, 256) looking +x."""
    sm = SectorMap()
    sm.sectors.append(Sector(floor_h=0, ceil_h=128, light=255))
    sm.sectors.append(Sector(floor_h=32, ceil_h=64, light=255))
    _line(sm, 0, 0, 512, 0, 0, mid="WALLA")
    _line(sm, 512, 0, 512, 224, 0, mid="WALLA")     # window top jamb
    _line(sm, 512, 224, 512, 288, 0, 1, mid="-", up="WALLA", low="WALLA")  # window
    _line(sm, 512, 288, 512, 512, 0, mid="WALLA")   # window sill jamb
    _line(sm, 512, 512, 0, 512, 0, mid="WALLA")
    _line(sm, 0, 512, 0, 0, 0, mid="WALLA")
    _line(sm, 512, 0, 1024, 0, 1, mid="WALLB")
    _line(sm, 1024, 0, 1024, 512, 1, mid="WALLB")   # far wall
    _line(sm, 1024, 512, 512, 512, 1, mid="WALLB")
    sm.things.append(Thing(412, 256))
    assert not sm.validate(), sm.validate()
    return sm


def _window_seg_id(scene, side=1):
    """The window linedef's seg on the requested side (1 = the B-side face).

    The portal line's direction is fixed by the sector polygon closure, so for
    the camera in room A looking +x the kept (back-face cull, change
    ``sector-step-down-pit``) twin is the ``side=1`` seg — its direction has
    the camera on its right.  The ``side=0`` seg is the culled back face and
    must stay silent.
    """
    bsp = scene.ensure_bsp()
    lds = scene.sm.linedefs
    verts = scene.sm.verts
    li = next(i for i, ld in enumerate(lds)
              if verts[ld.v1].x == 512 and verts[ld.v2].x == 512
              and {verts[ld.v1].y, verts[ld.v2].y} == {224.0, 288.0})
    out = [i for i in _segs_of(bsp, li) if bsp.segs[i].side == side]
    assert out, "window seg missing"
    return out[0]


def _frame_state(builder=_make_two_room):
    from engine.sector_render import SectorScene

    gs.sector_mode = True
    gs.render_scale = 1
    gs.vs_scale = 1
    gs.view_dist = 900
    gs.flat_textured = False
    gs.quality_tier = "high"
    gs.vh720 = False
    gs.pz = 0.0
    _textures()
    gs.sec_scene = SectorScene(builder())


def test_painter_near_over_far():
    from engine import sector_render

    _frame_state()
    gs.px, gs.py, gs.pa = 412, 256, 0.0
    order = sector_render.bsp_painter_frame(700, 0)
    assert order, "no visible subsectors"
    # Screen col 200: covered by the near portal jamb (WALLA, red-tinted).
    # Far wall (WALLB, blue) is behind it and must not show through.
    reds = [c for c in _pix_region(200, 350, 2)]
    assert any(r > 120 and b < 60 for r, g, b in reds), f"no near wall at col 200: {reds}"
    # Screen col 400: through the portal opening, the far wall is visible.
    blues = [c for c in _pix_region(400, 350, 2)]
    assert any(b > 50 and r < 60 for r, g, b in blues), f"no far wall in opening: {blues}"
    print("  painter near/far OK")


def test_wall_window_step():
    from engine import sector_render

    _frame_state(_make_window_step)
    gs.px, gs.py, gs.pa = 412, 256, 0.0
    order, clip_top, clip_bot = sector_render.bsp_clipped_frame(700, 0)
    assert order, "no visible subsectors"
    # Inside the window (col 700, row 340): the far room's wall.
    blues = [c for c in _pix_region(700, 340, 2)]
    assert any(b > 50 and r < 60 for r, g, b in blues), f"no far wall in window: {blues}"
    # Above the far wall / below the window top: the B ceiling gap — no
    # "smeared" A ceiling over the opening, so the region stays void.
    over = [c for c in _pix_region(700, 250, 2)]
    assert not any(r > 90 and b < 60 for r, g, b in over), f"ceiling smear: {over}"
    # Below the opening but on the sill lip (the step): room A red lip.
    lows = [c for c in _pix_region(700, 560, 2)]
    assert any(r > 120 and b < 60 for r, g, b in lows), f"no step lip: {lows}"
    # Outside the window (col 150): the near solid jamb covers the column.
    sides = [c for c in _pix_region(150, 300, 2)]
    assert any(r > 120 and b < 60 for r, g, b in sides), f"no jamb: {sides}"
    print("  wall window/step OK")


def test_two_sided_clips():
    from engine import sector_render

    _frame_state(_make_window_step)
    scene = gs.sec_scene
    bsp = scene.ensure_bsp()
    si = _window_seg_id(scene, side=1)
    iw, ih = 1000, 700
    clip_top = [0.0] * iw
    clip_bot = [float(ih)] * iw
    bands = []
    sector_render._seg_wall_bands(bands, scene, bsp, si,
                                  gs.px, gs.py, gs.pa, 750.0, 350.0,
                                  iw, ih, 41.0, 900.0, clip_top, clip_bot)
    assert bands, "window seg must emit upper/lower lip bands"
    # The back-facing twin (side 0) contributes nothing: one seg per column
    # owns the clip movement, so the pit/stair floor is never evicted by the
    # reversed face (sector-step-down-pit back-face cull).
    si_back = _window_seg_id(scene, side=0)
    back_bands = []
    sector_render._seg_wall_bands(back_bands, scene, bsp, si_back,
                                  gs.px, gs.py, gs.pa, 750.0, 350.0,
                                  iw, ih, 41.0, 900.0, clip_top[:], clip_bot[:])
    assert not back_bands, f"back-facing twin must be culled: {back_bands}"
    # Columns inside the opening stay open and pinch exactly to the lip edges:
    # the camera-facing twin closes the ceiling to B's ceiling (the higher
    # bound) and the floor to ITS front floor line (the visible face).
    ct, cb = clip_top[400], clip_bot[400]
    assert 176.0 <= ct <= 179.0, f"opening ceiling bound: {ct}"
    assert 656.0 <= cb <= 659.0, f"opening floor bound: {cb}"
    print("  two-sided clips OK")


def test_clip_discard():
    from engine import sector_render

    _frame_state(_make_window_step)
    scene = gs.sec_scene
    bsp = scene.ensure_bsp()
    si = _window_seg_id(scene, side=1)
    iw, ih = 1000, 700
    clip_top = [0.0] * iw
    clip_bot = [float(ih)] * iw
    # Pretend every column of the window span is closed by nearer walls.
    for col in range(200, 800):
        clip_top[col] = clip_bot[col] = 350.0
    before = (clip_top[:], clip_bot[:])
    bands = []
    sector_render._seg_wall_bands(bands, scene, bsp, si,
                                  gs.px, gs.py, gs.pa, 750.0, 350.0,
                                  iw, ih, 41.0, 900.0, clip_top, clip_bot)
    assert not bands, f"fully closed seg must not emit bands: {bands}"
    assert clip_top == before[0] and clip_bot == before[1], (
        "discarded seg must not touch the clip arrays")
    print("  clip discard    OK")


def test_flats_span_clips():
    """Visplane spans (4.3) are bounded by the per-column clips: a seg whose
    columns are fully closed pushes no spans; open columns push the front
    sector's ceiling/floor spans against THIS wall's seams."""
    from engine import sector_render

    _frame_state(_make_window_step)
    scene = gs.sec_scene
    bsp = scene.ensure_bsp()
    si = _window_seg_id(scene, side=1)
    # Window lies over cols ~223..777; leave [300, 600) open.
    iw, ih = 1000, 700
    clip_top = [0.0] * iw
    clip_bot = [float(ih)] * iw
    for col in range(iw):
        if col < 300 or col >= 600:
            clip_top[col] = clip_bot[col] = 350.0
    flat_spans = []
    bands = []
    sector_render._seg_wall_bands(bands, scene, bsp, si,
                                  gs.px, gs.py, gs.pa, 750.0, 350.0,
                                  iw, ih, 41.0, 900.0, clip_top, clip_bot,
                                  flat_spans)
    assert bands, "window lips still drawn in the open columns"
    assert flat_spans, "open window columns must contribute flat spans"
    seen_floor = False
    for (x, y0, y1, sid, floor) in flat_spans:
        assert 300 <= x < 600, f"span leaked into a closed column: {x}"
        assert sid == 1, f"camera-facing window seg front is B (sid 1): {sid}"
        if not floor:
            # Front ceiling row (B ceiling 64) sits mid-frame here -> non-empty.
            assert y1 > y0
        else:
            # B floor behind the window: from the step base ~417 (B floor)
            # down to the frame bottom.
            assert 415.0 <= y0 <= 420.0, f"B floor bound: {y0}"
            assert y1 == ih
            seen_floor = True
    assert seen_floor, "B floor span missing in the open columns"
    # Fully closed columns: whole seg discarded, no spans, clips untouched.
    clip_top = [350.0] * iw
    clip_bot = [350.0] * iw
    flat_spans = []
    sector_render._seg_wall_bands([], scene, bsp, si,
                                  gs.px, gs.py, gs.pa, 750.0, 350.0,
                                  iw, ih, 41.0, 900.0, clip_top, clip_bot,
                                  flat_spans)
    assert not flat_spans, f"closed seg must not contribute spans: {flat_spans}"
    # Far side: B's far wall owes its spans to the far room's seg, not A's.
    print("  flat span clips OK")


def test_flats_solid_frame():
    """Solid (flat_textured off) fills: B ceiling/floor visible through the
    window as the average flat colour, while walls are drawn on top."""
    from engine import sector_render

    _frame_state(_make_window_step)   # flat_textured = False
    gs.px, gs.py, gs.pa = 412, 256, 0.0
    order, _, _ = sector_render.bsp_clipped_frame(700, 0)
    assert order

    # B ceiling solid fill (avg of the blue ceiling tex, light-shaded).
    r, g, b = gs.screen.get_at((500, 250))[:3]
    assert b > 120 and g < 110 and r < 60, f"B ceiling solid: {(r, g, b)}"
    # B floor solid fill (avg of the green floor tex).
    r, g, b = gs.screen.get_at((500, 390))[:3]
    assert g > 120 and r < 60 and b < 60, f"B floor solid: {(r, g, b)}"
    # Far wall band: wall colour drawn over the flat.
    r, g, b = gs.screen.get_at((500, 341))[:3]
    assert b > r and g < 50, f"far wall covered by flat: {(r, g, b)}"
    # Closed column (off the window): floor flat under the A wall, red wall
    # drawn over it (no flat leaks over a nearer wall).
    r, g, b = gs.screen.get_at((100, 690))[:3]
    assert g > 120 and r < 60, f"A floor solid: {(r, g, b)}"
    r, g, b = gs.screen.get_at((100, 341))[:3]
    assert r > 150 and g < 60, f"A wall over the flat: {(r, g, b)}"
    print("  flats solid      OK")


def test_flats_textured_frame():
    """Textured (flat_textured on) fills: numpy perspective rasterization of
    the B floor/ceiling through the window; walls still drawn on top and no
    flat leaks into columns closed by nearer walls (недо-рисовка за стенами)."""
    from engine import sector_render

    _frame_state(_make_window_step)
    gs.flat_textured = True
    gs.px, gs.py, gs.pa = 412, 256, 0.0
    sector_render.bsp_clipped_frame(700, 0)

    r, g, b = gs.screen.get_at((500, 390))[:3]
    assert g > 120 and r < 60 and b < 60, f"B floor tex missing: {(r, g, b)}"
    r, g, b = gs.screen.get_at((500, 250))[:3]
    assert b > 120 and r < 60 and g < 100, f"B ceiling tex missing: {(r, g, b)}"
    # Far wall above the floor flat (occlusion: walls over flats).
    r, g, b = gs.screen.get_at((500, 341))[:3]
    assert b > r and g < 50, f"far wall lost under flat: {(r, g, b)}"
    # Column closed by A's jamb: no green floor leaks over the wall.
    r, g, b = gs.screen.get_at((100, 341))[:3]
    assert r > 150 and g < 60, f"A wall leaked over: {(r, g, b)}"
    # The A floor (visible under the jamb) IS textured.
    r, g, b = gs.screen.get_at((100, 690))[:3]
    assert g > 120 and r < 60, f"A floor tex missing: {(r, g, b)}"
    print("  flats textured   OK")


def test_door_island_both_sides():
    """A door on an island's edge must render whole from BOTH sides.

    ``maps/sector2.mt1`` is an island/hole: one subsector holds both the
    door's seg and a far island wall, so the far two-sided lip used to be
    processed first and pinch the door's columns to its own opening — half the
    door vanished from the sector-0 side (change ``sector-doors-and-keys``,
    design D10).  The near-to-far within-subsector order fixes it; this guards
    the frame on the real map.
    """
    from engine import constants as const
    from engine import sector_render
    from shared.sector_map import SectorMap

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "maps", "sector2.mt1")
    if not os.path.exists(path):
        print("  door island (map missing, skipped)")
        return

    gs.textures = {}
    for n in ("wall", "stone", "wood", "exit", "elevator", "spikes",
              "door_R", "door_Y", "door_B"):
        s = pygame.Surface((const.TEX_W, const.TEX_W))
        s.fill((0, 255, 0))
        gs.textures[n] = s
    door_tex = pygame.Surface((const.TEX_W, const.TEX_W))
    door_tex.fill((255, 0, 255))
    gs.textures["door_D"] = door_tex
    gs.flats = {}
    for n in ("floor", "ceiling"):
        s = pygame.Surface((const.TEX_W, const.TEX_W))
        s.fill((10, 10, 10))
        gs.flats[n] = s

    sm = SectorMap.from_mt1(open(path, "rb").read())
    dl = sm.door_linedefs()[0]

    def _door_pixels(cam):
        gs.sec_scene = sector_render.SectorScene(sm)
        gs.sec_scene.door_progress = {dl: 0.0}
        gs.sector_mode = True
        gs.render_scale = 1
        gs.vs_scale = 1
        gs.view_dist = 1500
        gs.flat_textured = False
        gs.px, gs.py = cam
        gs.pa = math.atan2(328 - cam[1], 240 - cam[0])
        gs.pz = 0.0
        gs.vh720 = False
        sector_render.render_scene(700, 0)
        w, h = gs.screen.get_size()
        tot = mid = 0
        for y in range(h):
            r, g, b = gs.screen.get_at((w // 2, y))[:3]
            if r > 150 and b > 150 and g < 100:
                mid += 1
        for y in range(0, h, 3):
            for x in range(0, w, 3):
                r, g, b = gs.screen.get_at((x, y))[:3]
                if r > 150 and b > 150 and g < 100:
                    tot += 1
        return tot, mid

    tot_b, mid_b = _door_pixels((265.0, 476.0))   # sector 0, outside the island
    tot_a, mid_a = _door_pixels((215.0, 180.0))   # sector 2, inside the island
    # Both faces see the full door plate (the bug left sector 0 with mid=210,
    # tot=16569 while sector 2 stayed whole at mid=555, tot=34687).
    assert mid_b >= 500 and mid_a >= 500, (mid_b, mid_a)
    assert tot_b >= 30000 and tot_a >= 30000, (tot_b, tot_a)
    print("  door island both sides OK")


def main():
    _init_display()
    test_walk_order()
    test_frustum_cull()
    test_painter_near_over_far()
    test_wall_window_step()
    test_two_sided_clips()
    test_clip_discard()
    test_flats_span_clips()
    test_flats_solid_frame()
    test_flats_textured_frame()
    test_door_island_both_sides()
    print("smoke_bsp_render: ALL OK")


if __name__ == "__main__":
    main()