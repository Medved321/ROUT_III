import math, random, pygame
from client.engine.constants import (TILE, BLOCK_TILES, MAX_PARTICLES, MAX_DECALS, MAX_WEATHER, EYE_H,
    PARTICLE_SIZE_MIN, PARTICLE_SIZE_MAX, PARTICLE_FRICTION, PARTICLE_GRAVITY,
    SMOKE_FLOAT, SMOKE_DRAG, SPARK_SPEED_MIN_FACTOR,
    DECAL_LIFETIME, SCORCH_LIFETIME, BLOOD_LIFETIME)
from client.engine import constants as const
from client.engine import game_state as gs


def spawn_particles(x, y, col, count, spd, life):
    count = max(0, int(count * gs.blood_amount))
    if len(gs.particles) >= MAX_PARTICLES:
        return
    remaining = MAX_PARTICLES - len(gs.particles)
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
    if len(gs.particles) >= MAX_PARTICLES:
        return
    count = min(count, MAX_PARTICLES - len(gs.particles))
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
    for _ in range(8):
        a = random.random() * math.pi * 2
        s = random.uniform(0.5, 2)
        gs.particles.append({'x': x, 'y': y, 'vx': math.cos(a) * s, 'vy': math.sin(a) * s,
                             'life': random.randint(30, 50), 'max_life': 50,
                             'col': (180, 180, 180), 'size': random.uniform(6, 14),
                             'z': random.uniform(8, 20), 'zv': random.uniform(1, 3),
                             'smoke': True})


def spawn_sparks(x, y, count=8, spd=4):
    for _ in range(count):
        a = random.random() * math.pi * 2
        s = random.uniform(spd * SPARK_SPEED_MIN_FACTOR, spd)
        gs.particles.append({'x': x, 'y': y, 'vx': math.cos(a) * s, 'vy': math.sin(a) * s,
                             'life': random.randint(5, 15), 'max_life': 15,
                             'col': (255, 255, 200), 'size': random.uniform(1, 2.5),
                             'z': random.uniform(2, 6), 'zv': random.uniform(1, 3),
                             'spark': True})



def spawn_smoke_trail(x, y, col=(200, 200, 200)):
    """Smoke puff for projectile trails."""
    if len(gs.particles) >= MAX_PARTICLES:
        return
    gs.particles.append({'x': x, 'y': y, 'vx': random.uniform(-0.3, 0.3), 'vy': random.uniform(-0.3, 0.3),
                         'life': random.randint(15, 30), 'max_life': 30,
                         'col': col, 'size': random.uniform(2, 5),
                         'z': random.uniform(3, 8), 'zv': random.uniform(0.2, 0.8),
                         'smoke': True})


def add_bullet_hole(x, y, col=(60, 60, 60), pitch=None, hit_z=None):
    if len(gs.decals) >= MAX_DECALS:
        return
    d = {'x': x, 'y': y, 'col': col, 'size': random.uniform(2, 4),
         'type': 'hole', 'life': DECAL_LIFETIME}
    if pitch is not None:
        d['pitch'] = pitch
    if hit_z is not None:
        d['hit_z'] = hit_z
    gs.decals.append(d)


def add_scorch(x, y, col=(40, 35, 30)):
    if len(gs.decals) >= MAX_DECALS:
        return
    gs.decals.append({'x': x, 'y': y, 'col': col, 'size': random.uniform(8, 18),
                      'type': 'scorch', 'life': SCORCH_LIFETIME})


def add_blood_stain(x, y, col=(120, 10, 10)):
    if len(gs.decals) >= MAX_DECALS:
        return
    gs.decals.append({'x': x, 'y': y, 'col': col, 'size': random.uniform(3, 8),
                      'type': 'blood', 'life': BLOOD_LIFETIME})


def spawn_weather(count=8):
    if len(gs.weather) >= MAX_WEATHER:
        return
    count = min(count, MAX_WEATHER - len(gs.weather))
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


