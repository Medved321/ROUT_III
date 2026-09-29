"""Tile map editor screen.

Paint solid/structural tiles on a square grid, save/load as ``maps/*.json``
(``GameMap`` format).  Launched from the lobby.
"""
from __future__ import annotations

import os

import pygame

from shared.map_data import GameMap
from client.map_editor import (
    MAT_COLORS, TB_ITEMS, TileEditor, CHAR_TO_TILE,
)
from client.ui_layout import (
    BUTTON_GAP, CANVAS_MARGIN, clip_tail, ellipsize, fit_width, layout_row,
)

BG = (18, 18, 26)
PANEL = (30, 34, 48)
TEXT = (235, 235, 235)
MUTED = (150, 150, 165)
ACCENT = (210, 70, 70)
GREEN = (90, 210, 120)
GRID_LINE = (60, 62, 78)
SELECT = (70, 95, 150)

MAPS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "maps"
)

# toolbar / stacking metrics: the top row of buttons, then the name field, then
# the canvas and palette. The toolbar is sized from its labels, so everything
# below it is derived from the height it actually took.
TOOLBAR_Y = 50
TOOLBAR_H = 30
TOOLBAR_PAD = 8
TOOLBAR_GAP = 8
CANVAS_MARGIN = 12
NAME_BOX_W = 220
NAME_BOX_H = 30
PALETTE_SPLIT = 0.64


