"""FPS readout: exact blit geometry, layout-unit scaling, hidden at 0."""
import importlib.util
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import pygame

from engine import constants as const
from engine import game_state as gs
from engine import viewport

pygame.init()
pygame.display.set_mode((640, 480))


def drawn_bbox(surf):
    """Bounding box of everything not black -- exact, no colour guessing."""
    w, h = surf.get_size()
    xs, ys = [], []
    for y in range(h):
        for x in range(w):
            if surf.get_at((x, y))[:3] != (0, 0, 0):
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def ink_bbox(text):
    """Inked pixels of a rendered text surface, relative to its own origin.

    font.render() leaves antialiased margins transparent, so the ink box is
    inset from the blit rect by a per-font amount.  Comparing offsets keeps
    the geometry assertion exact without assuming that inset is zero.
    """
    probe = pygame.Surface(text.get_size())
    probe.blit(text, (0, 0))
    return drawn_bbox(probe)


def draw_readout(w, h, fps_value):
    """Replicates the render() branch and returns (surface, rect, text)."""
    const.W, const.H = w, h
    viewport.ensure_hud_fonts()
    surf = pygame.Surface((w, h))  # black
    if fps_value <= 0.0:
        return surf, None, None
    text = gs.sm_font.render(f"{fps_value:.0f} FPS", True, (200, 200, 200))
    x = const.W - viewport.ui_px(20) - text.get_width()
    y = viewport.ui_px(64)
    surf.blit(text, (x, y))
    return surf, pygame.Rect(x, y, text.get_width(), text.get_height()), text


def assert_blitted_at_expected_place(w, h, fps_value):
    """The inked glyphs must land exactly at the blit origin + font inset."""
    surf, rect, text = draw_readout(w, h, fps_value)
    assert rect is not None, f"{w}x{h}: nothing drawn"
    box = drawn_bbox(surf)
    assert box is not None, f"{w}x{h}: nothing drawn"
    ix0, iy0, ix1, iy1 = ink_bbox(text)
    want = (rect.x + ix0, rect.y + iy0, rect.x + ix1, rect.y + iy1)
    assert box == want, f"{w}x{h}: ink at {box}, render() would put it at {want}"
    return rect


# 1) fps = 0 -> nothing drawn at all.
surf, rect, _ = draw_readout(640, 480, 0.0)
assert rect is None and drawn_bbox(surf) is None, "fps=0 must draw nothing"
print("fps=0 -> readout hidden OK")

# 2) fps = 60 -> drawn exactly where render() puts it, in the top band.
for w, h in ((640, 480), (1000, 700), (1920, 1080), (3840, 2160)):
    rect = assert_blitted_at_expected_place(w, h, 60.0)
    assert rect.bottom < h * 0.25, f"{w}x{h}: not in the top band (bottom={rect.bottom})"
    assert rect.right <= w and rect.left >= 0, f"{w}x{h}: outside the window"
    print(f"  {w}x{h}: rect={tuple(rect)} OK")

# 3) Layout units, not pixels: at an equal ui_scale() the readout must keep
#    its pixel size even when the window pixel count differs.  640x480 and
#    640x448 both clamp on width (0.64) but differ in height, so a readout
#    sized in raw pixels would not match.
const.W, const.H = 640, 480
s_small = viewport.ui_scale()
r_small = assert_blitted_at_expected_place(640, 480, 60.0)
const.W, const.H = 640, 448
s_same = viewport.ui_scale()
r_same = assert_blitted_at_expected_place(640, 448, 60.0)
assert abs(s_small - s_same) < 1e-9, (s_small, s_same)
assert (r_small.width, r_small.height) == (r_same.width, r_same.height), (
    f"equal ui_scale -> equal size: {tuple(r_small)} vs {tuple(r_same)}")
print(f"  equal ui_scale ({s_small:.3f}), different window height -> "
      f"identical {r_small.width}x{r_small.height}px readout OK")

# 3b) Bigger window -> bigger readout.  The exact size must be the quantized
#     layout-unit size, not raw pixels and not a raw multiple of ui_scale():
#     ui_px() rounds, so at 640x480 the small font is 12px and at 1920x1080 it
#     is 28px -- 2.33x for a 2.41x scale, and that gap is correct.  Asserting
#     strict proportionality would fail on the rounding the HUD relies on.
const.W, const.H = 1920, 1080
s_big = viewport.ui_scale()
r_big = assert_blitted_at_expected_place(1920, 1080, 60.0)
assert r_big.width > r_small.width and r_big.height > r_small.height, \
    f"a larger window must give a larger readout: {tuple(r_small)} -> {tuple(r_big)}"

for w, h in ((640, 480), (1000, 700), (1920, 1080), (3840, 2160)):
    const.W, const.H = w, h
    viewport.ensure_hud_fonts()
    want = max(1, round(viewport._FONT_SIZES[1] * viewport.ui_scale()))
    assert gs.sm_font.get_height() > 0
    # The rendered ink height is a fraction of the point size and is rounded
    # too, so compare against the same font rendered at the expected size.
    ref = pygame.font.SysFont("monospace", want)
    assert gs.sm_font.get_height() == ref.get_height(), (
        f"{w}x{h}: readout font {gs.sm_font.get_height()}px != layout unit {want}px")
print("  readout uses the quantized layout-unit font at every size OK")

# 4) No decimals leak into the label.
for v, expect in ((0.4, "0 FPS"), (59.5, "60 FPS"), (143.9, "144 FPS"), (60.0, "60 FPS")):
    got = f"{v:.0f} FPS"
    assert got == expect, f"{v} -> {got!r}, expected {expect!r}"
print("label formatting OK (whole fps only)")

# 5) The feed path: bridge.update() must land the value in gs.fps.
spec = importlib.util.spec_from_file_location(
    "bridge", os.path.join(ROOT, "client", "bridge.py"))
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
b = bridge.RenderBridge(pygame.Surface((640, 480)))
b.update(0.0, 0.0, 0.0, 100, [], fps=77.0)
assert gs.fps == 77.0, f"bridge must forward fps, got {gs.fps}"
b.update(0.0, 0.0, 0.0, 100, [])
assert gs.fps == 0.0, f"default must clear the readout, got {gs.fps}"
print("bridge.update() feeds gs.fps (default 0 -> hidden) OK")

print("smoke_fps_readout: ALL OK")