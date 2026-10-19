import math

W, H, FPS = 1000, 700, 60
SCALE, TILE = 4, 64
FOV, SENS = math.pi / 3, 0.0015
SAVE_DIR = "saves/"
MAP_DIR = "maps/"
NUM_SLOTS = 3

# Camera eye height (world units).  Tile-mode walls/sprites are shifted so the
# horizon sits at this height instead of the old wall-centre (TILE/2 = 32).
CAM_Z = 41.0

TEX_W = 64
TILE_HALF = TILE // 2
BLOCK_TILES = {'#', 'R', 'Y', 'B', 'T', 'W', 'D', 'M', 'I', 'P', 'H', 'V', 'C', 'Q', 'K'}
DM_SPAWN_CHARS = {'1', '2'}

# Collision / interaction radii
PLAYER_RADIUS = 16
ENEMY_RADIUS = 8
BOT_RADIUS = 10
BULLET_HIT_RADIUS = 30
# Engine-side pickup geometry. These are display heights only: the pickup radius
# and the respawn timer live on the server (server/game_data.py), which is the
# side that knows whether an item was actually taken.
#
# ITEM_FLOAT_HEIGHT is a *ratio* of the item's own projected height, not a pixel
# count, so a pickup floats the same visual amount at any distance and at any
# window size -- the alternative (a fixed pixel offset) lifts a far medkit out of
# proportion and makes it drift as the window is resized.
ITEM_FLOAT_HEIGHT = 0.15
# Every placeable item type must appear here. A missing key is not a harmless
# omission: the renderer falls back to a default height, so a weapon pickup would
# silently come out the size of whatever that default happens to be.
ITEM_GAME_HEIGHTS = {
    'med': 28, 'med_small': 20, 'amm': 24, 'amm_small': 18,
    'armor_green': 28, 'armor_blue': 28, 'berserk': 24,
    'haste': 24, 'invuln': 24, 'heart': 24,
    'wpn_0': 30, 'wpn_2': 30, 'wpn_3': 30, 'wpn_4': 30, 'wpn_5': 30,
    'key_red': 18, 'key_yellow': 18, 'key_blue': 18,
}
MAX_PARTICLES = 200
MAX_DECALS = 100
MAX_WEATHER = 300
MELEE_RANGE = 35

# Adaptive quality (frame budget). ``render_scale`` in the config is the best
# (starting) quality; the client may raise it up to this bound under load.
MAX_ADAPTIVE_SCALE = 8
# Sector-mode vertical resolution scale: I_H = viewport_h // vs_scale.  The
# adaptive controller may raise it under load and restore on headroom (D9).
MAX_ADAPTIVE_VS = 6
# Fixed network-input rate (independent of the render frame rate).
INPUT_SEND_INTERVAL = 1.0 / 25.0
# Re-evaluate decal wall occlusion at most once per this many drawn frames.
DECAL_OCCLUSION_INTERVAL = 6

# Timing constants
DM_INVULN_TICKS = 120
POWERUP_DURATION = 600
DAMAGE_FLASH = 5
STUN_FRAMES = 8
LAVA_DMG_INTERVAL = 30
LAVA_DMG_AMOUNT = 5
ELEVATOR_FADE_TIME = 30

# Health / damage
PLAYER_MAX_HP = 200
MED_SMALL_HEAL = 10
DEFAULT_BULLET_DMG = 15

# Melee combat
MELEE_ATTACKS = {
    'jab':       {'dmg': 5,  'range': 45, 'spd': 6,  'hand': 'left',  'snd': 'punch'},
    'cross':     {'dmg': 8,  'range': 50, 'spd': 10, 'hand': 'right', 'snd': 'punch'},
    'hook':      {'dmg': 12, 'range': 55, 'spd': 14, 'hand': 'any',   'snd': 'punch'},
    'uppercut':  {'dmg': 10, 'range': 40, 'spd': 12, 'hand': 'any',   'snd': 'swoosh'},
    'front_kick':{'dmg': 10, 'range': 60, 'spd': 14, 'hand': None,    'snd': 'kick'},
    'side_kick': {'dmg': 8,  'range': 55, 'spd': 12, 'hand': None,    'snd': 'kick'},
    'roundhouse':{'dmg': 15, 'range': 65, 'spd': 18, 'hand': None,    'snd': 'kick'},
}
HANDS_LEFT  = ['jab', 'hook', 'uppercut']
HANDS_RIGHT = ['cross', 'hook', 'uppercut']
KICKS       = ['front_kick', 'side_kick', 'roundhouse']

