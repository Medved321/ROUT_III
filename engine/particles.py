import math, random, bisect, pygame
from engine.constants import (TILE, BLOCK_TILES, MAX_PARTICLES, MAX_DECALS, MAX_WEATHER,
    PARTICLE_SIZE_MIN, PARTICLE_SIZE_MAX, PARTICLE_FRICTION, PARTICLE_GRAVITY,
    SMOKE_FLOAT, SMOKE_DRAG, SPARK_SPEED_MIN_FACTOR,
    DECAL_LIFETIME, SCORCH_LIFETIME, BLOOD_LIFETIME, DECAL_OCCLUSION_INTERVAL,
    DASH_FX_BURST, DASH_FX_TRAIL, CIRCLE_MAX_R,
    CIRCLE_RADIUS_STEP, CIRCLE_CACHE_PX)
from engine import constants as const
from engine import game_state as gs
from engine.framebuffer import depth_col
from engine.viewport import project_point


# Blend surfaces for smoke and scorch, keyed by (radius, colour).  Bounded by
# entry count *and* by total pixels: a projected effect radius is unbounded in
# principle, so a count cap alone is not a memory bound.
_SMOKE_SURFACES = {}
_SCORCH_SURFACES = {}
_SURFACE_CACHE_MAX = 64

# Radius ladder for the cached circles (effect-surface-budget).  Built once:
# radius changes every frame as an effect moves, and an exact radius would make
# the cache miss constantly and re-allocate nearly every entry per frame.
_RADIUS_LADDER = []
_ladder_r = 1
while _ladder_r < CIRCLE_MAX_R:
    _RADIUS_LADDER.append(int(_ladder_r))
    _ladder_r *= CIRCLE_RADIUS_STEP
_RADIUS_LADDER.append(CIRCLE_MAX_R)

# Frame counter used to throttle the per-decal wall-occlusion ray march.
_decal_occlusion_tick = 0


def _circle_radius(r):
    """Nearest step of the radius ladder, clamped to the surface cap.

    Both ends matter: the top because anything bigger is drawn as a primitive
    (see ``_draw_soft_circle``), the bottom because radius 0 has no circle.
    """
    r = int(r)
    if r <= _RADIUS_LADDER[0]:
        return _RADIUS_LADDER[0]
    if r >= _RADIUS_LADDER[-1]:
        return _RADIUS_LADDER[-1]
    i = bisect.bisect_left(_RADIUS_LADDER, r)
    lo, hi = _RADIUS_LADDER[i - 1], _RADIUS_LADDER[i]
    return lo if r - lo <= hi - r else hi


def _cached_circle_surface(cache, size, col):
    key = (_circle_radius(size), tuple(col))
    surf = cache.get(key)
    if surf is None:
        px = key[0] * key[0] * 4
        # Measured from the entries themselves rather than from a counter: a
        # counter can drift out of sync with reality, this cannot.
        cached = sum(s.get_width() * s.get_height() for s in cache.values())
        if len(cache) >= _SURFACE_CACHE_MAX or cached + px > CIRCLE_CACHE_PX:
            cache.clear()
        r = key[0]
        surf = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
        pygame.draw.circle(surf, (*key[1], 255), (r, r), r)
        cache[key] = surf
    return surf


def _draw_soft_circle(cache, cx, cy, r, col, alpha):
    """Alpha-blended filled circle, centred on (cx, cy).

    A cached blend surface while the radius fits the cache, a direct primitive
    once it does not.  The primitive path exists because SDL clips draws to the
    screen and allocates nothing, while a surface cannot be sized from a
    projected radius that reaches tens of thousands of pixels when an effect
    ends up inside the camera.  Its alpha is folded into the colour exactly like
    the opaque-particle branch does, which is imperceptible on a circle that
    already covers most of the frame.
    """
    if r > CIRCLE_MAX_R:
        k = alpha / 255.0
        pygame.draw.circle(gs.screen, tuple(int(c * k) for c in col[:3]), (cx, cy), r)
        return
    s = _cached_circle_surface(cache, r, col)
    s.set_alpha(alpha)
    off = s.get_width() // 2
    gs.screen.blit(s, (cx - off, cy - off))


def _effect_limit(base):
    """Effect budget for the current adaptive quality (scales down under load)."""
    return max(8, int(base * gs.effects_factor))


