"""smoke_fire_intent.py — детерминированные проверки единственного пути выстрела.

Покрытие (openspec changes/fire-intent-single-path):
  1.1  фронт нажатия не теряется в закрытых вратах темпа (отдача была, выстрела нет)
  1.2  PLAYER_INPUT не создаёт выстрел на сервере (единственный путь — FIRE)
  1.3  клики быстрее кулдауна: ровно одно открытие врат -> ровно один выстрел
  1.4  решение сервера доезжает в снимке мира (fire_seq + число тиков)
  1.5  клиент берёт таймер оружия из серверного решения, а не из своего нажатия

Сценарии детерминированные: без UDP, без pygame-окна, без живого сервера.

Baseline до правки (зафиксировано, чтобы отличать регресс от старого падения):
  - smoke_fire_intent: красный на клиентском фронте нажатия, на выстреле из
    PLAYER_INPUT, на молчаливом отказе handle_shoot и на отсутствии решения в
    снимке мира (см. 1.1/1.2/1.4).
  - smoke_projectiles.py падает на чистом HEAD 56a2abc («frame with player ball
    identical to frame without», затем «server/client A never saw a player
    ball» из-за оставшегося UDP-порта). Это НЕ регресс этого изменения.
"""
from __future__ import annotations

from shared.combat import wpn_cooldown_ticks, TICK_INTERVAL

PISTOL = 1          # f=16 -> 5 тиков -> врата 0.30 с
RIFLE = 3           # f=8  -> 3 тика  -> врата 0.20 с


def _gate_seconds(weapon_id: int) -> float:
    """Ширина клиентских врат темпа: кулдаун в секундах + один тик запаса."""
    from engine.constants import WPN
    ticks = wpn_cooldown_ticks(int(WPN[weapon_id]['f']))
    return ticks * TICK_INTERVAL + TICK_INTERVAL


class _Clock:
    """Подменяет time.monotonic в client.client, чтобы кадр был детерминирован."""

    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t


class _StubInput:
    """Минимальный InputHandler: только то, что читает _process_input.

    Порядок как у настоящего: update() сначала обнуляет одно-кадровые фронты,
    затем применяет события, заложенные на этот кадр. Поэтому «нажатие» нужно
    заложить ДО вызова _process_input, а флаг, выставленный заранее, из update()
    не выживает — ровно как в бою.
    """

    def __init__(self) -> None:
        self.rotate_delta = 0.0
        self.shoot = False
        self.shoot_pressed = False
        self.weapon_id = PISTOL
        self.updates = 0
        self._queued: list[tuple[bool, bool]] = []

    def queue(self, shoot: bool, pressed: bool) -> None:
        self._queued.append((shoot, pressed))

    def update(self) -> None:
        self.updates += 1
        self.rotate_delta = 0.0
        self.shoot = False
        self.shoot_pressed = False
        shoot, pressed = self._queued.pop(0) if self._queued else (False, False)
        self.shoot = self.shoot or shoot
        self.shoot_pressed = self.shoot_pressed or pressed


class _StubNet:
    def __init__(self) -> None:
        self.fires: list[int] = []

    def send_fire(self, weapon_id: int = 1) -> None:
        self.fires.append(weapon_id)


def _client_stub(ammo: int = 200, weapon_id: int = PISTOL):
    """GameClient без конструктора: только поля, которые читает цикл огня."""
    from client.client import GameClient

    c = object.__new__(GameClient)
    c.input_handler = _StubInput()
    c.input_handler.weapon_id = weapon_id
    c.local_angle = 0.0
    c.local_ammo = {'pstl': ammo, 'shl': ammo, 'slg': ammo}
    c._next_fire_time = 0.0
    c._fire_anim_until = 0.0
    c._fire_anim_wpn = -1
    c._fire_intent_pending = False
    c._fire_undecided = 0
    c._server_fire_seq = 0
    c._server_fire_ready = 0
    c.local_weapon = weapon_id
    c.network = _StubNet()
    return c