# Weapon specifics
SG_PELLETS = 7
SSG_PELLETS = 20
SG_SPREAD = 0.15
SSG_SPREAD = 0.15
RIFFLE_BULLET_SPEED = 20

# Ammo pickup amounts
AMMO_SMALL = {'pstl': 4, 'shl': 1, 'slg': 5, 'rck': 1}
AMMO_BIG = {'pstl': 6, 'shl': 3, 'slg': 10, 'rck': 3}

# Enemy alert range
ENEMY_ALERT_RANGE = 350

# Explosion defaults
BARREL_HP = 60
BARREL_EXPLODE_DMG = 30
BARREL_EXPLODE_RANGE = 60
DWALL_HP = 60

# Rendering
VIEW_DIST_DEFAULT = 900
FOG_DENSITY_DEFAULT = 60

# Item / projectile rendering
PROJECTILE_SIZE_MIN = 30
PROJECTILE_SIZE_MAX = 400
PROJECTILE_SIZE_FACTOR = 0.9
ITEM_WPN_MIN_H = 3
ITEM_ATT_MIN_SZ = 6

# Bot
BOT_COUNT_DEFAULT = 4

# Deathmatch
KILL_LOG_MAX = 5

# Sector mode
SECTOR_HEIGHT = 128
DOOR_OPEN_HEIGHT = 100
EYE_H = 24
BARREL_OFFSET = 4
HEADSHOT_Z = TILE * 0.25
PLAYER_HEIGHT = 38
MAX_STEP_UP = 16
MAX_STEP_DOWN = 999

# Physics
GRAVITY = 1.5
JUMP_FORCE = -12
MAX_FALL = 15
PITCH_CLAMP = 0.5
RAISED_FLOOR_HEIGHT = 24.0
LOWERED_FLOOR_HEIGHT = -32.0

# Combat
HEADSHOT_DMG_MULT = 2
HEADSHOT_SKULL_DURATION = 30
EXPLOSION_DMG_TO_PLAYER = 0.5
BARREL_CHAIN_DMG_MULT = 0.7
BARREL_CHAIN_RADIUS_OFFSET = 6
ARMOR_GREEN_ABSORB = 0.3
ARMOR_BLUE_ABSORB = 0.8
GRIP_SPREAD_MULT = 0.6

# Hit angle thresholds
MELEE_ANGLE_THRESHOLD = 0.6
HITSCAN_ANGLE_THRESHOLD = 0.3
MANCUBUS_HIT_ANGLE = 0.4
CUSTOM_WPN_HIT_ANGLE_PADDING = 0.2

# Bullet / projectile
BULLET_MAX_DIST = 800
PROJECTILE_DEFAULT_MAX_DIST = 800
BULLET_SPAWN_OFFSET = 20
SSG_EXECUTION_RANGE = 120
SMOKE_TRAIL_CHANCE = 0.4

# Enemy behavior
DIAGONAL_COST = 1.414
DEATH_FRICTION = 0.85
DEATH_SPIN_DECAY = 0.9
ENEMY_SEPARATION_RANGE = 40
SEPARATION_FORCE = 0.8
PLAYER_PUSH_RANGE = 32
PLAYER_PUSH_FORCE = 6.0
ENEMY_RETREAT_HP = 0.3
RANGED_PREFER_DIST = 200
MELEE_PREFER_DIST = 45
RUSH_SPEED_MULT = 1.3
STRAFE_FACTOR = 0.6
PATH_SPEED_FACTOR = 0.8
WANDER_SPEED_FACTOR = 0.4
PATROL_SPEED_FACTOR = 0.5
SCATTER_SPEED_MULT = 1.2
RUSH_DMG_MULT = 2.0
SPIDER_SPAWN_THRESHOLD = 0.5
ENEMY_DETECT_RANGE = 500
ENEMY_ALERT_AOE = 300

