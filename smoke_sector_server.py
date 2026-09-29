"""Smoke: sector map through the real server — validation, session, physics.

Covers tasks 2.2-2.3 of the advanced-sector-editor change:
- CREATE_PARTY with map_format=1 (sector JSON) is validated and delivered
- invalid sector map falls back to the default map
- GameWorld movement/LOS/hitscan work on a SectorMap via the common interface
"""
import asyncio
import math

from server.server import GameServer
from server.game_world import GameWorld
from server.game_data import ENEMY_DB as _server_enemy_db
from server.weapons import _ray_wall_dist
from client.network_client import NetworkClient
from shared.components import Position, Velocity
from shared.map_data import GameMap, TILE
from shared.sector_map import (MAP_FORMAT_SECTOR, LineDef, Sector, SectorMap,
                                SideDef, Thing, Vertex)


async def run():
    server = GameServer(host="127.0.0.1", port=7794)
    task = asyncio.ensure_future(server.start())
    await asyncio.sleep(0.3)

    sector_map = SectorMap.from_tiles(GameMap())
    raw = sector_map.to_bytes()
    assert sector_map.validate() == []

    # Creator uploads a sector map (w/h unused, format=1)
    c1 = NetworkClient("127.0.0.1", 7794)
    await c1.connect("Alice")
    c1.send_create_party("Sector", "pve", raw, 0, 0, MAP_FORMAT_SECTOR)
    await asyncio.sleep(0.5)
    assert c1.state == "game", c1.state
    assert c1.map_format == MAP_FORMAT_SECTOR, c1.map_format
    assert c1.map_data == raw, "sector map bytes were not preserved"
    received = SectorMap.from_bytes(c1.map_data)
    assert received.validate() == []
    assert len(received.linedefs) == len(sector_map.linedefs)
    print("sector map delivered to creator OK")

    # Joiner receives the same sector map
    c2 = NetworkClient("127.0.0.1", 7794)
    await c2.connect("Bob")
    c2.send_join_party(1)
    await asyncio.sleep(0.5)
    assert c2.state == "game", c2.state
    assert c2.map_format == MAP_FORMAT_SECTOR, c2.map_format
    assert c2.map_data == raw, "joiner got a different map"
    print("sector map delivered to joiner OK")

    # Invalid sector map (no player1) must fall back to the default map
    bad = sector_map.to_json()
    bad["things"] = []
    import json as _json
    bad_raw = _json.dumps(bad, separators=(",", ":")).encode()
    c3 = NetworkClient("127.0.0.1", 7794)
    await c3.connect("Carol")
    c3.send_create_party("BadSector", "pve", bad_raw, 0, 0, MAP_FORMAT_SECTOR)
    await asyncio.sleep(0.5)
    assert c3.state == "game", c3.state
    assert c3.map_format == 0, "invalid sector map was accepted"
    assert c3.map_width and c3.map_height, (c3.map_width, c3.map_height)
    print("invalid sector map rejected, default used OK")

    server.stop()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # --- GameWorld physics on a SectorMap (common GameMap interface) ------
    world = GameWorld(mode='pve', game_map=sector_map)
    assert len(world.spawn_points) >= 1
    assert all(0 <= x for x, _ in world.spawn_points)
    for sx, sy in world.spawn_points:
        assert sector_map.find_sector_at(sx, sy) >= 0, (sx, sy)

    # Player spawn is walkable
    eid = world.add_player(1, "Alice")
    from shared.components import Position
    pos = world.entities.get_component(eid, Position)
    assert world.game_map.can_move_px(pos.x, pos.y, 16), "spawn must be walkable"

    # Move towards the outer border wall -> blocked
    x0 = pos.x
    target_x = 16.0  # just inside the left border (verts at x=0)
    steps = 0
    while pos.x > target_x + 4 and steps < 500:
        world.game_map.can_move_px(pos.x - 4, pos.y, 16) or None
        if world.game_map.can_move_px(pos.x - 4, pos.y, 16):
            pos.x -= 4
        steps += 1
    assert pos.x >= TILE, f"clipped through wall: {pos.x}"
    print(f"wall collision OK (stopped at x={pos.x:.1f})")

    # Hitscan ray stops at the outer wall
    dist = _ray_wall_dist(world, pos.x, pos.y, math.pi, 2000.0)  # heading -x
    assert dist < 2000.0, "ray passed through the outer wall"
    print(f"hitscan blocked by wall OK (dist={dist:.1f})")

    # Enemy spawn + a full tick on the sector map
    world.spawn_enemies(4)
    assert len(world.enemies) >= 1
    for en in world.enemies:
        assert world.game_map.find_sector_at(en['x'], en['y']) >= 0, en
    for _ in range(30):
        world.update()
    print("sector game world ticks OK")

    # Enemies placed on the map spawn exactly once, at their coordinates
    sm2 = SectorMap.from_tiles(GameMap())
    pts = [(sx, sy) for sx, sy in sm2.spawn_px()]
    assert pts
    probe = [(pts[0][0], pts[0][1]),
             (pts[0][0] - 128, pts[0][1]),
             (pts[0][0], pts[0][1] + 128)]
    row = [t for t in probe if sm2.find_sector_at(t[0], t[1]) >= 0][:3]
    for i, (ex, ey) in enumerate(row):
        sm2.things.append(Thing(ex, ey, ["soldier", "imp", "hell_knight"][i],
                                angle=90))
    world2 = GameWorld(mode='pve', game_map=sm2)
    placed = [t for t in sm2.things if t.type in _server_enemy_db]
    assert placed, "test map must place enemies"
    n = world2.spawn_enemies_from_map()
    assert n == len(placed), (n, len(placed))
    by_pos = {(en['x'], en['y']) for en in world2.enemies}
    for t in placed:
        assert (t.x, t.y) in by_pos, (t.x, t.y)
    types = {en['t'] for en in world2.enemies}
    for t in placed:
        assert t.type in types, t.type
    print(f"enemies from map things OK (spawned {n})")

    await _steps_and_solid_world()
    print("SECTOR SERVER TEST PASSED")


