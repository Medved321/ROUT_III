"""Smoke: weapon viewmodel animation (renderer/weapon-view).

Three groups of checks:

* regression -- idle differs from fire, recoil frames are distinct, a weapon
  switch changes the rendered gun;
* timing -- the animation lasts exactly the server cooldown (``wpn_cooldown_
  ticks``) in wall-clock seconds, and the 30/60/144 fps cases agree. The real
  ``GameClient`` methods run; only the clock and the input handler are
  injected, so the shipped arithmetic is what gets asserted;
* layout -- frame thresholds are fractions of the weapon tempo ``f``, not
  absolute frame numbers, and an oversized ``w_tmr`` clamps instead of
  wrapping.
"""
import os

os.environ['SDL_VIDEODRIVER'] = 'dummy'
os.environ['SDL_AUDIODRIVER'] = 'dummy'
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pygame  # noqa: E402

pygame.init()
pygame.display.set_mode((1000, 700))

from client.bridge import RenderBridge  # noqa: E402
import client.client as cc  # noqa: E402
import engine.game_state as gs  # noqa: E402
import engine.rendering as er  # noqa: E402
from engine.constants import WPN  # noqa: E402
from shared.combat import wpn_cooldown_ticks, TICK_INTERVAL, FPS_60  # noqa: E402

AMMO = {'pstl': 10, 'shl': 10, 'slg': 10, 'rck': 10, 'BFG': 10}

# ── regression: the animation is visible and the frames differ ──────────────
MAP = [[1] * 16] + [[1] + [0] * 14 + [1] for _ in range(14)] + [[1] * 16]
surf = pygame.Surface((1000, 700))
b = RenderBridge(surf)
b.set_map(MAP, 16)


def frame(w_tmr, wpn, fire):
    b.update(500.0, 500.0, 0.0, 200, [], [], [], [], wpn, AMMO,
             w_tmr, 'pve')
    gs.fire_flag = fire
    gs.w_tmr = w_tmr
    b.render()
    return pygame.image.tostring(gs.screen, 'RGB')


idle = frame(0, 1, False)
fire = frame(16, 1, True)
assert idle != fire, "pistol fire frame identical to idle -> no animation"
print("pistol fire animation changes frame OK")

f1 = frame(50, 2, True)
f2 = frame(30, 2, False)
f3 = frame(10, 2, False)
assert len({f1, f2, f3}) == 3, "shotgun animation frames not distinct"
print("shotgun recoil animation frames distinct OK")

assert frame(0, 1, False) != frame(0, 3, False), \
    "weapon switch does not change rendered gun"
print("weapon switch rendering OK")


# ── timing helpers: drive the real client methods with a fake clock ─────────
class FakeInput:
    def __init__(self, weapon_id, shoot=False, pressed=False):
        self.weapon_id = weapon_id
        self.shoot = shoot
        self.shoot_pressed = pressed
        self.rotate_delta = 0.0

    def update(self):
        pass


class FakeNet:
    def __init__(self):
        self.fired = []

    def send_fire(self, wpn):
        self.fired.append(wpn)


def make_client(weapon_id=0, shoot=False, pressed=False, anim_until=0.0,
                anim_wpn=-1, gate=0.0):
    """A GameClient with only the input/clock surface replaced.

    ``__init__`` is skipped on purpose (it opens sockets and a window); every
    method used below is the real one.
    """
    c = object.__new__(cc.GameClient)
    c._fire_anim_until = anim_until
    c._fire_anim_wpn = anim_wpn
    c._next_fire_time = gate
    c._fire_intent_pending = False
    c._fire_undecided = 0
    c._server_fire_seq = 0
    c._server_fire_ready = 0
    c.local_weapon = weapon_id
    c.input_handler = FakeInput(weapon_id, shoot, pressed)
    c.network = FakeNet()
    c.local_angle = 0.0
    c.local_ammo = dict(AMMO)
    return c


def frames_at(c, elapsed, cooldown):
    """`gs.w_tmr` the client would report `elapsed` seconds into the animation.

    ``_fire_anim_frames`` only reads the stored deadline, so replaying it over
    a synthetic `now` is the same code path the frame loop takes -- without
    sleeping through 3 frame rates.
    """
    return c._fire_anim_frames(c._fire_anim_until - cooldown + elapsed)