# Enemy projectile speeds
ENEMY_PROJ_SPEED = 9
IMP_PROJ_SPEED = 7
HELL_KNIGHT_PROJ_SPEED = 8
REVENANT_PROJ_SPEED = 6
ARACHNOTRON_PROJ_SPEED = 12
CYBER_PROJ_SPEED = 8
SPIDER_PROJ_SPEED = 10

# Enemy ranged attack distances
ENEMY_RANGED_MIN_DIST = 55
ENEMY_IMP_RANGE = 400
ENEMY_HK_RANGE = 500
ENEMY_REV_RANGE = 600
ENEMY_CHAINGUNNER_RANGE = 600
ENEMY_ARACH_RANGE = 500
ENEMY_CYBER_RANGE = 700
ENEMY_SPIDER_RANGE = 600
BOSS_BARON_RANGE = 650

# Enemy attack cooldowns
ENEMY_MELEE_COOLDOWN = 500
ENEMY_RANGED_COOLDOWN = 800
ENEMY_PROJ_COOLDOWN = 1500
ENEMY_SHOTGUN_COOLDOWN = 2000
ENEMY_CHAINGUN_COOLDOWN = 400
ENEMY_ARACH_COOLDOWN = 600
ENEMY_CYBER_COOLDOWN = 3000
ENEMY_SPIDER_COOLDOWN = 900
BOSS_BARON_COOLDOWN = 2500
IMP_COOLDOWN = 1500
HK_COOLDOWN = 2000
REV_COOLDOWN = 2200
SPIDER_SPAWN_COOLDOWN = 6000
PAIN_ELEM_COOLDOWN = 4000
ARCHVILE_COOLDOWN = 5000

# Particles
PARTICLE_SIZE_MIN = 1.5
PARTICLE_SIZE_MAX = 3.5
PARTICLE_FRICTION = 0.95
PARTICLE_GRAVITY = 0.3
SMOKE_FLOAT = 0.02
SMOKE_DRAG = 0.97
SPARK_SPEED_MIN_FACTOR = 0.3
DECAL_LIFETIME = 1800
SCORCH_LIFETIME = 1800
BLOOD_LIFETIME = 1800

# Effect surface budget (effect-surface-budget).  A projected effect radius is
# unbounded in principle -- an effect that ends up inside the camera projects to
# tens of thousands of pixels -- so anything that *allocates* a surface for an
# effect has to be told where to stop.
# Near plane for effect projection, world units.  Dash dust is spawned within
# ~5 units of the player, so the plane has to stay above that or the dust stops
# being visible.  It is applied by the projection itself (viewport.project_point,
# sector_render.project_object) and only to the *scale*: the floor line and the
# depth test keep the true distance, so an effect at your feet stays on its own
# floor.
EFFECT_NEAR_DIST = 8.0
# Largest radius (px) that may be cached as a blend surface.  256 -> 512x512,
# i.e. 1 MiB per surface.  Anything bigger is drawn as a clipped primitive.
CIRCLE_MAX_R = 256
# Radius ladder step: neighbouring keys differ by 1.25, so a growing puff
# changes size by at most ~11% and the cache stops missing every frame.
CIRCLE_RADIUS_STEP = 1.25
# Pixel budget per circle cache (smoke, scorch).  A count cap alone allowed 64
# multi-hundred-MB surfaces; this is the real memory bound.
CIRCLE_CACHE_PX = 1 << 20
# Cap for the degraded (no model provider) enemy/bot stand-in surfaces, which
# are otherwise sized straight from the projection.
FALLBACK_SPRITE_MAX = 1024

