"""Server-side game data (px space).

Ported from ``client/engine/constants.py`` so the server never imports the
client package.  The server operates in pixel coordinates (TILE = 64), same
as the ROUT_II_III port and renderer.
"""

from shared.combat import (  # noqa: F401  (re-exports for legacy imports)
    DEFAULT_TICK_RATE, FPS_60, MAX_TICK_RATE, MIN_TICK_RATE,
    fps60_per_tick, ms_to_ticks, tick_interval, ticks_for_seconds,
    validate_tick_rate, wpn_cooldown_ticks,
)

TILE = 64.0
# Legacy names stay as the DEFAULT values for imports that do not need runtime
# frequency. The authoritative rate lives on GameServer and is threaded into
# GameWorld; never use these aliases for a live tick conversion.
TICK_RATE = DEFAULT_TICK_RATE
TICK_INTERVAL = tick_interval()

# How many ticks the game loop may run back-to-back inside one wake-up to catch
# up on missed periods (see GameServer._game_loop). Bounded, because draining
# an unbounded backlog in a single wake-up is what turns a stall into a freeze:
# the loop spends all its time replaying the past instead of accepting packets
# and answering the current one.
TICK_DRAIN_MAX = 4

# --- player ---------------------------------------------------------------
PLAYER_RADIUS = 16
PLAYER_MAX_HP = 200
MOVE_SPEED_PX = 250.0   # 3.0 tiles/s * 64  (per-second speed)
# Dash (player dash change): a burst of speed along the current movement input.
# Durations are declared in real seconds and converted to ticks at the running
# rate; the constants below are only the default-rate view for legacy imports.
DASH_SPEED_MULT = 6
DASH_SECONDS = 0.35
DASH_COOLDOWN_SECONDS = 0.75
DASH_TICKS = ticks_for_seconds(DASH_SECONDS)
DASH_COOLDOWN_TICKS = ticks_for_seconds(DASH_COOLDOWN_SECONDS)
# Longest step a single can_move_px() call may take. Equals the walking step, so
# ordinary movement is never sub-stepped (and pays no extra collision checks),
# while a dash step is split into pieces small enough not to cross a solid
# sector linedef (linedefs have zero thickness).
MAX_MOVE_STEP_PX = MOVE_SPEED_PX * TICK_INTERVAL


def dash_ticks(tick_rate: int = DEFAULT_TICK_RATE) -> int:
    return ticks_for_seconds(DASH_SECONDS, tick_rate)


def dash_cooldown_ticks(tick_rate: int = DEFAULT_TICK_RATE) -> int:
    return ticks_for_seconds(DASH_COOLDOWN_SECONDS, tick_rate)


def max_move_step_px(tick_rate: int = DEFAULT_TICK_RATE) -> float:
    # MOVE_SPEED_PX is px per SECOND (3 tiles/s * 64), not px/frame@60.
    return MOVE_SPEED_PX * tick_interval(tick_rate)
SPEED_FACTOR = PLAYER_RADIUS * 0.01  # reserve, unused for now
START_PISTOL_AMMO = 50
START_SHELLS = 15
START_RIFLE_AMMO = 100
START_BFG_AMMO = 3
START_INITIAL_AMMO = {
    'pstl': 200, 'shl': 40, 'slg': 100, 'rck': 10, 'BFG': 5,
}

AMMO_KEYS = ('pstl', 'shl', 'slg', 'rck', 'BFG')
# Carrying reserve per ammo key.  This is the rule (a pickup that would push a
# stack past it stops at it), not a wire detail: WORLD_STATE packs each ammo
# slot as one byte, and an uncapped stack -- the old ``min(999, ...)`` -- grew
# past 255 within a few pickups and took the whole game loop down with
# ``struct.error: 'B' format requires 0 <= number <= 255`` in the broadcast.
# 255 is therefore the largest reserve that reaches the client intact; the
# encoder clamps as a guard, but the cap is enforced where ammo is granted so
# the server and the HUD never disagree about a number.
AMMO_MAX = 255

