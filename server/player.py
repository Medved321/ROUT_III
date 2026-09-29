from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class PlayerSession:
    player_id: int
    name: str
    address: tuple[str, int]
    entity_id: int | None = None
    party_id: int | None = None
    last_input_tick: int = 0
    last_fire_seq: int = -1        # last reliably-fired click intent (fire_seq)
    last_ping: float = 0.0
    ping_rtt: float = 0.0
    sequence: int = 0
    last_received_seq: int = 0
    last_create_request: int = 0

    def next_sequence(self) -> int:
        self.sequence = (self.sequence + 1) & 0xFFFF
        return self.sequence