# Dash feedback (movement cue, deliberately independent of the gore amount)
DASH_FX_BURST = 9            # puffs thrown out when a dash starts
DASH_FX_TRAIL = 2            # puffs per trail emission while dashing
DASH_FX_TRAIL_PX = 16.0      # distance covered between trail emissions
DASH_FX_COLOR = (120, 200, 255)   # own dash world dust (remote players use shirt color)
DASH_FX_STREAK_RGB = (255, 255, 255)   # speed wedges: white, like every other game's
DASH_FX_FADE = 0.055         # per-frame decay of the radial-ray envelope
# The cue is a full circle of white wedges, not four bars on the screen axes, so
# it reads the same wherever the player is looking and never as a "+" glued to
# the edges of the view. Rings are listed one after another, which is what makes
# any prefix of the table a complete circle (see rendering._ray_table).
DASH_FX_STREAKS = 96         # white speed wedges at full quality (3 rings x 32)
DASH_FX_STREAKS_MIN = 32     # one whole ring: the cue is the only dash signal
DASH_FX_STREAK_RING = 32     # wedges per ring
DASH_FX_STREAK_FLOW = 0.11   # share of a wedge's travel per frame at a full envelope
DASH_FX_STREAK_DEADZONE = 0.18   # empty fraction of the short side around the aim point
DASH_FX_STREAK_WIDTH = 4.0   # layout units across a wedge at its wide (outer) end
# Length is a layout unit too, but it is deliberately only a few times the width:
# a turned sprite is blitted as its axis-aligned bounding box, and for a long thin
# triangle that box is a couple of dozen times the ink in it (measured: a 4K field
# of full-radius wedges covered 87 % of the frame in boxes to paint 3.6 % of it,
# 13 ms a frame). Short streaks also just read better as speed.
DASH_FX_STREAK_LEN = 26.0    # layout units from the apex to the base

# Rendering
MIN_SHADE = 0.15
VIEW_DIST_MULT = 1.2
GLOW_SPEED = 0.005
BOB_SPEED = 0.004
SPECTRE_VIS_SPEED = 0.01
EYE_Z_OFFSET = 41
SCOPE_ZOOM_FACTOR = 0.5

# Melee arm rendering
FIST_RADIUS = 60
FOREARM_WIDTH = 48
LEFT_ARM_BASE_X = 60
RIGHT_ARM_BASE_X_OFFSET = 60
MELEE_BASE_Y_OFFSET = 100
HOOK_ARC_RANGE = 1.6
HOOK_ARC_START_LEFT = -0.8

# Weapon sprite scales
PISTOL_SPRITE_SCALE = 3.0
RIFLE_SPRITE_SCALE = 3.0
CHAINSAW_SPRITE_SCALE = 3.0
VOXEL_WPN_SCALE = 6

# Corpse friction
CORPSE_FRICTION = 0.85

# Bot
BOT_AIM_ERROR_BASE = 0.08
BOT_ARRIVE_WEIGHT = 0.6
BOT_SEPARATE_WEIGHT = 0.4
BOT_MAX_APPROACH = 0.8
BOT_MAX_RETREAT = 0.6
BOT_STRAFE_FACTOR = 0.5

# Slaughter / Generation
SLAUGHTER_BASE_ENEMIES = 8
SLAUGHTER_ENEMIES_PER_ROUND = 3
SLAUGHTER_ENEMIES_PER_DIFF = 5
PORTAL_WAVE_DELAY = 2000
SLAUGHTER_MAP_SIZE = 34
SLAUGHTER_COLUMNS_PER_DIFF = 20
SLAUGHTER_BASE_POWERUPS = 10
SLAUGHTER_POWERUPS_PER_DIFF = 5
SLAUGHTER_PISTOL_AMMO = 50
SLAUGHTER_SHELLS = 15
SLAUGHTER_RIFLE_AMMO = 100
SLAUGHTER_BFG_AMMO = 3
DM_SIZE_BONUS = 6
DEFAULT_SIZE_BONUS = 5
DM_WIDEN_INTERVAL = 1
ROOM_CARVE_CHANCE = 0.6
POOL_ITEM_CHANCE = 0.4
ARENA_RING_WIDTH = 1.5
PILLAR_CHANCE = 0.5
BASE_ENEMIES = 10
ENEMIES_PER_LEVEL = 3

