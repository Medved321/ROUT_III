"""smoke_resolution_controls.py — window size and the tractor control mode.

Covers the `window-size-and-tractor-controls` change:
  * `normalize_size` clamps to a usable range and forces even dimensions;
  * `Renderer.set_window_size` moves the display, `const.W/H` and `gs.screen`,
    and notifies the client through `on_resize`;
  * `Renderer.present` letterboxes the 1000x700 menu canvas into any window;
  * `RenderBridge.set_output_surface` re-subscribes the blit destination and
    re-allocates the engine output surface to the new pixel grid;
  * window size: presets cycle, a typed `Ш×В` is applied, bad input is refused
    with a reason, and a save/load roundtrip survives;
  * tractor: arrows steer (left/right/none), Space fires, Left Shift dashes on
    the key edge only, the mouse is neither read nor grabbed, and switching
    modes drops the steering ramp;
  * mouse mode keeps its old layout (LMB fire, Space dash);
  * the settings screen scrolls to the new rows and commits/rejects a custom size.
"""
import json
import os
import sys
import tempfile

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
import pygame

pygame.init()
pygame.display.set_mode((1000, 700))

import client.settings as settings_mod
from client.settings import (
    CONTROL_MODE_MOUSE, CONTROL_MODE_TRACTOR, DEFAULTS, MAX_WINDOW,
    MIN_WINDOW, SPEC, Settings, WINDOW_SIZE_PRESETS, control_hints,
    control_mode, is_tractor, size_text, validate_size,
)

tmpdir = tempfile.mkdtemp()
settings_mod.CONFIG_PATH = os.path.join(tmpdir, 'config.json')

# ── normalize_size: clamp + even dimensions ───────────────────────
from client.renderer import LETTERBOX, Renderer, normalize_size

assert normalize_size(3840, 2160) == (3840, 2160)
assert normalize_size(100, 100) == MIN_WINDOW, normalize_size(100, 100)
assert normalize_size(99999, 99999) == MAX_WINDOW
assert normalize_size(1367, 769) == (1366, 768), normalize_size(1367, 769)
assert normalize_size('nope', None) == (1000, 700), normalize_size('nope', None)
print("normalize_size clamps and rounds to even OK")

# ── set_window_size moves window + engine globals ─────────────────
from engine import constants as const
from engine import game_state as gs

seen_sizes = []
r = Renderer()
r.on_resize = lambda size: seen_sizes.append(tuple(size))

assert r.window_size == (1000, 700), r.window_size
assert r.ui.get_size() == (1000, 700), r.ui.get_size()

applied = r.set_window_size((3840, 2160))
assert applied == (3840, 2160) and r.window_size == (3840, 2160)
assert const.W == 3840 and const.H == 2160, (const.W, const.H)
assert seen_sizes == [(3840, 2160)], seen_sizes

# An odd size is rounded, and the client is told what was really used.
applied = r.set_window_size((1921, 1081))
assert applied == (1920, 1080), applied
assert r.window_size == (1920, 1080)
assert const.W == 1920 and const.H == 1080
print("set_window_size moves window/const.W/const.H/gs.screen + on_resize OK")

# ── present() letterboxes the virtual menu canvas ──────────────────
r.set_window_size((1000, 700))
r.ui.fill((10, 200, 30))
r.present()
assert r.screen.get_at((500, 350)) == (10, 200, 30), '1:1 window must be a plain blit'

