"""Death / victory overlay drawn on top of the frozen game frame.

Two screens share one layout because they are the same kind of thing: a big
title, a block of results for the local player and a single action.  Death
offers "Возродиться"; victory offers "В меню".  A networked PvE victory adds a
roster on the left: arrow keys or a click pick a teammate, and the block on the
right shows that player's authoritative counters.  Everything is laid out on
the fixed 1000x700 menu canvas -- the same surface ``client/pause_screen.py``
uses -- so the boxes drawn here are exactly the boxes hit-tested by ``handle``,
and the caller's ``Renderer.to_canvas_pos`` mapping keeps a click at any window
size landing where the player pointed.
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

# Roster geometry lives next to the hitboxes it feeds: the rectangles built
# here are the ones ``handle`` tests, so a row cannot drift away from its paint.
ROSTER_X, ROSTER_W, ROSTER_TOP = 72, 350, 168
ROSTER_PAGE_ROWS = 11
STATS_X, STATS_VALUE_W = 470, 400


class EndScreen:
    """One overlay, two faces: pass a model to ``set_model`` each frame."""

    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self._selected = 0
        self.model: dict | None = None
        self.buttons: list[pygame.Rect] = []
        self._roster_selected = 0
        self._roster_key: tuple | None = None
        self._roster_rects: list[pygame.Rect] = []

        self.title_font = pygame.font.SysFont("monospace", 56, bold=True)
        self.font = pygame.font.SysFont("monospace", 22, bold=True)
        self.small_font = pygame.font.SysFont("monospace", 16)

        self._overlay = pygame.Surface((width, height), pygame.SRCALPHA)
        self._overlay.fill((6, 6, 12, 200))

    # ── model / geometry ─────────────────────────────────────────────
    def set_model(self, model: dict) -> None:
        self.model = model
        self.buttons = self._build_buttons(model.get("buttons", []))
        self._sync_roster(model.get("roster") or [])

    def reset_roster(self) -> None:
        """Forget the previous match's cursor before the next overlay opens."""
        self._roster_selected = 0
        self._roster_key = None
        self._roster_rects = []

    def _sync_roster(self, roster: list[dict]) -> None:
        if not roster:
            self.reset_roster()
            return
        key = tuple((e.get("id"), e.get("name")) for e in roster)
        if key != self._roster_key:
            self._roster_key = key
            chosen = next((i for i, e in enumerate(roster) if e.get("local")), 0)
            self._roster_selected = chosen
        self._roster_selected %= len(roster)
        page = self._roster_selected // ROSTER_PAGE_ROWS
        start = page * ROSTER_PAGE_ROWS
        self._roster_rects = [
            pygame.Rect(ROSTER_X, ROSTER_TOP + i * ROW_H, ROSTER_W, ROW_H - 2)
            for i in range(min(ROSTER_PAGE_ROWS, len(roster) - start))
        ]

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
    def _move_roster(self, step: int) -> None:
        roster = (self.model or {}).get("roster") or []
        if roster:
            self._roster_selected = (self._roster_selected + step) % len(roster)
            # The visible page is derived from the cursor, so moving across a
            # page boundary must rebuild the same rectangles that are painted.
            self._sync_roster(roster)

    def handle(self, events) -> str | None:
        buttons = self.model.get("buttons", []) if self.model else []
        primary = self.model.get("primary") if self.model else None
        roster = (self.model or {}).get("roster") or []
        for event in events:
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                for i, rect in enumerate(self._roster_rects):
                    if rect.collidepoint(event.pos):
                        page = self._roster_selected // ROSTER_PAGE_ROWS
                        self._roster_selected = page * ROSTER_PAGE_ROWS + i
                        return None
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
                if roster and event.key in (pygame.K_LEFT, pygame.K_RIGHT):
                    self._move_roster(1 if event.key == pygame.K_RIGHT else -1)
                    continue
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

        roster = self.model.get("roster") or []
        if roster:
            self._draw_roster(surface, roster, accent)
        else:
            y = self._draw_stats(surface, y)
            y = self._draw_table(surface, y)
        self._draw_buttons(surface, accent)

        hint = self.model.get("hint", "Enter — выбрать")
        hint_s = self.small_font.render(hint, True, MUTED)
        surface.blit(hint_s, ((self.width - hint_s.get_width()) // 2,
                              self.height - 28))

    def _draw_buttons(self, surface, accent) -> None:
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

    def _draw_roster(self, surface, roster: list[dict], accent) -> None:
        page = self._roster_selected // ROSTER_PAGE_ROWS
        start = page * ROSTER_PAGE_ROWS
        shown = roster[start:start + ROSTER_PAGE_ROWS]
        pygame.draw.rect(
            surface, PANEL,
            pygame.Rect(ROSTER_X - 6, ROSTER_TOP - 6,
                        ROSTER_W + 12, len(shown) * ROW_H + 12),
            border_radius=8)
        for i, (entry, rect) in enumerate(zip(shown, self._roster_rects)):
            index = start + i
            selected = index == self._roster_selected
            pygame.draw.rect(surface, SELECT if selected else PANEL, rect,
                             border_radius=4)
            label = ellipsize(entry.get("name", ""), self.small_font,
                              ROSTER_W - 2 * BTN_PAD)
            col = GOOD if selected else TEXT
            txt = self.small_font.render(label, True, col)
            surface.blit(txt, (rect.x + BTN_PAD,
                               rect.y + (rect.height - txt.get_height()) // 2))

        count = f"Игрок {self._roster_selected + 1} из {len(roster)}"
        txt = self.small_font.render(count, True, MUTED)
        surface.blit(txt, (ROSTER_X, ROSTER_TOP + len(shown) * ROW_H + 10))

        current = roster[self._roster_selected]
        head = self.font.render(
            ellipsize(current.get("name", ""), self.font, STATS_VALUE_W),
            True, accent)
        surface.blit(head, (STATS_X, ROSTER_TOP))
        self._draw_stats(surface, ROSTER_TOP + ROW_H + 10,
                         label_x=STATS_X, value_x=STATS_X + STATS_VALUE_W)

    def _draw_stats(self, surface, y: int,
                    label_x: int | None = None,
                    value_x: int | None = None) -> int:
        stats = self.model.get("stats", [])
        if label_x is None:
            label_x = self.width // 2 - 190
        if value_x is None:
            value_x = self.width // 2 + 190
        roster = self.model.get("roster") or []
        if roster:
            stats = roster[self._roster_selected].get("stats", [])
        if not stats:
            return y
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
