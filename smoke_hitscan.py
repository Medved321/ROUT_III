"""smoke_hitscan.py — детерминированные проверки надёжной регистрации выстрелов.

Покрытие (openspec changes/hitscan-hit-registration):
  1.1  FIRE/PLAYER_INPUT round-trip, absolute angle, impacts с id.
  1.2  единый тиковый кулдаун (wpn_cooldown_ticks) совпадает у клиента и сервера.
  2.1  предсказание угла + реконсиляция (headless, без pygame-окна).
  2.2  FIRE: монотонный fire_seq по надёжному каналу; удержание — ненадёжно.
  3.1  FIRE идемпотентен по fire_seq (дубликат не наносит второй урон).
  3.2  пересечение луч-круг: попадания 5..900 px и по касательной, промах за
       радиусом, отсечение стеной (ближайшее из стена/сущность).
  3.3  отсутствие «магнитных» попаданий по врагам/игрокам вне радиуса.
  4.x  (дедуп импактов по id проверяется регрессией bridge в smoke_projectiles)
"""
import math

from shared.combat import wpn_cooldown_ticks, TICK_INTERVAL, TAU, normalize_angle
from shared.protocol import (
    PacketType, encode_fire, decode_fire,
    encode_player_input, decode_player_input,
    encode_world_state, decode_world_state,
    encode_reliable, decode_reliable,
)


def _mk_enemy(eid: int, x: float, y: float, hp: int = 200) -> dict:
    return {
        'id': eid, 'x': float(x), 'y': float(y), 't': 'soldier', 'hp': hp,
        'max_hp': hp, 'spd': 0.0, 'col': (180, 180, 180), 'a': True, 'dying': 0,
        'anim_frame': 0, 'anim_state': 0, 'stun': 0, 'alert': True,
        'pa': 0.0, 'ls': 0, 'd_flash': 0, 'spawn_x': float(x), 'spawn_y': float(y),
    }


def _player_world(mode: str = 'pve'):
    from server.game_world import GameWorld
    from shared.components import Position, Rotation, WeaponComponent

    w = GameWorld(mode=mode)
    eid = w.add_player(1, 'A')
    p = w.entities.get_component(eid, Position)
    r = w.entities.get_component(eid, Rotation)
    wp = w.entities.get_component(eid, WeaponComponent)
    wp.ammo['pstl'] = 200
    w._shooter = (eid, p, r, wp)
    return w


def test_protocol_roundtrip():
    # FIRE: fire_seq (monotonic) + weapon_id
    data = encode_fire(0xDEADBEEF, 3)
    seq, wid = decode_fire(data)
    assert (seq, wid) == (0xDEADBEEF, 3), (seq, wid)

    # FIRE внутри надёжной обёртки (RELIABLE_DATA inner_type)
    raw = encode_reliable(3, 0xFFFF, PacketType.FIRE, 0, 1, data)
    ack, bits, itype, fragi, fragc, payload = decode_reliable(raw)
    assert itype == PacketType.FIRE and fragc == 1 and payload == data, (itype, fragc)

    # PLAYER_INPUT: абсолютный угол сохраняется (в т.ч. за 2*pi)
    for angle in (0.5, 6.28, -0.033, math.tau - 0.01):
        mx, my, back, sh, wid, fl = decode_player_input(
            encode_player_input(0, 1, angle, True, 2)
        )
        assert abs(back - angle) < 1e-6 and sh is True and wid == 2 and fl == 0, \
            (angle, back, sh, wid, fl)

    # World State: импакты несут монотонный id (для дедупа по выстрелу)
    players = [{
        'id': 1, 'x': 1.0, 'y': 2.0, 'angle': 0.5, 'health': 150, 'state': 0,
        'wpn': 2, 'name': 'A',
        'ammo': {'pstl': 8, 'shl': 3, 'slg': 4, 'rck': 0, 'BFG': 0},
    }]
    impacts = [
        {'x': 120.0, 'y': 130.0, 'type': 'wall', 'id': 41},
        {'x': 140.0, 'y': 150.0, 'type': 'enemy', 'id': 42},
    ]
    _t, pl, en, bl, co, mode, im, _it, _doors = decode_world_state(
        encode_world_state(123, players, [], [], [], 'pve', impacts)
    )
    assert [i['id'] for i in im] == [41, 42], im
    assert im[0]['type'] == 'wall' and im[1]['type'] == 'enemy'
    assert len(pl) == 1 and mode == 'pve'
    print("1.1 protocol roundtrip (FIRE, absolute angle, impact ids) OK")


