import ast
import os
import sys
import tempfile
import unicodedata
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
os.environ['SDL_VIDEODRIVER'] = 'dummy'
import pygame
pygame.init()
surface = pygame.display.set_mode((1000, 700))

import client.settings as settings_mod
from client.settings import Settings
from client.settings_screen import SettingsScreen
from client.pause_screen import PauseScreen
from client.sector_editor_screen import SectorEditorScreen
from client.lobby_screen import LobbyScreen

tmpdir = tempfile.mkdtemp()
settings_mod.CONFIG_PATH = os.path.join(tmpdir, 'config.json')
import client.sector_editor_screen as sector_mod
from client import map_library
sector_mod.MAPS_DIR = os.path.join(tmpdir, 'maps')
map_library.MAPS_DIR = sector_mod.MAPS_DIR

# ── no decorative glyphs in user-visible strings ────────────────
# Comments and docstrings are developer text, not labels. Every other string
# literal in a UI module must be made of letters, digits, punctuation and
# ordinary whitespace. Arrows, ticks and box-drawing glyphs are symbols (Sm/So)
# and are rejected by the whitelist below; the in-memory mutation proves the
# scanner is not a no-op.
ROOT = Path(__file__).resolve().parent
UI_TEXT_FILES = (
    'client/lobby_screen.py', 'client/settings.py', 'client/settings_screen.py',
    'client/pause_screen.py', 'client/end_screen.py', 'client/connect_screen.py',
    'client/sector_editor_screen.py', 'client/renderer.py', 'client/ui_layout.py',
    'shared/text_map.py', 'engine/rendering.py',
)
_ASCII = frozenset(chr(c) for c in range(0x20, 0x7F))
_EXTRA = frozenset('\n\r\t×°№\ufeff')


def _allowed_char(ch):
    if ch in _ASCII or ch in _EXTRA:
        return True
    return unicodedata.category(ch)[0] in {'L', 'N', 'P', 'Z'}


def _docstring_ids(tree):
    ids = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, 'body', ())
        if not body or not isinstance(body[0], ast.Expr):
            continue
        value = body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            ids.add(id(value))
    return ids


def _scan_sources(sources):
    bad = []
    for path, text in sources.items():
        tree = ast.parse(text, filename=path)
        skip = _docstring_ids(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if id(node) in skip:
                continue
            for ch in node.value:
                if not _allowed_char(ch):
                    bad.append((path, node.lineno, repr(ch), node.value[:60]))
                    break
    return bad


assert _scan_sources({'client/pause_screen.py': 'HINT = "Сетка ✓"\n'}), \
    'glyph scanner missed an injected tick'
_sources = {rel: (ROOT / rel).read_text(encoding='utf-8') for rel in UI_TEXT_FILES}
_violations = _scan_sources(_sources)
assert not _violations, 'decorative glyphs in UI strings: %r' % (_violations,)
print('UI strings contain no decorative glyphs OK')

# ── settings ─────────────────────────────────────────────────────
s = Settings()
assert s.get('fov') == 90
s.adjust('fov', +1)
assert s.get('fov') == 95
for _ in range(20):
    s.adjust('fov', +1)
assert s.get('fov') == 110, s.get('fov')
s.adjust('fov', -1)
assert s.get('fov') == 105
s.adjust('show_minimap', +1)
assert s.get('show_minimap') is False
s.adjust('render_scale', +1)
assert s.get('render_scale') == 5
s.save()
s2 = Settings()
s2.load()
assert s2.get('fov') == 105 and s2.get('show_minimap') is False and s2.get('render_scale') == 5
print("settings roundtrip OK")

class FakeInput:
    mouse_sensitivity = 0.0
fi = FakeInput()
s.apply_runtime(fi)
from engine import constants as const
from engine import rendering
from engine import game_state as gs
assert abs(fi.mouse_sensitivity - s.get('mouse_sensitivity')) < 1e-9
assert abs(rendering.FOV - const.FOV) < 1e-9
assert gs.view_dist == int(s.get('view_dist'))
assert const.SCALE == int(s.get('render_scale'))
print("settings apply_runtime OK")

# ── settings screen ──────────────────────────────────────────────
ss = SettingsScreen(1000, 700)
esc = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE, unicode='')
assert ss.handle([esc], s) == 'back'
ss.draw(surface, s)
down = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_DOWN, unicode='')
ss.handle([down], s)
assert ss._selected == 1
right = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RIGHT, unicode='')
ss.handle([right], s)
print("settings screen OK")

