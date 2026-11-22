"""Smoke: saved maps are selectable from the lobby.

Sections:

1. One catalogue: deduplicated names, ``.mts`` priority, safe names, errors.
2. Lobby picker geometry and pointer/keyboard actions at any window size.
3. Selection state, party creation, clear, F5, restart and bad maps.

Run: ``py -3.12 smoke_lobby_map_picker.py``
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

pygame.init()
SCREEN = pygame.display.set_mode((1000, 700))

from client import map_library  # noqa: E402
from shared import text_map  # noqa: E402
from shared.sector_map import MAP_FORMAT_SECTOR, SectorMap  # noqa: E402

REAL_MAPS_DIR = map_library.MAPS_DIR
TMP = tempfile.mkdtemp(prefix="rout3-lobby-picker-")
MAPS = os.path.join(TMP, "maps")
os.makedirs(MAPS, exist_ok=True)
map_library.MAPS_DIR = MAPS


def _make_sm(spawn_x: float):
    """A minimal valid sector map with a distinguishable player spawn."""
    from client.sector_editor import SectorEditor, new_room

    ed = SectorEditor(new_room(800, 800))
    ed.things.clear()
    ed.add_thing(spawn_x, 100.0, "player1")
    sm = ed.sm
    assert not sm.validate(), sm.validate()
    return sm


def _fixture(name: str, spawn_x: float, binary_x: float | None = None) -> bytes:
    """Write a pair of maps; ``.mt1`` may hold a different spawn on purpose."""
    sm = _make_sm(spawn_x)
    text_map.save(sm, os.path.join(MAPS, f"{name}.mts"))
    raw = (_make_sm(binary_x) if binary_x is not None else sm).to_bytes()
    with open(os.path.join(MAPS, f"{name}.mt1"), "wb") as fh:
        fh.write(raw)
    return raw


def _spawn(payload: bytes) -> float:
    return SectorMap.from_bytes(payload).things[0].x


def section_catalog() -> None:
    # The editor snaps to its grid, so compare against the constructed models
    # instead of the requested coordinates.
    text_spawn = _make_sm(111.0).things[0].x
    binary_spawn = _make_sm(555.0).things[0].x
    assert text_spawn != binary_spawn
    _fixture("both", 111.0, binary_x=555.0)
    mt1_only = _fixture("only_binary", 222.0)
    # Removing the .mts leaves a binary-only name in the catalogue.
    os.remove(os.path.join(MAPS, "only_binary.mts"))
    with open(os.path.join(MAPS, "broken.mts"), "w", encoding="utf-8") as fh:
        fh.write("это не карта\n")

    names = map_library.list_map_names()
    assert names == ["both", "broken", "only_binary"], names
    print("catalogue: .mts/.mt1 names deduplicated and sorted OK")

    got_name, payload, errs = map_library.load("both")
    assert got_name == "both" and not errs, (got_name, errs)
    assert _spawn(payload) == text_spawn, ".mts must win over .mt1 for the same name"
    assert not SectorMap.validate_bytes(payload), "loaded .mts must be wire-valid"
    got_name, payload, errs = map_library.load("only_binary")
    assert got_name == "only_binary" and not errs and payload == mt1_only, errs

    # The editor's dropdown offers the very same names, and a binary-only one
    # opens when clicked (it used to say "не найдено" because it read only .mts).
    from client.sector_editor_screen import SectorEditorScreen
    scr = SectorEditorScreen(1000, 700)
    scr._map_files = map_library.list_map_names()
    scr._map_list_open = True
    scr.draw(SCREEN)
    rows = dict(scr._map_rows)
    assert "only_binary" in rows, rows
    scr.handle([pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1,
                                   pos=rows["only_binary"].center)])
    assert scr.map_name == "only_binary", scr.map_name
    assert "Загружено" in scr.status, scr.status
    assert _spawn(scr.editor.sm.to_bytes()) == _spawn(mt1_only)
    print("catalogue: .mts wins, .mt1-only maps load from the editor list OK")

    for bad in ("../evil", "sub/name", "..", ""):
        got_name, payload, errs = map_library.load(bad)
        assert payload is None and errs, (bad, payload, errs)
    got_name, payload, errs = map_library.load("broken")
    assert payload is None and errs, (payload, errs)
    print("catalogue: path escape refused, broken map reports an error OK")


def _click(rect) -> pygame.event.Event:
    return pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1,
                              pos=(rect.centerx, rect.centery))


def section_ui() -> None:
    """One geometry for drawing and clicking; gestures scroll, never select."""
    import types

    from client import client as client_mod
    from client.lobby_screen import NO_MAP, LobbyScreen
    from client.ui_layout import ellipsize
    from engine import constants as const

    lb = LobbyScreen(1000, 700, "Player", "localhost:7777")

    # The button opens the list and a second click closes it; rows are the
    # painted rectangles, so a centre click picks exactly the item drawn.
    assert lb.handle([_click(lb.map_btn)]) is None and lb._map_list_open
    lb.draw(SCREEN)
    rows = dict(lb._map_dropdown_geometry()[1])
    assert list(rows) == [NO_MAP, "both", "broken", "only_binary"], list(rows)
    assert lb.handle([_click(rows["both"])]) == "map:both"
    assert not lb._map_list_open
    lb.handle([_click(lb.map_btn)])
    lb.draw(SCREEN)
    rows = dict(lb._map_dropdown_geometry()[1])
    assert lb.handle([_click(rows[NO_MAP])]) == "clear_map"
    assert not lb._map_list_open

    # A click outside the overlay only closes it and activates no lobby button.
    lb.handle([_click(lb.map_btn)])
    assert lb.handle([_click(lb.create_btn)]) is None and not lb._map_list_open
    assert lb.handle([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_c,
                                         unicode="c")]) == "clear_map"

    # Wheel and keyboard move the scroll, never emit a selection. The fixture
    # catalogue is short, so pad it to make overflow reachable.
    lb.handle([_click(lb.map_btn)])
    # Opening reads the catalogue, so pad AFTER the click.
    lb._map_names += [f"filler-{i:02d}" for i in range(30)]
    before = lb._map_scroll
    assert lb.handle([pygame.event.Event(pygame.MOUSEWHEEL, y=-1)]) is None
    assert lb._map_scroll > before
    assert lb.handle([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_UP,
                                         unicode="")]) is None
    assert lb._map_scroll == before
    assert lb.handle([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE,
                                         unicode="")]) is None
    assert not lb._map_list_open

    # A vertical finger swipe over a row is translated by the real menu path:
    # it becomes wheel notches and must not fire the row under the thumb.
    stub = client_mod.GameClient.__new__(client_mod.GameClient)
    stub._menu_touch = {}
    stub._menu_touch_step = 44
    stub.renderer = types.SimpleNamespace(
        to_canvas_pos=lambda pos: (int(pos[0]), int(pos[1])))
    old_w, old_h = const.W, const.H
    const.W, const.H = 1000, 700
    try:
        lb._map_scroll = 0
        lb.handle([_click(lb.map_btn)])
        lb.draw(SCREEN)
        row = dict(lb._map_dropdown_geometry()[1])["both"]
        finger = lambda kind, y: pygame.event.Event(  # noqa: E731
            kind, finger_id=3, x=row.centerx / const.W, y=y / const.H,
            dx=0.0, dy=0.0)
        raw = [finger(client_mod.FINGERDOWN, row.centery)]
        for delta in (-50, -100):
            raw.append(finger(client_mod.FINGERMOTION, row.centery + delta))
        raw.append(finger(client_mod.FINGERUP, row.centery - 100))
        mapped = stub._menu_events(raw)
        assert [e.type for e in mapped] == [pygame.MOUSEWHEEL] * 2, [
            e.type for e in mapped]
        assert lb.handle(mapped) is None, "a swipe must not select a map row"
        assert lb._map_list_open, "a swipe keeps the list open"
    finally:
        const.W, const.H = old_w, old_h
    print("lobby picker: open/close, row actions, reset and swipe OK")

    # Error and long-name text live in the map band, cut to the space left of
    # the picker button instead of running under it.
    band = lb.bottom_bands()["map"]
    text_w = band.width - lb.map_btn.width - 24
    long_name = "очень-длинное-имя-карты-" * 4
    cut = ellipsize(f"Карта: {long_name}", lb.small_font, text_w)
    assert lb.small_font.size(cut)[0] <= text_w and cut.endswith("…"), cut
    lb.set_custom_map("both", "не удалось загрузить")
    SCREEN.fill((0, 0, 0))
    lb.draw(SCREEN)
    accent = (210, 70, 70)
    assert any(SCREEN.get_at((x, band.y + band.height // 2))[:3] == accent
               for x in range(band.x, band.right)), "the error must be visible"
    lb.set_custom_map(None)
    print("lobby picker: map band holds an error and cuts long names OK")


def _open_rows(lb) -> dict:
    """Rows the picker will show; leave it closed so a click can open it."""
    lb._map_names = map_library.list_map_names()
    lb._map_scroll = 0
    lb._map_list_open = True
    lb.draw(SCREEN)
    rows = dict(lb._map_dropdown_geometry()[1])
    lb._map_list_open = False
    return rows


async def _pump(client, *events, wait_game: bool = False) -> None:
    """Run one lobby loop on a scripted event stream, then restore pygame."""
    original = pygame.event.get
    queued = list(events)
    pygame.event.get = lambda: [queued.pop(0)] if queued else []
    task = asyncio.ensure_future(client._lobby_loop())
    try:
        for _ in range(300):
            await asyncio.sleep(0.01)
            if (wait_game and client.network.state == "game") or not client.running:
                break
        else:
            raise AssertionError("lobby loop did not finish")
        await asyncio.wait_for(asyncio.shield(task), 1.0)
    finally:
        pygame.event.get = original
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


async def section_state() -> None:
    """The lobby choice, not a file read at create time, feeds CREATE_PARTY."""
    import client.settings as settings_mod
    from client.client import GameClient
    from server.server import GameServer

    old_config = settings_mod.CONFIG_PATH
    settings_mod.CONFIG_PATH = os.path.join(TMP, "config.json")
    server = GameServer(host="127.0.0.1", port=7793)
    task = asyncio.ensure_future(server.start())
    await asyncio.sleep(0.3)
    real_get = pygame.event.get
    clients = []
    try:
        quit_event = pygame.event.Event(pygame.QUIT)
        creator = GameClient("127.0.0.1", 7793, "Picker")
        clients.append(creator)
        creator.running = True
        creator.renderer.set_window_size((1000, 700))
        assert await creator.network.connect("Picker"), creator.network.last_error
        lb = creator.lobby_screen

        # Choose through the real list: button -> row -> selection.
        lb.draw(SCREEN)
        row = _open_rows(lb)["both"]
        await _pump(creator, _click(lb.map_btn), _click(row), quit_event)
        assert creator.map_selection is not None
        assert creator.map_selection.name == "both"
        chosen = map_library.load("both")[1]
        assert creator.map_selection.payload == chosen
        assert lb.custom_map_label == "both"

        # A damaged map must not replace a good choice; the error is shown in
        # the map band while the previous label stays visible.
        row = _open_rows(lb)["broken"]
        creator.running = True
        await _pump(creator, _click(lb.map_btn), _click(row), quit_event)
        assert creator.map_selection.name == "both"
        assert creator.map_selection.payload == chosen
        assert lb.custom_map_label == "both" and lb.custom_map_error

        # F5 and the lobby write the very same field.
        creator.running = True
        creator._ui = "editor"
        creator.sector_editor_screen.map_name = "f5_picker"
        f5 = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F5, unicode="")
        pygame.event.get = lambda: [f5]
        try:
            await creator._editor_loop()
        finally:
            pygame.event.get = real_get
        assert creator.map_selection.name == "f5_picker"

        # C clears, and the next selection is used for a real party.
        creator.running = True
        await _pump(creator, pygame.event.Event(pygame.KEYDOWN,
                                                key=pygame.K_c, unicode="c"),
                    quit_event)
        assert creator.map_selection is None and lb.custom_map_label is None
        creator.running = True
        row = _open_rows(lb)["both"]
        await _pump(creator, _click(lb.map_btn), _click(row), quit_event)
        # The visible button, not just the P shortcut, must reach CREATE_PARTY.
        # A server-side exception here used to be swallowed as a malformed
        # datagram, leaving the lobby stuck with no error shown to the player.
        creator.running = True
        await _pump(creator, _click(lb.create_btn), wait_game=True)
        assert server.lobby.parties, "create button never reached the server"
        assert creator.network.map_format == MAP_FORMAT_SECTOR
        assert creator.network.map_data == chosen

        # A joiner's own local choice is irrelevant: it gets the creator's map.
        joiner = GameClient("127.0.0.1", 7793, "Joiner")
        clients.append(joiner)
        joiner.running = True
        joiner.renderer.set_window_size((1000, 700))
        assert await joiner.network.connect("Joiner"), joiner.network.last_error
        joiner._select_map("only_binary")
        assert joiner.map_selection.name == "only_binary"
        party_id = next(iter(server.lobby.parties))
        joiner.network.send_join_party(party_id)
        await _pump(joiner, wait_game=True)
        assert joiner.network.map_data == chosen, "joiner ignored the party map"
        assert joiner.network.map_format == MAP_FORMAT_SECTOR
        assert joiner.map_selection.name == "only_binary"

        # Without a selection CREATE_PARTY carries no sector map.
        plain = GameClient("127.0.0.1", 7793, "Plain")
        clients.append(plain)
        plain.running = True
        plain.renderer.set_window_size((1000, 700))
        assert await plain.network.connect("Plain"), plain.network.last_error
        assert plain.map_selection is None
        await _pump(plain, pygame.event.Event(pygame.KEYDOWN,
                                              key=pygame.K_p, unicode="p"),
                    wait_game=True)
        assert plain.network.map_format != MAP_FORMAT_SECTOR
        print("client: lobby/F5 selection, bad map, clear and party wire OK")
    finally:
        pygame.event.get = real_get
        assert not os.path.exists(os.path.join(TMP, "config.json")), \
            "map selection must not be persisted between runs"
        settings_mod.CONFIG_PATH = old_config
        for client in clients:
            client.network.disconnect()
            client.renderer.cleanup()
        server.stop()
        await asyncio.sleep(0.05)
        try:
            await task
        except asyncio.CancelledError:
            pass


def main() -> None:
    try:
        section_catalog()
        section_ui()
        asyncio.run(section_state())
    finally:
        map_library.MAPS_DIR = REAL_MAPS_DIR
        shutil.rmtree(TMP, ignore_errors=True)
    print("smoke_lobby_map_picker: ALL OK")


if __name__ == "__main__":
    main()