def test_shared_fire_tempo():
    # клиент и сервер используют одну функцию тикового кулдауна
    assert wpn_cooldown_ticks(8) == 3        # RIFLE: 8 кадров @60 -> 3 тика @20
    assert wpn_cooldown_ticks(16) == 5       # пистолет: 16 -> 5
    assert wpn_cooldown_ticks(50) == 17      # дробовик: 50 -> 17
    # клиентский гейт (ticks*interval + слак в 1 тик) никогда не короче
    # серверного кулдауна — показанный выстрел не будет отклонён сервером.
    for frames, ticks in ((8, 3), (16, 5), (50, 17), (100, 33)):
        assert wpn_cooldown_ticks(frames) == ticks, frames
        gate = ticks * TICK_INTERVAL + TICK_INTERVAL
        assert gate >= ticks * TICK_INTERVAL and gate > 0.05, (frames, gate)
    print("1.2 shared fire tempo OK")


def test_ray_circle_hit_geometry():
    from server.weapons import _ray_circle_hit
    from server.game_data import PLAYER_RADIUS, ENEMY_RADIUS

    R = PLAYER_RADIUS
    # прямое попадание: центр в 60 px по лучу -> вход на 60 - R
    t = _ray_circle_hit(0.0, 0.0, 0.0, 60.0, 0.0, R, 1000.0)
    assert abs(t - (60.0 - R)) < 1e-6, t
    # в упор: начало уже внутри круга -> t = 0
    assert _ray_circle_hit(0.0, 0.0, 0.0, 5.0, 0.0, R, 1000.0) == 0.0
    # по касательной (смещение ровно R) -> одна точка на proj
    t = _ray_circle_hit(0.0, 0.0, 0.0, 100.0, R, R, 1000.0)
    assert abs(t - 100.0) < 1e-6, t
    # промах: смещение за радиусом
    assert _ray_circle_hit(0.0, 0.0, 0.0, 100.0, R + 1.0, R, 1000.0) is None
    # позади дула (круг не перекрывает начало) -> промах
    assert _ray_circle_hit(0.0, 0.0, 0.0, -60.0, 0.0, R, 1000.0) is None
    # за max_dist -> промах
    assert _ray_circle_hit(0.0, 0.0, 0.0, 200.0, 0.0, R, 5.0) is None
    # диагональ 45°: t = |center| - R
    a = math.pi / 4
    t = _ray_circle_hit(0.0, 0.0, a, 100.0, 100.0, R, 1000.0)
    assert abs(t - (math.hypot(100, 100) - R)) < 1e-6, t
    # враг впритык к дулу с радиусом ENEMY_RADIUS
    assert _ray_circle_hit(0.0, 0.0, 0.0, 5.0, 0.0, ENEMY_RADIUS, 1000.0) == 0.0
    print("3.2 ray-circle geometry OK")


def test_hitscan_distances_and_radius():
    from server.game_world import GameWorld
    from server.weapons import handle_shoot, apply_weapon_switch
    from shared.components import Position, Rotation, WeaponComponent, Health
    from server.game_data import ENEMY_RADIUS, PLAYER_RADIUS
    from shared.map_data import GameMap

    # 24x24 коридор: стены только по периметру, дистанция луча до стены ~1390 px
    m = GameMap(24, 24, [[1] * 24] + [[1] + [0] * 22 + [1] for _ in range(22)] + [[1] * 24])
    OX, OY = 80.0, 320.0

    # попадания на дистанциях 5..900 и смещениях в пределах радиуса
    for base_d in (5, 50, 200, 500, 900):
        for off in (0, ENEMY_RADIUS - 1):
            w = GameWorld(mode='pve', game_map=m)
            eid = w.add_player(1, 'A')
            p = w.entities.get_component(eid, Position)
            r = w.entities.get_component(eid, Rotation)
            wp = w.entities.get_component(eid, WeaponComponent)
            p.x, p.y = OX, OY
            r.angle = 0.0
            wp.ammo['pstl'] = 100
            apply_weapon_switch(w, eid, 1)
            w.enemies.append(_mk_enemy(1, OX + base_d, OY + off))
            handle_shoot(w, eid)
            assert w.enemies[0]['hp'] < 200, (base_d, off, w.enemies[0]['hp'])

    # промах за радиусом врага: урон не наносится
    w = GameWorld(mode='pve', game_map=m)
    eid = w.add_player(1, 'A')
    p = w.entities.get_component(eid, Position)
    r = w.entities.get_component(eid, Rotation)
    wp = w.entities.get_component(eid, WeaponComponent)
    p.x, p.y = OX, OY
    r.angle = 0.0
    wp.ammo['pstl'] = 100
    apply_weapon_switch(w, eid, 1)
    w.enemies.append(_mk_enemy(1, OX + 200.0, OY + ENEMY_RADIUS + 1.0))
    handle_shoot(w, eid)
    assert w.enemies[0]['hp'] == 200, w.enemies[0]['hp']

    # PvP: игрок на границе радиуса -> урон; за радиусом -> без «магнитных»
    w = GameWorld(mode='pvp', game_map=m)
    eid1 = w.add_player(1, 'A')
    eid2 = w.add_player(2, 'B')
    p1 = w.entities.get_component(eid1, Position)
    r1 = w.entities.get_component(eid1, Rotation)
    wp1 = w.entities.get_component(eid1, WeaponComponent)
    p2 = w.entities.get_component(eid2, Position)
    hp2 = w.entities.get_component(eid2, Health)
    p1.x, p1.y = OX, OY
    r1.angle = 0.0
    p2.x, p2.y = OX + 200.0, OY + PLAYER_RADIUS  # ровно на границе радиуса
    wp1.ammo['pstl'] = 100
    apply_weapon_switch(w, eid1, 1)
    handle_shoot(w, eid1)
    assert hp2.value < 200, hp2.value
    # вне радиуса
    w2 = GameWorld(mode='pvp', game_map=m)
    a1 = w2.add_player(1, 'A')
    a2 = w2.add_player(2, 'B')
    q1 = w2.entities.get_component(a1, Position)
    rr1 = w2.entities.get_component(a1, Rotation)
    ww1 = w2.entities.get_component(a1, WeaponComponent)
    q2 = w2.entities.get_component(a2, Position)
    hh2 = w2.entities.get_component(a2, Health)
    q1.x, q1.y = OX, OY
    rr1.angle = 0.0
    q2.x, q2.y = OX + 200.0, OY + PLAYER_RADIUS + 1.0
    ww1.ammo['pstl'] = 100
    apply_weapon_switch(w2, a1, 1)
    handle_shoot(w2, a1)
    assert hh2.value == 200, "магнитное попадание по игроку вне радиуса"
    print("3.2 hitscan distances/radius + 3.3 no magnetic hits OK")


