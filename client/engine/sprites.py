import pygame, math, random
from client.engine.constants import TILE

FRAME_CACHE = {}

ANIM_STATES = {
    'idle':   {'frames': 2, 'speed': 20},
    'walk':   {'frames': 4, 'speed': 8},
    'attack': {'frames': 2, 'speed': 4},
    'death':  {'frames': 3, 'speed': 10},
}

def init_anim(entity, state='idle'):
    entity['anim_state'] = state
    entity['anim_frame'] = 0
    entity['anim_timer'] = 0

def update_anim(entity):
    info = ANIM_STATES.get(entity.get('anim_state', 'idle'), ANIM_STATES['idle'])
    entity['anim_timer'] = entity.get('anim_timer', 0) + 1
    if entity['anim_timer'] >= info['speed']:
        entity['anim_timer'] = 0
        entity['anim_frame'] = (entity.get('anim_frame', 0) + 1) % info['frames']

def set_anim_state(entity, state):
    if entity.get('anim_state') != state:
        entity['anim_state'] = state
        entity['anim_frame'] = 0
        entity['anim_timer'] = 0

def get_anim_frame(entity):
    info = ANIM_STATES.get(entity.get('anim_state', 'idle'), ANIM_STATES['idle'])
    return min(entity.get('anim_frame', 0), info['frames'] - 1)

WPN_COLORS = {0: (180, 0, 0), 1: (50, 50, 55), 2: (100, 70, 40), 3: (40, 60, 40), 4: (0, 60, 0), 5: (80, 50, 25)}

