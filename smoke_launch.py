"""Launch/bootstrap smoke tests: launch_config, bootstrap.ensure, launch_client,
the artifact __main__ template, ConnectScreen and the GameClient connect flow."""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from shared.asyncio_util import use_udp_safe_event_loop  # noqa: E402

use_udp_safe_event_loop()

os.environ['SDL_VIDEODRIVER'] = 'dummy'  # noqa: E402
import pygame  # noqa: E402
pygame.init()  # noqa: E402
surface = pygame.display.set_mode((1000, 700))  # noqa: E402

import client.settings as settings_mod  # noqa: E402
import tools.bootstrap as bootstrap  # noqa: E402
import tools.launch_client as launch_client  # noqa: E402
import tools.build_client as build_client  # noqa: E402
from client.connect_screen import ConnectScreen  # noqa: E402
from shared.releases import artifact_path, write_latest_pointer  # noqa: E402
from shared.version import CLIENT_VERSION  # noqa: E402


def test_launch_config():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Path(tmp) / "config.json"
        settings_mod.CONFIG_PATH = str(cfg)

        # defaults without a config file
        host, port, name = settings_mod.launch_config([])
        assert (host, port, name) == ("localhost", 7777, "Player"), (host, port, name)

        # values from config.json
        cfg.write_text(json.dumps({
            "server_host": "192.168.1.5", "server_port": 9999,
            "player_name": "Bob", "fov": 95,
        }), encoding="utf-8")
        host, port, name = settings_mod.launch_config([])
        assert (host, port, name) == ("192.168.1.5", 9999, "Bob"), (host, port, name)

        # CLI overrides win and the file is not modified
        before = cfg.read_bytes()
        host, port, name = settings_mod.launch_config(
            ["--host", "10.0.0.1", "--port", "1234", "--name", "CLI"]
        )
        assert (host, port, name) == ("10.0.0.1", 1234, "CLI"), (host, port, name)
        assert cfg.read_bytes() == before, "config was modified by CLI overrides"

        # settings roundtrip keeps the new keys
        s = settings_mod.Settings()
        s.load()
        assert s.get("server_host") == "192.168.1.5"
        assert s.get("server_port") == 9999
        assert s.get("player_name") == "Bob"
    print("launch_config OK")


def test_settings_roundtrip_server_keys():
    with tempfile.TemporaryDirectory() as tmp:
        settings_mod.CONFIG_PATH = os.path.join(tmp, "config.json")
        s = settings_mod.Settings()
        s.set("server_host", "example.local")
        s.set("server_port", 7000)
        s.set("player_name", "Gamer")
        s.save()
        s2 = settings_mod.Settings()
        s2.load()
        assert s2.get("server_host") == "example.local"
        assert s2.get("server_port") == 7000
        assert s2.get("player_name") == "Gamer"
    print("settings server keys roundtrip OK")


def test_bootstrap_ensure():
    # 1.2: a venv with a broken pygame module must be recreated.
    base = bootstrap.find_python()
    if base is None:
        # No supported interpreter on this machine (bootstrap refuses
        # free-threaded builds: no pygame wheels). Assert the failure is
        # actionable instead of crashing on `[*None]`.
        try:
            bootstrap.ensure(Path(tempfile.mkdtemp()) / "venv", no_pip=True)
        except RuntimeError as exc:
            assert "Python" in str(exc), exc
            print("bootstrap.ensure no supported interpreter: actionable error OK")
        else:
            raise AssertionError("ensure() succeeded without a base interpreter")
        return
    with tempfile.TemporaryDirectory() as tmp:
        venv_dir = Path(tmp) / "venv"
        subprocess.run([*base, "-m", "venv", str(venv_dir)],
                       check=True)
        site = venv_dir / "Lib" / "site-packages"
        if not site.is_dir():
            site = venv_dir / "lib"
            site = next(site.glob("python*")) / "site-packages"
        broken = site / "pygame"
        broken.mkdir(parents=True, exist_ok=True)
        (broken / "__init__.py").write_text("raise ImportError('broken')", encoding="utf-8")

        python = bootstrap.ensure(venv_dir, no_pip=True)
        assert python.is_file()
        assert not broken.exists(), "broken venv was not recreated"
    print("bootstrap.ensure recreate OK")

    # 1.1: CLI passes python args through; no args prints the usage hint.
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ)
        env["ROUT_NO_PIP"] = "1"
        proc = subprocess.run(
            [*base, str(ROOT / "tools" / "bootstrap.py"),
             "--venv", os.path.join(tmp, "venv"), "-c",
             "import sys; print('ok')"],
            capture_output=True, text=True, env=env, cwd=str(ROOT),
        )
        assert proc.returncode == 0, proc.stderr
        assert "ok" in proc.stdout, proc.stdout
        proc = subprocess.run(
            [*base, str(ROOT / "tools" / "bootstrap.py")],
            capture_output=True, text=True, env=env, cwd=str(ROOT),
        )
        assert proc.returncode == 1 and "Usage" in proc.stdout, proc.stdout
    print("bootstrap CLI OK")


