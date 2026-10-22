"""Smoke test for map items: placement, pickup, effects, respawn, wire format.

Items were the one thing the editor could place but the server silently
dropped -- ``spawn_enemies_from_map`` filtered everything that was not an
``ENEMY_DB`` key, so a medkit in a .mts reached the game world as nothing at
all. This checks the whole chain, because each link has its own way of failing
quietly:

  1. map -> server  (a placed item exists, a marker does not)
  2. pickup         (radius, once per respawn cycle, snapshot visibility)
  3. effects        (health, ammo, armor, and the three powerups)
  4. reset          (items return on a player's death, same stable id)
  5. wire format    (armor/powerup/items survive a round trip)
  6. client contract(engine keys the HUD and renderer actually read)

Run: py -3.12 smoke_items.py
"""

import math
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

# Section 9 renders a real frame to measure the pickups on screen, so pygame
# needs a display driver before it is imported anywhere below.
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

failures = []


def check(label, cond, detail=""):
    if cond:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label} {detail}")
        failures.append(label)


print("1. map -> server")
from server.game_data import (ITEM_DB, ITEM_PICKUP_DIST,
                              ARMOR_MAX, TICK_RATE, AMMO_KEYS, AMMO_MAX,
                              BERSERK_DMG_MULT)
from server.game_world import GameWorld
from shared.components import (Position, Health, WeaponComponent, Armor,
                               Powerup, Rotation)
from shared.map_data import GameMap
from shared.sector_map import SectorMap, Thing

# Every type the editor palette can place must be known to the server, or it is
# dropped exactly like the enemies used to be. This is the assertion that keeps
# the palette and ITEM_DB from drifting apart. ITEM_NAMES is keyed by editor
# hotkey, so its *values* are the item type names.
from client.map_editor import ITEM_NAMES
check("editor palette == ITEM_DB",
      set(ITEM_NAMES.values()) <= set(ITEM_DB),
      sorted(set(ITEM_NAMES.values()) - set(ITEM_DB)))


def make_map(*specs):
    sm = SectorMap.from_tiles(GameMap())
    sm.things = [Thing(x, y, t, 0) for t, x, y in specs]
    return sm


w = GameWorld('pve', make_map(
    ('med', 200.0, 200.0),
    ('armor_blue', 400.0, 400.0),
    ('invuln', 600.0, 600.0),
    ('player1', 800.0, 800.0),      # a marker, not a pickup
    ('exit', 900.0, 900.0),         # likewise
))
check("placed items spawned", len(w.items) == 3, len(w.items))
check("non-item things ignored",
      {i['t'] for i in w.items} == {'med', 'armor_blue', 'invuln'})
check("items exist before anyone joins", len(w.items) == 3)

print("\n2. pickup")
eid = w.add_player(1, 'Ann')
pos = w.entities.get_component(eid, Position)
hp = w.entities.get_component(eid, Health)

# Just outside the radius: the item must survive.
pos.x, pos.y = 200.0 + ITEM_PICKUP_DIST + 5.0, 200.0
w.update()
check("item survives outside the radius",
      [i['t'] for i in w.get_item_states()]
      == ['med', 'armor_blue', 'invuln'],
      w.get_item_states())

pos.x, pos.y = 200.0, 200.0
w.update()
check("a full-HP player refuses the medkit", hp.value == 200, hp.value)
check("refused medkit stays on the floor",
      [i['t'] for i in w.get_item_states()]
      == ['med', 'armor_blue', 'invuln'],
      w.get_item_states())
check("a refusal leaves no respawn timer",
      'respawn' not in w.items[0], w.items[0])

# The very same medkit is taken on the next tick there is room for it.
hp.value = 150
w.update()
check("item taken inside the radius",
      [i['t'] for i in w.get_item_states()] == ['armor_blue', 'invuln'],
      w.get_item_states())
check("taken item absent from the snapshot", len(w.get_item_states()) == 2)
check("a hurt player takes the medkit that was refused",
      hp.value == 175, hp.value)
check("the taken medkit did not overheal", hp.value <= 200, hp.value)

