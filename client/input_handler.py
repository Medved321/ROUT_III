from __future__ import annotations

import time

import pygame

from client.mobile import TOUCH_NO_MOUSE
from client.settings import (
    CONTROL_MODE_MOUSE, CONTROL_MODE_TRACTOR, CONTROL_MODE_TOUCH)
from client.touch_controls import TouchControls

# Frames longer than this do not advance the keyboard turn: a paused window, a
# blocked resize drag or a debugger break must not make the camera jump.
MAX_TURN_DT = 0.1

# How long one tap of the touch "use" button keeps the flag up.  The input
# stream is unreliable UDP at 25 Hz, so a single-frame pulse would be lost with
# the first dropped packet; a press is one attempt on the server either way
# (it reacts to the rising edge and ignores a held flag).
TOUCH_USE_HOLD = 0.2

# Keys that start a fresh dash press, per control mode. Holding one must not
# re-trigger the dash -- the client owes exactly one dash packet per press.
DASH_KEYS = {
    CONTROL_MODE_MOUSE: (pygame.K_SPACE,),
    CONTROL_MODE_TRACTOR: (
        getattr(pygame, "K_LSHIFT", None),
        getattr(pygame, "K_RSHIFT", None),
    ),
    # The touch overlay reports its own dash edge; no keyboard key is a second
    # trigger (one action, one button -- the same rule the tractor follows).
    CONTROL_MODE_TOUCH: (),
}

# Fire is LMB under the mouse; the tractor trades the mouse out entirely, so
# Space is the trigger there. A tractor dash key never doubles as fire.
FIRE_KEYS = {
    CONTROL_MODE_MOUSE: (),
    CONTROL_MODE_TRACTOR: (pygame.K_SPACE,),
    CONTROL_MODE_TOUCH: (),
}

# Finger events; looked up defensively so an import never dies on a build
# without the touch constants.
FINGERDOWN = getattr(pygame, "FINGERDOWN", -1)
FINGERUP = getattr(pygame, "FINGERUP", -1)
FINGERMOTION = getattr(pygame, "FINGERMOTION", -1)
WINDOWFOCUSLOST = getattr(pygame, "WINDOWFOCUSLOST", -1)

WEAPON_COUNT = 6


