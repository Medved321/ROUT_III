"""Pause menu overlay drawn on top of the frozen game frame."""
from __future__ import annotations

import pygame

from client.ui_layout import ellipsize, fit_width

TEXT = (235, 235, 235)
MUTED = (160, 160, 175)
ACCENT = (210, 70, 70)
PANEL = (26, 28, 40)
SELECT = (70, 95, 150)

OPTIONS = [
    ("resume", "ПРОДОЛЖИТЬ"),
    ("settings", "НАСТРОЙКИ"),
    ("leave", "ВЫЙТИ В МЕНЮ"),
    ("quit", "ВЫХОД ИЗ ИГРЫ"),
]
BTN_H, BTN_GAP, BTN_PAD = 52, 14, 18
BTN_MIN_W = 260


class PauseScreen:
    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self._selected = 0

        self.title_font = pygame.font.SysFont("monospace", 52, bold=True)
        self.font = pygame.font.SysFont("monospace", 22, bold=True)
        self.small_font = pygame.font.SysFont("monospace", 14)

        # Each button is as wide as its own label (never below BTN_MIN_W), so an
        # option with a longer caption cannot spill over its neighbours. The
        # same rects are drawn and hit-tested, so the clickable area is exactly
        # the painted box.
        self.buttons = self._build_buttons()
        self._hint_y = min(self._top + len(self.buttons)
                           * (BTN_H + BTN_GAP) - BTN_GAP + 24, height - 34)

        self._overlay = pygame.Surface((width, height), pygame.SRCALPHA)
        self._overlay.fill((6, 6, 12, 190))

    def _build_buttons(self) -> list[pygame.Rect]:
        rows = []
        for i, (_, label) in enumerate(OPTIONS):
            w = max(BTN_MIN_W, fit_width(label, self.font, BTN_PAD))
            rows.append(w)
        widest = max(rows)
        total = len(OPTIONS) * BTN_H + (len(OPTIONS) - 1) * BTN_GAP
        self._top = (self.height - total) // 2 + 30
        return [
            pygame.Rect((self.width - widest) // 2,
                        self._top + i * (BTN_H + BTN_GAP), rows[i], BTN_H)
            for i in range(len(OPTIONS))
        ]

    def handle(self, events) -> str | None:
        for event in events:
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                for i, rect in enumerate(self.buttons):
                    if rect.collidepoint(event.pos):
                        self._selected = i
                        return OPTIONS[i][0]
            elif event.type == pygame.MOUSEMOTION:
                for i, rect in enumerate(self.buttons):
                    if rect.collidepoint(event.pos):
                        self._selected = i
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_p):
                    return "resume"
                elif event.key == pygame.K_UP:
                    self._selected = (self._selected - 1) % len(OPTIONS)
                elif event.key == pygame.K_DOWN:
                    self._selected = (self._selected + 1) % len(OPTIONS)
                elif event.key == pygame.K_RETURN:
                    return OPTIONS[self._selected][0]
        return None

    def draw(self, surface) -> None:
        surface.blit(self._overlay, (0, 0))

        title = self.title_font.render("ПАУЗА", True, ACCENT)
        surface.blit(title, ((self.width - title.get_width()) // 2, self._top - 90))

        for i, ((_, label), rect) in enumerate(zip(OPTIONS, self.buttons)):
            selected = i == self._selected
            pygame.draw.rect(surface, SELECT if selected else PANEL, rect, border_radius=8)
            pygame.draw.rect(surface, ACCENT if selected else MUTED, rect, 2, border_radius=8)
            # Belt and braces: the box is sized from the label, so this only
            # bites if the font renders wider than font.size reported.
            txt = self.font.render(
                ellipsize(label, self.font, rect.width - 2 * BTN_PAD), True, TEXT)
            surface.blit(
                txt,
                (rect.centerx - txt.get_width() // 2,
                 rect.centery - txt.get_height() // 2),
            )

        hint = self.small_font.render(
            "↑/↓ — выбор | Enter — выбрать | Esc — продолжить", True, MUTED
        )
        surface.blit(hint, ((self.width - hint.get_width()) // 2, self._hint_y))
