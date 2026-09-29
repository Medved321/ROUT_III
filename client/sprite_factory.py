import pygame, math, random

# Bot sprites only (registered as ``gs.sprite_provider``).  Enemy art used to
# have a second, parallel implementation here -- a per-type ``if/elif`` chain
# over square surfaces with its own cache -- and it was dead: nothing ever
# called it.  The live enemy path is ``client/enemy_models.py`` behind
# ``gs.enemy_surface_provider`` (change ``enemy-model-overhaul``, task 8.1).

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
