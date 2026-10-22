"""Smoke: sector renderer (BSP path, tasks 4.1-4.4).

* SectorScene: two-sector portal map (raised platform) builds cleanly.
* render_scene: BSP near->far frames in solid and textured-flat modes at
  several quality steps — per-column z-buffer sane (closed columns carry the
  closing wall's view-axis depth, open ones keep ``view``), walls actually
  drawn (non-void), sprites & items project, adaptive ladder fields intact.
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


def _init_display():
    pygame.init()
    gs.screen = pygame.display.set_mode((1000, 700))


def _make_scene():
    gs.textures = {}
    gs.flats = {}


def _vertex(sm, x, y):
    for i, v in enumerate(sm.verts):
        if v.x == x and v.y == y:
            return i
    sm.verts.append(Vertex(float(x), float(y)))
    return len(sm.verts) - 1


def _line(sm, ax, ay, bx, by, front=-1, back=-1):
    f = -1
    if front >= 0:
        f = len(sm.sidedefs)
        sm.sidedefs.append(SideDef(sector=front))
    b = -1
    if back >= 0:
        b = len(sm.sidedefs)
        sm.sidedefs.append(SideDef(sector=back))
    sm.linedefs.append(LineDef(_vertex(sm, ax, ay), _vertex(sm, bx, by),
                               front=f, back=b))
    return len(sm.linedefs) - 1


def _make_room(floor_h=0, ceil_h=128, light=160):
    # Room A: (0,0)-(512,512); raised platform sector 1 inside.
    sm = SectorMap()
    sm.sectors.append(Sector(floor_h=floor_h, ceil_h=ceil_h, light=light))
    sm.sectors.append(Sector(floor_h=floor_h + 48, ceil_h=ceil_h, light=200))
    _line(sm, 0, 0, 512, 0, 0)
    _line(sm, 512, 0, 512, 512, 0)
    _line(sm, 512, 512, 0, 512, 0)
    _line(sm, 0, 512, 0, 0, 0)
    # Platform (two-sided portal box) inside room A.
    _line(sm, 160, 160, 352, 160, 0, 1)
    _line(sm, 352, 160, 352, 256, 0, 1)
    _line(sm, 352, 256, 160, 256, 0, 1)
    _line(sm, 160, 256, 160, 160, 0, 1)
    sm.things.append(Thing(256, 64))
    # One-sided pillar inside the room (solid obstacle).
    _line(sm, 96, 224, 128, 224, 0)
    _line(sm, 128, 224, 128, 256, 0)
    _line(sm, 128, 256, 96, 256, 0)
    _line(sm, 96, 256, 96, 224, 0)
    assert not sm.validate(), sm.validate()
    return sm


def scene_from(sm):
    from engine.sector_render import SectorScene

    return SectorScene(sm)


def test_scene_fields():
    sm = _make_room()
    sc = scene_from(sm)
    assert sc.n_lines == len(sm.linedefs)
    assert sc.back_sec == [0] * 0 or all(x >= -1 for x in sc.back_sec)
    # platform lines are two-sided
    assert sc.two_sided[4] and sc.two_sided[7]
    assert sc.back_sec[4] == 1
    assert sc.floor_h[1] - sc.floor_h[0] == 48
    print("  scene fields     OK")


def _frame(flat_textured, render_scale, vs_scale, px, py, pa):
    from engine import sector_render

    gs.sec_scene = scene_from(_make_room())
    gs.sector_mode = True
    gs.render_scale = render_scale
    gs.vs_scale = vs_scale
    gs.view_dist = 900
    gs.flat_textured = flat_textured
    gs.px, gs.py, gs.pa = px, py, pa
    gs.pz = 0.0
    gs.vh720 = False
    return sector_render.render_scene(700, 0), sector_render


def test_render_scene():
    z_b, sr = _frame(True, 4, 2, 256, 224, 0.0)
    iw = 1000 // 4
    assert len(z_b) == iw, len(z_b)
    assert all(0 < v <= 900 for v in z_b), z_b
    mid = z_b[iw // 2]
    assert mid < 900 and mid > 10, f"center ray should hit a wall, z={mid}"
    # Task 4.4: the z-buffer column that a solid wall closes carries the
    # wall's view-axis depth (player at x=256, facing +x → east wall x=512).
    assert abs(mid - 256.0) < 1.0, mid
    # Solid-flat path at worst quality - no crash, sane buffer.
    z_b2, _ = _frame(False, 8, 6, 256, 224, 0.0)
    assert len(z_b2) == 1000 // 8
    assert all(0 < v <= 900 for v in z_b2)
    # Another look direction (towards platform edge).
    z_b3, _ = _frame(True, 2, 1, 256, 224, 0.4)
    assert len(z_b3) == 1000 // 2
    print("  render scene     OK")


def test_open_columns_stay_view():
    # Task 4.4 z-buffer fallback: columns whose closest wall sits beyond the
    # view distance must keep z = view (fog/particle fallback), while columns
    # that close on a real wall carry that wall's view-axis depth.  One big
    # room; player near the north-west corner looking east puts the up-facing
    # frustum edge on the near wall (closes) and the rest into the void
    # (stays open).
    sm = SectorMap()
    sm.sectors.append(Sector(floor_h=0, ceil_h=128, light=160))
    _line(sm, 0, 0, 4096, 0, 0)
    _line(sm, 4096, 0, 4096, 4096, 0)
    _line(sm, 4096, 4096, 0, 4096, 0)
    _line(sm, 0, 4096, 0, 0, 0)
    sm.things.append(Thing(256, 256))
    assert not sm.validate(), sm.validate()
    from engine.sector_render import render_scene

    gs.sec_scene = scene_from(sm)
    gs.sector_mode = True
    gs.render_scale = 4
    gs.vs_scale = 2
    gs.view_dist = 900
    gs.flat_textured = True
    gs.px, gs.py, gs.pa = 384, 384, 0.0
    gs.pz = 0.0
    gs.vh720 = False
    z_b = render_scene(700, 0)
    iw = 1000 // 4
    assert len(z_b) == iw
    assert all(0 < v <= 900 for v in z_b), z_b
    assert z_b[iw // 2] == 900, ("center faces open room", z_b[iw // 2])
    closed = [v for v in z_b if v < 900]
    assert closed, "expected some columns to close on a wall"
    assert min(closed) > 100 and min(closed) < 900, ("depth sane", min(closed))
    reopen = [v for v in z_b if v == 900]
    assert reopen, "expected some still-open columns to keep z = view"
    print("  open columns stay view  OK")


def test_walls_drawn():
    # Look straight at the pillar: a solid wall must occupy the middle band.
    _, _ = _frame(True, 4, 2, 256, 240, 0.0)
    mid_row = 350
    colors = {gs.screen.get_at((x, mid_row))[:3] for x in range(450, 550)}
    from engine.sector_render import VOID

    core = {c for c in colors if c != VOID}
    assert core, "no non-void pixels in middle band (wall missing)"
    print("  walls drawn      OK")


def test_project_object():
    from engine import sector_render

    gs.sec_scene = scene_from(_make_room())
    gs.px, gs.py, gs.pa = 256, 64, 0.0
    proj = sector_render.project_object(320, 64, 56, 700, 0)
    assert proj is not None
    sx, sh, ground_y, perp = proj
    assert 0 <= sx < 1000, sx
    assert sh > 0 and perp > 0
    assert ground_y > 350, ground_y  # below the horizon
    assert sector_render.project_object(256 - 100, 64, 56, 700, 0) is None  # behind
    assert sector_render.project_object(256, 64 + 2000, 56, 700, 0) is None  # far flank
    print("  project_object   OK")


def test_sprite_occlusion():
    # Task 4.4 verification: a sprite/enemy is drawn only when the zbuffer
    # column in front of it (view-axis wall depth) is FURTHER than the
    # sprite's perpendicular distance — mirror of rendering.py's
    # ``z_b[idx] > occ`` predicate (idx = sx//SCALE, SCALE = render_scale).
    # Player inside the raised platform at (256,224) facing +x: the east room
    # wall closes the center column at view-axis depth 256.
    from engine import sector_render

    z_b, _ = _frame(True, 4, 2, 256, 224, 0.0)
    for wx, wy in ((300, 224), (600, 224)):
        proj = sector_render.project_object(wx, wy, 56, 700, 0)
        assert proj is not None
        sx, sh, sp_flr, perp = proj
        idx = max(0, min(1000 // 4 - 1, sx // 4))
        visible = z_b[idx] > perp
        if wy == 224 and wx == 300:
            assert visible, ("enemy before the wall must draw", sx, perp)
            assert sp_flr > 175, ("enemy stands on the step floor", sp_flr)
        else:
            assert not visible, ("enemy behind the wall must be hidden",
                                 sx, perp, z_b[idx])
    # The step floor under a platform enemy projects below the horizon.
    proj = sector_render.project_object(300, 224, 56, 700, 0)
    sx, sh, sp_flr, perp = proj
    assert sh > 0 and sp_flr > 175, (sp_flr, sh)
    print("  sprite occlusion OK")


def test_tier_frames():
    # Switching the graphic tier changes the framebuffer resolution and keeps
    # the sector scene intact (walls still drawn at each tier).
    from engine import sector_render

    from engine.quality import resolution

    for tier in ("high", "medium", "fast"):
        iw, ih = resolution(tier)
        gs.quality_tier = tier
        gs.flat_textured = tier == "high"
        _, _ = _frame(gs.flat_textured, 4, 2, 256, 240, 0.0)
        assert gs.frame.size == (iw, ih), (tier, gs.frame.size)
        assert len(sector_render.render_scene(700, 0)) == 250
    gs.quality_tier = "high"
    print("  tier switch       OK")


def test_adaptive_ladder():
    from client.performance import AdaptiveQuality

    q = AdaptiveQuality(4, base_vs=2, target_fps=60)
    assert (q.scale, q.vs_scale, q.flat_textured) == (4, 2, True)
    t0 = 0.0
    # Overload for several intervals: ladder goes scale->vs->flats.
    q.sample(55.0, t0)
    t0 += 1.001
    for _ in range(12):
        q.sample(55.0, t0)
        t0 += 1.001
    assert q.scale == 8, q.scale
    assert q.vs_scale == 6, q.vs_scale
    assert not q.flat_textured
    # Headroom restores in reverse (EMA needs a few samples to drop below
    # the headroom threshold, then one step per interval).
    t0 += 1.001
    for _ in range(30):
        q.sample(5.0, t0)
        t0 += 1.001
    assert q.flat_textured and q.vs_scale == 2 and q.scale == 4, (
        q.flat_textured, q.vs_scale, q.scale)
    assert q.effects_factor() == 1.0
    print("  adaptive ladder  OK")


def test_strip_cache_two_frames():
    # Task 2.2: two adjacent frames with identical geometry use the cached
    # distance-bucket strips — the hit counter grows and memory stays under
    # the budget (LRU).
    from engine.wallcache import DEFAULT_CACHE

    gs.sec_scene = scene_from(_make_room())
    gs.sector_mode = True
    gs.render_scale = 4
    gs.vs_scale = 2
    gs.view_dist = 900
    gs.flat_textured = True
    gs.px, gs.py, gs.pa = 256, 224, 0.0
    gs.pz = 0.0
    gs.vh720 = False
    from engine import sector_render

    DEFAULT_CACHE.clear_tex()
    DEFAULT_CACHE.hits = DEFAULT_CACHE.misses = 0
    sector_render.render_scene(700, 0)
    h1, m1 = DEFAULT_CACHE.hits, DEFAULT_CACHE.misses
    sector_render.render_scene(700, 0)          # identical geometry
    assert DEFAULT_CACHE.hits > h1, (h1, DEFAULT_CACHE.hits)
    assert DEFAULT_CACHE.misses == m1, (m1, DEFAULT_CACHE.misses)
    stats = DEFAULT_CACHE.stats()
    assert stats["strips"] > 0
    assert stats["bytes"] <= stats["max_bytes"], stats
    print("  strip cache 2 frames  OK")


def _two_rooms(solid=False, floor_b=0, lower="-"):
    """Room A x 0..512 and room B x 512..1024 (y 0..512) joined at x=512.

    Room walls carry distinct mid textures so a test can tell whether the band
    came from the near room, the far room, or the shared line.
    """
    sm = SectorMap()
    sm.sectors.append(Sector(floor_h=0, ceil_h=128, light=160))
    sm.sectors.append(Sector(floor_h=floor_b, ceil_h=128, light=90))
    _line(sm, 0, 0, 512, 0, 0)
    _line(sm, 0, 512, 0, 0, 0)
    _line(sm, 512, 512, 0, 512, 0)
    _line(sm, 512, 0, 1024, 0, 1)
    _line(sm, 1024, 0, 1024, 512, 1)
    _line(sm, 1024, 512, 512, 512, 1)
    for li, ld in enumerate(sm.linedefs):
        sm.sidedefs[ld.front].mid = "near_wall" if li < 3 else "far_wall"
    wall = _line(sm, 512, 0, 512, 512, 0, 1)
    sm.linedefs[wall].solid = solid
    # The step lip carries the CAMERA-facing face's lower texture; which face
    # that is depends on the winding the sector-polygon closure forces on the
    # shared line, so set the assigned lower/fallback on BOTH sidedefs and
    # assert the lip uses it (not the neutral fallback) no matter the side.
    sm.sidedefs[sm.linedefs[wall].front].mid = "wall"
    sm.sidedefs[sm.linedefs[wall].front].lower = lower
    sm.sidedefs[sm.linedefs[wall].back].lower = lower
    sm.things.append(Thing(256, 256))
    assert not sm.validate(), sm.validate()
    return sm, wall


def _render(sm, px=256, py=256, pa=0.0, record_bands=False):
    """Render one frame; optionally capture the band list _draw_wall_bands got."""
    from engine import sector_render

    gs.sec_scene = scene_from(sm)
    gs.sector_mode = True
    gs.render_scale = 4
    gs.vs_scale = 2
    gs.view_dist = 1400
    gs.flat_textured = True
    gs.px, gs.py, gs.pa = px, py, pa
    gs.pz = 0.0
    gs.vh720 = False
    captured = []
    real = sector_render._draw_wall_bands
    if record_bands:
        def spy(frame, bands, scene, view, iw, ih, iw_f, ih_f, fwidths):
            captured.extend(bands)
            return real(frame, bands, scene, view, iw, ih, iw_f, ih_f,
                        fwidths)
        sector_render._draw_wall_bands = spy
    try:
        z_b = sector_render.render_scene(700, 0)
    finally:
        sector_render._draw_wall_bands = real
    return z_b, captured


def test_solid_wall_and_step():
    """solid folds into the wall path; a plain step stays an open portal."""
    iw = 1000 // 4
    mid = iw // 2

    # a two-sided line of equal heights is a portal: the far room is visible
    sm, wall = _two_rooms()
    z_b, bands = _render(sm, record_bands=True)
    assert z_b[mid] > 512, f"portal must show the far wall, z={z_b[mid]}"
    assert any(b[3] == "far_wall" for b in bands if b[0] == mid), \
        "portal must draw the far room behind it"

    # the same line marked solid renders as a wall: it stops the z-buffer at
    # its own depth and nothing behind it reaches those columns
    sm, wall = _two_rooms(solid=True)
    sc = scene_from(sm)
    assert sc.solid[wall] is True
    assert sc.two_sided[wall] is False, "solid must leave the portal path"
    z_b, bands = _render(sm, record_bands=True)
    assert abs(z_b[mid] - 256.0) < 1.0, \
        f"z-buffer must stop at the solid wall: {z_b[mid]}"
    at_mid = [b[3] for b in bands if b[0] == mid]
    assert at_mid, "the solid wall must contribute bands"
    # Only the solid wall's OWN depth may visibly show: the back-face cull
    # (sector-step-down-pit) makes the twin facing the camera the sole owner
    # of the column, so any band deeper than the wall (e.g. the far room's
    # far wall, if an intra-leaf order drew it first) is overpainted and must
    # not leak into what the column shows.
    visible = {b[3] for b in bands if b[0] == mid and b[10] <= 256.0 + 1.0}
    assert visible and all(t == "wall" for t in visible), \
        f"only the front mid texture may visibly show, got {visible}"

    # a floor step still renders as an open step, and the lower lip carries the
    # camera-facing face's lower texture (assigned on both faces above)
    sm, wall = _two_rooms(floor_b=16, lower="step_low")
    sc = scene_from(sm)
    assert sc.two_sided[wall] is True, "a step stays a portal"
    z_b, bands = _render(sm, record_bands=True)
    assert z_b[mid] > 512, f"step must show the far wall, z={z_b[mid]}"
    assert any(b[3] == "step_low" for b in bands), \
        "step lip must use the assigned lower texture"

    # without an assigned texture the lip falls back to the neutral texture
    sm, wall = _two_rooms(floor_b=16)
    _z, bands = _render(sm, record_bands=True)
    assert any(b[3] == "-" for b in bands), \
        "unassigned step lip must use the neutral fallback"

    # a step taller than STEP_HEIGHT still renders as a step: the height limit
    # is a movement rule, not a rendering one
    sm, wall = _two_rooms(floor_b=48)
    assert scene_from(sm).two_sided[wall] is True
    z_b, _bands = _render(sm)
    assert z_b[mid] > 512
    print("  solid wall + step  OK")


def _grid_map(nx=3, ny=3, cell=512, pillars=((1, 1), (2, 0), (0, 2))):
    """Grid of equal-height cells — one sector each — portals between them.

    Several subsectors per map (task 3.1): every cell is its own sector, the
    partitions are two-sided lines with equal floor/ceiling heights (so they
    pinch no lips and emit no bands), and the pillars are one-sided islands.
    Front-side convention as in ``_make_room``: the front sector lies on the
    LEFT of ``v1 -> v2``.
    """
    sm = SectorMap()
    for _ in range(nx * ny):
        sm.sectors.append(Sector(floor_h=0, ceil_h=128, light=160))

    def sec(i, j):
        return j * nx + i

    w, h = nx * cell, ny * cell
    # outer one-sided boundary, split at every grid vertex
    for i in range(nx):
        _line(sm, i * cell, 0, (i + 1) * cell, 0, sec(i, 0))
        _line(sm, (i + 1) * cell, h, i * cell, h, sec(i, ny - 1))
    for j in range(ny):
        _line(sm, 0, (j + 1) * cell, 0, j * cell, sec(0, j))
        _line(sm, w, j * cell, w, (j + 1) * cell, sec(nx - 1, j))
    # internal two-sided portals, split at every grid vertex
    for i in range(1, nx):
        for j in range(ny):
            _line(sm, i * cell, j * cell, i * cell, (j + 1) * cell,
                  sec(i - 1, j), sec(i, j))
    for j in range(1, ny):
        for i in range(nx):
            _line(sm, (i + 1) * cell, j * cell, i * cell, j * cell,
                  sec(i, j - 1), sec(i, j))
    # pillars (one-sided islands): CW loop keeps the front side — and with it
    # the surrounding cell — on the left of every edge
    for ci, cj in pillars:
        x0, y0 = ci * cell + 64, cj * cell + 64
        s = sec(ci, cj)
        _line(sm, x0, y0, x0, y0 + 64, s)
        _line(sm, x0, y0 + 64, x0 + 64, y0 + 64, s)
        _line(sm, x0 + 64, y0 + 64, x0 + 64, y0, s)
        _line(sm, x0 + 64, y0, x0, y0, s)
    sm.things.append(Thing(cell // 2, cell // 2))
    errs = sm.validate()
    assert not errs, errs
    return sm


def _solid_segments(sm):
    """World segments a ray is stopped by: one-sided or solid-flagged."""
    segs = []
    for ld in sm.linedefs:
        if not (ld.back < 0 or ld.solid):
            continue
        a, b = sm.verts[ld.v1], sm.verts[ld.v2]
        segs.append((a.x, a.y, b.x, b.y))
    return segs


def _ref_solid_depth(segs, px, py, pa, iw):
    """Per internal column: view-axis depth of the nearest solid the ray hits.

    Independent of the renderer (raw linedefs, no BSP, no clip state) — the
    эталон for the differential check of task 3.1.
    """
    ca, sa = math.cos(pa), math.sin(pa)
    tan_h = math.tan(const.FOV * 0.5)
    refs = [math.inf] * iw
    for col in range(iw):
        k = (2.0 * ((col + 0.5) / iw) - 1.0) * tan_h
        rx, ry = ca - k * sa, sa + k * ca
        best = math.inf
        for (ax, ay, bx, by) in segs:
            dx, dy = bx - ax, by - ay
            den = rx * dy - ry * dx
            if abs(den) < 1e-12:
                continue
            w = ((ax - px) * dy - (ay - py) * dx) / den
            if w <= 1e-9:
                continue
            s = ((ax - px) * ry - (ay - py) * rx) / den
            if s < 0.0 or s > 1.0:
                continue
            t = w * (rx * ca + ry * sa)
            if 0.0 < t < best:
                best = t
        refs[col] = best
    return refs


def test_near_wall_ordering():
    """Order-independent near-solid occlusion (task 3.1).

    Sweep angles and positions over the grid map, capture every band the
    renderer hands to ``_draw_wall_bands`` and compare the MINIMUM drawn
    depth of each column with the analytic nearest solid along that column's
    ray.  A farther solid wall processed before a nearer one used to close
    the column and evict it, which shows up exactly here: everything drawn
    in the column is deeper than the true nearest wall.
    """
    sm = _grid_map()
    segs = _solid_segments(sm)
    iw = 1000 // 4
    positions = [(256, 256), (768, 256), (1280, 256),
                 (256, 768), (768, 768), (1280, 768),
                 (256, 1280), (768, 1280), (1280, 1280),
                 (640, 768), (860, 700), (768, 400)]
    angles = [0.0, 0.6, 1.2, math.pi / 2, 2.1, 3.0, 3.6, 4.2,
              3 * math.pi / 2, 5.3, -0.5, -1.3]
    cols_total = 0
    for (px, py) in positions:
        for pa in angles:
            _z, bands = _render(sm, px, py, pa, record_bands=True)
            refs = _ref_solid_depth(segs, px, py, pa, iw)
            by_col = {}
            for b in bands:
                by_col.setdefault(b[0], []).append(b[10])
            for col in range(iw):
                ref = refs[col]
                if not math.isfinite(ref):
                    continue
                cols_total += 1
                ts = by_col.get(col, [])
                eps = max(1.0, 1e-3 * ref)
                assert ts and min(ts) <= ref + eps, \
                    (px, py, pa, col, round(ref, 1),
                     [round(t, 1) for t in ts[:6]])
    assert cols_total > 10000, cols_total
    print("  near wall ordering OK")


def test_on_vertex_observer():
    """Observer exactly level with a vertex (task 3.2).

    ``pa = 0.0`` gives ``ca = 1.0, sa = 0.0`` exactly, so a seg endpoint with
    ``x == px`` yields ``t1 == 0.0``; the near-plane clip used to test
    ``t1 < 0.0`` and the frame died with ``ZeroDivisionError`` a few lines
    below.  Integer poses, integer angles — no exception and a real frame.
    """
    sm = _grid_map()
    for px, py, pa in ((640, 768, 0.0),        # pillar vertex x = 640
                       (1152, 768, 0.0),       # pillar vertex x = 1152
                       (640, 768, math.pi),
                       (768, 1152, math.pi / 2)):
        z_b, _ = _render(sm, px, py, pa)
        assert z_b and all(0.0 < v <= 1400.0 for v in z_b), (px, py, pa, z_b)
        assert min(z_b) < 1400.0, \
            f"pose ({px},{py},{pa}) drew no wall at all"
    print("  on-vertex observer  OK")


def test_coop_player_render():
    """A visible co-op player must render a full frame without raising.

    Regression: project_object() returns sh/ground_y as floats, and the
    humanoid block derived every line width from them (``max(2, br2 // 3)``).
    pygame.draw.line rejects a float width with TypeError, so any second
    player on screen crashed the client -- which is exactly what a LAN party
    hits. circle/rect tolerate floats, so the crash only showed up on the
    draw.line calls.
    """
    from engine import rendering
    from engine import sector_render

    gs.font = pygame.font.Font(None, 28)
    gs.sm_font = pygame.font.Font(None, 20)
    gs.p = dict(gs.p)
    gs.sec_scene = scene_from(_make_room())
    gs.sector_mode = True
    gs.render_scale = 4
    gs.vs_scale = 2
    gs.view_dist = 900
    gs.flat_textured = False
    gs.px, gs.py, gs.pa = 256, 224, 0.0
    gs.pz = 0.0
    gs.vh720 = False
    gs.w_tmr = 0
    gs.fire_flag = False
    gs.bots = []
    gs.enemies = []
    gs.corpses = []
    gs.impacts = []
    gs.projectiles = []
    gs.viewmap = None

    # the second player must land on a z-buffer column that is not occluded
    z_b = sector_render.render_scene(700, 0)
    other = (300, 224)
    proj = sector_render.project_object(other[0], other[1], 56, 700, 0)
    assert proj is not None
    idx = max(0, min(1000 // 4 - 1, proj[0] // 4))
    assert z_b[idx] > proj[3], (z_b[idx], proj[3])

    gs.players = [{"px": float(other[0]), "py": float(other[1]), "pa": 3.0,
                   "wpn": 1, "col": (60, 140, 210)}]
    rendering.render(700, 0)
    assert max(gs.frame.buf) > 0, "frame buffer empty (VOID)"

    # every pixel-space value the humanoid block derives must be an int:
    # pygame.draw.line raises on a float width
    for ox, oy in ((other[0], other[1]), (288, 240), (320, 200)):
        sx2, sh2, plr_flr, _occ = sector_render.project_object(ox, oy, 56, 700, 0)
        if sx2 is None:
            continue
        sh2 = int(min(sh2, 700 * 2))
        for value in (sh2 // 4, sh2 // 2, sh2 // 5, max(2, sh2 // 4 // 3),
                      max(2, sh2 // 4 // 2)):
            assert isinstance(value, int), (ox, oy, value, type(value))
    print("  co-op player render OK")


def main():
    _init_display()
    _make_scene()
    test_scene_fields()
    test_render_scene()
    test_open_columns_stay_view()
    test_walls_drawn()
    test_project_object()
    test_sprite_occlusion()
    test_coop_player_render()
    test_tier_frames()
    test_solid_wall_and_step()
    test_near_wall_ordering()
    test_on_vertex_observer()
    test_strip_cache_two_frames()
    test_adaptive_ladder()
    print("smoke_sector_render: ALL OK")


if __name__ == "__main__":
    main()