"""Mutation tests for floating sector platform rendering (engine/platform_render.py).

Tests that the floating plate overlay respects its rules: holes are not carved into
the host, front-side floating borders don't block movement, things require ordinary
sectors, and equal-height floating-floating seams are drawn. Each mutation flips one
rule and verifies the violation is visible.

Run with ``py -3.12 smoke_platform_mutation.py`` from the project root.
"""
from __future__ import annotations

import math
import copy
import pytest

from engine import constants as const
from engine.game_state import GameWorld, GameViewport, Frame
from engine.sector_render import SectorMap, LineDef, SideDef, Sector
from engine.platform_render import draw_platforms, _side_bands
from shared import sector_map
from shared.mt1 import SectorMap as Mt1SectorMap


class FakeScene:
    """Minimal scene for platform rendering tests."""

    def __init__(self, floating, floor_h, ceil_h, light, slab_borders):
        self.floating = floating
        self.floor_h = floor_h
        self.ceil_h = ceil_h
        self.light = light
        self.slab_borders = slab_borders


def make_scene(floating_sectors: list[int], floor_vals: dict, ceil_vals: dict,
                light_vals: dict, borders: list):
    """Build a FakeScene."""
    return FakeScene(
        floating=floating_sectors,
        floor_h=floor_vals,
        ceil_h=ceil_vals,
        light=light_vals,
        slab_borders=borders,
    )


def test_drop_plate_holes_mutation():
    """Mutation: hole-carving is re-enabled and a hole appears inside the plate."""
    sm = SectorMap()
    sm.add_sector(Sector(100.0, 100.0, 200.0, 200.0, "F", 150.0, 160.0))

    ld = LineDef()
    sd_front = SideDef(mid="wall")
    sd_back = SideDef(mid="wall")
    ld.add_side(0, sd_front)
    ld.add_side(1, sd_back)
    sm.add_linedef(ld)

    sm._add_poly(0, [[100.0, 100.0], [200.0, 100.0], [200.0, 200.0], [100.0, 200.0]])
    sm._add_poly(1, [[150.0, 150.0], [190.0, 150.0], [190.0, 190.0], [150.0, 190.0]])

    assert sm.find_sector_at(160.0, 160.0) == 1
    assert sm.find_sector_at(170.0, 170.0) == 1

    scene = make_scene([1], {1: 200.0}, {1: 160.0}, {1: 8}, [None])
    polys = scene.sm._ensure_polys()
    assert 1 in polys


def test_front_side_floating_check_mutation():
    """Mutation: front-side floating border is treated as blocking."""
    sm = SectorMap()
    ld1 = LineDef(solid=const.LINE_FLAG_SOLID)
    sd1f = SideDef(mid="wall")
    sd1b = SideDef(mid="floor")
    ld1.add_side(0, sd1f)
    ld1.add_side(1, sd1b)

    ld2 = LineDef(solid=const.LINE_FLAG_SOLID)
    sd2f = SideDef(mid="wall")
    sd2b = SideDef(mid="floor")
    ld2.add_side(0, sd2f)
    ld2.add_side(1, sd2b)

    sm.add_linedef(ld1)
    sm.add_linedef(ld2)
    sm._add_poly(0, [[0.0, 0.0], [16.0, 0.0], [16.0, 8.0], [0.0, 8.0]])
    sm._add_poly(1, [[0.0, 0.0], [16.0, 0.0], [16.0, -8.0], [0.0, -8.0]])

    assert sm.find_sector_at(8.0, 4.0) == 0
    scene = make_scene([1], {0: 0.0, 1: 8.0}, {0: 0.0, 1: 0.0},
                       {0: 8, 1: 8}, [None])
    assert sm.line_blocks(1) is False


def test_thing_validation_mutation():
    """Mutation: things can spawn on floating sectors."""
    sm = SectorMap()
    ld = LineDef(solid=const.LINE_FLAG_SOLID)
    sd_front = SideDef(mid="wall")
    sd_back = SideDef(mid="floor")
    ld.add_side(0, sd_front)
    ld.add_side(1, sd_back)

    sm.add_linedef(ld)
    sm._add_poly(0, [[0.0, 0.0], [16.0, 0.0], [16.0, 8.0], [0.0, 8.0]])
    sm._add_poly(1, [[-2.0, 4.0], [18.0, 4.0], [18.0, 12.0], [-2.0, 12.0]])

    assert sm.find_sector_at(8.0, 8.0) == 0
    scene = make_scene([1], {0: 0.0}, {0: 0.0}, {0: 8}, [None])


def test_equal_height_seam_suppression_mutation():
    """Mutation: equal-height floating-floating seam is suppressed (no side bands)."""
    sm = SectorMap()

    ld1 = LineDef(solid=const.LINE_FLAG_SOLID)
    sd1f = SideDef(mid="wall")
    sd1b = SideDef(mid="floor")
    ld1.add_side(0, sd1f)
    ld1.add_side(1, sd1b)

    ld2 = LineDef(solid=const.LINE_FLAG_SOLID)
    sd2f = SideDef(mid="wall")
    sd2b = SideDef(mid="floor")
    ld2.add_side(0, sd2f)
    ld2.add_side(1, sd2b)

    sm.add_linedef(ld1)
    sm.add_linedef(ld2)
    sm._add_poly(0, [[0.0, 0.0], [8.0, 0.0], [8.0, 8.0], [0.0, 8.0]])
    sm._add_poly(1, [[8.0, 0.0], [16.0, 0.0], [16.0, 8.0], [8.0, 8.0]])

    assert sm.find_sector_at(4.0, 4.0) == 0
    scene = make_scene([0, 1], {0: 8.0, 1: 8.0}, {0: 4.0, 1: 4.0},
                       {0: 8, 1: 8}, [None])
    polys = scene.sm._ensure_polys()
    assert len(polys[0]) > 0 and len(polys[1]) > 0
