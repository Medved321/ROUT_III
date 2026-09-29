from __future__ import annotations

import math

from shared.map_data import GameMap

# Rendering constants
INTERNAL_WIDTH = 320
INTERNAL_HEIGHT = 200
SCALE = 3
SCREEN_WIDTH = 1000
SCREEN_HEIGHT = 700
FOV = 66.0  # degrees
HALF_FOV = FOV / 2.0
NUM_RAYS = INTERNAL_WIDTH
DELTA_ANGLE = math.radians(FOV) / NUM_RAYS
MAX_DEPTH = 20.0
PROJ_COEFF = INTERNAL_WIDTH / (2.0 * math.tan(math.radians(HALF_FOV)))


class Raycaster:
    def __init__(self, game_map: GameMap):
        self.game_map = game_map

    def cast_ray(self, px: float, py: float, ray_angle: float) -> float:
        sin_a = math.sin(ray_angle)
        cos_a = math.cos(ray_angle)

        for depth in range(1, int(MAX_DEPTH * 100)):
            d = depth / 100.0
            x = px + d * cos_a
            y = py + d * sin_a

            if self.game_map.is_wall(x, y):
                return d

        return MAX_DEPTH

    def render_frame(
        self,
        px: float, py: float, player_angle: float
    ) -> list[tuple[int, float, int]]:
        """Returns list of (column, depth, wall_type) for each ray."""
        walls = []
        start_angle = player_angle - math.radians(HALF_FOV)

        for ray in range(NUM_RAYS):
            ray_angle = start_angle + ray * DELTA_ANGLE
            depth = self.cast_ray(px, py, ray_angle)

            depth *= math.cos(ray_angle - player_angle)

            tx = int(px + depth * math.cos(ray_angle))
            ty = int(py + depth * math.sin(ray_angle))
            wall_type = self.game_map.get_tile(tx, ty)

            walls.append((ray, depth, wall_type))

        return walls