def _run_frames(c, clock, times_and_presses):
    """Прогоняет кадры: список (now, shoot, shoot_pressed)."""
    import client.client as cc

    real = cc.time
    cc.time = clock
    try:
        for now, shoot, pressed in times_and_presses:
            clock.t = now
            c.input_handler.queue(shoot, pressed)
            c._process_input()
    finally:
        cc.time = real


def test_client_edge_survives_closed_gate():
    """1.1 Нажатие в закрытых вратах не пропадает: один клик -> один FIRE."""
    c = _client_stub()
    clock = _Clock()
    gate = _gate_seconds(PISTOL)

    # первый клик: врата открыты -> интент уходит
    _run_frames(c, clock, [(0.10, True, True)])
    assert len(c.network.fires) == 1, c.network.fires

    # клик через 0.05 с, врата ещё закрыты (gate = 0.30 с): фронт обязан пережить
    # окно и выстрелить на ближайшем открытии, а не потеряться.
    _run_frames(c, clock, [
        (0.15, True, True),
        (0.16, False, False),
        (0.17, False, False),
    ])
    assert len(c.network.fires) == 1, \
        "нажатие в закрытых вратах потерялось (отдача без выстрела)"

    # ближайшее открытие врат -> ровно один отложенный выстрел
    first = 0.10
    _run_frames(c, clock, [(first + gate + 0.01, False, False)])
    assert len(c.network.fires) == 2, c.network.fires

    # и ровно один, а не два на следующем открытии
    _run_frames(c, clock, [(first + 2 * gate + 0.01, False, False)])
    assert len(c.network.fires) == 2, c.network.fires
    print("1.1 client fire edge survives a closed gate OK")


def test_client_held_fire_sends_every_gate():
    """1.1 Удержание кнопки = интент на каждом открытии врат."""
    c = _client_stub()
    clock = _Clock()
    gate = _gate_seconds(PISTOL)

    # Кнопка нажата все 4 окна подряд, фронт только на самом первом кадре.
    # Кадры идут чаще открытия врат (шаг gate/4), поэтому охват не зависит от
    # частоты кадров — ровно как в игре.
    step = gate / 4.0
    frames = []
    t = 0.05
    for i in range(4 * 4):
        frames.append((t, True, i == 0))
        t += step
    _run_frames(c, clock, frames)

    assert len(c.network.fires) == 4, \
        (f"удержание дало {len(c.network.fires)} интентов вместо 4", c.network.fires)
    print("1.1 held fire sends one intent per gate OK")


def test_input_does_not_shoot_on_server():
    """1.2 PLAYER_INPUT только удержание: сервер из него не стреляет."""
    from server.game_world import GameWorld
    from server.server import GameServer
    from server.weapons import apply_weapon_switch
    from shared.components import Position, WeaponComponent, Player

    w = GameWorld(mode='pve')
    eid = w.add_player(1, 'A')
    wp = w.entities.get_component(eid, WeaponComponent)
    pl = w.entities.get_component(eid, Player)
    wp.ammo['pstl'] = 50
    apply_weapon_switch(w, eid, PISTOL)

    server = object.__new__(GameServer)
    for _ in range(5):
        server._apply_input(w, eid, None, 0, 0, 0.0, True, PISTOL)

    assert wp.ammo['pstl'] == 50, wp.ammo['pstl']
    assert not w.bullets, w.bullets
    assert wp.fire_cooldown == 0, wp.fire_cooldown
    assert pl.state != 2, "ввод выставил state=2 без созданного выстрела"
    print("1.2 PLAYER_INPUT does not fire on the server OK")


