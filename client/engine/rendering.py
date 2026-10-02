import math, random, pygame
from client.engine.constants import SCALE, TILE, FOV, TEX_W, WPN, ENEMY_DB, EYE_H
from client.engine import constants as const
from client.engine.map_editor import TILE_CHAR_TO_TEX
from client.engine import game_state as gs
from client.engine.particles import draw_particles, draw_decals, draw_weather
from client.engine.map_utils import get_t, _player_in_any_sector
from client.engine.sprites import get_bot_surface

_RH = const.H
_RY = 0

# Camera eye height (world units).  Tile-mode walls/sprites are shifted so the
# horizon sits at this height instead of the old wall-centre (TILE/2 = 32),
# which made the camera look like it was at other players' waist level.
CAM_Z = 41.0

# Pre-generated star field


# Load SSG sprite frames from PNG files
_SSG_FILES = {
    1: 'skins/dwust_1.png', 2: 'skins/dwust_2.png', '23': 'skins/dwust_2-3.png', '233': 'skins/dwust_23-3.png',
    3: 'skins/dwust_3.png', 4: 'skins/dwust_4.png', 5: 'skins/dwust_5.png', 6: 'skins/dwust_6.png'
}
_ssg_frames = {}
_SSG_SCALE = {1: 1.5, 2: 1.3, '23': 1.3, '233': 0.87, 3: 2.2, 4: 1.3, 5: 1.3, 6: 1.3}
_wpn_sprites = {}

# DOOM sprite base names for enemy types
_ENEMY_SPRITE_MAP = {
    'soldier': 'POSS', 'shotgunguy': 'SPOS', 'chaingunner': 'CPOS',
    'imp': 'TROO', 'pinkie': 'SARG', 'spectre': 'SARG', 'demon': 'SARG',
    'lost_soul': 'SKUL', 'cacodemon': 'HEAD',
    'hell_knight': 'BOS2', 'boss_baron': 'BOSS',
    'revenant': 'SKEL', 'mancubus': 'FATT',
    'archvile': 'VILE', 'arachnotron': 'SPID',
    'boss_spider': 'SPID', 'boss_cyber': 'CYBR',
    'pain_elemental': 'PAIN',
}

def _get_ssg(i):
    if i not in _ssg_frames:
        try:
            img = pygame.image.load(_SSG_FILES[i]).convert_alpha()
            s = _SSG_SCALE[i]
            if s != 1.0:
                w, h = int(img.get_width() * s), int(img.get_height() * s)
                if w > 0 and h > 0:
                    img = pygame.transform.scale(img, (w, h))
            _ssg_frames[i] = img
        except:
            surf = pygame.Surface((1, 1))
            _ssg_frames[i] = surf
    return _ssg_frames[i]




# Weapon sprite frames
_PISTOL_IDLE = None
_PISTOL_FIRE = None
_RIFLE_IDLE = None
_RIFLE_FIRE = None
_SHOTGUN_IDLE = None
_SHOTGUN_FIRE = None
_SHOTGUN_RECOIL = None
_CHAINSAW_IDLE = None
_CHAINSAW_FIRE = None

def _load_pistol():
    global _PISTOL_IDLE, _PISTOL_FIRE
    try:
        img1 = pygame.image.load('skins/pistol_1.png').convert_alpha()
        img2 = pygame.image.load('skins/pistol_2.png').convert_alpha()
        s = 3.0
        w, h = int(img1.get_width() * s), int(img1.get_height() * s)
        _PISTOL_IDLE = pygame.transform.scale(img1, (w, h)) if w > 0 and h > 0 else img1
        w, h = int(img2.get_width() * s), int(img2.get_height() * s)
        _PISTOL_FIRE = pygame.transform.scale(img2, (w, h)) if w > 0 and h > 0 else img2
    except:
        _PISTOL_IDLE = pygame.Surface((1, 1))
        _PISTOL_FIRE = pygame.Surface((1, 1))

def _load_rifle():
    global _RIFLE_IDLE, _RIFLE_FIRE
    try:
        img1 = pygame.image.load('skins/riffle_1.png').convert_alpha()
        img2 = pygame.image.load('skins/riffle_2.png').convert_alpha()
        s = 1
        w, h = int(img1.get_width() * s), int(img1.get_height() * s)
        _RIFLE_IDLE = pygame.transform.scale(img1, (w, h)) if w > 0 and h > 0 else img1
        w, h = int(img2.get_width() * s), int(img2.get_height() * s)
        _RIFLE_FIRE = pygame.transform.scale(img2, (w, h)) if w > 0 and h > 0 else img2
    except:
        _RIFLE_IDLE = pygame.Surface((1, 1))
        _RIFLE_FIRE = pygame.Surface((1, 1))

def _load_shotgun():
    global _SHOTGUN_IDLE, _SHOTGUN_FIRE, _SHOTGUN_RECOIL
    try:
        img1 = pygame.image.load('skins/shootgun_1.png').convert_alpha()
        img2 = pygame.image.load('skins/shootgun_2.png').convert_alpha()
        img3 = pygame.image.load('skins/shootgun_3.png').convert_alpha()
        s1, s2, s3 = 3.0, 2.5, round(2.0 / 1.5, 2)
        w, h = int(img1.get_width() * s1), int(img1.get_height() * s1)
        _SHOTGUN_IDLE = pygame.transform.scale(img1, (w, h)) if w > 0 and h > 0 else img1
        w, h = int(img2.get_width() * s2), int(img2.get_height() * s2)
        _SHOTGUN_FIRE = pygame.transform.scale(img2, (w, h)) if w > 0 and h > 0 else img2
        w, h = int(img3.get_width() * s3), int(img3.get_height() * s3)
        _SHOTGUN_RECOIL = pygame.transform.scale(img3, (w, h)) if w > 0 and h > 0 else img3
    except:
        _SHOTGUN_IDLE = _SHOTGUN_FIRE = _SHOTGUN_RECOIL = pygame.Surface((1, 1))


def _load_chainsaw():
    global _CHAINSAW_IDLE, _CHAINSAW_FIRE
    try:
        img1 = pygame.image.load('skins/chainsaw_1.png').convert_alpha()
        img2 = pygame.image.load('skins/chainsaw_2.png').convert_alpha()
        s = 3.0
        w, h = int(img1.get_width() * s), int(img1.get_height() * s)
        _CHAINSAW_IDLE = pygame.transform.scale(img1, (w, h)) if w > 0 and h > 0 else img1
        w, h = int(img2.get_width() * s), int(img2.get_height() * s)
        _CHAINSAW_FIRE = pygame.transform.scale(img2, (w, h)) if w > 0 and h > 0 else img2
    except:
        _CHAINSAW_IDLE = _CHAINSAW_FIRE = pygame.Surface((1, 1))


