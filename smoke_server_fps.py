"""smoke_server_fps.py — сквозная проверка настраиваемой частоты тиков сервера.

Покрывает change `sector-minimap-editor-fps-cleanup` (группы 4 и 5):

  * границы и перевод частоты в sekundy/тики через `shared.combat`;
  * единый CLI-парсер в обоих точках входа, отказ до открытия сокета;
  * `GameServer` хранит частоту, передаёт её в `Lobby`/`GameWorld`
    и публикует в конце `HELLO_ACK`;
  * клиент принимает объявленную частоту и использует её в интерполяции,
    вратах стрельбы и времени матча в HUD;
  * цикл сервера держит запрошенный tps на квантованных (как на Windows)
    часах и не крутится на `sleep(0)`;
  * мутация «клиент игнорирует объявленную частоту» ломает проверку.

Все сценарии детерминированные: сеть не поднимается, pygame-окно не создаётся.
"""
from __future__ import annotations

import asyncio
import math
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from shared.combat import (
    DEFAULT_TICK_RATE, MAX_TICK_RATE, MIN_TICK_RATE,
    fps60_per_tick, tick_interval, validate_tick_rate, wpn_cooldown_ticks,
)
from shared.protocol import (
    decode_hello_ack, encode_hello_ack,
)
from shared.version import CLIENT_VERSION, PROTOCOL_VERSION

ROOT = Path(__file__).resolve().parent


def _check_tick_helpers() -> None:
    assert DEFAULT_TICK_RATE == 20
    assert (MIN_TICK_RATE, MAX_TICK_RATE) == (5, 120)
    assert validate_tick_rate("30") == 30
    for bad in (4, 121, 3.5, True, None, "abc"):
        try:
            validate_tick_rate(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"validate_tick_rate accepted {bad!r}")

    # Один и тот же темп оружия в секундах на разных частотах.
    # Кадры, кратные трём, дают целое число тиков на 20 и 40 Гц без ошибки
    # округления; проверка именно про совпадение реальных секунд.
    for frames in (15, 30, 75):
        slow = wpn_cooldown_ticks(frames, 20)
        fast = wpn_cooldown_ticks(frames, 40)
        assert abs(slow * tick_interval(20) - fast * tick_interval(40)) < 1e-9
        assert wpn_cooldown_ticks(frames, 5) >= 1

    assert fps60_per_tick(6.0, 20) == 18.0
    assert fps60_per_tick(6.0, 40) == 9.0
    print("1.1 границы 5..120 и перевод кулдауна 20/40 OK")


