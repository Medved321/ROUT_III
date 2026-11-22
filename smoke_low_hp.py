"""Smoke: low HP is a steady red tint, not a blinking overlay.

The old renderer added a second, time-gated overlay for ``hp <= 20``::

    if gs.p['hp'] <= 20 and int(curr_t * 0.005) % 2 == 0:
        ...

so the whole view alternated between two brightness levels roughly twice a
second.  This smoke renders the same frame at several ``pygame.time.get_ticks``
values covering both phases and requires the world part of the frame to stay
byte-identical.  The steady tint itself must still be there: low HP changes the
view towards red compared with full HP.

Run: ``py -3.12 smoke_low_hp.py``.
"""
from __future__ import annotations

import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pygame  # noqa: E402

from engine import constants as const  # noqa: E402
from engine import game_state as gs  # noqa: E402
from engine import rendering  # noqa: E402
from engine import textures as texgen  # noqa: E402
from engine.constants import SCALE  # noqa: E402

ROOM = [
    "#########",
    "#.......#",
    "#.......#",
    "#.......#",
    "#.......#",
    "#########",
]

# The pulse phase is ``int(t * 0.005) % 2``; these ticks cover both the old
# "on" and "off" halves several times.  The HUD face blink is confined to the
# bottom bar, which is excluded from the compared region below.
TICKS = (0, 200, 400, 600, 800, 1000)


def _init():
    pygame.init()
    gs.screen = pygame.display.set_mode((const.W, const.H))
    gs.font = pygame.font.SysFont("monospace", 34, bold=True)
    gs.sm_font = pygame.font.SysFont("monospace", 18)
    gs.fog_enabled = False
    gs.view_dist = 900
    gs.render_scale = SCALE
    gs.vs_scale = 2
    gs.flat_textured = True
    gs.sector_mode = False
    gs.sector_grid = []
    gs.sectors = []
    gs.bridge_open = set()
    gs.dwall_hp = {}
    gs.explored = []
    gs.particles = []
    gs.decals = []
    gs.weather = []
    gs.enemies = []
    gs.items = []
    gs.bul = []
    gs.proj = []
    gs.corpses = []
    gs.portals = []
    gs.barrels = []
    gs.bots = []
    gs.players = []
    gs.exit_pos = (0, 0)
    gs.show_minimap = False
    gs.show_crosshair = False
    gs.d_flash = 0
    gs.dash_fx = 0
    gs.w_tmr = 0
    gs.fire_flag = False
    gs.p['guns'] = {0, 1}
    gs.p['powerup'] = None
    gs.p['hp'] = 100
    gs.px, gs.py, gs.pa = const.W // 2, const.H // 2, 0.0
    gs.MAP = [list(row) for row in ROOM]
    gs.M_SZ = len(ROOM)
    texgen.gen_textures()
    from engine.wallcache import DEFAULT_CACHE
    DEFAULT_CACHE.clear_tex()


def _view_bytes():
    """World part only: the HUD bar and the weapon live at the bottom."""
    view = gs.screen.subsurface((0, 0, const.W, const.H // 2))
    return pygame.image.tostring(view, "RGB")


def _shot(tick, hp):
    gs.p['hp'] = hp
    pygame.time.get_ticks = lambda: tick
    rendering.render()
    return _view_bytes()


def _avg_rgb(pixels):
    r = g = b = 0
    n = 0
    # Every 37th byte is a deliberately sparse sample: enough to see the tint,
    # independent of any particular wall pixel.
    for i in range(0, len(pixels) - 2, 37 * 3):
        r += pixels[i]
        g += pixels[i + 1]
        b += pixels[i + 2]
        n += 1
    return r / n, g / n, b / n


def main():
    _init()
    original_ticks = pygame.time.get_ticks
    try:
        healthy = _shot(200, 100)
        low = _shot(200, 10)
        hr, hg, hb = _avg_rgb(healthy)
        lr, lg, lb = _avg_rgb(low)
        assert low != healthy, "low HP produced no red tint at all"
        assert lr > hr and lg < hg, \
            f"low-HP tint is not red: healthy {(hr, hg, hb)}, low {(lr, lg, lb)}"
        print(f"  steady red tint present                          OK "
              f"({(round(hr), round(hg), round(hb))} -> "
              f"{(round(lr), round(lg), round(lb))})")

        shots = [(t, _shot(t, 10)) for t in TICKS]
        for t, shot in shots[1:]:
            assert shot == shots[0][1], \
                f"low-HP view changed with time (tick {t}): screen still blinks"
        print("  low-HP view identical across pulse phases       OK")
    finally:
        pygame.time.get_ticks = original_ticks


if __name__ == "__main__":
    main()
    print("smoke_low_hp: OK")
