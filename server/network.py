from __future__ import annotations

import asyncio
import logging
import time

from shared.protocol import (
    Packet, PacketType,
    decode_player_input, encode_world_state,
    encode_join_accept, decode_join_request,
    encode_ping, decode_ping,
    encode_lobby_state, encode_join_fail, encode_party_created,
    decode_create_party, decode_join_party,
    decode_hello, encode_hello_ack, decode_file_complete, decode_fire,
)
from shared.reliability import ReliableEndpoint
from shared.map_data import GameMap
from server.player import PlayerSession

logger = logging.getLogger(__name__)

# Control-plane messages that must go through the reliable channel.
RELIABLE_INNER = frozenset({
    PacketType.JOIN_REQUEST,
    PacketType.GET_LOBBY,
    PacketType.CREATE_PARTY,
    PacketType.JOIN_PARTY,
    PacketType.LEAVE_PARTY,
    PacketType.FIRE,
    PacketType.PLAYER_RESPAWN,
})

# Hard ceiling on how many peer addresses the server will remember at once.
# `_endpoints`, `client_info` and `_last_seen` are keyed by source address, and
# a peer is free to rotate its UDP source port on every datagram: HELLO, ACK and
# RELIABLE_DATA all create/keep an entry for an address that never joins, so
# without a cap a flood is unbounded growth (a memory DoS).  Joined sessions and
# addresses waiting on the update prompt are pinned (never evicted); everything
# else is dropped least-recently-seen once the table is full.
MAX_TRACKED_ADDRS = 1024

# Hard ceiling on concurrent joined sessions.  JOIN_REQUEST already requires an
# accepted handshake, but a single host can complete the handshake from many
# ports; each session is a player record in the lobby, so the count is capped.
MAX_SESSIONS = 64



