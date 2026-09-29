"""Scratch: render a pit and print a per-material map of the framebuffer."""
import os
os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import pygame

from engine import game_state as gs
from engine import constants as const

pygame.init()
gs.screen = pygame.display.set_mode((const.W, const.H))
gs.textures = {}
gs.flats = {}


def tex(rgb):
    s = pygame.Surface((64, 64))
    s.fill(rgb)
    return s


# distinct material colours so the dump says WHAT is drawn where
COL = {
    "floor0": (200, 40, 40),     # room floor          -> '0'
    "floor1": (40, 60, 220),     # pit floor            -> '1'
    "ceil0":  (200, 200, 60),    # room ceiling         -> 'c'
    "ceil1":  (60, 200, 200),    # pit ceiling          -> 'C'
    "wall":   (170, 170, 170),   # one-sided wall       -> '#'
    "lower":  (250, 140, 20),    # two-sided lower lip  -> 'L'
    "upper":  (140, 250, 60),    # two-sided upper lip  -> 'U'
    "mid":    (255, 0, 255),     # two-sided mid        -> 'M'
}
for k, v in COL.items():
    gs.flats[k] = tex(v)
    gs.textures[k] = tex(v)
LEGEND = {v: c for c, v in COL.items()}
# nearest-colour classification (rendering shades by light, so match hue)
NAMES = [(c, n) for n, c in COL.items()]


def classify(rgb):
    best, bd = " ", 1e9
    for c, n in NAMES:
        d = sum((a - b) ** 2 for a, b in zip(rgb, c))
        if d < bd:
            bd, best = d, n
    return {"floor0": "0", "floor1": "1", "ceil0": "c", "ceil1": "C",
            "wall": "#", "lower": "L", "upper": "U", "mid": "M"}[best]


from shared.sector_map import SectorMap, Sector, LineDef, SideDef, Thing, Vertex
from engine.sector_render import SectorScene, render_scene
from engine.rendering import _ensure_frame


def vertex(sm, x, y):
    for i, v in enumerate(sm.verts):
        if v.x == x and v.y == y:
            return i
    sm.verts.append(Vertex(float(x), float(y)))
    return len(sm.verts) - 1


def line(sm, ax, ay, bx, by, front=-1, back=-1, up="-", low="-", mid="-"):
    a, b = vertex(sm, ax, ay), vertex(sm, bx, by)
    f = -1
    if front >= 0:
        f = len(sm.sidedefs)
        sm.sidedefs.append(SideDef(sector=front, upper=up, lower=low, mid=mid))
    bk = -1
    if back >= 0:
        bk = len(sm.sidedefs)
        sm.sidedefs.append(SideDef(sector=back, upper=up, lower=low, mid=mid))
    sm.linedefs.append(LineDef(a, b, front=f, back=bk))


def pit_map(depth=-64, pit_ceil=128):
    """Room 0 (floor 0, ceil 128) with sunken sector 1 (floor `depth`)."""
    sm = SectorMap()
    sm.sectors.append(Sector(floor_h=0, ceil_h=128, light=255,
                             floor_tex="floor0", ceil_tex="ceil0"))
    sm.sectors.append(Sector(floor_h=depth, ceil_h=pit_ceil, light=255,
                             floor_tex="floor1", ceil_tex="ceil1"))
    line(sm, 0, 0, 512, 0, 0, mid="wall")
    line(sm, 512, 0, 512, 512, 0, mid="wall")
    line(sm, 512, 512, 0, 512, 0, mid="wall")
    line(sm, 0, 512, 0, 0, 0, mid="wall")
    # pit walls: front side is the room, back side the pit; lower lip only
    line(sm, 192, 192, 320, 192, 0, 1, low="lower", mid="mid")
    line(sm, 320, 192, 320, 320, 0, 1, low="lower", mid="mid")
    line(sm, 320, 320, 192, 320, 0, 1, low="lower", mid="mid")
    line(sm, 192, 320, 192, 192, 0, 1, low="lower", mid="mid")
    sm.things.append(Thing(256, 100))
    errs = sm.validate()
    assert not errs, errs
    return sm


def dump(label, pa, **kw):
    gs.sec_scene = SectorScene(pit_map(**kw))
    gs.sector_mode = True
    gs.render_scale = 2
    gs.vs_scale = 2
    gs.view_dist = 2000
    gs.flat_textured = True
    gs.px, gs.py, gs.pa = 256.0, 100.0, pa
    gs.pz = 0.0
    gs.vh720 = False
    frame = _ensure_frame()
    frame.clear((0, 0, 0))
    z = render_scene(const.H, 0)
    surf = pygame.transform.scale(frame.to_surface(), (const.W, const.H))
    print(f"--- {label} {kw} ---")
    for y in range(0, const.H, 6):
        print("  " + "".join(classify(surf.get_at((x, y))[:3])
                             for x in range(0, const.W, 5)))
    n = len(z)
    print("  z_b:", [round(z[i], 1) for i in range(0, n, max(1, n // 10))])


SOUTH = 1.5707963
print("legend: 0=room floor 1=pit floor c=ceil0 C=ceil1 #=wall L=lower lip U=upper lip M=mid\n")

dump("deep pit, floor -64, pit ceiling 128", SOUTH, depth=-64, pit_ceil=128)
print()
dump("pit floor -64, pit ceiling REMOVED (open to sky)", SOUTH, depth=-64, pit_ceil=-1)
print()
dump("shallow step -16", SOUTH, depth=-16)
print()
dump("flat control, no step", SOUTH, depth=0)
