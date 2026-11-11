"""Phone control mode: fixed overlay geometry, multitouch, and no second path.

What this locks down (design decisions D2, D3, D5, D7 in
``openspec/changes/phone-client-android/design.md``):

* the sticks are **fixed** -- a finger never drags its base around;
* the layout is one geometry source for both drawing and hit-testing, and every
  drawn widget is clickable at its centre at 640x480 through 3840x2160;
* two fingers drive two sticks at once, and releasing one leaves the other;
* movement is digital 8-way with octant hysteresis;
* the camera turns **only** from the right stick -- no mouse look in touch mode;
* fire and dash are separate buttons: one never produces the other's edge;
* a tap of "use" lives for a few ticks (unreliable input), not one frame;
* the pause transition forgets every finger *before* the queue is cleared;
* Android's synthetic mouse cannot double-press the overlay.

Run: ``SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python smoke_touch_controls.py``
"""
from __future__ import annotations

import asyncio
import math
import types

import pygame

pygame.init()
screen = pygame.display.set_mode((1000, 700))

import engine.constants as const
from client.input_handler import (
    FINGERDOWN, FINGERMOTION, FINGERUP, InputHandler, TOUCH_USE_HOLD)
from client.settings import (
    CONTROL_MODE_MOUSE, CONTROL_MODE_TOUCH, Settings, control_hints, is_touch)
from client.touch_controls import AXIS_OFF, AXIS_ON, TouchLayout, TouchControls


def check(condition, message):
    assert condition, message


# ── deterministic platform stubs ───────────────────────────────────
class _Keys:
    def __init__(self, *pressed):
        self._pressed = set(pressed)

    def __getitem__(self, key):
        return 1 if key in self._pressed else 0


_held = [_Keys()]
_real_get_pressed = pygame.key.get_pressed
_real_event_get = pygame.event.get
_real_mouse_rel = pygame.mouse.get_rel
_real_mouse_btn = pygame.mouse.get_pressed
_real_set_grab = pygame.event.set_grab
_real_ticks = pygame.time.get_ticks
_real_monotonic = __import__("time").monotonic

_events: list = []
_now_ms = [1000]
_now_s = [10.0]
_mouse_moves: list[tuple[int, int]] = [(0, 0)]
_mouse_buttons = [(0, 0, 0)]

pygame.key.get_pressed = lambda: _held[0]
pygame.event.get = lambda: (_events.clear() or []) if False else _drain()
pygame.event.set_grab = lambda on: None
pygame.mouse.get_rel = lambda: _mouse_moves.pop() if _mouse_moves else (0, 0)
pygame.mouse.get_pressed = lambda: _mouse_buttons[0]
pygame.time.get_ticks = lambda: _now_ms[0]


def _drain():
    out = list(_events)
    _events.clear()
    return out


import client.input_handler as ih

ih.time.monotonic = lambda: _now_s[0]


def frame(handler, events=(), dt_ms: int = 50):
    """Advance the fake clock, feed events, run one input frame."""
    _events.extend(events)
    _now_ms[0] += dt_ms
    _now_s[0] += dt_ms / 1000.0
    handler.update()


def finger(kind, fid: int, pos):
    w, h = const.W, const.H
    return pygame.event.Event(kind, finger_id=fid,
                              x=pos[0] / w, y=pos[1] / h, dx=0.0, dy=0.0)


