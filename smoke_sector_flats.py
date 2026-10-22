"""Smoke: flat integration into the framebuffer (total-performance task 3.3).

* The textured-flat fill (``sector_render._np_fill_fb``) is driven by the
  precomputed ``np_flats`` offset maps: perspective comes from the per-row
  depth map (``v_off``), per-column tangent (``u_off``), clipped to each span
  ``clip_top/clip_bottom``.
* Flats are verified pixel-exact against an independent per-pixel reference
  that re-implements the same math (row-depth -> world point -> texel -> light),
  using the same viewport focal the sector renderer computes.
* Walls are not painted over: with two flat palettes the wall band is
  byte-identical while flat rows differ (walls are opaque to the flat pass).
* Steps/windows: a two-sided portal to a pit sector renders the far sector's
  own floor/ceiling planes (depth, light) inside the opening, and changing
  the far sector's light or floor height changes those pixels.
"""
from __future__ import annotations

import math
import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pygame  # noqa: E402

from engine import game_state as gs  # noqa: E402
from engine import sector_render  # noqa: E402
from engine import np_flats  # noqa: E402
from engine.constants import (  # noqa: E402
    FOV, TILE, TEX_W, W, H,
)
from shared.sector_map import (  # noqa: E402
    LineDef, Sector, SectorMap, SideDef, Vertex,
)

FOCAL = 750.0


def _init_display():
    pygame.init()
    gs.screen = pygame.display.set_mode((W, H))


def _scene_env():
    gs.textures = {}
    gs.flats = {}
    gs.sector_mode = True
    gs.render_scale = 4
    gs.vs_scale = 2
    gs.view_dist = 900
    gs.flat_textured = True
    gs.quality_tier = "high"
    gs.vh720 = False
    gs.pz = 0.0


def _vertex(sm, x, y):
    for i, v in enumerate(sm.verts):
        if v.x == x and v.y == y:
            return i
    sm.verts.append(Vertex(float(x), float(y)))
    return len(sm.verts) - 1


def _line(sm, ax, ay, bx, by, front=-1, back=-1, mid="wall"):
    f = -1
    if front >= 0:
        f = len(sm.sidedefs)
        sm.sidedefs.append(SideDef(sector=front, mid=mid))
    b = -1
    if back >= 0:
        b = len(sm.sidedefs)
        sm.sidedefs.append(SideDef(sector=back, mid=mid))
    sm.linedefs.append(LineDef(_vertex(sm, ax, ay), _vertex(sm, bx, by),
                               front=f, back=b))
    return len(sm.linedefs) - 1


def _flat_surface(color=None):
    surf = pygame.Surface((TEX_W, TEX_W))
    for i in range(TEX_W):
        for j in range(TEX_W):
            v = (i + j) % 256
            if color is None:
                rgb = (v, v, v)
            else:
                rgb = tuple(
                    (c + v) % 256 for c in color)
            surf.set_at((j, i), rgb)
    return surf


def _make_plain_room(light=180):
    sm = SectorMap()
    sm.sectors.append(Sector(floor_h=0, ceil_h=128, light=light))
    _line(sm, 0, 0, 512, 0, 0)
    _line(sm, 512, 0, 512, 512, 0)
    _line(sm, 512, 512, 0, 512, 0)
    _line(sm, 0, 512, 0, 0, 0)
    return sm


def _frame(px, py, pa, scene):
    gs.px, gs.py, gs.pa = px, py, pa
    sector_render.render_scene(700, 0)
    return gs.frame.np_view()


