"""Reliability smoke tests: lossy control channel + server-driven client update."""
import asyncio
import random
import tempfile
import zipapp
from pathlib import Path

from shared.asyncio_util import use_udp_safe_event_loop

use_udp_safe_event_loop()

from shared.protocol import PacketType
from shared.reliability import ReliableEndpoint, sha256_hex
from shared.releases import artifact_path, latest_version, write_latest_pointer
from shared.version import CLIENT_VERSION, PROTOCOL_VERSION
from server.server import GameServer
from client.network_client import NetworkClient
from client.updater import (
    UpdateError, apply_update, restart, rollback, validate_offer)


class LossyChannel:
    def __init__(self, seed, loss=0.3, dup=0.1, jitter=0.25):
        self.rng = random.Random(seed)
        self.queue = []
        self.now = 0.0
        self.loss = loss
        self.dup = dup
        self.jitter = jitter

    def wire(self, dst):
        def send(ptype, seq, payload):
            if self.rng.random() < self.loss:
                return
            delay = 0.01 + self.rng.random() * self.jitter
            self.queue.append((self.now + delay, dst, ptype, seq, payload))
            if self.rng.random() < self.dup:
                self.queue.append((self.now + delay + 0.003, dst, ptype, seq, payload))
        return send

    def pump(self, endpoints):
        due = [e for e in self.queue if e[0] <= self.now]
        self.queue = [e for e in self.queue if e[0] > self.now]
        due.sort(key=lambda e: e[0])
        for _, dst, ptype, seq, payload in due:
            endpoint = endpoints[dst]
            if ptype == PacketType.RELIABLE_DATA:
                endpoint.on_reliable(seq, payload)
            elif ptype == PacketType.ACK:
                endpoint.on_ack(payload)
        for endpoint in endpoints.values():
            endpoint.tick(self.now)


def test_exactly_once_over_loss():
    rng = random.Random(7)
    messages = [(0x03, bytes(rng.randrange(256) for _ in range(40)) + i.to_bytes(2, 'big'))
                for i in range(6)]
    messages.append((0x25, bytes(rng.randrange(256) for _ in range(23000))))
    for seed in range(8):
        channel = LossyChannel(seed)
        sender = ReliableEndpoint(channel.wire('recv'), clock=lambda: channel.now)
        receiver = ReliableEndpoint(channel.wire('send'), clock=lambda: channel.now)
        endpoints = {'send': sender, 'recv': receiver}
        for inner, payload in messages:
            sender.send_reliable(inner, payload)
        delivered = []
        for i in range(5000):
            channel.now = i * 0.02
            channel.pump(endpoints)
            delivered.extend(receiver.pop_messages())
            if len(delivered) == len(messages):
                break
        assert len(delivered) == len(messages), f"seed {seed}: {len(delivered)}"
        for i, (inner, payload) in enumerate(messages):
            assert delivered[i] == (inner, payload), f"seed {seed}: order at {i}"
    print("exactly-once over loss/dup/reorder OK")


def test_partial_and_corrupt_file():
    channel = LossyChannel(0, loss=0.0, dup=0.0, jitter=0.0)
    receiver = ReliableEndpoint(channel.wire('send'), clock=lambda: channel.now)
    from shared.protocol import encode_reliable
    part0 = encode_reliable(0, 0, 0x25, 0, 2, b'hello')
    receiver.on_reliable(0, part0)
    assert receiver.pop_messages() == [], "incomplete file was delivered"

    channel2 = LossyChannel(0, loss=0.0, dup=0.0, jitter=0.0)
    sender = ReliableEndpoint(channel2.wire('recv'), clock=lambda: channel2.now)
    receiver2 = ReliableEndpoint(channel2.wire('send'), clock=lambda: channel2.now)
    endpoints = {'send': sender, 'recv': receiver2}
    receiver2.expect_sha256(0x25, sha256_hex(b'expected'))
    sender.send_reliable(0x25, b'actual-payload')
    channel2.now = 1.0
    channel2.pump(endpoints)
    assert receiver2.pop_messages() == [], "corrupted file was delivered"
    assert receiver2.pop_failed() == [0x25], "corrupted file not flagged"
    print("partial/corrupt file rejection OK")


