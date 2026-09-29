"""Client game settings: values, persistence and runtime application.

Kept deliberately small and dependency-free so it can be imported from any
screen.  ``SPEC`` is the single source of truth for the settings UI (label,
range, step) and ``apply_runtime`` pushes the values into the input handler and
the ported renderer.
"""
from __future__ import annotations

import json
import math
import os
import sys


def _project_root() -> str:
    """Project root even when running from a .pyz (it is a file, not a dir)."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if base and os.path.isdir(base):
        return base
    return os.getcwd()


CONFIG_PATH = os.path.join(_project_root(), "config.json")

DEFAULTS: dict = {
    "mouse_sensitivity": 0.0035,
    "control_mode": "Мышь",
    "turn_speed": 2.4,
    "turn_accel": 8.0,
    "window_size": "1000\u00d7700",
    "fov": 90,
    "view_dist": 900,
    "render_scale": 4,
    "vs_scale": 2,
    "quality_tier": 0,
    "show_minimap": True,
    "show_crosshair": True,
    "fps_cap": 60,
    "sfx_vol": 0.8,
    "music_vol": 0.4,
    "server_host": "localhost",
    "server_port": 7777,
    "player_name": "Player",
    "skipped_update_version": "",
}

# Window presets; the arrows cycle them. 4K (3840×2160) is a preset, not the
# ceiling -- the settings screen also shows a ``Ш×В`` field where any width and
# height the desktop allows can be typed.
WINDOW_SIZE_PRESETS: list[str] = [
    "640\u00d7480",
    "800\u00d7600",
    "1000\u00d7700",
    "1280\u00d7720",
    "1366\u00d7768",
    "1600\u00d7900",
    "1920\u00d71080",
    "2560\u00d71440",
    "3440\u00d71440",
    "3840\u00d72160",
]

CONTROL_MODES: list[str] = ["Мышь", "Трактор"]

# Smallest usable window. Below this the raycaster loses columns
# (``const.W // SCALE``) and the HUD has no room.
MIN_WINDOW = (320, 200)
# Hard ceiling for a typed size (8K). Bigger is refused with a message rather
# than passed to SDL, which would fail obscurely or allocate a window nobody
# can use. The desktop size is deliberately NOT the bound: a fullscreen-window
# taller than the visible work area is still created (and mostly visible) by
# the window manager, and 4K on a 4K panel is a legitimate request.
MAX_WINDOW = (7680, 4320)

# key, label, kind ('float'|'int'|'bool'|'list'), min, max, step
SPEC: list[dict] = [
    {"key": "mouse_sensitivity", "label": "Чувствительность мыши",
     "kind": "float", "min": 0.001, "max": 0.010, "step": 0.0005},
    {"key": "control_mode", "label": "Управление",
     "kind": "list", "values": CONTROL_MODES},
    {"key": "turn_speed", "label": "Скорость поворота (трактор)",
     "kind": "float", "min": 0.5, "max": 6.0, "step": 0.2},
    {"key": "turn_accel", "label": "Разгон поворота (трактор)",
     "kind": "float", "min": 1.0, "max": 24.0, "step": 1.0},
    {"key": "window_size", "label": "Разрешение окна",
     "kind": "list", "values": WINDOW_SIZE_PRESETS, "custom": True},
    {"key": "fov", "label": "Угол обзора (FOV)",
     "kind": "int", "min": 60, "max": 110, "step": 5},
    {"key": "view_dist", "label": "Дальность прорисовки",
     "kind": "int", "min": 400, "max": 1600, "step": 100},
    {"key": "render_scale", "label": "Качество (масштаб лучей)",
     "kind": "int", "min": 2, "max": 8, "step": 1},
    {"key": "vs_scale", "label": "Вертикальное разрешение",
     "kind": "int", "min": 1, "max": 6, "step": 1},
    {"key": "quality_tier", "label": "Графический тир",
     "kind": "int", "min": 0, "max": 3, "step": 1},
    {"key": "show_minimap", "label": "Мини-карта", "kind": "bool"},
    {"key": "show_crosshair", "label": "Прицел", "kind": "bool"},
    {"key": "fps_cap", "label": "Лимит FPS",
     "kind": "int", "min": 30, "max": 240, "step": 30},
    {"key": "sfx_vol", "label": "Громкость звука",
     "kind": "float", "min": 0.0, "max": 1.0, "step": 0.1},
    {"key": "music_vol", "label": "Громкость музыки",
     "kind": "float", "min": 0.0, "max": 1.0, "step": 0.1},
]

SPEC_BY_KEY = {s["key"]: s for s in SPEC}

CONTROL_MODE_MOUSE = 0
CONTROL_MODE_TRACTOR = 1


def control_mode(values) -> int:
    """Stored control mode (label, legacy index or name) -> 0 = Мышь, 1 = Трактор."""
    if isinstance(values, str):
        text = values.strip().lower()
        if text in ("трактор", "tractor") or text == str(CONTROL_MODE_TRACTOR):
            return CONTROL_MODE_TRACTOR
        if text in CONTROL_MODES or text == str(CONTROL_MODE_MOUSE):
            return CONTROL_MODE_MOUSE
        return CONTROL_MODE_MOUSE
    try:
        index = int(values)
    except (TypeError, ValueError):
        return CONTROL_MODE_MOUSE
    return CONTROL_MODE_TRACTOR if index == CONTROL_MODE_TRACTOR else CONTROL_MODE_MOUSE


def control_hints(mode) -> str:
    """Control layout shown in the lobby and in the party-start log."""
    if is_tractor(mode):
        return ("WASD — движение | ←/→ — обзор | Пробел — огонь | "
                "Левый Shift — дэш")
    return "WASD — движение | Мышь — обзор | ЛКМ — огонь | Пробел — дэш"


def is_tractor(values) -> bool:
    return control_mode(values) == CONTROL_MODE_TRACTOR


def _parse_size(text) -> tuple[int, int] | None:
    """``"1600x900"`` / ``"1600×900"`` -> ``(1600, 900)``, else ``None``."""
    if isinstance(text, (tuple, list)) and len(text) == 2:
        try:
            return int(text[0]), int(text[1])
        except (TypeError, ValueError):
            return None
    if not isinstance(text, str):
        return None
    for sep in ("\u00d7", "x", "X", "*"):
        if sep in text:
            left, _, right = text.partition(sep)
            try:
                return int(left.strip()), int(right.strip())
            except ValueError:
                return None
    return None


def size_text(width: int, height: int) -> str:
    return f"{int(width)}\u00d7{int(height)}"


def validate_size(text) -> tuple[tuple[int, int] | None, str]:
    """Parse and range-check a ``Ш×В`` window size -> ``(size, error)``.

    The error text is non-empty exactly when the size is ``None``, so the
    settings screen can refuse a bad value *and* say why instead of clamping it
    to something the player did not ask for.
    """
    parsed = _parse_size(text)
    if parsed is None:
        return None, "Не похоже на «Ш×В», например 1600×900"
    width, height = parsed
    if width < MIN_WINDOW[0] or height < MIN_WINDOW[1]:
        return None, f"Минимум {MIN_WINDOW[0]}×{MIN_WINDOW[1]}"
    if width > MAX_WINDOW[0] or height > MAX_WINDOW[1]:
        return None, f"Максимум {MAX_WINDOW[0]}×{MAX_WINDOW[1]}"
    return (width, height), ""


def _cli_value(args: list[str], flag: str):
    if flag in args:
        idx = args.index(flag)
        if idx + 1 < len(args):
            return args[idx + 1]
    return None


def _has_flag(args: list[str], flag: str) -> bool:
    return flag in args


def launch_config(cli_args=None) -> tuple[str, int, str]:
    """Resolve (host, port, name) from config.json with CLI priority.

    CLI values (--host/--port/--name) override the config only for the
    current launch and are never written back to the file.
    """
    if cli_args is None:
        cli_args = sys.argv[1:]
    cfg = Settings()
    cfg.load()
    host = str(cfg.get("server_host"))
    port = int(cfg.get("server_port"))
    name = str(cfg.get("player_name"))
    cli = list(cli_args)
    cli_host = _cli_value(cli, "--host")
    cli_port = _cli_value(cli, "--port")
    cli_name = _cli_value(cli, "--name")
    if cli_host is not None:
        host = cli_host
    if cli_port is not None:
        port = int(cli_port)
    if cli_name is not None:
        name = cli_name
    return host, port, name


def format_value(key: str, value) -> str:
    if key == "mouse_sensitivity":
        return f"{value * 1000:.1f}"
    if key == "fov":
        return f"{int(value)}°"
    if key in ("sfx_vol", "music_vol"):
        return f"{int(round(value * 100))}%"
    if key == "quality_tier":
        return {0: "Авто", 1: "Хорошая", 2: "Средняя", 3: "Быстрая"}.get(
            int(value), "Авто")
    if key == "control_mode":
        return CONTROL_MODES[control_mode(value)]
    if key == "turn_speed":
        return f"{float(value):.1f} рад/с"

    if key == "turn_accel":
        return f"{float(value):.1f}"
    if key == "window_size":
        text = str(value)
        return text if text in WINDOW_SIZE_PRESETS else f"Другое → {text}"
    if isinstance(value, bool):
        return "ВКЛ" if value else "ВЫКЛ"
    return str(int(value)) if isinstance(value, float) else str(value)


class Settings:
    def __init__(self) -> None:
        self.values: dict = dict(DEFAULTS)
        self.dirty = False
        # Set by ``set_window_size``/``resolution`` when the stored size is
        # unusable; shown in the settings screen instead of a silent clamp.
        self.resolution_error: str = ""

    def get(self, key: str):
        return self.values.get(key, DEFAULTS.get(key))

    def set(self, key: str, value) -> None:
        self.values[key] = value
        self.dirty = True

    def adjust(self, key: str, direction: int) -> None:
        spec = SPEC_BY_KEY.get(key)
        if spec is None:
            return
        if spec["kind"] == "bool":
            self.values[key] = not bool(self.values.get(key))
            self.dirty = True
            return
        if spec["kind"] == "list":
            values = spec.get("values") or []
            if not values:
                return
            current = self.values.get(key)
            try:
                index = int(current)
            except (TypeError, ValueError):
                index = values.index(current) if current in values else 0
            index = max(0, min(len(values) - 1, index + direction))
            self.store_preset(key, index)
            return
        step = spec["step"]
        value = self.values.get(key, DEFAULTS.get(key, 0)) + direction * step
        value = max(spec["min"], min(spec["max"], value))
        if spec["kind"] == "int":
            value = int(round(value))
        else:
            value = round(value, 6)
        self.values[key] = value
        self.dirty = True

    def load(self) -> None:
        if not os.path.exists(CONFIG_PATH):
            return
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return
        for key in DEFAULTS:
            if key in data:
                self.values[key] = data[key]

    def save(self) -> None:
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.values, f, indent=2, ensure_ascii=False)
            self.dirty = False
        except Exception:
            pass

    # ── window size ─────────────────────────────────────────────────
    @property
    def window_size_text(self) -> str:
        return str(self.get("window_size") or DEFAULTS["window_size"])

    def set_window_size(self, text: str) -> tuple[int, int] | None:
        """Apply a typed ``Ш×В`` size. Invalid input is rejected with
        :attr:`resolution_error` and leaves the value untouched, so the
        settings screen can tell the player why nothing happened."""
        parsed, error = validate_size(text)
        self.resolution_error = error
        if parsed is None:
            return None
        self.set("window_size", size_text(*parsed))
        return parsed

    def resolution(self) -> tuple[int, int] | None:
        """Configured window size, or ``None`` if the stored value is unusable."""
        parsed, error = validate_size(self.window_size_text)
        self.resolution_error = error
        return parsed

    def store_preset(self, key: str, index: int) -> None:
        """Store a list-kind setting as the *label* of its entry.

        All ``list`` settings store the entry label, so a value typed by hand
        (window size) simply is not in the list and reads as «Другое → …».
        """
        spec = SPEC_BY_KEY.get(key)
        if spec is None or spec.get("kind") != "list":
            self.set(key, index)
            return
        values = spec.get("values") or []
        if not values:
            self.set(key, index)
            return
        index = max(0, min(len(values) - 1, int(index)))
        value = values[index]
        parsed = _parse_size(value)
        if parsed is not None:
            value = size_text(*parsed)
        self.set(key, value)
        self.resolution_error = ""

    # ── runtime application ─────────────────────────────────────────
    def apply_runtime(self, input_handler=None) -> None:
        if input_handler is not None:
            input_handler.mouse_sensitivity = float(self.get("mouse_sensitivity"))
            input_handler.control_mode = control_mode(self.get("control_mode"))
            input_handler.turn_speed = float(self.get("turn_speed"))
            input_handler.turn_accel = float(self.get("turn_accel"))

        from engine import constants as const
        from engine import rendering
        from engine import game_state as gs

        const.FOV = math.radians(float(self.get("fov")))
        rendering.FOV = const.FOV

        gs.view_dist = int(self.get("view_dist"))
        gs.show_minimap = bool(self.get("show_minimap"))
        gs.show_crosshair = bool(self.get("show_crosshair"))

        scale = int(self.get("render_scale"))
        const.SCALE = scale
        rendering.SCALE = scale
        gs.render_scale = scale

        gs.vs_scale = max(1, min(int(self.get("vs_scale")), const.MAX_ADAPTIVE_VS))

        # Graphic tier (0 = auto; 1/2/3 = manual high/medium/fast lock).
        from engine.quality import TIER_DEFS, tier_order

        tier_val = int(self.get("quality_tier") or 0)
        if tier_val <= 0:
            gs.quality_mode = "auto"
        else:
            tier = tier_order()[tier_val - 1]
            gs.quality_tier = tier
            gs.quality_mode = "manual"
            gs.flat_textured = TIER_DEFS[tier]["flats"] == "textured"

        self._apply_audio()

    def _apply_audio(self) -> None:
        """Apply volumes if an audio mixer happens to be initialised."""
        try:
            import pygame
            if not pygame.mixer.get_init():
                return
            pygame.mixer.music.set_volume(float(self.get("music_vol")))
        except Exception:
            pass


def save_server_settings(host: str, port: int, name: str, cli_args=None) -> None:
    """Persist a successful connect's address/name into ``config.json``.

    Launch overrides (``--host``/``--port``/``--name``) apply only to the
    current run and are never written back, so the file is left untouched when
    a field was supplied on the command line.  ``host``/``port``/``name`` must
    already be normalized (fallbacks ``localhost``/``7777``/``Player``).
    """
    if cli_args is None:
        cli_args = sys.argv[1:]
    cfg = Settings()
    cfg.load()
    if not _has_flag(cli_args, "--host"):
        cfg.set("server_host", str(host))
    if not _has_flag(cli_args, "--port"):
        cfg.set("server_port", int(port))
    if not _has_flag(cli_args, "--name"):
        cfg.set("player_name", str(name))
    if cfg.dirty:
        cfg.save()
