"""Background music for the client: the single owner of the mixer.

Everything audio lives here: bringing the mixer up, finding tracks in ``music/``
next to the ``client/`` package, and playing them during a battle. Music is a
battle feature only: menus, editors, settings and the lobby stay silent, while
pause (which lives inside the game loop) keeps the battle music going. The rest
of the client never touches ``pygame.mixer`` — one subsystem, one owner.

The module degrades to silence on any audio failure: no sound card, no ``music/``
directory, no tracks, or an unreadable file.  None of those is an error.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
from pathlib import Path

import pygame

logger = logging.getLogger(__name__)

# Tracks live next to the client package, not in the working directory: a
# shortcut, scheduler or releases/ archive would otherwise silently lose them.
MUSIC_DIR = Path(__file__).resolve().parent.parent / "music"
EXTENSIONS = {".ogg", ".mp3", ".wav", ".flac", ".opus"}
POLL_INTERVAL = 0.05

# Fixed mixer parameters, so it comes up the same on every machine; one fallback
# (lower rate, mono) if the primary is refused.
_PRIMARY = {"frequency": 44100, "size": -16, "channels": 2, "buffer": 512}
_FALLBACK = {"frequency": 22050, "size": -16, "channels": 1, "buffer": 512}


class MusicPlayer:
    """Owns the mixer, the track list and the background advance task."""

    def __init__(self, directory=None, rng=None):
        self.directory = Path(directory) if directory is not None else MUSIC_DIR
        self._rng = rng if rng is not None else random.Random()
        self._tracks: list[Path] = []
        self._scanned = False
        self._unreadable: set[str] = set()
        self._current: Path | None = None
        self._context = "menu"
        self._volume = 0.4
        self._task: asyncio.Task | None = None
        self._is_running = None
        self._saw_busy = False
        # None = not tried yet, True = up, False = gave up (no retries).
        self._mixer_ready: bool | None = None

    # ── mixer ─────────────────────────────────────────────────────────
    def ready(self) -> bool:
        return self._mixer_ready is True

    def ensure_mixer(self) -> bool:
        """Bring the mixer up once, with one fallback; remember any refusal."""
        if self._mixer_ready is not None:
            return self._mixer_ready
        try:
            if pygame.mixer.get_init():
                self._mixer_ready = True
                return True
        except Exception:
            pass
        for params in (_PRIMARY, _FALLBACK):
            try:
                pygame.mixer.init(**params)
            except Exception:
                continue
            try:
                if pygame.mixer.get_init():
                    self._mixer_ready = True
                    return True
            except Exception:
                continue
        self._mixer_ready = False
        return False

    # ── tracks ────────────────────────────────────────────────────────
    def scan(self) -> list[Path]:
        """Read ``music/`` once into one battle playlist.

        Music is battle-only, so every supported file is a battle track and the
        menu screens simply have none. Unknown extensions are ignored, and the
        order is sorted by name so the playlist is the same on every launch.
        """
        if self._scanned:
            return self._tracks
        self._scanned = True
        tracks: list[Path] = []
        try:
            names = sorted(os.listdir(self.directory))
        except OSError:
            names = []
        for name in names:
            path = self.directory / name
            if path.suffix.lower() not in EXTENSIONS:
                continue
            if not path.is_file():
                continue
            tracks.append(path)
        self._tracks = tracks
        return tracks

    def _pool(self) -> list[Path]:
        return [p for p in self._tracks if str(p) not in self._unreadable]

    # ── playback ──────────────────────────────────────────────────────
    def set_context(self, context: str) -> None:
        """Music plays only in the ``game`` context; every other screen is silent."""
        if context not in ("menu", "game"):
            return
        changed = context != self._context
        self._context = context
        if not self.ready():
            return
        if context == "game":
            if changed:
                self._play_next()
            elif not self._busy() and self._pool():
                # came back to the battle and the playlist went quiet
                self._play_next()
        elif changed:
            self._silence()

    def _silence(self) -> None:
        self._current = None
        self._saw_busy = False
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass

    def set_volume(self, value) -> None:
        """Clamp to 0..1 and apply now; 0 is silence, not a stop."""
        try:
            value = float(value)
        except (TypeError, ValueError):
            return
        self._volume = max(0.0, min(1.0, value))
        if self.ready():
            try:
                pygame.mixer.music.set_volume(self._volume)
            except Exception:
                pass

    def _busy(self) -> bool:
        try:
            return bool(pygame.mixer.music.get_busy())
        except Exception:
            return False

    def _play_next(self) -> None:
        if not self.ready() or self._context != "game":
            return
        pool = self._pool()
        if not pool:
            self._current = None
            return
        choices = [p for p in pool if p != self._current] or pool
        while choices:
            pick = self._rng.choice(choices)
            try:
                pygame.mixer.music.load(str(pick))
                pygame.mixer.music.set_volume(float(self._volume))
                pygame.mixer.music.play()
            except Exception as exc:
                self._unreadable.add(str(pick))
                logger.warning("Трек не читается (%s): %s", pick.name, exc)
                choices = [p for p in self._pool() if p != self._current]
                continue
            self._current = pick
            self._saw_busy = False
            return
        self._current = None

    def start(self, is_running=None) -> asyncio.Task | None:
        """Start the background advance task; no-op without a mixer."""
        if self._task is not None and not self._task.done():
            return self._task
        if not self.ensure_mixer():
            return None
        self.scan()
        self._is_running = is_running
        self._play_next()
        self._task = asyncio.create_task(self._loop())
        return self._task

    def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
        try:
            if self.ready():
                pygame.mixer.music.stop()
        except Exception:
            pass

    def _running(self) -> bool:
        if self._is_running is not None:
            try:
                return bool(self._is_running())
            except Exception:
                return False
        return True

    async def _loop(self) -> None:
        try:
            while self._running():
                await asyncio.sleep(POLL_INTERVAL)
                if not self.ready():
                    continue
                if self._busy():
                    self._saw_busy = True
                elif self._saw_busy:
                    # a track we saw playing has ended: advance exactly once
                    self._saw_busy = False
                    self._play_next()
        except asyncio.CancelledError:
            pass


_default = MusicPlayer()


def ensure_mixer() -> bool:
    return _default.ensure_mixer()


def ready() -> bool:
    """True once the mixer is up — the flag other audio code (sfx) reads."""
    return _default.ready()


def scan_tracks() -> list[Path]:
    return _default.scan()


def set_context(context: str) -> None:
    _default.set_context(context)


def set_volume(value) -> None:
    _default.set_volume(value)


def start(is_running=None) -> asyncio.Task | None:
    return _default.start(is_running)


def stop() -> None:
    _default.stop()
