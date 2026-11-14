"""Fixed on-screen sticks and buttons for the phone control mode.

This module owns exactly three things, and they are the three that must not be
duplicated (see ``design.md`` decisions D2 and D3):

``TouchLayout``
    The geometry.  One function computes the rectangles and the stick bases from
    the window size and the player's sliders, and *both* drawing and hit-testing
    read that same result -- a drawn button whose click area was computed
    somewhere else is the classic "the fifth chip is not clickable" bug.

``TouchControls``
    The per-finger state machine: which finger holds which widget, how far a
    stick is deflected, and the one-shot edges (fire press, dash press, "use"
    tap, weapon step).  It knows nothing about the network or the snapshot; the
    ``InputHandler`` turns this state into ``move_x``/``move_y``/
    ``rotate_delta``/``shoot``/``dash``/``use``.

``draw()``
    The overlay itself, in window pixels, blitted after the world and the HUD.

Two properties are deliberate:

* The bases are **fixed**.  A finger is captured by the base it lands in and the
  knob follows the finger; the base never moves to the finger, so a player can
  find the stick again without looking.
* Nothing is allocated per frame.  The base, the knob and each button are cached
  ``SRCALPHA`` surfaces keyed by ``(kind, pixels, alpha, label)``, so the frame
  pays a handful of blits.  A full-screen ``SRCALPHA`` layer would be ~10 MB and
  a full blending pass at 2400x1080, which is exactly what the effect budget in
  ``AGENTS.md`` forbids.
"""
from __future__ import annotations

import math

import pygame

from engine import viewport

#: Widget names, in the order the overlay draws them.
WIDGETS = ("move", "look", "fire", "dash", "use", "wprev", "wnext")

# Authored geometry, in interface units (``viewport.ui_px``): the overlay keeps
# the same share of the screen at 640x480 and at 2400x1080, like the HUD.
STICK_RADIUS_UNITS = 58.0
BUTTON_SIDE_UNITS = 74.0
SMALL_SIDE_UNITS = 50.0
MARGIN_UNITS = 14.0
GAP_UNITS = 10.0
KNOB_RATIO = 0.40

# Octant thresholds: an axis engages at 35 % of the radius and lets go at 25 %.
# Without the gap a thumb resting on an octant boundary makes ``move_x``/
# ``move_y`` flip between (1,0) and (1,1) every frame, and the 25 Hz input
# stream turns that into a visible stutter.
AXIS_ON = 0.35
AXIS_OFF = 0.25

# Overlay colours (RGB); alpha comes from ``touch_opacity``.
STICK_FILL = (235, 235, 245)
STICK_RING = (255, 255, 255)
BUTTON_FILL = (40, 44, 58)
BUTTON_EDGE = (215, 215, 225)
FIRE_FILL = (150, 40, 40)
FIRE_EDGE = (255, 120, 110)
TEXT = (240, 240, 245)

#: Cache ceiling; a window drag or a slider sweep must not grow it without bound.
_SURFACE_CACHE_MAX = 32


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def _axis_step(current: int, value: float) -> int:
    """One axis of the digital 8-way movement, with hysteresis.

    ``current`` is the axis state from the previous frame: a zero crosses the
    full threshold, a non-zero one only lets go below the release threshold, so
    a thumb sitting on the boundary does not chatter.
    """
    if value >= AXIS_ON:
        return 1
    if value <= -AXIS_ON:
        return -1
    if abs(value) <= AXIS_OFF:
        return 0
    return current