# Menu settings values
WINDOW_SCALE_VALS = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3, 3.5, 4, 4.5, 5]
FPS_CAP_VALS = [30, 60, 120, 144, 0]
FOV_VALS = [60, 70, 80, 90, 100, 110, 120]
VIEW_DIST_VALS = [100, 200, 300, 400, 500, 600, 800, 1000, 1500]
RAY_STEP_VALS = [1, 2, 3, 4, 6, 8, 12, 16, 24, 28]
RAY_LIGHT_VALS = [1, 2, 3, 4, 6, 8, 12, 16, 20]
BLOOD_AMOUNT_VALS = [0, 0.5, 1.0, 1.5, 2.0]
ENEMY_DENSITY_STEP = 0.5
ENEMY_DENSITY_MAX = 2.0
SLAUGHTER_DMG_STEP = 0.5
SLAUGHTER_DMG_MAX = 5

MAP_SIZE_SETTING = 50
ENEMY_DENSITY = 1.0
DIFF_MOD = 1.0
HUD_COLOR = (255, 255, 255)
PERK = "NONE"
PLAYER_SPD = 5.5
START_HP = 200

WPN = [
    {'n': 'CHAINSAW', 'd': 10, 'r': 100, 'f': 4, 'a': None, 'c': (180, 0, 0), 'snd': 'chainsaw'},
    {'n': 'PISTOL', 'd': 3, 'r': 900, 'f': 16, 'a': 'pstl', 'c': (50, 50, 55), 'snd': 'pistol'},
    {'n': 'SHOTGUN', 'd': 15, 'r': 720, 'f': 50, 'a': 'shl', 'c': (100, 70, 40), 'snd': 'shotgun'},
    {'n': 'RIFLE', 'd': 4, 'r': 850, 'f': 8, 'a': 'slg', 'c': (40, 60, 40), 'snd': 'rifle'},
    {'n': 'BFG', 'd': 5000, 'r': 1650, 'f': 30, 'a': 'BFG', 'c': (0, 60, 0), 'snd': 'bfg'},
    {'n': 'SSG', 'd': 5, 'r': 520, 'f': 75, 'a': 'shl', 'c': (80, 50, 25), 'snd': 'ssg', 'cost': 2},
]

ENEMY_DB = {
    'soldier': {'hp': 15, 'spd': 1.5, 'col': (160, 80, 80), 'name': 'Soldier', 'melee': True, 'dmg': 15},
    'shotgunguy': {'hp': 12, 'spd': 1.2, 'col': (180, 120, 50), 'name': 'ShotgunGuy', 'melee': False, 'dmg': 15},
    'chaingunner': {'hp': 30, 'spd': 1.4, 'col': (130, 130, 140), 'name': 'Chaingunner', 'melee': False, 'dmg': 8},
    'imp': {'hp': 60, 'spd': 1.8, 'col': (180, 130, 80), 'name': 'Imp', 'melee': True, 'dmg': 12},
    'spectre': {'hp': 25, 'spd': 3.0, 'col': (60, 80, 70), 'name': 'Spectre', 'melee': True, 'dmg': 10},
    'lost_soul': {'hp': 20, 'spd': 4.5, 'col': (200, 100, 50), 'name': 'LostSoul', 'melee': True, 'dmg': 8},
    'pain_elemental': {'hp': 100, 'spd': 1.8, 'col': (140, 60, 180), 'name': 'PainElem', 'melee': False, 'dmg': 1},
    'hell_knight': {'hp': 80, 'spd': 1.2, 'col': (140, 80, 40), 'name': 'HellKnight', 'melee': False, 'dmg': 20},
    'revenant': {'hp': 100, 'spd': 1.6, 'col': (200, 180, 160), 'name': 'Revenant', 'melee': True, 'dmg': 15},
    'arachnotron': {'hp': 100, 'spd': 1.2, 'col': (60, 140, 60), 'name': 'Arachnotron', 'melee': False, 'dmg': 10},
    'archvile': {'hp': 40, 'spd': 2.0, 'col': (130, 20, 130), 'name': 'Archvile', 'melee': False, 'dmg': 1},
    'cacodemon': {'hp': 10, 'spd': 5.0, 'col': (40, 180, 200), 'name': 'Cacodemon', 'melee': False, 'dmg': 10},
    'mancubus': {'hp': 60, 'spd': 1.5, 'col': (110, 20, 20), 'name': 'Mancubus', 'melee': False, 'dmg': 15},
    'dog': {'hp': 10, 'spd': 3.6, 'col': (170, 130, 70), 'name': 'Dog', 'melee': True, 'dmg': 5},
    'pinkie': {'hp': 25, 'spd': 3.0, 'col': (235, 100, 170), 'name': 'Pinky', 'melee': True, 'dmg': 10},
    'boss_baron': {'hp': 200, 'spd': 1.2, 'col': (220, 40, 40), 'name': 'Baron', 'melee': False, 'dmg': 25},
    'boss_cyber': {'hp': 400, 'spd': 1.0, 'col': (70, 70, 75), 'name': 'Cyberdemon', 'melee': True, 'dmg': 40},
    'boss_spider': {'hp': 2500, 'spd': 1.8, 'col': (180, 0, 200), 'name': 'Spider', 'melee': False, 'dmg': 20},
    'healer': {'hp': 50, 'spd': 1.5, 'col': (50, 220, 100), 'name': 'Healer', 'melee': False, 'dmg': 5},
    'artillery': {'hp': 60, 'spd': 0.8, 'col': (180, 120, 40), 'name': 'Artillery', 'melee': False, 'dmg': 30},
}

