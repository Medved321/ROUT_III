"""Canonical shared combat timing helpers (single source of truth).

See the hitscan-hit-registration change: the client and the server must agree
on the fire-rate cooldown so a shot shown on the client is never rejected by
the server's cooldown gate.  ``wpn_cooldown_ticks`` converts a 60 fps weapon
frame count into server ticks at the tick rate agreed for the connection —
the one function both sides use.  The default is 20 tps, but the server may
run at any rate in ``MIN_TICK_RATE..MAX_TICK_RATE`` and publishes it in
``HELLO_ACK``.
"""
from __future__ import annotations

import math

DEFAULT_TICK_RATE = 20
MIN_TICK_RATE = 5
MAX_TICK_RATE = 120
# Legacy aliases: imported all over the tree, still equal to the default.
TICK_RATE = DEFAULT_TICK_RATE
TICK_INTERVAL = 1.0 / DEFAULT_TICK_RATE
FPS_60 = 60.0

TAU = 2.0 * math.pi


def validate_tick_rate(value) -> int:
    """Return ``value`` as a tick rate, or raise for an unusable one."""
    if isinstance(value, bool):
        raise ValueError("tick rate must be an integer from 5 to 120")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError("tick rate must be an integer from 5 to 120")
    try:
        rate = int(value)
    except (TypeError, ValueError):
        raise ValueError("tick rate must be an integer from 5 to 120") from None
    if not MIN_TICK_RATE <= rate <= MAX_TICK_RATE:
        raise ValueError("tick rate must be an integer from 5 to 120")
    return rate


def tick_interval(tick_rate: int = DEFAULT_TICK_RATE) -> float:
    """Seconds per tick at the given rate."""
    return 1.0 / validate_tick_rate(tick_rate)


def fps60_per_tick(px_per_frame: float, tick_rate: int = DEFAULT_TICK_RATE) -> float:
    """ROUT_II_III speed (px/frame @60fps) -> px/tick."""
    return px_per_frame * FPS_60 / validate_tick_rate(tick_rate)


def ticks_for_seconds(seconds: float, tick_rate: int = DEFAULT_TICK_RATE) -> int:
    """Duration in seconds -> at least one tick at the given rate."""
    return max(1, round(float(seconds) * validate_tick_rate(tick_rate)))


def ms_to_ticks(milliseconds: float, tick_rate: int = DEFAULT_TICK_RATE) -> int:
    """Millisecond cooldown -> ticks at the given rate."""
    return ticks_for_seconds(float(milliseconds) / 1000.0, tick_rate)


def normalize_angle(angle: float) -> float:
    """Wrap an angle into [0, 2*pi)."""
    return angle % TAU


def wpn_cooldown_ticks(
    frames: int, tick_rate: int = DEFAULT_TICK_RATE, fps: float = FPS_60,
) -> int:
    """Fire-rate frames (60fps) -> server ticks at ``tick_rate``."""
    return max(1, round(frames * validate_tick_rate(tick_rate) / fps))


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


# Wire codes for the server's decision about one "use" (door) intent.  Advisory:
# the client only shows a short hint, so a single byte with no sequence number is
# enough (design D6).
USE_REASON_OK = 0
USE_REASON_CODES = {
    '': USE_REASON_OK,
    'locked': 1,
}
USE_REASON_LOCKED = USE_REASON_CODES['locked']
USE_REASON_NAMES = {v: k for k, v in USE_REASON_CODES.items()}


def use_reason_code(reason: str) -> int:
    """Server-side reason string -> the code that goes on the wire."""
    return USE_REASON_CODES.get(reason, USE_REASON_OK)


def use_reason_name(code: int) -> str:
    """Wire code -> reason string ('' means the attempt was accepted)."""
    return USE_REASON_NAMES.get(code, '')