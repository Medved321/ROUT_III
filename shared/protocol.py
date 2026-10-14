from __future__ import annotations

import struct
from enum import IntEnum
from dataclasses import dataclass
from typing import Optional


class PacketType(IntEnum):
    PLAYER_INPUT = 0x01
    WORLD_STATE = 0x02
    JOIN_REQUEST = 0x03
    JOIN_ACCEPT = 0x04
    PLAYER_LEAVE = 0x05
    PING = 0x06
    PONG = 0x07
    GET_LOBBY = 0x08
    LOBBY_STATE = 0x09
    CREATE_PARTY = 0x0A
    PARTY_CREATED = 0x0B
    JOIN_PARTY = 0x0C
    JOIN_FAIL = 0x0D
    LEAVE_PARTY = 0x0E
    BULLET_SPAWN = 0x10
    DAMAGE_EVENT = 0x11
    DEATH_EVENT = 0x12
    RESPAWN_EVENT = 0x13
    # Reliability / version handshake / client update channel.
    HELLO = 0x20
    HELLO_ACK = 0x21
    RELIABLE_DATA = 0x22
    ACK = 0x23
    FILE_REQUEST = 0x24
    FILE_DATA = 0x25
    FILE_COMPLETE = 0x26
    # Reliable per-click fire intent (fire_seq monotonic). Gameplay fire via
    # the unreliable PLAYER_INPUT channel stays as hold-fire auto-fire.
    FIRE = 0x27


# Action intent bits carried by the PLAYER_INPUT `flags` byte. A bitfield
# rather than a dedicated bool so the next input action (sprint, double tap)
# costs no protocol change.
INPUT_FLAG_DASH = 0x01
# "Use" action (open a nearby door).  A bit, not a message: the action is
# idempotent over the door's hold window, so the 25 Hz input channel is enough
# and the server reacts to its rising edge (design D4).
INPUT_FLAG_USE = 0x02


@dataclass
class Packet:
    ptype: PacketType
    sequence: int
    payload: bytes

    def encode(self) -> bytes:
        header = struct.pack("!BH", self.ptype, self.sequence & 0xFFFF)
        return header + self.payload

    @staticmethod
    def decode(data: bytes) -> Optional[Packet]:
        if len(data) < 3:
            return None
        ptype = data[0]
        seq = struct.unpack("!H", data[1:3])[0]
        try:
            pt = PacketType(ptype)
        except ValueError:
            return None
        return Packet(pt, seq, data[3:])


# Order is shared between server and client (index sent over the wire).
ENEMY_TYPES = (
    "soldier", "shotgunguy", "chaingunner", "imp", "dog", "pinkie", "spectre",
    "lost_soul", "cacodemon", "hell_knight", "revenant", "arachnotron",
    "mancubus", "pain_elemental", "archvile", "healer", "artillery",
    "boss_baron", "boss_cyber", "boss_spider",
)

AMMO_KEYS = ("pstl", "shl", "slg", "rck", "BFG")

# Pickup types, as one wire index. The order must stay identical to
# server/game_data.py:ITEM_DB -- protocol.py cannot import the server, so the two
# tables are kept in sync by a smoke test asserting the key sets match, not by a
# shared import. Appending is safe; reordering silently re-labels every item
# placed on an existing map.
ITEM_TYPES = (
    "med", "med_small", "heart", "amm", "amm_small",
    "armor_green", "armor_blue",
    "berserk", "haste", "invuln",
    "wpn_0", "wpn_2", "wpn_3", "wpn_4", "wpn_5",
    "key_red", "key_yellow", "key_blue",
)

# Armor colour: both behave identically, this only picks the HUD colour.
ARMOR_TYPES = ("green", "blue")
# Active timed buff. Index 0 is "none", so an empty byte means no buff and the
# names travel verbatim -- the HUD already matches on these exact spellings.
POWERUP_TYPES = ("", "BERSERK", "HASTE", "INVULN")

MODES = {"pve": 0, "pvp": 1}
MODE_NAMES = {v: k for k, v in MODES.items()}

# Impact event types (tracer markers for wall/enemy hits). Wire order fixed.
IMPACT_TYPES = ("wall", "enemy")