# --- items -----------------------------------------------------------------
# Pickup radius in px. Half a tile, not the engine's unused ITEM_PICKUP_DIST = 90:
# 90 px is wider than a TILE, so an item would be taken from the far side of a
# corridor the player is only looking down. Player radius is 16 and an item is
# about as wide, so this is "walked over", not "in sight of".
ITEM_PICKUP_DIST = 32.0
# Taken items have no respawn timer any more: they come back only when the wave
# restarts on a player's death (see `GameWorld._reset_items`).
# Armor soaks a hit completely before HP does. Not a third of the damage like
# classic Doom: the HUD shows one integer, so a partial absorb would need a
# second number nobody displays.
ARMOR_MAX = 200
# HASTE multiplies walking speed, BERSERK doubles damage and makes shots free,
# INVULN refuses damage outright. Durations are declared in seconds and become
# ticks at the running rate.
HASTE_SPEED_MULT = 1.5
BERSERK_DMG_MULT = 20
POWERUP_SECONDS = {'BERSERK': 60.0, 'HASTE': 20.0, 'INVULN': 30.0}

# Every placeable item type, keyed by the name the editor palette writes.
# ``wpn_1`` is absent on purpose: the pistol is the starting weapon, so there is
# nothing to pick up. A weapon entry names the WPN index and the ammo it grants;
# a chainsaw (index 0) has no ammo key and grants none.
ITEM_DB = {
    'med':         {'kind': 'health',  'hp': 25},
    'med_small':   {'kind': 'health',  'hp': 10},
    'heart':       {'kind': 'health',  'hp': 100},
    'amm':         {'kind': 'ammo',    'amount': 25},
    'amm_small':   {'kind': 'ammo',    'amount': 10},
    'armor_green': {'kind': 'armor',   'points': 100, 'armor_type': 'green'},
    'armor_blue':  {'kind': 'armor',   'points': 200, 'armor_type': 'blue'},
    'berserk':     {'kind': 'powerup', 'pu': 'BERSERK',
                     'ticks': ticks_for_seconds(POWERUP_SECONDS['BERSERK'])},
    'haste':       {'kind': 'powerup', 'pu': 'HASTE',
                     'ticks': ticks_for_seconds(POWERUP_SECONDS['HASTE'])},
    'invuln':      {'kind': 'powerup', 'pu': 'INVULN',
                     'ticks': ticks_for_seconds(POWERUP_SECONDS['INVULN'])},
    'key_red':     {'kind': 'key', 'lock': 'red'},
    'key_yellow':  {'kind': 'key', 'lock': 'yellow'},
    'key_blue':    {'kind': 'key', 'lock': 'blue'},
    'wpn_0':       {'kind': 'weapon',  'wpn': 0, 'ammo': 0},
    'wpn_2':       {'kind': 'weapon',  'wpn': 2, 'ammo': 10},
    'wpn_3':       {'kind': 'weapon',  'wpn': 3, 'ammo': 30},
    'wpn_4':       {'kind': 'weapon',  'wpn': 4, 'ammo': 3},
    'wpn_5':       {'kind': 'weapon',  'wpn': 5, 'ammo': 10},
}


def item_spec(itype: str, tick_rate: int = DEFAULT_TICK_RATE) -> dict | None:
    """Item description with powerup durations converted to runtime ticks."""
    spec = ITEM_DB.get(itype)
    if spec is None:
        return None
    seconds = POWERUP_SECONDS.get(spec.get('pu'))
    if seconds is None:
        return spec
    out = dict(spec)
    out['ticks'] = ticks_for_seconds(seconds, tick_rate)
    return out

# --- doors -----------------------------------------------------------------
# A door is a two-sided linedef whose ``special`` marks it (SPECIAL_DOORS) and
# whose middle texture names its lock.  The runtime state (phase / how far open /
# hold timer) lives on GameWorld, never on the immutable map data (design D2).
# A door blocks movement, sight, hitscans and projectiles until it is fully open.
DOOR_OPEN_SECONDS = 1.0
DOOR_HOLD_SECONDS = 5.0
DOOR_CLOSE_SECONDS = 1.0
DOOR_OPEN_TICKS = ticks_for_seconds(DOOR_OPEN_SECONDS)
DOOR_HOLD_TICKS = ticks_for_seconds(DOOR_HOLD_SECONDS)
DOOR_CLOSE_TICKS = ticks_for_seconds(DOOR_CLOSE_SECONDS)
DOOR_USE_DIST = 64.0     # one tile: how far ahead "use" reaches for a door


