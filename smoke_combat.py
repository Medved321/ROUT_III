"""Round-trip smoke test for the new protocol + a minimal server run."""
import asyncio
import sys

from shared.protocol import (
    encode_player_input, decode_player_input,
    encode_world_state, decode_world_state,
    encode_create_party, decode_create_party,
    encode_lobby_state, decode_lobby_state,
    encode_join_accept, decode_join_accept,
)


def test_protocol_roundtrip():
    # player input with weapon_id
    data = encode_player_input(1, -1, 0.5, True, 4)
    mx, my, rot, sh, wid, fl = decode_player_input(data)
    assert (mx, my, rot, sh, wid, fl) == (1, -1, 0.5, True, 4, 0), \
        (mx, my, rot, sh, wid, fl)

    # player input with an action flag set: it must not disturb the other
    # fields (dash change)
    data = encode_player_input(0, 1, 0.25, True, 5, 0x01)
    mx, my, rot, sh, wid, fl = decode_player_input(data)
    assert (mx, my, rot, sh, wid, fl) == (0, 1, 0.25, True, 5, 0x01), \
        (mx, my, rot, sh, wid, fl)

    # world state with enemies, bullets, corpses, mode
    players = [{
        'id': 1, 'x': 32.0, 'y': 64.0, 'angle': 1.0,
        'health': 150, 'state': 2, 'wpn': 3, 'name': 'Bob',
        'ammo': {'pstl': 10, 'shl': 4, 'slg': 20, 'rck': 2, 'BFG': 1},
    }]
    enemies = [{'id': 7, 'x': 100.0, 'y': 200.0, 'angle': 0.5, 't': 'imp', 'hp': 45, 'state': 0}]
    bullets = [
        {'x': 50.0, 'y': 50.0, 'col': (255, 0, 0)},
        {'x': 60.0, 'y': 60.0, 'col': (40, 60, 40), 'from_player': True},
    ]
    corpses = [{'x': 90.0, 'y': 90.0, 't': 'soldier'}]
    impacts = [
        {'x': 120.0, 'y': 130.0, 'type': 'wall'},
        {'x': 140.0, 'y': 150.0, 'type': 'enemy'},
    ]
    data = encode_world_state(1234, players, enemies, bullets, corpses, 'pvp', impacts)
    tick, pl, en, bl, co, mode, im, it, _doors = decode_world_state(data)
    assert tick == 1234, tick
    assert mode == 'pvp', mode
    assert len(pl) == 1 and pl[0]['name'] == 'Bob' and pl[0]['wpn'] == 3
    assert pl[0]['ammo']['BFG'] == 1
    assert pl[0]['health'] == 150
    assert len(en) == 1 and en[0]['type'] == 3 and en[0]['hp'] == 45, en
    assert len(bl) == 2, bl
    assert bl[0]['col'] == (255, 0, 0) and bl[0]['from_player'] is False, bl[0]
    assert bl[1]['col'] == (40, 60, 40) and bl[1]['from_player'] is True, bl[1]
    assert len(co) == 1 and co[0]['type'] == 0, co
    assert len(im) == 2 and im[0]['type'] == 'wall' and im[1]['type'] == 'enemy', im

    # impacts round-trip with 0 events
    data0 = encode_world_state(1, [], [], [], [], 'pve')
    _t, _p, _e, _b, _c, _m, im0, _it, _d = decode_world_state(data0)
    assert im0 == [], im0

    # create party (pve / pvp)
    name, mode2, mw, mh, mdata = decode_create_party(encode_create_party("test", "pvp"))
    assert name == "test" and mode2 == "pvp" and mdata == b""
    from shared.map_data import GameMap
    m = GameMap(8, 8, [[1] * 8] + [[1] + [0] * 6 + [1] for _ in range(6)] + [[1] * 8])
    name3, mode3, mw3, mh3, mdata3 = decode_create_party(
        encode_create_party("custom", "pve", m.width, m.height, m.to_bytes())
    )
    assert name3 == "custom" and mode3 == "pve"
    assert mw3 == 8 and mh3 == 8 and len(mdata3) == 64
    assert GameMap.validate_network(mw3, mh3, mdata3) is not None
    assert GameMap.validate_network(0, 0, b"") is None
    assert GameMap.validate_network(8, 8, b"\x01" * 64) is None  # no floor
    assert name == 'test' and mode2 == 'pvp', (name, mode2)

    # lobby state
    parties = [{'id': 1, 'name': 'A', 'mode': 'pvp', 'players': [{'id': 1, 'name': 'X'}]}]
    pl2 = decode_lobby_state(encode_lobby_state(parties))
    assert pl2[0]['mode'] == 'pvp' and pl2[0]['players'][0]['name'] == 'X', pl2

    # join accept with map
    payload = encode_join_accept(5, 16, 16, bytes(256))
    pid, mw, mh, has_map, mdata, mfmt = decode_join_accept(payload)
    assert pid == 5 and has_map and len(mdata) == 256, (pid, mw, mh, has_map, len(mdata))
    assert mfmt == 0, mfmt

    # player_id is uint16 (PROTOCOL_VERSION 11): an id above the old byte
    # ceiling must round-trip and must not crash the encoder.  As a `!B` field,
    # join 256 raised struct.error in send_join_accept and killed the server.
    big = 300
    pid_big, *_ = decode_join_accept(encode_join_accept(big))
    assert pid_big == big, pid_big
    big_party = [{'id': 1, 'name': 'A', 'mode': 'pve',
                  'players': [{'id': big, 'name': 'X'}]}]
    assert decode_lobby_state(encode_lobby_state(big_party))[0]['players'][0]['id'] == big

    print("protocol roundtrip OK")


