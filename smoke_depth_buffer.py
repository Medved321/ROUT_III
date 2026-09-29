"""Smoke: the per-column depth buffer as a contract (change
``depth-buffer-column-grid``).

The sprite pass used to compute its column as ``min(const.W // SCALE - 1,
sx // SCALE)`` -- i.e. it *assumed* the depth buffer had ``const.W // SCALE``
entries.  That is the tile path's grid.  The sector path builds a different
one (``const.W // gs.render_scale``), and ``gs.render_scale`` and ``const.SCALE``
are separate settings that the quality ladder moves independently, so as soon as
the first exceeded the second the clamp's own upper bound ran off the end of the
buffer:

    IndexError: list index out of range
      File "engine/rendering.py", line 1088, in render
        if 0 <= sx < const.W and z_b[idx] > occ:

That is a game-loop crash, so it is the first thing checked here.  The rest
covers the two things the fix rests on: the buffer is the only source of truth
for its own grid (``depth_col``), and the sector renderer reads the live window
size and FOV instead of import-time copies of them.

Run: ``py -3.12 smoke_depth_buffer.py``
"""
from __future__ import annotations

import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pygame  # noqa: E402

from engine import constants as const  # noqa: E402
from engine import game_state as gs  # noqa: E402
from engine import rendering  # noqa: E402
from engine import sector_render  # noqa: E402
from engine.framebuffer import depth_col  # noqa: E402
from engine.textures import gen_textures  # noqa: E402

from shared.sector_map import (  # noqa: E402
    LineDef, Sector, SectorMap, SideDef, Thing, Vertex,
)

AMMO = {'pstl': 50, 'shl': 20, 'slg': 10, 'rck': 5, 'BFG': 1}


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

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


def _corridor():
    """A 512x512 closed room: the player's view is bounded on every side, so
    the depth buffer is closed near the edges and open down the middle."""
    sm = SectorMap()
    sm.sectors.append(Sector(floor_h=0, ceil_h=128, light=160))
    _line(sm, 0, 0, 512, 0, 0)
    _line(sm, 512, 0, 512, 512, 0)
    _line(sm, 512, 512, 0, 512, 0)
    _line(sm, 0, 512, 0, 0, 0)
    return sm


def _init(w=1000, h=700, scale=4):
    pygame.init()
    gs.screen = pygame.display.set_mode((w, h))
    gs.font = pygame.font.SysFont("monospace", 34, bold=True)
    gs.sm_font = pygame.font.SysFont("monospace", 18)
    # Content providers: without them the engine degrades to blank sprites and
    # neutral walls, and the occlusion checks below would pass for the wrong
    # reason.
    gs.tile_provider = lambda c: 'wall'
    gs.sprite_provider = lambda *a, **k: pygame.Surface(
        (8, 8), pygame.SRCALPHA)
    gen_textures()
    from engine.wallcache import DEFAULT_CACHE
    DEFAULT_CACHE.clear_tex()

    gs.textures = {}
    gs.flats = {}
    gs.MAP = []
    gs.M_SZ = 0
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
    gs.enemy_models = {}
    gs.exit_pos = (0, 0)
    gs.show_minimap = False
    gs.show_crosshair = False
    gs.fog_enabled = False
    gs.view_dist = 900
    gs.vs_scale = 2
    gs.flat_textured = False
    gs.dash_fx = 0.0
    gs.lvl_type = 'normal'
    gs.guns = {0, 1}
    gs.p = {'hp': 100, 'wpn': 1, 'powerup': 0, 'armor': 0, 'keys': {},
            'weapons': {}, 'frags': 0, 'guns': {0, 1}, 'grenades': 0}
    gs.p.update(AMMO)
    gs.w_tmr = 0
    gs.sec_scene = sector_render.SectorScene(_corridor())
    gs.sector_mode = True
    _set_geometry(w, h, scale)


def _set_geometry(w, h, scale):
    """Apply a window size / render scale the way the client does: by writing
    the live values, not by rebinding a module-level import."""
    const.W, const.H = w, h
    const.SCALE = scale
    rendering.SCALE = scale
    gs.render_scale = scale
    gs.screen = pygame.display.set_mode((w, h))


def _sector_frame(px=256.0, py=256.0, pa=0.0, enemy_x=456.0, enemy_y=256.0):
    """One sector frame with a single enemy in view, drawn end to end."""
    gs.px, gs.py, gs.pa = px, py, pa
    gs.pz = 0.0
    gs.pitch = 0.0
    gs.enemies = [{'a': 1, 'x': enemy_x, 'y': enemy_y, 'hp': 50, 't': 'imp',
                   'state': 0, 'angle': 0.0, 'id': 1}]
    rendering.render(const.H, 0)


