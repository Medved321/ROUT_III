from __future__ import annotations

import asyncio
import logging
import math
import socket
import time

from shared.protocol import (
    Packet, PacketType,
    encode_player_input, decode_world_state, decode_match_block,
    encode_join_request, decode_join_accept,
    encode_ping, decode_ping,
    encode_create_party, encode_join_party,
    decode_lobby_state, decode_join_fail,
    encode_hello, decode_hello_ack,
    encode_file_request, encode_file_complete,
    encode_fire, encode_player_respawn,
)
from shared.reliability import ReliableEndpoint
from shared.version import CLIENT_VERSION, PROTOCOL_VERSION

logger = logging.getLogger(__name__)

RELIABLE_TICK = 0.02

# Snapshot smoothing: remote entities render with a small fixed delay and are
# linearly interpolated between the last two snapshots with receive time.
INTERP_DELAY = 0.04  # ~1 server tick (50 ms)
MAX_EXTRAPOLATION = 0.12  # bounded extrapolation after a lost snapshot
SNAP_DISTANCE = 96.0  # px: larger moves (teleports) are applied instantly


class UpdateOffer:
    def __init__(self, version: str, size: int, sha256: str):
        self.version = version
        self.size = size
        self.sha256 = sha256


class NetworkClient:
    def __init__(self, host: str = "localhost", port: int = 7777):
        self.host = host
        self.port = port
        self.server_addr: tuple[str, int] = (host, port)
        self.transport: asyncio.DatagramTransport | None = None
        self.player_id: int | None = None
        self.connected: bool = False
        self.state: str = "disconnected"  # "disconnected" | "lobby" | "game"
        self.map_data: bytes | None = None
        self.map_width: int = 0
        self.map_height: int = 0
        self.map_format: int = 0
        self.party_list: list[dict] = []
        self.last_error: str | None = None
        # (tick, players, enemies, bullets, corpses, mode, impacts, items, doors)
        self.latest_state: tuple[int, list[dict], list[dict], list[dict], list[dict], str, list[dict], list[dict], list[dict]] | None = None
        # Trailing match block of the newest snapshot (phase/winner/clock/secrets),
        # read separately from the 9-element entity tuple so nothing that unpacks
        # `latest_state` has to learn a tenth element.
        self.latest_match: dict = {
            "phase": "playing", "winner_id": 0, "time_left": 0,
            "secrets_total": 0,
        }
        # Recent (recv_time, state) history for interpolation; trimmed to a
        # small window so the renderer can blend between two consecutive states.
        self.snapshots: list[tuple[float, tuple]] = []
        self.ping_rtt: float = 0.0
        self._sequence: int = 0
        self._request_id: int = 0
        self._fire_seq: int = 0
        self._ping_start: float = 0.0
        self._on_state_callback = None

        self.protocol_ok: bool = False
        self.hello_ack: dict | None = None
        self.update_offer: UpdateOffer | None = None
        self.update_data: bytes | None = None
        self.update_complete: bool = False
        self._endpoint: ReliableEndpoint | None = None
        self._hello_event: asyncio.Event | None = None
        self._file_event: asyncio.Event | None = None

    def on_state(self, callback):
        self._on_state_callback = callback

    def _resolve_server(self) -> tuple[str, int]:
        family = socket.AF_INET
        try:
            infos = socket.getaddrinfo(self.host, self.port, family, socket.SOCK_DGRAM)
            ip = infos[0][4][0] if infos else self.host
        except socket.gaierror:
            logger.warning(f"Cannot resolve {self.host}, using as-is")
            ip = self.host
        self.server_addr = (ip, self.port)
        return self.server_addr

    def _init_endpoint(self) -> None:
        if self._endpoint is None:
            self._endpoint = ReliableEndpoint(
                lambda ptype, seq, payload: self._send(Packet(ptype, seq, payload))
            )
        self.hello_ack = None
        self.update_offer = None
        self.update_data = None
        self.update_complete = False

    async def connect(self, player_name: str) -> bool:
        loop = asyncio.get_running_loop()
        self._resolve_server()
        self.transport, _ = await loop.create_datagram_endpoint(
            lambda: self,
            local_addr=("0.0.0.0", 0)
        )
        self._init_endpoint()

        # 1. Handshake: negotiate protocol and learn about client updates.
        self._hello_event = asyncio.Event()
        self._endpoint.send_reliable(
            PacketType.HELLO, encode_hello(PROTOCOL_VERSION, CLIENT_VERSION)
        )
        if not await self._wait_for(self._hello_event, 5.0):
            logger.error("Connection timeout (handshake)")
            return False

        if not self.hello_ack or not self.hello_ack.get("accepted"):
            server_protocol = (self.hello_ack or {}).get("server_protocol", "?")
            self.last_error = (
                f"Несовместимая версия протокола: сервер {server_protocol}, "
                f"клиент {PROTOCOL_VERSION}. Обновите клиент."
            )
            logger.error(self.last_error)
            return False
        self.protocol_ok = True

        if self.hello_ack.get("has_update"):
            self.update_offer = UpdateOffer(
                self.hello_ack.get("update_version", ""),
                self.hello_ack.get("update_size", 0),
                self.hello_ack.get("update_sha256", ""),
            )
            logger.info(
                f"Доступно обновление клиента: {self.update_offer.version} "
                f"({self.update_offer.size} байт)"
            )

        # 2. Join the lobby over the reliable channel.
        self._endpoint.send_reliable(
            PacketType.JOIN_REQUEST, encode_join_request(player_name)
        )
        deadline = time.monotonic() + 5.0
        while not self.connected:
            self._endpoint.tick()
            await asyncio.sleep(0.01)
            if time.monotonic() > deadline:
                logger.error("Connection timeout")
                return False

        return True

    async def _wait_for(self, event: asyncio.Event, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while not event.is_set():
            if self._endpoint:
                self._endpoint.tick()
            if time.monotonic() > deadline:
                return False
            await asyncio.sleep(0.01)
        return True

    async def start_reliable_loop(self) -> None:
        while self.transport is not None and not self.transport.is_closing():
            if self._endpoint is not None:
                if self.ping_rtt > 0:
                    self._endpoint.rto = max(0.1, min(1.0, (self.ping_rtt / 1000.0) * 2.0))
                self._endpoint.tick()
            await asyncio.sleep(RELIABLE_TICK)

    async def start_ping_loop(self) -> None:
        while self.connected:
            self._send_ping()
            await asyncio.sleep(1.0)

    def _send_ping(self) -> None:
        self._ping_start = time.monotonic()
        packet = Packet(PacketType.PING, self._next_seq(), encode_ping(self._ping_start))
        self._send(packet)

    def _next_request_id(self) -> int:
        self._request_id = (self._request_id + 1) & 0xFFFFFFFF
        return self._request_id

    def send_get_lobby(self) -> None:
        if not self.connected or self._endpoint is None:
            return
        self.last_error = None
        self._endpoint.send_reliable(PacketType.GET_LOBBY, b"")

    def send_create_party(self, name: str, mode: str = 'pve',
                          map_data: bytes = b"", map_width: int = 0, map_height: int = 0,
                          map_format: int = 0) -> None:
        if not self.connected or self._endpoint is None:
            return
        payload = encode_create_party(
            name, mode, map_width, map_height, map_data,
            request_id=self._next_request_id(), map_format=map_format,
        )
        self._endpoint.send_reliable(PacketType.CREATE_PARTY, payload)

    def send_join_party(self, party_id: int) -> None:
        if not self.connected or self._endpoint is None:
            return
        self.last_error = None
        self._endpoint.send_reliable(PacketType.JOIN_PARTY, encode_join_party(party_id))

    def send_leave_party(self) -> None:
        if not self.connected or self._endpoint is None:
            return
        self.state = "lobby"
        self.latest_state = None
        self.latest_match = {"phase": "playing", "winner_id": 0,
                             "time_left": 0, "secrets_total": 0}
        self.snapshots = []
        self._endpoint.send_reliable(PacketType.LEAVE_PARTY, b"")

    def request_update(self) -> None:
        if self._endpoint is None or self.update_offer is None:
            return
        self.update_data = None
        self.update_complete = False
        self._file_event = asyncio.Event()
        self._endpoint.expect_sha256(PacketType.FILE_DATA, self.update_offer.sha256)
        self._endpoint.send_reliable(PacketType.FILE_REQUEST, encode_file_request())

    def send_file_complete(self, ok: bool) -> None:
        if self._endpoint is None:
            return
        self._endpoint.send_reliable(PacketType.FILE_COMPLETE, encode_file_complete(ok))

    def send_input(self, move_x: int, move_y: int, angle: float, shoot: bool,
                   weapon_id: int = 1, flags: int = 0) -> None:
        if self.state != "game":
            return
        packet = Packet(
            PacketType.PLAYER_INPUT,
            self._next_seq(),
            encode_player_input(move_x, move_y, angle, shoot, weapon_id, flags),
        )
        self._send(packet)

    def send_fire(self, weapon_id: int = 1) -> int:
        """Reliable per-click fire intent (monotonic fire_seq, exactly once).

        Returns the intent's number. The caller has to know which intent a later
        server decision is about: the snapshot's ``fire_seq``/``fire_reason`` pair
        cannot be attributed to an intent on its own, so the client keeps the
        numbers it handed out.
        """
        if self.state != "game" or self._endpoint is None:
            return self._fire_seq
        self._fire_seq = (self._fire_seq + 1) & 0xFFFFFFFF
        self._endpoint.send_reliable(
            PacketType.FIRE, encode_fire(self._fire_seq, weapon_id)
        )
        return self._fire_seq

    def send_respawn(self) -> None:
        """Reliable "put me back in" request; the server ignores it while alive.

        Sent exactly like every other control message (single UDP socket,
        RELIABLE_DATA envelope), so a lost request is retried instead of
        leaving the client dead on a screen nobody can dismiss.
        """
        if self.state != "game" or self._endpoint is None:
            return
        self._endpoint.send_reliable(
            PacketType.PLAYER_RESPAWN, encode_player_respawn()
        )

    def connection_made(self, transport) -> None:
        self.transport = transport

    def connection_lost(self, exc) -> None:
        pass

    def error_received(self, exc) -> None:
        logger.warning(f"Network error: {exc}")

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        # The socket is unconnected (bound to 0.0.0.0:0), so anyone who guesses
        # the ephemeral port can inject datagrams. Everything of consequence
        # arrives here -- world state, lobby, and the update offer -- so only the
        # server we resolved is trusted; anything else is dropped.
        if addr != self.server_addr:
            logger.debug(f"Dropping datagram from unexpected {addr}")
            return
        try:
            self._dispatch_datagram(data)
        except Exception:
            logger.debug(f"Malformed datagram from {addr} ({len(data)} bytes)",
                         exc_info=True)

    def _dispatch_datagram(self, data: bytes) -> None:
        packet = Packet.decode(data)
        if packet is None:
            return

        if packet.ptype == PacketType.HELLO_ACK:
            self._handle_hello_ack(packet.payload)
        elif packet.ptype == PacketType.RELIABLE_DATA:
            if self._endpoint is None:
                self._init_endpoint()
            self._endpoint.on_reliable(packet.sequence, packet.payload)
            self._drain_reliable()
        elif packet.ptype == PacketType.ACK:
            if self._endpoint is None:
                self._init_endpoint()
            self._endpoint.on_ack(packet.payload)
        elif packet.ptype == PacketType.WORLD_STATE:
            self._handle_world_state(packet)
        elif packet.ptype == PacketType.PONG:
            self._handle_pong(packet)
        elif packet.ptype == PacketType.DEATH_EVENT:
            self._handle_death(packet)

    def _drain_reliable(self) -> None:
        for inner_type, payload in self._endpoint.pop_messages():
            try:
                inner = PacketType(inner_type)
            except ValueError:
                logger.warning(f"Unknown reliable message {inner_type:#x}")
                continue
            if inner == PacketType.HELLO_ACK:
                self._handle_hello_ack(payload)
            elif inner == PacketType.JOIN_ACCEPT:
                self._handle_join_accept(payload)
            elif inner == PacketType.LOBBY_STATE:
                self._handle_lobby_state(payload)
            elif inner == PacketType.JOIN_FAIL:
                self._handle_join_fail(payload)
            elif inner == PacketType.PARTY_CREATED:
                logger.info(f"Party created (id={payload[:2]})")
            elif inner == PacketType.FILE_DATA:
                self.update_data = payload
                if self._file_event is not None:
                    self._file_event.set()
            elif inner == PacketType.FILE_COMPLETE:
                from shared.protocol import decode_file_complete
                self.update_complete = decode_file_complete(payload)
        for inner_type in self._endpoint.pop_failed():
            logger.warning(f"Проверка целостности не пройдена: {inner_type:#x}")

    def _handle_hello_ack(self, payload: bytes) -> None:
        self.hello_ack = decode_hello_ack(payload)
        if self._hello_event is not None:
            self._hello_event.set()

    def _handle_join_accept(self, payload: bytes) -> None:
        pid, mw, mh, has_map, map_data, map_format = decode_join_accept(payload)
        self.player_id = pid
        if has_map:
            self.map_data = map_data
            self.map_width = mw
            self.map_height = mh
            self.map_format = map_format
            self.state = "game"
            self.latest_state = None
            self.snapshots = []
        else:
            self.map_format = 0
            self.state = "lobby"
            self.send_get_lobby()
        self.connected = True
        logger.info(f"Connected as player {pid}, state={self.state}")

    def _handle_lobby_state(self, payload: bytes) -> None:
        self.party_list = decode_lobby_state(payload)

    def _handle_join_fail(self, payload: bytes) -> None:
        self.last_error = decode_join_fail(payload)
        logger.warning(f"Join failed: {self.last_error}")

    def _handle_world_state(self, packet: Packet) -> None:
        (tick, players, enemies, bullets, corpses,
         mode, impacts, items, doors) = decode_world_state(packet.payload)
        state = (tick, players, enemies, bullets, corpses, mode, impacts, items,
                 doors)
        self.latest_state = state
        self.latest_match = decode_match_block(packet.payload)
        self.snapshots.append((time.monotonic(), state))
        if len(self.snapshots) > 3:
            del self.snapshots[:-3]
        if self._on_state_callback:
            self._on_state_callback(tick, players, enemies, bullets,
                                    corpses, mode, impacts, items, doors)

    @staticmethod
    def _angle_diff(a: float, b: float) -> float:
        """Signed shortest delta (a - b) in radians, normalized to (-pi, pi]."""
        d = (a - b) % math.tau
        if d > math.pi:
            d -= math.tau
        return d

    def _interp_angle(self, a: float, b: float, t: float) -> float:
        return a + self._angle_diff(b, a) * t

    @staticmethod
    def _moved(a: dict, b: dict) -> float:
        dx = b['x'] - a['x']
        dy = b['y'] - a['y']
        return (dx * dx + dy * dy) ** 0.5

    def _blend_bullets(self, old_bul: list[dict], new_bul: list[dict], t: float) -> list[dict]:
        if not old_bul:
            return new_bul
        result: list[dict] = []
        used: set[int] = set()
        for nb in new_bul:
            best_i = None
            best_d = 1e18
            for i, ob in enumerate(old_bul):
                if i in used:
                    continue
                if ob.get('col') != nb.get('col') or bool(ob.get('from_player')) != bool(nb.get('from_player')):
                    continue
                d = (nb['x'] - ob['x']) ** 2 + (nb['y'] - ob['y']) ** 2
                if d < best_d:
                    best_d = d
                    best_i = i
            if best_i is not None and best_d < 90000:  # matched within 300 px
                used.add(best_i)
                ob = old_bul[best_i]
                result.append({
                    'x': ob['x'] + (nb['x'] - ob['x']) * t,
                    'y': ob['y'] + (nb['y'] - ob['y']) * t,
                    'col': nb.get('col'),
                    'from_player': bool(nb.get('from_player', 0)),
                })
            else:
                result.append(nb)
        return result

    def _blend_state(self, st0, st1, t: float):
        """Interpolate remote entities between st0 (t=0) and st1 (t=1).

        ``t`` may exceed 1 during bounded extrapolation. The local player is
        always taken from the newest snapshot (no interpolation delay).
        """
        _t0, p0, e0, b0, _c0, _m0, _im0, _it0, _d0 = st0
        _t1, p1, e1, b1, c1, mode1, im1, it1, d1 = st1

        old_players = {p['id']: p for p in p0}
        players = []
        for p in p1:
            pid = p['id']
            if pid == self.player_id:
                players.append(p)
                continue
            op = old_players.get(pid)
            if op is None or self._moved(op, p) > SNAP_DISTANCE:
                players.append(p)
                continue
            players.append({
                'id': pid,
                'x': op['x'] + (p['x'] - op['x']) * t,
                'y': op['y'] + (p['y'] - op['y']) * t,
                'angle': self._interp_angle(op['angle'], p['angle'], t),
                'health': p.get('health', op.get('health', 0)),
                'state': p.get('state', op.get('state', 0)),
                'name': p.get('name', op.get('name', '')),
                'wpn': p.get('wpn', op.get('wpn', 1)),
                'ammo': p.get('ammo', op.get('ammo', {})),
            })

        old_enemies = {e['id']: e for e in e0}
        enemies = []
        for e in e1:
            oe = old_enemies.get(e['id'])
            if oe is None or self._moved(oe, e) > SNAP_DISTANCE:
                enemies.append(e)
                continue
            enemies.append({
                'id': e['id'],
                'x': oe['x'] + (e['x'] - oe['x']) * t,
                'y': oe['y'] + (e['y'] - oe['y']) * t,
                'angle': self._interp_angle(oe.get('angle', 0.0), e.get('angle', 0.0), t),
                'type': e.get('type', oe.get('type', 0)),
                'hp': e.get('hp', oe.get('hp', 0)),
                'state': e.get('state', oe.get('state', 0)),
            })

        bullets = self._blend_bullets(b0, b1, t)
        # Pickups are not interpolated: they never move, so blending them would
        # only smear a disappearance across the pickup radius. They come from
        # the newer snapshot, i.e. they vanish the instant the server takes them.
        # Doors are interpolated by the renderer from their ``progress`` and are
        # likewise taken from the newer snapshot (a door that has just started
        # moving must not lag a snapshot behind).
        return _t1, players, enemies, bullets, c1, mode1, im1, it1, d1

    def interpolated_state(self, now: float):
        """Smooth state for rendering at ``now`` (remote entities interpolated).

        Returns None while no world snapshot has arrived yet. On a missed
        snapshot the last two snapshots are extrapolated for at most
        ``MAX_EXTRAPOLATION`` seconds, after which the newest known position is
        held (no teleporting). Large moves (teleports) snap instantly.
        """
        snaps = self.snapshots
        if not snaps:
            return None
        if len(snaps) == 1:
            return snaps[0][1]

        target = now - INTERP_DELAY
        newest_t, newest_state = snaps[-1]
        if target >= newest_t:
            t0, st0 = snaps[-2]
            span = newest_t - t0
            if span > 1e-6 and target - newest_t <= MAX_EXTRAPOLATION:
                return self._blend_state(st0, newest_state, 1.0 + (target - newest_t) / span)
            return newest_state

        for i in range(len(snaps) - 1, 0, -1):
            t0, st0 = snaps[i - 1]
            t1, st1 = snaps[i]
            if t0 <= target <= t1:
                span = t1 - t0
                f = (target - t0) / span if span > 1e-6 else 1.0
                return self._blend_state(st0, st1, f)
        return snaps[0][1]

    def _handle_pong(self, packet: Packet) -> None:
        decode_ping(packet.payload)
        self.ping_rtt = (time.monotonic() - self._ping_start) * 1000

    def _handle_death(self, packet: Packet) -> None:
        from shared.protocol import decode_death_event
        target_id, killer_id = decode_death_event(packet.payload)
        logger.info(f"Player {target_id} died, killed by {killer_id}")

    def _send(self, packet: Packet) -> None:
        if self.transport:
            self.transport.sendto(packet.encode(), self.server_addr)

    def _next_seq(self) -> int:
        self._sequence = (self._sequence + 1) & 0xFFFF
        return self._sequence

    def disconnect(self) -> None:
        self.connected = False
        if self.transport:
            self.transport.close()