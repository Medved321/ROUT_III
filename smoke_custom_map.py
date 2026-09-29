"""Integration: custom map uploaded via CREATE_PARTY reaches the joined client."""
import asyncio

from server.server import GameServer
from client.network_client import NetworkClient
from shared.map_data import GameMap


def make_map(w=10, h=10, fill=0):
    tiles = [[1] * w]
    for _ in range(h - 2):
        tiles.append([1] + [fill] * (w - 2) + [1])
    tiles.append([1] * w)
    tiles[3][3] = 2  # a stone pillar so the map isn't trivial
    return GameMap(w, h, tiles)


async def run():
    server = GameServer(host="127.0.0.1", port=7791)
    task = asyncio.ensure_future(server.start())
    await asyncio.sleep(0.3)

    custom = make_map(10, 10)
    raw = custom.to_bytes()

    c1 = NetworkClient("127.0.0.1", 7791)
    await c1.connect("Alice")
    c1.send_create_party("Custom", "pve", raw, custom.width, custom.height)
    await asyncio.sleep(0.4)
    assert c1.state == "game", c1.state
    assert c1.map_width == 10 and c1.map_height == 10, (c1.map_width, c1.map_height)
    assert c1.map_data == raw, "custom map bytes were not preserved"
    received = GameMap.from_bytes(c1.map_data, c1.map_width, c1.map_height)
    assert received.tiles == custom.tiles, "custom map tiles mismatch"
    print("custom map delivered to creator OK")

    # second client joins and must receive the same custom map
    c2 = NetworkClient("127.0.0.1", 7791)
    await c2.connect("Bob")
    c2.send_join_party(1)
    await asyncio.sleep(0.4)
    assert c2.state == "game", c2.state
    assert c2.map_data == raw, "joiner got a different map"
    print("custom map delivered to joiner OK")

    # invalid map (too small / no floor) must fall back to the default map
    c3 = NetworkClient("127.0.0.1", 7791)
    await c3.connect("Carol")
    c3.send_create_party("Bad", "pve", b"\x01" * 64, 8, 8)
    await asyncio.sleep(0.4)
    assert c3.state == "game", c3.state
    assert not (c3.map_width == 8 and c3.map_height == 8), "invalid map was accepted"
    assert c3.map_width and c3.map_height, (c3.map_width, c3.map_height)
    print("invalid map rejected, default used OK")

    server.stop()
    try:
        await task
    except asyncio.CancelledError:
        pass
    print("CUSTOM MAP TEST PASSED")


if __name__ == "__main__":
    asyncio.run(run())
