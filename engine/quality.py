"""Graphic quality tiers (change ``total-performance``, task 1.3/1.5).

Discrete tiers set the internal framebuffer resolution and the effect
budget.  ``auto`` is handled by the budget controller (``budget.py``), which
moves the effective tier between ``high``/``medium``/``fast`` under load.
"""
from __future__ import annotations

# Tier -> internal framebuffer resolution and visual effect multipliers.
# Internal resolution is independent of the screen size (render-backend spec:
# "Ширина внутреннего буфера SHALL определяться уровнем качества").
TIER_DEFS = {
    # textured walls + per-perspective (numpy) flats
    "high": {"iw": 500, "ih": 400, "flats": "textured",
             "effect_ratio": 1.0, "detail_ratio": 1.0, "view_dist": 1.0},
    # textured walls, solid flats
    "medium": {"iw": 320, "ih": 240, "flats": "solid",
               "effect_ratio": 0.7, "detail_ratio": 0.6, "view_dist": 0.8},
    # minimum effects
    "fast": {"iw": 100, "ih": 40, "flats": "solid",
             "effect_ratio": 0.35, "detail_ratio": 0.35, "view_dist": 0.6},
}

DEFAULT_TIER = "high"

_TIER_ORDER = ("high", "medium", "fast")


def tier_order() -> tuple:
    return _TIER_ORDER


def next_lower(tier: str):
    """The tier directly below ``tier`` (or ``None`` at the bottom)."""
    i = _TIER_ORDER.index(tier)
    return _TIER_ORDER[i + 1] if i + 1 < len(_TIER_ORDER) else None


def next_higher(tier: str):
    """The tier directly above ``tier`` (or ``None`` at the top)."""
    i = _TIER_ORDER.index(tier)
    return _TIER_ORDER[i - 1] if i > 0 else None


def resolution(tier: str):
    """Internal framebuffer ``(iw, ih)`` for a concrete tier."""
    d = TIER_DEFS[tier]
    return d["iw"], d["ih"]


def flats_mode(tier: str) -> str:
    return TIER_DEFS[tier]["flats"]


# ---------------------------------------------------------------------------
# Enemy model detail
# ---------------------------------------------------------------------------

#: Detail levels a model can be built at: 1 silhouette only, 2 plus the
#: features that read at a distance, 3 everything.
MIN_ENEMY_DETAIL = 1
MAX_ENEMY_DETAIL = 3

#: tier ``detail_ratio`` -> detail level
_DETAIL_STEPS = ((0.50, 1), (0.85, 2))
#: below this many pixels a model cannot carry even level 2 legibly
_TINY_PX = 20
_SMALL_PX = 40


def enemy_detail(tier: str, px_height: int) -> int:
    """Detail level for an enemy drawn ``px_height`` pixels tall on ``tier``.

    A tier caps how much detail is built, and a small on-screen size caps it
    again: a 14 px model cannot carry three levels, and building them would
    only waste cache on variants that look identical.  This is what makes
    "Fast" cheaper without making enemies fewer or less recognisable -- the
    silhouette and the palette are level 1, so they survive.
    """
    level = MAX_ENEMY_DETAIL
    ratio = TIER_DEFS.get(tier, TIER_DEFS[DEFAULT_TIER])["detail_ratio"]
    for edge, lvl in _DETAIL_STEPS:
        if ratio < edge:
            level = lvl
            break
    if px_height < _TINY_PX:
        return MIN_ENEMY_DETAIL
    if px_height < _SMALL_PX and level > 2:
        return 2
    return level


def validate(tier: str) -> bool:
    return tier in TIER_DEFS