# Fragment payload budget that keeps a RELIABLE_DATA datagram under a safe MTU.
MAX_FRAGMENT_PAYLOAD = 1000
# Window of sequence numbers covered by the cumulative/selective ACK bitmap.
ACK_BITMAP_BITS = 32


def encode_hello(protocol_version: int, client_version: str) -> bytes:
    return struct.pack("!B", protocol_version & 0xFF) + _encode_string(client_version, 64)


def decode_hello(data: bytes) -> tuple[int, str]:
    protocol_version = data[0]
    client_version, _ = _decode_string(data, 1)
    return protocol_version, client_version


def encode_hello_ack(
    server_protocol: int,
    accepted: bool,
    latest_client_version: str = "",
    has_update: bool = False,
    update_version: str = "",
    update_size: int = 0,
    update_sha256: str = "",
) -> bytes:
    out = bytearray(struct.pack("!BB", server_protocol & 0xFF, 1 if accepted else 0))
    out += _encode_string(latest_client_version, 64)
    if has_update:
        out += struct.pack("!B", 1)
        out += _encode_string(update_version, 64)
        out += struct.pack("!I", update_size & 0xFFFFFFFF)
        out += _encode_string(update_sha256, 64)
    else:
        out += struct.pack("!B", 0)
    return bytes(out)


def decode_hello_ack(data: bytes) -> dict:
    server_protocol, accepted = struct.unpack("!BB", data[:2])
    latest_client_version, offset = _decode_string(data, 2)
    has_update = bool(data[offset])
    offset += 1
    result = {
        "server_protocol": server_protocol,
        "accepted": bool(accepted),
        "latest_client_version": latest_client_version,
        "has_update": has_update,
        "update_version": "",
        "update_size": 0,
        "update_sha256": "",
    }
    if has_update:
        result["update_version"], offset = _decode_string(data, offset)
        result["update_size"] = struct.unpack("!I", data[offset:offset + 4])[0]
        offset += 4
        result["update_sha256"], offset = _decode_string(data, offset)
    return result


def encode_reliable(
    ack: int,
    ack_bits: int,
    inner_type: int,
    frag_index: int,
    frag_count: int,
    payload: bytes = b"",
) -> bytes:
    header = struct.pack(
        "!HIBHH", ack & 0xFFFF, ack_bits & 0xFFFFFFFF, inner_type & 0xFF,
        frag_index & 0xFFFF, frag_count & 0xFFFF,
    )
    return header + payload


def decode_reliable(data: bytes) -> tuple[int, int, int, int, int, bytes]:
    ack, ack_bits, inner_type, frag_index, frag_count = struct.unpack("!HIBHH", data[:11])
    return ack, ack_bits, inner_type, frag_index, frag_count, data[11:]


def encode_ack(ack: int, ack_bits: int) -> bytes:
    return struct.pack("!HI", ack & 0xFFFF, ack_bits & 0xFFFFFFFF)


def decode_ack(data: bytes) -> tuple[int, int]:
    return struct.unpack("!HI", data[:6])


def encode_file_request() -> bytes:
    return b""


def encode_file_complete(ok: bool = True) -> bytes:
    return struct.pack("!B", 1 if ok else 0)


def decode_file_complete(data: bytes) -> bool:
    return bool(data[0]) if data else False


def encode_player_input(
    move_x: int, move_y: int, angle: float, shoot: bool, weapon_id: int = 1,
    flags: int = 0,
) -> bytes:
    return struct.pack("!bbfBBB", move_x, move_y, angle, int(shoot),
                       weapon_id & 0xFF, flags & 0xFF)


def decode_player_input(data: bytes) -> tuple[int, int, float, bool, int, int]:
    mx, my, angle, sh, wid, flags = struct.unpack("!bbfBBB", data)
    return mx, my, angle, bool(sh), wid, flags


def encode_fire(fire_seq: int, weapon_id: int) -> bytes:
    """Reliable per-click fire intent. ``fire_seq`` is monotonic per client."""
    return struct.pack("!IB", fire_seq & 0xFFFFFFFF, weapon_id & 0xFF)


def decode_fire(data: bytes) -> tuple[int, int]:
    return struct.unpack("!IB", data)


