"""Smoke test for the dash feedback (OpenSpec change `dash-fx`).

Covers the three things the feature is made of:
  1. the effect pools actually age (a dash trail must not eat the pool forever),
  2. burst / dust trail are driven by the *rising edge* of the authoritative
     ``state == 3`` and by distance covered, not by frames,
  3. the screen speed lines appear for the local dash only, sit under the HUD
     and fade out completely.

Run: py -3.13 smoke_dash_fx.py
"""
import os
import sys

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

import pygame

AMMO = {'pstl': 10, 'shl': 10, 'slg': 10, 'rck': 10, 'BFG': 10}
MAP = [[1] * 16] + [[1] + [0] * 14 + [1] for _ in range(14)] + [[1] * 16]
DASH = 3


def _reset():
    import engine.game_state as gs
    gs.particles.clear()
    gs.dash_fx = 0.0


def _new_bridge():
    from client.bridge import RenderBridge
    b = RenderBridge(pygame.Surface((320, 240)))
    b.set_map(MAP, 16)
    return b


def _update(b, px=500.0, py=500.0, players=(), impacts=(), local_state=0,
            health=100, fire=0):
    """Publish one frame of state *without* rendering, so a test can look at
    the effect pool right after a spawn and before the TTL step."""
    b.update(px, py, 0.0, health, list(players), [], [], [], 1, AMMO,
             fire, 'pve', list(impacts), local_state)


def _frame(b, px=500.0, py=500.0, players=(), impacts=(), local_state=0,
           health=100, fire=0):
    _update(b, px, py, players, impacts, local_state, health, fire)
    b.render()


def test_pools_age():
    """1. Particles/weather step their TTL every frame, so the pool frees up."""
    import engine.game_state as gs
    from engine.constants import MAX_PARTICLES
    b = _new_bridge()
    _reset()

    # Far more impacts than a single particle's lifetime can hold: with a stuck
    # pool this saturates at MAX_PARTICLES and stays there for the session.
    seen_max = 0
    for i in range(600):
        _frame(b, impacts=[{'id': i, 'x': 540.0, 'y': 500.0, 'type': 'wall'}])
        seen_max = max(seen_max, len(gs.particles))
    assert seen_max < MAX_PARTICLES, (
        f'pool saturated at {seen_max} == MAX_PARTICLES: effects never age')

    # The pool must not be left full: a fresh impact still has room to spawn.
    # Compared without a render step in between, otherwise the TTL step of that
    # very frame hides the new particles behind the dying ones.
    before = len(gs.particles)
    _update(b, impacts=[{'id': 10000, 'x': 540.0, 'y': 500.0, 'type': 'wall'}])
    assert len(gs.particles) > before, (
        f'no new effect after 600 impacts (pool stuck at {before})')
    print(f'1. pools age: steady state {before}, peak {seen_max} of '
          f'{MAX_PARTICLES}, still spawning OK')


def test_burst_only_on_rising_edge():
    """2. One burst per dash, not one per frame."""
    import engine.game_state as gs
    from engine.constants import DASH_FX_BURST
    b = _new_bridge()
    _reset()
    p = {'id': 1, 'x': 500.0, 'y': 500.0, 'angle': 0.0, 'state': DASH}

    _update(b, players=[p])
    first = len(gs.particles)
    assert first == DASH_FX_BURST, f'no burst on the rising edge: {first}'
    for _ in range(10):          # still dashing, nothing moves
        _update(b, players=[p])
    assert len(gs.particles) == first, (
        f'burst repeated while state stayed {DASH}: {len(gs.particles)}')
    print('2. burst fires once per dash OK')


def test_trail_follows_distance():
    """3. Trail is emitted per DASH_FX_TRAIL_PX travelled, not per frame."""
    import engine.game_state as gs
    from engine import constants as const
    b = _new_bridge()
    _reset()
    p = {'id': 1, 'x': 500.0, 'y': 500.0, 'angle': 0.0, 'state': DASH}
    _update(b, players=[p])
    base = len(gs.particles)

    # Dashing into a wall: the position does not move, so no dust.
    for _ in range(5):
        _update(b, players=[p])
    assert len(gs.particles) == base, 'dust spawned while not moving'

    # Move by less than one trail step: still nothing.
    p = dict(p, x=500.0 + const.DASH_FX_TRAIL_PX * 0.5)
    _update(b, players=[p])
    assert len(gs.particles) == base, 'dust spawned before a full trail step'

    # Cross the step: one emission of DASH_FX_TRAIL puffs.
    p = dict(p, x=500.0 + const.DASH_FX_TRAIL_PX * 1.5)
    _update(b, players=[p])
    assert len(gs.particles) == base + const.DASH_FX_TRAIL, len(gs.particles)

    # Frame rate independence: the same distance in one step or in many must
    # produce the same number of emissions.
    for label, steps in (('one step', 1), ('many steps', 20)):
        _reset()
        b2 = _new_bridge()
        q = {'id': 1, 'x': 500.0, 'y': 500.0, 'angle': 0.0, 'state': DASH}
        _update(b2, players=[q])
        start = len(gs.particles)
        x = 500.0
        for _s in range(steps):
            x += const.DASH_FX_TRAIL_PX
            _update(b2, players=[dict(q, x=x)])
        got = (len(gs.particles) - start) // const.DASH_FX_TRAIL
        assert got == steps, f'{label}: {got} emissions for {steps} steps'
    print('3. trail follows distance, FPS independent OK')


