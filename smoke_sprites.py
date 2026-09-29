"""Smoke: cached mini-sprites (total-performance task 2.3).

World sprites are pre-rendered per (type, light bucket, distance bucket) into
RGBA byte blobs and inserted into the framebuffer with one slice write
(``Frame.blit_mini``).  The cache is lazy, LRU-bounded, and its output is
compared against a no-cache reference render for a couple of buckets.

Run: ``py -3.12 smoke_sprites.py``.
"""
from __future__ import annotations

import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pygame  # noqa: E402

from engine.framebuffer import Frame  # noqa: E402
from engine.sprite_cache import (  # noqa: E402
    DEFAULT_SPRITE_CACHE, SpriteCache, size_for_bucket,
)
from engine.wallcache import (  # noqa: E402
    LIGHT_STEPS, bucket_edges,
)


def _build_enemy() -> pygame.Surface:
    s = pygame.Surface((32, 32), pygame.SRCALPHA)
    s.fill((0, 0, 0, 0))
    pygame.draw.circle(s, (180, 80, 40, 255), (16, 14), 10)
    pygame.draw.rect(s, (120, 40, 20, 230), (12, 20, 8, 8))
    return s


def _ref_render(bucket, light, base_h, max_h=256, aspect=1.0):
    """No-cache reference: scale SRCALPHA surface to the bucket size, bake
    the light level, pack RGBA bytes."""
    h = size_for_bucket(bucket_edges(900.0), bucket, base_h, max_h)
    w = max(1, int(round(h * aspect)))
    w = min(w, max_h)
    surf = _build_enemy()
    if surf.get_width() != w or surf.get_height() != h:
        surf = pygame.transform.scale(surf, (w, h))
    raw = pygame.image.tobytes(surf, "RGBA")
    m = (light + 0.5) / LIGHT_STEPS
    out = bytearray(len(raw))
    for i in range(0, len(raw), 4):
        out[i] = int(raw[i] * m)
        out[i + 1] = int(raw[i + 1] * m)
        out[i + 2] = int(raw[i + 2] * m)
        out[i + 3] = raw[i + 3]
    return bytes(out)


def test_cached_vs_uncached_reference():
    cache = SpriteCache()
    for bucket, light in ((0, 15), (8, 8), (15, 1)):
        w, h, data = cache.mini(("enemy", "imp"), _build_enemy, bucket, light,
                                base_h=56)
        ref = _ref_render(bucket, light, 56)
        assert data == ref, (bucket, light)
        assert (w, h) == (size_for_bucket(bucket_edges(900.0), bucket, 56),
                          size_for_bucket(bucket_edges(900.0), bucket, 56))
    assert cache.misses == 3
    print("  cached == no-cache reference  OK")


def max_h_of(bucket, base_h, max_h=256):
    """Width ceiling for a bucket: the same cap ``mini()`` applies."""
    return max_h


def test_non_square_aspect():
    """A wide boss must not be letterboxed into a square (task 3.1).

    The invariant that actually matters is the byte count: a blob that is not
    exactly ``w*h*4`` would be sliced into the framebuffer at the wrong stride,
    so that is what is asserted, together with the ratio.
    """
    cache = SpriteCache()
    # A different type per aspect, because ``aspect`` is deliberately *not*
    # part of the key: it is a property of the type, and the type is in the
    # key already.  Reusing one id across aspects would (correctly) hit the
    # first entry.
    for aspect, etype in ((0.5, "cacodemon"), (1.0, "imp"), (1.2, "mancubus")):
        w, h, data = cache.mini(("enemy", etype), _build_enemy, 3, 10,
                                base_h=56, aspect=aspect)
        assert w * h * 4 == len(data), (aspect, w, h, len(data))
        assert w == min(max_h_of(3, 56), max(1, int(round(h * aspect)))), \
            (aspect, w, h)
        assert data == _ref_render(3, 10, 56, aspect=aspect)
    # The default is square, so an existing caller is unaffected.
    w, h, data = cache.mini(("enemy", "imp"), _build_enemy, 4, 10, base_h=56)
    assert w == h and w * h * 4 == len(data)
    print("  non-square aspect + strides  OK")


def test_lru_eviction_order():
    """Least-recently-*used* goes first, not insertion order (task 3.2)."""
    built = []

    def _builder():
        built.append(1)
        return _build_enemy()

    # One entry's worth of budget at this size/light, so a second entry
    # evicts the first.
    _, h0, blob = SpriteCache().mini(("x",), _builder, 15, 15, 56)
    per = len(blob)
    cache = SpriteCache(max_bytes=per * 2)
    cache.mini(("a",), _builder, 15, 15, 56)
    cache.mini(("b",), _builder, 15, 15, 56)
    assert len(cache._items) == 2
    # Touch 'a' so 'b' becomes the least recently used.
    cache.mini(("a",), _builder, 15, 15, 56)
    assert cache.hits == 1
    cache.mini(("c",), _builder, 15, 15, 56)
    keys = set(cache._items)
    assert ("a", 15, 15) in keys, "recently used entry was evicted"
    assert ("b", 15, 15) not in keys, "least recently used entry survived"
    assert len(keys) == 2 and cache.stats()["bytes"] <= cache.max_bytes
    print("  LRU evicts by recency        OK")