def _strip_map(floors, solid_edge=None, w=512, h=512):
    """Side-by-side rooms of equal height, one vertical edge per sector pair.

    ``floors`` gives one floor height per room; room i spans x in
    ``[i*w, (i+1)*w]`` and y in ``[0, h]``. ``solid_edge`` marks that shared
    edge as a solid two-sided linedef.
    """
    sm = SectorMap()
    for fh in floors:
        sm.sectors.append(Sector(floor_h=fh, ceil_h=256))
    sides = []
    for si in range(len(floors)):
        sides.append(len(sm.sidedefs))
        sm.sidedefs.append(SideDef(sector=si))

    def gid(px: float, py: float) -> int:
        for idx, v in enumerate(sm.verts):
            if v.x == px and v.y == py:
                return idx
        sm.verts.append(Vertex(px, py))
        return len(sm.verts) - 1

    def edge(ax, ay, bx, by, front, back=-1, ref=None):
        # orient the linedef so the front sector sits on the expected side
        if ref is not None:
            cross = (bx - ax) * (ref[1] - ay) - (by - ay) * (ref[0] - ax)
            if cross >= 0:
                ax, ay, bx, by = bx, by, ax, ay
        sm.linedefs.append(LineDef(gid(ax, ay), gid(bx, by),
                                   front=front, back=back))

    n = len(floors)
    for i in range(n):
        x0, x1 = i * w, (i + 1) * w
        ref = ((x0 + x1) * 0.5, h * 0.5)
        edge(x0, 0, x1, 0, sides[i], ref=ref)      # bottom
        edge(x0, h, x1, h, sides[i], ref=ref)      # top
        if i == 0:
            edge(0, 0, 0, h, sides[i], ref=ref)    # left cap
        if i == n - 1:
            edge(x1, 0, x1, h, sides[i], ref=ref)  # right cap
    # shared edges, facing the sector on their left
    shared = []
    for i in range(n - 1):
        x = (i + 1) * w
        edge(x, 0, x, h, sides[i], sides[i + 1], ref=(i * w + w * 0.5, h * 0.5))
        shared.append(len(sm.linedefs) - 1)
    if solid_edge is not None:
        sm.linedefs[shared[solid_edge]].solid = True
    sm.things.append(Thing(256, 256))
    assert sm.validate() == [], sm.validate()
    return sm