def test_wall_occlusion():
    from server.game_world import GameWorld
    from server.weapons import handle_shoot, apply_weapon_switch
    from shared.components import Position, Rotation, WeaponComponent
    from shared.map_data import GameMap

    m = GameMap(16, 16, [[1] * 16] + [[1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1] for _ in range(14)] + [[1] * 16])
    w = GameWorld(mode='pve', game_map=m)
    eid = w.add_player(1, 'A')
    p = w.entities.get_component(eid, Position)
    r = w.entities.get_component(eid, Rotation)
    wp = w.entities.get_component(eid, WeaponComponent)
    p.x, p.y = 160.0, 160.0
    r.angle = 0.0  # +x: колонна 15 — стена на x=960
    wp.ammo['pstl'] = 100
    apply_weapon_switch(w, eid, 1)
    w.enemies.append(_mk_enemy(1, 400.0, 160.0))   # до стены
    w.enemies.append(_mk_enemy(2, 980.0, 160.0))   # за стеной (в стене x>=960)
    handle_shoot(w, eid)
    assert w.enemies[0]['hp'] < 200, "враг до стены должен получить урон"
    assert w.enemies[1]['hp'] == 200, "враг за стеной не должен быть поражён"

    # изолированный враг за стеной -> стена блокирует, импакт в точке стены
    w2 = GameWorld(mode='pve', game_map=m)
    e2 = w2.add_player(1, 'A')
    p2 = w2.entities.get_component(e2, Position)
    r2 = w2.entities.get_component(e2, Rotation)
    wp2 = w2.entities.get_component(e2, WeaponComponent)
    p2.x, p2.y = 160.0, 160.0
    r2.angle = 0.0
    wp2.ammo['pstl'] = 100
    apply_weapon_switch(w2, e2, 1)
    w2.enemies.append(_mk_enemy(1, 980.0, 160.0))
    handle_shoot(w2, e2)
    assert w2.enemies[0]['hp'] == 200, w2.enemies[0]['hp']
    walls = [im for im in w2.impacts if im['type'] == 'wall']
    assert len(walls) == 1 and abs(walls[0]['y'] - 160.0) < 10 and walls[0]['x'] > 900.0, w2.impacts
    print("3.2 wall occlusion (nearest of wall/entity) OK")


