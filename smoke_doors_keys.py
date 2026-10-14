"""Smoke: doors and keys end to end (change sector-doors-and-keys).

Covers tasks 7.1-7.2:
- map data: door lock from the mid texture, mismatched locks rejected
- server automaton: closed -> opening -> open -> hold -> closing -> closed,
  blocked while shut/moving, passable only when fully open, occupied gap waits
- "use": nearest door ahead, locked without a key, locked with the key, wall no-op
- keys: pickup sets the mask bit, no item-timer respawn, wave restart returns them
- snapshot: sparse non-closed doors, per-player key mask and advisory use_reason
- protocol round-trip of the new fields
- mutations: each old behaviour below must make the suite fail

Run: py -3.12 smoke_doors_keys.py            (scenarios)
     py -3.12 smoke_doors_keys.py --mutate   (scenario + mutation harness)
"""
import io
import math
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

from server.game_world import GameWorld
from shared.combat import USE_REASON_LOCKED, USE_REASON_OK
from shared.components import Position, Rotation, Velocity, Keys, Health
from shared.protocol import encode_world_state, decode_world_state
from shared.sector_map import (
    DOOR_TEXTURE_BY_LOCK, KEY_BITS, LineDef, Sector, SectorMap, SideDef,
    SPECIAL_DOORS, Thing, Vertex)

from server.game_data import (DOOR_CLOSE_TICKS, DOOR_HOLD_TICKS,
                              DOOR_OPEN_TICKS)


# ---------------------------------------------------------------------------
# Map builders
# ---------------------------------------------------------------------------

def _build(rooms, doors, things, h=512.0):
    """Side-by-side rooms sharing vertical edges; ``doors`` marks shared edges.

    ``rooms`` is a list of ``(x0, x1)`` spans (left to right); ``doors`` maps a
    shared-edge index (0-based between room i and i+1) to a lock colour or None
    (unlocked); ``things`` is a list of ``Thing``. Edges are oriented so the
    front sector sits on the expected side, which is what lets the polygon
    builder close each sector (same rule as smoke_sector_server._strip_map).
    """
    sm = SectorMap()
    for _ in rooms:
        sm.sectors.append(Sector(floor_h=0, ceil_h=256))
    sides = []
    for si in range(len(rooms)):
        sides.append(len(sm.sidedefs))
        sm.sidedefs.append(SideDef(sector=si))

    seen: dict[tuple, int] = {}

    def gid(px, py):
        key = (px, py)
        if key in seen:
            return seen[key]
        sm.verts.append(Vertex(px, py))
        seen[key] = len(sm.verts) - 1
        return seen[key]

    def edge(ax, ay, bx, by, front, back=-1, ref=None):
        if ref is not None:
            cross = (bx - ax) * (ref[1] - ay) - (by - ay) * (ref[0] - ax)
            if cross >= 0:
                ax, ay, bx, by = bx, by, ax, ay
        sm.linedefs.append(LineDef(gid(ax, ay), gid(bx, by), front=front,
                                   back=back))
        return len(sm.linedefs) - 1

    n = len(rooms)
    for i, (x0, x1) in enumerate(rooms):
        ref = ((x0 + x1) * 0.5, h * 0.5)
        edge(x0, 0.0, x1, 0.0, sides[i], ref=ref)     # floor span
        edge(x0, h, x1, h, sides[i], ref=ref)         # ceiling span
        if i == 0:
            edge(x0, 0.0, x0, h, sides[i], ref=ref)   # left cap
        if i == n - 1:
            edge(x1, 0.0, x1, h, sides[i], ref=ref)   # right cap

    door_lis: dict[int, int] = {}
    for di, lock in doors.items():
        x = rooms[di][1]
        ref = ((rooms[di][0] + rooms[di][1]) * 0.5, h * 0.5)
        li = edge(x, 0.0, x, h, sides[di], sides[di + 1], ref=ref)
        sm.linedefs[li].special = SPECIAL_DOORS
        tex = DOOR_TEXTURE_BY_LOCK[lock]
        sm.sidedefs[sides[di]].mid = tex
        sm.sidedefs[sides[di + 1]].mid = tex
        door_lis[di] = li

    for t in things:
        sm.things.append(t)
    errs = sm.validate()
    assert errs == [], errs
    return sm, door_lis


