from __future__ import annotations

import asyncio
import logging
import os
import time
import math
from pathlib import Path

from shared.components import (
    Velocity, Rotation, Player, Dash, Powerup, Health,
)
from shared.map_data import GameMap
from shared.sector_map import MAP_FORMAT_SECTOR, SectorMap
from shared.releases import ReleaseStore, is_newer
from shared.version import CLIENT_VERSION, PROTOCOL_VERSION
from shared.combat import normalize_angle, fire_reason_code
from shared.protocol import INPUT_FLAG_DASH, INPUT_FLAG_USE
from server.game_world import GameWorld
from server.game_data import (
    MOVE_SPEED_PX, TICK_RATE, TICK_INTERVAL, TICK_DRAIN_MAX,
    DASH_SPEED_MULT, DASH_TICKS, DASH_COOLDOWN_TICKS, HASTE_SPEED_MULT,
)
from server.lobby import Lobby, Party
from server.network import ServerNetwork
from server.player import PlayerSession
from server.weapons import (
    FireResult, handle_shoot as weapons_handle_shoot, apply_weapon_switch)

logger = logging.getLogger(__name__)

# Minimum seconds between two client artifacts served to the same address.
# Retransmits of a lost FILE_REQUEST legitimately want a re-send, so the gate is
# a cooldown rather than "once per session".
ARTIFACT_RESEND_COOLDOWN = 30.0

# Hard ceiling on concurrent parties. Each party runs its own world and tick, so
# a flood of sessions (spoofed source ports, each completing HELLO+JOIN before
# the ping timeout) must not turn into an unbounded number of worlds.
MAX_PARTIES = 64

