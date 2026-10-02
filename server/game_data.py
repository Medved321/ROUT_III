"""Server-side game data (px space).

Ported from ``client/engine/constants.py`` so the server never imports the
client package.  The server operates in pixel coordinates (TILE = 64), same
as the ROUT_II_III port and renderer.
"""

TILE = 64.0
TICK_RATE = 20
TICK_INTERVAL = 1.0 / TICK_RATE
FPS_60 = 60.0


def fps60_per_tick(px_per_frame: float) -> float:
    """ROUT_II_III speed (px/frame @60fps) -> px/tick."""
    return px_per_frame * FPS_60 / TICK_RATE


# Canonical fire-rate cooldown shared with the client (hitscan change).
from shared.combat import wpn_cooldown_ticks  # noqa: F401  (re-export)

# --- player ---------------------------------------------------------------
PLAYER_RADIUS = 16
PLAYER_MAX_HP = 200
MOVE_SPEED_PX = 250.0   # 3.0 tiles/s * 64  (per-second speed)
# Dash (player dash change): a burst of speed along the current movement input.
# All in server ticks, so the dash needs no network timer -- the world already
# ticks at TICK_RATE. 2.5x for 5 ticks covers ~120 px (~1.9 tiles) in 250 ms.
DASH_SPEED_MULT = 6
DASH_TICKS = 7
DASH_COOLDOWN_TICKS = 15
# Longest step a single can_move_px() call may take. Equals the walking step, so
# ordinary movement is never sub-stepped (and pays no extra collision checks),
# while a dash step is split into pieces small enough not to cross a solid
# sector linedef (linedefs have zero thickness).
MAX_MOVE_STEP_PX = MOVE_SPEED_PX * TICK_INTERVAL
SPEED_FACTOR = PLAYER_RADIUS * 0.01  # reserve, unused for now
START_PISTOL_AMMO = 50
START_SHELLS = 15
START_RIFLE_AMMO = 100
START_BFG_AMMO = 3
START_INITIAL_AMMO = {
    'pstl': 200, 'shl': 40, 'slg': 100, 'rck': 10, 'BFG': 5,
}

AMMO_KEYS = ('pstl', 'shl', 'slg', 'rck', 'BFG')

# --- combat ---------------------------------------------------------------
MELEE_RANGE = 35
PLAYER_MELEE_DMG = 10
MELEE_ANGLE_THRESHOLD = 1.3

# Bullet / projectile (enemy projectiles + player plasma balls are server-side)
BULLET_HIT_RADIUS = 30
ENEMY_PROJ_SPEED = 9.0 * TICK_INTERVAL * FPS_60  # px/frame@60 -> px/tick
BULLET_SPAWN_OFFSET = 20
BALL_SUB_STEP = 4.0

# Player projectile (RIFLE plasma ball): server-authoritative, damage on hit.
PLAYER_PROJ_SPEED = fps60_per_tick(32.0)   # ~12 px/frame@60 -> px/tick
PLAYER_PROJ_MAX_DIST = 900.0
MAX_PLAYER_PROJ = 32

# Impact events (wall/enemy hit tracers) — short-lived, capped per world.
IMPACT_TTL = 12          # ticks (0.6 s at 20 tps)
MAX_IMPACTS = 64

# shots: hitscan is resolved instantly server-side; spread supported
WPN = [
    {'n': 'CHAINSAW', 'd': 10, 'r': 100,   'f': 4,  'a': None, 'c': (180, 0, 0),  'snd': 'chainsaw', 'melee': True},
    {'n': 'PISTOL',   'd': 3,  'r': 900,   'f': 16, 'a': 'pstl', 'c': (50, 50, 55), 'snd': 'pistol'},
    {'n': 'SHOTGUN',  'd': 25, 'r': 720,   'f': 50, 'a': 'shl',  'c': (100, 70, 40), 'snd': 'shotgun', 'pellets': 7, 'cone': 0.15},
    {'n': 'RIFLE',    'd': 4,  'r': 850,   'f': 8,  'a': 'slg',  'c': (100, 0, 0),  'snd': 'rifle', 'proj': True, 'proj_spd': PLAYER_PROJ_SPEED, 'proj_max_dist': PLAYER_PROJ_MAX_DIST},
    {'n': 'BFG',      'd': 50, 'r': 16500,  'f': 30, 'a': 'BFG',  'c': (0, 60, 0),    'snd': 'bfg', 'charge': True},
    {'n': 'SSG',      'd': 5,  'r': 520,   'f': 75, 'a': 'shl',  'c': (80, 50, 25),  'snd': 'ssg', 'cost': 2, 'pellets': 20, 'cone': 0.25},
]

