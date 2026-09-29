"""numpy perspective flats (OpenSpec change ``total-performance``, D2).

Perspective floor/ceiling rasterization through precomputed offset maps:

  * ``u_off`` — per-column horizontal map ``tan(rel[x])`` where
    ``rel[x] = (x+0.5)*FOV/IW - FOV/2`` is the ray's angular offset from the
    view direction (fixed for a buffer, reused every frame);
  * ``v_off`` — per-row vertical map ``FOCAL / max(row -+ horizon, 0.5)``
    (perspective depth of each scanline relative to the horizon).

For one flat (world height ``z``, camera ``(px, py, pa)``, eye height ``eye``)
a scanline set is a single broadcast:

    rd = dz * v_off[rows]                 # per-row distance  (dz = |eye - z|)
    wx = px + rd*cos(pa) - rd*sin(pa)*u_off
    wy = py + rd*sin(pa) + rd*cos(pa)*u_off
    tu = mod(wx / TILE, 1) * TEX_W        # texture u by world x
    tv = mod(wy / TILE, 1) * TEX_W        # texture v by world y
    out = flat[tv, tu] * light(rd)

Textures are pushed through ``np.frombuffer`` reshape (one ``tobytes`` per
flat surface, cached) and sampled with index maps — no Python per-pixel loop,
no per-frame ray tables.  ``light`` is the sector light scaled by distance
fog on the row distance ``rd`` (same palette levels as the wall cache).
"""
from __future__ import annotations

import math

import pygame

import numpy as np

from engine.constants import FOV, TILE, TEX_W


FOCAL = 750.0


class FlatMaps:
    """Precomputed offset maps for one internal buffer resolution.

    Instances are immutable geometry (keyed by ``(IW, IH)``) and safe to share
    across frames and scenes (see :func:`get_maps`).
    """

    __slots__ = ("iw", "ih", "hc", "focal", "u_off", "v_off_floor",
                 "v_off_ceil", "_cols")

    def __init__(self, iw: int, ih: int, focal: float = FOCAL,
                 fov: float = FOV) -> None:
        self.iw = int(iw)
        self.ih = int(ih)
        self.hc = ih / 2.0
        self.focal = float(focal)
        xs = np.arange(iw, dtype=np.float64)
        rel = (xs + 0.5) * (fov / iw) - fov / 2.0
        self.u_off = np.tan(rel)
        rows = np.arange(ih, dtype=np.float64) - self.hc
        self.v_off_floor = focal / np.maximum(rows, 0.5)
        self.v_off_ceil = focal / np.maximum(-rows, 0.5)
        self._cols = np.arange(iw, dtype=np.int32)

    # -- texture sampling -----------------------------------------------

    @staticmethod
    def tex_array(surface) -> np.ndarray:
        """``(TEX_W, TEX_W, 3)`` uint8 texture from a flat surface via
        ``np.frombuffer`` reshape (one bulk ``tobytes``, no pixel loop)."""
        return np.frombuffer(pygame.image.tobytes(surface, "RGB"),
                             dtype=np.uint8).reshape(TEX_W, TEX_W, 3)

    def sample_row(self, y: int, px: float, py: float, pa: float,
                   eye_z: float, z: float, is_floor: bool, flat_rgb,
                   light: int, view: float) -> np.ndarray:
        """Rasterize one scanline ``y`` at full width.

        Returns ``(IW, 3)`` uint8 (the lit flat row)."""
        rows = np.array([y], dtype=np.int64)
        return self.sample_rows(rows, px, py, pa, eye_z, z, is_floor,
                                flat_rgb, light, view)[0]

    def sample_rows(self, rows, px: float, py: float, pa: float,
                    eye_z: float, z: float, is_floor: bool, flat_rgb,
                    light: int, view: float) -> np.ndarray:
        """Rasterize the given row array at full width.

        ``rows`` — int64 array of scanline indices; ``dz = |eye_z - z|``.
        Returns ``(len(rows), IW, 3)`` uint8."""
        v_off = self.v_off_floor if is_floor else self.v_off_ceil
        dz = eye_z - z if is_floor else z - eye_z
        rd = dz * v_off[rows][:, None]
        cp, sp = math.cos(pa), math.sin(pa)
        wx = px + rd * cp - (rd * sp) * self.u_off
        wy = py + rd * sp + (rd * cp) * self.u_off
        tu = (np.mod(wx / TILE, 1.0) * TEX_W).astype(np.int64) % TEX_W
        tv = (np.mod(wy / TILE, 1.0) * TEX_W).astype(np.int64) % TEX_W
        rgb = flat_rgb[tv, tu]
        lf = _light_rows(light, rd[:, 0], view)
        adj = np.clip(rgb * lf[:, None, None] + 0.5, 0.0, 255.0).astype(
            np.uint8)
        return adj

    def sample_pixels(self, xa, ya, px: float, py: float, pa: float,
                      eye_z: float, z: float, is_floor: bool, flat_rgb,
                      light: int, view: float) -> np.ndarray:
        """Sample an arbitrary pixel index pair (bulk gather).

        ``xa``/``ya`` are int arrays of the same length (expanded flat spans
        after per-column clipping, steps/windows — task 3.3).  Returns
        ``(len(xa), 3)`` uint8."""
        v_off = self.v_off_floor if is_floor else self.v_off_ceil
        dz = eye_z - z if is_floor else z - eye_z
        rd = dz * v_off[ya]
        cp, sp = math.cos(pa), math.sin(pa)
        wx = px + rd * cp - (rd * sp) * self.u_off[xa]
        wy = py + rd * sp + (rd * cp) * self.u_off[xa]
        tu = (np.mod(wx / TILE, 1.0) * TEX_W).astype(np.int64) % TEX_W
        tv = (np.mod(wy / TILE, 1.0) * TEX_W).astype(np.int64) % TEX_W
        rgb = flat_rgb[tv, tu]
        lf = _light_rows(light, rd, view)
        adj = np.clip(rgb * lf[:, None] + 0.5, 0.0, 255.0).astype(np.uint8)
        return adj


_MAPS: dict[tuple, FlatMaps] = {}


def get_maps(iw: int, ih: int, focal: float = FOCAL) -> FlatMaps:
    """Cached offset maps for an internal resolution (shared across frames).

    ``focal`` is the perspective factor in buffer rows (defaults to the module
    ``FOCAL``); sector rendering passes the viewport-scaled focal so flat depth
    matches the wall projections in the same framebuffer."""
    key = (int(iw), int(ih), float(focal))
    m = _MAPS.get(key)
    if m is None:
        m = FlatMaps(*key[:2], focal=focal)
        _MAPS[key] = m
    return m


def _light_rows(light: int | float, rd, view: float) -> np.ndarray:
    """Per-row light factor: sector light * distance fog, 0..1 (same levels
    as the wall strip cache palette)."""
    f = (light / 255.0) * np.maximum(
        0.0, 1.0 - rd / (max(view, 1.0) * 1.2))
    return np.clip(f, 0.0, 1.0)