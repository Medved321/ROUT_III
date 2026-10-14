"""Smoke test for the dash feedback (OpenSpec change `dash-fx`).

Covers the three things the feature is made of:
  1. the effect pools actually age (a dash trail must not eat the pool forever),
  2. burst / dust trail are driven by the *rising edge* of the authoritative
     ``state == 3`` and by distance covered, not by frames,
  3. the screen speed lines appear for the local dash only, sit under the HUD
     and fade out completely.

Run: py -3.13 smoke_dash_fx.py
"""
import math
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
             fire, 'pve', list(impacts), local_state=local_state)


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
    """9. Speed rays: own dash only, aimed at the centre, fading to nothing.

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
        field = rendering._ray_field
        if field is None:
            return -1
        return sum(1 for ray in field
                   for y in range(0, ray.spr.get_height())
                   for x in range(0, ray.spr.get_width(), 2)
                   if ray.spr.get_at((x, y))[3] > 0)

    # At rest: nothing is drawn, and not even the ray sprites are baked.
    gs.screen.fill((10, 20, 30))
    idle = pygame.image.tostring(gs.screen, 'RGB')
    rendering.draw_dash_streaks(rh, ry)
    assert pygame.image.tostring(gs.screen, 'RGB') == idle, 'drew at rest'
    assert rendering._ray_field is None, 'ray field baked while idle'

    # Dashing: the frame changes, and the envelope decays per call.
    gs.dash_fx = 1.0
    rendering.draw_dash_streaks(rh, ry)
    dashed = pygame.image.tostring(gs.screen, 'RGB')
    assert dashed != idle, 'own dash painted nothing'
    assert gs.dash_fx == 1.0 - const.DASH_FX_FADE, gs.dash_fx
    assert lit_pixels() > 0, 'ray field is empty while dashing'

    # Every wedge is a triangle pointing at the aim point: transparent at the apex
    # and at the border end, brightest in the middle stretch, and feathered across
    # its width. Solid rects with hard ends are what this replaces -- they are what
    # made the cue read as tearing.
    #
    # Read the profile off the TEMPLATE, not off the baked field sprites. Every
    # field sprite is turned to its own angle, so "along the length" and "across
    # the width" are not screen axes anymore and reading them off a rotated sprite
    # would only measure the rotation. The template is the single unturned shape
    # all of them are scaled from, and the scaling cannot turn a clear alpha at an
    # end into a hard edge.
    tpl = rendering._ray_template()
    ln, th = tpl.get_size()
    mid_c = th // 2

    def along(t):
        return tpl.get_at((t, mid_c))[3]

    # Both ends of the length reach zero in the template itself. The criterion is
    # still relative: an end may only be a sliver of the peak, because averaging
    # the fade-out tail into the last pixel is expected, a hard end is not.
    samples = [along((ln - 1) * k // 15) for k in range(16)]
    peak = max(samples)
    assert peak > 8, f'wedge is flat at {peak}, not a gradient'
    assert samples[0] * 6 <= peak, f'wedge starts on a hard edge ({samples[0]}/{peak})'
    assert samples[-1] * 6 <= peak, f'wedge ends on a hard edge ({samples[-1]}/{peak})'
    # The brightest stretch is in the middle, not pinned to one end: a wedge that
    # peaks at its base would read as a solid bar with a fade, not a triangle.
    assert samples.index(peak) > 2 and samples.index(peak) < len(samples) - 3, (
        f'wedge peaks at {samples.index(peak)} of {len(samples) - 1}, not in the middle')
    if th >= 3:
        # The wedge widens towards the border, so the column at the peak is the
        # widest one and the only fair place to read the side profile.
        col = int(ln * 0.5)
        mid = along(col)
        top = tpl.get_at((col, 0))[3]
        bot = tpl.get_at((col, th - 1))[3]
        assert max(top, bot) * 2 < mid * 3, (
            f'wedge has hard side edges: {top}/{bot} vs {mid}')
    # And it is WHITE, which is the whole difference between a speed cue and the
    # blue hit-flash this replaced.
    lit_cols = [tpl.get_at((x, mid_c)) for x in range(ln)
                if tpl.get_at((x, mid_c))[3] > 32]
    assert lit_cols, 'template has no lit centre line'
    for px in lit_cols:
        r, g, b, _a = px
        assert r > 200 and abs(r - g) < 12 and abs(g - b) < 12, (
            f'wedge is not white: {px}')
    assert const.DASH_FX_STREAK_RGB == (255, 255, 255), const.DASH_FX_STREAK_RGB

    # The field is a full circle, not four bars on the screen axes. Every quadrant
    # has to carry ink, and the gaps BETWEEN wedges have to stay small, or the cue
    # reads as a "+" or a star of spokes glued to the edges of the view. Counted
    # as the largest angular hole between neighbours, because "every quadrant has
    # something in it" passes for a field with one wedge in three of them.
    field = rendering._ray_field
    assert len(field) == const.DASH_FX_STREAKS, len(field)
    quads = {0: 0, 1: 0, 2: 0, 3: 0}
    angs = sorted(math.degrees(math.atan2(r.uy, r.ux)) % 360.0 for r in field)
    for r in field:
        quads[(0 if r.ux >= 0 else 1) | (2 if r.uy >= 0 else 0)] += 1
    assert all(v > 0 for v in quads.values()), f'quadrants {quads}'
    assert len(quads) == 4
    gaps = [angs[0] + 360.0 - angs[-1]] + [b - a for a, b in zip(angs, angs[1:])]
    # One ring is 360/32 = 11.25 deg apart; the jitter in the table is a share of
    # that, so the widest hole must stay under a full step and some.
    assert max(gaps) < 360.0 / const.DASH_FX_STREAK_RING * 1.5, max(gaps)
    # The rings must be at different radii, or "three rings" is one ring drawn
    # three times and the field has no depth.
    rings = {round(r.r0 / 8.0) for r in field}
    assert len(rings) >= len(rendering._RAY_RING_OFF), sorted(rings)

    # Area budget. A turned sprite is blitted as its axis-aligned bounding box and
    # SDL blends every pixel of that box, empty ones included -- which for a long
    # thin triangle is a couple of dozen times the ink. The first full-radius
    # version measured 87 % of a 4K frame in boxes to paint 3.6 % of it and cost
    # 13 ms a frame. This is the guard that keeps the length from creeping back.
    box = sum(r.spr.get_width() * r.spr.get_height() for r in field)
    assert box < w * h * 0.06, f'wedges cover {box / (w * h) * 100:.1f}% of the frame'

    # Nothing may reach the aim point: the crosshair sits on top of it and a ray
    # through the middle is what makes the cue unreadable exactly when it fires.
    cx, cy = w * 0.5, ry + rh * 0.5
    for y in range(int(cy) - 6, int(cy) + 7):
        for x in range(int(cx) - 6, int(cx) + 7):
            assert gs.screen.get_at((x, y))[:3] == (10, 20, 30), (
                f'ray covers the aim point at {x},{y}')

    # The rays sweep outward instead of sitting still. The envelope has to be
    # frozen for the comparison, or the fade alone would move the ink and prove
    # nothing.
    fade = const.DASH_FX_FADE
    const.DASH_FX_FADE = 0.0
    try:
        gs.dash_fx = 1.0
        rendering.draw_dash_streaks(rh, ry)
        held = pygame.image.tostring(gs.screen, 'RGB')
        rendering.draw_dash_streaks(rh, ry)
        assert pygame.image.tostring(gs.screen, 'RGB') != held, 'rays do not move'
    finally:
        const.DASH_FX_FADE = fade
        gs.dash_fx = 1.0 - const.DASH_FX_FADE

    # Only the viewport rows change: the HUD band above and below stays intact.
    def row(y):
        return pygame.image.tostring(gs.screen, 'RGB')[y * w * 3:(y + 1) * w * 3]
    top_same = all(row(y) == idle[y * w * 3:(y + 1) * w * 3] for y in range(ry))
    bottom_same = all(row(y) == idle[y * w * 3:(y + 1) * w * 3]
                      for y in range(ry + rh, h))
    assert top_same, 'rays painted over the HUD band above the view'
    assert bottom_same, 'rays painted over the HUD band below the view'

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
    print(f'9. screen rays: viewport-bounded, fade out in {frames} frames OK')


def test_streaks_armed_only_by_own_dash():
    """10. Only the local player's dash arms the overlay, and it survives the
    lowest quality setting."""
    import engine.game_state as gs
    import engine.rendering as rendering
    from engine import constants as const
    b = _new_bridge()
    _reset()
    # The ray field is module-level and cached between tests; drop it so
    # "was it ever baked" means something here.
    rendering._ray_field = None

    # A remote dash spawns world dust but must not touch the screen overlay.
    p = {'id': 1, 'x': 500.0, 'y': 500.0, 'angle': 0.0, 'state': DASH}
    _frame(b, players=[p])
    assert gs.dash_fx == 0.0, 'remote dash armed the own-dash overlay'
    assert rendering._ray_field is None, 'remote dash baked the ray field'

    # Own dash arms it, even with effects at the floor: this is the only
    # screen-space signal that the dash happened.
    _reset()
    gs.effects_factor = 0.0
    _frame(b, local_state=DASH)
    assert gs.dash_fx > 0.0, 'own dash did not arm the overlay'
    field = rendering._ray_field
    lit = sum(1 for r in field
              for y in range(0, r.spr.get_height())
              for x in range(0, r.spr.get_width(), 2)
              if r.spr.get_at((x, y))[3] > 0)
    assert lit > 0, 'no ray drawn at effects_factor 0'
    assert len(field) == const.DASH_FX_STREAKS_MIN, (
        f'{len(field)} rays at effects_factor 0')
    gs.effects_factor = 1.0
    print('10. rays: remote dash draws none, own dash always draws some OK')


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
