"""Smoke: local playability of a sector `.mt1` map (single player).

Covers task 3.3 of the bsp-renderer-mt1-format change:
- a `.mt1` map from the editor (with BSP slices) is sent via CREATE_PARTY
- the client enters the party, spawns at the `player1` thing
- the player can move inside the sector rooms via game world input
"""
import asyncio
import math

from server.server import GameServer
from server.game_world import GameWorld
from client.network_client import NetworkClient
from shared.components import Position
from shared.sector_map import MAP_FORMAT_SECTOR, SectorMap
from client.sector_editor import new_room


def _editor_mt1() -> bytes:
    """A `.mt1` map as the editor would save it (new room + no BSP slices;
    the server re-builds BSP on load, the client caches it via ensure_bsp)."""
    sm = new_room(1200.0, 800.0)
    assert sm.validate() == []
    raw = sm.to_bytes()
    assert SectorMap.validate_bytes(raw) == []
    return raw


async def run():
    server = GameServer(host="127.0.0.1", port=7797)
    task = asyncio.ensure_future(server.start())
    await asyncio.sleep(0.3)

    raw = _editor_mt1()

    # One client creates a party on the `.mt1` map and enters the game.
    # pvp mode: no enemies, so movement is deterministic.
    c = NetworkClient("127.0.0.1", 7797)
    ok = await c.connect("Solo")
    assert ok, c.last_error
    c.send_create_party("Mt1Party", "pvp", raw, 0, 0, MAP_FORMAT_SECTOR)
    await asyncio.sleep(0.5)
    assert c.state == "game", c.state
    assert c.map_format == MAP_FORMAT_SECTOR
    assert c.map_data == raw
    print("client entered sector .mt1 party OK")

    # Server side: the party world runs on the delivered SectorMap.
    party = server.lobby.get_party(1)
    assert party is not None, "party was not created"
    world: GameWorld = party.world
    assert isinstance(world.game_map, SectorMap)
    spawn = world.spawn_points[0]
    # Editor room spawns the player at the room centre via `player1`.
    exp_x, exp_y = 600.0, 400.0
    assert math.isclose(spawn[0], exp_x, rel_tol=0.02), spawn
    assert math.isclose(spawn[1], exp_y, rel_tol=0.02), spawn
    print(f"spawn at player1 ({spawn[0]:.0f},{spawn[1]:.0f}) OK")

    # Movement: walk west; a wall stops the player at x ~= 16 (radius).
    members = list(party.members.values())
    assert len(members) == 1
    player_id = members[0].player_id
    eid = world.players[player_id]
    pos = world.entities.get_component(eid, Position)
    assert math.isclose(pos.x, exp_x, rel_tol=0.02), pos.x
    for _ in range(30):
        c.send_input(1, 0, math.pi, False)   # move_x forward, facing -x
        await asyncio.sleep(0.05)            # let the server tick ~3x
    assert pos.x < exp_x - 50, f"player did not move left: x={pos.x}"
    assert math.isclose(pos.y, exp_y, rel_tol=0.1), pos.y
    for _ in range(100):
        c.send_input(1, 0, math.pi, False)
        await asyncio.sleep(0.05)
    assert pos.x >= 16, f"player clipped through the west wall: x={pos.x}"
    print(f"movement + wall collision OK (stopped at x={pos.x:.0f})")

    server.stop()
    try:
        await task
    except asyncio.CancelledError:
        pass
    print("MT1 PARTY TEST PASSED")


if __name__ == "__main__":
    asyncio.run(run())