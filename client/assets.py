from __future__ import annotations

import os
import pygame


class AssetManager:
    def __init__(self, base_path: str = "assets"):
        self.base_path = base_path
        self.textures: dict[int, pygame.Surface] = {}
        self._load_default_textures()

    def _load_default_textures(self) -> None:
        for wall_id in range(1, 8):
            surface = pygame.Surface((64, 64))
            colors = {
                1: (180, 40, 40), 2: (40, 180, 40), 3: (40, 40, 180),
                4: (180, 180, 40), 5: (180, 40, 180), 6: (40, 180, 180),
                7: (128, 128, 128),
            }
            base_color = colors.get(wall_id, (150, 150, 150))
            surface.fill(base_color)

            brick_color = tuple(max(0, c - 30) for c in base_color)
            for by in range(0, 64, 16):
                offset = 8 if (by // 16) % 2 else 0
                for bx in range(-8, 72, 32):
                    rect = (bx + offset, by, 30, 14)
                    pygame.draw.rect(surface, brick_color, rect, 1)

            self.textures[wall_id] = surface

    def get_texture(self, wall_id: int) -> pygame.Surface | None:
        return self.textures.get(wall_id)

    def load_texture(self, path: str, wall_id: int) -> None:
        if os.path.exists(path):
            surface = pygame.image.load(path).convert()
            surface = pygame.transform.scale(surface, (64, 64))
            self.textures[wall_id] = surface
