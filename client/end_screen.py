"""Death / victory overlay drawn on top of the frozen game frame.

Two screens share one layout because they are the same kind of thing: a big
title, a block of results for the local player and a single action.  Death
offers "Возродиться"; victory offers "В меню".  Everything is laid out on the
fixed 1000x700 menu canvas -- the same surface ``client/pause_screen.py`` uses --
so the boxes drawn here are exactly the boxes hit-tested by ``handle``, and the
caller's ``Renderer.to_canvas_pos`` mapping keeps a click at any window size
landing where the player pointed.
"""
from __future__ import annotations

import pygame

from client.ui_layout import ellipsize, fit_width

TEXT = (235, 235, 235)
MUTED = (160, 160, 175)
ACCENT = (210, 70, 70)
GOOD = (120, 200, 120)
PANEL = (26, 28, 40)
SELECT = (70, 95, 150)

BTN_H, BTN_GAP, BTN_PAD = 52, 14, 18
BTN_MIN_W = 260
ROW_H = 30


class EndScreen:
    """One overlay, two faces: pass a model to ``set_model`` each frame."""

    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self._selected = 0
        self.model: dict | None = None
        self.buttons: list[pygame.Rect] = []

        self.title_font = pygame.font.SysFont("monospace", 56, bold=True)
        self.font = pygame.font.SysFont("monospace", 22, bold=True)
        self.small_font = pygame.font.SysFont("monospace", 16)

        self._overlay = pygame.Surface((width, height), pygame.SRCALPHA)
        self._overlay.fill((6, 6, 12, 200))

    # ── model / geometry ─────────────────────────────────────────────
    def set_model(self, model: dict) -> None:
        self.model = model
        self.buttons = self._build_buttons(model.get("buttons", []))

    def _build_buttons(self, buttons: list[tuple[str, str]]) -> list[pygame.Rect]:
        if not buttons:
            return []
        widths = [max(BTN_MIN_W, fit_width(label, self.font, BTN_PAD))
                  for _, label in buttons]
        total = sum(widths) + BTN_GAP * (len(widths) - 1)
        x = (self.width - total) // 2
        y = self.height - BTN_H - 44
        rects = []
        for w in widths:
            rects.append(pygame.Rect(x, y, w, BTN_H))
            x += w + BTN_GAP
        return rects

    # ── input ────────────────────────────────────────────────────────
    def handle(self, events) -> str | None:
        buttons = self.model.get("buttons", []) if self.model else []
        primary = self.model.get("primary") if self.model else None
        for event in events:
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                for i, rect in enumerate(self.buttons):
                    if rect.collidepoint(event.pos):
                        self._selected = i
                        return buttons[i][0]
            elif event.type == pygame.MOUSEMOTION:
                for i, rect in enumerate(self.buttons):
                    if rect.collidepoint(event.pos):
                        self._selected = i
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_RETURN and primary:
                    return primary
                if event.key == pygame.K_ESCAPE and self.model.get("escapable"):
                    return "leave"
                if event.key == pygame.K_UP:
                    self._selected = (self._selected - 1) % max(1, len(buttons))
                elif event.key == pygame.K_DOWN:
                    self._selected = (self._selected + 1) % max(1, len(buttons))
                elif event.key == pygame.K_TAB and buttons:
                    self._selected = (self._selected + 1) % len(buttons)
        return None

    # ── drawing ──────────────────────────────────────────────────────
    def draw(self, surface) -> None:
        if self.model is None:
            return
        surface.blit(self._overlay, (0, 0))

        accent = GOOD if self.model.get("tone") == "win" else ACCENT
        title = self.title_font.render(self.model.get("title", ""), True, accent)
        surface.blit(title, ((self.width - title.get_width()) // 2, 84))

        y = 168
        reason = self.model.get("reason", "")
        if reason:
            sub = self.font.render(
                ellipsize(reason, self.font, self.width - 80), True, TEXT)
            surface.blit(sub, ((self.width - sub.get_width()) // 2, y))
            y += ROW_H + 16

        y = self._draw_stats(surface, y)
        y = self._draw_table(surface, y)

        for i, ((_, label), rect) in enumerate(
                zip(self.model.get("buttons", []), self.buttons)):
            selected = i == self._selected
            pygame.draw.rect(surface, SELECT if selected else PANEL, rect,
                             border_radius=8)
            pygame.draw.rect(surface, accent if selected else MUTED, rect, 2,
                             border_radius=8)
            txt = self.font.render(
                ellipsize(label, self.font, rect.width - 2 * BTN_PAD), True, TEXT)
            surface.blit(txt, (rect.centerx - txt.get_width() // 2,
                               rect.centery - txt.get_height() // 2))

        hint = self.model.get("hint", "Enter — выбрать")
        hint_s = self.small_font.render(hint, True, MUTED)
        surface.blit(hint_s, ((self.width - hint_s.get_width()) // 2,
                              self.height - 28))

    def _draw_stats(self, surface, y: int) -> int:
        stats = self.model.get("stats", [])
        if not stats:
            return y
        label_x = self.width // 2 - 190
        value_x = self.width // 2 + 190
        for label, value in stats:
            lt = self.small_font.render(label, True, MUTED)
            vt = self.small_font.render(str(value), True, TEXT)
            surface.blit(lt, (label_x, y))
            surface.blit(vt, (value_x - vt.get_width(), y))
            y += ROW_H
        return y + 12

    def _draw_table(self, surface, y: int) -> int:
        table = self.model.get("table")
        if not table:
            return y
        headers = table.get("headers", [])
        rows = table.get("rows", [])
        highlight = table.get("highlight")
        col_x = self._table_columns(len(headers))
        head = self.small_font.render("   ".join(headers), True, MUTED)
        surface.blit(head, (col_x[0], y))
        y += ROW_H
        for i, row in enumerate(rows):
            for cx, cell in zip(col_x, row):
                col = GOOD if i == highlight else TEXT
                ct = self.small_font.render(str(cell), True, col)
                surface.blit(ct, (cx, y))
            y += ROW_H
        return y + 12

    def _table_columns(self, n: int) -> list[int]:
        if n <= 0:
            return [self.width // 2]
        total_w = 520
        left = (self.width - total_w) // 2
        step = total_w // n
        return [left + i * step for i in range(n)]
