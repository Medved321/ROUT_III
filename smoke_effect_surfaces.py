"""Smoke test for the effect surface budget (OpenSpec change
`effect-surface-budget`).

The bug it exists for: `pygame.error: Out of memory` in
`engine/particles.py:_cached_circle_surface()`, reached from
`draw_particles()`.  A puff spawned at the player's feet projected to a radius
of ~8600 px and asked pygame for a 17318x17318 SRCALPHA surface -- 1.1 GiB for
one effect -- and the cache was bounded by entry count, not by pixels.

What is covered:
  1. the reported crash: dash dust / explosion smoke right on top of the camera,
  2. the same for scorch and blood decals at arm's length,
  3. a sweep over window sizes, FOVs and distances: nothing raises, and no
     surface exceeds the cap,
  4. an oversized effect really is drawn (as a primitive, without allocating),
  5. the near plane covers size *and* height consistently, while the depth test
     keeps using the true distance,
  6. the cache respects its pixel budget and stops re-allocating on a steady
     scene,
  7. the degraded enemy/bot stand-in surfaces are capped too.

Run: py -3.12 smoke_effect_surfaces.py
"""
import os
import sys

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

import pygame

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine import constants as const
from engine import game_state as gs
from engine import particles as P
from engine import rendering as R

OPEN_MAP = [[1] * 12] + [[1] + [0] * 10 + [1] for _ in range(10)] + [[1] * 12]

# Settings as imported: the sweep below rewrites them, every scenario starts
# from these again.
BASE_W, BASE_H, BASE_FOV = const.W, const.H, const.FOV

# Every surface size requested while a guard is armed, as (w, h).
_alloc_log = []
_alloc_depth = 0
_real_surface = pygame.Surface
_UNSET = object()


def _spy_surface(size, flags=_UNSET, depth=_UNSET, masks=_UNSET):
    """`pygame.Surface` that remembers how big it was asked to be.

    The memory bound is not observable from the outside -- a surface that gets
    allocated is a surface that gets allocated -- so the test watches the
    allocation itself.  Unset arguments are forwarded as unset (pygame's own
    defaults are sentinels, not 0), otherwise SRCALPHA surfaces stop building.
    """
    if _alloc_depth:
        _alloc_log.append((int(size[0]), int(size[1])))
    kw = {}
    if flags is not _UNSET:
        kw['flags'] = flags
    if depth is not _UNSET:
        kw['depth'] = depth
    if masks is not _UNSET:
        kw['masks'] = masks
    return _real_surface(size, **kw)


class _watch_alloc:
    """Arm the allocation spy. Also drops the effect surface caches."""

    def __enter__(self):
        global _alloc_depth
        _alloc_log.clear()
        P._SMOKE_SURFACES.clear()
        P._SCORCH_SURFACES.clear()
        R._BLANK_SPRITES.clear()
        R._GHOST_SPRITES.clear()
        _alloc_depth += 1
        self._saved = pygame.Surface
        pygame.Surface = _spy_surface
        return _alloc_log

    def __exit__(self, *exc):
        global _alloc_depth
        pygame.Surface = self._saved
        _alloc_depth -= 1
        return False


def _scene(w=None, h=None, fov=None, px=320.0, py=320.0):
    """Reset the renderer state the effect paths read, and return the screen."""
    const.W = BASE_W if w is None else w
    const.H = BASE_H if h is None else h
    const.FOV = BASE_FOV if fov is None else fov
    gs.screen = pygame.Surface((const.W, const.H))
    gs.screen.fill((20, 24, 32))
    gs.sector_mode = False
    gs.sec_scene = None
    gs.particles.clear()
    gs.decals.clear()
    gs.weather.clear()
    gs.weather_type = 'none'
    gs.MAP = OPEN_MAP
    gs.M_SZ = len(OPEN_MAP)
    gs.px, gs.py, gs.pz = px, py, 0.0
    gs.pa, gs.pitch = 0.0, 0.0
    gs.view_dist = 900.0
    gs.effects_factor = 1.0
    gs.blood_amount = 1.0
    return gs.screen


