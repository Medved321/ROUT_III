"""Startup diagnostics for the packaged build: never close silently.

python-for-android's ``start.c`` runs the entry point with
``PyRun_SimpleFile`` and ends with ``exit(ret)``.  Anything that escapes the
entry point -- including ``SystemExit`` -- therefore closes the app: on a
phone that reads as "it crashes before it loads anything", with no dialog and
no way to see the traceback without ``adb logcat``.  This module gives the
entry point the two things that make such a crash *readable on the device*:

``StartupLog``
    Stage markers written as the app boots, so "how far did it get" survives
    even when nothing can be drawn.  The file is written next to the app
    (``ANDROID_PRIVATE``, which also survives an APK update) and mirrored into
    the app's external files dir
    (``/storage/emulated/0/Android/data/<package>/files``), which can be
    copied off the phone with a file manager or ``adb pull``.  It also owns the
    ``faulthandler`` sink, so a native crash inside a ``.so`` is recorded
    instead of vanishing.

``show_fatal()``
    Draws the traceback on the screen and keeps the process alive until the
    user dismisses it.  A crash the player can screenshot is a bug report; a
    window that disappears is not.

The log path alone is not enough on a phone: ``ANDROID_PRIVATE`` is invisible
without root and ``Android/data`` is hidden from most file managers on Android
11.  So the last startup stage is *also* written to a small boot marker, and
the next launch shows it on screen (``StartupLog.previous``).  A native crash that
kills the process before an exception can be drawn therefore still names its
stage on the following start.

Only the standard library is imported at module level: the failure being
reported may *be* the pygame import, so pygame is imported inside
``show_fatal()`` and a failure there still leaves the log behind.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback

LOG_NAME = "rout3-startup.log"
BOOT_NAME = "rout3-boot.json"

#: Where Android mounts app-specific external storage.  The package name is
#: derived from ``ANDROID_PRIVATE`` (``/data/user/0/<package>/files``) instead
#: of asking Java: this module must work before anything else does.
EXTERNAL_ROOT = "/storage/emulated/0/Android/data"

TITLE = "ROUT III — ошибка при запуске"

HINT = "тап / пробел — листать · Esc — закрыть"

_MAX_MEMORY_LINES = 400


def _stamp() -> str:
    now = time.time()
    return time.strftime("%H:%M:%S", time.localtime(now)) + f".{int(now * 1000) % 1000:03d}"


def external_files_dir(private: str | None) -> str | None:
    """``.../files`` for the package that owns ``private``, or ``None``.

    ``ANDROID_PRIVATE`` is ``/data/user/0/<package>/files``; the external twin
    is ``/storage/emulated/0/Android/data/<package>/files``.  Nothing here
    writes yet -- the caller treats "cannot create" as "no mirror".
    """
    if not private:
        return None
    private = private.rstrip("/")
    package = os.path.basename(os.path.dirname(private))
    if not package or "/" in package or package in (".", ".."):
        return None
    return os.path.join(EXTERNAL_ROOT, package, "files")


def _log_dirs(private: str | None = None,
              app_root: str | None = None) -> list[str]:
    """Directories the diagnostics want to live in, most durable first.

    No "current directory" candidate: p4a always exports both variables (and
    ``main.py`` chdirs into the program tree anyway), so a cwd fallback would
    only ever write a log into the source checkout during development.
    """
    private = private if private is not None else os.environ.get("ANDROID_PRIVATE", "")
    app_root = app_root if app_root is not None else os.environ.get("ANDROID_APP_PATH", "")
    out: list[str] = []
    for directory in (private, app_root, external_files_dir(private)):
        if directory and directory not in out:
            out.append(directory)
    return out


def log_candidates(private: str | None = None,
                   app_root: str | None = None) -> list[str]:
    """Every place the log wants to live, most durable first, deduplicated."""
    return [os.path.join(directory, LOG_NAME)
            for directory in _log_dirs(private, app_root)]


def boot_candidates(private: str | None = None,
                    app_root: str | None = None) -> list[str]:
    """Every place the boot marker wants to live, same order as the log."""
    return [os.path.join(directory, BOOT_NAME)
            for directory in _log_dirs(private, app_root)]


class StartupLog:
    """Stage markers + traceback sink that never raises at the caller.

    Writing diagnostics must not be able to break the run it is diagnosing,
    so every filesystem operation here is best-effort: a read-only or missing
    directory is skipped, and the in-memory copy is still there for the screen.
    """

    def __init__(self, private: str | None = None, app_root: str | None = None):
        self.lines: list[str] = []
        self.paths: list[str] = []
        self.path: str | None = None
        self.last_stage = "start"
        self._files: list = []
        self._boot_paths = boot_candidates(private, app_root)
        # Read before the first ``stage()`` overwrites the marker: this is the
        # only record of a run that died natively after that marker was written.
        self.previous = self._read_previous()
        for path in log_candidates(private, app_root):
            handle = self._open(path)
            if handle is None:
                continue
            self.paths.append(path)
            self._files.append(handle)
            if self.path is None:
                self.path = path
        # Written into every boot marker: the phone may report a different
        # interpreter or ABI than the build assumed, and that is the first thing
        # to rule out when the app dies before anything can be drawn.
        self._info = {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "machine": os.environ.get("ANDROID_ABI", "") or "",
        }
        self.stage("entrypoint loaded")


    # ── files ───────────────────────────────────────────────────────
    @staticmethod
    def _open(path: str):
        try:
            directory = os.path.dirname(path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            # Line buffering: a hard crash right after a marker must not lose
            # the marker to a buffer we never get to flush.
            return open(path, "a", encoding="utf-8", buffering=1)
        except OSError:
            return None

    def write(self, text: str) -> None:
        line = f"[{_stamp()}] {text}"
        self.lines.append(line)
        if len(self.lines) > _MAX_MEMORY_LINES:
            del self.lines[:-_MAX_MEMORY_LINES]
        for handle in list(self._files):
            try:
                handle.write(line + "\n")
            except (OSError, ValueError):
                try:
                    handle.close()
                except (OSError, ValueError):
                    pass
                self._files.remove(handle)
        # p4a redirects stderr to logcat, so the trail is readable there even
        # when every file candidate is unwritable (or the user cannot reach
        # app-private storage).  ``LogFile`` in start.c flushes per newline.
        try:
            sys.stderr.write(line + "\n")
            sys.stderr.flush()
        except (OSError, ValueError, AttributeError):
            pass

    def _write_boot(self, status: str) -> None:
        """Best-effort record of the stage, for the next start's screen.

        The last few log lines travel with the marker: when the exception was
        the display itself, ``show_fatal()`` cannot draw it, and the next launch
        is the only chance to put the traceback in front of the player.
        """
        payload = json.dumps(
            {
                "stage": self.last_stage,
                "time": _stamp(),
                "status": status,
                **self._info,
                "tail": [line[:200] for line in self.lines[-8:]],
            },
            ensure_ascii=False,
        )
        for path in self._boot_paths:
            try:
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                return
            except OSError:
                continue

    def _read_previous(self) -> dict | None:
        """The boot marker of the run before this one, if it ended badly.

        Only a marker left *during startup* is a crash.  ``running`` means the
        startup finished and the app was later closed by the player (or killed
        by Android), which is not an error; ``shown`` means the traceback was
        already on screen and the player dismissed it.
        """
        for path in self._boot_paths:
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, ValueError, TypeError):
                continue
            if not isinstance(data, dict):
                continue
            if data.get("status") in ("startup", "failed"):
                return data
            return None
        return None

    def clear_boot(self) -> None:
        """Forget the previous run's marker (startup finished cleanly)."""
        self.flush()
        for path in self._boot_paths:
            try:
                os.remove(path)
            except OSError:
                pass

    def mark_running(self) -> None:
        """Startup succeeded; a later exit is a normal close, not a crash."""
        self._write_boot("running")

    def stage(self, name: str) -> None:
        self.last_stage = name
        self.write(f"stage: {name}")
        self._write_boot("startup")

    def flush(self) -> None:
        for handle in list(self._files):
            try:
                handle.flush()
            except (OSError, ValueError):
                pass

    def close(self) -> None:
        self.flush()
        for handle in list(self._files):
            try:
                handle.close()
            except (OSError, ValueError):
                pass
        self._files.clear()

    # ── integration ─────────────────────────────────────────────────
    def crash(self, exc: BaseException) -> str:
        """Record ``exc`` and return the text the screen should show."""
        summary = f"{type(exc).__name__}: {exc}"
        text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        self.write(f"FAILED at stage '{self.last_stage}' — {summary}")
        for line in text.rstrip().splitlines():
            self.write("    " + line)
        self.flush()
        self._write_boot("failed")
        return f"Этап: {self.last_stage}\n\n{text}".rstrip()

    def attach_logging(self) -> None:
        """Send the client's own log lines to the same file (and stderr)."""
        import logging

        handlers: list[logging.Handler] = [logging.StreamHandler()]
        if self.path:
            try:
                handlers.append(logging.FileHandler(self.path, mode="a",
                                                    encoding="utf-8"))
            except OSError:
                pass
        try:
            logging.basicConfig(
                level=logging.INFO,
                format="[%(asctime)s] %(name)s %(levelname)s: %(message)s",
                datefmt="%H:%M:%S",
                handlers=handlers,
                force=True,
            )
        except Exception:  # diagnostics must never be the thing that crashes
            pass

    def enable_faulthandler(self) -> None:
        """Record native crashes (SIGSEGV in numpy/pygame) in the same file."""
        if not self._files:
            return
        try:
            import faulthandler

            faulthandler.enable(file=self._files[0], all_threads=True)
        except (ImportError, OSError, ValueError, RuntimeError):
            pass

    def report(self) -> str:
        """One-line summary of where the log lives, for the screen footer."""
        return self.path or (self.paths[0] if self.paths else "журнал недоступен")


