"""Client end screens: death + victory overlays, and the input gate.

The results screens are drawn entirely from the authoritative snapshot, so the
things worth pinning here are the crossings between client code and the wire:

* the ``PLAYER_RESPAWN`` request really rides the reliable channel, and only
  while a party is live;
* the trailing match block is parsed into ``latest_match`` verbatim;
* the model (title, tone, table order, accuracy) is derived from the snapshot,
  not from any local guess;
* the single input gate ``_input_enabled()`` closes while dead/over and while
  paused, so no held key keeps feeding a finished player;
* ``Enter`` is a menu key, not a game input -- it must not fire or move.

Run: ``py -3.12 smoke_end_screens.py``
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

pygame.init()
pygame.display.set_mode((1000, 700))

import client.settings as settings_mod

_tmp = tempfile.mkdtemp()
settings_mod.CONFIG_PATH = os.path.join(_tmp, "config.json")

from client.client import GameClient
from client.end_screen import EndScreen
from client.input_handler import InputHandler
from client.network_client import NetworkClient
from shared.protocol import (
    Packet, PacketType, decode_match_block, encode_world_state,
)

failures: list[str] = []


def check(ok: bool, message: str) -> None:
    if not ok:
        failures.append(message)


def _player(pid, name, x=100.0, y=100.0, health=100, state=0, stats=None):
    return {
        "id": pid, "name": name, "x": x, "y": y, "angle": 0.0,
        "health": health, "state": state, "wpn": 1,
        "ammo": {}, "stats": stats or {},
    }


# ── wire: respawn request ─────────────────────────────────────────
class _Endpoint:
    def __init__(self):
        self.sent: list[tuple] = []

    def send_reliable(self, ptype, payload):
        self.sent.append((ptype, payload))


def test_send_respawn_uses_reliable_channel() -> None:
    net = NetworkClient("localhost", 7777)
    ep = _Endpoint()
    net._endpoint = ep
    net.state = "lobby"
    net.send_respawn()
    check(ep.sent == [], "respawn sent while not in a game")
    net.state = "game"
    net.send_respawn()
    check(len(ep.sent) == 1, "respawn not sent in a game")
    if ep.sent:
        check(ep.sent[0][0] == PacketType.PLAYER_RESPAWN,
              f"respawn used {ep.sent[0][0]!r}, want PLAYER_RESPAWN")


def test_latest_match_parsed_from_snapshot() -> None:
    net = NetworkClient("localhost", 7777)
    payload = encode_world_state(
        tick=7, players=[_player(1, "P", stats={"kills": 4})],
        mode="pvp",
        match={"phase": "over", "winner_id": 3, "time_left": 0,
               "secrets_total": 5},
    )
    net._handle_world_state(Packet(PacketType.WORLD_STATE, 0, payload))
    lm = net.latest_match
    check(lm["phase"] == "over", f"phase {lm['phase']!r}")
    check(lm["winner_id"] == 3, f"winner {lm['winner_id']}")
    check(lm["secrets_total"] == 5, f"secrets_total {lm['secrets_total']}")
    check(decode_match_block(payload)["phase"] == "over",
          "decode_match_block disagrees with the client field")


# ── client model ──────────────────────────────────────────────────
client = GameClient()


def _armed_client(**kw) -> GameClient:
    """The shared client, in a party-looking state for model tests."""
    for k, v in kw.items():
        setattr(client, k, v)
    return client


def test_accuracy_pct_boundary() -> None:
    _armed_client(local_stats={"shots_fired": 0, "shots_hit": 0})
    check(client._accuracy_pct() == 0, "accuracy with no shots must be 0%")
    _armed_client(local_stats={"shots_fired": 4, "shots_hit": 1})
    check(client._accuracy_pct() == 25, f"1/4 -> {client._accuracy_pct()}%")
    _armed_client(local_stats={"shots_fired": 3, "shots_hit": 3})
    check(client._accuracy_pct() == 100, "3/3 must be 100%")


def test_death_model() -> None:
    client.bridge = object()
    _armed_client(match_phase="playing", local_health=0, local_state=1,
                  mode="pve", local_stats={"kills": 2, "deaths": 1})
    check(client._end_active(), "dead player must raise the overlay")
    m = client._end_model()
    check(m["kind"] == "death", f"kind {m['kind']!r}")
    check(m["title"] == "ВЫ ПОГИБЛИ", f"title {m['title']!r}")
    check(m["primary"] == "respawn", f"primary {m['primary']!r}")
    check([b[0] for b in m["buttons"]] == ["respawn"], "death button set")
    check(m["escapable"] is True, "death overlay must be escapable")


def test_pve_victory_model() -> None:
    client.bridge = object()
    _armed_client(match_phase="over", local_health=100, local_state=0,
                  mode="pve")
    m = client._end_model()
    check(m["kind"] == "victory", f"kind {m['kind']!r}")
    check(m["title"] == "УРОВЕНЬ ПРОЙДЕН", f"title {m['title']!r}")
    check(m["tone"] == "win", f"tone {m['tone']!r}")
    check([b[0] for b in m["buttons"]] == ["leave"], "victory button set")


def test_pvp_titles() -> None:
    client.bridge = object()
    _armed_client(match_phase="over", local_health=100, local_state=0,
                  mode="pvp", player_id=2)
    client.match_winner = 2
    check(client._end_model()["title"] == "ПОБЕДА", "winner=self must be a win")
    client.match_winner = 9
    check(client._end_model()["title"] == "ПОРАЖЕНИЕ", "other winner must be a loss")
    client.match_winner = 0
    check(client._end_model()["title"] == "НИЧЬЯ", "no winner must be a draw")


def test_pvp_table_sorted_highlight() -> None:
    client.bridge = object()
    _armed_client(
        match_phase="over", local_health=100, local_state=0, mode="pvp",
        player_id=2,
        _players_snapshot=[
            _player(2, "Me", stats={"kills": 1, "deaths": 4}),
            _player(5, "Top", stats={"kills": 7, "deaths": 0}),
            _player(1, "Mid", stats={"kills": 3, "deaths": 2}),
        ],
    )
    m = client._end_model()
    rows = m["table"]["rows"]
    check([r[0] for r in rows] == ["Top", "Mid", "Me"],
          f"table not sorted by kills: {rows}")
    check(m["table"]["highlight"] == 2, f"highlight {m['table']['highlight']}!="
          "the local row")
    check(m["table"]["headers"] == ["Игрок", "Убийства", "Смерти"],
          "table headers changed")


def test_input_gate() -> None:
    client.bridge = object()
    _armed_client(match_phase="playing", local_health=100, local_state=0,
                  _paused=False)
    check(client._input_enabled(), "healthy playing client must send input")
    _armed_client(local_health=0, local_state=1)
    check(not client._input_enabled(), "dead client must not send input")
    _armed_client(local_health=100, local_state=0, match_phase="over")
    check(not client._input_enabled(), "finished match must not send input")
    _armed_client(match_phase="playing", _paused=True)
    check(not client._input_enabled(), "paused client must not send input")
    _armed_client(_paused=False)


def test_enter_is_not_gameplay_input() -> None:
    handler = InputHandler()
    pygame.event.clear()
    before_weapon = handler.weapon_id
    pygame.event.post(pygame.event.Event(
        pygame.KEYDOWN, key=pygame.K_RETURN, unicode="\r"))
    handler.update()
    check(not handler.shoot, "Enter must not fire")
    check(not handler.shoot_pressed, "Enter must not queue a shot")
    check(handler.move_x == 0 and handler.move_y == 0, "Enter must not move")
    check(handler.weapon_id == before_weapon, "Enter must not switch weapons")
    check(not handler.dash_pressed, "Enter must not dash")


def test_feed_engine_meta() -> None:
    from engine import game_state as gs
    client.bridge = object()
    _armed_client(
        match_phase="over", match_winner=2, match_time_left=0,
        secrets_total=4, _match_tick=400, player_id=2, mode="pvp",
        local_stats={"kills": 6, "secrets": 2, "shots_fired": 10,
                     "shots_hit": 5, "damage_dealt": 90, "damage_taken": 30,
                     "items": 3},
    )
    client._feed_engine_meta()
    check(gs.kills == 6, f"gs.kills {gs.kills}")
    check(gs.shots_fired == 10 and gs.shots_hit == 5, "gs shot counters")
    check(gs.secrets_total == 4 and gs.secrets_found == 2, "gs secrets")
    check(gs.dm_game_over is True, "gs.dm_game_over must follow the phase")
    check(gs.dm_winner == 2, f"gs.dm_winner {gs.dm_winner}")
    check(gs.items_picked == 3, f"gs.items_picked {gs.items_picked}")


# ── overlay hit-testing through EndScreen.handle ──────────────────
def test_overlay_button_hit() -> None:
    screen = EndScreen(1000, 700)
    screen.set_model({"title": "ПОБЕДА", "stats": [], "primary": "leave",
                      "buttons": [("leave", "В МЕНЮ")], "hint": ""})
    rect = screen.buttons[0]
    down = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1,
                              pos=(rect.centerx, rect.centery))
    check(screen.handle([down]) == "leave", "click on the button missed it")
    miss = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1,
                              pos=(rect.centerx, rect.centery - 300))
    check(screen.handle([miss]) is None, "click off the button activated one")


def test_overlay_enter_confirms() -> None:
    screen = EndScreen(1000, 700)
    screen.set_model({"title": "ВЫ ПОГИБЛИ", "stats": [], "primary": "respawn",
                      "buttons": [("respawn", "ВОЗРОДИТЬСЯ")],
                      "escapable": True, "hint": ""})
    enter = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN)
    check(screen.handle([enter]) == "respawn", "Enter must confirm the primary")
    esc = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE)
    check(screen.handle([esc]) == "leave", "Esc must leave when escapable")


def test_overlay_esc_ignored_when_not_escapable() -> None:
    screen = EndScreen(1000, 700)
    screen.set_model({"title": "X", "stats": [], "primary": "respawn",
                      "buttons": [("respawn", "R")], "escapable": False,
                      "hint": ""})
    esc = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE)
    check(screen.handle([esc]) is None, "Esc must not leave a locked overlay")


TESTS = [
    test_send_respawn_uses_reliable_channel,
    test_latest_match_parsed_from_snapshot,
    test_accuracy_pct_boundary,
    test_death_model,
    test_pve_victory_model,
    test_pvp_titles,
    test_pvp_table_sorted_highlight,
    test_input_gate,
    test_enter_is_not_gameplay_input,
    test_feed_engine_meta,
    test_overlay_button_hit,
    test_overlay_enter_confirms,
    test_overlay_esc_ignored_when_not_escapable,
]


def main() -> int:
    for t in TESTS:
        t()
        print(f"  ok  {t.__name__}")
    if failures:
        print(f"\n{len(failures)} FAILURES:")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("smoke_end_screens: ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