class MapEditorScreen:
    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self.editor = TileEditor(20)
        self.brush = '#'
        self.map_name = "map1"
        self.status = "ЛКМ — рисовать | ПКМ — стирать | Ctrl+Z — отменить"
        self._name_focused = False
        self._painting = 0  # 0 none, 1 paint, 2 erase
        self._drag_undo: list[list[str]] | None = None

        self.title_font = pygame.font.SysFont("monospace", 26, bold=True)
        self.font = pygame.font.SysFont("monospace", 16)
        self.small_font = pygame.font.SysFont("monospace", 13)

        # Toolbar, name field and canvas stack from the top: the toolbar is
        # sized from its labels and may wrap, so everything below it is derived
        # from the height it actually took rather than from fixed y values.
        # (The hand-placed rects used to end at 988px of a 1000px canvas and
        # ran straight through the name field at x=660.)
        self.buttons = self._build_toolbar()
        self._name_row_y = TOOLBAR_Y + self._toolbar_h + TOOLBAR_GAP
        self.name_box = pygame.Rect(
            self.width - NAME_BOX_W - CANVAS_MARGIN, self._name_row_y,
            NAME_BOX_W, NAME_BOX_H,
        )
        top = self._name_row_y + NAME_BOX_H + TOOLBAR_GAP
        body_h = self.height - top - CANVAS_MARGIN
        self.canvas = pygame.Rect(
            CANVAS_MARGIN, top, self.width * PALETTE_SPLIT - CANVAS_MARGIN * 2, body_h
        )
        self.palette_area = pygame.Rect(
            self.canvas.right + TOOLBAR_GAP, top,
            self.width - self.canvas.right - TOOLBAR_GAP - CANVAS_MARGIN, body_h,
        )

        self._palette_items = self._build_palette_rects()

    def _button_labels(self) -> dict[str, str]:
        return {
            "size_minus": "-", "size_plus": "+", "new": "Новый",
            "clear": "Очистить", "save": "Сохранить", "load": "Загрузить",
            "play": "▶ Играть", "back": "Назад", "sector_mode": "Сектора ▶",
        }

    def _build_toolbar(self) -> dict[str, pygame.Rect]:
        """Top toolbar: one button per action, sized to its own label.

        The ``-``/``+`` steppers are a pair around the current grid size, so they
        are laid out first and the size label is placed in the gap between them
        rather than over a button.
        """
        labels = self._button_labels()
        items = [(key, fit_width(text, self.font, TOOLBAR_PAD, min_width=40))
                 for key, text in labels.items()]
        layout = layout_row(items, height=TOOLBAR_H, y=0, margin=CANVAS_MARGIN,
                            gap=TOOLBAR_GAP, canvas_width=self.width)
        self._toolbar_h = layout["height"]
        return {key: rect.move(0, TOOLBAR_Y) for key, rect in layout["rects"].items()}

    # ── layout helpers ──────────────────────────────────────────────
    def _build_palette_rects(self) -> list[tuple]:
        items = []
        y = self.palette_area.y + 6
        item_h = 26
        for entry in TB_ITEMS:
            rect = pygame.Rect(self.palette_area.x + 6, y, self.palette_area.width - 12, item_h - 2)
            items.append((entry, rect))
            y += item_h
        return items

    def _cell_size(self) -> int:
        return max(4, min(self.canvas.width, self.canvas.height) // self.editor.sz)

    def _cell_at(self, pos) -> tuple[int, int] | None:
        if not self.canvas.collidepoint(pos):
            return None
        cell = self._cell_size()
        grid_px = cell * self.editor.sz
        ox = self.canvas.x + (self.canvas.width - grid_px) // 2
        oy = self.canvas.y + (self.canvas.height - grid_px) // 2
        cx = (pos[0] - ox) // cell
        cy = (pos[1] - oy) // cell
        if 0 <= cx < self.editor.sz and 0 <= cy < self.editor.sz:
            return int(cx), int(cy)
        return None

    # ── input ───────────────────────────────────────────────────────
    def handle(self, events) -> str | None:
        for event in events:
            if event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1:
                    action = self._on_left_down(event.pos)
                    if action:
                        return action
                elif event.button == 3:
                    self._painting = 2
                    self._drag_undo = self.editor.copy_grid()
                    self._apply_brush(event.pos, erase=True)
            elif event.type == pygame.MOUSEBUTTONUP:
                self._painting = 0
            elif event.type == pygame.MOUSEMOTION and self._painting:
                self._apply_brush(event.pos, erase=(self._painting == 2))
            elif event.type == pygame.KEYDOWN:
                if self._name_focused:
                    if event.key == pygame.K_RETURN:
                        self._name_focused = False
                    elif event.key == pygame.K_BACKSPACE:
                        self.map_name = self.map_name[:-1]
                    elif event.unicode and event.unicode.isprintable() and len(self.map_name) < 24:
                        self.map_name += event.unicode
                elif event.key == pygame.K_ESCAPE:
                    return "back"
                elif event.key == pygame.K_F5:
                    self._save()
                    return "play"
                elif event.key == pygame.K_z and (event.mod & pygame.KMOD_CTRL):
                    if self._drag_undo is not None:
                        self.editor.restore_grid(self._drag_undo)
                        self.status = "Отменено"
        return None

    def _on_left_down(self, pos) -> str | None:
        for key, rect in self.buttons.items():
            if rect.collidepoint(pos):
                return self._button(key)
        if self.name_box.collidepoint(pos):
            self._name_focused = True
            return None
        self._name_focused = False
        for entry, rect in self._palette_items:
            if rect.collidepoint(pos):
                if len(entry) == 2:
                    self.brush = entry[0]
                    self.status = f"Кисть: {entry[1]}"
                return None
        if self.canvas.collidepoint(pos):
            self._painting = 1
            self._drag_undo = self.editor.copy_grid()
            self._apply_brush(pos, erase=False)
        return None

    def _button(self, key: str) -> str | None:
        if key == "size_minus":
            self.editor.resize(max(8, self.editor.sz - 4))
            self.status = f"Размер: {self.editor.sz}x{self.editor.sz}"
        elif key == "size_plus":
            self.editor.resize(min(64, self.editor.sz + 4))
            self.status = f"Размер: {self.editor.sz}x{self.editor.sz}"
        elif key == "new":
            sz = self.editor.sz
            self.editor = TileEditor(sz)
            self.status = "Новая карта"
        elif key == "clear":
            for y in range(self.editor.sz):
                for x in range(self.editor.sz):
                    self.editor.set_cell(x, y, '.')
            self.status = "Очищено"
        elif key == "save":
            self._save()
        elif key == "load":
            self._load()
        elif key == "play":
            self._save()
            return "play"
        elif key == "back":
            return "back"
        elif key == "sector_mode":
            return "sector"
        return None

    def to_game_map(self) -> GameMap:
        return self.editor.to_game_map()

    def _apply_brush(self, pos, erase: bool) -> None:
        cell = self._cell_at(pos)
        if cell is None:
            return
        self.editor.set_cell(cell[0], cell[1], '.' if erase else self.brush)

    # ── save / load ─────────────────────────────────────────────────
    def _save(self) -> None:
        os.makedirs(MAPS_DIR, exist_ok=True)
        path = os.path.join(MAPS_DIR, f"{self.map_name or 'map'}.json")
        try:
            self.editor.to_game_map().save(path)
            self.status = f"Сохранено: maps/{os.path.basename(path)}"
        except Exception as exc:
            self.status = f"Ошибка сохранения: {exc}"

    def _load(self) -> None:
        path = os.path.join(MAPS_DIR, f"{self.map_name or 'map'}.json")
        if not os.path.exists(path):
            self.status = f"Не найдено: maps/{os.path.basename(path)}"
            return
        try:
            game_map = GameMap.load(path)
            self.editor = TileEditor.from_game_map(game_map)
            self.status = f"Загружено: {os.path.basename(path)} ({game_map.width}x{game_map.height})"
        except Exception as exc:
            self.status = f"Ошибка загрузки: {exc}"

    # ── drawing ─────────────────────────────────────────────────────
    def draw(self, surface) -> None:
        surface.fill(BG)

        title = self.title_font.render("РЕДАКТОР КАРТ", True, ACCENT)
        surface.blit(title, (14, 12))

        self._draw_buttons(surface)

        name_lbl = self.small_font.render("Имя:", True, MUTED)
        surface.blit(name_lbl, (self.name_box.x - 38, self.name_box.y + 7))
        pygame.draw.rect(surface, PANEL, self.name_box, border_radius=4)
        pygame.draw.rect(surface, SELECT if self._name_focused else MUTED,
                         self.name_box, 2, border_radius=4)
        # A long map name is cut to the field, keeping the tail visible so the
        # characters being typed stay on screen.
        shown = clip_tail(
            self.map_name + ("|" if self._name_focused else ""),
            self.font, self.name_box.width - 12,
        )
        name_txt = self.font.render(shown, True, TEXT)
        surface.blit(name_txt, (self.name_box.x + 6, self.name_box.y + 6))

        self._draw_canvas(surface)
        self._draw_palette(surface)

        status = self.small_font.render(self.status, True, GREEN)
        surface.blit(status, (14, self.height - 22))

    def _draw_buttons(self, surface) -> None:
        # The grid size belongs between the two steppers, in the gap the layout
        # left for it -- never over a button.
        minus = self.buttons["size_minus"]
        plus = self.buttons["size_plus"]
        size_txt = self.font.render(f"{self.editor.sz}", True, TEXT)
        if plus.x - minus.right >= size_txt.get_width() + 8:
            surface.blit(size_txt, (
                (minus.right + plus.x - size_txt.get_width()) // 2,
                minus.centery - size_txt.get_height() // 2,
            ))

        labels = self._button_labels()
        for key, rect in self.buttons.items():
            pygame.draw.rect(surface, PANEL, rect, border_radius=5)
            pygame.draw.rect(surface, MUTED, rect, 1, border_radius=5)
            txt = self.font.render(
                ellipsize(labels[key], self.font, rect.width - TOOLBAR_PAD), True, TEXT)
            surface.blit(txt, (rect.centerx - txt.get_width() // 2,
                               rect.centery - txt.get_height() // 2))

    def _draw_canvas(self, surface) -> None:
        pygame.draw.rect(surface, (12, 12, 18), self.canvas)
        cell = self._cell_size()
        sz = self.editor.sz
        grid_px = cell * sz
        ox = self.canvas.x + (self.canvas.width - grid_px) // 2
        oy = self.canvas.y + (self.canvas.height - grid_px) // 2

        for y in range(sz):
            for x in range(sz):
                ch = self.editor.grid[y][x]
                color = MAT_COLORS.get(ch, (90, 90, 90))
                rect = pygame.Rect(ox + x * cell, oy + y * cell, cell, cell)
                pygame.draw.rect(surface, color, rect)
                if cell >= 8:
                    pygame.draw.rect(surface, GRID_LINE, rect, 1)

        pygame.draw.rect(surface, ACCENT, pygame.Rect(ox, oy, grid_px, grid_px), 2)

    def _draw_palette(self, surface) -> None:
        pygame.draw.rect(surface, (22, 24, 34), self.palette_area, border_radius=6)
        pygame.draw.rect(surface, PANEL, self.palette_area, 1, border_radius=6)
        for entry, rect in self._palette_items:
            if len(entry) == 1:
                hdr = self.small_font.render(entry[0], True, ACCENT)
                surface.blit(hdr, (rect.x + 4, rect.y + 4))
                continue
            ch, label = entry
            if ch == self.brush:
                pygame.draw.rect(surface, SELECT, rect, border_radius=4)
            swatch = pygame.Rect(rect.x + 4, rect.y + 3, 20, rect.height - 6)
            pygame.draw.rect(surface, MAT_COLORS.get(ch, (90, 90, 90)), swatch)
            pygame.draw.rect(surface, GRID_LINE, swatch, 1)
            txt = self.font.render(f"{ch}  {label}  #{CHAR_TO_TILE.get(ch, 0)}",
                                   True, TEXT)
            surface.blit(txt, (rect.x + 32, rect.y + 4))
