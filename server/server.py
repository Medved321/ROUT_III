from __future__ import annotations

import asyncio
import logging
import os
import time
import math
from pathlib import Path

from shared.components import (
    Velocity, Rotation, Player, Dash,
)
from shared.map_data import GameMap
from shared.sector_map import MAP_FORMAT_SECTOR, SectorMap
from shared.releases import ReleaseStore, is_newer
from shared.version import CLIENT_VERSION, PROTOCOL_VERSION
from shared.combat import normalize_angle, fire_reason_code
from shared.protocol import INPUT_FLAG_DASH
from server.game_world import GameWorld
from server.game_data import (
    MOVE_SPEED_PX, TICK_RATE, TICK_INTERVAL,
    DASH_SPEED_MULT, DASH_TICKS, DASH_COOLDOWN_TICKS,
)
from server.lobby import Lobby, Party
from server.network import ServerNetwork
from server.player import PlayerSession
from server.weapons import (
    FireResult, handle_shoot as weapons_handle_shoot, apply_weapon_switch)

logger = logging.getLogger(__name__)


class GameServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 7777, releases_dir: str | None = None):
        self.lobby = Lobby()
        self.network = ServerNetwork(host, port)
        if releases_dir is None:
            releases_dir = os.environ.get(
                "ROUT_RELEASES_DIR", str(Path(__file__).resolve().parent.parent / "releases")
            )
        self.releases = ReleaseStore(releases_dir)
        self.running = False
        self._last_tick_time: float = 0.0

    async def start(self) -> None:
        self.running = True
        self._last_tick_time = time.monotonic()

        self.network.on_join(self._on_player_join)
        self.network.on_leave(self._on_player_leave)
        self.network.on_input(self._on_player_input)
        self.network.on_ping(self._on_player_ping)
        self.network.on_lobby_request(self._on_lobby_request)
        self.network.on_create_party(self._on_create_party)
        self.network.on_join_party(self._on_join_party)
        self.network.on_leave_party(self._on_leave_party)
        self.network.on_hello(self._on_hello)
        self.network.on_file_request(self._on_file_request)
        self.network.on_file_complete(self._on_file_complete)
        self.network.on_fire(self._on_player_fire)

        await self.network.start()
        logger.info(f"Game server started, tick rate: {TICK_RATE}")

        await self._game_loop()

    async def _game_loop(self) -> None:
        while self.running:
            now = time.monotonic()
            dt = now - self._last_tick_time
            if dt >= TICK_INTERVAL:
                self._last_tick_time = now
                self.lobby.update_all()
                self._broadcast_parties()
                self._remove_stale_players()
            self.network.tick()
            await asyncio.sleep(0.001)

    def _broadcast_parties(self) -> None:
        for party in self.lobby.parties.values():
            w = party.world
            players = w.get_all_player_states()
            # fire_seq/fire_reason live on the session (intents are numbered and decided
            # per client), so they are stamped in here; fire_ready already came
            # from the weapon component and counts down on its own.
            for p in players:
                sess = party.members.get(p["id"])
                p["fire_seq"] = max(0, sess.last_fire_seq) if sess else 0
                p["fire_reason"] = (
                    fire_reason_code(sess.last_fire_result.reason)
                    if sess and sess.last_fire_result else 0
                )
            enemies = w.get_enemy_states()
            bullets = w.get_bullet_states()
            corpses = w.get_corpse_states()
            impacts = w.get_impact_states()
            addresses = [s.address for s in party.members.values()]
            self.network.broadcast_world_state(
                addresses, w.tick, players, enemies, bullets, corpses,
                party.mode, impacts,
            )

    def _remove_stale_players(self) -> None:
        now = time.monotonic()
        # Addresses that produced a connection-reset/port-unreachable error:
        # remove their sessions immediately (no 10-second flood window). The
        # marking is done by ServerNetwork when a sendto fails; the standard
        # ping timeout below stays as a fallback safety net.
        removed = False
        for addr in list(self.network.unreachable):
            session = self.network.remove_player(addr)
            if session:
                self.lobby.remove_player(session)
                logger.info(f"Player unreachable, removed: {session.name}")
                removed = True
        stale = [
            addr for addr, session in self.network.players.items()
            if session.last_ping > 0 and now - session.last_ping > 10.0
        ]
        for addr in stale:
            session = self.network.remove_player(addr)
            if session:
                self.lobby.remove_player(session)
                logger.info(f"Player timed out: {session.name}")
                removed = True
        if removed:
            self._broadcast_lobby()

    def _broadcast_lobby(self) -> None:
        snapshot = self.lobby.get_snapshot()
        for session in self.network.players.values():
            self.network.send_lobby_state(session, snapshot)

    def _on_hello(self, addr, protocol_version: int, client_version: str) -> None:
        accepted = protocol_version == PROTOCOL_VERSION
        release = self.releases.latest()
        has_update = False
        update_version = ""
        update_size = 0
        update_sha256 = ""
        if accepted and release and is_newer(release["version"], client_version):
            has_update = True
            update_version = release["version"]
            update_size = release["size"]
            update_sha256 = release["sha256"]
        info = self.network.client_info.setdefault(addr, {})
        info["protocol_ok"] = accepted
        info["needs_update"] = has_update
        self.network.send_hello_ack(
            addr,
            server_protocol=PROTOCOL_VERSION,
            accepted=accepted,
            latest_client_version=release["version"] if release else CLIENT_VERSION,
            has_update=has_update,
            update_version=update_version,
            update_size=update_size,
            update_sha256=update_sha256,
        )
        if accepted:
            logger.info(
                f"HELLO from {addr}: client {client_version} "
                f"(update={'yes' if has_update else 'no'})"
            )
        else:
            logger.warning(
                f"Rejected client {addr}: protocol {protocol_version} != {PROTOCOL_VERSION}"
            )

    def _on_file_request(self, addr) -> None:
        release = self.releases.latest()
        if release is None:
            logger.warning(f"Update requested by {addr} but no artifact is available")
            return
        data = self.releases.read(release["version"])
        if data is None:
            logger.warning(f"Release {release['version']} listed but unreadable")
            return
        logger.info(f"Sending client {release['version']} ({len(data)} bytes) to {addr}")
        self.network.send_file(addr, data)

    def _on_file_complete(self, addr, ok: bool) -> None:
        logger.info(f"Client {addr} reported update complete: ok={ok}")

    def _on_player_join(self, session: PlayerSession) -> None:
        self.network.send_join_accept(session)
        self.network.send_lobby_state(session, self.lobby.get_snapshot())

    def _on_player_leave(self, session: PlayerSession) -> None:
        self.lobby.remove_player(session)
        self._broadcast_lobby()

    def _on_lobby_request(self, session: PlayerSession) -> None:
        self.network.send_lobby_state(session, self.lobby.get_snapshot())

    def _on_create_party(
        self, session: PlayerSession, party_name: str, mode: str = 'pve',
        map_width: int = 0, map_height: int = 0, map_data: bytes = b"",
        request_id: int = 0, map_format: int = 0,
    ) -> None:
        # Idempotent: a repeated create (retransmit or double click) must not
        # spawn a second party for a session that is already in one.
        current = self.lobby.find_party_of(session)
        if current is not None:
            session.last_create_request = request_id
            self.network.send_party_created(session, current.id)
            self._broadcast_lobby()
            return
        session.last_create_request = request_id
        game_map = None
        if map_data:
            if map_format == MAP_FORMAT_SECTOR:
                game_map = SectorMap.validate_network(map_data)
                label = f"sector map ({len(map_data)} bytes)"
            else:
                game_map = GameMap.validate_network(map_width, map_height, map_data)
                label = f"tile map {map_width}x{map_height} ({len(map_data)} bytes)"
            if game_map is None:
                logger.warning(
                    f"Rejected custom {label} from {session.name}; using default"
                )
            else:
                logger.info(f"Custom {label} accepted from {session.name}")
        party = self.lobby.create_party(
            party_name or "Новая партия", mode=mode, game_map=game_map
        )
        self._add_to_party(session, party)
        self.network.send_party_created(session, party.id)
        self._broadcast_lobby()

    def _on_join_party(self, session: PlayerSession, party_id: int) -> None:
        party = self.lobby.get_party(party_id)
        if party is None:
            self.network.send_join_fail(session, "Партия не найдена")
            return
        current = self.lobby.find_party_of(session)
        if current is party:
            self.network.send_join_accept(session, game_map=party.world.game_map)
            return
        if current is not None:
            self.lobby.remove_player(session)
        self._add_to_party(session, party)
        self._broadcast_lobby()

    def _add_to_party(self, session: PlayerSession, party: Party) -> None:
        party.add_member(session)
        self.network.send_join_accept(session, game_map=party.world.game_map)
        # For PvE: spawn a starting wave once the party has at least one player
        w = party.world
        if w.mode == 'pve' and len(w.enemies) == 0:
            spawned = 0
            if isinstance(w.game_map, SectorMap):
                spawned = w.spawn_enemies_from_map()
            if spawned == 0:
                from server.game_data import SPAWN_POOL
                w.spawn_enemies(8 + len(party.members) * 2)

    def _on_leave_party(self, session: PlayerSession) -> None:
        self.lobby.remove_player(session)
        self._broadcast_lobby()

    def _on_player_input(
        self, session: PlayerSession,
        move_x: int, move_y: int, angle: float,
        shoot: bool, weapon_id: int = 1, flags: int = 0,
    ) -> None:
        party = self.lobby.find_party_of(session)
        if party is None:
            return
        entity_id = party.world.get_player_entity(session.player_id)
        if entity_id is None:
            return
        self._apply_input(party.world, entity_id, session,
                          move_x, move_y, angle, shoot, weapon_id, flags)

    def _on_player_fire(
        self, session: PlayerSession, fire_seq: int, weapon_id: int = 1
    ) -> None:
        # Idempotent: the reliable channel may redeliver an intent, and it may
        # redeliver it LATE. The test is therefore "not newer than the last
        # decided intent", not "equal to the last accepted one": the reliable
        # channel retransmits with rto=0.2 and exponential backoff, so an intent
        # refused for cooldown can come back after a later intent was accepted.
        # Deciding it again would overwrite that acceptance with a bogus refusal,
        # which the client then charges against whatever it has in flight (the
        # accepted shot loses its recoil mid-animation). The client numbers its
        # intents strictly increasing (send_fire bumps the counter on every
        # send), so "not newer" can never reject a fresh intent.
        if fire_seq <= session.last_fire_seq:
            return
        party = self.lobby.find_party_of(session)
        if party is None:
            # An intent the server cannot process still gets a decision: a
            # silent drop is indistinguishable from a lost packet, and the
            # client would keep the predicted recoil playing with no shot and no
            # answer ever coming. The press is spent (retrying cannot help).
            session.last_fire_result = FireResult(False, 'noop', 0)
            return
        entity_id = party.world.get_player_entity(session.player_id)
        if entity_id is None:
            session.last_fire_result = FireResult(False, 'noop', 0)
            return
        apply_weapon_switch(party.world, entity_id, weapon_id)
        result = weapons_handle_shoot(party.world, entity_id)
        if result.fired:
            session.last_fire_seq = fire_seq
        session.last_fire_result = result

    def _apply_input(
        self, world: GameWorld, entity_id: int, session: PlayerSession,
        move_x: int, move_y: int, angle: float,
        shoot: bool, weapon_id: int = 1, flags: int = 0,
    ) -> None:
        vel = world.entities.get_component(entity_id, Velocity)
        rot = world.entities.get_component(entity_id, Rotation)
        pl = world.entities.get_component(entity_id, Player)
        dash = world.entities.get_component(entity_id, Dash)

        if rot:
            rot.angle = normalize_angle(angle)

        # Dash needs movement input to read a direction from, and must not be
        # on cooldown.  Setting ticks and cooldown together makes the start
        # idempotent: a redelivered PLAYER_INPUT cannot double-trigger it.
        dashing = False
        if dash is not None:
            # The flag only triggers; the boost then lasts on its own timer, so
            # a single press keeps dashing for the whole DASH_TICKS window.
            if (flags & INPUT_FLAG_DASH) and (move_x or move_y) \
                    and dash.cooldown <= 0:
                dash.ticks = DASH_TICKS
                dash.cooldown = DASH_COOLDOWN_TICKS
            if dash.ticks > 0:
                dashing = True

        if vel:
            cos_a = math.cos(rot.angle)
            sin_a = math.sin(rot.angle)
            spd = MOVE_SPEED_PX * TICK_INTERVAL
            if dashing:
                spd *= DASH_SPEED_MULT
            vel.dx = (move_x * cos_a - move_y * sin_a) * spd
            vel.dy = (move_x * sin_a + move_y * cos_a) * spd

        # weapon switch
        apply_weapon_switch(world, entity_id, weapon_id)

        # The `shoot` field is the HELD fire button and carries no trigger: the
        # only path to a shot is the reliable FIRE intent, so the 25 Hz input
        # channel cannot race it and no intent can be silently eaten by the
        # weapon cooldown here.
        if pl:
            # Dash state is derived from the authoritative dash component, not
            # from a key press, so a refused dash cannot flash. Shooting is not
            # published from input at all: state = 2 is set by handle_shoot()
            # only when a shot really happened.
            pl.state = 3 if dashing else 0

    def _on_player_ping(self, session: PlayerSession, timestamp: float) -> None:
        session.last_ping = time.monotonic()
        self.network.send_pong(session, timestamp)

    def stop(self) -> None:
        self.running = False
        self.network.stop()
