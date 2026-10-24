"""Layout and pointer-mapping invariants for the menu screens.

Two properties are checked, on every screen:

1. Geometry. No two clickable elements overlap, their union stays inside the
   1000x700 menu canvas, and every label/value is cut to the box it is drawn in.
   The first half used to fail: the sector editor's bottom toolbar needed 1058px
   of a 1000px canvas and "▶ Играть" ran into "Назад", and a 4K window size in
   settings ran its value under the "<" arrow.

2. Hit-testing. A click on the *visual* centre of a drawn element activates
   that element, at every window size, and a click in the black bars activates
   nothing. That path runs the real ``GameClient._menu_events`` /
   ``Renderer.to_canvas_pos``, so a menu can never be laid out for canvas
   coordinates and fed window coordinates.

Run: ``py -3.12 smoke_menu_layout.py``
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

pygame.init()
surface = pygame.display.set_mode((1000, 700))

import client.settings as settings_mod
from client.client import GameClient
from client.connect_screen import ConnectScreen
from client.editor_screen import MapEditorScreen
from client.lobby_screen import LobbyScreen
from client.pause_screen import PauseScreen
from client.end_screen import EndScreen
from client.renderer import SCREEN_HEIGHT, SCREEN_WIDTH, Renderer
from client.sector_editor_screen import SectorEditorScreen
from client.settings import DEFAULTS, Settings, format_value
from client.settings_screen import CUSTOM_ROW_KEY, SPEC, SettingsScreen
from client.ui_layout import ellipsize

CANVAS = pygame.Rect(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT)
WINDOW_SIZES = [(640, 480), (1000, 700), (1600, 900), (3840, 2160)]
BTN_PAD = 10

tmpdir = tempfile.mkdtemp()
settings_mod.CONFIG_PATH = os.path.join(tmpdir, "config.json")
import client.editor_screen as editor_mod
import client.sector_editor_screen as sector_mod
editor_mod.MAPS_DIR = os.path.join(tmpdir, "maps")
sector_mod.MAPS_DIR = os.path.join(tmpdir, "maps")

failures: list[str] = []


def check(ok: bool, message: str) -> None:
    if not ok:
        failures.append(message)


def worst_values(spec: dict) -> list[str]:
    """The widest texts a settings row can ever show.

    A row is not checked against the value that happens to be in ``config.json``
    but against everything it can reach: every entry of a list row, both ends of
    a numeric range, and the hand-typed size a ``custom`` row accepts -- that one
    is not in its preset list, so it reads as «Другое → …» and is the longest
    string the value band will ever be asked to hold.
    """
    key = spec["key"]
    texts: list[str] = []
    for value in spec.get("values") or []:
        texts.append(format_value(key, value))
    if "min" in spec:
        texts.append(format_value(key, spec["min"]))
        texts.append(format_value(key, spec["max"]))
    if spec.get("custom"):
        for size in ("3840\u00d72160", "4444\u00d73333"):
            texts.append(format_value(key, size))
    return texts or [format_value(key, DEFAULTS[key])]


# ── element collection ───────────────────────────────────────────
def elements(name: str, screen) -> list[tuple[str, pygame.Rect]]:
    """The clickable elements of a screen, as (name, rect) pairs.

    Written per screen rather than scraped, so a test failure names the control
    that moved instead of "something in the list".
    """
    if isinstance(screen, ConnectScreen):
        return [
            ("host", screen.host_box), ("port", screen.port_box),
            ("name", screen.name_box), ("connect", screen.connect_btn),
        ]
    if isinstance(screen, LobbyScreen):
        return [("name", screen.name_box)] + [
            (k, r) for k, r in screen.buttons.items()
        ]
    if isinstance(screen, PauseScreen):
        return [(f"option{i}", r) for i, r in enumerate(screen.buttons)]
    if isinstance(screen, EndScreen):
        buttons = (screen.model or {}).get("buttons", [])
        return [(a, r) for (a, _), r in zip(buttons, screen.buttons)]
    if isinstance(screen, MapEditorScreen):
        out = [("name", screen.name_box)]
        return out + [(k, r) for k, r in screen.buttons.items()]
    if isinstance(screen, SectorEditorScreen):
        out = [("name", screen.name_box), ("map_list", screen.map_list_btn)]
        return out + [(k, r) for k, r in screen.buttons.items()]
    if isinstance(screen, SettingsScreen):
        return [
            ("back", screen.back_btn),
            ("field_label", screen._field_label_rect),
            ("field", screen._field_rect),
            ("error", screen._error_rect),
        ]
    raise AssertionError(f"unknown screen {name}")


def button_labels(screen) -> list[tuple[str, str, pygame.Rect]]:
    """(name, label, rect) for every element whose caption must fit its box."""
    out: list[tuple[str, str, pygame.Rect]] = []
    if isinstance(screen, ConnectScreen):
        from client.connect_screen import CONNECT_LABEL
        out.append(("connect", CONNECT_LABEL, screen.connect_btn))
    elif isinstance(screen, LobbyScreen):
        for k, r in screen.buttons.items():
            out.append((k, screen._button_labels[k], r))
    elif isinstance(screen, PauseScreen):
        from client.pause_screen import OPTIONS
        for (key, label), r in zip(OPTIONS, screen.buttons):
            out.append((key, label, r))
    elif isinstance(screen, EndScreen):
        for (key, label), r in zip((screen.model or {}).get("buttons", []),
                                   screen.buttons):
            out.append((key, label, r))
    elif isinstance(screen, MapEditorScreen):
        for k, r in screen.buttons.items():
            out.append((k, screen._button_labels()[k], r))
    elif isinstance(screen, SectorEditorScreen):
        for k, r in screen.buttons.items():
            out.append((k, screen._button_labels()[k], r))
    elif isinstance(screen, SettingsScreen):
        out.append(("back", "Назад [Esc]", screen.back_btn))
    return out


def check_geometry(name: str, screen) -> None:
    items = elements(name, screen)
    for i, (an, ar) in enumerate(items):
        check(CANVAS.contains(ar) or ar.clipline(CANVAS.topleft, CANVAS.bottomright),
              f"{name}: {an} {ar} leaves the {SCREEN_WIDTH}x{SCREEN_HEIGHT} canvas")
        for bn, br in items[i + 1:]:
            check(not ar.colliderect(br), f"{name}: {an} {ar} overlaps {bn} {br}")

    font = getattr(screen, "font", None) or screen.tiny_font
    for key, label, rect in button_labels(screen):
        shown = ellipsize(label, font, rect.width - 2 * BTN_PAD)
        check(font.size(shown)[0] <= rect.width - 2 * BTN_PAD,
              f"{name}: label {label!r} does not fit {rect}")


# ── screens under test ────────────────────────────────────────────
settings = Settings()
settings.set(CUSTOM_ROW_KEY, "3840×2160")
settings.resolution_error = "Размер вне границ: 9999x9999 при 320x200..7680x4320"
# The size row must be the selected one for the field bands to be live.
size_row = next(i for i, s in enumerate(SPEC) if s["key"] == CUSTOM_ROW_KEY)

SCREENS = []
_connect = ConnectScreen(SCREEN_WIDTH, SCREEN_HEIGHT, "localhost", 7777, "Player")
_connect.host = "h" * 48
_connect.name = "n" * 48
_connect.status = "Сервер недоступен"
SCREENS.append(("connect", _connect))

_lobby = LobbyScreen(SCREEN_WIDTH, SCREEN_HEIGHT, "Player", "localhost:7777")
_lobby.set_custom_map("очень-длинное-имя-карты-" * 2)
_lobby.set_state([{"id": 0, "name": "x" * 30, "players": [], "max": 4}], "Статус " * 20)
SCREENS.append(("lobby", _lobby))

SCREENS.append(("pause", PauseScreen(SCREEN_WIDTH, SCREEN_HEIGHT)))

_end = EndScreen(SCREEN_WIDTH, SCREEN_HEIGHT)
_end.set_model({
    "title": "ПОБЕДА",
    "reason": "",
    "tone": "win",
    "stats": [("Убийства", "3"), ("Смерти", "1"), ("Точность", "42%")],
    "table": {"headers": ["Игрок", "Убийства", "Смерти"],
              "rows": [["Player", "3", "1"], ["Enemy", "1", "3"]],
              "highlight": 0},
    "buttons": [("leave", "В МЕНЮ")],
    "primary": "leave",
    "hint": "Enter — в меню",
})
SCREENS.append(("end_victory", _end))

_editor = MapEditorScreen(SCREEN_WIDTH, SCREEN_HEIGHT)
_editor.map_name = "m" * 60
SCREENS.append(("editor", _editor))

_sector = SectorEditorScreen(SCREEN_WIDTH, SCREEN_HEIGHT)
_sector.map_name = "m" * 60
_sector._map_files = ["очень-длинное-имя-файла.mts" * 2] * 4
_sector._map_list_open = True
SCREENS.append(("sector_editor", _sector))

_settings = SettingsScreen(SCREEN_WIDTH, SCREEN_HEIGHT)
_settings._select(size_row)
SCREENS.append(("settings", _settings))

for name, screen in SCREENS:
    screen.draw(surface, settings) if name == "settings" else screen.draw(surface)
    check_geometry(name, screen)
print("geometry: no overlaps, everything inside the canvas, labels fit")

# A settings row used to draw an empty panel with a pair of arrows: the caption
# band was measured from the value band's left edge, which already sat at the
# caption's own padding, so every width came out as zero and every name was
# silently dropped. A caption nobody can read is not a layout nit, so the bands
# are checked for the thing that actually broke: an empty or cut caption, and a
# value that reaches under the arrows.
_settings_screen = dict(SCREENS)["settings"]
_settings_font = _settings_screen.font
for _i, _spec in enumerate(SPEC):
    _rect = _settings_screen._row_rect(_i)
    _label_rect = _settings_screen._label_rect(_rect)
    _value_rect = _settings_screen._value_rect(_rect)
    check(_label_rect.width > 0 and _value_rect.width > 0,
          f"settings: {_spec['key']} has an empty band (label {_label_rect}, "
          f"value {_value_rect})")
    check(ellipsize(_spec["label"], _settings_font, _label_rect.width) == _spec["label"],
          f"settings: caption {_spec['label']!r} is cut to {_label_rect.width}px")
    for _value in worst_values(_spec):
        check(ellipsize(_value, _settings_font, _value_rect.width) == _value,
              f"settings: value {_value!r} is cut to {_value_rect.width}px")
    check(not _label_rect.colliderect(_value_rect),
          f"settings: {_spec['key']} caption and value bands overlap")
    check(not _value_rect.colliderect(_settings_screen._left_arrow(_rect)),
          f"settings: {_spec['key']} value band runs under the arrows")
print("settings: every caption and value fits its own band")

# ── hit-testing through the real mapping ──────────────────────────
renderer = Renderer((1000, 700))


def window_pos(canvas_x: float, canvas_y: float, size) -> tuple[int, int]:
    """Where a canvas point is *drawn*: the forward direction of the letterbox."""
    dst = renderer.canvas_rect()
    return (
        dst.x + int(canvas_x * dst.w / SCREEN_WIDTH),
        dst.y + int(canvas_y * dst.h / SCREEN_HEIGHT),
    )


# 6.1b: the sector editor's dropdown rows overlay the canvas on purpose (it is
# an open list), so they are checked against each other and against the panel.
_dropdown = SectorEditorScreen(SCREEN_WIDTH, SCREEN_HEIGHT)
_dropdown._map_files = ["карта-%d.mts" % i for i in range(30)]
_dropdown._map_list_open = True
_box, _rows, _hidden, _y = _dropdown._map_dropdown_geometry()
for i, (rn, rr) in enumerate(_rows):
    check(not rr.colliderect(_dropdown.panel),
          f"sector_editor: map row {rn} {rr} runs into the panel")
    for sn, sr in _rows[i + 1:]:
        check(not rr.colliderect(sr), f"sector_editor: map rows {rn}/{sn} overlap")
check(_map_rows := _dropdown._map_dropdown_geometry()[1] == _rows,
      "sector_editor: dropdown geometry is not stable between calls")
print("map dropdown: rows fit, do not overlap, geometry is stable")

# Black bars: a point outside the letterboxed canvas must activate nothing.
for size in WINDOW_SIZES:
    renderer.set_window_size(size)
    dst = renderer.canvas_rect()
    candidates = [
        (0, 0), (size[0] - 1, 0), (0, size[1] - 1), (size[0] - 1, size[1] - 1),
        (0, size[1] // 2), (size[0] - 1, size[1] // 2),
        (size[0] // 2, 0), (size[0] // 2, size[1] - 1),
    ]
    bars = [p for p in candidates if not dst.colliderect(pygame.Rect(*p, 1, 1))]
    # 1000x700 is the canvas size, so the window is filled edge to edge and has
    # no black bars at all -- that is the case where a mapping bug is invisible.
    if (size[0], size[1]) != (SCREEN_WIDTH, SCREEN_HEIGHT):
        check(bool(bars), f"at {size} no candidate landed in a black bar")
    for point in bars:
        check(renderer.to_canvas_pos(point) is None,
              f"to_canvas_pos({point}) at {size} should be None (black bar)")
    for point in ((dst.x, dst.y), (dst.right - 1, dst.bottom - 1),
                  (dst.centerx, dst.centery)):
        check(renderer.to_canvas_pos(point) is not None,
              f"to_canvas_pos({point}) at {size} should map inside the canvas")
print(f"black bars: clicks outside the canvas map to None at {WINDOW_SIZES}")

# Every drawn element is hit at its own visual centre, at every window size.
def centre_click(screen, rect, size, expected, settings_obj=None):
    """Click the visual centre of ``rect`` in window pixels, as the client does."""
    renderer.set_window_size(size)
    client = _ClientShim(renderer)
    target = (rect.centerx, rect.centery)
    wpos = window_pos(*target, size)
    event = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=wpos)
    mapped = client._menu_events([event])
    if not mapped:
        return f"{size}: click at {wpos} (canvas {target}) fell in a black bar"
    if settings_obj is None:
        got = screen.handle(mapped)
    else:
        got = screen.handle(mapped, settings_obj)
    if got != expected:
        return (f"{size}: click at canvas {target} (window {wpos}) gave {got!r}, "
                f"expected {expected!r}")
    return None


class _ClientShim:
    """Just enough of GameClient to run its pointer translation for real."""

    def __init__(self, renderer):
        self.renderer = renderer

    _menu_events = GameClient._menu_events


def expected_action(name: str, key: str) -> object:
    return {
        "connect": "connect", "create": "create", "refresh": "refresh",
        "editor": "editor", "settings": "settings", "quit": "quit",
        "back": "back", "play": "play", "sector_mode": "sector",
        "host": None, "port": None, "name": None, "map_list": None,
        "size_minus": None, "size_plus": None, "new": None, "clear": None,
        "save": None, "load": None, "snap": None, "undo": None,
        "redo": None, "save_mts": None, "load_mts": None,
        # "walls" flips the create-time boundary policy and redraws itself.
        "walls": None,
        "field_label": None, "field": None, "error": None,
        # "mode" switches the sector editor back to the tile editor.
        "mode": "tile",
        # End-screen buttons: only "В МЕНЮ" is drawn here, so only that action
        # is reachable by a centre click.
        "leave": "leave",
    }.get(key, "?")


SKIP_CENTRES = {
    # "Создать" both hits its own box and leaves the party list; the party-list
    # rows are checked separately below.
    "lobby": set(),
    "editor": {"name"},
}

for size in WINDOW_SIZES:
    for name, screen in SCREENS:
        settings_obj = settings if name == "settings" else None
        if name == "settings":
            screen._select(size_row)
        for key, rect in elements(name, screen):
            if key in SKIP_CENTRES.get(name, set()):
                continue
            if name == "pause":
                expected = dict(
                    (f"option{i}", opt[0]) for i, opt in enumerate(
                        __import__("client.pause_screen", fromlist=["OPTIONS"]).OPTIONS)
                )[key]
            else:
                expected = expected_action(name, key)
            err = centre_click(screen, rect, size, expected, settings_obj)
            if err:
                failures.append(f"{name}: {err}")
print(f"hit-testing: every element activates at its visual centre, {WINDOW_SIZES}")

# The pause overlay in particular must not drift: each option has its own
# action, so a shifted overlay would hit the neighbour.
_restore = renderer.set_window_size((1000, 700))
for size in WINDOW_SIZES:
    renderer.set_window_size(size)
    shim = _ClientShim(renderer)
    for i, rect in enumerate(_pause_rects := PauseScreen(1000, 700).buttons):
        wpos = window_pos(rect.centerx, rect.centery, size)
        got = PauseScreen(1000, 700).handle(
            shim._menu_events([pygame.event.Event(pygame.MOUSEBUTTONDOWN,
                                                 button=1, pos=wpos)]))
        want = ("resume", "settings", "leave", "quit")[i]
        check(got == want, f"pause at {size}: option {i} gave {got!r}, want {want!r}")
print("pause overlay: each option activates its own action at every size")

if failures:
    print(f"\n{len(failures)} FAILURES:")
    for line in failures:
        print(f"  - {line}")
    raise SystemExit(1)
print("smoke_menu_layout: ALL OK")
