"""smoke_config.py — config.json is actually applied and address persists.

Covers the `client/config-application` change:
  * values from config.json reach the renderer (const/rendering/gs) and are not
    clobbered by RenderBridge construction;
  * SCALE/FOV stay consistent across const/rendering/game_state;
  * a successful connect writes server_host/server_port/player_name;
  * CLI --host/--port/--name win for the current run but are never written back;
  * config.json always ends up with every DEFAULTS key.
"""
import asyncio
import json
import math
import os
import tempfile

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')
import pygame
pygame.init()
pygame.display.set_mode((1000, 700))

import client.settings as settings_mod
from client.settings import DEFAULTS, Settings, launch_config, save_server_settings

tmpdir = tempfile.mkdtemp()
settings_mod.CONFIG_PATH = os.path.join(tmpdir, 'config.json')

# ── config.json is applied on game entry ──────────────────────────
with open(settings_mod.CONFIG_PATH, 'w', encoding='utf-8') as f:
    json.dump({
        'fov': 110, 'view_dist': 1600, 'render_scale': 2,
        'fps_cap': 30, 'mouse_sensitivity': 0.006,
        'show_minimap': False, 'show_crosshair': False,
    }, f)

s = Settings()
s.load()
assert s.get('fov') == 110 and s.get('view_dist') == 1600
assert s.get('render_scale') == 2 and int(s.get('fps_cap')) == 30

class FakeInput:
    mouse_sensitivity = 0.0

fi = FakeInput()
s.apply_runtime(fi)

from engine import constants as const
from engine import rendering
from engine import game_state as gs

assert abs(const.FOV - math.radians(110)) < 1e-9, const.FOV
assert abs(rendering.FOV - const.FOV) < 1e-9
assert abs(fi.mouse_sensitivity - 0.006) < 1e-9
assert gs.view_dist == 1600, gs.view_dist
assert const.SCALE == rendering.SCALE == gs.render_scale == 2, (
    const.SCALE, rendering.SCALE, gs.render_scale)
assert gs.show_minimap is False and gs.show_crosshair is False
print("config.json values applied + scale/FOV consistent OK")

# ── bridge construction must not clobber applied values ───────────
from client.bridge import RenderBridge

RenderBridge(pygame.Surface((1000, 700)))
assert gs.view_dist == 1600, gs.view_dist
assert gs.render_scale == 2, gs.render_scale
assert const.SCALE == rendering.SCALE == gs.render_scale == 2
print("RenderBridge keeps applied view_dist/render_scale OK")

# ── successful connect persists address + name ────────────────────
save_server_settings('game.example.net', 5555, 'Hero')
with open(settings_mod.CONFIG_PATH, encoding='utf-8') as f:
    data = json.load(f)
assert data['server_host'] == 'game.example.net'
assert data['server_port'] == 5555
assert data['player_name'] == 'Hero'
assert set(DEFAULTS) <= set(data), set(DEFAULTS) - set(data)
print("connect persists address + all DEFAULTS keys OK")

# ── CLI overrides win for the run but are not written back ────────
before = open(settings_mod.CONFIG_PATH, 'rb').read()
save_server_settings('cli.example.net', 6000, 'CLI',
                     cli_args=['--host', 'cli.example.net', '--port', '6000', '--name', 'CLI'])
assert open(settings_mod.CONFIG_PATH, 'rb').read() == before, \
    'CLI-only launch must not modify config.json'

save_server_settings('new.example.net', 6100, 'FromCli', cli_args=['--name', 'FromCli'])
with open(settings_mod.CONFIG_PATH, encoding='utf-8') as f:
    data = json.load(f)
assert data['player_name'] == 'Hero', data['player_name']
assert data['server_host'] == 'new.example.net' and data['server_port'] == 6100
print("CLI priority (no write-back for overridden fields) OK")

host, port, name = launch_config(['--host', 'h', '--port', '1234', '--name', 'N'])
assert (host, port, name) == ('h', 1234, 'N'), (host, port, name)
host, port, name = launch_config([])
assert (host, port, name) == ('new.example.net', 6100, 'Hero'), (host, port, name)
print("launch_config CLI priority / config fallback OK")

# ── _attempt_connect really writes what was entered ───────────────
import client.client as client_mod

class _FakeNet:
    def __init__(self, host, port):
        self.host, self.port = host, port
        self.last_error = None
        self.player_id = 1

    async def connect(self, name):
        return True

    def disconnect(self):
        pass

orig_net = client_mod.NetworkClient
client_mod.NetworkClient = _FakeNet
try:
    gc = client_mod.GameClient('ignored', 1, 'ignored')
    assert gc.fps_cap == 30, gc.fps_cap
    assert const.SCALE == 2 and gs.view_dist == 1600
    gc.connect_screen.host = 'typed.example.org'
    gc.connect_screen.port = '4321'
    gc.connect_screen.name = 'TypedName'
    assert asyncio.run(gc._attempt_connect()) is True
finally:
    client_mod.NetworkClient = orig_net

with open(settings_mod.CONFIG_PATH, encoding='utf-8') as f:
    data = json.load(f)
assert data['server_host'] == 'typed.example.org', data['server_host']
assert data['server_port'] == 4321, data['server_port']
assert data['player_name'] == 'TypedName', data['player_name']
print("_attempt_connect persists entered address/name + fps_cap applied OK")

# ── entering a party clears the previous party's transient state ──
from engine import game_state as gs

class _FakeGameMap:
    def __init__(self):
        self.tiles = [[0]]
        self.width = self.height = 1
    @classmethod
    def from_bytes(cls, data, w, h):
        return cls()

class _FakeBridge:
    def __init__(self, surface):
        pass
    def set_map(self, *a, **k):
        pass
    def set_sector_map(self, *a, **k):
        pass

orig_gamemap = client_mod.GameMap
orig_bridge = client_mod.RenderBridge
client_mod.GameMap = _FakeGameMap
client_mod.RenderBridge = _FakeBridge
try:
    gc.network.map_format = 0
    gc.network.map_data = b""
    gc.network.map_width = gc.network.map_height = 1
    gc.network.player_id = 1
    # Dirty every transient field with something from a "previous party".
    gs.particles = [{'x': 1}]
    gs.decals = [{'x': 1}]
    gs.weather = [{'x': 1}]
    gs.enemies = [{'id': 1}]
    gs.corpses = [{'id': 1}]
    gs.items = [{'id': 1}]
    gs.dwall_hp = {(0, 0): 3}
    gs.bridge_open = {(0, 0)}
    gc._init_from_server()
finally:
    client_mod.GameMap = orig_gamemap
    client_mod.RenderBridge = orig_bridge

assert gs.particles == [] and gs.decals == [] and gs.weather == []
assert gs.enemies == [] and gs.corpses == [] and gs.items == []
assert gs.dwall_hp == {} and gs.bridge_open == set()
print("starting a party resets transient state OK")

print("ALL CONFIG SMOKE TESTS PASSED")