def _drive(world, eid, dx, dy, ticks, radius=16.0):
    """Feed non-zero movement input into real simulation ticks."""
    pos = world.entities.get_component(eid, Position)
    vel = world.entities.get_component(eid, Velocity)
    for _ in range(ticks):
        vel.dx, vel.dy = dx, dy
        world.update()
    return pos


async def _steps_and_solid_world():
    """Steps, tall steps and solid walls under real GameWorld ticks."""
    # --- a 16-unit step is walkable both ways, a 32-unit step is not -------
    sm = _strip_map([0, 16, 48])
    world = GameWorld(mode='pve', game_map=sm)
    eid = world.add_player(1, "Stepper")
    pos = world.entities.get_component(eid, Position)
    vel = world.entities.get_component(eid, Velocity)
    start_x = pos.x
    assert start_x < 512, start_x

    pos = _drive(world, eid, 8.0, 0.0, 80)      # walk up the 16 step
    assert sm.find_sector_at(pos.x, pos.y) == 1, pos.x
    assert pos.x > 512 + 16, f"did not cross the 16 step: {pos.x}"
    assert pos.x < 1024, f"crossed the blocked 32 step: {pos.x}"
    assert sm.get_floor_at(pos.x, pos.y) == 16, sm.get_floor_at(pos.x, pos.y)

    # walking back down the 16 step works too
    pos = _drive(world, eid, -8.0, 0.0, 80)
    assert pos.x < 512, f"did not walk back down: {pos.x}"
    assert sm.get_floor_at(pos.x, pos.y) == 0

    # the 32 step stops the player, and they never end up off the floor
    pos = _drive(world, eid, 8.0, 0.0, 200)
    assert pos.x < 1024, f"crossed the 32 step: {pos.x}"
    sec = sm.find_sector_at(pos.x, pos.y)
    assert sec >= 0, pos.x
    assert sm.get_floor_at(pos.x, pos.y) == sm.sectors[sec].floor_h, sec
    print("  server steps OK (16 up/down, 32 blocked)")

    # --- a solid two-sided line blocks movement, sight and hitscan --------
    sm_wall = _strip_map([0, 0], solid_edge=0)
    w2 = GameWorld(mode='pve', game_map=sm_wall)
    e2 = w2.add_player(1, "Blocked")
    p2 = w2.entities.get_component(e2, Position)
    v2 = w2.entities.get_component(e2, Velocity)
    p2 = _drive(w2, e2, 8.0, 0.0, 120)
    assert p2.x < 512 + 16, f"solid wall did not stop the player: {p2.x}"
    assert not sm_wall.has_los_px(256, 256, 768, 256), "solid wall gave sight"
    ray = _ray_wall_dist(w2, 256, 256, 0.0, 2000.0)     # heading +x
    assert ray < 2000.0, "hitscan passed through the solid wall"

    # without the flag the same line is a plain opening again
    sm_open = _strip_map([0, 0])
    w3 = GameWorld(mode='pve', game_map=sm_open)
    e3 = w3.add_player(1, "Open")
    p3 = _drive(w3, e3, 8.0, 0.0, 120)
    assert p3.x > 512 + 16, f"open edge blocked the player: {p3.x}"
    assert sm_open.has_los_px(256, 256, 768, 256)
    assert _ray_wall_dist(w3, 256, 256, 0.0, 2000.0) > 500.0

    # a hitscan crosses a walkable step without stopping on it
    sm_step = _strip_map([0, 16])
    w4 = GameWorld(mode='pve', game_map=sm_step)
    e4 = w4.add_player(1, "Shooter")
    step_ray = _ray_wall_dist(w4, 256, 256, 0.0, 2000.0)
    assert step_ray > 500.0, f"hitscan stopped on a walkable step: {step_ray}"
    print("  server solid wall + hitscan OK")


if __name__ == "__main__":
    asyncio.run(run())
