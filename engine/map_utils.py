"""Trimmed helpers for the renderer (tile lookup)."""
from engine.constants import TILE
from engine import game_state as gs


def get_t(x, y):
    tx, ty = int(x // TILE), int(y // TILE)
    if 0 <= ty < len(gs.MAP) and 0 <= tx < len(gs.MAP[ty]):
        return gs.MAP[ty][tx]
    return '#'