# A dead player must not loot: the body would eat the item before the wave
# restarts. Death no longer starts a respawn timer, so the body simply stays
# dead and the item on the floor is observed on its own -- were the wave to
# restart it would put the item back and mask a body that looted on the way down.
w2 = GameWorld('pve', make_map(('med', 200.0, 200.0)))
e2 = w2.add_player(1, 'Ann')
p2 = w2.entities.get_component(e2, Position)
p2.x, p2.y = 200.0, 200.0
w2.damage_player(e2, 500)
w2.update()
check("dead player does not loot",
      len(w2.get_item_states()) == 1, w2.get_item_states())

print("\n3. effects")
w = GameWorld('pve', make_map(
    ('med', 200.0, 200.0), ('heart', 260.0, 200.0), ('amm', 320.0, 200.0),
    ('armor_green', 380.0, 200.0), ('armor_blue', 440.0, 200.0),
    ('haste', 500.0, 200.0), ('berserk', 560.0, 200.0), ('invuln', 620.0, 200.0),
    ('haste', 680.0, 200.0),      # a second haste, to test re-picking
    ('wpn_3', 740.0, 200.0),
))
# Placements are kept further apart than ITEM_PICKUP_DIST, because a player
# standing between two of them would (correctly) pick up both in one tick and
# the per-item assertions below would then measure the wrong buff.
eid = w.add_player(1, 'Ann')
pos = w.entities.get_component(eid, Position)
hp = w.entities.get_component(eid, Health)
wp = w.entities.get_component(eid, WeaponComponent)
arm = w.entities.get_component(eid, Armor)
pu = w.entities.get_component(eid, Powerup)


def walk_onto(x):
    pos.x, pos.y = x, 200.0
    w.update()


hp.value = 100
walk_onto(200.0)
check("med heals 25", hp.value == 125, hp.value)
walk_onto(260.0)
check("heart heals 100, capped at max", hp.value == 200, hp.value)

before = dict(wp.ammo)
walk_onto(320.0)
check("ammo box adds to every ammo key",
      all(wp.ammo[k] > before[k] for k in AMMO_KEYS),
      {k: (before[k], wp.ammo[k]) for k in AMMO_KEYS})

# The carrying reserve is capped where ammo is granted, not where it is sent:
# the old min(999, ...) grew past the wire byte within a few pickups and the
# next snapshot raised struct.error inside the broadcast, taking the whole
# game loop down. A fresh world, because the box above is already taken.
w_cap = GameWorld('pve', make_map(('amm', 320.0, 200.0)))
e_cap = w_cap.add_player(1, 'Res')
p_cap = w_cap.entities.get_component(e_cap, Position)
wp_cap = w_cap.entities.get_component(e_cap, WeaponComponent)
wp_cap.ammo['pstl'] = AMMO_MAX - 5
p_cap.x, p_cap.y = 320.0, 200.0
w_cap.update()
check("an ammo pickup stops at the carrying reserve",
      wp_cap.ammo['pstl'] == AMMO_MAX, wp_cap.ammo['pstl'])
check("every stack stays inside the wire byte",
      all(v <= AMMO_MAX for v in wp_cap.ammo.values()), wp_cap.ammo)

check("player starts with no armor", arm.value == 0, arm.value)
walk_onto(380.0)
check("green armor gives 100", (arm.value, arm.kind) == (100, 'green'),
      (arm.value, arm.kind))
walk_onto(440.0)
check("blue armor adds and takes the better colour",
      (arm.value, arm.kind) == (200, 'blue'), (arm.value, arm.kind))
check("armor never exceeds its max", arm.value <= ARMOR_MAX, arm.value)

walk_onto(500.0)
check("haste is active", pu.kind == 'HASTE', pu.kind)
check("haste lasts 20 s", pu.ticks == TICK_RATE * 20, pu.ticks)

# Re-picking the *same* buff must refresh it, not double its life. A different
# buff replacing the first is a separate rule, checked below.
walk_onto(680.0)
check("re-picking the same buff refreshes instead of stacking",
      pu.ticks == TICK_RATE * 20, pu.ticks)

# A different buff replaces the first rather than living alongside it.
walk_onto(560.0)
check("a second buff replaces the first, it does not stack",
      pu.kind == 'BERSERK' and pu.ticks == TICK_RATE * 60,
      (pu.kind, pu.ticks))

