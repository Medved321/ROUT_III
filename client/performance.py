"""Frame-budget quality adaptation and fixed-rate input pacing.

Pure helpers (no pygame dependency) so they can be exercised headlessly and
reused by the client loop.
"""
from __future__ import annotations

import asyncio

from engine.constants import MAX_ADAPTIVE_SCALE, MAX_ADAPTIVE_VS


class AdaptiveQuality:
    """Hysteresis quality adapter driven by measured frame time.

    The configured ``render_scale`` (and ``base_vs``) is the best (starting)
    quality.  When frames consistently exceed the budget derived from the
    target FPS the adapter degrades through a fixed ladder — ``render_scale``
    to its max, then ``vs_scale`` up to ``max_vs``, then textured flats off —
    and with sustained headroom it walks back in reverse.  Steps are
    rate-limited to one per ``interval`` seconds (hysteresis) and the
    configured values are never changed.
    """

    def __init__(
        self,
        base_scale: int,
        max_scale: int = MAX_ADAPTIVE_SCALE,
        base_vs: int = 2,
        target_fps: int = 60,
        overload_ratio: float = 1.2,
        headroom_ratio: float = 0.75,
        interval: float = 1.0,
        ema_alpha: float = 0.25,
    ) -> None:
        self.base_scale = max(1, int(base_scale))
        self.max_scale = max(self.base_scale, int(max_scale))
        self.base_vs = max(1, int(base_vs))
        self.max_vs = max(self.base_vs, int(MAX_ADAPTIVE_VS))
        self.target_fps = max(1, int(target_fps))
        self.overload_ratio = float(overload_ratio)
        self.headroom_ratio = float(headroom_ratio)
        self.interval = float(interval)
        self.ema_alpha = float(ema_alpha)
        self.avg_frame = 0.0
        self.scale = self.base_scale
        self.vs_scale = self.base_vs
        self.flat_textured = True
        self._last_change: float | None = None
        self._initialized = False

    @property
    def budget_ms(self) -> float:
        return 1000.0 / self.target_fps

    def reset(
        self,
        base_scale: int | None = None,
        base_vs: int | None = None,
        target_fps: int | None = None,
    ) -> None:
        if base_scale is not None:
            self.base_scale = max(1, int(base_scale))
            self.max_scale = max(self.base_scale, self.max_scale)
            self.scale = self.base_scale
        if base_vs is not None:
            self.base_vs = max(1, int(base_vs))
            self.max_vs = max(self.base_vs, int(MAX_ADAPTIVE_VS))
            self.vs_scale = self.base_vs
        if target_fps is not None:
            self.target_fps = max(1, int(target_fps))
        self.avg_frame = 0.0
        self._last_change = None
        self._initialized = False

    def sample(self, frame_ms: float, now: float) -> int:
        """Feed one frame duration (ms) and return the current render scale."""
        if frame_ms < 0:
            frame_ms = 0.0
        if not self._initialized:
            self.avg_frame = frame_ms
            self._initialized = True
        else:
            self.avg_frame += self.ema_alpha * (frame_ms - self.avg_frame)
        if self._last_change is None:
            self._last_change = now
            return self.scale
        if now - self._last_change < self.interval:
            return self.scale
        budget = self.budget_ms
        if self.avg_frame > budget * self.overload_ratio:
            # Degrade ladder: render scale, then vertical scale, then flats.
            if self.scale < self.max_scale:
                self.scale += 1
                self._last_change = now
            elif self.vs_scale < self.max_vs:
                self.vs_scale += 1
                self._last_change = now
            elif self.flat_textured:
                self.flat_textured = False
                self._last_change = now
        elif self.avg_frame < budget * self.headroom_ratio:
            # Restore ladder in reverse order.
            if not self.flat_textured:
                self.flat_textured = True
                self._last_change = now
            elif self.vs_scale > self.base_vs:
                self.vs_scale -= 1
                self._last_change = now
            elif self.scale > self.base_scale:
                self.scale -= 1
                self._last_change = now
        return self.scale

    def quality_factor(self) -> float:
        """1.0 at the configured quality, 0.0 at the worst allowed quality."""
        span = self.max_scale - self.base_scale
        if span <= 0:
            return 1.0
        return 1.0 - (self.scale - self.base_scale) / span

    def effects_factor(self) -> float:
        """Effect-budget multiplier: 1.0 (best) .. 0.5 (worst)."""
        return 0.5 + 0.5 * self.quality_factor()

    def view_dist_factor(self) -> float:
        """View-distance multiplier: 1.0 (best) .. 0.7 (worst)."""
        return 0.7 + 0.3 * self.quality_factor()


async def input_send_loop(network, snapshot, interval: float, should_run=None) -> None:
    """Send the freshest input snapshot at a fixed rate, independent of FPS.

    ``snapshot`` is a zero-arg callable returning ``(move_x, move_y, angle,
    shoot, weapon_id, flags)``; it is read right before every send so the latest
    local prediction (including mouse rotation) always goes out.  ``flags``
    carries one-shot action intents (dash).
    """
    try:
        while should_run is None or should_run():
            move_x, move_y, angle, shoot, weapon_id, flags = snapshot()
            network.send_input(move_x, move_y, angle, shoot, weapon_id, flags)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        pass
