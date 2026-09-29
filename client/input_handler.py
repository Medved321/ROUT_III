from __future__ import annotations

import pygame

from client.settings import CONTROL_MODE_MOUSE, CONTROL_MODE_TRACTOR

# Frames longer than this do not advance the keyboard turn: a paused window, a
# blocked resize drag or a debugger break must not make the camera jump.
MAX_TURN_DT = 0.1

# Keys that start a fresh dash press, per control mode. Holding one must not
# re-trigger the dash -- the client owes exactly one dash packet per press.
DASH_KEYS = {
    CONTROL_MODE_MOUSE: (pygame.K_SPACE,),
    CONTROL_MODE_TRACTOR: (
        getattr(pygame, "K_LSHIFT", None),
        getattr(pygame, "K_RSHIFT", None),
    ),
}

# Fire is LMB under the mouse; the tractor trades the mouse out entirely, so
# Space is the trigger there. A tractor dash key never doubles as fire.
FIRE_KEYS = {
    CONTROL_MODE_MOUSE: (),
    CONTROL_MODE_TRACTOR: (pygame.K_SPACE,),
}


class InputHandler:
    """Raw device state -> per-frame input snapshot.

    Two control modes share this handler (see ``client/settings.py``):
      * mouse   -- mouse look, LMB fire, Space dash, arrows duplicate W/S;
      * tractor -- Left/Right steer the camera (with a ramp), Space fires,
        Left Shift dashes, the mouse is not read and not grabbed.
    """

    def __init__(self):
        self.move_x: int = 0
        self.move_y: int = 0
        self.rotate_delta: float = 0.0
        self.shoot: bool = False
        self.shoot_pressed: bool = False  # fresh fire intent this frame
        self.dash: bool = False          # dash key held
        self.dash_pressed: bool = False  # fresh dash key press this frame (one dash)
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

    @property
    def control_mode(self) -> int:
        return self._control_mode

    @control_mode.setter
    def control_mode(self, value) -> None:
        """Switching modes drops steering momentum and the pointer grab.

        A stale ramp would otherwise swing the camera on the first frame of
        the new mode, and a grab left over from the mouse mode would hide the
        cursor exactly when the tractor player needs it to aim the keyboard.
        """
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = CONTROL_MODE_MOUSE
        if value == self._control_mode:
            return
        self._control_mode = value
        self.reset_turn()
        if value == CONTROL_MODE_TRACTOR:
            self.unlock_mouse()

    @property
    def tractor(self) -> bool:
        return self._control_mode == CONTROL_MODE_TRACTOR

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
        self.pause_requested = False
        self.window_events.clear()

        tractor = self.tractor
        # Only the mouse mode steers with the mouse. Tractor mode keeps the
        # cursor visible and un-grabbed, so a stray mouse move cannot spin
        # the player while they are trying to click a menu.
        if not pygame.display.get_init():
            self._grabbed = False
        if self._grabbed and not tractor:
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
        if not tractor:
            # Under the mouse the arrows are a convenience alias for W/S; in
            # tractor mode they belong to the camera, so a given key always
            # means the same thing.
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
            elif event.type == pygame.KEYDOWN and event.key in dash_keys:
                # Edge only: holding the key must not re-trigger the dash. The
                # server keeps a cooldown either way, but the client owes the
                # player exactly one dash per press.
                self.dash = True
                self.dash_pressed = True
            elif event.type == pygame.KEYDOWN and event.key in fire_keys:
                self.shoot = True
                self.shoot_pressed = True
            elif not tractor and event.type == pygame.MOUSEBUTTONDOWN \
                    and event.button == 1:
                self.shoot = True
                self.shoot_pressed = True
            elif event.type == pygame.KEYDOWN and event.key in (
                pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4, pygame.K_5, pygame.K_6
            ):
                self.weapon_id = event.key - pygame.K_1
            elif event.type == pygame.MOUSEWHEEL:
                if event.y > 0:
                    self.weapon_id = (self.weapon_id + 1) % 6
                elif event.y < 0:
                    self.weapon_id = (self.weapon_id - 1) % 6

        if not tractor and pygame.mouse.get_pressed()[0]:
            self.shoot = True

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
        """Grab the pointer. A no-op in tractor mode, which keeps the cursor."""
        if self.tractor:
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