def door_open_ticks(tick_rate: int = DEFAULT_TICK_RATE) -> int:
    return ticks_for_seconds(DOOR_OPEN_SECONDS, tick_rate)


def door_hold_ticks(tick_rate: int = DEFAULT_TICK_RATE) -> int:
    return ticks_for_seconds(DOOR_HOLD_SECONDS, tick_rate)


def door_close_ticks(tick_rate: int = DEFAULT_TICK_RATE) -> int:
    return ticks_for_seconds(DOOR_CLOSE_SECONDS, tick_rate)

# --- combat ---------------------------------------------------------------
MELEE_RANGE = 35
PLAYER_MELEE_DMG = 10
MELEE_ANGLE_THRESHOLD = 1.3

# Bullet / projectile (enemy projectiles + player plasma balls are server-side)
BULLET_HIT_RADIUS = 30
ENEMY_PROJ_FRAME_SPEED = 9.0  # px/frame@60 -> px/tick at the running rate
ENEMY_PROJ_SPEED = fps60_per_tick(ENEMY_PROJ_FRAME_SPEED)
BULLET_SPAWN_OFFSET = 20
BALL_SUB_STEP = 4.0

# Player projectile (RIFLE plasma ball): server-authoritative, damage on hit.
PLAYER_PROJ_FRAME_SPEED = 32.0
PLAYER_PROJ_SPEED = fps60_per_tick(PLAYER_PROJ_FRAME_SPEED)


def enemy_proj_speed(tick_rate: int = DEFAULT_TICK_RATE) -> float:
    return fps60_per_tick(ENEMY_PROJ_FRAME_SPEED, tick_rate)


def player_proj_speed(tick_rate: int = DEFAULT_TICK_RATE) -> float:
    return fps60_per_tick(PLAYER_PROJ_FRAME_SPEED, tick_rate)
PLAYER_PROJ_MAX_DIST = 900.0
MAX_PLAYER_PROJ = 32
# Enemy projectiles had no budget: on an enemy-dense map every shooter could add
# its volley each cooldown, so the world grew with the wave instead of with the
# live shots. Capped like player projectiles, with the oldest dropped first.
MAX_ENEMY_PROJ = 48

# Impact events (wall/enemy hit tracers) — short-lived, capped per world.
IMPACT_SECONDS = 0.6
IMPACT_TTL = ticks_for_seconds(IMPACT_SECONDS)
MAX_IMPACTS = 64


def impact_ttl(tick_rate: int = DEFAULT_TICK_RATE) -> int:
    return ticks_for_seconds(IMPACT_SECONDS, tick_rate)

# shots: hitscan is resolved instantly server-side; spread supported
WPN = [
    {'n': 'CHAINSAW', 'd': 10, 'r': 100,   'f': 4,  'a': None, 'c': (180, 0, 0),  'snd': 'chainsaw', 'melee': True},
    {'n': 'PISTOL',   'd': 3,  'r': 900,   'f': 16, 'a': 'pstl', 'c': (50, 50, 55), 'snd': 'pistol'},
    {'n': 'SHOTGUN',  'd': 25, 'r': 720,   'f': 50, 'a': 'shl',  'c': (100, 70, 40), 'snd': 'shotgun', 'pellets': 7, 'cone': 0.15},
    {'n': 'RIFLE',    'd': 4,  'r': 850,   'f': 8,  'a': 'slg',  'c': (100, 0, 0),  'snd': 'rifle', 'proj': True, 'proj_spd': PLAYER_PROJ_SPEED, 'proj_max_dist': PLAYER_PROJ_MAX_DIST},
    {'n': 'BFG',      'd': 5000, 'r': 16500,  'f': 30, 'a': 'BFG',  'c': (0, 60, 0),    'snd': 'bfg', 'charge': True},
    {'n': 'SSG',      'd': 25,  'r': 720,   'f': 50, 'a': 'shl',  'c': (80, 50, 25),  'snd': 'ssg', 'cost': 2, 'pellets': 14, 'cone': 0.15},
]

# --- enemies --------------------------------------------------------------
ENEMY_RADIUS = 8
ENEMY_DETECT_RANGE = 500
ENEMY_ALERT_AOE = 300
# infighting: how many ticks a hurt enemy keeps the attacker as its target
INFIGHT_TICKS = 99999   # ~83 min at 20 tps: effectively "until it dies"
INFIGHT_SECONDS = INFIGHT_TICKS / TICK_RATE