async def test_server_serves_update():
    with tempfile.TemporaryDirectory() as tmp:
        artifact = b'FAKE-CLIENT-ARTIFACT' * 100
        path = artifact_path(tmp, "2.0.0")
        path.write_bytes(artifact)
        write_latest_pointer(tmp, "2.0.0")

        server = GameServer(host="127.0.0.1", port=7795, releases_dir=tmp)
        task = asyncio.ensure_future(server.start())
        await asyncio.sleep(0.3)

        client = NetworkClient("127.0.0.1", 7795)
        ok = await client.connect("Updater")
        assert ok, "connect failed"
        assert client.update_offer is not None, "no update offer"
        assert client.update_offer.version == "2.0.0", client.update_offer.version
        assert client.update_offer.size == len(artifact)

        client.request_update()
        assert await client._wait_for(client._file_event, 5.0), "file not received"
        assert client.update_data == artifact, "file bytes differ"
        assert sha256_hex(client.update_data) == client.update_offer.sha256

        # Applying stores a new version file and moves the pointer.
        dest = Path(tmp) / "client-releases"
        applied = apply_update(dest, client.update_offer.version,
                               client.update_data, client.update_offer.sha256)
        assert applied.read_bytes() == artifact
        assert latest_version(dest) == "2.0.0"

        # A bad hash must never be applied.
        try:
            apply_update(dest, "2.0.1", artifact, "deadbeef")
            raise AssertionError("bad hash was applied")
        except UpdateError:
            pass

        server.stop()
        try:
            await task
        except asyncio.CancelledError:
            pass
    print("server update distribution OK")


async def test_idempotent_create():
    server = GameServer(host="127.0.0.1", port=7797)
    task = asyncio.ensure_future(server.start())
    await asyncio.sleep(0.3)

    client = NetworkClient("127.0.0.1", 7797)
    assert await client.connect("Repeater")
    client.send_create_party("Once", "pve")
    await asyncio.sleep(0.4)
    assert client.state == "game", client.state
    client.send_create_party("Once", "pve")
    client.send_create_party("Once", "pve")
    await asyncio.sleep(0.4)

    parties = [p for p in client.party_list if client.player_id in {pl['id'] for pl in p['players']}]
    assert len(parties) == 1, f"expected 1 party, got {len(parties)}"
    server_parties = len(server.lobby.parties)
    assert server_parties == 1, f"server has {server_parties} parties"

    server.stop()
    try:
        await task
    except asyncio.CancelledError:
        pass
    print("idempotent create OK")


def test_rollback():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "rout-client-1.0.0.pyz").write_bytes(b"v1")
        (d / "rout-client-1.1.0.pyz").write_bytes(b"v2")
        write_latest_pointer(d, "1.1.0")
        assert latest_version(d) == "1.1.0"
        assert rollback(d) == "1.0.0"
        assert latest_version(d) == "1.0.0"
    print("rollback OK")


def test_restart_launches_artifact():
    with tempfile.TemporaryDirectory() as tmp:
        build_dir = Path(tmp) / "build"
        build_dir.mkdir()
        marker = Path(tmp) / "marker.txt"
        (build_dir / "__main__.py").write_text(
            "from pathlib import Path\n"
            f"Path(r'{marker}').write_text('launched')\n",
            encoding="utf-8",
        )
        artifact = Path(tmp) / "test-client.pyz"
        zipapp.create_archive(str(build_dir), str(artifact), interpreter=None)
        proc = restart(artifact)
        proc.wait(timeout=15)
        assert marker.read_text(encoding="utf-8") == "launched", "artifact not restarted"
    print("restart OK")