def _one_door(lock='red', extra_things=()):
    things = [Thing(256, 256)]
    things.extend(extra_things)
    sm, door_lis = _build([(0, 512), (512, 1024)], {0: lock}, things)
    return sm, door_lis[0]


def _spawned_world(sm, player_id=1, name="T"):
    world = GameWorld(mode='pve', game_map=sm)
    eid = world.add_player(player_id, name)
    return world, eid


def _face(world, eid, x, y, angle):
    pos = world.entities.get_component(eid, Position)
    rot = world.entities.get_component(eid, Rotation)
    pos.x, pos.y = x, y
    rot.angle = angle


def _tick(world, n=1):
    for _ in range(n):
        world.update()


def _drive(world, eid, dx, dy, ticks):
    """Feed non-zero movement input into real simulation ticks."""
    pos = world.entities.get_component(eid, Position)
    vel = world.entities.get_component(eid, Velocity)
    for _ in range(ticks):
        vel.dx, vel.dy = dx, dy
        world.update()
    return pos


# ---------------------------------------------------------------------------
# 1. Map data
# ---------------------------------------------------------------------------

def test_map_data():
    sm, door_li = _one_door('red')
    assert sm.door_linedefs() == [door_li], sm.door_linedefs()
    assert sm.door_lock(door_li) == 'red'
    assert sm.linedefs[door_li].special == SPECIAL_DOORS
    assert not sm.linedefs[door_li].solid, "a door is not a plain solid wall"

    # An unlocked door carries no lock.
    sm2, d2 = _one_door(None)
    assert sm2.door_lock(d2) is None

    # Both faces must agree on the lock: a mismatch is invalid.
    sm3, d3 = _one_door('red')
    sm3.sidedefs[sm3.linedefs[d3].back].mid = "door_Y"
    errs = sm3.validate()
    assert any("lock differs" in e for e in errs), errs

    # A non-door texture on a door linedef is invalid too.
    sm4, d4 = _one_door('red')
    sm4.sidedefs[sm4.linedefs[d4].front].mid = "wall"
    errs = sm4.validate()
    assert any("not a door texture" in e for e in errs), errs
    print("1. door map data / locks OK")


# ---------------------------------------------------------------------------
# 2. Server automaton
# ---------------------------------------------------------------------------

