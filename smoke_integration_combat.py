"""End-to-end integration: real server + 2 network clients over UDP."""
import asyncio

from server.server import GameServer
from client.network_client import NetworkClient


async def main():
    server = GameServer(host="127.0.0.1", port=7789)
    task = asyncio.ensure_future(server.start())
    await asyncio.sleep(0.3)

    c1 = NetworkClient("127.0.0.1", 7789)
    c2 = NetworkClient("127.0.0.1", 7789)
    ok1 = await c1.connect("Alice")
    ok2 = await c2.connect("Bob")
    assert ok1 and ok2, "connect failed"
    assert c1.state == "lobby" and c2.state == "lobby", (c1.state, c2.state)

    # Alice creates a pve party -> both auto-enter game
    c1.send_create_party("A's party", "pve")
    await asyncio.sleep(0.4)
    assert c1.state == "game", c1.state
    assert c2.state == "lobby", c2.state  # Bob must join explicitly
    c2.send_join_party(1)
    await asyncio.sleep(0.4)
    assert c2.state == "game", c2.state

    # send inputs + wait for several world states
    for _ in range(6):
        c1.send_input(1, 0, 0.01, True, 2)
        c2.send_input(-1, 1, -0.01, False, 1)
        await asyncio.sleep(0.1)

    await asyncio.sleep(0.3)
    assert c1.latest_state is not None
    (tick, players, enemies, bullets, corpses, mode, impacts, items,
     _doors) = c1.latest_state
    print(f"tick={tick} players={len(players)} enemies={len(enemies)} "
          f"bullets={len(bullets)} corpses={len(corpses)} mode={mode} "
          f"impacts={len(impacts)} items={len(items)}")
    assert len(players) == 2, players
    assert mode == 'pve', mode
    assert enemies, "no enemies spawned"
    ids = {p['id'] for p in players}
    assert ids == {c1.player_id, c2.player_id}, ids

    # weapon switching reaches the server
    c1.send_input(0, 0, 0.0, True, 4)
    await asyncio.sleep(0.3)
    for p in c1.latest_state[1]:
        if p['id'] == c1.player_id:
            assert p['wpn'] == 4, p
            break

    # leave party -> back to lobby
    c1.send_leave_party()
    await asyncio.sleep(0.3)
    assert c1.state == "lobby", c1.state

    server.stop()
    try:
        await task
    except asyncio.CancelledError:
        pass
    print("INTEGRATION TEST PASSED")


def test_pvp():
    from server.server import GameServer

    async def run():
        server = GameServer(host="127.0.0.1", port=7790)
        task = asyncio.ensure_future(server.start())
        await asyncio.sleep(0.2)

        c1 = NetworkClient("127.0.0.1", 7790)
        c2 = NetworkClient("127.0.0.1", 7790)
        await c1.connect("Alice")
        await c2.connect("Bob")
        c1.send_create_party("P", "pvp")
        await asyncio.sleep(0.2)
        assert c1.state == "game"
        c2.send_join_party(1)
        await asyncio.sleep(0.2)
        assert c2.state == "game"

        # PvP live flight: exercise movement + shooting + weapon switch without
# asserting damage (aiming is timing-sensitive; damage is covered by the
# deterministic test in smoke_combat).
        import math
        for _ in range(60):
            c2.send_input(1, 0, 0.1, True, 2)
            c1.send_input(-1, 0, -0.1, False, 1)
            await asyncio.sleep(0.03)
        await asyncio.sleep(0.2)

        # weapon switch reaches the server
        c2.send_input(0, 0, 0.0, False, 4)
        await asyncio.sleep(0.3)
        bob_wpn = None
        for p in c2.latest_state[1]:
            if p['id'] == c2.player_id:
                bob_wpn = p['wpn']
        assert bob_wpn == 4, bob_wpn
        assert c1.latest_state[5] == 'pvp'
        assert not c1.latest_state[2], c1.latest_state[2]

        server.stop()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())


def test_client_bridge():
    """Full render pass with the bridge under the dummy SDL driver."""
    import os
    os.environ['SDL_VIDEODRIVER'] = 'dummy'
    import pygame
    pygame.init()
    pygame.display.set_mode((1000, 700))

    from client.bridge import RenderBridge

    bridge = RenderBridge(pygame.Surface((1000, 700)))
    tiles = [
        [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
        [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
        [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
        [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
        [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
        [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
        [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
        [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
        [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
        [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
        [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
        [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
        [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
        [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
        [1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1],
        [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
    ]
    bridge.set_map(tiles, 16)

    enemies = [
        {'id': 1, 'x': 200.0, 'y': 200.0, 'angle': 0.0, 'type': 3, 'hp': 30, 'state': 0, 'd_flash': 2},
        {'id': 2, 'x': 300.0, 'y': 220.0, 'angle': 1.0, 'type': 0, 'hp': 0, 'state': 1, 'd_flash': 0},
    ]
    bullets = [{'x': 250.0, 'y': 250.0, 'col': (255, 0, 0)}]
    corpses = [{'x': 260.0, 'y': 260.0, 'type': 2}]
    players = [{'id': 2, 'x': 400.0, 'y': 300.0, 'angle': 1.5, 'name': 'Bob', 'wpn': 1, 'state': 0}]

    for _ in range(5):
        bridge.update(160.0, 160.0, 0.5, 200, players, enemies, bullets, corpses, 2,
                      {'pstl': 10, 'shl': 5, 'slg': 20, 'rck': 2, 'BFG': 1}, 3, 'pve')
        bridge.render()
    print("client bridge render OK")


if __name__ == '__main__':
    asyncio.run(main())
    test_pvp()
    test_client_bridge()
    print("ALL INTEGRATION TESTS PASSED")