from __future__ import annotations

import pygame

from client import map_library
from client.ui_layout import ellipsize, fit_width, layout_row

BG = (18, 18, 26)
PANEL = (30, 34, 48)
TEXT = (235, 235, 235)
MUTED = (150, 150, 165)
ACCENT = (210, 70, 70)
GREEN = (90, 210, 120)
SELECT = (70, 95, 150)
ROW_H = 48

UI_HINTS = "WASD — движение | Мышь — обзор | ЛКМ — огонь | Esc — пауза"
TRACTOR_HINTS = ("WASD — движение | Стрелки влево/вправо — обзор | "
                 "Пробел — огонь | Левый Shift — дэш")

# lobby buttons: height, gap between them, margin off the canvas edge, padding
# inside a button around its label, and the narrowest a button may get
BTN_H, BTN_GAP, BTN_MARGIN, BTN_PAD, BTN_MIN_W = 40, 24, 60, 10, 120

# Free-text rows under the buttons: the status/map pair shares the upper row,
# the control hints get the whole lower row.  Every band is measured once here.
BAND_MARGIN, BAND_GAP, BAND_BOTTOM_PAD = 20, 12, 8

# Map picker: the button sits in the map band, the list opens upward into the
# empty space above it. One row pitch is used for drawing and for hit-testing.
MAP_BTN_H, MAP_LIST_GAP, MAP_ROW_H = 22, 6, 22
MAP_LIST_MAX_ROWS, MAP_LIST_W = 9, 320
NO_MAP = "без карты"


