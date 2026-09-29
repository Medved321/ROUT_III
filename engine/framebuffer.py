"""Fixed-resolution RGB framebuffer backend (change ``total-performance``, D1).

The whole 3D scene is composed into one flat ``bytearray`` of ``IW*IH*3``
bytes and transferred to the screen once per frame with
``pygame.image.frombuffer`` + a single ``pygame.transform.scale``.  All fills
are batch operations (slice assignment, ``memoryview``, numpy views) — no
per-pixel Python loops and no per-column ``Surface`` allocations in the hot
path.
"""
from __future__ import annotations

import pygame

try:
    import numpy as _np

    _HAVE_NP = True
except Exception:  # pragma: no cover - numpy is auto-installed at launch
    _np = None
    _HAVE_NP = False


class Frame:
    """Flat RGB24 pixel buffer of a fixed internal size (independent of the
    screen resolution).

    Pixel layout is row-major: byte offset of ``(x, y)`` is
    ``(y * iw + x) * 3``.  All public write helpers clip to the buffer bounds
    and are expressed as bulk slice operations.
    """

    __slots__ = ("iw", "ih", "buf", "mv", "size", "_row0", "_pix",
                 "_surface", "_pixels3d")

    def __init__(self, iw: int, ih: int) -> None:
        self.iw = int(iw)
        self.ih = int(ih)
        self.size = (self.iw, self.ih)
        self.buf = bytearray(self.iw * self.ih * 3)
        self.mv = memoryview(self.buf)
        self._row0 = self.iw * 3
        self._pix = self._row0  # bytes per row
        self._surface = None
        self._pixels3d = None

    # -- bulk fills -------------------------------------------------------

    def clear(self, rgb=(0, 0, 0)) -> None:
        """Fill the whole buffer with a solid colour (one bulk operation)."""
        r, g, b = rgb
        if r == g == b:
            # fast path: repeated single byte
            self.buf[:] = bytes((r,)) * (self.iw * self.ih * 3)
        else:
            self.buf[:] = bytes((r, g, b)) * (self.iw * self.ih)

    def fill_rect(self, x: int, y: int, w: int, h: int, rgb) -> None:
        """Fill an axis-aligned rectangle with a solid colour."""
        iw, ih = self.iw, self.ih
        if w <= 0 or h <= 0:
            return
        x0 = 0 if x < 0 else x
        y0 = 0 if y < 0 else y
        x1 = iw if x + w > iw else x + w
        y1 = ih if y + h > ih else y + h
        if x0 >= x1 or y0 >= y1:
            return
        r, g, b = rgb
        row_bytes = (x1 - x0) * 3
        row = bytes((r, g, b)) * (x1 - x0)
        buf = self.buf
        for yy in range(y0, y1):
            off = yy * self._row0 + x0 * 3
            buf[off:off + row_bytes] = row

    def fill_row(self, y: int, rgb) -> None:
        """Fill an entire horizontal row (used for solid flat bands)."""
        if y < 0 or y >= self.ih:
            return
        r, g, b = rgb
        off = y * self._row0
        self.buf[off:off + self._row0] = bytes((r, g, b)) * self.iw

    # -- vertical strips (wall columns) -----------------------------------
    # Buffers are row-major, so a vertical strip is not contiguous: each row
    # contributes ``3`` bytes at offset ``row*row0 + x*3``.  Writes use one
    # strided bulk op per strip when numpy is available, else a per-row slice
    # loop (never a per-pixel Python loop).

    def blit_column(self, x: int, y: int, data, h: int) -> None:
        """Write a vertical strip of RGB bytes (``h`` pixels, ``h*3`` bytes)
        at ``(x, y)``, clipped to the buffer.  ``data`` is any buffer of
        ``h*3`` bytes (bytes/bytearray/memoryview)."""
        iw, ih = self.iw, self.ih
        if h <= 0 or x < 0 or x >= iw:
            return
        src_off = 0
        y0 = y
        if y0 < 0:
            src_off = -y0 * 3
            h += y0
            y0 = 0
        if y0 + h > ih:
            h = ih - y0
        if h <= 0:
            return
        src = memoryview(data)[src_off:src_off + h * 3]
        if _HAVE_NP:
            view = _np.frombuffer(self.buf, dtype=_np.uint8).reshape(
                ih, iw, 3)
            view[y0:y0 + h, x] = _np.frombuffer(
                src, dtype=_np.uint8).reshape(-1, 3)
            return
        dst_base = y0 * self._row0 + x * 3
        for r in range(h):
            off = dst_base + r * self._row0
            self.buf[off:off + 3] = src[r * 3:r * 3 + 3]

    def vspan_bytes(self, x: int, y0: int, y1: int, rgb) -> None:
        """Fill a vertical span of one column with a solid colour."""
        iw, ih = self.iw, self.ih
        if x < 0 or x >= iw:
            return
        if y0 < 0:
            y0 = 0
        if y1 > ih:
            y1 = ih
        if y0 >= y1:
            return
        r, g, b = rgb
        n = y1 - y0
        col = bytes((r, g, b)) * n
        dst_base = y0 * self._row0 + x * 3
        for r in range(n):
            off = dst_base + r * self._row0
            self.buf[off:off + 3] = col[r * 3:r * 3 + 3]

    # -- sprite / rect blit -----------------------------------------------

    def blit_mini(self, x: int, y: int, w: int, h: int, rgba) -> None:
        """Alpha-composite an RGBA mini-sprite into the buffer (task 2.3).

        ``rgba`` is ``w*h*4`` bytes; pixels with ``a=255`` take the sprite
        colour outright (slice copy), others blend with the current buffer —
        one bulk numpy op per sprite, clipped to the buffer bounds."""
        iw, ih = self.iw, self.ih
        if w <= 0 or h <= 0:
            return
        sx = 0
        sy = 0
        dw, dh = w, h
        if x < 0:
            sx = -x
            dw += x
            x = 0
        if y < 0:
            sy = -y
            dh += y
            y = 0
        if x + dw > iw:
            dw = iw - x
        if y + dh > ih:
            dh = ih - y
        if dw <= 0 or dh <= 0:
            return
        src = memoryview(rgba)
        src_row = w * 4
        if _HAVE_NP:
            view = _np.frombuffer(self.buf, dtype=_np.uint8).reshape(
                ih, iw, 3)
            sp = _np.frombuffer(src, dtype=_np.uint8).reshape(h, w, 4)
            sp = sp[sy:sy + dh, sx:sx + dw]
            dst = view[y:y + dh, x:x + dw]
            if _np.all(sp[..., 3] == 255):
                dst[:] = sp[..., :3]
                return
            a = sp[..., 3:4].astype(_np.float32) / 255.0
            rgb = sp[..., :3].astype(_np.float32)
            blended = (dst.astype(_np.float32) * (1.0 - a) + rgb * a)
            blended += 0.5
            dst[:] = _np.clip(blended, 0, 255).astype(_np.uint8)
            return
        buf = self.buf
        base = y * self._row0 + x * 3
        for row in range(dh):
            soff = (sy + row) * src_row + sx * 4
            doff = base + row * self._row0
            for px in range(dw):
                a = src[soff + px * 4 + 3]
                if a == 255:
                    off = doff + px * 3
                    buf[off] = src[soff + px * 4]
                    buf[off + 1] = src[soff + px * 4 + 1]
                    buf[off + 2] = src[soff + px * 4 + 2]
                elif a > 0:
                    f = a / 255.0
                    off = doff + px * 3
                    buf[off] = int(buf[off] * (1 - f) + src[soff + px * 4] * f)
                    buf[off + 1] = int(
                        buf[off + 1] * (1 - f) + src[soff + px * 4 + 1] * f)
                    buf[off + 2] = int(
                        buf[off + 2] * (1 - f) + src[soff + px * 4 + 2] * f)

    def blit_rgb(self, x: int, y: int, w: int, h: int, data) -> None:
        """Insert a packed RGB sub-rectangle ``data`` (``w*h*3`` bytes) at
        ``(x, y)``, clipped to the buffer — used for cached mini-sprites."""
        iw, ih = self.iw, self.ih
        if w <= 0 or h <= 0:
            return
        sx = 0
        sy = 0
        dw, dh = w, h
        if x < 0:
            sx = -x
            dw += x
            x = 0
        if y < 0:
            sy = -y
            dh += y
            y = 0
        if x + dw > iw:
            dw = iw - x
        if y + dh > ih:
            dh = ih - y
        if dw <= 0 or dh <= 0:
            return
        src = memoryview(data)
        row_bytes = dw * 3
        src_row = w * 3
        buf = self.buf
        base = y * self._row0 + x * 3
        for row in range(dh):
            soff = (sy + row) * src_row + sx * 3
            doff = base + row * self._row0
            buf[doff:doff + row_bytes] = src[soff:soff + row_bytes]

    # -- conversion -------------------------------------------------------

    def to_surface(self) -> pygame.Surface:
        """Build the frame ``Surface`` (one copy, no intermediate buffers)."""
        return pygame.image.frombuffer(self.buf, self.size, "RGB")

    def ensure_surface(self) -> pygame.Surface:
        """Persistent 32-bit surface over the frame pixels (Path B).

        Created once per resolution and reused between frames.  The buffer is
        synced one bulk copy at a time via ``surfarray.pixels3d`` in
        :meth:`draw_to` (the view is acquired and released on each call, so
        the surface stays blittable)."""
        if self._surface is None or self._surface.get_size() != self.size:
            self._surface = pygame.Surface(self.size)
            self._pixels3d = None
        return self._surface

    def draw_to(self, target, target_size=None, smooth: bool = False,
                direct: bool = True) -> None:
        """Transfer the buffer to ``target`` with one scale per frame.

        ``direct=False`` uses ``pygame.image.frombuffer`` (Path A, 24-bit
        frame surface, scale-then-blit); the default writes into a persistent
        32-bit surface pixel view (Path B) so the scale lands directly on a
        matching-format target with no extra intermediate surface."""
        size = target_size or target.get_size()
        if direct:
            surf = self.ensure_surface()
            if _HAVE_NP:
                arr = pygame.surfarray.pixels3d(surf)
                # surfarray is indexed [x][y] (width-first); the frame buffer
                # is row-major (y first) -> transposed bulk copy.
                arr[:] = _np.frombuffer(
                    self.buf, dtype=_np.uint8
                ).reshape(self.ih, self.iw, 3).transpose(1, 0, 2)
                del arr  # unlock surface so it can be blitted/scaled
            if target.get_size() != size:
                tmp = (pygame.transform.smoothscale(surf, size)
                       if smooth else pygame.transform.scale(surf, size))
                target.blit(tmp, (0, 0))
            elif size == self.size:
                target.blit(surf, (0, 0))
            elif smooth:
                pygame.transform.smoothscale(surf, size, target)
            else:
                pygame.transform.scale(surf, size, target)
        else:
            surf = self.to_surface()
            tmp = (pygame.transform.smoothscale(surf, size)
                   if smooth else pygame.transform.scale(surf, size))
            target.blit(tmp, (0, 0))

    # -- numpy view (optional) --------------------------------------------

    def np_view(self):
        """``(ih, iw, 3)`` uint8 numpy view over the buffer, or ``None`` if
        numpy is unavailable."""
        if not _HAVE_NP:
            return None
        return _np.frombuffer(self.buf, dtype=_np.uint8).reshape(
            self.ih, self.iw, 3)

    def resize(self, iw: int, ih: int) -> None:
        """Reallocate for a new internal resolution (quality-tier change)."""
        iw, ih = int(iw), int(ih)
        if iw == self.iw and ih == self.ih:
            return
        self.iw, self.ih = iw, ih
        self.size = (iw, ih)
        self.buf = bytearray(iw * ih * 3)
        self.mv = memoryview(self.buf)
        self._row0 = iw * 3
        self._pix = self._row0
        self._surface = None
        self._pixels3d = None

    @property
    def nbytes(self) -> int:
        return len(self.buf)


