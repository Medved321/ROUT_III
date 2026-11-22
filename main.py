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

``rout3-boot.json``
    The screen above is drawn by Python, so it cannot report a death that
    happens *before* Python can draw (a native crash in SDL/numpy, a display
    that never comes up).  ``StartupLog`` therefore writes the current stage to
    a marker file as it goes, and the next launch shows that marker on screen
    before retrying.  That is the only trail a phone without ``adb`` leaves.
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
# Guarded because an exception here would escape before any diagnostics exist.
try:
    os.chdir(ROOT)
except OSError:
    pass

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
    # From here on the app is running: being closed later (or killed by
    # Android) is not a startup failure and must not raise an alarm next time.
    startup.mark_running()
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
    previous = startup.previous
    if previous:
        # A previous run died before it could draw anything (a native crash, a
        # display that never came up, or the player dismissing a report).  The
        # marker is the only record, and app-private files are unreachable from
        # a phone's file manager, so the stage and the last log lines are shown
        # on the next start.
        tail = previous.get("tail") or []
        details = "\n".join(str(line) for line in tail[-6:])
        text = (
            "Предыдущий запуск завершился аварийно.\n\n"
            f"Этап: {previous.get('stage', '?')}\n"
            f"Время: {previous.get('time', '?')}\n"
            f"Python: {previous.get('python', '?')} ({previous.get('platform', '?')})\n"
        )
        if details:
            text += "\nПоследние строки журнала:\n" + details + "\n"
        text += "\nЖурнал: " + startup.report() + "\n\nтап / Esc — продолжить запуск"
        try:
            show_fatal(
                text,
                title="ROUT III — прошлый запуск прервался",
                log=startup,
            )
        except BaseException:  # diagnostics must never stop the next try
            pass
    try:
        _startup(startup)
    except BaseException as exc:  # noqa: BLE001 - nothing may escape to exit()
        text = startup.crash(exc)
        try:
            log.exception("startup failed")
        except BaseException:
            pass
        # Keep the process alive with the traceback on screen: a phone with no
        # adb can still show (and screenshot) what went wrong.  The reporter
        # itself is guarded too -- it is the last line of defence, and a
        # failure inside it must not turn a readable crash into a silent close.
        try:
            shown = show_fatal(text, log=startup)
        except BaseException:
            shown = False
        if shown:
            # The player has seen this report; do not repeat it next launch.
            startup.clear_boot()
        else:
            for stream in (sys.stderr, sys.stdout):
                try:
                    print(text, file=stream)
                    print("Журнал: " + ", ".join(startup.paths), file=stream)
                    stream.flush()
                except BaseException:
                    pass
        startup.close()
        return 1
    startup.clear_boot()
    startup.close()
    return 0


if __name__ == "__main__":
    # No ``sys.exit()``: ``SystemExit`` escaping the entry point is exactly the
    # silent window close this module exists to prevent.  p4a ends the process
    # on its own once the script returns.
    main()
