"""In-game / main-menu settings screen (lobby visual style)."""
from __future__ import annotations

import pygame

from client.settings import SPEC, format_value, validate_size
from client.ui_layout import clip_tail, ellipsize

BG = (18, 18, 26)
PANEL = (30, 34, 48)
TEXT = (235, 235, 235)
MUTED = (150, 150, 165)
ACCENT = (210, 70, 70)
GREEN = (90, 210, 120)
SELECT = (70, 95, 150)
WARN = (240, 170, 60)
ROW_H = 46
HINTS = "↑/↓ — выбор | ←/→ — изменить | Enter — переключить | Esc — назад"
# Row that owns a free-text field (the window size): the field is the only way
# to a resolution that is not in the preset list, e.g. 4K on an odd monitor.
CUSTOM_ROW_KEY = "window_size"

# Band geometry below the row viewport, top to bottom. Each element owns a
# strip of its own so a long label or error cannot land on the next element.
ROWS_Y = 128
GAP = 6
FIELD_LABEL_BAND = 20
FIELD_W = 420
ERROR_BAND = 18
BACK_H = 42
HINT_BAND = 34
BTN_PAD = 12
VALUE_GAP = 12
FIELD_LABEL = "Размер окна (Ш×В, Enter — применить):"


class SettingsScreen:
    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self._selected = 0
        self._scroll = 0
        # Free-text editing of the current row's value (see CUSTOM_ROW_KEY).
        self._editing = False
        self._text = ""
        self.title_font = pygame.font.SysFont("monospace", 42, bold=True)
        self.font = pygame.font.SysFont("monospace", 20)
        self.small_font = pygame.font.SysFont("monospace", 14)

        self.field_h = 34
        self.back_btn = pygame.Rect(0, 0, 200, 42)
        self._layout()

    def _layout(self) -> None:
        """Vertical geometry, built top-down so every band is derived from the
        one above it instead of hardcoded y values.

        The row list is clipped to a viewport, because the option list outgrew
        the fixed 1000x700 menu canvas. Below the viewport each band owns its
        own strip, so a label, the field, an error and the back button cannot
        land on top of each other: rows -> field label -> field -> error ->
        back button -> key hints.
        """
        # What the bands below the viewport need, counted from its bottom edge.
        below = (FIELD_LABEL_BAND + GAP + self.field_h + GAP + ERROR_BAND
                 + GAP + BACK_H + HINT_BAND)
        self._rows_rect = pygame.Rect(
            140, ROWS_Y, 720, max(ROW_H, self.height - ROWS_Y - below)
        )

        bottom = self._rows_rect.bottom
        self._field_label_rect = pygame.Rect(
            self._field_x(), bottom + GAP, FIELD_W, FIELD_LABEL_BAND
        )
        self._field_rect = pygame.Rect(
            self._field_x(), self._field_label_rect.bottom + GAP, FIELD_W, self.field_h
        )
        self._error_rect = pygame.Rect(
            self._field_x(), self._field_rect.bottom + GAP, FIELD_W, ERROR_BAND
        )
        self.back_btn = pygame.Rect(
            (self.width - 200) // 2, self._error_rect.bottom + GAP, 200, BACK_H
        )

    def _field_x(self) -> int:
        return (self.width - FIELD_W) // 2

    @property
    def _visible_rows(self) -> int:
        return max(1, self._rows_rect.height // ROW_H)

    @property
    def _max_scroll(self) -> int:
        return max(0, len(SPEC) - self._visible_rows)

    def _row_rect(self, index: int) -> pygame.Rect:
        return pygame.Rect(
            self._rows_rect.x,
            self._rows_rect.y + (index - self._scroll) * ROW_H,
            self._rows_rect.width,
            ROW_H - 4,
        )

    def _left_arrow(self, rect: pygame.Rect) -> pygame.Rect:
        return pygame.Rect(rect.right - 150, rect.y + 8, 34, rect.height - 16)

    def _right_arrow(self, rect: pygame.Rect) -> pygame.Rect:
        return pygame.Rect(rect.right - 44, rect.y + 8, 34, rect.height - 16)

    def _ensure_visible(self) -> None:
        self._scroll = max(0, min(self._max_scroll, self._scroll))
        if self._selected < self._scroll:
            self._scroll = self._selected
        elif self._selected >= self._scroll + self._visible_rows:
            self._scroll = self._selected - self._visible_rows + 1

    @property
    def _editing_row(self) -> bool:
        return self._editing and SPEC[self._selected]["key"] == CUSTOM_ROW_KEY

    def _value_rect(self, rect: pygame.Rect) -> pygame.Rect:
        """The strip a row's value is drawn in: between the label on the left
        and the arrow zone on the right, with a gutter on each side.

        Drawing used to place the value by its own width from ``rect.right-54``,
        so a long value (a 4K size, a long texture path) ran under the arrows
        and over the next row's label. The value is now cut to this band.
        """
        left = self._left_arrow(rect).x - VALUE_GAP
        return pygame.Rect(rect.x + 16, rect.y, max(0, left - rect.x - 16), rect.height)

    def _label_rect(self, rect: pygame.Rect) -> pygame.Rect:
        """The strip a row's label is drawn in, ending where the value begins."""
        return pygame.Rect(rect.x + 16, rect.y, max(0, self._value_rect(rect).x - rect.x - 16),
                           rect.height)

    def _select(self, index: int) -> None:
        if index == self._selected:
            return
        self._selected = max(0, min(len(SPEC) - 1, index))
        self._editing = False

    def _open_editor(self, settings) -> None:
        spec = SPEC[self._selected]
        self._editing = True
        self._text = str(settings.get(spec["key"]))

    def _apply_text(self, settings) -> None:
        """Commit the free-text field. Bad input is reported, not applied."""
        spec = SPEC[self._selected]
        size, error = validate_size(self._text)
        if size is None:
            settings.resolution_error = error
            return
        settings.resolution_error = ""
        settings.set(spec["key"], f"{size[0]}\u00d7{size[1]}")

    def handle(self, events, settings) -> str | None:
        for event in events:
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if self.back_btn.collidepoint(event.pos):
                    settings.save()
                    return "back"
                for i, spec in enumerate(SPEC):
                    rect = self._row_rect(i)
                    if not self._rows_rect.colliderect(rect):
                        continue  # scrolled out of the viewport
                    if self._left_arrow(rect).collidepoint(event.pos):
                        self._select(i)
                        settings.adjust(spec["key"], -1)
                        self._editing = False
                    elif self._right_arrow(rect).collidepoint(event.pos):
                        self._select(i)
                        settings.adjust(spec["key"], +1)
                        self._editing = False
                    elif rect.collidepoint(event.pos):
                        self._select(i)
                        if spec["kind"] == "bool":
                            settings.adjust(spec["key"], +1)
                        elif spec.get("custom") and not self._editing:
                            self._open_editor(settings)
            elif event.type == pygame.MOUSEWHEEL:
                self._select(self._selected - event.y)
            elif event.type == pygame.KEYDOWN:
                if self._editing:
                    if event.key == pygame.K_RETURN:
                        self._apply_text(settings)
                        self._editing = False
                    elif event.key == pygame.K_ESCAPE:
                        self._editing = False
                    elif event.key == pygame.K_BACKSPACE:
                        self._text = self._text[:-1]
                    elif event.unicode and event.unicode.isprintable():
                        if len(self._text) < 16:
                            self._text += event.unicode
                    continue
                if event.key == pygame.K_ESCAPE:
                    settings.save()
                    return "back"
                elif event.key == pygame.K_UP:
                    self._select(self._selected - 1)
                elif event.key == pygame.K_DOWN:
                    self._select(self._selected + 1)
                elif event.key in (pygame.K_LEFT, pygame.K_MINUS):
                    settings.adjust(SPEC[self._selected]["key"], -1)
                    self._editing = False
                elif event.key in (pygame.K_RIGHT, pygame.K_PLUS, pygame.K_EQUALS):
                    settings.adjust(SPEC[self._selected]["key"], +1)
                    self._editing = False
                elif event.key == pygame.K_RETURN:
                    spec = SPEC[self._selected]
                    if spec["kind"] == "bool":
                        settings.adjust(spec["key"], +1)
                    elif spec.get("custom"):
                        self._open_editor(settings)
        return None

    def draw(self, surface, settings) -> None:
        surface.fill(BG)

        title = self.title_font.render("НАСТРОЙКИ", True, ACCENT)
        surface.blit(title, ((self.width - title.get_width()) // 2, 30))

        self._ensure_visible()
        top = self._rows_rect.y
        for i, spec in enumerate(SPEC):
            rect = self._row_rect(i)
            if rect.bottom < top or rect.top > self._rows_rect.bottom:
                continue
            selected = i == self._selected
            pygame.draw.rect(surface, SELECT if selected else PANEL, rect,
                             border_radius=6)

            label_rect = self._label_rect(rect)
            label = self.font.render(
                ellipsize(spec["label"], self.font, label_rect.width), True, TEXT)
            surface.blit(label, (label_rect.x,
                                 rect.y + (rect.height - label.get_height()) // 2))

            # Right-aligned inside its own band: the arrows keep their zone and a
            # long value is cut instead of running under them.
            value_rect = self._value_rect(rect)
            value = settings.get(spec["key"])
            value_txt = self.font.render(
                ellipsize(format_value(spec["key"], value), self.font, value_rect.width),
                True, GREEN if selected else MUTED,
            )
            surface.blit(
                value_txt,
                (value_rect.right - value_txt.get_width(),
                 rect.y + (rect.height - value_txt.get_height()) // 2),
            )

            if spec["kind"] != "bool":
                for arrow_rect, char in (
                    (self._left_arrow(rect), "<"),
                    (self._right_arrow(rect), ">"),
                ):
                    pygame.draw.rect(surface, (22, 24, 34), arrow_rect, border_radius=4)
                    pygame.draw.rect(surface, MUTED, arrow_rect, 1, border_radius=4)
                    a = self.font.render(char, True, TEXT)
                    surface.blit(
                        a,
                        (arrow_rect.centerx - a.get_width() // 2,
                         arrow_rect.centery - a.get_height() // 2),
                    )

        if self._scroll > 0:
            up = self.small_font.render("\u25b2", True, MUTED)
            surface.blit(up, (self._rows_rect.right + 6, top))
        if self._scroll < self._max_scroll:
            down = self.small_font.render("\u25bc", True, MUTED)
            surface.blit(down, (self._rows_rect.right + 6,
                                self._rows_rect.bottom - 14))

        self._draw_field(surface, settings)

        pygame.draw.rect(surface, PANEL, self.back_btn, border_radius=6)
        pygame.draw.rect(surface, ACCENT, self.back_btn, 2, border_radius=6)
        back = self.font.render(
            ellipsize("Назад [Esc]", self.font, self.back_btn.width - 2 * BTN_PAD),
            True, TEXT)
        surface.blit(
            back,
            (self.back_btn.centerx - back.get_width() // 2,
             self.back_btn.centery - back.get_height() // 2),
        )

        hint = self.small_font.render(HINTS, True, MUTED)
        surface.blit(hint, ((self.width - hint.get_width()) // 2, self.height - 28))

    def _draw_field(self, surface, settings) -> None:
        """The custom-size field: visible only while the size row is selected.

        The label and the error each live in their own band above and below the
        field (see ``_layout``). The label used to be drawn at ``field.y - 16``,
        which put it inside the row viewport, and the error at ``field.bottom+2``,
        which overlapped the back button.
        """
        if SPEC[self._selected]["key"] != CUSTOM_ROW_KEY:
            return

        label_rect = self._field_label_rect
        label_txt = self.small_font.render(
            ellipsize(FIELD_LABEL, self.small_font, label_rect.width), True, MUTED)
        surface.blit(label_txt, (label_rect.x,
                                 label_rect.centery - label_txt.get_height() // 2))

        rect = self._field_rect
        pygame.draw.rect(surface, PANEL, rect, border_radius=4)
        border = SELECT if self._editing_row else MUTED
        pygame.draw.rect(surface, border, rect, 2, border_radius=4)
        text = self._text if self._editing else str(settings.get(CUSTOM_ROW_KEY))
        shown = text + ("|" if self._editing_row else "")
        # Tail-clipped: the value is typed here, so the tail being edited has to
        # stay visible even when it outgrows the field.
        txt = self.small_font.render(
            clip_tail(shown, self.small_font, rect.width - 16), True, TEXT)
        surface.blit(txt, (rect.x + 8, rect.centery - txt.get_height() // 2))

        error = settings.resolution_error
        if error:
            err_rect = self._error_rect
            err = self.small_font.render(
                ellipsize(error, self.small_font, err_rect.width), True, WARN)
            surface.blit(err, (err_rect.x, err_rect.centery - err.get_height() // 2))
