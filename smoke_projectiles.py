"""Smoke: RIFLE plasma ball is seen by both clients (PvE), damage on flight,
hit-scan impacts reach the second client, and impact particles are budgeted."""
import asyncio
import os

from server.server import GameServer
from client.network_client import NetworkClient


def _count_player_balls(state) -> int:
    if state is None:
        return 0
    return sum(1 for b in state[3] if b.get('from_player'))


async def run_live():
    server = GameServer(host="127.0.0.1", port=7791)
    task = asyncio.ensure_future(server.start())
    await asyncio.sleep(0.3)

    c1 = NetworkClient("127.0.0.1", 7791)
    c2 = NetworkClient("127.0.0.1", 7791)
    ok1 = await c1.connect("Alice")
    ok2 = await c2.connect("Bob")
    assert ok1 and ok2, "connect failed"
    c1.send_create_party("A", "pve")
    await asyncio.sleep(0.2)
    c2.send_join_party(1)
    await asyncio.sleep(0.3)
    assert c1.state == "game" and c2.state == "game"

    # 5.3: fire RIFLE (weapon 3) from c1; both clients must see the from_player ball
    #
    # The shot goes through the reliable FIRE intent, NOT through the shoot flag
    # of PLAYER_INPUT: since fire-intent-single-path the flag is display state
    # only and the server never shoots from it, so this loop used to assert on
    # balls that could not exist ("never saw a player ball") — a stale premise,
    # not a live defect.
    c1_balls = 0
    c2_balls = 0
    for _ in range(60):
        c1.send_fire(3)
        c1.send_input(0, 0, 0.0, True, 3)
        c2.send_input(0, 0, 0.0, False, 1)
        await asyncio.sleep(0.03)
        c1_balls += _count_player_balls(c1.latest_state)
        c2_balls += _count_player_balls(c2.latest_state)
    assert c1_balls > 0, "server/client A never saw a player ball"
    assert c2_balls > 0, "second client never saw the player ball"

    # 5.3 c: hit-scan (pistol, weapon 1) shots generate impact events on the wire;
    # enemies are present, so bullets hit walls/enemies and impacts reach clients.
    c1_imps = 0
    c2_imps = 0
    for _ in range(40):
        c1.send_fire(1)
        c1.send_input(0, 0, 0.0, True, 1)
        c2.send_input(0, 0, 0.0, False, 1)
        await asyncio.sleep(0.03)
        if c1.latest_state is not None:
            c1_imps += len(c1.latest_state[6])
        if c2.latest_state is not None:
            c2_imps += len(c2.latest_state[6])
    assert c1_imps > 0, "no impact events reached client A"
    assert c2_imps > 0, "no impact events reached client B"

    server.stop()
    try:
        await task
    except asyncio.CancelledError:
        pass
    print("live: player ball visible to 2nd client + impacts delivered OK")


def test_bridge_particles_and_render():
    """4.1/4.2/4.3: from_player ball renders an orb; impacts spawn capped particles."""
    os.environ['SDL_VIDEODRIVER'] = 'dummy'
    import pygame
    pygame.init()
    pygame.display.set_mode((1000, 700))
    from client.bridge import RenderBridge
    from engine.constants import MAX_PARTICLES
    import engine.game_state as gs

    surf = pygame.Surface((1000, 700))
    b = RenderBridge(surf)
    MAP = [[1] * 16] + [[1] + [0] * 14 + [1] for _ in range(14)] + [[1] * 16]
    b.set_map(MAP, 16)

    # frame without ball
    b.update(500.0, 500.0, 0.0, 200, [], [], [], [], 3,
             {'pstl': 10, 'shl': 10, 'slg': 10, 'rck': 10, 'BFG': 10}, 0, 'pve')
    b.render()
    empty = pygame.image.tostring(gs.screen, 'RGB')

    # frame with a from_player ball in front of the camera
    b.update(500.0, 500.0, 0.0, 200, [], [], [{'x': 560.0, 'y': 500.0, 'col': (40, 60, 40),
                                               'from_player': True}], [], 3,
             {'pstl': 10, 'shl': 10, 'slg': 10, 'rck': 10, 'BFG': 10}, 0, 'pve')
    b.render()
    with_ball = pygame.image.tostring(gs.screen, 'RGB')
    assert with_ball != empty, "frame with player ball identical to frame without"
    print("render: from_player ball changes the frame OK")

    # impacts spawn particles, respecting MAX_PARTICLES
    for _ in range(3):
        b.update(500.0, 500.0, 0.0, 200, [], [], [], [], 1,
                 {'pstl': 10, 'shl': 10, 'slg': 10, 'rck': 10, 'BFG': 10}, 0, 'pve',
                 [{'x': 540.0, 'y': 500.0, 'type': 'wall'},
                  {'x': 520.0, 'y': 480.0, 'type': 'enemy'}])
        b.render()
    assert len(gs.particles) > 0, "impact did not spawn particles"
    assert len(gs.particles) <= MAX_PARTICLES, len(gs.particles)
    print(f"render: impact particles spawned ({len(gs.particles)} <= {MAX_PARTICLES}) OK")

    pygame.quit()


def test_damage_on_flight():
    """Damage is applied on travel, not instantly (deterministic server sim)."""
    from server.game_world import GameWorld
    from server.weapons import handle_shoot, apply_weapon_switch
    from shared.components import Position, Rotation, WeaponComponent

    w = GameWorld(mode='pve')
    eid = w.add_player(1, 'A')
    p = w.entities.get_component(eid, Position); p.x, p.y = 300.0, 300.0
    r = w.entities.get_component(eid, Rotation); r.angle = 0.0
    wp = w.entities.get_component(eid, WeaponComponent)
    apply_weapon_switch(w, eid, 3)
    wp.ammo['slg'] = 10
    w.enemies.append({
        'id': 1, 'x': 400.0, 'y': 300.0, 't': 'imp', 'hp': 4, 'max_hp': 60,
        'spd': 0.0, 'col': (180, 130, 80), 'a': True, 'dying': 0,
        'anim_frame': 0, 'anim_state': 0, 'stun': 0, 'alert': True,
        'pa': 0.0, 'ls': 0, 'd_flash': 0, 'spawn_x': 400.0, 'spawn_y': 300.0,
    })
    handle_shoot(w, eid)
    assert w.enemies[0]['hp'] == 4, "instant damage"
    ticks = 0
    reached = False
    while ticks < 30:
        w.update()
        ticks += 1
        if not w.enemies or w.enemies[0]['hp'] <= 0:
            reached = True
            break
    assert reached, "ball never hit the enemy"
    # snapshot carries from_player flag for client render
    states = w.get_bullet_states()
    assert all('from_player' in b for b in states)
    print("damage on flight + from_player snapshot OK")


if __name__ == '__main__':
    asyncio.run(run_live())
    test_bridge_particles_and_render()
    test_damage_on_flight()
    print("PROJECTILES SMOKE PASSED")