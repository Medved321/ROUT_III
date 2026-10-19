import math, os, random, pygame
try:
    import numpy as np
    _HAVE_NP = True
except Exception:
    np = None
    _HAVE_NP = False
from engine.constants import (SCALE, TILE, FOV, TEX_W, WPN, ENEMY_DB, EYE_H,
                               CAM_Z, ENEMY_GAME_HEIGHTS, FALLBACK_SPRITE_MAX,
                               ITEM_GAME_HEIGHTS, ITEM_FLOAT_HEIGHT)
from engine import constants as const
from engine import game_state as gs
from engine.framebuffer import depth_col
from engine.viewport import ensure_hud_fonts, ui_px, ui_scale, project_point
from engine.particles import draw_particles, draw_decals, draw_weather
from engine.particles import update_particles, update_decals, update_weather
from engine.map_utils import get_t, _player_in_any_sector
from engine.quality import enemy_detail
from engine.sprite_cache import view_index

# ---------------------------------------------------------------------------
# Framebuffer backend (total-performance, D1/D4): the tile 3D scene is
# composed into a fixed-resolution ``gs.frame`` and upscaled to the screen
# once per frame.  Walls are written as sampled column strips (bulk), flats
# as rect fills; DDA/projection logic is untouched.
# ---------------------------------------------------------------------------
_FRAME_FLAT_TOP = (20, 20, 25)
_FRAME_FLAT_BOT = (30, 30, 35)

# Blank stand-in sprite used when no ``gs.sprite_provider`` is registered
# (updating_engine D2): the engine must degrade, not raise.  Cached per size so
# the fallback costs one dict lookup instead of a surface allocation per bot.
_BLANK_SPRITES = {}


def _brighter(rgb, f=0.42):
    """Lift a base colour towards white, clamped per channel."""
    return tuple(min(255, int(c + (255 - c) * f)) for c in rgb[:3])


def _blank_sprite(size):
    size = max(1, min(int(size), FALLBACK_SPRITE_MAX))
    surf = _BLANK_SPRITES.get(size)
    if surf is None:
        surf = pygame.Surface((size, size), pygame.SRCALPHA)
        _BLANK_SPRITES[size] = surf
    return surf


def _ensure_frame():
    """Lazily (re)create the internal framebuffer for the current tier."""
    from engine.framebuffer import Frame
    from engine.quality import resolution

    iw, ih = resolution(gs.quality_tier)
    gs.iwidth, gs.iheight = iw, ih
    if gs.frame is None or gs.frame.size != (iw, ih):
        gs.frame = Frame(iw, ih)
    return gs.frame


def _wall_strip(tex, tex_x, h, br):
    """``h`` screen-space rows (``h*3`` bytes) of texture column ``tex_x``
    shaded by ``br`` (0..255).  The source column comes from the pre-lit
    column cache (task 2.1: 16 light levels, selection by brightness) and is
    vertical-sampled to ``h`` in one bulk copy."""
    from engine.wallcache import light_level, DEFAULT_CACHE
    level = light_level(br)
    if level == 0:
        return bytes(h * 3)
    col = DEFAULT_CACHE.column(tex, level, tex_x)
    if _HAVE_NP:
        arr = np.frombuffer(col, dtype=np.uint8).reshape(TEX_W, 3)
        idx = (np.arange(h, dtype=np.int64) * (TEX_W - 1)) // max(1, h - 1)
        return arr[idx].tobytes()
    out = bytearray(h * 3)
    for r in range(h):
        sr = (r * (TEX_W - 1)) // max(1, h - 1)
        off = sr * 3
        oo = r * 3
        out[oo] = col[off]
        out[oo + 1] = col[off + 1]
        out[oo + 2] = col[off + 2]
    return bytes(out)


def _frame_column_span(i, nc, iw):
    """(bx, fw): framebuffer column span for screen column ``i``."""
    bx = (i * iw) // nc
    bx2 = (i + 1) * iw // nc
    if bx2 <= bx:
        bx2 = bx + 1
    return bx, bx2 - bx


def _frame_ys(y0, y1, rh, ih):
    """Convert a screen-space vertical band [y0, y1) to framebuffer rows."""
    f0 = int(y0 * ih / rh)
    f1 = int(y1 * ih / rh)
    if f1 <= f0:
        f1 = f0 + 1
    if f0 < 0:
        f0 = 0
    if f1 > ih:
        f1 = ih
    return f0, f1


def _frame_blit_wall(frame, tex, tex_x, bx, bw, top, bot, br, rd=None):
    """Write a wall column (framebuffer rect ``bw`` px wide, rows top..bot)
    sampled from ``tex`` column ``tex_x`` shaded by ``br``.

    With ``rd`` (ray distance) the hot path takes a pre-scaled
    (texture, light, distance bucket) strip column from the wall cache and
    slices it into the frame (task 2.2); without it the level-1 lit column
    cache is vertical-sampled to ``h`` (task 2.1)."""
    h = bot - top
    if h <= 0 or bx >= frame.iw:
        return
    from engine.wallcache import (DEFAULT_CACHE, bucket_for_dist,
                                         light_level)
    strip = None
    if rd is not None:
        try:
            DEFAULT_CACHE.set_view(gs.view_dist)
            edges = DEFAULT_CACHE._bucket_edges
            bucket = bucket_for_dist(max(0.0, rd), edges)
            hb = DEFAULT_CACHE.bucket_heights()[bucket]
            col = DEFAULT_CACHE.strip_column(tex, light_level(br), bucket,
                                             tex_x)
            if col is not None:
                if h == hb:
                    strip = col
                elif _HAVE_NP:
                    arr = np.frombuffer(col, dtype=np.uint8).reshape(hb, 3)
                    idx = (np.arange(h, dtype=np.int64) * (hb - 1)) // (
                        max(1, h - 1))
                    strip = arr[idx].tobytes()
                else:
                    out = bytearray(h * 3)
                    for r in range(h):
                        sr = (r * (hb - 1)) // max(1, h - 1)
                        out[r * 3:r * 3 + 3] = col[sr * 3:sr * 3 + 3]
                    strip = bytes(out)
        except Exception:
            strip = None
    if strip is None:
        strip = _wall_strip(tex, tex_x, h, br)
    if bw == 1 or h <= 0:
        frame.blit_column(bx, top, strip, h)
        return
    # widen the column horizontally: byte-level replication
    data = bytearray(h * bw * 3)
    src = memoryview(strip)
    for r in range(h):
        base = r * bw * 3
        rgb = src[r * 3:r * 3 + 3]
        for c in range(bw):
            data[base + c * 3:base + c * 3 + 3] = rgb
    frame.blit_rgb(bx, top, bw, h, data)


def _refresh_frame(frame, nc, rh):
    """Draw the solid flats into the framebuffer (tile mode)."""
    iw, ih = frame.iw, frame.ih
    top_h = rh // 2
    bat, bbot = _frame_ys(0, top_h, rh, ih)
    if bat < bbot:
        frame.fill_rect(0, bat, iw, bbot - bat, _FRAME_FLAT_TOP)
    fat, fbot = _frame_ys(top_h, rh, rh, ih)
    if fat < fbot:
        frame.fill_rect(0, fat, iw, fbot - fat, _FRAME_FLAT_BOT)

_RH = const.H
_RY = 0

# Pre-generated star field


# Load SSG sprite frames from PNG files
_SSG_FILES = {
    1: 'skins/dwust_1.png', 2: 'skins/dwust_2.png', '23': 'skins/dwust_2-3.png', '233': 'skins/dwust_23-3.png',
    3: 'skins/dwust_3.png', 4: 'skins/dwust_4.png', 5: 'skins/dwust_5.png', 6: 'skins/dwust_6.png'
}
_ssg_frames = {}
_wpn_sprites = {}

# Weapon view models are normalised to one height per weapon, expressed in
# reference-window layout units (REF_H = 700) and multiplied by ui_scale().
# The loaders used to bake a literal 3.0x/2.5x/1.33x into each PNG, so the
# weapons spanned 17%..119% of the window height -- the rifle overflowed a 700px
# window outright and every one of them was only correct at the single size it
# was tuned at.
WPN_TARGET_H = {
    'CHAINSAW': 230, 'PISTOL': 250, 'SHOTGUN': 250,
    'RIFLE': 280, 'BFG': 220, 'SSG': 216,
}
_wpn_fitted = {}
_WPN_FITTED_MAX = 64


def _fit_wpn(key, img, target_h):
    """`img` scaled to `target_h`, preserving aspect ratio.

    Keyed by (frame, target height): a resize misses once and re-fits, a steady
    window is a dict hit.  No per-frame transform, which is the point -- the
    weapon blit is on the hot path every frame.
    """
    ck = (key, target_h)
    surf = _wpn_fitted.get(ck)
    if surf is None:
        iw, ih = img.get_width(), img.get_height()
        if iw < 1 or ih < 1 or target_h < 1:
            surf = img
        else:
            surf = pygame.transform.scale(img, (max(1, int(iw * target_h / ih)), target_h))
        if len(_wpn_fitted) >= _WPN_FITTED_MAX:
            _wpn_fitted.clear()
        _wpn_fitted[ck] = surf
    return surf

# Compose a skin path from the base assets dir (not CWD).
def _skin_path(rel):
    return os.path.join(gs.assets_dir, rel)


def _get_ssg(i):
    if i not in _ssg_frames:
        try:
            # Stored at native resolution: the per-frame multipliers in
            # _SSG_SCALE were absolute pixel results, not proportions, so they
            # had no meaning at any other window size.  _fit_wpn() now normalises
            # every frame of a weapon to one target height.
            _ssg_frames[i] = pygame.image.load(_skin_path(_SSG_FILES[i])).convert_alpha()
        except:
            _ssg_frames[i] = pygame.Surface((1, 1))
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
        _PISTOL_IDLE = pygame.image.load(_skin_path('skins/pistol_1.png')).convert_alpha()
        _PISTOL_FIRE = pygame.image.load(_skin_path('skins/pistol_2.png')).convert_alpha()
    except:
        _PISTOL_IDLE = pygame.Surface((1, 1))
        _PISTOL_FIRE = pygame.Surface((1, 1))

def _load_rifle():
    global _RIFLE_IDLE, _RIFLE_FIRE
    try:
        _RIFLE_IDLE = pygame.image.load(_skin_path('skins/riffle_1.png')).convert_alpha()
        _RIFLE_FIRE = pygame.image.load(_skin_path('skins/riffle_2.png')).convert_alpha()
    except:
        _RIFLE_IDLE = pygame.Surface((1, 1))
        _RIFLE_FIRE = pygame.Surface((1, 1))