r.set_window_size((1920, 1080))
r.present()
w, h = r.screen.get_size()
assert r.screen.get_at((w // 2, h // 2)) == (10, 200, 30), 'menu must be centred'
assert r.screen.get_at((0, 0)) == LETTERBOX, 'bars must be letterboxed'
assert r.screen.get_at((w - 1, 0)) == LETTERBOX, 'bars must be letterboxed'

# The frozen pause background is captured once, already at canvas size, so a
# paused frame costs a 1000x700 copy and not another 4K rescale.
frozen = r.capture_menu_frame()
assert frozen.get_size() == (1000, 700), frozen.get_size()
assert frozen.get_at((500, 350)) == (10, 200, 30), 'frozen frame must keep the world'
r.ui.fill((0, 0, 0))
r.ui.blit(frozen, (0, 0))
assert r.ui.get_at((500, 350)) == (10, 200, 30)
print("present() letterboxes the 1000x700 canvas OK")

# ── bridge re-subscribes to the new display surface ────────────────
from client.bridge import RenderBridge

r.set_window_size((1600, 900))
bridge = RenderBridge(r.screen)
target = pygame.Surface((1280, 720))
bridge.set_output_surface(target)
# The engine keeps its own output surface (rendered at const.W/H and blitted by
# ``render``); what must follow the window is the blit destination and the
# pixel grid, not the surface identity.
assert bridge._surface is target, 'bridge must follow the new display surface'
assert gs.screen is not target, 'the engine output surface is its own'
assert gs.screen.get_size() == (const.W, const.H) == (1600, 900), (
    gs.screen.get_size(), const.W, const.H)
print("RenderBridge.set_output_surface re-subscribes the display surface OK")

# ── window size setting: presets, custom text, refusal ─────────────
s = Settings()
assert s.get('window_size') == DEFAULTS['window_size']
assert s.resolution() == (1000, 700)

first = SPEC[4]
assert first['key'] == 'window_size' and first.get('custom') is True
assert '3840\u00d72160' in WINDOW_SIZE_PRESETS, '4K must be a preset'

# Arrows cycle the presets and never fall off either end. The default sits in
# the middle of the list, so one step down reaches the first preset.
s.adjust('window_size', -1)
while s.get('window_size') != WINDOW_SIZE_PRESETS[0]:
    s.adjust('window_size', -1)
assert s.get('window_size') == WINDOW_SIZE_PRESETS[0], s.get('window_size')
s.adjust('window_size', -1)
assert s.get('window_size') == WINDOW_SIZE_PRESETS[0], 'must clamp at the first preset'
s.adjust('window_size', +1)
assert s.get('window_size') == WINDOW_SIZE_PRESETS[1]
assert WINDOW_SIZE_PRESETS[0] == '640\u00d7480', WINDOW_SIZE_PRESETS[0]
print("window_size arrows cycle the presets and clamp OK")

# A typed size is honoured, with either separator.
for text in ('3440x1440', '3440\u00d71440', '3440 * 1440'):
    t = Settings()
    assert t.set_window_size(text) == (3440, 1440), (text, t.resolution_error)
    assert t.get('window_size') == '3440\u00d71440', t.get('window_size')

t = Settings()
t.set_window_size('3840\u00d72160')
assert t.resolution() == (3840, 2160)
print("typed Ш×В sizes are parsed and stored normalised OK")

# Bad input is refused with a reason, and leaves the value alone.
t = Settings()
t.set_window_size('1600\u00d7900')
for bad, why in (('abc', 'Ш×В'), ('10\u00d710', 'Минимум'),
                 ('9000\u00d79000', 'Максимум')):
    assert t.set_window_size(bad) is None, bad
    assert t.resolution_error and why in t.resolution_error, (
        bad, t.resolution_error)
    assert t.get('window_size') == '1600\u00d7900', 'refused input must not apply'
print("invalid window sizes are refused with a message OK")

# Roundtrip through config.json.
persisted = Settings()
persisted.set_window_size('3840\u00d72160')
persisted.save()
s2 = Settings()
s2.load()
assert s2.get('window_size') == '3840\u00d72160', s2.get('window_size')
assert s2.resolution() == (3840, 2160)
print("window size survives a config.json roundtrip OK")

# ── control mode plumbing ─────────────────────────────────────────
assert control_mode('Мышь') == CONTROL_MODE_MOUSE
assert control_mode('Трактор') == CONTROL_MODE_TRACTOR
assert control_mode('tractor') == CONTROL_MODE_TRACTOR
assert control_mode(1) == CONTROL_MODE_TRACTOR
assert control_mode('garbage') == CONTROL_MODE_MOUSE
assert is_tractor('Трактор') and not is_tractor('Мышь')
assert '\u2190' in control_hints('Трактор') and 'Пробел' in control_hints('Трактор')
assert 'ЛКМ' in control_hints('Мышь')
print("control_mode parsing + per-mode hints OK")

# ── input handler: tractor mode ───────────────────────────────────
from client.input_handler import InputHandler


class _Keys:
    """Stand-in for pygame's ScancodeWrapper."""

    def __init__(self, *pressed):
        self._pressed = set(pressed)

    def __getitem__(self, key):
        return 1 if key in self._pressed else 0


_held: list = [_Keys()]
_real_get_pressed = pygame.key.get_pressed
pygame.key.get_pressed = lambda: _held[0]
_real_get_rel = pygame.mouse.get_rel
_real_get_pressed_btn = pygame.mouse.get_pressed
_mouse_moves: list = [(0, 0)]
pygame.mouse.get_rel = lambda: _mouse_moves.pop() if _mouse_moves else (0, 0)
pygame.mouse.get_pressed = lambda: (0, 0, 0)
_real_set_grab = pygame.event.set_grab
_grabs: list = []
pygame.event.set_grab = lambda on: _grabs.append(on)

try:
    h = InputHandler()
    h.control_mode = CONTROL_MODE_TRACTOR
    assert h.tractor
    assert _grabs and _grabs[-1] is False, 'tractor must not grab the mouse'
    assert h._grabbed is False

    # Aiming: the mouse must not move the camera.
    _mouse_moves.append((400, 0))
    _held[0] = _Keys()
    h.update()
    assert h.rotate_delta == 0.0, h.rotate_delta

    # Steering is per-direction and zero when no key is held. Each case starts
    # from a neutral ramp: the handler deliberately coasts to a stop after a
    # release, so a shared ramp would leak the previous key into this one.
    def steer(*keys):
        h.reset_turn()
        _held[0] = _Keys(*keys)
        h._last_tick = pygame.time.get_ticks() - 16  # deterministic 16 ms frame
        h.update()
        return h.rotate_delta

    right = steer(pygame.K_RIGHT)
    left = steer(pygame.K_LEFT)
    none = steer()
    assert right > 0.0 and left < 0.0 and none == 0.0, (right, left, none)
    assert not h.shoot and not h.dash_pressed, 'steering must not fire or dash'

    # Releasing the key coasts the steering down instead of snapping it.
    _held[0] = _Keys(pygame.K_RIGHT)
    h.reset_turn()
    for _ in range(40):
        h._last_tick = pygame.time.get_ticks() - 16
        h.update()
    held_turn = h._turn
    assert held_turn > 1.0, held_turn
    _held[0] = _Keys()
    coasts = []
    for _ in range(40):
        h._last_tick = pygame.time.get_ticks() - 16
        h.update()
        coasts.append(abs(h.rotate_delta))
    assert coasts[0] < held_turn * 0.1, (coasts[0], held_turn)
    assert h._turn < held_turn * 0.02, (h._turn, held_turn)
    assert all(coasts[i] >= coasts[i + 1] for i in range(len(coasts) - 1)), \
        'the steering ramp must decay monotonically'

    # Frame-rate independence: one simulated second of holding the key must
    # turn the camera by the same angle at 30 and at 240 FPS. Frames are fed
    # by back-dating ``_last_tick``, so no sleeping and no real-time flake.
    def turn_for_one_second(dt_ms: int) -> float:
        h.reset_turn()
        _held[0] = _Keys(pygame.K_RIGHT)
        total = 0.0
        for _ in range(1000 // dt_ms):
            h._last_tick = pygame.time.get_ticks() - dt_ms
            h.update()
            total += h.rotate_delta
        return total

    at_30 = turn_for_one_second(33)
    at_240 = turn_for_one_second(4)
    # The residual gap is the discrete ramp (min(1, accel*dt) per frame), not
    # the rate: 5% still separates "frame-rate independent" from the 8x spread
    # a constant per-frame rate would produce.
    assert abs(at_30 - at_240) / at_30 < 0.05, (at_30, at_240)
    # ~2.4 rad/s for a second, once the ramp has settled.
    assert 2.0 < at_30 < 2.5, at_30
    print("tractor: arrows steer, mouse ignored, turn is frame-rate independent OK")

    # Space fires in tractor mode, and holding it does not dash.
    pygame.event.clear()
    _held[0] = _Keys(pygame.K_SPACE)
    h.update()
    assert h.shoot is True and not h.shoot_pressed, 'held fire must not re-trigger'
    assert not h.dash_pressed, 'Space must not dash in tractor mode'
    # Left Shift dashes on the key edge only. The dash is an event edge (not a
    # held-key state) on purpose: holding Shift must not re-trigger it, and the
    # client owes the server exactly one dash per press.
    pygame.event.clear()
    _held[0] = _Keys(pygame.K_LSHIFT)
    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_LSHIFT, mod=0,
                                         unicode='', scancode=0))
    h.update()
    assert h.dash_pressed and h.dash, 'Left Shift must dash in tractor mode'
    h.update()  # key still held, no new event
    assert not h.dash_pressed, 'a held dash key must not re-trigger'

    # Right Shift is the same dash (one arm of the trigger, not a second one).
    pygame.event.clear()
    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RSHIFT, mod=0,
                                         unicode='', scancode=0))
    h.update()
    assert h.dash_pressed, 'Right Shift must dash in tractor mode'

    # Space fires on the edge too, and never dashes in this mode.
    pygame.event.clear()
    _held[0] = _Keys(pygame.K_SPACE)
    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE, mod=0,
                                         unicode=' ', scancode=0))
    h.update()
    assert h.shoot_pressed and h.shoot, 'Space must fire in tractor mode'
    assert not h.dash_pressed, 'Space must not dash in tractor mode'
    h.update()
    assert h.shoot and not h.shoot_pressed, 'held fire must be continuous, not edged'
    print("tractor: Space fires, Left Shift dashes once per press OK")

    # Switching modes drops the ramp and hands the mouse back in mouse mode.
    _held[0] = _Keys(pygame.K_RIGHT)
    h._last_tick = pygame.time.get_ticks() - 16
    h.update()
    assert h._turn != 0.0
    h.control_mode = CONTROL_MODE_MOUSE
    assert not h.tractor and h._turn == 0.0 and h._last_tick is None

    # Mouse mode keeps the original layout.
    _mouse_moves.append((100, 0))
    _held[0] = _Keys()
    h.update()
    assert h.rotate_delta == 0.0, 'an ungrabbed mouse must not steer'

    pygame.event.clear()
    pygame.event.post(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(0, 0)))
    h.update()
    assert h.shoot and h.shoot_pressed, 'LMB must fire in mouse mode'
    assert not h.dash_pressed

    pygame.event.clear()
    pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE, mod=0,
                                         unicode=' ', scancode=0))
    h.update()
    assert h.dash_pressed, 'Space must dash in mouse mode'
    assert not h.shoot, 'Space must not fire in mouse mode'

    # Arrows stay movement aliases under the mouse (never camera). Steering is
    # read only while the pointer is grabbed, so grab it first.
    h.lock_mouse()
    assert h._grabbed and _grabs[-1] is True
    _mouse_moves.append((120, 0))
    _held[0] = _Keys(pygame.K_UP)
    h.update()
    assert h.move_x == 1, h.move_x
    assert h.rotate_delta == 120 * h.mouse_sensitivity, h.rotate_delta
    assert h.tractor is False
    print("mouse mode unchanged (LMB fire, Space dash, arrows = W/S) OK")

    # Settings push the mode and the turn rates into the handler.
    cfg = Settings()
    cfg.load()
    cfg.set('control_mode', 'Трактор')
    cfg.set('turn_speed', 3.0)
    cfg.set('turn_accel', 12.0)
    cfg.apply_runtime(h)
    assert h.control_mode == CONTROL_MODE_TRACTOR
    assert h.turn_speed == 3.0 and h.turn_accel == 12.0