# ── timing: length == server cooldown, identical at every frame rate ────────
for idx, name in ((0, 'CHAINSAW'), (1, 'PISTOL'), (2, 'SHOTGUN'),
                  (5, 'SSG')):
    info = WPN[idx]
    ticks = wpn_cooldown_ticks(int(info['f']))
    cooldown = ticks * TICK_INTERVAL

    c = make_client(weapon_id=idx, shoot=True, pressed=True)
    t0 = time.monotonic()
    c._process_input()
    cooldown_wall = cooldown

    # the animation deadline is the cooldown, without the gate's +1 tick
    assert c.network.fired == [idx], f"{name}: shot not sent"
    assert abs(c._fire_anim_until - (t0 + cooldown_wall)) < 0.02, (
        name, c._fire_anim_until - t0, cooldown_wall)
    assert abs(c._next_fire_time - c._fire_anim_until - TICK_INTERVAL) < 1e-9, (
        name, "gate slack must be exactly one tick and belong to the gate only")

    # starts at the weapon's full 60 fps frame budget
    assert frames_at(c, 0.0, cooldown_wall) == ticks * 3, (
        name, frames_at(c, 0.0, cooldown_wall), ticks * 3)
    # still alive one frame before the cooldown is over, over on it
    assert frames_at(c, cooldown_wall - 1.0 / FPS_60, cooldown_wall) >= 1, (
        name, "animation ended before the cooldown did")
    assert frames_at(c, cooldown_wall, cooldown_wall) == 0, name

    # walking the animation at a fixed step (= emulating a frame rate) has to
    # consume it in `cooldown` seconds at 30, 60 and 144 fps alike. The frame is
    # the quantum of the 60 fps convention, so the deadline is allowed to be
    # hit within one frame -- but never a whole frame of drift.
    for fps in (30.0, 60.0, 144.0):
        step = 1.0 / fps
        t = 0.0
        while frames_at(c, t, cooldown_wall) > 0:
            t += step
            assert t < 5 * cooldown_wall, f"{name}@{fps:g}fps: animation never ends"
        assert cooldown_wall - 1.0 / FPS_60 <= t <= cooldown_wall + step, (
            f"{name}@{fps:g}fps: ended at {t:.4f}s, cooldown {cooldown_wall:.4f}s")
print("animation duration == cooldown at 30/60/144 fps OK")


# ── timing: the +1 tick gate slack never lengthens the animation ────────────
idx = 1  # PISTOL
c = make_client(weapon_id=idx, shoot=True, pressed=True)
c._process_input()
t0 = time.monotonic()
gate_free = c._next_fire_time - t0
anim_left = c._fire_anim_frames(t0)
assert gate_free > anim_left / FPS_60, (
    "the gate must stay open later than the animation finishes")
print("gate slack is not folded into the animation OK")


# ── layout / ownership: a weapon switch drops the foreign animation ─────────
far = time.monotonic() + 10.0
c = make_client(weapon_id=0, anim_until=far, anim_wpn=5)
c._process_input()
assert c._fire_anim_frames(time.monotonic()) == 0, \
    "chainsaw inherited the SSG recoil"
assert c._fire_anim_wpn == -1
print("weapon switch drops foreign animation OK")

# ... and the same weapon keeps its own animation
c = make_client(weapon_id=5, anim_until=far, anim_wpn=5)
c._process_input()
assert c._fire_anim_frames(time.monotonic()) > 0, \
    "weapon switch killed its own animation"
assert c._fire_anim_wpn == 5
print("same weapon keeps its animation OK")


# ── renderer: thresholds are fractions of the tempo ────────────────────────
class Recorder:
    """Stands in for gs.screen: records what draw_wpn would blit."""
    def __init__(self):
        self.last = None

    def blit(self, src, pos, *a, **k):
        self.last = src


def pick(idx, tmr):
    rec = Recorder()
    gs.screen = rec
    gs.w_tmr = tmr
    er.draw_wpn(idx, tmr, tmr > 0, 0)
    return rec.last