class TouchLayout:
    """Rects and stick bases for one window size and one slider position."""

    __slots__ = ("width", "height", "rects", "sticks", "key")

    def __init__(self, width: int, height: int,
                 stick_scale: float = 1.0, button_scale: float = 1.0,
                 opacity: float = 0.45) -> None:
        self.width = int(width)
        self.height = int(height)
        self.key = (self.width, self.height, round(stick_scale, 3),
                    round(button_scale, 3), round(opacity, 3))

        width, height = self.width, self.height
        # The layout is a pure function of the window it is handed, so a resize
        # is laid out for the new size immediately instead of after the next
        # ``const.W`` update -- and a geometry test can sweep sizes without
        # moving the global window.  The factor itself still lives in
        # ``engine.viewport``, the owner of interface sizes.
        def px(units: float) -> int:
            return viewport.ui_px(units, width, height)

        margin = px(MARGIN_UNITS)
        gap = px(GAP_UNITS)
        radius = max(16, int(round(px(STICK_RADIUS_UNITS) * stick_scale)))
        side = max(26, int(round(px(BUTTON_SIDE_UNITS) * button_scale)))
        small = max(20, int(round(px(SMALL_SIDE_UNITS) * button_scale)))

        # Thumb zones in the bottom corners; the buttons live above the right
        # stick, because that is the hand that also fires.
        move_c = (margin + radius, height - margin - radius)
        look_c = (width - margin - radius, height - margin - radius)

        fire_y = height - margin - 2 * radius - gap - side
        fire_x = width - margin - side
        rects = {
            "fire": pygame.Rect(fire_x, fire_y, side, side),
            "dash": pygame.Rect(fire_x - gap - side, fire_y, side, side),
            "use": pygame.Rect(fire_x, fire_y - gap - side, side, side),
            # Weapon cycling is a rare action, so the pair sits in the middle
            # bottom where either thumb can reach it without leaving its stick.
            "wprev": pygame.Rect(width // 2 - gap - small,
                                 height - margin - small, small, small),
            "wnext": pygame.Rect(width // 2 + gap,
                                 height - margin - small, small, small),
        }
        self.rects = rects
        self.sticks = {"move": move_c, "look": look_c, "radius": radius}

    def rect_for(self, widget: str) -> pygame.Rect:
        return self.rects[widget]

    def stick_center(self, widget: str) -> tuple[int, int]:
        return self.sticks[widget]

    @property
    def radius(self) -> int:
        return self.sticks["radius"]

    def hit(self, pos) -> str | None:
        """Which widget a point belongs to, or ``None``.

        Sticks are circles, buttons are their drawn rectangles: the hit area is
        the shape the player sees, so a point just outside a button cannot fire
        it and a point inside is guaranteed to.
        """
        x, y = pos
        best: str | None = None
        best_d = 0.0
        for name in ("move", "look"):
            cx, cy = self.sticks[name]
            d = math.hypot(x - cx, y - cy)
            if d <= self.radius and (best is None or d < best_d):
                best, best_d = name, d
        if best is not None:
            return best
        for name, rect in self.rects.items():
            if rect.collidepoint(x, y):
                return name
        return None