def test_handle_shoot_reports_decision():
    """1.2 Отказ возвращается, а не теряется молча."""
    from server.game_world import GameWorld
    from server.weapons import handle_shoot, apply_weapon_switch
    from shared.components import WeaponComponent, Player

    w = GameWorld(mode='pve')
    eid = w.add_player(1, 'A')
    wp = w.entities.get_component(eid, WeaponComponent)
    pl = w.entities.get_component(eid, Player)
    wp.ammo['pstl'] = 10
    apply_weapon_switch(w, eid, PISTOL)

    res = handle_shoot(w, eid)
    assert res is not None, "handle_shoot() не вернул решение"
    assert res.fired is True, res
    assert wp.ammo['pstl'] == 9, wp.ammo['pstl']
    assert wp.fire_cooldown == wpn_cooldown_ticks(16), wp.fire_cooldown
    assert pl.state == 2, "принятый выстрел не выставил state=2"

    # тик: пульс стрельбы гаснет, кулдаун уменьшается
    w.update()
    assert pl.state == 0, pl.state

    # второй интент в кулдауне -> явный отказ, патрон не списывается
    again = handle_shoot(w, eid)
    assert again.fired is False, again
    assert again.reason == 'cooldown', again
    assert wp.ammo['pstl'] == 9, wp.ammo['pstl']
    assert pl.state != 2, "отказ выставил state=2"

    # пустой магазин -> отказ с причиной, а не тишина
    wp.fire_cooldown = 0
    wp.ammo['pstl'] = 0
    dry = handle_shoot(w, eid)
    assert dry.fired is False, dry
    assert dry.reason == 'no_ammo', dry

    # отказ не публикует признак стрельбы
    assert pl.state != 2, "отказ выставил state=2"
    print("1.2 handle_shoot reports fired/refused OK")


def test_clicks_faster_than_cooldown_are_not_lost():
    """1.3 Клики быстрее кулдауна: N кликов -> N открытий врат -> N выстрелов."""
    from server.game_world import GameWorld
    from server.server import GameServer
    from server.weapons import apply_weapon_switch
    from server.player import PlayerSession
    from shared.components import Position, WeaponComponent

    ammo = 50
    w = GameWorld(mode='pve')
    eid = w.add_player(1, 'A')
    wp = w.entities.get_component(eid, WeaponComponent)
    wp.ammo['pstl'] = ammo
    apply_weapon_switch(w, eid, PISTOL)

    server = object.__new__(GameServer)

    class _Party:
        def __init__(self, world):
            self.world = world

    class _Lobby:
        def find_party_of(self, session):
            return _Party(w)

    server.lobby = _Lobby()
    session = PlayerSession(1, 'A', ('127.0.0.1', 1))

    c = _client_stub()
    clock = _Clock()
    gate = _gate_seconds(PISTOL)

    # 6 кликов с интервалом gate/3 (заведомо быстрее кулдауна) + ввод удержания
    ticks = wpn_cooldown_ticks(16)
    step = gate / 3.0
    frames = []
    t = 0.10
    for i in range(6):
        frames.append((t, True, True))
        frames.append((t + step / 2.0, True, False))
        frames.append((t + step, False, False))
        t += step
    _run_frames(c, clock, frames)

    # каждый интент клиента доигрываем на сервере, прогоняя тики
    fire_seq = 0
    for _wid in c.network.fires:
        fire_seq += 1
        server._on_player_fire(session, fire_seq, PISTOL)
        for _ in range(ticks):
            w.update()

    assert len(c.network.fires) > 1, "интенты не дошли до сети вообще"
    assert wp.ammo['pstl'] == ammo - len(c.network.fires), \
        (wp.ammo['pstl'], len(c.network.fires), ammo)
    print(f"1.3 {len(c.network.fires)} clicks -> {len(c.network.fires)} shots, "
          f"no dropped intent OK")


def test_shooting_flag_is_one_tick():
    """1.3 Признак стрельбы живёт ровно один тик и не задевает дэш."""
    from server.game_world import GameWorld
    from server.weapons import handle_shoot, apply_weapon_switch
    from shared.components import WeaponComponent, Player

    w = GameWorld(mode='pve')
    eid = w.add_player(1, 'A')
    wp = w.entities.get_component(eid, WeaponComponent)
    pl = w.entities.get_component(eid, Player)
    wp.ammo['pstl'] = 10
    apply_weapon_switch(w, eid, PISTOL)

    handle_shoot(w, eid)
    assert pl.state == 2, pl.state
    # пульс гаснет на следующем тике: иначе клиент показал бы вечную вспышку
    w.update()
    assert pl.state == 0, pl.state

    # дэш (state = 3) переживает обновление: он выводится из компонента дэша
    pl.state = 3
    w.update()
    assert pl.state == 3, "обновление тика сбило признак дэша"
    print("1.3 shooting flag is a one-tick pulse, dash untouched OK")


