"""Smoke: wall strip caches (total-performance tasks 2.1-2.2).

Task 2.1: every texture is pre-lit into 16 discrete light levels in
column-major byte form; a wall column is a contiguous slice picked by
(texture, light level).  The cached column is compared against a per-pixel
reference tint built independently (``int(v * m)``, ``m=(level+0.5)/16``).

Task 2.2: distance-bucket strips are bounded by the byte budget (LRU) and two
consecutive queries of identical geometry hit the cache (hit counter grows).
Run: ``py -3.12 smoke_wallcache.py``.
"""
from __future__ import annotations

import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pygame  # noqa: E402

from engine.constants import TEX_W  # noqa: E402
from engine.wallcache import (  # noqa: E402
    DEFAULT_CACHE, LIGHT_STEPS, WallStripCache, bucket_for_dist,
    light_level, tint3,
)


def _make_tex():
    surf = pygame.Surface((TEX_W, TEX_W))
    for y in range(TEX_W):
        for x in range(TEX_W):
            surf.set_at((x, y), (x * 3 % 256, y * 3 % 256,
                                 (x * 7 + y * 5) % 256))
    return surf

def _ref_column(test_surf, level, tex_x, steps):
    """Per-pixel reference: tint every source pixel, top row = first byte."""
    m = (level + 0.5) / steps
    out = bytearray(TEX_W * 3)
    for y in range(TEX_W):
        r, g, b = test_surf.get_at((tex_x, y))[:3]
        o = y * 3
        out[o] = int(r * m)
        out[o + 1] = int(g * m)
        out[o + 2] = int(b * m)
    return bytes(out)


def test_lit_columns_match_reference():
    cache = WallStripCache()
    surf = _make_tex()
    cache.texture(surf)
    for level in (0, 1, 4, 8, 12, 15):
        for tex_x in (0, 1, 17, 63):
            col = cache.column(surf, level, tex_x)
            assert col is not None and len(col) == TEX_W * 3
            ref = _ref_column(surf, level, tex_x, LIGHT_STEPS)
            assert col == ref, (level, tex_x)
    # hits incremented on a repeated request of the same lit texture
    before = cache.hits
    cache.column(surf, 8, 5)
    cache.column(surf, 8, 5)
    assert cache.hits == before + 2
    print("  lit columns vs reference  OK")


def test_light_level_mapping():
    assert light_level(0) == 0
    assert light_level(255) == LIGHT_STEPS - 1
    assert light_level(300) == LIGHT_STEPS - 1
    assert light_level(128) == int(128 * LIGHT_STEPS / 255)
    print("  light mapping             OK")


def test_repeated_queries_use_cache():
    cache = WallStripCache()
    surf = _make_tex()
    cache.texture(surf)
    cache.lit(surf, 8)
    c1 = cache.hits
    # identical geometry twice → both hit (misses frozen, hits grow)
    strip = cache.strip(surf, 8, 4)
    assert strip is not None
    h1, m1 = cache.hits, cache.misses
    strip2 = cache.strip(surf, 8, 4)
    assert strip2 == strip
    assert cache.hits == h1 + 1 and cache.misses == m1
    col = cache.strip_column(surf, 8, 4, 12)
    col2 = cache.strip_column(surf, 8, 4, 12)
    assert col == col2 and len(col) == cache.bucket_heights()[4] * 3
    assert cache.hits == h1 + 3
    assert cache.misses == m1
    print("  repeated queries hit cache OK")


def test_bucket_heights_and_dist():
    cache = WallStripCache()
    borders = [cache.bucket_heights()[0] >= cache.bucket_heights()[k]
               for k in range(16)]
    assert all(borders)
    edges = cache._bucket_edges
    for k in range(16):
        d = edges[k] + (edges[k + 1] - edges[k]) / 2
        assert bucket_for_dist(d, edges) == k, k
    assert bucket_for_dist(edges[15], edges) == 15
    assert bucket_for_dist(0, edges) >= 0
    print("  bucket geometry           OK")


def test_memory_limit_lru():
    cache = WallStripCache(max_bytes=64 * 1024)
    surf = _make_tex()
    cache.texture(surf)
    for light in (1, 3, 5, 7, 9, 11, 13, 15):
        for bucket in range(16):
            cache.strip(surf, light, bucket)
    stats = cache.stats()
    assert stats["bytes"] <= cache.max_bytes, stats
    # the smallest bucket strips are the biggest (near buckets); with a small
    # budget the LRU must have evicted early large strips in favour of smaller.
    assert stats["strips"] > 0
    print("  memory limit + LRU       OK")


def main():
    pygame.init()
    test_lit_columns_match_reference()
    test_light_level_mapping()
    test_repeated_queries_use_cache()
    test_bucket_heights_and_dist()
    test_memory_limit_lru()
    # shared default cache should stay usable but unused by this smoke
    assert isinstance(DEFAULT_CACHE, WallStripCache)
    print("smoke_wallcache: ALL OK")


if __name__ == "__main__":
    main()