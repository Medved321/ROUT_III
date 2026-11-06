"""Entry point for the Android build (python-for-android / buildozer).

p4a runs this file as the app's ``main.py`` with the application directory as
the working directory and already on ``sys.path``.  It is deliberately a thin
twin of ``run_client.py``: the desktop launchers stay the desktop story, and
only the platform bits that must happen *before* SDL starts live here.

One ordering detail matters more than it looks:

``SDL_TOUCH_MOUSE_EVENTS``
    SDL2 synthesises a mouse press/motion pair from the first finger.  The
    touch overlay reads real ``FINGER*`` events, so on the phone that synthetic
    mouse would press every button twice -- once as a finger, once as a mouse.
    Menus translate fingers to clicks themselves (``GameClient._menu_events``),
    so the same double press would appear there too.  The variable must be set
    *before* ``pygame``/SDL initialises, which is why it is the first statement
    here and not a line inside the client.
"""
from __future__ import annotations

import os

# Must precede the pygame import: SDL reads these at init.
os.environ.setdefault("SDL_TOUCH_MOUSE_EVENTS", "0")
# The phone is always the "touch" story; nothing else needs a mouse.
os.environ.setdefault("SDL_VIDEO_X11_NET_WM_BYPASS_COMPOSITOR", "0")

import asyncio  # noqa: E402
import logging  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# p4a already chdirs into the extracted app dir; do it anyway so a run from an
# IDE with a different cwd still finds ``maps/`` and ``skins/`` the same way.
os.chdir(ROOT)

from client.numpy_bootstrap import require_numpy  # noqa: E402

if not require_numpy():
    raise SystemExit(1)

from client.client import GameClient  # noqa: E402
from client.settings import launch_config  # noqa: E402
from shared.asyncio_util import use_udp_safe_event_loop  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)

log = logging.getLogger("main")


def main() -> None:
    # No CLI on Android -- the address is typed in the settings row and stored
    # in the app-private config, so ``launch_config`` must read only that.
    host, port, name = launch_config([])
    log.info("ROUT III client starting: %s:%s as %s", host, port, name)
    client = GameClient(host, port, name)

    use_udp_safe_event_loop()
    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        client.running = False
        client.renderer.cleanup()
        log.info("Client stopped")
    except Exception:
        # Nothing on a phone is more useless than a silent close: logcat keeps
        # the traceback, and on-device it at least names the failing module.
        log.exception("Client crashed")
        raise


if __name__ == "__main__":
    main()