def test_fire_seq_only_advances_on_accept():
    """1.4 last_fire_seq двигается только принятым интентом."""
    from server.server import GameServer
    from server.lobby import Party
    from server.player import PlayerSession
    from server.weapons import apply_weapon_switch
    from shared.components import WeaponComponent

    party = Party(1, 'p', 'pve')
    session = PlayerSession(1, 'A', ('127.0.0.1', 1))
    party.add_member(session)
    eid = party.world.get_player_entity(1)
    wp = party.world.entities.get_component(eid, WeaponComponent)
    wp.ammo['pstl'] = 3
    apply_weapon_switch(party.world, eid, PISTOL)

    server = object.__new__(GameServer)

    class _Lobby:
        def find_party_of(self, s):
            return party

    server.lobby = _Lobby()

    # отказ по кулдауну не занимает слот «последнего принятого»
    server._on_player_fire(session, 1, PISTOL)
    assert session.last_fire_seq == 1, session.last_fire_seq
    server._on_player_fire(session, 2, PISTOL)      # кулдаун -> отказ
    assert session.last_fire_seq == 1, session.last_fire_seq
    assert session.last_fire_result.fired is False, session.last_fire_result

    # дубликат принятого интента не стреляет и не двигает счётчик
    ammo_before = wp.ammo['pstl']
    server._on_player_fire(session, 1, PISTOL)
    assert wp.ammo['pstl'] == ammo_before, (wp.ammo['pstl'], ammo_before)
    assert session.last_fire_seq == 1, session.last_fire_seq

    # как только кулдаун истёк, следующий интент снова принимается
    wp.fire_cooldown = 0
    server._on_player_fire(session, 3, PISTOL)
    assert session.last_fire_seq == 3, session.last_fire_seq
    print("1.4 last_fire_seq advances only on an accepted intent OK")


def test_decision_reaches_every_client():
    """1.4 Решение публикуется всем клиентам партии, у каждого своё."""
    from server.server import GameServer
    from server.lobby import Party
    from server.player import PlayerSession
    from server.weapons import FireResult
    from shared.combat import fire_reason_code

    party = Party(1, 'p', 'pve')
    a = PlayerSession(1, 'A', ('127.0.0.1', 1))
    b = PlayerSession(2, 'B', ('127.0.0.1', 2))
    party.add_member(a)
    party.add_member(b)
    a.last_fire_seq = 7
    b.last_fire_seq = 3
    b.last_fire_result = FireResult(False, 'no_ammo', 4)

    captured = {}

    class _Net:
        def broadcast_world_state(self, addresses, tick, players, *rest):
            captured['addresses'] = addresses
            captured['players'] = players

    server = object.__new__(GameServer)
    server.network = _Net()
    server.lobby = type('_L', (), {'parties': {1: party}})()

    server._broadcast_parties()

    assert sorted(captured['addresses']) == [('127.0.0.1', 1), ('127.0.0.1', 2)], \
        captured['addresses']
    got = {p['id']: p for p in captured['players']}
    assert got[1]['fire_seq'] == 7, got[1]
    assert got[2]['fire_seq'] == 3, got[2]
    for pid in (1, 2):
        assert 'fire_ready' in got[pid], got[pid]
        assert 'fire_reason' in got[pid], got[pid]
    # у A интент принят (причины нет), у B последний отказ — «нет патрона»
    assert got[1]['fire_reason'] == 0, got[1]
    assert got[2]['fire_reason'] == fire_reason_code('no_ammo'), got[2]
    print("1.4 fire decision is broadcast to every client OK")