def upscale(src: pygame.Surface, dst: pygame.Surface, dst_size) -> None:
    """Single scale of the internal frame onto the screen surface."""
    if dst.get_size() == dst_size:
        dst.blit(src, (0, 0))
    else:
        pygame.transform.scale(src, dst_size, dst)


def depth_col(z_buf, sx: int, screen_w: int) -> int:
    """Index of the depth-buffer column that covers screen column ``sx``.

    A per-column depth buffer is a *different* grid from the screen: the tile
    renderer builds ``screen_w // SCALE`` columns, the sector renderer
    ``screen_w // gs.render_scale``, and the two scales are separate settings
    that the quality ladder moves independently.  So the grid must be read off
    the buffer itself -- ``len(z_buf)`` is the one number that cannot disagree
    with what was actually stored -- and never recomputed from a scale the
    consumer happens to know about.  Recomputing it is what let an out-of-range
    column through (``IndexError`` in the sprite pass) and, before that, silently
    tested the depth of a neighbouring column.

    Returns ``-1`` when the screen column has no depth column at all (empty
    buffer, ``sx`` off screen, degenerate width).  Callers must treat that as
    "not visible" rather than clamping it: a clamped index answers a question
    about a different part of the screen, so the sprite either vanishes or shows
    through a wall, and neither is worth the crash.
    """
    n = len(z_buf)
    if n <= 0 or screen_w <= 0:
        return -1
    if sx < 0 or sx >= screen_w:
        return -1
    col = sx * n // screen_w
    return col if col < n else n - 1
