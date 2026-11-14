"""Platform facts the client branches on, in one place.

Only two questions are asked of the platform today, and both would otherwise be
answered by a different ad-hoc check in every module: "is this the packaged
Android build?" and "does this platform invent mouse events from touches?".
Bundling them here means a desktop run and a phone run differ in *one* imported
constant set, not in a scatter of ``sys.platform`` comparisons.
"""
from __future__ import annotations

import os
import sys


def is_android() -> bool:
    """True inside the python-for-android APK.

    p4a exports ``ANDROID_ARGUMENT``/``ANDROID_APP_PATH`` before the app starts;
    ``sys.platform`` is checked too because a native Android Python reports
    ``"android"``.  The environment is the reliable signal, though: the SDL2
    bootstrap keeps a Linux-looking ``sys.platform`` on some p4a versions.
    """
    if sys.platform == "android":
        return True
    return bool(os.environ.get("ANDROID_ARGUMENT")
                or os.environ.get("ANDROID_APP_PATH")
                or os.environ.get("P4A_BOOTSTRAP"))


IS_ANDROID = is_android()

#: Android's SDL turns the first finger into a mouse press/motion pair.  In the
#: touch mode that would press every overlay button twice (once from the finger,
#: once from the synthetic mouse), so the phone build ignores the mouse in game.
#: Desktop keeps reading it, which is how the overlay is tested without a phone.
TOUCH_NO_MOUSE = IS_ANDROID

#: The desktop self-updater replaces a .pyz and restarts it; inside an APK there
#: is no writable artifact to swap, and updating means installing a new APK.
UPDATER_ENABLED = not IS_ANDROID


def writable_root() -> str:
    """Directory the app may write to; the player's settings live here.

    On Android the unpacked program sits in ``<files>/app`` (``ANDROID_APP_PATH``)
    and that directory is **deleted and re-extracted** whenever the APK version
    changes (p4a's ``unpackAsset(..., cleanup_on_version_update=True)``).  The
    parent files directory (``ANDROID_PRIVATE``) survives an update, so the
    config goes one level up -- otherwise every APK update silently reset the
    player's settings.  Desktop keeps the project root, next to the launcher.
    """
    private = os.environ.get("ANDROID_PRIVATE")
    if private:
        return private
    app_path = os.environ.get("ANDROID_APP_PATH")
    if app_path:
        # Fallback inside the program tree: writable, just not durable.
        return os.path.dirname(os.path.abspath(app_path))
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # Inside a zipapp ``__file__`` points into the archive, so ``base`` is the
    # .pyz *file*, not a directory; the launcher's cwd is the project then.
    if base and os.path.isdir(base):
        return base
    return os.getcwd()
