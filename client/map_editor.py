"""Shared tile-map constants for the renderer and legacy map loading.

The tile editor UI was removed in change ``sector-minimap-editor-fps-cleanup``;
this module keeps only the char/integer format tables used by ``bridge.py``,
the sector editor's thing palette and old ``.json`` maps.  Runtime support for
tile maps (``GameMap``, DDA rendering, old parties) is intentionally unchanged.
"""
from __future__ import annotations

# character -> texture used by the renderer
TILE_CHAR_TO_TEX = {
    '#': 'wall', 'T': 'stone', 'W': 'wood', 'M': 'meat', 'I': 'infected_stone',
    'P': 'martyr', 'H': 'hwall', 'V': 'switch', 'C': 'bridge', 'Q': 'platform',
    'K': 'pentagram', 'L': 'lava', '=': 'wood', 'X': 'barrel',
    'D': 'door_D', 'R': 'door_R', 'Y': 'door_Y', 'B': 'door_B',
    'F': 'exit', 'E': 'elevator', 'J': 'spikes',
}

# single source of truth for the on-disk integer tile format.
# 0 is always empty; values 1..6 keep the historical DEFAULT_MAP meaning.
CHAR_TO_TILE: dict[str, int] = {
    '.': 0,
    '#': 1, 'T': 2, 'W': 3, 'M': 4, 'I': 5, 'P': 6,
    'X': 7, 'H': 8, 'D': 9, 'R': 10, 'Y': 11, 'B': 12,
    'V': 13, 'C': 14, 'Q': 15, 'K': 16,
}
TILE_TO_CHAR: dict[int, str] = {v: k for k, v in CHAR_TO_TILE.items()}
EMPTY_CHAR = '.'


def tile_to_char(tile: int) -> str:
    return TILE_TO_CHAR.get(tile, '#')


def char_to_tile(ch: str) -> int:
    return CHAR_TO_TILE.get(ch, 0)


def grid_to_tiles(grid: list[list[str]]) -> list[list[int]]:
    return [[char_to_tile(ch) for ch in row] for row in grid]


def tiles_to_grid(tiles: list[list[int]]) -> list[list[str]]:
    return [[tile_to_char(t) for t in row] for row in tiles]


ENEMY_NAMES = {
    '1': 'soldier', '2': 'shotgunguy', '3': 'archvile', '4': 'cacodemon',
    '5': 'mancubus', '6': 'dog', '7': 'pinkie', '8': 'boss_baron',
    '9': 'boss_cyber', '0': 'boss_spider',
    'c': 'chaingunner', 'g': 'imp', 'v': 'revenant', 'p': 'pain_elemental',
    'l': 'lost_soul', 'n': 'hell_knight', 'x': 'arachnotron', 'z': 'spectre',
}
ITEM_NAMES = {'m': 'med', 'a': 'amm', 'k': 'berserk', 'h': 'haste', 'i': 'invuln',
              'w': 'wpn_0', 'u': 'wpn_2', 't': 'wpn_3', 'j': 'wpn_4', 'o': 'wpn_5',
              'd': 'heart', 'e': 'armor_green', 'f': 'armor_blue',
              'r': 'key_red', 'y': 'key_yellow', 'b': 'key_blue'}