def test_automaton():
    sm, door_li = _one_door(None)
    world, eid = _spawned_world(sm)
    assert world.doors[door_li]['phase'] == 'closed'
    assert door_li in world._blocked_doors

    # A shut door blocks movement, sight and projectiles.
    _face(world, eid, 480, 256, 0.0)
    pos = _drive(world, eid, 8.0, 0.0, 60)
    assert pos.x < 512, f"crossed a closed door: {pos.x}"
    assert not world.has_los_px(256, 256, 768, 256), "closed door gave sight"
    # A point just past the plane, within DOOR_POINT_RADIUS (2 px), is solid
    # while the door is shut -- that is what stops a projectile mid-step.
    assert world.is_wall_px(513.0, 256), "closed door was not solid for shots"

    # Open it; while it moves it must still block.
    _face(world, eid, 480, 256, 0.0)
    assert world.try_use(eid) == USE_REASON_OK
    assert world.doors[door_li]['phase'] == 'opening'
    _tick(world, DOOR_OPEN_TICKS // 2)
    assert not world.has_los_px(256, 256, 768, 256), "half-open gave sight"
    pos = _drive(world, eid, 8.0, 0.0, DOOR_OPEN_TICKS // 2)
    assert pos.x < 512, f"crossed a half-open door: {pos.x}"

    # Fully open -> a plain portal.
    _tick(world, DOOR_OPEN_TICKS)
    assert world.doors[door_li]['phase'] == 'open'
    assert world.doors[door_li]['progress'] == 1.0
    assert door_li not in world._blocked_doors
    assert world.has_los_px(256, 256, 768, 256), "open door blocked sight"
    assert not world.is_wall_px(513.0, 256), "open door still solid for shots"
    _face(world, eid, 480, 256, 0.0)
    pos = _drive(world, eid, 8.0, 0.0, 40)
    assert pos.x > 512, f"could not cross an open door: {pos.x}"
    print("2a. door opens and only then is passable OK")

    # Occupied gap: the door waits instead of closing on an actor.
    _face(world, eid, 512, 256, 0.0)
    _tick(world, DOOR_HOLD_TICKS + 5)
    assert world.doors[door_li]['phase'] == 'open', "closed on an actor in the gap"

    # Empty gap: it closes by itself and blocks again.
    _face(world, eid, 256, 256, 0.0)
    _tick(world, DOOR_HOLD_TICKS + DOOR_CLOSE_TICKS + 5)
    assert world.doors[door_li]['phase'] == 'closed'
    assert door_li in world._blocked_doors
    assert not world.has_los_px(256, 256, 768, 256)
    print("2b. auto-close, occupied gap waits OK")

    # Re-using an open door restarts the hold.
    _face(world, eid, 480, 256, 0.0)
    world.try_use(eid)
    _tick(world, DOOR_OPEN_TICKS + 3)
    world.doors[door_li]['hold'] = 5
    assert world.try_use(eid) == USE_REASON_OK
    assert world.doors[door_li]['hold'] == DOOR_HOLD_TICKS
    print("2c. re-use restarts the hold OK")


# ---------------------------------------------------------------------------
# 3. The "use" action
# ---------------------------------------------------------------------------

def test_use():
    # Locked without the key: refused, door stays shut, reason is LOCKED.
    sm, door_li = _one_door('yellow')
    world, eid = _spawned_world(sm)
    _face(world, eid, 480, 256, 0.0)
    assert world.try_use(eid) == USE_REASON_LOCKED
    assert world.get_use_reason(1) == USE_REASON_LOCKED
    assert world.doors[door_li]['phase'] == 'closed'
    print("3a. locked door refused without the key OK")

    # With the yellow key it opens.
    world.entities.get_component(eid, Keys).mask |= KEY_BITS['yellow']
    assert world.try_use(eid) == USE_REASON_OK
    assert world.doors[door_li]['phase'] == 'opening'
    assert world.get_use_reason(1) == USE_REASON_OK
    print("3b. locked door opens with the key OK")

    # Using a wall (no door in reach) changes nothing.
    sm2, d2 = _one_door(None)
    w2, e2 = _spawned_world(sm2)
    _face(w2, e2, 256, 256, math.pi)     # facing away from the door
    assert w2.try_use(e2) == USE_REASON_OK
    assert w2.doors[d2]['phase'] == 'closed', "door opened from behind"
    _face(w2, e2, 100, 256, 0.0)         # far from the door
    assert w2.try_use(e2) == USE_REASON_OK
    assert w2.doors[d2]['phase'] == 'closed'
    print("3c. use into empty space changes nothing OK")

    # Only the nearest door in reach opens.
    sm3, dl = _build([(0, 512), (512, 528), (528, 1040)], {0: None, 1: None},
                     [Thing(256, 256)])
    w3, e3 = _spawned_world(sm3)
    near, far = dl[0], dl[1]
    _face(w3, e3, 480, 256, 0.0)         # near d=32, far d=48, both in front
    assert w3.try_use(e3) == USE_REASON_OK
    assert w3.doors[near]['phase'] == 'opening'
    assert w3.doors[far]['phase'] == 'closed', "opened the farther door"
    print("3d. only the nearest door opens OK")

    # Rising edge only: a held "use" does not reopen every input packet.
    sm4, d4 = _one_door(None)
    w4, e4 = _spawned_world(sm4)
    _face(w4, e4, 480, 256, 0.0)
    w4.apply_use_input(e4, True)
    assert w4.doors[d4]['phase'] == 'opening'
    _tick(w4, DOOR_OPEN_TICKS + 3)
    assert w4.doors[d4]['phase'] == 'open'
    w4.doors[d4]['hold'] = 5
    w4.apply_use_input(e4, True)         # still held: no new edge
    assert w4.doors[d4]['hold'] == 5, "held use re-fired on the edge"
    w4.apply_use_input(e4, False)
    w4.apply_use_input(e4, True)         # fresh press: restarts the hold
    assert w4.doors[d4]['hold'] == DOOR_HOLD_TICKS
    print("3e. use is edge-triggered OK")


# ---------------------------------------------------------------------------
# 4. Keys as items
# ---------------------------------------------------------------------------

def test_keys():
    sm, door_li = _one_door('red', extra_things=[Thing(256, 300, 'key_red')])
    world, eid = _spawned_world(sm)
    keys = world.entities.get_component(eid, Keys)
    assert keys.mask == 0, "player started with a key"

    item = next(it for it in world.items if it['t'] == 'key_red')
    # Walk the player onto the key: the pickup radius does its job.
    _face(world, eid, item['x'], item['y'], 0.0)
    _tick(world)
    assert keys.mask & KEY_BITS['red'], "key pickup did not set the mask bit"
    assert not item['a'], "taken key still active"

    # A taken item only comes back on the wave restart: no timer brings it back
    # on its own, however long the player waits.
    _tick(world, 2000)
    assert not item['a'], "a taken key came back on its own"
    print("4a. key pickup sets the mask and never self-respawns OK")

    # A dead player does not loot.
    sm2, _ = _one_door('red', extra_things=[Thing(256, 300, 'key_blue')])
    w2, e2 = _spawned_world(sm2)
    item2 = next(it for it in w2.items if it['t'] == 'key_blue')
    _face(w2, e2, item2['x'], item2['y'], 0.0)
    w2.entities.get_component(e2, Health).value = 0
    _tick(w2)
    assert item2['a'], "a dead player looted the key"
    assert w2.entities.get_component(e2, Keys).mask == 0
    print("4b. a dead player does not loot a key OK")

    # Wave restart returns the key and clears the mask.
    _face(world, eid, 100, 100, 0.0)
    world.damage_player(eid, 9999)
    _tick(world, 3)                       # respawn timer is one tick
    assert keys.mask == 0, "restart kept the key"
    assert item['a'], "restart did not put the key back"
    print("4c. wave restart returns keys and clears masks OK")


# ---------------------------------------------------------------------------
# 5. Snapshot and protocol
# ---------------------------------------------------------------------------

def test_snapshot():
    sm, door_li = _one_door('red', extra_things=[Thing(256, 300, 'key_red')])
    world, eid = _spawned_world(sm)

    # A closed door is silent in the snapshot.
    assert world.get_door_states() == [], world.get_door_states()

    _face(world, eid, 480, 256, 0.0)
    world.try_use(eid)                    # locked, but the reason is advisory
    states = world.get_all_player_states()
    assert states[0]['keys'] == 0
    assert states[0]['use_reason'] == USE_REASON_LOCKED

    # Reset the reason and take the key; the open door then appears.
    world.entities.get_component(eid, Keys).mask |= KEY_BITS['red']
    world.try_use(eid)
    _tick(world, DOOR_OPEN_TICKS + 2)
    dstate = world.get_door_states()
    assert len(dstate) == 1 and dstate[0]['li'] == door_li, dstate
    assert dstate[0]['progress'] > 0, dstate
    print("5a. sparse door state and player fields OK")

    # Round-trip through the wire.
    players = [{"id": 1, "x": 1.0, "y": 2.0, "angle": 0.0, "health": 100,
                "state": 0, "name": "A", "keys": 0x05, "use_reason": 1}]
    doors = [{"li": 5, "progress": 128}]
    data = encode_world_state(7, players, [], [], [], "pve", [], [], doors)
    _t, p, _e, _b, _c, _m, _im, _it, dd = decode_world_state(data)
    assert p[0]['keys'] == 0x05, p[0]
    assert p[0]['use_reason'] == 1, p[0]
    assert dd == [{"li": 5, "progress": 128}], dd
    print("5b. keys/use_reason/doors survive the wire OK")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_scenarios():
    test_map_data()
    test_automaton()
    test_use()
    test_keys()
    test_snapshot()
    print("DOORS/KEYS SMOKE PASSED")


# ---------------------------------------------------------------------------
# Mutation harness (task 7.2)
# ---------------------------------------------------------------------------

MUTATIONS = [
    (
        "a closed door is passable",
        "server/game_world.py",
        "        self._blocked_doors = frozenset(\n"
        "            li for li, d in self.doors.items() if d['progress'] < 1.0)",
        "        self._blocked_doors = frozenset()  # MUTATED",
    ),
    (
        "the lock is ignored (any key opens any door)",
        "server/game_world.py",
        "            if not (self._player_keys(eid) & bit):\n"
        "                self._use_reason[pl.player_id] = USE_REASON_LOCKED\n"
        "                return USE_REASON_LOCKED",
        "            if False:  # MUTATED\n"
        "                self._use_reason[pl.player_id] = USE_REASON_LOCKED\n"
        "                return USE_REASON_LOCKED",
    ),
    (
        "a key pickup grants no bit",
        "server/game_world.py",
        "        elif kind == 'key':\n"
        "            keys = self.entities.get_component(eid, Keys)\n"
        "            if keys is not None:\n"
        "                keys.mask |= KEY_BITS.get(spec['lock'], 0)",
        "        elif kind == 'key':\n"
        "            pass  # MUTATED",
    ),
    (
        "the wave restart does not put items back",
        "server/game_world.py",
        "        for it in self.items:\n"
        "            it['a'] = True",
        "        for it in self.items:\n"
        "            pass  # MUTATED: taken items stay gone",
    ),
    (
        "the wave restart keeps the key masks",
        "server/game_world.py",
        "        for _pid, eid in self.players.items():\n"
        "            keys = self.entities.get_component(eid, Keys)\n"
        "            if keys is not None:\n"
        "                keys.mask = 0",
        "        for _pid, eid in self.players.items():\n"
        "            keys = self.entities.get_component(eid, Keys)\n"
        "            if False:  # MUTATED\n"
        "                keys.mask = 0",
    ),
    (
        "the snapshot never carries door state",
        "server/game_world.py",
        "        return [\n"
        "            {'li': li, 'progress': int(round(d['progress'] * 255))}\n"
        "            for li, d in self.doors.items() if d['phase'] != 'closed'\n"
        "        ]",
        "        return []  # MUTATED",
    ),
    (
        "the player record drops the key mask",
        "shared/protocol.py",
        "            int(p.get(\"keys\", 0)) & 0xFF,",
        "            0,  # MUTATED",
    ),
]


def _run_subprocess(args):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, __file__] + args,
                          capture_output=True, text=True, encoding="utf-8",
                          env=env)


