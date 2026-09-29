"""Canonical shared combat timing helpers (single source of truth).

See the hitscan-hit-registration change: the client and the server must agree
on the fire-rate cooldown so a shot shown on the client is never rejected by
the server's cooldown gate.  ``wpn_cooldown_ticks`` converts a 60 fps weapon
frame count into server ticks (20 tps) — the one function both sides use.
"""
from __future__ import annotations

import math

TICK_RATE = 20
TICK_INTERVAL = 1.0 / TICK_RATE
FPS_60 = 60.0

TAU = 2.0 * math.pi


def normalize_angle(angle: float) -> float:
    """Wrap an angle into [0, 2*pi)."""
    return angle % TAU


def wpn_cooldown_ticks(frames: int, tick_rate: int = TICK_RATE, fps: float = FPS_60) -> int:
    """fire-rate frames (60fps) -> server ticks (20 tps)."""
    return max(1, round(frames * tick_rate / fps))