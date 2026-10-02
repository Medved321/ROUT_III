"""Smoke: viewport-relative screen space (change `viewport-scaling`).

The bug it exists for: nothing drawn *over* the world was expressed as a share
of the window.  The HUD bar was 100 literal pixels -- 20.8 % of a 640x480 window
and 4.6 % of a 3840x2160 one -- the crosshair was a fixed 20px, the weapon
sprites had a literal 3.0x baked into each PNG (the rifle was taller than a
700px window), and particles carried their own focal, their own eye height and
their own idea of which distance the depth buffer holds, which put them 350px
below the floor.

What is covered:
  1. ui_scale() / ui_px() agree with the window at every preset, and a layout
     unit is a *share*, not a pixel count;
  2. the HUD bar and the crosshair keep that share and stay on screen;
  3. every first-person weapon lands on one normalised height, and no gun eats
     the screen at any size;
  4. project_point() puts an effect on the floor line the sprite pass uses, and
     the depth test compares perpendicular distance;
  5. the near plane clamps size without rewriting the distance;
  6. the fonts follow the window (the client bridge no longer freezes one size).

The measurements are of *fractions*, deliberately: comparing pixel counts
between two window sizes is exactly the mistake this change is about.
"""
import math
import os
import sys

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
os.environ.setdefault('SDL_AUDIODRIVER', 'dummy')

import pygame  # noqa: E402

pygame.init()
pygame.display.set_mode((16, 16))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine import constants as const  # noqa: E402
from engine import game_state as gs  # noqa: E402
from engine import rendering as R  # noqa: E402
from engine import viewport as V  # noqa: E402
from engine.constants import TILE, CAM_Z, WPN  # noqa: E402
from engine.framebuffer import depth_col  # noqa: E402

PRESETS = ((640, 480), (800, 600), (1000, 700), (1600, 900), (1920, 1080),
           (2560, 1440), (3840, 2160))

OPEN_MAP = [[1] * 12] + [[1] + [0] * 10 + [1] for _ in range(10)] + [[1] * 12]
BASE_W, BASE_H, BASE_FOV = const.W, const.H, const.FOV


def _scene(w, h, fov=None):
    const.W, const.H = w, h
    if fov is not None:
        const.FOV = fov
    gs.screen = pygame.Surface((w, h))
    gs.screen.fill((18, 20, 28))
    gs.particles.clear()
    gs.decals.clear()
    gs.weather.clear()
    gs.weather_type = 'none'
    gs.MAP = OPEN_MAP
    gs.M_SZ = len(OPEN_MAP)
    gs.sector_mode = False
    gs.sec_scene = None
    gs.px, gs.py, gs.pz = 320.0, 320.0, 0.0
    gs.pa, gs.pitch = 0.0, 0.0
    gs.view_dist = 900.0
    gs.effects_factor = 1.0
    gs.blood_amount = 1.0
    gs.dash_fx = 0.0
    gs.start_hp = 100
    gs.p = {'hp': 100, 'armor': None, 'wpn': 1, 'powerup': '', 'guns': [1],
            'keys': {'R': 0, 'Y': 0, 'B': 0}, 'pstl': 50}
    gs.face_dir = 0
    gs.show_crosshair = True
    gs.show_minimap = False
    gs.show_key_msg = 0
    gs.cheat_open = False
    gs.lvl_type = 'pve'
    gs.kills = gs.items_picked = 0
    gs.secrets_found = gs.secrets_total = 0
    gs.shots_fired = gs.shots_hit = 0
    gs.damage_dealt = gs.damage_taken = 0
    gs.speedrun_timer = 0.0
    gs.wave_msg_timer = 0
    gs.replay_mode = 'off'
    gs.dm_frags = {'p1': 0, 'p2': 0}
    gs.dm_team_mode = False
    gs.dm_timer = 0
    gs.dm_game_over = False
    gs.elevator_prompt = False
    gs.explored = [[False] * gs.M_SZ for _ in range(gs.M_SZ)]
    gs.enemies = []
    gs.bots = []
    gs.fog_enabled = False
    gs.rounds = 0
    gs.orda_wave = 0
    return gs.screen


class Recorder:
    """Stands in for gs.screen: remembers the last blit."""

    def __init__(self):
        self.last = None

    def blit(self, src, pos, *a, **k):
        self.last = src


def _drawn_weapon(idx):
    rec = Recorder()
    prev = gs.screen
    gs.screen = rec
    gs.w_tmr = 0
    try:
        R.draw_wpn(idx, 0, False, 0)
    finally:
        gs.screen = prev
    return rec.last


