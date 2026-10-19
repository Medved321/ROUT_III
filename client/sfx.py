"""Procedural sound effects for the client.

The client has no sound-effect assets and never will: this module synthesises a
small bank of short sounds in memory at startup and plays them through the
mixer that :mod:`client.music` already owns.  Nothing here imports numpy — only
stdlib ``array``/``math``/``random`` and pygame — so the bank builds on every
machine the client runs on.

Like music, every failure is silence: no mixer, no audio device, a failed
synthesis or a bad sound name must never reach the game loop as an exception.
"""
from __future__ import annotations

import array
import math
import random
import time

import pygame

from client import music

# One sound must not stack into a wall of noise: at most two copies at once,
# and no more often than its own cooldown.
MAX_PER_SOUND = 2
DEFAULT_COOLDOWN = 0.05

# How many mixer channels to keep for effects. Music plays on its own reserved
# stream, so this pool is only for one-shot sounds; opening a few more channels
# than pygame's default keeps a busy fight from swallowing every hit.
CHANNELS = 24

# ── the bank ──────────────────────────────────────────────────────────────
# Each entry is a synthesis recipe, not a file. ``shape`` is the waveform,
# ``f0``->``f1`` an optional frequency sweep, ``noise`` the blend of white
# noise, and the envelope is linear attack/release over the duration.
SOUNDS: dict[str, dict] = {
    # weapons (one timbre per WPN 'snd')
    "shoot_chainsaw": {"dur": 0.16, "f0": 90, "f1": 70, "shape": "saw",
                       "noise": 0.35, "amp": 0.35, "release": 0.06, "seed": 11},
    "shoot_pistol": {"dur": 0.11, "f0": 360, "f1": 150, "shape": "square",
                     "noise": 0.25, "amp": 0.30, "release": 0.05, "seed": 12},
    "shoot_shotgun": {"dur": 0.30, "f0": 200, "f1": 55, "shape": "square",
                      "noise": 0.75, "amp": 0.40, "release": 0.12, "seed": 13},
    "shoot_rifle": {"dur": 0.10, "f0": 460, "f1": 260, "shape": "square",
                    "noise": 0.30, "amp": 0.28, "release": 0.04, "seed": 14},
    "shoot_bfg": {"dur": 0.55, "f0": 60, "f1": 220, "shape": "sine",
                  "noise": 0.20, "amp": 0.40, "release": 0.25, "seed": 15},
    "shoot_ssg": {"dur": 0.34, "f0": 150, "f1": 40, "shape": "square",
                  "noise": 0.80, "amp": 0.45, "release": 0.14, "seed": 16},
    # world feedback
    "hit_wall": {"dur": 0.07, "f0": 900, "f1": 300, "shape": "square",
                 "noise": 0.75, "amp": 0.22, "release": 0.03, "seed": 21},
    "hit_enemy": {"dur": 0.10, "f0": 220, "f1": 90, "shape": "square",
                  "noise": 0.45, "amp": 0.30, "release": 0.05, "seed": 22},
    "pickup": {"dur": 0.16, "f0": 520, "f1": 1040, "shape": "sine",
               "noise": 0.0, "amp": 0.30, "release": 0.07, "seed": 23},
    "hurt": {"dur": 0.20, "f0": 300, "f1": 120, "shape": "saw",
             "noise": 0.30, "amp": 0.35, "release": 0.10, "seed": 24},
    "death": {"dur": 0.60, "f0": 240, "f1": 45, "shape": "saw",
              "noise": 0.35, "amp": 0.40, "release": 0.30, "seed": 25},
    "door": {"dur": 0.45, "f0": 120, "f1": 180, "shape": "saw",
             "noise": 0.40, "amp": 0.30, "release": 0.18, "seed": 26},
    "dash": {"dur": 0.18, "f0": 700, "f1": 200, "shape": "sine",
             "noise": 0.65, "amp": 0.25, "release": 0.09, "seed": 27},
}

# Per-sound minimum interval between plays, so repeat events (a burst of hits)
# fold into one sound instead of a machine-gun rattle.
COOLDOWNS: dict[str, float] = {
    "hit_wall": 0.03,
    "hit_enemy": 0.03,
    "hurt": 0.20,
    "death": 0.50,
    "door": 0.10,
    "dash": 0.25,
    "pickup": 0.08,
}

_bank: dict[str, "pygame.mixer.Sound"] = {}
_last_play: dict[str, float] = {}
_volume = 0.8
_ready: bool | None = None