def test_malformed_datagram_is_dropped():
    """A datagram is untrusted: a valid header with a short payload used to
    raise ``struct.error``/``IndexError`` out of the transport callback.  The
    handler must drop it and stay silent.
    """
    from server.network import ServerNetwork
    from shared.protocol import Packet, PacketType
    net = ServerNetwork()
    addr = ("127.0.0.1", 5555)
    # HELLO with an empty payload: decode_hello(data[0]) would raise IndexError.
    net.datagram_received(Packet(PacketType.HELLO, 0, b"").encode(), addr)
    # Truncated / empty datagrams.
    net.datagram_received(b"\x01", addr)
    net.datagram_received(b"", addr)
    # PLAYER_INPUT with a short payload and no session: dropped cleanly.
    net.datagram_received(Packet(PacketType.PLAYER_INPUT, 0, b"\x00").encode(), addr)
    assert net.players == {} and net.client_info == {}
    print("malformed datagrams dropped OK")


def test_party_cap_is_enforced():
    """A flood of sessions (spoofed ports, each HELLO+JOIN before the timeout)
    must not turn into an unbounded number of worlds."""
    from server.server import GameServer, MAX_PARTIES
    from server.player import PlayerSession
    server = GameServer()
    for i in range(MAX_PARTIES):
        server.lobby.create_party(f"P{i}", "pve")
    refused = []
    server.network.send_join_fail = lambda session, reason: refused.append(reason)
    server.network.send_party_created = lambda *a, **k: None
    sess = PlayerSession(1000, "Flooder", ("10.0.0.2", 5))
    server._on_create_party(sess, "OneMore", "pve")
    assert refused, "create past the cap was not refused"
    assert len(server.lobby.parties) == MAX_PARTIES, len(server.lobby.parties)
    print("party cap enforced OK")


def test_pvp_damage():
    import math
    from server.game_world import GameWorld
    from server.weapons import handle_shoot, apply_weapon_switch
    from shared.components import Position, Rotation, Health, WeaponComponent

    w = GameWorld(mode='pvp')
    eid1 = w.add_player(1, 'A')
    eid2 = w.add_player(2, 'B')
    p1 = w.entities.get_component(eid1, Position); p1.x, p1.y = 300.0, 300.0
    r1 = w.entities.get_component(eid1, Rotation); r1.angle = math.radians(0)
    p2 = w.entities.get_component(eid2, Position); p2.x, p2.y = 360.0, 300.0
    apply_weapon_switch(w, eid1, 2)  # shotgun
    wp = w.entities.get_component(eid1, WeaponComponent)
    hp2 = w.entities.get_component(eid2, Health)
    # Damage is asserted on the lowest HP seen, not on the final one: a killed
    # player respawns immediately (see smoke_respawn), so the survivor teleports
    # back to its join point and stops taking pellets, ending the volley at full
    # HP with the damage already dealt.
    lowest = hp2.value
    for _ in range(5):
        handle_shoot(w, eid1)
        wp.fire_cooldown = 0
        w.update()
        lowest = min(lowest, hp2.value)
    assert lowest < 200, lowest
    print("pvp damage OK")