def _zbuf(dist=10_000.0):
    """A depth buffer that sees everything (`const.W // SCALE` columns)."""
    return [dist] * (const.W // const.SCALE)


def _max_side(sizes):
    return max((max(w, h) for w, h in sizes), default=0)


def _pixels():
    """Raw RGB bytes of the current screen, for "did anything get drawn"."""
    return bytes(pygame.image.tobytes(gs.screen, 'RGB'))


def test_dash_dust_at_camera():
    """1. The reported crash: dash dust and explosion smoke on the camera."""
    _scene()
    cap = const.CIRCLE_MAX_R * 2
    z_b = _zbuf()
    worst = 0
    for spawn in (lambda: P.spawn_dash_burst(gs.px, gs.py),
                  lambda: P.spawn_dash_puff(gs.px, gs.py, const.DASH_FX_COLOR, 0.0),
                  lambda: P.spawn_explosion(gs.px, gs.py),
                  lambda: P.spawn_gib(gs.px + 1.0, gs.py, (150, 20, 20), count=5)):
        _scene()
        with _watch_alloc() as log:
            spawn()
            P.draw_particles(z_b, const.H, 0)
        side = _max_side(log)
        assert side <= cap, (
            f'surface of side {side} allocated for an effect at the camera '
            f'(cap {cap}) -- this is the OOM')
        worst = max(worst, side)
    print(f'1. dust/smoke at the camera: no raise, largest surface '
          f'{worst}px (cap {cap}px)')


def test_decals_at_camera():
    """2. Scorch / blood / bullet holes a couple of units away."""
    _scene()
    cap = const.CIRCLE_MAX_R * 2
    z_b = _zbuf()
    worst = 0
    cases = [(lambda: P.add_scorch(gs.px + 2.0, gs.py), 'scorch'),
             (lambda: P.add_blood_stain(gs.px + 2.0, gs.py), 'blood'),
             (lambda: P.add_bullet_hole(gs.px + 2.0, gs.py), 'hole')]
    for spawn, name in cases:
        _scene()
        with _watch_alloc() as log:
            spawn()
            P.draw_decals(const.H, 0)
            P.draw_particles(z_b, const.H, 0)
        side = _max_side(log)
        assert side <= cap, f'{name} at 2 units: surface of side {side} (cap {cap})'
        worst = max(worst, side)
    print(f'2. scorch/blood/hole at 2 units: no raise, largest surface '
          f'{worst}px (cap {cap}px)')


def test_resolution_sweep():
    """3. No (W, H, FOV, distance) combination raises or over-allocates."""
    worst = 0
    combos = 0
    for w in (640, 1000, 1600, 3840):
        for fov in (const.FOV, 1.2, 2.0):
            for dist in (2.0, 2.5, 4.0, 8.0, 16.0, 64.0, 256.0):
                _scene(w=w, h=max(400, w // 2), fov=fov)
                z_b = _zbuf()
                with _watch_alloc() as log:
                    P.spawn_explosion(gs.px + dist, gs.py)
                    P.spawn_particles(gs.px + dist, gs.py, (150, 20, 20), 20, 3, 30)
                    P.spawn_sparks(gs.px + dist, gs.py, count=8)
                    P.add_scorch(gs.px + dist, gs.py)
                    P.add_blood_stain(gs.px + dist, gs.py)
                    P.add_bullet_hole(gs.px + dist, gs.py)
                    P.draw_particles(z_b, const.H, 0)
                    P.draw_decals(const.H, 0)
                side = _max_side(log)
                assert side <= const.CIRCLE_MAX_R * 2, (
                    f'W={w} FOV={fov:.2f} dist={dist}: surface of side {side}')
                worst = max(worst, side)
                combos += 1
    print(f'3. sweep over {combos} window/FOV/distance combinations: no raise, '
          f'largest surface {worst}px (cap {const.CIRCLE_MAX_R * 2}px)')


class _watch_draws:
    """Record the radii of the filled circles drawn while active.

    The oversized path is a primitive draw, and "it drew" has to be asserted on
    the call itself: at two units the tile projection puts the floor line far
    below the screen, so nothing of the puff is inside the frame and comparing
    pixels would pass for a skipped effect.
    """

    def __init__(self):
        self.radii = []

    def __enter__(self):
        self._real = pygame.draw.circle
        log = self.radii

        def spy(surface, color, center, radius, width=0):
            log.append(int(radius))
            return self._real(surface, color, center, radius, width)

        pygame.draw.circle = spy
        return self

    def __exit__(self, *exc):
        pygame.draw.circle = self._real
        return False


def test_oversized_uses_primitive():
    """4. A too-big effect is drawn, not skipped -- and allocates nothing."""
    _scene()
    z_b = _zbuf()
    with _watch_alloc() as log, _watch_draws() as draws:
        P.spawn_explosion(gs.px + 2.0, gs.py)
        P.draw_particles(z_b, const.H, 0)
    assert not P._SMOKE_SURFACES, (
        f'oversized smoke allocated {len(P._SMOKE_SURFACES)} surfaces')
    assert not log, f'oversized smoke allocated {len(log)} surfaces'
    oversized = [r for r in draws.radii if r > const.CIRCLE_MAX_R]
    assert oversized, (
        f'no oversized circle drawn (radii {sorted(set(draws.radii))[-3:]}) -- '
        f'an effect over {const.CIRCLE_MAX_R}px must still be drawn')
    print(f'4. oversized smoke: drawn as a primitive (r={max(oversized)}px), '
          f'0 surfaces allocated')


def test_near_plane_is_consistent():
    """5. The near plane bounds the scale effects derive from; the floor line
    and the depth test keep the true distance."""
    from engine.constants import TILE

    def scale_and_size(dst):
        """What the effect path derives from the projection at ``dst``."""
        _sx, sh, _ground_y, _perp = P.project_point(gs.px + dst, gs.py, TILE,
                                                    const.H, 0)
        return sh, int(14 * sh / TILE)

    at_plane = scale_and_size(const.EFFECT_NEAR_DIST)
    for dst in (2.0, 3.0, 6.0, 7.99):
        assert scale_and_size(dst) == at_plane, (
            f'dst={dst} projects differently from the near plane: '
            f'{scale_and_size(dst)} != {at_plane}')
    assert scale_and_size(64.0)[1] < at_plane[1], 'a far puff is not smaller'

    # The plane clamps scale only: the floor line still moves with the true
    # distance, so a puff at your feet is on the near floor, not glued to the
    # plane's.
    floors = [P.project_point(gs.px + d, gs.py, TILE, const.H, 0)[2]
              for d in (2.0, const.EFFECT_NEAR_DIST, 32.0)]
    assert floors[0] > floors[1] > floors[2], (
        f'the floor line stopped following distance: {floors}')

    # And the depth test still uses the true distance: an effect behind a wall
    # at 2 units draws nothing, however close the near plane is.
    _scene()
    P.spawn_dash_burst(gs.px + 2.0, gs.py)
    before = _pixels()
    P.draw_particles([1.0] * (const.W // const.SCALE), const.H, 0)
    assert _pixels() == before, (
        'an occluded effect drew pixels: the near plane leaked into the depth test')
    print('5. near plane bounds size; floor line and depth test keep the true '
          'distance')


def test_cache_budget():
    """6. Ladder is discrete, pixel budget holds, steady scene stops allocating."""
    _scene()
    # The ladder: every radius lands on a step, and the cap is reachable only
    # as a step, never exceeded.
    for r in range(1, 5000):
        snapped = P._circle_radius(r)
        assert snapped in P._RADIUS_LADDER, f'radius {r} snapped off-ladder'
        assert snapped <= const.CIRCLE_MAX_R, f'radius {r} -> {snapped} over cap'
        if r <= const.CIRCLE_MAX_R:
            assert abs(snapped - r) <= r * (const.CIRCLE_RADIUS_STEP - 1) / 2 + 1, (
                f'radius {r} snapped to {snapped}: more than half a step away')
    assert P._circle_radius(const.CIRCLE_MAX_R + 1) == const.CIRCLE_MAX_R
    assert P._circle_radius(10 ** 7) == const.CIRCLE_MAX_R

    peak = 0
    for r in range(1, 5000):
        P._draw_soft_circle(P._SMOKE_SURFACES, 0, 0, r, (200, 200, 200), 120)
        peak = max(peak, sum(s.get_width() * s.get_height()
                             for s in P._SMOKE_SURFACES.values()))
    assert peak <= const.CIRCLE_CACHE_PX, (
        f'cache holds {peak}px, budget is {const.CIRCLE_CACHE_PX}px')

    # A "steady" scene: a puff whose radius creeps by one pixel per frame.
    P._SMOKE_SURFACES.clear()
    with _watch_alloc() as log:
        r = 8
        for _ in range(400):
            P._draw_soft_circle(P._SMOKE_SURFACES, 0, 0, r, (200, 200, 200), 120)
            r += 1
    steps = len(P._RADIUS_LADDER)
    assert len(log) <= steps, (
        f'{len(log)} allocations for a slowly growing puff -- the ladder is '
        f'not being hit ({steps} steps available)')
    print(f'6. ladder discrete ({steps} steps), cache budget peak {peak}px of '
          f'{const.CIRCLE_CACHE_PX}px; a 400-frame growing puff costs '
          f'{len(log)} allocations')


def test_fallback_sprites_capped():
    """7. The degraded stand-in surfaces are capped as well."""
    cap = const.FALLBACK_SPRITE_MAX
    with _watch_alloc() as log:
        for size in (4, 56, 1000, 100_000, 10 ** 7):
            R._blank_sprite(size)
            R._ghost_sprite(size)
    side = _max_side(log)
    assert side <= cap, f'fallback sprite of side {side}, cap {cap}'
    assert R._blank_sprite(10 ** 7).get_width() == cap
    assert R._ghost_sprite(10 ** 7).get_width() == cap
    print(f'7. fallback stand-ins: side {side}px at a 10^7 request (cap {cap}px)')


def main():
    pygame.init()
    pygame.display.set_mode((320, 240))
    try:
        test_dash_dust_at_camera()
        test_decals_at_camera()
        test_resolution_sweep()
        test_oversized_uses_primitive()
        test_near_plane_is_consistent()
        test_cache_budget()
        test_fallback_sprites_capped()
    finally:
        pygame.quit()
    print('\neffect surface budget: all scenarios passed')


if __name__ == '__main__':
    main()
