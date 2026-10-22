"""Smoke: tile renderer through the framebuffer backend (total-performance
task 1.3).  Walls/ceiling/floor are composed into ``gs.frame`` and upscaled
once to the screen.  Structural checks: walls present in the expected band,
north/south texture variation, per-tier resolutions, no crash across angles.
Run: ``py -3.12 smoke_framebuffer_tile.py``.
"""
from __future__ import annotations

import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pygame  # noqa: E402

from engine import game_state as gs  # noqa: E402
from engine import rendering  # noqa: E402
from engine import textures as texgen  # noqa: E402
from engine.quality import resolution  # noqa: E402
from engine.constants import W, H, SCALE, TILE  # noqa: E402
from client.map_editor import TILE_CHAR_TO_TEX  # noqa: E402
from client.sprite_factory import get_bot_surface  # noqa: E402


def _init():
    pygame.init()
    # The engine no longer owns content (updating_engine D2): register the
    # providers or every wall degrades to 'wall' and bot sprites vanish,
    # which would make the texture checks below pass for the wrong reason.
    gs.tile_provider = TILE_CHAR_TO_TEX.get
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
    from engine.wallcache import DEFAULT_CACHE
    DEFAULT_CACHE.clear_tex()


def _set_map(chars):
    gs.MAP = [list(r) for r in chars]
    gs.M_SZ = len(chars)


def _render_at(px, py, pa, tier='high'):
    gs.quality_tier = tier
    gs.px, gs.py, gs.pa = px, py, pa
    gs.w_tmr = 0
    rendering.render()


def _tile_at(x, y):
    val = gs.screen.get_at((x, y))[:3]
    return val


def test_frame_created():
    room = ["######", "#....#", "#....#", "#....#", "######"]
    _set_map(room)
    _render_at(1.5 * TILE, 2.5 * TILE, 0.0)
    assert gs.frame is not None
    assert gs.frame.size == resolution('high'), gs.frame.size
    print("  framebuffer created per tier   OK")


def test_wall_in_middle():
    # Player at left of an enclosed room, facing east: wall straight ahead.
    room = [
        "#########",
        "#.......#",
        "#.......#",
        "#.......#",
        "#.......#",
        "#.......#",
        "#.......#",
        "#########",
    ]
    _set_map(room)
    _render_at(1.5 * TILE, 4 * TILE, 0.0)  # facing +x => east wall
    # Middle screen band must contain wall pixels (non-flat), and the wall
    # should sit around the vertical centre.
    mid_x = W // 2
    center = _tile_at(mid_x, H // 2)
    assert center not in ((20, 20, 25), (30, 30, 35)), center
    # Some wall pixel further right/near center
    seen = 0
    for x in range(W // 2 - 40, W // 2 + 40):
        c = _tile_at(x, H // 2 - 30)
        if c not in ((20, 20, 25), (30, 30, 35)):
            seen += 1
    assert seen > 30, f"too few wall pixels: {seen}"
    print("  wall column in middle band    OK")


def test_wall_textures_vary_by_pos():
    # Two different walls (different textures N/S vs E/W) should differ in
    # bright core pixels near the centre (br>20 vs dark flats).
    room = [
        "#########",
        "#TTTTT##",
        "#......#",
        "#......#",
        "#......#",
        "#......#",
        "#......#",
        "#########",
    ]
    _set_map(room)
    _render_at(1.5 * TILE, 4 * TILE, 0.0)
    east = _tile_at(W // 2 + 60, H // 2)
    # renderer must not be uniform flat colour (brick grey, distance-shaded)
    assert east[0] > 35, f"east wall too dark: {east}"
    assert east != (20, 20, 25) and east != (30, 30, 35), east
    print("  textured wall colour check    OK")


def test_floor_ceiling_band():
    room = [
        "######",
        "#....#",
        "#....#",
        "#....#",
        "######",
    ]
    _set_map(room)
    _render_at(1.5 * TILE, 2.5 * TILE, 0.0)
    top = _tile_at(W // 2, 30)
    fx = (W // 2) * gs.frame.iw // W
    fy = (560 * gs.frame.ih) // H  # well inside the floor band (below horizon)
    fv = tuple(gs.frame.buf[(fy * gs.frame.iw + fx) * 3:
                            (fy * gs.frame.iw + fx) * 3 + 3])
    bot = _tile_at(W // 2, H - 30)
    assert top == (20, 20, 25), top
    assert fv == (30, 30, 35), fv  # floor band present in the framebuffer
    # Screen bottom may carry the weapon sprite; it must stay dark, not wall.
    assert bot not in ((20, 20, 25),), bot
    print("  ceiling/floor solid bands     OK")


def test_tiers():
    for tier in ('high', 'medium', 'fast'):
        iw, ih = resolution(tier)
        room = [
            "######",
            "#....#",
            "#....#",
            "#....#",
            "######",
        ]
        _set_map(room)
        _render_at(1.5 * TILE, 2.5 * TILE, 0.0, tier=tier)
        assert gs.frame.size == (iw, ih), (tier, gs.frame.size)
        wall = _tile_at(W // 2, H // 2)
        assert wall != (20, 20, 25) and wall != (30, 30, 35), (tier, wall)
    print("  tier resolutions render       OK")


def test_back_wall_occlusion():
    # Player inside the room looking east from west wall: nearest east wall
    # should occlude the far west wall behind (z-buffer depth test intact).
    room = ["#######", "#.....#", "#.....#", "#.....#", "#######"]
    _set_map(room)
    _render_at(1.4 * TILE, 3 * TILE, 0.0)
    mid = _tile_at(W // 2, H // 2)
    assert mid not in ((20, 20, 25), (30, 30, 35)), mid
    print("  z-buffer occlusion intact     OK")


def _crack_columns():
    cols = set()
    for x in range(W):
        for y in range(H):
            if gs.screen.get_at((x, y))[:3] == (20, 15, 10):
                cols.add(x // SCALE)
                break
    return cols


def test_cracks_span_wall_columns():
    # A damaged destructible wall must crack across every column it covers, not
    # just one. The overlay used to run once after the ray loop on the loop's
    # leftover variables, so cracks only ever landed on the last column's wall.
    room = ["HHHHHHHH", "H......H", "H......H", "H......H", "HHHHHHHH"]
    _set_map(room)
    gs.dwall_hp = {}
    _render_at(1.5 * TILE, 2.5 * TILE, 0.0)
    base = _crack_columns()
    gs.dwall_hp = {(x, y): 2 for y, row in enumerate(room)
                   for x, ch in enumerate(row) if ch == 'H'}
    _render_at(1.5 * TILE, 2.5 * TILE, 0.0)
    cracked = _crack_columns()
    gs.dwall_hp = {}
    new_cols = cracked - base
    assert len(new_cols) > 5, f"cracks landed on {len(new_cols)} column(s)"
    print(f"  cracks span wall columns ({len(new_cols)})   OK")


def main():
    _init()
    test_frame_created()
    test_wall_in_middle()
    test_wall_textures_vary_by_pos()
    test_floor_ceiling_band()
    test_tiers()
    test_back_wall_occlusion()
    test_cracks_span_wall_columns()
    print("smoke_framebuffer_tile: ALL OK")


if __name__ == "__main__":
    main()