class InputHandler:
    """Raw device state -> per-frame input snapshot.

    Three control modes share this handler (see ``client/settings.py``):
      * mouse   -- mouse look, LMB fire, Space dash, arrows duplicate W/S;
      * tractor -- Left/Right steer the camera (with a ramp), Space fires,
        Left Shift dashes, the mouse is not read and not grabbed;
      * touch   -- two fixed sticks and four buttons on screen (see
        ``client/touch_controls.py``); the mouse and the arrows are not read.

    The overlay lives here, not in the renderer, because this is the one place
    that already owns "device -> input snapshot" and the one place that drains
    the event queue.  Drawing reads the same ``TouchLayout`` the hit test used.
    """

    def __init__(self):
        self.move_x: int = 0
        self.move_y: int = 0
        self.rotate_delta: float = 0.0
        self.shoot: bool = False
        self.shoot_pressed: bool = False  # fresh fire intent this frame
        self.dash: bool = False          # dash key held
        self.dash_pressed: bool = False  # fresh dash key press this frame (one dash)
        self._use_key: bool = False      # "use" key (E) held: opens a door
        self.weapon_id: int = 1  # index into WPN (0=chainsaw ... 5=SSG)
        self.quit_requested: bool = False
        self.pause_requested: bool = False
        self.mouse_sensitivity: float = 0.0035
        # VIDEORESIZE events are drained by ``update`` but are none of its
        # business; the client collects them here to re-create the window. They
        # are cleared at the start of every frame, so the list never grows.
        self.window_events: list = []
        # Keyboard steering: peak rad/s, and how fast it is reached (1/s).
        self.turn_speed: float = 2.4
        self.turn_accel: float = 8.0
        self._control_mode: int = CONTROL_MODE_MOUSE
        self._turn: float = 0.0   # current steering rate (rad/s, signed)
        self._last_tick: int | None = None
        self._grabbed: bool = False

        # Touch overlay (phone mode).  Sizes are multipliers of the authored
        # geometry; ``touch_look_speed`` is the camera rate at full deflection.
        self.touch = TouchControls()
        self.touch_stick_size: float = 1.0
        self.touch_button_size: float = 1.0
        self.touch_opacity: float = 0.45
        self.touch_look_speed: float = 3.0
        # Android turns the first finger into a mouse event as well; reading
        # both would press every button twice.  On desktop this stays False so
        # the overlay is testable with the mouse (one pointer, id 0).
        self.touch_no_mouse: bool = TOUCH_NO_MOUSE
        self._touch_use_until: float = 0.0
        self._last_look_tick: int | None = None

    @property
    def control_mode(self) -> int:
        return self._control_mode

    @control_mode.setter
    def control_mode(self, value) -> None:
        """Switching modes drops steering momentum and the pointer grab.

        A stale ramp would otherwise swing the camera on the first frame of
        the new mode, and a grab left over from the mouse mode would hide the
        cursor exactly when the tractor player needs it to aim the keyboard.
        A held touch button must not survive the switch either: the finger may
        already be down when the mode changes, and its FINGERUP would otherwise
        land in a screen that never saw the press.
        """
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = CONTROL_MODE_MOUSE
        if value == self._control_mode:
            return
        self._control_mode = value
        self.reset_turn()
        self.reset_touch()
        if value != CONTROL_MODE_MOUSE:
            self.unlock_mouse()

    @property
    def tractor(self) -> bool:
        return self._control_mode == CONTROL_MODE_TRACTOR

    @property
    def touch_active(self) -> bool:
        return self._control_mode == CONTROL_MODE_TOUCH

    @property
    def use(self) -> bool:
        """"Use" is high while E is held or while a touch tap is still fresh."""
        return self._use_key or time.monotonic() < self._touch_use_until

    @use.setter
    def use(self, value: bool) -> None:
        # Kept as a settable attribute for tests and custom input providers;
        # the touch tap is time-based, so it cannot be expressed as a bool.
        self._use_key = bool(value)
        if not value:
            self._touch_use_until = 0.0

    @staticmethod
    def _down(keys, state) -> bool:
        """Any of ``keys`` currently held.

        ``state`` is pygame's ``ScancodeWrapper``. A ``None`` entry is a
        constant this SDL build does not define, so it must read as "not held"
        rather than index the wrapper.
        """
        return any(k is not None and state[k] for k in keys)

    def update(self) -> None:
        self.move_x = 0
        self.move_y = 0
        self.rotate_delta = 0.0
        self.shoot = False
        self.shoot_pressed = False
        self.dash = False
        self.dash_pressed = False
        self._use_key = False
        self.pause_requested = False
        self.window_events.clear()

        touch = self.touch_active
        tractor = self.tractor
        # Edges are per frame: drop the previous frame's before this frame's
        # events are read, so a tap is reported exactly once.
        if touch:
            self._sync_touch_geometry()
            self.touch.begin_frame()

        # Only the mouse mode steers with the mouse.  Tractor and touch modes
        # keep the cursor visible and un-grabbed, so a stray mouse move cannot
        # spin the player while they are trying to click a button.
        if not pygame.display.get_init():
            self._grabbed = False
        if self._grabbed and not tractor and not touch:
            # Re-assert the grab every frame so it survives focus changes.
            pygame.event.set_grab(True)
            pygame.mouse.set_visible(False)
            dx, _dy = pygame.mouse.get_rel()
            self.rotate_delta = dx * self.mouse_sensitivity
        elif not tractor:
            # Mouse released: drop relative motion so the camera does not
            # lurch when it is grabbed again.
            pygame.mouse.get_rel()

        keys = pygame.key.get_pressed()
        if keys[pygame.K_w]:
            self.move_x += 1
        if keys[pygame.K_s]:
            self.move_x -= 1
        if keys[pygame.K_a]:
            self.move_y -= 1
        if keys[pygame.K_d]:
            self.move_y += 1
        if not (tractor or touch):
            # Under the mouse the arrows are a convenience alias for W/S; in
            # tractor mode they belong to the camera, and in touch mode they
            # belong to nobody (the phone has no physical arrow keys).
            if keys[pygame.K_UP]:
                self.move_x += 1
            if keys[pygame.K_DOWN]:
                self.move_x -= 1

        if tractor:
            self._update_keyboard_turn(keys)

        fire_keys = FIRE_KEYS[self.control_mode]
        if self._down(fire_keys, keys):
            self.shoot = True

        dash_keys = DASH_KEYS[self.control_mode]
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.quit_requested = True
            elif event.type == pygame.VIDEORESIZE:
                self.window_events.append(event)
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                self.pause_requested = True
            elif event.type == WINDOWFOCUSLOST:
                # The FINGERUP of a held button may never arrive after the app
                # loses focus, so forget the fingers instead of walking forever.
                self.reset_touch()
            elif event.type == pygame.KEYDOWN and event.key in dash_keys:
                # Edge only: holding the key must not re-trigger the dash. The
                # server keeps a cooldown either way, but the client owes the
                # player exactly one dash per press.
                self.dash = True
                self.dash_pressed = True
            elif event.type == pygame.KEYDOWN and event.key in fire_keys:
                self.shoot = True
                self.shoot_pressed = True
            elif not (tractor or touch) and event.type == pygame.MOUSEBUTTONDOWN \
                    and event.button == 1:
                self.shoot = True
                self.shoot_pressed = True
            elif event.type == pygame.KEYDOWN and event.key in (
                pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4, pygame.K_5, pygame.K_6
            ):
                self.weapon_id = event.key - pygame.K_1
            elif event.type == pygame.MOUSEWHEEL and not touch:
                if event.y > 0:
                    self.weapon_id = (self.weapon_id + 1) % WEAPON_COUNT
                elif event.y < 0:
                    self.weapon_id = (self.weapon_id - 1) % WEAPON_COUNT
            elif touch and event.type == FINGERDOWN:
                self.touch.press(getattr(event, "finger_id", 0),
                                 self._finger_pos(event))
            elif touch and event.type == FINGERMOTION:
                self.touch.move(getattr(event, "finger_id", 0),
                                self._finger_pos(event))
            elif touch and event.type == FINGERUP:
                self.touch.release(getattr(event, "finger_id", 0))
            elif touch and not self.touch_no_mouse:
                # Desktop fallback: one pointer (the mouse) drives the overlay,
                # so the phone layout can be exercised without a touchscreen.
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    self.touch.press(0, event.pos)
                elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                    self.touch.release(0)
                elif event.type == pygame.MOUSEMOTION and self.touch.is_captured(0):
                    self.touch.move(0, event.pos)

        if touch:
            self._apply_touch()

        if not tractor and not touch and pygame.mouse.get_pressed()[0]:
            self.shoot = True

        # "Use" (E) is a plain held flag: the server reacts on the rising edge,
        # so a held key is exactly one attempt, and the bit simply rides along
        # in the existing PLAYER_INPUT flag byte.
        if keys[pygame.K_e]:
            self._use_key = True

    # ── touch overlay ───────────────────────────────────────────────
    def _sync_touch_geometry(self) -> None:
        """Push slider values and the window size into the overlay.

        ``TouchLayout`` is rebuilt only when one of the inputs changes, and it
        is the single source of rectangles for both drawing and hit-testing.
        """
        from engine import constants as const

        if (self.touch.stick_scale != self.touch_stick_size
                or self.touch.button_scale != self.touch_button_size
                or self.touch.opacity != self.touch_opacity):
            self.touch.configure(self.touch_stick_size, self.touch_button_size,
                                 self.touch_opacity)
        self.touch.set_viewport(max(1, int(const.W)), max(1, int(const.H)))

    @staticmethod
    def _finger_pos(event) -> tuple[int, int]:
        """Finger coordinates arrive normalised (0..1) -> window pixels."""
        from engine import constants as const

        return (int(event.x * const.W), int(event.y * const.H))

    def _apply_touch(self) -> None:
        """Turn the overlay state into this frame's input snapshot fields."""
        touch = self.touch
        # Digital movement replaces the keyboard's contribution when the stick
        # is deflected; with the stick centred the keyboard (if any) still works.
        if touch.move_x or touch.move_y:
            self.move_x, self.move_y = touch.move_x, touch.move_y

        now = pygame.time.get_ticks()
        dt = (now - self._last_look_tick) / 1000.0 if self._last_look_tick is not None else 0.0
        self._last_look_tick = now
        if dt > 0.0:
            dt = min(dt, MAX_TURN_DT)
            self.rotate_delta = touch.look_x * self.touch_look_speed * dt

        if touch.fire:
            self.shoot = True
        if touch.fire_pressed:
            self.shoot_pressed = True
        if touch.dash_pressed:
            self.dash = True
            self.dash_pressed = True
        if touch.use_pressed:
            self._touch_use_until = time.monotonic() + TOUCH_USE_HOLD
        if touch.weapon_step:
            self.weapon_id = (self.weapon_id + touch.weapon_step) % WEAPON_COUNT

    def draw_touch(self, surface) -> None:
        """Draw the overlay onto the window surface (after the world)."""
        if not self.touch_active or surface is None:
            return
        self._sync_touch_geometry()
        self.touch.draw(surface)

    def reset_touch(self) -> None:
        """Forget every finger: pause, end screen, lobby, focus loss.

        ``pygame.event.clear()`` in those transitions destroys the ``FINGERUP``
        that would have released a held stick or button, so the overlay has to
        be told explicitly or the player keeps walking while the menu is up.
        The per-frame snapshot fields are cleared with it: a caller that reads
        the handler between the reset and the next ``update`` must not see the
        previous frame's held fire.
        """
        self.touch.reset()
        self._touch_use_until = 0.0
        self._last_look_tick = None
        self.move_x = 0
        self.move_y = 0
        self.rotate_delta = 0.0
        self.shoot = False
        self.shoot_pressed = False
        self.dash = False
        self.dash_pressed = False

    def _update_keyboard_turn(self, keys) -> None:
        """Steer with Left/Right at a frame-rate independent rate.

        The instantaneous rate is ramped towards the target with
        ``turn_accel`` over the real elapsed time, so holding the key for one
        second turns the camera by the same angle at 30 and at 240 FPS; a
        constant rate would make the tractor's speed a function of the frame
        budget instead of the key.
        """
        target = 0.0
        if keys[pygame.K_RIGHT]:
            target += self.turn_speed
        if keys[pygame.K_LEFT]:
            target -= self.turn_speed
        now = pygame.time.get_ticks()
        dt = (now - self._last_tick) / 1000.0 if self._last_tick is not None else 0.0
        self._last_tick = now
        if dt <= 0.0:
            return
        dt = min(dt, MAX_TURN_DT)
        # Same first-order ramp up and down: releasing the key coasts the
        # steering to a stop instead of snapping it (and thus never overshoots).
        self._turn += (target - self._turn) * min(1.0, self.turn_accel * dt)
        self.rotate_delta = self._turn * dt

    def lock_mouse(self) -> None:
        """Grab the pointer. A no-op in tractor/touch mode, which keep it."""
        if self.tractor or self.touch_active:
            self.unlock_mouse()
            return
        self._grabbed = True
        self._set_grab(True)

    def unlock_mouse(self) -> None:
        self._grabbed = False
        self.reset_turn()
        self._set_grab(False)

    @staticmethod
    def _set_grab(grabbed: bool) -> None:
        # No display yet (headless import, unit test): nothing to grab.
        if not pygame.display.get_init():
            return
        pygame.event.set_grab(grabbed)
        pygame.mouse.set_visible(not grabbed)

    def reset_mouse_position(self) -> None:
        if not pygame.display.get_init():
            return
        pygame.mouse.get_rel()

    def reset_turn(self) -> None:
        """Drop steering momentum (mode change, pause, return from a menu)."""
        self._turn = 0.0
        self._last_tick = None