def test_fire_decision_in_snapshot():
    """1.4 Решение сервера едет в снимке мира."""
    from shared.protocol import encode_world_state, decode_world_state
    from shared.combat import fire_reason_code

    players = [{
        'id': 1, 'x': 1.0, 'y': 2.0, 'angle': 0.5, 'health': 150, 'state': 0,
        'wpn': PISTOL, 'name': 'A',
        'ammo': {'pstl': 8, 'shl': 3, 'slg': 4, 'rck': 0, 'BFG': 0},
        'fire_seq': 41, 'fire_ready': 5, 'fire_reason': fire_reason_code('no_ammo'),
    }, {
        'id': 2, 'x': 3.0, 'y': 4.0, 'angle': 0.0, 'health': 100, 'state': 0,
        'wpn': PISTOL, 'name': 'B',
        'ammo': {'pstl': 1, 'shl': 0, 'slg': 0, 'rck': 0, 'BFG': 0},
        'fire_seq': 9, 'fire_ready': 0, 'fire_reason': 0,
    }]
    _t, pl, _en, _bl, _co, _mode, _im = decode_world_state(
        encode_world_state(7, players)
    )
    assert len(pl) == 2, pl
    # имя идёт сразу после записи, поэтому рост записи не должен съесть его
    assert [p['name'] for p in pl] == ['A', 'B'], pl
    assert pl[0]['fire_seq'] == 41, pl[0]
    assert pl[0]['fire_ready'] == 5, pl[0]
    assert pl[0]['fire_reason'] == fire_reason_code('no_ammo'), pl[0]
    assert pl[1]['fire_seq'] == 9 and pl[1]['fire_reason'] == 0, pl[1]
    print("1.4 fire decision survives the WORLD_STATE round-trip OK")


def test_local_weapon_timer_comes_from_server():
    """1.5 Таймер локального оружия — из серверного решения, не из нажатия."""
    from client.client import GameClient, TICK_INTERVAL, FPS_60

    c = _client_stub()

    def expected(ticks):
        # A tick is a 50 ms server tick, the renderer counts frames at 60 fps,
        # so the conversion is ticks * TICK_INTERVAL * FPS_60 — NOT ticks * 60,
        # which would silently claim a tick is a frame and stretch every recoil
        # by 20x.
        return int(ticks * TICK_INTERVAL * FPS_60 + 0.5)

    assert c._fire_anim_frames_from_server(5) == expected(5)
    assert c._fire_anim_frames_from_server(5) == 15, c._fire_anim_frames_from_server(5)

    # сервер отказал (0 тиков) -> анимации нет
    assert c._fire_anim_frames_from_server(0) == 0

    # сервер тикает кулдаун вниз -> кадры убывают
    assert c._fire_anim_frames_from_server(3) == expected(3)
    assert c._fire_anim_frames_from_server(3) < c._fire_anim_frames_from_server(5)
    print("1.5 local weapon timer is derived from the server decision OK")