def _render_with_b(floor_h, ceil_h, light, px=256.0, py=256.0, pa=0.0):
    """Room A (x 0..512, floor 0 / ceil 128) with a step/window portal to a
    pit room B (x 512..2048, y 0..512) whose floor/ceil/light vary.

    Winding: the ``edge`` helper orients each linedef so its front sector's
    interior point lies on the front side (standard SectorMap requirement).
    """
    sm = SectorMap()
    sm.sectors.append(Sector(floor_h=0, ceil_h=128, light=160))
    sm.sectors.append(Sector(floor_h=floor_h, ceil_h=ceil_h, light=light))
    side_a = len(sm.sidedefs)
    sm.sidedefs.append(SideDef(sector=0))
    side_b = len(sm.sidedefs)
    sm.sidedefs.append(SideDef(sector=1))

    def vid(px: float, py: float) -> int:
        for idx, v in enumerate(sm.verts):
            if v.x == px and v.y == py:
                return idx
        sm.verts.append(Vertex(px, py))
        return len(sm.verts) - 1

    def edge(ax, ay, bx, by, side, ref):
        cross = (bx - ax) * (ref[1] - ay) - (by - ay) * (ref[0] - ax)
        if cross >= 0:
            ax, ay, bx, by = bx, by, ax, ay
        sm.linedefs.append(LineDef(vid(ax, ay), vid(bx, by), front=side))

    ca, cb = (256.0, 256.0), (1792.0, 256.0)
    edge(0, 0, 512, 0, side_a, ca)         # A north
    edge(0, 512, 0, 0, side_a, ca)         # A west
    edge(512, 512, 0, 512, side_a, ca)     # A south
    edge(2048, 0, 512, 0, side_b, cb)      # B north
    edge(2048, 0, 2048, 512, side_b, cb)   # B east
    edge(2048, 512, 512, 512, side_b, cb)  # B south
    edge(512, 0, 512, 512, side_a, ca)     # portal (A side)
    sm.linedefs[-1].back = side_b
    scene = sector_render.SectorScene(sm)
    gs.sec_scene = scene
    return _frame(px, py, pa, scene)


def _view_focal(vh, ih_f):
    return FOCAL * ih_f / max(vh, 1.0)


def _ref_pixel(maps, x, y, px, py, pa, eye_z, z, is_floor, flat, light, view):
    """Independent per-pixel flat reference (separate from ``sample_pixels``).

    Same row-depth/offset math as the offset maps but recomputed here so the
    integration is checked against the math, not against the same function."""
    v_off = maps.v_off_floor if is_floor else maps.v_off_ceil
    dz = eye_z - z if is_floor else z - eye_z
    rd = dz * v_off[y]
    ux = math.tan((x + 0.5) * FOV / maps.iw - FOV / 2)
    cp, sp = math.cos(pa), math.sin(pa)
    wx = px + rd * cp - rd * sp * ux
    wy = py + rd * sp + rd * cp * ux
    tu = int(np.mod(wx / TILE, 1.0) * TEX_W) % TEX_W
    tv = int(np.mod(wy / TILE, 1.0) * TEX_W) % TEX_W
    rgb = flat[tv, tu]
    f = (light / 255.0) * max(0.0, 1.0 - rd / (max(view, 1.0) * 1.2))
    lf = max(0.0, min(1.0, f))
    return np.clip(rgb * lf + 0.5, 0, 255).astype(int)


def _install_flats(scene, color=None):
    """Give both sectors texturable flats and textured walls."""
    gs.flats["floor"] = _flat_surface(color)
    gs.flats["ceiling"] = _flat_surface(color)
    gs.textures["wall"] = _flat_surface()
    gs.textures["step"] = _flat_surface()
    return np_flats.FlatMaps.tex_array(gs.flats["floor"])


