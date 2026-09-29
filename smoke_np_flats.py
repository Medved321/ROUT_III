"""Smoke: numpy perspective flats (total-performance task 3.1).

* FlatMaps precompute per-column ``u_off`` (tangent map) and per-row
  ``v_off`` (horizon depth) offset maps.
* ``tex_array`` turns a flat surface into a ``(TEX_W, TEX_W, 3)`` uint8
  array via ``np.frombuffer`` reshape.
* A floor and a ceiling scanline from the offset-map path are compared with a
  per-pixel Python reference (tolerance 1-2 palette levels); the arbitrary-
  pixel gather (``sample_pixels``) matches the row path; maps are cached.
"""
from __future__ import annotations

import math
import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
import pygame  # noqa: E402

from engine.constants import FOV, TILE, TEX_W  # noqa: E402
from engine import np_flats  # noqa: E402


def _flat_surface():
    # Smooth ramp so a neighbouring-texel index pick stays within the 1-2
    # level tolerance (no wrap within 64x64: i+j max 126).
    surf = pygame.Surface((TEX_W, TEX_W))
    for i in range(TEX_W):
        for j in range(TEX_W):
            v = (i + j) % 256
            surf.set_at((j, i), (v, v, v))
    return surf


def _light(light, rd, view):
    f = (light / 255.0) * max(0.0, 1.0 - rd / (max(view, 1.0) * 1.2))
    return max(0.0, min(1.0, f))


def _ref_row(maps, y, px, py, pa, eye_z, z, is_floor, flat, light, view):
    """Per-pixel Python reference for one scanline (the task's эталон)."""
    iw = maps.iw
    cp, sp = math.cos(pa), math.sin(pa)
    ref = np.zeros((iw, 3), dtype=np.uint8)
    for x in range(iw):
        dz = eye_z - z if is_floor else z - eye_z
        rd = dz * (maps.v_off_floor[y] if is_floor else maps.v_off_ceil[y])
        ux = math.tan((x + 0.5) * FOV / iw - FOV / 2)
        wx = px + rd * cp - rd * sp * ux
        wy = py + rd * sp + rd * cp * ux
        tu = int(np.mod(wx / TILE, 1.0) * TEX_W) % TEX_W
        tv = int(np.mod(wy / TILE, 1.0) * TEX_W) % TEX_W
        rgb = flat[tv, tu]
        lf = _light(light, rd, view)
        ref[x] = np.clip(rgb * lf + 0.5, 0, 255).astype(np.uint8)
    return ref


def _check_row(y, is_floor, flat, maps, px, py, pa, eye_z, z, light, view):
    got = maps.sample_row(y, px, py, pa, eye_z, z, is_floor, flat, light, view)
    ref = _ref_row(maps, y, px, py, pa, eye_z, z, is_floor, flat, light, view)
    d = int(np.abs(got.astype(int) - ref.astype(int)).max())
    assert d <= 2, f"row {y} floor={is_floor}: max diff {d} > 2"


def test_maps():
    m = np_flats.get_maps(320, 240)
    assert m.u_off.shape == (320,)
    assert m.v_off_floor.shape == (240,) and m.v_off_ceil.shape == (240,)
    assert m.v_off_floor[130] > m.v_off_floor[200]   # near horizon = larger
    assert m.hc == 120.0
    assert np_flats.get_maps(320, 240) is m           # cached instance
    assert np_flats.get_maps(160, 100) is not m
    # near-horizon guard: finite, sign split between floor/ceil
    assert np.isfinite(m.v_off_floor[121]) and np.isfinite(m.v_off_ceil[119])
    assert m.v_off_floor[119] == 0.0 or abs(m.v_off_floor[119]) < 1e9
    print("  offset maps       OK")


def test_tex_array():
    surf = _flat_surface()
    arr = np_flats.FlatMaps.tex_array(surf)
    assert arr.shape == (TEX_W, TEX_W, 3)
    assert arr.dtype == np.uint8
    assert arr[0, 0, 0] == 0 and arr[5, 3, 0] == 5 + 3
    print("  tex_array         OK")


def test_floor_row():
    maps = np_flats.get_maps(320, 240)
    flat = np_flats.FlatMaps.tex_array(_flat_surface())
    for y in (130, 160, 200, 236):
        _check_row(y, True, flat, maps,
                   px=96.5, py=123.25, pa=0.37, eye_z=41.0, z=0.0,
                   light=190, view=900)
    print("  floor rows        OK")


def test_ceiling_row():
    maps = np_flats.get_maps(320, 240)
    flat = np_flats.FlatMaps.tex_array(_flat_surface())
    for y in (110, 80, 40, 6):
        _check_row(y, False, flat, maps,
                   px=42.0, py=-17.5, pa=-0.62, eye_z=29.0, z=128.0,
                   light=150, view=800)
    print("  ceiling rows      OK")


def test_sample_pixels_matches():
    maps = np_flats.get_maps(320, 240)
    flat = np_flats.FlatMaps.tex_array(_flat_surface())
    px, py, pa, eye_z, z, light, view = 88.0, 44.0, 0.9, 41.0, 0.0, 200, 900
    # full row via rows path == per-pixel gather on its indices
    rows = np.array([150, 180], dtype=np.int64)
    band = maps.sample_rows(rows, px, py, pa, eye_z, z, True, flat, light, view)
    xa = np.tile(np.arange(320, dtype=np.int64), 2)
    ya = np.repeat(rows, 320)
    pix = maps.sample_pixels(xa, ya, px, py, pa, eye_z, z, True, flat,
                             light, view)
    assert pix.shape == (2 * 320, 3)
    assert (pix.reshape(2, 320, 3) == band).all()
    print("  sample_pixels     OK")


def main():
    test_maps()
    test_tex_array()
    test_floor_row()
    test_ceiling_row()
    test_sample_pixels_matches()
    print("smoke_np_flats: ALL OK")


if __name__ == "__main__":
    main()