def _u8(value) -> int:
    """Fit a byte-sized field instead of letting struct raise on it.

    The byte range is a wire constraint, not a game rule: the rules live
    wherever the value is produced (AMMO_MAX for a reserve, PLAYER_MAX_HP for
    health), and a stray one here used to take the whole broadcast -- with it
    the game loop -- down with ``struct.error``.  Clamping keeps the server
    running and the snapshot decodable; masking would not (9999 & 0xFF is 15,
    a number nobody ever had).  Values below 0 are clamped too: struct rejects
    those with the same error, and negative ammo is not a thing.
    """
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 0
    if v < 0:
        return 0
    return 255 if v > 255 else v


def encode_world_state(
    tick: int,
    players: list[dict],
    enemies: list[dict] | None = None,
    bullets: list[dict] | None = None,
    corpses: list[dict] | None = None,
    mode: str = 'pve',
    impacts: list[dict] | None = None,
    items: list[dict] | None = None,
    doors: list[dict] | None = None,
) -> bytes:
    mode_byte = MODES.get(mode, 0)
    header = struct.pack("!HBB", tick, mode_byte, len(players))
    body = b""
    for p in players:
        ammo = p.get("ammo", {})
        # fire_seq: last ACCEPTED fire intent (monotonic), fire_ready: ticks
        # left before this player's weapon can fire again, fire_reason: why the
        # last processed intent was not accepted (0 = accepted).  Together they
        # are the server's decision about the fire intent, so a refused shot is
        # visible instead of being dropped in silence (fire-intent-single-path).
        # armor/powerup are per-player state with no other source in the
        # snapshot; armor_type/powerup carry wire indices so an unknown value
        # from a future build degrades to "no armor"/"no buff" instead of
        # crashing an old client on an index error.
        armor = p.get("armor") or {}
        powerup = p.get("powerup") or ""
        armor_type = armor.get("type", "green")
        armor_idx = ARMOR_TYPES.index(armor_type) if armor_type in ARMOR_TYPES else 0
        pu_idx = POWERUP_TYPES.index(powerup) if powerup in POWERUP_TYPES else 0
        body += struct.pack(
            # player_id is uint16 on purpose: it comes from a monotonic join
            # counter with no 255 ceiling, and as a byte it was the second way
            # to crash the server with the very same struct.error (the first
            # was an uncapped ammo stack).  An id is an identity, so it is
            # widened rather than clamped -- a clamped id would collide with
            # another live player instead of failing loudly.
            # keys: the player's key bitmask (R/Y/B), use_reason: advisory code
            # for the last "use" attempt (0 = none/ok).  Both are per-player
            # state with no other source in the snapshot.
            "!HfffBBBBBBBBIBBBBBBB",
            p["id"] & 0xFFFF,
            p["x"], p["y"], p["angle"],
            _u8(p["health"]),
            _u8(p["state"]),
            _u8(p.get("wpn", 1)),
            _u8(ammo.get("pstl", 0)), _u8(ammo.get("shl", 0)),
            _u8(ammo.get("slg", 0)), _u8(ammo.get("rck", 0)),
            _u8(ammo.get("BFG", 0)),
            p.get("fire_seq", 0) & 0xFFFFFFFF,
            p.get("fire_ready", 0) & 0xFF,
            p.get("fire_reason", 0) & 0xFF,
            int(armor.get("value", 0)) & 0xFF,
            armor_idx,
            pu_idx,
            int(p.get("keys", 0)) & 0xFF,
            int(p.get("use_reason", 0)) & 0xFF,
        )
        body += _encode_string(p.get("name", ""))
    # Enemies
    enemies = enemies or []
    body += struct.pack("!H", len(enemies))
    for e in enemies:
        etype = e.get("type", e.get("t", 0))
        if isinstance(etype, str):
            etype = ENEMY_TYPES.index(etype) if etype in ENEMY_TYPES else 0
        body += struct.pack(
            "!HfffBHB",
            e["id"] & 0xFFFF,
            e["x"], e["y"], e.get("angle", 0.0),
            etype & 0xFF,
            max(0, int(e.get("hp", 0))) & 0xFFFF,
            e.get("state", 0) & 0xFF,
        )
    # Bullets / player projectiles (x, y, r, g, b, from_player)
    bullets = bullets or []
    body += struct.pack("!H", len(bullets))
    for b in bullets:
        body += struct.pack(
            "!ffBBBB", b["x"], b["y"],
            *(b.get("col", (200, 200, 200))[:3]),
            1 if b.get("from_player") else 0,
        )
    # Corpses
    corpses = corpses or []
    body += struct.pack("!H", len(corpses))
    for c in corpses:
        ctype = c.get("type", c.get("t", 0))
        if isinstance(ctype, str):
            ctype = ENEMY_TYPES.index(ctype) if ctype in ENEMY_TYPES else 0
        body += struct.pack("!ffB", c["x"], c["y"], ctype & 0xFF)
    # Impact events (wall/enemy hit markers for client-side tracers); each
    # shot carries a monotonic server id so clients dedupe by shot, not by x,y.
    impacts = impacts or []
    body += struct.pack("!H", len(impacts))
    for im in impacts:
        itype = im.get("type", "wall")
        if isinstance(itype, str):
            itype = IMPACT_TYPES.index(itype) if itype in IMPACT_TYPES else 0
        body += struct.pack("!ffBI", im["x"], im["y"], itype & 0xFF,
                            im.get("id", 0) & 0xFFFFFFFF)
    # Pickups (id, x, y, type). Only items currently on the floor are sent: a
    # taken item is absent rather than flagged, so a client cannot draw a ghost
    # of something the server has already given away. The id is stable across the
    # item's whole life including its respawn, which is what lets the client
    # reconcile instead of guessing from position.
    items = items or []
    body += struct.pack("!H", len(items))
    for it in items:
        itype = it.get("type", it.get("t", ""))
        if isinstance(itype, str):
            itype = ITEM_TYPES.index(itype) if itype in ITEM_TYPES else 0
        body += struct.pack("!HffB",
                            it.get("id", 0) & 0xFFFF,
                            it["x"], it["y"], itype & 0xFF)
    # Doors: sparse block of NON-closed doors only (linedef_id u16, progress u8
    # where 0 = shut ... 255 = fully open).  A silent door is therefore a closed
    # one, which is exactly the map default, so an idle level costs nothing
    # (design D5).
    doors = doors or []
    body += struct.pack("!H", len(doors))
    for d in doors:
        body += struct.pack("!HB",
                            d.get("li", d.get("id", 0)) & 0xFFFF,
                            _u8(d.get("progress", 0)))
    return header + body


