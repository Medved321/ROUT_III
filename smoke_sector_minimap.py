"""Smoke: geometry-only minimap for sector maps (change
`sector-minimap-editor-fps-cleanup`, group 1).

The sector path used to clear ``gs.MAP``/``gs.M_SZ`` and therefore never drew a
minimap at all.  This test holds the four properties the change promises:

  1. the hook in ``rendering.render()`` paints sector geometry;
  2. no actor from the world snapshot appears on it (compare frames with and
     without enemies/bots/players -- pixel-identical is stronger than looking
     for one particular marker colour);
  3. the box follows ``ui_px`` from 640x480 to 3840x2160 and stays on screen;
  4. segments are cached per (scene, size), and no ``Surface`` is allocated on
     a settled frame; a mutated line changes the drawing after invalidation.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pygame  # noqa: E402

pygame.init()

from engine import constants as const  # noqa: E402
from engine import game_state as gs  # noqa: E402
from engine import rendering as R  # noqa: E402
from engine import sector_minimap as smm  # noqa: E402
from engine.sector_render import SectorScene  # noqa: E402
from engine.viewport import ui_px  # noqa: E402
from shared.sector_map import LineDef, Sector, SectorMap, SideDef, Vertex  # noqa: E402

PRESETS = ((640, 480), (1000, 700), (1600, 900), (3840, 2160))
PALETTE = tuple(bytes(c) for c in (smm.SOLID_COLOR, smm.PORTAL_COLOR,
                                   smm.FLOATING_COLOR))


def _vertex(sm, x, y):
    for i, v in enumerate(sm.verts):
        if v.x == x and v.y == y:
            return i
    sm.verts.append(Vertex(float(x), float(y)))
    return len(sm.verts) - 1


def _line(sm, ax, ay, bx, by, front=-1, back=-1, flags=0):
    f = b = -1
    if front >= 0:
        f = len(sm.sidedefs)
        sm.sidedefs.append(SideDef(sector=front, mid="WALL"))
    if back >= 0:
        b = len(sm.sidedefs)
        sm.sidedefs.append(SideDef(sector=back, mid="-"))
    sm.linedefs.append(LineDef(_vertex(sm, ax, ay), _vertex(sm, bx, by),
                               front=f, back=b, flags=flags))
    return len(sm.linedefs) - 1


def _map():
    """Two rooms, a portal between them, and a floating-slab border."""
    sm = SectorMap()
    sm.sectors.append(Sector(floor_h=0, ceil_h=128, light=255))
    sm.sectors.append(Sector(floor_h=0, ceil_h=128, light=255))
    sm.sectors.append(Sector(floor_h=48, ceil_h=8, light=255, floating=True))
    _line(sm, 0, 0, 512, 0, 0)
    _line(sm, 512, 0, 512, 224, 0)
    _line(sm, 512, 224, 512, 288, 0, 1)          # two-sided portal
    _line(sm, 512, 288, 512, 512, 0)
    _line(sm, 512, 512, 0, 512, 0)
    _line(sm, 0, 512, 0, 0, 0)
    _line(sm, 512, 0, 1024, 0, 1)
    _line(sm, 1024, 0, 1024, 512, 1)
    _line(sm, 1024, 512, 512, 512, 1)
    # A plate edge (one-sided, touching the floating sector).
    _line(sm, 128, 128, 384, 128, 2)
    _line(sm, 384, 128, 384, 384, 2)
    _line(sm, 384, 384, 128, 384, 2)
    _line(sm, 128, 384, 128, 128, 2)
    return sm


def _setup(w=1000, h=700):
    const.W, const.H = w, h
    scene = SectorScene(_map())
    gs.sec_scene = scene
    gs.sector_mode = True
    gs.MAP = []
    gs.M_SZ = 0
    gs.explored = []
    gs.screen = pygame.Surface((w, h))
    gs.px, gs.py, gs.pa = 256.0, 256.0, 0.0
    gs.show_minimap = True
    gs.font = pygame.font.Font(None, 28)
    gs.sm_font = pygame.font.Font(None, 20)
    gs.textures = {}
    gs.flats = {}
    gs.bots = []
    gs.enemies = []
    gs.players = []
    gs.items = []
    gs.bul = []
    gs.proj = []
    gs.corpses = []
    gs.portals = []
    gs.barrels = []
    gs.impacts = []
    smm.invalidate()
    return scene


def _box_pixels(surface, box=smm.BOX_UNITS):
    """Bytes of the authored minimap block, including its margin."""
    ox = oy = ui_px(smm.MARGIN_UNITS)
    n = ui_px(box) + 2
    out = bytearray()
    for y in range(oy, min(oy + n, surface.get_height())):
        for x in range(ox, min(ox + n, surface.get_width())):
            out += bytes(surface.get_at((x, y))[:3])
    return bytes(out)


def _has_geometry(pixels):
    return any(pixels[i:i + 3] in PALETTE for i in range(0, len(pixels), 3))


def test_geometry_and_no_entities():
    _setup()
    gs.bots = [{"x": 256.0, "y": 256.0, "col": (255, 40, 40)}]
    gs.enemies = [{"id": 3, "x": 300.0, "y": 256.0, "hp": 50, "a": True,
                   "state": "idle", "type": "soldier", "t": "soldier",
                   "angle": 0.0, "pa": 0.0, "frame": 0, "etime": 0.0}]
    gs.players = [{"id": 2, "px": 280.0, "py": 256.0, "pa": 0.0,
                   "angle": 0.0, "col": (40, 200, 255)}]
    R.render(const.H, 0)
    with_actors = _box_pixels(gs.screen)
    assert _has_geometry(with_actors), "sector render did not paint minimap geometry"

    gs.bots = []
    gs.enemies = []
    gs.players = []
    R.render(const.H, 0)
    without_actors = _box_pixels(gs.screen)
    assert with_actors == without_actors, \
        "entity markers leaked into the geometry-only sector minimap"

    # The tile path must keep its own markers: a bot dot is still green/red.
    gs.MAP = [["." for _ in range(12)] for _ in range(12)]
    gs.M_SZ = 12
    gs.explored = [[True] * 12 for _ in range(12)]
    gs.sector_mode = False
    gs.sec_scene = None
    gs.bots = [{"x": 64.0, "y": 64.0, "col": (255, 40, 40)}]
    R.render(const.H, 0)
    assert bytes((255, 40, 40)) in _box_pixels(gs.screen, 60), \
        "tile minimap lost its bot marker"


def test_no_scene_no_minimap():
    _setup()
    gs.sec_scene = None
    gs.screen.fill((123, 45, 67))
    R.render(const.H, 0)
    pixels = _box_pixels(gs.screen)
    assert not _has_geometry(pixels), "minimap drew geometry without a sector scene"


def test_flip_switch():
    _setup()
    gs.show_minimap = False
    gs.screen.fill((0, 0, 0))
    R.render(const.H, 0)
    assert not _has_geometry(_box_pixels(gs.screen))
    gs.show_minimap = True
    R.render(const.H, 0)
    assert _has_geometry(_box_pixels(gs.screen)), "minimap did not come back"


def test_resize_keeps_share_and_box_on_screen():
    shares = []
    for w, h in PRESETS:
        _setup(w, h)
        smm.draw_hud()
        box = ui_px(smm.BOX_UNITS)
        margin = ui_px(smm.MARGIN_UNITS)
        assert margin + box <= w and margin + box <= h, (w, h, box)
        assert _has_geometry(_box_pixels(gs.screen)), (w, h)
        shares.append(box / float(h))
    assert max(shares) - min(shares) <= 0.02, shares


def test_cache_and_no_allocation():
    scene = _setup()
    calls = []
    real_build = smm._build_segments
    smm._build_segments = lambda sc, box: (calls.append(1), real_build(sc, box))[1]
    real_surface = pygame.Surface
    allocations = []

    def counted(*args, **kwargs):
        allocations.append(1)
        return real_surface(*args, **kwargs)

    pygame.Surface = counted
    try:
        smm.draw_hud()
        smm.draw_hud()
    finally:
        pygame.Surface = real_surface
        smm._build_segments = real_build
    assert len(calls) == 1, f"geometry rebuilt {len(calls)} times in a settled window"
    assert not allocations, f"{len(allocations)} surfaces allocated for a settled minimap"

    before = _box_pixels(gs.screen)
    # Move one plate edge vertically: unlike the shared top boundary, this
    # segment has no neighbour painted on the same screen row, so the change
    # is visible in the captured block.
    scene.y1[9] += 96.0
    smm.draw_hud()
    assert _box_pixels(gs.screen) == before, "cached frame must not rebuild on its own"
    smm.invalidate()
    smm.draw_hud()
    assert _box_pixels(gs.screen) != before, "mutated line did not change the drawing"


def main():
    tests = [test_geometry_and_no_entities, test_no_scene_no_minimap,
             test_flip_switch, test_resize_keeps_share_and_box_on_screen,
             test_cache_and_no_allocation]
    for fn in tests:
        fn()
        print(f"{fn.__name__}: OK")
    print(f"sector minimap smoke: {len(tests)} sections OK")


if __name__ == "__main__":
    main()