finally:
    pygame.key.get_pressed = _real_get_pressed
    pygame.mouse.get_rel = _real_get_rel
    pygame.mouse.get_pressed = _real_get_pressed_btn
    pygame.event.set_grab = _real_set_grab
print("apply_runtime pushes control mode + turn rates OK")

# ── settings screen: scrolling and the custom size field ───────────
from client.settings_screen import SettingsScreen

scr = SettingsScreen(1000, 700)
scr.draw(r.ui, s)
assert len(SPEC) > scr._visible_rows, 'the option list no longer fits the canvas'
scr._select(len(SPEC) - 1)
scr._ensure_visible()
assert scr._scroll > 0, 'the option list must scroll once it outgrows the canvas'
scr.draw(r.ui, s)
scr._select(4)
scr._ensure_visible()
assert scr._row_rect(4).bottom <= scr._rows_rect.bottom, 'selection must be visible'

typed = Settings()
typed.set('window_size', '1600\u00d7900')
scr.handle([], typed)
scr._open_editor(typed)
assert scr._editing and scr._text == '1600\u00d7900', scr._text


def key_event(key, unicode=''):
    return [pygame.event.Event(pygame.KEYDOWN, key=key, mod=0, unicode=unicode,
                               scancode=0)]


def backspaces(count):
    for _ in range(count):
        scr.handle(key_event(pygame.K_BACKSPACE), typed)