# Armor soaks damage before HP, and the hit that breaks through still lands.
arm.value = 0
arm.kind = 'blue'
wpn_before = sorted(wp.owned)
walk_onto(740.0)
check("weapon pickup keeps ownership", 3 in wp.owned, sorted(wp.owned))
check("weapon pickup adds its ammo", wp.ammo['slg'] > 100, wp.ammo['slg'])

print("\n4. armor and invulnerability in damage_player")
arm.value = 100
hp.value = 200
w.damage_player(eid, 40)
check("armor absorbs the whole hit", (hp.value, arm.value) == (200, 60),
      (hp.value, arm.value))
w.damage_player(eid, 1000)
check("a hit past the armor kills", hp.value == 0, hp.value)

# Respawn clears carried-over protection.
w.respawn_player(1)
w.update()
check("respawn restores HP", hp.value == 200, hp.value)
check("respawn clears armor", arm.value == 0, arm.value)
check("respawn clears the buff", pu.kind == "", pu.kind)

# INVULN refuses damage outright, and expires on its own.
pu.kind = 'INVULN'
pu.ticks = 2
hp.value = 200
w.damage_player(eid, 100)
check("invulnerability refuses damage", hp.value == 200, hp.value)
w.update()
w.update()
check("invulnerability expires", pu.kind == "", pu.kind)
w.damage_player(eid, 100)
check("damage lands once it expires", hp.value == 100, hp.value)

print("\n5. powerup effects on movement and shooting")
from server.weapons import handle_shoot
from server.game_data import MOVE_SPEED_PX, TICK_INTERVAL, DASH_SPEED_MULT
import server.server as srv_mod
from server.player import PlayerSession
from shared.components import Velocity

# HASTE multiplies walking, and walking only: a dash is already
# DASH_SPEED_MULT, and stacking the buff would make it a 9x movement button.
wc = GameWorld(mode='pvp')
we = wc.add_player(1, 'A')
fake_session = PlayerSession.__new__(PlayerSession)
bare_server = srv_mod.GameServer.__new__(srv_mod.GameServer)
wpu = wc.entities.get_component(we, Powerup)


def walk_speed(powerup, dash=False):
    wpu.kind = powerup
    wpu.ticks = 100
    srv_mod.GameServer._apply_input(
        bare_server, wc, we, fake_session, 1, 0, 0.0, False, 1,
        1 if dash else 0)
    return abs(wc.entities.get_component(we, Velocity).dx)


base = walk_speed('')
check("haste speeds up walking",
      abs(walk_speed('HASTE') / base - 1.5) < 1e-6,
      walk_speed('HASTE') / base)
check("haste does not compound the dash",
      abs(walk_speed('HASTE', dash=True) / walk_speed('', dash=True) - 1.0) < 1e-6,
      walk_speed('HASTE', dash=True) / walk_speed('', dash=True))
check("dash still outruns walking",
      walk_speed('', dash=True) > base * DASH_SPEED_MULT * 0.9,
      (walk_speed('', dash=True), base))

# BERSERK: multiplied damage, free shots, and an empty magazine still refuses.
# The multiplier is read from game_data so balance tuning does not silently
# invalidate the check; the point is that the buff is applied at all.
ws = GameWorld(mode='pvp')
s1 = ws.add_player(1, 'A')
s2 = ws.add_player(2, 'B')
p1 = ws.entities.get_component(s1, Position)
p1.x, p1.y = 300.0, 300.0
ws.entities.get_component(s1, Rotation).angle = 0.0
ws.entities.get_component(s2, Position).x, ws.entities.get_component(
    s2, Position).y = 400.0, 300.0
wpn = ws.entities.get_component(s1, WeaponComponent)
wpn.current = 1
wpn.ammo['pstl'] = 100
spu = ws.entities.get_component(s1, Powerup)
hp2 = ws.entities.get_component(s2, Health)


def hit_for(powerup):
    spu.kind = powerup
    spu.ticks = 100
    wpn.fire_cooldown = 0
    hp2.value = 200
    handle_shoot(ws, s1)
    return 200 - hp2.value


plain_dmg = hit_for('')
berserk_dmg = hit_for('BERSERK')
check("berserk multiplies damage", berserk_dmg == plain_dmg * BERSERK_DMG_MULT,
      (plain_dmg, berserk_dmg))

