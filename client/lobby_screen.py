from __future__ import annotations

import pygame

from client.ui_layout import ellipsize, fit_width, layout_row

BG = (18, 18, 26)
PANEL = (30, 34, 48)
TEXT = (235, 235, 235)
MUTED = (150, 150, 165)
ACCENT = (210, 70, 70)
GREEN = (90, 210, 120)
SELECT = (70, 95, 150)
ROW_H = 44

UI_HINTS = "WASD — движение | Мышь — обзор | ЛКМ — огонь | Esc — пауза"
TRACTOR_HINTS = "WASD — движение | ←/→ — обзор | Пробел — огонь | Левый Shift — дэш"

# lobby buttons: height, gap between them, margin off the canvas edge, padding
# inside a button around its label, and the narrowest a button may get
BTN_H, BTN_GAP, BTN_MARGIN, BTN_PAD, BTN_MIN_W = 40, 24, 60, 10, 120


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
            "create": "Создать [P]", "refresh": "Обновить [R]",
            "editor": "Редактор [E]", "settings": "Настройки [S]",
            "quit": "Выход [Esc]",
        }
        top_row = ["create", "refresh", "editor"]
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

        # The status line, the custom-map line and the control hints all sit on
        # the bottom row of the screen, so each gets a width budget that keeps
        # it from running into the others.
        self._status_w = max(120, self.width // 3)
        self._map_w = max(120, self.width // 3)

        # Kept as attributes for callers that reach for a single button.
        self.create_btn = self.buttons["create"]
        self.refresh_btn = self.buttons["refresh"]
        self.editor_btn = self.buttons["editor"]
        self.settings_btn = self.buttons["settings"]
        self.quit_btn = self.buttons["quit"]

    def set_custom_map(self, label: str | None) -> None:
        self.custom_map_label = label

    def set_state(self, party_list: list[dict], status: str) -> None:
        self.party_list = party_list
        self.status = status
        if self.party_list:
            self._selected = min(self._selected, len(self.party_list) - 1)
        else:
            self._selected = 0

    def handle(self, events) -> str | None:
        result = None
        for event in events:
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                pos = event.pos
                if self.name_box.collidepoint(pos):
                    self._name_focused = True
                else:
                    self._name_focused = False
                if self.create_btn.collidepoint(pos):
                    result = "create"
                elif self.refresh_btn.collidepoint(pos):
                    result = "refresh"
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
                self._scroll = max(0, min(self._scroll - event.y, self._max_scroll()))
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
                    if event.key == pygame.K_p:
                        result = "create"
                    elif event.key == pygame.K_m:
                        self.create_mode = 'pvp' if self.create_mode == 'pve' else 'pve'
                    elif event.key == pygame.K_r:
                        result = "refresh"
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

            name_txt = self.font.render(party["name"], True, TEXT)
            surface.blit(name_txt, (row.x + 10, row.y + 10))

            mode_txt = self.small_font.render(
                'PvP' if party.get('mode') == 'pvp' else 'PvE', True,
                (240, 160, 60) if party.get('mode') == 'pvp' else GREEN,
            )
            surface.blit(mode_txt, (row.x + 10, row.y + ROW_H - 14))

            count_txt = self.small_font.render(
                f"{len(party['players'])} игрок(а)", True, MUTED
            )
            surface.blit(count_txt, (row.right - count_txt.get_width() - 10, row.y + 12))

            if party["players"]:
                names = ", ".join(p["name"] for p in party["players"][:3])
                players_txt = self.small_font.render(names, True, GREEN)
                surface.blit(players_txt, (row.x + 10, row.y + ROW_H - 16))

        for key, label, accent in (
            ("create", "Создать [P]", True),
            ("refresh", "Обновить [R]", False),
            ("editor", "Редактор [E]", False),
            ("settings", "Настройки [S]", False),
            ("quit", "Выход [Esc]", False),
        ):
            rect = self.buttons[key]
            pygame.draw.rect(surface, PANEL, rect, border_radius=6)
            pygame.draw.rect(surface, ACCENT if accent else MUTED, rect, 2, border_radius=6)
            txt = self.font.render(
                ellipsize(label, self.font, rect.width - 2 * BTN_PAD), True, TEXT)
            surface.blit(
                txt,
                (rect.x + (rect.width - txt.get_width()) // 2,
                 rect.y + (rect.height - txt.get_height()) // 2),
            )

        status_txt = self.small_font.render(
            ellipsize(self.status, self.small_font, self._status_w), True,
            ACCENT if self.status else GREEN,
        )
        surface.blit(status_txt, (20, self.height - 26))

        mode_hint = self.small_font.render(
            f"[M] режим создания: {'PvP' if self.create_mode == 'pvp' else 'PvE'}", True,
            (240, 160, 60) if self.create_mode == 'pvp' else GREEN,
        )
        surface.blit(mode_hint, (self.list_area.right - mode_hint.get_width(),
                                 self.list_area.y - 26))

        hint_txt = self.small_font.render(self.control_hints, True, MUTED)
        surface.blit(hint_txt, (self.width - hint_txt.get_width() - 20, self.height - 26))

        if self.custom_map_label:
            # Sits on the same line as the status, so it gets that line's width
            # budget instead of overrunning the status and the hints.
            map_txt = self.small_font.render(
                ellipsize(
                    f"Карта: {self.custom_map_label} (при создании партии) [C — сброс]",
                    self.small_font, self._map_w,
                ), True, GREEN,
            )
            surface.blit(map_txt, ((self.width - map_txt.get_width()) // 2, self.height - 26))