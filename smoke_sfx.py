"""Smoke: client sound effects (client/sfx.py + client hooks).

No audio device is required: the mixer is either patched to refuse, or its
``get_init``/``Sound`` are replaced by fakes, so the checks are about synthesis
format, rate-limiting, failure-as-silence and the event hooks — never about real
playback.
"""
from __future__ import annotations

import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pygame  # noqa: E402

from client import music, sfx  # noqa: E402
from client.client import GameClient  # noqa: E402


class FakeSound:
    def __init__(self, buffer=None, **kwargs):
        self.buffer = buffer
        self.vol = None
        self.channels = 0
        self.played = 0

    def set_volume(self, v):
        self.vol = v

    def get_num_channels(self):
        return self.channels

    def play(self, *a, **k):
        self.played += 1
        self.channels += 1


class FakeMusic:
    def __init__(self, ready=True):
        self._ready = ready
        self.init_calls = 0

    def ready(self):
        return self._ready

    def ensure_mixer(self):
        self.init_calls += 1
        return self._ready


class _Swap:
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


def _mixer(freq=44100, size=-16, channels=2):
    return (freq, size, channels)


def test_mixer_absent_is_silent():
    sfx.reset()
    with _Swap((pygame.mixer, "init",
                lambda **k: (_ for _ in ()).throw(pygame.error("no device"))),
               (pygame.mixer, "get_init", lambda: None),
               (sfx, "music", FakeMusic(ready=False))):
        assert sfx.ensure() is False
        assert sfx._bank == {}
        sfx.play("shoot_pistol")      # must not raise
        sfx.set_volume(0.5)
        sfx.play("does_not_exist")
    sfx.reset()
    print("  no mixer         OK")


def test_render_buffer_matches_mixer_format():
    for freq, channels in ((44100, 2), (22050, 1)):
        for name, recipe in sfx.SOUNDS.items():
            buf = sfx.render_buffer(recipe, freq, channels)
            frames = max(1, int(freq * recipe["dur"]))
            assert len(buf) == frames * channels * 2, (
                f"{name} @{freq}/{channels}: {len(buf)} != {frames*channels*2}")
            assert any(buf), f"{name} produced silence"
        # stereo interleaves two identical samples; mono has no second copy
        recipe = sfx.SOUNDS["pickup"]
        stereo = sfx.render_buffer(recipe, freq, 2) if channels == 2 else None
        if stereo is not None:
            left = stereo[0::4]
            right = stereo[2::4]
            assert left == right, "stereo channels must carry the same sample"
    print("  synth format     OK")


def test_bank_builds_without_second_init():
    sfx.reset()
    init_calls = {"n": 0}

    def fail_init(**kwargs):
        init_calls["n"] += 1
        raise pygame.error("must not be called")

    with _Swap((pygame.mixer, "get_init", lambda: _mixer()),
               (pygame.mixer, "Sound", FakeSound),
               (pygame.mixer, "set_num_channels", lambda n: None),
               (pygame.mixer, "get_num_channels", lambda: 8),
               (pygame.mixer, "init", fail_init),
               (sfx, "music", FakeMusic(ready=True))):
        assert sfx.ensure() is True
        assert init_calls["n"] == 0, "sfx must never init the mixer itself"
        assert set(sfx.SOUNDS).issubset(sfx._bank), "not every sound was built"
        assert all(isinstance(s, FakeSound) for s in sfx._bank.values())
    sfx.reset()
    print("  bank / one init   OK")


def test_ensure_asks_music_when_not_ready():
    sfx.reset()
    fake = FakeMusic(ready=False)
    with _Swap((pygame.mixer, "get_init", lambda: _mixer()),
               (pygame.mixer, "Sound", FakeSound),
               (pygame.mixer, "set_num_channels", lambda n: None),
               (pygame.mixer, "get_num_channels", lambda: 8),
               (sfx, "music", fake)):
        assert sfx.ensure() is False
        assert fake.init_calls == 1, "sfx should ask music to bring the mixer up"
    sfx.reset()
    print("  asks music        OK")