def type_text(text):
    for ch in text:
        scr.handle(key_event(ord(ch), ch), typed)


# Backspace edits the buffer; the committed value is unchanged until Enter.
backspaces(len('1600\u00d7900'))
assert scr._text == '', repr(scr._text)
assert typed.get('window_size') == '1600\u00d7900', 'editing must not apply yet'

# "abc" cannot be parsed: the field reports why and the value survives.
type_text('abc')
scr.handle(key_event(pygame.K_RETURN, '\r'), typed)
assert not scr._editing
assert typed.resolution_error, 'an unparsable size must report an error'
assert typed.get('window_size') == '1600\u00d7900', 'rejected input must not apply'
scr.draw(r.ui, typed)

# A valid typed size is applied and normalised to 'Ш×В'. Reopening seeds the
# buffer with the current value, so clear it the way a player would.
scr._open_editor(typed)
assert scr._text == '1600\u00d7900', scr._text
backspaces(len('1600\u00d7900'))
type_text('3840x2160')  # 'x' instead of '×': the parser accepts both
assert scr._text == '3840x2160', scr._text
scr.handle(key_event(pygame.K_RETURN, '\r'), typed)
assert not scr._editing
assert typed.resolution_error == '', typed.resolution_error
assert typed.get('window_size') == '3840\u00d72160', typed.get('window_size')
assert typed.resolution() == (3840, 2160)
scr.draw(r.ui, typed)

# Escape abandons an edit instead of committing it.
scr._open_editor(typed)
backspaces(len('3840\u00d72160'))
type_text('800x600')
assert scr._text == '800x600', scr._text
scr.handle(key_event(pygame.K_ESCAPE, ''), typed)
assert not scr._editing
assert typed.get('window_size') == '3840\u00d72160', 'Esc must not commit'
print("settings screen scrolls + accepts/rejects a custom size OK")

print("ALL RESOLUTION/CONTROLS SMOKE TESTS PASSED")
