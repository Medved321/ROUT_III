"""Smoke: graphic tier selection (total-performance task 1.5).

Settings-driven tier: 0 = auto, 1/2/3 = manual high/medium/fast lock.
Manual tier fixes the internal framebuffer resolution and flats mode and is not
overridden by the adaptive ladder.  Run: ``py -3.12 smoke_tiers.py``.
"""
from __future__ import annotations

import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pygame  # noqa: E402

from engine import game_state as gs  # noqa: E402
from engine.quality import (  # noqa: E402
    TIER_DEFS, flats_mode, next_higher, next_lower, resolution, tier_order,
)
from client.settings import DEFAULTS, SPEC_BY_KEY, Settings, format_value  # noqa: E402


def test_settings_spec():
    assert "quality_tier" in DEFAULTS and DEFAULTS["quality_tier"] == 0
    spec = SPEC_BY_KEY["quality_tier"]
    assert spec["min"] == 0 and spec["max"] == 3 and spec["kind"] == "int"
    assert format_value("quality_tier", 0) == "Авто"
    assert format_value("quality_tier", 1) == "Хорошая"
    assert format_value("quality_tier", 2) == "Средняя"
    assert format_value("quality_tier", 3) == "Быстрая"
    print("  settings spec     OK")


def test_apply_manual():
    s = Settings()
    for val, tier in ((1, "high"), (2, "medium"), (3, "fast")):
        s.set("quality_tier", val)
        s.apply_runtime()
        assert gs.quality_mode == "manual", (val, gs.quality_mode)
        assert gs.quality_tier == tier, (val, gs.quality_tier)
        assert gs.flat_textured == (flats_mode(tier) == "textured"), (val, tier)
        iw, ih = resolution(tier)
        assert (iw, ih) == (TIER_DEFS[tier]["iw"], TIER_DEFS[tier]["ih"])
    print("  manual apply      OK")


def test_auto_default():
    s = Settings()
    s.set("quality_tier", 0)
    s.apply_runtime()
    assert gs.quality_mode == "auto"
    print("  auto default      OK")


def test_tier_order():
    assert tier_order() == ("high", "medium", "fast")
    assert next_lower("high") == "medium"
    assert next_lower("medium") == "fast"
    assert next_lower("fast") is None
    assert next_higher("fast") == "medium"
    assert next_higher("high") is None
    print("  tier order        OK")


def main():
    pygame.init()
    test_settings_spec()
    test_auto_default()
    test_apply_manual()
    test_tier_order()
    print("smoke_tiers: ALL OK")


if __name__ == "__main__":
    main()