# --- enemies --------------------------------------------------------------
ENEMY_RADIUS = 8
ENEMY_DETECT_RANGE = 500
ENEMY_ALERT_AOE = 300
# infighting: how many ticks a hurt enemy keeps the attacker as its target
INFIGHT_TICKS = 100   # 5 s at 20 tps
ENEMY_SEPARATION_RANGE = 40
SEPARATION_FORCE = 0.8
MELEE_PREFER_DIST = 45
ENEMY_MELEE_COOLDOWN = 500   # ms
ENEMY_RANGED_COOLDOWN = 800  # ms
ENEMY_PROJ_COOLDOWN = 1500   # ms
ENEMY_RANGED_MIN_DIST = 55
DEATH_FRICTION = 0.85
DEATH_SPIN_DECAY = 0.9
RUSH_SPEED_MULT = 1.3
RUSH_DMG_MULT = 2.0
ENEMY_RETREAT_HP = 0.3
STRAFE_FACTOR = 0.6
WANDER_SPEED_FACTOR = 0.4
DIAGONAL_COST = 1.414
CORPSE_LIFETIME = 600       # ticks (30 s at 20 tps)

CORPSE_COLORS = {
    'soldier': (160, 80, 80), 'shotgunguy': (180, 120, 50), 'chaingunner': (130, 130, 140),
    'imp': (180, 130, 80), 'spectre': (60, 80, 70), 'lost_soul': (200, 100, 50),
    'pain_elemental': (140, 60, 180), 'hell_knight': (140, 80, 40), 'revenant': (200, 180, 160),
    'arachnotron': (60, 140, 60), 'archvile': (130, 20, 130), 'cacodemon': (40, 180, 200),
    'mancubus': (110, 20, 20), 'dog': (170, 130, 70), 'pinkie': (235, 100, 170),
    'boss_baron': (220, 40, 40), 'boss_cyber': (70, 70, 75), 'boss_spider': (180, 0, 200),
    'healer': (50, 220, 100), 'artillery': (180, 120, 40),
}

