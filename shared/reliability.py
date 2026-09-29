from __future__ import annotations

import hashlib
import time
from collections import deque
from dataclasses import dataclass

from shared.protocol import (
    ACK_BITMAP_BITS,
    MAX_FRAGMENT_PAYLOAD,
    PacketType,
    decode_ack,
    decode_reliable,
    encode_ack,
    encode_reliable,
)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_sha256(data: bytes, expected_hex: str) -> bool:
    return sha256_hex(data) == (expected_hex or "").lower()


def _newer(a: int, b: int) -> bool:
    return 0 < ((a - b) & 0xFFFF) < 0x8000


@dataclass
class _Pending:
    seq: int
    data: bytes
    sent_at: float
    tries: int = 0


class ReliableEndpoint:
    """In-order, exactly-once delivery of control messages over an unreliable UDP path.

    The transport callback has the signature ``send_cb(ptype, seq, payload)``.
    Gameplay traffic never goes through this class.
    """

    def __init__(
        self,
        send_cb,
        *,
        window: int = 16,
        rto: float = 0.2,
        max_retries: int = 8,
        chunk_size: int = MAX_FRAGMENT_PAYLOAD,
        clock=time.monotonic,
    ):
        self._send = send_cb
        self.window = window
        self.rto = rto
        self.max_retries = max_retries
        self.chunk_size = chunk_size
        self._clock = clock
        self.alive = True

        self._next_seq = 0
        self._pending: dict[int, _Pending] = {}
        self._out_queue: deque = deque()

        self._received: set[int] = set()
        self._recv_order: deque = deque()
        self._highest = -1

        self._reasm: dict[int, dict] = {}
        self._expected_sha: dict[int, str] = {}
        self._ready: dict[int, tuple[int, bytes, int]] = {}
        self._next_deliver = 0

        self.inbox: deque = deque()
        self.failed_transfers: deque = deque()

    # ------------------------------------------------------------------ ack
    def _ack_state(self) -> tuple[int, int]:
        if self._highest < 0:
            return 0, 0
        ack = self._highest & 0xFFFF
        bits = 0
        for i in range(1, ACK_BITMAP_BITS + 1):
            if ((self._highest - i) & 0xFFFF) in self._received:
                bits |= 1 << (i - 1)
        return ack, bits

    # -------------------------------------------------------------- sending
    def expect_sha256(self, inner_type: int, expected_hex: str) -> None:
        self._expected_sha[int(inner_type)] = (expected_hex or "").lower()

    def send_reliable(self, inner_type: int, payload: bytes) -> None:
        data = bytes(payload)
        inner = int(inner_type)
        count = max(1, (len(data) + self.chunk_size - 1) // self.chunk_size)
        for index in range(count):
            chunk = data[index * self.chunk_size:(index + 1) * self.chunk_size]
            self._out_queue.append((inner, index, count, chunk))
        self._flush()

    def _flush(self) -> None:
        now = self._clock()
        while self._out_queue and len(self._pending) < self.window:
            inner_type, index, count, chunk = self._out_queue.popleft()
            seq = self._next_seq & 0xFFFF
            self._next_seq += 1
            ack, bits = self._ack_state()
            data = encode_reliable(ack, bits, inner_type, index, count, chunk)
            self._pending[seq] = _Pending(seq, data, now)
            self._send(PacketType.RELIABLE_DATA, seq, data)

    def tick(self, now: float | None = None) -> None:
        if not self.alive:
            return
        now = self._clock() if now is None else now
        for seq, pending in list(self._pending.items()):
            if now - pending.sent_at >= self.rto * (2 ** pending.tries):
                if pending.tries >= self.max_retries:
                    self.mark_dead()
                    return
                pending.tries += 1
                pending.sent_at = now
                self._send(PacketType.RELIABLE_DATA, seq, pending.data)
        self._flush()

    def mark_dead(self) -> None:
        self.alive = False
        self._pending.clear()
        self._out_queue.clear()

    # ------------------------------------------------------------ receiving
    def on_reliable(self, seq: int, payload: bytes) -> None:
        seq &= 0xFFFF
        ack, bits, inner_type, index, count, chunk = decode_reliable(payload)
        duplicate = seq in self._received
        if not duplicate:
            self._received.add(seq)
            self._recv_order.append(seq)
            while len(self._recv_order) > 4096:
                self._received.discard(self._recv_order.popleft())
            if self._highest < 0 or _newer(seq, self._highest):
                self._highest = seq
            self._store_fragment(seq, inner_type, index, count, chunk)
        # Always acknowledge so the sender can clear its window (also for dupes).
        self._send_ack()

    def _store_fragment(self, seq: int, inner_type: int, index: int, count: int, chunk: bytes) -> None:
        base_seq = (seq - index) & 0xFFFF
        if count <= 1:
            expected = self._expected_sha.pop(inner_type, None)
            if expected is not None and not verify_sha256(chunk, expected):
                self._fail_message(inner_type, base_seq, 1)
                return
            self._ready[base_seq] = (inner_type, chunk, 1)
            self._flush_delivery()
            return
        entry = self._reasm.setdefault(inner_type, {"count": count, "parts": {}})
        entry["parts"][index] = chunk
        if len(entry["parts"]) == entry["count"]:
            parts = entry["parts"]
            data = b"".join(parts[i] for i in range(entry["count"]))
            del self._reasm[inner_type]
            expected = self._expected_sha.pop(inner_type, None)
            if expected is not None and not verify_sha256(data, expected):
                self._fail_message(inner_type, base_seq, entry["count"])
                return
            self._ready[base_seq] = (inner_type, data, entry["count"])
            self._flush_delivery()

    def _fail_message(self, inner_type: int, base_seq: int, count: int) -> None:
        # Keep the delivery order intact: reserve the slot but emit nothing.
        self.failed_transfers.append(inner_type)
        self._ready[base_seq] = (-1, b"", count)
        self._flush_delivery()

    def _flush_delivery(self) -> None:
        # Messages are delivered strictly in sequence order: a gap blocks the
        # queue until retransmission fills it. The first reliable sequence of a
        # session is always 0, so delivery starts there.
        while self._next_deliver in self._ready:
            inner_type, data, count = self._ready.pop(self._next_deliver)
            if inner_type != -1:
                self.inbox.append((inner_type, data))
            self._next_deliver = (self._next_deliver + count) & 0xFFFF

    def on_ack(self, payload: bytes) -> None:
        ack, bits = decode_ack(payload)
        confirmed = {ack}
        for i in range(ACK_BITMAP_BITS):
            if bits & (1 << i):
                confirmed.add((ack - 1 - i) & 0xFFFF)
        for seq in confirmed:
            self._pending.pop(seq, None)
        self._flush()

    # -------------------------------------------------------------- helpers
    def pop_messages(self) -> list[tuple[int, bytes]]:
        messages = list(self.inbox)
        self.inbox.clear()
        return messages

    def pop_failed(self) -> list[int]:
        failed = list(self.failed_transfers)
        self.failed_transfers.clear()
        return failed

    def _send_ack(self) -> None:
        ack, bits = self._ack_state()
        self._send(PacketType.ACK, 0, encode_ack(ack, bits))
