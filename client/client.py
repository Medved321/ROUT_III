from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from collections import deque

import pygame

from shared.map_data import GameMap
from shared.sector_map import MAP_FORMAT_SECTOR, SectorMap
from shared.combat import (
    wpn_cooldown_ticks, TICK_INTERVAL, FPS_60, TAU, normalize_angle,
    FIRE_REASON_CODES, USE_REASON_LOCKED)
from shared.protocol import INPUT_FLAG_DASH, INPUT_FLAG_USE
from client.raycaster import SCREEN_WIDTH, SCREEN_HEIGHT
from client.renderer import Renderer
from client.bridge import RenderBridge
from client.input_handler import InputHandler
from client.network_client import NetworkClient
from client.assets import AssetManager
from client.lobby_screen import LobbyScreen
from client.connect_screen import ConnectScreen
from client.settings import (
    Settings, control_hints, is_tractor, save_server_settings, size_text)
from client.settings_screen import SettingsScreen
from client.pause_screen import PauseScreen
from client.end_screen import EndScreen
from client.editor_screen import MapEditorScreen
from engine.constants import WPN, INPUT_SEND_INTERVAL
from client.performance import AdaptiveQuality, input_send_loop
from client.updater import (
    UpdateError, apply_update, default_releases_dir, restart, validate_offer)
from client import music, sfx

logger = logging.getLogger(__name__)

UPDATE_TIMEOUT = 30.0
# The client's fire pace comes from the server's own tick cooldown plus one tick
# of slack, so an intent is sent on a gate opening only — and only while the
# button is actually down. A click that lands on a closed gate is eaten, the way
# a hot weapon eats a click in any shooter: there is no queue of pending shots,
# so nothing can grow with the click rate and nothing fires minutes after the
# player let go (the SSG's 1.30 s gate against a 10 Hz clicker used to queue ~39
# shots for three seconds).
FIRE_REASON_COOLDOWN = FIRE_REASON_CODES['cooldown']
# How long the "нужен ключ" message stays on screen after the player presses E.
USE_HINT_SECONDS = 1.5
# An item that vanishes within this radius of the local player reads as "I picked
# it up"; a farther disappearance (an ally's pickup) gets no sound. The server's
# own pickup radius is half a tile, but the snapshot's item list is global, so the
# client needs its own, slightly more forgiving, distance test.
SFX_PICKUP_RADIUS_SQ = 72.0 * 72.0


def _angle_diff(a: float, b: float) -> float:
    """Signed shortest delta (a - b) in radians, normalized to (-pi, pi]."""
    d = (a - b) % TAU
    if d > math.pi:
        d -= TAU
    return d


