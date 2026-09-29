"""Cached mini-sprites (change ``total-performance``, task 2.3).

World sprites (enemies, corpses, particles, projectiles — anything with a
(final RGBA buffer, light level, distance bucket) identity) are pre-rendered
once per *light bucket × distance bucket* and inserted into the framebuffer
with a slice write (:meth:`~engine.framebuffer.Frame.blit_mini`) — no
per-frame ``Surface`` scaling or alphablit in the hot path.

Keys are caller-supplied opaque tuples ``(kind, type_id, light, bucket,
variant)``; the caller provides a ``build`` callback that returns the
``pygame.Surface`` (SRCALPHA) for the *unscaled* sprite, and the cache
scales it to the bucket's size and bakes the light before storing the RGBA
bytes.  The cache is lazy and bounded by a byte budget with LRU eviction
(shared pattern with :mod:`engine.wallcache`).

Sprites are not all square: ``aspect`` is the width/height ratio of the type
(bosses and arachnotrons are wide, floaters are round), so ``mini`` returns
``(w, h, rgba)`` with ``h`` = bucket size and ``w = round(h * aspect)``.
"""
from __future__ import annotations

import math
from collections import OrderedDict

import pygame

try:
    import numpy as _np

    _HAVE_NP = True
except Exception:  # pragma: no cover - numpy is mandatory on the client
    _np = None
    _HAVE_NP = False

from engine.wallcache import LIGHT_STEPS, bucket_edges

#: Number of billboard views an enemy is drawn from, 45 degrees apart.
VIEWS = 8
_VIEW_STEP = 2.0 * math.pi / VIEWS


def view_index(bearing: float) -> int:
    """Which of the :data:`VIEWS` views to draw for a bearing, 0..7.

    0 is the enemy facing the player, 2 is a profile, 4 is its back.  The
    ``step/2`` bias is what makes this "nearest sector" rather than "floor":
    the view changes only when the bearing crosses a 45 degree boundary, so
    walking past an enemy does not make its model flicker every frame.

    It lives here, next to the rest of "how a cached sprite is identified",
    because the engine picks the view (it has the angles) and the client
    builds the surface -- one implementation, two importers, and no
    engine -> client import.
    """
    return int((float(bearing) + _VIEW_STEP * 0.5) / _VIEW_STEP) % VIEWS


def size_for_bucket(edges, k: int, base: int, max_h: int = 256) -> int:
    """Mini-sprite dimension for a distance bucket: inversely proportional to
    the bucket's near-edge distance (like wall strip heights)."""
    near = max(edges[k], 1.0)
    size = max(1, min(max_h, int(base * 750.0 / near)))
    return size


class SpriteCache:
    """Bounded, lazy cache of RGBA mini-sprites keyed by (kind, type,
    light bucket, distance bucket, variant)."""

    __slots__ = ("max_bytes", "_items", "_bytes", "hits", "misses",
                 "_edges")

    def __init__(self, max_bytes: int = 8 * 1024 * 1024) -> None:
        self.max_bytes = int(max_bytes)
        # OrderedDict doubles as the LRU list: move_to_end on a hit,
        # popitem(last=False) on evict -- O(1) instead of the O(n) list.remove
        # a parallel list needed.
        self._items = OrderedDict()   # key -> (w, h, rgba bytes), MRU last
        self._bytes = 0
        self.hits = 0
        self.misses = 0
        self._edges = bucket_edges(900.0)

    def set_view(self, view: float) -> None:
        edges = bucket_edges(float(view))
        if edges == self._edges:
            return
        self._edges = edges
        self.clear()

    def clear(self) -> None:
        """Drop every entry.  Called on a view change and on a map change:
        entries carry a distance bucket, so they are only valid for the
        bucket table they were built with."""
        self._items.clear()
        self._bytes = 0

    def _evict(self) -> None:
        while self._bytes > self.max_bytes and self._items:
            _, item = self._items.popitem(last=False)
            self._bytes -= len(item[2])

    def mini(self, key, build, bucket: int, light: int,
             base_h: int, max_h: int = 256, aspect: float = 1.0):
        """Cached ``(w, h, rgba bytes)`` mini-sprite for ``key`` at the given
        distance/light buckets.

        ``build()`` returns an unscaled SRCALPHA ``Surface``; it is scaled to
        the bucket's height (``w = round(h * aspect)``) and multiplied by the
        light level once, then stored.  On a cache hit the stored byte blob is
        returned as-is -- the caller inserts it with ``frame.blit_mini`` (one
        slice write).  ``aspect`` needs no place in the key: it is a property
        of the type, and the type is already part of it."""
        if not isinstance(key, tuple):
            key = (key,)
        key = key + (light, bucket)
        item = self._items.get(key)
        if item is not None:
            self.hits += 1
            self._items.move_to_end(key)
            return item
        self.misses += 1
        surf = build()
        h = size_for_bucket(self._edges, bucket, base_h, max_h)
        w = max(1, int(round(h * aspect)))
        w = min(w, max_h)
        if surf.get_width() != w or surf.get_height() != h:
            surf = pygame.transform.scale(surf, (w, h))
        m = (light + 0.5) / LIGHT_STEPS
        if m < 1.0 and _HAVE_NP:
            lit = _np.frombuffer(pygame.image.tobytes(surf, "RGBA"),
                                 dtype=_np.uint8).reshape(
                h, w, 4).astype(_np.float32)
            lit[..., :3] *= m
            lit = _np.clip(lit, 0, 255).astype(_np.uint8)
            data = lit.tobytes()
        elif m < 1.0:
            src = pygame.image.tobytes(surf, "RGBA")
            out = bytearray(len(src))
            for i in range(0, len(src), 4):
                out[i] = int(src[i] * m)
                out[i + 1] = int(src[i + 1] * m)
                out[i + 2] = int(src[i + 2] * m)
                out[i + 3] = src[i + 3]
            data = bytes(out)
        else:
            data = pygame.image.tobytes(surf, "RGBA")
        self._bytes += len(data)
        self._items[key] = (w, h, data)
        self._evict()
        return self._items[key]

    def stats(self) -> dict:
        return {
            "hits": self.hits, "misses": self.misses,
            "bytes": self._bytes, "max_bytes": self.max_bytes,
            "items": len(self._items),
        }


DEFAULT_SPRITE_CACHE = SpriteCache()


def set_view_dist(view: float) -> None:
    """Point the default cache at a view distance.

    Distance buckets are derived from it, so a change invalidates every entry:
    the cache drops them rather than serving sizes computed for the old range.
    """
    DEFAULT_SPRITE_CACHE.set_view(view)