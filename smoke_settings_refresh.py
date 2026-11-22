"""Smoke: открытие списка партий live в Настройках, а не в лобби.

Проверяет три вещи:

1. в лобби больше нет кнопки «Обновить» и клавиши R;
2. в настройках есть кнопка/клавиша R, она возвращает действие ``refresh``,
   а в паузе (из матча) скрыта;
3. клиент по этому действию шлёт ``GET_LOBBY`` и возвращается в лобби.

Run: ``py -3.12 smoke_settings_refresh.py``.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pygame  # noqa: E402

pygame.init()
pygame.display.set_mode((1000, 700))

import client.settings as settings_mod  # noqa: E402

_TMP = tempfile.mkdtemp(prefix="rout3-settings-refresh-")
settings_mod.CONFIG_PATH = os.path.join(_TMP, "config.json")

from client.client import GameClient  # noqa: E402
from client.lobby_screen import LobbyScreen  # noqa: E402
from client.settings import Settings  # noqa: E402
from client.settings_screen import SettingsScreen  # noqa: E402


def _click(rect_or_pos) -> pygame.event.Event:
    pos = rect_or_pos.center if hasattr(rect_or_pos, "center") else rect_or_pos
    return pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos)


def _key(key: int) -> pygame.event.Event:
    return pygame.event.Event(pygame.KEYDOWN, key=key, unicode="")


def test_screens() -> None:
    lobby = LobbyScreen(1000, 700, "Player", "localhost:7777")
    assert "refresh" not in lobby.buttons
    assert not hasattr(lobby, "refresh_btn")
    assert lobby.handle([_key(pygame.K_r)]) is None, \
        "R в лобби всё ещё обновляет список"
    print("  lobby has no refresh button / R shortcut             OK")

    settings = Settings()
    screen = SettingsScreen(1000, 700)
    assert screen.refresh_enabled and screen.refresh_btn.width > 0
    old_center = screen.refresh_btn.center
    assert screen.handle([_click(old_center)], settings) == "refresh"
    assert screen.handle([_key(pygame.K_r)], settings) == "refresh"
    print("  settings button and R both return 'refresh'          OK")

    screen.set_refresh_enabled(False)
    assert screen.refresh_btn.width == 0
    assert screen.handle([_click(old_center)], settings) is None, \
        "скрытая кнопка всё ещё кликабельна"
    assert screen.handle([_key(pygame.K_r)], settings) is None, \
        "R в паузе всё ещё обновляет список"
    assert screen.handle([_key(pygame.K_ESCAPE)], settings) == "back"
    print("  refresh hidden and inert in pause settings           OK")


async def _pump_settings(client: GameClient) -> None:
    original = pygame.event.get
    events = [_click(client.settings_screen.refresh_btn.center)]
    pygame.event.get = lambda: [events.pop(0)] if events else []
    task = asyncio.ensure_future(client._settings_loop(from_pause=False))
    try:
        for _ in range(300):
            await asyncio.sleep(0.01)
            if task.done():
                break
        else:
            raise AssertionError("settings loop did not return after refresh")
        await asyncio.wait_for(asyncio.shield(task), 1.0)
    finally:
        pygame.event.get = original
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


async def test_client_wiring() -> None:
    client = GameClient("127.0.0.1", 7777, "Refresh")
    client.running = True
    client._ui = "settings"
    calls: list[str] = []
    client.network.send_get_lobby = lambda: calls.append("get_lobby")

    await _pump_settings(client)

    assert calls == ["get_lobby"], calls
    assert client._ui == "lobby", client._ui
    print("  settings refresh sends GET_LOBBY, returns to lobby   OK")


async def main() -> None:
    test_screens()
    await test_client_wiring()


if __name__ == "__main__":
    asyncio.run(main())
    print("smoke_settings_refresh: OK")