def infight_ticks(tick_rate: int = DEFAULT_TICK_RATE) -> int:
    return ticks_for_seconds(INFIGHT_SECONDS, tick_rate)
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
CORPSE_LIFETIME = 6000       # ticks (5 min at 20 tps)
CORPSE_LIFETIME_SECONDS = 300.0


def corpse_lifetime(tick_rate: int = DEFAULT_TICK_RATE) -> int:
    return ticks_for_seconds(CORPSE_LIFETIME_SECONDS, tick_rate)
# --- match end ------------------------------------------------------------
# How close a live player must get to the map's `exit` thing to finish the
# level.  Three quarters of a tile: reaching the marker, not merely its cell,
# so the win is a deliberate walk into the goal.
EXIT_RADIUS = 48.0
# PvP time limit, in ticks. When it hits zero the match ends and the player
# with the most frags wins (a tie is a draw); `time_left` is published in
# seconds. A dead player has no timer of its own any more: respawn is a client
# request (_respawn_player), not a countdown (match-end design D4).
PVP_MATCH_SECONDS = 180.0   # 3 minutes
PVP_MATCH_TICKS = ticks_for_seconds(PVP_MATCH_SECONDS)


def pvp_match_ticks(tick_rate: int = DEFAULT_TICK_RATE) -> int:
    return ticks_for_seconds(PVP_MATCH_SECONDS, tick_rate)

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

# ranged attackers per enemy type -> fire off screen-tracer bullets.
# ``spd_frames`` is the authored px/frame@60 speed; runtime speed is derived
# from it with the active tick rate, so the default table stays intact.
RANGED_DB = {
    'shotgunguy': {'proj': True, 'dmg': 15, 'cd': 2000, 'spd_frames': 9.0,  'spd': ENEMY_PROJ_SPEED, 'cone': 0.10, 'n': 6},
    'imp':        {'proj': True, 'dmg': 12, 'cd': 1500, 'spd_frames': 7.0,  'spd': fps60_per_tick(7.0), 'cone': 0.06, 'n': 1},
    'hell_knight':{'proj': True, 'dmg': 20, 'cd': 2000, 'spd_frames': 8.0,  'spd': fps60_per_tick(8.0), 'cone': 0.05, 'n': 1},
    'revenant':   {'proj': True, 'dmg': 15, 'cd': 2200, 'spd_frames': 6.0,  'spd': fps60_per_tick(6.0), 'cone': 0.04, 'n': 2},
    'mancubus':   {'proj': True, 'dmg': 15, 'cd': 2000, 'spd_frames': 8.0,  'spd': fps60_per_tick(8.0), 'cone': 0.10, 'n': 6},
    'cacodemon':  {'proj': True, 'dmg': 10, 'cd': 1500, 'spd_frames': 8.0,  'spd': fps60_per_tick(8.0), 'cone': 0.05, 'n': 1},
    'boss_baron': {'proj': True, 'dmg': 25, 'cd': 2500, 'spd_frames': 8.0,  'spd': fps60_per_tick(8.0), 'cone': 0.06, 'n': 2},
    'boss_spider':{'proj': True, 'dmg': 20, 'cd': 900,  'spd_frames': 10.0, 'spd': fps60_per_tick(10.0), 'cone': 0.03, 'n': 1},
}


def ranged_db(tick_rate: int = DEFAULT_TICK_RATE) -> dict:
    """Ranged enemy table with projectile speeds and cooldowns at ``tick_rate``."""
    rate = validate_tick_rate(tick_rate)
    out = {}
    for etype, spec in RANGED_DB.items():
        item = dict(spec)
        item['spd'] = fps60_per_tick(spec['spd_frames'], rate)
        item['cd_ticks'] = ms_to_ticks(spec['cd'], rate)
        out[etype] = item
    return out

# map tiles treated as solid by the server (mirror of BLOCK_TILES)
SOLID_TILES = {1, 2, 3, 4, 5, 6, 7, 8, 9,
               '#', 'R', 'Y', 'B', 'T', 'W', 'D', 'M', 'I', 'P', 'H', 'V', 'C', 'Q', 'K'}

START_WPN = 1  # pistol