def test_cooldown_and_channel_limit():
    sfx.reset()
    snd = FakeSound()
    sfx._bank["hit_wall"] = snd
    sfx._ready = True
    sfx.play("hit_wall")
    sfx.play("hit_wall")
    assert snd.played == 1, "a cooldown must fold a burst into one sound"
    # past the cooldown the next play is allowed
    sfx._last_play["hit_wall"] = time.monotonic() - 100.0
    sfx.play("hit_wall")
    assert snd.played == 2

    # concurrent copies of the same sound are capped
    loud = FakeSound()
    loud.channels = sfx.MAX_PER_SOUND
    sfx._bank["hit_enemy"] = loud
    sfx.play("hit_enemy", cooldown=0)
    assert loud.played == 0, "at the channel cap no new copy may start"
    loud.channels = 0
    sfx.play("hit_enemy", cooldown=0)
    assert loud.played == 1
    sfx.reset()
    print("  rate limits      OK")


def test_volume_clamps_and_applies():
    sfx.reset()
    a, b = FakeSound(), FakeSound()
    sfx._bank["hurt"] = a
    sfx._bank["death"] = b
    sfx._ready = True
    sfx.set_volume(2.0)
    assert a.vol == 1.0 and b.vol == 1.0
    sfx.set_volume(-1)
    assert a.vol == 0.0 and b.vol == 0.0
    sfx.set_volume(0.5)
    assert a.vol == 0.5 and b.vol == 0.5
    sfx.set_volume("nonsense")
    assert a.vol == 0.5, "a bad value must be ignored, not applied"
    sfx.reset()
    print("  volume clamp     OK")


class _Stub:
    def __init__(self):
        self._sfx_seen_impacts = set()
        self._sfx_seen_items = {}
        self._sfx_items_ready = False
        self._sfx_seen_doors = set()
        self._sfx_prev_health = None
        self._sfx_prev_state = 0
        self.local_x = 100.0
        self.local_y = 100.0


def test_impact_hook_dedupes_by_id():
    calls = []
    s = _Stub()
    with _Swap((sfx, "play", lambda name, *a, **k: calls.append(name))):
        GameClient._sfx_impacts(s, [{"id": 1, "type": "enemy"}])
        GameClient._sfx_impacts(s, [{"id": 1, "type": "enemy"}])   # same id: silent
        GameClient._sfx_impacts(s, [{"id": 2, "type": "wall"}])
        GameClient._sfx_impacts(s, [])                            # expired
        GameClient._sfx_impacts(s, [{"id": 1, "type": "enemy"}])  # re-used id: sounds
    assert calls == ["hit_enemy", "hit_wall", "hit_enemy"], calls
    print("  impact hook      OK")


def test_item_hook_near_vs_far():
    calls = []
    s = _Stub()
    with _Swap((sfx, "play", lambda name, *a, **k: calls.append(name))):
        # first snapshot only remembers: it must not sound
        GameClient._sfx_items(s, [{"id": 1, "x": 110.0, "y": 100.0}])
        assert calls == [], "the first snapshot must not sound pickups"
        # a near item vanishes -> pickup
        GameClient._sfx_items(s, [])
        assert calls == ["pickup"], calls
        # a far item vanishes -> an ally's pickup, silent
        GameClient._sfx_items(s, [{"id": 2, "x": 500.0, "y": 500.0}])
        GameClient._sfx_items(s, [])
        assert calls == ["pickup"], calls
    print("  item hook        OK")


def test_door_hook_on_new_open():
    calls = []
    s = _Stub()
    with _Swap((sfx, "play", lambda name, *a, **k: calls.append(name))):
        GameClient._sfx_doors(s, [])
        assert calls == []
        GameClient._sfx_doors(s, [{"li": 5, "progress": 128}])
        GameClient._sfx_doors(s, [{"li": 5, "progress": 200}])   # still same door
        assert calls == ["door"], calls
        GameClient._sfx_doors(s, [{"li": 5}, {"li": 7}])         # a second opens
        assert calls == ["door", "door"], calls
        GameClient._sfx_doors(s, [])                             # both close
        GameClient._sfx_doors(s, [{"li": 5, "progress": 10}])    # reopens
        assert calls == ["door", "door", "door"], calls
    print("  door hook        OK")