# Hard ceiling on members in one party.  Both the lobby snapshot and the world
# state pack their player count as a single byte ("!B"), so a party with 256
# members makes ``encode_world_state`` raise ``struct.error`` inside the tick's
# broadcast -- one over-full party would take down every player's snapshot.
# The cap stays far below 255 so both counts are safe by construction.
MAX_PARTY_MEMBERS = 16


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
        # Last time a client artifact was sent per address, for the update
        # channel rate limit. A FILE_REQUEST is an inner reliable message, so
        # any peer that completed the handshake can send it; without a gate it
        # re-sends the whole (multi-megabyte) artifact on every datagram.
        self._artifact_sent_at: dict[tuple[str, int], float] = {}

    async def start(self) -> None:
        self.running = True

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
        self.network.on_respawn(self._on_player_respawn)

        await self.network.start()
        logger.info(f"Game server started, tick rate: {TICK_RATE}")

        await self._game_loop()

    async def _game_loop(self) -> None:
        # The tick period is an ABSOLUTE deadline, not "how long the last
        # iteration happened to take". Two separate things were wrong before,
        # and together they put the whole server into permanent slow motion:
        #
        # 1. `await asyncio.sleep` on Windows quantizes to the system clock tick
        #    (~15.6 ms): sleep(0.05) returns after ~63 ms, not 50. A poll loop
        #    that re-checks `dt >= TICK_INTERVAL` every 1 ms therefore measures
        #    a steady 62.5 ms period — 15.8 tps instead of 20, and sleeping
        #    "the right amount" does not help either, it rounds up just the
        #    same.
        # 2. `self._last_tick_time = now` threw the overshoot away, so the
        #    shortfall never compensated: every tick permanently dropped the
        #    ~12.5 ms it woke up late. Fixing only the sleep is not enough.
        #
        # Why a 21 % slow tick rate breaks firing specifically: TICK_RATE is
        # what `wpn_cooldown_ticks` converts weapon fire rates with, on BOTH
        # sides of the wire, so the client's fire gate is measured in nominal
        # ticks while the server drains them in real ones. At 15.8 tps a 5-tick
        # pistol cooldown takes 0.32 s of wall time while the gate stays 0.30 s,
        # so every SECOND intent arrives with a hot barrel, is refused for
        # cooldown, and cuts the recoil the client had already predicted:
        # "animation, ammo counter, no shot, animation, no shot". The same 21 %
        # also slowed movement, dash and enemy AI for every player.
        #
        # Missed periods are made up by running ticks back to back (bounded by
        # TICK_DRAIN_MAX), so the cooldowns still drain at the nominal rate even
        # when the wake-ups are late or uneven.
        deadline = time.monotonic()
        while self.running:
            now = time.monotonic()
            ticks = 0
            while now - deadline >= TICK_INTERVAL:
                deadline += TICK_INTERVAL
                # One bad tick must not end the server: an exception here used
                # to escape `_game_loop`, stop `while self.running` and kill the
                # process for every connected player.  Simulation and broadcast
                # are isolated so the loop keeps a stable deadline.
                try:
                    self.lobby.update_all()
                    self._broadcast_parties()
                    self._remove_stale_players()
                except Exception:
                    logger.exception("Game tick failed; server continues")
                ticks += 1
                if ticks >= TICK_DRAIN_MAX:
                    # Fell too far behind (map load, GC pause). Replaying the
                    # whole backlog here would stall the loop even harder, so
                    # the debt is dropped instead of repaid.
                    deadline = now
                    break
                now = time.monotonic()
            try:
                self.network.tick()
            except Exception:
                logger.exception("Network tick failed; server continues")
            # Следующий тик наступает в `deadline + TICK_INTERVAL`: на выходе
            # цикла инвариант `deadline <= now < deadline + TICK_INTERVAL`.
            # Сон по одному `deadline - now` всегда <= 0 после пробуждения с
            # опозданием меньше тика (а опоздание до тика — норма: asyncio на
            # Windows квантует вверх до ~15.6 мс), и `sleep(0)` превращался в
            # карусель, которая жрала целое ядро между тиками. Тайп от этого
            # не страдал (дедлайн двигался верно), ядро — страдало.
            left = deadline + TICK_INTERVAL - time.monotonic()
            await asyncio.sleep(left if left > 0.0 else 0.0)

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
            items = w.get_item_states()
            doors = w.get_door_states()
            addresses = [s.address for s in party.members.values()]
            self.network.broadcast_world_state(
                addresses, w.tick, players, enemies, bullets, corpses,
                party.mode, impacts, items, doors, w.get_match_state(),
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
            self._artifact_sent_at.pop(addr, None)
            if session:
                self.lobby.remove_player(session)
                logger.info(f"Player unreachable, removed: {session.name}")
                removed = True
        # `last_ping` starts at session creation, so a session that joined but
        # never pinged is timed out like any other; there is no `> 0` guard
        # because a positive timestamp is now guaranteed from the first moment.
        stale = [
            addr for addr, session in self.network.players.items()
            if now - session.last_ping > 10.0
        ]
        for addr in stale:
            session = self.network.remove_player(addr)
            self._artifact_sent_at.pop(addr, None)
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
        info = self.network.client_info.get(addr, {})
        # Only a client that completed the handshake AND was told it needs an
        # update has any business asking for the artifact. Serving on any
        # request turns the server into a free file host and lets one peer pull
        # the whole multi-megabyte artifact over and over.
        if not info.get("protocol_ok") or not info.get("needs_update"):
            logger.debug(f"Ignoring FILE_REQUEST from {addr}: no update pending")
            return
        now = time.monotonic()
        if now - self._artifact_sent_at.get(addr, 0.0) < ARTIFACT_RESEND_COOLDOWN:
            logger.debug(f"Throttling FILE_REQUEST from {addr}")
            return
        release = self.releases.latest()
        if release is None:
            logger.warning(f"Update requested by {addr} but no artifact is available")
            return
        data = self.releases.read(release["version"])
        if data is None:
            logger.warning(f"Release {release['version']} listed but unreadable")
            return
        self._artifact_sent_at[addr] = now
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
        if len(self.lobby.parties) >= MAX_PARTIES:
            logger.warning(f"Refused party create from {session.name}: at cap")
            self.network.send_join_fail(session, "Слишком много партий на сервере")
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
        # Full party is refused here, while the joined session still belongs to
        # its old party (if any): kicking it out first and then refusing would
        # drop the player from a party they were in for no reason.
        if len(party.members) >= MAX_PARTY_MEMBERS and session.player_id not in party.members:
            self.network.send_join_fail(session, "Партия заполнена")
            return
        if current is not None:
            self.lobby.remove_player(session)
        self._add_to_party(session, party)
        self._broadcast_lobby()

    def _add_to_party(self, session: PlayerSession, party: Party) -> None:
        # Defensive: every caller must have checked the cap already, but adding
        # past it would corrupt the "!B" player count in the broadcasts, so the
        # guard lives next to the mutation as well.
        if len(party.members) >= MAX_PARTY_MEMBERS and session.player_id not in party.members:
            self.network.send_join_fail(session, "Партия заполнена")
            return
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
        # Exactly once per intent, always answered. Two facts are kept apart:
        # `last_fire_decided` is the watermark of everything already decided
        # (accepted or refused); `last_fire_seq` is the last ACCEPTED one.
        if fire_seq <= session.last_fire_decided:
            return
        party = self.lobby.find_party_of(session)
        if party is None:
            session.last_fire_decided = fire_seq
            session.last_fire_result = FireResult(False, 'noop', 0)
            return
        entity_id = party.world.get_player_entity(session.player_id)
        if entity_id is None:
            session.last_fire_decided = fire_seq
            session.last_fire_result = FireResult(False, 'noop', 0)
            return
        # A dead player cannot fire, and neither can anyone after the match is
        # over: the intent is answered (never dropped silently) but it does not
        # move fire_seq, so the client sees the refusal, not a phantom shot.
        hp = party.world.entities.get_component(entity_id, Health)
        pl = party.world.entities.get_component(entity_id, Player)
        if party.world.match_phase == 'over' \
                or (pl is not None and pl.state == 1) \
                or (hp is not None and hp.value <= 0):
            session.last_fire_decided = fire_seq
            session.last_fire_result = FireResult(False, 'noop', 0)
            return
        apply_weapon_switch(party.world, entity_id, weapon_id)
        result = weapons_handle_shoot(party.world, entity_id)
        if result.fired:
            session.last_fire_seq = fire_seq
        session.last_fire_decided = fire_seq
        session.last_fire_result = result

    def _on_player_respawn(self, session: PlayerSession) -> None:
        # A reliable request; the world ignores it for a living player, so the
        # only thing to check here is that the session actually belongs to a
        # party world with a player entity.
        party = self.lobby.find_party_of(session)
        if party is None:
            return
        party.world.respawn_player(session.player_id)

    def _apply_input(
        self, world: GameWorld, entity_id: int, session: PlayerSession,
        move_x: int, move_y: int, angle: float,
        shoot: bool, weapon_id: int = 1, flags: int = 0,
    ) -> None:
        vel = world.entities.get_component(entity_id, Velocity)
        rot = world.entities.get_component(entity_id, Rotation)
        pl = world.entities.get_component(entity_id, Player)
        dash = world.entities.get_component(entity_id, Dash)

        # Frozen match: nothing may change.  Dead player: input is ignored
        # wholesale, so a client that keeps sending movement/dash/use cannot
        # drive a corpse around or open a door behind it (spec: input of a dead
        # player is ignored).  Input arrives at 25 Hz, so this cannot be left to
        # the display state alone.
        if world.match_phase == 'over':
            return
        hp_now = world.entities.get_component(entity_id, Health)
        if (pl is not None and pl.state == 1) \
                or (hp_now is not None and hp_now.value <= 0):
            return

        # The input channel is untrusted: move and angle are exactly what the
        # client sent, and nothing downstream re-checks them.  A non-finite
        # angle (NaN/Inf) survives `normalize_angle` (NaN % TAU is NaN), then
        # poisons cos/sin, the velocity, the position and finally the collision
        # helper -- `int(math.ceil(nan / MAX_MOVE_STEP_PX))` raises ValueError
        # and takes the whole game loop down.  Motion is applied only when the
        # angle is finite; an out-of-range move vector (the wire allows -128..127,
        # which would be a 127x speed hack) is clamped to the unit square.
        move_x = -1 if move_x < -1 else 1 if move_x > 1 else move_x
        move_y = -1 if move_y < -1 else 1 if move_y > 1 else move_y
        if rot and math.isfinite(angle):
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
            # HASTE and the dash are alternatives, not multipliers of each other:
            # a dash is already DASH_SPEED_MULT (6x), and stacking the buff on
            # top would make it a 9x movement button that trivialises the map
            # for as long as it lasts. While dashing, HASTE is ignored.
            if dashing:
                spd *= DASH_SPEED_MULT
            else:
                pu = world.entities.get_component(entity_id, Powerup)
                if pu is not None and pu.kind == 'HASTE':
                    spd *= HASTE_SPEED_MULT
            vel.dx = (move_x * cos_a - move_y * sin_a) * spd
            vel.dy = (move_x * sin_a + move_y * cos_a) * spd

        # "Use" (open a nearby door) is a rising-edge action: the flag is held
        # while the key is down, and only the transition triggers one attempt
        # (design D4).  It is independent of movement and the fire button.
        world.apply_use_input(entity_id, bool(flags & INPUT_FLAG_USE))

        # weapon switch
        apply_weapon_switch(world, entity_id, weapon_id)

        # The `shoot` field is the HELD fire button and carries no trigger: the
        # only path to a shot is the reliable FIRE intent, so the 25 Hz input
        # channel cannot race it and no intent can be silently eaten by the
        # weapon cooldown here.
        if pl and pl.state != 1:
            # Dash state is derived from the authoritative dash component, not
            # from a key press, so a refused dash cannot flash. Shooting is not
            # published from input at all: state = 2 is set by handle_shoot()
            # only when a shot really happened.
            # A dead player keeps state = 1: input arrives at 25 Hz independently
            # of the 20 Hz tick, so an unguarded write here can clear the death
            # that a same-tick respawn timer is about to expire.
            pl.state = 3 if dashing else 0

    def _on_player_ping(self, session: PlayerSession, timestamp: float) -> None:
        session.last_ping = time.monotonic()
        self.network.send_pong(session, timestamp)

    def stop(self) -> None:
        self.running = False
        self.network.stop()