spu.kind = 'BERSERK'
spu.ticks = 100
wpn.ammo['pstl'] = 50
wpn.fire_cooldown = 0
handle_shoot(ws, s1)
check("berserk does not spend ammo", wpn.ammo['pstl'] == 50, wpn.ammo['pstl'])

wpn.ammo['pstl'] = 0
wpn.fire_cooldown = 0
res = handle_shoot(ws, s1)
check("berserk still refuses an empty magazine",
      (res.fired, res.reason) == (False, 'no_ammo'), (res.fired, res.reason))

print("\n5b. refusal: full pockets leave the loot lying")
# One rule decides every row below: a pickup is refused when applying it would
# not change a single number a fight is won with (see design.md's table).  Each
# row gets its own world, so nothing can pass on a leftover from the row before.


def _pickup_world(*specs):
    gw = GameWorld('pve', make_map(*specs))
    geid = gw.add_player(1, 'Ann')
    return gw, geid


def _stand(gw, geid, x, y):
    p = gw.entities.get_component(geid, Position)
    p.x, p.y = x, y


# --- health: nothing to heal ------------------------------------------------
gw, geid = _pickup_world(('med', 200.0, 200.0))
ghp = gw.entities.get_component(geid, Health)
med = gw.items[0]
refused_id = med['id']
ghp.value = ghp.max_value
_stand(gw, geid, 200.0, 200.0)
gw.update()
check("full HP refuses the medkit",
      ghp.value == ghp.max_value and med['a'] is True, (ghp.value, med))
check("refusal leaves no respawn timer", 'respawn' not in med, med)

# There is no clock: waiting and standing on it changes nothing.
for _ in range(700):
    gw.update()
check("standing on it never expires into a respawn",
      med['a'] is True, med)
check("the refused item keeps its id", med['id'] == refused_id, med['id'])

ghp.value = ghp.max_value - 10
gw.update()
check("a missing 10 HP lets that same medkit go",
      med['a'] is False and ghp.value == ghp.max_value, (med['a'], ghp.value))

# --- ammo: nothing to top up ------------------------------------------------
gw, geid = _pickup_world(('amm', 200.0, 200.0))
gwp = gw.entities.get_component(geid, WeaponComponent)
box = gw.items[0]
for _k in AMMO_KEYS:
    gwp.ammo[_k] = AMMO_MAX
_stand(gw, geid, 200.0, 200.0)
gw.update()
check("a full reserve refuses the ammo box",
      box['a'] is True and all(gwp.ammo[_k] == AMMO_MAX for _k in AMMO_KEYS),
      (box['a'], gwp.ammo))

gwp.ammo['rck'] = AMMO_MAX - 1
gw.update()
check("one non-full stack takes the ammo box",
      box['a'] is False and gwp.ammo['rck'] == AMMO_MAX,
      (box['a'], gwp.ammo['rck']))

# --- armor: a full plate gains nothing, not even a repaint ------------------
gw, geid = _pickup_world(('armor_green', 200.0, 200.0),
                         ('armor_blue', 260.0, 200.0))
garm = gw.entities.get_component(geid, Armor)
garm.value = garm.max_value
garm.kind = 'green'
_stand(gw, geid, 200.0, 200.0)
gw.update()
check("full plate refuses the green pickup",
      gw.items[0]['a'] is True
      and (garm.value, garm.kind) == (garm.max_value, 'green'),
      (gw.items[0]['a'], garm.value, garm.max_value, garm.kind))

_stand(gw, geid, 260.0, 200.0)
gw.update()
check("full plate refuses the blue pickup too (colour is not a gain)",
      gw.items[1]['a'] is True and garm.kind == 'green',
      (gw.items[1]['a'], garm.kind))

garm.value = garm.max_value - 50
gw.update()
check("a short plate takes the blue pickup",
      gw.items[1]['a'] is False
      and (garm.value, garm.kind) == (garm.max_value, 'blue'),
      (gw.items[1]['a'], garm.value, garm.kind))

# --- weapon: owned + full stack = refused; not owned = taken ---------------
gw, geid = _pickup_world(('wpn_3', 200.0, 200.0))
gwp = gw.entities.get_component(geid, WeaponComponent)
for _k in AMMO_KEYS:
    gwp.ammo[_k] = AMMO_MAX
