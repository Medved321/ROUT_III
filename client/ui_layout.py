"""Shared layout helpers for the menu screens.

The menus are laid out by hand on a fixed 1000x700 canvas, and the two
guarantees that keep that hand layout honest live here:

* a button is exactly as wide as its label, so a label can never grow out of
  its own box, and a row of buttons knows when it no longer fits;
* text is measured, not guessed -- ``font.size`` instead of rendering a throwaway
  surface -- and is cut with an ellipsis when it exceeds the width it was given.

Every rect used for hit-testing is the one that was drawn: the layout helpers
return the rects, and both the ``draw`` and the ``handle`` side of a screen use
them.
"""
from __future__ import annotations

import pygame

# Horizontal breathing room kept between the canvas edge and the first/last
# button, and between two adjacent buttons in a row.
BUTTON_PAD = 10
BUTTON_GAP = 8
CANVAS_MARGIN = 12
ELLIPSIS = "…"


def text_width(text: str, font: pygame.font.Font) -> int:
    """Width of ``text`` in this font, without rendering a surface."""
    return font.size(text)[0]


def fit_width(text: str, font: pygame.font.Font, pad: int = BUTTON_PAD,
              min_width: int = 0) -> int:
    """Width a button needs to show ``text`` with ``pad`` on each side."""
    return max(min_width, text_width(text, font) + 2 * pad)


def ellipsize(text: str, font: pygame.font.Font, max_width: int) -> str:
    """Shorten ``text`` until it fits ``max_width``, ending in an ellipsis.

    Returns ``text`` unchanged when it already fits. Cyrillic monospace is
    fixed-width per glyph, so a binary search over character count converges in
    a handful of measurements; the label is cut on a character boundary so it
    never splits a letter in half.
    """
    if max_width <= 0:
        return ""
    if text_width(text, font) <= max_width:
        return text

    ell_w = text_width(ELLIPSIS, font)
    if ell_w > max_width:
        return ""

    lo, hi = 0, len(text)
    best = ""
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = text[:mid].rstrip() + ELLIPSIS
        if text_width(candidate, font) <= max_width:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def clip_tail(text: str, font: pygame.font.Font, max_width: int) -> str:
    """Keep the *end* of ``text`` visible, marking the cut with a leading ellipsis.

    Used for text the player types: the cursor sits at the end, so a truncated
    tail keeps the character being entered visible instead of hiding it behind
    a full line of earlier input.
    """
    if max_width <= 0:
        return ""
    if text_width(text, font) <= max_width:
        return text

    ell_w = text_width(ELLIPSIS, font)
    if ell_w > max_width:
        return ""

    lo, hi = 0, len(text)
    best = ""
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = ELLIPSIS + text[len(text) - mid:] if mid else ELLIPSIS
        if text_width(candidate, font) <= max_width:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def layout_row(items: list[tuple[str, int]], *, height: int, y: int,
               margin: int = CANVAS_MARGIN, gap: int = BUTTON_GAP,
               canvas_width: int = 1000, min_width: int = 0) -> dict:
    """Lay buttons out left to right, wrapping to a new row when they run out.

    ``items`` is an ordered list of ``(key, width)``; a button wider than the
    usable width is capped to it rather than overflowing the canvas. Returns the
    rect per key plus the number of rows used, so callers can derive the space
    the toolbar actually needs instead of assuming a single row.

    A button that cannot fit even on a row of its own is placed on a row to
    itself rather than on top of the previous one, so the returned rects are
    always disjoint.
    """
    usable = canvas_width - 2 * margin
    rects: dict[str, pygame.Rect] = {}
    cursor = margin
    row = 0
    for key, width in items:
        width = max(min_width, min(width, usable))
        if cursor > margin and cursor + width > canvas_width - margin:
            row += 1
            cursor = margin
        rects[key] = pygame.Rect(cursor, y + row * (height + gap), width, height)
        cursor += width + gap
    return {"rects": rects, "rows": row + 1, "height": (row + 1) * height + row * gap}