def draw_wpn(idx, tmr, fire, bob):
    if idx >= 100:
        cw_idx = idx - 100
        cg = gs.p.get('custom_guns', [])
        if 0 <= cw_idx < len(cg):
            from custom_content import get_custom_weapons
            cw = get_custom_weapons()
            wdef = cw.get(cg[cw_idx], {})
            hand_sprite_path = wdef.get('hand_sprite')
            if hand_sprite_path and not (wdef.get('proj_sprite') and len(gs.proj) > 0):
                if hand_sprite_path not in _wpn_sprites:
                    try:
                        _wpn_sprites[hand_sprite_path] = pygame.image.load(hand_sprite_path).convert_alpha()
                    except Exception:
                        _wpn_sprites[hand_sprite_path] = None
                spr = _wpn_sprites.get(hand_sprite_path)
                if spr:
                    scale = min(const.W * 0.5 / spr.get_width(), const.H * 0.4 / spr.get_height(), 4)
                    sw = int(spr.get_width() * scale)
                    sh_s = int(spr.get_height() * scale)
                    bx = const.W // 2 - sw // 2
                    by = const.H - sh_s - 20 + bob
                    gs.screen.blit(pygame.transform.scale(spr, (sw, sh_s)), (bx, by))
            else:
                model_name = wdef.get('model')
                if model_name:
                    from voxel import load_voxel, render_voxel_facing
                    voxels, palette = load_voxel(model_name)
                    if voxels:
                        spr, _ = render_voxel_facing(voxels, palette, 30, 64)
                        if spr:
                            scale_f = 6
                            sw = spr.get_width() * scale_f
                            sh = spr.get_height() * scale_f
                            bx = const.W // 2 - sw // 2
                            by = const.H - sh - 30 + bob
                            gs.screen.blit(pygame.transform.scale(spr, (sw, sh)), (bx, by))
                else:
                    col = wdef.get('color', (200, 200, 100))
                    sz = 80
                    bx = const.W // 2 - sz // 2
                    by = const.H - sz - 30 + bob
                    pygame.draw.rect(gs.screen, col, (bx, by, sz, sz))
                    pygame.draw.rect(gs.screen, (255, 255, 255), (bx, by, sz, sz), 2)
        return
    info = WPN[idx]
    rcl = math.sin((tmr / info['f']) * math.pi) * 100 if tmr > 0 else 0
    wx, wy = const.W // 2, _RY + _RH - 230 + rcl + bob
    wn = info['n']

    if wn == 'CHAINSAW':
        if _CHAINSAW_IDLE is None:
            _load_chainsaw()
        shake = random.randint(-8, 8) if fire else 0
        img = _CHAINSAW_FIRE if fire else _CHAINSAW_IDLE
        gw, gh = img.get_size()
        gs.screen.blit(img, (wx - gw // 2 + shake, wy + 200 - gh))

    elif wn == 'PISTOL':
        if _PISTOL_IDLE is None:
            _load_pistol()
        img = _PISTOL_FIRE if fire else _PISTOL_IDLE
        gw, gh = img.get_size()
        gs.screen.blit(img, (wx - gw // 2, wy + 200 - gh))
    elif wn == 'SHOTGUN':
        if _SHOTGUN_IDLE is None:
            _load_shotgun()
        if tmr > 25:
            img = _SHOTGUN_FIRE
        elif tmr > 0:
            img = _SHOTGUN_RECOIL
        else:
            img = _SHOTGUN_IDLE
        gw, gh = img.get_size()
        gs.screen.blit(img, (wx - gw // 2, wy + 200 - gh))
    elif wn == 'RIFLE':
        if _RIFLE_IDLE is None:
            _load_rifle()
        img = _RIFLE_FIRE if fire else _RIFLE_IDLE
        gw, gh = img.get_size()
        gs.screen.blit(img, (wx - gw // 2 - 100, wy + 200 - gh))

    elif wn == 'BFG':
        glow = 255 if fire else 100
        pygame.draw.rect(gs.screen, (0, 40, 0), (wx - 100, wy - 60, 200, 220))
        pygame.draw.rect(gs.screen, (0, 70, 0), (wx - 80, wy - 40, 160, 180))
        pygame.draw.polygon(gs.screen, (0, 100, 0), [
            (wx - 55, wy - 40), (wx + 55, wy - 40),
            (wx + 80, wy - 100), (wx + 60, wy - 120),
            (wx - 60, wy - 120), (wx - 80, wy - 100)
        ])
        pg = min(glow + 155, 255)
        pygame.draw.circle(gs.screen, (0, pg, 0), (wx, wy - 105), 50)
        pygame.draw.circle(gs.screen, (pg, 255, pg), (wx, wy - 105), 28)
        pygame.draw.circle(gs.screen, (255, 255, 255), (wx, wy - 105), 12)
        pygame.draw.rect(gs.screen, (60, 40, 20), (wx - 22, wy + 150, 44, 36))

    elif wn == 'SSG':
        if tmr > 66:
            f = _get_ssg(2)
        elif tmr > 56:
            f = _get_ssg('23')
        elif tmr > 46:
            f = _get_ssg('233')
        elif tmr > 36:
            f = _get_ssg(3)
        elif tmr > 26:
            f = _get_ssg(4)
        elif tmr > 16:
            f = _get_ssg(5)
        elif tmr > 8:
            f = _get_ssg(6)
        else:
            f = _get_ssg(1)
        gs.screen.blit(f, (const.W // 2 - f.get_width() // 2, _RY + _RH - f.get_height() + bob))



def draw_enemy_sprite(e, sx, sh, tick):
    t = e['t']; col = e['col']
    is_dying = e.get('dying', 0) > 0
    af = e.get('anim_frame', 0)
    anim_state = e.get('anim_state', 'idle')
    if is_dying:
        die_pct = e.get('dying', 15) / 15.0
        sh = int(sh * die_pct)
    is_stunned = e.get('stun', 0) > 0 and not is_dying
    is_waking = e.get('alert', 0) > 20 and not is_dying
    is_rushing = e.get('rush_timer', 0) > 0 and not is_dying
    s = max(sh / 54.0, 0.5)
    if gs.sector_mode:
        floor_y = _RY + _RH // 2 + sh // 2
        if t == 'cacodemon':
            by = floor_y - int(sh * 0.3)
        else:
            by = min(floor_y, _RY + _RH)
    else:
        if t == 'cacodemon':
            by = _RY + _RH // 2
        else:
            by = min(_RY + _RH // 2 + int(CAM_Z * sh / TILE), _RY + _RH)
    ground_y = by

    # Drop shadow
    sw = max(int(sh / 3), 8)
    sh_surf = pygame.Surface((sw * 2, sw // 2), pygame.SRCALPHA)
    pygame.draw.ellipse(sh_surf, (0, 0, 0, 60), (0, 0, sw * 2, sw // 2))
    try:
        gs.screen.blit(sh_surf, (sx - sw, ground_y - sw // 4), special_flags=pygame.BLEND_ALPHA_SDL2)
    except:
        gs.screen.blit(sh_surf, (sx - sw, ground_y - sw // 4))

    if anim_state == 'walk':
        bob = int(math.sin(af * math.pi / 2) * 3 * s)
        walk = math.sin(af * math.pi)
        walk2 = math.cos(af * math.pi)
    elif anim_state == 'attack':
        bob = -2 * s if af == 0 else 0
        walk = 0
        walk2 = 0
    elif anim_state == 'death':
        bob = af * 4 * s
        walk = 0
        walk2 = 0
    else:
        bob = int(math.sin(af * math.pi) * 2 * s)
        walk = math.sin(af * math.pi)
        walk2 = math.cos(af * math.pi)
    glow = (math.sin(tick * 0.005) + 1) * 0.5
    atk_t = tick - e.get('ls', 0)
    atk_pct = max(0, 1 - atk_t / 300)

    if t == 'soldier':
        bw, bh = int(20*s), int(36*s)
        body_y = by - bh - int(12*s) + bob
        recoil = atk_pct * int(6 * s)
        # Legs
        l_swing = int(7*s * walk)
        pygame.draw.line(gs.screen, (60, 60, 60), (sx - int(5*s), by - int(8*s) + bob), (sx - int(8*s) + l_swing, by), int(4*s))
        pygame.draw.line(gs.screen, (60, 60, 60), (sx + int(5*s), by - int(8*s) + bob), (sx + int(8*s) - l_swing, by), int(4*s))
        # Body armor
        lean = int(3*s * walk2)
        pygame.draw.polygon(gs.screen, (100, 120, 100), [
            (sx - bw//2 + lean, body_y + bh), (sx - bw//2 + lean, body_y + int(8*s)),
            (sx + lean, body_y), (sx + bw//2 + lean, body_y + int(8*s)), (sx + bw//2 + lean, body_y + bh)
        ])
        # Head
        hr = int(9*s)
        head_y = body_y
        head_x = sx + lean
        pygame.draw.circle(gs.screen, (220, 180, 150), (int(head_x), int(head_y)), hr)
        # Helmet
        pygame.draw.arc(gs.screen, (80, 90, 80), (int(head_x) - hr, int(head_y) - hr, hr*2, hr*2), 0, math.pi, int(3*s))
        # Eyes - aggressive glare
        pygame.draw.ellipse(gs.screen, (255, 180, 180), (int(head_x) - int(4*s), int(head_y) - int(2*s), int(3*s), int(2*s)))
        pygame.draw.ellipse(gs.screen, (255, 180, 180), (int(head_x) + int(1*s), int(head_y) - int(2*s), int(3*s), int(2*s)))
        pygame.draw.circle(gs.screen, (220, 0, 0), (int(head_x) - int(2*s), int(head_y) - int(1*s)), int(1*s))
        pygame.draw.circle(gs.screen, (220, 0, 0), (int(head_x) + int(2*s), int(head_y) - int(1*s)), int(1*s))
        # Angry eyebrows
        pygame.draw.line(gs.screen, (50, 50, 50), (int(head_x) - int(6*s), int(head_y) - int(5*s)), (int(head_x) - int(1*s), int(head_y) - int(3*s)), int(2*s))
        pygame.draw.line(gs.screen, (50, 50, 50), (int(head_x) + int(6*s), int(head_y) - int(5*s)), (int(head_x) + int(1*s), int(head_y) - int(3*s)), int(2*s))
        # Battle scar
        pygame.draw.line(gs.screen, (180, 120, 120), (int(head_x) + int(4*s), int(head_y) - int(6*s)), (int(head_x) + int(6*s), int(head_y) - int(3*s)), int(1*s))
        # Rifle with recoil
        ao = int(2*s * math.sin(tick * 0.004))
        rx = sx + int(6*s) + recoil
        ry = body_y + int(12*s)
        pygame.draw.line(gs.screen, (60, 60, 60), (rx, ry), (rx + int(16*s), body_y + int(6*s) + ao - recoil), int(4*s))
        pygame.draw.line(gs.screen, (180, 180, 180), (rx + int(16*s), body_y + int(6*s) + ao - recoil), (rx + int(22*s), body_y + int(2*s) + ao - recoil), int(3*s))
        # Arms
        ao2 = int(2*s * math.cos(tick * 0.004))
        pygame.draw.line(gs.screen, (220, 180, 150), (sx - bw//2 + lean, body_y + int(6*s)), (sx - int(10*s), body_y + int(16*s) + ao2), int(3*s))

    elif t == 'shotgunguy':
        bw, bh = int(24*s), int(36*s)
        body_y = by - bh - int(12*s) + bob
        recoil = atk_pct * int(8 * s)
        # Legs
        l_swing = int(7*s * walk)
        pygame.draw.line(gs.screen, (50, 50, 80), (sx - int(5*s), by - int(8*s) + bob), (sx - int(8*s) + l_swing, by), int(4*s))
        pygame.draw.line(gs.screen, (50, 50, 80), (sx + int(5*s), by - int(8*s) + bob), (sx + int(8*s) - l_swing, by), int(4*s))
        # Body
        lean = int(3*s * walk2)
        pygame.draw.polygon(gs.screen, (70, 90, 150), [
            (sx - bw//2 + lean, body_y + bh), (sx - bw//2 + lean, body_y + int(8*s)),
            (sx + lean, body_y), (sx + bw//2 + lean, body_y + int(8*s)), (sx + bw//2 + lean, body_y + bh)
        ])
        # Body armor highlight
        pygame.draw.polygon(gs.screen, (90, 110, 170), [
            (sx - bw//2 + int(2*s) + lean, body_y + int(10*s)), (sx - int(2*s) + lean, body_y + int(2*s)),
            (sx + int(2*s) + lean, body_y + int(2*s)), (sx + bw//2 - int(2*s) + lean, body_y + int(10*s))
        ], int(2*s))
        # Head
        hr = int(9*s)
        head_y = body_y
        head_x = sx + lean
        pygame.draw.circle(gs.screen, (220, 180, 150), (int(head_x), int(head_y)), hr)
        # Helmet
        pygame.draw.arc(gs.screen, (50, 70, 130), (int(head_x) - hr, int(head_y) - hr, hr*2, hr*2), 0, math.pi, int(3*s))
        # Eyes - aggressive glare
        pygame.draw.ellipse(gs.screen, (255, 180, 180), (int(head_x) - int(4*s), int(head_y) - int(2*s), int(3*s), int(2*s)))
        pygame.draw.ellipse(gs.screen, (255, 180, 180), (int(head_x) + int(1*s), int(head_y) - int(2*s), int(3*s), int(2*s)))
        pygame.draw.circle(gs.screen, (220, 0, 0), (int(head_x) - int(2*s), int(head_y) - int(1*s)), int(1*s))
        pygame.draw.circle(gs.screen, (220, 0, 0), (int(head_x) + int(2*s), int(head_y) - int(1*s)), int(1*s))
        # Angry eyebrows
        pygame.draw.line(gs.screen, (40, 40, 70), (int(head_x) - int(6*s), int(head_y) - int(5*s)), (int(head_x) - int(1*s), int(head_y) - int(3*s)), int(2*s))
        pygame.draw.line(gs.screen, (40, 40, 70), (int(head_x) + int(6*s), int(head_y) - int(5*s)), (int(head_x) + int(1*s), int(head_y) - int(3*s)), int(2*s))
        # Shotgun with recoil
        sgx = sx + int(4*s) + recoil
        sgy = body_y + int(12*s)
        pygame.draw.line(gs.screen, (120, 90, 50), (sgx, sgy), (sgx + int(24*s), body_y + int(8*s) - recoil), int(5*s))
        # Foregrip
        pygame.draw.line(gs.screen, (80, 60, 30), (sgx + int(12*s), sgy), (sgx + int(12*s), body_y + int(18*s)), int(3*s))
        # Muzzle flash
        fire_flash = (255, 255, 150) if atk_pct > 0 else (80, 60, 30)
        pygame.draw.rect(gs.screen, fire_flash, (sgx + int(20*s), body_y + int(6*s) - recoil, int(12*s), int(8*s)))
        # Arm
        pygame.draw.line(gs.screen, (220, 180, 150), (sx - bw//2 + lean, body_y + int(8*s)), (sx - int(10*s), body_y + int(18*s)), int(3*s))

    elif t == 'chaingunner':
        bw, bh = int(20*s), int(36*s)
        body_y = by - bh - int(12*s) + bob
        l_swing = int(4*s * walk)
        pygame.draw.line(gs.screen, (80, 80, 90), (sx - int(4*s), by - int(6*s) + bob), (sx - int(6*s) + l_swing, by), int(4*s))
        pygame.draw.line(gs.screen, (80, 80, 90), (sx + int(4*s), by - int(6*s) + bob), (sx + int(6*s) - l_swing, by), int(4*s))
        pygame.draw.polygon(gs.screen, (90, 100, 110), [
            (sx - bw//2, body_y + bh), (sx - bw//2, body_y + int(8*s)),
            (sx, body_y), (sx + bw//2, body_y + int(8*s)), (sx + bw//2, body_y + bh)
        ])
        hr = int(9*s)
        head_y = body_y
        pygame.draw.circle(gs.screen, (200, 180, 160), (sx, int(head_y)), hr)
        pygame.draw.arc(gs.screen, (60, 60, 70), (sx - hr, int(head_y) - hr, hr*2, hr*2), 0, math.pi, int(3*s))
        pygame.draw.ellipse(gs.screen, (255, 180, 180), (sx - int(4*s), int(head_y) - int(2*s), int(3*s), int(2*s)))
        pygame.draw.ellipse(gs.screen, (255, 180, 180), (sx + int(1*s), int(head_y) - int(2*s), int(3*s), int(2*s)))
        pygame.draw.circle(gs.screen, (220, 0, 0), (sx - int(2*s), int(head_y) - int(1*s)), int(1*s))
        pygame.draw.circle(gs.screen, (220, 0, 0), (sx + int(2*s), int(head_y) - int(1*s)), int(1*s))
        pygame.draw.line(gs.screen, (50, 50, 50), (sx - int(6*s), int(head_y) - int(5*s)), (sx - int(1*s), int(head_y) - int(3*s)), int(2*s))
        pygame.draw.line(gs.screen, (50, 50, 50), (sx + int(6*s), int(head_y) - int(5*s)), (sx + int(1*s), int(head_y) - int(3*s)), int(2*s))
        ao = int(2*s * math.sin(tick * 0.004))
        for b in (-1, 1):
            for i in range(3):
                cx = sx + b*int(6*s) + i*int(3*s*b)
                pygame.draw.line(gs.screen, (120, 120, 130), (cx, body_y + int(10*s)), (cx + b*int(20*s), body_y + int(6*s) + ao), int(2*s))
        pygame.draw.rect(gs.screen, (100, 100, 100), (sx + int(4*s), body_y + int(10*s), int(18*s), int(4*s)))
        pygame.draw.line(gs.screen, (220, 180, 160), (sx - bw//2, body_y + int(6*s)), (sx - int(10*s), body_y + int(16*s) + ao), int(3*s))

    elif t == 'imp':
        bw, bh = int(22*s), int(32*s)
        body_y = by - bh - int(14*s) + bob
        l_swing = int(5*s * walk)
        pygame.draw.line(gs.screen, (120, 80, 50), (sx - int(5*s), by - int(8*s) + bob), (sx - int(8*s) + l_swing, by), int(5*s))
        pygame.draw.line(gs.screen, (120, 80, 50), (sx + int(5*s), by - int(8*s) + bob), (sx + int(8*s) - l_swing, by), int(5*s))
        pygame.draw.ellipse(gs.screen, col, (sx - bw//2, body_y, bw, bh))
        pygame.draw.ellipse(gs.screen, (140, 100, 60), (sx - int(6*s), body_y + int(4*s), int(12*s), int(16*s)))
        hr = int(10*s)
        head_y = body_y - hr + int(2*s)
        pygame.draw.circle(gs.screen, col, (sx, int(head_y)), hr)
        pygame.draw.ellipse(gs.screen, (255, 200, 100), (sx - int(4*s), int(head_y) - int(3*s), int(3*s), int(3*s)))
        pygame.draw.ellipse(gs.screen, (255, 200, 100), (sx + int(1*s), int(head_y) - int(3*s), int(3*s), int(3*s)))
        pygame.draw.circle(gs.screen, (0, 0, 0), (sx - int(2*s), int(head_y) - int(2*s)), int(2*s))
        pygame.draw.circle(gs.screen, (0, 0, 0), (sx + int(2*s), int(head_y) - int(2*s)), int(2*s))
        pygame.draw.ellipse(gs.screen, (150, 50, 50), (sx - int(4*s), int(head_y) + int(4*s), int(8*s), int(4*s)))
        for dx in [-1, 1]:
            pygame.draw.line(gs.screen, (160, 160, 160), (sx + dx*int(7*s), head_y - int(1*s)),
                           (sx + dx*int(11*s), head_y - int(12*s)), int(3*s))
        for dx in [-1, 1]:
            ax = sx + dx*int(12*s)
            ay = body_y + int(12*s)
            pygame.draw.line(gs.screen, col, (sx + dx*int(6*s), ay), (ax, ay + int(8*s)), int(4*s))
            for c in range(3):
                cx = ax + dx*int(2*s*c)
                pygame.draw.line(gs.screen, (200, 200, 200), (cx, ay + int(8*s)),
                               (cx + dx*int(3*s), ay + int(12*s) + int(2*s*c)), int(2*s))

    elif t == 'dog':
        bw, bh = int(24*s), int(18*s)
        body_y = by - bh - int(6*s) + bob
        # Legs with walk
        lf = int(4*s * walk)
        for i, (dx, phase) in enumerate([(-8, 1), (-3, -1), (3, 1), (8, -1)]):
            lx = sx + int(dx*s) + int(phase * lf)
            pygame.draw.line(gs.screen, (120, 80, 40), (lx, by - int(4*s) + bob), (lx, by), int(3*s))
        # Body
        pygame.draw.ellipse(gs.screen, col, (sx - bw//2, body_y, bw, bh))
        # Body shading
        pygame.draw.ellipse(gs.screen, (180, 140, 80), (sx - bw//2 + int(2*s), body_y + int(2*s), bw - int(4*s), bh - int(4*s)), 1)
        # Head
        hr = int(8*s)
        head_y = body_y - hr + int(3*s)
        pygame.draw.circle(gs.screen, col, (sx, int(head_y)), hr)
        # Snout
        pygame.draw.ellipse(gs.screen, (190, 150, 90), (sx - int(4*s), int(head_y) + int(1*s), int(8*s), int(5*s)))
        # Eyes - glowing red
        eye_g = int(200 + 55 * glow)
        pygame.draw.circle(gs.screen, (eye_g, 0, 0), (sx - int(3*s), int(head_y) - int(2*s)), int(2*s))
        pygame.draw.circle(gs.screen, (eye_g, 0, 0), (sx + int(3*s), int(head_y) - int(2*s)), int(2*s))
        # Mouth
        pygame.draw.line(gs.screen, (100, 50, 50), (sx - int(3*s), int(head_y) + int(4*s)), (sx + int(3*s), int(head_y) + int(4*s)), int(2*s))
        # Tail
        tail_wag = math.sin(tick * 0.008) * int(6*s)
        pygame.draw.line(gs.screen, col, (sx, body_y + int(2*s)), (sx + int(14*s) + tail_wag, body_y - int(12*s)), int(3*s))
        # Ears
        pygame.draw.polygon(gs.screen, (150, 100, 50), [
            (sx - int(5*s), int(head_y) - hr + int(2*s)),
            (sx - int(8*s), int(head_y) - hr - int(8*s)),
            (sx - int(2*s), int(head_y) - hr + int(1*s))
        ])
        pygame.draw.polygon(gs.screen, (150, 100, 50), [
            (sx + int(5*s), int(head_y) - hr + int(2*s)),
            (sx + int(8*s), int(head_y) - hr - int(8*s)),
            (sx + int(2*s), int(head_y) - hr + int(1*s))
        ])

    elif t == 'pinkie':
        pulse = 1 + math.sin(tick * 0.004) * 0.06
        bw, bh = int(30*s*pulse), int(26*s*pulse)
        body_y = by - bh - int(18*s) + bob
        # Legs - long muscular demon legs
        l_swing = int(6*s * walk)
        leg_col = (max(col[0]-40, 50), max(col[1]-20, 30), max(col[2]-20, 50))
        # Back legs (idx 0,3) swing opposite to front legs (idx 1,2)
        swing = [l_swing, -l_swing, -l_swing, l_swing]
        leg_dx = [-int(10*s), -int(5*s), int(5*s), int(10*s)]
        for i in range(4):
            lx = sx + leg_dx[i] + swing[i]
            pygame.draw.line(gs.screen, leg_col, (sx + leg_dx[i], by - int(18*s) + bob), (lx, by), int(5*s))
            # Hoof
            pygame.draw.ellipse(gs.screen, (60, 20, 40), (lx - int(3*s), by - int(3*s), int(6*s), int(4*s)))
        # Body - muscular
        pygame.draw.ellipse(gs.screen, col, (sx - bw//2, body_y, bw, bh))
        # Belly
        pygame.draw.ellipse(gs.screen, (255, 150, 200), (sx - int(6*s), body_y + int(6*s), int(12*s), int(12*s)))
        # Back spikes
        for i in range(3):
            sp_x = sx - int(8*s) + i * int(8*s)
            sp_y = body_y + int(2*s) + i * int(4*s)
            pygame.draw.polygon(gs.screen, (100, 20, 60), [
                (sp_x, sp_y), (sp_x + int(2*s), sp_y - int(6*s)), (sp_x + int(4*s), sp_y)
            ])
        # Head
        hr = int(12*s)
        head_y = body_y - hr + int(2*s)
        pygame.draw.circle(gs.screen, col, (sx, int(head_y)), hr)
        # Muscular neck
        pygame.draw.rect(gs.screen, col, (sx - int(5*s), int(head_y) + int(4*s), int(10*s), int(6*s)))
        # Angry eyes - narrow slits
        eye_g = int(180 + 75 * glow)
        for ex in [-int(5*s), int(5*s)]:
            pygame.draw.ellipse(gs.screen, (255, 200, 0), (sx + ex - int(3*s), int(head_y) - int(3*s), int(6*s), int(4*s)))
            pygame.draw.circle(gs.screen, (0, 0, 0), (sx + ex, int(head_y) - int(1*s)), int(2*s))
        # Angry brows
        for dx in [-1, 1]:
            pygame.draw.line(gs.screen, (80, 20, 40), (sx + dx*int(8*s), int(head_y) - int(5*s)),
                           (sx + dx*int(2*s), int(head_y) - int(3*s)), int(2*s))
        # Mouth with sharp teeth
        mouth_w = int(10*s)
        mouth_open = int(4*s * (1 + math.sin(tick * 0.006)))
        pygame.draw.ellipse(gs.screen, (150, 0, 50), (sx - mouth_w//2, int(head_y) + int(5*s), mouth_w, mouth_open))
        # Teeth - sharp triangles
        for tx in range(-int(4*s), int(4*s), int(2*s)):
            pygame.draw.polygon(gs.screen, (255, 255, 255), [
                (sx + tx, int(head_y) + int(5*s)),
                (sx + tx + int(2*s), int(head_y) + int(5*s)),
                (sx + tx + int(1*s), int(head_y) + int(5*s) - int(3*s))
            ])
        # Lower fangs
        for tx in (-int(3*s), int(3*s)):
            pygame.draw.polygon(gs.screen, (255, 255, 255), [
                (sx + tx, int(head_y) + int(5*s) + mouth_open),
                (sx + tx + int(2*s), int(head_y) + int(5*s) + mouth_open),
                (sx + tx + int(1*s), int(head_y) + int(5*s) + mouth_open + int(2*s))
            ])
        # Horns - long, twisted
        for dx in [-1, 1]:
            hx = sx + dx*int(9*s)
            hy = head_y - int(2*s)
            pygame.draw.line(gs.screen, (180, 180, 180), (hx, hy),
                           (hx + dx*int(8*s), hy - int(16*s) - int(3*s*glow)), int(4*s))
            # Horn tip glow
            tip_g = int(200 + 55 * glow)
            pygame.draw.circle(gs.screen, (tip_g, tip_g, tip_g), (hx + dx*int(8*s), int(hy - int(16*s) - int(3*s*glow))), int(3*s))
        # Arm/claw details
        for dx in [-1, 1]:
            ax = sx + dx*int(14*s)
            ay = body_y + int(16*s)
            pygame.draw.line(gs.screen, col, (sx + dx*int(8*s), ay), (ax, ay + int(8*s)), int(4*s))
            # Claws
            for c in range(3):
                cx = ax + dx*int(2*s*c)
                pygame.draw.line(gs.screen, (200, 200, 200), (cx, ay + int(8*s)),
                               (cx + dx*int(3*s), ay + int(12*s) + int(2*s*c)), int(2*s))

    elif t == 'spectre':
        vis = 0.3 + 0.7 * (0.5 + 0.5 * math.sin(tick * 0.01))
        bw, bh = int(28*s), int(24*s)
        body_y = by - bh - int(18*s) + bob
        l_swing = int(6*s * walk)
        for dx in [-int(9*s) + l_swing, -int(3*s) - l_swing, int(3*s) - l_swing, int(9*s) + l_swing]:
            pygame.draw.line(gs.screen, (30, 50, 40), (sx + dx, by - int(18*s) + bob), (sx + dx, by), int(5*s))
        surf = pygame.Surface((bw + int(8*s), bh + int(8*s)), pygame.SRCALPHA)
        pygame.draw.ellipse(surf, (int(60*vis), int(80*vis), int(70*vis), int(180*vis)), (4, 4, bw, bh))
        pygame.draw.circle(surf, (int(180*vis), int(80*vis), int(120*vis), int(200*vis)),
                          (bw//2 + 4, int(bh*0.3)), int(10*s))
        eye_r = int(4*s)
        pygame.draw.circle(surf, (int(255*vis), int(50*vis), int(50*vis), int(220*vis)),
                          (bw//2 - int(4*s) + 4, int(bh*0.3) - int(1*s)), eye_r)
        pygame.draw.circle(surf, (int(255*vis), int(50*vis), int(50*vis), int(220*vis)),
                          (bw//2 + int(4*s) + 4, int(bh*0.3) - int(1*s)), eye_r)
        try:
            gs.screen.blit(surf, (sx - bw//2 - 4, body_y - 4), special_flags=pygame.BLEND_ALPHA_SDL2)
        except:
            gs.screen.blit(surf, (sx - bw//2 - 4, body_y - 4))

    elif t == 'cacodemon':
        float_off = math.sin(tick * 0.002) * int(8*s)
        r = int(24*s)
        cy = _RY + _RH // 2 + float_off
        # Outer glow
        for gr in range(r + int(8*s), r, -4):
            a = int(100 * (1 - (gr - r) / 8))
            pygame.draw.circle(gs.screen, (col[0], col[1], col[2], 0), (sx, int(cy)), gr, 2)
        # Body
        pygame.draw.circle(gs.screen, col, (sx, int(cy)), r)
        # Belly
        pygame.draw.circle(gs.screen, (60, 200, 220), (sx, int(cy) + int(4*s)), int(r * 0.6))
        # Main eye
        eye_r = int(12*s)
        pygame.draw.circle(gs.screen, (255, 255, 0), (sx, int(cy)), eye_r)
        # Pupil
        pupil_off = int(5*s * math.sin(tick * 0.004))
        pygame.draw.circle(gs.screen, (0, 0, 0), (sx + pupil_off, int(cy)), int(6*s))
        # Eye highlight
        pygame.draw.circle(gs.screen, (255, 255, 255), (sx + pupil_off - int(2*s), int(cy) - int(2*s)), int(2*s))
        # Horns
        for dx in [-1, 1]:
            pygame.draw.line(gs.screen, (200, 200, 200), (sx + dx*int(10*s), int(cy - r + int(6*s))),
                           (sx + dx*int(18*s), int(cy - r - int(20*s))), int(4*s))
            pygame.draw.circle(gs.screen, (255, 100, 100), (sx + dx*int(18*s), int(cy - r - int(20*s))), int(3*s))
        # Tentacles
        for i in range(3):
            a = tick * 0.003 + i * 2.094
            tx = sx + int(math.cos(a) * r * 0.7)
            ty = int(cy + math.sin(a) * r * 0.7)
            tl = int(14*s)
            ex = tx + int(math.cos(a) * tl)
            ey = ty + int(math.sin(a) * tl)
            pygame.draw.line(gs.screen, col, (tx, ty), (ex, ey), int(4*s))
            pygame.draw.circle(gs.screen, (60, 200, 220), (ex, ey), int(3*s))

    elif t == 'lost_soul':
        float_off = math.sin(tick * 0.003) * int(10*s)
        r = int(10*s)
        cy = _RY + _RH // 2 + float_off
        fire = (200 + 55 * glow, 100 + 100 * glow, 50)
        pygame.draw.circle(gs.screen, (30, 30, 30), (sx, int(cy)), r)
        pygame.draw.circle(gs.screen, (80, 60, 40), (sx, int(cy)), r - int(2*s))
        for dx in (-1, 1):
            pygame.draw.circle(gs.screen, (255, 255, 200), (sx + dx*int(5*s), int(cy) - int(2*s)), int(2*s))
            pygame.draw.circle(gs.screen, (0, 0, 0), (sx + dx*int(5*s), int(cy) - int(2*s)), int(1*s))
        # Mouth flame
        pygame.draw.ellipse(gs.screen, fire, (sx - int(4*s), int(cy) + int(2*s), int(8*s), int(5*s)))
        # Fire trail
        for i in range(3):
            fy = int(cy) + r + i*int(5*s)
            fr = int(4*s) - i*int(1*s)
            pygame.draw.circle(gs.screen, (255, 150+50*glow, 50), (sx, fy + int(3*s)), fr)

    elif t == 'pain_elemental':
        float_off = math.sin(tick * 0.002) * int(12*s)
        r = int(20*s)
        cy = _RY + _RH // 2 + float_off
        pygame.draw.circle(gs.screen, col, (sx, int(cy)), r)
        pygame.draw.circle(gs.screen, (100, 40, 140), (sx, int(cy)), r - int(4*s))
        pygame.draw.circle(gs.screen, (200, 50, 50), (sx, int(cy)), int(8*s))
        for dx in (-1, 1):
            pygame.draw.circle(gs.screen, (0, 0, 0), (sx + dx*int(3*s), int(cy)), int(2*s))
        for dx in (-1, 1):
            pt = sx + dx*int(18*s)
            pygame.draw.line(gs.screen, col, (sx + dx*int(14*s), int(cy)), (pt, int(cy) - int(6*s)), int(4*s))
            pygame.draw.circle(gs.screen, (255, 100, 50), (pt, int(cy) - int(6*s)), int(4*s))
        for i in range(4):
            a = tick * 0.004 + i * 1.57
            tx = sx + int(math.cos(a) * r * 0.6)
            ty = int(cy + math.sin(a) * r * 0.6)
            tl = int(10*s)
            ex = tx + int(math.cos(a) * tl)
            ey = ty + int(math.sin(a) * tl)
            pygame.draw.line(gs.screen, (120, 40, 160), (tx, ty), (ex, ey), int(3*s))
            pygame.draw.circle(gs.screen, (200, 100, 50), (ex, ey), int(3*s))

    elif t == 'mancubus':
        wobble = 1 + math.sin(tick * 0.003) * 0.04
        bw, bh = int(52*s*wobble), int(36*s)
        body_y = by - bh - int(18*s) + bob
        # Legs
        l_swing = int(6*s * walk)
        for dx in [-int(16*s) + l_swing, int(16*s) - l_swing]:
            pygame.draw.line(gs.screen, col, (sx + dx, by - int(18*s) + bob), (sx + dx, by), int(6*s))
        # Body
        pygame.draw.ellipse(gs.screen, col, (sx - bw//2, body_y, bw, bh))
        # Armor plates
        for ax in range(-1, 2, 2):
            pygame.draw.ellipse(gs.screen, (140, 30, 30), (sx + ax*int(14*s) - int(8*s), body_y + int(6*s), int(16*s), int(20*s)))
        # Belly
        pygame.draw.ellipse(gs.screen, (140, 40, 40), (sx - int(12*s), body_y + int(10*s), int(24*s), int(18*s)))
        # Head
        hr = int(11*s)
        head_y = body_y - hr
        pygame.draw.circle(gs.screen, (140, 60, 60), (sx, int(head_y)), hr)
        # Jaw
        pygame.draw.ellipse(gs.screen, (120, 40, 40), (sx - int(6*s), int(head_y) + int(3*s), int(12*s), int(8*s)))
        # Glowing eyes
        eye_glow = int(255 * glow)
        pygame.draw.circle(gs.screen, (eye_glow, 50, 50), (sx - int(5*s), int(head_y)), int(4*s))
        pygame.draw.circle(gs.screen, (eye_glow, 50, 50), (sx + int(5*s), int(head_y)), int(4*s))
        pygame.draw.circle(gs.screen, (255, 255, 100), (sx - int(5*s), int(head_y)), int(2*s))
        pygame.draw.circle(gs.screen, (255, 255, 100), (sx + int(5*s), int(head_y)), int(2*s))
        # Cannons
        cannon_flash = (255, 200, 100) if tick % 2500 < 200 else (80, 80, 80)
        pygame.draw.rect(gs.screen, (100, 100, 100), (sx - bw//2 - int(12*s), body_y + int(6*s), int(14*s), int(16*s)))
        pygame.draw.rect(gs.screen, cannon_flash, (sx - bw//2 - int(14*s), body_y + int(6*s), int(4*s), int(16*s)))
        pygame.draw.rect(gs.screen, (100, 100, 100), (sx + bw//2 - int(2*s), body_y + int(6*s), int(14*s), int(16*s)))
        pygame.draw.rect(gs.screen, cannon_flash, (sx + bw//2 + int(10*s), body_y + int(6*s), int(4*s), int(16*s)))

    elif t == 'hell_knight':
        bw, bh = int(36*s), int(44*s)
        body_y = by - bh - int(18*s) + bob
        l_swing = int(5*s * walk)
        for dx in [-int(14*s) + l_swing, int(14*s) - l_swing]:
            pygame.draw.line(gs.screen, (100, 50, 30), (sx + dx, by - int(18*s) + bob + bh), (sx + dx, by), int(6*s))
        for dx in [-int(14*s), int(14*s)]:
            pygame.draw.ellipse(gs.screen, (80, 30, 10), (sx + dx - int(3*s), by - int(3*s), int(6*s), int(4*s)))
        pygame.draw.rect(gs.screen, col, (sx - bw//2, body_y, bw, bh))
        pygame.draw.rect(gs.screen, (100, 60, 30), (sx - int(8*s), body_y + int(8*s), int(16*s), int(16*s)))
        hr = int(13*s)
        head_y = body_y - hr + int(4*s)
        pygame.draw.circle(gs.screen, (120, 70, 40), (sx, int(head_y)), hr)
        pygame.draw.arc(gs.screen, (100, 50, 30), (sx - hr, int(head_y) - hr, hr*2, hr*2), -math.pi*0.3, math.pi*0.3, int(3*s))
        eye_glow = int(255 * glow)
        pygame.draw.circle(gs.screen, (eye_glow, 50, 0), (sx - int(5*s), int(head_y)), int(3*s))
        pygame.draw.circle(gs.screen, (eye_glow, 50, 0), (sx + int(5*s), int(head_y)), int(3*s))
        pygame.draw.circle(gs.screen, (255, 200, 100), (sx - int(5*s), int(head_y)), int(1*s))
        pygame.draw.circle(gs.screen, (255, 200, 100), (sx + int(5*s), int(head_y)), int(1*s))
        for dx in [-1, 1]:
            pygame.draw.line(gs.screen, (140, 140, 140), (sx + dx*int(10*s), head_y + int(2*s)),
                           (sx + dx*int(16*s), head_y - int(10*s)), int(4*s))
        for dx in [-1, 1]:
            pygame.draw.line(gs.screen, (120, 70, 40), (sx + dx*int(14*s), body_y + int(10*s)),
                           (sx + dx*int(20*s), body_y + int(24*s)), int(4*s))
            pygame.draw.circle(gs.screen, (140, 60, 30), (sx + dx*int(20*s), body_y + int(24*s)), int(4*s))

    elif t == 'revenant':
        bw, bh = int(20*s), int(48*s)
        body_y = by - bh - int(18*s) + bob
        l_swing = int(4*s * walk)
        for dx in [-int(8*s) + l_swing, int(8*s) - l_swing]:
            pygame.draw.line(gs.screen, (160, 140, 130), (sx + dx, by - int(18*s) + bob), (sx + dx, by), int(4*s))
        pygame.draw.rect(gs.screen, col, (sx - bw//2, body_y, bw, bh))
        pygame.draw.rect(gs.screen, (160, 150, 140), (sx - int(6*s), body_y + int(8*s), int(12*s), int(18*s)))
        # Ribcage
        for ri in range(4):
            ry = body_y + int(10*s) + ri*int(5*s)
            pygame.draw.line(gs.screen, (120, 100, 90), (sx - int(6*s), ry), (sx + int(6*s), ry), int(2*s))
        hr = int(10*s)
        head_y = body_y - hr + int(2*s)
        pygame.draw.circle(gs.screen, (200, 190, 180), (sx, int(head_y)), hr)
        # Skull face
        pygame.draw.circle(gs.screen, (160, 150, 140), (sx, int(head_y)), hr - int(2*s))
        eye_glow = int(255 * glow)
        pygame.draw.circle(gs.screen, (eye_glow, 0, 0), (sx - int(3*s), int(head_y) - int(1*s)), int(2*s))
        pygame.draw.circle(gs.screen, (eye_glow, 0, 0), (sx + int(3*s), int(head_y) - int(1*s)), int(2*s))
        pygame.draw.circle(gs.screen, (255, 200, 200), (sx - int(3*s), int(head_y) - int(1*s)), int(1*s))
        pygame.draw.circle(gs.screen, (255, 200, 200), (sx + int(3*s), int(head_y) - int(1*s)), int(1*s))
        # Rocket launcher on shoulder
        for dx in [-1, 1]:
            rx = sx + dx*int(10*s)
            ry = body_y + int(6*s)
            pygame.draw.rect(gs.screen, (80, 80, 80), (rx - int(3*s), ry, int(6*s), int(16*s)))
            pygame.draw.rect(gs.screen, (60, 60, 60), (rx - int(2*s), ry - int(2*s), int(4*s), int(4*s)))
            pygame.draw.circle(gs.screen, (150, 100, 50), (rx, ry + int(14*s)), int(3*s))

    elif t == 'boss_baron':
        bw, bh = int(42*s), int(52*s)
        body_y = by - bh - int(20*s) + bob
        # Legs
        l_swing = int(6*s * walk)
        for dx in [-int(16*s) + l_swing, int(16*s) - l_swing]:
            pygame.draw.line(gs.screen, col, (sx + dx, by - int(20*s) + bob + bh), (sx + dx, by), int(7*s))
        # Hooves
        for dx in [-int(16*s), int(16*s)]:
            pygame.draw.ellipse(gs.screen, (150, 0, 0), (sx + dx - int(4*s), by - int(4*s), int(8*s), int(5*s)))
        # Outer glow
        glow_r = int(20 + 60 * glow)
        pygame.draw.rect(gs.screen, (glow_r, 30, 30), (sx - bw//2 - int(3*s), body_y - int(3*s), bw + int(6*s), bh + int(6*s)))
        pygame.draw.rect(gs.screen, col, (sx - bw//2, body_y, bw, bh))
        # Chest armor
        pygame.draw.rect(gs.screen, (180, 50, 50), (sx - int(10*s), body_y + int(10*s), int(20*s), int(20*s)))
        # Head
        hr = int(15*s)
        head_y = body_y - hr + int(4*s)
        pygame.draw.circle(gs.screen, (200, 100, 100), (sx, int(head_y)), hr)
        # Crown/horns detail
        pygame.draw.arc(gs.screen, (150, 50, 50), (sx - hr, int(head_y) - hr, hr*2, hr*2), -math.pi*0.3, math.pi*0.3, int(3*s))
        # Glowing eyes
        eye_glow = int(255 * glow)
        pygame.draw.circle(gs.screen, (eye_glow, 0, 0), (sx - int(6*s), int(head_y)), int(4*s))
        pygame.draw.circle(gs.screen, (eye_glow, 0, 0), (sx + int(6*s), int(head_y)), int(4*s))
        pygame.draw.circle(gs.screen, (255, 200, 100), (sx - int(6*s), int(head_y)), int(2*s))
        pygame.draw.circle(gs.screen, (255, 200, 100), (sx + int(6*s), int(head_y)), int(2*s))
        # Horns
        for dx in [-1, 1]:
            pygame.draw.line(gs.screen, (180, 180, 180), (sx + dx*int(12*s), head_y + int(4*s)),
                           (sx + dx*int(20*s), head_y - int(14*s)), int(5*s))
        # Arms
        for dx in [-1, 1]:
            pygame.draw.line(gs.screen, (200, 100, 100), (sx + dx*int(18*s), body_y + int(12*s)),
                           (sx + dx*int(24*s), body_y + int(28*s)), int(5*s))
            # Fists
            pygame.draw.circle(gs.screen, (220, 80, 80), (sx + dx*int(24*s), body_y + int(28*s)), int(5*s))

    elif t == 'boss_cyber':
        bw, bh = int(56*s), int(64*s)
        body_y = by - bh - int(28*s) + bob
        pulse = int(40 + 60 * glow)
        # Legs
        l_swing = int(5*s * walk)
        for dx in [-int(22*s) + l_swing, int(22*s) - l_swing]:
            pygame.draw.line(gs.screen, (60, 60, 60), (sx + dx, by - int(28*s) + bob + int(10*s)), (sx + dx, by), int(8*s))
            pygame.draw.rect(gs.screen, (100, 100, 100), (sx + dx - int(6*s), by - int(6*s), int(12*s), int(6*s)))
        # Outer rings
        for rng in range(4, 0, -1):
            c = max(0, 255 - rng * 60)
            pygame.draw.rect(gs.screen, (c, c, c), (sx - bw//2 - rng*3, body_y - rng*3, bw + rng*6, bh + rng*6), 1)
        # Body
        pygame.draw.rect(gs.screen, col, (sx - bw//2, body_y, bw, bh))
        # Chest panel
        pygame.draw.rect(gs.screen, (50, 50, 55), (sx - int(12*s), body_y + int(8*s), int(24*s), int(24*s)))
        # Chest glow
        for gy in range(4):
            g_alpha = int(100 + 155 * (0.5 + 0.5 * math.sin(tick * 0.005 + gy)))
            pygame.draw.rect(gs.screen, (g_alpha, g_alpha // 3, 0), (sx - int(8*s) + gy * int(4*s), body_y + int(12*s), int(3*s), int(16*s)))
        # Head
        hr = int(16*s)
        head_y = body_y - hr + int(4*s)
        pygame.draw.circle(gs.screen, (100, 100, 120), (sx, int(head_y)), hr)
        # Faceplate
        pygame.draw.circle(gs.screen, (60, 60, 80), (sx, int(head_y)), hr - int(3*s))
        # Visor
        pygame.draw.rect(gs.screen, (pulse, 0, 0), (sx - int(8*s), int(head_y) - int(2*s), int(16*s), int(4*s)))
        # Red eye glow
        pygame.draw.circle(gs.screen, (pulse, 0, 0), (sx - int(9*s), int(head_y)), int(6*s))
        pygame.draw.circle(gs.screen, (pulse, 0, 0), (sx + int(9*s), int(head_y)), int(6*s))
        pygame.draw.circle(gs.screen, (255, 100, 100), (sx - int(9*s), int(head_y)), int(3*s))
        pygame.draw.circle(gs.screen, (255, 100, 100), (sx + int(9*s), int(head_y)), int(3*s))
        # Shoulder cannons
        cannon_fire = (255, 200, 50) if tick % 3500 < 100 else (80, 80, 80)
        pygame.draw.rect(gs.screen, (100, 100, 100), (sx - bw//2 - int(16*s), body_y + int(8*s), int(18*s), int(22*s)))
        pygame.draw.rect(gs.screen, cannon_fire, (sx - bw//2 - int(20*s), body_y + int(8*s), int(6*s), int(22*s)))
        pygame.draw.rect(gs.screen, (100, 100, 100), (sx + bw//2 - int(2*s), body_y + int(8*s), int(18*s), int(22*s)))
        pygame.draw.rect(gs.screen, cannon_fire, (sx + bw//2 + int(14*s), body_y + int(8*s), int(6*s), int(22*s)))

    elif t == 'boss_spider':
        wobble = 1 + math.sin(tick * 0.002) * 0.03
        bw, bh = int(60*s*wobble), int(34*s*wobble)
        body_y = by - bh - int(22*s) + bob
        # Legs (8 legs)
        for i in range(8):
            a = tick * 0.005 + i * 0.785
            phase = 1 if i < 4 else -1
            lx = sx + int(math.cos(a) * bw * 0.4 * phase)
            ly = int(body_y + bh//2 + math.sin(a) * bh * 0.3)
            leg_len = int(24*s)
            ex = lx + int(math.cos(a * 0.8) * leg_len)
            ey = ly + int(math.sin(a * 0.8) * leg_len)
            pygame.draw.line(gs.screen, (100, 0, 120), (lx, ly), (ex, ey), int(3*s))
            pygame.draw.circle(gs.screen, (150, 0, 170), (ex, ey), int(2*s))
        # Outer rings
        for rng in range(4, 0, -1):
            col_v = max(0, 200 - rng * 40)
            pygame.draw.ellipse(gs.screen, (col_v * 2 // 3, 0, col_v), (sx - bw//2 - rng*2, body_y - rng*2, bw + rng*4, bh + rng*4), 1)
        # Body
        pygame.draw.ellipse(gs.screen, col, (sx - bw//2, body_y, bw, bh))
        # Body pattern
        pygame.draw.ellipse(gs.screen, (120, 0, 140), (sx - int(10*s), body_y + int(4*s), int(20*s), int(14*s)))
        # Head
        hr = int(12*s)
        head_y = body_y - hr + int(2*s)
        pygame.draw.circle(gs.screen, (200, 50, 220), (sx, int(head_y)), hr)
        # Multiple eyes
        eye_glow = int(255 * glow)
        for ex_i, ex in enumerate([-int(6*s), -int(2*s), int(2*s), int(6*s)]):
            e_r = int(3*s) if ex_i % 2 == 0 else int(2*s)
            pygame.draw.circle(gs.screen, (eye_glow, 0, eye_glow), (sx + ex, int(head_y)), e_r)
            pygame.draw.circle(gs.screen, (255, 255, 255), (sx + ex, int(head_y)), e_r // 2)
        # Mandibles
        for dx in [-int(4*s), int(4*s)]:
            pygame.draw.line(gs.screen, (150, 0, 170), (sx + dx, int(head_y) + int(4*s)),
                           (sx + dx, int(head_y) + int(8*s) + int(3*s*glow)), int(3*s))

    elif t == 'arachnotron':
        wobble = 1 + math.sin(tick * 0.002) * 0.03
        bw, bh = int(40*s*wobble), int(24*s*wobble)
        body_y = by - bh - int(18*s) + bob
        for i in range(6):
            a = tick * 0.004 + i * 1.047
            phase = 1 if i < 3 else -1
            lx = sx + int(math.cos(a) * bw * 0.35 * phase)
            ly = int(body_y + bh//2 + math.sin(a) * bh * 0.3)
            leg_len = int(18*s)
            ex = lx + int(math.cos(a * 0.8) * leg_len)
            ey = ly + int(math.sin(a * 0.8) * leg_len)
            pygame.draw.line(gs.screen, (40, 100, 40), (lx, ly), (ex, ey), int(3*s))
            pygame.draw.circle(gs.screen, (80, 140, 80), (ex, ey), int(2*s))
        for rng in range(3, 0, -1):
            c = max(0, 180 - rng * 40)
            pygame.draw.ellipse(gs.screen, (c, c*2//3, c*2//3), (sx - bw//2 - rng*2, body_y - rng*2, bw + rng*4, bh + rng*4), 1)
        pygame.draw.ellipse(gs.screen, col, (sx - bw//2, body_y, bw, bh))
        pygame.draw.ellipse(gs.screen, (40, 100, 40), (sx - int(8*s), body_y + int(4*s), int(16*s), int(10*s)))
        hr = int(10*s)
        head_y = body_y - hr + int(2*s)
        pygame.draw.circle(gs.screen, (80, 160, 80), (sx, int(head_y)), hr)
        eye_glow = int(255 * glow)
        pygame.draw.circle(gs.screen, (eye_glow, 0, 0), (sx - int(4*s), int(head_y)), int(3*s))
        pygame.draw.circle(gs.screen, (eye_glow, 0, 0), (sx + int(4*s), int(head_y)), int(3*s))
        pygame.draw.circle(gs.screen, (255, 255, 100), (sx - int(4*s), int(head_y)), int(1*s))
        pygame.draw.circle(gs.screen, (255, 255, 100), (sx + int(4*s), int(head_y)), int(1*s))
        # Chaingun
        cannon_flash = (255, 200, 100) if tick % 2000 < 100 else (80, 80, 80)
        pygame.draw.rect(gs.screen, (100, 100, 100), (sx - bw//2 - int(10*s), body_y + int(4*s), int(12*s), int(10*s)))
        pygame.draw.rect(gs.screen, cannon_flash, (sx - bw//2 - int(12*s), body_y + int(4*s), int(4*s), int(10*s)))

    elif str(t).startswith('custom:'):
        # Generic custom monster
        bw, bh = int(18*s), int(36*s)
        body_y = by - bh - int(12*s) + bob
        # Body
        pygame.draw.rect(gs.screen, col, (sx - bw//2, body_y, bw, bh), border_radius=int(6*s))
        # Head
        hr = int(10*s)
        head_y = body_y - hr + int(4*s)
        pygame.draw.circle(gs.screen, col, (sx, int(head_y)), hr)
        # Eyes
        eye_col = (255, 255, 200)
        pygame.draw.circle(gs.screen, eye_col, (sx - int(4*s), int(head_y)), int(2*s))
        pygame.draw.circle(gs.screen, eye_col, (sx + int(4*s), int(head_y)), int(2*s))
        pygame.draw.circle(gs.screen, (0, 0, 0), (sx - int(4*s), int(head_y)), int(1*s))
        pygame.draw.circle(gs.screen, (0, 0, 0), (sx + int(4*s), int(head_y)), int(1*s))
        # Arms
        arm_swing = int(6*s * walk)
        pygame.draw.line(gs.screen, col, (sx - bw//2, body_y + int(6*s)), (sx - bw//2 - int(10*s) + arm_swing, body_y + int(20*s)), int(4*s))
        pygame.draw.line(gs.screen, col, (sx + bw//2, body_y + int(6*s)), (sx + bw//2 + int(10*s) - arm_swing, body_y + int(20*s)), int(4*s))
        # Legs
        l_swing = int(5*s * walk)
        pygame.draw.line(gs.screen, col, (sx - int(4*s), body_y + bh), (sx - int(6*s) + l_swing, by), int(4*s))
        pygame.draw.line(gs.screen, col, (sx + int(4*s), body_y + bh), (sx + int(6*s) - l_swing, by), int(4*s))

    elif t == 'archvile':
        sway = math.sin(tick * 0.003) * int(4*s)
        bw, bh = int(18*s), int(52*s)
        body_x = sx + sway
        body_y = by - bh - int(14*s) + bob
        # Ghostly robe
        pygame.draw.polygon(gs.screen, col, [
            (body_x - bw//2, body_y + bh),
            (body_x - bw//2 - int(4*s), body_y + int(10*s)),
            (body_x, body_y),
            (body_x + bw//2 + int(4*s), body_y + int(10*s)),
            (body_x + bw//2, body_y + bh)
        ])
        # Ghostly glow overlay
        g_alpha = int(40 + 60 * glow)
        pygame.draw.polygon(gs.screen, (200, 100, 200, 0) if False else (200, 100, 200), [
            (sx - bw//2, body_y + bh),
            (sx - bw//2, body_y + int(10*s)),
            (sx, body_y),
            (sx + bw//2, body_y + int(10*s)),
            (sx + bw//2, body_y + bh)
        ], int(2*s))
        # Head
        hr = int(10*s)
        head_y = body_y - hr
        pygame.draw.circle(gs.screen, (200, 180, 200), (int(body_x), int(head_y)), hr)
        # Hood
        pygame.draw.arc(gs.screen, col, (int(body_x) - hr, int(head_y) - hr, hr*2, hr*2), math.pi, 2*math.pi, int(3*s))
        # Glowing eyes
        eye_g = int(255 * glow)
        pygame.draw.circle(gs.screen, (eye_g, 0, 0), (int(body_x - int(3*s)), int(head_y)), int(3*s))
        pygame.draw.circle(gs.screen, (eye_g, 0, 0), (int(body_x + int(3*s)), int(head_y)), int(3*s))
        pygame.draw.circle(gs.screen, (255, 200, 200), (int(body_x - int(3*s)), int(head_y)), int(1*s))
        pygame.draw.circle(gs.screen, (255, 200, 200), (int(body_x + int(3*s)), int(head_y)), int(1*s))
        # Arms with flame
        arm_phase = math.sin(tick * 0.003) * int(10*s)
        for dx in [-1, 1]:
            pygame.draw.line(gs.screen, col, (body_x + dx*bw//2, body_y + int(15*s)),
                           (body_x + dx*(bw//2 + int(18*s)) + arm_phase, body_y + int(5*s)), int(4*s))
            # Hand flame
            fx = body_x + dx*(bw//2 + int(18*s)) + arm_phase
            fy = body_y + int(5*s)
            flame_r = int(4*s + 3*s*glow)
            pygame.draw.circle(gs.screen, (eye_g, 100, 50), (int(fx), int(fy)), flame_r)
            pygame.draw.circle(gs.screen, (255, 200, 100), (int(fx), int(fy)), flame_r // 2)

    # Stun visual — dark shader
    if is_stunned:
        stun_alpha = int(60 + 40 * math.sin(tick * 0.02))
        stun_surf = pygame.Surface((int(30*s), int(sh * 0.8)), pygame.SRCALPHA)
        stun_surf.fill((20, 20, 60, stun_alpha))
        try:
            gs.screen.blit(stun_surf, (sx - int(15*s), _RY + _RH // 2 - int(sh * 0.4)), special_flags=pygame.BLEND_ALPHA_SDL2)
        except:
            gs.screen.blit(stun_surf, (sx - int(15*s), _RY + _RH // 2 - int(sh * 0.4)))


    # Damage flash overlay
    if e.get('d_flash', 0) > 0:
        fw, fh = int(sh * 0.4), int(sh * 0.6)
        flash_surf = pygame.Surface((fw, fh), pygame.SRCALPHA)
        alpha = min(180, e['d_flash'] * 36)
        flash_surf.fill((255, 50, 50, alpha))
        fy = by - sh // 4 - fh // 2
        try:
            gs.screen.blit(flash_surf, (sx - fw // 2, fy), special_flags=pygame.BLEND_ALPHA_SDL2)
        except:
            gs.screen.blit(flash_surf, (sx - fw // 2, fy))

    # Rush glow (red outline when player is low)
    if is_rushing:
        glow_size = int(sh * 0.3)
        glow_surf = pygame.Surface((glow_size * 2, sh + glow_size), pygame.SRCALPHA)
        pygame.draw.ellipse(glow_surf, (255, 30, 30, 40), (0, glow_size // 2, glow_size * 2, sh))
        try:
            gs.screen.blit(glow_surf, (sx - glow_size, by - sh), special_flags=pygame.BLEND_ALPHA_SDL2)
        except:
            gs.screen.blit(glow_surf, (sx - glow_size, by - sh))

    # Glowing eyes in dark areas
    if not is_dying and not is_rushing:
        eye_level = _RY + _RH // 2 - int(sh * 0.35)
        eye_spread = int(5 * s)
        glow_col = (255, 200, 100)
        edx2 = e['x'] - gs.px
        edy2 = e['y'] - gs.py
        dst2 = math.sqrt(edx2 ** 2 + edy2 ** 2)
        glow_alpha = max(0, min(180, int((dst2 - 100) * 0.4)))
        if glow_alpha > 20:
            for ex in (-eye_spread, eye_spread):
                sg = pygame.Surface((8, 8), pygame.SRCALPHA)
                pygame.draw.circle(sg, (*glow_col, glow_alpha), (4, 4), 4)
                try:
                    gs.screen.blit(sg, (sx + ex - 4, eye_level - 4), special_flags=pygame.BLEND_ALPHA_SDL2)
                except:
                    gs.screen.blit(sg, (sx + ex - 4, eye_level - 4))

    # HP bar
    mhp = e.get('max_hp', ENEMY_DB.get(t, {}).get('hp', 100))
    bar_w = int(22*s)
    bh_bar = int(4*s)
    by_bar = ground_y + (2 if t != 'cacodemon' else -int(8*s))
    pygame.draw.rect(gs.screen, (60, 0, 0), (sx - bar_w//2, by_bar, bar_w, bh_bar))
    if e['hp'] > 0:
        pygame.draw.rect(gs.screen, (0, 200, 0), (sx - bar_w//2, by_bar, int(bar_w * e['hp'] / mhp), bh_bar))


def draw_corpse(c, sx, sh):
    t = c['t']; col = c['col']
    s = max(sh / 64.0, 0.3)
    dc = tuple(max(0, x - 60) for x in col)
    dy = _RY + _RH // 2 + int(CAM_Z * sh / TILE) if not gs.sector_mode else _RY + _RH // 2 + sh // 2

    if t == 'soldier':
        pygame.draw.ellipse(gs.screen, dc, (sx - int(16*s), dy - int(4*s), int(32*s), int(8*s)))
        pygame.draw.circle(gs.screen, dc, (sx - int(16*s), dy - int(3*s)), int(5*s))
        pygame.draw.line(gs.screen, dc, (sx - int(4*s), dy), (sx - int(8*s), dy + int(4*s)), int(2*s))
        pygame.draw.line(gs.screen, dc, (sx + int(4*s), dy), (sx + int(8*s), dy + int(4*s)), int(2*s))
    elif t == 'shotgunguy':
        pygame.draw.ellipse(gs.screen, dc, (sx - int(16*s), dy - int(4*s), int(32*s), int(8*s)))
        pygame.draw.circle(gs.screen, dc, (sx - int(16*s), dy - int(3*s)), int(5*s))
        pygame.draw.line(gs.screen, dc, (sx + int(4*s), dy - int(1*s)), (sx + int(20*s), dy - int(3*s)), int(3*s))
    elif t == 'dog':
        pygame.draw.ellipse(gs.screen, dc, (sx - int(12*s), dy - int(4*s), int(24*s), int(6*s)))
        pygame.draw.circle(gs.screen, dc, (sx - int(14*s), dy - int(3*s)), int(4*s))
        for dx in [-int(6*s), -int(2*s), int(2*s), int(6*s)]:
            pygame.draw.line(gs.screen, dc, (sx + dx, dy), (sx + dx, dy + int(3*s)), int(2*s))
    elif t == 'pinkie':
        pygame.draw.ellipse(gs.screen, dc, (sx - int(18*s), dy - int(6*s), int(36*s), int(12*s)))
        pygame.draw.circle(gs.screen, dc, (sx - int(18*s), dy - int(5*s)), int(7*s))
        pygame.draw.line(gs.screen, dc, (sx - int(20*s), dy - int(8*s)), (sx - int(24*s), dy - int(12*s)), int(2*s))
        pygame.draw.line(gs.screen, dc, (sx - int(16*s), dy - int(8*s)), (sx - int(12*s), dy - int(12*s)), int(2*s))
        for dx in [-int(8*s), int(8*s)]:
            pygame.draw.line(gs.screen, dc, (sx + dx, dy), (sx + dx, dy + int(6*s)), int(4*s))
    elif t == 'cacodemon':
        pygame.draw.ellipse(gs.screen, dc, (sx - int(16*s), dy - int(4*s), int(32*s), int(8*s)))
    elif t == 'mancubus':
        pygame.draw.ellipse(gs.screen, dc, (sx - int(24*s), dy - int(8*s), int(48*s), int(14*s)))
        pygame.draw.circle(gs.screen, dc, (sx - int(22*s), dy - int(6*s)), int(7*s))
        for dx in [-int(14*s), int(14*s)]:
            pygame.draw.line(gs.screen, dc, (sx + dx, dy), (sx + dx, dy + int(6*s)), int(4*s))
    elif t == 'archvile':
        pygame.draw.ellipse(gs.screen, dc, (sx - int(12*s), dy - int(12*s), int(24*s), int(22*s)))
        pygame.draw.circle(gs.screen, dc, (sx, dy - int(14*s)), int(6*s))
    elif t in ('chaingunner', 'imp', 'spectre', 'lost_soul', 'pain_elemental', 'hell_knight', 'revenant', 'arachnotron'):
        bw = {'chaingunner': 18, 'imp': 20, 'spectre': 22, 'lost_soul': 12,
              'pain_elemental': 24, 'hell_knight': 28, 'revenant': 18, 'arachnotron': 30}.get(t, 20)
        bh = {'chaingunner': 6, 'imp': 8, 'spectre': 8, 'lost_soul': 6,
              'pain_elemental': 10, 'hell_knight': 10, 'revenant': 8, 'arachnotron': 8}.get(t, 8)
        pygame.draw.ellipse(gs.screen, dc, (sx - int(bw*s), dy - int(bh*s), int(bw*2*s), int(bh*2*s)))


def _render_sector_scene():
    """3D sector renderer for sector-based maps with textured walls."""
    NC = const.W // SCALE
    pz = gs.pz + EYE_H
    px = gs.px; py = gs.py; pa = gs.pa
    verts = gs.sec_verts; lines = gs.sec_lines; sectors = gs.sec_sectors

    tex = gs.textures
    def_wall = tex.get('wall')
    if def_wall is None:
        def_wall = pygame.Surface((TEX_W, TEX_W))
        def_wall.fill((140, 120, 100))

    # Pre-compute line endpoints (skip open doors)
    line_data = []
    sec_line_door = getattr(gs, 'sec_line_door', {})
    open_doors = getattr(gs, 'open_doors', {})
    for li, ld in enumerate(lines):
        if li in sec_line_door:
            gx, gy = sec_line_door[li]
            if (gx, gy) in open_doors:
                continue
        v1i, v2i = ld[0], ld[1]
        if v1i < len(verts) and v2i < len(verts):
            line_data.append((verts[v1i][0], verts[v1i][1], verts[v2i][0], verts[v2i][1], ld))

    # Ceiling base
    pygame.draw.rect(gs.screen, (20, 20, 25), (0, 0, const.W, const.H // 2))
    # Floor base
    pygame.draw.rect(gs.screen, (30, 30, 35), (0, const.H // 2, const.W, const.H // 2))

    player_si = _player_in_any_sector(px, py)

    z_b = [999.0] * NC
    for i in range(NC):
        ra = pa - FOV / 2 + (i / NC) * FOV
        cs, sn = math.cos(ra), math.sin(ra)

        closest_d = gs.view_dist
        hit_ld = None
        hit_ix = hit_iy = 0
        hit_si = -1
        tex_u = i / NC

        end_x = px + cs * gs.view_dist
        end_y = py + sn * gs.view_dist

        for v1x, v1y, v2x, v2y, ld in line_data:
            dx1, dy1 = end_x - px, end_y - py
            dx2, dy2 = v2x - v1x, v2y - v1y
            denom = dx1 * dy2 - dy1 * dx2
            if abs(denom) < 0.0001:
                continue
            t = ((v1x - px) * dy2 - (v1y - py) * dx2) / denom
            u = ((v1x - px) * dy1 - (v1y - py) * dx1) / denom
            if 0 <= t <= 1 and 0 <= u <= 1:
                ix = px + t * dx1
                iy = py + t * dy1
                d2 = math.hypot(ix - px, iy - py)
                if d2 > 0.5 and d2 < closest_d:
                    closest_d = d2
                    hit_ld = ld
                    hit_ix, hit_iy = ix, iy
                    hit_si = ld[2] if ld[2] is not None and ld[2] >= 0 else (ld[3] if ld[3] is not None and ld[3] >= 0 else -1)

        rd = closest_d * math.cos(ra - pa)
        if rd < 1: rd = 1
        z_b[i] = rd

        wall_si = hit_si
        if hit_ld is not None:
            for ldi in line_data:
                if ldi[4] is hit_ld:
                    v1x, v1y, v2x, v2y = ldi[:4]
                    line_len = math.hypot(v2x - v1x, v2y - v1y) or 1
                    tex_u = math.hypot(hit_ix - v1x, hit_iy - v1y) / line_len
                    if hit_ld[2] is not None and hit_ld[2] >= 0 and hit_ld[3] is not None and hit_ld[3] >= 0:
                        mx = (v1x + v2x) / 2
                        my = (v1y + v2y) / 2
                        nx = -(v2y - v1y)
                        ny = v2x - v1x
                        nl = math.hypot(nx, ny) or 1
                        nx /= nl; ny /= nl
                        dot = (px - mx) * nx + (py - my) * ny
                        wall_si = hit_ld[2] if dot > 0 else hit_ld[3]
                    break

        if wall_si >= 0 and wall_si < len(sectors):
            sec = sectors[wall_si]
            fh = sec.get('fh', 0)
            ch = sec.get('ch', 128)
            fc = sec.get('fc', (80, 60, 40))
            cc = sec.get('cc', (60, 60, 120))
            wc = sec.get('wc', (140, 120, 100))
            if not isinstance(fc, (list, tuple)): fc = (fc, fc, fc)
            if not isinstance(cc, (list, tuple)): cc = (cc, cc, cc)
            if not isinstance(wc, (list, tuple)): wc = (wc, wc, wc)

            half_h = const.H / 2
            wt = int(half_h - (pz - ch) * 750 / rd)
            wb = int(half_h - (pz - fh) * 750 / rd)
            if wt > wb: wt, wb = wb, wt
            if wt > const.H: wt = const.H
            if wb < 0: wb = 0

            shade = max(0.15, 1.0 - closest_d / (gs.view_dist * 1.2))

            # Ceiling
            if wt > 0:
                ccol = tuple(min(255, int(v * shade)) for v in cc)
                pygame.draw.line(gs.screen, ccol, (i * SCALE, 0), (i * SCALE, wt))

            # Wall (textured column)
            if wb - wt > 0:
                tex_x = int(tex_u * TEX_W) % TEX_W
                try:
                    col_strip = def_wall.subsurface((tex_x, 0, 1, TEX_W))
                except:
                    col_strip = None
                if col_strip:
                    scaled = pygame.transform.scale(col_strip, (SCALE, wb - wt))
                    tint = pygame.Surface(scaled.get_size())
                    tint.fill(tuple(min(255, int(v * shade)) for v in wc))
                    scaled.blit(tint, (0, 0), special_flags=pygame.BLEND_RGB_MULT)
                    gs.screen.blit(scaled, (i * SCALE, wt))
                else:
                    wcol = tuple(min(255, int(v * shade)) for v in wc)
                    pygame.draw.line(gs.screen, wcol, (i * SCALE, wt), (i * SCALE, wb))

            # Floor
            if wb < const.H:
                fcol = tuple(min(255, int(v * shade)) for v in fc)
                pygame.draw.line(gs.screen, fcol, (i * SCALE, wb), (i * SCALE, const.H))
        else:
            if player_si >= 0:
                sec = sectors[player_si]
                fh = sec.get('fh', 0)
                ch = sec.get('ch', 128)
                fc = sec.get('fc', (80, 60, 40))
                cc = sec.get('cc', (60, 60, 120))
                if not isinstance(fc, (list, tuple)): fc = (fc, fc, fc)
                if not isinstance(cc, (list, tuple)): cc = (cc, cc, cc)
                half_h = const.H / 2
                wt = int(half_h - (pz - ch) * 750 / rd)
                wb = int(half_h - (pz - fh) * 750 / rd)
                if wt > wb: wt, wb = wb, wt
                if wt > const.H: wt = const.H
                if wb < 0: wb = 0
                shade = max(0.15, 1.0 - closest_d / (gs.view_dist * 1.2))
                if wt > 0:
                    ccol = tuple(min(255, int(v * shade)) for v in cc)
                    pygame.draw.line(gs.screen, ccol, (i * SCALE, 0), (i * SCALE, wt))
                if wb < const.H:
                    fcol = tuple(min(255, int(v * shade)) for v in fc)
                    pygame.draw.line(gs.screen, fcol, (i * SCALE, wb), (i * SCALE, const.H))
            else:
                pygame.draw.line(gs.screen, (30, 35, 45), (i * SCALE, 0), (i * SCALE, const.H))

    return z_b


def render(vh=None, vy=None):
    global _RH, _RY
    _RH = vh if vh is not None else const.H
    _RY = vy if vy is not None else 0
    curr_t = pygame.time.get_ticks()
    NC = const.W // SCALE

    # ── Sector mode — use vertex/line renderer ──
    if gs.sector_mode:
        z_b = _render_sector_scene()
    else:
        # ── Original tile-based rendering ──
        # Player eye height (DOOM standard: 41 units)
        pz = CAM_Z
        # Player current sector
        pgx, pgy = int(gs.px // TILE), int(gs.py // TILE)
        p_sector = None
        if gs.sector_grid and 0 <= pgy < len(gs.sector_grid) and 0 <= pgx < len(gs.sector_grid[0]):
            psi = gs.sector_grid[pgy][pgx]
            if psi is not None and psi < len(gs.sectors):
                p_sector = gs.sectors[psi]

        # Ray angles per column (shared by floor, ceiling, walls)
        ra_start = gs.pa - FOV / 2
        cos_ra = [0.0] * NC
        sin_ra = [0.0] * NC
        for i in range(NC):
            ra = ra_start + (i / NC) * FOV
            cos_ra[i] = math.cos(ra)
            sin_ra[i] = math.sin(ra)

        # Helper: project world Z to screen Y
        def _z_to_sy(world_z, dist):
            if dist < 1: dist = 1
            return int(_RY + _RH // 2 - (pz - world_z) * 750 / dist)

        # Helper: get flat texture by name
        def _flat(tex_name):
            if not tex_name:
                return None
            f = gs.flats.get(tex_name)
            if f is None:
                f = gs.textures.get('wall')
            return f

        # Ceiling — solid color base
        pygame.draw.rect(gs.screen, (20, 20, 25), (0, _RY, const.W, _RH // 2))
        # Floor — solid color base
        pygame.draw.rect(gs.screen, (30, 30, 35), (0, _RY + _RH // 2, const.W, _RH // 2))

        # Wall raycasting with textures
        z_b = [999.0] * NC
        for i in range(NC):
            ra = ra_start + (i / NC) * FOV
            c, s = cos_ra[i], sin_ra[i]
            d = 0
            hit_x, hit_y = 0, 0
            tile = '.'
            prev_tx, prev_ty = int(gs.px // TILE), int(gs.py // TILE)
            side = 0
            tex_x = 0
            while d < gs.view_dist:
                d += 6
                test_x = gs.px + c * d
                test_y = gs.py + s * d
                cur_tx, cur_ty = int(test_x // TILE), int(test_y // TILE)
                tile = get_t(test_x, test_y)
                if tile in {'#', 'R', 'Y', 'B', 'T', 'W', 'D', 'S', 'F', 'E', 'M', 'I', 'P', 'H', 'V', 'C', 'Q', 'L', 'K'}:
                    if tile in ('F', 'E') and cur_tx == int(gs.px // TILE) and cur_ty == int(gs.py // TILE):
                        prev_tx, prev_ty = cur_tx, cur_ty
                        continue
                    if tile == 'C' and (cur_tx, cur_ty) in gs.bridge_open:
                        prev_tx, prev_ty = cur_tx, cur_ty
                        continue
                    hit_x, hit_y = test_x, test_y
                    if cur_tx != prev_tx:
                        side = 0
                        tex_x = int((hit_y - cur_ty * TILE) / TILE * TEX_W) % TEX_W
                    else:
                        side = 1
                        tex_x = int((hit_x - cur_tx * TILE) / TILE * TEX_W) % TEX_W
                    break
                prev_tx, prev_ty = cur_tx, cur_ty

            rd = d * math.cos(ra - gs.pa)
            z_b[i] = rd
            # Sector-based wall height
            hit_si = None
            if gs.sector_grid and 0 <= cur_ty < len(gs.sector_grid) and 0 <= cur_tx < len(gs.sector_grid[0]):
                hit_si = gs.sector_grid[cur_ty][cur_tx]
            wh = (TILE * 750) / (rd + 0.1)  # default (non-sector) height
            draw_y = int(_RY + _RH//2 - wh//2 + (CAM_Z - TILE / 2) * 750 / (rd + 0.1))
            if hit_si is not None and hit_si < len(gs.sectors) and not tile in ('F', 'E'):
                sec = gs.sectors[hit_si]
                fh = sec['floor_h']
                ch = sec['ceil_h']
                wall_top = _z_to_sy(ch, rd)
                wall_bot = _z_to_sy(fh, rd)
                wh = wall_bot - wall_top
                if wh < 1: continue
                draw_y = wall_top
                # Portal: check behind wall for ceiling gap
                if wall_top > _RY:
                    d2 = d + 4
                    test_x2 = gs.px + c * d2
                    test_y2 = gs.py + s * d2
                    c2x, c2y = int(test_x2 // TILE), int(test_y2 // TILE)
                    if 0 <= c2y < len(gs.sector_grid) and 0 <= c2x < len(gs.sector_grid[0]):
                        si2 = gs.sector_grid[c2y][c2x]
                        if si2 is not None and si2 < len(gs.sectors):
                            sec2 = gs.sectors[si2]
                            if sec2['ceil_h'] > ch:
                                # Draw the gap as upper wall
                                gap_top = _z_to_sy(sec2['ceil_h'], rd)
                                gap_bot = wall_top
                                if gap_bot > gap_top and gap_bot > _RY and gap_top < _RY + _RH:
                                    if gap_top < _RY: gap_top = _RY
                                    if gap_bot > _RY + _RH: gap_bot = _RY + _RH
                                    hh = gap_bot - gap_top
                                    if hh > 0:
                                        # Draw the gap area with the floor color of the back sector
                                        gap_col = tuple(min(255, v) for v in sec2.get('fc', (80,60,40)))
                                        pygame.draw.rect(gs.screen, gap_col, (i * SCALE, gap_top, SCALE, hh))

            if wh <= 0:
                continue
            # Texture
            tx = gs.textures
            if tile == 'S':
                adj = [(cur_tx-1, cur_ty), (cur_tx+1, cur_ty), (cur_tx, cur_ty-1), (cur_tx, cur_ty+1)]
                adj_names = []
                for ax, ay in adj:
                    if 0 <= ay < gs.M_SZ and 0 <= ax < gs.M_SZ:
                        at = gs.MAP[ay][ax]
                        if at not in ('.', 'S', 'F', 'E', 'L', 'K', 'C'):
                            an = TILE_CHAR_TO_TEX.get(at, 'wall')
                            adj_names.append(an)
                tex_name = max(set(adj_names), key=adj_names.count) if adj_names else 'wall'
            else:
                tex_name = TILE_CHAR_TO_TEX.get(tile, 'wall')
            tex = tx.get(tex_name) or tx.get('wall')
            if tex is None:
                tex = pygame.Surface((TEX_W, TEX_W))
                tex.fill((40, 40, 40))
            br = 255
            try:
                tex_h = TEX_W
                if tile == 'C':
                    tex = tx.get('bridge_wall') or tx.get('wall')
                if tile == 'C' and (cur_tx, cur_ty) in gs.bridge_open:
                    tex_name = 'bridge' if (cur_tx, cur_ty) not in gs.bridge_open else 'floor'
                    tex = gs.flats.get(tex_name) or tx.get('wall')
                if tile == 'C' and (cur_tx, cur_ty) in gs.bridge_open:
                    wh = max(1, int(wh))
                    col_strip = pygame.Surface((1, wh))
                    col_strip.fill((100, 100, 100))
                    scaled = pygame.transform.scale(col_strip, (SCALE, wh))
                    gs.screen.blit(scaled, (i * SCALE, draw_y))
                    continue
                col_strip = tex.subsurface((tex_x % TEX_W, 0, 1, min(tex_h, tex.get_height())))
            except:
                col_strip = pygame.Surface((1, 1))
                col_strip.fill((br, br, br))
            try:
                scaled = pygame.transform.scale(col_strip, (SCALE, wh))
                tint = pygame.Surface(scaled.get_size())
                br = max(0, 255 - int(rd * 0.25))
                # Animated textures (blinking lights)
                if tile in ('E', 'K', 'S', 'L'):
                    pulse = math.sin(curr_t * 0.005 + cur_tx + cur_ty) * 0.3 + 0.7
                    br = int(br * pulse)
                tint.fill((br, br, br))
                scaled.blit(tint, (0, 0), special_flags=pygame.BLEND_RGB_MULT)
                gs.screen.blit(scaled, (i * SCALE, draw_y))
            except:
                br = max(0, 255 - int(rd * 0.25))
                pygame.draw.rect(gs.screen, (br, br, br), (i * SCALE, draw_y, SCALE, wh))

        # Crack overlay on damaged destructible walls
        if tile in ('H', 'T', 'W', 'M', 'I', 'P'):
            dwall_key = (cur_tx, cur_ty)
            if dwall_key in gs.dwall_hp:
                dhp = gs.dwall_hp[dwall_key]
                if dhp < 60 and wh > 10:
                    crack_density = 1.0 - (dhp / 60.0)
                    crack_col = (20, 15, 10)
                    wh_i = int(wh)
                    for _ in range(max(1, int(3 * crack_density))):
                        cy2 = draw_y + random.randint(0, max(1, wh_i - 1))
                        cx2 = i * SCALE + random.randint(0, SCALE - 1)
                        cl = max(1, int(3 * crack_density))
                        pygame.draw.line(gs.screen, crack_col, (cx2 - cl, cy2), (cx2 + cl, cy2 - cl // 2), 1)
                        pygame.draw.line(gs.screen, crack_col, (cx2 - cl // 2, cy2 + 1), (cx2 + cl, cy2 + 1), 1)

    # Sprites (shared between tile and sector modes)
    objs = sorted([e for e in gs.enemies if e['a'] or e.get('dying', 0) > 0] + [i for i in gs.items if i['a']] + gs.bul + gs.proj + gs.corpses + gs.portals + [b for b in gs.barrels if b.get('a', True)],
                  key=lambda x: (gs.px - x['x']) ** 2 + (gs.py - x['y']) ** 2, reverse=True)
    for s_obj in objs:
        edx, edy = s_obj['x'] - gs.px, s_obj['y'] - gs.py
        dst = math.sqrt(edx ** 2 + edy ** 2)
        ang = math.atan2(edy, edx) - gs.pa
        while ang > math.pi: ang -= 2 * math.pi
        while ang < -math.pi: ang += 2 * math.pi
        if abs(ang) < 1.0:
            sx = int((0.5 * (ang / (FOV / 2)) + 0.5) * const.W)
            if gs.sector_mode:
                si = _player_in_any_sector(gs.px, gs.py)
                if 0 <= si < len(gs.sec_sectors):
                    sec_h = gs.sec_sectors[si]['ch'] - gs.sec_sectors[si]['fh']
                else:
                    sec_h = 128
                sh = int((sec_h * 750) / (dst + 0.1))
                spr_flr = _RY + _RH // 2 + sh // 8
            else:
                sh = int((TILE * 750) / (dst + 0.1))
                # spr_flr = floor line minus object height (objects draw downward)
                spr_flr = _RY + _RH // 2 + int(CAM_Z * sh / TILE) - sh // 4
            sh = min(sh, _RH * 2)
            idx = max(0, min(const.W // SCALE - 1, sx // SCALE))
            if 0 <= sx < const.W and z_b[idx] > dst:
                if s_obj.get('type') == 'barrel':
                    r = int(sh * 0.4)
                    brl_col = (160, 40, 20)
                    pygame.draw.rect(gs.screen, brl_col, (sx - r, spr_flr - r, r * 2, r * 2))
                    pygame.draw.rect(gs.screen, (80, 60, 50), (sx - r, spr_flr - r - 2, r * 2, 4))
                    pygame.draw.rect(gs.screen, (80, 60, 50), (sx - r, spr_flr + r - 2, r * 2, 4))
                elif 'hp' in s_obj:
                    draw_enemy_sprite(s_obj, sx, sh, curr_t)
                elif 'time' in s_obj:
                    draw_corpse(s_obj, sx, sh)
                elif 't' in s_obj:
                    t = s_obj['t']
                    if t == 'med':
                        pygame.draw.rect(gs.screen, (255, 255, 255), (sx - sh // 4, spr_flr, sh // 2, sh // 4))
                        pygame.draw.line(gs.screen, (200, 0, 0), (sx, spr_flr), (sx, spr_flr + sh // 4), 3)
                        pygame.draw.line(gs.screen, (200, 0, 0), (sx - sh // 8, spr_flr + sh // 8), (sx + sh // 8, spr_flr + sh // 8), 3)
                    elif t == 'amm':
                        pygame.draw.rect(gs.screen, (220, 220, 0), (sx - sh // 4, spr_flr, sh // 2, sh // 4))
                    elif t == 'med_small':
                        size = sh // 6
                        pygame.draw.rect(gs.screen, (255, 255, 255), (sx - size, spr_flr, size * 2, size))
                        pygame.draw.line(gs.screen, (200, 0, 0), (sx, spr_flr), (sx, spr_flr + size), 2)
                        pygame.draw.line(gs.screen, (200, 0, 0), (sx - size // 2, spr_flr + size // 2), (sx + size // 2, spr_flr + size // 2), 2)
                    elif t == 'amm_small':
                        size = sh // 6
                        pygame.draw.rect(gs.screen, (220, 220, 0), (sx - size, spr_flr, size * 2, size))
                    elif t == 'berserk':
                        r = sh // 4
                        pygame.draw.circle(gs.screen, (200, 0, 0), (sx, spr_flr), r)
                        pygame.draw.circle(gs.screen, (255, 50, 50), (sx, spr_flr), r * 2 // 3)
                        pygame.draw.line(gs.screen, (255, 255, 255), (sx - 3, spr_flr - 3), (sx + 3, spr_flr + 3), 2)
                    elif t == 'haste':
                        r = sh // 4
                        pygame.draw.circle(gs.screen, (0, 200, 200), (sx, spr_flr), r)
                        pygame.draw.polygon(gs.screen, (255, 255, 255), [
                            (sx, spr_flr - r + 3),
                            (sx - 4, spr_flr + 5),
                            (sx + 4, spr_flr + 5)
                        ])
                    elif t == 'invuln':
                        r = sh // 4
                        for ir in range(r, 0, -3):
                            alpha_ring = int(255 * ir / r)
                            color = (255, 255 - alpha_ring // 2, 0)
                            pygame.draw.circle(gs.screen, color, (sx, spr_flr), ir, 2)
                    elif t == 'heart':
                        r = sh // 5
                        cx, cy = sx, spr_flr
                        pygame.draw.circle(gs.screen, (200, 20, 50), (cx - r // 2, cy - r // 3), r // 2)
                        pygame.draw.circle(gs.screen, (200, 20, 50), (cx + r // 2, cy - r // 3), r // 2)
                        pts = [(cx - r, cy - r // 6), (cx, cy + r // 2), (cx + r, cy - r // 6)]
                        pygame.draw.polygon(gs.screen, (200, 20, 50), pts)
                        pygame.draw.circle(gs.screen, (255, 80, 120), (cx - r // 2, cy - r // 3), r // 4)
                        pygame.draw.circle(gs.screen, (255, 80, 120), (cx + r // 2, cy - r // 3), r // 4)
                        pygame.draw.polygon(gs.screen, (255, 80, 120), pts)
                    elif t == 'armor_green':
                        r = sh // 4
                        pygame.draw.rect(gs.screen, (50, 200, 50), (sx - r, spr_flr - r, r * 2, r * 2), 2)
                        pygame.draw.rect(gs.screen, (100, 255, 100), (sx - r + 3, spr_flr - r + 3, r * 2 - 6, r * 2 - 6))
                        pygame.draw.rect(gs.screen, (50, 200, 50), (sx - 3, spr_flr, 6, r))
                    elif t == 'armor_blue':
                        r = sh // 4
                        pygame.draw.rect(gs.screen, (50, 50, 200), (sx - r, spr_flr - r, r * 2, r * 2), 2)
                        pygame.draw.rect(gs.screen, (100, 100, 255), (sx - r + 3, spr_flr - r + 3, r * 2 - 6, r * 2 - 6))
                        pygame.draw.rect(gs.screen, (50, 50, 200), (sx - 3, spr_flr, 6, r))
                    elif t.startswith('key_'):
                        kc = t.split('_')[1]
                        color_map = {'R': (255, 50, 50), 'Y': (255, 255, 50), 'B': (50, 50, 255)}
                        kcol = color_map.get(kc, (255, 255, 255))
                        pygame.draw.polygon(gs.screen, kcol, [
                            (sx, spr_flr - sh // 6),
                            (sx - sh // 8, spr_flr),
                            (sx, spr_flr + sh // 8),
                            (sx + sh // 8, spr_flr)
                        ])
                        pygame.draw.circle(gs.screen, (255, 255, 255), (sx, spr_flr - 2), 3)
                    elif t.startswith('wpn_'):
                        wi = int(t.split('_')[1])
                        wcol = WPN[wi]['c']
                        r2 = sh // 5
                        if wi == 0:
                            pygame.draw.rect(gs.screen, wcol, (sx - r2, spr_flr - r2 // 2, r2 * 2, r2))
                            for d in (-1, 1):
                                pygame.draw.line(gs.screen, (200, 200, 200), (sx + d * r2 // 2, spr_flr - r2 // 2), (sx + d * r2, spr_flr - r2 // 2 - 3), 2)
                        elif wi in (2, 5):
                            bw = r2 if wi == 2 else r2 * 2
                            pygame.draw.rect(gs.screen, wcol, (sx - bw // 2, spr_flr - r2 // 2, bw, r2))
                            pygame.draw.rect(gs.screen, (60, 50, 30), (sx - 2, spr_flr - r2 // 2 - 3, 4, 3))
                        elif wi == 3:
                            pygame.draw.rect(gs.screen, wcol, (sx - 2, spr_flr - r2, 4, r2 * 2))
                            pygame.draw.circle(gs.screen, (200, 200, 200), (sx, spr_flr - r2), 3)
                        elif wi == 4:
                            pygame.draw.circle(gs.screen, wcol, (sx, spr_flr), r2)
                            pygame.draw.circle(gs.screen, (100, 255, 100), (sx, spr_flr), r2 - 3)

                elif 'timer' in s_obj:
                    # Portal
                    pulse = 1 + math.sin(curr_t * 0.003) * 0.15
                    r = int(16 * pulse)
                    glow = int(100 + 155 * (math.sin(curr_t * 0.005) + 1) * 0.5)
                    pygame.draw.circle(gs.screen, (glow // 3, 0, glow // 2), (sx, spr_flr), r + 6)
                    pygame.draw.circle(gs.screen, (glow // 2, glow // 4, glow), (sx, spr_flr), r)
                    pygame.draw.circle(gs.screen, (glow, glow // 2, 255), (sx, spr_flr), r - 4)
                    inner = int(4 + 3 * math.sin(curr_t * 0.007))
                    pygame.draw.circle(gs.screen, (255, 255, 255), (sx, spr_flr), inner)
                elif s_obj.get('sprite'):
                    proj_path = s_obj['sprite']
                    if proj_path not in _wpn_sprites:
                        try:
                            _wpn_sprites[proj_path] = pygame.image.load(proj_path).convert_alpha()
                        except Exception:
                            _wpn_sprites[proj_path] = None
                    spr = _wpn_sprites.get(proj_path)
                    if spr:
                        size = max(12, min(200, int(sh * 0.6)))
                        angle = s_obj.get('dist', 0) * 3 % 360
                        rot = pygame.transform.rotozoom(spr, angle, size / max(spr.get_width(), spr.get_height()))
                        gs.screen.blit(rot, (sx - rot.get_width() // 2, spr_flr - rot.get_height() // 2))
                else:
                    bcol = s_obj.get('col', (255, 255, 0))
                    if s_obj.get('from_player'):
                        br = max(int(sh / 12), 7)
                        pygame.draw.circle(gs.screen, (bcol[0]//2, bcol[1]//2, bcol[2]//2), (sx, spr_flr), int(br * 2))
                        pygame.draw.circle(gs.screen, bcol, (sx, spr_flr), br)
                        pygame.draw.circle(gs.screen, (255, 255, 255), (sx, spr_flr), max(2, br // 3))
                    else:
                        br = max(int(sh / 24), 4)
                        pygame.draw.circle(gs.screen, bcol, (sx, spr_flr), br)

        # Sector mode exit marker
        if gs.sector_mode and gs.exit_pos:
            ex, ey = gs.exit_pos
            edx, edy = ex - gs.px, ey - gs.py
            dst = math.hypot(edx, edy)
            if dst > 4 and dst < gs.view_dist:
                ang = math.atan2(edy, edx) - gs.pa
                while ang > math.pi: ang -= 2 * math.pi
                while ang < -math.pi: ang += 2 * math.pi
                if abs(ang) < 1.2:
                    sx = int((0.5 * (ang / (FOV / 2)) + 0.5) * const.W)
                    if gs.sector_mode:
                        si = _player_in_any_sector(gs.px, gs.py)
                        if 0 <= si < len(gs.sec_sectors):
                            sec_h = gs.sec_sectors[si]['ch'] - gs.sec_sectors[si]['fh']
                        else:
                            sec_h = 128
                        sh = int((sec_h * 750) / (dst + 0.1))
                        cy = _RY + _RH // 2 + sh // 8
                    else:
                        sh = int((TILE * 750) / (dst + 0.1))
                        cy = _RY + _RH // 2 + sh // 4
                    idx = max(0, min(const.W // SCALE - 1, sx // SCALE))
                    if 0 <= sx < const.W and z_b[idx] > dst:
                        pulse = 1 + math.sin(curr_t * 0.004) * 0.15
                        r = int(20 * pulse)
                        glow = int(100 + 155 * (math.sin(curr_t * 0.006) + 1) * 0.5)
                        pygame.draw.circle(gs.screen, (glow // 2, glow // 3, 0), (sx, cy), r + 8)
                        pygame.draw.circle(gs.screen, (glow, glow // 2, 0), (sx, cy), r)
                        pygame.draw.circle(gs.screen, (255, 255, 200), (sx, cy), r - 4)

        # Bots (deathmatch) — sprite animation
        for bot in gs.bots:
            edx2 = bot['x'] - gs.px
            edy2 = bot['y'] - gs.py
            dst2 = math.hypot(edx2, edy2)
            ang2 = math.atan2(edy2, edx2) - gs.pa
            while ang2 > math.pi: ang2 -= 2 * math.pi
            while ang2 < -math.pi: ang2 += 2 * math.pi
            if abs(ang2) < 1.0 and dst2 > 4:
                sx2 = int((0.5 * (ang2 / (FOV / 2)) + 0.5) * const.W)
                if gs.sector_mode:
                    si = _player_in_any_sector(gs.px, gs.py)
                    if 0 <= si < len(gs.sec_sectors):
                        sec_h = gs.sec_sectors[si]['ch'] - gs.sec_sectors[si]['fh']
                    else:
                        sec_h = 128
                    sh2 = int((sec_h * 1000) / (dst2 + 0.1))
                    bot_flr = _RY + _RH // 2 + sh2 // 8
                else:
                    sh2 = int((TILE * 1000) / (dst2 + 0.1))
                    bot_flr = _RY + _RH // 2 + sh2 // 4
                sh2 = min(sh2, _RH * 2)
                idx2 = max(0, min(const.W // SCALE - 1, sx2 // SCALE))
                if 0 <= sx2 < const.W and z_b[idx2] > dst2:
                    pcol = bot.get('col', (200, 40, 40))
                    if bot.get('d_flash', 0) > 0:
                        pcol = (255, 255, 255)
                    frame = bot.get('anim_frame', 0)
                    state = bot.get('anim_state', 'idle')
                    # Facing direction: compare bot facing angle vs angle to player
                    ang_to_player = math.atan2(-edy2, -edx2)
                    facing_diff = ang_to_player - bot.get('pa', 0)
                    while facing_diff > math.pi: facing_diff -= 2 * math.pi
                    while facing_diff < -math.pi: facing_diff += 2 * math.pi
                    ad = abs(facing_diff)
                    if ad < math.pi / 4:
                        bot_view = 'front'
                    elif ad > math.pi * 3 / 4:
                        bot_view = 'back'
                    else:
                        bot_view = 'side'
                    surf = get_bot_surface(pcol, sh2, sh2, frame, state, bot.get('pa', 0), bot.get('wpn', 1), view=bot_view)
                    if bot_view == 'side' and facing_diff < 0:
                        surf = pygame.transform.flip(surf, True, False)
                    sr = surf.get_width() // 2
                    blit_y = max(0, bot_flr - sr)
                    gs.screen.blit(surf, (sx2 - sr, blit_y))
                    # HP bar (max 200)
                    bw = sh2 // 4
                    pygame.draw.rect(gs.screen, (60, 0, 0), (sx2 - bw, bot_flr + sr + 4, bw * 2, 4))
                    hp_w = int(bw * 2 * bot['hp'] / 200)
                    hp_col = (50, 200, 50) if bot['hp'] > 100 else (200, 200, 50) if bot['hp'] > 50 else (200, 50, 50)
                    pygame.draw.rect(gs.screen, hp_col, (sx2 - bw, bot_flr + sr + 4, hp_w, 4))

    # Other players (co-op)
    for pl in gs.players:
        ox, oy, oa = pl['px'], pl['py'], pl['pa']
        edx2 = ox - gs.px
        edy2 = oy - gs.py
        dst2 = math.sqrt(edx2 ** 2 + edy2 ** 2)
        ang2 = math.atan2(edy2, edx2) - gs.pa
        while ang2 > math.pi: ang2 -= 2 * math.pi
        while ang2 < -math.pi: ang2 += 2 * math.pi
        if abs(ang2) < 1.0 and dst2 > 4:
            sx2 = int((0.5 * (ang2 / (FOV / 2)) + 0.5) * const.W)
            if gs.sector_mode:
                si = _player_in_any_sector(gs.px, gs.py)
                if 0 <= si < len(gs.sec_sectors):
                    sec_h = gs.sec_sectors[si]['ch'] - gs.sec_sectors[si]['fh']
                else:
                    sec_h = 128
                sh2 = int((sec_h * 750) / (dst2 + 0.1))
                plr_flr = _RY + _RH // 2 + sh2 // 8
            else:
                sh2 = int((TILE * 750) / (dst2 + 0.1))
                plr_flr = _RY + _RH // 2 + sh2 // 4
            sh2 = min(sh2, _RH * 2)
            idx2 = max(0, min(const.W // SCALE - 1, sx2 // SCALE))
            if 0 <= sx2 < const.W and z_b[idx2] > dst2:
                skin = (230, 190, 150)
                shirt = pl.get('col', (50, 130, 200))
                pants = (70, 70, 85)
                boots = (55, 45, 35)
                br2 = sh2 // 4
                body_h = sh2 // 2
                leg_h = sh2 // 5
                head_y = plr_flr - body_h - leg_h - br2

                # Head
                pygame.draw.circle(gs.screen, skin, (sx2, head_y + br2), br2)
                # Eyes
                eye_off = br2 // 3
                pygame.draw.circle(gs.screen, (40, 40, 40), (sx2 - eye_off, head_y + br2 - eye_off), max(1, br2 // 5))
                pygame.draw.circle(gs.screen, (40, 40, 40), (sx2 + eye_off, head_y + br2 - eye_off), max(1, br2 // 5))

                # Torso
                tw = int(br2 * 1.3)
                torso_top = head_y + br2 * 2
                torso_bot = torso_top + body_h
                pygame.draw.rect(gs.screen, shirt, (sx2 - tw, torso_top, tw * 2, body_h))

                # Arms
                arm_len = int(body_h * 0.7)
                ax1 = sx2 - tw
                ax2 = sx2 + tw
                ay = torso_top + body_h // 3
                pygame.draw.line(gs.screen, skin, (ax1, ay),
                                 (ax1 - int(math.cos(oa + 0.6) * arm_len),
                                  ay + int(math.sin(oa + 0.6) * arm_len)), max(2, br2 // 3))
                pygame.draw.line(gs.screen, skin, (ax2, ay),
                                 (ax2 - int(math.cos(oa - 0.6) * arm_len),
                                  ay + int(math.sin(oa - 0.6) * arm_len)), max(2, br2 // 3))

                # Legs
                lw = max(2, br2 // 2)
                pygame.draw.line(gs.screen, pants, (sx2 - lw, torso_bot), (sx2 - lw, torso_bot + leg_h), lw)
                pygame.draw.line(gs.screen, pants, (sx2 + lw, torso_bot), (sx2 + lw, torso_bot + leg_h), lw)
                pygame.draw.line(gs.screen, boots, (sx2 - lw, torso_bot + leg_h), (sx2 - lw - 1, torso_bot + leg_h + 3), max(2, lw))
                pygame.draw.line(gs.screen, boots, (sx2 + lw, torso_bot + leg_h), (sx2 + lw + 1, torso_bot + leg_h + 3), max(2, lw))

                # Weapon
                wpn_idx = pl.get('wpn', 1)
                if 0 <= wpn_idx < len(WPN):
                    wcol = WPN[wpn_idx]['c']
                else:
                    wcol = (180, 180, 100)
                whb = max(2, br2 // 3)
                wlen = int(br2 * 1.8)
                wdx = int(math.cos(oa) * wlen)
                wdy = int(math.sin(oa) * wlen)
                wx2 = sx2 - tw + wdx
                wy2 = ay + wdy
                pygame.draw.line(gs.screen, wcol, (sx2 - tw, ay), (wx2, wy2), whb + 1)

                # Name tag
                oname = pl.get('name', '')
                if oname:
                    nm_surf = gs.sm_font.render(oname, True, (255, 255, 255))
                    nm_x = sx2 - nm_surf.get_width() // 2
                    nm_y = head_y - nm_surf.get_height() - 2
                    if nm_y > _RY - 10:
                        gs.screen.blit(nm_surf, (nm_x, nm_y))

    # Floor glow when standing on F/E/L tiles
    pt_x, pt_y = int(gs.px // TILE), int(gs.py // TILE)
    if 0 <= pt_y < gs.M_SZ and 0 <= pt_x < gs.M_SZ:
        ft = gs.MAP[pt_y][pt_x]
        if ft == 'F':
            ov = pygame.Surface((const.W, 40))
            ov.set_alpha(80)
            ov.fill((200, 150, 20))
            gs.screen.blit(ov, (0, _RY + _RH - 40))
        elif ft == 'E':
            ov = pygame.Surface((const.W, 40))
            ov.set_alpha(60)
            ov.fill((60, 180, 220))
            gs.screen.blit(ov, (0, _RY + _RH - 40))
        elif ft == 'L':
            ov = pygame.Surface((const.W, 40))
            ov.set_alpha(60)
            ov.fill((200, 50, 5))
            gs.screen.blit(ov, (0, _RY + _RH - 40))
            ov2 = pygame.Surface((const.W, 80))
            ov2.set_alpha(30)
            ov2.fill((200, 80, 10))
            gs.screen.blit(ov2, (0, _RY + _RH - 80))

    # Particles
    draw_particles(z_b, NC, _RH, _RY)
    # Decals (bullet holes, scorch, blood)
    draw_decals(_RH, _RY)
    # Weather
    draw_weather()

    # Distance fog
    if gs.fog_enabled and z_b:
        fog_surf = pygame.Surface((const.W, _RH), pygame.SRCALPHA)
        nc = len(z_b)
        for i in range(nc):
            d = z_b[i]
            if d >= gs.view_dist:
                continue
            alpha = int((d / gs.view_dist) * gs.fog_density)
            if alpha <= 0:
                continue
            fog_surf.fill((*gs.fog_color, alpha), (i * SCALE, 0, SCALE, _RH))
        gs.screen.blit(fog_surf, (0, _RY))

    # HUD
    dx, dy = 0.0, 0.0
    k = pygame.key.get_pressed()
    if k[pygame.K_w] or k[pygame.K_s] or k[pygame.K_a] or k[pygame.K_d]:
        dx, dy = 1.0, 1.0
    bob = int(math.sin(curr_t * 0.015) * 15 if abs(dx) + abs(dy) > 0 else 0)
    fire = getattr(gs, '_replay_fire', False) if getattr(gs, 'replay_mode', 'off') == 'playback' else bool(getattr(gs, 'fire_flag', False) or pygame.key.get_pressed()[pygame.K_SPACE] or pygame.mouse.get_pressed()[0])
    draw_wpn(gs.p['wpn'], gs.w_tmr, fire, bob)

    if gs.d_flash > 0:
        ov = pygame.Surface((const.W, _RH), pygame.SRCALPHA)
        ov.set_alpha(120)
        ov.fill((255, 0, 0))
        gs.screen.blit(ov, (0, _RY))
        # Blood splatter droplets
        for _ in range(8):
            bx = random.randint(0, const.W)
            by = random.randint(_RY, _RY + _RH)
            br = random.randint(4, 20)
            ba = random.randint(30, 100)
            bcol = (140, 10, 10, ba)
            s = pygame.Surface((br * 2, br * 2), pygame.SRCALPHA)
            pygame.draw.circle(s, bcol, (br, br), br)
            try:
                gs.screen.blit(s, (bx - br, by - br), special_flags=pygame.BLEND_ALPHA_SDL2)
            except:
                gs.screen.blit(s, (bx - br, by - br))
        gs.d_flash -= 1

    if gs.p['powerup']:
        pu_glow = int(155 + 100 * math.sin(curr_t * 0.008))
        ov = pygame.Surface((const.W, _RH))
        ov.set_alpha(pu_glow // 4)
        if gs.p['powerup'] == 'BERSERK':
            ov.fill((200, 0, 0))
        elif gs.p['powerup'] == 'HASTE':
            ov.fill((0, 100, 200))
        elif gs.p['powerup'] == 'INVULN':
            ov.fill((255, 255, 0))
        gs.screen.blit(ov, (0, _RY))

    if gs.p['hp'] <= 50 and gs.p['hp'] > 0:
        intensity = int(80 * (1 - gs.p['hp'] / 50))
        ov = pygame.Surface((const.W, _RH))
        ov.set_alpha(intensity)
        ov.fill((180, 0, 0))
        gs.screen.blit(ov, (0, _RY))
        if gs.p['hp'] <= 20 and int(curr_t * 0.005) % 2 == 0:
            ov2 = pygame.Surface((const.W, _RH))
            ov2.set_alpha(60)
            ov2.fill((200, 0, 0))
            gs.screen.blit(ov2, (0, _RY))

    # Elevator prompt
    if gs.elevator_prompt:
        msg = gs.sm_font.render("ELEVATOR: PRESS ENTER", True, (0, 255, 255))
        gs.screen.blit(msg, (const.W // 2 - msg.get_width() // 2, _RY + _RH // 2 - 80))

    # Update explored tiles (fog of war) — always track
    radius = 8
    pt_x = int(gs.px // TILE)
    pt_y = int(gs.py // TILE)
    explore_sz = len(gs.explored)
    if explore_sz > 0:
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                nx, ny = pt_x + dx, pt_y + dy
                if 0 <= nx < explore_sz and 0 <= ny < explore_sz:
                    gs.explored[ny][nx] = True

    if gs.show_minimap and gs.M_SZ > 0 and explore_sz > 0:
        ms = 5
        draw_sz = min(gs.M_SZ, explore_sz)
        pygame.draw.rect(gs.screen, (0, 0, 0), (10, 10, draw_sz * ms, draw_sz * ms))
        for y in range(draw_sz):
            for x in range(draw_sz):
                if not gs.explored[y][x]:
                    continue
                tile = gs.MAP[y][x]
                if tile == 'S':
                    continue
                if tile == '.':
                    pygame.draw.rect(gs.screen, (25, 25, 30), (10 + x * ms, 10 + y * ms, ms, ms))
                elif tile == '#':
                    pygame.draw.rect(gs.screen, (100, 100, 100), (10 + x * ms, 10 + y * ms, ms, ms))
                elif tile in ('F', 'E'):
                    c = (60, 180, 220) if tile == 'E' else (200, 150, 0)
                    pygame.draw.rect(gs.screen, c, (10 + x * ms, 10 + y * ms, ms, ms))
                elif tile in ('R', 'Y', 'B', 'T', 'W', 'D', 'M', 'I', 'P', 'H', 'V', 'C', 'Q', 'L', 'K'):
                    c = {'R': (180, 40, 40), 'Y': (180, 180, 40), 'B': (40, 40, 180),
                         'T': (130, 130, 140), 'W': (120, 80, 40),
                         'D': (160, 120, 60),
                         'M': (140, 30, 30), 'I': (90, 110, 85), 'P': (170, 80, 50),
                         'H': (100, 80, 60), 'V': (100, 200, 100), 'C': (140, 100, 60),
                         'Q': (60, 180, 200), 'L': (200, 80, 0), 'K': (200, 50, 200)}.get(tile, (100, 100, 100))
                    pygame.draw.rect(gs.screen, c, (10 + x * ms, 10 + y * ms, ms, ms))
        pygame.draw.circle(gs.screen, (0, 255, 0), (10 + gs.px / TILE * ms, 10 + gs.py / TILE * ms), 3)
        for bot in gs.bots:
            bc = bot.get('col', (255, 40, 40))
            pygame.draw.circle(gs.screen, bc, (10 + bot['x'] / TILE * ms, 10 + bot['y'] / TILE * ms), 3)
        kx = 10 + gs.M_SZ * ms + 5
        ky = 10
        if gs.p['keys']['R'] > 0:
            gs.screen.blit(gs.sm_font.render(f"R:{gs.p['keys']['R']}", True, (255, 50, 50)), (kx, ky)); ky += 15
        if gs.p['keys']['Y'] > 0:
            gs.screen.blit(gs.sm_font.render(f"Y:{gs.p['keys']['Y']}", True, (255, 255, 50)), (kx, ky)); ky += 15
        if gs.p['keys']['B'] > 0:
            gs.screen.blit(gs.sm_font.render(f"B:{gs.p['keys']['B']}", True, (50, 50, 255)), (kx, ky)); ky += 15
        if gs.secrets_total > 0:
            gs.screen.blit(gs.sm_font.render(f"SEC: {gs.secrets_found}/{gs.secrets_total}", True, (100, 200, 100)), (kx, ky))

    # Speedrun timer
    mins = int(gs.speedrun_timer // 60)
    secs = int(gs.speedrun_timer % 60)
    tenths = int((gs.speedrun_timer * 10) % 10)
    gs.screen.blit(gs.sm_font.render(f"{mins:02}:{secs:02}.{tenths}", True, (200, 200, 200)), (const.W - 130, 85))

    if gs.wave_msg_timer > 0:
        gs.wave_msg_timer -= 1
        alpha = min(255, gs.wave_msg_timer * 8)
        msg = gs.font.render(f"WAVE {gs.rounds}", True, (255, 255, 0))
        msg.set_alpha(alpha)
        gs.screen.blit(msg, (const.W // 2 - msg.get_width() // 2, _RY + _RH // 2 - 100))

    if gs.show_key_msg > 0:
        gs.show_key_msg -= 1
        alpha = min(255, gs.show_key_msg * 8)
        msg = gs.font.render(gs.key_msg, True, (255, 100, 100))
        msg.set_alpha(alpha)
        gs.screen.blit(msg, (const.W // 2 - msg.get_width() // 2, _RY + _RH // 2 + 50))

    # Replay indicator
    if gs.replay_mode == 'record':
        gs.screen.blit(gs.sm_font.render("REC", True, (255, 50, 50)), (10, _RY + _RH - 20))
    elif gs.replay_mode == 'playback':
        gs.screen.blit(gs.sm_font.render(f"PLAY ({gs.replay_index}/{len(gs.replay_data)})", True, (50, 255, 50)), (10, _RY + _RH - 20))

    # Armor display
    arm = gs.p.get('armor')
    arm_str = ""
    if arm:
        arm_color = (50, 200, 50) if arm['type'] == 'green' else (50, 50, 200)
        arm_str = f" ARM:{arm['value']}%"
        gs.screen.blit(gs.sm_font.render(f"ARMOR {arm['value']}", True, arm_color), (410, _RY + _RH - 20))

    # Deathmatch frag display
    if gs.lvl_type == 'deathmatch':
        f1 = gs.dm_frags.get('p1', 0)
        f2 = gs.dm_frags.get('p2', 0)
        bot_total = sum(b.get('frags', 0) for b in gs.bots)
        dm_x = 20 + (gs.M_SZ * 5 + 10 if gs.show_minimap else 0)
        if gs.dm_team_mode:
            rs, bs = gs.dm_team_scores['red'], gs.dm_team_scores['blue']
            gs.screen.blit(gs.sm_font.render(f"TEAMS  RED:{rs}  BLUE:{bs}", True, (255, 200, 0)), (dm_x, 10))
            yy = 24
            for b in gs.bots:
                bc = b.get('col', (200, 40, 40))
                team_c = 'R' if b.get('team') == 'red' else 'B'
                gs.screen.blit(gs.sm_font.render(f"{team_c}{b.get('id',0)} {b.get('type','?')[:3].upper()}  F:{b.get('frags',0)}  HP:{b['hp']}", True, bc), (dm_x, yy))
                yy += 14
        else:
            gs.screen.blit(gs.sm_font.render(f"P1:{f1}  P2:{f2}  BOTS:{bot_total}", True, (255, 200, 0)), (dm_x, 10))
            yy = 24
            for b in gs.bots:
                bc = b.get('col', (200, 40, 40))
                gs.screen.blit(gs.sm_font.render(f"B{b.get('id',0)} {b.get('type','?')[:3].upper()}  F:{b.get('frags',0)}  HP:{b['hp']}  const.W:{b.get('wpn',1)}", True, bc), (dm_x, yy))
                yy += 14
        if gs.dm_timer > 0:
            mins, secs = divmod(gs.dm_timer // 60, 60)
            timer_col = (255, 100, 100) if gs.dm_timer < 180 * 60 else (200, 200, 200)
            gs.screen.blit(gs.font.render(f"{mins}:{secs:02d}", True, timer_col), (const.W // 2 - 30, 10))
        # Game over screen
        if gs.dm_game_over:
            overlay = pygame.Surface((const.W, const.H), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 180))
            gs.screen.blit(overlay, (0, 0))
            win_text = f"{gs.dm_winner} WINS!" if gs.dm_winner and gs.dm_winner != 'DRAW' else "DRAW!"
            gs.screen.blit(gs.font.render(win_text, True, (255, 255, 0)), (const.W // 2 - 80, const.H // 2 - 20))
            gs.screen.blit(gs.sm_font.render("Press ENTER to restart", True, (150, 150, 150)), (const.W // 2 - 80, const.H // 2 + 20))

    # ── HUD ──
    hud_bh = 100
    hud_by = _RY + _RH - hud_bh
    pygame.draw.rect(gs.screen, (15, 15, 20), (0, hud_by, const.W, hud_bh))
    pygame.draw.rect(gs.screen, (100, 100, 100), (0, hud_by, const.W, hud_bh), 1)

    hp = gs.p['hp']
    max_hp = gs.start_hp
    arm = gs.p.get('armor')
    arm_val = arm['value'] if arm else 0
    arm_type = arm['type'] if arm else None

    # ── Square face, big and expressive ──
    face_sz = 52
    face_x, face_y = const.W // 2, hud_by + hud_bh // 2
    face_rect = pygame.Rect(face_x - face_sz // 2, face_y - face_sz // 2, face_sz, face_sz)
    blink = (int(curr_t * 0.003) % 200) < 3

    if hp <= 0:
        fc = (60, 60, 60); skin = (80, 80, 80); phase = 'dead'
    elif hp < 15:
        fc = (180, 0, 0); skin = (190, 110, 60); phase = 'critical'
    elif hp < 30:
        fc = (180, 10, 10); skin = (200, 120, 70); phase = 'hurt'
    elif hp < 50:
        fc = (180, 130, 10); skin = (210, 150, 90); phase = 'worried'
    elif hp <= 80:
        fc = (0, 180, 0); skin = (195, 145, 105); phase = 'calm'
    elif hp <= 125:
        fc = (0, 190, 0); skin = (195, 145, 105); phase = 'relaxed'
    elif hp <= 175:
        fc = (0, 220, 0); skin = (190, 140, 100); phase = 'happy'
    else:
        fc = (0, 255, 0); skin = (180, 130, 90); phase = 'ecstatic'

    fd = gs.face_dir
    cx = face_x    # head stays centered
    cy = face_y - 1
    feat_off = fd * 3   # features shift to show turning

    # Head — rounded rect, no frame (centered)
    head_r = 6
    pygame.draw.rect(gs.screen, skin, (cx - 19, cy - 20, 38, 38), border_radius=head_r)

    # ── Eyebrows ──
    brow_col = (50, 35, 25) if phase != 'dead' else (100, 100, 100)
    fex = cx + feat_off  # shifted features X
    if phase in ('critical', 'hurt'):
        pygame.draw.line(gs.screen, brow_col, (fex - 14, cy - 16), (fex - 5, cy - 11), 4)
        pygame.draw.line(gs.screen, brow_col, (fex + 14, cy - 16), (fex + 5, cy - 11), 4)
    elif phase == 'worried':
        pygame.draw.line(gs.screen, brow_col, (fex - 13, cy - 14), (fex - 4, cy - 14), 4)
        pygame.draw.line(gs.screen, brow_col, (fex + 13, cy - 14), (fex + 4, cy - 14), 4)
    elif phase == 'relaxed':
        pygame.draw.line(gs.screen, brow_col, (fex - 13, cy - 15), (fex - 4, cy - 15), 4)
        pygame.draw.line(gs.screen, brow_col, (fex + 13, cy - 15), (fex + 4, cy - 15), 4)
    elif phase == 'calm':
        pygame.draw.line(gs.screen, brow_col, (fex - 13, cy - 15), (fex - 4, cy - 14), 4)
        pygame.draw.line(gs.screen, brow_col, (fex + 13, cy - 15), (fex + 4, cy - 14), 4)
    elif phase == 'happy':
        pygame.draw.line(gs.screen, brow_col, (fex - 13, cy - 16), (fex - 4, cy - 15), 4)
        pygame.draw.line(gs.screen, brow_col, (fex + 13, cy - 16), (fex + 4, cy - 15), 4)
    elif phase == 'ecstatic':
        pygame.draw.line(gs.screen, brow_col, (fex - 13, cy - 18), (fex - 4, cy - 16), 4)
        pygame.draw.line(gs.screen, brow_col, (fex + 13, cy - 18), (fex + 4, cy - 16), 4)

    # ── Eyes ──
    e_y = cy - 4
    # Pupil offset: when fd != 0, pupils shift to edge for side-glance
    pupil_off = fd * 2
    if not blink and phase != 'dead':
        if phase == 'ecstatic':
            # Wide open eyes
            pygame.draw.rect(gs.screen, (255, 255, 255), (fex - 10, e_y - 3, 9, 7))
            pygame.draw.rect(gs.screen, (255, 255, 255), (fex + 3, e_y - 3, 9, 7))
            pygame.draw.rect(gs.screen, (30, 15, 5), (fex - 7 + pupil_off, e_y - 1, 4, 3))
            pygame.draw.rect(gs.screen, (30, 15, 5), (fex + 6 + pupil_off, e_y - 1, 4, 3))
            pygame.draw.circle(gs.screen, (255, 255, 200), (fex - 8 + pupil_off, e_y - 3), 2)
            pygame.draw.circle(gs.screen, (255, 255, 200), (fex + 5 + pupil_off, e_y - 3), 2)
        elif phase == 'happy':
            pygame.draw.rect(gs.screen, (255, 255, 255), (fex - 10, e_y + 1, 9, 3))
            pygame.draw.rect(gs.screen, (255, 255, 255), (fex + 3, e_y + 1, 9, 3))
            pygame.draw.rect(gs.screen, (30, 15, 5), (fex - 7 + pupil_off, e_y + 1, 4, 2))
            pygame.draw.rect(gs.screen, (30, 15, 5), (fex + 6 + pupil_off, e_y + 1, 4, 2))
        else:
            pygame.draw.rect(gs.screen, (255, 255, 255), (fex - 10, e_y - 2, 9, 5))
            pygame.draw.rect(gs.screen, (255, 255, 255), (fex + 3, e_y - 2, 9, 5))
            pygame.draw.rect(gs.screen, (30, 15, 5), (fex - 7 + pupil_off, e_y - 1, 4, 3))
            pygame.draw.rect(gs.screen, (30, 15, 5), (fex + 6 + pupil_off, e_y - 1, 4, 3))
        if phase in ('critical', 'hurt'):
            pygame.draw.line(gs.screen, (200, 0, 0), (fex - 10, e_y - 4), (fex + 2, e_y - 4), 1)
            pygame.draw.line(gs.screen, (200, 0, 0), (fex + 3, e_y - 4), (fex + 14, e_y - 4), 1)
            if phase == 'critical':
                pygame.draw.line(gs.screen, (200, 0, 0), (fex - 12, e_y + 4), (fex + 12, e_y + 4), 1)
    elif phase == 'dead':
        pygame.draw.line(gs.screen, (150, 0, 0), (fex - 9, e_y - 3), (fex - 1, e_y + 3), 2)
        pygame.draw.line(gs.screen, (150, 0, 0), (fex - 1, e_y - 3), (fex - 9, e_y + 3), 2)
        pygame.draw.line(gs.screen, (150, 0, 0), (fex + 3, e_y - 3), (fex + 11, e_y + 3), 2)
        pygame.draw.line(gs.screen, (150, 0, 0), (fex + 11, e_y - 3), (fex + 3, e_y + 3), 2)
    else:
        pygame.draw.line(gs.screen, brow_col, (fex - 8, e_y), (fex + 1, e_y), 2)
        pygame.draw.line(gs.screen, brow_col, (fex + 3, e_y), (fex + 12, e_y), 2)

    # ── Nose ──
    if phase != 'dead':
        pygame.draw.line(gs.screen, brow_col, (fex, cy - 2), (fex, cy + 4), 2)
        pygame.draw.line(gs.screen, brow_col, (fex, cy + 4), (fex + 3, cy + 2), 2)

    # ── Mouth ──
    m_y = cy + 10
    if phase == 'dead':
        pygame.draw.line(gs.screen, (100, 100, 100), (fex - 8, m_y + 2), (fex + 8, m_y + 2), 2)
    elif phase == 'ecstatic':
        pygame.draw.arc(gs.screen, (220, 220, 220), (fex - 11, m_y - 7, 22, 14), math.pi + 0.05, 2 * math.pi - 0.05, 3)
        pygame.draw.arc(gs.screen, (255, 255, 255), (fex - 9, m_y - 5, 18, 10), math.pi + 0.05, 2 * math.pi - 0.05, 2)
    elif phase == 'happy':
        pygame.draw.arc(gs.screen, (200, 200, 200), (fex - 9, m_y - 5, 18, 12), math.pi + 0.1, 2 * math.pi - 0.1, 3)
        pygame.draw.arc(gs.screen, (255, 255, 255), (fex - 7, m_y - 3, 14, 8), math.pi + 0.1, 2 * math.pi - 0.1, 2)
    elif phase == 'relaxed':
        pygame.draw.line(gs.screen, (180, 180, 180), (fex - 6, m_y + 1), (fex + 6, m_y + 1), 2)
        pygame.draw.line(gs.screen, (200, 200, 200), (fex + 6, m_y + 1), (fex + 8, m_y + 2), 2)
    elif phase == 'calm':
        pygame.draw.line(gs.screen, (180, 180, 180), (fex - 7, m_y + 1), (fex + 1, m_y), 2)
        pygame.draw.line(gs.screen, (200, 200, 200), (fex + 1, m_y), (fex + 7, m_y + 1), 2)
    elif phase == 'worried':
        pygame.draw.line(gs.screen, (160, 160, 160), (fex - 7, m_y + 2), (fex + 7, m_y + 2), 3)
    elif phase == 'hurt':
        pygame.draw.arc(gs.screen, (180, 40, 40), (fex - 8, m_y - 2, 16, 10), math.pi + 0.15, 2 * math.pi - 0.15, 3)
        pygame.draw.line(gs.screen, (180, 0, 0), (fex - 4, m_y + 7), (fex - 4, m_y + 11), 2)
        pygame.draw.line(gs.screen, (180, 0, 0), (fex + 4, m_y + 7), (fex + 4, m_y + 10), 2)
    elif phase == 'critical':
        pygame.draw.arc(gs.screen, (180, 20, 20), (fex - 9, m_y - 2, 18, 12), math.pi + 0.2, 2 * math.pi - 0.2, 3)
        pygame.draw.line(gs.screen, (180, 0, 0), (fex - 5, m_y + 8), (fex - 5, m_y + 13), 2)
        pygame.draw.line(gs.screen, (180, 0, 0), (fex + 5, m_y + 8), (fex + 5, m_y + 12), 2)
        pygame.draw.line(gs.screen, (180, 0, 0), (fex - 1, m_y + 9), (fex - 1, m_y + 12), 2)

    # ── Left: HP + thin bar + stats ──
    lx = 12
    hp_col = (0, 220, 0) if hp > 50 else ((220, 220, 0) if hp > 25 else (220, 0, 0))
    hp_str = f"{hp}/{max_hp}"
    gs.screen.blit(gs.font.render(hp_str, True, hp_col), (lx, hud_by + 4))
    # Stats (compact)
    stats_y = hud_by + 14
    gs.screen.blit(gs.sm_font.render(f"K:{gs.kills}", True, (180, 120, 120)), (lx, stats_y))
    gs.screen.blit(gs.sm_font.render(f"I:{gs.items_picked}", True, (180, 180, 120)), (lx, stats_y + 12))
    gs.screen.blit(gs.sm_font.render(f"S:{gs.secrets_found}/{gs.secrets_total}", True, (160, 160, 160)), (lx, stats_y + 24))
    # Accuracy
    if gs.shots_fired > 0:
        acc = gs.shots_hit * 100 // gs.shots_fired
        acc_col = (100, 200, 100) if acc > 50 else ((200, 200, 100) if acc > 25 else (200, 100, 100))
        gs.screen.blit(gs.sm_font.render(f"A:{acc}%", True, acc_col), (lx, stats_y + 36))

    # ── Right: Armor + weapon + ammo + kills count ──
    rx = const.W - 120

    # Armor value with type icon
    if arm_val > 0:
        arm_label = f"ARM {arm_val}"
        arm_col2 = (50, 220, 50) if arm_type == 'green' else ((50, 80, 220) if arm_type == 'blue' else (100, 100, 100))
        gs.screen.blit(gs.font.render(arm_label, True, arm_col2), (rx, hud_by + 4))

    if gs.p['wpn'] >= 100:
        cw_idx = gs.p['wpn'] - 100
        cg = gs.p.get('custom_guns', [])
        if 0 <= cw_idx < len(cg):
            from custom_content import get_custom_weapons
            cw = get_custom_weapons()
            wdef = cw.get(cg[cw_idx], {})
            wpn_name = wdef.get('name', 'Custom')
            ammo_type = wdef.get('ammo', 'pstl')
            amm = gs.p.get(ammo_type, 0) if ammo_type else "INF"
        else:
            wpn_name, amm = "Custom", "?"
    else:
        wpn = WPN[gs.p['wpn']]
        wpn_name = wpn['n']
        amm_key = wpn['a']
        amm = gs.p[amm_key] if amm_key else "INF"
    if gs.lvl_type == 'slaughter':
        amm = "INF"

    wpn_y = hud_by + 28
    gs.screen.blit(gs.sm_font.render(wpn_name, True, (210, 210, 210)), (rx + 2, wpn_y))
    if isinstance(amm, int):
        amc = {'pstl': (180, 180, 100), 'shl': (200, 120, 50), 'slg': (100, 200, 100), 'rck': (0, 200, 0), 'BFG': (0, 200, 50)}.get(amm_key if gs.p['wpn'] < 100 else 'pstl', (200, 200, 200))
        amm_str = f"{amm}"
        gs.screen.blit(gs.font.render(amm_str, True, amc), (rx, wpn_y + 16))
        # Thin ammo bar
        amm_max = {'pstl': 200, 'shl': 50, 'slg': 50, 'rck': 10, 'BFG': 10}.get(amm_key if gs.p['wpn'] < 100 else 'pstl', 100)
        if amm_max > 0:
            amm_frac = min(1.0, amm / amm_max)
            abw, abh = 90, 4
            pygame.draw.rect(gs.screen, (25, 25, 25), (rx, wpn_y + 38, abw, abh))
            pygame.draw.rect(gs.screen, amc, (rx, wpn_y + 38, int(abw * amm_frac), abh))
    # Remaining enemies
    ene_left = len(gs.enemies)
    if ene_left > 0:
        ene_col = (200, 100, 100) if ene_left > 5 else ((200, 200, 100) if ene_left > 1 else (200, 50, 50))
        gs.screen.blit(gs.sm_font.render(f"ENE:{ene_left}", True, ene_col), (rx, wpn_y + 48))

    # ── Weapon slots (top area between panels) ──
    owned = sorted(gs.p['guns'])
    slot_w = 30
    slots_start = max(lx + 80, face_rect.left - len(owned) * slot_w - 8)
    slot_y = hud_by + 8
    for i, w in enumerate(owned):
        wx = slots_start + i * slot_w
        wcol = (255, 255, 255) if w == gs.p['wpn'] else (120, 120, 120)
        lbl = str(w + 1) if w < 6 else f"C{w-99}"
        if w < 6:
            pygame.draw.rect(gs.screen, WPN[w]['c'], (wx + 2, slot_y + 1, 6, 5))
        lw = gs.sm_font.size(lbl)[0]
        gs.screen.blit(gs.sm_font.render(lbl, True, wcol), (wx + (slot_w - lw) // 2, slot_y + 8))
        if w == gs.p['wpn']:
            pygame.draw.rect(gs.screen, (255, 255, 255), (wx, slot_y, slot_w - 1, 22), 1)

    # ── Under face: mode / dmg / powerup / keys ──
    bot_y = hud_by + hud_bh - 16
    mode_str = ""
    if gs.lvl_type == 'slaughter':
        mode_str = f"W:{gs.rounds}"
    elif gs.lvl_type == 'deathmatch':
        mode_str = "DM"
    elif gs.lvl_type == 'pvp':
        mode_str = "PvP"
    elif gs.lvl_type == 'pve':
        mode_str = f"W:{gs.orda_wave}" if gs.orda_wave else "PvE"
    elif gs.lvl_type == 'orda':
        mode_str = f"W{gs.orda_wave}"
    else:
        mode_str = 'ENDLESS' if gs.lvl_type == 'endless' else 'EXIT'
    if mode_str:
        gs.screen.blit(gs.sm_font.render(mode_str, True, (150, 150, 150)), (face_rect.centerx - 15, bot_y))
    # Damage dealt/taken on the bottom line
    gs.screen.blit(gs.sm_font.render(f"DMG:{gs.damage_dealt}d/{gs.damage_taken}t", True, (140, 140, 140)), (face_rect.centerx - 45, bot_y + 10))
    pu_str = f"{gs.p['powerup']}" if gs.p['powerup'] else ""
    if pu_str:
        gs.screen.blit(gs.sm_font.render(pu_str, True, (255, 255, 0)), (face_rect.centerx - 15, hud_by + 2))
    kx = face_rect.right + 8
    for kc, kv in sorted(gs.p.get('keys', {}).items()):
        if kv > 0:
            kcol = {'R': (255, 50, 50), 'Y': (255, 255, 50), 'B': (50, 50, 255)}.get(kc, (200, 200, 200))
            gs.screen.blit(gs.sm_font.render(f"{kc}:{kv}", True, kcol), (kx, bot_y))
            kx += 28

    # Cheat input display
    if gs.cheat_open:
        pygame.draw.rect(gs.screen, (0, 0, 0), (0, _RY + _RH - 42, const.W, 42))
        pygame.draw.rect(gs.screen, (100, 100, 100), (0, _RY + _RH - 42, const.W, 42), 1)
        txt = f"CHEAT: {gs.cheat_buf}█"
        gs.screen.blit(gs.font.render(txt, True, (255, 255, 0)), (30, _RY + _RH - 35))

    # Crosshair
    if gs.show_crosshair:
        ch_x, ch_y = const.W // 2, const.H // 2
        ch_col = (255, 255, 255)
        pygame.draw.line(gs.screen, ch_col, (ch_x - 10, ch_y), (ch_x - 3, ch_y), 2)
        pygame.draw.line(gs.screen, ch_col, (ch_x + 3, ch_y), (ch_x + 10, ch_y), 2)
        pygame.draw.line(gs.screen, ch_col, (ch_x, ch_y - 10), (ch_x, ch_y - 3), 2)
        pygame.draw.line(gs.screen, ch_col, (ch_x, ch_y + 3), (ch_x, ch_y + 10), 2)