def test_world_tick():
    from server.game_world import GameWorld

    w = GameWorld(mode='pve')
    eid1 = w.add_player(1, 'A')
    eid2 = w.add_player(2, 'B')
    from shared.components import Velocity, Rotation, Position
    vel = w.entities.get_component(eid1, Velocity)
    pos = w.entities.get_component(eid1, Position)
    vel.dx = 5.0
    vel.dy = 0.0
    for _ in range(100):
        w.update()
    # player should still be inside the map
    assert 0 <= pos.x < 16 * 64 and 0 <= pos.y < 16 * 64, (pos.x, pos.y)

    # enemies spawn, update, hit player eventually
    w.spawn_enemies(4)
    assert len(w.enemies) == 4
    for _ in range(2000):
        w.update()
    # player position stays valid
    assert isinstance(w.get_all_player_states()[0]['wpn'], int)
    print("world tick OK")


def test_server_boot():
    from server.server import GameServer

    server = GameServer(host="127.0.0.1", port=7788)

    async def run():
        task = asyncio.ensure_future(server.start())
        await asyncio.sleep(0.3)
        server.stop()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    print("server boot OK")


def test_rifle_ball_spawns():
    """2.2: RIFLE (proj) spawns a from_player ball, no instant damage."""
    import math
    from server.game_world import GameWorld
    from server.weapons import handle_shoot, apply_weapon_switch
    from shared.components import Position, Rotation, Health, WeaponComponent

    w = GameWorld(mode='pve')
    eid = w.add_player(1, 'A')
    p = w.entities.get_component(eid, Position); p.x, p.y = 300.0, 300.0
    r = w.entities.get_component(eid, Rotation); r.angle = 0.0
    apply_weapon_switch(w, eid, 3)  # RIFLE
    wp = w.entities.get_component(eid, WeaponComponent)
    wp.ammo['slg'] = 10

    # enemy 200px ahead, alive
    w.enemies.append({
        'id': 1, 'x': 500.0, 'y': 300.0, 't': 'imp', 'hp': 60, 'max_hp': 60,
        'spd': 1.0, 'col': (180, 130, 80), 'a': True, 'dying': 0,
        'anim_frame': 0, 'anim_state': 0, 'stun': 0, 'alert': True,
        'pa': 0.0, 'ls': 0, 'd_flash': 0, 'spawn_x': 500.0, 'spawn_y': 300.0,
    })

    hp_before = w.enemies[0]['hp']
    handle_shoot(w, eid)
    balls = [b for b in w.bullets if b.get('from_player')]
    assert len(balls) == 1, w.bullets
    # spawned at the muzzle and moving straight ahead (+x), no instant damage
    assert balls[0]['x'] >= 300.0 and balls[0]['vx'] > 0 and balls[0]['dmg'] == 4, balls[0]
    # no instant damage
    assert w.enemies[0]['hp'] == hp_before, w.enemies[0]['hp']
    print("rifle ball spawn OK")


