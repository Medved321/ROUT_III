"""smoke_fire_intent.py — детерминированные проверки единственного пути выстрела.

Покрытие (openspec changes/fire-intent-single-path, fire-decision-attribution,
fire-no-shot-backlog):
  1.1  клик по горячему оружию съедается: ни интента, ни отдачи
  1.2  PLAYER_INPUT не создаёт выстрел на сервере (единственный путь — FIRE)
  1.3  клики быстрее кулдауна не теряются: ровно одно открытие врат -> ровно
       один выстрел (а отказ по кулдауну не превращается в повторные интенты)
  1.4  решение сервера доезжает в снимке мира (fire_seq + число тиков)
  1.5  клиент берёт таймер оружия из серверного решения, а не из своего нажатия
  1.6  темп врат считается по оружию СЕРВЕРА, а не по локально выбранному
  1.7  серия кликов не оставляет отложенных выстрелов; стрелять чаще врат можно
       только удержанием
  1.8  отказ по cooldown отодвигает врата по серверному остатку и НЕ порождает
       повторов (возвращать нечего: нажатий не хранится)
  1.9  решение адресуется к своему интенту, а не к самому свежему; поздний
       повтор отказа не затирает более новое решение и не решается заново;
       интент без партии получает решение `noop`

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

import math
from collections import deque

from shared.combat import wpn_cooldown_ticks, TICK_INTERVAL

PISTOL = 1          # f=16 -> 5 тиков -> врата 0.30 с
RIFLE = 3           # f=8  -> 3 тика  -> врата 0.20 с
SSG = 5             # f=75 -> 25 тиков -> врата 1.30 с


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
        self.dash_pressed = False
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
        self.dash_pressed = False
        shoot, pressed = self._queued.pop(0) if self._queued else (False, False)
        self.shoot = self.shoot or shoot
        self.shoot_pressed = self.shoot_pressed or pressed


class _StubNet:
    def __init__(self) -> None:
        self.fires: list[int] = []
        self._seq = 0

    def send_fire(self, weapon_id: int = 1) -> int:
        # The real NetworkClient numbers its intents strictly increasing from
        # 1; the tests drive _sync_fire_anim with those same numbers.
        self._seq += 1
        self.fires.append(weapon_id)
        return self._seq


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
    c._fire_press_buffered = False
    c._fire_pending = deque()
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


class _StopLoop(Exception):
    """Останавливает прогон серверного цикла по виртуальному времени."""


class _WindowsClock:
    """Часы с квантованием, как на Windows.

    Настоящий ProactorEventLoop округляет `await asyncio.sleep` вверх до
    системного тика таймера (~15.6 мс): `sleep(0.05)` возвращается через ~63 мс.
    Именно это ломало частоту тиков сервера, поэтому часы повторяют именно его,
    а не идеальные: иначе проверка проходила бы на баге, который нельзя увидеть
    на идеальных часах. `time.sleep(0.001)` при этом стоит 1.9 мс — квантует
    именно asyncio, поэтому фиксированной части не избежать ни при каком
    ожидании, и чинить надо сам цикл, а не сон.
    """

    GRANULARITY = 0.016

    def __init__(self, stop_at: float | None = None) -> None:
        self.t = 0.0
        self.stop_at = stop_at

    def monotonic(self) -> float:
        return self.t

    async def sleep(self, delay: float) -> None:
        if self.stop_at is not None and self.t >= self.stop_at:
            raise _StopLoop
        if delay <= 0.0:
            self.t += self.GRANULARITY
        else:
            self.t += math.ceil(delay / self.GRANULARITY) * self.GRANULARITY


def _run_server_loop(server, clock: _WindowsClock) -> None:
    """Прогоняет НАСТОЯЩИЙ GameServer._game_loop на виртуальных часах.

    Часы подменяются на уровне модуля (`server.server.time`), а ожидание — на
    уровне `asyncio.sleep`, ровно как их видит `_game_loop`. Сам цикл не
    переписывается: тест ломается, если сломается он.
    """
    import asyncio
    import server.server as ss

    real_sleep, real_time = asyncio.sleep, ss.time
    asyncio.sleep, ss.time = clock.sleep, clock
    try:
        coro = server._game_loop()
        try:
            while True:
                coro.send(None)
        except (_StopLoop, StopIteration):
            pass
    finally:
        asyncio.sleep, ss.time = real_sleep, real_time


def test_client_press_on_a_closed_gate_is_remembered_once():
    """1.1 Клик по горячему оружию запоминается РОВНО на одно открытие врат.

    Без памяти клик в закрытые врата просто пропадает, а у дробовика (врата
    0.90 с) это девять кликов из десяти — «оружие стреляет раз в 10 нажатий».
    С очередью кликов хуже: она растёт со скоростью кликов и расходуется со
    скоростью кулдауна. Поэтому память ровно на одно нажатие: клик не теряется,
    и расти нечему.
    """
    c = _client_stub()
    clock = _Clock()
    gate = _gate_seconds(PISTOL)

    # первый клик: врата открыты -> интент уходит
    _run_frames(c, clock, [(0.10, True, True)])
    assert len(c.network.fires) == 1, c.network.fires
    accepted = c._fire_anim_until
    assert accepted > 0.0, accepted

    # клик через 0.05 с, врата ещё закрыты (gate = 0.30 с): интента нет и отдача
    # НЕ продлевается — выстрела ещё не было, значит и отдачи пока не видно
    _run_frames(c, clock, [
        (0.15, True, True),
        (0.16, False, False),
        (0.17, False, False),
    ])
    assert len(c.network.fires) == 1, \
        "клик по горячему оружию отправил интент (выстрел из ничего)"
    assert c._fire_anim_until == accepted, c._fire_anim_until
    assert c._fire_press_buffered is True, "нажатие не запомнилось"

    # ближайшее открытие врат обслуживает запомненный клик, кнопка уже отпущена
    _run_frames(c, clock, [(0.10 + gate + 0.01, False, False)])
    assert len(c.network.fires) == 2, c.network.fires
    assert c._fire_press_buffered is False, "нажатие пережило своё открытие"

    # и следующее открытие без нажатия уже молчит: память кончилась
    _run_frames(c, clock, [(0.10 + 2 * gate + 0.01, False, False)])
    assert len(c.network.fires) == 2, c.network.fires
    print("1.1 a press on a closed gate fires on the next opening, once OK")


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


def test_clicks_on_a_hot_weapon_never_reach_the_server():
    """1.3 Клик по горячему оружию не доходит до сети, а дошедший интент стреляет.

    Две стороны одного контракта. Первая: клиент отсекает клик сам, поэтому в сеть
    уходит ровно столько интентов, сколько было открытий врат, и ни одним
    больше. Вторая: он при этом НЕ отсекает лишнего — каждый дошедший интент
    сервер принимает, ни одного отказа по cooldown. Раньше вторая сторона была
    недостижимой без очереди: чтобы «не терять» клики, их надо было где-то
    хранить, и хранилище отдавало их пачками уже после кулдауна.
    """
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

    # шесть кликов за 0.62 с при вратах 0.30 с = два открытия врат, и интентов
    # ровно столько же, плюс одно запомненное нажатие, обслуженное на них.
    # Кликов шесть, интентов три: клики не копятся, но и не теряются.
    assert len(c.network.fires) == 3, c.network.fires
    # и каждый дошедший интент сервер ПРИНЯЛ — ни одного отказа по cooldown
    assert session.last_fire_result.fired is True, session.last_fire_result
    assert wp.ammo['pstl'] == ammo - len(c.network.fires), \
        (wp.ammo['pstl'], len(c.network.fires), ammo)
    print(f"1.3 {len(c.network.fires)} intents -> {len(c.network.fires)} shots, "
          f"clicks on a hot weapon never left the client OK")


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
    _t, pl, _en, _bl, _co, _mode, _im, _it, _doors = decode_world_state(
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
    c._fire_pending.append(3)
    c._fire_anim_until = 0.0
    c._fire_anim_wpn = 2
    c._sync_fire_anim(3, 4, 0, 1, now)
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

def test_gate_tempo_follows_the_server_weapon():
    """1.6 Темп врат — по оружию сервера, а не по локально выбранному.

    Локальный выбор — только запрос на смену: сервер применяет его лишь для
    оружия, которым игрок владеет, и сам выдаёт оружие при подборе. Если врата
    считать по локальному id, клиент ограничивает темп чужим стволом: SSG
    (врата 1.30 с) при пистолете на сервере съедает 3 нажатия из 4 — «нажимал по
    кулдауну, выстрела не было». Обратный рассинхрон (пистолет локально, SSG на
    сервере) даёт отказы по cooldown и срезанную отдачу.

    Проверяется удержанием: только оно вообще даёт более одного интента на
    окно, поэтому темп виден как число открытий, а клики по горячему оружию
    теперь просто съедаются (1.7).
    """
    c = _client_stub()
    clock = _Clock()
    c.input_handler.weapon_id = SSG      # игрок выбрал SSG локально...
    c.local_weapon = PISTOL              # ...а сервер держит пистолет
    fast_gate = _gate_seconds(PISTOL)

    # Кнопка нажата 6 пистолетных окон подряд: ровно 6 интентов.
    # Время кадра в окне считается от якоря, а не накапливается (`t += step`):
    # срок врат накапливает ту же float-ошибку, и на шестом окне кадрopen'а
    # уезжал за последний кадр. +1e-9 — это запас от одного ulp, не сдвиг темпа.
    step = fast_gate / 4.0
    frames = []
    for k in range(6):
        base = 0.10 + k * fast_gate + 1e-9
        for j in range(4):
            frames.append((base + j * step, True, k == 0 and j == 0))
    _run_frames(c, clock, frames)
    assert len(c.network.fires) == 6, c.network.fires
    # и ни один нажатый ствол не подменился: в FIRE уходит локальный выбор
    assert c.network.fires[0] == SSG, c.network.fires

    # Обратный рассинхрон: локально пистолет, на сервере SSG. Врата обязаны
    # стать длинными, иначе интенты сыплются в отказ по cooldown.
    c2 = _client_stub()
    c2.input_handler.weapon_id = PISTOL
    c2.local_weapon = SSG
    clock2 = _Clock()
    slow_gate = _gate_seconds(SSG)
    step = slow_gate / 4.0
    frames = []
    for k in range(2):
        base = 0.10 + k * slow_gate + 1e-9
        for j in range(4):
            frames.append((base + j * step, True, k == 0 and j == 0))
    _run_frames(c2, clock2, frames)
    # два окна SSG = два интента, хотя пистолетные врата дали бы больше шести
    assert len(c2.network.fires) == 2, c2.network.fires
    print("1.6 gate tempo follows the server's weapon OK")


def test_a_burst_of_presses_becomes_one_shot_per_opening():
    """1.7 Серия нажатий = выстрел на открытие, а не очередь выстрелов.

    Было: очередь нажатий копилась со скоростью кликов и расходовалась со
    скоростью кулдауна — для SSG (врата 1.30 с) при клике 10 Гц это ~39 отложенных
    выстрелов на три секунды, залп после отпускания кнопки. Память ровно на одно
    нажатие: клик не теряется (иначе дробовик «стреляет раз в 10 кликов»), но и
    не копится.
    """
    c = _client_stub()
    clock = _Clock()
    gate = _gate_seconds(PISTOL)
    presses = 10

    # десять нажатий за 0.2 с — все в первом закрытом окне врат
    _run_frames(c, clock, [(0.05 + 0.02 * i, True, True) for i in range(presses)])
    assert len(c.network.fires) == 1, c.network.fires
    # из девяти нажатий в закрытом окне запомнено ровно одно
    assert c._fire_press_buffered is True, c._fire_press_buffered

    # ближайшее открытие отдаёт этот один запомненный выстрел, и всё
    _run_frames(c, clock, [(0.05 + gate + 0.01, False, False)])
    assert len(c.network.fires) == 2, c.network.fires
    assert c._fire_press_buffered is False, c._fire_press_buffered

    # дальше тишина: кнопка отпущена, память пуста
    t = 0.05 + gate + 0.01
    for _ in range(presses):
        _run_frames(c, clock, [(t, False, False)])
        assert len(c.network.fires) == 2, (t, c.network.fires)
        t += gate + 0.01

    # то же самое на медленном оружии: 20 кликов за 2 с при вратах SSG (1.30 с).
    # Открытия врат — на 0.05 и на 1.45, то есть два интента, а не двадцать.
    # На втором открытии приходит свой клик, поэтому он и стреляет, а
    # запомненное нажатие просто погашается вместе с ним.
    c2 = _client_stub()
    c2.local_weapon = SSG
    clock2 = _Clock()
    _run_frames(c2, clock2, [(0.05 + 0.10 * i, True, True) for i in range(20)])
    assert len(c2.network.fires) == 2, c2.network.fires
    slow = _gate_seconds(SSG)
    _run_frames(c2, clock2, [(0.05 + slow + 0.01, False, False)])
    assert len(c2.network.fires) == 2, c2.network.fires
    print("1.7 a burst of clicks fires once per opening, nothing queues up OK")


def test_held_fire_is_the_only_way_to_keep_shooting():
    """1.7 Удержание — единственный способ стрелять чаще, чем открываются врата."""
    c = _client_stub()
    clock = _Clock()
    gate = _gate_seconds(PISTOL)

    # удержание: интент на каждом открытии врат, темп оружия соблюдён
    frames = []
    t = 0.05
    for i in range(int(4 * gate / 0.05) + 1):
        frames.append((t, True, i == 0))
        t += 0.05
    _run_frames(c, clock, frames)
    shots = len(c.network.fires)
    assert shots >= 4, shots
    assert shots <= int(4 * gate / 0.05) / int(gate / 0.05) + 1, shots

    # а после отпускания — тишина, сколько бы врат ни открылось
    before = len(c.network.fires)
    for _ in range(5):
        _run_frames(c, clock, [(t, False, False)])
        t += gate + 0.01
    assert len(c.network.fires) == before, c.network.fires
    print("1.7 held fire shoots per gate, release stops it dead OK")


def test_a_stall_produces_one_shot_not_a_burst():
    """1.7 После фриза — ровно один выстрел, а не залп.

    Запомненное нажатие переживает простой (оно и предназначено следующему
    открытию), но переживает РОВНО ОДНО. Восемь нажатий, фриз на 5 с, и на
    первом открытии после фриза стреляет одно — из восьми, а не из восьми.
    """
    c = _client_stub()
    clock = _Clock()
    gate = _gate_seconds(PISTOL)

    # нажатия идут, потом клиент подвисает (alt-tab / длинный кадр)
    _run_frames(c, clock, [(0.05 + 0.02 * i, True, True) for i in range(8)])
    assert len(c.network.fires) == 1, c.network.fires
    assert c._fire_press_buffered is True, c._fire_press_buffered

    # первое открытие после фриза отдаёт ровно один запомненный выстрел
    stale = 0.05 + 0.02 * 7 + 5.0
    _run_frames(c, clock, [(stale, False, False)])
    assert len(c.network.fires) == 2, "после фриза выстрелов больше двух"
    assert c._fire_press_buffered is False, c._fire_press_buffered

    # и дальше тишина: залпа нет ни на первом, ни на последующих открытиях
    _run_frames(c, clock, [(stale + gate + 0.01, False, False)])
    assert len(c.network.fires) == 2, c.network.fires
    print("1.7 a stall yields one shot, not a burst OK")


def test_cooldown_refusal_waits_the_weapon_out():
    """1.8 Отказ по кулдауну отодвигает врата и НЕ порождает повторных интентов.

    Возвращать нажатие было можно только потому, что оно хранилось. Хранилища
    больше нет, поэтому повторять нечего: единственное действие — дождаться
    серверного остатка кулдауна. Иначе клик, отказанный кулдауном, превратился бы
    в цикл попыток.
    """
    from shared.combat import fire_reason_code

    c = _client_stub()
    clock = _Clock()
    gate = _gate_seconds(PISTOL)

    _run_frames(c, clock, [(0.10, True, True)])
    assert len(c.network.fires) == 1
    assert c._fire_undecided == 1, c._fire_pending

    # сервер отказал: ствол был горячий, и остаток кулдауна больше локальных врат
    c._sync_fire_anim(0, 8, fire_reason_code('cooldown'), PISTOL, 0.11)
    assert c._fire_undecided == 0, c._fire_pending
    assert c._fire_anim_until == 0.0, c._fire_anim_until

    # остаток кулдауна отодвинул врата, поэтому мгновенный повтор не бьёт снова
    ready_gate = 0.11 + (8 + 1) * TICK_INTERVAL
    assert c._next_fire_time == ready_gate, (c._next_fire_time, ready_gate)
    _run_frames(c, clock, [(0.10 + gate + 0.01, True, True)])
    assert len(c.network.fires) == 1, "повтор ушёл в горячий ствол"
    _run_frames(c, clock, [(ready_gate + 0.01, True, True)])
    assert len(c.network.fires) == 2, c.network.fires

    # локальный срок позже серверного — остаётся локальный (max, а не min)
    refusal_now = ready_gate + 0.02
    c._sync_fire_anim(1, 1, fire_reason_code('cooldown'), PISTOL, refusal_now)
    local = ready_gate + 0.01 + gate
    assert c._next_fire_time >= local, (c._next_fire_time, local)

    # отказ по патронам — это решение: отдача снята, и повторов тоже нет
    t = ready_gate + 0.01 + gate + 0.01
    _run_frames(c, clock, [(t, True, True)])
    assert len(c.network.fires) == 3, c.network.fires
    c._sync_fire_anim(1, 0, fire_reason_code('no_ammo'), PISTOL, t + 0.01)
    assert c._fire_anim_until == 0.0, c._fire_anim_until
    assert c._fire_undecided == 0, c._fire_pending
    for _ in range(3):
        t += gate + 0.01
        _run_frames(c, clock, [(t, False, False)])
    assert len(c.network.fires) == 3, "отказ по патронам породил повторные выстрелы"
    print("1.8 a cooldown refusal waits the weapon out, nothing retries OK")


def test_refusal_does_not_cut_the_confirmed_shot():
    """1.9 Отказ списывается со СВОЕГО интента, а не с чужого.

    Снимок «fire_seq прыгнул И пришла причина» — это ровно «#1 принят, #2 отказ,
    #3 ещё в полёте». Старый код разбирал снимок в обратном порядке: сначала
    принятие обнуляло счётчик нерешённых интентов, и отказ в том же снимке
    проходил мимо — отдача принятого выстрела обрывалась, и на экране было
    «анимация прервалась, выстрела нет».
    """
    from shared.combat import fire_reason_code

    c = _client_stub()
    clock = _Clock()
    gate = _gate_seconds(PISTOL)

    # три интента в полёте: клик, удержание, удержание — на трёх открытиях врат
    first = 0.10
    _run_frames(c, clock, [(first, True, True),
                            (first + gate + 0.01, True, True),
                            (first + 2 * gate + 0.02, True, True)])
    assert list(c._fire_pending) == [1, 2, 3], c._fire_pending

    ready = wpn_cooldown_ticks(16)
    now = first + 2 * gate + 0.04
    c._sync_fire_anim(1, ready, fire_reason_code('cooldown'), PISTOL, now)

    # #1 принят (сброшен), #2 отказан (списан), #3 ещё ждёт решения
    assert list(c._fire_pending) == [3], c._fire_pending
    assert c._fire_undecided == 1, c._fire_pending
    # отказ достался не новейшему (#3), поэтому отдача принятого выстрела выжила:
    # сервер прямо говорит, что ствол горячий, то есть выстрел был
    assert c._fire_anim_until >= now + ready * TICK_INTERVAL, c._fire_anim_until

    # тот же снимок повторно: липкая причина всё ещё приходит, и пока в полёте
    # есть интент выше fire_seq, отказ достаётся ему — единственный случай, где
    # причина может быть списана на ещё не решённый интент (см. design.md,
    # остаток)
    c._sync_fire_anim(1, ready, fire_reason_code('cooldown'), PISTOL, now + 0.5)
    assert c._fire_undecided == 0, c._fire_pending

    # когда в полёте не осталось ничего, повтор той же причины — уже не решение
    c._fire_anim_until = now + 0.7
    c._sync_fire_anim(1, ready, fire_reason_code('cooldown'), PISTOL, now + 0.7)
    assert c._fire_anim_until == now + 0.7, c._fire_anim_until
    print("1.9 a refusal is charged to its own intent, not to a confirmed shot OK")


def test_stale_refusal_does_not_shadow_a_later_shot():
    """1.9 Поздняя повторная доставка отказа не затирает более новое решение.

    Надёжный канал передоставляет пакеты. Старый код решал отказ заново, если его
    номер просто не совпадал с последним принятым, — и поздняя копия отказа №6
    публиковала ложный «cooldown» поверх принятого выстрела №7.
    """
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
    wp.ammo['pstl'] = 50
    apply_weapon_switch(party.world, eid, PISTOL)

    server = object.__new__(GameServer)
    server.lobby = type('_L', (), {'find_party_of': lambda self, s: party})()

    server._on_player_fire(session, 5, PISTOL)      # принят
    server._on_player_fire(session, 6, PISTOL)      # отказ по кулдауну
    assert session.last_fire_result.fired is False, session.last_fire_result
    assert session.last_fire_result.reason == 'cooldown', session.last_fire_result

    wp.fire_cooldown = 0
    server._on_player_fire(session, 7, PISTOL)      # принят снова
    assert session.last_fire_seq == 7, session.last_fire_seq
    assert session.last_fire_result.fired is True, session.last_fire_result

    # отказ №6 приходит повторно уже ПОСЛЕ принятия №7
    server._on_player_fire(session, 6, PISTOL)
    assert session.last_fire_seq == 7, session.last_fire_seq
    assert session.last_fire_result.fired is True, \
        f"поздний отказ затёр принятый выстрел: {session.last_fire_result}"
    assert session.last_fire_result.reason == '', session.last_fire_result
    print("1.9 a late duplicate refusal never overwrites a newer decision OK")


def test_unhandled_intent_is_answered():
    """1.9 Интент, который сервер не может разобрать, всё равно получает ответ.

    Молчание по интенту оставляло его в полёте навсегда: счётчик нерешённых
    интентов рос, и поверх него копилась очередь нажатий. Теперь это решение
    `noop`, и очередь нерешённых интентов опустошается.
    """
    from server.server import GameServer
    from server.player import PlayerSession

    session = PlayerSession(1, 'A', ('127.0.0.1', 1))
    server = object.__new__(GameServer)
    server.lobby = type('_L', (), {'find_party_of': lambda self, s: None})()

    server._on_player_fire(session, 4, PISTOL)
    res = session.last_fire_result
    assert res is not None, "интент без партии остался без решения"
    assert res.fired is False, res
    assert res.reason == 'noop', res
    print("1.9 an unhandled intent is answered with noop OK")


def test_redelivered_refusal_is_not_decided_twice():
    """1.9 Передоставленный ОТКАЗ решается один раз, а не два.

    Надёжный канал передоставляет, и rto растёт экспоненциально — так что
    повторно пришедший отказ это не теория, а обычное дело. Старый фильтр смотрел
    на `last_fire_seq`, который двигается ТОЛЬКО при принятом интенте: номер
    отказа оставался ниже него, и повтор решался заново. К моменту повтора
    кулдаун мог уже истечь, и сервер стрелял из интента, который клиент давно
    списал, — выстрел, который никто не нажимал, да ещё и опубликованный как
    свежее принятие поверх решения, пришедшего следом.

    Отдельно проверяется, что отказ не «съедает» номер: после него обычный
    следующий интент (номер выше) по-прежнему принимается.
    """
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
    wp.ammo['pstl'] = 50
    apply_weapon_switch(party.world, eid, PISTOL)

    server = object.__new__(GameServer)
    server.lobby = type('_L', (), {'find_party_of': lambda self, s: party})()

    server._on_player_fire(session, 1, PISTOL)        # принят -> кулдаун
    server._on_player_fire(session, 2, PISTOL)        # отказ по кулдауну
    assert session.last_fire_result.fired is False, session.last_fire_result
    refused_result = session.last_fire_result

    # кулдаун истёк, и отказ №2 приходит повторно (потерянный ACK)
    wp.fire_cooldown = 0
    ammo_before = wp.ammo['pstl']
    server._on_player_fire(session, 2, PISTOL)        # redelivery
    assert wp.ammo['pstl'] == ammo_before, \
        f"передоставленный отказ выстрелил: {ammo_before} -> {wp.ammo['pstl']}"
    assert session.last_fire_result == refused_result, \
        f"передоставленный отказ опубликовал новое решение: {session.last_fire_result}"
    assert session.last_fire_seq == 1, session.last_fire_seq

    # отказ не занял номер: следующий интент (номер выше) принимается
    server._on_player_fire(session, 3, PISTOL)
    assert session.last_fire_seq == 3, session.last_fire_seq
    assert session.last_fire_result.fired is True, session.last_fire_result
    assert wp.ammo['pstl'] == ammo_before - 1, wp.ammo['pstl']
    print("1.9 a redelivered refusal is decided once OK")


def test_server_tick_loop_holds_the_rate_the_fire_gate_is_built_on():
    """1.10 Тиковый цикл держит TICK_RATE — на котором стоят врата выстрела.

    Симптом: при долгой стрельбе «через раз» — отдача начинается, патрон
    пропадает, анимация обрывается, урона нет. Это отказ по cooldown, и он
    возникал систематически из-за частоты тиков, а не из-за клика.

    `TICK_RATE` — это то, чем `wpn_cooldown_ticks` переводит темп оружия, и
    переводят ОБЕ стороны, поэтому врата клиента посчитаны в номинальных тиках,
    а сервер сливает их в настоящих. Старый цикл делал `last_tick = now` и
    просыпался через `asyncio.sleep(0.001)` — на Windows это 15.8 tps вместо 20,
    то есть 21 % медленнее. Кулдаун пистолета в 5 тиков растягивался с 0.25 с до
    0.32 с при вратах 0.30 с, и КАЖДЫЙ ВТОРОЙ интент приходил в горячий ствол.
    Запас в один тик — это весь запас, и он уходил целиком.

    Часы здесь намеренно квантуют ожидание по-виндовски, поэтому проверка
    воспроизводит баг детерминированно, а не «если у машины плохой таймер».
    """
    import asyncio
    from server.server import GameServer
    from server.game_data import TICK_RATE, WPN
    from server.game_world import GameWorld
    from server.player import PlayerSession
    from server.weapons import apply_weapon_switch
    from shared.components import WeaponComponent

    seconds = 6.0
    window = seconds * 1000.0
    latency = 0.060           # 3 тика пути туда-обратно — обычная локальная сеть

    for wpn in (PISTOL, 2, SSG):
        ticks = wpn_cooldown_ticks(int(WPN[wpn]['f']))
        gate = ticks * TICK_INTERVAL + TICK_INTERVAL

        world = GameWorld(mode='pve')
        eid = world.add_player(1, 'A')
        comp = world.entities.get_component(eid, WeaponComponent)
        comp.ammo['pstl'] = 9999
        comp.ammo['shl'] = 9999
        apply_weapon_switch(world, eid, wpn)

        server = object.__new__(GameServer)
        server.running = False
        session = PlayerSession(1, 'A', ('127.0.0.1', 1))
        fired = refused = 0
        reasons: list[str] = []
        sent = 0
        next_fire = 0.0
        inflight: list[tuple[float, int]] = []
        n_ticks = 0

        class _Lobby:
            def update_all(self):
                nonlocal n_ticks, fired, refused, sent, next_fire
                n_ticks += 1
                world.update()
                now = clock.t
                # клиентские врата живут на тех же часах, что и сервер
                if now >= next_fire:
                    sent += 1
                    inflight.append((now + latency, sent))
                    next_fire += gate
                while inflight and inflight[0][0] <= now:
                    _, s = inflight.pop(0)
                    server._on_player_fire(session, s, wpn)
                    res = session.last_fire_result
                    if res.fired:
                        fired += 1
                    else:
                        refused += 1
                        reasons.append(res.reason)

        _party = type('_P', (), {'world': world})()
        server.lobby = type('_L', (), {
            'update_all': _Lobby().update_all,
            'find_party_of': lambda self, sess: _party,
            'parties': {1: _party},
        })()
        server.network = type('_N', (), {'tick': lambda self: None})()
        server._broadcast_parties = lambda: None
        server._remove_stale_players = lambda: None
        server.running = True

        clock = _WindowsClock(stop_at=seconds)
        _run_server_loop(server, clock)

        rate = n_ticks / seconds
        assert abs(rate - TICK_RATE) <= TICK_RATE * 0.1, \
            f"{WPN[wpn]['n']}: {rate:.2f} tps вместо {TICK_RATE}"
        assert refused == 0, \
            (f"{WPN[wpn]['n']}: {refused} отказов из {sent} интентов "
             f"({reasons[:4]}) при {rate:.2f} tps")
        print(f"1.10 {WPN[wpn]['n']:9s} gate={gate:.2f}s -> {rate:5.2f} tps, "
              f"{sent} интентов, {fired} выстрелов, 0 отказов OK")

    _ = asyncio  # импорт нужен ради единообразия прогона


if __name__ == '__main__':
    test_client_press_on_a_closed_gate_is_remembered_once()
    test_client_held_fire_sends_every_gate()
    test_input_does_not_shoot_on_server()
    test_handle_shoot_reports_decision()
    test_clicks_on_a_hot_weapon_never_reach_the_server()
    test_shooting_flag_is_one_tick()
    test_fire_seq_only_advances_on_accept()
    test_decision_reaches_every_client()
    test_fire_decision_in_snapshot()
    test_local_weapon_timer_comes_from_server()
    test_recoil_follows_server_decision()
    test_in_flight_intent_is_not_mistaken_for_refusal()
    test_gate_tempo_follows_the_server_weapon()
    test_a_burst_of_presses_becomes_one_shot_per_opening()
    test_held_fire_is_the_only_way_to_keep_shooting()
    test_a_stall_produces_one_shot_not_a_burst()
    test_cooldown_refusal_waits_the_weapon_out()
    test_refusal_does_not_cut_the_confirmed_shot()
    test_stale_refusal_does_not_shadow_a_later_shot()
    test_redelivered_refusal_is_not_decided_twice()
    test_unhandled_intent_is_answered()
    test_server_tick_loop_holds_the_rate_the_fire_gate_is_built_on()
    print("ALL FIRE INTENT SMOKE TESTS PASSED")