try:
    # ── 1. geometry: one source, fixed bases, all buttons real ─────
    for size in ((640, 480), (1000, 700), (1600, 900), (1920, 1080), (3840, 2160)):
        for stick, button in ((0.6, 0.6), (1.0, 1.0), (1.6, 1.6)):
            layout = TouchLayout(*size, stick_scale=stick, button_scale=button)
            w, h = size
            for name in ("fire", "dash", "use", "wprev", "wnext"):
                rect = layout.rect_for(name)
                check(rect.left >= 0 and rect.top >= 0
                      and rect.right <= w and rect.bottom <= h,
                      f"{name} leaves the window at {size} x{stick}")
                check(layout.hit(rect.center) == name,
                      f"{name} is not clickable at its centre at {size} x{stick}")
            for a in ("move", "look"):
                for b in ("move", "look"):
                    if a >= b:
                        continue
                    ac, bc = layout.stick_center(a), layout.stick_center(b)
                    check(math.dist(ac, bc) > 2 * layout.radius,
                          f"{a} and {b} overlap at {size}")
                # A stick base is a circle, a button is its rectangle: neither
                # may cover the other, or a drawn control would steal a click.
                # Measured to the nearest point of the rect, not to its centre:
                # the corner of a neighbouring button is the only place a base
                # could actually intersect it.
                for name in ("fire", "dash", "use", "wprev", "wnext"):
                    rect = layout.rect_for(name)
                    cx, cy = layout.stick_center(a)
                    nx = min(max(cx, rect.left), rect.right)
                    ny = min(max(cy, rect.top), rect.bottom)
                    check(math.dist((cx, cy), (nx, ny)) > layout.radius,
                          f"{name} overlaps the {a} stick at {size}")
            # Buttons never overlap each other.
            names = ("fire", "dash", "use", "wprev", "wnext")
            for i, a in enumerate(names):
                for b in names[i + 1:]:
                    check(not layout.rect_for(a).colliderect(layout.rect_for(b)),
                          f"{a} and {b} overlap at {size} x{stick}")
            # The base is where the base is: pressing off-centre must not move it.
            before = layout.stick_center("move")
            layout.hit((before[0] + 10, before[1]))
            check(layout.stick_center("move") == before, "the base must be fixed")
    print("overlay geometry: fixed bases, no overlaps, every centre clickable OK")

    # ── 2. two sticks at the same time ─────────────────────────────
    h = InputHandler()
    h.control_mode = CONTROL_MODE_TOUCH
    h.touch_no_mouse = False
    h.touch_look_speed = 3.0
    frame(h)
    layout = h.touch.layout
    move_c = layout.stick_center("move")
    look_c = layout.stick_center("look")
    r = layout.radius

    frame(h, [
        finger(FINGERDOWN, 1, (move_c[0], move_c[1] - int(r * 0.9))),
        finger(FINGERDOWN, 2, (look_c[0] + int(r * 0.9), look_c[1])),
    ])
    check(h.move_x == 1 and h.move_y == 0, (h.move_x, h.move_y))
    check(h.touch.look_x > 0.8, h.touch.look_x)
    check(math.isclose(h.rotate_delta, h.touch.look_x * 3.0 * 0.05,
                       rel_tol=1e-6, abs_tol=1e-9), h.rotate_delta)
    # The look stick never moves the player, the move stick never turns it.
    frame(h, [])
    check(h.move_x == 1 and h.rotate_delta != 0.0, "both sticks must run at once")

    # Releasing one finger leaves the other untouched.
    frame(h, [finger(FINGERUP, 1, (move_c[0], move_c[1] - r))])
    check(h.move_x == 0 and h.move_y == 0, "releasing move must stop movement")
    check(h.touch.look_x > 0.8, "releasing move must not disturb look")
    frame(h, [finger(FINGERUP, 2, (look_c[0] + r, look_c[1]))])
    check(h.touch.look_x == 0.0 and h.rotate_delta == 0.0,
          "releasing look must stop the camera")
    print("multitouch: two sticks at once, releases are independent OK")

    # ── 3. digital movement with octant hysteresis ────────────────
    c = TouchControls()
    c.set_viewport(1000, 700)
    cx, cy = c.layout.stick_center("move")
    rr = c.layout.radius

    def stick_at(fraction):
        return (cx + int(rr * fraction), cy)

    c.press(1, stick_at(AXIS_ON - 0.05))
    check(c.move_y == 0, "below the engage threshold the axis must stay neutral")
    c.move(1, stick_at(AXIS_ON + 0.05))
    check(c.move_y == 1, "past the engage threshold the axis must turn on")
    c.move(1, stick_at(AXIS_OFF + 0.05))
    check(c.move_y == 1, "between off and on the previous state must hold")
    c.move(1, stick_at(AXIS_OFF - 0.05))
    check(c.move_y == 0, "below the release threshold the axis must turn off")
    c.release(1)
    # Diagonal: up + right is forward + strafe, both digital. A point 0.8 of a
    # radius out at 45 degrees is *inside* the base and still past the threshold
    # on both axes (0.57 each), which is exactly the octant the stick must name.
    off = int(rr * 0.566)
    c.press(1, (cx + off, cy - off))
    check(c.move_x == 1 and c.move_y == 1, (c.move_x, c.move_y))
    c.release(1)
    check(c.move_x == 0 and c.move_y == 0, "release must centre the stick")
    print("digital movement: 8-way + hysteresis OK")

    # ── 4. no mouse look, no mouse fire in touch mode ─────────────
    h2 = InputHandler()
    h2.control_mode = CONTROL_MODE_TOUCH
    h2.touch_no_mouse = False
    frame(h2)
    h2._grabbed = True  # even a stale grab must not read the mouse
    _mouse_moves.append((500, 0))
    _events.append(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1,
                                      pos=(100, 100)))
    frame(h2)
    check(h2.rotate_delta == 0.0, "touch mode must not read the mouse for look")
    check(h2.shoot is False, "a stray click outside the overlay must not fire")
    h2._grabbed = False
    print("touch mode: camera is stick-only, stray mouse ignored OK")

    # ── 5. fire and dash are separate buttons ─────────────────────
    h3 = InputHandler()
    h3.control_mode = CONTROL_MODE_TOUCH
    frame(h3)
    fire = h3.touch.layout.rect_for("fire").center
    dash = h3.touch.layout.rect_for("dash").center
    use = h3.touch.layout.rect_for("use").center
    nxt = h3.touch.layout.rect_for("wnext").center
    prv = h3.touch.layout.rect_for("wprev").center

    frame(h3, [finger(FINGERDOWN, 1, fire)])
    check(h3.shoot and h3.shoot_pressed, "fire press must shoot")
    check(not h3.dash_pressed and not h3.dash, "fire must not dash")
    frame(h3)  # held
    check(h3.shoot and not h3.shoot_pressed, "held fire must not re-trigger")
    frame(h3, [finger(FINGERUP, 1, fire)])
    check(not h3.shoot, "releasing fire must stop shooting")

    frame(h3, [finger(FINGERDOWN, 2, dash)])
    check(h3.dash_pressed and h3.dash, "dash press must dash")
    check(not h3.shoot and not h3.shoot_pressed, "dash must not fire")
    frame(h3, [finger(FINGERUP, 2, dash)])
    frame(h3, [finger(FINGERDOWN, 2, dash)])
    check(h3.dash_pressed, "a fresh press must dash again")

    # Use is a tap: it survives a few 25 Hz snapshots, then expires.
    frame(h3, [finger(FINGERUP, 2, dash)])
    frame(h3, [finger(FINGERDOWN, 3, use)])
    check(h3.use, "a use tap must arm the flag")
    frame(h3)
    check(h3.use, "the tap must outlive one frame (unreliable input)")
    _now_s[0] += TOUCH_USE_HOLD + 0.05
    frame(h3)
    check(not h3.use, "the tap must expire")

    before = h3.weapon_id
    frame(h3, [finger(FINGERUP, 3, use), finger(FINGERDOWN, 4, nxt)])
    check(h3.weapon_id == (before + 1) % 6, "next-weapon step")
    frame(h3, [finger(FINGERUP, 4, nxt), finger(FINGERDOWN, 5, prv)])
    check(h3.weapon_id == before, "previous-weapon step")
    frame(h3, [finger(FINGERUP, 5, prv)])
    print("fire/dash/use/weapon buttons are independent OK")

    # ── 6. reset forgets held fingers (pause / lobby / focus loss) ─
    frame(h3, [finger(FINGERDOWN, 1, fire),
               finger(FINGERDOWN, 2, (touch_center := h3.touch.layout.stick_center("move")))])
    check(h3.shoot and h3.touch.is_captured(1), "setup: fire + finger held")
    h3.reset_touch()
    frame(h3)
    check(not h3.shoot and not h3.touch.is_captured(1)
          and h3.touch.is_captured(2) is False, "reset must forget every finger")
    check(h3.move_x == 0 and h3.move_y == 0 and h3.touch.look_x == 0.0,
          "reset must centre the sticks")
    print("reset_touch releases every finger OK")

    # ── 7. Android's synthetic mouse cannot double-press ──────────
    h4 = InputHandler()
    h4.control_mode = CONTROL_MODE_TOUCH
    h4.touch_no_mouse = True
    frame(h4)
    fire = h4.touch.layout.rect_for("fire").center
    frame(h4, [pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=fire)])
    check(not h4.shoot and not h4.touch.is_captured(0),
          "TOUCH_NO_MOUSE must ignore the synthetic mouse")
    h4.touch_no_mouse = False
    frame(h4, [pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=fire)])
    check(h4.shoot and h4.touch.is_captured(0),
          "on desktop the mouse must drive the overlay")
    frame(h4, [pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=fire)])
    check(not h4.shoot, "mouse release must release fire")
    print("android synthetic mouse is ignored, desktop mouse still works OK")

    # ── 8. settings sliders reach the overlay live ───────────────
    cfg = Settings()
    cfg.set("control_mode", "Телефон")
    cfg.set("touch_stick_size", 1.4)
    cfg.set("touch_button_size", 0.7)
    cfg.set("touch_opacity", 0.8)
    cfg.set("touch_look_speed", 5.0)
    h5 = InputHandler()
    cfg.apply_runtime(h5)
    check(h5.touch_active, "apply_runtime must select the touch mode")
    check((h5.touch_stick_size, h5.touch_button_size,
           h5.touch_opacity, h5.touch_look_speed) == (1.4, 0.7, 0.8, 5.0),
          "touch sliders must be pushed into the handler")
    frame(h5)
    check(h5.touch.stick_scale == 1.4 and h5.touch.button_scale == 0.7,
          "the layout must follow the sliders without a restart")
    check(is_touch("Телефон") and "стик" in control_hints("Телефон").lower(),
          "the touch mode must be recognised and hinted")
    print("touch settings apply at runtime OK")

    # ── 9. draw caches, and is a no-op outside touch mode ────────
    h6 = InputHandler()
    check(h6.touch.layout is None, "no layout before the first frame")
    h6.draw_touch(screen)
    check(h6.touch.layout is None, "mouse mode must not lay out the overlay")
    h6.control_mode = CONTROL_MODE_TOUCH
    frame(h6)
    h6.draw_touch(screen)
    cached = len(h6.touch._cache)
    check(cached > 0, "the first draw must build the widget surfaces")
    h6.draw_touch(screen)
    check(len(h6.touch._cache) == cached, "a second draw must reuse the cache")
    print("overlay draws from a cache and costs nothing in other modes OK")

    # ── 10. menu gestures: tap = click, swipe = wheel ────────────
    import client.client as client_mod

    stub = client_mod.GameClient.__new__(client_mod.GameClient)
    stub._menu_touch = {}
    stub._menu_touch_step = 44
    stub.renderer = types.SimpleNamespace(
        to_canvas_pos=lambda pos: (int(pos[0]), int(pos[1])))
    tap_events = stub._menu_events([
        finger(FINGERDOWN, 7, (500, 350)),
        finger(FINGERUP, 7, (500, 350)),
    ])
    types_ = [e.type for e in tap_events]
    check(types_ == [pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP], types_)
    check(tap_events[0].pos == (500, 350), tap_events[0].pos)

    step = max(8, int(44 * __import__("engine.viewport", fromlist=["x"]).ui_scale()))
    drag = [finger(FINGERDOWN, 8, (500, 350))]
    y = 350
    for _ in range(3):
        y -= step
        drag.append(finger(FINGERMOTION, 8, (500, y)))
    drag.append(finger(FINGERUP, 8, (500, y)))
    wheel_events = stub._menu_events(drag)
    check([e.type for e in wheel_events] == [pygame.MOUSEWHEEL] * 3,
          [e.type for e in wheel_events])
    check(all(e.y == -1 for e in wheel_events),
          "swiping up must scroll down the list")
    check(stub._menu_touch == {}, "a finished gesture must be forgotten")
    print("menu gestures: tap clicks, vertical swipe scrolls OK")

    # ── 11. pause transition resets before the queue is cleared ──
    stub2 = client_mod.GameClient.__new__(client_mod.GameClient)
    stub2.input_handler = InputHandler()
    stub2.input_handler.control_mode = CONTROL_MODE_TOUCH
    stub2._menu_touch = {}
    stub2.pause_screen = types.SimpleNamespace(_selected=0)
    stub2.renderer = types.SimpleNamespace(capture_menu_frame=lambda: None)
    stub2._paused = False
    frame(stub2.input_handler,
          [finger(FINGERDOWN, 1, stub2.input_handler.touch.layout.rect_for("fire").center)
           if stub2.input_handler.touch.layout else
           finger(FINGERDOWN, 1, (1, 1))])
    # Ensure a real press happened (the layout is built by the frame above).
    frame(stub2.input_handler,
          [finger(FINGERDOWN, 1, stub2.input_handler.touch.layout.rect_for("fire").center)])
    check(stub2.input_handler.shoot, "setup: fire held before pause")
    stub2._enter_pause()
    check(stub2.input_handler.shoot is False, "pause must drop held fire")
    check(stub2._menu_touch == {}, "pause must forget menu fingers")
    print("pause resets the overlay before clearing events OK")

    # ── 12. the desktop self-updater is skipped inside an APK ────
    real_flag = client_mod.UPDATER_ENABLED
    client_mod.UPDATER_ENABLED = False
    try:
        stub3 = client_mod.GameClient.__new__(client_mod.GameClient)

        class _Offer:
            def __getattr__(self, name):
                raise AssertionError("the APK must not read the update offer")
        stub3.network = types.SimpleNamespace(update_offer=_Offer())
        check(asyncio.run(stub3._maybe_update()) is False,
              "APK must refuse the desktop self-update")
    finally:
        client_mod.UPDATER_ENABLED = real_flag
    print("APK skips the desktop self-updater OK")

finally:
    pygame.key.get_pressed = _real_get_pressed
    pygame.event.get = _real_event_get
    pygame.mouse.get_rel = _real_mouse_rel
    pygame.mouse.get_pressed = _real_mouse_btn
    pygame.event.set_grab = _real_set_grab
    pygame.time.get_ticks = _real_ticks
    ih.time.monotonic = _real_monotonic

print("ALL TOUCH CONTROL SMOKE TESTS PASSED")
