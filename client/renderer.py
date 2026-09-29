from __future__ import annotations

import pygame
import math

from client.raycaster import (
    INTERNAL_WIDTH, INTERNAL_HEIGHT, SCALE,
    SCREEN_WIDTH, SCREEN_HEIGHT, PROJ_COEFF
)
from client.settings import MAX_WINDOW, MIN_WINDOW

WALL_COLORS = {
    1: (180, 0, 0),
    2: (0, 180, 0),
    3: (0, 0, 180),
    4: (180, 180, 0),
    5: (180, 0, 180),
    6: (0, 180, 180),
    7: (128, 128, 128),
}

FLOOR_COLOR = (50, 50, 50)
CEILING_COLOR = (100, 100, 120)

# Letterbox colour for the menu letterbox: the menus are laid out for a
# 1000x700 virtual canvas, so a window with a different aspect ratio pads them.
LETTERBOX = (0, 0, 0)


def normalize_size(width, height) -> tuple[int, int]:
    """Clamp a requested window size into a range SDL can actually give us.

    Width and height are forced even (the pixel-format rounding in
    ``transform.scale`` and some drivers dislike odd strides), the floor is
    ``MIN_WINDOW`` so the raycaster keeps at least one column per ray, and the
    ceiling is ``MAX_WINDOW``. Returns the size that was actually used.
    """
    try:
        width = int(width)
        height = int(height)
    except (TypeError, ValueError):
        width, height = SCREEN_WIDTH, SCREEN_HEIGHT
    width = max(MIN_WINDOW[0], min(MAX_WINDOW[0], width))
    height = max(MIN_WINDOW[1], min(MAX_WINDOW[1], height))
    return (width - (width % 2), height - (height % 2))