def decode_world_state(data: bytes) -> tuple[int, list[dict], list[dict], list[dict], list[dict], str, list[dict], list[dict], list[dict]]:
    tick, mode_byte, count = struct.unpack("!HBB", data[:4])
    players = []
    offset = 4
    for _ in range(count):
        pid, x, y, angle, hp, state, wpn, ap, ash, asg, ark, abfg, fire_seq, fire_ready, fire_reason, armor_value, armor_idx, pu_idx, keys, use_reason = struct.unpack(
            "!HfffBBBBBBBBIBBBBBBB", data[offset:offset + 33]
        )
        offset += 33
        name, offset = _decode_string(data, offset)
        players.append({
            "id": pid, "x": x, "y": y, "angle": angle,
            "health": hp, "state": state, "name": name, "wpn": wpn,
            "ammo": {"pstl": ap, "shl": ash, "slg": asg, "rck": ark, "BFG": abfg},
            "fire_seq": fire_seq, "fire_ready": fire_ready,
            "fire_reason": fire_reason,
            # Armor is absent (None) rather than a zero-valued dict: the HUD asks
            # `if arm:` before reading its keys, so a present-but-empty armor
            # would draw an "ARMOR 0" readout with no armor to justify it.
            "armor": ({"type": ARMOR_TYPES[armor_idx], "value": armor_value}
                      if 0 <= armor_idx < len(ARMOR_TYPES) and armor_value > 0
                      else None),
            "powerup": (POWERUP_TYPES[pu_idx]
                        if 0 <= pu_idx < len(POWERUP_TYPES) else ""),
            "keys": keys,
            "use_reason": use_reason,
        })
    # Enemies
    e_count = struct.unpack("!H", data[offset:offset + 2])[0]
    offset += 2
    enemies = []
    for _ in range(e_count):
        eid, ex, ey, ea, etype, ehp, est = struct.unpack("!HfffBHB", data[offset:offset + 18])
        offset += 18
        enemies.append({
            "id": eid, "x": ex, "y": ey, "angle": ea, "type": etype,
            "hp": ehp, "state": est,
        })
    # Bullets (x, y, r, g, b, from_player)
    b_count = struct.unpack("!H", data[offset:offset + 2])[0]
    offset += 2
    bullets = []
    for _ in range(b_count):
        bx, by, r, g, b, from_player = struct.unpack("!ffBBBB", data[offset:offset + 12])
        offset += 12
        bullets.append({"x": bx, "y": by, "col": (r, g, b), "from_player": bool(from_player)})
    # Corpses
    c_count = struct.unpack("!H", data[offset:offset + 2])[0]
    offset += 2
    corpses = []
    for _ in range(c_count):
        cx, cy, ctype = struct.unpack("!ffB", data[offset:offset + 9])
        offset += 9
        corpses.append({"x": cx, "y": cy, "type": ctype})
    # Impacts (x, y, type, id)
    im_count = struct.unpack("!H", data[offset:offset + 2])[0]
    offset += 2
    impacts = []
    for _ in range(im_count):
        ix, iy, itype, iid = struct.unpack("!ffBI", data[offset:offset + 13])
        offset += 13
        impacts.append({
            "x": ix, "y": iy,
            "type": IMPACT_TYPES[itype] if 0 <= itype < len(IMPACT_TYPES) else "wall",
            "id": iid,
        })
    # Pickups (id, x, y, type)
    it_count = struct.unpack("!H", data[offset:offset + 2])[0]
    offset += 2
    items = []
    for _ in range(it_count):
        iid, ix, iy, itype = struct.unpack("!HffB", data[offset:offset + 11])
        offset += 11
        items.append({
            "id": iid, "x": ix, "y": iy,
            "type": ITEM_TYPES[itype] if 0 <= itype < len(ITEM_TYPES) else "med",
        })
    # Doors (linedef_id, progress).  Absent door = closed (map default).
    d_count = struct.unpack("!H", data[offset:offset + 2])[0]
    offset += 2
    doors = []
    for _ in range(d_count):
        dli, dprog = struct.unpack("!HB", data[offset:offset + 3])
        offset += 3
        doors.append({"li": dli, "progress": dprog})
    return (tick, players, enemies, bullets, corpses,
            MODE_NAMES.get(mode_byte, 'pve'), impacts, items, doors)