ENEMY_GAME_HEIGHTS = {
    'soldier': 56, 'shotgunguy': 56, 'chaingunner': 56,
    'imp': 50, 'pinkie': 45, 'spectre': 50,
    'dog': 38, 'lost_soul': 38, 'cacodemon': 45,
    'pain_elemental': 45, 'hell_knight': 64, 'revenant': 56,
    'arachnotron': 45, 'mancubus': 56, 'archvile': 64,
    'boss_baron': 64, 'boss_cyber': 64, 'boss_spider': 64,
    'healer': 50, 'artillery': 56,
}

SPAWN_POOL_EXIT = ['soldier', 'shotgunguy', 'chaingunner', 'imp', 'archvile', 'cacodemon', 'mancubus', 'dog', 'pinkie', 'healer', 'artillery',
                   'shotgunguy', 'shotgunguy', 'shotgunguy', 'shotgunguy']
SPAWN_POOL_ENDLESS = ['soldier', 'shotgunguy', 'chaingunner', 'imp', 'cacodemon', 'mancubus', 'dog', 'pinkie', 'healer', 'artillery',
                      'shotgunguy', 'shotgunguy', 'dog', 'dog', 'imp', 'imp']

SPAWN_POOL_SLAUGHTER = ['soldier', 'shotgunguy', 'chaingunner', 'imp', 'cacodemon', 'mancubus', 'dog', 'pinkie',
                        'soldier', 'shotgunguy', 'cacodemon', 'dog', 'pinkie',
                        'imp', 'imp', 'hell_knight', 'revenant', 'spectre', 'lost_soul',
                        'boss_baron', 'boss_cyber', 'boss_spider']

MAT_NAMES = {'#': 'BRICK', 'T': 'STONE', 'W': 'WOOD', 'D': 'DOOR', 'M': 'MEAT', 'I': 'RSTONE', 'P': 'MARTYR',
             'X': 'BARREL', 'H': 'HWALL', 'L': 'LAVA', 'V': 'SWITCH', 'C': 'BRIDGE', 'Q': 'PLATFORM', 'K': 'PENTA'}
MAP_ENEMY_CHARS = {'1': 'soldier', '2': 'shotgunguy', '3': 'archvile', '4': 'cacodemon', '5': 'mancubus', '6': 'dog', '7': 'pinkie',
                   '8': 'boss_baron', '9': 'boss_cyber', '0': 'boss_spider',
                   'c': 'chaingunner', 'g': 'imp', 'v': 'revenant', 'p': 'pain_elemental',
                   'l': 'lost_soul', 'n': 'hell_knight', 'x': 'arachnotron', 'z': 'spectre'}
