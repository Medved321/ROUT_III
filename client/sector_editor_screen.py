"""Sector map editor screen (tasks 4.3, 4.4).

Canvas with pan/zoom/snap coordinates, tool buttons, per-selection property
panel, floor/ceiling texture palette and a two-group thing palette
(enemies ``ENEMY_NAMES`` / items ``ITEM_NAMES`` plus ``player1``/``exit``),
a persistent base layer cache (grid + sector fills + linedefs, redrawn only
when geometry/viewport changes) and an on-screen FPS meter.
"""
from __future__ import annotations

import os
import time

import pygame

from client.sector_editor import (
    STAIR_RISE_DEFAULT, SectorEditor, new_room, save_mt1_with_bsp,
)
from client.map_editor import ENEMY_NAMES, ITEM_NAMES
from client.ui_layout import clip_tail, ellipsize, fit_width, layout_row
from shared import text_map
from shared.text_map import WALL_TEXTURE_ORDER

BG = (14, 14, 20)
PANEL = (28, 32, 46)
TEXT = (235, 235, 235)
MUTED = (150, 150, 165)
ACCENT = (210, 70, 70)
GREEN = (90, 210, 120)
CYAN = (80, 200, 240)
GRID_LINE = (52, 55, 70)
SELECT = (70, 95, 150)

MAPS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "maps"
)

FLAT_PALETTE = ["floor", "ceiling", "lava", "bridge", "platform"]
THING_SPECIAL = [("player1", (90, 220, 90)), ("exit", (240, 160, 40))]
TOOL_NAMES = {"select": "Выбор", "vertex": "Вершина", "line": "Линия",
              "sector": "Сектор", "thing": "Вещь", "stair": "Лестница"}
# Tool keys are assigned to the first six number keys, so adding a tool must not
# silently renumber the others: the mapping is explicit for exactly that reason.
TOOL_KEYS = {pygame.K_1: "select", pygame.K_2: "vertex", pygame.K_3: "line",
             pygame.K_4: "sector", pygame.K_5: "thing", pygame.K_6: "stair"}

# 2D overlay for each linedef kind: impassable wall, walkable step, portal
LINE_KINDS = {
    "wall": ((205, 208, 214), 3),
    "step": ((240, 190, 90), 2),
    "open": (CYAN, 1),
}

# panel layout rows (the panel must fit any window height, so the thing
# palette is columnized against the space that is left)
THING_ROW_H = 20
TEX_ROW_H = 15
BASE_CHIPS_H = 48
HINT_BLOCK_H = 34
TEX_CHIP_NAMES = {"front": "ПЕРЕД", "back": "ЗАД",
                   "lower": "НИЗ", "upper": "ВЕРХ"}
# map picker: row pitch of the dropdown, and the padding kept below the last
# visible row so the box never reaches the window edge
MAP_ROW_H = 22
MAP_BOTTOM_PAD = 8

# bottom toolbar: height of one button, the gap inside a button around its
# label, the gap between buttons, and the margin kept off the canvas edge
TOOLBAR_H = 32
TOOLBAR_PAD = 8
TOOLBAR_GAP = 6
TOOLBAR_MARGIN = 8