class ServerNetwork:
    def __init__(self, host: str = "0.0.0.0", port: int = 7777):
        self.host = host
        self.port = port
        self.transport: asyncio.DatagramTransport | None = None
        self.players: dict[tuple[str, int], PlayerSession] = {}
        self.client_info: dict[tuple[str, int], dict] = {}
        self._next_player_id: int = 1
        self._endpoints: dict[tuple[str, int], ReliableEndpoint] = {}
        # Last time each tracked address sent a datagram.  The LRU key that
        # bounds ``_endpoints``/``client_info`` under an address-rotating flood.
        self._last_seen: dict[tuple[str, int], float] = {}
        self._on_input_callback = None
        self._on_join_callback = None
        self._on_leave_callback = None
        self._on_ping_callback = None
        self._on_lobby_request_callback = None
        self._on_create_party_callback = None
        self._on_join_party_callback = None
        self._on_leave_party_callback = None
        self._on_hello_callback = None
        self._on_file_request_callback = None
        self._on_file_complete_callback = None
        self._on_fire_callback = None
        self._on_respawn_callback = None
        # Addresses that produced a connection-reset / port-unreachable error:
        # they get no more traffic and their sessions are removed next tick.
        self.unreachable: set[tuple[str, int]] = set()
        # Throttle window per error signature and its repeat counter.
        self._error_interval = 5.0
        self._error_log: dict[tuple, list[float, int]] = {}

    def on_input(self, callback):
        self._on_input_callback = callback

    def on_join(self, callback):
        self._on_join_callback = callback

    def on_leave(self, callback):
        self._on_leave_callback = callback

    def on_ping(self, callback):
        self._on_ping_callback = callback

    def on_lobby_request(self, callback):
        self._on_lobby_request_callback = callback

    def on_create_party(self, callback):
        self._on_create_party_callback = callback

    def on_join_party(self, callback):
        self._on_join_party_callback = callback

    def on_leave_party(self, callback):
        self._on_leave_party_callback = callback

    def on_hello(self, callback):
        self._on_hello_callback = callback

    def on_file_request(self, callback):
        self._on_file_request_callback = callback

    def on_file_complete(self, callback):
        self._on_file_complete_callback = callback

    def on_fire(self, callback):
        self._on_fire_callback = callback

    def on_respawn(self, callback):
        self._on_respawn_callback = callback

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self.transport, _ = await loop.create_datagram_endpoint(
            lambda: self,
            local_addr=(self.host, self.port)
        )
        logger.info(f"Server listening on {self.host}:{self.port}")

    def connection_made(self, transport) -> None:
        self.transport = transport

    def connection_lost(self, exc) -> None:
        pass

    def _is_expected_conn_error(self, exc) -> bool:
        """Connection-reset / port-unreachable errors are routine (debug level)."""
        if isinstance(exc, (ConnectionResetError, ConnectionRefusedError, BrokenPipeError)):
            return True
        errno = getattr(exc, 'errno', None)
        winerror = getattr(exc, 'winerror', None)
        return errno in (10054, 10061) or winerror in (10054, 10061)

    def error_received(self, exc) -> None:
        # Repeated identical socket errors (e.g. a storm of WinError 10054 from
        # a closed client socket) are aggregated and logged at most once per
        # interval instead of one line per error.
        key = (type(exc).__name__, getattr(exc, 'errno', None), getattr(exc, 'winerror', None))
        now = time.monotonic()
        entry = self._error_log.get(key)
        if entry is not None and now - entry[0] < self._error_interval:
            entry[1] += 1
            return
        if entry is not None:
            repeats = entry[1] + 1  # the ones suppressed since the last log
        else:
            repeats = 0
        self._error_log[key] = [now, 0]
        level = logging.DEBUG if self._is_expected_conn_error(exc) else logging.WARNING
        if repeats:
            logger.log(level, f"Network error (x{repeats} since last log): {exc}")
        else:
            logger.log(level, f"Network error: {exc}")

    # ------------------------------------------------------------- transport
    def _endpoint(self, addr: tuple[str, int]) -> ReliableEndpoint:
        endpoint = self._endpoints.get(addr)
        if endpoint is None:
            endpoint = ReliableEndpoint(
                lambda ptype, seq, payload: self._send_to(addr, Packet(ptype, seq, payload))
            )
            self._endpoints[addr] = endpoint
        self._last_seen[addr] = time.monotonic()
        self._evict_addrs_if_needed()
        return endpoint

    def _evict_addrs_if_needed(self) -> None:
        """Bound the per-address tables under an address-rotating flood.

        A peer never joins and only needs an entry for HELLO/ACK/RELIABLE_DATA,
        so its address stays in ``_endpoints`` and ``client_info`` forever unless
        something removes it.  Joined sessions are pinned (they belong to the
        lobby and are only removed by the ping timeout), as are addresses still
        waiting on the update prompt (evicting those would wipe ``protocol_ok``
        and make the next FILE_REQUEST look unauthorised).  Everything else goes
        least-recently-seen first until the table fits.
        """
        if len(self._last_seen) <= MAX_TRACKED_ADDRS:
            return
        pinned = set(self.players)
        for addr, info in self.client_info.items():
            if info.get("needs_update"):
                pinned.add(addr)
        evictable = [a for a in self._last_seen if a not in pinned]
        if not evictable:
            return
        evictable.sort(key=lambda a: self._last_seen[a])
        for addr in evictable[:len(self._last_seen) - MAX_TRACKED_ADDRS]:
            self._drop_addr(addr)

    def _drop_addr(self, addr: tuple[str, int]) -> None:
        """Forget every per-address table entry (does NOT touch a session)."""
        self._endpoints.pop(addr, None)
        self.client_info.pop(addr, None)
        self._last_seen.pop(addr, None)

    def tick(self) -> None:
        now = time.monotonic()
        for endpoint in self._endpoints.values():
            endpoint.tick(now)

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        # A datagram is untrusted bytes: a valid 3-byte header with a short or
        # garbage payload makes every `decode_*` raise `struct.error`, which
        # would otherwise surface as an unhandled callback exception and flood
        # the log.  A malformed datagram is dropped; one bad packet must never
        # affect the rest of the server.
        self._last_seen[addr] = time.monotonic()
        self._evict_addrs_if_needed()
        try:
            packet = Packet.decode(data)
            if packet is None:
                return

            if packet.ptype == PacketType.HELLO:
                self._handle_hello(addr, packet.payload)
            elif packet.ptype == PacketType.RELIABLE_DATA:
                self._endpoint(addr).on_reliable(packet.sequence, packet.payload)
                self._drain_reliable(addr)
            elif packet.ptype == PacketType.ACK:
                self._endpoint(addr).on_ack(packet.payload)
            elif packet.ptype == PacketType.JOIN_REQUEST:
                # Accepted only through the reliable channel.
                return
            elif packet.ptype == PacketType.PLAYER_INPUT:
                self._handle_input(addr, packet)
            elif packet.ptype == PacketType.PING:
                self._handle_ping(addr, packet)
        except Exception:
            logger.debug(f"Malformed datagram from {addr} ({len(data)} bytes)",
                         exc_info=True)

    def _drain_reliable(self, addr: tuple[str, int]) -> None:
        endpoint = self._endpoint(addr)
        for inner_type, payload in endpoint.pop_messages():
            self._dispatch_inner(addr, inner_type, payload)
        for inner_type in endpoint.pop_failed():
            logger.warning(f"Integrity check failed for message {inner_type:#x} from {addr}")

    def _dispatch_inner(self, addr: tuple[str, int], inner_type: int, payload: bytes) -> None:
        try:
            inner = PacketType(inner_type)
        except ValueError:
            logger.warning(f"Unknown reliable message {inner_type:#x} from {addr}")
            return
        if inner == PacketType.HELLO:
            self._handle_hello(addr, payload)
        elif inner == PacketType.JOIN_REQUEST:
            self._handle_join(addr, payload)
        elif inner == PacketType.GET_LOBBY:
            self._with_session(addr, self._on_lobby_request_callback)
        elif inner == PacketType.CREATE_PARTY:
            self._handle_create_party(addr, payload)
        elif inner == PacketType.JOIN_PARTY:
            self._handle_join_party(addr, payload)
        elif inner == PacketType.LEAVE_PARTY:
            self._with_session(addr, self._on_leave_party_callback)
        elif inner == PacketType.FILE_REQUEST:
            if self._on_file_request_callback:
                self._on_file_request_callback(addr)
        elif inner == PacketType.FILE_COMPLETE:
            if self._on_file_complete_callback:
                self._on_file_complete_callback(addr, decode_file_complete(payload))
        elif inner == PacketType.FIRE:
            self._handle_fire(addr, payload)
        elif inner == PacketType.PLAYER_RESPAWN:
            self._with_session(addr, self._on_respawn_callback)

    def _get_session(self, addr: tuple[str, int]) -> PlayerSession | None:
        return self.players.get(addr)

    def _with_session(self, addr: tuple[str, int], callback) -> None:
        session = self._get_session(addr)
        if session is not None and callback:
            callback(session)

    def _protocol_ok(self, addr: tuple[str, int]) -> bool:
        return bool(self.client_info.get(addr, {}).get("protocol_ok"))

    # ------------------------------------------------------------ handshake
    def _handle_hello(self, addr: tuple[str, int], payload: bytes) -> None:
        protocol_version, client_version = decode_hello(payload)
        info = self.client_info.setdefault(addr, {})
        self._last_seen[addr] = time.monotonic()
        self._evict_addrs_if_needed()
        info["protocol_version"] = protocol_version
        info["client_version"] = client_version
        if self._on_hello_callback:
            self._on_hello_callback(addr, protocol_version, client_version)

    def send_hello_ack(self, addr: tuple[str, int], **fields) -> None:
        payload = encode_hello_ack(**fields)
        self._endpoint(addr).send_reliable(PacketType.HELLO_ACK, payload)

    # ------------------------------------------------------------ join/lobby
    def _handle_join(self, addr: tuple[str, int], payload: bytes) -> None:
        if addr in self.players:
            return
        if not self._protocol_ok(addr):
            logger.info(f"Ignoring JOIN_REQUEST from {addr}: handshake not accepted")
            return
        # Cap concurrent sessions BEFORE allocating a player id: one host can
        # complete the handshake from many source ports, and each session is a
        # lobby record.  The refusal is a proper JOIN_FAIL so the client is told,
        # not silently left waiting.
        if len(self.players) >= MAX_SESSIONS:
            logger.warning(f"Refusing JOIN_REQUEST from {addr}: at session cap")
            name_attempt = decode_join_request(payload)
            self.send_join_fail(
                PlayerSession(0, name_attempt, addr), "Сервер переполнен"
            )
            return

        name = decode_join_request(payload)
        player_id = self._next_player_id
        self._next_player_id += 1

        session = PlayerSession(player_id, name, addr)
        self.players[addr] = session
        logger.info(f"Player joined: {name} (id={player_id}) from {addr}")

        if self._on_join_callback:
            self._on_join_callback(session)

    def send_join_accept(self, session: PlayerSession, game_map: GameMap | None = None) -> None:
        if game_map is None:
            payload = encode_join_accept(session.player_id)
        else:
            from shared.sector_map import MAP_FORMAT_SECTOR, SectorMap
            if isinstance(game_map, SectorMap):
                payload = encode_join_accept(
                    session.player_id,
                    0,
                    0,
                    game_map.to_bytes(),
                    MAP_FORMAT_SECTOR,
                )
            else:
                payload = encode_join_accept(
                    session.player_id,
                    game_map.width,
                    game_map.height,
                    game_map.to_bytes(),
                )
        self._endpoint(session.address).send_reliable(PacketType.JOIN_ACCEPT, payload)

    def send_lobby_state(self, session: PlayerSession, snapshot: list[dict]) -> None:
        payload = encode_lobby_state(snapshot)
        self._endpoint(session.address).send_reliable(PacketType.LOBBY_STATE, payload)

    def send_join_fail(self, session: PlayerSession, reason: str) -> None:
        payload = encode_join_fail(reason)
        self._endpoint(session.address).send_reliable(PacketType.JOIN_FAIL, payload)

    def send_party_created(self, session: PlayerSession, party_id: int) -> None:
        payload = encode_party_created(party_id)
        self._endpoint(session.address).send_reliable(PacketType.PARTY_CREATED, payload)

    def send_file(self, addr: tuple[str, int], data: bytes) -> None:
        self._endpoint(addr).send_reliable(PacketType.FILE_DATA, data)

    def send_file_complete(self, session: PlayerSession, ok: bool) -> None:
        from shared.protocol import encode_file_complete
        payload = encode_file_complete(ok)
        self._endpoint(session.address).send_reliable(PacketType.FILE_COMPLETE, payload)

    def _handle_input(self, addr: tuple[str, int], packet: Packet) -> None:
        session = self._get_session(addr)
        if session is None:
            return

        move_x, move_y, angle, shoot, weapon_id, flags = decode_player_input(
            packet.payload)
        session.last_input_tick = packet.sequence

        if self._on_input_callback:
            self._on_input_callback(session, move_x, move_y, angle, shoot,
                                    weapon_id, flags)

    def _handle_fire(self, addr: tuple[str, int], payload: bytes) -> None:
        session = self._get_session(addr)
        if session is None:
            return
        fire_seq, weapon_id = decode_fire(payload)
        if self._on_fire_callback:
            self._on_fire_callback(session, fire_seq, weapon_id)

    def _handle_ping(self, addr: tuple[str, int], packet: Packet) -> None:
        session = self._get_session(addr)
        if session is None:
            return

        timestamp = decode_ping(packet.payload)
        if self._on_ping_callback:
            self._on_ping_callback(session, timestamp)

    def _handle_create_party(self, addr: tuple[str, int], payload: bytes) -> None:
        session = self._get_session(addr)
        if session is None:
            return
        from shared.protocol import decode_create_party_ex
        party_name, mode, map_width, map_height, map_data, request_id, map_format = (
            decode_create_party_ex(payload)
        )
        if self._on_create_party_callback:
            self._on_create_party_callback(
                session, party_name, mode, map_width, map_height, map_data,
                request_id, map_format,
            )

    def _handle_join_party(self, addr: tuple[str, int], payload: bytes) -> None:
        session = self._get_session(addr)
        if session is None:
            return
        party_id = decode_join_party(payload)
        if self._on_join_party_callback:
            self._on_join_party_callback(session, party_id)

    def send_pong(self, session: PlayerSession, timestamp: float) -> None:
        payload = encode_ping(timestamp)
        packet = Packet(PacketType.PONG, session.next_sequence(), payload)
        self._send_to(session.address, packet)

    def broadcast_world_state(
        self, addresses: list[tuple[str, int]], tick: int,
        player_states: list[dict],
        enemies: list[dict] | None = None,
        bullets: list[dict] | None = None,
        corpses: list[dict] | None = None,
        mode: str = 'pve',
        impacts: list[dict] | None = None,
        items: list[dict] | None = None,
        doors: list[dict] | None = None,
        match: dict | None = None,
    ) -> None:
        payload = encode_world_state(tick, player_states, enemies, bullets,
                                     corpses, mode, impacts, items, doors,
                                     match)
        packet = Packet(PacketType.WORLD_STATE, tick & 0xFFFF, payload)
        data = packet.encode()
        for addr in addresses:
            if addr in self.unreachable:
                continue
            if not self._protocol_ok(addr):
                continue
            try:
                self.transport.sendto(data, addr)
            except OSError as exc:
                self._mark_unreachable(addr, exc)

    def send_packet(self, session: PlayerSession, packet: Packet) -> None:
        self._send_to(session.address, packet)

    def _send_to(self, addr: tuple[str, int], packet: Packet) -> None:
        if not self.transport:
            return
        try:
            self.transport.sendto(packet.encode(), addr)
        except OSError as exc:
            self._mark_unreachable(addr, exc)

    def _mark_unreachable(self, addr: tuple[str, int], exc: Exception | None = None) -> None:
        """Flag an address as dead and stop sending traffic to it."""
        if addr in self.unreachable:
            return
        self.unreachable.add(addr)
        logger.debug(
            f"Address {addr} marked unreachable and excluded from traffic: {exc}"
        )

    def remove_player(self, addr: tuple[str, int]) -> PlayerSession | None:
        self.unreachable.discard(addr)
        self._drop_addr(addr)
        return self.players.pop(addr, None)

    def stop(self) -> None:
        if self.transport:
            self.transport.close()