def draw_particles(z_b, NC, vh=const.H, vy=0):
    focal = int((const.W / 2) / math.tan(const.FOV / 2)) if const.FOV > 0.001 else const.W
    pitch_off = -int(gs.pitch * focal)
    for p in gs.particles:
        edx, edy = p['x'] - gs.px, p['y'] - gs.py
        dst = math.sqrt(edx ** 2 + edy ** 2)
        if dst < 2: continue
        ang = math.atan2(edy, edx) - gs.pa
        while ang > math.pi: ang -= 2 * math.pi
        while ang < -math.pi: ang += 2 * math.pi
        if abs(ang) < 1.2:
            sx = int((0.5 * (ang / (const.FOV / 2)) + 0.5) * const.W)
            sh = int((TILE * focal) / (dst + 0.1))
            idx = max(0, min(NC - 1, sx // gs.render_scale))
            if 0 <= sx < const.W and z_b[idx] > dst:
                sz = max(1, int(p['size'] * sh / 64))
                z_off = int(p['z'] * sh / 64)
                alpha = int(255 * p['life'] / p['max_life'])
                c = tuple(min(255, int(c * alpha // 255)) for c in p['col'])
                base_y = vy + vh // 2 + pitch_off + sh // 2 - z_off
                if c[0] or c[1] or c[2]:
                    if p.get('spark'):
                        sz = max(1, sz // 2)
                        pygame.draw.circle(gs.screen, (255, 255, 255), (sx, base_y), sz)
                        pygame.draw.circle(gs.screen, (255, 255, 200, 0), (sx, base_y), sz + 1, 1)
                    elif p.get('smoke'):
                        sz = int(sz * (1 + 0.5 * (1 - p['life'] / p['max_life'])))
                        alpha_smoke = int(120 * p['life'] / p['max_life'])
                        cs = tuple(min(255, int(c * alpha_smoke // 255)) for c in p['col'])
                        if cs[0] or cs[1] or cs[2]:
                            s = pygame.Surface((sz * 2, sz * 2), pygame.SRCALPHA)
                            pygame.draw.circle(s, (*cs, alpha_smoke), (sz, sz), sz)
                            try:
                                gs.screen.blit(s, (sx - sz, base_y - sz), special_flags=pygame.BLEND_ALPHA_SDL2)
                            except:
                                gs.screen.blit(s, (sx - sz, base_y - sz))
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


def draw_decals(vh=const.H, vy=0):
    focal = int((const.W / 2) / math.tan(const.FOV / 2)) if const.FOV > 0.001 else const.W
    pitch_off = -int(gs.pitch * focal)
    for d in gs.decals:
        edx, edy = d['x'] - gs.px, d['y'] - gs.py
        dst = math.sqrt(edx ** 2 + edy ** 2)
        if dst < 2 or dst > gs.view_dist: continue
        # Wall occlusion check: step along line from player to decal
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
        if occluded: continue
        ang = math.atan2(edy, edx) - gs.pa
        while ang > math.pi: ang -= 2 * math.pi
        while ang < -math.pi: ang += 2 * math.pi
        if abs(ang) < 1.2:
            sx = int((0.5 * (ang / (const.FOV / 2)) + 0.5) * const.W)
            sh = int((TILE * focal) / (dst + 0.1))
            sz = max(1, int(d['size'] * sh / 64))
            # World Z offset: keep decals fixed in world space as player moves vertically
            if d['type'] == 'hole':
                wz = d.get('hit_z', TILE)
            else:
                wz = TILE
            z_off = -int((gs.pz + EYE_H - wz) * focal / (dst + 0.1))
            if d['type'] == 'hole':
                if 'pitch' in d:
                    cy = vy + vh // 2 + pitch_off + z_off + int(d['pitch'] * sh)
                else:
                    cy = vy + vh // 2 + pitch_off + z_off
                pygame.draw.circle(gs.screen, d['col'], (sx, cy), max(1, sz))
            elif d['type'] == 'scorch':
                cy = vy + vh // 2 + pitch_off + z_off + sh // 2
                alpha = 100
                ss = max(1, sz)
                s = pygame.Surface((ss * 2, ss * 2), pygame.SRCALPHA)
                pygame.draw.circle(s, (*d['col'], alpha), (ss, ss), ss)
                gs.screen.blit(s, (sx - ss, cy - ss))
            elif d['type'] == 'blood':
                cy = vy + vh // 2 + pitch_off + z_off + sh // 2
                pygame.draw.ellipse(gs.screen, d['col'], (sx - sz, cy - sz // 2, sz * 2, sz))