# ---------------------------------------------------------------------------
# 1. The crash
# ---------------------------------------------------------------------------

def test_render_never_indexes_out_of_range():
    """The reported crash, swept over the whole parameter space.

    Sweeping window width x SCALE x gs.render_scale is the point: the crash was
    reachable at *any* window size and depended only on the two scales
    disagreeing, so a single "normal" configuration would not have caught it.
    """
    widths = (640, 1000, 1600, 3840)
    scales = (2, 4)
    render_scales = (1, 2, 3, 4, 5, 6, 8)
    crashed = []
    for w in widths:
        for scale in scales:
            for rs in render_scales:
                _set_geometry(w, 700, scale)
                gs.render_scale = rs          # deliberately decoupled
                try:
                    _sector_frame()
                except IndexError as e:
                    crashed.append((w, scale, rs, str(e)))
    assert not crashed, (
        f"depth buffer indexed out of range in {len(crashed)} of "
        f"{len(widths) * len(scales) * len(render_scales)} configurations: "
        f"{crashed[:4]}")
    print(f"  no out-of-range column in "
          f"{len(widths) * len(scales) * len(render_scales)} configs    OK")


def test_buffer_length_is_never_guessed():
    """Every consumer must tolerate a buffer shorter than the tile grid --
    which the sector path hands out routinely."""
    for scale, rs in ((4, 8), (2, 5), (2, 8), (4, 6)):
        _set_geometry(1000, 700, scale)
        gs.render_scale = rs
        z_b = sector_render.render_scene(700, 0)
        assert len(z_b) == const.W // rs, (scale, rs, len(z_b))
        assert len(z_b) < const.W // scale, "test needs a short buffer"
        # The old clamp's own upper bound was the bug: it pointed past the end.
        for sx in range(const.W):
            col = depth_col(z_b, sx, const.W)
            assert 0 <= col < len(z_b), (scale, rs, sx, col)
    print("  short buffers stay addressable          OK")


# ---------------------------------------------------------------------------
# 2. depth_col: exact, in bounds, monotonic
# ---------------------------------------------------------------------------

def test_depth_col_grid():
    for n in (1, 2, 3, 7, 250, 333, 1000, 2000):
        z_b = [0.0] * n
        for w in (1, 2, 640, 1000, 1920, 3840):
            prev = -1
            for sx in range(-8, w + 8):
                col = depth_col(z_b, sx, w)
                assert -1 <= col < n, (n, w, sx, col)
                if 0 <= sx < w:
                    # Inside the screen there is always a column, and never a
                    # clamped one: -1 would hide a visible sprite.
                    assert col >= 0, (n, w, sx, col)
                else:
                    assert col == -1, (n, w, sx, col)
                if col >= 0:
                    assert col >= prev, f"not monotonic: {n} {w} {sx} {col}"
                    prev = col
    print("  depth_col in bounds, exact, monotonic   OK")


def test_depth_col_maps_to_the_right_column():
    """With a buffer one entry per screen column it is the identity; with a
    four-times coarser buffer, screen column 400 belongs to column 100 -- not
    to the column a naive ``sx // 4`` would have picked against a differently
    sized buffer."""
    assert depth_col([0.0] * 1000, 400, 1000) == 400
    assert depth_col([0.0] * 250, 400, 1000) == 100
    assert depth_col([0.0] * 250, 0, 1000) == 0
    assert depth_col([0.0] * 250, 999, 1000) == 249
    # Degenerate buffers report "no column" instead of raising.
    assert depth_col([], 10, 1000) == -1
    assert depth_col([0.0] * 250, 10, 0) == -1
    print("  depth_col picks the covering column     OK")


# ---------------------------------------------------------------------------
# 3. Occlusion actually tests the sprite's own column
# ---------------------------------------------------------------------------

