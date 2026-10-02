"""Window-relative screen space and the world->screen projection.

Two numbers decide everything that is drawn *over* the world, and this module
owns both of them so they cannot drift apart again:

``ui_scale()`` / ``ui_px()``
    One factor for every size that is part of the interface rather than of the
    world: the HUD bar, the crosshair, the minimap, the overlay text and the
    first-person weapon.  Sizes are authored in *units of a reference window*
    and multiplied by the factor, so the interface keeps the same share of the
    screen at 640x480 and at 3840x2160.  Without it the HUD was the same 100
    pixels tall everywhere -- 20.8 % of a 480px window and 4.6 % of a 2160px
    one.

``project_point()``
    World -> screen for points that are not sprites: particles, decals.  The
    world is projected with a focal of 750 and an eye at ``CAM_Z`` (tile path)
    or through the sector scene (sector path).  Copying that arithmetic into
    a second place is what put particles 350px below the floor: they had their
    own focal, their own eye height, and their own idea of which distance the
    depth buffer holds.

The distance returned is ``perp`` -- perpendicular to the view direction --
because that is what ``z_b`` stores (``rendering.py``: ``rd = d*cos(ra - pa)``;
``sector_render``: forward coordinate).  Comparing it against a radial distance
makes every off-axis effect fail its own occlusion test, so it vanishes next to
a wall that does not actually cover it.

``W``, ``H`` and ``FOV`` are read at call time, never captured at import: they
are rewritten on every window resize and in the settings screen, and a module
that copied the values would keep projecting for whichever window was open
when it was first imported.
"""

from __future__ import annotations

import math

import pygame

from engine.constants import TILE, CAM_Z, EFFECT_NEAR_DIST
from engine import constants as const
from engine import game_state as gs

#: Reference window the interface is authored against.  Same 1000x700 the menu
#: canvas uses, so the two layers derive their layout from one number.
REF_W, REF_H = 1000, 700

#: Focal length of the tile path.  The walls use it in seven places
#: (``rendering.py``) and the sector path has its own identical constant
#: (``sector_render.FOCAL``); it is named here so a second copy of the number
#: has a place to be wrong loudly.
TILE_FOCAL = 750.0

#: Half-angle cone accepted for a projected object, shared with the sprite pass.
VIEW_CONE = 1.0

_FONT_SIZES = (34, 18)
_font_cache: dict[tuple[int, int], tuple] = {}


def ui_scale() -> float:
    """Factor for interface sizes at the current window.

    ``min`` over both axes rather than height alone: the HUD bar spans the full
    width, so a very wide window must not buy it elements that no longer fit
    across.  Every preset from 640x480 to 3840x2160 is height-limited anyway,
    so ``min`` only matters for extreme aspect ratios.
    """
    w = const.W if const.W > 0 else REF_W
    h = const.H if const.H > 0 else REF_H
    return min(w / float(REF_W), h / float(REF_H))


def ui_px(units: float) -> int:
    """A length authored against the reference window, in current pixels.

    Rounded to whole pixels on purpose.  The world is upscaled with
    ``pygame.transform.scale`` (nearest neighbour), and a fractional font or
    border would be resampled into a smear that also shifts between frames.
    Caches keyed on this value therefore only miss on real resizes, not on
    every pixel of a window drag.
    """
    return max(1, int(round(units * ui_scale())))


def ensure_hud_fonts() -> None:
    """Size ``gs.font`` / ``gs.sm_font`` to the window.

    The font size is an interface size, so it belongs next to the rest of the
    interface rather than in the client: two owners for "how big is the HUD"
    is the same bug as two owners for the projection.  One pair is cached and
    rebuilt only when the quantized size changes, because dragging a window
    fires a resize per pixel and re-creating a ``SysFont`` on each one is both
    slow and unbounded.
    """
    s = ui_scale()
    key = (max(1, round(_FONT_SIZES[0] * s)), max(1, round(_FONT_SIZES[1] * s)))
    hit = _font_cache.get(key)
    if hit is None:
        hit = (pygame.font.SysFont("monospace", key[0], bold=True),
               pygame.font.SysFont("monospace", key[1]))
        _font_cache.clear()  # a window drag must not grow this without bound
        _font_cache[key] = hit
    if gs.font is not hit[0]:
        gs.font, gs.sm_font = hit


def _tile_ground_y(vh: float, vy: int, perp: float) -> float:
    """Screen y of the floor at perpendicular distance ``perp`` (tile path).

    This is the line the sprite pass puts its bases on
    (``rendering.py``: ``spr_flr = center + CAM_Z*sh/TILE - sh/4`` with
    ``sh = TILE*750/dist``), written out.  It is *not* the naive
    ``center + CAM_Z*focal/dist``: the ``- sh/4`` term is a real part of where
    this renderer considers the ground to be, and dropping it leaves
    particles floating above the sprites that stand on the same spot.
    """
    return vy + vh / 2 + (CAM_Z - TILE / 4.0) * TILE_FOCAL / (perp + 0.1)


def project_point(x: float, y: float, world_h: float,
                  vh: float | None = None, vy: int | None = None):
    """Project a world point to ``(sx, sh, ground_y, perp)``, or ``None``.

    ``sh``      on-screen height of something ``world_h`` units tall.
    ``ground_y`` screen y of its base, i.e. of the floor it stands on -- for a
               point ``z`` units above that floor, add ``- z * sh / world_h``.
    ``perp``    distance perpendicular to the view direction, in the same units
               as ``z_b``, so it can be compared against a depth column
               directly.

    In sector mode this delegates to ``sector_render.project_object``, so
    particles follow the floor of the sector they are in instead of the
    horizon.
    """
    if vh is None:
        vh = const.H
    if vy is None:
        vy = 0
    if gs.sector_mode and gs.sec_scene is not None:
        from engine import sector_render
        proj = sector_render.project_object(x, y, world_h, vh, vy)
        if proj is None:
            return None
        sx, sh, ground_y, perp = proj
        # Same near plane as the tile branch, so a puff at your feet cannot
        # blow up to a screen-filling blob in one renderer and not the other.
        return sx, min(sh, world_h * TILE_FOCAL / EFFECT_NEAR_DIST), ground_y, perp

    edx, edy = x - gs.px, y - gs.py
    dst = math.hypot(edx, edy)
    if dst < 1:
        return None
    ang = math.atan2(edy, edx) - gs.pa
    while ang > math.pi:
        ang -= 2 * math.pi
    while ang < -math.pi:
        ang += 2 * math.pi
    if abs(ang) >= VIEW_CONE:
        return None
    perp = dst * math.cos(ang)
    if perp < 1:
        perp = 1
    # The near plane clamps *scale only*: `perp` and `ground_y` keep the true
    # distance, so an effect right underfoot still parallaxes against the floor
    # while it does not project to tens of thousands of pixels.
    sh = world_h * TILE_FOCAL / (perp if perp > EFFECT_NEAR_DIST else EFFECT_NEAR_DIST)
    sx = int((0.5 * (ang / (const.FOV / 2)) + 0.5) * const.W)
    return sx, sh, _tile_ground_y(vh, vy, perp), perp
