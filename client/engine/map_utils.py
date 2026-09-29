"""Trimmed helpers for the renderer (tile lookup + sector checks)."""
from client.engine.constants import TILE
from client.engine import game_state as gs


def get_t(x, y):
    tx, ty = int(x // TILE), int(y // TILE)
    if 0 <= ty < len(gs.MAP) and 0 <= tx < len(gs.MAP[ty]):
        return gs.MAP[ty][tx]
    return '#'


def _point_in_sector(px, py, si, verts, lines):
    """Crossing-number test for sector mode collision."""
    DOOR_NONE = 0
    crossings = 0
    for li, ld in enumerate(lines):
        door_type = ld[4] & 0xF if len(ld) > 4 else 0
        if door_type != DOOR_NONE:
            continue
        front, back = ld[2], ld[3]
        if front != si and back != si:
            continue
        v1i, v2i = ld[0], ld[1]
        if v1i >= len(verts) or v2i >= len(verts):
            continue
        x1, y1 = verts[v1i]
        x2, y2 = verts[v2i]
        if (y1 > py) == (y2 > py):
            continue
        if abs(y2 - y1) < 0.0001:
            continue
        try:
            x_intersect = x1 + (x2 - x1) * (py - y1) / (y2 - y1)
            if px < x_intersect:
                crossings += 1
        except Exception:
            pass
    return crossings % 2 == 1


def _player_in_any_sector(x, y):
    if x == gs.px and y == gs.py:
        if getattr(gs, 'player_sector_timer', 0) > 0:
            return gs.player_sector_cache
    for si in range(len(gs.sec_sectors)):
        if _point_in_sector(x, y, si, gs.sec_verts, gs.sec_lines):
            if x == gs.px and y == gs.py:
                gs.player_sector_cache = si
                gs.player_sector_timer = 2
            return si
    if x == gs.px and y == gs.py:
        gs.player_sector_cache = -1
        gs.player_sector_timer = 2
    return -1