def test_local_hook_hurt_death_dash():
    calls = []
    s = _Stub()
    with _Swap((sfx, "play", lambda name, *a, **k: calls.append(name))):
        GameClient._sfx_local(s, 100, 0)     # first snapshot: baseline only
        assert calls == []
        GameClient._sfx_local(s, 100, 0)     # unchanged
        assert calls == []
        GameClient._sfx_local(s, 70, 0)      # lost health
        assert calls == ["hurt"], calls
        GameClient._sfx_local(s, 70, 3)      # dash front
        assert calls == ["hurt", "dash"], calls
        GameClient._sfx_local(s, 70, 3)      # held dash: no repeat
        assert calls == ["hurt", "dash"], calls
        GameClient._sfx_local(s, 0, 1)       # death front
        assert calls == ["hurt", "dash", "death"], calls
        GameClient._sfx_local(s, 100, 0)     # respawn: no sound
        assert calls == ["hurt", "dash", "death"], calls
    print("  local hook       OK")


def test_music_exposes_mixer_to_sfx():
    assert hasattr(music, "ready") and hasattr(music, "ensure_mixer")
    assert sfx.shoot_name("ssg") == "shoot_ssg"
    assert sfx.shoot_name("") == "shoot_pistol"
    # the required event sounds all exist
    for name in ("hit_wall", "hit_enemy", "pickup", "hurt", "death", "door",
                 "dash", "shoot_pistol", "shoot_shotgun", "shoot_ssg",
                 "shoot_rifle", "shoot_bfg", "shoot_chainsaw"):
        assert name in sfx.SOUNDS, f"missing sound {name}"
    print("  names / api      OK")


def test_hooks_are_callable_on_a_real_client():
    """A storage attribute must never shadow a hook method (crash on match start)."""
    import tempfile
    from client import settings as settings_mod
    from client.client import GameClient
    with tempfile.TemporaryDirectory() as tmp:
        settings_mod.CONFIG_PATH = os.path.join(tmp, "config.json")
        gc = GameClient("127.0.0.1", 1, "Smoke")
        for name in ("_sfx_impacts", "_sfx_items", "_sfx_doors", "_sfx_local"):
            assert callable(getattr(gc, name)), \
                f"{name} is shadowed by a non-callable attribute"
    print("  hooks callable   OK")


def test_settings_routes_sfx_volume():
    from client.settings import Settings
    seen = []
    with _Swap((sfx, "set_volume", lambda v: seen.append(v))):
        Settings()._apply_audio()
    assert seen, "settings must route sfx_vol through the sfx module"
    print("  settings sfx     OK")


def test_adjust_applies_volume_live():
    """Changing the row on the settings screen applies audio right away."""
    from client.settings import Settings
    from client import music
    applied = []
    with _Swap((sfx, "set_volume", lambda v: applied.append(("sfx", v))),
               (music, "set_volume", lambda v: applied.append(("music", v)))):
        st = Settings()
        st.adjust("sfx_vol", +1)
        st.adjust("music_vol", -1)
    assert ("sfx", st.get("sfx_vol")) in applied, applied
    assert ("music", st.get("music_vol")) in applied, applied
    print("  live volume      OK")


def main():
    pygame.init()
    test_mixer_absent_is_silent()
    test_render_buffer_matches_mixer_format()
    test_bank_builds_without_second_init()
    test_ensure_asks_music_when_not_ready()
    test_cooldown_and_channel_limit()
    test_volume_clamps_and_applies()
    test_impact_hook_dedupes_by_id()
    test_item_hook_near_vs_far()
    test_door_hook_on_new_open()
    test_local_hook_hurt_death_dash()
    test_music_exposes_mixer_to_sfx()
    test_hooks_are_callable_on_a_real_client()
    test_settings_routes_sfx_volume()
    test_adjust_applies_volume_live()
    print("smoke_sfx: ALL OK")


if __name__ == "__main__":
    main()