def test_ball_flies_damages_wall():
    """2.3: ball reaches enemy & kills; wall stops it; no instant damage."""
    import math
    from server.game_world import GameWorld
    from shared.components import Position, Rotation, WeaponComponent
    from server.weapons import handle_shoot, apply_weapon_switch
    from server.game_data import PLAYER_PROJ_SPEED

    # -- open map: ball reaches enemy, applies damage on hit --
    w = GameWorld(mode='pve')
    eid = w.add_player(1, 'A')
    p = w.entities.get_component(eid, Position); p.x, p.y = 300.0, 300.0
    r = w.entities.get_component(eid, Rotation); r.angle = 0.0
    wp = w.entities.get_component(eid, WeaponComponent)
    apply_weapon_switch(w, eid, 3)
    wp.ammo['slg'] = 10

    def imp_at(hp):
        return {
            'id': 1, 'x': 400.0, 'y': 300.0, 't': 'imp', 'hp': hp, 'max_hp': 60,
            'spd': 0.0, 'col': (180, 130, 80), 'a': True, 'dying': 0,
            'anim_frame': 0, 'anim_state': 0, 'stun': 0, 'alert': True,
            'pa': 0.0, 'ls': 0, 'd_flash': 0,
            'spawn_x': 400.0, 'spawn_y': 300.0,
        }

    # A surviving target, so the damage the ball dealt can actually be read:
    # a lethal hit and the immediate respawn both land inside one update().
    w.enemies.append(imp_at(60))
    handle_shoot(w, eid)
    assert w.enemies[0]['hp'] == 60, "instant damage should not happen"
    ticks = 0
    while ticks < 30 and w.enemies[0]['hp'] >= 60:
        w.update()
        ticks += 1
    assert ticks < 30 and w.enemies[0]['hp'] < 60, (
        "ball never reached enemy (enemy survived/dissipated)", ticks)

    # A lethal hit: the enemy dies and STAYS dead -- it has no timer of its own,
    # so nothing but a player's death can start the wave over.  The kill is
    # observable as hp == 0 for as long as the player lives, which is exactly
    # what used to be impossible to see (the old one-tick timer revived it
    # inside the same update() the ball landed in).
    wk = GameWorld(mode='pve')
    ek = wk.add_player(1, 'A')
    pk = wk.entities.get_component(ek, Position); pk.x, pk.y = 300.0, 300.0
    wk.entities.get_component(ek, Rotation).angle = 0.0
    wpk = wk.entities.get_component(ek, WeaponComponent)
    apply_weapon_switch(wk, ek, 3)
    wpk.ammo['slg'] = 10
    wk.enemies.append(imp_at(4))
    assert handle_shoot(wk, ek).fired
    assert wk.enemies[0]['hp'] == 4, "instant damage should not happen"
    killed = False
    for _ in range(30):
        wk.update()
        if wk.enemies[0]['hp'] == 0:
            killed = True
            break
    assert killed, "ball never reached enemy (enemy survived)"
    for _ in range(30):
        wk.update()
    down = wk.enemies[0]
    assert down['hp'] == 0 and down['a'] is False, "a dead enemy revived itself"
    assert (down['x'], down['y']) == (400.0, 300.0), down

    # ...and a player's respawn is the one thing that does bring it back.
    wk.damage_player(ek, 1000)
    wk.respawn_player(1)
    wk.update()
    back = wk.enemies[0]
    assert back['hp'] == 60 and back['a'] is True, back
    assert (back['x'], back['y']) == (400.0, 300.0), back

    # -- wall: ball dissipates, no damage --
    from shared.map_data import GameMap
    m = GameMap(16, 16, [[1]*16] + [[1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1] for _ in range(14)] + [[1]*16])
    w2 = GameWorld(mode='pve', game_map=m)
    eid2 = w2.add_player(2, 'B')
    p2 = w2.entities.get_component(eid2, Position); p2.x, p2.y = 160.0, 160.0
    r2 = w2.entities.get_component(eid2, Rotation); r2.angle = 0.0  # toward +x wall at x=832
    wp2 = w2.entities.get_component(eid2, WeaponComponent)
    apply_weapon_switch(w2, eid2, 3)
    wp2.ammo['slg'] = 10
    handle_shoot(w2, eid2)
    survived_ticks = 0
    while survived_ticks < 40 and w2.bullets:
        w2.update()
        survived_ticks += 1
    assert not w2.bullets, "ball should stop against the wall"
    assert survived_ticks < 40
    print("ball damage-on-flight + wall stop OK")


