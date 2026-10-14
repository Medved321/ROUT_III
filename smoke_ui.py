import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")  # labels contain arrows
os.environ['SDL_VIDEODRIVER'] = 'dummy'
import pygame
pygame.init()
surface = pygame.display.set_mode((1000, 700))

import client.settings as settings_mod
from client.settings import Settings
from client.settings_screen import SettingsScreen
from client.pause_screen import PauseScreen
from client.editor_screen import MapEditorScreen
from client.lobby_screen import LobbyScreen

tmpdir = tempfile.mkdtemp()
settings_mod.CONFIG_PATH = os.path.join(tmpdir, 'config.json')
import client.editor_screen as editor_mod
editor_mod.MAPS_DIR = os.path.join(tmpdir, 'maps')

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

# ── editor screen ────────────────────────────────────────────────
ed = MapEditorScreen(1000, 700)
sz0 = ed.editor.sz
ed._button('size_plus')
assert ed.editor.sz == sz0 + 4
ed._button('size_minus')
assert ed.editor.sz == sz0
c = ed.canvas.center
cell = ed._cell_at(c)
assert cell is not None
assert ed._cell_at((0, 0)) is None
ed._apply_brush(c, erase=False)
cx, cy = cell
assert ed.editor.grid[cy][cx] == ed.brush, (ed.editor.grid[cy][cx], ed.brush)
ed._apply_brush(c, erase=True)
assert ed.editor.grid[cy][cx] == '.'
ed._apply_brush(c, erase=False)
ed.map_name = 'smoke_test'
ed._save()
assert os.path.exists(os.path.join(editor_mod.MAPS_DIR, 'smoke_test.json'))
saved = [row[:] for row in ed.editor.grid]
ed._button('clear')
assert ed.editor.grid[cy][cx] == '.'
ed._load()
assert ed.editor.grid == saved
ed.draw(surface)
play_click = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=ed.buttons['play'].center)
assert ed.handle([play_click]) == 'play'
gm = ed.to_game_map()
assert gm.width == ed.editor.sz and gm.height == ed.editor.sz
print("editor screen OK (incl. save/load, play)")

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
    assert ed.handle(click_at(ed.buttons['play'].center, size)) == 'play', size
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