class TouchControls:
    """Fingers -> widget state.  ``InputHandler`` reads the fields below."""

    def __init__(self) -> None:
        self.layout: TouchLayout | None = None
        self.stick_scale = 1.0
        self.button_scale = 1.0
        self.opacity = 0.45
        # Digital movement (same eight directions as WASD) and analog look.
        self.move_x = 0
        self.move_y = 0
        self.look_x = 0.0
        # Held / edge state for the four buttons.
        self.fire = False
        self.fire_pressed = False
        self.dash_pressed = False
        self.use_pressed = False
        self.weapon_step = 0
        # finger id -> widget it holds; one finger owns one widget at a time.
        self._fingers: dict[int, str] = {}
        # widget -> last known finger position (window pixels).
        self._pos: dict[str, tuple[int, int]] = {}
        self._cache: dict[tuple, pygame.Surface] = {}
        self._cache_px = 0
        self._font_cache: dict[int, pygame.font.Font] = {}

    # ── settings / geometry ─────────────────────────────────────────
    def configure(self, stick_scale=None, button_scale=None, opacity=None) -> None:
        """Apply slider values; the next ``set_viewport`` re-lays the overlay."""
        if stick_scale is not None:
            self.stick_scale = float(stick_scale)
        if button_scale is not None:
            self.button_scale = float(button_scale)
        if opacity is not None:
            self.opacity = float(opacity)
        self.layout = None
        self._pos.clear()
        self._cache.clear()
        self._cache_px = 0

    def set_viewport(self, width: int, height: int) -> TouchLayout:
        """Recompute the geometry when the window or the sliders changed."""
        width = max(1, int(width))
        height = max(1, int(height))
        key = (width, height, round(self.stick_scale, 3),
               round(self.button_scale, 3), round(self.opacity, 3))
        if self.layout is None or self.layout.key != key:
            self.layout = TouchLayout(width, height, self.stick_scale,
                                      self.button_scale, self.opacity)
            # A resize invalidates every cached surface: they are sized in the
            # old window's pixels. Same for a slider move (alpha is in the key).
            self._cache.clear()
            self._cache_px = 0
            self._pos.clear()
            self._fingers.clear()
            self.move_x = self.move_y = 0
            self.look_x = 0.0
            self.fire = self.fire_pressed = False
            self.dash_pressed = self.use_pressed = False
        return self.layout

    # ── frame bookkeeping ───────────────────────────────────────────
    def begin_frame(self) -> None:
        """Drop the one-shot edges; held state survives."""
        self.fire_pressed = False
        self.dash_pressed = False
        self.use_pressed = False
        self.weapon_step = 0

    def reset(self) -> None:
        """Forget every touch: pause, end screen, lobby, focus loss."""
        self._fingers.clear()
        self._pos.clear()
        self.move_x = self.move_y = 0
        self.look_x = 0.0
        self.fire = False
        self.fire_pressed = False
        self.dash_pressed = False
        self.use_pressed = False
        self.weapon_step = 0

    # ── pointer input ───────────────────────────────────────────────
    def press(self, pointer: int, pos) -> None:
        """A press by ``pointer`` (a finger id, or 0 for the testing mouse).

        Capturing is per pointer id, so two sticks work at the same time and
        releasing one never disturbs the other.
        """
        if self.layout is None:
            return
        if pointer in self._fingers:
            return
        widget = self.layout.hit(pos)
        if widget is None:
            return
        self._fingers[pointer] = widget
        self._pos[widget] = (int(pos[0]), int(pos[1]))
        if widget == "move" or widget == "look":
            self._update_sticks()
        elif widget == "fire":
            self.fire = True
            self.fire_pressed = True
        elif widget == "dash":
            self.dash_pressed = True
        elif widget == "use":
            self.use_pressed = True
        elif widget == "wprev":
            self.weapon_step = -1
        elif widget == "wnext":
            self.weapon_step = 1

    def move(self, pointer: int, pos) -> None:
        widget = self._fingers.get(pointer)
        if widget is None:
            return
        self._pos[widget] = (int(pos[0]), int(pos[1]))
        if widget == "move" or widget == "look":
            self._update_sticks()

    def release(self, pointer: int) -> None:
        widget = self._fingers.pop(pointer, None)
        if widget is None:
            return
        self._pos.pop(widget, None)
        if widget == "fire":
            self.fire = False
        if widget == "move" or widget == "look":
            self._update_sticks()

    def is_captured(self, pointer: int) -> bool:
        return pointer in self._fingers

    def _update_sticks(self) -> None:
        layout = self.layout
        if layout is None:
            return
        radius = float(max(1, layout.radius))
        # Movement: up on screen is forward (``move_x`` like W), right is strafe
        # (``move_y`` like D). The vertical axis is inverted because screen y
        # grows downwards while the world's forward is +x.
        pos = self._pos.get("move")
        if pos is None:
            self.move_x = self.move_y = 0
        else:
            cx, cy = layout.stick_center("move")
            dx = _clamp((pos[0] - cx) / radius, -1.0, 1.0)
            dy = _clamp((pos[1] - cy) / radius, -1.0, 1.0)
            self.move_x = _axis_step(self.move_x, -dy)
            self.move_y = _axis_step(self.move_y, dx)
        pos = self._pos.get("look")
        if pos is None:
            self.look_x = 0.0
        else:
            cx, _cy = layout.stick_center("look")
            self.look_x = _clamp((pos[0] - cx) / radius, -1.0, 1.0)

    # ── drawing ─────────────────────────────────────────────────────
    def draw(self, surface: pygame.Surface) -> None:
        layout = self.layout
        if layout is None:
            return
        radius = layout.radius
        for name in ("move", "look"):
            cx, cy = layout.stick_center(name)
            base = self._base_surface(radius)
            surface.blit(base, (cx - radius, cy - radius))
            knob_r = max(6, int(radius * KNOB_RATIO))
            kx, ky = cx, cy
            pos = self._pos.get(name)
            if pos is not None:
                kx, ky = self._clamped_knob(cx, cy, pos, radius - knob_r)
            knob = self._knob_surface(knob_r)
            surface.blit(knob, (kx - knob_r, ky - knob_r))

        for name in ("fire", "dash", "use", "wprev", "wnext"):
            rect = layout.rect_for(name)
            label = _BUTTON_LABELS[name]
            surf = self._button_surface(name, rect.width, label)
            surface.blit(surf, rect.topleft)

    @staticmethod
    def _clamped_knob(cx: int, cy: int, pos, limit: float):
        dx = pos[0] - cx
        dy = pos[1] - cy
        dist = math.hypot(dx, dy)
        if dist <= limit or dist <= 0:
            return int(pos[0]), int(pos[1])
        k = limit / dist
        return int(cx + dx * k), int(cy + dy * k)

    def _alpha(self, factor: float = 1.0) -> int:
        return max(0, min(255, int(round(255 * self.opacity * factor))))

    def _font(self, px: int) -> pygame.font.Font:
        px = max(8, int(px))
        font = self._font_cache.get(px)
        if font is None:
            font = pygame.font.SysFont("monospace", px, bold=True)
            self._font_cache.clear()
            self._font_cache[px] = font
        return font

    def _remember(self, key, surf: pygame.Surface) -> pygame.Surface:
        px = surf.get_width() * surf.get_height()
        if len(self._cache) >= _SURFACE_CACHE_MAX or self._cache_px + px > 3_000_000:
            self._cache.clear()
            self._cache_px = 0
        self._cache[key] = surf
        self._cache_px += px
        return surf

    def _base_surface(self, radius: int) -> pygame.Surface:
        key = ("base", radius, self._alpha())
        hit = self._cache.get(key)
        if hit is None:
            size = 2 * radius + 2
            surf = pygame.Surface((size, size), pygame.SRCALPHA)
            centre = (radius + 1, radius + 1)
            pygame.draw.circle(surf, (*STICK_FILL, self._alpha(0.25)), centre, radius)
            pygame.draw.circle(surf, (*STICK_RING, self._alpha(0.55)), centre,
                               radius, max(1, radius // 14))
            hit = self._remember(key, surf)
        return hit

    def _knob_surface(self, radius: int) -> pygame.Surface:
        key = ("knob", radius, self._alpha())
        hit = self._cache.get(key)
        if hit is None:
            size = 2 * radius + 2
            surf = pygame.Surface((size, size), pygame.SRCALPHA)
            centre = (radius + 1, radius + 1)
            pygame.draw.circle(surf, (*STICK_FILL, self._alpha(0.85)), centre, radius)
            hit = self._remember(key, surf)
        return hit

    def _button_surface(self, name: str, side: int, label: str) -> pygame.Surface:
        key = ("btn", name, side, self._alpha(), label)
        hit = self._cache.get(key)
        if hit is None:
            surf = pygame.Surface((side, side), pygame.SRCALPHA)
            fire = name == "fire"
            fill = FIRE_FILL if fire else BUTTON_FILL
            edge = FIRE_EDGE if fire else BUTTON_EDGE
            rect = pygame.Rect(0, 0, side, side)
            radius = max(4, side // 6)
            pygame.draw.rect(surf, (*fill, self._alpha(0.75)), rect,
                             border_radius=radius)
            pygame.draw.rect(surf, (*edge, self._alpha()), rect,
                             max(2, side // 24), border_radius=radius)
            if label:
                font = self._font(max(9, int(side * 0.28)))
                text = font.render(label, True, TEXT)
                # A label that does not fit is cut by the box, not by an
                # ellipsis: single short words are used precisely so the drawn
                # caption never claims more room than the hit box has.
                clip = surf.get_clip()
                surf.set_clip(rect.inflate(-4, -4))
                surf.blit(text, (rect.centerx - text.get_width() // 2,
                                 rect.centery - text.get_height() // 2))
                surf.set_clip(clip)
            hit = self._remember(key, surf)
        return hit


#: Button captions. Single short words: the caption is drawn inside the box that
#: is also the hit area, so it must fit at the smallest slider position.
_BUTTON_LABELS = {
    "fire": "ОГОНЬ",
    "dash": "ДЭШ",
    "use": "ИСП",
    "wprev": "\u2039",
    "wnext": "\u203a",
}