gun = gw.items[0]
_stand(gw, geid, 200.0, 200.0)
gw.update()
check("an owned gun with a full stack stays on the floor",
      gun['a'] is True and 3 in gwp.owned
      and all(gwp.ammo[_k] == AMMO_MAX for _k in AMMO_KEYS),
      (gun['a'], gwp.owned, gwp.ammo))

gw, geid = _pickup_world(('wpn_3', 200.0, 200.0))
gwp = gw.entities.get_component(geid, WeaponComponent)
for _k in AMMO_KEYS:
    gwp.ammo[_k] = AMMO_MAX
gwp.owned.clear()
gwp.owned.add(1)
gun = gw.items[0]
_stand(gw, geid, 200.0, 200.0)
gw.update()
check("a gun the player does not own is taken even with a full reserve",
      gun['a'] is False and 3 in gwp.owned, (gun['a'], gwp.owned))

# --- powerup: a refresh is always a change ----------------------------------
gw, geid = _pickup_world(('haste', 200.0, 200.0))
gpu = gw.entities.get_component(geid, Powerup)
gpu.kind, gpu.ticks = 'HASTE', 5
_stand(gw, geid, 200.0, 200.0)
gw.update()
check("an active buff is refreshed, never refused",
      gw.items[0]['a'] is False and gpu.ticks == TICK_RATE * 20,
      (gw.items[0]['a'], gpu.ticks))

# --- a full player must not wall off loot for everyone else ----------------
gw = GameWorld('pve', make_map(('med', 200.0, 200.0)))
e_full = gw.add_player(1, 'Full')
e_hurt = gw.add_player(2, 'Hurt')
p_full = gw.entities.get_component(e_full, Position)
p_hurt = gw.entities.get_component(e_hurt, Position)
hp_full = gw.entities.get_component(e_full, Health)
hp_hurt = gw.entities.get_component(e_hurt, Health)
p_full.x, p_full.y = 200.0, 200.0          # exactly on the medkit, first in
p_hurt.x, p_hurt.y = 200.0 + ITEM_PICKUP_DIST - 2.0, 200.0
hp_full.value = hp_full.max_value
hp_hurt.value = 100
gw.update()
check("a full player does not wall off the loot",
      gw.items[0]['a'] is False
      and hp_hurt.value == min(hp_hurt.max_value, 125)
      and hp_full.value == hp_full.max_value,
      (gw.items[0]['a'], hp_full.value, hp_hurt.value))

print("\n6. items return on a player's respawn, not on a timer")
w = GameWorld('pve', make_map(('heart', 500.0, 500.0)))
eid = w.add_player(1, 'Ann')
pos = w.entities.get_component(eid, Position)
pos.x, pos.y = 500.0, 500.0
# The heart is refused at full HP, and a refused pickup is never taken -- so
# the player has to be short of healing for this section to measure a pickup.
w.entities.get_component(eid, Health).value = 100
w.update()
item = w.items[0]
check("item marked taken", item['a'] is False, item)
check("a taken item carries no respawn timer", 'respawn' not in item, item)
item_id = item['id']
pos.x, pos.y = 100.0, 100.0

# No amount of waiting brings it back: the timer is gone, not frozen.
for _ in range(700):
    w.update()
check("a taken item never returns on its own", item['a'] is False, item)

# Killing an enemy is not a wave restart, so it returns nothing either.
enemy = {'id': 1, 'x': 800.0, 'y': 800.0, 't': 'soldier',
         'hp': 20, 'max_hp': 20, 'spd': 0.0, 'col': (1, 2, 3), 'a': True,
         'dying': 0, 'anim_frame': 0, 'anim_state': 0, 'stun': 0,
         'alert': False, 'pa': 0.0, 'ls': 0, 'd_flash': 0,
         'spawn_x': 800.0, 'spawn_y': 800.0, 'spawn_pa': 0.0,
         'attack_from': None, 'attack_ticks': 0}
w.enemies.append(enemy)
w.kill_enemy(enemy)
for _ in range(20):
    w.update()
check("killing an enemy does not return items", item['a'] is False, item)