def test_trail_stops_with_dash():
    """4. Falling edge clears the slot, so the next dash bursts again."""
    import engine.game_state as gs
    b = _new_bridge()
    _reset()
    dashing = {'id': 1, 'x': 500.0, 'y': 500.0, 'angle': 0.0, 'state': DASH}
    idle = dict(dashing, state=0)

    _update(b, players=[dashing])
    assert 1 in b._dash_active, 'no dash bookkeeping'
    _update(b, players=[idle])
    assert 1 not in b._dash_active, 'slot kept after the dash ended'
    assert 1 not in b._dash_px and 1 not in b._dash_pos, 'stale trail state'
    # The burst of the finished dash is still in the pool (no TTL step here),
    # but nothing new may be added while the player is not dashing.
    left = len(gs.particles)
    _update(b, players=[idle])
    assert len(gs.particles) == left, 'effects spawned while not dashing'
    _update(b, players=[dashing])
    assert len(gs.particles) > left, 'second dash produced no burst'
    print('4. trail stops with the dash, next dash bursts again OK')


def test_refused_dash_is_silent():
    """5. state 0 with no prior dash (server refused) spawns nothing."""
    import engine.game_state as gs
    b = _new_bridge()
    _reset()
    p = {'id': 1, 'x': 500.0, 'y': 500.0, 'angle': 0.0, 'state': 0}
    for i in range(10):
        _update(b, px=500.0 + i * 8, players=[p], local_state=0)
    assert len(gs.particles) == 0, f'{len(gs.particles)} particles without a dash'
    assert gs.dash_fx == 0.0, gs.dash_fx
    print('5. refused dash is silent OK')


def test_trail_ignores_blood_amount():
    """6. The dash cue is not gore: blood_amount 0 must not silence it."""
    import engine.game_state as gs
    b = _new_bridge()
    _reset()
    gs.blood_amount = 0.0
    p = {'id': 1, 'x': 500.0, 'y': 500.0, 'angle': 0.0, 'state': DASH}
    _update(b, players=[p])
    x = 500.0
    for _ in range(4):
        x += 40
        _update(b, players=[dict(p, x=x)])
    assert len(gs.particles) > 0, 'blood_amount=0 silenced the dash trail'
    gs.blood_amount = 1.0
    print('6. trail independent of blood_amount OK')


def test_particle_cap():
    """7. Dash effects obey the quality-scaled particle cap."""
    import engine.game_state as gs
    from engine.constants import MAX_PARTICLES
    b = _new_bridge()
    _reset()
    p = {'id': 1, 'x': 500.0, 'y': 500.0, 'angle': 0.0, 'state': DASH}
    x = 500.0
    for _ in range(200):
        x += 60
        _update(b, players=[dict(p, x=x)])
    assert len(gs.particles) <= MAX_PARTICLES, len(gs.particles)
    # And the scaled cap is respected as well. Start from an empty pool: the cap
    # is enforced when spawning, existing particles are not culled by it.
    _reset()
    gs.effects_factor = 0.1
    scaled = int(MAX_PARTICLES * 0.1)
    x = 500.0
    for _ in range(50):
        x += 60
        _update(b, players=[dict(p, x=x)])
    assert len(gs.particles) <= scaled, f'{len(gs.particles)} > {scaled}'
    assert len(gs.particles) > 0, 'scaled cap silenced the dash entirely'
    gs.effects_factor = 1.0
    print('7. particle cap respected (absolute and scaled) OK')


def test_player_colors():
    """8. Remote dashes use the shirt colour, own dash uses the dash colour."""
    import engine.game_state as gs
    from client.bridge import PLAYER_COLORS
    from engine import constants as const
    b = _new_bridge()
    _reset()
    pid = 3
    p = {'id': pid, 'x': 500.0, 'y': 500.0, 'angle': 0.0, 'state': DASH}
    _update(b, players=[p])
    cols = {tuple(pt['col']) for pt in gs.particles}
    assert cols == {PLAYER_COLORS[pid % len(PLAYER_COLORS)]}, cols

    _reset()
    _update(b, local_state=DASH)
    cols = {tuple(pt['col']) for pt in gs.particles}
    assert cols == {const.DASH_FX_COLOR}, cols
    print('8. per-player dash colour OK')