class SectorEditorScreen:
    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self.editor = SectorEditor(new_room(1600, 1600))
        self.map_name = "sector1"
        self._name_focused = False
        self._map_list_open = False
        self._map_files: list[str] = self._scan_map_files()
        self._map_rows: list[tuple[str, pygame.Rect]] = []
        self.status = "ЛКМ — действие | колесо — зум | ПКМ/СКМ — панорама | Ctrl+Z/Y — отмена/повтор"

        self.title_font = pygame.font.SysFont("monospace", 24, bold=True)
        self.font = pygame.font.SysFont("monospace", 16)
        self.small_font = pygame.font.SysFont("monospace", 13)
        self.tiny_font = pygame.font.SysFont("monospace", 12)

        self.tool = "select"
        self.thing_type = "imp"
        self.sel_thing: int | None = None
        self.sel_line: int | None = None
        self.sel_vertex: int | None = None
        self.sel_sector: int | None = None
        self.contour: list[int] = []
        self.precious: tuple[int, float, float] | None = None
        self._drag_v: int | None = None
        # staircase input: four world points, two per wall, then build
        self.stair_pts: list[tuple[float, float]] = []
        self.stair_rise = STAIR_RISE_DEFAULT
        self._pan = False
        self._pre: tuple[float, float] = (0.0, 0.0)
        self._prop_text = ""
        self._prop_chips: dict[str, pygame.Rect] = {}
        self._height_fields: dict[str, pygame.Rect] = {}
        self._height_edit: str | None = None      # field being typed into
        self._height_buffer = ""
        self._base_chips: dict[str, tuple[pygame.Rect, tuple]] = {}
        # side-texture palette target: which sidedef and which slot it writes
        self._tex_side = "front"
        self._tex_target = "lower"
        self._tex_rects: dict[str, pygame.Rect] = {}
        self._tex_chips: dict[str, pygame.Rect] = {}

        self.zoom = 0.35
        self.cam = (800.0, 800.0)

        self.panel = pygame.Rect(width - 292, 8, 284, height - 16)
        self._base = None
        self._cache_dirty = True

        self._fps_n = 0
        self._fps_t0 = time.time()
        self.fps = 0.0

        # The bottom toolbar is laid out from its label widths, not from hand
        # picked constants: ten labels do not fit on one 1000px row, and the old
        # fixed rects silently overlapped ("play" and "back" shared 114px of
        # canvas, so clicks landed on whichever was tested first). The toolbar
        # may now occupy more than one row, and the canvas is derived from the
        # height it actually took.
        self.buttons = self._build_toolbar()
        self.canvas = pygame.Rect(
            TOOLBAR_MARGIN, 56, width - 300 - TOOLBAR_MARGIN * 2,
            height - 56 - self._toolbar_h - 8,
        )
        self.name_box = pygame.Rect(330, 17, 170, 28)
        self.map_list_btn = pygame.Rect(506, 17, 84, 28)
        self._build_panel()

    def _button_labels(self) -> dict[str, str]:
        return {"mode": "Тайлы ▶", "snap": "Сетка ✓" if self.editor.snap_on else "Сетка",
                "undo": "Отмена", "redo": "Повтор", "save": "Сохранить",
                "load": "Загрузить", "play": "▶ Играть", "back": "Назад",
                "save_mts": "Сохранить .mts", "load_mts": "Открыть .mts"}

    def _build_toolbar(self) -> dict[str, pygame.Rect]:
        """Bottom toolbar: one button per action, sized to its own label.

        ``snap`` toggles its label ("Сетка" / "Сетка ✓"), so the row is rebuilt
        whenever that changes; the widths only ever grow by the tick glyph, and
        ``_build_toolbar`` is cheap enough to call from the toggle.
        """
        labels = self._button_labels()
        items = [(key, fit_width(text, self.tiny_font, TOOLBAR_PAD))
                 for key, text in labels.items()]
        layout = layout_row(items, height=TOOLBAR_H, y=0,
                            margin=TOOLBAR_MARGIN, gap=TOOLBAR_GAP,
                            canvas_width=self.width, min_width=48)
        self._toolbar_h = layout["height"]
        # The layout is anchored at y=0, so the whole block is shifted down by a
        # single constant. Shifting each row by its own height would collapse
        # every row onto the same y and stack the buttons on top of each other.
        top = self.height - 8 - self._toolbar_h
        return {key: rect.move(0, top) for key, rect in layout["rects"].items()}

    # ── layout ────────────────────────────────────────────────────────
    def _build_panel(self) -> None:
        x = self.panel.x + 6
        w = self.panel.width - 12
        y = self.panel.y + 34
        # Tool buttons wrap instead of being squeezed into a fixed 5-wide
        # grid: a label that no longer fits its own box is a label the player
        # cannot read, and `per = (w - 16) // 5` had no way to say so.  The row
        # height comes back from layout_row, so the block below is pushed down
        # by however many rows were actually used.
        tools = [(key, fit_width(TOOL_NAMES[key], self.tiny_font, 6))
                 for key in TOOL_NAMES]
        tlayout = layout_row(tools, height=30, y=y, margin=0, gap=4,
                             canvas_width=w, min_width=40)
        self._tool_buttons = {key: rect.move(x, 0)
                              for key, rect in tlayout["rects"].items()}
        y += tlayout["height"] + 8
        self._prop_rect = pygame.Rect(x, y, w, 148)
        self._palette_label_y = self._prop_rect.y + self._prop_rect.height + 8
        # contextual block: wall textures for a two-sided linedef, otherwise
        # the sector flats. Only one of them is ever useful, so they share the
        # same space and the hint row below keeps a fixed y.
        self._tex_block_h = 52 + (-(-len(WALL_TEXTURE_ORDER) // 3)) * TEX_ROW_H
        self._flat_block_h = 16 + (-(-len(FLAT_PALETTE) // 3)) * 20 + 4
        context_h = max(self._tex_block_h, self._flat_block_h)
        y = self._build_palette(x, self._palette_label_y, w, context_h)
        self._build_base_chips(x, y + 6, w)
        self._context_y = y + 6 + BASE_CHIPS_H
        self._build_tex_palette(x, self._context_y, w)
        self._hint_y = min(self._context_y + context_h, self.panel.bottom - 8)

    def _build_palette(self, x: int, y: int, w: int, context_h: int) -> int:
        """Thing palette: as many columns as the panel height allows."""
        entries = [(name, ch) for ch, name in ENEMY_NAMES.items()]
        entries += [(name, ch) for ch, name in ITEM_NAMES.items()]
        # everything below the palette has a reserved height
        bottom = (self.panel.bottom - 8 - HINT_BLOCK_H - BASE_CHIPS_H - 6
                  - context_h)
        rows = max(1, (bottom - y) // THING_ROW_H)
        cols = max(1, -(-len(entries) // rows))
        cw = max(28, (w - (cols - 1) * 4) // cols)
        self._palette_items = [[] for _ in range(cols)]
        for i, (name, ch) in enumerate(entries):
            col = i // rows
            ci = i % rows
            rect = pygame.Rect(x + col * (cw + 4), y + ci * THING_ROW_H, cw, 20)
            self._palette_items[col].append((name, rect, ch))
        return y + min(rows, len(entries)) * THING_ROW_H

    def _build_base_chips(self, x: int, y: int, w: int) -> None:
        """player1 / exit chips, always visible above the contextual block."""
        cw = (w - 8) // 2
        self._base_chips = {}
        for i, (name, col) in enumerate(THING_SPECIAL):
            rect = pygame.Rect(x + (i % 2) * (cw + 8),
                               y + (i // 2) * 22, cw, 20)
            self._base_chips[name] = (rect, col)
        self._base_chips_y = y

    def _build_tex_palette(self, x: int, y: int, w: int) -> None:
        """Fixed layout of the wall texture palette (drawn only when used)."""
        self._tex_rects = {}
        self._tex_chips = {}
        self._tex_label_y = y
        y += 16
        cw = (w - 12) // 4
        for i, (key, name) in enumerate(TEX_CHIP_NAMES.items()):
            rect = pygame.Rect(x + i * (cw + 4), y, cw, 18)
            self._tex_chips[key] = rect
        y += 22
        self._tex_value_y = y
        y += 14
        cw2 = (w - 8) // 3
        for i, name in enumerate(WALL_TEXTURE_ORDER):
            rect = pygame.Rect(x + (i % 3) * (cw2 + 4), y + (i // 3) * TEX_ROW_H,
                               cw2, TEX_ROW_H - 2)
            self._tex_rects[name] = rect
        self._tex_block_h = 52 + (-(-len(WALL_TEXTURE_ORDER) // 3)) * TEX_ROW_H

    def _fit(self, text: str, maxw: int) -> str:
        """Trim a label to the chip width so nothing spills over the panel."""
        return ellipsize(text, self.tiny_font, maxw)

    # ── camera ───────────────────────────────────────────────────────
    def _to_screen(self, wx: float, wy: float) -> tuple[int, int]:
        return (int(self.canvas.x + self.canvas.w / 2
                    + (wx - self.cam[0]) * self.zoom),
                int(self.canvas.y + self.canvas.h / 2
                    + (wy - self.cam[1]) * self.zoom))

    def _to_world(self, sx: float, sy: float) -> tuple[float, float]:
        return ((sx - self.canvas.x - self.canvas.w / 2) / self.zoom + self.cam[0],
                (sy - self.canvas.y - self.canvas.h / 2) / self.zoom + self.cam[1])

    def _dirty(self) -> None:
        self._cache_dirty = True

    def frame_around(self) -> None:
        xs = [v.x for v in self.editor.verts] or [0.0]
        ys = [v.y for v in self.editor.verts] or [0.0]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        zx = (self.canvas.w - 80) / max(1.0, x1 - x0)
        zy = (self.canvas.h - 80) / max(1.0, y1 - y0)
        self.zoom = max(0.05, min(2.0, min(zx, zy)))
        self.cam = ((x0 + x1) / 2, (y0 + y1) / 2)
        self._dirty()

    # ── picking ───────────────────────────────────────────────────────
    @staticmethod
    def _dist_seg(px: float, py: float, x1: float, y1: float,
                  x2: float, y2: float) -> float:
        dx, dy = x2 - x1, y2 - y1
        t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy or 1.0)
        t = max(0.0, min(1.0, t))
        qx, qy = x1 + t * dx, y1 + t * dy
        return ((px - qx) ** 2 + (py - qy) ** 2) ** 0.5

    def _pick(self, wx: float, wy: float) -> None:
        r = 14.0 / self.zoom
        best_thing = None
        best_d = r
        for i, t in enumerate(self.editor.things):
            d = ((t.x - wx) ** 2 + (t.y - wy) ** 2) ** 0.5
            if d < best_d:
                best_d = d
                best_thing = i
        if best_thing is not None:
            self._set_sel(thing=best_thing)
            return
        best_line, best_d = None, 8.0 / self.zoom
        for i, ld in enumerate(self.editor.lines):
            v1, v2 = self.editor.verts[ld.v1], self.editor.verts[ld.v2]
            d = self._dist_seg(wx, wy, v1.x, v1.y, v2.x, v2.y)
            if d < best_d:
                best_d = d
                best_line = i
        if best_line is not None:
            self._set_sel(line=best_line)
            return
        best_v, best_d = None, 10.0 / self.zoom
        for i, v in enumerate(self.editor.verts):
            d = ((v.x - wx) ** 2 + (v.y - wy) ** 2) ** 0.5
            if d < best_d:
                best_d = d
                best_v = i
        if best_v is not None:
            self._set_sel(vertex=best_v)
            return
        self._set_sel(sector=self.editor.sm.find_sector_at(wx, wy))

    def _set_sel(self, thing=None, line=None, vertex=None, sector=None) -> None:
        self.sel_thing, self.sel_line, self.sel_vertex, self.sel_sector = (
            thing, line, vertex, sector)

    def _sync_sel(self) -> None:
        """Drop a selection that no longer points at a live entity.

        A selection is an array index, and the arrays are replaced wholesale by
        a map load and renumbered by every delete, so an index kept across such
        an operation may address nothing. Reading it used to take the whole
        editor loop down with an IndexError, so every selection is validated
        before it is read; a stale field is cleared exactly like ``_set_sel()``
        with no arguments. Reloading a map SHALL clear the selection outright
        (see _load / _load_mts) — an index cannot carry over to another map.
        """
        ed = self.editor
        for attr, items in (("sel_thing", ed.things), ("sel_line", ed.lines),
                            ("sel_vertex", ed.verts), ("sel_sector", ed.sectors)):
            sel = getattr(self, attr)
            if sel is not None and not 0 <= sel < len(items):
                setattr(self, attr, None)

    def _nearest_vertex(self, wx, wy) -> int | None:
        r = 12.0 / self.zoom
        for i, v in enumerate(self.editor.verts):
            if (v.x - wx) ** 2 + (v.y - wy) ** 2 < r * r:
                return i
        return None

    def _pick_thing(self, wx, wy) -> int | None:
        r = 14.0 / self.zoom
        for i, t in enumerate(self.editor.things):
            if (t.x - wx) ** 2 + (t.y - wy) ** 2 < r * r:
                return i
        return None

    # ── input ─────────────────────────────────────────────────────────
    def _scan_map_files(self) -> list[str]:
        """Map names available in maps/, each listed once.

        A map can be present as both .mts and .mt1, so the names are collected
        into a set: without it the picker offered the same map twice.
        """
        if not os.path.isdir(MAPS_DIR):
            return []
        names = {os.path.splitext(fn)[0] for fn in os.listdir(MAPS_DIR)
                 if fn.lower().endswith((".mts", ".mt1"))}
        return sorted(names)

    def _load_by_name(self, name: str) -> None:
        self.map_name = name
        self._name_focused = False
        self._map_list_open = False
        self._load_mts()

    def handle(self, events) -> str | None:
        for event in events:
            if event.type == pygame.MOUSEBUTTONDOWN:
                if self.name_box.collidepoint(event.pos):
                    self._name_focused = True
                    self._map_list_open = False
                    continue
                if self.map_list_btn.collidepoint(event.pos):
                    self._map_list_open = not self._map_list_open
                    if self._map_list_open:
                        self._map_files = self._scan_map_files()
                    continue
                # the open dropdown overlays the canvas, so it is hit-tested
                # first: its rows must get the click, and a miss closes it
                if self._map_list_open:
                    hit = None
                    # Same geometry the rows are drawn from, not the cached list.
                    for name, rect in self._map_dropdown_geometry()[1]:
                        if rect.collidepoint(event.pos):
                            hit = name
                            break
                    if hit is not None:
                        self._load_by_name(hit)
                        continue
                    self._map_list_open = False
                if self.canvas.collidepoint(event.pos):
                    if event.button == 1:
                        self._canvas_left_down(event.pos)
                    elif event.button in (2, 3):
                        self._pan = True
                        self._pre = self._to_world(*event.pos)
                    continue
                self._name_focused = False
                act = self._panel_down(event.pos)
                if act:
                    return act
            elif event.type == pygame.MOUSEMOTION:
                if self._pan:
                    w = self._to_world(*event.pos)
                    self.cam = (self.cam[0] - (w[0] - self._pre[0]),
                                self.cam[1] - (w[1] - self._pre[1]))
                    self._pre = w
                    self._dirty()
                elif (self.tool == "vertex" and self._drag_v is not None
                        and event.buttons[0]):
                    w = self._to_world(*event.pos)
                    self.editor.move_vertex(self._drag_v, w[0], w[1])
                    self._dirty()
            elif event.type == pygame.MOUSEBUTTONUP:
                self._pan = False
                self._drag_v = None
            elif event.type == pygame.MOUSEWHEEL:
                if self.canvas.collidepoint(pygame.mouse.get_pos()):
                    self.zoom = max(0.05, min(4.0,
                                    self.zoom * (1.2 if event.y > 0 else 1 / 1.2)))
                    self._dirty()
            elif event.type == pygame.KEYDOWN:
                act = self._key(event)
                if act:
                    return act
        return None

    def _key(self, event) -> str | None:
        k = event.key
        if self._height_edit is not None:
            if k == pygame.K_RETURN:
                self._commit_height()
                return None
            if k == pygame.K_ESCAPE:
                self._height_edit, self._height_buffer = None, ""
                return None
            if k == pygame.K_BACKSPACE:
                self._height_buffer = self._height_buffer[:-1]
                return None
            if event.unicode and event.unicode in "0123456789-":
                if len(self._height_buffer) < 6:
                    self._height_buffer += event.unicode
            return None
        if self._name_focused:
            if k == pygame.K_RETURN:
                self._name_focused = False
                return None
            if k == pygame.K_ESCAPE:
                self._name_focused = False
                return None
            if k == pygame.K_BACKSPACE:
                self.map_name = self.map_name[:-1]
                return None
            if event.unicode and event.unicode.isprintable() and len(self.map_name) < 24:
                self.map_name += event.unicode
            return None
        if k == pygame.K_ESCAPE:
            # Esc consumes half-finished tool input before it leaves the
            # screen: backing out of a staircase with three points already
            # placed should not silently throw them away, and the second Esc
            # still goes back.
            if self.stair_pts or self.contour or self.precious is not None:
                self._reset_tool_state()
                self.status = "Ввод инструмента сброшен"
                self._dirty()
                return None
            return "back"
        if k == pygame.K_F5:
            if self.editor.validate():
                self.status = "Ошибки: " + "; ".join(self.editor.validate()[:3])
                return None
            self._save()
            return "play"
        if k == pygame.K_z and event.mod & pygame.KMOD_CTRL:
            self.editor.undo()
            self._dirty()
            return None
        if k == pygame.K_y and event.mod & pygame.KMOD_CTRL:
            self.editor.redo()
            self._dirty()
            return None
        if k == pygame.K_g:
            self.editor.snap_on = not self.editor.snap_on
            self._dirty()
            self._snap_label_changed()
            return None
        if k == pygame.K_f:
            self.frame_around()
            return None
        tool = TOOL_KEYS.get(k)
        if tool:
            if self.tool != tool:
                self._reset_tool_state()
            self.tool = tool
            return None
        if k == pygame.K_RETURN and self.tool == "sector" and self.contour:
            return self._create_sector()
        return None

    def _canvas_left_down(self, pos) -> None:
        wx, wy = self._to_world(*pos)
        if self.tool == "vertex":
            iv = self._nearest_vertex(wx, wy)
            if iv is not None:
                self._drag_v = iv
            else:
                self.editor.add_vertex(wx, wy)
                self._dirty()
            return
        if self.tool == "line":
            iv = self._nearest_vertex(wx, wy)
            if iv is None:
                iv = self.editor.add_vertex(wx, wy)
                self._dirty()
            if self.precious is None:
                self.precious = (iv, wx, wy)
            else:
                try:
                    self.editor.add_line(self.precious[0], iv)
                    self.status = f"Линия (фронт: сектор {self.editor.cur_sector})"
                except ValueError as exc:
                    self.status = str(exc)
                self.precious = None
                self._dirty()
            return
        if self.tool == "sector":
            for li, ld in enumerate(self.editor.lines):
                v1, v2 = self.editor.verts[ld.v1], self.editor.verts[ld.v2]
                if self._dist_seg(wx, wy, v1.x, v1.y, v2.x, v2.y) < 8.0 / self.zoom:
                    if li not in self.contour:
                        self.contour.append(li)
                        self.status = f"Контур: {len(self.contour)} рёбер"
                    return
            return
        if self.tool == "thing":
            self.editor.add_thing(wx, wy, self.thing_type)
            self._dirty()
            return
        if self.tool == "stair":
            self._stair_click(wx, wy)
            return
        self._pick(wx, wy)

    # ── stairs ───────────────────────────────────────────────────────
    def _reset_tool_state(self) -> None:
        """Drop half-finished tool input when the tool changes.

        A pending contour or a half-collected staircase belongs to the tool
        that is being left: keeping it means the next tool's first click is
        interpreted as the second half of the previous one.
        """
        self.contour = []
        self.precious = None
        self._drag_v = None
        self.stair_pts = []

    def _stair_click(self, wx: float, wy: float) -> None:
        """Collect two points per wall, then build on the fourth."""
        if len(self.stair_pts) == 4:
            self.stair_pts = []
        self.stair_pts.append((wx, wy))
        self._dirty()
        if len(self.stair_pts) < 4:
            self.status = (f"Лестница: {len(self.stair_pts)}/4 точек — по две "
                           "на одной стене и две на другой")
            return
        info, errs = self.editor.build_stair(self.stair_pts, self.stair_rise)
        self.stair_pts = []
        if errs:
            self.status = errs[0]
            return
        self._dirty()
        rails = "проёмы" if info["rails_open"] else "сплошные стены"
        self.status = (f"Лестница: {info['steps']} ступ. по {info['rise']} "
                       f"({info['low']}→{info['high']}), бок {rails}")

    def _panel_down(self, pos) -> str | None:
        for key, rect in self.buttons.items():
            if rect.collidepoint(pos):
                return self._button(key)
        for key, rect in self._tool_buttons.items():
            if rect.collidepoint(pos):
                if self.tool != key:
                    self._reset_tool_state()
                self.tool = key
                return None
        for name, (rect, _col) in self._base_chips.items():
            if rect.collidepoint(pos):
                self.thing_type = name
                return None
        for gi, items in enumerate(self._palette_items):
            for name, rect, ch in items:
                if rect.collidepoint(pos):
                    self.thing_type = name
                    return None
        for key, rect in self._prop_chips.items():
            if rect.collidepoint(pos):
                return self._prop(key)
        for key, rect in self._height_fields.items():
            if rect.collidepoint(pos):
                self._height_edit = key
                s = self.editor.sectors[self.sel_sector]
                self._height_buffer = str(s.floor_h if key == "floor"
                                          else s.ceil_h)
                return None
        for key, rect in self._tex_chips.items():
            if rect.collidepoint(pos):
                if key in ("front", "back"):
                    self._tex_side = key
                else:
                    self._tex_target = key
                return None
        for name, rect in self._tex_rects.items():
            if rect.collidepoint(pos):
                self._assign_tex(name)
                return None
        return None

    def _snap_label_changed(self) -> None:
        """Re-lay the toolbar when "Сетка" gains its tick.

        The label is the button's width, so the row shifts by the width of the
        tick glyph. Re-running the layout keeps the hitboxes equal to the drawn
        boxes instead of letting the label poke out of its own button.
        """
        self.buttons = self._build_toolbar()
        self.canvas.height = self.height - 56 - self._toolbar_h - 8
        self._build_panel()
        self._dirty()

    def _button(self, key: str) -> str | None:
        if key == "mode":
            return "tile"
        if key == "snap":
            self.editor.snap_on = not self.editor.snap_on
            self._dirty()
            self._snap_label_changed()
            return None
        if key == "undo":
            self.editor.undo()
            self._dirty()
            return None
        if key == "redo":
            self.editor.redo()
            self._dirty()
            return None
        if key == "save":
            self._save()
            return None
        if key == "load":
            self._load()
            return None
        if key == "save_mts":
            self._save_mts()
            return None
        if key == "load_mts":
            self._load_mts()
            return None
        if key == "play":
            errs = self.editor.validate()
            if errs:
                self.status = "Ошибки: " + "; ".join(errs[:3])
                return None
            self._save()
            return "play"
        if key == "back":
            return "back"
        return None

    def _create_sector(self) -> str | None:
        sec, errs = self.editor.sector_from_contour(self.contour)
        if errs:
            self.status = "; ".join(errs)
            return None
        self.contour = []
        self._set_sel(sector=sec)
        self.status = f"Сектор {sec} создан"
        self._dirty()
        return None

    def _height_status(self, got) -> None:
        """Show the pair the clamp actually applied (a rejected value is visible)."""
        self.status = f"пол {got[0]}  потол {got[1]}"

    def _commit_height(self) -> None:
        """Apply the typed height through the shared clamp and show what stuck."""
        field, buf = self._height_edit, self._height_buffer
        self._height_edit, self._height_buffer = None, ""
        if field is None or self.sel_sector is None or not buf:
            return
        try:
            want = int(float(buf))
        except ValueError:
            self.status = f"Не число: {buf}"
            return
        got = self.editor.set_sector_heights(
            self.sel_sector,
            floor_h=want if field == "floor" else None,
            ceil_h=want if field == "ceil" else None,
        )
        if got is None:
            return
        applied = got[0] if field == "floor" else got[1]
        label = "пол" if field == "floor" else "потол"
        if applied != want:
            self.status = (f"{label} {want} скорректирован до {applied} "
                           f"(диапазон и пол<=потол)")
        else:
            self.status = f"{label} = {applied}"
        self._dirty()

    def _prop(self, key: str) -> str | None:
        if self.sel_line is not None:
            if key == "two":
                self.editor.set_two_sided(self.sel_line, True)
            elif key == "one":
                self.editor.set_two_sided(self.sel_line, False)
            elif key == "solid":
                ld = self.editor.lines[self.sel_line]
                want = not ld.solid
                if self.editor.set_line_solid(self.sel_line, want):
                    self.status = ("Сплошная стена" if want
                                   else "Проём (ступени)")
                else:
                    self.status = "Признак только для двусторонней линии"
            elif key == "flip":
                self.editor.flip_line(self.sel_line)
            elif key == "split":
                ld = self.editor.lines[self.sel_line]
                v1, v2 = self.editor.verts[ld.v1], self.editor.verts[ld.v2]
                self.editor.split_line_at(self.sel_line,
                                          (v1.x + v2.x) / 2, (v1.y + v2.y) / 2)
        elif self.sel_sector is not None and self.sel_sector >= 0:
            s = self.editor.sectors[self.sel_sector]
            if key == "floor_up":
                got = self.editor.set_sector_heights(self.sel_sector,
                                                     floor_h=s.floor_h + 16)
            elif key == "floor_dn":
                got = self.editor.set_sector_heights(self.sel_sector,
                                                     floor_h=s.floor_h - 16)
            elif key == "ceil_up":
                got = self.editor.set_sector_heights(self.sel_sector,
                                                     ceil_h=s.ceil_h + 16)
            elif key == "ceil_dn":
                got = self.editor.set_sector_heights(self.sel_sector,
                                                     ceil_h=s.ceil_h - 16)
            elif key == "light":
                got = None
                self.editor.set_sector_props(self.sel_sector,
                                             light=(s.light + 48) % 256)
            if got is not None:
                self._height_status(got)
        elif self.sel_thing is not None:
            t = self.editor.things[self.sel_thing]
            if key == "angle":
                self.editor.set_thing_angle(self.sel_thing, t.angle + 45)
            elif key == "del":
                self.editor.delete_thing(self.sel_thing)
                self._set_sel()
        self._dirty()
        return None

    # ── save / load ───────────────────────────────────────────────────
    def _save(self) -> None:
        os.makedirs(MAPS_DIR, exist_ok=True)
        path = os.path.join(MAPS_DIR, f"{self.map_name or 'sector1'}.mt1")
        try:
            save_mt1_with_bsp(self.editor.sm, path)
            self.status = f"Сохранено: maps/{os.path.basename(path)}"
        except Exception as exc:
            self.status = f"Ошибка сохранения: {exc}"

    def _mts_path(self, suffix: str) -> str:
        return os.path.join(MAPS_DIR, f"{self.map_name or 'sector1'}.{suffix}")

    def _save_mts(self) -> None:
        os.makedirs(MAPS_DIR, exist_ok=True)
        path = self._mts_path("mts")
        try:
            text_map.save(self.editor.sm, path)
            self._map_files = self._scan_map_files()
            self.status = f"Сохранено: maps/{os.path.basename(path)}"
        except Exception as exc:
            self.status = f"Ошибка сохранения .mts: {exc}"

    def _load_mts(self) -> None:
        path = self._mts_path("mts")
        if not os.path.exists(path):
            self.status = f"Не найдено: maps/{os.path.basename(path)}"
            return
        try:
            sm = text_map.load(path, validate=False)
            self.editor = SectorEditor(sm)
            self._set_sel()
            self.frame_around()
        except Exception as exc:
            self.status = f"Ошибка загрузки .mts: {exc}"
            return
        errs = sm.validate()
        name = f"maps/{os.path.basename(path)}"
        self.status = (f"Загружено: {name} — ошибок: {'; '.join(errs[:2])}"
                       if errs else f"Загружено: {name}")

    def _load(self) -> None:
        name = self.map_name or "sector1"
        path = os.path.join(MAPS_DIR, f"{name}.mt1")
        legacy = os.path.join(MAPS_DIR, f"{name}.json")
        if os.path.exists(path):
            shown = os.path.basename(path)
        elif os.path.exists(legacy):
            path = legacy
            shown = os.path.basename(legacy)  # legacy .json import
        else:
            self.status = f"Не найдено: maps/{os.path.basename(path)}"
            return
        try:
            self.editor = SectorEditor(self.editor.sm.__class__.load(path))
            self._set_sel()
            self.status = f"Загружено: {shown}"
            self.frame_around()
        except Exception as exc:
            self.status = f"Ошибка загрузки: {exc}"

    # ── drawing ───────────────────────────────────────────────────────
    def draw(self, surface) -> None:
        surface.fill(BG)
        self._sync_sel()
        title = self.title_font.render("РЕДАКТОР СЕКТОРОВ", True, ACCENT)
        surface.blit(title, (10, 14))
        self._draw_map_picker(surface)
        self._draw_canvas(surface)
        self._draw_panel(surface)
        self._draw_buttons(surface)
        self._draw_map_dropdown(surface)
        status = self.tiny_font.render(self.status, True, GREEN)
        surface.blit(status, (12, self.height - 22))

        self._fps_n += 1
        now = time.time()
        if now - self._fps_t0 >= 1.0:
            self.fps = self._fps_n / (now - self._fps_t0)
            self._fps_n = 0
            self._fps_t0 = now
        fps_txt = self.tiny_font.render(f"FPS: {self.fps:4.1f}", True, MUTED)
        surface.blit(fps_txt, (self.canvas.right - 84, self.canvas.bottom - 20))

    def _draw_map_picker(self, surface) -> None:
        lbl = self.tiny_font.render("Карта:", True, MUTED)
        surface.blit(lbl, (self.name_box.x - 44, self.name_box.y + 7))
        pygame.draw.rect(surface, PANEL, self.name_box, border_radius=4)
        pygame.draw.rect(surface, SELECT if self._name_focused else MUTED,
                         self.name_box, 2, border_radius=4)
        # Tail-clipped: the map name is typed here, so the characters being
        # entered must stay visible even when the name is longer than the box.
        name_txt = self.tiny_font.render(
            clip_tail(
                self.map_name + ("|" if self._name_focused else ""),
                self.tiny_font, self.name_box.width - 12,
            ), True, TEXT)
        surface.blit(name_txt, (self.name_box.x + 6, self.name_box.y + 6))
        pygame.draw.rect(surface, PANEL, self.map_list_btn, border_radius=4)
        pygame.draw.rect(surface, MUTED, self.map_list_btn, 1, border_radius=4)
        btn_txt = self.tiny_font.render("" if self._map_list_open else "Карты ▾",
                                        True, TEXT)
        surface.blit(btn_txt, (self.map_list_btn.centerx - btn_txt.get_width() // 2,
                               self.map_list_btn.centery - btn_txt.get_height() // 2))

    def _map_dropdown_geometry(self):
        """The dropdown's box, its rows and the hidden-file count.

        Single source for both drawing and hit-testing: ``handle`` used to read
        ``self._map_rows``, which only existed after a ``draw()``, so a click
        landing before the first frame (or after a resize) hit-tested a stale
        list. Both paths now ask here, so the clickable row is the drawn row.
        """
        y = self.map_list_btn.bottom + 6
        box_w = self.name_box.width + self.map_list_btn.width + 12
        budget = max(1, (self.height - MAP_BOTTOM_PAD - y) // MAP_ROW_H)
        shown = self._map_files[:budget]
        hidden = len(self._map_files) - len(shown)
        if hidden and budget > 1:
            shown = shown[:budget - 1]       # keep a slot for the "…ещё N" row
            hidden = len(self._map_files) - len(shown)
        rows = [
            (name, pygame.Rect(self.name_box.x + 3, y + i * MAP_ROW_H,
                               box_w - 6, MAP_ROW_H - 2))
            for i, name in enumerate(shown)
        ]
        n_rows = len(rows) + (1 if hidden else 0)
        box = pygame.Rect(self.name_box.x, y - 4, box_w, n_rows * MAP_ROW_H + 10)
        return box, rows, hidden, y + len(rows) * MAP_ROW_H

    def _draw_map_dropdown(self, surface) -> None:
        """The open map list, drawn last so the canvas cannot bury it.

        The picker chrome lives in _draw_map_picker, but the dropdown is an
        overlay: it is blitted after the canvas, the panel and the buttons, so
        the rows stay visible and clickable. Height is clamped to the window —
        the row budget is whatever fits below the button — and a "…ещё N" row
        accounts for the files that did not fit instead of hiding them.
        """
        if not self._map_list_open:
            self._map_rows = []
            return
        box, rows, hidden, y = self._map_dropdown_geometry()
        pygame.draw.rect(surface, (20, 22, 34), box, border_radius=5)
        pygame.draw.rect(surface, MUTED, box, 1, border_radius=5)
        self._map_rows = rows
        for name, rect in rows:
            bg = SELECT if name == self.map_name else (40, 44, 62)
            pygame.draw.rect(surface, bg, rect, border_radius=3)
            row = self.tiny_font.render(
                ellipsize(name, self.tiny_font, rect.width - 12), True, TEXT)
            surface.blit(row, (rect.x + 6, rect.y + 3))
            y += MAP_ROW_H
        if hidden:
            more = self.tiny_font.render(f"…ещё {hidden}", True, MUTED)
            surface.blit(more, (self.name_box.x + 8, y))
        elif not self._map_files:
            empty = self.tiny_font.render(
                "(в maps/ нет .mts / .mt1)", True, MUTED)
            surface.blit(empty, (self.name_box.x + 8, y))

    def _draw_buttons(self, surface) -> None:
        labels = self._button_labels()
        for key, rect in self.buttons.items():
            pygame.draw.rect(surface, PANEL, rect, border_radius=5)
            pygame.draw.rect(surface, MUTED, rect, 1, border_radius=5)
            # The label is cut to the button it sits in, so it can never bleed
            # into the neighbour even if a font renders wider than measured.
            txt = self.tiny_font.render(
                ellipsize(labels.get(key, key), self.tiny_font,
                          rect.width - TOOLBAR_PAD), True, TEXT)
            surface.blit(txt, (rect.centerx - txt.get_width() // 2,
                               rect.centery - txt.get_height() // 2))

    def _draw_canvas(self, surface) -> None:
        rect = self.canvas
        if self._cache_dirty or self._base is None:
            self._base = pygame.Surface((rect.w, rect.h))
            self._render_base(self._base)
            self._cache_dirty = False
        surface.blit(self._base, (rect.x, rect.y))
        pygame.draw.rect(surface, ACCENT, rect, 1)
        self._draw_things(surface)
        self._draw_selector(surface)
        if self.precious is not None and self.tool == "line":
            sx, sy = self._to_screen(self.precious[1], self.precious[2])
            pygame.draw.circle(surface, CYAN, (sx, sy), 4)
        if self.tool == "sector" and self.contour:
            txt = self.tiny_font.render(
                f"Контур: {len(self.contour)} рёбер (Enter — создать)", True, CYAN)
            surface.blit(txt, (rect.x + 8, rect.y + 8))

    def _render_base(self, surf) -> None:
        z = self.zoom
        cx, cy = self.cam
        ox = surf.get_width() / 2 - cx * z
        oy = surf.get_height() / 2 - cy * z
        step = self.editor.snap_step
        if z * step >= 6:
            wx0 = (0 - ox) / z
            wx1 = (surf.get_width() - ox) / z
            wy0 = (0 - oy) / z
            wy1 = (surf.get_height() - oy) / z
            for gx in range(int(wx0 // step), int(wx1 // step) + 1):
                sx = int(ox + gx * step * z)
                pygame.draw.line(surf, GRID_LINE, (sx, 0), (sx, surf.get_height()))
            for gy in range(int(wy0 // step), int(wy1 // step) + 1):
                sy = int(oy + gy * step * z)
                pygame.draw.line(surf, GRID_LINE, (0, sy), (surf.get_width(), sy))
        def local(px: float, py: float) -> tuple[int, int]:
            return int(ox + px * z), int(oy + py * z)

        polys = self.editor.sm._ensure_polys()
        for si, loops in enumerate(polys):
            light = self.editor.sectors[si].light if si < len(self.editor.sectors) else 128
            lv = 14 + int(light / 255 * 72)
            col = (lv, lv + 8, lv + 22)
            for points, _bbox, _area in loops:
                scr = [local(px, py) for px, py in points]
                pygame.draw.polygon(surf, col, scr)
        for li, ld in enumerate(self.editor.lines):
            v1, v2 = self.editor.verts[ld.v1], self.editor.verts[ld.v2]
            kind = self.editor.line_wall_kind(li)
            col, width = LINE_KINDS[kind]
            a = local(v1.x, v1.y)
            b = local(v2.x, v2.y)
            pygame.draw.line(surf, col, a, b, width)
            if kind == "step" and width > 1:
                # a walkable step gets a lip tick so it reads as a ledge and
                # not as a plain portal line
                pygame.draw.line(surf, col, (a[0], a[1] + 3), (b[0], b[1] + 3), 1)
        for v in self.editor.verts:
            a = local(v.x, v.y)
            pygame.draw.rect(surf, (250, 250, 120), (a[0] - 2, a[1] - 2, 4, 4))

    def _draw_things(self, surface) -> None:
        for i, t in enumerate(self.editor.things):
            if t.type in ENEMY_NAMES.values():
                col = (220, 90, 90)
            elif t.type in ITEM_NAMES.values():
                col = (240, 220, 90)
            else:
                col = dict(THING_SPECIAL).get(t.type, (120, 120, 200))
            a = self._to_screen(t.x, t.y)
            pygame.draw.circle(surface, col, a, max(3, int(6 * min(1.0, self.zoom))))
            if self.sel_thing == i:
                pygame.draw.circle(surface, (255, 255, 255), a,
                                   max(5, int(7 * min(1.0, self.zoom))), 1)

    def _draw_selector(self, surface) -> None:
        if self.sel_vertex is not None and 0 <= self.sel_vertex < len(self.editor.verts):
            a = self._to_screen(*self._vertex_xy(self.sel_vertex))
            pygame.draw.circle(surface, ACCENT, a, 6, 1)
        if self.sel_line is not None and 0 <= self.sel_line < len(self.editor.lines):
            ld = self.editor.lines[self.sel_line]
            a = self._to_screen(*self._vertex_xy(ld.v1))
            b = self._to_screen(*self._vertex_xy(ld.v2))
            pygame.draw.line(surface, ACCENT, a, b, 3)
        if self.tool == "sector":
            for li in self.contour:
                if 0 <= li < len(self.editor.lines):
                    ld = self.editor.lines[li]
                    a = self._to_screen(*self._vertex_xy(ld.v1))
                    b = self._to_screen(*self._vertex_xy(ld.v2))
                    pygame.draw.line(surface, (255, 200, 80), a, b, 3)

    def _vertex_xy(self, idx: int) -> tuple[float, float]:
        v = self.editor.verts[idx]
        return v.x, v.y

    def _draw_panel(self, surface) -> None:
        pygame.draw.rect(surface, PANEL, self.panel, border_radius=6)
        pygame.draw.rect(surface, MUTED, self.panel, 1, border_radius=6)
        for key, rect in self._tool_buttons.items():
            pygame.draw.rect(surface, SELECT if key == self.tool else (40, 44, 62),
                             rect, border_radius=4)
            pygame.draw.rect(surface, MUTED, rect, 1, border_radius=4)
            txt = self.tiny_font.render(TOOL_NAMES[key], True, TEXT)
            surface.blit(txt, (rect.x + 4, rect.y + 7))
        self._draw_props(surface)
        self._draw_palette(surface)

    def _tool_selection_label(self) -> str:
        if self.sel_thing is not None:
            return f"вещь {self.editor.things[self.sel_thing].type}"
        if self.sel_line is not None:
            ld = self.editor.lines[self.sel_line]
            return f"лайндеф {self.sel_line} ({'2ст.' if ld.two_sided else '1ст.'})"
        if self.sel_vertex is not None:
            return f"вершина {self.sel_vertex}"
        if self.sel_sector is not None:
            return f"сектор {self.sel_sector}"
        return "ничего"

    def _draw_props(self, surface) -> None:
        x = self.panel.x + 6
        head = self.tiny_font.render("СВОЙСТВА — " + self._tool_selection_label(),
                                     True, ACCENT)
        surface.blit(head, (x, self._prop_rect.y - 6))
        chips: dict[str, str] = {}
        self._prop_text = ""
        self._height_fields = {}
        if self.sel_line is not None:
            chips = {"two": "2-ст.", "one": "1-ст.", "flip": "Flip",
                     "split": "Split"}
            ld = self.editor.lines[self.sel_line]
            if ld.two_sided:
                chips["solid"] = "сплошная" if ld.solid else "проём"
        elif self.sel_sector is not None and self.sel_sector >= 0:
            s = self.editor.sectors[self.sel_sector]
            self._prop_text = (f"пол {s.floor_h}  потол {s.ceil_h}  "
                               f"свет {s.light}  пол:{s.floor_tex}  пот:{s.ceil_tex}")
            chips = {"floor_up": "пол ▲", "floor_dn": "пол ▼",
                     "ceil_up": "потол ▲", "ceil_dn": "потол ▼", "light": "свет +"}
        elif self.sel_thing is not None:
            t = self.editor.things[self.sel_thing]
            self._prop_text = f"{t.type} @ ({t.x:.0f},{t.y:.0f}) угол {t.angle}"
            chips = {"angle": "поворот +45", "del": "удалить"}
        cy = self._prop_rect.y + 26
        cw = (self.panel.width - 12 - 8) // 2
        self._prop_chips = {}
        for i, (key, label) in enumerate(chips.items()):
            rect = pygame.Rect(x + (i % 2) * (cw + 8), cy + (i // 2) * 22, cw, 20)
            pygame.draw.rect(surface, (40, 44, 62), rect, border_radius=3)
            txt = self.tiny_font.render(label, True, TEXT)
            surface.blit(txt, (rect.x + 4, rect.y + 3))
            self._prop_chips[key] = rect
        # numeric floor/ceiling fields; they show the value actually applied
        if self.sel_sector is not None and self.sel_sector >= 0:
            s = self.editor.sectors[self.sel_sector]
            fy = cy + ((len(chips) + 1) // 2) * 22 + 6
            for i, (key, label, value) in enumerate(
                    (("floor", "пол", s.floor_h), ("ceil", "потол", s.ceil_h))):
                rect = pygame.Rect(x + i * (cw + 8), fy, cw, 20)
                focused = self._height_edit == key
                pygame.draw.rect(surface, SELECT if focused else (28, 31, 45),
                                 rect, border_radius=3)
                pygame.draw.rect(surface, MUTED, rect, 1, border_radius=3)
                shown = (self._height_buffer if focused
                         else f"{label} {value}")
                txt = self.tiny_font.render(shown + ("▌" if focused else ""),
                                            True, TEXT)
                surface.blit(txt, (rect.x + 4, rect.y + 3))
                self._height_fields[key] = rect
            hint = self.tiny_font.render(
                "клик по полю — ввод, Enter — применить", True, MUTED)
            surface.blit(hint, (x, fy + 22))
        if self._prop_text:
            extra = self.tiny_font.render(self._prop_text, True, MUTED)
            surface.blit(extra, (x, self._prop_rect.y + 118))
        if self.tool == "sector" and self.contour:
            txt = self.tiny_font.render("Enter — создать сектор", True, GREEN)
            surface.blit(txt, (x, self._prop_rect.y + 134))

    def _draw_palette(self, surface) -> None:
        x = self.panel.x + 6
        hdr = self.tiny_font.render("ВЕЩИ — ВРАГИ / ПРЕДМЕТЫ / БАЗА", True, ACCENT)
        surface.blit(hdr, (x, self._palette_label_y - 4))
        for items in self._palette_items:
            for name, rect, ch in items:
                bg = SELECT if self.thing_type == name else (40, 44, 62)
                pygame.draw.rect(surface, bg, rect, border_radius=3)
                txt = self.tiny_font.render(
                    self._fit(f"{ch} {name}", rect.width - 4), True, TEXT)
                surface.blit(txt, (rect.x + 2, rect.y + 2))
        # base entities chips (player1 / exit)
        for name, (rect, col) in self._base_chips.items():
            bg = SELECT if self.thing_type == name else (40, 44, 62)
            pygame.draw.rect(surface, bg, rect, border_radius=3)
            txt = self.tiny_font.render(self._fit(name, rect.width - 8), True, col)
            surface.blit(txt, (rect.x + 4, rect.y + 2))
        # contextual block: wall textures for a two-sided line, else flats
        if not self._draw_tex_palette(surface, x):
            flh = self.tiny_font.render("ФЛАТЫ (для сектора)", True, ACCENT)
            surface.blit(flh, (x, self._context_y))
            y = self._context_y + 16
            cw3 = (self.panel.width - 12 - 8) // 3 - 4
            for i, name in enumerate(FLAT_PALETTE):
                rect = pygame.Rect(x + (i % 3) * (cw3 + 4),
                                   y + (i // 3) * 20, cw3, 18)
                pygame.draw.rect(surface, (40, 44, 62), rect, border_radius=3)
                txt = self.tiny_font.render(
                    self._fit(name, rect.width - 4), True, TEXT)
                surface.blit(txt, (rect.x + 2, rect.y + 1))
        hint = self.tiny_font.render(
            "1-5 — инструменты | G — сетка | F — кадр | F5 — играть", True, MUTED)
        surface.blit(hint, (x, self._hint_y))
        hint2 = self.tiny_font.render("CTRL+Z/Y отмена/повтор | ESC назад", True, MUTED)
        surface.blit(hint2, (x, self._hint_y + 14))

    def _tex_side_idx(self) -> int | None:
        """Sidedef index the texture palette currently writes to."""
        if self.sel_line is None:
            return None
        ld = self.editor.lines[self.sel_line]
        if not ld.two_sided:
            return None
        return ld.back if self._tex_side == "back" else ld.front

    def _draw_tex_palette(self, surface, x: int) -> bool:
        """Draw the wall texture palette for the selected two-sided linedef.

        Only the front/back sidedef slot chosen by the chips is written, so the
        step lips of a line can be textured as seen from either sector.
        """
        side_idx = self._tex_side_idx()
        if side_idx is None:
            return False
        sd = self.editor.sides[side_idx]
        lbl = self.tiny_font.render(
            f"ТЕКСТУРЫ СТЕН — сторона "
            f"{'ЗАДНЯЯ' if self._tex_side == 'back' else 'ПЕРЕДНЯЯ'}", True, ACCENT)
        surface.blit(lbl, (x, self._tex_label_y))
        for key, rect in self._tex_chips.items():
            on = (self._tex_side == key if key in ("front", "back")
                  else self._tex_target == key)
            pygame.draw.rect(surface, SELECT if on else (40, 44, 62),
                             rect, border_radius=3)
            txt = self.tiny_font.render(
                self._fit(TEX_CHIP_NAMES[key], rect.width - 6), True, TEXT)
            surface.blit(txt, (rect.x + 3, rect.y + 2))
        sel = getattr(sd, self._tex_target, "-") or "-"
        cur = self.tiny_font.render(self._fit(f"{self._tex_target}: {sel}",
                                              self.panel.width - 20), True, MUTED)
        surface.blit(cur, (x, self._tex_value_y))
        for name, rect in self._tex_rects.items():
            bg = SELECT if name == sel else (40, 44, 62)
            pygame.draw.rect(surface, bg, rect, border_radius=3)
            txt = self.tiny_font.render(
                self._fit(name, rect.width - 4), True, TEXT)
            surface.blit(txt, (rect.x + 2, rect.y + 1))
        return True

    def _assign_tex(self, name: str) -> None:
        side_idx = self._tex_side_idx()
        if side_idx is None:
            return
        if not self.editor.set_side_tex(side_idx, **{self._tex_target: name}):
            return
        self.status = (f"Текстура {self._tex_target} стороны "
                       f"{'задней' if self._tex_side == 'back' else 'передней'}: "
                       f"{name}")
        self._dirty()