# ── the on-screen report ─────────────────────────────────────────────
def show_fatal(text: str, *, title: str = TITLE, log: StartupLog | None = None,
               interactive: bool = True) -> bool:
    """Draw ``text`` on screen and wait for the user; ``False`` if no display.

    ``interactive=False`` draws one frame and returns -- the smoke test uses it
    to prove the renderer works without waiting for a human.
    """
    try:
        import pygame
    except BaseException:
        return False
    try:
        pygame.init()
        screen = _open_display(pygame)
        if screen is None:
            return False
        return _draw_text(pygame, screen, text, title, log, interactive)
    except Exception:
        # The screen is best-effort: when it cannot be drawn the caller still
        # prints the traceback.  ``KeyboardInterrupt`` is deliberately not
        # caught, so Ctrl-C on a desktop run still ends the app.
        return False


def _open_display(pygame):
    """Create a display surface, trying the modes in order of preference."""
    attempts = (((0, 0), pygame.FULLSCREEN), ((0, 0), 0), ((1024, 768), 0),
                ((640, 480), 0))
    for size, flags in attempts:
        try:
            return pygame.display.set_mode(size, flags)
        except BaseException:
            try:
                pygame.display.quit()
                pygame.display.init()
            except BaseException:
                pass
    return None


def _make_font(pygame, size: int):
    for factory in (lambda: pygame.font.Font(pygame.font.get_default_font(), size),
                    lambda: pygame.font.SysFont(None, size)):
        try:
            return factory()
        except BaseException:
            continue
    return None