# A player's respawn restarts the wave and puts every taken item back.  Death
# itself no longer does: the player stays dead until the client asks for it.
w.damage_player(eid, 500)
w.respawn_player(1)
w.update()            # respawn + wave restart
check("a player's respawn returns the taken item", item['a'] is True, item)
check("the item is back on its own spot",
      (item['x'], item['y']) == (500.0, 500.0), (item['x'], item['y']))
check("id is stable across the wave restart", item['id'] == item_id, item['id'])

print("\n6. wire format")
from shared.protocol import (encode_world_state, decode_world_state,
                             ITEM_TYPES, ARMOR_TYPES, POWERUP_TYPES)
from shared.version import PROTOCOL_VERSION

check("ITEM_TYPES covers ITEM_DB", set(ITEM_TYPES) == set(ITEM_DB),
      set(ITEM_TYPES) ^ set(ITEM_DB))
check("protocol version bumped for the new fields", PROTOCOL_VERSION >= 9,
      PROTOCOL_VERSION)

player = {
    'id': 0, 'x': 1.0, 'y': 2.0, 'angle': 0.5, 'health': 150, 'state': 0,
    'wpn': 3, 'ammo': {'pstl': 10, 'shl': 20, 'slg': 30, 'rck': 1, 'BFG': 2},
    'fire_seq': 7, 'fire_ready': 3, 'fire_reason': 0, 'name': 'Ann',
    'armor': {'type': 'blue', 'value': 175}, 'powerup': 'HASTE',
}
items = [{'id': 1, 'x': 10.0, 'y': 20.0, 't': 'armor_blue'},
         {'id': 2, 'x': 30.0, 'y': 40.0, 't': 'med'}]
out = encode_world_state(9, [player], [], [], [], 'pvp', [], items)
(_t, ps, _e, _b, _c, mode, _im, its, _doors) = decode_world_state(out)
p = ps[0]
check("armor survives the round trip", p['armor'] == {'type': 'blue', 'value': 175},
      p['armor'])
check("powerup survives the round trip", p['powerup'] == 'HASTE', p['powerup'])
check("items survive the round trip",
      [(i['id'], i['type']) for i in its] == [(1, 'armor_blue'), (2, 'med')], its)
check("mode still decodes", mode == 'pvp', mode)

# Both byte-range crashes of the player record, in one frame: a reserve at the
# wire ceiling and a player id past 255 (the join counter has no ceiling, so
# the 256th session of a long-running server hit it). Neither may raise out of
# encode_world_state -- struct.error there killed the broadcast and with it the
# game loop (see _u8 / AMMO_MAX).
wide = dict(player, id=300,
            ammo={'pstl': 255, 'shl': 255, 'slg': 255, 'rck': 255, 'BFG': 255})
(_t, ps5, *_rest) = decode_world_state(encode_world_state(1, [wide]))
check("full reserve and a 3-digit player id survive the round trip",
      ps5[0]['id'] == 300
      and all(v == 255 for v in ps5[0]['ammo'].values()),
      (ps5[0]['id'], ps5[0]['ammo']))

bare = dict(player, armor=None, powerup='')
(_t, ps2, *_rest) = decode_world_state(encode_world_state(1, [bare]))
check("no armor decodes as None, not an empty dict", ps2[0]['armor'] is None,
      ps2[0]['armor'])
check("no powerup decodes as an empty string", ps2[0]['powerup'] == '',
      repr(ps2[0]['powerup']))

# An unknown index must not raise: a newer server must not crash an older client
# with an IndexError inside the render loop. Byte 29/30 of the player record are
# armor_idx / pu_idx (header is 6 bytes after the uint32 tick, the record is 33:
# id is uint16).
out = bytearray(encode_world_state(1, [player], [], [], [], 'pve', [], []))
out[6 + 29] = 250
out[6 + 30] = 250
try:
    (_t, ps3, *_rest) = decode_world_state(bytes(out))
    check("unknown armor index degrades instead of raising",
          ps3[0]['armor'] is None, repr(ps3[0]['armor']))
    check("unknown powerup index degrades instead of raising",
          ps3[0]['powerup'] == '', repr(ps3[0]['powerup']))
