"""Smoke: floating slab overlay (change ``floating-sector-platforms``, section 4).

The plate is not a wall: its border never splits the BSP and never draws a
band, so the host's floor keeps spanning the footprint underneath, and
``engine/platform_render.py`` paints the plate on top of the wall pass.

Checks (tasks 4.1-4.5):

* the scene marks the slab's four border lines, ``floor_at`` under a plate is
  the host floor, and a map loaded with a stored BSP rebuilds instead of
  reusing a tree that partitioned along a slab border;
* the base pass really draws the host floor under the plate (no hole) and the
  plate itself is an overlay: it never partitions, never appears twice, and
  costs nothing on a map without plates or with ``gs.platforms_enabled`` off;
* top is ``floor_tex``, underside is ``ceil_tex``, a side is the *slab's own*
  ``mid`` — never the host's;
* a nearer wall keeps its column (the plate does not repaint through it) and
  the near plate wins over the far one, whose order does not depend on the
  sector indices;
* ``gs.player_z`` raises the camera above a plate while the floor under it
  stays put, and ``floor_z`` moves only the vertical of a projection;
* a sprite completely behind a plate is dropped, a partially covered one is
  drawn whole.
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

# Marker textures: every surface gets its own channel pair, so a rendered
# pixel can be classified back to "which surface is this" through any light or
# fog multiplier (light scales all channels together).
WALL = (255, 0, 0)          # red        -> "r"
HOST_FLOOR = (0, 255, 0)    # green      -> "g"
HOST_CEIL = (0, 0, 255)     # blue       -> "b"
TOP = (255, 255, 0)         # red+green  -> "rg"
UNDER = (0, 255, 255)       # green+blue -> "gb"
SIDE = (255, 0, 255)        # red+blue   -> "rb"
HOST_SIDE = (128, 128, 128)  # neutral   -> "rgb"


def _init():
    pygame.init()
    gs.screen = pygame.display.set_mode((1000, 700))
    gs.textures = {
        "wall": _tex(WALL),
        "side": _tex(SIDE),
        "host_side": _tex(HOST_SIDE),
    }
    gs.flats = {
        "host_floor": _tex(HOST_FLOOR),
        "host_ceil": _tex(HOST_CEIL),
        "top": _tex(TOP),
        "under": _tex(UNDER),
    }


def _tex(rgb):
    s = pygame.Surface((64, 64))
    s.fill(rgb)
    return s


def _sig(color) -> str:
    """Classify a rendered pixel by which channels survive the light scaling."""
    r, g, b = color[:3]
    hi = max(r, g, b)
    if hi <= 0:
        return "void"
    out = ""
    for ch, name in ((r, "r"), (g, "g"), (b, "b")):
        if ch >= hi * 0.7:
            out += name
    return out


def _slab_map(top=88, under=72, w=1024, h=512, host_ceil=256,
              slab_rect=(384, 128, 640, 384), slab_mid="side"):
    """Host room with one floating slab (top/underside given) in its middle."""
    sm = SectorMap()
    sm.sectors.append(Sector(floor_h=0, ceil_h=host_ceil, light=255,
                             floor_tex="host_floor", ceil_tex="host_ceil"))
    sm.sectors.append(Sector(floor_h=top, ceil_h=under, light=255,
                             floating=True, floor_tex="top", ceil_tex="under"))

    def vid(px, py):
        for idx, v in enumerate(sm.verts):
            if v.x == px and v.y == py:
                return idx
        sm.verts.append(Vertex(px, py))
        return len(sm.verts) - 1

    def rect(x1, y1, x2, y2, sector, back=None, mid="wall"):
        front = len(sm.sidedefs)
        sm.sidedefs.append(SideDef(sector=sector, mid=mid))
        back_side = None
        if back is not None:
            back_side = len(sm.sidedefs)
            sm.sidedefs.append(SideDef(sector=back, mid="host_side"))
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        for i in range(4):
            ax, ay = corners[i]
            bx, by = corners[(i + 1) % 4]
            if (bx - ax) * (cy - ay) - (by - ay) * (cx - ax) < 0:
                ax, ay, bx, by = bx, by, ax, ay
            sm.linedefs.append(LineDef(
                vid(ax, ay), vid(bx, by), front=front,
                back=back_side if back_side is not None else -1))

    rect(0, 0, w, h, 0)
    rect(*slab_rect, 1, back=0, mid=slab_mid)
    sm.things = [Thing(128, 256, "player1")]
    sm.invalidate()
    assert sm.validate() == [], sm.validate()
    return sm


def _plain_room(w=1024, h=512):
    sm = SectorMap()
    sm.sectors.append(Sector(floor_h=0, ceil_h=256, light=255,
                             floor_tex="host_floor", ceil_tex="host_ceil"))
    verts = [(0, 0), (w, 0), (w, h), (0, h)]
    idx = []
    for x, y in verts:
        sm.verts.append(Vertex(float(x), float(y)))
        idx.append(len(sm.verts) - 1)
    for i in range(4):
        front = len(sm.sidedefs)
        sm.sidedefs.append(SideDef(sector=0, mid="wall"))
        sm.linedefs.append(LineDef(idx[i], idx[(i + 1) % 4], front=front))
    sm.things = [Thing(128, 256, "player1")]
    sm.invalidate()
    assert sm.validate() == [], sm.validate()
    return sm


def _scene(sm):
    from engine.sector_render import SectorScene

    return SectorScene(sm)


def _render(sm, px=128.0, py=256.0, pa=0.0, player_z=0, flat_textured=True,
            view_dist=900):
    from engine import sector_render

    gs.sec_scene = _scene(sm)
    gs.sector_mode = True
    gs.render_scale = 4
    gs.vs_scale = 2
    gs.view_dist = view_dist
    gs.flat_textured = flat_textured
    gs.px, gs.py, gs.pa = px, py, pa
    gs.player_z = player_z
    gs.vh720 = False
    return sector_render.render_scene(700, 0)


def _px(x, y):
    return gs.screen.get_at((x, y))[:3]


# --- 4.1: model/BSP integration ------------------------------------------

def test_scene_marks_slab():
    sm = _slab_map()
    sc = _scene(sm)
    assert sc.floating == [False, True], sc.floating
    assert sc.has_floating and sc.floating_ids == [1]
    marked = [i for i, f in enumerate(sc.floating_line) if f]
    assert len(marked) == 4, marked
    for li in marked:
        ld = sm.linedefs[li]
        secs = {sm.sidedefs[s].sector for s in (ld.front, ld.back) if s >= 0}
        assert secs == {0, 1}, (li, secs)
    # under the plate: the camera/particles stand on the HOST floor
    assert sc.floor_at(512, 256) == 0, sc.floor_at(512, 256)
    # off the plate the answers agree with the plain single-sector lookup
    assert sc.floor_at(128, 256) == 0
    plain = _scene(_plain_room())
    assert plain.has_floating is False
    assert plain.floor_at(128, 256) == 0
    print("  scene marks slab  OK")


def test_bsp_not_partitioned_by_slab():
    from engine.bsp import (_floating_lines, _initial_segs, _pick_partition,
                            build_bsp)

    sm = _slab_map()
    skip = _floating_lines(sm)
    assert len(skip) == 4, skip
    # Teeth: on this minimal map the *only* legal root partition is a slab
    # border, so a builder that ignores the skip set provably partitions along
    # the plate (and hands the area under it to the plate's own subsector).
    natural = _pick_partition(_initial_segs(sm))
    assert natural is not None and natural.linedef in skip, (
        natural and natural.linedef, skip)
    assert _pick_partition(_initial_segs(sm), skip) is None

    tree = build_bsp(sm)
    borders = []
    verts = sm.verts
    for li in skip:
        ld = sm.linedefs[li]
        borders.append((verts[ld.v1].x, verts[ld.v1].y,
                        verts[ld.v2].x - verts[ld.v1].x,
                        verts[ld.v2].y - verts[ld.v1].y))
    for ni in range(tree.node_count()):
        n = tree._node_list[ni]
        for (bx, by, bdx, bdy) in borders:
            cross = bdx * n.pdy - bdy * n.pdx
            if abs(cross) > 1e-6:
                continue
            # collinear directions: also require the node line to pass through
            # the border segment (distance from its start point)
            dist = abs(bdx * (n.py - by) - bdy * (n.px - bx)) / (
                math.hypot(bdx, bdy) or 1.0)
            assert dist > 1e-6, (ni, (n.px, n.py), (bx, by), dist)
    assert _floating_lines(_plain_room()) == set()
    print("  bsp skips slab    OK")


def _with_stored_bsp(sm, floating_off=False):
    """Pack ``sm`` as ``.mt1`` with a BSP built from its *legacy* geometry."""
    from engine.bsp import build_bsp
    from shared.sector_map import Vertex

    legacy = _slab_map() if floating_off else sm
    if floating_off:
        legacy.sectors[1].floating = False
    lumps = build_bsp(legacy).to_lumps(legacy.verts)
    ext = lumps.pop("VERTEX_EXTRA")
    ext_sm = SectorMap([Vertex(v[0], v[1]) for v in ext],
                       list(sm.linedefs), list(sm.sidedefs),
                       list(sm.sectors), list(sm.things))
    return SectorMap.from_bytes(ext_sm.to_mt1(lumps))


def test_stored_bsp_rebuilt_for_slabs():
    from engine import bsp as bsp_mod

    # A legacy file: its BSP was built when the plate was just an ordinary
    # sector (the container had no SFLAGS to say otherwise), so it partitions
    # the world along the plate's border.  The scene must not reuse it.
    sm = _slab_map()
    loaded = _with_stored_bsp(sm, floating_off=True)
    assert getattr(loaded, "_bsp", None), "fixture must carry a stored BSP"

    calls = []
    real = bsp_mod.BspTree.from_lumps.__func__

    def spy(cls, *a, **kw):
        calls.append(1)
        return real(cls, *a, **kw)

    bsp_mod.BspTree.from_lumps = classmethod(spy)
    try:
        _scene(loaded).ensure_bsp()
        assert not calls, "a map with a slab must rebuild its BSP"
        _scene(_with_stored_bsp(_plain_room())).ensure_bsp()
        assert calls, ("a map without slabs must reuse the stored BSP",
                       len(calls))
    finally:
        bsp_mod.BspTree.from_lumps = classmethod(real)
    print("  stored bsp        OK")


def test_host_floor_spans_under_plate():
    # The plate must not be a hole in its host: with the overlay switched off
    # the base pass alone still has to paint the host floor under it.
    gs.platforms_enabled = False
    try:
        _render(_slab_map(top=88, under=72), px=128.0, py=256.0)
    finally:
        gs.platforms_enabled = True
    # the centre column of the room floor, under the plate's footprint
    rows = [_sig(_px(500, y)) for y in range(420, 700, 20)]
    assert rows and all(r == "g" for r in rows), rows
    print("  host floor under  OK")


# --- 4.2/4.3: the overlay itself -----------------------------------------

def test_plate_is_an_overlay():
    from engine import platform_render, sector_render

    calls = []
    real_draw = platform_render.draw_platforms
    real_up = sector_render._upscale

    def spy_draw(*a, **kw):
        calls.append("draw")
        return real_draw(*a, **kw)

    def spy_up(*a, **kw):
        calls.append("up")
        return real_up(*a, **kw)

    platform_render.draw_platforms = spy_draw
    sector_render._upscale = spy_up
    try:
        _render(_plain_room())
        assert calls == ["up"], calls          # no plates -> no overlay at all
        calls.clear()
        _render(_slab_map())
        # exactly one upscale, and the plate lands in the buffer before it
        assert calls == ["draw", "up"], calls
        calls.clear()
        gs.platforms_enabled = False
        try:
            _render(_slab_map())
        finally:
            gs.platforms_enabled = True
        assert calls == ["up"], calls          # rollback flag
    finally:
        platform_render.draw_platforms = real_draw
        sector_render._upscale = real_up
    print("  overlay order     OK")


def test_plate_covers_wall_behind_it():
    # A plate raised towards eye height paints over the host wall behind it:
    # the overlay runs after the wall pass, never before it.  With the plate
    # lower the two never share a row, so this is the case that tells them
    # apart (``test_plate_is_an_overlay`` only sees the call order).
    sm = _slab_map(top=200, under=184)
    band = range(412, 468, 8)
    _render(sm, px=128.0, py=256.0, player_z=200)
    rows = [_sig(_px(500, y)) for y in band]
    assert all(r == "rg" for r in rows), (rows, [_px(500, y) for y in band])
    # ... and with the overlay off that same band really is the host wall, so
    # the assertion above cannot be satisfied by "the wall was never there".
    gs.platforms_enabled = False
    try:
        _render(sm, px=128.0, py=256.0, player_z=200)
    finally:
        gs.platforms_enabled = True
    alone = [_sig(_px(500, y)) for y in band]
    assert all(r == "r" for r in alone), (alone, [_px(500, y) for y in band])
    print("  plate over wall   OK")


def test_top_and_underside():
    # Camera above the plate: its top is drawn with the slab's floor_tex.
    _render(_slab_map(top=88, under=72), px=128.0, py=256.0, player_z=200)
    assert _sig(_px(500, 660)) == "rg", (_sig(_px(500, 660)), _px(500, 660))
    # Camera below the plate: the underside (ceil_tex) shows, the top does not.
    _render(_slab_map(top=88, under=72), px=128.0, py=256.0, player_z=0)
    under_rows = [_sig(_px(500, y)) for y in (270, 285, 295)]
    assert under_rows and all(r == "gb" for r in under_rows), under_rows
    print("  top / underside   OK")


def test_side_uses_slab_texture():
    # Eye below and outside the plate: its near side face is the slab's own
    # `mid`, never the host's (the host side is deliberately neutral here).
    _render(_slab_map(top=88, under=72), px=128.0, py=256.0, player_z=0)
    rows = [_sig(_px(500, y)) for y in (215, 230, 245)]
    assert rows and all(r == "rb" for r in rows), rows
    print("  side texture      OK")


def _with_pillar(sm):
    """Add a one-sided pillar between the camera and the plate."""
    front = len(sm.sidedefs)
    sm.sidedefs.append(SideDef(sector=0, mid="wall"))
    ring = [(384, 128), (448, 128), (448, 384), (384, 384)]
    ids = []
    for x, y in ring:
        sm.verts.append(Vertex(float(x), float(y)))
        ids.append(len(sm.verts) - 1)
    for i in range(4):
        sm.linedefs.append(LineDef(ids[i], ids[(i + 1) % 4], front=front))
    sm.invalidate()
    assert sm.validate() == [], sm.validate()
    return sm


def test_near_wall_keeps_column():
    # A one-sided pillar between the camera and the plate: the column belongs
    # to the wall, and the plate must not repaint through it — neither its
    # side faces (eye below the plate) ...
    sm = _with_pillar(_slab_map(top=56, under=24,
                                slab_rect=(512, 128, 768, 384)))
    _render(sm, px=128.0, py=256.0, player_z=0)
    # the pillar face fills the centre column; no slab colour may be there
    sigs = {_sig(_px(500, y)) for y in range(250, 460, 10)}
    assert sigs == {"r"}, sigs
    # ... nor its top surface (eye above it).  That is the path that rests on
    # the wall z-buffer alone: a solid wall leaves the column's clip interval
    # open (it is not an irreversible close), so only ``view_buf`` can keep
    # the plate's flats off that column.
    sm = _with_pillar(_slab_map(top=200, under=184,
                                slab_rect=(512, 128, 768, 384)))
    _render(sm, px=128.0, py=256.0, player_z=200)
    top_rows = [_sig(_px(500, y)) for y in range(388, 414, 5)]
    assert all(r == "r" for r in top_rows), top_rows
    print("  near wall wins    OK")


def _dual_slab_map(swap=False, no_near=False):
    """Host with a near plate and a far plate, in either sector order.

    Both tops are below the camera (eye = ``200 + CAM_Z``), so both are seen
    from above.  ``floor_h`` is picked so that the two *screen* bands overlap:
    the near plate's top runs from its near edge (deep down the screen) up to
    its far edge, and the far plate's top starts inside that band.  The near
    plate must win the shared rows — whichever sector index each of them got.
    ``no_near`` drops the near plate, which is the baseline for "the far plate
    really is what is behind that band".
    """
    sm = SectorMap()
    sm.sectors.append(Sector(floor_h=0, ceil_h=256, light=255,
                             floor_tex="host_floor", ceil_tex="host_ceil"))
    near = (384, 128, 512, 384, 88, 72, "top")
    far = (640, 128, 896, 384, 20, 4, "under")
    rects = [near, far] if not swap else [far, near]
    if no_near:
        rects = [far]
    for (_x1, _y1, _x2, _y2, top, under, top_tex) in rects:
        sm.sectors.append(Sector(floor_h=top, ceil_h=under, light=255,
                                 floating=True, floor_tex=top_tex,
                                 ceil_tex="under"))

    def vid(px, py):
        for idx, v in enumerate(sm.verts):
            if v.x == px and v.y == py:
                return idx
        sm.verts.append(Vertex(px, py))
        return len(sm.verts) - 1

    def rect(x1, y1, x2, y2, sector, back=None, mid="wall"):
        front = len(sm.sidedefs)
        sm.sidedefs.append(SideDef(sector=sector, mid=mid))
        back_side = None
        if back is not None:
            back_side = len(sm.sidedefs)
            sm.sidedefs.append(SideDef(sector=back, mid="host_side"))
        corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        for i in range(4):
            ax, ay = corners[i]
            bx, by = corners[(i + 1) % 4]
            if (bx - ax) * (cy - ay) - (by - ay) * (cx - ax) < 0:
                ax, ay, bx, by = bx, by, ax, ay
            sm.linedefs.append(LineDef(
                vid(ax, ay), vid(bx, by), front=front,
                back=back_side if back_side is not None else -1))

    rect(0, 0, 1024, 512, 0)
    for slot, (x1, y1, x2, y2, _t, _u, _tex) in enumerate(rects):
        rect(x1, y1, x2, y2, 1 + slot, back=0,
             mid="side" if _tex == "top" else "host_side")
    sm.things = [Thing(64, 256, "player1")]
    sm.invalidate()
    assert sm.validate() == [], sm.validate()
    return sm


def test_two_plates_depth_order():
    from engine import platform_render

    sm = _dual_slab_map()
    sc = _scene(sm)
    ids = [sid for _d, sid, _l in
           platform_render._visible_slabs(sc, 128.0, 256.0, 0.0, 900)]
    near_id = next(i for i in (1, 2) if sc.floor_h[i] == 88)
    far_id = 3 - near_id
    assert ids == [far_id, near_id], (ids, "plates are painted far to near")
    # Row band shared by both tops (measured: near 648..699, far 566..671).
    band = range(650, 670, 5)
    _render(sm, px=128.0, py=256.0, player_z=200)
    rows = [_sig(_px(500, y)) for y in band]
    assert all(r == "rg" for r in rows), (rows, [_px(500, y) for y in band])
    # ... and that band really is the far plate: without the near plate the
    # same rows show the far plate's own texture, not the host floor.
    _render(_dual_slab_map(no_near=True), px=128.0, py=256.0, player_z=200)
    alone = [_sig(_px(500, y)) for y in band]
    assert all(r == "gb" for r in alone), (alone, [_px(500, y) for y in band])
    # Swapping the sector creation order changes nothing: the order comes from
    # depth, not from the sector index.
    _render(_dual_slab_map(swap=True), px=128.0, py=256.0, player_z=200)
    rows_swapped = [_sig(_px(500, y)) for y in band]
    assert rows_swapped == rows, (rows, rows_swapped)
    print("  two plates order  OK")



# --- 4.4/4.5: camera, projection, sprites --------------------------------

def test_player_z_raises_camera():
    from engine import sector_render

    gs.sec_scene = _scene(_slab_map(top=88, under=72))
    gs.px, gs.py = 128.0, 256.0
    gs.player_z = None
    assert sector_render._current_eye(gs.sec_scene) == 0 + const.CAM_Z
    gs.player_z = 88
    assert sector_render._current_eye(gs.sec_scene) == 88 + const.CAM_Z
    # standing on the plate, the camera is above its top: the plate is below
    # the eye, so its top surface shows where the host floor used to be
    gs.player_z = 88
    _render(_slab_map(top=88, under=72), px=512.0, py=150.0, pa=math.pi / 2,
            player_z=88)
    gs.player_z = None
    print("  player z camera   OK")


def test_floor_z_moves_only_vertical():
    from engine import sector_render
    from engine import viewport

    gs.sec_scene = _scene(_slab_map(top=88, under=72))
    gs.sector_mode = True
    gs.px, gs.py, gs.pa = 128.0, 256.0, 0.0
    gs.player_z = 0
    low = sector_render.project_object(512, 256, 56, 700, 0)
    high = sector_render.project_object(512, 256, 56, 700, 0, floor_z=88)
    assert low and high
    assert low[0] == high[0], (low, high)      # sx unchanged
    assert low[3] == high[3], (low, high)      # perp unchanged
    assert low[1] == high[1], (low, high)      # sh unchanged
    assert high[2] < low[2], (low, high)       # drawn higher on the plate
    # the viewport wrapper propagates it (particles/effects path)
    vp_low = viewport.project_point(512, 256, 56, 700, 0)
    vp_high = viewport.project_point(512, 256, 56, 700, 0, floor_z=88)
    assert vp_high[2] < vp_low[2], (vp_low, vp_high)
    # an actor under the plate stands on the host floor, not on the plate top
    under = sector_render.project_object(512, 256, 56, 700, 0)
    assert under[2] > 350, under               # below the horizon
    print("  floor_z vertical  OK")


def test_sprite_occlusion_rules():
    from engine import platform_render

    occ = [[(100.0, 300.0, 400.0)] for _ in range(250)]
    # fully behind the plate and inside its span -> dropped
    assert platform_render.sprite_occluded(occ, 500, 30, 320, 380, 200.0, 1000)
    # in front of the plate -> drawn
    assert not platform_render.sprite_occluded(occ, 500, 30, 320, 380, 50.0,
                                               1000)
    # taller than the plate (partial cover) -> drawn whole, no clipping
    assert not platform_render.sprite_occluded(occ, 500, 30, 250, 380, 200.0,
                                               1000)
    # one uncovered column is enough to keep the sprite
    occ2 = [list(row) for row in occ]
    occ2[120] = []
    assert not platform_render.sprite_occluded(occ2, 500, 30, 320, 380, 200.0,
                                               1000)
    assert not platform_render.sprite_occluded(None, 500, 30, 320, 380, 200.0,
                                               1000)
    print("  sprite occlusion  OK")


def test_enemy_behind_plate_not_drawn():
    """End to end: the renderer really drops a sprite the plate covers."""
    from engine import rendering, sector_render

    sm = _slab_map(top=48, under=8)
    gs.font = pygame.font.Font(None, 28)
    gs.sm_font = pygame.font.Font(None, 20)
    gs.bots = []
    gs.corpses = []
    gs.items = []
    gs.bul = []
    gs.proj = []
    gs.portals = []
    gs.barrels = []
    gs.players = []
    gs.w_tmr = 0
    gs.fire_flag = False
    gs.viewmap = None
    gs.enemies = []
    _render(sm, px=128.0, py=256.0, player_z=0)
    occ = gs.platform_occluders
    assert occ, "the plate must register occluders"

    enemy = {"x": 700.0, "y": 256.0, "a": 1, "hp": 100, "t": "imp",
             "pa": math.pi, "state": 0}
    proj = sector_render.project_object(700.0, 256.0, 50, 700, 0)
    assert proj is not None
    sx, sh, flr, perp = proj
    assert gs.platform_occluders and rendering is not None
    from engine import platform_render

    assert platform_render.sprite_occluded(occ, int(sx), int(sh) // 2,
                                           int(flr - sh), int(flr), perp,
                                           const.W), (sx, sh, flr, perp)

    drawn = []
    real = rendering.draw_enemy_sprite

    def spy(*a, **kw):
        drawn.append(a[0]['x'])
        return real(*a, **kw)

    rendering.draw_enemy_sprite = spy
    try:
        gs.enemies = [dict(enemy)]
        rendering.render(700, 0)
        assert not drawn, "a sprite fully behind the plate must not be drawn"
        # the same enemy in front of the plate is drawn normally
        gs.enemies = [dict(enemy, x=320.0, y=256.0)]
        rendering.render(700, 0)
        assert drawn == [320.0], drawn
    finally:
        rendering.draw_enemy_sprite = real
        gs.enemies = []
    print("  enemy behind plate OK")


def _bridge_shared_bands(level_change=False):
    """Bands of the face two plates share, seen from one fixed camera."""
    from engine import platform_render
    from smoke_sector_model import make_plate_bridge

    sm = make_plate_bridge()
    if level_change:
        sm.sectors[2].floor_h = 88
        sm.sectors[2].ceil_h = 72
        sm.invalidate()
    assert sm.validate() == [], sm.validate()
    scene = _scene(sm)
    iw = 64
    bands = platform_render._side_bands(
        scene, 2, 64.0, 256.0, 0.0, 72, 750.0, 350.0, iw, 700,
        [0] * iw, [700] * iw, [900.0] * iw, 900)
    shared = {(256.0, 128.0), (256.0, 384.0)}
    return [b for b in bands
            if {(b[6], b[7]), (b[8], b[9])} == shared]


def test_bridge_seam():
    """Two equal plates sharing a side are one slab, not two with a seam."""
    # Same top and underside: the shared face is interior to one continuous
    # slab.  Drawing it would put a vertical wall down the middle of a bridge.
    assert _bridge_shared_bands() == [], "the shared face must not be drawn"
    # A level change is a real riser, and that one has to stay visible.
    assert _bridge_shared_bands(level_change=True), "the step must be drawn"
    print("  bridge seam       OK")


def main():
    _init()
    test_scene_marks_slab()
    test_bsp_not_partitioned_by_slab()
    test_stored_bsp_rebuilt_for_slabs()
    test_host_floor_spans_under_plate()
    test_plate_is_an_overlay()
    test_plate_covers_wall_behind_it()
    test_top_and_underside()
    test_side_uses_slab_texture()
    test_near_wall_keeps_column()
    test_two_plates_depth_order()
    test_bridge_seam()
    test_player_z_raises_camera()
    test_floor_z_moves_only_vertical()
    test_sprite_occlusion_rules()
    test_enemy_behind_plate_not_drawn()
    print("smoke_platform_render: ALL OK")


if __name__ == "__main__":
    main()
