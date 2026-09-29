"""Pre-lit wall column strips (change ``total-performance``, tasks 2.1-2.2).

Task 2.1 (this module, level 1): every wall texture is pre-lit into 16
discrete light levels and stored in **column-major** byte form, so a 1-pixel
wide wall column is a single contiguous slice of ``TEX_W*3`` bytes — the hot
path picks a column by ``(surface, light level)`` and vertical-samples it
with one bulk copy instead of per-pixel tinting.

Task 2.2 (level 2): full strips keyed by ``(surface, light level, distance
bucket)``.  Distance buckets form a geometric ladder so a bucket's strip
height roughly matches the projected wall height in the frame; each stored
strip holds all 64 columns pre-scaled to ``H_bucket``.  The cache is lazy and
bounded by a byte budget with LRU eviction (never recreated per frame).

Textures are keyed by ``id(surface)`` — surfaceless access via a cached
per-pixel SNES reference is unnecessary since renderers always hold their
surfaces.
"""
from __future__ import annotations

try:
    import numpy as np

    _HAVE_NP = True
except Exception:  # pragma: no cover - numpy is mandatory on the client
    np = None
    _HAVE_NP = False

from engine.constants import TEX_W, TILE

FOCAL = 750.0
LIGHT_STEPS = 16
BUCKETS = 16
TEX_PIXELS = TEX_W * TEX_W * 3


def light_level(br: float, scale: float = 255.0) -> int:
    """Brightness in ``[0, scale]`` -> discrete light level in ``[0, 15]``."""
    if br <= 0.0:
        return 0
    if br >= scale:
        return LIGHT_STEPS - 1
    return int(br * LIGHT_STEPS / scale)


def tint3(r: int, g: int, b: int, level: int, steps: int = LIGHT_STEPS):
    """Per-pixel tint reference: ``int(v * m)`` with ``m=(level+0.5)/steps``."""
    m = (level + 0.5) / steps
    return int(r * m), int(g * m), int(b * m)


def _buckets_for(view: float) -> list:
    """Geometric distance bucket edges ``[d_0, d_1, ..., d_BUCKETS]``.

    ``d_k = d_min * (view / d_min) ** (k / BUCKETS)`` so each bucket occupies
    an equal share of the log-depth range (D3)."""
    lo = max(16.0, float(view) * 0.01)
    hi = max(float(view), lo + 1.0)
    return [lo * (hi / lo) ** (k / BUCKETS) for k in range(BUCKETS + 1)]


def bucket_edges(view: float) -> list:
    """Public distance-bucket edges for a view distance (D3)."""
    return _buckets_for(view)


def bucket_for_dist(d: float, edges) -> int:
    """Index of the geometric bucket containing distance ``d`` (``[0, 15]``)."""
    for k in range(BUCKETS - 1, -1, -1):
        if d >= edges[k]:
            return k
    return 0


def strip_height_for_bucket(edges, k: int, max_h: int) -> int:
    """Tallest projected wall height inside bucket ``k`` (near edge distance)."""
    return max(1, min(max_h, int(TILE * FOCAL / max(edges[k], 1.0))))