ENEMY_DB = {
    'soldier': {'hp': 15,  'spd': 1.5, 'col': (160, 80, 80),   'name': 'Soldier',     'melee': True,  'dmg': 15},
    'shotgunguy': {'hp': 12,  'spd': 1.2, 'col': (180, 120, 50), 'name': 'ShotgunGuy', 'melee': False, 'dmg': 15},
    'chaingunner': {'hp': 30,  'spd': 1.4, 'col': (130, 130, 140), 'name': 'Chaingunner', 'melee': False, 'dmg': 8},
    'imp': {'hp': 60,  'spd': 1.8, 'col': (180, 130, 80),  'name': 'Imp',         'melee': True,  'dmg': 12},
    'spectre': {'hp': 25,  'spd': 3.0, 'col': (60, 80, 70),   'name': 'Spectre',     'melee': True,  'dmg': 10},
    'lost_soul': {'hp': 20,  'spd': 4.5, 'col': (200, 100, 50), 'name': 'LostSoul',    'melee': True,  'dmg': 8},
    'pain_elemental': {'hp': 100, 'spd': 1.8, 'col': (140, 60, 180), 'name': 'PainElem',  'melee': False, 'dmg': 1},
    'hell_knight': {'hp': 80,  'spd': 1.2, 'col': (140, 80, 40), 'name': 'HellKnight', 'melee': False, 'dmg': 20},
    'revenant': {'hp': 100, 'spd': 1.6, 'col': (200, 180, 160), 'name': 'Revenant', 'melee': True, 'dmg': 15},
    'arachnotron': {'hp': 100, 'spd': 1.2, 'col': (60, 140, 60), 'name': 'Arachnotron', 'melee': False, 'dmg': 10},
    'archvile': {'hp': 40,  'spd': 2.0, 'col': (130, 20, 130), 'name': 'Archvile',   'melee': False, 'dmg': 1},
    'cacodemon': {'hp': 10,  'spd': 5.0, 'col': (40, 180, 200), 'name': 'Cacodemon',  'melee': False, 'dmg': 10},
    'mancubus': {'hp': 60,  'spd': 1.5, 'col': (110, 20, 20), 'name': 'Mancubus',   'melee': False, 'dmg': 15},
    'dog': {'hp': 10,  'spd': 3.6, 'col': (170, 130, 70), 'name': 'Dog',        'melee': True,  'dmg': 5},
    'pinkie': {'hp': 25,  'spd': 3.0, 'col': (235, 100, 170), 'name': 'Pinky',      'melee': True,  'dmg': 10},
    'boss_baron': {'hp': 200, 'spd': 1.2, 'col': (220, 40, 40), 'name': 'Baron',       'melee': False, 'dmg': 25},
    'boss_cyber': {'hp': 400, 'spd': 1.0, 'col': (70, 70, 75), 'name': 'Cyberdemon', 'melee': True,  'dmg': 40},
    'boss_spider': {'hp': 2500, 'spd': 1.8, 'col': (180, 0, 200), 'name': 'Spider',    'melee': False, 'dmg': 20},
    'healer': {'hp': 50,  'spd': 1.5, 'col': (50, 220, 100), 'name': 'Healer',     'melee': False, 'dmg': 5},
    'artillery': {'hp': 60,  'spd': 0.8, 'col': (180, 120, 40), 'name': 'Artillery', 'melee': False, 'dmg': 30},
}

# spawn pool weights for PvE waves (thin stack, room for big boys)
SPAWN_POOL = [
    'soldier', 'soldier', 'shotgunguy', 'shotgunguy', 'chaingunner',
    'imp', 'cacodemon', 'mancubus', 'dog', 'dog', 'pinkie',
    'spectre', 'lost_soul', 'hell_knight', 'revenant', 'arachnotron','healer','archvile','artillery','archvile','archvile','archvile','archvile',
]

# ranged attackers per enemy type -> fire off screen-tracer bullets
RANGED_DB = {
    'shotgunguy': {'proj': True, 'dmg': 15, 'cd': 2000, 'spd': ENEMY_PROJ_SPEED, 'cone': 0.10, 'n': 6},
    'imp':        {'proj': True, 'dmg': 12, 'cd': 1500, 'spd': 7.0 * TICK_INTERVAL * FPS_60, 'cone': 0.06, 'n': 1},
    'hell_knight':{'proj': True, 'dmg': 20, 'cd': 2000, 'spd': 8.0 * TICK_INTERVAL * FPS_60, 'cone': 0.05, 'n': 1},
    'revenant':   {'proj': True, 'dmg': 15, 'cd': 2200, 'spd': 6.0 * TICK_INTERVAL * FPS_60, 'cone': 0.04, 'n': 2},
    'mancubus':   {'proj': True, 'dmg': 15, 'cd': 2000, 'spd': 8.0 * TICK_INTERVAL * FPS_60, 'cone': 0.10, 'n': 6},
    'cacodemon':  {'proj': True, 'dmg': 10, 'cd': 1500, 'spd': 8.0 * TICK_INTERVAL * FPS_60, 'cone': 0.05, 'n': 1},
    'boss_baron': {'proj': True, 'dmg': 25, 'cd': 2500, 'spd': 8.0 * TICK_INTERVAL * FPS_60, 'cone': 0.06, 'n': 2},
    'boss_spider':{'proj': True, 'dmg': 20, 'cd': 900,  'spd': 10.0 * TICK_INTERVAL * FPS_60, 'cone': 0.03, 'n': 1},
}

# map tiles treated as solid by the server (mirror of BLOCK_TILES)
SOLID_TILES = {1, 2, 3, 4, 5, 6, 7, 8, 9,
               '#', 'R', 'Y', 'B', 'T', 'W', 'D', 'M', 'I', 'P', 'H', 'V', 'C', 'Q', 'K'}

START_WPN = 1  # pistol