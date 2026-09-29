"""Update-prompt loop smoke tests: version comparator, strictly-newer server
decision, client skip memory and build-time source version sync."""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

os.environ['SDL_VIDEODRIVER'] = 'dummy'  # noqa: E402
import pygame  # noqa: E402
pygame.init()  # noqa: E402
pygame.display.set_mode((1000, 700))  # noqa: E402

import client.settings as settings_mod  # noqa: E402
from shared.releases import is_newer, parse_version  # noqa: E402
from shared.version import PROTOCOL_VERSION  # noqa: E402


def test_comparator():
    assert parse_version("1.0.10") == (1, 0, 10)
    assert parse_version("1.0.2") == (1, 0, 2)
    assert parse_version("2.0") == (2, 0)
    assert parse_version("abc") == ()
    assert parse_version("") == ()
    assert parse_version("1.0.0-beta") == (1, 0, 0)

    assert is_newer("1.0.10", "1.0.9")
    assert is_newer("2.0.0", "1.9.9")
    assert is_newer("1.0.3", "1.0.2")
    assert not is_newer("1.0.2", "1.0.2")
    assert not is_newer("1.0.2", "1.0.3")
    assert not is_newer("0.9.0", "1.0.0")
    assert not is_newer("1.0.0-beta", "1.0.0")
    assert not is_newer("abc", "1.0.0")
    assert not is_newer("", "1.0.0")
    assert not is_newer("1.0.2", "garbage")
    print("parse_version / is_newer OK")


def test_server_strictly_newer():
    from server.server import GameServer

    class FakeNetwork:
        def __init__(self):
            self.client_info = {}
            self.calls = []

        def send_hello_ack(self, addr, **kwargs):
            self.calls.append(kwargs)

    class FakeRelease:
        def __init__(self, version):
            self._version = version

        def latest(self):
            return {
                "version": self._version,
                "size": 100,
                "sha256": "ab12" * 16,
            }

    cases = [
        # client_version, release_version, expected_has_update
        ("1.0.2", "1.0.2", False),
        ("1.0.0", "1.0.2", True),
        ("1.0.3", "1.0.2", False),
    ]
    for client_version, release_version, expected in cases:
        server = GameServer.__new__(GameServer)
        server.releases = FakeRelease(release_version)
        server.network = FakeNetwork()
        server._on_hello(("127.0.0.1", 1), PROTOCOL_VERSION, client_version)
        ack = server.network.calls[-1]
        assert ack["accepted"] is True, ack
        assert ack["has_update"] is expected, (client_version, release_version, ack)
        if expected:
            assert ack["update_version"] == release_version
        else:
            assert ack["update_version"] == ""
    print("_on_hello strictly-newer OK")


def _make_client(config_dir):
    settings_mod.CONFIG_PATH = os.path.join(config_dir, "config.json")
    from client.client import GameClient
    from client.network_client import UpdateOffer
    client = GameClient("127.0.0.1", 7803, "Smoker")
    client.settings.load()
    return client, UpdateOffer


def test_client_skip_memory():
    with tempfile.TemporaryDirectory() as tmp:
        client, UpdateOffer = _make_client(tmp)
        try:
            # Same version as the saved decline -> no dialog at all.
            client.settings.set("skipped_update_version", "1.0.2")
            client.settings.save()
            client.network.update_offer = UpdateOffer("1.0.2", 100, "abc")
            called = []
            original = client._confirm_update

            async def spy(offer):
                called.append(offer.version)
                return False

            client._confirm_update = spy
            assert asyncio.run(client._maybe_update()) is False
            assert called == [], f"dialog shown for a skipped version: {called}"
            client._confirm_update = original

            # A strictly newer version is offered again; refusal is remembered.
            client.settings.set("skipped_update_version", "1.0.2")
            client.settings.save()
            client.network.update_offer = UpdateOffer("1.0.3", 100, "abc")
            old = os.environ.get("ROUT_UPDATE_ANSWER")
            os.environ["ROUT_UPDATE_ANSWER"] = "n"
            try:
                assert asyncio.run(client._maybe_update()) is False
            finally:
                if old is None:
                    os.environ.pop("ROUT_UPDATE_ANSWER", None)
                else:
                    os.environ["ROUT_UPDATE_ANSWER"] = old
            assert client.settings.get("skipped_update_version") == "1.0.3", \
                client.settings.get("skipped_update_version")
        finally:
            client.renderer.cleanup()
    print("client skip memory OK")


def test_build_syncs_source_version():
    from tools import build_client
    source = ROOT / "shared" / "version.py"
    original = source.read_text(encoding="utf-8")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            build_client.build("9.9.9", Path(tmp), set_latest=True, sync_source=True)
        text = source.read_text(encoding="utf-8")
        assert 'CLIENT_VERSION = "9.9.9"' in text, text
        assert f"PROTOCOL_VERSION = {PROTOCOL_VERSION}" in text, text

        # Without --set-latest the source stays untouched.
        with tempfile.TemporaryDirectory() as tmp:
            build_client.build("8.8.8", Path(tmp), set_latest=False, sync_source=True)
        assert source.read_text(encoding="utf-8") == text

        # --no-source-version keeps the source intact even with --set-latest.
        with tempfile.TemporaryDirectory() as tmp:
            build_client.build("7.7.7", Path(tmp), set_latest=True, sync_source=False)
        assert source.read_text(encoding="utf-8") == text
    finally:
        source.write_text(original, encoding="utf-8")
    print("build source version sync OK")


def main():
    test_comparator()
    test_server_strictly_newer()
    test_client_skip_memory()
    test_build_syncs_source_version()
    print("UPDATE-PROMPT SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()