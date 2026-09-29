"""Unit checks for ``engine.framebuffer`` (total-performance, task 1.1).

Writes patterns through the ``Frame`` bulk API and verifies every pixel
against a pure-Python reference buffer.  Run: ``py -3.12 smoke_framebuffer.py``.
"""
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import pygame  # noqa: E402

from engine.framebuffer import Frame  # noqa: E402

IW, IH = 32, 24


class Ref:
    """Pure-python reference: list of (r, g, b) per pixel."""

    def __init__(self, w, h, rgb=(0, 0, 0)):
        self.w, self.h = w, h
        self.px = [rgb] * (w * h)

    def set(self, x, y, rgb):
        if 0 <= x < self.w and 0 <= y < self.h:
            self.px[y * self.w + x] = tuple(rgb)

    def get(self, x, y):
        return self.px[y * self.w + x]

    def fill_rect(self, x, y, w, h, rgb):
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                self.set(xx, yy, rgb)

    def blit_rgb(self, x, y, w, h, data):
        for row in range(h):
            for col in range(w):
                off = (row * w + col) * 3
                self.set(x + col, y + row, (data[off], data[off + 1],
                                            data[off + 2]))


def frame_get(f: Frame, x, y):
    off = (y * f.iw + x) * 3
    return (f.buf[off], f.buf[off + 1], f.buf[off + 2])


def check(name, f: Frame, ref: Ref):
    assert f.iw == ref.w and f.ih == ref.h, f"size mismatch {name}"
    for y in range(ref.h):
        for x in range(ref.w):
            got = frame_get(f, x, y)
            want = ref.get(x, y)
            assert got == want, (
                f"{name}: pixel ({x},{y}) = {got}, expected {want}")
    print(f"  ok  {name} ({ref.w}x{ref.h})")