def spawn_particles(x, y, col, count, spd, life):
    count = max(0, int(count * gs.blood_amount))
    limit = _effect_limit(MAX_PARTICLES)
    if len(gs.particles) >= limit:
        return
    remaining = limit - len(gs.particles)
    count = min(count, remaining)
    for _ in range(count):
        a = random.random() * math.pi * 2
        s = random.random() * spd
        gs.particles.append({'x': x, 'y': y, 'vx': math.cos(a) * s, 'vy': math.sin(a) * s,
                     'life': random.randint(life // 2, life), 'max_life': life,
                     'col': col, 'size': random.uniform(PARTICLE_SIZE_MIN, PARTICLE_SIZE_MAX),
                             'z': random.uniform(3, 10)})

def spawn_gib(x, y, col, count=5, force=4):
    count = max(0, int(count * gs.blood_amount))
    limit = _effect_limit(MAX_PARTICLES)
    if len(gs.particles) >= limit:
        return
    count = min(count, limit - len(gs.particles))
    for _ in range(count):
        a = random.random() * math.pi * 2
        s = random.uniform(force * 0.5, force)
        gs.particles.append({'x': x, 'y': y, 'vx': math.cos(a) * s, 'vy': math.sin(a) * s,
                             'life': random.randint(30, 60), 'max_life': 60,
                             'col': col, 'size': random.uniform(4, 10),
                             'z': random.uniform(5, 15), 'zv': random.uniform(2, 6),
                             'gib': True})


def spawn_explosion(x, y):
    spawn_particles(x, y, (255, 200, 50), 30, 5, 25)
    spawn_particles(x, y, (255, 80, 0), 20, 3, 15)
    spawn_particles(x, y, (200, 200, 200), 10, 2, 10)
    # Smoke cloud
    limit = _effect_limit(MAX_PARTICLES)
    for _ in range(8):
        if len(gs.particles) >= limit:
            break
        a = random.random() * math.pi * 2
        s = random.uniform(0.5, 2)
        gs.particles.append({'x': x, 'y': y, 'vx': math.cos(a) * s, 'vy': math.sin(a) * s,
                             'life': random.randint(30, 50), 'max_life': 50,
                             'col': (180, 180, 180), 'size': random.uniform(6, 14),
                             'z': random.uniform(8, 20), 'zv': random.uniform(1, 3),
                             'smoke': True})


def spawn_sparks(x, y, count=8, spd=4):
    limit = _effect_limit(MAX_PARTICLES)
    for _ in range(count):
        if len(gs.particles) >= limit:
            break
        a = random.random() * math.pi * 2
        s = random.uniform(spd * SPARK_SPEED_MIN_FACTOR, spd)
        gs.particles.append({'x': x, 'y': y, 'vx': math.cos(a) * s, 'vy': math.sin(a) * s,
                             'life': random.randint(5, 15), 'max_life': 15,
                             'col': (255, 255, 200), 'size': random.uniform(1, 2.5),
                             'z': random.uniform(2, 6), 'zv': random.uniform(1, 3),
                             'spark': True})



def spawn_smoke_trail(x, y, col=(200, 200, 200)):
    """Smoke puff for projectile trails."""
    if len(gs.particles) >= _effect_limit(MAX_PARTICLES):
        return
    gs.particles.append({'x': x, 'y': y, 'vx': random.uniform(-0.3, 0.3), 'vy': random.uniform(-0.3, 0.3),
                         'life': random.randint(15, 30), 'max_life': 30,
                         'col': col, 'size': random.uniform(2, 5),
                         'z': random.uniform(3, 8), 'zv': random.uniform(0.2, 0.8),
                         'smoke': True})


def spawn_dash_burst(x, y, col=const.DASH_FX_COLOR):
    """Ground puff thrown out when a dash starts.

    A movement cue, not gore: the count is NOT scaled by gs.blood_amount, so
    switching blood amount off never silences the dash.
    """
    limit = _effect_limit(MAX_PARTICLES)
    if len(gs.particles) >= limit:
        return
    count = min(DASH_FX_BURST, limit - len(gs.particles))
    for _ in range(count):
        a = random.uniform(0, math.tau)
        s = random.uniform(0.8, 2.2)
        gs.particles.append({'x': x + random.uniform(-4, 4), 'y': y + random.uniform(-4, 4),
                             'vx': math.cos(a) * s, 'vy': math.sin(a) * s,
                             'life': random.randint(10, 18), 'max_life': 18,
                             'col': col, 'size': random.uniform(2, 5),
                             'z': random.uniform(1, 5), 'zv': random.uniform(0.1, 0.4),
                             'smoke': True})


def spawn_dash_puff(x, y, col, ang):
    """Trail puff behind a dashing body; `ang` is the travel direction, so the
    dust is thrown backwards, opposite to the motion. Emitted per DASH_FX_TRAIL_PX
    of distance covered, never per frame -- that keeps it FPS independent and
    silent when a dash runs into a wall."""
    limit = _effect_limit(MAX_PARTICLES)
    if len(gs.particles) >= limit:
        return
    count = min(DASH_FX_TRAIL, limit - len(gs.particles))
    back = ang + math.pi
    for _ in range(count):
        a = back + random.uniform(-0.6, 0.6)
        s = random.uniform(0.7, 1.7)
        gs.particles.append({'x': x + random.uniform(-5, 5), 'y': y + random.uniform(-5, 5),
                             'vx': math.cos(a) * s, 'vy': math.sin(a) * s,
                             'life': random.randint(8, 15), 'max_life': 15,
                             'col': col, 'size': random.uniform(3, 7),
                             'z': random.uniform(0, 3), 'zv': random.uniform(0.05, 0.2),
                             'smoke': True})


def add_bullet_hole(x, y, col=(60, 60, 60), pitch=None, hit_z=None):
    if len(gs.decals) >= _effect_limit(MAX_DECALS):
        return
    d = {'x': x, 'y': y, 'col': col, 'size': random.uniform(2, 4),
         'type': 'hole', 'life': DECAL_LIFETIME}
    if pitch is not None:
        d['pitch'] = pitch
    if hit_z is not None:
        d['hit_z'] = hit_z
    gs.decals.append(d)


def add_scorch(x, y, col=(40, 35, 30)):
    if len(gs.decals) >= _effect_limit(MAX_DECALS):
        return
    gs.decals.append({'x': x, 'y': y, 'col': col, 'size': random.uniform(8, 18),
                      'type': 'scorch', 'life': SCORCH_LIFETIME})


def add_blood_stain(x, y, col=(120, 10, 10)):
    if len(gs.decals) >= _effect_limit(MAX_DECALS):
        return
    gs.decals.append({'x': x, 'y': y, 'col': col, 'size': random.uniform(3, 8),
                      'type': 'blood', 'life': BLOOD_LIFETIME})


def spawn_weather(count=8):
    if len(gs.weather) >= _effect_limit(MAX_WEATHER):
        return
    count = min(count, _effect_limit(MAX_WEATHER) - len(gs.weather))
    if gs.weather_type == 'rain':
        for _ in range(count):
            gs.weather.append({'x': random.uniform(-50, const.W + 50), 'y': random.uniform(-50, 0),
                               'vx': random.uniform(-0.8, 0.8), 'vy': random.uniform(5, 10),
                               'life': random.randint(25, 50), 'col': (180, 200, 230), 'size': 1})
    elif gs.weather_type == 'snow':
        for _ in range(count):
            gs.weather.append({'x': random.uniform(-50, const.W + 50), 'y': random.uniform(-50, 0),
                               'vx': random.uniform(-0.5, 0.5), 'vy': random.uniform(0.5, 1.8),
                               'life': random.randint(50, 100), 'col': (230, 240, 255), 'size': random.uniform(1.5, 4)})


def update_weather():
    gs.weather[:] = [p for p in gs.weather if not (p['life'] <= 0 or p['y'] > const.H + 20)]
    for p in gs.weather:
        p['x'] += p['vx']
        p['y'] += p['vy']
        p['life'] -= 1


def update_decals():
    for d in gs.decals[:]:
        if d['life'] > 0:
            d['life'] -= 1
    gs.decals[:] = [d for d in gs.decals if d['life'] > 0]


def update_particles():
    for p in gs.particles[:]:
        p['x'] += p['vx']; p['y'] += p['vy']
        p['vx'] *= PARTICLE_FRICTION; p['vy'] *= PARTICLE_FRICTION
        zv = p.get('zv', 0)
        p['z'] = max(0, p['z'] + zv)
        p['zv'] = zv - PARTICLE_GRAVITY
        if p.get('smoke'):
            p['zv'] += SMOKE_FLOAT
            p['vx'] *= SMOKE_DRAG
            p['vy'] *= SMOKE_DRAG
        p['life'] -= 1
    gs.particles[:] = [p for p in gs.particles if p['life'] > 0]


def draw_particles(z_b, vh=None, vy=0):
    if vh is None:
        vh = const.H
    for p in gs.particles:
        if math.hypot(p['x'] - gs.px, p['y'] - gs.py) > gs.view_dist:
            continue
        proj = project_point(p['x'], p['y'], TILE, vh, vy)
        if proj is None:
            continue
        sx, sh, ground_y, perp = proj
        idx = depth_col(z_b, sx, const.W)
        # `perp` is the metric z_b holds (rd = d*cos(ra - pa)).  Testing the
        # radial distance instead made every off-axis effect fail against a
        # wall that does not actually cover it, so dust blinked out next to
        # walls and at the edges of the screen.
        if idx >= 0 and z_b[idx] > perp:
            sz = max(1, int(p['size'] * sh / 64))
            base_y = int(ground_y - p['z'] * sh / 64)
            alpha = int(255 * p['life'] / p['max_life'])
            c = tuple(min(255, int(c * alpha // 255)) for c in p['col'])
            if c[0] or c[1] or c[2]:
                if p.get('spark'):
                    sz = max(1, sz // 2)
                    pygame.draw.circle(gs.screen, (255, 255, 255), (sx, base_y), sz)
                    pygame.draw.circle(gs.screen, (255, 255, 200, 0), (sx, base_y), sz + 1, 1)
                elif p.get('smoke'):
                    sz = int(sz * (1 + 0.5 * (1 - p['life'] / p['max_life'])))
                    alpha_smoke = int(120 * p['life'] / p['max_life'])
                    if alpha_smoke > 2:
                        _draw_soft_circle(_SMOKE_SURFACES, sx, base_y, sz, p['col'], alpha_smoke)
                else:
                    pygame.draw.circle(gs.screen, c, (sx, base_y), sz)


def draw_weather():
    for p in gs.weather:
        if gs.weather_type == 'rain':
            alpha = min(255, int(220 * p['life'] / 40))
            c = (*p['col'], alpha)
            end_x = int(p['x'] - p['vx'] * 5)
            end_y = int(p['y'] - p['vy'] * 5)
            if alpha > 100:
                pygame.draw.line(gs.screen, (255, 255, 255, alpha), (int(p['x']), int(p['y'])), (end_x, end_y), 2)
                pygame.draw.line(gs.screen, c[:3], (int(p['x'])-1, int(p['y'])), (end_x-1, end_y), 1)
            else:
                pygame.draw.line(gs.screen, c[:3], (int(p['x']), int(p['y'])), (end_x, end_y), 1)
        elif gs.weather_type == 'snow':
            alpha = min(255, int(220 * p['life'] / 80))
            sz = p['size'] * 1.5
            pygame.draw.circle(gs.screen, (255, 255, 255, alpha), (int(p['x']), int(p['y'])), int(sz))
            if sz > 2:
                pygame.draw.circle(gs.screen, (200, 220, 255, alpha//2), (int(p['x'])-1, int(p['y'])-1), int(sz)-1)


def draw_decals(vh=None, vy=0):
    # `vh` defaults to None, not to const.H: a default argument is evaluated at
    # import time, and const.H is rewritten on every window resize.
    if vh is None:
        vh = const.H
    global _decal_occlusion_tick
    _decal_occlusion_tick += 1
    for d in gs.decals:
        edx, edy = d['x'] - gs.px, d['y'] - gs.py
        dst = math.sqrt(edx ** 2 + edy ** 2)
        if dst < 2 or dst > gs.view_dist: continue
        # Wall occlusion is an expensive ray march; recompute it at most every
        # DECAL_OCCLUSION_INTERVAL frames and cache the result on the decal.
        if _decal_occlusion_tick - d.get('_occ_tick', -(1 << 30)) >= DECAL_OCCLUSION_INTERVAL:
            d['_occ_tick'] = _decal_occlusion_tick
            occluded = False
            step = 8
            for dist in range(16, max(16, int(dst) - 4), step):
                cx = gs.px + (edx / dst) * dist
                cy = gs.py + (edy / dst) * dist
                tx, ty = int(cx // TILE), int(cy // TILE)
                if 0 <= ty < gs.M_SZ and 0 <= tx < gs.M_SZ:
                    if gs.MAP[ty][tx] in BLOCK_TILES:
                        occluded = True
                        break
            d['_occ'] = occluded
        else:
            occluded = d.get('_occ', False)
        if occluded: continue
        proj = project_point(d['x'], d['y'], TILE, vh, vy)
        if proj is None: continue
        sx, sh, ground_y, _perp = proj
        sz = max(1, int(d['size'] * sh / 64))
        # World Z of the decal itself: a bullet hole sits where the shot landed,
        # scorch and blood sit on the floor.
        if d['type'] == 'hole':
            wz = d.get('hit_z', TILE)
        else:
            wz = 0
        cy = int(ground_y - wz * sh / 64)
        if d['type'] == 'hole':
            if 'pitch' in d:
                cy += int(d['pitch'] * sh)
            pygame.draw.circle(gs.screen, d['col'], (sx, cy), max(1, sz))
        elif d['type'] == 'scorch':
            cy += sh // 2
            _draw_soft_circle(_SCORCH_SURFACES, sx, cy, max(1, sz), d['col'], 100)
        elif d['type'] == 'blood':
            cy += sh // 2
            pygame.draw.ellipse(gs.screen, d['col'], (sx - sz, cy - sz // 2, sz * 2, sz))