class WallStripCache:
    """Lazy, bounded cache of lit wall columns and distance-bucket strips."""

    __slots__ = ("max_bytes", "_tex", "_lit", "_strips", "_lru", "_bytes",
                 "hits", "misses", "_bucket_edges", "_bucket_h", "_view")

    def __init__(self, max_bytes: int = 6 * 1024 * 1024) -> None:
        self.max_bytes = int(max_bytes)
        self._tex = {}      # id(surface) -> row-major RGB bytes
        self._lit = {}      # (id, level) -> column-major lit bytes
        self._strips = {}   # (id, level, bucket) -> column-major strip bytes
        self._lru = []      # LRU order of strip keys
        self._bytes = 0
        self.hits = 0
        self.misses = 0
        self._view = FOCAL * 1.2
        self._bucket_edges = _buckets_for(self._view)
        self._bucket_h = [strip_height_for_bucket(self._bucket_edges, k, 512)
                          for k in range(BUCKETS)]

    # ── registration ------------------------------------------------

    def texture(self, tex, name: str | None = None) -> int:
        """Register a texture surface once; row-major RGB bytes are kept."""
        import pygame

        tid = id(tex)
        if tid in self._tex:
            return tid
        try:
            w, h = tex.get_size()
            if w != TEX_W or h != TEX_W:
                tex = pygame.transform.scale(tex, (TEX_W, TEX_W))
            data = pygame.image.tobytes(tex, "RGB")
        except Exception:
            data = bytes((90, 90, 90)) * TEX_PIXELS
        self._tex[tid] = data
        return tid

    def drop_texture(self, tex) -> None:
        tid = id(tex)
        if tid not in self._tex:
            return
        self._tex.pop(tid, None)
        for k in [kk for kk in self._lit if kk[0] == tid]:
            self._lit.pop(k)
        for k in [kk for kk in self._strips if kk[0] == tid]:
            self._bytes -= len(self._strips.pop(k))
        self._lru = [k for k in self._lru if k[0] != tid]

    def clear_tex(self) -> None:
        self._tex.clear()
        self._lit.clear()
        self._strips.clear()
        self._lru.clear()
        self._bytes = 0

    # ── level 1: lit columns (task 2.1) ----------------------------

    def _column_major(self, row_major: bytes) -> bytes:
        """Transpose row-major 64x64 RGB into column-major (64 columns of
        64*3 contiguous bytes) so a column is one slice."""
        if _HAVE_NP:
            arr = np.frombuffer(row_major, dtype=np.uint8).reshape(
                TEX_W, TEX_W, 3)
            return arr.transpose(1, 0, 2).tobytes()
        out = bytearray(TEX_PIXELS)
        for x in range(TEX_W):
            for y in range(TEX_W):
                s = (y * TEX_W + x) * 3
                d = (x * TEX_W + y) * 3
                out[d:d + 3] = row_major[s:s + 3]
        return bytes(out)

    def lit(self, tex, level: int) -> bytes | None:
        """Column-major lit bytes for (surface, level); all 64 columns."""
        tid = id(tex)
        key = (tid, level)
        data = self._lit.get(key)
        if data is not None:
            self.hits += 1
            return data
        self.misses += 1
        if tid not in self._tex:
            self.texture(tex)
        base = self._tex.get(tid)
        if base is None:
            return None
        base = self._column_major(base)
        m = (level + 0.5) / LIGHT_STEPS
        if _HAVE_NP:
            a = np.frombuffer(base, dtype=np.uint8)
            lit = (a.astype(np.float32) * m).astype(np.uint8)
            data = lit.tobytes()
        else:
            lit = bytearray(len(base))
            for i, v in enumerate(base):
                lit[i] = _mult_rgb(v, m)
            data = bytes(lit)
        # 64*64*3 bytes per (texture, level) — bounded by LIGHT_STEPS x
        # textures and never evicted; the byte budget governs strips only.
        self._lit[key] = data
        return data

    def column(self, tex, level: int, tex_x: int) -> bytes | None:
        """One wall column (``TEX_W*3`` bytes) of a lit texture."""
        data = self.lit(tex, level)
        if data is None:
            return None
        x = tex_x % TEX_W
        return data[x * TEX_W * 3:(x + 1) * TEX_W * 3]

    # ── level 2: distance-bucket strips (task 2.2) ------------------

    def set_view(self, view: float, max_h: int = 512) -> None:
        view = float(view)
        if abs(view - self._view) < 1e-4:
            return
        self._view = view
        self._bucket_edges = _buckets_for(view)
        self._bucket_h = [strip_height_for_bucket(self._bucket_edges, k, max_h)
                          for k in range(BUCKETS)]
        # Bucket heights changed: existing strips no longer match the bucket
        # geometry (view changes are rare — settings), so drop them.
        self._strips.clear()
        self._lru.clear()
        self._bytes = 0

    def bucket_heights(self) -> list:
        return list(self._bucket_h)

    def _bump(self, key) -> None:
        try:
            self._lru.remove(key)
        except ValueError:
            pass
        self._lru.append(key)

    def _evict(self) -> None:
        while self._bytes > self.max_bytes and self._lru:
            key = self._lru.pop(0)
            data = self._strips.pop(key, None)
            if data is not None:
                self._bytes -= len(data)

    def strip(self, tex, light: int, bucket: int) -> bytes | None:
        """Full 64-column strip for (surface, light, distance bucket) with all
        columns pre-scaled to the bucket height."""
        bucket = max(0, min(BUCKETS - 1, int(bucket)))
        light = max(0, min(LIGHT_STEPS - 1, int(light)))
        tid = id(tex)
        key = (tid, light, bucket)
        data = self._strips.get(key)
        if data is not None:
            self.hits += 1
            self._bump(key)
            return data
        self.misses += 1
        col = self.lit(tex, light)
        if col is None:
            return None
        h = self._bucket_h[bucket]
        if _HAVE_NP:
            arr = np.frombuffer(col, dtype=np.uint8).reshape(TEX_W, TEX_W, 3)
            arr = arr.transpose(1, 0, 2)          # (y, x, 3) column-major
            idx = (np.arange(h, dtype=np.int64) * (TEX_W - 1)) // max(1, h - 1)
            scaled = arr[idx].transpose(1, 0, 2)  # (x, h, 3) column-major
            data = scaled.tobytes()
        else:
            out = bytearray(TEX_W * h * 3)
            for x in range(TEX_W):
                for yy in range(h):
                    sy = yy * (TEX_W - 1) // max(1, h - 1)
                    s = (x * TEX_W + sy) * 3
                    d = (x * h + yy) * 3
                    out[d:d + 3] = col[s:s + 3]
            data = bytes(out)
        self._bytes += len(data)
        self._strips[key] = data
        self._lru.append(key)
        self._evict()
        return data

    def strip_column(self, tex, light: int, bucket: int, tex_x: int):
        """Contiguous bytes of one column of a bucket strip, or ``None``."""
        data = self.strip(tex, light, bucket)
        if data is None:
            return None
        x = tex_x % TEX_W
        h = self._bucket_h[bucket]
        return data[x * h * 3:(x + 1) * h * 3]

    # ── diagnostics -------------------------------------------------

    def stats(self) -> dict:
        return {
            "hits": self.hits, "misses": self.misses,
            "bytes": self._bytes, "max_bytes": self.max_bytes,
            "columns": len(self._lit), "strips": len(self._strips),
        }


def _mult_rgb(v: float, m: float) -> int:
    return int(v * m)


# Shared instance (renderers may use their own bounded instances instead).
DEFAULT_CACHE = WallStripCache()