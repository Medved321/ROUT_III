"""Scratch: census of what the sector renderer paints for floor steps."""
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

COL = {
    "floor0": (200, 40, 40),
    "floor1": (40, 60, 220),
    "ceil0":  (200, 200, 60),
    "ceil1":  (60, 200, 200),
    "wall":   (170, 170, 170),
    "lower":  (250, 140, 20),
    "upper":  (140, 250, 60),
    "mid":    (255, 0, 255),
}
for k, v in COL.items():
    s = pygame.Surface((64, 64))
    s.fill(v)
    gs.flats[k] = s
    gs.textures[k] = s


def classify(rgb):
    best, bd = "?", 1e9
    for name, c in COL.items():
        d = sum((a - b) ** 2 for a, b in zip(rgb, c))
        if d < bd:
            bd, best = d, name
    return best


from shared.sector_map import SectorMap, Sector, LineDef, SideDef, Thing, Vertex
from engine import sector_render as SR
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


def box(sm, x0, y0, x1, y1, front, back=-1, low="-", mid="-"):
    """Rect whose interior sits on the BACK side (the editor's convention)."""
    line(sm, x0, y0, x1, y0, front, back, low=low, mid=mid)
    line(sm, x1, y0, x1, y1, front, back, low=low, mid=mid)
    line(sm, x1, y1, x0, y1, front, back, low=low, mid=mid)
    line(sm, x0, y1, x0, y0, front, back, low=low, mid=mid)


def pit_map(depth=-64, pit_ceil=128, y0=192, y1=448):
    """Room 0 (floor 0) around a sunken sector 1 (floor `depth`)."""
    sm = SectorMap()
    sm.sectors.append(Sector(floor_h=0, ceil_h=128, light=255,
                             floor_tex="floor0", ceil_tex="ceil0"))
    sm.sectors.append(Sector(floor_h=depth, ceil_h=pit_ceil, light=255,
                             floor_tex="floor1", ceil_tex="ceil1"))
    box(sm, 0, 0, 512, 512, 0, mid="wall")
    box(sm, 192, y0, 320, y1, 0, 1, low="lower")
    sm.things.append(Thing(256, 100))
    errs = sm.validate()
    assert not errs, errs
    return sm


def plat_map(plat_h=48):
    """Room 0 (floor 0) with a raised sector 1 (floor `plat_h`)."""
    sm = SectorMap()
    sm.sectors.append(Sector(floor_h=0, ceil_h=160, light=255,
                             floor_tex="floor0", ceil_tex="ceil0"))
    sm.sectors.append(Sector(floor_h=plat_h, ceil_h=160, light=255,
                             floor_tex="floor1", ceil_tex="ceil1"))
    box(sm, 0, 0, 512, 512, 0, mid="wall")
    box(sm, 192, 192, 320, 448, 0, 1, low="lower")
    sm.things.append(Thing(256, 100))
    errs = sm.validate()
    assert not errs, errs
    return sm


def flat_map():
    sm = SectorMap()
    sm.sectors.append(Sector(floor_h=0, ceil_h=128, light=255,
                             floor_tex="floor0", ceil_tex="ceil0"))
    sm.sectors.append(Sector(floor_h=0, ceil_h=128, light=255,
                             floor_tex="floor1", ceil_tex="ceil1"))
    box(sm, 0, 0, 512, 512, 0, mid="wall")
    box(sm, 192, 192, 320, 448, 0, 1, low="lower")
    sm.things.append(Thing(256, 100))
    errs = sm.validate()
    assert not errs, errs
    return sm


def column(mod, sm, pa, label, col=None):
    """Material + raw colour down one framebuffer column (rows in window px)."""
    census, z = census_of(mod, sm, pa)
    surf = _LAST_SURF[0]
    if col is None:
        col = const.W // 2
    print(f"--- column x={col} of {const.W} : {label}")
    rows = []
    for y in range(0, const.H, 8):
        rgb = surf.get_at((col, y))[:3]
        rows.append(f"{y:3d}:{classify(rgb)}{rgb}")
    for i in range(0, len(rows), 6):
        print("    " + "  ".join(rows[i:i + 6]))
    print()


_LAST_SURF = [None]


def census_of(mod, sm, pa, py=100.0):
    gs.sec_scene = mod.SectorScene(sm)
    gs.sector_mode = True
    gs.render_scale = 2
    gs.vs_scale = 2
    gs.view_dist = 2000
    gs.flat_textured = True
    gs.px, gs.py, gs.pa = 256.0, py, pa
    gs.pz = 0.0
    gs.vh720 = False
    frame = _ensure_frame()
    frame.clear((0, 0, 0))
    z = mod.render_scene(const.H, 0)
    surf = pygame.transform.scale(frame.to_surface(), (const.W, const.H))
    _LAST_SURF[0] = surf
    census = {}
    for y in range(0, const.H, 2):
        for x in range(0, const.W, 2):
            n = classify(surf.get_at((x, y))[:3])
            census[n] = census.get(n, 0) + 1
    return census, z


