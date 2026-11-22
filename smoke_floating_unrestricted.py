"""Smoke for openspec change floating-platforms-unrestricted.

Covers: validation accepts stacked/contact/overlapping floating sectors,
rejects thin/unsupported-thing; support_at handles stacked slabs.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shared.sector_map import (
    ACTOR_HEIGHT, MIN_PLATFORM_THICKNESS, STEP_HEIGHT,
    LineDef, Sector, SectorMap, SideDef, Thing, Vertex,
)


def _rect(sm, x1, y1, x2, y2, sector, back=None, mid="wall"):
    def vid(px, py):
        for i, v in enumerate(sm.verts):
            if v.x == px and v.y == py:
                return i
        sm.verts.append(Vertex(px, py))
        return len(sm.verts) - 1

    front = len(sm.sidedefs)
    sm.sidedefs.append(SideDef(sector=sector, mid=mid))
    back_side = -1
    if back is not None:
        back_side = len(sm.sidedefs)
        sm.sidedefs.append(SideDef(sector=back, mid=mid))
    corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    for i in range(4):
        ax, ay = corners[i]
        bx, by = corners[(i + 1) % 4]
        if (bx - ax) * (cy - ay) - (by - ay) * (cx - ax) < 0:
            ax, ay, bx, by = bx, by, ax, ay
        sm.linedefs.append(LineDef(vid(ax, ay), vid(bx, by), front=front, back=back_side))


def _base_map():
    sm = SectorMap()
    sm.sectors.append(Sector(floor_h=0, ceil_h=256, light=160,
                             floor_tex="floor", ceil_tex="ceiling"))
    _rect(sm, 0, 0, 512, 512, sector=0)
    sm.things.append(Thing(64, 64, "player1"))
    return sm


def test_stacked_floating_valid():
    sm = _base_map()
    sm.sectors.append(Sector(floor_h=64, ceil_h=48, light=160, floating=True,
                             floor_tex="floor", ceil_tex="ceiling"))
    sm.sectors.append(Sector(floor_h=128, ceil_h=112, light=160, floating=True,
                             floor_tex="floor", ceil_tex="ceiling"))
    _rect(sm, 128, 128, 256, 256, sector=1, back=0)
    _rect(sm, 128, 128, 256, 256, sector=2, back=0)
    errs = sm.validate()
    assert not errs, errs
    print("  stacked floating valid OK")


def test_overlapping_contact_valid():
    sm = _base_map()
    sm.sectors.append(Sector(floor_h=64, ceil_h=48, light=160, floating=True))
    sm.sectors.append(Sector(floor_h=64, ceil_h=48, light=160, floating=True))
    _rect(sm, 128, 128, 320, 320, sector=1, back=0)
    _rect(sm, 192, 192, 384, 384, sector=2, back=0)
    errs = sm.validate()
    assert not errs, errs
    print("  overlapping/contact valid OK")


def test_thin_slab_invalid():
    sm = _base_map()
    sm.sectors.append(Sector(floor_h=64, ceil_h=60, light=160, floating=True))
    _rect(sm, 128, 128, 256, 256, sector=1, back=0)
    errs = sm.validate()
    assert any("thinner" in e for e in errs), errs
    print("  thin slab invalid OK")


def test_thing_without_ordinary_support_invalid():
    sm = _base_map()
    sm.sectors.append(Sector(floor_h=64, ceil_h=48, light=160, floating=True))
    _rect(sm, 128, 128, 256, 256, sector=1)
    sm.things.append(Thing(192, 192, "imp"))
    errs = sm.validate()
    assert any("no floor with enough headroom" in e or "stands on floating" in e for e in errs), errs
    print("  thing without ordinary support invalid OK")


def test_support_at_stacked_slabs():
    sm = _base_map()
    sm.sectors.append(Sector(floor_h=24, ceil_h=8, light=160, floating=True))
    sm.sectors.append(Sector(floor_h=96, ceil_h=80, light=160, floating=True))
    _rect(sm, 128, 128, 256, 256, sector=1, back=0)
    _rect(sm, 128, 128, 256, 256, sector=2, back=0)
    sm.invalidate()
    assert sm.support_at(64, 64, 0.0) == 0.0
    assert sm.support_at(192, 192, 0.0) == 24.0
    assert sm.support_at(192, 192, 24.0) == 24.0
    assert sm.support_at(192, 192, 72.0) == 96.0
    # step-up limited by STEP_HEIGHT: from floor 0 cannot reach slab at 64
    sm2 = _base_map()
    sm2.sectors.append(Sector(floor_h=64, ceil_h=56, light=160, floating=True))
    _rect(sm2, 128, 128, 256, 256, sector=1, back=0)
    sm2.invalidate()
    # cannot step up to slab top (64-0 > STEP_HEIGHT), but can stand on floor under it
    assert sm2.support_at(192, 192, 0.0) == 0.0
    assert sm2.support_at(192, 192, 0.0, allow_platforms=False) == 0.0
    print("  support_at stacked slabs OK")


def test_text_map_round_trip_floating():
    from shared import text_map
    sm = _base_map()
    # stacked/overlapping at different heights, different boundaries
    sm.sectors.append(Sector(floor_h=64, ceil_h=48, light=160, floating=True))
    sm.sectors.append(Sector(floor_h=128, ceil_h=112, light=160, floating=True))
    _rect(sm, 128, 128, 256, 256, sector=1, back=0)
    _rect(sm, 160, 160, 288, 288, sector=2, back=0)
    # no-host / one-sided floating slab
    sm.sectors.append(Sector(floor_h=32, ceil_h=16, light=160, floating=True))
    _rect(sm, 320, 320, 384, 384, sector=3)
    sm.invalidate()
    txt = text_map.render(sm)
    sm2 = text_map.parse(txt)
    errs = sm2.validate()
    assert not errs, errs
    floats = {(s.floor_h, s.ceil_h) for s in sm2.sectors if s.floating}
    assert (64, 48) in floats
    assert (128, 112) in floats
    assert (32, 16) in floats
    print("  text_map round-trip floating OK")


def _slab_map():
    sm = _base_map()
    sm.sectors.append(Sector(floor_h=64, ceil_h=48, light=160, floating=True))
    _rect(sm, 128, 128, 384, 384, sector=1, back=0)
    sm.invalidate()
    return sm


def test_3d_los_slab():
    sm = _slab_map()
    # below slab: LOS clear at eye height 41
    assert sm.has_los_px(64, 256, 448, 256, z1=41.0, z2=41.0)
    # through slab volume: blocked
    assert not sm.has_los_px(64, 256, 448, 256, z1=56.0, z2=56.0)
    # above slab top: clear (shooter on slab, eye 64+41)
    assert sm.has_los_px(64, 256, 448, 256, z1=105.0, z2=105.0)
    print("  3d LOS slab OK")


def test_projectile_slab_block():
    from server.game_world import GameWorld
    sm = _slab_map()
    w = GameWorld('pve', sm)
    # bullet at height under slab should pass through
    w.bullets = [{'x': 64, 'y': 256, 'z': 20.0, 'vx': 100, 'vy': 0,
                  'dmg': 10, 'dist': 0, 'max_dist': 1000, 'col': (255,0,0),
                  'src': -1, 'from_player': True}]
    w._update_bullets()
    assert len(w.bullets) == 1, w.bullets
    # bullet through slab volume should be blocked
    w.bullets = [{'x': 64, 'y': 256, 'z': 56.0, 'vx': 100, 'vy': 0,
                  'dmg': 10, 'dist': 0, 'max_dist': 1000, 'col': (255,0,0),
                  'src': -1, 'from_player': True}]
    w._update_bullets()
    assert len(w.bullets) == 0, w.bullets
    print("  projectile slab block OK")


def test_enemy_steps_onto_platform():
    from server.game_world import GameWorld
    sm = _base_map()
    sm.sectors.append(Sector(floor_h=24, ceil_h=8, light=160, floating=True))
    _rect(sm, 128, 128, 256, 256, sector=1, back=0)
    sm.invalidate()
    w = GameWorld('pve', sm)
    en = {'id': 1, 'x': 120, 'y': 192, 'z': 0.0, 'pa': 0.0, 't': 'imp',
          'hp': 100, 'max_hp': 100, 'a': True, 'spd': 2.0, 'dying': 0,
          'stun': 0, 'alert': False, 'ls': 0, 'd_flash': 0, 'anim_frame': 0,
          'anim_state': 0, 'attack_from': None, 'attack_ticks': 0,
          'spawn_x': 120, 'spawn_y': 192, 'spawn_z': 0.0, 'spawn_pa': 0.0}
    w.enemies = [en]
    z = w._enemy_step_ok(en, 192, 192)
    assert z == 24.0, z
    print("  enemy steps onto platform OK")


def test_hitscan_and_projectile_3d():
    from server.game_world import GameWorld
    from server import weapons
    sm = _slab_map()  # slab top=64, under=48 at x 128..384
    w = GameWorld('pve', sm)
    # hitscan blocked by slab volume
    d_block = weapons._ray_wall_dist(w, 64, 256, 0.0, 500, z=56.0)
    assert d_block < 500, d_block
    # hitscan passes below slab (should reach outer wall, not slab)
    d_below = weapons._ray_wall_dist(w, 64, 256, 0.0, 500, z=20.0)
    assert d_below > 400, d_below
    # hitscan passes above slab
    d_above = weapons._ray_wall_dist(w, 64, 256, 0.0, 500, z=105.0)
    assert d_above > 400, d_above
    # actor on platform can be hit
    hit = weapons._ray_circle_hit(64, 256, 0.0, 256, 256, 24.0, 500,
                                  ray_z=105.0, target_z=64.0, target_h=56.0)
    assert hit is not None, hit
    # actor below slab not hit by shot above slab volume
    miss = weapons._ray_circle_hit(64, 256, 0.0, 256, 256, 24.0, 500,
                                   ray_z=105.0, target_z=0.0, target_h=56.0)
    assert miss is None, miss
    # projectile passes through 2D opening above/below slab when z gap exists
    w.bullets = [{'x': 64, 'y': 256, 'z': 20.0, 'vx': 100, 'vy': 0,
                  'dmg': 10, 'dist': 0, 'max_dist': 1000, 'col': (255,0,0),
                  'src': -1, 'from_player': True}]
    w._update_bullets()
    assert len(w.bullets) == 1
    w.bullets = [{'x': 64, 'y': 256, 'z': 105.0, 'vx': 100, 'vy': 0,
                  'dmg': 10, 'dist': 0, 'max_dist': 1000, 'col': (255,0,0),
                  'src': -1, 'from_player': True}]
    w._update_bullets()
    assert len(w.bullets) == 1
    print("  hitscan/projectile 3d OK")


def main():
    test_stacked_floating_valid()
    test_overlapping_contact_valid()
    test_thin_slab_invalid()
    test_thing_without_ordinary_support_invalid()
    test_support_at_stacked_slabs()
    test_text_map_round_trip_floating()
    test_3d_los_slab()
    test_projectile_slab_block()
    test_enemy_steps_onto_platform()
    test_hitscan_and_projectile_3d()
    print("smoke_floating_unrestricted: ALL OK")


if __name__ == "__main__":
    main()
