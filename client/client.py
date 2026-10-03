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
    FIRE_REASON_CODES)
from shared.protocol import INPUT_FLAG_DASH
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
from client.editor_screen import MapEditorScreen
from engine.constants import WPN, INPUT_SEND_INTERVAL
from client.performance import AdaptiveQuality, input_send_loop
from client.updater import UpdateError, apply_update, default_releases_dir, restart

logger = logging.getLogger(__name__)

UPDATE_TIMEOUT = 30.0
# How long a press stays queued while it waits for a gate opening: every press
# is owed a shot, so the bound must cover a real burst (a 3 s wait is ten pistol
# gates) and not just one. It still bounds the queue, so a stall (alt-tab, one
# very long frame) cannot turn into an unbounded burst afterwards, and it keeps
# a press the player has clearly moved on from from being fired minutes later.
FIRE_INTENT_MAX_AGE = 3.0
# The one refusal that does NOT consume the player's press: the weapon was still
# hot, so the intent lost a race with the server's tick phase rather than being
# declined. It is re-queued and paced by the server's own remaining cooldown.
FIRE_REASON_COOLDOWN = FIRE_REASON_CODES['cooldown']


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
        self.local_health: int = 100
        self.local_weapon: int = 1
        self.local_ammo: dict = {'pstl': 200, 'shl': 40, 'slg': 100, 'rck': 10, 'BFG': 5}
        # Authoritative world-state flag of the local player (3 = dashing),
        # consumed by the bridge for the own-dash feedback only.
        self.local_state: int = 0
        self.mode: str = 'pve'
        self.impacts: list[dict] = []

        self.other_players: list[dict] = []
        self.enemies: list[dict] = []
        self.bullets: list[dict] = []
        self.corpses: list[dict] = []
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
        # A click is a one-shot intent and lives exactly one frame, so presses
        # landing inside a closed gate would be gone by the next opening. They
        # are latched here as a QUEUE, one entry per press, and consumed one per
        # gate opening: a single latch flag collapsed every press of a fast
        # clicker into one shot ("fires one click in ten"), while a queue makes
        # each press fire exactly once, still limited by the weapon's tempo.
        self._fire_press_queue: deque[float] = deque()
        # Timestamp of the press the last sent intent was made from, so a
        # cooldown refusal can hand that same press back to the queue.
        self._fire_served_press_time: float | None = None
        # Authoritative fire decision from the snapshot: the last accepted fire
        # intent the server acknowledged, and the cooldown it reports. The
        # weapon animation is driven from these, not from the key press.
        self._server_fire_seq: int = 0
        self._server_fire_ready: int = 0
        # Intents sent but not yet answered by the server. ``fire_seq`` counts
        # accepted intents, so this is the gap between what was sent and what
        # came back — the client cannot call a shot refused before the server
        # has actually decided it.
        self._fire_undecided: int = 0
        # Dash intent is a one-shot pulse waiting for the next input packet.
        self._dash_pending: bool = False
        self._server_angle: float | None = None
        self._seen_first_state: bool = False
        # Frozen world frame shown under the pause menu (None while playing).
        self._pause_bg: pygame.Surface | None = None

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
        logger.info("Подключено. Выберите партию в меню.")

        if self.network.update_offer is not None and await self._maybe_update():
            self.network.disconnect()
            self.renderer.cleanup()
            return

        while self.running:
            if self.network.state == "game":
                if self.bridge is None:
                    self._init_from_server()
                await self._game_loop()
            elif self._ui == "editor":
                await self._editor_loop()
            elif self._ui == "settings":
                await self._settings_loop(from_pause=False)
            else:
                await self._lobby_loop()

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
            self._apply_resizes(events)

            self.lobby_screen.set_state(
                self.network.party_list,
                self.network.last_error or "Выберите партию или создайте новую",
            )
            action = self.lobby_screen.handle(self._menu_events(events))
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
            self._apply_resizes(events)
            screen = self._active_editor
            action = screen.handle(self._menu_events(events))
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
            # The drag is applied immediately (once, the last size of the frame);
            # the screen itself keeps its fixed 1000x700 layout. The new size is
            # only recorded in memory here -- a drag fires dozens of events per
            # second and each ``save()`` is a disk write. Leaving the screen
            # (Esc / Назад) persists it.
            if self._apply_resizes(events):
                self.settings.set(
                    "window_size",
                    size_text(*self.renderer.window_size),
                )
            if self.settings_screen.handle(self._menu_events(events), self.settings) == "back":
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
        if self.input_handler.dash_pressed:
            self.input_handler.dash_pressed = False
            self._dash_pending = True
        flags = INPUT_FLAG_DASH if self._dash_pending else 0
        self._dash_pending = False
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
            )
        )
        try:
            while self.running and self.network.state == "game":
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
        self.input_handler.lock_mouse()
        self.input_handler.reset_mouse_position()
        pygame.event.clear()

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
        self._pause_bg = None
        self._fire_press_queue.clear()
        self.input_handler.unlock_mouse()
        logger.info("Выход в меню")

    async def _paused_frame(self) -> None:
        events = pygame.event.get()
        for event in events:
            if event.type == pygame.QUIT:
                self.running = False
                return
        if self._apply_resizes(events):
            # The frozen frame was captured for the old window; re-grab it so the
            # pause backdrop keeps filling the new one.
            self._pause_bg = self.renderer.capture_menu_frame()

        action = self.pause_screen.handle(self._menu_events(events))
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
        laggy, but the server decides whether a shot happened at all:

        * ``fire_seq`` jumps -> intents were accepted; the recoil runs for the
          ``fire_ready`` ticks the server reports.  This also covers the reverse
          case — a snapshot lost between two shots — where the jump is the first
          sign the shot happened at all.
        * ``fire_reason`` != 0 with the sequence number unchanged -> the server
          refused an intent of ours.  The predicted recoil was a phantom, so it
          is cut.  Reason is what makes this safe: an unchanged ``fire_seq`` on
          its own is ambiguous between "refused" and "the ack is still in
          flight", and cutting on the latter would truncate a real shot.
          A ``cooldown`` refusal is the exception: the weapon was still hot, so
          the press was not spent — it goes back into the queue.
        * neither -> nothing decided yet, the prediction is left alone.
        """
        self._server_fire_ready = fire_ready
        if fire_seq > self._server_fire_seq:
            # intents accepted since the last snapshot. The gap is clamped:
            # after a respawn the server's counter can legitimately be ahead of
            # anything this client sent, and a negative "undecided" would then
            # swallow a later refusal.
            self._fire_undecided = max(
                0, self._fire_undecided - (fire_seq - self._server_fire_seq))
            self._server_fire_seq = fire_seq
            self._fire_served_press_time = None
            if fire_ready <= 0:
                return
            # A confirmation for the weapon the player already swapped away
            # from must not resurrect its recoil on the new gun.
            if self._fire_anim_wpn != -1 and self._fire_anim_wpn != wpn:
                return
            self._fire_anim_until = max(
                self._fire_anim_until, now + fire_ready * TICK_INTERVAL)
            self._fire_anim_wpn = wpn
            return
        if fire_reason and self._fire_undecided > 0:
            # this refusal answers one of our intents: the recoil it predicted
            # never became a shot, so it must not keep playing (an empty
            # magazine held down would flash forever otherwise)
            self._fire_undecided -= 1
            self._fire_anim_until = 0.0
            self._fire_anim_wpn = -1
            if fire_reason == FIRE_REASON_COOLDOWN:
                # The client's gate and the server's tick are independent clocks,
                # so one tick of slack does not always win the race: with real
                # network jitter an intent arrives while the weapon is still hot.
                # That press is not spent, so it is handed back with its ORIGINAL
                # timestamp — a fresh one would make an endlessly refused press
                # immortal — and the server's remaining cooldown moves the gate so
                # the retry does not race the same tick again.
                served = self._fire_served_press_time
                self._fire_served_press_time = None
                if served is not None:
                    self._fire_press_queue.appendleft(served)
                if fire_ready > 0:
                    self._next_fire_time = max(
                        self._next_fire_time,
                        now + (fire_ready + 1) * TICK_INTERVAL)

    def _process_input(self) -> None:
        self.input_handler.update()

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
        # Each press is latched as its own queue entry, served one per opening:
        # the edge is rebuilt by InputHandler.update() every frame, so a press
        # inside a closed gate would be gone by the next opening (a single latch
        # flag kept only the FIRST press of a burst and dropped the rest, which
        # is "one shot per ten clicks"). The queue makes every press fire
        # exactly once, still at the weapon's real rate.
        if self.input_handler.shoot_pressed:
            self._fire_press_queue.append(now)
        stale_before = now - FIRE_INTENT_MAX_AGE
        while self._fire_press_queue and self._fire_press_queue[0] < stale_before:
            self._fire_press_queue.popleft()

        wpn = self.input_handler.weapon_id
        fired_wpn = self.local_weapon if 0 <= self.local_weapon < len(WPN) else wpn
        if 0 <= fired_wpn < len(WPN):
            info = WPN[fired_wpn]
            if (
                now >= self._next_fire_time
                and (self.input_handler.shoot or self._fire_press_queue)
            ):
                ticks = wpn_cooldown_ticks(int(info['f']))
                # The +1 tick of slack belongs to the gate only: the animation
                # must show the real cooldown, so it gets none.
                self._next_fire_time = now + ticks * TICK_INTERVAL + TICK_INTERVAL
                self._fire_anim_until = now + ticks * TICK_INTERVAL
                self._fire_anim_wpn = fired_wpn
                # One queue entry is served per opening. A held button has no
                # entry: it re-opens the gate by itself every time.
                if self._fire_press_queue:
                    self._fire_served_press_time = self._fire_press_queue.popleft()
                else:
                    self._fire_served_press_time = None
                # The reliable FIRE is the single path to a shot, so every gate
                # opening sends an intent — held fire included. Ammo is not
                # checked here on purpose: the server is the only authority and
                # refuses an empty weapon with an explicit reason (see
                # handle_shoot's FireResult), which the client then reflects.
                self._fire_undecided += 1
                self.network.send_fire(wpn)

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
        _tick, players, enemies, bullets, corpses, mode, impacts = render_state
        self.other_players = [p for p in players if p["id"] != self.player_id]
        self.enemies = enemies
        self.bullets = bullets
        self.corpses = corpses
        self.impacts = impacts
        self.mode = mode

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

    def _render(self) -> None:
        if self.bridge is None:
            return

        self.bridge.update(
            self.local_x, self.local_y, self.local_angle,
            self.local_health, self.other_players,
            self.enemies, self.bullets, self.corpses,
            self.local_weapon, self.local_ammo,
            self._fire_anim_frames(time.monotonic()),
            self.mode, self.impacts, self.local_state,
            # clock.get_fps() is the rolling average over the frames just
            # drawn, which is what a readout should show: an instantaneous
            # 1/frame_ms would flicker on every slow frame.
            self.renderer.clock.get_fps(),
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