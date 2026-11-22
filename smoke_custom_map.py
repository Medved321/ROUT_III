"""Integration: custom map uploaded via CREATE_PARTY reaches the joined client.

Two paths are covered: the legacy tile map (runtime compatibility stays) and
the only player-facing editor path -- F5 in the sector editor, followed by P in
the lobby -- which must upload a sector map rather than a tile map.
"""
import asyncio
import os
import tempfile

from server.server import GameServer
from client.network_client import NetworkClient
from shared.map_data import GameMap
from shared.sector_map import MAP_FORMAT_SECTOR, SectorMap


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

    for client in (c1, c2, c3):
        client.disconnect()
    server.stop()
    # Drain the transports' connection_lost callbacks while the loop is alive.
    await asyncio.sleep(0.05)
    try:
        await task
    except asyncio.CancelledError:
        pass
    print("CUSTOM MAP TEST PASSED")


async def run_sector_editor_f5():
    """The single editor path: F5 -> play -> lobby P -> CREATE_PARTY(sector)."""
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    import pygame
    pygame.init()

    tmp = tempfile.mkdtemp()
    import client.settings as settings_mod
    settings_mod.CONFIG_PATH = os.path.join(tmp, "config.json")
    import client.sector_editor_screen as sector_mod
    from client import map_library
    sector_mod.MAPS_DIR = os.path.join(tmp, "maps")
    map_library.MAPS_DIR = sector_mod.MAPS_DIR
    from client.client import GameClient

    server = GameServer(host="127.0.0.1", port=7792)
    task = asyncio.ensure_future(server.start())
    await asyncio.sleep(0.3)
    client = None
    real_get = pygame.event.get
    try:
        client = GameClient("127.0.0.1", 7792, "Editor")
        client.running = True
        assert await client.network.connect("Editor")

        # F5 in the only editor returns "play" and stashes the sector map.
        client._ui = "editor"
        client.sector_editor_screen.map_name = "smoke_f5_sector"
        f5 = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F5, unicode="")
        pygame.event.get = lambda: [f5]
        await client._editor_loop()
        assert client._ui == "lobby", client._ui
        assert client.map_selection is not None, "F5 lost the sector map"
        assert client.map_selection.name == "smoke_f5_sector"
        raw = client.map_selection.payload
        assert not SectorMap.from_bytes(raw).validate()
        assert os.path.exists(os.path.join(sector_mod.MAPS_DIR,
                                           "smoke_f5_sector.mt1"))

        # P in the lobby must send that map with the sector format flag.
        create = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_p, unicode="p")
        pygame.event.get = lambda: [create]
        await client._lobby_loop()
        assert client.network.state == "game", client.network.state
        assert client.network.map_format == MAP_FORMAT_SECTOR, client.network.map_format
        assert client.network.map_data == raw, "the editor map was not uploaded"
        print("F5 in sector editor creates a party with the sector map OK")
    finally:
        pygame.event.get = real_get
        if client is not None:
            client.network.disconnect()
            client.renderer.cleanup()
        server.stop()
        # Let the UDP transports finish closing before asyncio.run tears the
        # loop down, otherwise their finalisers log "Event loop is closed".
        await asyncio.sleep(0.05)
        try:
            await task
        except asyncio.CancelledError:
            pass


async def main():
    await run()
    await run_sector_editor_f5()


if __name__ == "__main__":
    asyncio.run(main())
