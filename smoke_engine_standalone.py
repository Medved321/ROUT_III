"""Smoke: standalone engine render without the client package (engine-standalone, D8).

Loads a test sector map as ``.mt1`` bytes, rebuilds the ``SectorMap`` model,
builds a ``SectorScene`` and renders a frame through ``engine.rendering`` with
no ``client`` module imported.  Asserts ``gs.frame`` is non-empty and of the
expected internal resolution for the tier.
"""
from __future__ import annotations

import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pygame  # noqa: E402

from engine import game_state as gs  # noqa: E402
from shared.sector_map import (  # noqa: E402
    LineDef, Sector, SectorMap, SideDef, Thing, Vertex,
)


def _init_display():
    pygame.init()
    gs.screen = pygame.display.set_mode((1000, 700))
    gs.font = pygame.font.Font(None, 28)
    gs.sm_font = pygame.font.Font(None, 20)
    gs.p = dict(gs.p)


def _vertex(sm, x, y):
    for i, v in enumerate(sm.verts):
        if v.x == x and v.y == y:
            return i
    sm.verts.append(Vertex(float(x), float(y)))
    return len(sm.verts) - 1


def _line(sm, ax, ay, bx, by, front=-1, back=-1):
    f = -1
    if front >= 0:
        f = len(sm.sidedefs)
        sm.sidedefs.append(SideDef(sector=front))
    b = -1
    if back >= 0:
        b = len(sm.sidedefs)
        sm.sidedefs.append(SideDef(sector=back))
    sm.linedefs.append(LineDef(_vertex(sm, ax, ay), _vertex(sm, bx, by),
                               front=f, back=b))
    return len(sm.linedefs) - 1


def _make_room(floor_h=0, ceil_h=128, light=160):
    """Room A (0,0-512,512) with a raised two-sided platform sector inside."""
    sm = SectorMap()
    sm.sectors.append(Sector(floor_h=floor_h, ceil_h=ceil_h, light=light))
    sm.sectors.append(Sector(floor_h=floor_h + 48, ceil_h=ceil_h, light=200))
    _line(sm, 0, 0, 512, 0, 0)
    _line(sm, 512, 0, 512, 512, 0)
    _line(sm, 512, 512, 0, 512, 0)
    _line(sm, 0, 512, 0, 0, 0)
    _line(sm, 160, 160, 352, 160, 0, 1)
    _line(sm, 352, 160, 352, 256, 0, 1)
    _line(sm, 352, 256, 160, 256, 0, 1)
    _line(sm, 160, 256, 160, 160, 0, 1)
    sm.things.append(Thing(256, 64))
    _line(sm, 96, 224, 128, 224, 0)
    _line(sm, 128, 224, 128, 256, 0)
    _line(sm, 128, 256, 96, 256, 0)
    _line(sm, 96, 256, 96, 224, 0)
    assert not sm.validate(), sm.validate()
    return sm


def test_offline_render():
    sm = _make_room()

    # .mt1 round-trip: engine receives the map as bytes, not a live model.
    mt1_bytes = sm.to_mt1()
    assert isinstance(mt1_bytes, bytes) and mt1_bytes, "mt1 serialization failed"
    sm2 = SectorMap.from_mt1(mt1_bytes)
    assert len(sm2.sectors) == len(sm.sectors) == 2
    assert not sm2.validate(), sm2.validate()

    from engine.sector_render import SectorScene

    gs.sec_scene = SectorScene(sm2)
    gs.sector_mode = True
    gs.render_scale = 4
    gs.vs_scale = 2
    gs.view_dist = 900
    gs.flat_textured = False
    gs.px, gs.py, gs.pa = 256.0, 224.0, 0.0
    gs.pz = 0.0
    gs.vh720 = False
    gs.textures = {}
    gs.flats = {}

    from engine import rendering
    from engine import sector_render

    z_b = sector_render.render_scene(700, 0)
    rendering.render(700, 0)  # full engine path incl. HUD/upscale

    assert gs.frame is not None, "gs.frame not created"
    assert gs.frame.size == (320, 240), gs.frame.size
    assert len(gs.frame.buf) == gs.frame.iw * gs.frame.ih * 3
    assert max(gs.frame.buf) > 0, "frame buffer empty (VOID)"
    assert len(z_b) == 1000 // gs.render_scale == 250
    assert min(z_b) < gs.view_dist, "no near walls in z-buffer"
    assert "client" not in sys.modules, "client package leaked into standalone render"
    print(f"  offline frame OK size={gs.frame.size} max_byte={max(gs.frame.buf)}")


def main():
    _init_display()
    print("standalone engine render (no client import):")
    test_offline_render()
    print("STANDALONE SMOKE PASSED")


if __name__ == "__main__":
    main()