# ── pause screen ─────────────────────────────────────────────────
ps = PauseScreen(1000, 700)
for ev in ([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE)],
           [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_DOWN)],
           [pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN)]):
    res = ps.handle(ev)
    if res is not None:
        assert res in ('resume', 'settings', 'leave', 'quit'), res
        break
ps.draw(surface)
click = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=ps.buttons[0].center)
assert ps.handle([click]) == 'resume'
print("pause screen OK")

# ── sector editor is the only editor screen ─────────────────────
se = SectorEditorScreen(1000, 700)
assert 'mode' not in se.buttons, 'sector editor still offers a tile-mode switch'
se.draw(surface)
f5 = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F5, unicode='')
assert se.handle([f5]) == 'play', se.status
print("sector editor OK (single editor path, play)")

# ── lobby screen has new buttons ─────────────────────────────────
lb = LobbyScreen(1000, 700, "Player", "localhost:7777")
ok_editor = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=lb.editor_btn.center)
ok_settings = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=lb.settings_btn.center)
assert lb.handle([ok_editor]) == 'editor'
assert lb.handle([ok_settings]) == 'settings'
key_e = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_e, unicode='e')
assert lb.handle([key_e]) == 'editor'
lb.set_custom_map('my_map')
assert lb.custom_map_label == 'my_map'
lb.draw(surface)
key_c = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_c, unicode='c')
assert lb.handle([key_c]) == 'clear_map'
print("lobby editor/settings/custom-map OK")

# ── the same clicks through the real window→canvas path ──────────
# The checks above inject canvas coordinates straight into handle(), which is
# the contract the screens expect. This block goes through the client's actual
# translation (pygame reports window pixels, menus are laid out on the 1000x700
# canvas), so a drift between drawn and clicked geometry shows up here rather
# than in a game.
from client.client import GameClient
from client.renderer import SCREEN_HEIGHT, SCREEN_WIDTH, Renderer


class _Mapper:
    """Just the translation half of GameClient, without a network client."""

    def __init__(self, renderer):
        self.renderer = renderer

    _menu_events = GameClient._menu_events


_renderer = Renderer((1000, 700))
_mapper = _Mapper(_renderer)


def click_at(canvas_pos, size):
    """A click on where ``canvas_pos`` is drawn in a ``size`` window."""
    _renderer.set_window_size(size)
    dst = _renderer.canvas_rect()
    win = (dst.x + int(canvas_pos[0] * dst.w / SCREEN_WIDTH),
           dst.y + int(canvas_pos[1] * dst.h / SCREEN_HEIGHT))
    return _mapper._menu_events(
        [pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=win)])


for size in ((640, 480), (1000, 700), (1600, 900), (3840, 2160)):
    assert se.handle(click_at(se.buttons['play'].center, size)) == 'play', size
    assert lb.handle(click_at(lb.buttons['settings'].center, size)) == 'settings', size
    assert ps.handle(click_at(ps.buttons[2].center, size)) == 'leave', size
    # A point in the black bars belongs to no control and must do nothing.
    dst = _renderer.canvas_rect()
    outside = (0, 0) if dst.x or dst.y else None
    if outside is not None:
        bar = _mapper._menu_events(
            [pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=outside)])
        assert bar == [], (size, outside, bar)
_renderer.set_window_size((1000, 700))
print("window→canvas click mapping OK (incl. black bars)")

print("ALL UI SMOKE TESTS PASSED")
