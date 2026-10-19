from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from server.weapons import FireResult


@dataclass
class PlayerSession:
    player_id: int
    name: str
    address: tuple[str, int]
    entity_id: int | None = None
    party_id: int | None = None
    last_input_tick: int = 0
    last_fire_seq: int = -1        # last ACCEPTED fire intent (fire_seq)
    # Watermark of every intent already DECIDED, accepted OR refused. It is a
    # different fact from `last_fire_seq` and the idempotence guard needs
    # exactly this one: a refusal leaves `last_fire_seq` where it was, so a
    # redelivered refused intent still looked "not newer" and got decided a
    # second time — an extra shot the client never asked for, published as an
    # acceptance over whatever decision followed it. `last_fire_seq` stays
    # "accepted" because that is the number the client addresses decisions by
    # (a jump of fire_seq reads as a shot happened).
    last_fire_decided: int = -1
    # Server decision about the newest fire intent of this session; published
    # to the client in the WORLD_STATE player record so a refused shot is
    # visible instead of being dropped in silence.
    last_fire_result: FireResult | None = None
    # Last time the session was heard from. It starts at creation, not at 0: a
    # session that never sends a PING must still time out. With a 0 default the
    # stale filter's `last_ping > 0` guard skipped such a session forever, so a
    # client that completed the handshake and joined but never pinged lived on
    # the server for the whole uptime.
    last_ping: float = field(default_factory=time.monotonic)
    ping_rtt: float = 0.0
    sequence: int = 0
    last_received_seq: int = 0
    last_create_request: int = 0

    def next_sequence(self) -> int:
        self.sequence = (self.sequence + 1) & 0xFFFF
        return self.sequence