def run_mutations():
    print("baseline (unmutated)")
    res = _run_subprocess(["--scenarios"])
    if res.returncode != 0:
        print(res.stdout[-3000:])
        print(res.stderr[-2000:])
        print("BASELINE FAILS - fix the test before trusting any mutation result")
        sys.exit(1)
    print("  ok   baseline passes\n")

    survivors = []
    for name, path, old, new in MUTATIONS:
        original = io.open(path, encoding="utf-8").read()
        if old not in original:
            print(f"  SKIP {name}: anchor not found in {path}")
            survivors.append(f"{name} (anchor missing)")
            continue
        io.open(path, "w", encoding="utf-8", newline="").write(
            original.replace(old, new, 1))
        try:
            res = _run_subprocess(["--scenarios"])
            caught = res.returncode != 0
            if caught:
                print(f"  ok   caught: {name}")
            else:
                print(f"  FAIL survived: {name}")
                survivors.append(name)
        finally:
            io.open(path, "w", encoding="utf-8", newline="").write(original)

    print()
    res = _run_subprocess(["--scenarios"])
    if res.returncode != 0:
        print("WARNING: files not restored cleanly, baseline still fails")
        sys.exit(1)
    print("baseline restored and passing")

    if survivors:
        print(f"\nSURVIVED ({len(survivors)}): {survivors}")
        sys.exit(1)
    print(f"\nall {len(MUTATIONS)} mutations caught")


if __name__ == "__main__":
    if "--mutate" in sys.argv:
        run_mutations()
    else:
        run_scenarios()
