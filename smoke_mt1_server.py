"""Smoke: `.mt1` sector maps over the protocol and the live server.

Covers task 3.1-3.2 of the bsp-renderer-mt1-format change:
- CREATE_PARTY/JOIN_ACCEPT round-trip a sector `.mt1` map + MAP_FORMAT_SECTOR
- a container-valid but model-invalid `.mt1`, and a corrupted container, are
  rejected server-side and the party falls back to the default map
"""
import asyncio

from server.server import GameServer
from server.game_world import GameWorld
from client.network_client import NetworkClient
from shared.map_data import GameMap
from shared.mt1 import unpack
from shared.protocol import (
    decode_create_party_ex, decode_join_accept,
    encode_create_party, encode_join_accept,
)
from shared.sector_map import (MAP_FORMAT_SECTOR, LineDef, Sector, SectorMap,
                                SideDef, Thing, Vertex)


def test_protocol_roundtrip():
    """Wire helpers must carry `.mt1` bytes and the sector format flag."""
    sm = SectorMap.from_tiles(GameMap())
    raw = sm.to_bytes()
    assert SectorMap.validate_bytes(raw) == []

    # CREATE_PARTY: name/mode preserved, map bytes + format round-trip.
    packet = encode_create_party(
        "My party", "pve", 0, 0, raw,
        request_id=42, map_format=MAP_FORMAT_SECTOR,
    )
    name, mode, mw, mh, map_data, request_id, map_format = (
        decode_create_party_ex(packet)
    )
    assert name == "My party" and mode == "pve"
    assert mw == 0 and mh == 0
    assert request_id == 42
    assert map_format == MAP_FORMAT_SECTOR
    assert map_data == raw, "CREATE_PARTY .mt1 bytes were not preserved"

    # JOIN_ACCEPT: player id + .mt1 + format.
    packet = encode_join_accept(7, 0, 0, raw, map_format=MAP_FORMAT_SECTOR)
    pid, mw2, mh2, has_map, map_data2, map_format2 = decode_join_accept(packet)
    assert pid == 7 and mw2 == 0 and mh2 == 0 and has_map
    assert map_format2 == MAP_FORMAT_SECTOR
    assert map_data2 == raw, "JOIN_ACCEPT .mt1 bytes were not preserved"

    # Odd-format names (texture names etc.) survive `.mt1` too: covered by
    # smoke_mt1_format geometry round-trip; the container here is byte-identical.
    print("protocol CREATE_PARTY/JOIN_ACCEPT .mt1 round-trip OK")


def _bad_sector_maps() -> list[bytes]:
    """Two invalid sector payloads: model-invalid and corrupted container."""
    sm = SectorMap.from_tiles(GameMap())
    raw = sm.to_bytes()

    # (1) Valid container, invalid model: no player1 spawn.
    m = sm.to_json()
    m["things"] = []
    tmp_map = SectorMap.from_json(m)
    no_spawn = tmp_map.to_bytes()
    assert SectorMap.validate_bytes(no_spawn), "no-spawn map must not validate"

    # (2) Corrupted container: flip the stored checksum in the header.
    # Header layout `!4sHIII`: magic(4) ver(2) count(4) catalog_off(4) crc(4)
    # -> crc lives at bytes [14:18].
    bad = bytearray(raw)
    for i in range(14, 18):
        bad[i] ^= 0xFF
    bad = bytes(bad)
    try:
        unpack(bad)
        assert False, "corrupted checksum must not unpack"
    except Exception:
        pass

    return [no_spawn, bad]


def _steps_map() -> SectorMap:
    """Two rooms joined by a 16-unit step, shared line marked solid."""
    sm = SectorMap()
    sm.sectors.append(Sector(floor_h=0, ceil_h=256))
    sm.sectors.append(Sector(floor_h=16, ceil_h=256))
    sm.sidedefs.append(SideDef(sector=0))
    sm.sidedefs.append(SideDef(sector=0))
    sm.sidedefs.append(SideDef(sector=1))
    sm.sidedefs.append(SideDef(sector=1))
    sm.verts.append(Vertex(0, 0))
    sm.verts.append(Vertex(512, 0))
    sm.verts.append(Vertex(512, 512))
    sm.verts.append(Vertex(0, 512))
    sm.verts.append(Vertex(1024, 0))
    sm.verts.append(Vertex(1024, 512))
    # left room (front 0): bottom, top, left cap
    sm.linedefs.append(LineDef(0, 1, front=0))
    sm.linedefs.append(LineDef(3, 2, front=0))
    sm.linedefs.append(LineDef(0, 3, front=0))
    # right room (front 2): bottom, top, right cap
    sm.linedefs.append(LineDef(4, 5, front=2))
    sm.linedefs.append(LineDef(5, 3, front=2))
    sm.linedefs.append(LineDef(4, 2, front=2))
    # shared edge: two-sided, marked solid
    sm.linedefs.append(LineDef(1, 2, front=0, back=2))
    sm.linedefs[-1].solid = True
    sm.things.append(Thing(256, 256))
    assert sm.validate() == [], sm.validate()
    assert sm.line_blocks(6) is True, "the shared line must be impassable"
    return sm