def test_flat_rows_match_reference():
    """Textured-flat framebuffer rows equal the independent reference for the
    floor/ceiling of a plain room (single sector, no portals)."""
    _scene_env()
    flat_arr = _install_flats(None)

    sm = SectorMap()
    sm.sectors.append(Sector(floor_h=0, ceil_h=128, light=180))
    _line(sm, 0, 0, 512, 0, 0)
    _line(sm, 512, 0, 512, 512, 0)
    _line(sm, 512, 512, 0, 512, 0)
    _line(sm, 0, 512, 0, 0, 0)
    scene = sector_render.SectorScene(sm)
    gs.sec_scene = scene
    gs.px, gs.py, gs.pa = 256.0, 256.0, 0.0
    sector_render.render_scene(700, 0)

    fb = gs.frame.np_view()
    iw_f, ih_f = fb.shape[1], fb.shape[0]
    maps = np_flats.get_maps(iw_f, ih_f, _view_focal(700, ih_f))
    view = max(1, gs.view_dist)
    eye = sector_render._current_eye(scene)

    checks = [
        # (x, y, is_floor, z)
        (iw_f // 2, ih_f - 8, True, 0.0),     # mid column, deep floor
        (iw_f // 4, ih_f - 12, True, 0.0),    # off-axis floor
        (3 * iw_f // 4, ih_f - 10, True, 0.0),
        (iw_f // 2, 8, False, 128.0),         # ceiling near top
        (iw_f // 4, 12, False, 128.0),
    ]
    for (x, y, is_floor, z) in checks:
        got = fb[y, x]
        exp = _ref_pixel(maps, x, y, gs.px, gs.py, gs.pa, eye, z,
                         is_floor, flat_arr, scene.light[0], view)
        assert np.array_equal(got, exp), (
            f"px ({x},{y}) floor={is_floor}: got {got.tolist()} exp {exp.tolist()}")
    print("  flats match row-depth reference  OK")


def test_walls_not_painted():
    """Looking straight at the far wall of a plain room: the middle band must
    be the wall (differs from the would-be flat colour, non-VOID)."""
    _scene_env()
    _install_flats(None)
    scene = sector_render.SectorScene(_make_plain_room(light=180))
    gs.sec_scene = scene

    px, py, pa = 128.0, 256.0, 0.0
    gs.px, gs.py, gs.pa = px, py, pa
    sector_render.render_scene(700, 0)
    fb = gs.frame.np_view()
    iw_f, ih_f = fb.shape[1], fb.shape[0]
    maps = np_flats.get_maps(iw_f, ih_f, _view_focal(700, ih_f))
    flat_arr = np_flats.FlatMaps.tex_array(gs.flats["floor"])
    view = max(1, gs.view_dist)
    eye = sector_render._current_eye(scene)
    x = iw_f // 2
    # Center column hits the far wall (x=512) at t=384.
    t = 512.0 - px
    F = FOCAL * (700 // gs.vs_scale) / 700
    ih = 700 // gs.vs_scale
    hc = ih / 2.0
    yf = hc + (eye - 0.0) * F / t
    yc = hc + (eye - 128.0) * F / t
    fy0 = int(yc * ih_f / ih)
    fy1 = int(yf * ih_f / ih)
    # Phase C: walls are drawn in render_scene AFTER the flat pass.  To prove
    # the flat pass never touches wall rows we need to observe the flat-only
    # output.  Simplest strong check: render twice with two drastically
    # different floor/ceiling colours; wall rows must be byte-identical while
    # flat rows differ.  We verify that a sample of frames shows the wall
    # band constant under both palettes.
    _scene_env()
    _install_flats(None)
    scene = sector_render.SectorScene(_make_plain_room(light=180))
    gs.sec_scene = scene

    def render_with(color, tag):
        gs.flats[tag + "_floor"] = _flat_surface(color)
        gs.flats[tag + "_ceil"] = _flat_surface(color)
        scene.floor_tex[0] = tag + "_floor"
        scene.ceil_tex[0] = tag + "_ceil"
        gs.px, gs.py, gs.pa = 128.0, 256.0, 0.0
        sector_render.render_scene(700, 0)
        return np.array(gs.frame.np_view())

    grey = render_with(None, "grey")       # flat shades = (v,v,v)
    red = render_with((200, 0, 0), "red")  # flat shades = ((200+v)%256, v, v)

    iw_f, ih_f = grey.shape[1], grey.shape[0]
    x = iw_f // 2
    eye = 41.0
    F = FOCAL * (700 // gs.vs_scale) / 700
    ih = 700 // gs.vs_scale
    hc = ih / 2.0
    t = 512.0 - 128.0  # far wall at x=512 from camera x=128
    yf = hc + (eye - 0.0) * F / t
    yc = hc + (eye - 128.0) * F / t
    fy0 = int(yc * ih_f / ih)
    fy1 = int(yf * ih_f / ih)
    from engine.sector_render import VOID

    # The wall band is between the projected floor and ceiling edges; the flat
    # pass never colours it, so both palettes must leave it identical.
    wall_rows = [r for r in range(max(fy0 + 1, 0), min(fy1, ih_f))]
    assert len(wall_rows) > 2, f"wall band too small ({fy0}-{fy1})"
    for row in wall_rows:
        got = tuple(grey[row, x])
        assert tuple(got) != VOID, f"wall row {row} is VOID"
        assert np.array_equal(grey[row, x], red[row, x]), (
            f"wall row {row} depends on flat colour: "
            f"{tuple(grey[row, x])} vs {tuple(red[row, x])}")

    # Flat rows (clearly inside the floor and ceiling spans) must differ under
    # the two palettes — otherwise the check above could pass vacuously.
    def differs(y):
        return not np.array_equal(grey[y, x], red[y, x])

    assert any(differs(y) for y in range(fy1 + 1, ih_f)), "floor stays flat"
    assert any(differs(y) for y in range(0, max(fy0, 0))), "ceiling stays flat"
    print("  walls not painted by flats       OK",
          "| band", fy0, fy1, "| ih", ih, "ih_f", ih_f, "fy0", fy0, "fy1", fy1)


def test_step_window():
    """Two-sided portal into a pit sector B (floor −64, ceiling 160, bright):
    the opening must draw B's own ceiling and floor planes at B's depths and
    light.  Changing B's light or floor height must change those pixels, i.e.
    the flats in the window/step really come from the far sector B."""
    _scene_env()
    _install_flats(None)

    base = np.array(_render_with_b(-64, 160, 210))
    fb = base

    # Same geometry, different light: the window/step must change.
    light_lo = np.array(_render_with_b(-64, 160, 90))
    assert not np.array_equal(base, light_lo), (
        "B light must affect the window framebuffer")
    # Same light, different floor height: the step must change.
    step_hi = np.array(_render_with_b(0, 160, 210))
    assert not np.array_equal(base, step_hi), (
        "B floor height must affect the step framebuffer")

    iw_f, ih_f = gs.frame.iw, gs.frame.ih
    maps = np_flats.get_maps(iw_f, ih_f, _view_focal(700, ih_f))
    flat_floor = np_flats.FlatMaps.tex_array(gs.flats["floor"])
    flat_ceil = np_flats.FlatMaps.tex_array(gs.flats["ceiling"])
    view = max(1, gs.view_dist)
    x = iw_f // 2
    eye = 41.0  # room A floor 0 + CAM_Z
    px, py, pa = 256.0, 256.0, 0.0

    def matches(y, z, is_floor, arr, light):
        return np.array_equal(
            fb[y, x],
            _ref_pixel(maps, x, y, px, py, pa, eye, z, is_floor, arr,
                       light, view))

    # B's ceiling plane (z=160) must be visible through the opening: some rows
    # above the horizon must match B's own ceiling flat (light 210).
    ceil_rows = [y for y in range(ih_f)
                 if matches(y, 160.0, False, flat_ceil, 210)]
    assert len(ceil_rows) >= 4, f"B ceiling rows missing: {ceil_rows}"
    # B's floor plane (z=-64) must be visible below the horizon.
    floor_rows = [y for y in range(ih_f)
                  if matches(y, -64.0, True, flat_floor, 210)]
    assert len(floor_rows) >= 4, f"B floor rows missing: {floor_rows}"
    print("  step/window sector flats        OK "
          f"(ceil rows {ceil_rows[0]}..{ceil_rows[-1]}, "
          f"floor rows {floor_rows[0]}..{floor_rows[-1]})")


def main():
    _init_display()
    test_flat_rows_match_reference()
    test_walls_not_painted()
    test_step_window()
    print("smoke_sector_flats: ALL OK")


if __name__ == "__main__":
    main()