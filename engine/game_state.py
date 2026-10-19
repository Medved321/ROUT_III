"""Namespace shim for the ported ROUT_II_III renderer.

The original game accessed every piece of state through a single global
module (``game_state as gs``).  The multiplayer host fills these fields via
the bridge module before every frame.
"""
from __future__ import annotations

import os
import pygame

# Base assets directory: skins/*.png resolve from here (not CWD). Default is
# the project root (engine/ lives directly under it); override from outside
# e.g. for the zipapp artifact / offline render.
assets_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Display / fonts (set up by the bridge)
screen = None
font = None
sm_font = None

# Map state
MAP = []
M_SZ = 0
sector_mode = False
sector_grid = []
sectors = []
sec_verts = []
sec_lines = []
sec_sectors = []
sec_line_door = {}
sec_lines_by_sector = []
explored = []
show_minimap = True
show_crosshair = True

# Local player
p = {
    'hp': 100, 'lvl': 1, 'wpn': 1,
    'pstl': 200, 'shl': 0, 'slg': 0, 'rck': 0, 'BFG': 0,
    'keys': {'B': 0, 'R': 0, 'Y': 0},
    'armor': None, 'powerup': None, 'invuln': False, 'dmg_mult': 1.0,
    'guns': {0, 1}, 'custom_guns': [], 'custom_wpn_idx': 0,
    'attachments': {}, 'attachment_inv': set(),
    'aiming': False, 'melee_mode': False, 'melee_atk': None, 'melee_tmr': 0,
    'soul_charges': 0, 'soul_kill_counter': 0,
}
px = 128.0
py = 128.0
pz = 0.0
pa = 0.0
pitch = 0.0
# Weapon animation clock: frames at 60 fps still to run in the current
# weapon's fire/cooldown animation. 0 means idle. The renderer divides it by
# WPN[i]['f'] to get the animation phase, so it must be expressed in the same
# 60 fps frame unit the weapon tables use -- not in rendered frames.
w_tmr = 0
d_flash = 0
# Screen speed-line envelope for the local player's own dash: 1.0 on the frame
# the server confirms the dash, decaying per rendered frame. 0 = not dashing, and
# the overlay then costs nothing.
dash_fx = 0.0
face_dir = 0
start_hp = 100
view_dist = 900
render_scale = 4
# Adaptive-quality effect budget multiplier (1.0 = configured quality).
effects_factor = 1.0
# Sector-mode quality knobs: vertical buffer resolution scale and textured
# flat rendering (dropped by the adaptive controller under load, D9).
vs_scale = 2
flat_textured = True
# Built SectorScene for sector-mode maps (tasks 3.1-3.7).
sec_scene = None

# Framebuffer backend (total-performance): internal fixed-resolution RGB
# buffer and the effective graphic tier.
frame = None
quality_tier = "high"
quality_mode = "auto"   # "auto" | "high" | "medium" | "fast" (manual lock)
iwidth = 320
iheight = 240

# Effects
particles = []
decals = []
weather = []
weather_type = 'none'
blood_amount = 1.0

# Entities (kept empty by the multiplayer bridge)
enemies = []
bots = []
items = []
barrels = []
portals = []
bul = []
proj = []
corpses = []
dwall_hp = {}
bridge_open = set()

# Other players (filled by the bridge, one dict per opponent)
players = []

# Legacy single-opponent fields (unused, kept for API compatibility)
other_active = False
other_px = 0.0
other_py = 0.0
other_pa = 0.0
other_name = ''
other_wpn = 0

# HUD / stats
# Frame rate of the last rendered frame, in whole fps.  The engine has no
# clock of its own, so the client measures it and feeds it here once per
# frame; 0 means "not measured yet" and the readout stays hidden.
fps = 0.0
kills = 0
rounds = 0
shots_fired = 0
shots_hit = 0
damage_dealt = 0
damage_taken = 0
items_picked = 0
secrets_found = 0
secrets_total = 0
dm_frags = {}
dm_team_scores = {}
dm_timer = 0
dm_game_over = False
dm_team_mode = False
dm_winner = 0
orda_wave = 0
wave_msg_timer = 0
lvl_type = 'exit'
exit_pos = (0, 0)
show_key_msg = 0
key_msg = ''
elevator_prompt = ''
cheat_open = False
cheat_buf = ''
replay_mode = False
replay_data = []
replay_index = 0

# Textures
textures = {}
flats = {}

# Content providers (updating_engine D2): the engine creates no sprites and
# knows no editor palette, so the owner of the content (the client, through
# client/bridge.py) registers callables here before the first frame.
#   sprite_provider(color, w, h, frame, state, pa, wpn, view) -> pygame.Surface
#   tile_provider(tile_char) -> wall texture name
# None degrades predictably instead of raising: bots get a blank SRCALPHA
# surface, tiles get the neutral 'wall' texture.
#   sprite_provider(color, w, h, frame, state, pa, wpn, view) -> pygame.Surface
#   tile_provider(tile_char) -> wall texture name
#   enemy_surface_provider(etype, h, frame, state, view, detail) -> Surface
# None degrades predictably instead of raising: bots get a blank SRCALPHA
# surface, tiles get the neutral 'wall' texture, enemies get a neutral
# silhouette sized to the sprite height.
sprite_provider = None
tile_provider = None
enemy_surface_provider = None

# Enemy models (enemy-model-overhaul).  The flag keeps the old immediate-mode
# enemy path reachable for rollback: set False and the renderer draws the
# previous per-type primitives without any code change.
enemy_models_enabled = True

# Fog
fog_enabled = False
fog_color = (80, 75, 70)
fog_density = 60

# Cache used by map_utils._player_in_any_sector
player_sector_cache = -1
player_sector_timer = 0


def reset_transient_state() -> None:
    """Clear per-party world/effect state so a new party starts blank.

    The bridge refills the entity lists from the first snapshot and rebuilds
    ``explored`` when it installs the map, but effects, corpses and the
    destructible/bridge sets are client-side only: without this a party would
    inherit the previous one's dust, decals and damaged-wall state.  Per-session
    values (providers, textures, fog, settings) are deliberately left alone.
    """
    global particles, decals, weather, weather_type
    global enemies, bots, items, barrels, portals, bul, proj, corpses, players
    global dwall_hp, bridge_open, explored
    particles = []
    decals = []
    weather = []
    weather_type = 'none'
    enemies = []
    bots = []
    items = []
    barrels = []
    portals = []
    bul = []
    proj = []
    corpses = []
    players = []
    dwall_hp = {}
    bridge_open = set()
    explored = []