class GameClient:
    def __init__(self, host: str = "localhost", port: int = 7777, player_name: str = "Player"):
        self.host = host
        self.port = port
        self.player_name = player_name

        self.network = NetworkClient(host, port)
        self.renderer = Renderer()
        self.renderer.on_resize = self._on_window_resized
        self.input_handler = InputHandler()
        self.assets = AssetManager()
        self.connect_screen = ConnectScreen(
            SCREEN_WIDTH, SCREEN_HEIGHT, host, port, player_name
        )
        self.lobby_screen = LobbyScreen(
            SCREEN_WIDTH, SCREEN_HEIGHT, player_name, f"{host}:{port}"
        )
        self.settings = Settings()
        self.settings.load()
        self.settings_screen = SettingsScreen(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.pause_screen = PauseScreen(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.end_screen = EndScreen(SCREEN_WIDTH, SCREEN_HEIGHT)
        self.editor_screen = MapEditorScreen(SCREEN_WIDTH, SCREEN_HEIGHT)
        from client.sector_editor_screen import SectorEditorScreen
        self.sector_editor_screen = SectorEditorScreen(SCREEN_WIDTH, SCREEN_HEIGHT)
        self._editor_kind = "tile"  # 'tile' | 'sector'
        # The resize callback touches ``bridge``, so it must exist before the
        # first ``set_window_size``.
        self.bridge: RenderBridge | None = None
        self._apply_window()
        self.settings.apply_runtime(self.input_handler)
        self._sync_control_hints()
        self.fps_cap = int(self.settings.get("fps_cap"))
        # Config values are the best quality; AdaptiveQuality may only degrade
        # within bounds at runtime and never writes back to config.json.
        self._base_view_dist = int(self.settings.get("view_dist"))
        self.quality = AdaptiveQuality(
            int(self.settings.get("render_scale")),
            base_vs=int(self.settings.get("vs_scale")),
            target_fps=self.fps_cap,
        )
        self._input_task: asyncio.Task | None = None
        self._ui = "lobby"  # 'lobby' | 'editor' | 'settings'
        self._paused = False
        self.game_map: GameMap | None = None
        self.sector_map: SectorMap | None = None
        self.custom_map: GameMap | None = None
        self.custom_sector_map: SectorMap | None = None
        self.custom_map_name: str | None = None

        self.player_id: int | None = None
        self.local_x: float = 96.0
        self.local_y: float = 96.0
        self.local_angle: float = 0.0
        self.local_health: int = 200
        self.local_weapon: int = 1
        self.local_ammo: dict = {'pstl': 200, 'shl': 40, 'slg': 100, 'rck': 10, 'BFG': 5}
        # Authoritative world-state flag of the local player (3 = dashing),
        # consumed by the bridge for the own-dash feedback only.
        self.local_state: int = 0
        self.mode: str = 'pve'
        # Match outcome from the snapshot's trailing block, and the local
        # player's own counters from their record. Read verbatim -- the server
        # owns both -- so the end screens never show a value of their own.
        self.match_phase: str = 'playing'
        self.match_winner: int = 0
        self.match_time_left: int = 0
        self.secrets_total: int = 0
        self._match_tick: int = 0
        self.local_stats: dict | None = None
        self._players_snapshot: list[dict] = []
        self.impacts: list[dict] = []
        self.items: list[dict] = []
        self.doors: list[dict] = []
        self.local_keys: int = 0
        self.local_use_reason: int = 0
        # A "use" refusal is advisory and would otherwise sit in the snapshot
        # forever, so the HUD shows it only for a short spell after the player
        # actually pressed E (design D6). The window is armed on the press edge
        # and read by the renderer through the bridge.
        self._use_hint_until: float = 0.0
        self._prev_use: bool = False
        self.local_armor: dict | None = None
        self.local_powerup: str = ''

        self.other_players: list[dict] = []
        self.enemies: list[dict] = []
        self.bullets: list[dict] = []
        self.corpses: list[dict] = []
        # Sound-effect bookkeeping: what the previous snapshot already showed.
        # Kept here, not in client/sfx.py, because it is state of THIS game, not
        # of the mixer; the sfx module only knows how to make a noise.
        self._sfx_seen_impacts: set = set()
        self._sfx_seen_items: dict[int, tuple[float, float]] = {}
        self._sfx_items_ready = False
        self._sfx_seen_doors: set = set()
        self._sfx_prev_health: int | None = None
        self._sfx_prev_state = 0
        self.running = False
        # Weapon animation runs on a monotonic deadline, NOT on a frame counter:
        # its length in seconds is the server cooldown, at any frame rate. The
        # owner is remembered so a weapon switch cannot inherit a foreign
        # animation tail.
        self._fire_anim_until: float = 0.0
        self._fire_anim_wpn: int = -1
        # Time-based cooldown gate (shared tick tempo + one tick slack) so a
        # shown shot is never rejected by the server's cooldown.
        self._next_fire_time: float = 0.0
        # A click that lands on a closed gate is remembered for exactly one
        # opening — no more. It is a bool and not a queue on purpose: a queue
        # grows with the click rate and drains at the weapon's rate, so a slow
        # weapon (SSG, 1.30 s) turns a three-second burst into ~39 shots fired
        # long after the player let go. One buffered press keeps every click
        # meaningful on a weapon whose gate is longer than the click interval
        # (the shotgun's gate is 0.90 s, i.e. ~9 clicks at 10 Hz) and still has
        # nothing that can accumulate. Held fire never needs it: holding is one
        # intent per opening, which is auto-fire at the weapon's real rate.
        self._fire_press_buffered: bool = False
        #
        # Sent intents the server has not decided yet, oldest first: just their
        # own fire_seq. The server's decision is addressed to ONE of them, so
        # they have to be kept apart: the snapshot repeats the same
        # (fire_seq, fire_reason) pair in every tick until the next intent is
        # decided, and a decision must be spent on its own intent, never on
        # whatever is newest at the time. Order is the reliable channel's
        # delivery order, which is what makes the attribution in
        # _sync_fire_anim unambiguous.
        self._fire_pending: deque[int] = deque()
        # Authoritative fire decision from the snapshot: the last accepted fire
        # intent the server acknowledged, and the cooldown it reports. The
        # weapon animation is driven from these, not from the key press.
        self._server_fire_seq: int = 0
        self._server_fire_ready: int = 0
        # Dash intent is a one-shot pulse waiting for the next input packet.
        self._dash_pending: bool = False
        self._server_angle: float | None = None
        self._seen_first_state: bool = False
        # Frozen world frame shown under the pause menu (None while playing).
        self._pause_bg: pygame.Surface | None = None
        # Frozen world frame shown under the death/victory overlay; None while
        # the end screen is not up.
        self._end_bg: pygame.Surface | None = None

    # ── window size and control mode ─────────────────────────────────
    def _apply_window(self) -> None:
        """Apply the configured window size and keep the config in step.

        ``Settings.resolution`` refuses a bad value with a message instead of
        clamping it, so a hand-edited ``config.json`` cannot leave the player
        with an unexplained window. Whatever SDL actually gave us (rounded to
        even dimensions) is written back, so the file and the window agree.
        """
        size = self.settings.resolution()
        if size is None:
            size = (SCREEN_WIDTH, SCREEN_HEIGHT)
            logger.warning(
                f"Недопустимый размер окна в config.json: "
                f"{self.settings.resolution_error}; беру {size[0]}×{size[1]}")
        applied = self.renderer.set_window_size(size)
        text = size_text(*applied)
        if text != self.settings.window_size_text:
            self.settings.set("window_size", text)
            self.settings.save()

    def _on_window_resized(self, size) -> None:
        """Renderer callback: the window surface is a new object after a
        resize, so the bridge (which blits the engine frame onto it) has to be
        re-subscribed."""
        if self.bridge is not None:
            self.bridge.set_output_surface(self.renderer.screen)
        logger.info(f"Окно: {size[0]}×{size[1]}")

    def _menu_events(self, events) -> list:
        """Re-express pointer positions in menu-canvas coordinates.

        Menus are laid out on the fixed 1000x700 canvas and scaled into the
        window on the way out, but pygame reports ``event.pos`` in window pixels.
        Feeding that straight into a screen hit-tests window pixels against
        canvas geometry, so at any window size other than 1000x700 a click lands
        somewhere the player did not point -- and the bigger the window, the
        worse the drift. Every menu therefore gets its events through here.

        Events without a position (wheel, keys) are passed through untouched.
        A point in the black bars is dropped: it belongs to no control, so it
        must not activate one. ``InputHandler`` deliberately does *not* go
        through this -- mouse-look happens in the 3D scene, which lives in
        window pixels.
        """
        out = []
        for event in events:
            pos = getattr(event, "pos", None)
            if pos is None:
                out.append(event)
                continue
            mapped = self.renderer.to_canvas_pos(pos)
            if mapped is None:
                continue
            attrs = dict(getattr(event, "dict", None) or {})
            attrs["pos"] = (int(mapped[0]), int(mapped[1]))
            out.append(pygame.event.Event(event.type, attrs))
        return out

    def _sync_control_hints(self) -> None:
        mode = self.settings.get("control_mode")
        self.lobby_screen.control_hints = control_hints(mode)

    def _apply_window_events(self) -> None:
        """Re-create the window after a player drag.

        The events come from the input handler, not from ``event.get()``: the
        handler drains the queue every frame, so a resize it did not forward
        would be lost. A drag produces a burst of events and only the last one
        matters, which caps the cost at one re-create per frame.
        """
        events = self.input_handler.window_events
        if not events:
            return
        self._apply_resizes(events)
        events.clear()

    def _apply_resizes(self, events) -> bool:
        """Apply at most one ``VIDEORESIZE`` per frame: the last one.

        A drag emits dozens of sizes a second and each applied size costs a new
        window surface plus a new full-screen ``Surface`` for the engine. The
        intermediate sizes are never displayed, so keeping them only burns
        frames. Returns whether the window actually changed.
        """
        last = None
        for event in events:
            if event.type == pygame.VIDEORESIZE:
                last = event
        if last is None:
            return False
        return self.renderer.handle_resize_event(last) is not None

    async def run(self) -> None:
        self.running = True
        self._ui = "lobby"
        connected = await self._connect_loop()
        if not connected or not self.running:
            self.network.disconnect()
            self.renderer.cleanup()
            return

        self.player_id = self.network.player_id
        asyncio.create_task(self.network.start_ping_loop())
        asyncio.create_task(self.network.start_reliable_loop())
        music.start(lambda: self.running)
        # Build the effect bank once, up front, so the first shot does not pay
        # for synthesis mid-fire. Silent if the mixer never came up.
        sfx.ensure()
        logger.info("Подключено. Выберите партию в меню.")

        if self.network.update_offer is not None and await self._maybe_update():
            music.stop()
            self.network.disconnect()
            self.renderer.cleanup()
            return

        # One dispatcher owns the screen, so it is also the only place that owns
        # the music context: a new screen added here cannot be forgotten, whereas
        # setting it inside each loop would be four places to keep in sync.
        while self.running:
            if self.network.state == "game":
                music.set_context("game")
                if self.bridge is None:
                    self._init_from_server()
                await self._game_loop()
            else:
                music.set_context("menu")
                if self._ui == "editor":
                    await self._editor_loop()
                elif self._ui == "settings":
                    await self._settings_loop(from_pause=False)
                else:
                    await self._lobby_loop()

        music.stop()
        self.network.disconnect()
        self.renderer.cleanup()

        self.network.disconnect()
        self.renderer.cleanup()

    async def _connect_loop(self) -> bool:
        self.input_handler.unlock_mouse()
        while self.running:
            events = pygame.event.get()
            for event in events:
                if event.type == pygame.QUIT:
                    self.running = False
                    return False
            action = self.connect_screen.handle(self._menu_events(events))
            if action == "quit":
                self.running = False
                return False
            if action == "connect":
                self.connect_screen.status = "Подключение…"
                self.connect_screen.draw(self.renderer.ui)
                self.renderer.present()
                pygame.display.flip()
                self.connect_screen.status = ""
                connected = await self._attempt_connect()
                if connected:
                    return True

            self.connect_screen.draw(self.renderer.ui)
            self.renderer.present()
            pygame.display.flip()
            self.renderer.clock.tick(60)
            await asyncio.sleep(0.005)
        return False

    async def _attempt_connect(self) -> bool:
        host = self.connect_screen.host.strip() or "localhost"
        try:
            port = int(self.connect_screen.port.strip())
        except ValueError:
            self.connect_screen.status = "Порт должен быть числом"
            return False
        name = self.connect_screen.name.strip() or "Player"

        new_network = NetworkClient(host, port)
        ok = await new_network.connect(name)
        if not ok:
            self.connect_screen.status = new_network.last_error or "Сервер недоступен"
            new_network.disconnect()
            return False

        if self.network is not None:
            self.network.disconnect()
        self.network = new_network
        self.host = host
        self.port = port
        self.player_name = name
        # Remember the address/name for the next launch; CLI overrides
        # (--host/--port/--name) are intentionally not written back.
        save_server_settings(host, port, name)
        self.lobby_screen = LobbyScreen(
            SCREEN_WIDTH, SCREEN_HEIGHT, name, f"{host}:{port}"
        )
        self._sync_control_hints()
        return True

    async def _maybe_update(self) -> bool:
        offer = self.network.update_offer
        if offer.version == self.settings.get("skipped_update_version"):
            logger.info(
                f"Версия {offer.version} уже отклонена ранее; предложение не показываем"
            )
            return False
        logger.info(
            f"Доступна новая версия клиента {offer.version} ({offer.size} байт)"
        )
        if not await self._confirm_update(offer):
            logger.info("Обновление отклонено; продолжаем на текущей версии")
            return False

        try:
            validate_offer(offer.version, offer.size, offer.sha256)
        except UpdateError as exc:
            logger.error(str(exc))
            return False

        self.network.request_update()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + UPDATE_TIMEOUT
        while self.network.update_data is None and self.running:
            if loop.time() > deadline:
                break
            await asyncio.sleep(0.02)

        data = self.network.update_data
        if not data:
            logger.error("Не удалось скачать обновление; продолжаем на текущей версии")
            return False

        try:
            path = apply_update(default_releases_dir(), offer.version, data, offer.sha256)
        except UpdateError as exc:
            logger.error(str(exc))
            self.network.send_file_complete(False)
            return False

        self.network.send_file_complete(True)
        await asyncio.sleep(0.05)
        try:
            restart(path)
        except OSError as exc:
            logger.error(f"Не удалось перезапустить клиент: {exc}")
            return False
        return True

    def _remember_skipped(self, version: str) -> None:
        self.settings.set("skipped_update_version", version)
        self.settings.save()

    async def _confirm_update(self, offer) -> bool:
        forced = os.environ.get("ROUT_UPDATE_ANSWER", "").lower()
        if forced in ("y", "yes", "1"):
            return True
        if forced in ("n", "no", "0"):
            self._remember_skipped(offer.version)
            return False
        while self.running:
            events = pygame.event.get()
            for event in events:
                if event.type == pygame.QUIT:
                    self.running = False
                    return False
                if event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_y, pygame.K_RETURN):
                        return True
                    if event.key in (pygame.K_n, pygame.K_ESCAPE):
                        self._remember_skipped(offer.version)
                        return False
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    me = self._menu_events([event])
                    if me:
                        ev = me[0]
                        mx, my = ev.pos
                        if 40 <= mx <= 200 and 170 <= my <= 190:
                            return True
                        if 200 <= mx <= 300 and 170 <= my <= 190:
                            self._remember_skipped(offer.version)
                            return False
            ui = self.renderer.ui
            ui.fill((12, 12, 24))
            title = self.renderer.hud_font.render("Доступно обновление", True, (240, 240, 120))
            line1 = self.renderer.font.render(
                f"Версия {offer.version} ({offer.size} байт)", True, (220, 220, 220)
            )
            line2 = self.renderer.font.render(
                "Y / Enter — обновить,  N / Esc — позже", True, (160, 160, 180)
            )
            ui.blit(title, (40, 110))
            ui.blit(line1, (40, 150))
            ui.blit(line2, (40, 175))
            self.renderer.present()
            pygame.display.flip()
            self.renderer.clock.tick(30)
            await asyncio.sleep(0.02)
        return False

    async def _lobby_loop(self) -> None:
        self.input_handler.unlock_mouse()
        while self.running and self.network.state != "game":
            events = pygame.event.get()
            for event in events:
                if event.type == pygame.QUIT:
                    self.running = False
            mevents = self._menu_events(events)
            self._apply_resizes(events)

            self.lobby_screen.set_state(
                self.network.party_list,
                self.network.last_error or "Выберите партию или создайте новую",
            )
            action = self.lobby_screen.handle(mevents)
            if action == "create":
                name = self.lobby_screen.player_name or "Player"
                mode = self.lobby_screen.create_mode
                if self.custom_sector_map is not None:
                    self.network.send_create_party(
                        f"{name}'s party", mode,
                        self.custom_sector_map.to_bytes(),
                        0, 0,
                        map_format=MAP_FORMAT_SECTOR,
                    )
                elif self.custom_map is not None:
                    self.network.send_create_party(
                        f"{name}'s party", mode,
                        self.custom_map.to_bytes(),
                        self.custom_map.width, self.custom_map.height,
                    )
                else:
                    self.network.send_create_party(f"{name}'s party", mode)
            elif action and action.startswith("join:"):
                self.network.send_join_party(int(action.split(":")[1]))
            elif action == "refresh":
                self.network.send_get_lobby()
            elif action == "clear_map":
                self.custom_map = None
                self.custom_sector_map = None
                self.custom_map_name = None
                self.lobby_screen.set_custom_map(None)
            elif action == "editor":
                self._ui = "editor"
                return
            elif action == "settings":
                self._ui = "settings"
                return
            elif action == "quit":
                self.running = False

            if self.network.state != "game":
                self.lobby_screen.draw(self.renderer.ui)
                self.renderer.present()
                pygame.display.flip()
                self.renderer.clock.tick(60)

            await asyncio.sleep(0.005)

    @property
    def _active_editor(self):
        return (self.sector_editor_screen if self._editor_kind == "sector"
                else self.editor_screen)

    async def _editor_loop(self) -> None:
        self.input_handler.unlock_mouse()
        while self.running and self._ui == "editor":
            events = pygame.event.get()
            for event in events:
                if event.type == pygame.QUIT:
                    self.running = False
            mevents = self._menu_events(events)
            self._apply_resizes(events)
            screen = self._active_editor
            action = screen.handle(mevents)
            if action == "back":
                self._ui = "lobby"
                return
            if action == "tile":
                self._editor_kind = "tile"
                continue
            if action == "sector":
                self._editor_kind = "sector"
                continue
            if action == "play":
                if self._editor_kind == "sector":
                    self.custom_sector_map = self.sector_editor_screen.editor.sm
                    self.custom_map = None
                    self.custom_map_name = self.sector_editor_screen.map_name
                else:
                    self.custom_map = self.editor_screen.to_game_map()
                    self.custom_sector_map = None
                    self.custom_map_name = self.editor_screen.map_name
                self.lobby_screen.set_custom_map(self.custom_map_name)
                self._ui = "lobby"
                return
            self._active_editor.draw(self.renderer.ui)
            self.renderer.present()
            pygame.display.flip()
            self.renderer.clock.tick(60)
            await asyncio.sleep(0.005)

    async def _settings_loop(self, from_pause: bool) -> None:
        if not from_pause:
            self.input_handler.unlock_mouse()
        while self.running:
            events = pygame.event.get()
            for event in events:
                if event.type == pygame.QUIT:
                    self.running = False
            mevents = self._menu_events(events)
            # The drag is applied immediately (once, the last size of the frame);
            # the screen itself keeps its fixed 1000x700 layout. The new size is
            # only recorded in memory here -- a drag fires dozens of events per
            # second and each ``save()`` is a disk write. Leaving the screen
            # (Esc / Назад) persists it.
            resized = self._apply_resizes(events)
            if resized:
                self.settings.set(
                    "window_size",
                    size_text(*self.renderer.window_size),
                )
            if self.settings_screen.handle(mevents, self.settings) == "back":
                self.settings.apply_runtime(self.input_handler)
                self.fps_cap = int(self.settings.get("fps_cap"))
                # Re-sync the adaptive adapter with the freshly applied config
                # (render_scale is the new best quality, view_dist the new base).
                self._base_view_dist = int(self.settings.get("view_dist"))
                self.quality.reset(
                    base_scale=int(self.settings.get("render_scale")),
                    base_vs=int(self.settings.get("vs_scale")),
                    target_fps=self.fps_cap,
                )
                self._apply_quality()
                self._apply_window()
                self._sync_control_hints()
                if not from_pause:
                    self._ui = "lobby"
                return
            self.settings_screen.draw(self.renderer.ui, self.settings)
            self.renderer.present()
            pygame.display.flip()
            self.renderer.clock.tick(60)
            await asyncio.sleep(0.005)

    def _init_from_server(self) -> None:
        self.player_id = self.network.player_id
        # A new party starts from a blank slate: effects, corpses and the
        # destructible/bridge sets are client-side only, so they would otherwise
        # leak from the party we just left.
        from engine import game_state as gs
        gs.reset_transient_state()
        if self.network.map_format == MAP_FORMAT_SECTOR:
            self.game_map = None
            self.sector_map = SectorMap.from_bytes(self.network.map_data)
            self.bridge = RenderBridge(self.renderer.screen)
            self.bridge.set_sector_map(self.sector_map)
        else:
            self.game_map = GameMap.from_bytes(
                self.network.map_data,
                self.network.map_width,
                self.network.map_height,
            )
            self.sector_map = None
            self.bridge = RenderBridge(self.renderer.screen)
            self.bridge.set_map(self.game_map.tiles, self.game_map.height)
        # Re-apply config after the bridge exists so FOV/view_dist/render_scale
        # from config.json are exactly what the game renders with.
        self.settings.apply_runtime(self.input_handler)
        self._base_view_dist = int(self.settings.get("view_dist"))
        self.quality.reset(
            base_scale=int(self.settings.get("render_scale")),
            base_vs=int(self.settings.get("vs_scale")),
            target_fps=self.fps_cap,
        )
        self._apply_quality()
        self.input_handler.lock_mouse()
        self.input_handler.reset_mouse_position()
        if self.game_map is not None:
            logger.info(f"Карта загружена: {self.game_map.width}x{self.game_map.height}")
        else:
            logger.info(f"Секторная карта загружена: {len(self.sector_map.linedefs)} лайндефов")

    def _quality_enabled(self) -> bool:
        """Adaptive frame-budget ladder runs only in auto quality mode
        (a manual tier is a lock that never gets overridden)."""
        from engine import game_state as gs

        return gs.quality_mode == "auto"

    def _apply_quality(self) -> None:
        """Push the adaptive quality step into the renderer/runtime state."""
        from engine import constants as const
        from engine import rendering
        from engine import game_state as gs

        scale = self.quality.scale
        locked = gs.quality_mode != "auto"  # manual tier lock: never degrade flats
        if (
            const.SCALE == scale
            and rendering.SCALE == scale
            and gs.render_scale == scale
            and gs.vs_scale == self.quality.vs_scale
            and (locked or gs.flat_textured == self.quality.flat_textured)
            and (locked or gs.effects_factor == self.quality.effects_factor())
            and (locked or gs.view_dist == int(self._base_view_dist * self.quality.view_dist_factor()))
        ):
            return
        const.SCALE = scale
        rendering.SCALE = scale
        gs.render_scale = scale
        gs.vs_scale = self.quality.vs_scale
        if not locked:
            gs.flat_textured = self.quality.flat_textured
            gs.effects_factor = self.quality.effects_factor()
            gs.view_dist = int(self._base_view_dist * self.quality.view_dist_factor())

    def _input_snapshot(self) -> tuple:
        """Freshest input state, sampled right before every network send."""
        # Dash is a one-shot pulse. Latch the press so a single-frame edge is
        # never lost between two 25 Hz sends, and consume the edge itself so
        # one press yields exactly one packet. The server stays authoritative
        # over duration and cooldown and ignores duplicates anyway.
        flags = INPUT_FLAG_DASH if self._dash_pending else 0
        self._dash_pending = False
        # "Use" rides along while E is held; the server collapses it to one
        # attempt per rising edge, so no latching is needed on the client.
        if self.input_handler.use:
            flags |= INPUT_FLAG_USE
        # Arm the refusal hint on the press edge, not on the (sticky) refusal
        # itself: the server keeps the last result in `use_reason`, so a raw
        # "is it locked?" check would leave the message on screen forever.
        if self.input_handler.use and not self._prev_use:
            self._use_hint_until = time.monotonic() + USE_HINT_SECONDS
        self._prev_use = self.input_handler.use
        return (
            self.input_handler.move_x,
            self.input_handler.move_y,
            self.local_angle,
            self.input_handler.shoot,
            self.input_handler.weapon_id,
            flags,
        )

    async def _game_loop(self) -> None:
        tractor = is_tractor(self.settings.get("control_mode"))
        logger.info(
            "Игра началась! "
            + ("WASD — движение, стрелки — обзор, Пробел — огонь, "
               "Левый Shift — дэш" if tractor else
               "WASD — движение, мышь — обзор, ЛКМ — огонь, Пробел — дэш")
        )
        # Network input runs on its own fixed-rate timer so its frequency is
        # independent of how fast frames are rendered.
        self._input_task = asyncio.create_task(
            input_send_loop(
                self.network,
                self._input_snapshot,
                INPUT_SEND_INTERVAL,
                should_run=lambda: self.running and self.network.state == "game",
                # While the pause menu or an end screen is up the loop stays
                # alive but sends nothing.  Forwarding the last held keys would
                # keep the server moving and shooting the player behind the
                # frozen frame -- and firing while dead is refused anyway, so an
                # input stream would only cost packets.
                enabled=self._input_enabled,
            )
        )
        try:
            while self.running and self.network.state == "game":
                # An end screen takes priority over pause: the world keeps
                # running under it, so the local player can die while the pause
                # menu is up, and the death overlay must win.
                if self._end_active():
                    if self._end_bg is None:
                        self._enter_end()
                    await self._end_frame()
                    continue
                if self._end_bg is not None:
                    self._exit_end()
                if self._paused:
                    await self._paused_frame()
                    continue

                self._process_input()
                self._apply_window_events()

                if self.input_handler.pause_requested:
                    self._enter_pause()
                    continue
                if self.input_handler.quit_requested:
                    self.running = False
                    return

                self._update_local_state()
                self._render()
                await asyncio.sleep(0.001)
        finally:
            task, self._input_task = self._input_task, None
            if task is not None:
                task.cancel()

    def _enter_pause(self) -> None:
        self._paused = True
        self.pause_screen._selected = 0
        self.input_handler.unlock_mouse()
        # Snapshot the frame once, already scaled to the menu canvas: at 4K a
        # fresh capture is a 33 Mpixel scale, and a paused frame only needs to
        # blit a 1000x700 surface back under the overlay.
        self._pause_bg = self.renderer.capture_menu_frame()
        pygame.event.clear()

    def _exit_pause(self) -> None:
        self._paused = False
        self._pause_bg = None
        self._dash_pending = False
        self.input_handler.lock_mouse()
        self.input_handler.reset_mouse_position()
        pygame.event.clear()

    # ── end screens (death / victory) ────────────────────────────────
    def _input_enabled(self) -> bool:
        """The single gate on outbound gameplay input.

        Pause and the end overlay are the two states in which the client must
        not send ``PLAYER_INPUT``/``FIRE``: the server is authoritative and
        trusts fresh input, so keys held across the overlay would otherwise keep
        feeding a dead or finished player.  One predicate, so a new state cannot
        forget one of the two loops.
        """
        return not self._paused and not self._end_active()

    def _end_active(self) -> bool:
        """The death or victory overlay is up.

        Two independent triggers, one overlay: the match ended (someone reached
        the exit, the PvP clock ran out) or the local player is dead and has not
        been put back in yet.  Read from the authoritative snapshot fields, so
        the overlay appears on the same frame as the death/phase change.
        """
        if self.bridge is None:
            return False
        if self.match_phase == 'over':
            return True
        return self.local_health <= 0 or self.local_state == 1

    def _enter_end(self) -> None:
        # A death can land while the pause menu is up (the world never stops).
        # Drop the pause state and take over the frozen frame, so the two
        # overlays can never stack.
        self._paused = False
        self._pause_bg = None
        self.end_screen._selected = 0
        self.input_handler.unlock_mouse()
        self._end_bg = self.renderer.capture_menu_frame()
        pygame.event.clear()

    def _exit_end(self) -> None:
        self._end_bg = None
        self._dash_pending = False
        self.input_handler.lock_mouse()
        self.input_handler.reset_mouse_position()
        pygame.event.clear()

    def _accuracy_pct(self) -> int:
        st = self.local_stats or {}
        fired = int(st.get('shots_fired', 0))
        if fired <= 0:
            return 0
        return int(round(100.0 * int(st.get('shots_hit', 0)) / fired))

    @staticmethod
    def _format_time(seconds: int) -> str:
        seconds = max(0, int(seconds))
        return f"{seconds // 60}:{seconds % 60:02d}"

    def _end_model(self) -> dict:
        st = self.local_stats or {}
        stats = [
            ("Убийства", f"{int(st.get('kills', 0))}"),
            ("Смерти", f"{int(st.get('deaths', 0))}"),
            ("Урон нанесён", f"{int(st.get('damage_dealt', 0))}"),
            ("Урон получен", f"{int(st.get('damage_taken', 0))}"),
            ("Точность", f"{self._accuracy_pct()}%"),
            ("Предметы", f"{int(st.get('items', 0))}"),
            ("Секреты", f"{int(st.get('secrets', 0))}/{int(self.secrets_total)}"),
            ("Время", self._format_time(self._match_tick // 20)),
        ]
        if self.match_phase != 'over':
            return {
                "kind": "death",
                "title": "ВЫ ПОГИБЛИ",
                "reason": "",
                "tone": "lose",
                "stats": stats,
                "buttons": [("respawn", "ВОЗРОДИТЬСЯ")],
                "primary": "respawn",
                "escapable": True,
                "hint": "Enter — возродиться | Esc — в меню",
            }
        if self.mode != 'pvp':
            return {
                "kind": "victory",
                "title": "УРОВЕНЬ ПРОЙДЕН",
                "reason": "",
                "tone": "win",
                "stats": stats,
                "buttons": [("leave", "В МЕНЮ")],
                "primary": "leave",
                "escapable": True,
                "hint": "Enter — в меню",
            }
        if self.match_winner == 0:
            title, tone = "НИЧЬЯ", "lose"
        elif self.match_winner == self.player_id:
            title, tone = "ПОБЕДА", "win"
        else:
            title, tone = "ПОРАЖЕНИЕ", "lose"
        players = sorted(
            self._players_snapshot,
            key=lambda p: int((p.get('stats') or {}).get('kills', 0)),
            reverse=True,
        )
        rows = []
        highlight = None
        for i, p in enumerate(players):
            st_p = p.get('stats') or {}
            rows.append([
                p.get('name', '') or f"ID{p.get('id')}",
                str(int(st_p.get('kills', 0))),
                str(int(st_p.get('deaths', 0))),
            ])
            if p.get('id') == self.player_id:
                highlight = i
        return {
            "kind": "victory",
            "title": title,
            "reason": "",
            "tone": tone,
            "stats": stats,
            "table": {"headers": ["Игрок", "Убийства", "Смерти"],
                      "rows": rows, "highlight": highlight},
            "buttons": [("leave", "В МЕНЮ")],
            "primary": "leave",
            "escapable": True,
            "hint": "Enter — в меню",
        }

    async def _end_frame(self) -> None:
        events = pygame.event.get()
        for event in events:
            if event.type == pygame.QUIT:
                self.running = False
                return
        # Keep reading the world under the overlay: the server answers the
        # respawn request with a new snapshot, and only applying it can clear
        # the death overlay.  Without this the state would freeze on the frame
        # of death and the overlay could never come down.
        self._update_local_state()
        if not self._end_active():
            self._exit_end()
            return

        mevents = self._menu_events(events)
        if self._apply_resizes(events):
            # The backdrop was captured for the old window; re-grab it so the
            # frozen frame keeps filling the new one.
            self._end_bg = self.renderer.capture_menu_frame()

        self.end_screen.set_model(self._end_model())
        action = self.end_screen.handle(mevents)
        if action == "respawn":
            self.network.send_respawn()
        elif action == "leave":
            self._exit_end()
            self._leave_to_lobby()
            return

        if self._end_bg is not None:
            self.renderer.ui.blit(self._end_bg, (0, 0))
        self.end_screen.draw(self.renderer.ui)
        self.renderer.present()
        pygame.display.flip()
        self.renderer.clock.tick(self.fps_cap)
        await asyncio.sleep(0.005)

    def _leave_to_lobby(self) -> None:
        self.network.state = "lobby"
        self.network.send_leave_party()
        self.bridge = None
        self.game_map = None
        self.other_players = []
        self.enemies = []
        self.bullets = []
        self.corpses = []
        self.impacts = []
        self.local_state = 0
        self._paused = False
        # Sound bookkeeping belongs to the party that just ended; the next party
        # must not read its first snapshot as a burst of events.
        self._sfx_seen_impacts.clear()
        self._sfx_seen_items.clear()
        self._sfx_items_ready = False
        self._sfx_seen_doors.clear()
        self._sfx_prev_health = None
        self._sfx_prev_state = 0
        self._pause_bg = None
        self._end_bg = None
        self.match_phase = 'playing'
        self.match_winner = 0
        self.match_time_left = 0
        self.secrets_total = 0
        self._match_tick = 0
        self.local_stats = None
        self._players_snapshot = []
        # A remembered press died with the party: it was meant for a weapon in
        # a world that no longer exists.
        self._fire_press_buffered = False
        # The pending intents died with the party: their decisions will never
        # arrive, so they must not be carried into the next one.
        self._fire_pending.clear()
        self._dash_pending = False
        self.input_handler.unlock_mouse()
        logger.info("Выход в меню")

    async def _paused_frame(self) -> None:
        events = pygame.event.get()
        for event in events:
            if event.type == pygame.QUIT:
                self.running = False
                return
        mevents = self._menu_events(events)
        if self._apply_resizes(events):
            # The frozen frame was captured for the old window; re-grab it so the
            # pause backdrop keeps filling the new one.
            self._pause_bg = self.renderer.capture_menu_frame()

        action = self.pause_screen.handle(mevents)
        if action == "resume":
            self._exit_pause()
            return
        if action == "settings":
            await self._settings_loop(from_pause=True)
            return
        if action == "leave":
            self._exit_pause()
            self._leave_to_lobby()
            return
        if action == "quit":
            self.running = False
            return

        # The frozen game frame goes back under the overlay, already at menu
        # canvas size, so the pause menu shows the world behind it at any
        # window size without rescaling per frame.
        if self._pause_bg is not None:
            self.renderer.ui.blit(self._pause_bg, (0, 0))
        self.pause_screen.draw(self.renderer.ui)
        self.renderer.present()
        pygame.display.flip()
        self.renderer.clock.tick(self.fps_cap)
        await asyncio.sleep(0.005)

    @property
    def _fire_undecided(self) -> int:
        """Intents sent but not yet decided by the server.

        Read-only and derived from ``_fire_pending`` on purpose: a separate
        counter could drift from the intents it counts, and the drift showed up
        as a server decision charged to the wrong intent (a real shot losing its
        recoil mid-animation).
        """
        return len(self._fire_pending)

    def _fire_anim_frames(self, now: float) -> int:
        """Remaining animation in 60 fps-equivalent frames.

        The renderer works in frames at 60 fps (`gs.w_tmr`, `info['f']`), so the
        remaining wall-clock time is converted once per frame instead of
        counting rendered frames. Rounding (not ceiling) keeps the conversion
        unbiased, so the animation ends within half a frame of the cooldown
        instead of a full frame early.
        """
        left = self._fire_anim_until - now
        if left <= 0.0:
            return 0
        return int(left * FPS_60 + 0.5)

    def _fire_anim_frames_from_server(self, ticks: int) -> int:
        """Server cooldown ticks -> weapon animation frames (60 fps equivalent).

        The renderer works in frames at 60 fps (``gs.w_tmr``, ``info['f']``), so
        the authoritative tick countdown is converted once, here.
        """
        if ticks <= 0:
            return 0
        return int(ticks * TICK_INTERVAL * FPS_60 + 0.5)

    def _sync_fire_anim(self, fire_seq: int, fire_ready: int, fire_reason: int,
                        wpn: int, now: float) -> None:
        """Reconcile the predicted recoil with the server's fire decision.

        The recoil *starts* on the local intent so the weapon does not feel
        laggy, but the server decides whether a shot happened at all, and its
        decision is addressed to ONE of our intents:

        * ``fire_seq`` jumps -> every pending intent up to that number was
          accepted (the reliable channel decides in delivery order, so none can
          still be in flight above it). The recoil runs for the ``fire_ready``
          ticks the server reports.  This also covers the reverse case — a
          snapshot lost between two shots — where the jump is the first sign the
          shot happened at all.
        * ``fire_reason`` != 0 with the sequence number unchanged -> the newest
          decision refused the OLDEST intent still pending: a refusal leaves
          ``fire_seq`` where it was, so it is the last decision made.  Only
          intents ABOVE ``fire_seq`` are candidates: the snapshot has already
          settled everything below it, and a shot that happened must not have
          its recoil cut by a refusal meant for the next intent.

        The pending intents are spent, not counted, so the same decision
        repeated in every following snapshot (the server keeps it until the next
        intent is decided) has nothing left to charge and does nothing.  Two
        identical refusals in a row stay two decisions, because the second one
        finds the next intent waiting — identifying a decision by its
        ``(fire_seq, fire_reason)`` pair instead would have collapsed them and
        left a phantom recoil behind.
        """
        self._server_fire_ready = fire_ready
        # The refusal first, the acceptance second: decisions arrive in intent
        # order, and the acceptance is authoritative about the recoil's length,
        # so it must be the last word.
        if fire_reason and self._fire_pending:
            # A refusal can only be about an intent the server did NOT accept, so
            # everything the snapshot has already accepted drops out of the way
            # first. Without this the refusal lands on an accepted shot: the
            # snapshot "fire_seq jumped AND a refusal came with it" is exactly
            # "intent #1 fired, intent #2 was refused", and charging #1 cuts the
            # recoil of a shot that really happened.
            while self._fire_pending and self._fire_pending[0] <= fire_seq:
                self._fire_pending.popleft()
        if fire_reason and self._fire_pending:
            self._fire_pending.popleft()
            newest = not self._fire_pending
            if newest:
                # The recoil on screen was predicted for THIS intent, and no
                # shot happened: cut it, or an empty magazine held down flashes
                # forever.
                self._fire_anim_until = 0.0
                self._fire_anim_wpn = -1
            if fire_reason == FIRE_REASON_COOLDOWN and fire_ready > 0:
                # The client's gate and the server's tick are independent clocks,
                # so one tick of slack does not always win the race: with real
                # network jitter an intent arrives while the weapon is still hot.
                # There is nothing to retry — the click was consumed when the
                # intent was sent — so the only correct action is to wait out the
                # weapon's real cooldown instead of racing the same tick again.
                self._next_fire_time = max(
                    self._next_fire_time,
                    now + (fire_ready + 1) * TICK_INTERVAL)
        if fire_seq > self._server_fire_seq:
            # Everything the server has accepted is settled; a later refused
            # intent can no longer be charged against it.
            while self._fire_pending and self._fire_pending[0] <= fire_seq:
                self._fire_pending.popleft()
            self._server_fire_seq = fire_seq
            if fire_ready <= 0:
                return
            # A confirmation for the weapon the player already swapped away
            # from must not resurrect its recoil on the new gun.
            if self._fire_anim_wpn != -1 and self._fire_anim_wpn != wpn:
                return
            self._fire_anim_until = max(
                self._fire_anim_until, now + fire_ready * TICK_INTERVAL)
            self._fire_anim_wpn = wpn

    def _process_input(self) -> None:
        self.input_handler.update()
        # Frame-level latch for dash edge: survives until next 25 Hz snapshot
        if self.input_handler.dash_pressed:
            self.input_handler.dash_pressed = False
            self._dash_pending = True

        now = time.monotonic()

        # Predicted angle: apply the mouse immediately, without waiting for the
        # server echo; the server receives the absolute angle every packet.
        self.local_angle = normalize_angle(
            self.local_angle + self.input_handler.rotate_delta
        )

        # Fire tempo uses the shared tick cooldown (converted to seconds) plus
        # one tick of slack, so the client never fires faster than the server
        # allows and a shown shot is never rejected by the server's cooldown.
        #
        # The tempo comes from the weapon the SERVER has equipped
        # (``local_weapon``, rewritten by every snapshot), never from the locally
        # selected id. The two are not the same thing: the server honours
        # apply_weapon_switch only for weapons the player owns and picks up
        # weapons itself, so a gate built from the local id rate-limits against
        # a weapon the server never fires with. Gating on the SSG's 1.30 s while
        # the pistol is equipped swallows every press but one in four ("pressed
        # at the cooldown, no shot"), and gating on the pistol's 0.30 s while the
        # SSG is equipped gets 3 intents in 4 refused as cooldown, so the
        # predicted recoil is cut and no shot is shown at all.
        #
        # A press is remembered for AT MOST ONE gate opening. The shotgun's gate
        # is 0.90 s, so at an ordinary 10 Hz click rate the player lands ~9 clicks
        # per opening: with nothing remembered that is "one shot in ten clicks",
        # which reads as the weapon ignoring the mouse. With a queue it is worse
        # — the queue grows at the click rate and drains at the weapon's rate, so
        # the SSG (1.30 s) banked ~39 shots for three seconds and fired them long
        # after the player let go. One buffered press is the whole difference: the
        # click is never lost, and the backlog cannot grow no matter how fast the
        # player clicks. It is a bool, not a deque, precisely so that there is
        # nothing to grow.
        if self.input_handler.shoot_pressed and now < self._next_fire_time:
            self._fire_press_buffered = True

        wpn = self.input_handler.weapon_id
        fired_wpn = self.local_weapon if 0 <= self.local_weapon < len(WPN) else wpn
        if 0 <= fired_wpn < len(WPN):
            info = WPN[fired_wpn]
            if now >= self._next_fire_time and (
                self.input_handler.shoot
                or self.input_handler.shoot_pressed
                or self._fire_press_buffered
            ):
                ticks = wpn_cooldown_ticks(int(info['f']))
                # The +1 tick of slack belongs to the gate only: the animation
                # must show the real cooldown, so it gets none.
                self._next_fire_time = now + ticks * TICK_INTERVAL + TICK_INTERVAL
                self._fire_anim_until = now + ticks * TICK_INTERVAL
                self._fire_anim_wpn = fired_wpn
                # The buffered press has been spent, whatever fired this opening:
                # a held button does not need it, and it must not survive to give
                # a second shot the player did not ask for.
                self._fire_press_buffered = False
                # Fire sound starts with the predicted recoil, on the gate
                # opening, so it is heard the moment the shot is shown. It is not
                # delayed to the snapshot's confirmed fire_seq: that would trail
                # by a round-trip and read as lag.
                sfx.play(sfx.shoot_name(info.get("snd", "pistol")))
                # The reliable FIRE is the single path to a shot, so every gate
                # opening sends an intent — held fire included. Ammo is not
                # checked here on purpose: the server is the only authority and
                # refuses an empty weapon with an explicit reason (see
                # handle_shoot's FireResult), which the client then reflects.
                # The intent is filed under its own number: that is what lets
                # the server's decision be charged to the intent it answers.
                self._fire_pending.append(self.network.send_fire(wpn))

        # A weapon switch drops the running animation: the other gun's recoil
        # must not keep playing (and counting down) on the new one. The owner is
        # the server's weapon, the same id _sync_fire_anim confirms against.
        if self._fire_anim_wpn != -1 and self._fire_anim_wpn != fired_wpn:
            self._fire_anim_until = 0.0
            self._fire_anim_wpn = -1

        # NOTE: PLAYER_INPUT is sent by the fixed-rate `_input_task` loop, not
        # here, so the network input rate does not depend on the frame rate.
        # The loop always samples the freshest state via `_input_snapshot`.

    def _update_local_state(self) -> None:
        now = time.monotonic()
        render_state = self.network.interpolated_state(now)
        if render_state is None:
            return

        # Remote entities are rendered interpolated (smoothed) between the last
        # two snapshots; the local player uses the authoritative latest state
        # with no interpolation delay (prediction).
        (_tick, players, enemies, bullets, corpses,
         mode, impacts, items, doors) = render_state
        self.other_players = [p for p in players if p["id"] != self.player_id]
        self.enemies = enemies
        self.bullets = bullets
        self.corpses = corpses
        self.impacts = impacts
        self._sfx_impacts(impacts)
        # Pickups are taken straight from the snapshot: the server owns both
        # the position and the moment of disappearance, so there is nothing
        # here to predict and nothing to smooth.
        self.items = items
        self._sfx_items(items)
        # Doors send only their non-closed state, keyed by linedef id (see
        # shared/protocol.py). The bridge joins them to the map's door lines.
        self.doors = doors
        self._sfx_doors(doors)
        self.mode = mode
        # Match block, read from the newest snapshot: the overlay and the HUD
        # read these, never a locally guessed phase.
        lm = self.network.latest_match or {}
        self.match_phase = lm.get('phase', 'playing')
        self.match_winner = int(lm.get('winner_id', 0))
        self.match_time_left = int(lm.get('time_left', 0))
        self.secrets_total = int(lm.get('secrets_total', 0))
        self._match_tick = int(_tick)
        self._players_snapshot = players

        latest = self.network.latest_state
        if latest is None:
            return
        for p in latest[1]:
            if p["id"] == self.player_id:
                self._server_angle = p["angle"]
                self.local_x = p["x"]
                self.local_y = p["y"]
                self.local_health = p["health"]
                self.local_weapon = p["wpn"]
                self.local_ammo = p.get("ammo", self.local_ammo)
                # Authoritative dash flag, used only for the own-dash feedback
                # (dust burst + screen streaks). Taken from the snapshot rather
                # than from the local key press on purpose: a refused dash
                # (cooldown, no movement input) must not flash, and the effect
                # then arrives exactly when the body starts moving.
                self.local_state = p.get("state", 0)
                self._sfx_local(self.local_health, self.local_state)
                # Armor and the active buff are stored verbatim from the
                # snapshot and handed to the bridge unchanged. They are not
                # derived here: the server decides both, and a local guess
                # would be a second source of truth for how protected the
                # player is.
                self.local_armor = p.get("armor")
                self.local_powerup = p.get("powerup", "")
                # Key bitmask and the advisory "use" result, taken verbatim
                # from the snapshot: the server owns both (design D6/D9).
                self.local_keys = p.get("keys", 0)
                self.local_use_reason = p.get("use_reason", 0)
                # Per-player counters, verbatim from the record (design D2).
                self.local_stats = p.get("stats") or {}
                # Fire decision from the snapshot: drives the local recoil's
                # length instead of the key press, so a refused intent cannot
                # leave a phantom shot behind.
                self._sync_fire_anim(
                    p.get("fire_seq", 0), p.get("fire_ready", 0),
                    p.get("fire_reason", 0), self.local_weapon, now,
                )
                break

        # Reconcile the predicted angle toward the server angle once the
        # player stops turning; the first state and big gaps snap instantly.
        if self._server_angle is not None:
            diff = _angle_diff(self._server_angle, self.local_angle)
            if not self._seen_first_state:
                self.local_angle = self._server_angle
            elif self.input_handler.rotate_delta == 0.0:
                if abs(diff) > 0.45:
                    self.local_angle = self._server_angle
                elif abs(diff) > 1e-4:
                    self.local_angle = normalize_angle(self.local_angle + diff * 0.3)
            self._seen_first_state = True

        self._feed_engine_meta()

    def _feed_engine_meta(self) -> None:
        """Publish the snapshot's match counters to the engine HUD state.

        The results overlay is a client screen, but the in-game HUD (kills,
        accuracy, the PvP clock) is drawn by the engine, and the engine owns no
        network state.  This is the one place the values cross the boundary, so
        there is a single owner of "what the HUD shows".
        """
        from engine import game_state as gs

        st = self.local_stats or {}
        gs.kills = int(st.get('kills', 0))
        gs.shots_fired = int(st.get('shots_fired', 0))
        gs.shots_hit = int(st.get('shots_hit', 0))
        gs.damage_dealt = int(st.get('damage_dealt', 0))
        gs.damage_taken = int(st.get('damage_taken', 0))
        gs.items_picked = int(st.get('items', 0))
        gs.secrets_found = int(st.get('secrets', 0))
        gs.secrets_total = int(self.secrets_total)
        gs.dm_game_over = self.match_phase == 'over'
        gs.dm_winner = int(self.match_winner)
        gs.dm_timer = int(self.match_time_left)

    # ── sound effects (state of THIS snapshot vs the last) ────────────────
    def _sfx_impacts(self, impacts) -> None:
        """One hit sound per new impact id; a live id is never replayed."""
        active = {im.get("id") for im in impacts}
        self._sfx_seen_impacts.intersection_update(active)
        for im in impacts:
            iid = im.get("id")
            if iid in self._sfx_seen_impacts:
                continue
            self._sfx_seen_impacts.add(iid)
            sfx.play("hit_enemy" if im.get("type") == "enemy" else "hit_wall")

    def _sfx_items(self, items) -> None:
        """A pickup sounds when an item near the player disappears.

        The snapshot's item list is global, so distance decides whose pickup it
        was: a vanished item within reach is ours, a distant one is an ally's.
        """
        now = {it["id"]: (it["x"], it["y"]) for it in items}
        if self._sfx_items_ready:
            for iid, (x, y) in self._sfx_seen_items.items():
                if iid in now:
                    continue
                dx, dy = x - self.local_x, y - self.local_y
                if dx * dx + dy * dy <= SFX_PICKUP_RADIUS_SQ:
                    sfx.play("pickup")
                    break
        self._sfx_seen_items = now
        self._sfx_items_ready = True

    def _sfx_doors(self, doors) -> None:
        """A door just started opening: an id that was not non-closed last frame."""
        now = {d.get("li", d.get("id")) for d in doors}
        if now - self._sfx_seen_doors:
            sfx.play("door")
        self._sfx_seen_doors = now

    def _sfx_local(self, health: int, state: int) -> None:
        """Own hurt/death/dash, read from the authoritative snapshot fields."""
        dying = state == 1 and self._sfx_prev_state != 1
        if dying:
            # The fatal hit drops health to zero too; the death sound is the one
            # that matters, so it replaces the hurt sound for this frame.
            sfx.play("death")
        else:
            if self._sfx_prev_health is not None and health < self._sfx_prev_health:
                sfx.play("hurt")
            if state == 3 and self._sfx_prev_state != 3:
                sfx.play("dash")
        self._sfx_prev_health = health
        self._sfx_prev_state = state

    def _render(self) -> None:
        if self.bridge is None:
            return

        self.bridge.update(
            self.local_x, self.local_y, self.local_angle,
            self.local_health, self.other_players,
            self.enemies, self.bullets, self.corpses,
            self.local_weapon, self.local_ammo,
            self._fire_anim_frames(time.monotonic()),
            self.mode, self.impacts, self.items, self.local_state,
            # clock.get_fps() is the rolling average over the frames just
            # drawn, which is what a readout should show: an instantaneous
            # 1/frame_ms would flicker on every slow frame.
            self.renderer.clock.get_fps(),
            armor=self.local_armor, powerup=self.local_powerup,
            doors=self.doors, keys=self.local_keys,
            use_hint=(self.local_use_reason == USE_REASON_LOCKED
                      and time.monotonic() < self._use_hint_until),
        )
        self.bridge.render()
        pygame.display.flip()
        # clock.tick returns the wall-clock frame period in ms, i.e. exactly
        # the frame budget we need to measure for adaptive quality.
        frame_ms = self.renderer.clock.tick(self.fps_cap)
        if self._quality_enabled():
            prev_scale = self.quality.scale
            self.quality.sample(frame_ms, time.monotonic())
            if self.quality.scale != prev_scale:
                self._apply_quality()