def _wave(shape: str, phase: float) -> float:
    if shape == "square":
        return 1.0 if math.sin(phase) >= 0.0 else -1.0
    if shape == "saw":
        frac = (phase / (2.0 * math.pi)) % 1.0
        return 2.0 * frac - 1.0
    return math.sin(phase)


def render_buffer(params: dict, freq: int, channels: int) -> bytes:
    """Synthesise one sound into the mixer's exact sample format.

    The buffer is interleaved 16-bit signed samples, ``len == frames * channels
    * 2``; it is taken straight to ``pygame.mixer.Sound(buffer=)``, so it must
    match the *actual* mixer rate and channel count, not ours.
    """
    freq = max(1, int(freq))
    channels = max(1, int(channels))
    n = max(1, int(freq * float(params.get("dur", 0.1))))
    f0 = float(params.get("f0", 440.0))
    f1 = params.get("f1")
    f1 = f0 if f1 is None else float(f1)
    noise = max(0.0, min(1.0, float(params.get("noise", 0.0))))
    shape = params.get("shape", "sine")
    attack = max(0.0, float(params.get("attack", 0.005)))
    release = max(0.0, float(params.get("release", 0.05)))
    amp = max(0.0, min(1.0, float(params.get("amp", 0.35))))
    rng = random.Random(params.get("seed", 0))

    step = 2.0 * math.pi / freq
    phase = 0.0
    samples = array.array("h")
    for i in range(n):
        t = i / max(1, n - 1)
        f = f0 + (f1 - f0) * t
        phase += f * step
        value = (1.0 - noise) * _wave(shape, phase) + noise * rng.uniform(-1.0, 1.0)
        env = 1.0
        if attack > 0.0 and t < attack:
            env = t / attack
        if release > 0.0 and t > 1.0 - release:
            env = min(env, (1.0 - t) / release)
        if env < 0.0:
            env = 0.0
        value = max(-1.0, min(1.0, value * amp * env))
        sample = int(value * 32767.0)
        for _ in range(channels):
            samples.append(sample)
    return samples.tobytes()


def _mixer_params():
    try:
        init = pygame.mixer.get_init()
    except Exception:
        return None
    if not init:
        return None
    return init[0], init[1], init[2]


def ensure() -> bool:
    """Make sure the shared mixer is up and the bank is built. Idempotent.

    The mixer belongs to :mod:`client.music`; this only asks it, never calls
    ``pygame.mixer.init`` itself, so there is exactly one init owner.
    """
    global _ready
    if _ready is not None:
        return _ready
    try:
        if not music.ready():
            music.ensure_mixer()
        _ready = bool(music.ready())
    except Exception:
        _ready = False
    if _ready:
        _build_bank()
    return _ready


def _build_bank() -> dict:
    if _bank:
        return _bank
    params = _mixer_params()
    if params is None:
        return _bank
    freq, size, channels = params
    if size not in (-16, 16):
        return _bank
    try:
        pygame.mixer.set_num_channels(max(CHANNELS, pygame.mixer.get_num_channels()))
    except Exception:
        pass
    for name, recipe in SOUNDS.items():
        try:
            buf = render_buffer(recipe, freq, channels)
            _bank[name] = pygame.mixer.Sound(buffer=buf)
        except Exception:
            continue
    _apply_volume()
    return _bank


def _apply_volume() -> None:
    for snd in _bank.values():
        try:
            snd.set_volume(_volume)
        except Exception:
            pass


def play(name: str, cooldown: float | None = None) -> None:
    """Play one effect, rate-limited. Unknown names and failures are silent."""
    try:
        if not ensure() or name not in _bank:
            return
        snd = _bank[name]
        now = time.monotonic()
        wait = COOLDOWNS.get(name, DEFAULT_COOLDOWN) if cooldown is None else cooldown
        if now - _last_play.get(name, -1e9) < wait:
            return
        try:
            if snd.get_num_channels() >= MAX_PER_SOUND:
                return
        except Exception:
            pass
        _last_play[name] = now
        snd.play()
    except Exception:
        pass


def set_volume(value) -> None:
    """Clamp to 0..1 and apply to every built sound; 0 is silence."""
    global _volume
    try:
        value = float(value)
    except (TypeError, ValueError):
        return
    _volume = max(0.0, min(1.0, value))
    _apply_volume()


def volume() -> float:
    return _volume


def shoot_name(snd: str) -> str:
    """Bank name for a weapon's ``snd`` suffix (WPN[i]['snd'])."""
    return "shoot_" + (snd or "pistol")


def reset() -> None:
    """Drop all built state (tests only)."""
    global _ready
    _bank.clear()
    _last_play.clear()
    _ready = None