def test_impacts_wall():
    """3.2 + 3.1: hitscan pistol shot at a wall emits a wall impact in ray point."""
    import math
    from server.game_world import GameWorld
    from server.weapons import handle_shoot, apply_weapon_switch
    from shared.components import Position, Rotation, WeaponComponent
    from shared.map_data import GameMap

    m = GameMap(16, 16, [[1]*16] + [[1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1] for _ in range(14)] + [[1]*16])
    w = GameWorld(mode='pve', game_map=m)
    eid = w.add_player(1, 'A')
    p = w.entities.get_component(eid, Position); p.x, p.y = 160.0, 160.0
    r = w.entities.get_component(eid, Rotation); r.angle = 0.0  # toward +x wall x=832
    # Pistol (index 1) is hitscan
    wp = w.entities.get_component(eid, WeaponComponent)
    apply_weapon_switch(w, eid, 1)
    wp.ammo['pstl'] = 10
    handle_shoot(w, eid)
    walls = [im for im in w.impacts if im['type'] == 'wall']
    assert len(walls) == 1, w.impacts
    assert len(w.impacts) <= 64, len(w.impacts)
    # impact must be ON the wall point along the ray (x ~832, y~160)
    im = walls[0]
    assert abs(im['y'] - 160.0) < 10 and im['x'] > 800.0, im
    # TTL cleanup: impact expires after IMPACT_TTL+ ticks
    for _ in range(16):
        w.update()
    assert not w.impacts, "impacts should expire by TTL"
    print("hit-scan wall impact + TTL OK")


def test_enemy_turn_is_rate_limited():
    """An enemy body pivots at ENEMY_TURN_RATE; it must not snap to the bearing."""
    import math
    from server.game_world import GameWorld, ENEMY_TURN_RATE, _turn_toward
    from server.game_data import TICK_INTERVAL
    from shared.components import Position
    from shared.map_data import GameMap

    m = GameMap(16, 16, [[1]*16] + [[1] + [0]*14 + [1] for _ in range(14)] + [[1]*16])
    w = GameWorld(mode='pve', game_map=m)
    eid = w.add_player(1, 'A')
    p = w.entities.get_component(eid, Position); p.x, p.y = 160.0, 160.0
    en = {
        'id': 1, 'x': 320.0, 'y': 160.0, 't': 'imp',
        'hp': 60, 'max_hp': 60, 'spd': 0.0, 'col': (1, 2, 3), 'a': True,
        'dying': 0, 'anim_frame': 0, 'anim_state': 0, 'stun': 0, 'alert': False,
        'pa': 0.0, 'ls': 0, 'd_flash': 0,
        'spawn_x': 320.0, 'spawn_y': 160.0, 'spawn_pa': 0.0,
        'attack_from': None, 'attack_ticks': 0,
    }
    w.enemies.append(en)
    bearing = math.pi                    # player is straight to the enemy's left
    step = ENEMY_TURN_RATE * TICK_INTERVAL

    w.update()
    moved = math.hypot(math.cos(en['pa']) - 1.0, math.sin(en['pa']))
    assert abs(moved - 2 * math.sin(step / 2)) < 1e-9, (en['pa'], step)
    assert abs((en['pa'] - bearing + math.pi) % (2 * math.pi) - math.pi) > 1.0, \
        "must not snap to the bearing in one tick"
    # Turn until the shortest arc (pi radians) is covered, with margin: the
    # tick budget must follow ENEMY_TURN_RATE, not a hardcoded frame count.
    for _ in range(int(math.ceil(math.pi / step)) + 5):
        w.update()
    err = (en['pa'] - bearing + math.pi) % (2 * math.pi) - math.pi
    assert abs(err) < 1e-6, en['pa']
    # takes the shortest way around the -pi/pi seam
    assert _turn_toward(3.0, -3.0, 0.1) > 3.0
    print("enemy turn is rate-limited OK")


if __name__ == '__main__':
    test_protocol_roundtrip()
    test_malformed_datagram_is_dropped()
    test_party_cap_is_enforced()
    test_world_tick()
    test_pvp_damage()
    test_server_boot()
    test_rifle_ball_spawns()
    test_ball_flies_damages_wall()
    test_impacts_wall()
    test_enemy_turn_is_rate_limited()
    print("ALL SMOKE TESTS PASSED")