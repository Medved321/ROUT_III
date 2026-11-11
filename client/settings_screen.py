"""In-game / main-menu settings screen (lobby visual style)."""
from __future__ import annotations

import pygame

from client.settings import SPEC, format_value, validate_size
from client.ui_layout import clip_tail, ellipsize, text_width

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

# Rows that own a free-text field, with the caption above it. Two very different
# values are typed the same way (a ``Ш×В`` size and a server address), so the
# field, its band and its hit area are shared instead of copied.
TEXT_ROW_LABELS = {
    "window_size": "Размер окна (Ш×В, Enter — применить):",
    "server_host": "Адрес сервера (host или host:port, Enter — применить):",
}

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
# Left padding of a row, and the gutter between the caption and the value. The
# caption column is sized from the widest label that is actually in SPEC, so a
# name is measured, never guessed and never cut on the default canvas.
LABEL_PAD = 16
LABEL_GAP = 14
# Floor for the value column: a pathologically long caption must still leave the
# arrows their room, and the value degrades to an ellipsis before the arrows do.
VALUE_MIN_W = 120
# The two steppers sit together at the right of the row. The insets are named
# because their *gap* is what the caption and the value compete for: they used
# to be 150/44 with a 72px dead gutter, which is exactly the room a caption needs.
ARROW_W = 34
ARROW_INSET_L = 118
ARROW_INSET_R = 42