def test_launch_client_selection():
    assert launch_client.newer("1.0.10", "1.0.9")
    assert launch_client.newer("2.0.0", "1.9.9")
    assert not launch_client.newer("0.9.0", "1.0.0")
    assert not launch_client.newer("1.0.9", "1.0.10")
    assert not launch_client.newer("1.0.0-beta", "1.0.0")
    assert not launch_client.newer("garbage", "1.0.0")

    with tempfile.TemporaryDirectory() as tmp:
        # nothing installed -> sources
        assert launch_client.select_artifact(tmp) is None
        # older artifact -> sources
        older = "0.9.0"
        artifact_path(tmp, older).write_bytes(b"fake-old")
        write_latest_pointer(tmp, older)
        assert launch_client.select_artifact(tmp) is None
        # newer artifact -> that artifact
        newer = "9.9.9"
        artifact_path(tmp, newer).write_bytes(b"fake-new")
        write_latest_pointer(tmp, newer)
        selected = launch_client.select_artifact(tmp)
        assert selected is not None
        assert selected.name == f"rout-client-{newer}.pyz", selected.name
    print("launch_client selection OK")


def test_build_template_uses_launch_config():
    template = build_client.MAIN_TEMPLATE
    assert "launch_config" in template, "MAIN_TEMPLATE does not use launch_config"
    assert "client.settings" in template
    assert "--host" not in template and "--port" not in template and "--name" not in template
    print("build_client MAIN_TEMPLATE OK")


def test_connect_screen():
    screen = ConnectScreen(1000, 700, "localhost", 7777, "Player")
    screen.status = "Сервер недоступен"
    screen.draw(surface)
    frame_err = pygame.image.tostring(surface, "RGB")
    screen.status = ""
    screen.draw(surface)
    frame_ok = pygame.image.tostring(surface, "RGB")
    assert frame_err != frame_ok, "error frame equals normal frame"

    screen.host = "myhost"
    screen._focus = 0
    key = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_g, unicode="g")
    assert screen.handle([key]) is None
    assert screen.host == "myhostg"
    enter = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, unicode="\r")
    assert screen.handle([enter]) == "connect"
    esc = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE, unicode="\x1b")
    assert screen.handle([esc]) == "quit"
    click = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=screen.connect_btn.center)
    assert screen.handle([click]) == "connect"
    print("ConnectScreen OK")


def test_gameclient_connect_flow():
    from server.server import GameServer
    from client.client import GameClient

    def post(key):
        pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=key, unicode="\r"))

    async def lobby_flow():
        with tempfile.TemporaryDirectory() as tmp:
            server = GameServer(host="127.0.0.1", port=7801, releases_dir=tmp)
            task = asyncio.ensure_future(server.start())
            await asyncio.sleep(0.3)

            client = GameClient("127.0.0.1", 7801, "Smoker")
            runner = asyncio.ensure_future(client.run())
            await asyncio.sleep(0.3)
            assert client.running

            post(pygame.K_RETURN)
            for _ in range(600):
                await asyncio.sleep(0.05)
                if client.network.state == "lobby":
                    break
            assert client.network.state == "lobby", client.network.state
            client.running = False
            await runner

            server.stop()
            try:
                await task
            except asyncio.CancelledError:
                pass
    asyncio.run(lobby_flow())
    print("GameClient -> lobby OK")

    async def unreachable_flow():
        with tempfile.TemporaryDirectory() as tmp:
            client = GameClient("127.0.0.1", 7802, "Smoker")
            runner = asyncio.ensure_future(client.run())
            await asyncio.sleep(0.3)
            assert client.running
            post(pygame.K_RETURN)
            for _ in range(150):
                await asyncio.sleep(0.05)
                if client.connect_screen.status:
                    break
            assert "Сервер недоступен" in client.connect_screen.status, \
                client.connect_screen.status
            assert client.running, "client stopped after a failed connect"
            client.running = False
            await runner
    asyncio.run(unreachable_flow())
    print("GameClient unreachable -> screen error, process alive OK")


def test_artifact_build_and_launch():
    with tempfile.TemporaryDirectory() as tmp:
        releases = Path(tmp)
        target = build_client.build("9.9.9", releases, set_latest=False)
        assert target.is_file()

        import zipfile
        with zipfile.ZipFile(target) as zf:
            main_py = zf.read("__main__.py").decode("utf-8")
        assert "launch_config" in main_py, "generated __main__ lacks launch_config"
        assert "client.settings" in main_py

        # The artifact must start and reach the connect phase (stay alive).
        env = dict(os.environ, PYTHONUTF8="1", SDL_VIDEODRIVER="dummy")
        proc = subprocess.Popen(
            [sys.executable, str(target)], cwd=str(ROOT), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            proc.wait(timeout=6)
            raise AssertionError(
                f"artifact exited early (returncode={proc.returncode})"
            )
        except subprocess.TimeoutExpired:
            pass
        proc.terminate()
        proc.wait(timeout=10)
    print("artifact build + launch OK")


def main():
    test_launch_config()
    test_settings_roundtrip_server_keys()
    test_bootstrap_ensure()
    test_launch_client_selection()
    test_build_template_uses_launch_config()
    test_connect_screen()
    test_artifact_build_and_launch()
    test_gameclient_connect_flow()
    print("ALL LAUNCH SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()