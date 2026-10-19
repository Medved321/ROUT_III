"""Smoke: client music (client/music.py).

Every scenario runs without an audio device: the mixer is either patched to
refuse (the "no sound card" case) or replaced by a fake that records load/play,
so the checks are about our logic and silence, never about real playback.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pygame  # noqa: E402

from client import music  # noqa: E402


class FakeMusic:
    """Stand-in for pygame.mixer.music that records what was asked of it."""

    def __init__(self, fail_on=()):
        self.loaded: list[str] = []
        self.playing = False
        self.busy = False
        self.volume = None
        self.stops = 0
        self.fail_on = set(fail_on)

    def load(self, path):
        self.loaded.append(path)
        if path in self.fail_on:
            raise pygame.error("cannot read track")

    def play(self, *a, **k):
        self.playing = True

    def set_volume(self, value):
        self.volume = value

    def get_busy(self):
        return self.busy

    def stop(self):
        self.stops += 1
        self.playing = False


class _Swap:
    """Context manager that swaps attributes and restores them on exit."""

    def __init__(self, *pairs):
        self.pairs = pairs
        self.saved = []

    def __enter__(self):
        for obj, name, value in self.pairs:
            self.saved.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)
        return self

    def __exit__(self, *exc):
        for obj, name, value in reversed(self.saved):
            setattr(obj, name, value)
        return False


def _tree(files: dict[str, str]):
    d = tempfile.mkdtemp()
    for name in files:
        Path(d, name).write_bytes(b"x")
    return d


def _ready_player(directory=None, fake=None):
    """A player with the mixer already 'up' and a fake music pipe attached."""
    p = music.MusicPlayer(directory)
    p._mixer_ready = True
    p.scan()
    return p


def test_no_mixer_is_silent_and_tries_twice():
    calls = {"n": 0}

    def refuse(**kwargs):
        calls["n"] += 1
        raise pygame.error("no audio device")

    with _Swap((pygame.mixer, "init", refuse),
               (pygame.mixer, "get_init", lambda: None)):
        p = music.MusicPlayer()
        assert p.ensure_mixer() is False
        assert calls["n"] == 2, f"expected one fallback, got {calls['n']} tries"
        # remembered: a second ask makes no new attempt
        assert p.ensure_mixer() is False
        assert calls["n"] == 2, "a refused mixer must not be retried"
        # nothing else may raise, and start is a no-op
        assert p.start(lambda: True) is None
        p.set_context("game")
        p.set_volume(0.5)
        p.stop()
    print("  no mixer         OK")


def test_scan_reads_supported_files_once():
    d = _tree({
        "menu_theme.mp3": "",
        "boss.wav": "",
        "E1M1.flac": "",
        "notes.txt": "",
    })
    p = music.MusicPlayer(d)
    tracks = p.scan()
    assert [x.name for x in tracks] == ["E1M1.flac", "boss.wav", "menu_theme.mp3"]
    # read once: a file added later must not appear
    Path(d, "late.mp3").write_bytes(b"x")
    assert len(p.scan()) == 3

    # no directory at all is silence, not a crash
    missing = music.MusicPlayer(Path(d) / "nope")
    assert missing.scan() == []
    print("  scan tracks      OK")


def test_scan_missing_directory_is_empty():
    p = music.MusicPlayer(Path(tempfile.mkdtemp(), "nope"))
    groups = p.scan()
    assert groups == {"menu": [], "game": []}, groups
    print("  missing dir      OK")


def test_loop_advances_once_per_finished_track():
    d = _tree({"a.mp3": "", "b.mp3": "", "c.mp3": ""})
    p = _ready_player(d)
    fake = FakeMusic()
    seq = [True, False, False, False]

    async def scenario():
        with _Swap((pygame.mixer, "music", fake)):
            p._busy = lambda: seq.pop(0) if seq else False
            calls = {"n": 0}

            def running():
                calls["n"] += 1
                return calls["n"] <= 3

            p.start(running)
            await asyncio.sleep(0.3)
            p.stop()

    asyncio.run(scenario())
    assert len(fake.loaded) == 2, \
        f"one end-of-track must advance once, got {len(fake.loaded)} loads"
    assert fake.stops >= 1, "stop() must stop the pipe"
    print("  advance once     OK")


def test_menu_is_silent_and_game_plays():
    d = _tree({"menu_a.mp3": "", "game_a.mp3": "", "game_b.mp3": ""})
    p = _ready_player(d)
    fake = FakeMusic()
    with _Swap((pygame.mixer, "music", fake)):
        p.set_context("menu")
        assert fake.loaded == [], "menus must stay silent"
        assert fake.stops == 0
        p.set_context("game")
        assert fake.loaded, "entering the party must start a track"
        fake.busy = True               # the battle track is playing
        # the same screen again while playing must not reload
        n = len(fake.loaded)
        p.set_context("game")
        assert len(fake.loaded) == n, "a playing screen must not restart its track"
        # leaving to a menu silences the battle track
        p.set_context("menu")
        assert fake.stops >= 1, "leaving the battle must stop the music"
        # ...and coming back starts a track again
        p.set_context("game")
        assert len(fake.loaded) == n + 1, "returning to the battle must resume"
    print("  battle only      OK")


def test_pause_keeps_the_game_music():
    """Pause runs inside the game loop, so the context never leaves 'game'."""
    d = _tree({"game_x.mp3": ""})
    p = _ready_player(d)
    fake = FakeMusic()
    with _Swap((pygame.mixer, "music", fake)):
        p.set_context("game")
        game_track = fake.loaded[-1]
        # the pause overlay does not change the context; the dispatcher would
        # still pass 'game', so the track must stay the same
        p.set_context("game")
        assert fake.loaded[-1] == game_track
        assert fake.stops == 0, "pause must not silence the battle music"
    print("  pause group      OK")


def test_volume_clamps_and_zero_does_not_stop():
    d = _tree({"game_x.mp3": ""})
    p = _ready_player(d)
    fake = FakeMusic()
    with _Swap((pygame.mixer, "music", fake)):
        p.set_context("game")
        loads = len(fake.loaded)
        p.set_volume(0.0)
        assert fake.volume == 0.0
        assert fake.stops == 0, "volume 0 is silence, not a stop"
        assert len(fake.loaded) == loads, "volume must not reload the track"
        p.set_volume(2.0)
        assert fake.volume == 1.0
        p.set_volume(-1)
        assert fake.volume == 0.0
        p.set_volume("not a number")
        assert fake.volume == 0.0, "a bad value must be ignored, not applied"
    print("  volume clamp     OK")


def test_broken_and_missing_tracks_are_silent():
    d = _tree({"game_a.mp3": "", "game_b.mp3": ""})
    fake = FakeMusic(fail_on={str(Path(d, "game_a.mp3")), str(Path(d, "game_b.mp3"))})
    p = _ready_player(d)
    with _Swap((pygame.mixer, "music", fake)):
        p.set_context("game")          # the initial play, both fail
        assert p._current is None, "all-broken pool must fall silent"
        assert set(p._unreadable) >= {str(Path(d, "game_a.mp3")),
                                      str(Path(d, "game_b.mp3"))}
    # an empty directory is silence with no exception
    empty = _ready_player(tempfile.mkdtemp())
    with _Swap((pygame.mixer, "music", FakeMusic())):
        empty.set_context("game")
        assert empty._current is None
    print("  broken silent    OK")


def test_play_group_never_immediately_repeats():
    import random as _random
    d = _tree({"a.mp3": "", "b.mp3": "", "c.mp3": ""})
    p = _ready_player(d)
    p._rng = _random.Random(1234)
    fake = FakeMusic()
    with _Swap((pygame.mixer, "music", fake)):
        p.set_context("game")
        for _ in range(100):
            p._play_next()
        names = [Path(x).name for x in fake.loaded]
        repeats = [(a, b) for a, b in zip(names, names[1:]) if a == b]
        assert not repeats, f"a multi-track group must not repeat back-to-back: {repeats}"
    # a single-track group repeats it on purpose
    d1 = _tree({"only.mp3": ""})
    p1 = _ready_player(d1)
    one = FakeMusic()
    with _Swap((pygame.mixer, "music", one)):
        p1.set_context("game")
        p1._play_next()
        p1._play_next()
        assert [Path(x).name for x in one.loaded] == ["only.mp3"] * len(one.loaded)
        assert len(one.loaded) == 3, one.loaded
    print("  no repeat        OK")


def test_settings_hands_volume_to_the_module():
    from client.settings import Settings
    seen = []
    with _Swap((music, "set_volume", lambda v: seen.append(v))):
        Settings()._apply_audio()
    assert seen, "settings must route the volume through the music module"
    print("  settings volume  OK")


def main():
    pygame.init()
    test_no_mixer_is_silent_and_tries_twice()
    test_scan_reads_supported_files_once()
    test_menu_is_silent_and_game_plays()
    test_pause_keeps_the_game_music()
    test_volume_clamps_and_zero_does_not_stop()
    test_broken_and_missing_tracks_are_silent()
    test_play_group_never_immediately_repeats()
    test_settings_hands_volume_to_the_module()
    print("smoke_music: ALL OK")


if __name__ == "__main__":
    main()