def run(label, sm, pa):
    census, z = census_of(SR, sm, pa)
    print(f"--- {label}")
    print("    " + "  ".join(f"{k}={census.get(k, 0)}"
                             for k in ("floor0", "floor1", "ceil0", "ceil1",
                                       "wall", "lower", "upper", "mid")))
    print(f"    z_b center={round(z[len(z) // 2], 1)} len={len(z)}")


SOUTH = 1.5707963
# --- non-vacuity reference: the pre-fix renderer, loaded as its own module --
import subprocess  # noqa: E402

src = subprocess.run(["git", "show", "HEAD:engine/sector_render.py"],
                     capture_output=True).stdout
head = type(SR)("sector_render_head")
exec(compile(src, "sector_render_head", "exec"), head.__dict__)

run("pit -64 wide (y 192..448)", pit_map(depth=-64), SOUTH)
run("pit -64 narrow (y 192..320)", pit_map(depth=-64, y1=320), SOUTH)
run("pit -16 wide", pit_map(depth=-16), SOUTH)
run("platform +48", plat_map(), SOUTH)
run("flat control", flat_map(), SOUTH)
run("recess -64..96 (ceil above room floor)", pit_map(depth=-64, pit_ceil=96),
    SOUTH)
# a real shaft: floor and ceiling both below the room's floor slab
run("shaft -64..-16 (ceil BELOW room floor)", pit_map(depth=-64, pit_ceil=-16),
    SOUTH)
print()
column(SR, pit_map(depth=-64, pit_ceil=-16), SOUTH, "shaft, fixed code")
column(head, pit_map(depth=-64, pit_ceil=-16), SOUTH, "shaft, HEAD code")
def run_at(label, sm, py):
    """Same scene, camera at (256, py) looking south."""
    census, z = census_of(SR, sm, SOUTH, py=py)
    print(f"--- {label} (eye at y={py})")
    print("    " + "  ".join(f"{k}={census.get(k, 0)}"
                             for k in ("floor0", "floor1", "ceil0", "ceil1",
                                       "wall", "lower", "upper", "mid")))
    print(f"    z_b center={round(z[len(z) // 2], 1)}")


def trace(mod, sm, pa, py, cols=(500,)):
    """Log every _seg_wall_bands call and every _push_flat span, per column.

    Answers: which sector pushes the pit floor span, and which clip_bot value
    clips it away.
    """
    log = []
    real_seg = mod._seg_wall_bands
    real_push = mod._push_flat

    def push(spans, x, y0, y1, sid, is_floor, ih):
        if x in cols:
            log.append(f"      push sec={sid} col={x} floor={is_floor} "
                       f"span=[{y0:.0f}, {y1:.0f}]")
        return real_push(spans, x, y0, y1, sid, is_floor, ih)

    def seg(bands, scene, tree, si, px, py_, pa_, F, hc, iw, ih, eye, view,
            clip_top=None, clip_bot=None, flat_spans=None, view_buf=None):
        cb = (clip_bot[0] if clip_bot else None)
        before = len(log)
        real_seg(bands, scene, tree, si, px, py_, pa_, F, hc, iw, ih, eye, view,
                 clip_top, clip_bot, flat_spans, view_buf)
        log.append(f"  seg si={si} first_col clip_top={clip_top} "
                   f"clip_bot={cb}")
        for line in log[before:]:
            log.append(line)

    mod._push_flat = push
    mod._seg_wall_bands = seg
    try:
        census_of(mod, sm, pa, py=py)
    finally:
        mod._push_flat = real_push
        mod._seg_wall_bands = real_seg
    print(f"===== trace: eye y={py} (column {cols[0]})")
    for line in log:
        print(line)


run_at("pit -64 NARROW, camera 12 away", pit_map(depth=-64, y1=320), 180.0)
trace(SR, pit_map(depth=-64, y1=320), SOUTH, 180.0)
print()
run("pit -64 wide -- HEAD", pit_map(depth=-64), SOUTH)
run("shaft -64..-16 -- HEAD", pit_map(depth=-64, pit_ceil=-16), SOUTH)
run("platform +48 -- HEAD", plat_map(), SOUTH)
run("flat control -- HEAD", flat_map(), SOUTH)
