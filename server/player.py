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
    # Server decision about the newest fire intent of this session; published
    # to the client in the WORLD_STATE player record so a refused shot is
    # visible instead of being dropped in silence.
    last_fire_result: FireResult | None = None
    last_ping: float = 0.0
    ping_rtt: float = 0.0
    sequence: int = 0
    last_received_seq: int = 0
    last_create_request: int = 0

    def next_sequence(self) -> int:
        self.sequence = (self.sequence + 1) & 0xFFFF
        return self.sequence