class LobbyScreen:
    def __init__(self, width: int, height: int, player_name: str, server_label: str):
        self.width = width
        self.height = height
        self.player_name = player_name
        self.server_label = server_label
        # Set by the client from the active control mode, so the lobby never
        # advertises keys the player is not actually holding.
        self.control_hints: str = UI_HINTS
        self.party_list: list[dict] = []
        self.status: str = "Выберите партию или создайте новую"
        self._name_focused = False
        self._selected = 0
        self._scroll = 0
        self.create_mode = 'pve'  # 'pve' (враги) | 'pvp' (друзья против друзей)
        self.custom_map_label: str | None = None
        self.custom_map_error: str | None = None
        self._map_list_open = False
        self._map_names: list[str] = []
        self._map_scroll = 0

        self.title_font = pygame.font.SysFont("monospace", 46, bold=True)
        self.header_font = pygame.font.SysFont("monospace", 22, bold=True)
        self.font = pygame.font.SysFont("monospace", 18)
        self.small_font = pygame.font.SysFont("monospace", 14)

        self.name_box = pygame.Rect(330, 118, 420, 32)
        self.list_area = pygame.Rect(100, 208, 760, 290)

        # Two rows of buttons, each sized to its own label and wrapped if it
        # does not fit. The rects are built once and used for both drawing and
        # hit-testing, so the clickable area is exactly the painted box.
        self._button_labels = {
            "create": "Создать [P]",
            "editor": "Секторный редактор [E]", "settings": "Настройки [S]",
            "quit": "Выход [Esc]",
        }
        top_row = ["create", "editor"]
        bot_row = ["settings", "quit"]
        y = self.list_area.bottom + 18
        self.buttons: dict[str, pygame.Rect] = {}
        for keys, row_y in ((top_row, y), (bot_row, y + BTN_H + BTN_GAP)):
            layout = layout_row(
                [(k, fit_width(self._button_labels[k], self.font, BTN_PAD,
                               min_width=BTN_MIN_W)) for k in keys],
                height=BTN_H, y=row_y, margin=BTN_MARGIN, gap=BTN_GAP,
                canvas_width=self.width,
            )
            self.buttons.update(layout["rects"])
        self._button_bottom = y + 2 * BTN_H + BTN_GAP
        self.map_btn = self._map_button_rect()

        # Kept as attributes for callers that reach for a single button.
        self.create_btn = self.buttons["create"]
        self.editor_btn = self.buttons["editor"]
        self.settings_btn = self.buttons["settings"]
        self.quit_btn = self.buttons["quit"]

    def set_custom_map(self, label: str | None, error: str | None = None) -> None:
        """Show the selected map (or the last error) without owning its bytes."""
        self.custom_map_label = label
        self.custom_map_error = error

    def _map_button_rect(self) -> pygame.Rect:
        """The picker button lives at the right end of the map text band."""
        band = self.bottom_bands()["map"]
        width = fit_width("Карты", self.small_font, BTN_PAD, min_width=64)
        return pygame.Rect(band.right - width, band.y - 4, width, MAP_BTN_H)

    def _map_budget(self) -> int:
        """Rows that fit between the canvas top and the picker button."""
        bottom = self.map_btn.top - MAP_LIST_GAP
        return max(1, min(MAP_LIST_MAX_ROWS, (bottom - 8) // MAP_ROW_H))

    def _map_items(self) -> list[str]:
        return [NO_MAP] + list(self._map_names)

    def _max_map_scroll(self) -> int:
        items = self._map_items()
        budget = self._map_budget()
        visible = min(len(items), budget - (1 if len(items) > budget else 0))
        return max(0, len(items) - visible)

    def _map_dropdown_geometry(self):
        """Box, selectable rows, the "…ещё N" row and its hidden count.

        Drawing and hit-testing both read this result, so the row that is
        clicked is exactly the row that was painted.
        """
        items = self._map_items()
        budget = self._map_budget()
        hidden = len(items) > budget
        visible = min(len(items), budget - (1 if hidden else 0))
        start = max(0, min(self._map_scroll, max(0, len(items) - visible)))
        shown = items[start:start + visible]
        remaining = len(items) - len(shown)
        bottom = self.map_btn.top - MAP_LIST_GAP
        top = bottom - (len(shown) + (1 if remaining else 0)) * MAP_ROW_H
        x = self.map_btn.right - MAP_LIST_W
        rows = [(name, pygame.Rect(x + 3, top + i * MAP_ROW_H,
                                   MAP_LIST_W - 6, MAP_ROW_H - 2))
                for i, name in enumerate(shown)]
        more = (pygame.Rect(x + 3, bottom - MAP_ROW_H, MAP_LIST_W - 6,
                            MAP_ROW_H - 2) if remaining else None)
        box = pygame.Rect(x, top - 4, MAP_LIST_W,
                          (bottom - top) + 8)
        return box, rows, more, remaining

    def set_state(self, party_list: list[dict], status: str) -> None:
        self.party_list = party_list
        self.status = status
        if self.party_list:
            self._selected = min(self._selected, len(self.party_list) - 1)
        else:
            self._selected = 0

    def bottom_bands(self) -> dict[str, pygame.Rect]:
        """Named text bands below the buttons, shared by drawing and checks.

        Status and custom map split the upper row; the (possibly much longer)
        control hints own the full lower row.  The y positions are derived from
        the buttons and the font height, so growing a hint string can never
        slide it into the status band or back over a button.
        """
        line_h = self.small_font.get_height()
        hint_y = self.height - BAND_BOTTOM_PAD - line_h
        status_y = max(self._button_bottom + 6,
                       hint_y - line_h - BAND_GAP)
        hint_y = status_y + line_h + BAND_GAP
        inner_w = self.width - 2 * BAND_MARGIN
        status_w = max(120, inner_w // 2 - BAND_GAP)
        status = pygame.Rect(BAND_MARGIN, status_y, status_w, line_h)
        custom_map = pygame.Rect(status.right + BAND_GAP, status_y,
                                 self.width - BAND_MARGIN - status.right - BAND_GAP,
                                 line_h)
        hints = pygame.Rect(BAND_MARGIN, hint_y, inner_w, line_h)
        return {"status": status, "map": custom_map, "hints": hints}

    def party_row_bands(self, row: pygame.Rect, party: dict) -> dict[str, pygame.Rect]:
        """Text bands inside one party row, shared by drawing and checks.

        The mode badge used to be painted on the same lower strip as the
        player nicknames, so a long nickname and ``PvP``/``PvE`` covered each
        other. Name/count own the upper strip, mode/nicknames own the lower
        one, and every pair is separated by a gap measured from the fonts.
        """
        pad = 10
        big_h = self.font.get_height()
        small_h = self.small_font.get_height()
        top_y = row.y + 3
        bottom_y = row.bottom - small_h - 3

        count_text = f"{len(party['players'])} игрок(а)"
        count_w = self.small_font.size(count_text)[0]
        count = pygame.Rect(row.right - pad - count_w,
                            top_y + (big_h - small_h) // 2, count_w, small_h)
        name = pygame.Rect(row.x + pad, top_y,
                           max(1, count.x - pad - row.x - pad), big_h)

        mode_w = self.small_font.size("PvP")[0]
        mode = pygame.Rect(row.x + pad, bottom_y, mode_w, small_h)
        players = pygame.Rect(mode.right + 8, bottom_y,
                              max(1, row.right - pad - mode.right - 8), small_h)
        return {"name": name, "count": count, "mode": mode, "players": players}

    def handle(self, events) -> str | None:
        result = None
        for event in events:
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                pos = event.pos
                if self._map_list_open:
                    # The list is an overlay: its rows are tested before any
                    # lobby button, and a click outside it only closes it.
                    _box, rows, more, _hidden = self._map_dropdown_geometry()
                    picked = next((name for name, rect in rows
                                   if rect.collidepoint(pos)), None)
                    if picked is not None:
                        self._map_list_open = False
                        self._name_focused = False
                        result = ("clear_map" if picked == NO_MAP
                                  else f"map:{picked}")
                        continue
                    if more is not None and more.collidepoint(pos):
                        self._map_scroll = min(
                            self._map_scroll + self._map_budget() - 1,
                            self._max_map_scroll())
                        continue
                    self._map_list_open = False
                    self._name_focused = False
                    continue
                if self.map_btn.collidepoint(pos):
                    self._map_list_open = True
                    self._map_names = map_library.list_map_names()
                    self._map_scroll = 0
                    self._name_focused = False
                    continue
                if self.name_box.collidepoint(pos):
                    self._name_focused = True
                else:
                    self._name_focused = False
                if self.create_btn.collidepoint(pos):
                    result = "create"
                elif self.editor_btn.collidepoint(pos):
                    result = "editor"
                elif self.settings_btn.collidepoint(pos):
                    result = "settings"
                elif self.quit_btn.collidepoint(pos):
                    result = "quit"
                else:
                    party_id = self._hit_party(pos)
                    if party_id is not None:
                        result = f"join:{party_id}"
            elif event.type == pygame.MOUSEWHEEL:
                if self._map_list_open:
                    self._map_scroll = max(0, min(
                        self._map_scroll - event.y, self._max_map_scroll()))
                else:
                    self._scroll = max(0, min(self._scroll - event.y,
                                              self._max_scroll()))
            elif event.type == pygame.KEYDOWN:
                if self._name_focused:
                    if event.key == pygame.K_RETURN:
                        self._name_focused = False
                    elif event.key == pygame.K_BACKSPACE:
                        self.player_name = self.player_name[:-1]
                    elif event.unicode and event.unicode.isprintable():
                        if len(self.player_name) < 24:
                            self.player_name += event.unicode
                else:
                    if self._map_list_open:
                        if event.key == pygame.K_ESCAPE:
                            self._map_list_open = False
                            continue
                        if event.key in (pygame.K_UP, pygame.K_DOWN):
                            step = 1 if event.key == pygame.K_DOWN else -1
                            self._map_scroll = max(0, min(
                                self._map_scroll + step, self._max_map_scroll()))
                            continue
                        if event.key == pygame.K_c:
                            # Reset stays available with the overlay open: a
                            # player who opened the wrong list need not close
                            # it first, and C never scrolls it.
                            self._map_list_open = False
                            result = "clear_map"
                            continue
                    if event.key == pygame.K_p:
                        result = "create"
                    elif event.key == pygame.K_m:
                        self.create_mode = 'pvp' if self.create_mode == 'pve' else 'pve'
                    elif event.key == pygame.K_e:
                        result = "editor"
                    elif event.key == pygame.K_s:
                        result = "settings"
                    elif event.key == pygame.K_c:
                        result = "clear_map"
                    elif event.key == pygame.K_ESCAPE:
                        result = "quit"
                    elif event.key == pygame.K_UP:
                        self._selected = max(0, self._selected - 1)
                    elif event.key == pygame.K_DOWN:
                        self._selected = min(len(self.party_list) - 1, self._selected + 1)
                    elif event.key == pygame.K_RETURN:
                        if self.party_list:
                            result = f"join:{self.party_list[self._selected]['id']}"
        return result

    def _hit_party(self, pos) -> int | None:
        if not self.list_area.collidepoint(pos):
            return None
        index = (pos[1] - self.list_area.top) // ROW_H
        visible = self.party_list[self._scroll:self._scroll + self._visible_rows()]
        if 0 <= index < len(visible):
            self._selected = self._scroll + index
            return visible[index]["id"]
        return None

    def _visible_rows(self) -> int:
        return self.list_area.height // ROW_H

    def _max_scroll(self) -> int:
        return max(0, len(self.party_list) - self._visible_rows())

    def draw(self, surface) -> None:
        surface.fill(BG)

        title = self.title_font.render("ROUT III SHOOTER", True, ACCENT)
        surface.blit(title, ((self.width - title.get_width()) // 2, 18))

        server_txt = self.small_font.render(f"Сервер: {self.server_label}", True, MUTED)
        surface.blit(server_txt, ((self.width - server_txt.get_width()) // 2, 82))

        name_label = self.font.render("Имя:", True, TEXT)
        surface.blit(name_label, (265, 124))
        pygame.draw.rect(surface, PANEL, self.name_box, border_radius=5)
        border = SELECT if self._name_focused else MUTED
        pygame.draw.rect(surface, border, self.name_box, 2, border_radius=5)
        name_text = self.font.render(
            self.player_name + ("|" if self._name_focused else ""), True, TEXT
        )
        surface.blit(name_text, (self.name_box.x + 8, self.name_box.y + 7))

        header = self.header_font.render("Партии:", True, TEXT)
        surface.blit(header, (self.list_area.x, self.list_area.y - 28))

        pygame.draw.rect(surface, (22, 24, 34), self.list_area, border_radius=6)
        pygame.draw.rect(surface, PANEL, self.list_area, 1, border_radius=6)

        visible = self.party_list[self._scroll:self._scroll + self._visible_rows()]
        for i, party in enumerate(visible):
            row = pygame.Rect(
                self.list_area.x + 4,
                self.list_area.top + i * ROW_H + 4,
                self.list_area.width - 8,
                ROW_H - 4,
            )
            if self._scroll + i == self._selected:
                pygame.draw.rect(surface, SELECT, row, border_radius=4)

            bands = self.party_row_bands(row, party)
            name_band = bands["name"]
            name_txt = self.font.render(
                ellipsize(party["name"], self.font, name_band.width), True, TEXT)
            surface.blit(name_txt, name_band)

            mode_txt = self.small_font.render(
                'PvP' if party.get('mode') == 'pvp' else 'PvE', True,
                (240, 160, 60) if party.get('mode') == 'pvp' else GREEN,
            )
            surface.blit(mode_txt, bands["mode"])

            count_txt = self.small_font.render(
                f"{len(party['players'])} игрок(а)", True, MUTED
            )
            surface.blit(count_txt, bands["count"])

            if party["players"]:
                names = ", ".join(p["name"] for p in party["players"][:3])
                players_band = bands["players"]
                players_txt = self.small_font.render(
                    ellipsize(names, self.small_font, players_band.width), True, GREEN)
                surface.blit(players_txt, players_band)

        for key, accent in (
            ("create", True),
            ("editor", False),
            ("settings", False),
            ("quit", False),
        ):
            rect = self.buttons[key]
            label = self._button_labels[key]
            pygame.draw.rect(surface, PANEL, rect, border_radius=6)
            pygame.draw.rect(surface, ACCENT if accent else MUTED, rect, 2, border_radius=6)
            txt = self.font.render(
                ellipsize(label, self.font, rect.width - 2 * BTN_PAD), True, TEXT)
            surface.blit(
                txt,
                (rect.x + (rect.width - txt.get_width()) // 2,
                 rect.y + (rect.height - txt.get_height()) // 2),
            )

        bands = self.bottom_bands()
        status_band = bands["status"]
        status_txt = self.small_font.render(
            ellipsize(self.status, self.small_font, status_band.width), True,
            ACCENT if self.status else GREEN,
        )
        surface.blit(status_txt, status_band)

        mode_hint = self.small_font.render(
            f"[M] режим создания: {'PvP' if self.create_mode == 'pvp' else 'PvE'}", True,
            (240, 160, 60) if self.create_mode == 'pvp' else GREEN,
        )
        surface.blit(mode_hint, (self.list_area.right - mode_hint.get_width(),
                                 self.list_area.y - 26))

        map_band = bands["map"]
        hint_band = bands["hints"]
        hint_txt = self.small_font.render(
            ellipsize(self.control_hints, self.small_font, hint_band.width),
            True, MUTED)
        surface.blit(hint_txt, (hint_band.right - hint_txt.get_width(),
                                hint_band.y))

        if self.custom_map_error:
            map_text = f"Карта: {self.custom_map_error}"
            color = ACCENT
        elif self.custom_map_label:
            map_text = (f"Карта: {self.custom_map_label} "
                        "(при создании партии) [C — сброс]")
            color = GREEN
        else:
            map_text = f"Карта: {NO_MAP} (сервер по умолчанию)"
            color = MUTED
        # Its own band keeps a long name away from status and hints; the picker
        # button takes the right end, so text is cut before it.
        text_w = max(0, map_band.width - self.map_btn.width - BTN_GAP)
        map_txt = self.small_font.render(
            ellipsize(map_text, self.small_font, text_w), True, color)
        surface.blit(map_txt, map_band)
        pygame.draw.rect(surface, PANEL, self.map_btn, border_radius=4)
        pygame.draw.rect(surface, SELECT if self._map_list_open else MUTED,
                         self.map_btn, 1, border_radius=4)
        btn_txt = self.small_font.render("Карты", True, TEXT)
        surface.blit(btn_txt,
                     (self.map_btn.centerx - btn_txt.get_width() // 2,
                      self.map_btn.centery - btn_txt.get_height() // 2))
        self._draw_map_dropdown(surface)

    def _draw_map_dropdown(self, surface) -> None:
        """The open list, drawn last: its rows are the clickable ones."""
        if not self._map_list_open:
            return
        box, rows, more, hidden = self._map_dropdown_geometry()
        pygame.draw.rect(surface, (22, 24, 34), box, border_radius=5)
        pygame.draw.rect(surface, MUTED, box, 1, border_radius=5)
        for name, rect in rows:
            selected = (name == self.custom_map_label or
                        (name == NO_MAP and self.custom_map_label is None))
            pygame.draw.rect(surface, SELECT if selected else (40, 44, 62),
                             rect, border_radius=3)
            txt = self.small_font.render(
                ellipsize(name, self.small_font, rect.width - 12), True, TEXT)
            surface.blit(txt, (rect.x + 6, rect.y + 3))
        if more is not None:
            txt = self.small_font.render(f"…ещё {hidden}", True, MUTED)
            surface.blit(txt, (more.x + 6, more.y + 3))