def _check_cli() -> None:
    from server.cli import parse_server_args

    args = parse_server_args(
        ["--host", "127.0.0.1", "--port", "1234", "--fps", "30"])
    assert args.host == "127.0.0.1" and args.port == 1234
    assert args.tick_rate == 30
    assert parse_server_args([]).tick_rate == DEFAULT_TICK_RATE

    for name in ("run_server.py", "server/__main__.py"):
        source = (ROOT / name).read_text(encoding="utf-8")
        assert "parse_server_args" in source, name
        # Парсер вызывается до создания сервера: неверный --fps не откроет сокет.
        assert source.index("parse_server_args(sys.argv[1:])") < source.index(
            "GameServer("), name

    for entry in (
        [sys.executable, "run_server.py", "--help"],
        [sys.executable, "-m", "server", "--help"],
    ):
        cp = subprocess.run(
            entry, cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        assert cp.returncode == 0, (entry, cp.stderr)
        assert "--fps" in cp.stdout and "5..120" in cp.stdout, cp.stdout

    for bad in ("3", "121", "abc", "3.5"):
        cp = subprocess.run(
            [sys.executable, "run_server.py", "--fps", bad],
            cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        assert cp.returncode != 0, f"--fps {bad} должен отклоняться"
        assert "--fps" in (cp.stderr + cp.stdout)
    print("1.2 единый CLI в двух entry point, отказ до сокета OK")


def _check_world_rates() -> None:
    from server.game_data import item_spec
    from server.game_world import GameWorld

    slow = GameWorld(mode='pve', tick_rate=20)
    fast = GameWorld(mode='pve', tick_rate=40)
    assert slow.tick_rate == 20 and fast.tick_rate == 40
    # Реальные секунды не меняются с частотой тика.
    pairs = (
        ('dash_ticks', slow.dash_ticks, fast.dash_ticks, 20, 40),
        ('door_open_ticks', slow.door_open_ticks, fast.door_open_ticks, 20, 40),
        ('impact_ttl', slow.impact_ttl, fast.impact_ttl, 20, 40),
        ('corpse_lifetime', slow.corpse_lifetime, fast.corpse_lifetime, 20, 40),
    )
    for name, a, b, ra, rb in pairs:
        assert abs(a * tick_interval(ra) - b * tick_interval(rb)) < 1e-9, name
    # Ходьба задана в px/сек (250 = 3.75 тайла/с), а не в px/кадр@60:
    # подмена множителя 60/тик дала бы 750 px/тик на 20 Гц.
    assert abs(slow.max_move_step_px - 12.5) < 1e-9, slow.max_move_step_px
    assert abs(fast.max_move_step_px - 6.25) < 1e-9, fast.max_move_step_px
    assert fast.ranged_db['imp']['spd'] * 2 == slow.ranged_db['imp']['spd']
    assert item_spec('haste', 40)['ticks'] == item_spec('haste', 20)['ticks'] * 2
    print("1.3 GameWorld выводит скорости и длительности из частоты OK")


class _StopLoop(Exception):
    """Останавливает виртуальный цикл по истечении окна наблюдения."""


class _QuantizedClock:
    """Часы с округлением ожидания вверх, как asyncio на Windows."""

    GRANULARITY = 0.016

    def __init__(self, stop_at: float) -> None:
        self.t = 0.0
        self.stop_at = stop_at
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    async def sleep(self, delay: float) -> None:
        if self.t >= self.stop_at:
            raise _StopLoop
        self.sleeps.append(delay)
        if delay <= 0.0:
            self.t += self.GRANULARITY
        else:
            self.t += math.ceil(delay / self.GRANULARITY) * self.GRANULARITY


def _run_server_loop(server, clock: _QuantizedClock) -> None:
    """Прогоняет настоящий `GameServer._game_loop` на виртуальных часах."""
    import server.server as ss

    real_sleep = asyncio.sleep
    asyncio.sleep, ss.time = clock.sleep, clock
    try:
        coro = server._game_loop()
        try:
            while True:
                coro.send(None)
        except (_StopLoop, StopIteration):
            coro.close()
    finally:
        asyncio.sleep, ss.time = real_sleep, time


def _check_loop_rate() -> None:
    import server.server as ss

    seconds = 2.0
    for rate in (20, 40, 120):
        n_ticks = 0

        def update_all():
            nonlocal n_ticks
            n_ticks += 1

        server = object.__new__(ss.GameServer)
        server.tick_rate = rate
        server.tick_interval = tick_interval(rate)
        server.running = True
        server.lobby = SimpleNamespace(update_all=update_all)
        server.network = SimpleNamespace(tick=lambda: None)
        server._broadcast_parties = lambda: None
        server._remove_stale_players = lambda: None

        clock = _QuantizedClock(seconds)
        _run_server_loop(server, clock)
        measured = n_ticks / seconds
        assert abs(measured - rate) <= rate * 0.02, \
            f"{rate} tps запрошено, {measured:.2f} tps получено"

        # Карусель — это ПОДРЯД идущие неположительные ожидания; одиночное
        # sleep(0) на границе тика допустимо.
        run = worst = 0
        for delay in clock.sleeps:
            run = run + 1 if delay <= 0.0 else 0
            worst = max(worst, run)
        assert worst <= 1, f"sleep(0)-карусель при {rate} tps: {worst}"
        print(f"1.4 цикл {rate:3d} tps -> {measured:6.2f} tps, "
              f"максимум {worst} ожиданий <= 0 OK")


def _capture_hello(server, protocol: int, version: str) -> dict:
    captured: dict = {}
    server.releases = SimpleNamespace(latest=lambda: None)
    server.network.client_info = {}
    server.network.send_hello_ack = lambda addr, **kwargs: captured.update(kwargs)
    server._on_hello(('127.0.0.1', 9999), protocol, version)
    return captured


def _client_from_ack(payload: bytes):
    from client.network_client import NetworkClient

    client = object.__new__(NetworkClient)
    client._hello_event = None
    client.tick_rate = DEFAULT_TICK_RATE
    client._handle_hello_ack(payload)
    return client


def _check_hello_and_client() -> None:
    from server.server import GameServer
    from client.network_client import NetworkClient

    assert PROTOCOL_VERSION == 16
    server = GameServer(tick_rate=40)
    assert server.tick_rate == 40
    assert server.tick_interval == tick_interval(40)
    assert server.lobby.tick_rate == 40
    assert server.lobby.create_party('fps').world.tick_rate == 40

    captured = _capture_hello(server, PROTOCOL_VERSION, CLIENT_VERSION)
    assert captured['accepted'] is True
    assert captured['tick_rate'] == 40
    payload = encode_hello_ack(**captured)
    decoded = decode_hello_ack(payload)
    assert decoded['tick_rate'] == 40
    assert decoded['server_protocol'] == PROTOCOL_VERSION

    # Поле дописано в конец: без последнего байта старый разбор читает те же
    # поля обновления и деградирует до частоты по умолчанию.
    legacy = decode_hello_ack(payload[:-1])
    assert legacy['tick_rate'] == DEFAULT_TICK_RATE
    for key in ('server_protocol', 'accepted', 'latest_client_version',
                'has_update', 'update_version', 'update_size', 'update_sha256'):
        assert legacy[key] == decoded[key], key

    with_update = encode_hello_ack(
        PROTOCOL_VERSION, True, "9.9.9", has_update=True,
        update_version="9.9.9", update_size=12345, update_sha256="a" * 64,
        tick_rate=40,
    )
    du = decode_hello_ack(with_update)
    assert du['tick_rate'] == 40 and du['has_update'] is True
    assert du['update_size'] == 12345 and du['update_sha256'] == "a" * 64
    assert with_update[-1] == 40

    # Старый клиент отвергается существующей проверкой версии.
    old = _capture_hello(GameServer(tick_rate=40), PROTOCOL_VERSION - 1,
                         CLIENT_VERSION)
    assert old['accepted'] is False and old['tick_rate'] == 40

    client = _client_from_ack(payload)
    assert client.tick_rate == 40
    assert client.hello_ack['server_protocol'] == PROTOCOL_VERSION

    # Интерполяция использует объявленную частоту: при 40 tps цель отстоит
    # на 25 мс, при 20 tps — на 50 мс.
    def state(x: float) -> tuple:
        p = {'id': 7, 'x': x, 'y': 0.0, 'angle': 0.0}
        return (0, [p], [], [], [], 'pve', [], [], [])

    client.player_id = None
    client.snapshots = [(0.0, state(100.0)), (0.05, state(120.0))]
    x40 = client.interpolated_state(0.10)[1][0]['x']
    client.tick_rate = 20
    x20 = client.interpolated_state(0.10)[1][0]['x']
    assert abs(x40 - 130.0) < 1e-6, x40
    assert abs(x20 - 120.0) < 1e-6, x20
    print("1.5 HELLO_ACK: версия 16, поле в конце, старый клиент отвергнут OK")
    print(f"1.6 клиент: интерполяция 40 tps -> x={x40:.1f}, "
          f"20 tps -> x={x20:.1f} OK")

    # Мутация «клиент игнорирует объявленную частоту» обязана ломать проверку.
    def assert_declared_rate(expected: int) -> None:
        assert _client_from_ack(payload).tick_rate == expected

    assert_declared_rate(40)  # эталон проходит
    original = NetworkClient._handle_hello_ack

    def ignoring_rate(self, data: bytes) -> None:
        self.hello_ack = decode_hello_ack(data)

    NetworkClient._handle_hello_ack = ignoring_rate
    try:
        try:
            assert_declared_rate(40)
        except AssertionError:
            pass
        else:
            raise AssertionError("мутация не сломала проверку частоты")
        mutated = _client_from_ack(payload)
    finally:
        NetworkClient._handle_hello_ack = original
    assert mutated.tick_rate == DEFAULT_TICK_RATE
    print("1.7 мутация «клиент игнорирует tick_rate» ломает проверку OK")


def _check_client_timing() -> None:
    from client.client import GameClient

    # Время матча в HUD: 120 тиков при 40 tps — это 3 секунды, а не 6.
    c = object.__new__(GameClient)
    c.network = SimpleNamespace(tick_rate=40)
    c.local_stats = {}
    c.secrets_total = 0
    c.match_phase = 'dying'
    c._match_tick = 120
    stats = dict(c._end_model()['stats'])
    assert stats['Время'] == '0:03', stats
    c.network.tick_rate = 20
    assert dict(c._end_model()['stats'])['Время'] == '0:06'
    print("1.8 время матча в HUD считается от объявленной частоты OK")


if __name__ == '__main__':
    _check_tick_helpers()
    _check_cli()
    _check_world_rates()
    _check_loop_rate()
    _check_hello_and_client()
    _check_client_timing()
    print("ALL SERVER FPS SMOKE TESTS PASSED")