except Exception as exc:  # noqa: BLE001 - the point is that this must not raise
    check("unknown armor index degrades instead of raising", False, repr(exc))

# An unknown armor/powerup *name* from the server must degrade the same way.
odd = dict(player, armor={'type': 'plasma', 'value': 10}, powerup='RAGE')
(_t, ps4, *_rest) = decode_world_state(encode_world_state(1, [odd]))
check("unknown armor name decodes to a valid colour",
      ps4[0]['armor']['type'] in ARMOR_TYPES, ps4[0]['armor'])
check("unknown powerup name decodes to no buff", ps4[0]['powerup'] == '',
      repr(ps4[0]['powerup']))

print("\n7. client contract")
from engine.constants import ITEM_GAME_HEIGHTS, ITEM_FLOAT_HEIGHT
check("every item type has a render height",
      set(ITEM_TYPES) <= set(ITEM_GAME_HEIGHTS),
      sorted(set(ITEM_TYPES) - set(ITEM_GAME_HEIGHTS)))
check("ITEM_FLOAT_HEIGHT is a ratio, not a pixel count",
      0.0 < ITEM_FLOAT_HEIGHT < 1.0, ITEM_FLOAT_HEIGHT)

# The HUD reads gs.p['armor']['type']/['value'] and gs.p['powerup']; the
# renderer reads items keyed by 't'. These are the exact names, so a rename on
# either side of the wire has to fail here rather than on screen.
st = w.get_all_player_states()[0]
check("snapshot armor uses the HUD's keys",
      st['armor'] is None or set(st['armor']) == {'type', 'value'}, st['armor'])
check("snapshot powerup is one of the HUD's spellings",
      st['powerup'] in POWERUP_TYPES, st['powerup'])

import engine.game_state as gs
check("engine exposes items", hasattr(gs, 'items'))
check("engine player dict has armor/powerup keys",
      'armor' in gs.p and 'powerup' in gs.p)

print("\n8. server -> snapshot through the real world")
w = GameWorld('pve', make_map(('med', 200.0, 200.0), ('armor_blue', 300.0, 200.0)))
eid = w.add_player(1, 'Ann')
pos = w.entities.get_component(eid, Position)
check("both items published before pickup",
      len(w.get_item_states()) == 2, w.get_item_states())
pos.x, pos.y = 200.0, 200.0
# Full HP would refuse the medkit; this section is about the taken path.
w.entities.get_component(eid, Health).value = 150
w.update()
states = w.get_item_states()
check("one item left after pickup", len(states) == 1, states)
payload = encode_world_state(w.tick, w.get_all_player_states(), [], [], [],
                             'pve', [], states)
check("snapshot encodes with items present", len(payload) > 0)

print("\n9. pickup geometry on screen")
# A constant can stay correct while the renderer stops reading it: the two
# drawing rules for pickups (per-type height, a float above the floor) are only
# real if a painted frame says so. So one frame is rendered in sector mode and
# the pixels on screen are measured against the projection the sprite pass
# itself uses. The room is dark and non-white/non-yellow on purpose: the boxes
# are found by exact colour, so no wall or flat may share them.
import pygame

from engine import constants as eng_const
from engine import rendering
from engine import sector_render
from engine.sector_render import SectorScene
from shared.sector_map import LineDef, Sector, SideDef, Vertex

pygame.init()
gs.screen = pygame.display.set_mode((eng_const.W, eng_const.H))


def _pickup_room():
    sm = SectorMap()
    sm.sectors.append(Sector(floor_h=0, ceil_h=128, light=200))

    def _v(x, y):
        for i, v in enumerate(sm.verts):
            if v.x == x and v.y == y:
                return i
        sm.verts.append(Vertex(float(x), float(y)))
        return len(sm.verts) - 1

    for ax, ay, bx, by in ((0, 0, 512, 0), (512, 0, 512, 512),
                           (512, 512, 0, 512), (0, 512, 0, 0)):
        sm.sidedefs.append(SideDef(sector=0, mid="WALL"))
        sm.linedefs.append(LineDef(_v(ax, ay), _v(bx, by),
                                   front=len(sm.sidedefs) - 1, back=-1))
    return sm