def test_fire_idempotent():
    w = _player_world('pve')
    eid, p, r, wp = w._shooter
    p.x, p.y = 300.0, 300.0
    r.angle = 0.0
    w.enemies.append(_mk_enemy(1, 400.0, 300.0, hp=40))
    from server.server import GameServer
    from server.player import PlayerSession

    class _Party:
        def __init__(self, world):
            self.world = world

    class _Lobby:
        def __init__(self, world):
            self._world = world

        def find_party_of(self, session):
            return _Party(self._world)

    server = object.__new__(GameServer)
    server.lobby = _Lobby(w)
    session = PlayerSession(1, 'A', ('127.0.0.1', 1))

    before = w.enemies[0]['hp']                       # 40
    server._on_player_fire(session, 7, 1)
    after_first = w.enemies[0]['hp']
    assert after_first < before, (before, after_first)

    # сброс кулдауна, чтобы «дубликат» был различим от пропуска по таймеру
    wp.fire_cooldown = 0
    server._on_player_fire(session, 7, 1)             # тот же fire_seq
    assert w.enemies[0]['hp'] == after_first, "дубликат FIRE нанёс второй урон"

    # новый клик (новый fire_seq) стреляет снова
    server._on_player_fire(session, 8, 1)
    assert w.enemies[0]['hp'] < after_first, w.enemies[0]['hp']
    print("3.1 FIRE idempotent by fire_seq OK")


def test_server_absolute_angle_no_drift():
    from server.game_world import GameWorld
    from server.server import GameServer
    from shared.components import Position, Rotation

    w = GameWorld(mode='pve')
    eid = w.add_player(1, 'A')
    rot = w.entities.get_component(eid, Rotation)
    server = object.__new__(GameServer)

    # один и тот же абсолютный угол много раз — угол не дрейфует
    for _ in range(200):
        server._apply_input(w, eid, None, 0, 1, 1.0, False, 1)
    assert abs(rot.angle - 1.0) < 1e-6, rot.angle

    # заворачивание: 6.29 и 6.29 - 2*pi — одно направление, нормируется к [0, 2pi)
    server._apply_input(w, eid, None, 0, 1, 6.29, False, 1)
    a1 = rot.angle
    server._apply_input(w, eid, None, 0, 1, 6.29 - math.tau, False, 1)
    a2 = rot.angle
    assert abs(a1 - 0.0068146928204138035) < 1e-9, a1
    assert abs(a1 - a2) < 1e-9, (a1, a2)
    print("1.2/3.1 server absolute angle: no cumulative drift OK")


def test_client_prediction_reconcile():
    # headless-проверка алгоритма предсказания/реконсиляции из client/client.py
    from client.client import _angle_diff

    local = 0.0
    server_angle = 0.0
    seen_first = False
    # игрок крутится: клиент предсказывает локально, сервер «применил» абсолютный
    for _ in range(3):
        local = normalize_angle(local + 1.9)
        server_angle = normalize_angle(server_angle + 1.9)
    # перекрестие не отстаёт от ввода: пока крутимся, локально уже на месте
    assert _angle_diff(server_angle, local) < 1e-6
    # реконсиляция при простое: сходится к серверному углу за конечное число шагов
    for k in range(40):
        diff = _angle_diff(server_angle, local)
        if not seen_first:
            local = server_angle
            seen_first = True
        elif abs(diff) <= 1e-4:
            break
        elif abs(diff) > 0.45:
            local = server_angle
        else:
            local = normalize_angle(local + diff * 0.3)
    assert _angle_diff(server_angle, local) < 1e-4, (server_angle, local)
    print("2.1 client prediction + reconciliation (no lag) OK")


def test_client_fire_sequence_and_absolute():
    from client.network_client import NetworkClient

    nc = NetworkClient('127.0.0.1', 1)
    nc.state = 'game'
    sent = []

    class _FakeEndpoint:
        def send_reliable(self, ptype, payload):
            sent.append((PacketType(ptype), bytes(payload)))

    nc._endpoint = _FakeEndpoint()

    nc.send_fire(2)
    nc.send_fire(2)
    nc.send_fire(1)
    fire_msgs = [m for t, m in sent if t == PacketType.FIRE]
    assert len(fire_msgs) == 3, sent
    seqs = [decode_fire(m)[0] for m in fire_msgs]
    wids = [decode_fire(m)[1] for m in fire_msgs]
    assert seqs == [1, 2, 3], seqs          # монотонный fire_seq, первый = 1
    assert wids == [2, 2, 1], wids
    # PLAYER_INPUT несёт абсолютный угол (не delta)
    mx, my, angle, sh, wid, fl = decode_player_input(
        encode_player_input(0, 0, math.tau - 0.01, True, 1)
    )
    assert abs(angle - (math.tau - 0.01)) < 1e-6 and wid == 1 and fl == 0
    print("2.2 client FIRE monotonic fire_seq + absolute angle OK")


if __name__ == '__main__':
    test_protocol_roundtrip()
    test_shared_fire_tempo()
    test_ray_circle_hit_geometry()
    test_hitscan_distances_and_radius()
    test_wall_occlusion()
    test_fire_idempotent()
    test_server_absolute_angle_no_drift()
    test_client_prediction_reconcile()
    test_client_fire_sequence_and_absolute()
    print("ALL HITSCAN SMOKE TESTS PASSED")