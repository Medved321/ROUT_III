"""Entry point for the Android build (python-for-android / buildozer).

p4a runs this file as the app's ``main.py`` with the application directory as
the working directory and already on ``sys.path``.  It is deliberately a thin
twin of ``run_client.py``: the desktop launchers stay the desktop story, and
only the platform bits that must happen *before* SDL starts live here.

Two rules shape this file:

``SDL_TOUCH_MOUSE_EVENTS``
    SDL2 synthesises a mouse press/motion pair from the first finger.  The
    touch overlay reads real ``FINGER*`` events, so on the phone that synthetic
    mouse would press every button twice -- once as a finger, once as a mouse.
    Menus translate fingers to clicks themselves (``GameClient._menu_events``),
    so the same double press would appear there too.  The variable must be set
    *before* ``pygame``/SDL initialises, which is why it is the first statement
    here and not a line inside the client.

``exit(ret)``
    p4a ends the app with ``exit(ret)``, so *any* escaping exception closes the
    window with no dialog -- exactly what "it crashes before loading" looks
    like on a phone.  All startup work therefore lives in ``_startup()``,
    inside one ``except BaseException`` that writes a stage-by-stage log and
    leaves the traceback on the screen (``client/fatal.py``) instead of
    calling ``exit()``.  Imports are inside that function on purpose: an
    ``ImportError`` is the most likely Android-only failure, and a module-level
    import would run before the guard exists.
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

from client.fatal import StartupLog, show_fatal  # noqa: E402

log = logging.getLogger("main")


def _startup(startup: StartupLog) -> None:
    """Everything from the first import to the end of the match.

    Kept in one function so the caller's ``except BaseException`` sees import
    failures and ``SystemExit`` alike.  Stage markers are written before each
    step: when the failure is native (no traceback) the last marker still says
    where the app died.
    """
    startup.stage("numpy bootstrap")
    from client.mobile import IS_ANDROID
    from client.numpy_bootstrap import (
        ANDROID_FAILED_MESSAGE,
        FAILED_MESSAGE,
        require_numpy,
    )

    startup.stage("numpy import")
    if not require_numpy():
        # A missing numpy used to be ``SystemExit(1)`` -- on the phone that is
        # a silent close, which is indistinguishable from a native crash.
        raise RuntimeError(ANDROID_FAILED_MESSAGE if IS_ANDROID else FAILED_MESSAGE)

    startup.stage("client import")
    from client.client import GameClient
    from client.settings import launch_config
    from shared.asyncio_util import use_udp_safe_event_loop

    startup.stage("config")
    # No CLI on Android -- the address is typed in the settings row and stored
    # in the app-private config, so ``launch_config`` must read only that.
    host, port, name = launch_config([])
    log.info("ROUT III client starting: %s:%s as %s", host, port, name)

    startup.stage("client init")
    client = GameClient(host, port, name)

    startup.stage("event loop")
    use_udp_safe_event_loop()
    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        client.running = False
        client.renderer.cleanup()
        log.info("Client stopped")
    startup.stage("stopped")


def main() -> int:
    startup = StartupLog()
    startup.attach_logging()
    startup.enable_faulthandler()
    try:
        _startup(startup)
    except BaseException as exc:  # noqa: BLE001 - nothing may escape to exit()
        text = startup.crash(exc)
        try:
            log.exception("startup failed")
        except BaseException:
            pass
        # Keep the process alive with the traceback on screen: a phone with no
        # adb can still show (and screenshot) what went wrong.
        if not show_fatal(text, log=startup):
            print(text, file=sys.stderr)
            print("Журнал: " + ", ".join(startup.paths), file=sys.stderr)
        startup.close()
        return 1
    startup.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