wall_tex = pygame.Surface((64, 64))
wall_tex.fill((20, 30, 60))
floor_flat = pygame.Surface((64, 64))
floor_flat.fill((10, 60, 20))
ceil_flat = pygame.Surface((64, 64))
ceil_flat.fill((30, 30, 80))
gs.textures = {"WALL": wall_tex}
gs.flats = {"floor": floor_flat, "ceiling": ceil_flat}

_sm = _pickup_room()
_scene = SectorScene(_sm)
gs.sector_mode = True
gs.sec_scene = _scene
# Legacy fields some secondary paths still read; the bridge fills them the same
# way when it loads a sector map.
gs.MAP = []
gs.M_SZ = 0
gs.explored = []
gs.sec_verts = [(v.x, v.y) for v in _sm.verts]
gs.sec_lines = [[ld.v1, ld.v2,
                 _scene.sector_of_side(ld.front),
                 _scene.sector_of_side(ld.back),
                 ld.flags & 0xF]
                for ld in _sm.linedefs]
gs.sec_sectors = [{'fh': s.floor_h, 'ch': s.ceil_h,
                   'fc': _scene.flat_color(s.floor_tex),
                   'cc': _scene.flat_color(s.ceil_tex),
                   'wc': _scene.flat_color('wall')}
                  for s in _sm.sectors]
# Default (0, 0) is truthy and would draw an exit marker into the frame.
gs.exit_pos = None
gs.render_scale = 4
gs.vs_scale = 2
gs.view_dist = 900
gs.flat_textured = False
gs.px, gs.py, gs.pa = 100.0, 256.0, 0.0
gs.pz = 0.0
gs.show_crosshair = False
gs.show_minimap = False
gs.fog_enabled = False
gs.dash_fx = 0.0
gs.bots = []
gs.enemies = []
gs.corpses = []
gs.impacts = []
gs.projectiles = []
gs.barrels = []
gs.players = []
gs.p = dict(gs.p)
gs.p["hp"] = 100
gs.p["powerup"] = None
gs.p["armor"] = None
# wpn >= 100 with no custom guns: draw_wpn paints no viewmodel, so only the
# world and the two pickups are on screen for the measurement.
gs.p["wpn"] = 100
gs.font = pygame.font.Font(None, 28)
gs.sm_font = pygame.font.Font(None, 20)
# Two pickups at the same distance, off centre and far apart horizontally so
# neither scan window can contain the other.
gs.items = [
    {"id": 1, "x": 350.0, "y": 226.0, "t": "amm", "a": True},
    {"id": 2, "x": 350.0, "y": 286.0, "t": "med", "a": True},
]

rendering.render(eng_const.H, 0)


def _expected_box(x, y, t):
    """The box rendering.py must have painted for this pickup, from the same
    projection and the same rounding the sprite pass uses."""
    sx, sh_raw, flr_raw, _perp = sector_render.project_object(
        x, y, ITEM_GAME_HEIGHTS[t], eng_const.H, 0)
    sh = int(min(sh_raw, eng_const.H * 2))
    top = int(flr_raw) - int(sh * ITEM_FLOAT_HEIGHT)
    return sx - sh // 4, top, sh // 2, sh // 4


def _painted_box(colour, box, margin=24):
    left, top, w, h = box
    xs, ys = [], []
    for yy in range(max(0, top - margin), min(eng_const.H, top + h + margin)):
        for xx in range(max(0, left - margin),
                        min(eng_const.W, left + w + margin)):
            if gs.screen.get_at((xx, yy))[:3] == colour:
                xs.append(xx)
                ys.append(yy)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _check_paint(label, x, y, t, colour):
    want = _expected_box(x, y, t)
    got = _painted_box(colour, want)
    wl, wt, ww, wh = want
    check(f"{label} is painted at its per-type size",
          got is not None
          and abs((got[2] - got[0] + 1) - ww) <= 2
          and abs((got[3] - got[1] + 1) - wh) <= 2,
          (got, want))
    check(f"{label} floats above the floor line",
          got is not None and abs(got[1] - wt) <= 2, (got, want))


_check_paint("ammo box", 350.0, 226.0, "amm", (220, 220, 0))
_check_paint("medkit", 350.0, 286.0, "med", (255, 255, 255))

print()
if failures:
    print(f"FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("all item checks passed")