async def test_incompatible_protocol_rejected():
    server = GameServer(host="127.0.0.1", port=7796)
    task = asyncio.ensure_future(server.start())
    await asyncio.sleep(0.3)

    import client.network_client as nc
    original = nc.PROTOCOL_VERSION
    # Old client (previous protocol revision) must be rejected.
    nc.PROTOCOL_VERSION = PROTOCOL_VERSION - 1
    try:
        client = NetworkClient("127.0.0.1", 7796)
        ok = await client.connect("Old")
        assert not ok, "old protocol version was accepted"
        assert client.last_error, "no rejection reason"
        assert not client.protocol_ok
    finally:
        nc.PROTOCOL_VERSION = original
    # Too-new client (future revision) is equally rejected.
    nc.PROTOCOL_VERSION = PROTOCOL_VERSION + 1
    try:
        client = NetworkClient("127.0.0.1", 7796)
        ok = await client.connect("Future")
        assert not ok, "incompatible protocol was accepted"
        assert client.last_error, "no rejection reason"
        assert not client.protocol_ok
    finally:
        nc.PROTOCOL_VERSION = original

    server.stop()
    try:
        await task
    except asyncio.CancelledError:
        pass
    print("incompatible protocol rejected OK")


def test_reassembly_is_bounded():
    from shared.protocol import encode_reliable
    from shared import reliability as rel
    channel = LossyChannel(0, loss=0.0, dup=0.0, jitter=0.0)
    receiver = ReliableEndpoint(channel.wire('send'), clock=lambda: channel.now)
    # Many never-completed fragmented messages, one per inner type: without the
    # entry cap, `_reasm` would grow with the number of streams.
    seq = 0
    for inner in range(1, 200):
        receiver.on_reliable(seq, encode_reliable(0, 0, inner, 0, 2, b'x' * 100))
        seq = (seq + 2) & 0xFFFF
    assert len(receiver._reasm) <= rel.MAX_REASSEMBLY_ENTRIES, len(receiver._reasm)

    # An index at or past the declared count can never complete: rejected with
    # no parts buffered.
    fresh = ReliableEndpoint(channel.wire('send'), clock=lambda: channel.now)
    fresh.on_reliable(0, encode_reliable(0, 0, 5, 7, 3, b'x' * 100))
    assert 5 not in fresh._reasm, fresh._reasm
    assert fresh.pop_failed() == [5], fresh.failed_transfers

    # The byte budget is a hard ceiling: buffering past it drops the message
    # rather than growing. Lower it so the test stays cheap.
    original = rel.MAX_REASSEMBLY_BYTES
    rel.MAX_REASSEMBLY_BYTES = 1000
    try:
        capped = ReliableEndpoint(channel.wire('send'), clock=lambda: channel.now)
        capped.on_reliable(0, encode_reliable(0, 0, 6, 0, 4, b'a' * 600))
        capped.on_reliable(1, encode_reliable(0, 0, 6, 1, 4, b'b' * 600))
        assert capped._reasm_bytes <= rel.MAX_REASSEMBLY_BYTES, capped._reasm_bytes
    finally:
        rel.MAX_REASSEMBLY_BYTES = original

    # A normal completion nets the byte counter back to zero.
    done = ReliableEndpoint(channel.wire('send'), clock=lambda: channel.now)
    done.on_reliable(0, encode_reliable(0, 0, 7, 0, 2, b'hello'))
    done.on_reliable(1, encode_reliable(0, 0, 7, 1, 2, b' world'))
    assert done._reasm_bytes == 0, done._reasm_bytes
    assert done.pop_messages() == [(7, b'hello world')]
    print("reassembly bounds OK")