class SettingsScreen:
    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self._selected = 0
        self._scroll = 0
        # Free-text editing of the current row's value (see TEXT_ROW_LABELS).
        self._editing = False
        self._text = ""
        # Why the last committed text was refused (empty when it was applied).
        # Screen-local on purpose: it belongs to one field of one screen, unlike
        # ``Settings.resolution_error``, which the window sizing path reads.
        self._field_error = ""
        # Last typed character and which channel delivered it.  Desktop SDL
        # reports a printable key twice -- as ``KEYDOWN.unicode`` and as
        # ``TEXTINPUT`` -- while Android reports it only as ``TEXTINPUT``. Both
        # have to be accepted, so the pair is collapsed when it arrives in the
        # same batch instead of picking one channel and breaking the other.
        self._last_typed: tuple[str, str] | None = None
        self.title_font = pygame.font.SysFont("monospace", 42, bold=True)
        self.font = pygame.font.SysFont("monospace", 20)
        self.small_font = pygame.font.SysFont("monospace", 14)

        self.field_h = 34
        self.back_btn = pygame.Rect(0, 0, 200, 42)
        # Measured once from the row font (see ``_label_needed``).
        self._label_needed_px = None
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
        return pygame.Rect(rect.right - ARROW_INSET_L, rect.y + 8, ARROW_W,
                           rect.height - 16)

    def _right_arrow(self, rect: pygame.Rect) -> pygame.Rect:
        return pygame.Rect(rect.right - ARROW_INSET_R, rect.y + 8, ARROW_W,
                           rect.height - 16)

    def _ensure_visible(self) -> None:
        self._scroll = max(0, min(self._max_scroll, self._scroll))
        if self._selected < self._scroll:
            self._scroll = self._selected
        elif self._selected >= self._scroll + self._visible_rows:
            self._scroll = self._selected - self._visible_rows + 1

    @property
    def _field_spec(self) -> dict | None:
        """SPEC entry whose row owns the text field, or ``None``."""
        spec = SPEC[self._selected]
        return spec if spec["key"] in TEXT_ROW_LABELS else None

    @property
    def _editing_row(self) -> bool:
        spec = self._field_spec
        return bool(self._editing and spec is not None)

    def _value_rect(self, rect: pygame.Rect) -> pygame.Rect:
        """The strip a row's value is drawn in: from the column split up to the
        arrow zone, with a gutter on each side.

        Drawing used to place the value by its own width from ``rect.right-54``,
        so a long value (a 4K size, a long texture path) ran under the arrows
        and over the next row's label. The value is now cut to this band.
        """
        left = self._split_x(rect)
        return pygame.Rect(left, rect.y, max(0, self._arrow_zone_x(rect) - left),
                           rect.height)

    def _arrow_zone_x(self, rect: pygame.Rect) -> int:
        """Left edge of the value column's right end, where the arrows begin."""
        return self._left_arrow(rect).x - VALUE_GAP

    def _label_needed(self) -> int:
        """Width the widest caption in SPEC needs, measured in the row font."""
        if self._label_needed_px is None:
            self._label_needed_px = max(
                text_width(spec["label"], self.font) for spec in SPEC)
        return self._label_needed_px

    def _split_x(self, rect: pygame.Rect) -> int:
        """Where the value column starts; the caption owns everything left of it.

        One split feeds both bands, and both are measured from the row's left
        edge. Deriving the caption from the value's left edge instead (as the
        code used to) collapses it to zero width, because the value band already
        begins at the caption's left padding -- that is why every row used to
        render as an empty panel with a pair of arrows and no name.
        """
        arrow_x = self._arrow_zone_x(rect)
        usable = arrow_x - rect.x
        wanted = LABEL_PAD + self._label_needed() + LABEL_GAP
        cap = usable - VALUE_MIN_W
        return rect.x + max(1, min(wanted, max(1, cap)))

    def _label_rect(self, rect: pygame.Rect) -> pygame.Rect:
        """The strip a row's caption is drawn in, ending where the value begins."""
        left = rect.x + LABEL_PAD
        return pygame.Rect(left, rect.y,
                           max(0, self._split_x(rect) - LABEL_GAP - left), rect.height)

    def _select(self, index: int) -> None:
        if index == self._selected:
            return
        self._selected = max(0, min(len(SPEC) - 1, index))
        self._stop_editing()

    def _stop_editing(self) -> None:
        """Close the text field (if open) and hide the soft keyboard with it."""
        was = self._editing
        self._editing = False
        if was:
            self._text_input(False)

    def _text_input(self, start: bool) -> None:
        """Ask the platform for a soft keyboard while a text row is edited.

        Desktop SDL ignores this (physical keys already arrive as ``KEYDOWN``),
        Android shows the on-screen keyboard only after ``start_text_input`` --
        without it the field would be visible, editable in theory, and dead
        under the finger.
        """
        fn = getattr(pygame.key, "start_text_input" if start else "stop_text_input", None)
        if fn is None:
            return
        try:
            fn()
        except Exception:
            pass

    def _field_text(self, settings) -> str:
        key = SPEC[self._selected]["key"]
        if key == CUSTOM_ROW_KEY:
            return str(settings.get(CUSTOM_ROW_KEY))
        return settings.address_text

    def _open_editor(self, settings) -> None:
        spec = self._field_spec
        if spec is None:
            return
        self._editing = True
        self._text = self._field_text(settings)
        self._field_error = ""
        self._last_typed = None
        self._text_input(True)

    def _append_typed(self, ch: str, source: str) -> None:
        """Append one typed character, collapsing a two-channel duplicate.

        ``source`` is ``"key"`` (from ``KEYDOWN.unicode``) or ``"text"`` (from
        ``TEXTINPUT``).  The same keystroke delivered by both channels arrives
        one after the other, so a repeat of the identical character from the
        *other* channel is the same keystroke and is skipped; the same character
        twice from one channel is two keystrokes and is kept.
        """
        if not ch or not ch.isprintable() or len(self._text) >= 64:
            return
        other = "text" if source == "key" else "key"
        if self._last_typed == (ch, other):
            self._last_typed = (ch, source)
            return
        self._text += ch
        self._last_typed = (ch, source)

    def _apply_text(self, settings) -> None:
        """Commit the free-text field. Bad input is reported, not applied."""
        key = SPEC[self._selected]["key"]
        self._text_input(False)
        if key == CUSTOM_ROW_KEY:
            size, error = validate_size(self._text)
            settings.resolution_error = error
            self._field_error = error
            if size is None:
                return
            settings.set(key, f"{size[0]}\u00d7{size[1]}")
            return
        ok, error = settings.set_address(self._text)
        self._field_error = error
        if not ok:
            return

    def handle(self, events, settings) -> str | None:
        # One keystroke may be reported by two channels within this batch; the
        # duplicate is recognised only against the previous character, so the
        # memory is per batch.
        self._last_typed = None
        for event in events:
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if self.back_btn.collidepoint(event.pos):
                    self._stop_editing()
                    settings.save()
                    return "back"
                for i, spec in enumerate(SPEC):
                    rect = self._row_rect(i)
                    if not self._rows_rect.colliderect(rect):
                        continue  # scrolled out of the viewport
                    if self._left_arrow(rect).collidepoint(event.pos):
                        self._select(i)
                        settings.adjust(spec["key"], -1)
                        self._stop_editing()
                    elif self._right_arrow(rect).collidepoint(event.pos):
                        self._select(i)
                        settings.adjust(spec["key"], +1)
                        self._stop_editing()
                    elif rect.collidepoint(event.pos):
                        self._select(i)
                        if spec["kind"] == "bool":
                            settings.adjust(spec["key"], +1)
                        elif spec["kind"] == "text" and not self._editing:
                            self._open_editor(settings)
            elif event.type == pygame.MOUSEWHEEL:
                self._select(self._selected - event.y)
            elif event.type == pygame.TEXTINPUT and self._editing_row:
                # Android delivers printable characters here, not as KEYDOWN.
                for ch in getattr(event, "text", ""):
                    self._append_typed(ch, "text")
            elif event.type == pygame.KEYDOWN:
                if self._editing:
                    if event.key == pygame.K_RETURN:
                        self._apply_text(settings)
                        self._editing = False
                    elif event.key == pygame.K_ESCAPE:
                        self._stop_editing()
                    elif event.key == pygame.K_BACKSPACE:
                        self._text = self._text[:-1]
                        self._last_typed = None
                    elif event.unicode:
                        self._append_typed(event.unicode, "key")
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
                    elif spec["kind"] == "text":
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
            value = (settings.address_text if spec["key"] == "server_host"
                     else settings.get(spec["key"]))
            shown = format_value(spec["key"], value)
            if spec["kind"] == "text" and not selected:
                shown = f"{shown} [Enter]"
            value_txt = self.font.render(
                ellipsize(shown, self.font, value_rect.width),
                True, GREEN if selected else MUTED,
            )
            surface.blit(
                value_txt,
                (value_rect.right - value_txt.get_width(),
                 rect.y + (rect.height - value_txt.get_height()) // 2),
            )

            if spec["kind"] in ("float", "int", "list"):
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
        """The free-text field: visible only while a text row is selected.

        The label and the error each live in their own band above and below the
        field (see ``_layout``), so neither lands inside the row viewport or on
        the back button. One field serves both the window size and the server
        address; which row is selected decides the caption and the commit rule.
        """
        spec = self._field_spec
        if spec is None:
            return
        key = spec["key"]

        label_rect = self._field_label_rect
        label_txt = self.small_font.render(
            ellipsize(TEXT_ROW_LABELS[key], self.small_font, label_rect.width),
            True, MUTED)
        surface.blit(label_txt, (label_rect.x,
                                 label_rect.centery - label_txt.get_height() // 2))

        rect = self._field_rect
        pygame.draw.rect(surface, PANEL, rect, border_radius=4)
        border = SELECT if self._editing_row else MUTED
        pygame.draw.rect(surface, border, rect, 2, border_radius=4)
        text = self._text if self._editing else self._field_text(settings)
        shown = text + ("|" if self._editing_row else "")
        # Tail-clipped: the value is typed here, so the tail being edited has to
        # stay visible even when it outgrows the field.
        txt = self.small_font.render(
            clip_tail(shown, self.small_font, rect.width - 16), True, TEXT)
        surface.blit(txt, (rect.x + 8, rect.centery - txt.get_height() // 2))

        error = self._field_error
        if error:
            err_rect = self._error_rect
            err = self.small_font.render(
                ellipsize(error, self.small_font, err_rect.width), True, WARN)
            surface.blit(err, (err_rect.x, err_rect.centery - err.get_height() // 2))