ENEMY_CHAR_NAMES = {'1': 'SOL', '2': 'SHOT', '3': 'ARCH', '4': 'CACO', '5': 'MANC', '6': 'DOG', '7': 'PINK',
                    '8': 'BARON', '9': 'CYBER', '0': 'SPIDER',
                    'c': 'CHAIN', 'g': 'IMP', 'v': 'REV', 'p': 'PAIN',
                    'l': 'SOUL', 'n': 'KNIGHT', 'x': 'ARACH', 'z': 'SPEC'}
ITEM_CHARS = {'m': 'med', 'a': 'amm', 'k': 'berserk', 'h': 'haste', 'i': 'invuln',
              'w': 'wpn_0', 'u': 'wpn_2', 't': 'wpn_3', 'j': 'wpn_4', 'o': 'wpn_5', 'd': 'heart',
              'e': 'armor_green', 'f': 'armor_blue',
               'A': 'att_scope', 'N': 'att_silencer', 'G': 'att_grip'}

ATTACHMENTS = {
    'scope': {'name': 'Scope', 'desc': 'ПКМ: зум + прицел, мeдленнее',
              'fire_rate_mult': 1.5, 'color': (100, 150, 255),
              'speed_mult': 0.4},
    'silencer': {'name': 'Silencer', 'desc': 'ПКМ: тихий, -30% урона, без трассеров',
                 'fire_rate_mult': 1.3, 'color': (80, 80, 80),
                 'dmg_mult': 0.7, 'alert_suppress': True, 'hide_trail': True},
    'grip': {'name': 'Tactical Grip', 'desc': 'ПКМ: кучность ++',
             'fire_rate_mult': 1.05, 'color': (200, 150, 50)},
}
MAT_ORDER = ['#', 'T', 'W', 'M', 'I', 'P', 'X', 'H', 'L', 'V', 'C', 'Q', 'K']
MAT_COLORS = {'#': (100, 100, 100), 'T': (130, 130, 140), 'W': (120, 80, 40),
              '.': (40, 40, 45), 'E': (60, 180, 220), 'F': (200, 150, 0),
              'R': (180, 40, 40), 'Y': (180, 180, 40), 'B': (40, 40, 180),
              'S': (100, 200, 100),
              'M': (140, 30, 30), 'I': (90, 110, 85), 'P': (170, 80, 50),
              'r': (255, 80, 80), 'y': (255, 255, 80), 'b': (80, 80, 255),
              'm': (0, 200, 0), 'a': (200, 200, 0), 'k': (200, 50, 50),
              'h': (50, 100, 255), 'i': (255, 255, 100),
              '1': (160, 80, 80), '2': (180, 120, 50), '3': (130, 20, 130),
              '4': (40, 180, 200), '5': (110, 20, 20), '6': (170, 130, 70), '7': (235, 100, 170),
              '8': (220, 40, 40), '9': (70, 70, 75), '0': (180, 0, 200),
              'c': (130, 130, 140), 'g': (180, 130, 80), 'v': (200, 180, 160),
              'p': (140, 60, 180), 'l': (200, 100, 50), 'n': (140, 80, 40),
              'x': (60, 140, 60), 'z': (60, 80, 70),
              'D': (160, 120, 60),
              'w': (180, 0, 0), 'u': (100, 70, 40), 't': (40, 60, 40),
              'j': (0, 60, 0), 'o': (80, 50, 25), 'd': (200, 20, 50), 'e': (50, 200, 50), 'f': (50, 50, 200),
              'X': (180, 40, 20), 'H': (100, 80, 60), 'L': (200, 80, 0),
              'V': (100, 200, 100), 'C': (140, 100, 60), 'Q': (60, 180, 200), 'K': (200, 50, 200),
              '!': (200, 200, 50),
               'A': (100, 150, 255), 'N': (80, 80, 80), 'G': (200, 150, 50)}