def test_recoil_follows_server_decision():
    """1.5 Отдача сверяется с решением сервера, а не с нажатием."""
    from shared.combat import fire_reason_code

    c = _client_stub()
    clock = _Clock()
    clock.t = now = 1000.0
    c.local_weapon = 1
    c._fire_anim_wpn = -1

    # предсказание по интенту: отдача стартует сразу (так ощущается оружие)
    c._fire_intent_pending = True
    _run_frames(c, clock, [(now, True, True)])
    assert c._fire_anim_until > now, c._fire_anim_until
    assert c._fire_undecided == 1, c._fire_undecided

    # сервер подтвердил выстрел: fire_seq растёт, длительность авторитетна
    predicted = c._fire_anim_until
    c._sync_fire_anim(1, 4, 0, 1, now + 0.01)
    # серверные тики — пол: меньше предсказания отдача не укорачивается
    # (снимок может прийти на тик позже, и кулдаун уже отсчитан)
    assert c._fire_anim_until >= now + 0.01 + 4 * TICK_INTERVAL, c._fire_anim_until
    assert c._fire_anim_until == predicted, c._fire_anim_until
    assert c._server_fire_seq == 1 and c._fire_undecided == 0

    # сервер, сообщивший БОЛЬШЕ тиков, чем предсказание, выигрывает
    c._sync_fire_anim(2, 9, 0, 1, now + 0.02)
    assert c._fire_anim_until == now + 0.02 + 9 * TICK_INTERVAL, c._fire_anim_until
    assert c._server_fire_seq == 2 and c._fire_undecided == 0

    # повторный снимок с тем же решением не перезапускает анимацию
    prev = c._fire_anim_until
    c._sync_fire_anim(1, 4, 0, 1, now + 0.2)
    assert c._fire_anim_until == prev, c._fire_anim_until

    # отказ (нет патрона): fire_seq не двигается, отдача обрывается
    clock.t = now + 0.35          # врата огня уже открылись
    _run_frames(c, clock, [(clock.t, True, False)])
    assert c._fire_undecided == 1, c._fire_undecided
    assert c._fire_anim_until > clock.t, c._fire_anim_until
    c._sync_fire_anim(1, 3, fire_reason_code('no_ammo'), 1, now + 0.3)
    assert c._fire_anim_until == 0.0, c._fire_anim_until
    assert c._fire_anim_wpn == -1
    assert c._fire_undecided == 0, c._fire_undecided

    # тот же отказ повторно не обрывает ничего (решение уже учтено)
    c._fire_anim_until = now
    c._sync_fire_anim(1, 3, fire_reason_code('no_ammo'), 1, now + 0.4)
    assert c._fire_anim_until == now, c._fire_anim_until

    # смена оружия: подтверждение по старому стволу не воскрешает отдачу
    c._fire_undecided = 1
    c._fire_anim_until = 0.0
    c._fire_anim_wpn = 2
    c._sync_fire_anim(2, 4, 0, 1, now)
    assert c._fire_anim_until == 0.0, c._fire_anim_until

    # потерянный снимок: скачок fire_seq всё равно означает выстрел
    c._fire_anim_wpn = -1
    c._sync_fire_anim(9, 4, 0, 1, now)
    assert c._fire_anim_until == now + 4 * TICK_INTERVAL, c._fire_anim_until
    print("1.5 recoil is reconciled from the server decision OK")


def test_in_flight_intent_is_not_mistaken_for_refusal():
    """1.5 Решение, ещё не дошедшее, не обрывает настоящий выстрел."""
    from shared.combat import fire_reason_code

    c = _client_stub()
    clock = _Clock()
    clock.t = now = 1000.0
    c.local_weapon = 1

    # выстрел принят, отдача идёт
    c._fire_intent_pending = True
    _run_frames(c, clock, [(now, True, True)])
    c._sync_fire_anim(1, 4, 0, 1, now + 0.01)
    running = c._fire_anim_until
    assert running > now

    # клиент успел отправить следующий интент (врата открылись), и его собственное
    # предсказание продлило отдачу; сервер на это решение ещё не ответил
    clock.t = now + 0.35
    _run_frames(c, clock, [(clock.t, True, False)])
    predicted_next = c._fire_anim_until
    assert predicted_next > running, (running, predicted_next)
    assert c._fire_undecided == 1, c._fire_undecided
    # снимок без решения (fire_seq прежний, причины нет) НЕ обрывает отдачу
    c._sync_fire_anim(1, 4, 0, 1, now + 0.36)
    assert c._fire_undecided == 1, c._fire_undecided
    assert c._fire_anim_until == predicted_next, c._fire_anim_until
    print("1.5 an in-flight intent does not cancel a real shot OK")


if __name__ == '__main__':
    test_client_edge_survives_closed_gate()
    test_client_held_fire_sends_every_gate()
    test_input_does_not_shoot_on_server()
    test_handle_shoot_reports_decision()
    test_clicks_faster_than_cooldown_are_not_lost()
    test_shooting_flag_is_one_tick()
    test_fire_seq_only_advances_on_accept()
    test_decision_reaches_every_client()
    test_fire_decision_in_snapshot()
    test_local_weapon_timer_comes_from_server()
    test_recoil_follows_server_decision()
    test_in_flight_intent_is_not_mistaken_for_refusal()
    print("ALL FIRE INTENT SMOKE TESTS PASSED")