def _load_shotgun():
    global _SHOTGUN_IDLE, _SHOTGUN_FIRE, _SHOTGUN_RECOIL
    try:
        _SHOTGUN_IDLE = pygame.image.load(_skin_path('skins/shootgun_1.png')).convert_alpha()
        _SHOTGUN_FIRE = pygame.image.load(_skin_path('skins/shootgun_2.png')).convert_alpha()
        _SHOTGUN_RECOIL = pygame.image.load(_skin_path('skins/shootgun_3.png')).convert_alpha()
    except:
        _SHOTGUN_IDLE = _SHOTGUN_FIRE = _SHOTGUN_RECOIL = pygame.Surface((1, 1))


def _load_chainsaw():
    global _CHAINSAW_IDLE, _CHAINSAW_FIRE
    try:
        _CHAINSAW_IDLE = pygame.image.load(_skin_path('skins/chainsaw_1.png')).convert_alpha()
        _CHAINSAW_FIRE = pygame.image.load(_skin_path('skins/chainsaw_2.png')).convert_alpha()
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
                    by = const.H - sh_s - ui_px(20) + bob
                    gs.screen.blit(pygame.transform.scale(spr, (sw, sh_s)), (bx, by))
            else:
                model_name = wdef.get('model')
                if model_name:
                    from voxel import load_voxel, render_voxel_facing
                    voxels, palette = load_voxel(model_name)
                    if voxels:
                        spr, _ = render_voxel_facing(voxels, palette, 30, 64)
                        if spr:
                            # Fraction of the window, like the hand sprite above,
                            # so a voxel model is not a fixed pixel block either.
                            scale_f = min(const.W * 0.3 / spr.get_width(), const.H * 0.4 / spr.get_height())
                            sw = max(1, int(spr.get_width() * scale_f))
                            sh = max(1, int(spr.get_height() * scale_f))
                            bx = const.W // 2 - sw // 2
                            by = const.H - sh - ui_px(30) + bob
                            gs.screen.blit(pygame.transform.scale(spr, (sw, sh)), (bx, by))
                else:
                    col = wdef.get('color', (200, 200, 100))
                    sz = ui_px(80)
                    bx = const.W // 2 - sz // 2
                    by = const.H - sz - ui_px(30) + bob
                    pygame.draw.rect(gs.screen, col, (bx, by, sz, sz))
                    pygame.draw.rect(gs.screen, (255, 255, 255), (bx, by, sz, sz), 2)
        return
    info = WPN[idx]
    # Animation phase as a fraction of the weapon's own cooldown (1.0 at the
    # shot, 0.0 when idle). Thresholds are fractions of `f`, never absolute
    # frame counts, so the layout follows the weapon data instead of being
    # tuned to one weapon's tempo. Clamped: a switch or a slack gate can hand
    # us a tmr larger than this weapon's f.
    p = (min(max(tmr / info['f'], 0.0), 1.0) if info['f'] else 0.0) if tmr > 0 else 0.0
    rcl = math.sin(p * math.pi) * ui_px(100) if tmr > 0 else 0
    wn = info['n']
    # Every frame of a weapon lands on the same bottom gap above the screen
    # edge, in layout units -- the gun no longer grows out of the window at
    # 4K nor swallows it at 640x480.
    gap = ui_px(30)
    wx = const.W // 2
    target_h = ui_px(WPN_TARGET_H.get(wn, 250))

    def _blit(key, img, off=0):
        surf = _fit_wpn(key, img, target_h)
        gs.screen.blit(surf, (wx - surf.get_width() // 2, _RY + _RH - gap - surf.get_height() + rcl + bob + off))

    if wn == 'CHAINSAW':
        if _CHAINSAW_IDLE is None:
            _load_chainsaw()
        shake = random.randint(-ui_px(8), ui_px(8)) if fire else 0
        img = _CHAINSAW_FIRE if fire else _CHAINSAW_IDLE
        surf = _fit_wpn(('CHAINSAW', fire), img, target_h)
        gs.screen.blit(surf, (wx - surf.get_width() // 2 + shake,
                              _RY + _RH - gap - surf.get_height() + rcl + bob))
    elif wn == 'PISTOL':
        if _PISTOL_IDLE is None:
            _load_pistol()
        _blit(('PISTOL', fire), _PISTOL_FIRE if fire else _PISTOL_IDLE)
    elif wn == 'SHOTGUN':
        if _SHOTGUN_IDLE is None:
            _load_shotgun()
        if p > 0.5:
            img, frame = _SHOTGUN_FIRE, 'fire'
        elif p > 0.0:
            img, frame = _SHOTGUN_RECOIL, 'recoil'
        else:
            img, frame = _SHOTGUN_IDLE, 'idle'
        _blit(('SHOTGUN', frame), img)
    elif wn == 'RIFLE':
        if _RIFLE_IDLE is None:
            _load_rifle()
        _blit(('RIFLE', fire), _RIFLE_FIRE if fire else _RIFLE_IDLE)

    elif wn == 'BFG':
        glow = 255 if fire else 100
        # Drawn from primitives, so every offset below is a layout unit too.
        # wy is the muzzle reference point the shape was authored against.
        u = ui_px
        wy = _RY + _RH - gap + rcl + bob - u(160)
        pygame.draw.rect(gs.screen, (0, 40, 0), (wx - u(100), wy, u(200), u(220)))
        pygame.draw.rect(gs.screen, (0, 70, 0), (wx - u(80), wy + u(20), u(160), u(180)))
        pygame.draw.polygon(gs.screen, (0, 100, 0), [
            (wx - u(55), wy + u(20)), (wx + u(55), wy + u(20)),
            (wx + u(80), wy - u(60)), (wx + u(60), wy - u(80)),
            (wx - u(60), wy - u(80)), (wx - u(80), wy - u(60))
        ])
        pg = min(glow + 155, 255)
        pygame.draw.circle(gs.screen, (0, pg, 0), (wx, wy - u(65)), u(50))
        pygame.draw.circle(gs.screen, (pg, 255, pg), (wx, wy - u(65)), u(28))
        pygame.draw.circle(gs.screen, (255, 255, 255), (wx, wy - u(65)), u(12))
        pygame.draw.rect(gs.screen, (60, 40, 20), (wx - u(22), wy + u(150), u(44), u(36)))

    elif wn == 'SSG':
        # seven frames over eighths of the cooldown (the old 66/56/46/36/26/16/8
        # thresholds at f=75 are 0.88/0.75/0.61/0.48/0.35/0.21/0.11)
        if p > 7 / 8:
            k = 2
        elif p > 6 / 8:
            k = '23'
        elif p > 5 / 8:
            k = '233'
        elif p > 4 / 8:
            k = 3
        elif p > 3 / 8:
            k = 4
        elif p > 2 / 8:
            k = 5
        elif p > 1 / 8:
            k = 6
        else:
            k = 1
        # The pump is told by the frames themselves, so SSG keeps no recoil
        # offset -- only bob, exactly as before.
        surf = _fit_wpn(('SSG', k), _get_ssg(k), target_h)
        gs.screen.blit(surf, (wx - surf.get_width() // 2,
                              _RY + _RH - gap - surf.get_height() + bob))



# ---------------------------------------------------------------------------
# Enemy art (change ``enemy-model-overhaul``)
#
# The engine does not know what an enemy looks like.  It knows where the enemy
# stands, how tall it is on screen, which way it faces and what state it is in,
# and it asks ``gs.enemy_surface_provider`` for a finished SRCALPHA surface.
# Everything type-specific -- silhouette, palette, view, animation frame,
# vertical offset, overlays -- lives in the descriptor registry behind that
# slot (``client/enemy_models.py``).
#
# Rules that are easy to break by accident, so they are worth stating:
#   * no branch below may name an enemy type, not even to nudge a position;
#     a per-type special case here is exactly the duplication this change
#     removed, and it is what made a new type need new renderer code;
#   * a model's surface already carries its type's vertical offset, so the
#     floor line is the same for a floater as for a walker and the engine
#     bottom-aligns every one of them;
#   * every surface is a bounded-cache lookup, so a frame with 30 enemies in
#     4 states allocates nothing (enemy-presentation spec, "Стоимость кадра").
# ---------------------------------------------------------------------------

#: Cache identity the live surfaces were built under.  A tier change alters the
#: detail level, which is part of every model key, so the cache has to go.
_enemy_cache_tag = None

#: Neutral silhouette used when no provider is registered (updating_engine D2:
#: the engine degrades, it does not raise).  Cached per size, like
#: ``_blank_sprite`` above, so even the degraded path allocates nothing.
_GHOST_SPRITES = {}


def _ghost_sprite(size):
    size = max(4, min(int(size), FALLBACK_SPRITE_MAX))
    surf = _GHOST_SPRITES.get(size)
    if surf is None:
        surf = pygame.Surface((size, size), pygame.SRCALPHA)
        body = (size * 0.5, size * 0.86)
        pygame.draw.rect(surf, (185, 190, 200, 150),
                         (int(size * 0.25), int(size * 0.10),
                          max(1, int(body[0])), max(1, int(body[1]))))
        pygame.draw.circle(surf, (215, 220, 230, 150),
                           (size // 2, int(size * 0.13)),
                           max(1, int(size * 0.15)))
        _GHOST_SPRITES[size] = surf
    return surf


def _sync_enemy_cache():
    """Keep both enemy caches in step with the current settings.

    The engine's ``SpriteCache`` holds a distance-bucket table, so it follows
    the configured view distance; the model surfaces are keyed by detail level
    and are dropped when the tier (or the rollback flag) changes.  One place
    owns the lifecycle instead of every caller.
    """
    global _enemy_cache_tag
    from engine.sprite_cache import DEFAULT_SPRITE_CACHE, set_view_dist

    set_view_dist(getattr(gs, 'view_dist', 900.0))
    tag = (gs.quality_tier, bool(getattr(gs, 'enemy_models_enabled', True)))
    if tag == _enemy_cache_tag:
        return
    _enemy_cache_tag = tag
    _GHOST_SPRITES.clear()
    clear = getattr(getattr(gs, 'enemy_surface_provider', None), 'clear', None)
    if clear is not None:
        clear()


def _enemy_floor_y(sh, ground_y=None):
    """Screen y of the *floor line* an enemy stands on.

    Unchanged from the pre-model renderer: the sector floor when the projection
    supplied one, otherwise the same horizon/CAM_Z estimate the tile mode uses.
    The clamp to the lower band edge is what keeps a very close enemy from
    being drawn off the bottom of the buffer.
    """
    if ground_y is not None:
        return min(int(ground_y), _RY + _RH)
    if gs.sector_mode:
        return min(_RY + _RH // 2 + sh // 2, _RY + _RH)
    return min(_RY + _RH // 2 + int(CAM_Z * sh / TILE), _RY + _RH)


def _enemy_view(e):
    """Which of the 8 views to draw: where the player is, seen from the enemy.

    The enemy's own facing turns out to be the wrong input -- it is the *player's*
    position relative to the enemy that decides which side is visible -- so this
    is a pure geometry question and belongs to the engine, while the rounding
    rule lives in ``engine.sprite_cache.view_index`` (one definition, two
    importers, no engine -> client import).
    """
    bearing = math.atan2(gs.py - e['y'], gs.px - e['x'])
    return view_index(bearing - e.get('pa', 0.0))


def _enemy_blit(surf, x, y):
    """Blit a cached surface with alpha, tolerating SDL's blend quirk."""
    try:
        gs.screen.blit(surf, (x, y), special_flags=pygame.BLEND_ALPHA_SDL2)
    except pygame.error:
        gs.screen.blit(surf, (x, y))


def draw_enemy_sprite(e, sx, sh, tick, ground_y=None):
    """Draw one live enemy: shadow, model, state overlays, HP bar.

    ``sx``/``sh`` come from the same projection as every other sprite and
    ``ground_y`` from the sector floor, so positioning is the projection's
    business; everything below is "which surface, and where".
    """
    _sync_enemy_cache()
    provider = gs.enemy_surface_provider
    t = e['t']
    is_dying = e.get('dying', 0) > 0
    is_stunned = e.get('stun', 0) > 0 and not is_dying
    is_rushing = e.get('rush_timer', 0) > 0 and not is_dying
    state = e.get('anim_state') or ('death' if is_dying else 'idle')
    frame = e.get('anim_frame', 0)
    s = max(sh / 54.0, 0.5)
    floor_y = _enemy_floor_y(sh, ground_y)

    if provider is None or not gs.enemy_models_enabled:
        surf = _ghost_sprite(sh)
    else:
        surf = provider('model', t, sh, frame, state, _enemy_view(e),
                        enemy_detail(gs.quality_tier, sh), e.get('col'))
    if surf is None:
        surf = _ghost_sprite(sh)

    # The surface's bottom edge is the type's own floor contact, so one
    # bottom-align works for a walker, a floater and a corpse alike.
    w, h = surf.get_size()
    ox = int(sx - w // 2)
    oy = int(floor_y - h + 1)

    # Contact shadow, on the floor rather than on the body, which is why it
    # stays put while a floater hovers above it.
    if provider is not None and gs.enemy_models_enabled:
        shadow = provider('shadow', t, sh)
        if shadow is not None:
            sw, shh = shadow.get_size()
            _enemy_blit(shadow, int(sx - sw // 2), int(floor_y - shh // 2))

    _enemy_blit(surf, ox, oy)

    if is_stunned and provider is not None:
        wash = provider('stun', t, sh, step=min(3, int(e['stun'])),
                        detail=min(3, int(e['stun'])))
        if wash is not None:
            _enemy_blit(wash, ox, oy)

    if e.get('d_flash', 0) > 0 and provider is not None:
        flash = provider('flash', t, sh, step=min(5, int(e['d_flash']) - 1),
                         base=surf)
        if flash is not None:
            _enemy_blit(flash, ox, oy)

    if is_rushing and provider is not None:
        aura = provider('rush', t, sh, step=int(e['rush_timer']) % 4, base=surf)
        if aura is not None:
            _enemy_blit(aura, ox, oy)

    # Eye glow in unlit areas.  Brightness comes from distance only (never from
    # the frame clock), so it does not pulse with the frame rate, and the two
    # glows are one cached surface instead of two 8x8 allocations per enemy.
    if not is_dying and not is_rushing:
        edx2 = e['x'] - gs.px
        edy2 = e['y'] - gs.py
        dst2 = math.sqrt(edx2 ** 2 + edy2 ** 2)
        glow_alpha = max(0, min(180, int((dst2 - 100) * 0.4)))
        if glow_alpha > 20 and provider is not None:
            eg = provider('eyes', t, int(6 * s), min(3, glow_alpha // 48))
            if eg is not None:
                _enemy_blit(eg, int(sx - eg.get_width() // 2),
                            int(oy + h * 0.12))

    # HP bar on the floor line, so it reads the same for a floater.
    mhp = e.get('max_hp', ENEMY_DB.get(t, {}).get('hp', 100)) or 1
    bar_w = int(22 * s)
    bh_bar = max(1, int(4 * s))
    by_bar = floor_y + 2
    pygame.draw.rect(gs.screen, (60, 0, 0), (sx - bar_w // 2, by_bar, bar_w, bh_bar))
    if e['hp'] > 0:
        pygame.draw.rect(gs.screen, (0, 200, 0),
                         (sx - bar_w // 2, by_bar,
                          max(0, int(bar_w * e['hp'] / mhp)), bh_bar))


def draw_corpse(c, sx, sh, ground_y=None):
    """A dead enemy's body: its own silhouette, flattened and dimmed.

    The engine only supplies the type and the projected size -- which silhouette
    and palette that is, is the registry's business, including the generic
    fallback for a type it has never heard of.
    """
    _sync_enemy_cache()
    provider = gs.enemy_surface_provider
    floor_y = _enemy_floor_y(sh, ground_y)
    if provider is None or not gs.enemy_models_enabled:
        surf = _ghost_sprite(max(4, int(sh * 0.4)))
    else:
        surf = provider('corpse', c['t'], sh, col=c.get('col'),
                        detail=enemy_detail(gs.quality_tier, sh))
    if surf is None:
        surf = _ghost_sprite(max(4, int(sh * 0.4)))
    w, h = surf.get_size()
    _enemy_blit(surf, int(sx - w // 2), int(floor_y - h + 1))



def _render_sector_scene_legacy():
    """[removed in advanced-sector-editor] superseded by sector_render."""
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


# --- Own-dash radial rays (dash-fx) ---------------------------------------
# The cue is a handful of cached wedge sprites, one blit each per frame. The
# first version composited the streaks into one full-screen SRCALPHA overlay and
# blitted that: one W*H composite every frame. Measured here that costs 12 ms at
# 4K for a plain alpha blit of a full-screen surface and 43 ms when the surface
# alpha is modulated per frame, which is a budget the whole renderer does not
# have. SDL blends every pixel of the destination rect, so a full-screen
# overlay always pays for the whole screen even though the field covers a few
# percent of it -- the ink has to be blitted as small rects instead.
#
# A wedge is therefore a triangle: its apex points at the aim point and it widens
# towards the border. Alpha climbs to full over the first third of the length and
# fades back out over the last quarter, so both ends reach exactly zero and a wedge
# can be born out of the aim point and leave the screen without a seam or a hard
# cut. The sides are feathered, because an unfeathered triangle at 4K is a stair
# step. The profile is baked once into a single small template, scaled per wedge
# and turned to its angle, so a whole field costs a fraction of a millisecond and
# is rebuilt only when the viewport or the quality-derived count changes.
#
# The field is a full circle, not four bars on the screen axes: wedges are laid
# out in three rings around the aim point, and each one is clamped to the border
# of the view band ALONG ITS OWN DIRECTION, so the aperture is radially symmetric
# on any window shape. Four axis-aligned wedges read as a "+" pasted onto the
# edges of the view and as nothing at all in the corners; on a 16:9 window the
# vertical bars could not even get as long as the horizontal ones.
#
# Wedges are SHORT, and that is a budget decision as much as a look one. A turned
# sprite is blitted as its axis-aligned bounding box, and for a long thin triangle
# that box is a couple of dozen times the ink inside it: measured here, a 4K field
# of full-radius wedges covered 87 % of the frame in boxes while painting 3.6 % of
# it, and cost 13 ms a frame -- SDL blends every pixel of the rectangle, empty
# ones included. Sizing the length in layout units and keeping it a small multiple
# of the width keeps the box a few times the ink instead of two dozen, which is
# also what a speed line looks like in every other game: a streak, not a spear.
#
# Wedges sweep outward along their own ray and wrap: the apex walks from the dead
# zone past the border, where the transparent apex makes the reset invisible.
# Travel is a share of the distance to the border rather than a fixed number of
# pixels, so a dash reads the same on a 640x480 window and at 4K, and the pattern
# streams instead of flashing on the spot.
_RAY_TPL_W = 128
_RAY_TPL_H = 24
_RAY_RISE = 0.30        # share of the length over which a wedge reaches full alpha
_RAY_FALL = 0.26        # share of the length over which it fades back out to zero
_RAY_EDGE = 0.22        # feather of the wedge sides, as a share of half its width
_RAY_RINGS = 3
_RAY_RING_OFF = (0.0, 0.17, 0.31)   # inner radius of each ring, as a share of the border
_ray_tpl = None
_ray_rng = random.Random(0xD45A5)


def _ramp(u):
    """Smoothstep that saturates at 1 -- for feathers and anything clamped."""
    u = 0.0 if u < 0.0 else (1.0 if u > 1.0 else u)
    return u * u * (3.0 - 2.0 * u)


def _ray_alpha(t):
    """0 -> full -> 0 along a wedge's length, maximum across the middle stretch.

    The zeros at the endpoints are the point: they are what lets a wedge appear
    out of nothing and run off the border without a visible cut. It is also why
    this must not be reused for a feather -- a saturating ramp there would put a
    hole down the middle of every wedge.
    """
    if t <= 0.0 or t >= 1.0:
        return 0.0
    up = t / _RAY_RISE if t < _RAY_RISE else 1.0
    left = (1.0 - t) / _RAY_FALL if t > 1.0 - _RAY_FALL else 1.0
    return _ramp(up) * _ramp(left)


def _ray_template():
    """The one baked wedge every field sprite is scaled and turned from."""
    global _ray_tpl
    if _ray_tpl is not None:
        return _ray_tpl
    r, g, b = const.DASH_FX_STREAK_RGB[:3]
    tpl = pygame.Surface((_RAY_TPL_W, _RAY_TPL_H), pygame.SRCALPHA)
    half_max = _RAY_TPL_H * 0.5 - 1.0
    for x in range(_RAY_TPL_W):
        t = (x + 0.5) / _RAY_TPL_W
        prof = _ray_alpha(t)
        half = 0.10 + 0.90 * t            # wedge: apex -> full base
        for y in range(_RAY_TPL_H):
            d = abs(y - half_max) / half_max
            if d >= half:
                a = 0.0
            else:
                a = prof * _ramp((half - d) / _RAY_EDGE)
            tpl.set_at((x, y), (r, g, b, int(255.0 * a)))
    _ray_tpl = tpl
    return tpl


def _ray_sprite(tpl, length, width, ang):
    """Scale the wedge template to ``length`` x ``width`` and point it along ``ang``.

    pygame.rotate spins a surface about its centre, so where the apex lands is
    not something to re-derive here from its matrix -- and getting it wrong
    would fling every wedge sideways, which is exactly the bug the axis-aligned
    version could not have had. One opaque pixel goes through the very same call
    and is read back with getbbox(); both are C-level, and this only runs when
    the viewport or the count changes.
    """
    deg = -math.degrees(ang)             # screen y points down, so a positive
    spr = pygame.transform.smoothscale(tpl, (length, width))   # angle is CW on screen
    mark = pygame.Surface((length, width), pygame.SRCALPHA)
    mark.set_at((0, width >> 1), (255, 255, 255, 255))
    # get_bounding_rect, not Surface.get_bbox: on a Surface the name belongs to
    # the old pygame 1.x API that pygame 2 no longer has.
    bb = pygame.transform.rotate(mark, deg).get_bounding_rect()
    if bb.width < 1 or bb.height < 1:    # pragma: no cover - the marker is opaque
        return spr, 0, width >> 1
    rot = pygame.transform.rotate(spr, deg)
    # The rotated rectangle is a superset of the triangle's own footprint; the
    # empty corners would be blended every frame, so the ink is cropped to it.
    rbb = rot.get_bounding_rect()
    if rbb.width < 1 or rbb.height < 1:  # pragma: no cover - the template has ink
        rbb = pygame.Rect(0, 0, *rot.get_size())
    return rot.subsurface(rbb).copy(), bb.x - rbb.x, bb.y - rbb.y


def _ray_table():
    """``(angle, ring, len_mult, wid_mult, peak, phase, speed)`` for every wedge.

    Rings are listed one after another -- all of ring 0 first -- so that ANY
    prefix of the table is a complete circle: the quality knob drops the outer
    rings and what is left still points at the aim point from every direction
    instead of degenerating into a fan over the eight angles that happened to be
    interleaved first.
    """
    per_ring = const.DASH_FX_STREAK_RING
    spread = 2.0 * math.pi / per_ring
    out = []
    for ring_i in range(_RAY_RINGS):
        ring = _RAY_RING_OFF[ring_i]
        for k in range(per_ring):
            out.append((
                (k + _ray_rng.uniform(-0.30, 0.30)) * spread,
                ring,
                _ray_rng.uniform(0.62, 1.45),
                _ray_rng.uniform(0.60, 1.30),
                _ray_rng.randint(105, 195),
                _ray_rng.random(),
                _ray_rng.uniform(0.75, 1.30),
            ))
    return tuple(out)


_DASH_RAYS = _ray_table()


class _Ray:
    """One baked white wedge plus everything its per-frame blit needs."""

    __slots__ = ('spr', 'ax', 'ay', 'ux', 'uy', 'peak', 'travel', 'spd',
                 'phase', 'r0', 'd')


_ray_field = None
_ray_key = None
_ray_env = 0.0


def _build_ray_field(w, rh, n):
    """Bake ``n`` white wedges radiating from the aim point of a ``w`` x ``rh`` view band.

    Takes the first ``n`` wedges that FIT rather than the first ``n`` of the table:
    on a short viewport the outer rings have no room to travel, and dropping a
    wedge the quality knob asked for would make the field thinner exactly where
    it has least to show.
    """
    rays = []
    if w <= 0 or rh <= 0:
        return rays
    tpl = _ray_template()
    dead = min(w, rh) * const.DASH_FX_STREAK_DEADZONE
    for ang, ring, lmul, wmul, peak, phase, spd in _DASH_RAYS:
        if len(rays) >= n:
            break
        ux = math.cos(ang)
        uy = math.sin(ang)
        # Thickness is a layout unit too: at 4K a literal 2px wedge is invisible.
        width = ui_px(const.DASH_FX_STREAK_WIDTH * wmul) or 1
        # How far the wedge may reach along its own direction before its WIDE END
        # leaves the view band. The band is a rectangle and the wedge is not, so
        # its half width eats into the room at an angle: reaching the border and
        # then adding the width would put ink on the HUD bands above and below,
        # and a 45-degree wedge overshoots further than one aimed at the short
        # side. Solving for the reach directly is what keeps the field inside the
        # band for every angle without a per-wedge fudge.
        hw = width * 0.5
        ex, ey = abs(ux), abs(uy)
        bx = ((w * 0.5) - hw * ey) / ex if ex > 1e-6 else 1e9
        by = ((rh * 0.5) - hw * ex) / ey if ey > 1e-6 else 1e9
        border = bx if bx < by else by
        if border <= dead:
            continue
        r0 = dead + border * ring
        room = border - r0
        # Length is a layout unit, scaled off the border only so that a small
        # window still gets streaks proportional to its size instead of a fixed
        # count of full-length spears, and trimmed to what this direction leaves:
        # a wide, short band gives a wedge aimed up or down a fraction of the room
        # one aimed sideways has, and dropping those instead would leave a bare gap
        # in the vertical -- thinning the field on the shapes that can least afford it.
        length = min(ui_px(const.DASH_FX_STREAK_LEN * lmul) + 4,
                     max(6, int(border * 0.45)))
        length = min(length, int(room) - 2)
        travel = room - length
        if travel <= 2.0 or length < 6:
            continue
        spr, ax, ay = _ray_sprite(tpl, length, width, ang)
        r = _Ray()
        r.spr, r.ax, r.ay = spr, ax, ay
        r.ux, r.uy = ux, uy
        r.peak, r.travel, r.spd = peak, travel, spd
        r.phase = phase
        r.r0 = r0
        r.d = r0 + travel * phase
        rays.append(r)
    return rays


def draw_dash_streaks(rh=None, ry=None):
    """Radial speed wedges for the local player's own dash.

    Decays ``gs.dash_fx`` and, while it is positive, sweeps the wedge sprites
    outward from the aim point over the world. Costs nothing at rest: the first
    check is a float compare, and nothing is baked before the first dash.
    """
    global _ray_field, _ray_key, _ray_env
    fx = gs.dash_fx
    if fx <= 0.0:
        return
    fx -= const.DASH_FX_FADE
    gs.dash_fx = fx if fx > 0.0 else 0.0
    if fx <= 0.0:
        return
    if rh is None:
        rh = _RH
    if ry is None:
        ry = _RY
    w = const.W
    # The cue is the only screen-space signal of a dash, so the quality knob
    # scales the count down but never below one whole ring.
    n = int(const.DASH_FX_STREAKS * gs.effects_factor)
    n = max(const.DASH_FX_STREAKS_MIN, min(const.DASH_FX_STREAKS, n))
    if _ray_field is None or _ray_key != (w, rh, n):
        _ray_field = _build_ray_field(w, rh, n)
        _ray_key = (w, rh, n)
    if fx > _ray_env:
        # Rising edge: a fresh dash re-seeds the sweep, so the wedges start spread
        # over their travel again instead of continuing wherever the last one left
        # them. A wrap mid-dash stays invisible either way (both ends are clear).
        for r in _ray_field:
            r.d = r.r0 + r.travel * r.phase
    _ray_env = fx
    # Travel follows the envelope -- fastest while the dash bites, down to a
    # crawl as it fades -- so one dash is roughly one sweep to the border.
    step = const.DASH_FX_STREAK_FLOW * fx
    cx, cy = w * 0.5, rh * 0.5
    screen = gs.screen
    for r in _ray_field:
        d = r.d + r.travel * step * r.spd
        if d >= r.r0 + r.travel:
            # The apex has walked its whole range: the wedge sits at the border and
            # re-seeds at the dead zone, which is invisible because both of its ends
            # reach zero alpha.
            d -= r.travel
        r.d = d
        a = int(r.peak * fx)
        if a <= 2:
            continue
        r.spr.set_alpha(a)
        screen.blit(r.spr, (int(cx + r.ux * d) - r.ax, ry + int(cy + r.uy * d) - r.ay))


def render(vh=None, vy=None):
    global _RH, _RY
    _RH = vh if vh is not None else const.H
    _RY = vy if vy is not None else 0
    # The HUD fonts are sized in layout units, so they depend on the window and
    # must be re-resolved on every frame -- cheap: one scale compute and a dict
    # lookup, and the cache keeps a single pair alive between resizes.
    ensure_hud_fonts()
    curr_t = pygame.time.get_ticks()
    # Content providers, resolved once per frame (updating_engine 2.3): the
    # per-sprite / per-wall call sites must not pay an attribute lookup chain
    # on every hit.  None degrades to neutral content instead of raising.
    tile_provider = gs.tile_provider
    bot_surface = gs.sprite_provider

    # ── Sector mode — DDA portal renderer ──
    if gs.sector_mode:
        from engine import sector_render

        z_b = sector_render.render_scene(_RH, _RY)
    else:
        # ── Original tile-based rendering ──
        # Ray columns. This is the tile path's own grid, not the depth
        # buffer's: the two are only the same here, and every consumer of
        # ``z_b`` below asks the buffer for its column via ``depth_col``
        # instead of assuming this number.
        NC = const.W // SCALE
        # Player eye height (DOOM standard: 41 units)
        pz = CAM_Z
        # Internal framebuffer for this tier; ceiling/floor base bands.
        frame = _ensure_frame()
        _refresh_frame(frame, NC, _RH)
        iw_f = frame.iw
        ih_f = frame.ih
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

        # Ceiling — solid color base (into the internal framebuffer)
        # Floor — solid color base (into the internal framebuffer)
        # (handled by _refresh_frame above)

        # Wall raycasting with textures
        z_b = [999.0] * NC
        crack_segments = []
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
                    # Texel from the EXACT crossing of the crossed tile
                    # plane: the 6-unit step leaves test_x/test_y up to 6
                    # units past the face, so the texel used to depend on the
                    # player's position and view angle. Phase is unchanged.
                    if cur_tx != prev_tx:
                        side = 0
                        bx = ((prev_tx + 1) * TILE) if cur_tx > prev_tx \
                            else (cur_tx * TILE)
                        if c != 0.0:
                            hit_y = gs.py + s * ((bx - gs.px) / c)
                            hit_x = bx
                        else:
                            hit_x, hit_y = test_x, test_y
                        tex_x = int((hit_y - cur_ty * TILE) / TILE * TEX_W) % TEX_W
                    else:
                        side = 1
                        by = ((prev_ty + 1) * TILE) if cur_ty > prev_ty \
                            else (cur_ty * TILE)
                        if s != 0.0:
                            hit_x = gs.px + c * ((by - gs.py) / s)
                            hit_y = by
                        else:
                            hit_x, hit_y = test_x, test_y
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
                                        bx_, bw_ = _frame_column_span(i, NC, iw_f)
                                        ft_, fb_ = _frame_ys(gap_top, gap_bot, _RH, ih_f)
                                        if ft_ < fb_:
                                            frame.fill_rect(bx_, ft_, bw_, fb_ - ft_, gap_col)

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
                            an = tile_provider(at) if tile_provider else 'wall'
                            adj_names.append(an)
                tex_name = max(set(adj_names), key=adj_names.count) if adj_names else 'wall'
            else:
                tex_name = tile_provider(tile) if tile_provider else 'wall'
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
                    bx_, bw_ = _frame_column_span(i, NC, iw_f)
                    ft_, fb_ = _frame_ys(draw_y, draw_y + wh, _RH, ih_f)
                    if ft_ < fb_:
                        frame.fill_rect(bx_, ft_, bw_, fb_ - ft_, (100, 100, 100))
                    continue
                col_strip = tex.subsurface((tex_x % TEX_W, 0, 1, min(tex_h, tex.get_height())))
            except:
                col_strip = pygame.Surface((1, 1))
                col_strip.fill((br, br, br))
            try:
                br = max(0, 255 - int(rd * 0.25))
                # Animated textures (blinking lights)
                if tile in ('E', 'K', 'S', 'L'):
                    pulse = math.sin(curr_t * 0.005 + cur_tx + cur_ty) * 0.3 + 0.7
                    br = int(br * pulse)
                bx_, bw_ = _frame_column_span(i, NC, iw_f)
                ft_, fb_ = _frame_ys(draw_y, draw_y + wh, _RH, ih_f)
                _frame_blit_wall(frame, tex, tex_x, bx_, bw_, ft_, fb_, br, rd)
            except:
                br = max(0, 255 - int(rd * 0.25))
                bx_, bw_ = _frame_column_span(i, NC, iw_f)
                ft_, fb_ = _frame_ys(draw_y, draw_y + wh, _RH, ih_f)
                if ft_ < fb_:
                    frame.fill_rect(bx_, ft_, bw_, fb_ - ft_, (br, br, br))

            # Damaged destructible walls crack as HP drops. Segments are
            # collected per column here and drawn on the upscaled screen below:
            # the overlay must run for every column that hit such a wall, not
            # once after the ray loop with whatever the last column happened to
            # hit (which is why cracks only ever appeared on one column).
            if tile in ('H', 'T', 'W', 'M', 'I', 'P') and wh > 10:
                dhp = gs.dwall_hp.get((cur_tx, cur_ty))
                if dhp is not None and dhp < 60:
                    crack_density = 1.0 - (dhp / 60.0)
                    wh_i = int(wh)
                    for _ in range(max(1, int(3 * crack_density))):
                        cy2 = draw_y + random.randint(0, max(1, wh_i - 1))
                        cx2 = i * SCALE + random.randint(0, SCALE - 1)
                        cl = max(1, int(3 * crack_density))
                        crack_segments.append((cx2, cy2, cl))

        # One upscale of the internal buffer onto the screen per frame.
        gs.frame.draw_to(gs.screen, (const.W, _RH))

        # Crack overlay on damaged destructible walls, after the upscale so the
        # 1px cracks stay crisp.
        for cx2, cy2, cl in crack_segments:
            pygame.draw.line(gs.screen, (20, 15, 10),
                             (cx2 - cl, cy2), (cx2 + cl, cy2 - cl // 2), 1)
            pygame.draw.line(gs.screen, (20, 15, 10),
                             (cx2 - cl // 2, cy2 + 1), (cx2 + cl, cy2 + 1), 1)

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
            if gs.sector_mode and gs.sec_scene is not None:
                from engine import sector_render

                if 'hp' in s_obj or 'time' in s_obj:
                    world_h = ENEMY_GAME_HEIGHTS.get(s_obj['t'], 50)
                elif s_obj.get('type') == 'barrel':
                    world_h = 48
                elif 't' in s_obj and 'id' in s_obj:
                    # Pickup. Enemies and corpses also carry a 't', but they
                    # are matched by 'hp' / 'time' above, so a 't' that
                    # reaches this branch is a pickup -- told apart by the id
                    # the snapshot carries, which is also what lets the client
                    # reconcile a respawn instead of drawing a second copy.
                    # The height is per type: a flat 64 here drew a medkit as
                    # tall as a BFG pickup and made the floor look littered
                    # with billboards.
                    world_h = ITEM_GAME_HEIGHTS.get(s_obj['t'], 48)
                else:
                    world_h = 64
                proj = sector_render.project_object(
                    s_obj['x'], s_obj['y'], world_h, _RH, _RY)
                if proj is None:
                    continue
                sx, sh, spr_flr, perp = proj
                sh = int(min(sh, _RH * 2))
                spr_flr = int(spr_flr)
                occ = perp
            else:
                sx = int((0.5 * (ang / (FOV / 2)) + 0.5) * const.W)
                sh = int((TILE * 750) / (dst + 0.1))
                spr_flr = _RY + _RH // 2 + int(CAM_Z * sh / TILE) - sh // 4
                sh = min(sh, _RH * 2)
                occ = dst
            # Pickups hover instead of lying on the floor line. The offset is
            # a ratio of the height the projection just gave us, not a second
            # projection of ITEM_FLOAT_HEIGHT: recomputing FOCAL / perp here
            # would be the copy of that arithmetic that put particles under
            # the floor once already. In sector mode sh is the item's own
            # height; the legacy tile path has no per-type item heights (tile
            # maps carry no things), so there the ratio is against a tile.
            if 't' in s_obj and 'id' in s_obj:
                spr_flr -= int(sh * ITEM_FLOAT_HEIGHT)
            idx = depth_col(z_b, sx, const.W)
            if idx >= 0 and z_b[idx] > occ:
                if s_obj.get('type') == 'barrel':
                    r = int(sh * 0.4)
                    brl_col = (160, 40, 20)
                    pygame.draw.rect(gs.screen, brl_col, (sx - r, spr_flr - r, r * 2, r * 2))
                    pygame.draw.rect(gs.screen, (80, 60, 50), (sx - r, spr_flr - r - 2, r * 2, 4))
                    pygame.draw.rect(gs.screen, (80, 60, 50), (sx - r, spr_flr + r - 2, r * 2, 4))
                elif 'hp' in s_obj:
                    draw_enemy_sprite(s_obj, sx, sh, curr_t,
                                      ground_y=int(spr_flr) if gs.sector_mode else None)
                elif 'time' in s_obj:
                    draw_corpse(s_obj, sx, sh,
                                ground_y=int(spr_flr) if gs.sector_mode else None)
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
                    if gs.sector_mode and gs.sec_scene is not None:
                        from engine import sector_render

                        proj = sector_render.project_object(
                            ex, ey, 96, _RH, _RY)
                        if proj is None:
                            continue
                        sx, sh, cy, occ = proj
                    else:
                        sx = int((0.5 * (ang / (FOV / 2)) + 0.5) * const.W)
                        sh = int((TILE * 750) / (dst + 0.1))
                        cy = _RY + _RH // 2 + sh // 4
                        occ = dst
                    sh = int(min(sh, _RH * 2))
                    cy = int(cy)
                    idx = depth_col(z_b, sx, const.W)
                    if idx >= 0 and z_b[idx] > occ:
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
                if gs.sector_mode and gs.sec_scene is not None:
                    from engine import sector_render

                    proj = sector_render.project_object(
                        bot['x'], bot['y'], 56, _RH, _RY)
                    if proj is None:
                        continue
                    sx2, sh2, bot_flr, occ2 = proj
                else:
                    sx2 = int((0.5 * (ang2 / (FOV / 2)) + 0.5) * const.W)
                    sh2 = int((TILE * 1000) / (dst2 + 0.1))
                    bot_flr = _RY + _RH // 2 + sh2 // 4
                    occ2 = dst2
                sh2 = int(min(sh2, _RH * 2))
                bot_flr = int(bot_flr)
                idx2 = depth_col(z_b, sx2, const.W)
                if idx2 >= 0 and z_b[idx2] > occ2:
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
                    surf = bot_surface(pcol, sh2, sh2, frame, state, bot.get('pa', 0), bot.get('wpn', 1), view=bot_view) if bot_surface else _blank_sprite(sh2)
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
            if gs.sector_mode and gs.sec_scene is not None:
                from engine import sector_render

                proj = sector_render.project_object(
                    ox, oy, 56, _RH, _RY)
                if proj is None:
                    continue
                sx2, sh2, plr_flr, occ2 = proj
            else:
                sx2 = int((0.5 * (ang2 / (FOV / 2)) + 0.5) * const.W)
                sh2 = int((TILE * 750) / (dst2 + 0.1))
                plr_flr = _RY + _RH // 2 + sh2 // 4
                occ2 = dst2
            # project_object() returns sh/ground_y as floats (world_h * FOCAL /
            # perp), but pygame.draw.line rejects a float width, and every width
            # below is derived from these -- so snap once, here.
            sh2 = int(min(sh2, _RH * 2))
            plr_flr = int(plr_flr)
            idx2 = depth_col(z_b, sx2, const.W)
            if idx2 >= 0 and z_b[idx2] > occ2:
                skin = (230, 190, 150)
                shirt = pl.get('col', (50, 130, 200))
                # state 3 = dashing: lighten the shirt so the burst of speed is
                # readable to other players (the world-state player record is
                # unchanged in size, this only paints the value it already had).
                if pl.get('state') == 3:
                    shirt = _brighter(shirt)
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

    # Floor glow when standing on F/E/L tiles. The band is a layout unit: as a
    # literal 40px it was 8.3% of a 480px window and 1.9% of a 2160px one, so
    # the cue vanished at exactly the resolutions where the window was readable.
    pt_x, pt_y = int(gs.px // TILE), int(gs.py // TILE)
    if 0 <= pt_y < gs.M_SZ and 0 <= pt_x < gs.M_SZ:
        ft = gs.MAP[pt_y][pt_x]
        glow_h = ui_px(40)
        if ft in ('F', 'E', 'L'):
            col, alpha = {'F': ((200, 150, 20), 80), 'E': ((60, 180, 220), 60), 'L': ((200, 50, 5), 60)}[ft]
            ov = pygame.Surface((const.W, glow_h))
            ov.set_alpha(alpha)
            ov.fill(col)
            gs.screen.blit(ov, (0, _RY + _RH - glow_h))
        if ft == 'L':
            glow_h2 = ui_px(80)
            ov2 = pygame.Surface((const.W, glow_h2))
            ov2.set_alpha(30)
            ov2.fill((200, 80, 10))
            gs.screen.blit(ov2, (0, _RY + _RH - glow_h2))

    # Advance the effect pools before drawing them. The spawns live in the
    # client bridge, but stepping the TTL here keeps render() the single
    # per-frame tick of the engine (it already drives w_tmr, bob and explored
    # tiles) -- without it nothing ever aged and the pools stayed full for the
    # rest of the session.
    update_particles()
    update_decals()
    update_weather()
    # Particles
    draw_particles(z_b, _RH, _RY)
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
            # Column span from the buffer's own grid: a fixed SCALE-wide band
            # per column only lands right when the buffer stride happens to be
            # SCALE, and it runs off the surface for the last columns when it
            # is not.
            fx, fw = _frame_column_span(i, nc, const.W)
            fog_surf.fill((*gs.fog_color, alpha), (fx, 0, fw, _RH))
        gs.screen.blit(fog_surf, (0, _RY))

    # Speed lines for the local player's own dash. Placed after the world and
    # the fog but before the HUD block, so the crosshair, the face, the stats
    # and the weapon stay readable on top of them.
    draw_dash_streaks()

    # HUD
    dx, dy = 0.0, 0.0
    k = pygame.key.get_pressed()
    if k[pygame.K_w] or k[pygame.K_s] or k[pygame.K_a] or k[pygame.K_d]:
        dx, dy = 1.0, 1.0
    bob = int(math.sin(curr_t * 0.015) * 15 if abs(dx) + abs(dy) > 0 else 0)
    # Fire comes from the server-confirmed weapon timer (gs.fire_flag) or a
    # held LMB. Space is deliberately absent: it is the dash key now, and the
    # renderer must not read the key map as a source of game state.
    fire = getattr(gs, '_replay_fire', False) if getattr(gs, 'replay_mode', 'off') == 'playback' else bool(getattr(gs, 'fire_flag', False) or pygame.mouse.get_pressed()[0])
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
        gs.screen.blit(msg, (const.W // 2 - msg.get_width() // 2, _RY + _RH // 2 - ui_px(80)))

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
        ms = ui_px(5)
        mm_ox, mm_oy = ui_px(10), ui_px(10)
        mm_r = max(1, ui_px(3))
        draw_sz = min(gs.M_SZ, explore_sz)
        pygame.draw.rect(gs.screen, (0, 0, 0), (mm_ox, mm_oy, draw_sz * ms, draw_sz * ms))
        for y in range(draw_sz):
            for x in range(draw_sz):
                if not gs.explored[y][x]:
                    continue
                tile = gs.MAP[y][x]
                if tile == 'S':
                    continue
                if tile == '.':
                    pygame.draw.rect(gs.screen, (25, 25, 30), (mm_ox + x * ms, mm_oy + y * ms, ms, ms))
                elif tile == '#':
                    pygame.draw.rect(gs.screen, (100, 100, 100), (mm_ox + x * ms, mm_oy + y * ms, ms, ms))
                elif tile in ('F', 'E'):
                    c = (60, 180, 220) if tile == 'E' else (200, 150, 0)
                    pygame.draw.rect(gs.screen, c, (mm_ox + x * ms, mm_oy + y * ms, ms, ms))
                elif tile in ('R', 'Y', 'B', 'T', 'W', 'D', 'M', 'I', 'P', 'H', 'V', 'C', 'Q', 'L', 'K'):
                    c = {'R': (180, 40, 40), 'Y': (180, 180, 40), 'B': (40, 40, 180),
                         'T': (130, 130, 140), 'W': (120, 80, 40),
                         'D': (160, 120, 60),
                         'M': (140, 30, 30), 'I': (90, 110, 85), 'P': (170, 80, 50),
                         'H': (100, 80, 60), 'V': (100, 200, 100), 'C': (140, 100, 60),
                         'Q': (60, 180, 200), 'L': (200, 80, 0), 'K': (200, 50, 200)}.get(tile, (100, 100, 100))
                    pygame.draw.rect(gs.screen, c, (mm_ox + x * ms, mm_oy + y * ms, ms, ms))
        pygame.draw.circle(gs.screen, (0, 255, 0), (mm_ox + gs.px / TILE * ms, mm_oy + gs.py / TILE * ms), mm_r)
        for bot in gs.bots:
            bc = bot.get('col', (255, 40, 40))
            pygame.draw.circle(gs.screen, bc, (mm_ox + bot['x'] / TILE * ms, mm_oy + bot['y'] / TILE * ms), mm_r)
        kx = mm_ox + gs.M_SZ * ms + ui_px(5)
        ky = mm_oy
        mm_pitch = ui_px(15)
        if gs.p['keys']['R'] > 0:
            gs.screen.blit(gs.sm_font.render(f"R:{gs.p['keys']['R']}", True, (255, 50, 50)), (kx, ky)); ky += mm_pitch
        if gs.p['keys']['Y'] > 0:
            gs.screen.blit(gs.sm_font.render(f"Y:{gs.p['keys']['Y']}", True, (255, 255, 50)), (kx, ky)); ky += mm_pitch
        if gs.p['keys']['B'] > 0:
            gs.screen.blit(gs.sm_font.render(f"B:{gs.p['keys']['B']}", True, (50, 50, 255)), (kx, ky)); ky += mm_pitch
        if gs.secrets_total > 0:
            gs.screen.blit(gs.sm_font.render(f"SEC: {gs.secrets_found}/{gs.secrets_total}", True, (100, 200, 100)), (kx, ky))

    # Frame-rate readout, top edge of the screen.  The engine has no clock,
    # so gs.fps is whatever the client measured for the frame being drawn.
    # Hidden while it is 0 (unmeasured) rather than claiming "0 FPS".
    if gs.fps > 0.0:
        _fps = gs.sm_font.render(f"{gs.fps:.0f} FPS", True, (200, 200, 200))
        gs.screen.blit(_fps, (const.W - ui_px(20) - _fps.get_width(), ui_px(64)))

    if gs.wave_msg_timer > 0:
        gs.wave_msg_timer -= 1
        alpha = min(255, gs.wave_msg_timer * 8)
        msg = gs.font.render(f"WAVE {gs.rounds}", True, (255, 255, 0))
        msg.set_alpha(alpha)
        gs.screen.blit(msg, (const.W // 2 - msg.get_width() // 2, _RY + _RH // 2 - ui_px(100)))

    if gs.show_key_msg > 0:
        gs.show_key_msg -= 1
        alpha = min(255, gs.show_key_msg * 8)
        msg = gs.font.render(gs.key_msg, True, (255, 100, 100))
        msg.set_alpha(alpha)
        gs.screen.blit(msg, (const.W // 2 - msg.get_width() // 2, _RY + _RH // 2 + 50))

    # Replay indicator
    if gs.replay_mode == 'record':
        gs.screen.blit(gs.sm_font.render("REC", True, (255, 50, 50)), (ui_px(10), _RY + _RH - ui_px(20)))
    elif gs.replay_mode == 'playback':
        gs.screen.blit(gs.sm_font.render(f"PLAY ({gs.replay_index}/{len(gs.replay_data)})", True, (50, 255, 50)), (ui_px(10), _RY + _RH - ui_px(20)))

    # Armor display
    arm = gs.p.get('armor')
    arm_str = ""
    if arm:
        arm_color = (50, 200, 50) if arm['type'] == 'green' else (50, 50, 200)
        arm_str = f" ARM:{arm['value']}%"
        gs.screen.blit(gs.sm_font.render(f"ARMOR {arm['value']}", True, arm_color), (ui_px(410), _RY + _RH - ui_px(20)))

    # Deathmatch frag display
    if gs.lvl_type == 'deathmatch':
        f1 = gs.dm_frags.get('p1', 0)
        f2 = gs.dm_frags.get('p2', 0)
        bot_total = sum(b.get('frags', 0) for b in gs.bots)
        # Clears the minimap by the same scaled metrics the minimap used, not by
        # the literal 5px tiles it was written against.
        dm_x = ui_px(20 + (gs.M_SZ * ui_px(5) / ui_scale() + 10 if gs.show_minimap else 0))
        dm_pitch = ui_px(14)
        if gs.dm_team_mode:
            rs, bs = gs.dm_team_scores['red'], gs.dm_team_scores['blue']
            gs.screen.blit(gs.sm_font.render(f"TEAMS  RED:{rs}  BLUE:{bs}", True, (255, 200, 0)), (dm_x, ui_px(10)))
            yy = ui_px(24)
            for b in gs.bots:
                bc = b.get('col', (200, 40, 40))
                team_c = 'R' if b.get('team') == 'red' else 'B'
                gs.screen.blit(gs.sm_font.render(f"{team_c}{b.get('id',0)} {b.get('type','?')[:3].upper()}  F:{b.get('frags',0)}  HP:{b['hp']}", True, bc), (dm_x, yy))
                yy += dm_pitch
        else:
            gs.screen.blit(gs.sm_font.render(f"P1:{f1}  P2:{f2}  BOTS:{bot_total}", True, (255, 200, 0)), (dm_x, ui_px(10)))
            yy = ui_px(24)
            for b in gs.bots:
                bc = b.get('col', (200, 40, 40))
                gs.screen.blit(gs.sm_font.render(f"B{b.get('id',0)} {b.get('type','?')[:3].upper()}  F:{b.get('frags',0)}  HP:{b['hp']}  WPN:{b.get('wpn',1)}", True, bc), (dm_x, yy))
                yy += dm_pitch
        if gs.dm_timer > 0:
            mins, secs = divmod(gs.dm_timer // 60, 60)
            timer_col = (255, 100, 100) if gs.dm_timer < 180 * 60 else (200, 200, 200)
            _tmr2 = gs.font.render(f"{mins}:{secs:02d}", True, timer_col)
            gs.screen.blit(_tmr2, (const.W // 2 - _tmr2.get_width() // 2, ui_px(10)))
        # Game over screen
        if gs.dm_game_over:
            overlay = pygame.Surface((const.W, const.H), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 180))
            gs.screen.blit(overlay, (0, 0))
            win_text = f"{gs.dm_winner} WINS!" if gs.dm_winner and gs.dm_winner != 'DRAW' else "DRAW!"
            gs.screen.blit(gs.font.render(win_text, True, (255, 255, 0)), (const.W // 2 - 80, const.H // 2 - 20))
            gs.screen.blit(gs.sm_font.render("Press ENTER to restart", True, (150, 150, 150)), (const.W // 2 - 80, const.H // 2 + 20))

    # ── HUD ──
    # Every size below is a layout unit of the reference window (1000x700),
    # converted to pixels by ui_px().  Authoring them as literal pixels is what
    # made the HUD 20.8% of a 480px window and 4.6% of a 2160px one.
    hud_bh = ui_px(100)
    hud_by = _RY + _RH - hud_bh
    pygame.draw.rect(gs.screen, (15, 15, 20), (0, hud_by, const.W, hud_bh))
    pygame.draw.rect(gs.screen, (100, 100, 100), (0, hud_by, const.W, hud_bh), 1)

    hp = gs.p['hp']
    max_hp = gs.start_hp
    arm = gs.p.get('armor')
    arm_val = arm['value'] if arm else 0
    arm_type = arm['type'] if arm else None

    # ── Square face, big and expressive ──
    face_sz = ui_px(52)
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
    cy = face_y - ui_px(1)
    feat_off = fd * ui_px(3)   # features shift to show turning

    # Head — rounded rect, no frame (centered)
    head_r = ui_px(6)
    head_w, head_h = ui_px(38), ui_px(38)
    pygame.draw.rect(gs.screen, skin,
                     (cx - head_w // 2, cy - head_h // 2, head_w, head_h),
                     border_radius=head_r)
    brow_w = ui_px(4)
    lw_thin = ui_px(1)
    lw_mid = ui_px(2)
    lw_thick = ui_px(3)

    # ── Eyebrows ──
    brow_col = (50, 35, 25) if phase != 'dead' else (100, 100, 100)
    fex = cx + feat_off  # shifted features X
    if phase in ('critical', 'hurt'):
        pygame.draw.line(gs.screen, brow_col, (fex - ui_px(14), cy - ui_px(16)), (fex - ui_px(5), cy - ui_px(11)), brow_w)
        pygame.draw.line(gs.screen, brow_col, (fex + ui_px(14), cy - ui_px(16)), (fex + ui_px(5), cy - ui_px(11)), brow_w)
    elif phase == 'worried':
        pygame.draw.line(gs.screen, brow_col, (fex - ui_px(13), cy - ui_px(14)), (fex - ui_px(4), cy - ui_px(14)), brow_w)
        pygame.draw.line(gs.screen, brow_col, (fex + ui_px(13), cy - ui_px(14)), (fex + ui_px(4), cy - ui_px(14)), brow_w)
    elif phase == 'relaxed':
        pygame.draw.line(gs.screen, brow_col, (fex - ui_px(13), cy - ui_px(15)), (fex - ui_px(4), cy - ui_px(15)), brow_w)
        pygame.draw.line(gs.screen, brow_col, (fex + ui_px(13), cy - ui_px(15)), (fex + ui_px(4), cy - ui_px(15)), brow_w)
    elif phase == 'calm':
        pygame.draw.line(gs.screen, brow_col, (fex - ui_px(13), cy - ui_px(15)), (fex - ui_px(4), cy - ui_px(14)), brow_w)
        pygame.draw.line(gs.screen, brow_col, (fex + ui_px(13), cy - ui_px(15)), (fex + ui_px(4), cy - ui_px(14)), brow_w)
    elif phase == 'happy':
        pygame.draw.line(gs.screen, brow_col, (fex - ui_px(13), cy - ui_px(16)), (fex - ui_px(4), cy - ui_px(15)), brow_w)
        pygame.draw.line(gs.screen, brow_col, (fex + ui_px(13), cy - ui_px(16)), (fex + ui_px(4), cy - ui_px(15)), brow_w)
    elif phase == 'ecstatic':
        pygame.draw.line(gs.screen, brow_col, (fex - ui_px(13), cy - ui_px(18)), (fex - ui_px(4), cy - ui_px(16)), brow_w)
        pygame.draw.line(gs.screen, brow_col, (fex + ui_px(13), cy - ui_px(18)), (fex + ui_px(4), cy - ui_px(16)), brow_w)

    # ── Eyes ──
    e_y = cy - ui_px(4)
    # Pupil offset: when fd != 0, pupils shift to edge for side-glance
    pupil_off = fd * ui_px(2)
    ew, eh = ui_px(9), ui_px(5)
    pw, ph = ui_px(4), ui_px(3)
    px0 = fex - ui_px(7) + pupil_off
    px1 = fex + ui_px(6) + pupil_off
    if not blink and phase != 'dead':
        if phase == 'ecstatic':
            # Wide open eyes
            pygame.draw.rect(gs.screen, (255, 255, 255), (fex - ui_px(10), e_y - ui_px(3), ew, ui_px(7)))
            pygame.draw.rect(gs.screen, (255, 255, 255), (fex + ui_px(3), e_y - ui_px(3), ew, ui_px(7)))
            pygame.draw.rect(gs.screen, (30, 15, 5), (px0, e_y - ui_px(1), pw, ph))
            pygame.draw.rect(gs.screen, (30, 15, 5), (px1, e_y - ui_px(1), pw, ph))
            pygame.draw.circle(gs.screen, (255, 255, 200), (px0 - ui_px(1), e_y - ui_px(3)), ui_px(2))
            pygame.draw.circle(gs.screen, (255, 255, 200), (px1 - ui_px(1), e_y - ui_px(3)), ui_px(2))
        elif phase == 'happy':
            pygame.draw.rect(gs.screen, (255, 255, 255), (fex - ui_px(10), e_y + ui_px(1), ew, ui_px(3)))
            pygame.draw.rect(gs.screen, (255, 255, 255), (fex + ui_px(3), e_y + ui_px(1), ew, ui_px(3)))
            pygame.draw.rect(gs.screen, (30, 15, 5), (px0, e_y + ui_px(1), pw, ui_px(2)))
            pygame.draw.rect(gs.screen, (30, 15, 5), (px1, e_y + ui_px(1), pw, ui_px(2)))
        else:
            pygame.draw.rect(gs.screen, (255, 255, 255), (fex - ui_px(10), e_y - ui_px(2), ew, eh))
            pygame.draw.rect(gs.screen, (255, 255, 255), (fex + ui_px(3), e_y - ui_px(2), ew, eh))
            pygame.draw.rect(gs.screen, (30, 15, 5), (px0, e_y - ui_px(1), pw, ph))
            pygame.draw.rect(gs.screen, (30, 15, 5), (px1, e_y - ui_px(1), pw, ph))
        if phase in ('critical', 'hurt'):
            pygame.draw.line(gs.screen, (200, 0, 0), (fex - ui_px(10), e_y - ui_px(4)), (fex + ui_px(2), e_y - ui_px(4)), lw_thin)
            pygame.draw.line(gs.screen, (200, 0, 0), (fex + ui_px(3), e_y - ui_px(4)), (fex + ui_px(14), e_y - ui_px(4)), lw_thin)
            if phase == 'critical':
                pygame.draw.line(gs.screen, (200, 0, 0), (fex - ui_px(12), e_y + ui_px(4)), (fex + ui_px(12), e_y + ui_px(4)), lw_thin)
    elif phase == 'dead':
        pygame.draw.line(gs.screen, (150, 0, 0), (fex - ui_px(9), e_y - ui_px(3)), (fex - ui_px(1), e_y + ui_px(3)), lw_mid)
        pygame.draw.line(gs.screen, (150, 0, 0), (fex - ui_px(1), e_y - ui_px(3)), (fex - ui_px(9), e_y + ui_px(3)), lw_mid)
        pygame.draw.line(gs.screen, (150, 0, 0), (fex + ui_px(3), e_y - ui_px(3)), (fex + ui_px(11), e_y + ui_px(3)), lw_mid)
        pygame.draw.line(gs.screen, (150, 0, 0), (fex + ui_px(11), e_y - ui_px(3)), (fex + ui_px(3), e_y + ui_px(3)), lw_mid)
    else:
        pygame.draw.line(gs.screen, brow_col, (fex - ui_px(8), e_y), (fex + ui_px(1), e_y), lw_mid)
        pygame.draw.line(gs.screen, brow_col, (fex + ui_px(3), e_y), (fex + ui_px(12), e_y), lw_mid)

    # ── Nose ──
    if phase != 'dead':
        pygame.draw.line(gs.screen, brow_col, (fex, cy - ui_px(2)), (fex, cy + ui_px(4)), lw_mid)
        pygame.draw.line(gs.screen, brow_col, (fex, cy + ui_px(4)), (fex + ui_px(3), cy + ui_px(2)), lw_mid)

    # ── Mouth ──
    m_y = cy + ui_px(10)
    if phase == 'dead':
        pygame.draw.line(gs.screen, (100, 100, 100), (fex - ui_px(8), m_y + ui_px(2)), (fex + ui_px(8), m_y + ui_px(2)), lw_mid)
    elif phase == 'ecstatic':
        pygame.draw.arc(gs.screen, (220, 220, 220), (fex - ui_px(11), m_y - ui_px(7), ui_px(22), ui_px(14)), math.pi + 0.05, 2 * math.pi - 0.05, lw_thick)
        pygame.draw.arc(gs.screen, (255, 255, 255), (fex - ui_px(9), m_y - ui_px(5), ui_px(18), ui_px(10)), math.pi + 0.05, 2 * math.pi - 0.05, lw_mid)
    elif phase == 'happy':
        pygame.draw.arc(gs.screen, (200, 200, 200), (fex - ui_px(9), m_y - ui_px(5), ui_px(18), ui_px(12)), math.pi + 0.1, 2 * math.pi - 0.1, lw_thick)
        pygame.draw.arc(gs.screen, (255, 255, 255), (fex - ui_px(7), m_y - ui_px(3), ui_px(14), ui_px(8)), math.pi + 0.1, 2 * math.pi - 0.1, lw_mid)
    elif phase == 'relaxed':
        pygame.draw.line(gs.screen, (180, 180, 180), (fex - ui_px(6), m_y + ui_px(1)), (fex + ui_px(6), m_y + ui_px(1)), lw_mid)
        pygame.draw.line(gs.screen, (200, 200, 200), (fex + ui_px(6), m_y + ui_px(1)), (fex + ui_px(8), m_y + ui_px(2)), lw_mid)
    elif phase == 'calm':
        pygame.draw.line(gs.screen, (180, 180, 180), (fex - ui_px(7), m_y + ui_px(1)), (fex + ui_px(1), m_y), lw_mid)
        pygame.draw.line(gs.screen, (200, 200, 200), (fex + ui_px(1), m_y), (fex + ui_px(7), m_y + ui_px(1)), lw_mid)
    elif phase == 'worried':
        pygame.draw.line(gs.screen, (160, 160, 160), (fex - ui_px(7), m_y + ui_px(2)), (fex + ui_px(7), m_y + ui_px(2)), lw_thick)
    elif phase == 'hurt':
        pygame.draw.arc(gs.screen, (180, 40, 40), (fex - ui_px(8), m_y - ui_px(2), ui_px(16), ui_px(10)), math.pi + 0.15, 2 * math.pi - 0.15, lw_thick)
        pygame.draw.line(gs.screen, (180, 0, 0), (fex - ui_px(4), m_y + ui_px(7)), (fex - ui_px(4), m_y + ui_px(11)), lw_mid)
        pygame.draw.line(gs.screen, (180, 0, 0), (fex + ui_px(4), m_y + ui_px(7)), (fex + ui_px(4), m_y + ui_px(10)), lw_mid)
    elif phase == 'critical':
        pygame.draw.arc(gs.screen, (180, 20, 20), (fex - ui_px(9), m_y - ui_px(2), ui_px(18), ui_px(12)), math.pi + 0.2, 2 * math.pi - 0.2, lw_thick)
        pygame.draw.line(gs.screen, (180, 0, 0), (fex - ui_px(5), m_y + ui_px(8)), (fex - ui_px(5), m_y + ui_px(13)), lw_mid)
        pygame.draw.line(gs.screen, (180, 0, 0), (fex + ui_px(5), m_y + ui_px(8)), (fex + ui_px(5), m_y + ui_px(12)), lw_mid)
        pygame.draw.line(gs.screen, (180, 0, 0), (fex - ui_px(1), m_y + ui_px(9)), (fex - ui_px(1), m_y + ui_px(12)), lw_mid)

    # ── Left: HP + thin bar + stats ──
    lx = ui_px(12)
    hp_col = (0, 220, 0) if hp > 50 else ((220, 220, 0) if hp > 25 else (220, 0, 0))
    hp_str = f"{hp}/{max_hp}"
    gs.screen.blit(gs.font.render(hp_str, True, hp_col), (lx, hud_by + ui_px(4)))
    # Stats (compact)
    stats_y = hud_by + ui_px(14)
    pitch_stat = ui_px(12)
    gs.screen.blit(gs.sm_font.render(f"K:{gs.kills}", True, (180, 120, 120)), (lx, stats_y))
    gs.screen.blit(gs.sm_font.render(f"I:{gs.items_picked}", True, (180, 180, 120)), (lx, stats_y + pitch_stat))
    gs.screen.blit(gs.sm_font.render(f"S:{gs.secrets_found}/{gs.secrets_total}", True, (160, 160, 160)), (lx, stats_y + pitch_stat * 2))
    # Accuracy
    if gs.shots_fired > 0:
        acc = gs.shots_hit * 100 // gs.shots_fired
        acc_col = (100, 200, 100) if acc > 50 else ((200, 200, 100) if acc > 25 else (200, 100, 100))
        gs.screen.blit(gs.sm_font.render(f"A:{acc}%", True, acc_col), (lx, stats_y + pitch_stat * 3))

    # ── Right: Armor + weapon + ammo + kills count ──
    rx = const.W - ui_px(120)

    # Armor value with type icon
    if arm_val > 0:
        arm_label = f"ARM {arm_val}"
        arm_col2 = (50, 220, 50) if arm_type == 'green' else ((50, 80, 220) if arm_type == 'blue' else (100, 100, 100))
        gs.screen.blit(gs.font.render(arm_label, True, arm_col2), (rx, hud_by + ui_px(4)))

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

    wpn_y = hud_by + ui_px(28)
    gs.screen.blit(gs.sm_font.render(wpn_name, True, (210, 210, 210)), (rx + ui_px(2), wpn_y))
    if isinstance(amm, int):
        amc = {'pstl': (180, 180, 100), 'shl': (200, 120, 50), 'slg': (100, 200, 100), 'rck': (0, 200, 0), 'BFG': (0, 200, 50)}.get(amm_key if gs.p['wpn'] < 100 else 'pstl', (200, 200, 200))
        amm_str = f"{amm}"
        gs.screen.blit(gs.font.render(amm_str, True, amc), (rx, wpn_y + ui_px(16)))
        # Thin ammo bar
        amm_max = {'pstl': 200, 'shl': 50, 'slg': 50, 'rck': 10, 'BFG': 10}.get(amm_key if gs.p['wpn'] < 100 else 'pstl', 100)
        if amm_max > 0:
            amm_frac = min(1.0, amm / amm_max)
            abw, abh = ui_px(90), ui_px(4)
            pygame.draw.rect(gs.screen, (25, 25, 25), (rx, wpn_y + ui_px(38), abw, abh))
            pygame.draw.rect(gs.screen, amc, (rx, wpn_y + ui_px(38), int(abw * amm_frac), abh))
    # Remaining enemies
    ene_left = len(gs.enemies)
    if ene_left > 0:
        ene_col = (200, 100, 100) if ene_left > 5 else ((200, 200, 100) if ene_left > 1 else (200, 50, 50))
        gs.screen.blit(gs.sm_font.render(f"ENE:{ene_left}", True, ene_col), (rx, wpn_y + ui_px(48)))

    # ── Weapon slots (top area between panels) ──
    owned = sorted(gs.p['guns'])
    slot_w = ui_px(30)
    slots_start = max(lx + ui_px(80), face_rect.left - len(owned) * slot_w - ui_px(8))
    slot_y = hud_by + ui_px(8)
    for i, w in enumerate(owned):
        wx = slots_start + i * slot_w
        wcol = (255, 255, 255) if w == gs.p['wpn'] else (120, 120, 120)
        lbl = str(w + 1) if w < 6 else f"C{w-99}"
        if w < 6:
            pygame.draw.rect(gs.screen, WPN[w]['c'], (wx + ui_px(2), slot_y + ui_px(1), ui_px(6), ui_px(5)))
        lw = gs.sm_font.size(lbl)[0]
        gs.screen.blit(gs.sm_font.render(lbl, True, wcol), (wx + (slot_w - lw) // 2, slot_y + ui_px(8)))
        if w == gs.p['wpn']:
            pygame.draw.rect(gs.screen, (255, 255, 255), (wx, slot_y, slot_w - 1, ui_px(22)), 1)

    # ── Under face: mode / dmg / powerup / keys ──
    bot_y = hud_by + hud_bh - ui_px(16)
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
        # Centred on the face by measuring the label: a blind offset drifts as
        # soon as the string changes length ("ENDLESS" vs "EXIT").
        lbl = gs.sm_font.render(mode_str, True, (150, 150, 150))
        gs.screen.blit(lbl, (face_rect.centerx - lbl.get_width() // 2, bot_y))
    # Damage dealt/taken on the bottom line
    lbl = gs.sm_font.render(f"DMG:{gs.damage_dealt}d/{gs.damage_taken}t", True, (140, 140, 140))
    gs.screen.blit(lbl, (face_rect.centerx - lbl.get_width() // 2, bot_y + ui_px(10)))
    pu_str = f"{gs.p['powerup']}" if gs.p['powerup'] else ""
    if pu_str:
        lbl = gs.sm_font.render(pu_str, True, (255, 255, 0))
        gs.screen.blit(lbl, (face_rect.centerx - lbl.get_width() // 2, hud_by + ui_px(2)))
    kx = face_rect.right + ui_px(8)
    key_pitch = ui_px(28)
    for kc, kv in sorted(gs.p.get('keys', {}).items()):
        if kv > 0:
            kcol = {'R': (255, 50, 50), 'Y': (255, 255, 50), 'B': (50, 50, 255)}.get(kc, (200, 200, 200))
            gs.screen.blit(gs.sm_font.render(f"{kc}:{kv}", True, kcol), (kx, bot_y))
            kx += key_pitch

    # Cheat input display
    if gs.cheat_open:
        cheat_h = ui_px(42)
        pygame.draw.rect(gs.screen, (0, 0, 0), (0, _RY + _RH - cheat_h, const.W, cheat_h))
        pygame.draw.rect(gs.screen, (100, 100, 100), (0, _RY + _RH - cheat_h, const.W, cheat_h), 1)
        txt = f"CHEAT: {gs.cheat_buf}█"
        gs.screen.blit(gs.font.render(txt, True, (255, 255, 0)), (ui_px(30), _RY + _RH - ui_px(35)))

    # Crosshair — centred on the screen, sized in layout units so it stays
    # visible in 4K instead of shrinking to a 20px cross on 8.3 Mpx.
    if gs.show_crosshair:
        ch_x, ch_y = const.W // 2, const.H // 2
        ch_col = (255, 255, 255)
        ch_arm = ui_px(10)
        ch_gap = ui_px(3)
        ch_w = ui_px(2)
        pygame.draw.line(gs.screen, ch_col, (ch_x - ch_arm, ch_y), (ch_x - ch_gap, ch_y), ch_w)
        pygame.draw.line(gs.screen, ch_col, (ch_x + ch_gap, ch_y), (ch_x + ch_arm, ch_y), ch_w)
        pygame.draw.line(gs.screen, ch_col, (ch_x, ch_y - ch_arm), (ch_x, ch_y - ch_gap), ch_w)
        pygame.draw.line(gs.screen, ch_col, (ch_x, ch_y + ch_gap), (ch_x, ch_y + ch_arm), ch_w)

    # Advisory "you need a key" banner: the client arms it only for a short
    # spell after a "use" press (the server keeps the last refusal in the
    # snapshot), so the engine just paints the flag it is handed.
    if gs.p.get('use_hint'):
        lbl = gs.sm_font.render("НУЖЕН КЛЮЧ", True, (255, 220, 60))
        gs.screen.blit(lbl, (const.W // 2 - lbl.get_width() // 2,
                             const.H // 2 + ui_px(40)))