def main():
    pygame.init()
    pygame.display.set_mode((64, 48))

    # --- clear / fill_rect ------------------------------------------------
    f = Frame(IW, IH)
    ref = Ref(IW, IH)
    f.clear((10, 20, 30))
    ref.fill_rect(0, 0, IW, IH, (10, 20, 30))
    check("clear", f, ref)

    f.fill_rect(4, 3, 8, 6, (200, 100, 50))
    ref.fill_rect(4, 3, 8, 6, (200, 100, 50))
    check("fill_rect interior", f, ref)

    # clipping: partially off-buffer rects
    f.fill_rect(-3, -2, 6, 5, (1, 2, 3))
    ref.fill_rect(-3, -2, 6, 5, (1, 2, 3))
    f.fill_rect(IW - 3, IH - 2, 10, 10, (9, 8, 7))
    ref.fill_rect(IW - 3, IH - 2, 10, 10, (9, 8, 7))
    f.fill_rect(-100, 5, 5, 5, (55, 66, 77))   # fully left -> no-op
    check("fill_rect clip", f, ref)

    # --- fill_row ---------------------------------------------------------
    f.fill_row(10, (123, 45, 67))
    ref.fill_rect(0, 10, IW, 1, (123, 45, 67))
    f.fill_row(-1, (1, 1, 1))
    f.fill_row(IH, (1, 1, 1))
    check("fill_row", f, ref)

    # --- blit_column ------------------------------------------------------
    f = Frame(IW, IH)
    ref = Ref(IW, IH)
    col = bytes()
    for y in range(IH):
        col += bytes(((y * 3) % 256, (y * 5) % 256, (y * 7) % 256))
    f.blit_column(7, 0, col, IH)
    for y in range(IH):
        ref.set(7, y, ((y * 3) % 256, (y * 5) % 256, (y * 7) % 256))
    check("blit_column full", f, ref)

    # partial column with negative offset (clip top)
    col8 = bytes(range(24))  # 8 pixels
    f.blit_column(9, -3, col8, 8)
    # pixels y=0..4 come from source rows 3..7
    for y in range(0, 5):
        off = (y + 3) * 3
        ref.set(9, y, (col8[off], col8[off + 1], col8[off + 2]))
    check("blit_column clip-top", f, ref)

    # clip bottom
    f.blit_column(11, IH - 3, col8, 8)
    for i in range(3):
        off = i * 3
        ref.set(11, IH - 3 + i, (col8[off], col8[off + 1], col8[off + 2]))
    check("blit_column clip-bottom", f, ref)

    # out of bounds x -> no-op
    f.blit_column(-1, 0, col8, 8)
    f.blit_column(IW, 0, col8, 8)
    check("blit_column oob x no-op", f, ref)

    # --- vspan_bytes ------------------------------------------------------
    f.vspan_bytes(15, 2, 9, (240, 241, 242))
    for y in range(2, 9):
        ref.set(15, y, (240, 241, 242))
    f.vspan_bytes(16, -5, 3, (11, 22, 33))   # clip top
    for y in range(0, 3):
        ref.set(16, y, (11, 22, 33))
    check("vspan_bytes", f, ref)

    # --- blit_rgb (mini-sprite) ------------------------------------------
    f = Frame(IW, IH)
    ref = Ref(IW, IH)
    f.clear((0, 0, 0))
    sw, sh = 6, 5
    spr = b"".join(bytes(((c * 20) % 256, (c * 40) % 256, (c * 60) % 256))
                   for c in range(sw * sh))
    f.blit_rgb(10, 8, sw, sh, spr)
    ref.blit_rgb(10, 8, sw, sh, spr)
    check("blit_rgb interior", f, ref)

    # clipped sprite (partially off right/bottom)
    f.blit_rgb(IW - 3, IH - 2, sw, sh, spr)
    ref.blit_rgb(IW - 3, IH - 2, sw, sh, spr)
    check("blit_rgb clip-br", f, ref)

    # clipped sprite (partially off left/top)
    f.blit_rgb(-2, -3, sw, sh, spr)
    ref.blit_rgb(-2, -3, sw, sh, spr)
    check("blit_rgb clip-tl", f, ref)

    # fully off -> no-op
    f.blit_rgb(-100, -100, sw, sh, spr)
    f.blit_rgb(IW + 1, 0, sw, sh, spr)
    check("blit_rgb oob no-op", f, ref)

    # --- to_surface -------------------------------------------------------
    f = Frame(4, 3)
    f.clear((255, 0, 128))
    surf = f.to_surface()
    assert surf.get_size() == (4, 3), surf.get_size()
    assert surf.get_at((0, 0))[:3] == (255, 0, 128)
    assert surf.get_at((3, 2))[:3] == (255, 0, 128)
    print("  ok  to_surface")

    # --- draw_to both paths ------------------------------------------------
    screen = pygame.display.get_surface()
    f = Frame(4, 3)
    f.clear((10, 20, 30))
    scaled = pygame.Surface((8, 6))
    f.draw_to(scaled, (8, 6), direct=False)      # Path A: frombuffer + scale
    assert scaled.get_at((0, 0))[:3] == (10, 20, 30), scaled.get_at((0, 0))
    assert scaled.get_at((7, 5))[:3] == (10, 20, 30), scaled.get_at((7, 5))
    f.draw_to(scaled, (8, 6), direct=True)       # Path B: persistent pixels view
    assert scaled.get_at((0, 0))[:3] == (10, 20, 30)
    assert scaled.get_at((7, 5))[:3] == (10, 20, 30)
    # same-size blit (no scale)
    small = pygame.Surface((4, 3))
    f.clear((55, 66, 77))
    f.draw_to(small, direct=False)
    assert small.get_at((3, 2))[:3] == (55, 66, 77)
    f.draw_to(small, direct=True)
    assert small.get_at((3, 2))[:3] == (55, 66, 77)
    # non-square orientation: left half red, right half blue (Path B) --
    # guards against an x/y transposition when writing the pixels view.
    f = Frame(4, 3)
    f.clear((0, 0, 255))
    f.fill_rect(0, 0, 2, 3, (255, 0, 0))
    f.draw_to(scaled, (8, 6), direct=True)
    assert scaled.get_at((1, 3))[:3] == (255, 0, 0), scaled.get_at((1, 3))
    assert scaled.get_at((6, 3))[:3] == (0, 0, 255), scaled.get_at((6, 3))
    f.draw_to(scaled, (8, 6), direct=False)
    assert scaled.get_at((1, 3))[:3] == (255, 0, 0), scaled.get_at((1, 3))
    assert scaled.get_at((6, 3))[:3] == (0, 0, 255), scaled.get_at((6, 3))
    print("  ok  draw_to (Path A + Path B, scale + same-size)")

    # --- resize -----------------------------------------------------------
    f.resize(8, 6)
    assert f.size == (8, 6) and len(f.buf) == 8 * 6 * 3
    f.fill_rect(0, 0, 8, 6, (5, 5, 5))
    assert frame_get(f, 7, 5) == (5, 5, 5)
    f.resize(8, 6)  # no-op
    assert len(f.buf) == 8 * 6 * 3
    print("  ok  resize")

    # --- np view (optional) ----------------------------------------------
    try:
        import numpy as np
        f = Frame(4, 2)
        f.clear((7, 8, 9))
        v = f.np_view()
        assert v.shape == (2, 4, 3)
        assert tuple(v[1, 3]) == (7, 8, 9)
        v[0, 0] = (1, 2, 3)
        assert frame_get(f, 0, 0) == (1, 2, 3)
        print("  ok  np_view (numpy present)")
    except ImportError:
        assert f.np_view() is None
        print("  ok  np_view (numpy absent -> None)")

    pygame.quit()
    print("smoke_framebuffer: ALL OK")


if __name__ == "__main__":
    main()
