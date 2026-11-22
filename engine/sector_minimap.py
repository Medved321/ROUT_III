"""Geometry-only top-down minimap for sector maps.

The tile minimap in ``rendering.py`` is driven by ``gs.MAP``/``gs.M_SZ``, which
are cleared when the bridge installs a sector map.  This module draws the
equivalent top-left block straight from the active ``SectorScene`` instead:
walls, portals and floating-slab borders, and nothing else.

There are deliberately no actors here.  Entity positions belong to the world
snapshot and the sector view is a map overview, so a green player dot or a red
enemy dot would be a second, unrelated source of truth about "where things are"
on top of the geometry.  The tile minimap keeps its own markers unchanged.

The pixel segments are cached per (scene, box size).  A settled window reuses
the list and allocates no surfaces; a new scene (a new party or a rebuilt scene
after a map edit) misses the cache naturally because the old scene is held by
the entry and cannot be recycled under the same id.
"""
from __future__ import annotations

import pygame

from engine import game_state as gs

#: Same authored share as the tile minimap's default 24x5 px cell grid.
BOX_UNITS = 120
MARGIN_UNITS = 10

BACKGROUND = (0, 0, 0)
SOLID_COLOR = (190, 190, 190)
PORTAL_COLOR = (90, 90, 100)
FLOATING_COLOR = (215, 145, 60)

# key: (id(scene), box) -> (scene reference, list of segments)
_CACHE: dict[tuple[int, int], tuple[object, list]] = {}


def invalidate() -> None:
    """Drop cached geometry (map/scene replacement also does this by identity)."""
    _CACHE.clear()


def _build_segments(scene, box: int) -> list[tuple]:
    """Project all linedefs of ``scene`` into the box, preserving aspect ratio."""
    n = scene.n_lines
    if n <= 0 or box <= 0:
        return []

    xs = []
    ys = []
    for i in range(n):
        xs.append(scene.x1[i])
        xs.append(scene.x2[i])
        ys.append(scene.y1[i])
        ys.append(scene.y2[i])
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max(max_x - min_x, 1e-6)
    span_y = max(max_y - min_y, 1e-6)

    pad = max(1, box // 24)
    inner = max(1, box - 2 * pad)
    scale = inner / max(span_x, span_y)
    used_w, used_h = span_x * scale, span_y * scale
    off_x = pad + (inner - used_w) * 0.5
    off_y = pad + (inner - used_h) * 0.5
    width = max(1, box // 120)

    segs = []
    for i in range(n):
        if scene.floating_line[i]:
            color = FLOATING_COLOR
        elif scene.two_sided[i]:
            color = PORTAL_COLOR
        else:
            color = SOLID_COLOR
        segs.append((
            color,
            off_x + (scene.x1[i] - min_x) * scale,
            off_y + (scene.y1[i] - min_y) * scale,
            off_x + (scene.x2[i] - min_x) * scale,
            off_y + (scene.y2[i] - min_y) * scale,
            width,
        ))
    return segs


def _segments(scene, box: int) -> list[tuple]:
    key = (id(scene), box)
    cached = _CACHE.get(key)
    if cached is not None and cached[0] is scene:
        return cached[1]
    segs = _build_segments(scene, box)
    _CACHE[key] = (scene, segs)
    return segs


def draw(surface: pygame.Surface, scene, ox: int, oy: int, box: int) -> int:
    """Draw the sector geometry into ``(ox, oy, box, box)``.

    Returns the number of painted linedefs.  A scene without geometry leaves
    the surface untouched (no half-drawn empty frame).
    """
    if scene is None or box <= 0:
        return 0
    segs = _segments(scene, box)
    if not segs:
        return 0
    pygame.draw.rect(surface, BACKGROUND, (ox, oy, box, box))
    for color, x0, y0, x1, y1, width in segs:
        pygame.draw.line(surface, color,
                         (ox + int(x0), oy + int(y0)),
                         (ox + int(x1), oy + int(y1)), width)
    return len(segs)


def draw_hud(surface: pygame.Surface | None = None) -> int:
    """Convenience hook used by ``rendering.render()``.

    Keeps the placement numbers in one place while leaving the caller free to
    keep its existing ``ui_px`` offset.  Passing no surface draws to ``gs.screen``.
    """
    target = gs.screen if surface is None else surface
    from engine.viewport import ui_px
    return draw(target, gs.sec_scene, ui_px(MARGIN_UNITS), ui_px(MARGIN_UNITS),
                ui_px(BOX_UNITS))