def test_file_request_is_gated():
    """FILE_REQUEST is an inner reliable message any handshake-completed peer can
    send. It must be served only to a client that was actually told an update is
    pending, and only once per cooldown — otherwise the server is a free,
    repeatable, multi-megabyte file host."""
    with tempfile.TemporaryDirectory() as tmp:
        artifact = b'X' * 1000
        artifact_path(tmp, "9.9.9").write_bytes(artifact)
        write_latest_pointer(tmp, "9.9.9")
        server = GameServer(host="127.0.0.1", port=0, releases_dir=tmp)
        sent = []
        server.network.send_file = lambda addr, data: sent.append((addr, len(data)))
        addr = ("10.0.0.1", 1234)

        # 1. Never said HELLO: refused.
        server._on_file_request(addr)
        assert sent == [], sent
        # 2. Handshake ok but already on the latest version: refused.
        server.network.client_info[addr] = {"protocol_ok": True, "needs_update": False}
        server._on_file_request(addr)
        assert sent == [], sent
        # 3. Update pending: served exactly once.
        server.network.client_info[addr] = {"protocol_ok": True, "needs_update": True}
        server._on_file_request(addr)
        assert len(sent) == 1 and sent[0][1] == len(artifact), sent
        # 4. Repeat inside the cooldown: throttled.
        server._on_file_request(addr)
        assert len(sent) == 1, sent
    print("file request gated + throttled OK")


def test_client_drops_foreign_datagrams():
    """The client socket is unconnected, so anyone who guesses the ephemeral
    port can send datagrams. Only the resolved server address may be processed."""
    client = NetworkClient("127.0.0.1", 7777)
    client.server_addr = ("127.0.0.1", 7777)
    seen = []
    client._dispatch_datagram = lambda data: seen.append(data)
    client.datagram_received(b"\x00", ("9.9.9.9", 1))
    assert seen == [], seen
    client.datagram_received(b"\x00", ("127.0.0.1", 7777))
    assert seen == [b"\x00"], seen
    print("client drops foreign datagrams OK")


def test_updater_trust_policy():
    """The offer travels the same untrusted channel, so the client re-checks it:
    a downgrade or malformed digest is refused before any bytes are fetched."""
    # Downgrade to the running version is not newer.
    for version, digest in [(CLIENT_VERSION, "a" * 64), ("99.0.0", "deadbeef"),
                            ("99.0.0", ""), ("99.0.0", "z" * 64)]:
        try:
            validate_offer(version, 10, digest)
            raise AssertionError(f"offer accepted: {version!r}/{digest!r}")
        except UpdateError:
            pass
    validate_offer("99.0.0", 10, "a" * 64)
    print("update offer policy OK")


def test_updater_pin():
    """ROUT_UPDATE_SHA256 is an operator trust anchor: even an artifact whose
    advertised hash matches is refused when it does not match the pin."""
    import os
    with tempfile.TemporaryDirectory() as tmp:
        data = b"artifact"
        good = sha256_hex(data)
        os.environ["ROUT_UPDATE_SHA256"] = good
        try:
            path = apply_update(tmp, "9.9.9", data, good)
            assert path.read_bytes() == data
        finally:
            os.environ.pop("ROUT_UPDATE_SHA256", None)
        os.environ["ROUT_UPDATE_SHA256"] = "0" * 64
        try:
            try:
                apply_update(tmp, "9.9.10", data, good)
                raise AssertionError("pin mismatch accepted")
            except UpdateError:
                pass
        finally:
            os.environ.pop("ROUT_UPDATE_SHA256", None)
    print("update pin enforced OK")


def main():
    test_exactly_once_over_loss()
    test_partial_and_corrupt_file()
    test_reassembly_is_bounded()
    test_file_request_is_gated()
    test_client_drops_foreign_datagrams()
    test_updater_trust_policy()
    test_updater_pin()
    asyncio.run(test_server_serves_update())
    asyncio.run(test_idempotent_create())
    test_rollback()
    test_restart_launches_artifact()
    asyncio.run(test_incompatible_protocol_rejected())
    print("RELIABILITY SMOKE TESTS PASSED")


if __name__ == '__main__':
    main()