class Renderer:
    def __init__(self, size=None):
        pygame.init()
        self.on_resize = None  # set by the client; called after the window changed
        want = normalize_size(*(size or (SCREEN_WIDTH, SCREEN_HEIGHT)))
        self.screen = self._set_mode(want)
        # Menus are laid out for a fixed 1000x700 canvas and blitted into the
        # window as a whole, so their geometry never depends on the window size.
        self.ui = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
        self.surface = pygame.Surface((INTERNAL_WIDTH, INTERNAL_HEIGHT))
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("monospace", 14)
        self.hud_font = pygame.font.SysFont("monospace", 20, bold=True)

    # ── window ──────────────────────────────────────────────────────
    @staticmethod
    def _set_mode(size):
        """Create the display surface, falling back on failure.

        A 4K window can exhaust the display memory on some drivers, where
        ``set_mode`` raises ``pygame.error``. Dropping ``RESIZABLE`` is the
        fallback: a plain window of the same size always works, so the player
        keeps the resolution they asked for. Fullscreen is deliberately not
        offered -- a fullscreen surface cannot be resized by dragging, which is
        the point of this window.
        """
        try:
            return pygame.display.set_mode(size, pygame.RESIZABLE)
        except pygame.error:
            pygame.display.quit()
            pygame.display.init()
            return pygame.display.set_mode(size)

    def set_window_size(self, size) -> tuple[int, int]:
        """Resize the window and point the engine at the new pixel grid.

        ``const.W``/``const.H`` are what the renderer uses for every screen-space
        quantity (ray columns, HUD anchors, crosshair, overlays), so they must
        follow the window or the picture would be drawn for the old size and
        only scaled at the end. The bridge owns ``gs.screen`` and re-allocates
        it to these numbers through ``on_resize``.
        """
        width, height = normalize_size(*size)
        if self.screen.get_size() != (width, height):
            self.screen = self._set_mode((width, height))

        from engine import constants as const

        const.W, const.H = width, height

        if self.on_resize is not None:
            self.on_resize((width, height))
        return (width, height)

    @property
    def window_size(self) -> tuple[int, int]:
        return self.screen.get_size()

    def handle_resize_event(self, event) -> tuple[int, int] | None:
        """Apply a ``VIDEORESIZE`` event; returns the applied size, or ``None``.

        A drag generates dozens of events per second and every one of them would
        mean a new window surface plus a new full-screen ``Surface`` for the
        engine. The client therefore applies at most one size per frame: the
        game loop forwards the events the input handler drained
        (``InputHandler.window_events``), the menus read the queue themselves,
        and both keep only the last size of the frame.
        """
        if event.size == self.screen.get_size():
            return None
        return self.set_window_size(event.size)

    # ── menu presentation ───────────────────────────────────────────
    def canvas_rect(self) -> pygame.Rect:
        """Where the menu canvas actually lands inside the window.

        The single source of truth for the output geometry: ``present()`` and
        ``capture_menu_frame()`` blit through these numbers, and
        ``to_canvas_pos()`` inverts them. Deriving the offset from the raw
        ``scale`` instead would be off by a pixel -- ``_letterbox_fit``
        truncates, so the scale it implies is slightly smaller than the ideal
        one.
        """
        size = self.screen.get_size()
        dst_w, dst_h = self._letterbox_fit(size)
        return pygame.Rect(
            (size[0] - dst_w) // 2, (size[1] - dst_h) // 2, dst_w, dst_h
        )

    def to_canvas_pos(self, pos) -> tuple[float, float] | None:
        """Map a window position back onto the menu canvas.

        Menu hit-testing is done in canvas coordinates, but pygame reports
        ``event.pos`` in window pixels, and the canvas is scaled and letterboxed
        on the way out. Without this inverse a click at the visual centre of a
        button misses it at every window size other than 1000x700.

        Returns ``None`` when the point lands outside the canvas (in the black
        bars), which must not activate anything.
        """
        dst = self.canvas_rect()
        if dst.w <= 0 or dst.h <= 0 or not dst.colliderect(
            pygame.Rect(int(pos[0]), int(pos[1]), 1, 1)
        ):
            return None
        return (
            (pos[0] - dst.x) * SCREEN_WIDTH / dst.w,
            (pos[1] - dst.y) * SCREEN_HEIGHT / dst.h,
        )

    def present(self) -> None:
        """Scale the virtual menu canvas onto the window (letterboxed).

        Deliberately does not call ``display.flip()``: every caller already
        flips, and a menu frame is the only thing on the display surface here.
        Nearest-neighbour (``scale``, not ``smoothscale``) is both ~2.3x cheaper
        at 4K and the honest choice: the menu is authored on a 1000x700 pixel
        grid, so a filtered upscale would only blur it.
        """
        size = self.screen.get_size()
        if size == (SCREEN_WIDTH, SCREEN_HEIGHT):
            self.screen.blit(self.ui, (0, 0))
            return
        dst = self.canvas_rect()
        self.screen.fill(LETTERBOX)
        self.screen.blit(
            pygame.transform.scale(self.ui, (dst.w, dst.h)),
            (dst.x, dst.y),
        )

    def capture_menu_frame(self, target: pygame.Surface | None = None) -> pygame.Surface:
        """Copy the current window content into a menu-canvas-sized surface.

        The caller keeps the result and blits it back per frame. Capturing once
        matters: at 4K this is a 33 Mpixel scale, and doing it on every paused
        frame would rescale the same image 60 times a second for nothing.
        """
        target = target or pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
        size = self.screen.get_size()
        if size == (SCREEN_WIDTH, SCREEN_HEIGHT):
            target.blit(self.screen, (0, 0))
        else:
            dst = self.canvas_rect()
            target.fill(LETTERBOX)
            target.blit(
                pygame.transform.scale(self.screen, (dst.w, dst.h)),
                ((SCREEN_WIDTH - dst.w) // 2, (SCREEN_HEIGHT - dst.h) // 2),
            )
        return target

    @staticmethod
    def _letterbox_fit(size: tuple[int, int]) -> tuple[int, int]:
        scale = min(size[0] / SCREEN_WIDTH, size[1] / SCREEN_HEIGHT)
        return (max(1, int(SCREEN_WIDTH * scale)), max(1, int(SCREEN_HEIGHT * scale)))

    # ── legacy low-res helpers (unused by the current game loop) ────
    def render_3d(
        self,
        walls: list[tuple[int, float, int]],
        player_health: int,
        player_name: str,
        other_players: list[dict] | None = None,
    ) -> None:
        self.surface.fill(CEILING_COLOR, (0, 0, INTERNAL_WIDTH, INTERNAL_HEIGHT // 2))
        self.surface.fill(FLOOR_COLOR, (0, INTERNAL_HEIGHT // 2, INTERNAL_WIDTH, INTERNAL_HEIGHT // 2))

        for column, depth, wall_type in walls:
            if depth <= 0:
                depth = 0.001

            draw_height = min(INTERNAL_HEIGHT, int(PROJ_COEFF / depth))
            top = (INTERNAL_HEIGHT - draw_height) // 2
            bottom = top + draw_height

            base_color = WALL_COLORS.get(wall_type, (150, 150, 150))
            shade = max(0.15, 1.0 - depth / 15.0)
            color = tuple(int(c * shade) for c in base_color)

            pygame.draw.line(self.surface, color, (column, max(0, top)), (column, min(INTERNAL_HEIGHT, bottom)))

    def render_minimap(
        self,
        game_map,
        px: float, py: float, angle: float,
        other_players: list[dict] | None = None,
        map_offset_x: int = 4, map_offset_y: int = 4,
        tile_size: int = 3,
    ) -> None:
        for y in range(game_map.height):
            for x in range(game_map.width):
                tile = game_map.get_tile(x, y)
                color = (80, 80, 80) if tile != 0 else (30, 30, 30)
                rect = (
                    map_offset_x + x * tile_size,
                    map_offset_y + y * tile_size,
                    tile_size - 1,
                    tile_size - 1,
                )
                pygame.draw.rect(self.surface, color, rect)

        px_mini = int(map_offset_x + px * tile_size)
        py_mini = int(map_offset_y + py * tile_size)
        pygame.draw.circle(self.surface, (0, 255, 0), (px_mini, py_mini), 2)

        line_len = tile_size * 2
        end_x = int(px_mini + math.cos(angle) * line_len)
        end_y = int(py_mini + math.sin(angle) * line_len)
        pygame.draw.line(self.surface, (0, 255, 0), (px_mini, py_mini), (end_x, end_y))

        if other_players:
            for op in other_players:
                opx = int(map_offset_x + op["x"] * tile_size)
                opy = int(map_offset_y + op["y"] * tile_size)
                pygame.draw.circle(self.surface, (255, 0, 0), (opx, opy), 2)

    def render_hud(self, health: int, player_name: str, fps: float, ping_ms: float) -> None:
        hp_text = self.hud_font.render(f"HP: {health}", True, (255, 50, 50))
        self.surface.blit(hp_text, (INTERNAL_WIDTH - 80, INTERNAL_HEIGHT - 20))

        info = f"FPS: {fps:.0f}  Ping: {ping_ms:.0f}ms"
        info_text = self.font.render(info, True, (200, 200, 200))
        self.surface.blit(info_text, (4, INTERNAL_HEIGHT - 14))

        name_text = self.font.render(player_name, True, (0, 255, 0))
        self.surface.blit(name_text, (4, 4))

    def flip(self) -> None:
        scaled = pygame.transform.scale(self.surface, (SCREEN_WIDTH, SCREEN_HEIGHT))
        self.screen.blit(scaled, (0, 0))
        pygame.display.flip()
        self.clock.tick(60)

    def get_fps(self) -> float:
        return self.clock.get_fps()

    def cleanup(self) -> None:
        pygame.quit()
