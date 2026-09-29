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
from client.updater import UpdateError, apply_update, restart, rollback


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


def main():
    test_exactly_once_over_loss()
    test_partial_and_corrupt_file()
    asyncio.run(test_server_serves_update())
    asyncio.run(test_idempotent_create())
    test_rollback()
    test_restart_launches_artifact()
    asyncio.run(test_incompatible_protocol_rejected())
    print("RELIABILITY SMOKE TESTS PASSED")


if __name__ == '__main__':
    main()