def encode_join_request(name: str) -> bytes:
    name_bytes = name.encode("utf-8")[:32]
    return struct.pack("!B", len(name_bytes)) + name_bytes


def decode_join_request(data: bytes) -> str:
    length = data[0]
    return data[1:1 + length].decode("utf-8")


def encode_join_accept(
    player_id: int,
    map_width: int = 0,
    map_height: int = 0,
    map_data: bytes = b"",
    map_format: int = 0,
) -> bytes:
    has_map = 1 if map_data else 0
    header = struct.pack(
        "!BHHBB", player_id, map_width, map_height, has_map, map_format & 0xFF
    )
    return header + map_data


def decode_join_accept(data: bytes) -> tuple[int, int, int, bool, bytes, int]:
    pid, mw, mh, has_map, map_format = struct.unpack("!BHHBB", data[:7])
    map_data = data[7:] if has_map else b""
    return pid, mw, mh, bool(has_map), map_data, map_format


def encode_ping(timestamp: float) -> bytes:
    return struct.pack("!d", timestamp)


def decode_ping(data: bytes) -> float:
    return struct.unpack("!d", data)[0]


def encode_bullet_spawn(bullet_id: int, x: float, y: float, angle: float, owner: int) -> bytes:
    return struct.pack("!BfffB", bullet_id, x, y, angle, owner)


def decode_bullet_spawn(data: bytes) -> tuple[int, float, float, float, int]:
    bid, x, y, angle, owner = struct.unpack("!BfffB", data)
    return bid, x, y, angle, owner


def encode_damage_event(target_id: int, damage: int, source_id: int) -> bytes:
    return struct.pack("!BBB", target_id, damage, source_id)


def decode_damage_event(data: bytes) -> tuple[int, int, int]:
    return struct.unpack("!BBB", data)


def encode_death_event(player_id: int, killer_id: int) -> bytes:
    return struct.pack("!BB", player_id, killer_id)


def decode_death_event(data: bytes) -> tuple[int, int]:
    return struct.unpack("!BB", data)


