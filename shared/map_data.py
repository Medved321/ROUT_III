from __future__ import annotations

import json
import math
from pathlib import Path

TILE_EMPTY = 0
TILE_SIZE = 1
TILE = 64  # pixels per tile (renderer convention, ported from ROUT_II_III)

MIN_MAP_SIZE = 8
MAX_MAP_SIZE = 64  # keeps a raw map <= 4096 bytes for a single UDP datagram

DEFAULT_MAP = [
    [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
    [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
    [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
    [1, 0, 0, 2, 2, 0, 0, 0, 0, 0, 3, 3, 3, 0, 0, 1],
    [1, 0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0, 3, 0, 0, 1],
    [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
    [1, 0, 0, 0, 0, 0, 4, 4, 4, 0, 0, 0, 0, 0, 0, 1],
    [1, 0, 0, 0, 0, 0, 4, 0, 4, 0, 0, 0, 0, 0, 0, 1],
    [1, 0, 0, 0, 0, 0, 4, 0, 4, 0, 0, 0, 0, 0, 0, 1],
    [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
    [1, 0, 0, 5, 0, 0, 0, 0, 0, 0, 0, 6, 6, 0, 0, 1],
    [1, 0, 0, 5, 5, 5, 0, 0, 0, 0, 0, 6, 0, 0, 0, 1],
    [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
    [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
    [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
    [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
]


class GameMap:
    def __init__(self, width: int = 16, height: int = 16, tiles: list[list[int]] | None = None):
        self.width = width
        self.height = height
        self.tiles = tiles if tiles else [row[:] for row in DEFAULT_MAP]

    def get_tile(self, x: int, y: int) -> int:
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.tiles[y][x]
        return 1

    def is_wall(self, x: float, y: float) -> bool:
        tx, ty = int(x), int(y)
        return self.get_tile(tx, ty) != TILE_EMPTY

    def to_bytes(self) -> bytes:
        result = bytearray()
        for row in self.tiles:
            result.extend(row)
        return bytes(result)

    @staticmethod
    def from_bytes(data: bytes, width: int, height: int) -> GameMap:
        tiles = []
        for y in range(height):
            row = list(data[y * width:(y + 1) * width])
            tiles.append(row)
        return GameMap(width, height, tiles)

    @staticmethod
    def validate_network(width: int, height: int, data: bytes) -> GameMap | None:
        """Build a GameMap from untrusted client bytes, or None if invalid.

        Guards the server against oversized / malformed / spawn-less maps.
        """
        if not (MIN_MAP_SIZE <= width <= MAX_MAP_SIZE and MIN_MAP_SIZE <= height <= MAX_MAP_SIZE):
            return None
        if width * height != len(data):
            return None
        game_map = GameMap.from_bytes(data, width, height)
        empty = game_map.get_empty_cells()
        if not empty:
            return None  # no floor -> nowhere to spawn
        return game_map

    def to_json(self) -> dict:
        return {"width": self.width, "height": self.height, "tiles": self.tiles}

    @staticmethod
    def from_json(data: dict) -> GameMap:
        return GameMap(data["width"], data["height"], data["tiles"])

    @staticmethod
    def load(path: str | Path) -> GameMap:
        with open(path) as f:
            return GameMap.from_json(json.load(f))

    def save(self, path: str | Path) -> None:
        with open(path, "w") as f:
            json.dump(self.to_json(), f)

    def get_spawn_points(self) -> list[tuple[float, float]]:
        spawns = []
        for y in range(self.height):
            for x in range(self.width):
                if self.tiles[y][x] == TILE_EMPTY:
                    spawns.append((x + 0.5, y + 0.5))
        return spawns

    def get_empty_cells(self) -> list[tuple[int, int]]:
        return [
            (x, y)
            for y in range(self.height)
            for x in range(self.width)
            if self.tiles[y][x] == TILE_EMPTY
        ]

    def is_line_of_sight(self, x1: float, y1: float, x2: float, y2: float) -> bool:
        """DDA line-of-sight check: True if no wall between points."""
        cx, cy = int(x1), int(y1)
        dx_ = x2 - x1
        dy_ = y2 - y1
        dist = math.hypot(dx_, dy_)
        if dist < 1e-6:
            return True
        steps = max(int(dist * 32), 2)
        for i in range(1, steps):
            t = i / steps
            tx, ty = int(x1 + dx_ * t), int(y1 + dy_ * t)
            if (tx, ty) != (cx, cy) and self.get_tile(tx, ty) != TILE_EMPTY:
                return False
        return True

    # --- pixel-space helpers (ported from ROUT_II_III map_utils.py) ----------
    def is_wall_px(self, x: float, y: float) -> bool:
        tx, ty = int(x) // TILE, int(y) // TILE
        return self.get_tile(tx, ty) != TILE_EMPTY

    def can_move_px(self, x: float, y: float, r: float) -> bool:
        """True if a circle of radius ``r`` at (x, y) does not overlap a wall."""
        tx1 = max(0, int(x - r) // TILE)
        tx2 = min(self.width - 1, int(x + r) // TILE)
        ty1 = max(0, int(y - r) // TILE)
        ty2 = min(self.height - 1, int(y + r) // TILE)
        for ty in range(ty1, ty2 + 1):
            for tx in range(tx1, tx2 + 1):
                if self.get_tile(tx, ty) != TILE_EMPTY:
                    px = max(tx * TILE, min(x, tx * TILE + TILE))
                    py = max(ty * TILE, min(y, ty * TILE + TILE))
                    if (x - px) ** 2 + (y - py) ** 2 < r * r:
                        return False
        return True

    def has_los_px(self, x1: float, y1: float, x2: float, y2: float) -> bool:
        """Pixel-space LOS (sampling every 8 px), ported from ROUT_II_III."""
        dx_ = x2 - x1
        dy_ = y2 - y1
        dist = math.hypot(dx_, dy_)
        if dist < 1:
            return True
        steps = max(int(dist / 8), 1)
        for i in range(1, steps):
            t = i / steps
            if self.is_wall_px(x1 + dx_ * t, y1 + dy_ * t):
                return False
        return True

    def spawn_px(self) -> list[tuple[float, float]]:
        """Pixel-space spawn positions (tile centers * TILE)."""
        return [((x + 0.5) * TILE, (y + 0.5) * TILE) for x, y in self.get_empty_cells()]

    def open_points(self, count: int = 64) -> list[tuple[float, float]]:
        """Walkable sample points (common interface with SectorMap)."""
        return self.spawn_px()