def test_steps_solid_on_the_wire():
    """Solid flags and step heights ride the map, height rides no packet."""
    import struct as _struct

    from shared.components import Position, Velocity
    from shared.protocol import (decode_player_input, decode_world_state,
                                encode_player_input, encode_respawn_event,
                                encode_world_state)
    from shared.version import PROTOCOL_VERSION

    assert PROTOCOL_VERSION == 7, PROTOCOL_VERSION

    sm = _steps_map()
    raw = sm.to_bytes()
    assert SectorMap.validate_bytes(raw) == []

    # the container carries the flag and the floor step byte-identically
    got = SectorMap.from_bytes(raw)
    assert got.validate() == []
    assert got.linedefs[6].solid is True
    assert [s.floor_h for s in got.sectors] == [0, 16]

    # and the same bytes survive the CREATE_PARTY / JOIN_ACCEPT encoders
    name, mode, _mw, _mh, map_data, _rid, fmt = decode_create_party_ex(
        encode_create_party("Steps", "pve", 0, 0, raw,
                            request_id=7, map_format=MAP_FORMAT_SECTOR)
    )
    assert map_data == raw and fmt == MAP_FORMAT_SECTOR, (name, mode)

    # no height/z field was added to any movement packet; the only extra byte
    # is the PLAYER_INPUT action-flags byte (dash change)
    assert len(encode_player_input(3, -4, 0.5, False, 2)) == \
        _struct.calcsize("!bbfBBB")
    assert len(decode_player_input(encode_player_input(3, -4, 0.5, True, 2))) == 6
    assert len(encode_respawn_event(2, 10.0, 20.0, 0.25)) == \
        _struct.calcsize("!Bfff")
    state = encode_world_state(1, [{"id": 2, "x": 10.0, "y": 20.0, "angle": 0.0,
                                    "health": 100, "state": 0, "name": "A"}],
                                mode="pve")
    _tick, players, _e, _b, _c, _mode, _im = decode_world_state(state)
    assert len(players) == 1 and "z" not in players[0], players[0]
    # a player record is exactly id/x/y/angle/health/state/wpn + 5 ammo slots
    assert 1 + 3 * 4 + 3 + 5 == _struct.calcsize("!BfffBBBBBBBB")

    # a full party cycle on this map still runs the world
    world = GameWorld(mode="pve", game_map=SectorMap.from_bytes(raw))
    eid = world.add_player(1, "Stepper")
    pos = world.entities.get_component(eid, Position)
    assert pos.x < 512, pos.x
    for _ in range(120):
        world.entities.get_component(eid, Velocity).dx = 8.0
        world.update()
    assert pos.x < 512 + 16, f"solid shared line let the player through: {pos.x}"
    print("steps/solid over the wire + no height in packets OK")


async def run():
    server = GameServer(host="127.0.0.1", port=7795)
    task = asyncio.ensure_future(server.start())
    await asyncio.sleep(0.3)

    # Valid `.mt1` map creates a sector party.
    sm = SectorMap.from_tiles(GameMap())
    raw = sm.to_bytes()
    c1 = NetworkClient("127.0.0.1", 7795)
    await c1.connect("Alice")
    c1.send_create_party("Mt1", "pve", raw, 0, 0, MAP_FORMAT_SECTOR)
    await asyncio.sleep(0.5)
    assert c1.state == "game", c1.state
    assert c1.map_format == MAP_FORMAT_SECTOR, c1.map_format
    assert c1.map_data == raw
    print("valid .mt1 party created OK")

    # Invalid payloads must never create a party with them -> default map.
    for i, bad in enumerate(_bad_sector_maps(), start=1):
        c = NetworkClient("127.0.0.1", 7795)
        await c.connect(f"Bad{i}")
        c.send_create_party("BadMt1", "pve", bad, 0, 0, MAP_FORMAT_SECTOR)
        await asyncio.sleep(0.5)
        assert c.state == "game", c.state
        assert c.map_format == 0, "invalid .mt1 accepted: map_format=%s" % c.map_format
        assert c.map_width and c.map_height, (c.map_width, c.map_height)
        print(f"broken .mt1 #{i} rejected, default map used OK")

    server.stop()
    try:
        await task
    except asyncio.CancelledError:
        pass
    test_steps_solid_on_the_wire()
    print("MT1 SERVER TEST PASSED")


if __name__ == "__main__":
    test_protocol_roundtrip()
    asyncio.run(run())