try:
    # ── 1. ui_scale / ui_px ─────────────────────────────────────────────────
    for w, h in PRESETS:
        const.W, const.H = w, h
        s = V.ui_scale()
        want = min(w / V.REF_W, h / V.REF_H)
        assert abs(s - want) < 1e-9, f"{w}x{h}: ui_scale {s} != {want}"
        # A layout unit is a share of the window, not a pixel count: the same
        # number of units must mean the same thing at every size.  The exact
        # value is the contract; a "share of the height" check is not, because
        # `min` over both axes deliberately makes a window-limited preset
        # (640x480 is wider than 1000x700 is tall) track the *width* instead, so
        # that the full-width HUD bar still fits across.
        prev = 0
        for units in (1, 2, 3, 5, 10, 30, 100, 216):
            px = V.ui_px(units)
            assert px >= 1, f"{w}x{h}: ui_px({units}) collapsed to {px}"
            assert px == max(1, int(round(units * s))), (
                f"{w}x{h}: ui_px({units}) = {px}, "
                f"but round({units} * {s:.4f}) = {round(units * s)}")
            assert px >= prev, f"{w}x{h}: ui_px is not monotonic at {units}"
            prev = px
    # And it really does track the window rather than snapping to a constant:
    # 4K must buy several times the pixels 640x480 does for the same layout.
    const.W, const.H = 640, 480
    small_px = V.ui_px(100)
    const.W, const.H = 3840, 2160
    big_px = V.ui_px(100)
    assert big_px > small_px * 3, (
        f"ui_px(100) only went {small_px} -> {big_px} from 480p to 4K")
    print("ui_scale/ui_px are window-relative OK")

    # ── 2. the HUD keeps its share of the window ────────────────────────────
    shares = []
    for w, h in PRESETS:
        _scene(w, h)
        R.render(h, 0)
        bar_h = V.ui_px(100)
        shares.append(bar_h / float(h))
        share = bar_h / float(h)
        assert abs(share / (100.0 / V.REF_H) - 1.0) <= 0.10, (
            f"{w}x{h}: HUD bar is {share:.2%} of the window, "
            f"reference is {100.0 / V.REF_H:.2%}")
        # The crosshair stays a visible fraction of the window at 4K ...
        arm = V.ui_px(10)
        assert arm / float(h) > 0.002, f"{w}x{h}: crosshair vanished ({arm}px)"
        # ... and is still drawn at the centre of the screen. The centre pixel
        # itself is inside the crosshair's gap by design, so look at the box.
        cx, cy, found = const.W // 2, const.H // 2, False
        for dy in range(-arm - 1, arm + 2):
            for dx in range(-arm - 1, arm + 2):
                if gs.screen.get_at((cx + dx, cy + dy))[:3] == (255, 255, 255):
                    found = True
                    break
            if found:
                break
        assert found, f"{w}x{h}: no crosshair pixels near the screen centre"
    assert max(shares) - min(shares) < 0.02, f"HUD share wobbles: {shares}"
    print("HUD bar and crosshair hold their share OK")

    # ── 3. weapons: one normalised height, no gun eats the screen ───────────
    for w, h in PRESETS:
        _scene(w, h)
        rows = []
        for idx in (0, 1, 2, 3, 5):
            wn = WPN[idx]['n']
            surf = _drawn_weapon(idx)
            assert surf is not None, f"{wn}@{w}x{h} drew nothing"
            assert surf.get_height() == V.ui_px(R.WPN_TARGET_H[wn]), (
                f"{wn}@{w}x{h}: drawn {surf.get_height()}px, "
                f"wanted {V.ui_px(R.WPN_TARGET_H[wn])}px")
            rows.append((wn, surf.get_height() / float(h)))
        worst = max(r[1] for r in rows)
        best = min(r[1] for r in rows)
        assert worst < 0.5, f"{w}x{h}: a gun covers {worst:.0%} of the window"
        assert worst / best < 1.5, f"{w}x{h}: gun sizes diverge: {rows}"
    print("weapon viewmodel normalised OK")

    # ── 4. project_point lands on the sprite pass's floor line ──────────────
    _scene(1000, 700)
    gs.px, gs.py, gs.pa = 320.0, 320.0, 0.0
    # Dead ahead is the case the formula was derived for, and it must be exact:
    # the sprite pass and the effect pass have to agree where they overlap.
    for z in (1.5, 3.0, 8.0):
        proj = V.project_point(gs.px + z * TILE, gs.py, TILE)
        assert proj is not None, f"dead-ahead effect at z={z} was culled"
        sx, sh, ground_y, perp = proj
        # rendering.py's sprite floor line, from the same inputs.
        spr_sh = TILE * 750.0 / perp
        spr_flr = const.H // 2 + int(CAM_Z * spr_sh / TILE) - spr_sh // 4
        assert abs(ground_y - spr_flr) <= 1.0, (
            f"z={z}: effect floor {ground_y:.1f} vs sprite floor {spr_flr:.1f}")
    print("project_point agrees with the sprite floor line OK")

    # Off-axis: perpendicular distance, not radial, so the depth test can match.
    # A wide FOV keeps the 45-degree sample inside the visible screen, so the
    # assertion is about the distance, not about being off-frame.
    const.FOV = math.radians(100.0)
    proj = V.project_point(320.0 + 160.0, 320.0 + 160.0, TILE)
    assert proj is not None
    sx, sh, ground_y, perp = proj
    radial = math.hypot(160.0, 160.0)
    assert 0 <= sx < const.W, f"sample must be on screen, got column {sx}"
    assert abs(perp - radial * math.cos(math.pi / 4)) < 1e-6, (
        "perp must be the perpendicular distance the depth buffer stores")
    assert perp < radial, "perp must be smaller than the radial distance"
    # With a z_b filled with the perpendicular distance, the effect passes its
    # own depth test. This is the bug that made off-axis effects vanish behind
    # walls that do not actually cover them.
    z_b = [perp + 1.0] * (const.W // const.SCALE)
    idx = depth_col(z_b, sx, const.W)
    assert idx >= 0 and z_b[idx] > perp, "off-axis effect failed its own depth test"
    print("off-axis depth test uses perpendicular distance OK")

    # ── 5. near plane: clamp the size, keep the distance ────────────────────
    near = V.project_point(gs.px + 1.0, gs.py, TILE)
    assert near is not None
    assert near[1] <= TILE * 750.0 / const.EFFECT_NEAR_DIST + 1e-6, (
        f"effect at the camera projects to {near[1]:.0f}px tall")
    assert near[3] < const.EFFECT_NEAR_DIST + 1.0, (
        "the near plane must clamp the size, not rewrite the distance")
    print("near plane clamps size, not distance OK")

    # ── 6. the fonts follow the window ──────────────────────────────────────
    const.W, const.H = 640, 480
    V.ensure_hud_fonts()
    small = gs.font.get_height()
    const.W, const.H = 3840, 2160
    V.ensure_hud_fonts()
    big = gs.font.get_height()
    assert big > small * 2, f"HUD font did not scale: {small}px -> {big}px"
    print("HUD fonts follow the window OK")

    # ── 7. nothing is re-fit or re-allocated in a steady window ─────────────
    # The weapon blit and the font are on the per-frame path, so a resize must
    # be the only thing that costs a transform. (The world's own nearest-
    # neighbour upscale legitimately transforms every frame, so the count is
    # taken around the weapon path alone.)
    _scene(1000, 700)
    for idx in (0, 1, 2, 3, 5):
        _drawn_weapon(idx)
    V.ensure_hud_fonts()
    warm = (len(R._wpn_fitted), len(V._font_cache), gs.font)
    scales = []
    real_scale = pygame.transform.scale

    def counting_scale(src, size, *a):
        scales.append(size)
        return real_scale(src, size, *a)

    pygame.transform.scale = counting_scale
    try:
        for _ in range(20):
            for idx in (0, 1, 2, 3, 5):
                _drawn_weapon(idx)
    finally:
        pygame.transform.scale = real_scale
    assert not scales, f"{len(scales)} transforms in a steady window"
    assert (len(R._wpn_fitted), len(V._font_cache)) == warm[:2], (
        f"caches grew while the window was still: {warm[:2]} -> "
        f"{(len(R._wpn_fitted), len(V._font_cache))}")
    # ... and the font object itself is reused, not rebuilt per frame.
    assert gs.font is warm[2], "ensure_hud_fonts rebuilt the font with no resize"
    print("no per-frame transform in a steady window OK")

    # ── 8. a resize sweep must not grow the caches without bound ────────────
    for w, h in PRESETS + tuple(reversed(PRESETS)):
        const.W, const.H = w, h
        for idx in (0, 1, 2, 3, 5):
            _drawn_weapon(idx)
        V.ensure_hud_fonts()
        assert len(R._wpn_fitted) <= R._WPN_FITTED_MAX, (
            f"weapon fit cache overflowed at {w}x{h}: {len(R._wpn_fitted)}")
        assert len(V._font_cache) <= 1, (
            f"font cache kept {len(V._font_cache)} entries at {w}x{h}")
    print("resize sweep leaves the caches bounded OK")

    # ── 9. effects beyond the view distance are not drawn ───────────────────
    _scene(1000, 700)
    gs.view_dist = 200.0
    gs.particles.clear()
    gs.particles.append({'x': gs.px + 400.0, 'y': gs.py, 'vx': 0.0, 'vy': 0.0,
                         'life': 10, 'max_life': 10, 'col': (200, 200, 200),
                         'size': 4, 'z': 0.0, 'zv': 0.0})
    before = pygame.image.tobytes(gs.screen, 'RGB')
    R.draw_particles([10_000.0] * (const.W // const.SCALE), const.H, 0)
    assert pygame.image.tobytes(gs.screen, 'RGB') == before, (
        "a particle past gs.view_dist was drawn")
    gs.view_dist = 900.0
    R.draw_particles([10_000.0] * (const.W // const.SCALE), const.H, 0)
    assert pygame.image.tobytes(gs.screen, 'RGB') != before, (
        "the same particle inside view_dist was culled")
    print("effects respect gs.view_dist OK")

    print("VIEWPORT SCALING TEST PASSED")
finally:
    const.W, const.H, const.FOV = BASE_W, BASE_H, BASE_FOV