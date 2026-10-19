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


# Reassembly buffers come from untrusted fragments, and a peer that never
# completes a message grows `_reasm` without bound: a u16 `count` allows ~65k
# parts per inner type, each up to a full chunk, so nothing caps memory today.
# Two bounds fix that -- how many messages may be under reassembly at once, and
# how many bytes may be buffered in total.  The byte budget is two orders of
# magnitude above the largest legitimate transfer (the ~12 MB client artifact),
# so it only ever trips on abuse.  What costs memory is the bytes actually
# received, not the declared `count`.
MAX_REASSEMBLY_ENTRIES = 16
MAX_REASSEMBLY_BYTES = 64 * 1024 * 1024
# Out-of-order messages waiting for a gap to fill. Delivery is strictly in
# order and the sender window is 16, so a legitimate receiver holds a few dozen
# at most; anything approaching this many means the peer is not delivering.
MAX_READY_BUFFER = 1024


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
        self._reasm_bytes = 0
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
        self._reasm.clear()
        self._reasm_bytes = 0
        self._ready.clear()

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

    def _drop_reasm(self, inner_type: int) -> None:
        entry = self._reasm.pop(inner_type, None)
        if entry is not None:
            self._reasm_bytes -= sum(len(c) for c in entry["parts"].values())

    def _store_fragment(self, seq: int, inner_type: int, index: int, count: int, chunk: bytes) -> None:
        base_seq = (seq - index) & 0xFFFF
        if count <= 1:
            expected = self._expected_sha.pop(inner_type, None)
            if expected is not None and not verify_sha256(chunk, expected):
                self._fail_message(inner_type, base_seq, 1)
                return
            if len(self._ready) >= MAX_READY_BUFFER:
                self.mark_dead()
                return
            self._ready[base_seq] = (inner_type, chunk, 1)
            self._flush_delivery()
            return
        # Validate the framing before buffering anything. An index at or past
        # the declared count can never complete, and the same inner type
        # re-declared with a different count would mix two messages.
        entry = self._reasm.get(inner_type)
        if entry is not None and entry["count"] != count:
            self._drop_reasm(inner_type)
            entry = None
        if index >= count:
            self._drop_reasm(inner_type)
            self._fail_message(inner_type, base_seq, count)
            return
        if entry is None:
            if len(self._reasm) >= MAX_REASSEMBLY_ENTRIES:
                self._fail_message(inner_type, base_seq, count)
                return
            entry = self._reasm[inner_type] = {"count": count, "parts": {}}
        if index not in entry["parts"]:
            if self._reasm_bytes + len(chunk) > MAX_REASSEMBLY_BYTES:
                # Buffering this fragment would exceed the budget: drop this
                # message's parts instead of growing without bound.
                self._drop_reasm(inner_type)
                self._fail_message(inner_type, base_seq, count)
                return
            entry["parts"][index] = chunk
            self._reasm_bytes += len(chunk)
        if len(entry["parts"]) == entry["count"]:
            parts = entry["parts"]
            data = b"".join(parts[i] for i in range(entry["count"]))
            self._drop_reasm(inner_type)
            expected = self._expected_sha.pop(inner_type, None)
            if expected is not None and not verify_sha256(data, expected):
                self._fail_message(inner_type, base_seq, entry["count"])
                return
            if len(self._ready) >= MAX_READY_BUFFER:
                self.mark_dead()
                return
            self._ready[base_seq] = (inner_type, data, entry["count"])
            self._flush_delivery()

    def _fail_message(self, inner_type: int, base_seq: int, count: int) -> None:
        # Drop any checksum registered for this message: it belonged to a
        # transfer that will never complete, and keeping it would misjudge the
        # next message of the same inner type.
        self._expected_sha.pop(inner_type, None)
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
