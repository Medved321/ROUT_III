"""Connect screen: host/port/name fields + connect button shown before lobby."""
from __future__ import annotations

import pygame

from client.lobby_screen import BG, PANEL, TEXT, MUTED, ACCENT, GREEN, SELECT
from client.ui_layout import ellipsize

FIELD_W = 400
FIELD_H = 32
FIELD_X = 430
FIELD_Y0 = 150
FIELD_DY = 54
FIELD_PAD = 8
BTN_PAD = 10

CONNECT_LABEL = "Подключиться [Enter]"

HINTS = "Tab — поле | Enter — подключиться | Esc — выход"


class ConnectScreen:
    def __init__(self, width: int, height: int, host: str = "localhost",
                 port: int = 7777, name: str = "Player"):
        self.width = width
        self.height = height
        self.host = host
        self.port = str(port)
        self.name = name
        self.status: str = ""
        self._focus = 0  # 0=host, 1=port, 2=name

        self.title_font = pygame.font.SysFont("monospace", 46, bold=True)
        self.subtitle_font = pygame.font.SysFont("monospace", 24, bold=True)
        self.font = pygame.font.SysFont("monospace", 18)
        self.small_font = pygame.font.SysFont("monospace", 14)

        self.host_box = pygame.Rect(FIELD_X, FIELD_Y0, FIELD_W, FIELD_H)
        self.port_box = pygame.Rect(FIELD_X, FIELD_Y0 + FIELD_DY, 140, FIELD_H)
        self.name_box = pygame.Rect(FIELD_X, FIELD_Y0 + 2 * FIELD_DY, FIELD_W, FIELD_H)
        self.connect_btn = pygame.Rect(
            (width - 220) // 2, FIELD_Y0 + 3 * FIELD_DY + 14, 220, 46
        )

    def _boxes(self):
        return [(self.host_box, "host"), (self.port_box, "port"), (self.name_box, "name")]

    def handle(self, events) -> str | None:
        result = None
        for event in events:
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                pos = event.pos
                for box, key in self._boxes():
                    if box.collidepoint(pos):
                        self._focus = {"host": 0, "port": 1, "name": 2}[key]
                        break
                if self.connect_btn.collidepoint(pos):
                    result = "connect"
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_TAB:
                    self._focus = (self._focus + 1) % 3
                elif event.key == pygame.K_RETURN:
                    result = "connect"
                elif event.key == pygame.K_ESCAPE:
                    result = "quit"
                elif event.key == pygame.K_BACKSPACE:
                    self._delete_char()
                elif event.unicode and event.unicode.isprintable():
                    self._insert_char(event.unicode)
        return result

    def _field(self) -> str:
        return ("host", "port", "name")[self._focus]

    def _insert_char(self, char: str) -> None:
        field = self._field()
        value = getattr(self, field)
        if len(value) < 48:
            setattr(self, field, value + char)

    def _delete_char(self) -> None:
        field = self._field()
        setattr(self, field, getattr(self, field)[:-1])

    def draw(self, surface) -> None:
        surface.fill(BG)

        title = self.title_font.render("ROUT III SHOOTER", True, ACCENT)
        surface.blit(title, ((self.width - title.get_width()) // 2, 30))

        subtitle = self.subtitle_font.render("ПОДКЛЮЧЕНИЕ К СЕРВЕРУ", True, TEXT)
        surface.blit(subtitle, ((self.width - subtitle.get_width()) // 2, 92))

        labels = (("Хост:", self.host_box, 0), ("Порт:", self.port_box, 1),
                  ("Имя:", self.name_box, 2))
        for label, box, focus in labels:
            txt = self.font.render(label, True, TEXT)
            surface.blit(txt, (box.x - txt.get_width() - 14, box.y + 7))
            pygame.draw.rect(surface, PANEL, box, border_radius=5)
            border = SELECT if self._focus == focus else MUTED
            pygame.draw.rect(surface, border, box, 2, border_radius=5)
            value = (self.host, self.port, self.name)[focus]
            caret = "|" if self._focus == focus else ""
            # A 48-char value is wider than the 400px box, so the text is cut to
            # the field instead of bleeding over the label and the next field.
            field = self.font.render(
                ellipsize(value + caret, self.font, box.width - FIELD_PAD * 2), True, TEXT
            )
            surface.blit(field, (box.x + FIELD_PAD, box.y + 7))

        pygame.draw.rect(surface, PANEL, self.connect_btn, border_radius=6)
        pygame.draw.rect(surface, ACCENT, self.connect_btn, 2, border_radius=6)
        btn = self.font.render(
            ellipsize(CONNECT_LABEL, self.font, self.connect_btn.width - 2 * BTN_PAD),
            True, TEXT,
        )
        surface.blit(
            btn,
            (self.connect_btn.centerx - btn.get_width() // 2,
             self.connect_btn.centery - btn.get_height() // 2),
        )

        if self.status:
            is_err = self.status != "Подключение…"
            status = self.small_font.render(
                self.status, True, ACCENT if is_err else GREEN
            )
            surface.blit(status, (20, self.height - 26))

        hint = self.small_font.render(HINTS, True, MUTED)
        surface.blit(hint, (self.width - hint.get_width() - 20, self.height - 26))