def test_clear_and_set_view():
    """A distance-bucket change must not leave stale sizes behind (3.3/3.4)."""
    cache = SpriteCache()
    for b in range(0, 8):
        cache.mini(("enemy", "imp"), _build_enemy, b, 8, 56)
    before = cache.stats()
    assert before["items"] == 8 and before["bytes"] > 0
    cache.clear()
    after = cache.stats()
    assert after["bytes"] == 0 and after["items"] == 0
    # A view change re-buckets, so the entries built for the old range go.
    m_before = cache.misses
    for b in range(0, 8):
        cache.mini(("enemy", "imp"), _build_enemy, b, 8, 56)
    assert cache.misses == m_before + 8, "a cleared cache must rebuild"
    cache.set_view(450.0)
    assert cache.stats()["items"] == 0, cache.stats()
    cache.set_view(450.0)   # same value: no-op, stays empty
    assert cache.stats()["items"] == 0
    m_before = cache.misses
    cache.mini(("enemy", "imp"), _build_enemy, 0, 8, 56)
    assert cache.stats()["items"] == 1, "entries rebuilt after the change"
    assert cache.misses == m_before + 1
    print("  clear() + set_view lifecycle  OK")


def test_slice_insert_into_frame():
    frame = Frame(48, 40)
    frame.clear((10, 20, 30))
    base = frame.buf[:]
    # Opaque mini-sprite -> pure slice copy (exact bytes).
    size = 8
    op = bytearray(size * size * 4)
    for i in range(size * size):
        op[i * 4] = 200
        op[i * 4 + 1] = 100
        op[i * 4 + 2] = 50
        op[i * 4 + 3] = 255
    frame.blit_mini(5, 7, size, size, bytes(op))
    assert frame.buf[7 * frame._row0 + 5 * 3] == 200
    assert frame.buf[7 * frame._row0 + 5 * 3 + 1] == 100
    assert frame.buf[7 * frame._row0 + 5 * 3 + 2] == 50
    assert frame.buf[1 * 3 + 1] == 20  # untouched pixel
    # Semi-transparent: blended with the buffer, within 1 of the reference.
    alpha_blob = bytearray(size * size * 4)
    for i in range(size * size):
        alpha_blob[i * 4] = 0
        alpha_blob[i * 4 + 1] = 128
        alpha_blob[i * 4 + 2] = 255
        alpha_blob[i * 4 + 3] = 128
    frame.clear((10, 20, 30))
    frame.blit_mini(0, 0, size, size, bytes(alpha_blob))
    r, g, b = frame.buf[3], frame.buf[3 + 1], frame.buf[3 + 2]
    ref_r = int(10 * 0.5 + 0 * 0.5 + 0.5)
    ref_g = int(20 * 0.5 + 128 * 0.5 + 0.5)
    ref_b = int(30 * 0.5 + 255 * 0.5 + 0.5)
    assert abs(r - ref_r) <= 1 and abs(g - ref_g) <= 1 and abs(b - ref_b) <= 1
    # Clip: fully off-buffer is a no-op.
    n = len(frame.buf)
    frame.blit_mini(200, 200, size, size, bytes(alpha_blob))
    assert len(frame.buf) == n
    print("  slice insert + blending       OK")


def test_repeat_hit_and_memory():
    cache = SpriteCache(max_bytes=64 * 1024)
    cache.mini(("corpse", "imp"), lambda: _build_enemy(), 15, 6, 56)
    m1 = cache.misses
    cache.mini(("corpse", "imp"), lambda: _build_enemy(), 15, 6, 56)
    assert cache.misses == m1   # second request is a pure hit
    for b in range(12, 16):
        cache.mini(("particle", "fire"), _build_enemy, b, 15, 56)
    stats = cache.stats()
    assert stats["bytes"] <= cache.max_bytes, stats
    print("  hits + LRU byte budget        OK")


def main():
    pygame.init()
    test_cached_vs_uncached_reference()
    test_non_square_aspect()
    test_slice_insert_into_frame()
    test_repeat_hit_and_memory()
    test_lru_eviction_order()
    test_clear_and_set_view()
    assert isinstance(DEFAULT_SPRITE_CACHE, SpriteCache)
    print("smoke_sprites: ALL OK")


if __name__ == "__main__":
    main()