def test_screen_streaks():
    """9. Speed lines: own dash only, viewport-bounded, fading to nothing.

    Driven straight through ``draw_dash_streaks`` on a blank framebuffer: a full
    ``render()`` would mix animated textures and the world itself into the
    comparison, which has nothing to do with the overlay.
    """
    import engine.game_state as gs
    import engine.rendering as rendering
    from engine import constants as const
    b = _new_bridge()
    _reset()
    rh, ry = 100, 50
    w, h = const.W, const.H

    def lit_pixels():
        surf = rendering._streak_surf
        if surf is None:
            return -1
        return sum(1 for y in range(0, surf.get_height(), 2)
                   for x in range(0, surf.get_width(), 4)
                   if surf.get_at((x, y))[3] > 0)

    # At rest: nothing is drawn, and the overlay surface is not even allocated.
    gs.screen.fill((10, 20, 30))
    idle = pygame.image.tostring(gs.screen, 'RGB')
    rendering.draw_dash_streaks(rh, ry)
    assert pygame.image.tostring(gs.screen, 'RGB') == idle, 'drew at rest'
    assert rendering._streak_surf is None, 'overlay allocated while idle'

    # Dashing: the frame changes, and the envelope decays per call.
    gs.dash_fx = 1.0
    rendering.draw_dash_streaks(rh, ry)
    dashed = pygame.image.tostring(gs.screen, 'RGB')
    assert dashed != idle, 'own dash painted nothing'
    assert gs.dash_fx == 1.0 - const.DASH_FX_FADE, gs.dash_fx
    assert lit_pixels() > 0, 'streak surface is empty while dashing'

    # Only the viewport rows change: the HUD band above and below stays intact.
    def row(y):
        return pygame.image.tostring(gs.screen, 'RGB')[y * w * 3:(y + 1) * w * 3]
    top_same = all(row(y) == idle[y * w * 3:(y + 1) * w * 3] for y in range(ry))
    bottom_same = all(row(y) == idle[y * w * 3:(y + 1) * w * 3]
                      for y in range(ry + rh, h))
    assert top_same, 'streaks painted over the HUD band above the view'
    assert bottom_same, 'streaks painted over the HUD band below the view'

    # Decays to nothing within a dash-length window and then stops costing.
    frames = 1
    while gs.dash_fx > 0.0 and frames < 240:
        rendering.draw_dash_streaks(rh, ry)
        frames += 1
    assert gs.dash_fx == 0.0, gs.dash_fx
    assert frames < 60, f'envelope took {frames} frames, longer than a dash'
    gs.screen.fill((10, 20, 30))
    rendering.draw_dash_streaks(rh, ry)
    assert pygame.image.tostring(gs.screen, 'RGB') == idle, 'still drawing after fade'
    print(f'9. screen streaks: viewport-bounded, fade out in {frames} frames OK')


def test_streaks_armed_only_by_own_dash():
    """10. Only the local player's dash arms the overlay, and it survives the
    lowest quality setting."""
    import engine.game_state as gs
    import engine.rendering as rendering
    from engine import constants as const
    b = _new_bridge()
    _reset()
    # The overlay surface is module-level and cached between tests; drop it so
    # "was it ever allocated" means something here.
    rendering._streak_surf = None

    # A remote dash spawns world dust but must not touch the screen overlay.
    p = {'id': 1, 'x': 500.0, 'y': 500.0, 'angle': 0.0, 'state': DASH}
    _frame(b, players=[p])
    assert gs.dash_fx == 0.0, 'remote dash armed the own-dash overlay'
    assert rendering._streak_surf is None, 'remote dash allocated the overlay'

    # Own dash arms it, even with effects at the floor: this is the only
    # screen-space signal that the dash happened.
    _reset()
    gs.effects_factor = 0.0
    _frame(b, local_state=DASH)
    assert gs.dash_fx > 0.0, 'own dash did not arm the overlay'
    surf = rendering._streak_surf
    lit = sum(1 for y in range(0, surf.get_height(), 2)
              for x in range(0, surf.get_width(), 4)
              if surf.get_at((x, y))[3] > 0)
    assert lit > 0, 'no streak drawn at effects_factor 0'
    gs.effects_factor = 1.0
    print('10. streaks: remote dash draws none, own dash always draws some OK')


def main():
    pygame.init()
    pygame.display.set_mode((320, 240))
    try:
        test_pools_age()
        test_burst_only_on_rising_edge()
        test_trail_follows_distance()
        test_trail_stops_with_dash()
        test_refused_dash_is_silent()
        test_trail_ignores_blood_amount()
        test_particle_cap()
        test_player_colors()
        test_screen_streaks()
        test_streaks_armed_only_by_own_dash()
    finally:
        pygame.quit()
    print('\nsmoke_dash_fx: all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