def encode_respawn_event(player_id: int, x: float, y: float, angle: float) -> bytes:
    return struct.pack("!Bfff", player_id, x, y, angle)


def decode_respawn_event(data: bytes) -> tuple[int, float, float, float]:
    return struct.unpack("!Bfff", data)


def _encode_string(message: str, max_len: int = 32) -> bytes:
    data = message.encode("utf-8")[:max_len]
    return struct.pack("!B", len(data)) + data


def _decode_string(data: bytes, offset: int = 0) -> tuple[str, int]:
    length = data[offset]
    return data[offset + 1:offset + 1 + length].decode("utf-8"), offset + 1 + length


def encode_lobby_state(parties: list[dict]) -> bytes:
    out = bytearray(struct.pack("!B", len(parties)))
    for party in parties:
        out += struct.pack("!H", party["id"])
        name_bytes = party["name"].encode("utf-8")[:32]
        out += struct.pack("!B", len(name_bytes)) + name_bytes
        mode_byte = MODES.get(party.get("mode", "pve"), 0)
        out += struct.pack("!B", mode_byte)
        out += struct.pack("!B", len(party["players"]))
        for player in party["players"]:
            player_bytes = player["name"].encode("utf-8")[:32]
            out += struct.pack("!BB", player["id"], len(player_bytes)) + player_bytes
    return bytes(out)


def decode_lobby_state(data: bytes) -> list[dict]:
    count = data[0]
    offset = 1
    parties = []
    for _ in range(count):
        party_id = struct.unpack("!H", data[offset:offset + 2])[0]
        offset += 2
        name, offset = _decode_string(data, offset)
        mode = MODE_NAMES.get(data[offset], "pve")
        offset += 1
        player_count = data[offset]
        offset += 1
        players = []
        for _ in range(player_count):
            pid = data[offset]
            offset += 1
            pname, offset = _decode_string(data, offset)
            players.append({"id": pid, "name": pname})
        parties.append({"id": party_id, "name": name, "mode": mode, "players": players})
    return parties


def encode_create_party(
    name: str,
    mode: str = 'pve',
    map_width: int = 0,
    map_height: int = 0,
    map_data: bytes = b"",
    request_id: int = 0,
    map_format: int = 0,
) -> bytes:
    mode_byte = MODES.get(mode, 0)
    has_map = 1 if map_data else 0
    header = struct.pack(
        "!IHHBB", request_id & 0xFFFFFFFF, map_width, map_height, has_map,
        map_format & 0xFF,
    )
    return struct.pack("!B", mode_byte) + _encode_string(name) + header + (map_data if has_map else b"")


def decode_create_party(data: bytes) -> tuple[str, str, int, int, bytes]:
    name, mode, map_width, map_height, map_data, _, _ = decode_create_party_ex(data)
    return name, mode, map_width, map_height, map_data


def decode_create_party_ex(data: bytes) -> tuple[str, str, int, int, bytes, int, int]:
    mode_byte = data[0]
    name, offset = _decode_string(data, 1)
    request_id = 0
    map_width = map_height = 0
    map_data = b""
    map_format = 0
    # Trailing block (request_id + map header + map) added after the name.
    if len(data) >= offset + 10:
        request_id, map_width, map_height, has_map, map_format = struct.unpack(
            "!IHHBB", data[offset:offset + 10]
        )
        offset += 10
        if has_map:
            map_data = data[offset:]
    elif len(data) >= offset + 9:
        # Pre-v5 layout without map_format (protocol bumped; tolerated).
        request_id, map_width, map_height, has_map = struct.unpack(
            "!IHHB", data[offset:offset + 9]
        )
        offset += 9
        if has_map:
            map_data = data[offset:]
    return (name, MODE_NAMES.get(mode_byte, 'pve'), map_width, map_height,
            map_data, request_id, map_format)


def encode_join_party(party_id: int) -> bytes:
    return struct.pack("!H", party_id)


def decode_join_party(data: bytes) -> int:
    return struct.unpack("!H", data)[0]


def encode_party_created(party_id: int) -> bytes:
    return struct.pack("!H", party_id)


def decode_party_created(data: bytes) -> int:
    return struct.unpack("!H", data)[0]


def encode_join_fail(reason: str) -> bytes:
    return _encode_string(reason)


def decode_join_fail(data: bytes) -> str:
    return _decode_string(data)[0]