def get_bot_surface(col, w, h, frame, state, pa, wpn=1, view='front'):
    key = ('bot', col, w, h, frame, state, wpn, view)
    if key not in FRAME_CACHE:
        surf = pygame.Surface((w, h), pygame.SRCALPHA)
        cx, cy = w // 2, h // 2
        bob = 0
        if state == 'walk':
            bob = int(math.sin(frame * math.pi / 2) * 3)
        elif state == 'attack':
            bob = -2 if frame == 0 else 0
        elif state == 'death':
            bob = frame * 4
        head_r = max(5, w // 7)
        body_w = max(5, w // 5)
        body_h = max(10, h // 3)
        head_y = cy - body_h // 2 - head_r + bob
        body_y = cy - body_h // 2 + bob

        if view == 'back':
            back_col = (col[0] * 2 // 3, col[1] * 2 // 3, col[2] * 2 // 3)
            pygame.draw.circle(surf, back_col, (cx, head_y), head_r)
            pygame.draw.rect(surf, back_col, (cx - body_w // 2, body_y, body_w, body_h))
            leg_w = max(2, body_w // 3)
            leg_h = max(6, h // 5)
            pygame.draw.rect(surf, (back_col[0]//2, back_col[1]//2, back_col[2]//2), (cx - body_w//2, body_y + body_h, leg_w, leg_h))
            pygame.draw.rect(surf, (back_col[0]//2, back_col[1]//2, back_col[2]//2), (cx + body_w//2 - leg_w, body_y + body_h, leg_w, leg_h))
            pygame.draw.line(surf, (back_col[0]//2, back_col[1]//2, back_col[2]//2),
                             (cx, body_y + 2), (cx, body_y + body_h - 2), max(2, body_w // 6))
        elif view == 'side':
            side_col = (col[0] * 5 // 6, col[1] * 5 // 6, col[2] * 5 // 6)
            body_sw = max(3, body_w * 2 // 3)
            pygame.draw.circle(surf, side_col, (cx, head_y), head_r)
            pygame.draw.rect(surf, side_col, (cx - body_sw // 2, body_y, body_sw, body_h))
            leg_w = max(2, body_sw // 3)
            leg_h = max(6, h // 5)
            pygame.draw.rect(surf, (side_col[0]//2, side_col[1]//2, side_col[2]//2), (cx - body_sw//2, body_y + body_h, leg_w, leg_h))
            dx = int(math.cos(pa) * head_r * 1.5)
            dy = int(math.sin(pa) * head_r * 1.5)
            wpn_col = WPN_COLORS.get(wpn, (200, 200, 100))
            pygame.draw.line(surf, wpn_col, (cx, body_y + body_h // 2),
                             (cx + dx, body_y + body_h // 2 + dy), max(2, body_sw // 3))
            tip_r = max(2, body_sw // 5)
            pygame.draw.circle(surf, (255, 255, 255), (cx + dx, body_y + body_h // 2 + dy), tip_r)
        else:
            # Front view
            pygame.draw.circle(surf, col, (cx, head_y), head_r)
            pygame.draw.rect(surf, col, (cx - body_w // 2, body_y, body_w, body_h))
            leg_w = max(2, body_w // 3)
            leg_h = max(6, h // 5)
            pygame.draw.rect(surf, (col[0]//2, col[1]//2, col[2]//2), (cx - body_w//2, body_y + body_h, leg_w, leg_h))
            pygame.draw.rect(surf, (col[0]//2, col[1]//2, col[2]//2), (cx + body_w//2 - leg_w, body_y + body_h, leg_w, leg_h))
            if state != 'death':
                dx = int(math.cos(pa) * head_r * 1.8)
                dy = int(math.sin(pa) * head_r * 1.8)
                wpn_col = WPN_COLORS.get(wpn, (200, 200, 100))
                pygame.draw.line(surf, wpn_col, (cx, body_y + body_h // 2),
                                 (cx + dx, body_y + body_h // 2 + dy), max(2, body_w // 2))
                tip_r = max(2, body_w // 4)
                pygame.draw.circle(surf, (255, 255, 255), (cx + dx, body_y + body_h // 2 + dy), tip_r)
        FRAME_CACHE[key] = surf
    return FRAME_CACHE[key]

def get_enemy_surface(enemy, sh):
    etype = enemy.get('t', 'soldier')
    frame = get_anim_frame(enemy)
    state = enemy.get('anim_state', 'idle')
    key = ('enemy', etype, sh, frame, state, enemy.get('pulse', 0) > 0)
    if key not in FRAME_CACHE:
        surf = pygame.Surface((sh, sh), pygame.SRCALPHA)
        _draw_enemy_on_surf(surf, enemy, sh, frame, state)
        FRAME_CACHE[key] = surf
    return FRAME_CACHE[key]

def _draw_enemy_on_surf(surf, enemy, sh, frame, state):
    cx, cy = sh // 2, sh // 2
    bob = 0
    if state == 'walk':
        bob = int(math.sin(frame * math.pi / 2) * 2)
    elif state == 'death':
        bob = frame * 3
    t = enemy.get('t', 'soldier')
    col = enemy.get('col', (150, 100, 80))
    pulse = enemy.get('pulse', 0)
    if t == 'soldier':
        hw, hh = sh * 0.2, sh * 0.15
        pygame.draw.rect(surf, col, (cx - hw, cy - hh * 2 + bob, hw * 2, hh * 2))
        head_r = hw
        pygame.draw.circle(surf, (180, 150, 120), (cx, cy - hh * 2 - hw + bob), head_r)
        pygame.draw.circle(surf, (60, 60, 60), (cx - 3, cy - hh * 2 - hw + bob - 2), 2)
        pygame.draw.circle(surf, (60, 60, 60), (cx + 3, cy - hh * 2 - hw + bob - 2), 2)
        # weapon
        wpn_col = (50, 50, 55)
        pygame.draw.rect(surf, wpn_col, (cx + hw - 2, cy - hh + bob, 6, hh))
    elif t == 'imp':
        r = sh * 0.2
        body_col = (col[0] + 20, col[1] - 10, col[2] - 10)
        pygame.draw.ellipse(surf, body_col, (cx - r, cy - r * 1.5 + bob, r * 2, r * 3))
        head_r = r * 0.8
        pygame.draw.circle(surf, col, (cx, cy - r * 2 + bob), head_r)
        pygame.draw.circle(surf, (255, 200, 50), (cx - r * 0.3, cy - r * 2.2 + bob), 2)
        pygame.draw.circle(surf, (255, 200, 50), (cx + r * 0.3, cy - r * 2.2 + bob), 2)
        pygame.draw.circle(surf, (10, 10, 10), (cx - r * 0.3, cy - r * 2.2 + bob), 1)
        pygame.draw.circle(surf, (10, 10, 10), (cx + r * 0.3, cy - r * 2.2 + bob), 1)
        # horns
        pygame.draw.line(surf, (60, 40, 20), (cx - 5, cy - r * 2.4 + bob), (cx - 7, cy - r * 3 + bob), 2)
        pygame.draw.line(surf, (60, 40, 20), (cx + 5, cy - r * 2.4 + bob), (cx + 7, cy - r * 3 + bob), 2)
    elif t == 'demon':
        r = sh * 0.25
        wobble = math.sin(frame * 0.5 + pulse) * 2
        pygame.draw.ellipse(surf, col, (cx - r, cy - r * 0.8 + bob, r * 2, r * 1.6))
        head_r = r * 0.7
        pygame.draw.circle(surf, (180, 120, 100), (cx, cy - r + bob), head_r)
        ex = int(math.sin(frame * 2) * 2)
        pygame.draw.circle(surf, (255, 100, 50), (cx - r * 0.3 + ex, cy - r * 1.3 + bob), 2)
        pygame.draw.circle(surf, (255, 100, 50), (cx + r * 0.3 + ex, cy - r * 1.3 + bob), 2)
        pygame.draw.circle(surf, (10, 10, 10), (cx - r * 0.3 + ex, cy - r * 1.3 + bob), 1)
        pygame.draw.circle(surf, (10, 10, 10), (cx + r * 0.3 + ex, cy - r * 1.3 + bob), 1)
        # ears
        for s in (-1, 1):
            pygame.draw.polygon(surf, (140, 80, 60), [
                (cx + s * r * 0.6, cy - r * 0.8 + bob),
                (cx + s * r, cy - r * 1.5 + bob),
                (cx + s * r * 0.8, cy - r * 1.2 + bob)
            ])
    elif t == 'cacodemon':
        r = sh * 0.3
        float_y = int(math.sin(pulse * 0.1 + frame) * 3)
        pygame.draw.circle(surf, col, (cx, cy + float_y + bob), r)
        pygame.draw.circle(surf, (200, 50, 50), (cx, cy + float_y + bob), r * 0.3)
        pygame.draw.circle(surf, (255, 255, 100), (cx, cy + float_y + bob), r * 0.15)
        pygame.draw.circle(surf, (10, 10, 10), (cx - r * 0.2, cy + float_y + bob - r * 0.15), 2)
        pygame.draw.circle(surf, (10, 10, 10), (cx + r * 0.2, cy + float_y + bob - r * 0.15), 2)
        # teeth
        for tx in (-r * 0.1, r * 0.1, r * 0.25):
            pygame.draw.rect(surf, (255, 255, 255), (cx + tx - 1, cy + float_y + bob + 1, 2, 3))
    elif t == 'baron':
        r = sh * 0.3
        body_col = (col[0] + 10, col[1] - 10, col[2] - 5)
        pygame.draw.rect(surf, body_col, (cx - r, cy - r * 1.5 + bob, r * 2, r * 3))
        head_r = r * 0.8
        pygame.draw.circle(surf, col, (cx, cy - r * 2 + bob), head_r)
        pygame.draw.circle(surf, (255, 200, 0), (cx - r * 0.3, cy - r * 2.2 + bob), 3)
        pygame.draw.circle(surf, (255, 200, 0), (cx + r * 0.3, cy - r * 2.2 + bob), 3)
        pygame.draw.circle(surf, (10, 10, 10), (cx - r * 0.3, cy - r * 2.2 + bob), 1)
        pygame.draw.circle(surf, (10, 10, 10), (cx + r * 0.3, cy - r * 2.2 + bob), 1)
        # horns
        for s in (-1, 1):
            pygame.draw.line(surf, (60, 40, 20), (cx + s * r * 0.4, cy - r * 2 + bob), (cx + s * r * 0.8, cy - r * 3 + bob), 3)
    elif t == 'mancubus':
        r = sh * 0.35
        wobble = int(math.sin(pulse * 0.05) * 2)
        pygame.draw.ellipse(surf, col, (cx - r, cy - r * 0.6 + bob + wobble, r * 2, r * 1.2))
        pygame.draw.rect(surf, (80, 60, 50), (cx - r * 0.2, cy - r * 0.4 + bob + wobble, r * 0.4, r * 0.5))
        pygame.draw.circle(surf, (200, 170, 140), (cx, cy - r * 0.8 + bob + wobble), r * 0.5)
    elif t == 'archvile':
        r = sh * 0.25
        pygame.draw.polygon(surf, col, [(cx, cy - r * 2 + bob), (cx - r, cy + bob), (cx + r, cy + bob)])
        pygame.draw.circle(surf, (220, 200, 180), (cx, cy - r * 2.2 + bob), r * 0.5)
    elif t == 'revenant':
        r = sh * 0.2
        pygame.draw.rect(surf, col, (cx - r, cy - r * 1.5 + bob, r * 2, r * 3))
        pygame.draw.circle(surf, (180, 160, 140), (cx, cy - r * 2 + bob), r * 0.7)
        for side in (-1, 1):
            pygame.draw.line(surf, (200, 200, 220), (cx + side * r, cy - r + bob), (cx + side * r * 2, cy), 2)
    elif t == 'pain':
        r = sh * 0.3
        float_y = int(math.sin(pulse * 0.1 + frame) * 2)
        for i in range(3):
            pr = r - i * r * 0.25
            pygame.draw.circle(surf, (col[0] - i * 30, col[1] - i * 10, col[2] - i * 10),
                               (cx, cy + float_y + bob), int(pr))
    elif t == 'spider':
        r = sh * 0.35
        wobble = int(math.sin(pulse * 0.07) * 2)
        pygame.draw.ellipse(surf, col, (cx - r, cy - r * 0.4 + bob + wobble, r * 2, r * 0.8))
        for i in range(4):
            a = i * math.pi / 4 + math.pi / 8
            px = cx + int(math.cos(a) * r * 0.8)
            py = cy + int(math.sin(a) * r * 0.8) + bob + wobble
            pygame.draw.line(surf, col, (cx, cy + bob + wobble), (px, py), 2)
    else:
        hw, hh = sh * 0.15, sh * 0.2
        pygame.draw.rect(surf, col, (cx - hw, cy - hh + bob, hw * 2, hh * 2))
        pygame.draw.circle(surf, (200, 170, 140), (cx, cy - hh - hw + bob), hw)

def render_sprite_frame(entity_type, params, sh):
    key = (entity_type, tuple(sorted(params.items())), sh)
    if key in FRAME_CACHE:
        return FRAME_CACHE[key]
    surf = pygame.Surface((sh, sh), pygame.SRCALPHA)
    FRAME_CACHE[key] = surf
    return surf
