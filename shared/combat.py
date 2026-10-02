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


# Wire codes for the server's decision about one fire intent.
#
# ``fire_seq`` alone cannot tell "refused" from "the acknowledgement is still in
# flight", because a refusal leaves the sequence number where it was.  The
# client needs that distinction: without it, an empty magazine held down plays
# the predicted recoil over and over (a phantom shot), while refusing to cancel
# would also cut the real recoil of a shot whose ack is one snapshot late.  So
# the reason travels next to the sequence number.
FIRE_REASON_OK = 0
FIRE_REASON_CODES = {
    '': FIRE_REASON_OK,
    'cooldown': 1,
    'no_ammo': 2,
    'proj_cap': 3,
    'noop': 4,
}
FIRE_REASON_NAMES = {v: k for k, v in FIRE_REASON_CODES.items()}


def fire_reason_code(reason: str) -> int:
    """Server-side reason string -> the code that goes on the wire."""
    return FIRE_REASON_CODES.get(reason, FIRE_REASON_OK)


def fire_reason_name(code: int) -> str:
    """Wire code -> reason string ('' means the intent was accepted)."""
    return FIRE_REASON_NAMES.get(code, '')