def _wrap(font, line: str, max_w: int) -> list[str]:
    """Break ``line`` to ``max_w`` pixels, splitting long tokens if needed."""
    if not line:
        return [""]
    if font is None:
        return [line[:120]]
    out: list[str] = []
    for char_line in line.split("\n"):
        current = ""
        for word in char_line.split(" "):
            candidate = word if not current else current + " " + word
            if font.size(candidate)[0] <= max_w:
                current = candidate
                continue
            if current:
                out.append(current)
                current = ""
            # A single token wider than the screen: cut it by characters.
            while font.size(word)[0] > max_w:
                cut = len(word)
                while cut > 1 and font.size(word[:cut])[0] > max_w:
                    cut -= 1
                out.append(word[:cut])
                word = word[cut:]
            current = word
        out.append(current)
    return out


def _draw_text(pygame, screen, text: str, title: str, log, interactive: bool) -> bool:
    width, height = screen.get_size()
    pad = max(8, min(width, height) // 40)
    line_h = max(12, height // 40)
    body_size = max(11, int(line_h * 0.72))
    title_size = max(14, int(line_h * 0.95))
    body = _make_font(pygame, body_size)
    heading = _make_font(pygame, title_size)

    lines = _wrap(heading, title, width - 2 * pad)
    lines.append("")
    lines.extend(_wrap(body, text, width - 2 * pad))
    lines.append("")
    footer = "лог: " + (log.report() if log is not None else "—")
    lines.extend(_wrap(body, footer, width - 2 * pad))

    footer_h = line_h + pad
    per_page = max(1, (height - 3 * pad - footer_h) // max(1, line_h))
    pages = max(1, (len(lines) + per_page - 1) // per_page)
    page = 0

    clock = pygame.time.Clock()
    while True:
        screen.fill((8, 8, 12))
        row = pad
        for index, line in enumerate(lines[page * per_page:(page + 1) * per_page]):
            font = heading if (page == 0 and index < 2) else body
            color = (255, 90, 90) if (page == 0 and index == 0) else (235, 235, 235)
            if font is None:
                break
            try:
                surface = font.render(line, True, color)
                screen.blit(surface, (pad, row))
            except BaseException:
                pass
            row += line_h
        if body is not None:
            try:
                hint = f"{HINT}   {page + 1}/{pages}"
                screen.blit(body.render(hint, True, (250, 220, 120)),
                            (pad, height - footer_h + pad // 2))
            except BaseException:
                pass
        try:
            pygame.display.flip()
        except BaseException:
            pass
        if not interactive:
            return True

        for event in pygame.event.get():
            kind = event.type
            if kind == pygame.QUIT:
                return True
            if kind == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, getattr(pygame, "K_AC_BACK", -1)):
                    return True
                if event.key in (pygame.K_SPACE, pygame.K_PAGEDOWN, pygame.K_RIGHT,
                                 pygame.K_DOWN):
                    page = (page + 1) % pages
                elif event.key in (pygame.K_PAGEUP, pygame.K_LEFT, pygame.K_UP):
                    page = (page - 1) % pages
            elif kind in (pygame.MOUSEBUTTONDOWN, getattr(pygame, "FINGERDOWN", -1)):
                page = (page + 1) % pages
        clock.tick(20)