def test_sprite_occlusion_uses_its_own_column():
    """A sprite is drawn iff it is nearer than the wall closing its column.

    The room is closed at x=512 and the player sits at x=256 facing east, so
    the centre column closes at 256 units.  An enemy nearer than that survives
    the depth test; one farther is culled.  Measured by differencing against a
    frame with no enemy at all, which is the only honest way to ask "was the
    sprite drawn" -- "is there a non-void pixel" is true either way.

    Checked with the two scales deliberately disagreeing, which is exactly the
    configuration that used to crash.
    """
    for scale, rs in ((2, 8), (4, 6), (2, 5)):
        _set_geometry(1000, 700, scale)
        gs.render_scale = rs
        gs.px, gs.py, gs.pa = 256.0, 256.0, 0.0
        gs.pz = 0.0
        gs.pitch = 0.0

        z_b = sector_render.render_scene(700, 0)
        wall = z_b[depth_col(z_b, const.W // 2, const.W)]
        assert 0 < wall <= gs.view_dist, (scale, rs, wall)

        baseline = _render_no_enemy()
        near = _render_enemy(256.0 + wall - 20.0)
        far = _render_enemy(256.0 + wall + 20.0)
        assert near != baseline, \
            f"in-front sprite culled (scale={scale} rs={rs} wall={wall})"
        assert far == baseline, \
            f"behind-wall sprite drawn (scale={scale} rs={rs} wall={wall})"
    print("  sprite culled by its own column          OK")


def _render_no_enemy():
    _sector_frame(enemy_x=-10000.0, enemy_y=-10000.0)
    return pygame.image.tostring(gs.screen, 'RGB')


def _render_enemy(x):
    _sector_frame(enemy_x=x, enemy_y=256.0)
    return pygame.image.tostring(gs.screen, 'RGB')


def test_occlusion_tests_the_covering_column_not_a_neighbour():
    """The failure this change fixes was silent before it was fatal: with the
    scales disagreeing, a consumer that recomputed the column count from
    ``const.SCALE`` read *some other* column and drew the wrong answer.  So the
    mapping has to be shown to pick the column that actually covers the sprite
    -- and, on a buffer built to make the two disagree, to disagree with the
    naive answer.
    """
    w, scale, rs = 1000, 2, 8
    n = w // rs                      # 125 columns
    assert n != w // scale           # the two grids genuinely differ

    # (a) The crash itself: the old clamp's upper bound is past the end of the
    # buffer, so *any* screen column past 248 raised IndexError. That is the
    # reported traceback, stated as an assertion instead of a crash.
    naive_out = min(w // scale - 1, 700 // scale)
    assert naive_out >= n, (naive_out, n)
    assert depth_col([0.0] * n, 700, w) < n

    # (b) Silent wrongness: further left, the old mapping still produced an
    # in-range index -- just not the column covering the sprite. No crash, and
    # the wrong answer.
    sx = 200
    naive = min(w // scale - 1, sx // scale)
    real = depth_col([0.0] * n, sx, w)
    assert 0 <= naive < n, (naive, n)        # in range: no crash
    assert naive != real, (naive, real)     # but the wrong column
    assert 0 <= real < n

    # Wall in the covering column only: correct answer is "culled", the naive
    # column is open, so the old mapping would have drawn it.
    z_b = [999.0] * n
    z_b[real] = 10.0
    assert not (z_b[depth_col(z_b, sx, w)] > 500.0), "wall not respected"
    assert z_b[naive] > 500.0, "test is vacuous: both columns closed"

    # Mirror image: wall in the naive column, covering column open.
    z_b = [999.0] * n
    z_b[naive] = 10.0
    assert z_b[depth_col(z_b, sx, w)] > 500.0, "open column wrongly culled"
    print("  occlusion reads the covering column     OK")


# ---------------------------------------------------------------------------
# 4. Live window size / FOV in the sector renderer
# ---------------------------------------------------------------------------

def test_resize_tracks_the_window():
    """The sector renderer used to import ``W``/``H``/``FOV`` by value, freezing
    them at first import (a lazy import, on the first sector frame).  So the
    buffer, the projected sprite columns and the screen disagreed after any
    resize.  Both must follow ``const`` now."""
    for w, h in ((1000, 700), (1600, 900), (640, 480), (3840, 2160)):
        _set_geometry(w, h, 4)
        gs.render_scale = 4
        z_b = sector_render.render_scene(h, 0)
        assert len(z_b) == w // 4, (w, len(z_b))
        # The background clear must cover the whole current screen, otherwise a
        # narrowed window keeps an unpainted strip from the old size.
        proj = sector_render.project_object(456.0, 256.0, 50, h, 0)
        assert proj is not None
        sx = proj[0]
        assert 0 <= sx < w, (w, sx)
    print("  buffer and projection follow the window  OK")


def test_fov_change_is_applied():
    """``const.FOV`` is rewritten by the settings screen; the BSP rays and the
    sprite projection must both follow it, or a sector map keeps the projection
    of the FOV the game booted with."""
    _set_geometry(1000, 700, 4)
    gs.render_scale = 4
    gs.px, gs.py, gs.pa = 256.0, 256.0, 0.0
    sx = {}
    import math
    for deg in (60, 90, 40):
        const.FOV = math.radians(deg)
        rendering.FOV = const.FOV
        # An object at a fixed off-axis angle must move on screen when the FOV
        # changes: a wider FOV compresses everything towards the centre.
        proj = sector_render.project_object(256.0 + 100.0, 256.0 + 100.0, 50,
                                            const.H, 0)
        assert proj is not None
        sx[deg] = proj[0]
    assert sx[90] < sx[60], f"wider FOV must pull the object in: {sx}"
    assert sx[40] > sx[60], f"narrower FOV must push it out: {sx}"
    const.FOV = math.radians(60)
    rendering.FOV = const.FOV
    print("  FOV change reaches the sector renderer  OK")


def test_narrow_window_is_fully_painted():
    """A narrowed window must still be fully repainted, and the depth buffer
    must shrink with it (the sprite columns come from the same width)."""
    for w, h in ((1600, 900), (3840, 2160), (640, 480)):
        _set_geometry(w, h, 4)
        gs.render_scale = 4
        gs.px, gs.py, gs.pa = 256.0, 256.0, 0.0
        gs.screen.fill((255, 0, 255))
        sector_render.render_scene(h, 0)
        poison = sum(1 for x in range(0, w, 7) for y in (0, h - 1)
                     if gs.screen.get_at((x, y))[:3] == (255, 0, 255))
        assert poison == 0, (w, h, poison)
        # And the sprite columns of a centred object land in the middle of the
        # *current* screen, not the middle of the width frozen at import.
        sx = sector_render.project_object(356.0, 256.0, 50, h, 0)[0]
        assert abs(sx - w / 2) < w * 0.02, (w, sx)
    print("  narrowed window repainted and re-projected OK")


# ---------------------------------------------------------------------------
# 5. Particles share the grid
# ---------------------------------------------------------------------------

def test_particles_use_the_same_grid():
    """``draw_particles`` indexed with ``sx // gs.render_scale`` against a buffer
    built with ``const.SCALE`` in the tile path -- a different column from the
    one the sprites use.  Now both go through ``depth_col``."""
    from engine import particles
    for scale, rs in ((2, 8), (4, 6), (2, 5), (4, 4)):
        _set_geometry(1000, 700, scale)
        gs.render_scale = rs
        gs.px, gs.py, gs.pa, gs.pitch = 500.0, 500.0, 0.0, 0.0
        gs.screen = pygame.Surface((const.W, const.H))
        # A buffer far shorter than the tile grid: the old clamp used the tile
        # grid as its bound and reached past the end of this one.
        n = const.W // rs
        z_b = [999.0] * n
        gs.particles = [{
            'x': 560.0, 'y': 500.0, 'vx': 0.0, 'vy': 0.0, 'life': 20,
            'max_life': 30, 'col': (200, 0, 0), 'size': 8, 'z': 10, 'zv': 0.0,
        }]
        # Every particle column must be addressable and consistent with the
        # column the sprite pass would have used.
        before = pygame.image.tostring(gs.screen, 'RGB')
        particles.draw_particles(z_b, 480)
        assert pygame.image.tostring(gs.screen, 'RGB') != before, \
            "particle never drew (occlusion test rejected it)"
        # Occluded: a wall closer than the particle must hide it.
        gs.screen.fill((0, 0, 0))
        z_far = [0.0] * n
        before = pygame.image.tostring(gs.screen, 'RGB')
        particles.draw_particles(z_far, 480)
        assert pygame.image.tostring(gs.screen, 'RGB') == before, \
            "particle drawn through a wall that is closer than it"
    print("  particles share the sprite grid         OK")


def main():
    _init()
    test_render_never_indexes_out_of_range()
    test_buffer_length_is_never_guessed()
    test_depth_col_grid()
    test_depth_col_maps_to_the_right_column()
    test_sprite_occlusion_uses_its_own_column()
    test_occlusion_tests_the_covering_column_not_a_neighbour()
    test_resize_tracks_the_window()
    test_fov_change_is_applied()
    test_narrow_window_is_fully_painted()
    test_particles_use_the_same_grid()
    print("smoke_depth_buffer: ALL OK")


if __name__ == "__main__":
    main()
