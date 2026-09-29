"""Namespace shim for the ported ROUT_II_III renderer.

The original game accessed every piece of state through a single global
module (``game_state as gs``).  The multiplayer host fills these fields via
the bridge module before every frame.
"""
from __future__ import annotations

import pygame

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
w_tmr = 0
d_flash = 0
face_dir = 0
start_hp = 100
view_dist = 900
render_scale = 4

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
speedrun_timer = 0
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

# Fog
fog_enabled = False
fog_color = (80, 75, 70)
fog_density = 60

# Cache used by map_utils._player_in_any_sector
player_sector_cache = -1
player_sector_timer = 0