def fitted(key, img):
    """The surface draw_wpn is expected to blit for this frame.

    draw_wpn no longer blits the module-level frame surfaces directly: every
    frame is normalised to one height per weapon for the current window (change
    `viewport-scaling`), so the identity to assert is the *fitted* copy of the
    frame the thresholds select. Comparing against _fit_wpn() keeps this test
    about frame selection, which is what it is for.  The weapon name is the key's
    first field -- WPN_TARGET_H is keyed by name, not by weapon id.
    """
    return er._fit_wpn(key, img, er.ui_px(er.WPN_TARGET_H[key[0]]))


f = WPN[2]['f']  # SHOTGUN
assert pick(2, f) is fitted(('SHOTGUN', 'fire'), er._SHOTGUN_FIRE)
assert pick(2, int(f * 0.9)) is fitted(('SHOTGUN', 'fire'), er._SHOTGUN_FIRE)
assert pick(2, int(f * 0.6)) is fitted(('SHOTGUN', 'fire'), er._SHOTGUN_FIRE)
assert pick(2, int(f * 0.4)) is fitted(('SHOTGUN', 'recoil'), er._SHOTGUN_RECOIL)
assert pick(2, 1) is fitted(('SHOTGUN', 'recoil'), er._SHOTGUN_RECOIL)
assert pick(2, 0) is fitted(('SHOTGUN', 'idle'), er._SHOTGUN_IDLE)
print("shotgun thresholds follow the tempo OK")

g = WPN[5]['f']  # SSG: eight buckets over the cooldown
phases = [pick(5, min(int(g * k / 8) + 1, g)) for k in range(7, -1, -1)]
assert len(phases) == 8 and len({id(x) for x in phases}) == 8, (
    f"SSG animation collapsed to {len({id(x) for x in phases})} distinct frames")
# the same eighths boundary holds at a doubled tempo: same fractions, twice
# the frames
g2 = g * 2
phases2 = [pick(5, int(g2 * k / 8) + 1) for k in range(7, 0, -1)]
assert phases2[0] is phases[0] and phases2[-1] is phases[5], (
    "SSG phases must not move when the tempo changes")
assert pick(5, 0) is fitted(('SSG', 1), er._get_ssg(1))
print("SSG thresholds follow the tempo OK")

# tmr beyond the weapon's own tempo clamps: the phase stays in [0, 1] and the
# recoil offset never leaves its half-wave
assert pick(2, 10 * f) is fitted(('SHOTGUN', 'fire'), er._SHOTGUN_FIRE)
for tmr in (f, 5 * f, 40 * f):
    p = min(max(tmr / f, 0.0), 1.0)
    assert 0.0 <= p <= 1.0
print("oversized tmr clamps OK")

# ── normalisation: one height per weapon, at every window size ────────────────
# The loaders used to bake a literal 3.0x into each PNG, so the guns spanned
# 17%..119% of the window height and were only correct at the size they were
# tuned at.  What must hold now is a *share* of the window, not a pixel count.
from engine import constants as _c  # noqa: E402

BASE_W, BASE_H = _c.W, _c.H
try:
    for w, h in ((640, 480), (1000, 700), (1920, 1080), (3840, 2160)):
        _c.W, _c.H = w, h
        shares = {}
        for idx in (0, 1, 2, 3, 5):
            wn = WPN[idx]['n']
            surf = pick(idx, 0)
            target = er.ui_px(er.WPN_TARGET_H[wn])
            assert surf.get_height() == target, (
                f"{wn}@{w}x{h}: drawn {surf.get_height()}px, wanted {target}px")
            shares[wn] = surf.get_height() / float(h)
        lo, hi = min(shares.values()), max(shares.values())
        # Every gun occupies a comparable share of the window; before this change
        # the rifle alone was taller than the whole window at 480p.
        assert hi / lo < 1.5, f"{w}x{h}: gun sizes diverge too far: {shares}"
        # ... and none of them eats the screen.
        assert hi < 0.5, f"{w}x{h}: a gun covers {hi:.0%} of the window"
    print("weapon viewmodel normalised to the window OK")
finally:
    _c.W, _c.H = BASE_W, BASE_H

print("WEAPON ANIMATION TEST PASSED")
