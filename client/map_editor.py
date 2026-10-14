"""Tile map editor data model + palette.

Ported (trimmed) from ROUT_II_III ``map_editor.py``.  Lives in the client because
the renderer does not know tiles: the client registers ``TILE_CHAR_TO_TEX``
through ``gs.tile_provider`` (see engine/game_state.py).

Only structural tiles (solid walls) are supported by the multiplayer map
format: ``0`` is empty floor, any other integer is a solid wall whose concrete
texture is chosen by the integer <-> character table below.
"""
from __future__ import annotations

from shared.map_data import GameMap

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


# palette: (section header or (char, label))
TB_ITEMS: list[tuple] = [
    ('_СТЕНЫ',),
    ('#', 'КИРПИЧ'), ('T', 'КАМЕНЬ'), ('W', 'ДЕРЕВО'), ('M', 'МЯСО'),
    ('I', 'ЗАРАЖ.'), ('P', 'МУЧЕН.'), ('H', 'АД-СТЕНА'), ('X', 'БОЧКА'),
    ('_ПРОЧЕЕ',),
    ('D', 'ДВЕРЬ'), ('R', 'КР.ДВЕРЬ'), ('Y', 'Ж.ДВЕРЬ'), ('B', 'С.ДВЕРЬ'),
    ('V', 'РЫЧАГ'), ('C', 'МОСТ'), ('Q', 'ПЛАТФ.'), ('K', 'ПЕНТАГР.'),
]

MAT_COLORS: dict[str, tuple[int, int, int]] = {
    '#': (100, 100, 100), 'T': (130, 130, 140), 'W': (120, 80, 40),
    '.': (40, 40, 45), 'M': (140, 30, 30), 'I': (90, 110, 85),
    'P': (170, 80, 50), 'D': (160, 120, 60), 'H': (100, 80, 60),
    'V': (100, 200, 100), 'C': (140, 100, 60), 'Q': (60, 180, 200),
    'K': (200, 50, 200), 'X': (180, 40, 20), 'R': (180, 40, 40),
    'Y': (180, 180, 40), 'B': (40, 40, 180), 'E': (60, 180, 220),
    'F': (200, 150, 0), 'L': (200, 80, 0), 'S': (100, 200, 100),
}

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


class TileEditor:
    """Char-grid level buffer (same model as ROUT_II_III's TileEditor)."""

    def __init__(self, sz: int = 20):
        self.sz = sz
        self.grid = [self._empty_row(sz, i) for i in range(sz)]

    @staticmethod
    def _empty_row(sz: int, i: int) -> list[str]:
        if i == 0 or i == sz - 1:
            return ['#'] * sz
        return ['#'] + ['.'] * (sz - 2) + ['#']

    def ensure_border(self) -> None:
        for i in range(self.sz):
            for j in range(self.sz):
                if i == 0 or j == 0 or i == self.sz - 1 or j == self.sz - 1:
                    if self.grid[i][j] == '.':
                        self.grid[i][j] = '#'

    def resize(self, new_sz: int) -> None:
        old = self.grid
        old_sz = self.sz
        self.sz = new_sz
        self.grid = [self._empty_row(new_sz, i) for i in range(new_sz)]
        for i in range(min(old_sz, new_sz)):
            for j in range(min(old_sz, new_sz)):
                if old[i][j] != '.' and old[i][j] != '#':
                    self.grid[i][j] = old[i][j]
        self.ensure_border()

    def set_cell(self, x: int, y: int, tile: str) -> None:
        if 0 <= x < self.sz and 0 <= y < self.sz:
            if tile == '.' and (x == 0 or y == 0 or x == self.sz - 1 or y == self.sz - 1):
                return
            self.grid[y][x] = tile

    def to_game_map(self) -> GameMap:
        return GameMap(self.sz, self.sz, grid_to_tiles(self.grid))

    @classmethod
    def from_game_map(cls, m: GameMap) -> 'TileEditor':
        ed = cls(m.width)
        ed.sz = max(m.width, m.height)
        ed.grid = tiles_to_grid(m.tiles)
        return ed

    def copy_grid(self) -> list[list[str]]:
        return [row[:] for row in self.grid]

    def restore_grid(self, grid: list[list[str]]) -> None:
        self.grid = [row[:] for row in grid]
        self.sz = len(grid)
