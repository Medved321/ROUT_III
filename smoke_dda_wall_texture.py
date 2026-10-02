"""Smoke: DDA wall texture is locked to world space (change
``dda-wall-texture-world-lock``).

The wall texture column must be a function of the world point on the wall,
not of the player's position and view angle.  The marker texture encodes the
texel index in the *ratio* of two channels вЂ” ``(4t, 255-4t, 0)`` вЂ” because
distance shading multiplies every channel by the same factor, so the ratio
survives shading and the texel can be read back from the rendered frame:

    t = 255 * r / (4 * (r + g))

For each internal framebuffer column the exact world point it looks at is
computed independently (flat east wall at x = 8 tiles), then the same world
points are compared across two nearby viewpoints.  Tolerance is 1 texel
(8-bit read-back noise, measured at ~0.3).
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
from engine import rendering  # noqa: E402
from engine import constants as const  # noqa: E402
from engine import textures as texgen  # noqa: E402
from engine.constants import W, H, SCALE, TILE, TEX_W  # noqa: E402
from client.sprite_factory import get_bot_surface  # noqa: E402

WALL_X = 8 * TILE          # east wall of the test room
MARKER = "dda_marker"
TOL = 1                   # texels


def _init():
    pygame.init()
    gs.sprite_provider = get_bot_surface
    gs.screen = pygame.display.set_mode((W, H))
    gs.font = pygame.font.SysFont("monospace", 34, bold=True)
    gs.sm_font = pygame.font.SysFont("monospace", 18)
    gs.fog_enabled = False
    gs.view_dist = 900
    gs.render_scale = SCALE
    gs.vs_scale = 2
    gs.flat_textured = True
    gs.sector_mode = False
    gs.sector_grid = []
    gs.sectors = []
    gs.bridge_open = set()
    gs.dwall_hp = {}
    gs.explored = []
    gs.particles = []
    gs.decals = []
    gs.weather = []
    gs.enemies = []
    gs.items = []
    gs.bul = []
    gs.proj = []
    gs.corpses = []
    gs.portals = []
    gs.barrels = []
    gs.bots = []
    gs.players = []
    gs.exit_pos = (0, 0)
    gs.show_minimap = False
    gs.show_crosshair = False
    gs.p['guns'] = {0, 1}
    texgen.gen_textures()
    gs.textures[MARKER] = _marker()
    gs.tile_provider = lambda ch: MARKER
    from engine.wallcache import DEFAULT_CACHE
    DEFAULT_CACHE.clear_tex()


def _marker() -> pygame.Surface:
    surf = pygame.Surface((TEX_W, TEX_W))
    for x in range(TEX_W):
        v = 4 * x
        surf.fill((v, 255 - v, 0), (x, 0, 1, TEX_W))
    return surf


def _set_map(chars):
    gs.MAP = [list(r) for r in chars]
    gs.M_SZ = len(chars)


ROOM = [
    "#########",
    "#.......#",
    "#.......#",
    "#.......#",
    "#.......#",
    "#.......#",
    "#.......#",
    "#########",
]


def _sample(px, py, pa):
    """Render one frame and return {world_y_on_wall: texel}."""
    gs.px, gs.py, gs.pa = px, py, pa
    gs.w_tmr = 0
    rendering.render()
    iw, ih = gs.frame.iw, gs.frame.ih
    row = ih // 2                      # inside the wall band (screen centre)
    nc = const.W // SCALE              # rays per frame (local in render())
    fov = rendering.FOV
    buf = gs.frame.buf
    # Rays and framebuffer columns are NOT 1:1 (``_frame_column_span`` spreads
    # nc rays over iw columns), so the inverse map is taken from the engine's
    # own span helper instead of being re-derived.
    ray_of = {}
    for i in range(nc):
        bx, bw = rendering._frame_column_span(i, nc, iw)
        for fx in range(bx, min(iw, bx + bw)):
            ray_of[fx] = i
    out = {}
    for fx in range(iw):
        if fx not in ray_of:
            continue
        base = (row * iw + fx) * 3
        r = buf[base]
        g = buf[base + 1]
        if r + g < 150:
            continue                   # too dark: read-back noise dominates
        # Channel ratio recovers the texel: shading multiplies both channels
        # by one factor, so r/(r+g) = 4t/255.
        t = int(round(255.0 * r / (4.0 * (r + g))))
        if not 0 <= t < TEX_W:
            continue
        i = ray_of[fx]                 # ray this frame column belongs to
        ra = pa - fov / 2.0 + (i / nc) * fov
        c = math.cos(ra)
        if c <= 1e-6:
            continue
        k = (WALL_X - px) / c
        wy = py + math.sin(ra) * k
        if abs(wy - py) > 2.5 * TILE:
            continue                   # grazing column, outside the room
        if not _clear_to_face(px, py, ra):
            continue                   # this ray meets another wall first
        out.setdefault(round(wy, 2), t)
    return out


def _clear_to_face(px, py, ra):
    """True if the ray crosses the east wall face at x = WALL_X first.

    Without this the test would attribute another wall's texel to the east
    face whenever the ray grazes a corner.
    """
    from engine.map_utils import get_t
    c, s = math.cos(ra), math.sin(ra)
    d = 2.0
    while d < 40 * TILE:
        x = px + c * d
        y = py + s * d
        if x >= WALL_X:
            return True
        if get_t(x, y) != '.':
            return False
        d += 2.0
    return False


def _texel_nowrap(v: float) -> int:
    """World coordinate -> texel, exactly as the renderer maps it."""
    return int((v % TILE) / TILE * TEX_W) % TEX_W


def _against_world(label, px, py, pa, tol):
    """Every readable column must match the texel of its world position."""
    _set_map(ROOM)
    s = _sample(px, py, pa)
    assert s, f"{label}: no marker columns read"
    bad = []
    for wy, t in sorted(s.items()):
        d = _delta(t, _texel_nowrap(wy))
        if abs(d) > tol:
            bad.append((wy, t, _texel_nowrap(wy)))
    assert not bad, (
        f"{label}: {len(bad)}/{len(s)} columns off world position by more "
        f"than {tol} texel(s), e.g. {bad[:4]}")
    return len(s)


def _delta(a, b):
    d = (a - b) % TEX_W
    if d > TEX_W // 2:
        d -= TEX_W
    elif d < -TEX_W // 2:
        d += TEX_W
    return d


def test_same_world_point_two_viewpoints():
    """Same world point on the wall, two nearby viewpoints -> same texel.

    Compared at world points both viewpoints actually resolve (within half a
    world unit), so the check does not depend on the two frames sampling
    identical columns.  It is the invariant the player sees: standing still
    and turning the camera must not move the texture along the wall.

    Each view is first required to be world-locked on its own (``_delta`` to
    the analytic texel), otherwise a shared, equally-wrong phase would make
    this test pass for the wrong reason — which is exactly what the legacy
    overshot sample does.
    """
    _set_map(ROOM)
    views = ((1.5 * TILE, 4 * TILE, 0.0),
             (1.5 * TILE + 7, 4 * TILE + 3, 0.05))
    samples = []
    for px, py, pa in views:
        s = _sample(px, py, pa)
        assert s, f"no marker columns read at ({px}, {py}, {pa})"
        worst = max(abs(_delta(t, _texel_nowrap(wy)))
                    for wy, t in s.items())
        assert worst <= TOL, (
            f"view ({px}, {py}, {pa}) is not world-locked by itself: "
            f"{worst} texels off")
        samples.append(s)
    a, b = samples
    checked = 0
    worst = 0
    worst_at = None
    for wy, ta in sorted(a.items()):
        near = min(b, key=lambda w: abs(w - wy))
        if abs(near - wy) > 0.5:
            continue
        d = _delta(ta, b[near])
        checked += 1
        if abs(d) > worst:
            worst, worst_at = abs(d), (wy, ta, b[near])
    assert checked > 40, f"too few comparable columns: {checked}"
    assert worst <= TOL, (
        f"wall texture is not world-locked: {worst} texels apart at y="
        f"{worst_at[0]} (view A texel {worst_at[1]}, view B {worst_at[2]})")
    print(f"  {checked} world points agree    OK (worst {worst} texel)")


def test_phase_at_tile_edge():
    """tex_x == 0 at the tile's lower edge (phase preserved)."""
    assert _texel_nowrap(3 * TILE) == 0
    assert _texel_nowrap(3 * TILE + TILE / 2) == TEX_W // 2
    # continuous across neighbouring tiles of the same wall
    assert _texel_nowrap(4 * TILE - 1) == TEX_W - 1
    assert _texel_nowrap(4 * TILE) == 0
    print("  texture phase at tile edge    OK")


def test_matches_world_geometry():
    """Read-back texel follows the world coordinate, not the column index."""
    n = _against_world("face-on", 1.5 * TILE, 4 * TILE, 0.0, 1)
    print(f"  texel follows world position OK ({n} cols)")


def test_grazing_angles():
    """Skewed rays (screen edges) also stay locked to the wall plane.

    The old overshot sample was worst here: the tangential overshoot grows as
    the ray approaches the wall plane, so this is the column set that decides
    whether the texture is world-locked or swims with the player.
    """
    for label, (px, py, pa) in (
        ("left edge", (1.5 * TILE, 4 * TILE, 0.45)),
        ("right edge", (1.5 * TILE, 4 * TILE, -0.45)),
        ("steep down", (1.5 * TILE, 4 * TILE, 0.40)),
        ("skewed", (3.0 * TILE, 2.2 * TILE, -0.30)),
    ):
        n = _against_world(label, px, py, pa, 1)
        print(f"  {label:<12} world-locked  OK ({n} cols)")


def main():
    _init()
    test_same_world_point_two_viewpoints()
    test_phase_at_tile_edge()
    test_matches_world_geometry()
    test_grazing_angles()
    print("smoke_dda_wall_texture: ALL OK")


if __name__ == "__main__":
    main()
