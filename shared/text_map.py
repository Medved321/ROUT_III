"""ROUT III sector map text format ``.mts`` (spec ``map/text-format``).

A human readable source for sector maps: one command per line, Russian
keywords, rooms described by their corners and no manual entity numbering --
vertices, linedefs, sidedefs and sectors are built by the parser itself.  Two
rooms that share a pair of corners automatically get a two-sided line (a
portal), exactly like in Doom.

Grammar (case-insensitive keywords, ``#`` starts a comment)::

    # формат: ROUT III .mts v1
    КОМНАТА <имя>                # начать комнату (сектор)
    угол <x> <y>                 # угол комнаты, контур замыкается сам
    СТЕНА <x1> <y1> <x2> <y2>   # явная грань: дверь, рычаг, стена внутри
    ИГРОК <x> <y>                # точка спавна игрока
    ВРАГ <x> <y> <тип>          # враг из ENEMY_TYPES
    АПТЕЧКА <x> <y>             # предмет med
    ПАТРОНЫ <x> <y>             # предмет amm
    ВЕЩЬ <x> <y> <тип>          # любой другой тип вещи

    параметры комнаты: пол N, потолок N, свет N, пол_текстура ИМЯ,
        потол_текстура ИМЯ, стена ИМЯ, текстура ИМЯ, текстура_верх ИМЯ,
        текстура_низ ИМЯ, тег N, триггер тип ТИП [тег N]
    параметры СТЕНА: текстура ИМЯ, стена ИМЯ, текстура_верх ИМЯ,
        текстура_низ ИМЯ, тег N, триггер тип ТИП [тег N]
    параметры вещи: направление N, высота Z

Contours of three corners must be closed explicitly (repeat the first corner
at the end); four or more corners close automatically.  ``special``/``tag`` --
levers, doors, lifts -- travel through ``.mt1`` as plain model data, but the
server-side behaviour of those mechanics is a reserve of a future change; the
same holds for everything a map places except ``ИГРОК`` (the spawn the server
really uses).  The mechanic words of ``триггер тип ...`` are ``SPECIAL_NAMES``
(``нет``, ``рычаг``, ``дверь``, ``выход``, ``платформа``, ``лифт``, ``шипы``,
``мост``, ``телепорт``, ``секрет``); a raw number is accepted as well.
Textures and thing types are the ones the engine knows
(``engine/textures.py`` / ``client/map_editor.py``), mirrored here so this
module stays stdlib-only.
"""
from __future__ import annotations

import math
from pathlib import Path

from shared.sector_map import (
    DOOR_TEXTURE_BY_LOCK, DOOR_TEXTURE_NONE, KEY_LOCK_BY_ITEM, KEY_TYPES,
    SPECIAL_DOORS,
    LineDef, Sector, SectorMap, SideDef, Thing, Vertex,
)

MTS_HEADER = "# формат: ROUT III .mts v1"

# Texture names (mirror of engine/textures.py, kept stdlib-only here).
WALL_TEXTURE_ORDER = (
    "wall", "stone", "wood", "meat", "infected_stone", "martyr", "hwall",
    "switch", "bridge", "platform", "pentagram", "barrel", "door_D",
    "door_R", "door_Y", "door_B", "elevator", "exit", "spikes",
)
WALL_TEXTURES = frozenset(WALL_TEXTURE_ORDER)
FLOOR_TEXTURES = frozenset(("floor", "lava"))
CEIL_TEXTURES = frozenset(("ceiling",))
TEXTURE_NAMES = WALL_TEXTURES | FLOOR_TEXTURES | CEIL_TEXTURES

# Line/sector mechanic codes (mirror of nothing yet: the server does not run
# them, the numbers only travel through .mt1 -- reserve of a future change).
SPECIAL_NAMES = {
    "нет": 0,
    "рычаг": 1,
    "дверь": 2,
    "выход": 3,
    "платформа": 4,
    "лифт": 5,
    "шипы": 6,
    "мост": 7,
    "телепорт": 8,
    "секрет": 9,
}
SPECIAL_CODES = {code: name for name, code in SPECIAL_NAMES.items()}

# Thing types (mirror of ENEMY_NAMES / ITEM_NAMES in client/map_editor.py).
ENEMY_TYPES = {
    "солдат": "soldier",
    "дробовик": "shotgunguy",
    "архивиль": "archvile",
    "какодемон": "cacodemon",
    "манкубус": "mancubus",
    "собака": "dog",
    "розовая": "pinkie",
    "барон": "boss_baron",
    "кибердемон": "boss_cyber",
    "паук": "boss_spider",
    "пулеметчик": "chaingunner",
    "имп": "imp",
    "ревенант": "revenant",
    "бес": "pain_elemental",
    "душа": "lost_soul",
    "рыцарь": "hell_knight",
    "арахнотрон": "arachnotron",
    "спектр": "spectre",
}
ITEM_TYPES = {
    "аптечка": "med",
    "патроны": "amm",
    "ярость": "berserk",
    "спешка": "haste",
    "неуязвимость": "invuln",
    "пистолет": "wpn_0",
    "двустволка": "wpn_2",
    "ракетница": "wpn_3",
    "плазмоган": "wpn_4",
    "bfg": "wpn_5",
    "сердце": "heart",
    "броня_зеленая": "armor_green",
    "броня_синяя": "armor_blue",
    "ключ_красный": "key_red",
    "ключ_желтый": "key_yellow",
    "ключ_синий": "key_blue",
}
EXTRA_TYPES = {"выход": "exit"}
THING_TYPES = {**ITEM_TYPES, **EXTRA_TYPES}
ENEMY_BY_TYPE = {t: n for n, t in ENEMY_TYPES.items()}
THING_BY_TYPE = {t: n for n, t in THING_TYPES.items()}

# Door/key vocabulary. ''замок'' and ''цвет'' use colour words; ''нет'' = no
# key. ``ё`` is normalised to ``е`` so both spellings are accepted.
_KEY_COLORS = {
    "нет": None,
    "красный": "red", "красная": "red", "красное": "red",
    "желтый": "yellow",
    "синий": "blue", "синяя": "blue", "синее": "blue",
}
_KEY_COLOR_WORD = {None: "нет", "red": "красный",
                   "yellow": "жёлтый", "blue": "синий"}

MAX_HEIGHT = 32767
MIN_HEIGHT = -32768
MAX_TAG = 65535
MIN_LIGHT = 0
MAX_LIGHT = 255
_PROBE = 2.0            # px, how far a wall sample point sits off the line
_AREA_EPS = 1e-6

_MODEL_ERRORS = {
    "map has no sectors": "в файле нет ни одной КОМНАТЫ",
    "map has no player1 spawn": "на карте нет ИГРОК (точка спавна обязательна)",
}


class TextMapError(ValueError):
    """Понятная ошибка разбора ``.mts`` с номером строки."""

    def __init__(self, line: int, message: str):
        self.line = int(line)
        self.message = message
        super().__init__(f"строка {self.line}: {message}")


# ── helpers ───────────────────────────────────────────────────────────────

def _fmt(value: float) -> str:
    """Canonical number text: integers stay integral, halves allowed."""
    v = float(value)
    if v.is_integer():
        return str(int(v))
    return f"{round(v * 2) / 2:g}"


def _num(tok: str, line: int, what: str) -> float:
    try:
        value = float(tok)
    except ValueError:
        raise TextMapError(line, f"{what}: «{tok}» не число") from None
    if not math.isfinite(value):
        raise TextMapError(line, f"{what}: «{tok}» не число")
    return value


def _int(tok: str, line: int, what: str, lo: int, hi: int,
         label: str) -> int:
    value = _num(tok, line, what)
    if value != int(value):
        raise TextMapError(line, f"{what}: {label} должен быть целым")
    ivalue = int(value)
    if not lo <= ivalue <= hi:
        raise TextMapError(line, f"{what}: {label} вне {lo}..{hi} ({ivalue})")
    return ivalue


def _coord(tok: str, line: int, what: str) -> float:
    return _num(tok, line, what)


def _area(points: list[tuple[float, float]]) -> float:
    total = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return total / 2.0


def _texture(tok: str, line: int, what: str, role: str) -> str:
    name = tok.strip()
    pool = WALL_TEXTURES if role == "стены" else TEXTURE_NAMES
    if name not in pool:
        raise TextMapError(
            line, f"{what}: неизвестная текстура {role} «{name}»")
    return name


def _special(word: str, line: int, what: str) -> int:
    code = SPECIAL_NAMES.get(word)
    if code is not None:
        return code
    try:
        value = int(word)
    except ValueError:
        value = None
    if value is not None and 0 <= value <= MAX_TAG:
        return value
    known = ", ".join(SPECIAL_NAMES)
    raise TextMapError(
        line, f"{what}: неизвестный тип механики «{word}» (доступно: {known})")


def _special_name(code: int) -> str:
    return SPECIAL_CODES.get(int(code), str(int(code)))


# ── document model (pre-geometry) ─────────────────────────────────────────

class _Room:
    __slots__ = ("line", "name", "contours", "contour_lines", "floor_h",
                 "ceil_h", "floor_tex", "ceil_tex", "light", "upper", "mid",
                 "lower", "special", "tag")

    def __init__(self, line: int, name: str):
        self.line = line
        self.name = name
        # A room starts with one contour; ``контур`` opens extra ones (holes /
        # islands of the same sector).  A plain room never grows past one.
        self.contours: list[list[tuple[float, float]]] = [[]]
        self.contour_lines: list[list[int]] = [[]]
        self.floor_h = 0
        self.ceil_h = 128
        self.floor_tex = "floor"
        self.ceil_tex = "ceiling"
        self.light = 160
        self.upper: str | None = None
        self.mid: str | None = None
        self.lower: str | None = None
        self.special = 0
        self.tag = 0

    def add_corner(self, point: tuple[float, float], line: int) -> None:
        self.contours[-1].append(point)
        self.contour_lines[-1].append(line)

    def new_contour(self) -> None:
        self.contours.append([])
        self.contour_lines.append([])


class _Wall:
    __slots__ = ("line", "x1", "y1", "x2", "y2", "upper", "mid", "lower",
                 "special", "tag", "solid")

    def __init__(self, line: int, x1: float, y1: float, x2: float, y2: float):
        self.line = line
        self.x1 = x1
        self.y1 = y1
        self.x2 = x2
        self.y2 = y2
        self.upper: str | None = None
        self.mid: str | None = None
        self.lower: str | None = None
        self.special = 0
        self.tag = 0
        self.solid = False


class _Thing:
    __slots__ = ("line", "x", "y", "type", "angle", "z")

    def __init__(self, line: int, x: float, y: float, typ: str):
        self.line = line
        self.x = x
        self.y = y
        self.type = typ
        self.angle = 0
        self.z = 0.0


class _Document:
    def __init__(self):
        self.rooms: list[_Room] = []
        self.walls: list[_Wall] = []
        self.things: list[_Thing] = []
        self.last_line = 0


# ── parsing ───────────────────────────────────────────────────────────────

_ROOM_PARAM_HELP = "пол, потолок, свет, пол_текстура, потол_текстура, " \
                   "стена, текстура, текстура_верх, текстура_низ, тег, триггер"
_WALL_PARAM_HELP = "текстура, стена, текстура_верх, текстура_низ, тег, " \
                   "триггер, сплошная, замок"
_THING_PARAM_HELP = "направление, высота"

_KEYWORDS = frozenset((
    "комната", "угол", "контур", "стена", "дверь", "ключ", "текстура",
    "игрок", "враг",
    "аптечка", "патроны", "вещь", "пол", "потолок", "свет", "пол_текстура",
    "потол_текстура", "текстура_верх", "текстура_низ", "тег", "триггер",
    "направление", "высота", "сплошная", "замок",
))

_KEY_COLOR_KNOWN = "красный, жёлтый, синий, нет"


def _key_color(tok: str, line: int, what: str) -> str | None:
    key = tok.strip().lower().replace("ё", "е")
    if key not in _KEY_COLORS:
        raise TextMapError(
            line, f"{what}: неизвестный цвет ключа «{tok}» "
                  f"(доступно: {_KEY_COLOR_KNOWN})")
    return _KEY_COLORS[key]


def _parse_document(text: str) -> _Document:
    doc = _Document()
    current: object | None = None
    for lineno, raw in enumerate(text.splitlines(), 1):
        doc.last_line = lineno
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        kw = parts[0].lower()
        args = parts[1:]
        what = f"команда «{parts[0]}»"

        if kw == "комната":
            name = " ".join(args) or f"сектор {len(doc.rooms) + 1}"
            current = _Room(lineno, name)
            doc.rooms.append(current)
            continue
        if kw == "угол":
            if not isinstance(current, _Room):
                raise TextMapError(lineno, f"{what} без КОМНАТЫ")
            if len(args) != 2:
                raise TextMapError(lineno, f"{what} требует 2 числа (x y)")
            point = (_coord(args[0], lineno, what), _coord(args[1], lineno, what))
            current.add_corner(point, lineno)
            continue
        if kw == "контур":
            if not isinstance(current, _Room):
                raise TextMapError(lineno, f"{what} без КОМНАТЫ")
            if args:
                raise TextMapError(lineno, f"{what} не принимает значений")
            if len(current.contours[-1]) < 3:
                raise TextMapError(
                    lineno, f"{what}: у комнаты «{current.name}» нет "
                            f"замкнутого контура перед новым «{parts[0]}»")
            current.new_contour()
            continue
        if kw == "стена" and len(args) == 4:
            coords = [_coord(a, lineno, what) for a in args]
            current = _Wall(lineno, *coords)
            doc.walls.append(current)
            continue
        if kw == "игрок":
            current = _thing(doc, lineno, args, "player1", what)
            continue
        if kw == "враг":
            current = _enemy(doc, lineno, args, what)
            continue
        if kw in ("аптечка", "патроны"):
            current = _thing(doc, lineno, args,
                             ITEM_TYPES["аптечка" if kw == "аптечка" else "патроны"],
                             what)
            continue
        if kw == "вещь":
            current = _item(doc, lineno, args, what)
            continue
        if kw == "дверь":
            if len(args) not in (4, 5):
                raise TextMapError(
                    lineno, f"{what} требует 4 числа (x1 y1 x2 y2) и "
                            f"необязательный цвет замка")
            coords = [_coord(a, lineno, what) for a in args[:4]]
            wall = _Wall(lineno, *coords)
            lock = _key_color(args[4], lineno, what) if len(args) == 5 else None
            wall.special = SPECIAL_DOORS
            wall.mid = DOOR_TEXTURE_BY_LOCK.get(lock, DOOR_TEXTURE_NONE)
            doc.walls.append(wall)
            current = wall
            continue
        if kw == "ключ":
            if len(args) != 3:
                raise TextMapError(lineno, f"{what} требует 3 значения (x y цвет)")
            x = _coord(args[0], lineno, what)
            y = _coord(args[1], lineno, what)
            color = _key_color(args[2], lineno, what)
            if color is None:
                raise TextMapError(
                    lineno, f"{what}: «нет» — недопустимый цвет ключа "
                            f"(доступно: {_KEY_COLOR_KNOWN})")
            current = _Thing(lineno, x, y, KEY_TYPES[color])
            doc.things.append(current)
            continue

        if kw not in _KEYWORDS:
            raise TextMapError(lineno, f"неизвестная команда «{parts[0]}»")

        # --- parameters of the last entity ---
        if current is None:
            raise TextMapError(lineno, f"{what} без КОМНАТЫ, СТЕНЫ или вещи")
        if isinstance(current, _Room):
            _room_param(current, kw, args, lineno, what)
        elif isinstance(current, _Wall):
            _wall_param(current, kw, args, lineno, what)
        else:
            _thing_param(current, kw, args, lineno, what)
    if not doc.rooms and not doc.walls:
        raise TextMapError(max(doc.last_line, 1), "в файле нет ни одной КОМНАТЫ")
    return doc


def _thing(doc: _Document, lineno: int, args: list[str], typ: str,
           what: str) -> _Thing:
    if len(args) != 2:
        raise TextMapError(lineno, f"{what} требует 2 числа (x y)")
    thing = _Thing(lineno, _coord(args[0], lineno, what),
                   _coord(args[1], lineno, what), typ)
    doc.things.append(thing)
    return thing


def _enemy(doc: _Document, lineno: int, args: list[str], what: str) -> _Thing:
    if len(args) != 3:
        raise TextMapError(lineno, f"{what} требует 3 значения (x y тип)")
    x = _coord(args[0], lineno, what)
    y = _coord(args[1], lineno, what)
    name = args[2]
    key = name.lower()
    if key in ENEMY_TYPES:
        type_name = ENEMY_TYPES[key]
    elif name in ENEMY_BY_TYPE.values():
        type_name = name              # already the engine's type name
    else:
        raise TextMapError(
            lineno, f"{what}: неизвестный тип врага «{name}»"
                    f" (доступно: {', '.join(ENEMY_TYPES)})")
    thing = _Thing(lineno, x, y, type_name)
    doc.things.append(thing)
    return thing


def _item(doc: _Document, lineno: int, args: list[str], what: str) -> _Thing:
    if len(args) != 3:
        raise TextMapError(lineno, f"{what} требует 3 значения (x y тип)")
    x = _coord(args[0], lineno, what)
    y = _coord(args[1], lineno, what)
    thing = _Thing(lineno, x, y, THING_TYPES.get(args[2].lower(), args[2]))
    doc.things.append(thing)
    return thing


def _room_param(room: _Room, kw: str, args: list[str], lineno: int,
                what: str) -> None:
    if kw == "пол":
        room.floor_h = _int(_arg(args, lineno, what, "пол"), lineno, what,
                            MIN_HEIGHT, MAX_HEIGHT, "пол")
    elif kw == "потолок":
        room.ceil_h = _int(_arg(args, lineno, what, "потолок"), lineno, what,
                           MIN_HEIGHT, MAX_HEIGHT, "потолок")
    elif kw == "свет":
        room.light = _int(_arg(args, lineno, what, "свет"), lineno, what,
                          MIN_LIGHT, MAX_LIGHT, "свет")
    elif kw == "пол_текстура":
        room.floor_tex = _texture(_arg(args, lineno, what, "пол_текстура"),
                                  lineno, what, "пола")
    elif kw == "потол_текстура":
        room.ceil_tex = _texture(_arg(args, lineno, what, "потол_текстура"),
                                 lineno, what, "потолка")
    elif kw in ("стена", "текстура"):
        room.mid = _texture(_arg(args, lineno, what, kw), lineno, what, "стены")
    elif kw == "текстура_верх":
        room.upper = _texture(_arg(args, lineno, what, kw), lineno, what, "стены")
    elif kw == "текстура_низ":
        room.lower = _texture(_arg(args, lineno, what, kw), lineno, what, "стены")
    elif kw == "тег":
        room.tag = _int(_arg(args, lineno, what, "тег"), lineno, what,
                        0, MAX_TAG, "тег")
    elif kw == "триггер":
        room.special, room.tag = _trigger(args, lineno, what, room.tag)
    else:
        raise TextMapError(lineno, f"{what} не подходит к КОМНАТЕ "
                                   f"(допустимо: {_ROOM_PARAM_HELP})")


def _wall_param(wall: _Wall, kw: str, args: list[str], lineno: int,
                what: str) -> None:
    if kw in ("стена", "текстура"):
        wall.mid = _texture(_arg(args, lineno, what, kw), lineno, what, "стены")
    elif kw == "текстура_верх":
        wall.upper = _texture(_arg(args, lineno, what, kw), lineno, what, "стены")
    elif kw == "текстура_низ":
        wall.lower = _texture(_arg(args, lineno, what, kw), lineno, what, "стены")
    elif kw == "тег":
        wall.tag = _int(_arg(args, lineno, what, "тег"), lineno, what,
                        0, MAX_TAG, "тег")
    elif kw == "триггер":
        wall.special, wall.tag = _trigger(args, lineno, what, wall.tag)
    elif kw == "сплошная":
        if args:
            raise TextMapError(lineno, f"{what}: «сплошная» не принимает "
                                       f"значений")
        wall.solid = True
    elif kw == "замок":
        lock = _key_color(_arg(args, lineno, what, "замок"), lineno, what)
        wall.special = SPECIAL_DOORS
        wall.mid = DOOR_TEXTURE_BY_LOCK.get(lock, DOOR_TEXTURE_NONE)
    else:
        raise TextMapError(lineno, f"{what} не подходит к СТЕНЕ "
                                   f"(допустимо: {_WALL_PARAM_HELP})")


def _thing_param(thing: _Thing, kw: str, args: list[str], lineno: int,
                 what: str) -> None:
    if kw == "направление":
        thing.angle = _int(_arg(args, lineno, what, "направление"), lineno, what,
                           0, 359, "направление")
    elif kw == "высота":
        thing.z = _coord(_arg(args, lineno, what, "высота"), lineno, what)
    else:
        raise TextMapError(lineno, f"{what} не подходит к вещи "
                                   f"(допустимо: {_THING_PARAM_HELP})")


def _arg(args: list[str], lineno: int, what: str, label: str) -> str:
    if not args:
        raise TextMapError(lineno, f"{what}: нужно значение «{label}»")
    return args[0]


def _trigger(args: list[str], lineno: int, what: str,
             tag: int) -> tuple[int, int]:
    """``триггер тип <ТИП> [тег N]`` (the ``тип`` word is optional)."""
    rest = args
    if rest and rest[0].lower() == "тип":
        rest = rest[1:]
    if not rest:
        raise TextMapError(lineno, f"{what} требует тип механики "
                                   f"(например «{what} тип рычаг тег 2»)")
    special = _special(rest[0], lineno, what)
    if len(rest) >= 3 and rest[1].lower() == "тег":
        tag = _int(rest[2], lineno, what, 0, MAX_TAG, "тег")
    elif len(rest) >= 2 and rest[1].lower() != "тег":
        raise TextMapError(lineno, f"{what}: лишний аргумент «{rest[1]}»")
    return special, tag


# ── geometry build ────────────────────────────────────────────────────────

class _Builder:
    def __init__(self):
        self.sm = SectorMap()
        self._verts: dict[tuple[float, float], int] = {}
        self._lines: dict[tuple[int, int], int] = {}
        self._names: list[str] = []

    def vertex(self, x: float, y: float) -> int:
        key = (float(x), float(y))
        idx = self._verts.get(key)
        if idx is None:
            idx = len(self.sm.verts)
            self.sm.verts.append(Vertex(key[0], key[1]))
            self._verts[key] = idx
        return idx

    def side(self, upper: str | None, mid: str | None, lower: str | None,
             sector: int) -> int:
        self.sm.sidedefs.append(SideDef(upper or "-", mid or "wall",
                                        lower or "-", sector=sector))
        return len(self.sm.sidedefs) - 1

    def paint(self, side: int, wall: _Wall) -> None:
        if side < 0 or side >= len(self.sm.sidedefs):
            return
        sd = self.sm.sidedefs[side]
        if wall.mid:
            sd.mid = wall.mid
        if wall.upper:
            sd.upper = wall.upper
        if wall.lower:
            sd.lower = wall.lower

    def build(self, doc: _Document) -> SectorMap:
        for room in doc.rooms:
            self._room(room)
        for wall in doc.walls:
            self._wall(wall)
        for thing in doc.things:
            self.sm.things.append(Thing(thing.x, thing.y, thing.type,
                                        angle=thing.angle, z=thing.z))
        self.sm.invalidate()
        return self.sm

    def _contour(self, room: _Room, ci: int) -> list[tuple[float, float]]:
        points = list(room.contours[ci])
        closed = False
        if len(points) >= 2 and points[-1] == points[0]:
            points.pop()
            closed = True
        lines = room.contour_lines[ci][:len(points)]
        line = lines[-1] if lines else room.line
        seen: set[tuple[float, float]] = set()
        for point, ln in zip(points, lines):
            if point in seen:
                raise TextMapError(
                    ln, f"угол ({_fmt(point[0])}, {_fmt(point[1])}) "
                       f"дублируется в комнате «{room.name}»")
            seen.add(point)
        subject = (f"комната «{room.name}»" if ci == 0
                   else f"контур комнаты «{room.name}»")
        if len(points) < 3:
            raise TextMapError(
                line, f"{subject} не замкнута "
                      f"(мало углов: {len(points)})")
        if len(points) == 3 and not closed:
            raise TextMapError(
                line, f"{subject} не замкнута "
                      f"(повторите первый угол в конце)")
        if abs(_area(points)) < _AREA_EPS:
            raise TextMapError(
                line, f"{subject} не замкнута (вырожденный контур)")
        return points

    def _room(self, room: _Room) -> None:
        contours = [self._contour(room, ci)
                    for ci in range(len(room.contours))]
        si = len(self.sm.sectors)
        self._names.append(room.name)
        self.sm.sectors.append(Sector(
            floor_h=room.floor_h, ceil_h=room.ceil_h,
            floor_tex=room.floor_tex, ceil_tex=room.ceil_tex,
            light=room.light, special=room.special, tag=room.tag,
        ))
        # The first contour is the room's own boundary; later ones are holes
        # (or islands) of the same sector, so their front -- the sector's own
        # material -- faces away from the loop's centroid, not towards it.
        for ci, points in enumerate(contours):
            self._add_loop(points, room, si, ci, hole=ci > 0)

    def _add_loop(self, points: list[tuple[float, float]], room: _Room,
                  si: int, ci: int, *, hole: bool) -> None:
        cx = sum(p[0] for p in points) / len(points)
        cy = sum(p[1] for p in points) / len(points)
        ids = [self.vertex(x, y) for x, y in points]
        lines = room.contour_lines[ci]
        n = len(ids)
        for i in range(n):
            j = (i + 1) % n
            (ax, ay), (bx, by) = points[i], points[j]
            cross = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
            keep = cross > 0 if hole else cross < 0
            v1, v2 = (ids[i], ids[j]) if keep else (ids[j], ids[i])
            key = (v1, v2) if v1 <= v2 else (v2, v1)
            li = self._lines.get(key)
            if li is None:
                side = self.side(room.upper, room.mid, room.lower, si)
                self.sm.linedefs.append(LineDef(v1, v2, front=side))
                self._lines[key] = len(self.sm.linedefs) - 1
                continue
            ld = self.sm.linedefs[li]
            where = lines[i] if i < len(lines) else room.line
            if (v1, v2) == (ld.v1, ld.v2):
                other = self._names[self.sm.sidedefs[ld.front].sector]
                if self.sm.sidedefs[ld.front].sector == si:
                    continue        # the same room, the same face: ignore
                raise TextMapError(
                    where,
                    f"комнаты «{other}» и «{room.name}» лежат по одну "
                    f"сторону общей стены")
            if ld.back >= 0:
                if self.sm.sidedefs[ld.back].sector == si:
                    continue
                other = self._names[self.sm.sidedefs[ld.back].sector]
                raise TextMapError(
                    where,
                    f"грань «{other}»/«{room.name}»: третья комната "
                    f"на одной стене невозможна")
            ld.back = self.side(room.upper, room.mid, room.lower, si)

    def _wall(self, wall: _Wall) -> None:
        a = self.vertex(wall.x1, wall.y1)
        b = self.vertex(wall.x2, wall.y2)
        if a == b:
            raise TextMapError(
                wall.line, f"СТЕНА: концы ({_fmt(wall.x1)}, {_fmt(wall.y1)})"
                           f" и ({_fmt(wall.x2)}, {_fmt(wall.y2)}) совпадают")
        key = (a, b) if a <= b else (b, a)
        li = self._lines.get(key)
        if li is not None:
            ld = self.sm.linedefs[li]
            self.paint(ld.front, wall)
            self.paint(ld.back, wall)
            if wall.special:
                ld.special = wall.special
            if wall.tag:
                ld.tag = wall.tag
            if wall.solid:
                if ld.back < 0:
                    raise TextMapError(
                        wall.line, f"СТЕНА ({_fmt(wall.x1)}, {_fmt(wall.y1)})-"
                                   f"({_fmt(wall.x2)}, {_fmt(wall.y2)}): "
                                   f"«сплошная» требует общей грани двух комнат")
                ld.solid = True
            return
        self.sm.invalidate()
        front, back = _probe_sectors(self.sm, wall.x1, wall.y1,
                                     wall.x2, wall.y2)
        if front < 0:
            raise TextMapError(
                wall.line, f"СТЕНА ({_fmt(wall.x1)}, {_fmt(wall.y1)})-"
                           f"({_fmt(wall.x2)}, {_fmt(wall.y2)}): "
                           f"рядом нет ни одной комнаты")
        back = back if back >= 0 and back != front else -1
        if wall.solid and back < 0:
            raise TextMapError(
                wall.line, f"СТЕНА ({_fmt(wall.x1)}, {_fmt(wall.y1)})-"
                           f"({_fmt(wall.x2)}, {_fmt(wall.y2)}): "
                           f"«сплошная» требует общей грани двух комнат")
        back_side = (self.side(wall.upper, wall.mid, wall.lower, back)
                     if back >= 0 else -1)
        ld = LineDef(
            a, b, special=wall.special, tag=wall.tag,
            front=self.side(wall.upper, wall.mid, wall.lower, front),
            back=back_side,
        )
        if wall.solid:
            ld.solid = True
        self.sm.linedefs.append(ld)
        self._lines[key] = len(self.sm.linedefs) - 1
        self.sm.invalidate()


def _probe_sectors(sm: SectorMap, x1: float, y1: float,
                   x2: float, y2: float) -> tuple[int, int]:
    """Sectors on the front (cross<0) and back side of a segment."""
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length <= 0.0:
        return -1, -1
    nx, ny = dy / length, -dx / length
    front = back = -1
    for t in (0.5, 0.25, 0.75):
        mx = x1 + dx * t
        my = y1 + dy * t
        if front < 0:
            front = sm.find_sector_at(mx + nx * _PROBE, my + ny * _PROBE)
        if back < 0:
            back = sm.find_sector_at(mx - nx * _PROBE, my - ny * _PROBE)
        if front >= 0 and back >= 0:
            break
    return front, back


def _model_error(message: str) -> str:
    known = _MODEL_ERRORS.get(message)
    if known:
        return known
    if "above ceiling" in message:
        return f"ошибка модели карты: {message.replace('above ceiling', 'выше потолка')}"
    return f"ошибка модели карты: {message}"


# ── public API ────────────────────────────────────────────────────────────

def parse(text: str, *, validate: bool = True) -> SectorMap:
    """Parse ``.mts`` text into a validated ``SectorMap``."""
    if text.startswith("\ufeff"):
        text = text[1:]
    doc = _parse_document(text)
    sm = _Builder().build(doc)
    if validate:
        errors = sm.validate()
        if errors:
            raise TextMapError(max(doc.last_line, 1), _model_error(errors[0]))
    return sm


def load(path: str | Path, *, validate: bool = True) -> SectorMap:
    """Read a UTF-8 ``.mts`` file (BOM tolerated)."""
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TextMapError(
            0, f"файл не в кодировке UTF-8 ({exc.reason}); сохраните карту "
               f"в UTF-8, например «Блокнот» → Файл → Сохранить как → "
               f"Кодировка UTF-8") from None
    return parse(text, validate=validate)


def save(sm: SectorMap, path: str | Path, *, verify: bool = True) -> None:
    """Write ``sm`` as canonical ``.mts`` text (UTF-8, LF).

    With ``verify`` the rendered text is parsed back first, so a map the
    format cannot express is reported instead of silently written out.
    """
    text = render(sm)
    if verify:
        parse(text, validate=False)
    Path(path).write_text(text, encoding="utf-8", newline="\n")


def _sector_loops(sm: SectorMap, sector: int) -> list[list[int]]:
    """Directed boundary loops of one sector (front: v1->v2, back: v2->v1)."""
    n_sides = len(sm.sidedefs)
    adj: dict[int, list[int]] = {}
    for ld in sm.linedefs:
        if ld.v1 == ld.v2:
            continue
        if 0 <= ld.front < n_sides and sm.sidedefs[ld.front].sector == sector:
            adj.setdefault(ld.v1, []).append(ld.v2)
        if 0 <= ld.back < n_sides and sm.sidedefs[ld.back].sector == sector:
            adj.setdefault(ld.v2, []).append(ld.v1)
    open_v = {k for k, v in adj.items() if v}
    loops: list[list[int]] = []
    while open_v:
        start = next(iter(open_v))
        path = [start]
        cur = start
        closed = False
        while True:
            nxt = adj.get(cur)
            if not nxt:
                break
            vertex = nxt.pop()
            if vertex == start:
                closed = True
                break
            path.append(vertex)
            cur = vertex
        if closed and len(path) >= 3:
            loops.append(path)
        for v in path:                      # exhausted vertices leave the walk
            if not adj.get(v):
                open_v.discard(v)
    return loops


def _side_defaults(sm: SectorMap, sector: int) -> tuple[str, str, str]:
    """Most common (upper, mid, lower) among the sides of one sector."""
    votes: dict[tuple[str, str, str], int] = {}
    for sd in sm.sidedefs:
        if sd.sector == sector:
            key = (sd.upper, sd.mid, sd.lower)
            votes[key] = votes.get(key, 0) + 1
    if not votes:
        return ("-", "wall", "-")
    return min(votes.items(), key=lambda item: -item[1])[0]


def _thing_lines(t: Thing) -> list[str]:
    if t.type == "player1":
        head = f"ИГРОК {_fmt(t.x)} {_fmt(t.y)}"
    elif t.type in ENEMY_BY_TYPE:
        head = f"ВРАГ {_fmt(t.x)} {_fmt(t.y)} {ENEMY_BY_TYPE[t.type]}"
    elif t.type == "med":
        head = f"АПТЕЧКА {_fmt(t.x)} {_fmt(t.y)}"
    elif t.type == "amm":
        head = f"ПАТРОНЫ {_fmt(t.x)} {_fmt(t.y)}"
    elif t.type in KEY_LOCK_BY_ITEM:
        color = _KEY_COLOR_WORD[KEY_LOCK_BY_ITEM[t.type]]
        head = f"КЛЮЧ {_fmt(t.x)} {_fmt(t.y)} {color}"
    elif t.type in THING_BY_TYPE:
        head = f"ВЕЩЬ {_fmt(t.x)} {_fmt(t.y)} {THING_BY_TYPE[t.type]}"
    else:
        head = f"ВЕЩЬ {_fmt(t.x)} {_fmt(t.y)} {t.type}"
    out = [head]
    if t.angle:
        out.append(f"направление {int(t.angle)}")
    if t.z:
        out.append(f"высота {_fmt(t.z)}")
    return out


def render(sm: SectorMap) -> str:
    """Serialize a ``SectorMap`` as canonical ``.mts`` text.

    Rooms carry their floor/ceiling heights, light, flat and wall textures;
    lines that the room contours do not describe exactly (mechanics, free
    walls, offsets, foreign flags) are emitted as explicit ``СТЕНА``
    commands.  ``parse(render(m))`` reproduces ``m`` for every map the format
    can express: a sector is one contour plus optional holes (nested rooms, an
    extra ``контур`` per loop), a two-sided line separates exactly two sectors
    and its sides carry the texture of their own room.
    """
    out: list[str] = [
        MTS_HEADER,
        "# Сгенерировано shared.text_map: правится в любом текстовом редакторе",
        "# теги/механики (special/tag) — данные карты, поведение — резерв",
        "",
    ]
    n_sectors = len(sm.sectors)
    defaults = [_side_defaults(sm, si) for si in range(n_sectors)]

    def _canon(loop: list[int]) -> list[int]:
        # rotate to the lexicographically first corner: the canonical text of a
        # map must not depend on the order its linedefs happen to be built in
        start = min(range(len(loop)), key=lambda i: (sm.verts[loop[i]].x,
                                                     sm.verts[loop[i]].y,
                                                     loop[i]))
        return loop[start:] + loop[:start]

    contours: list[list[list[int]]] = []
    for si in range(n_sectors):
        loops = [_canon(lp) for lp in _sector_loops(sm, si)]
        # Biggest loop first: it is the room's own boundary, the rest are its
        # holes (nested rooms).  Ties break on the loops themselves so the text
        # stays canonical.
        loops.sort(key=lambda lp: (-abs(_area(
            [(sm.verts[i].x, sm.verts[i].y) for i in lp])), lp))
        contours.append(loops)

    owners: dict[tuple[int, int], list[tuple[int, int, int]]] = {}
    for si, loops in enumerate(contours):
        for loop in loops:
            n = len(loop)
            for i in range(n):
                a, b = loop[i], loop[(i + 1) % n]
                key = (a, b) if a <= b else (b, a)
                owners.setdefault(key, []).append((si, a, b))

    lines_by_pair: dict[tuple[int, int], list[int]] = {}
    for i, ld in enumerate(sm.linedefs):
        key = (ld.v1, ld.v2) if ld.v1 <= ld.v2 else (ld.v2, ld.v1)
        lines_by_pair.setdefault(key, []).append(i)

    covered: set[int] = set()
    for key, own in owners.items():
        indexes = lines_by_pair.get(key, [])
        if len(indexes) != 1 or len(own) > 2:
            continue
        ld = sm.linedefs[indexes[0]]
        fronts = [si for si, a, b in own if (a, b) == (ld.v1, ld.v2)]
        backs = [si for si, a, b in own if (a, b) != (ld.v1, ld.v2)]
        if len(fronts) != 1 or len(backs) > 1:
            continue
        front = fronts[0]
        back = backs[0] if backs else None
        if back is not None and back == front:
            continue
        if _line_matches_room(sm, indexes[0], front, back, defaults):
            covered.add(indexes[0])

    for si, loops in enumerate(contours):
        if not loops:
            continue
        sector = sm.sectors[si]
        upper, mid, lower = defaults[si]
        out.append(f"КОМНАТА {si + 1}")
        for ci, loop in enumerate(loops):
            if ci:
                out.append("контур")
            for vi in loop:
                v = sm.verts[vi]
                out.append(f"угол {_fmt(v.x)} {_fmt(v.y)}")
            if len(loop) == 3:           # explicit closing corner
                v = sm.verts[loop[0]]
                out.append(f"угол {_fmt(v.x)} {_fmt(v.y)}")
        if sector.floor_h:
            out.append(f"пол {sector.floor_h}")
        if sector.ceil_h != 128:
            out.append(f"потолок {sector.ceil_h}")
        if sector.light != 160:
            out.append(f"свет {sector.light}")
        if sector.floor_tex != "floor":
            out.append(f"пол_текстура {sector.floor_tex}")
        if sector.ceil_tex != "ceiling":
            out.append(f"потол_текстура {sector.ceil_tex}")
        if mid != "wall":
            out.append(f"стена {mid}")
        if upper != "-":
            out.append(f"текстура_верх {upper}")
        if lower != "-":
            out.append(f"текстура_низ {lower}")
        if sector.special:
            out.append(f"триггер тип {_special_name(sector.special)}")
        if sector.tag:
            out.append(f"тег {sector.tag}")
        out.append("")

    n_sides = len(sm.sidedefs)
    for i, ld in enumerate(sm.linedefs):
        if i in covered:
            continue
        v1, v2 = sm.verts[ld.v1], sm.verts[ld.v2]
        if ld.special == SPECIAL_DOORS and ld.back >= 0:
            lock = sm.door_lock(i)
            head = f"ДВЕРЬ {_fmt(v1.x)} {_fmt(v1.y)} " \
                   f"{_fmt(v2.x)} {_fmt(v2.y)}"
            if lock is not None:
                head += f" {_KEY_COLOR_WORD[lock]}"
            out.append(head)
            if 0 <= ld.front < n_sides:
                sd = sm.sidedefs[ld.front]
                if sd.upper != "-":
                    out.append(f"текстура_верх {sd.upper}")
                if sd.lower != "-":
                    out.append(f"текстура_низ {sd.lower}")
            if ld.tag:
                out.append(f"тег {ld.tag}")
            out.append("")
            continue
        out.append(f"СТЕНА {_fmt(v1.x)} {_fmt(v1.y)} "
                   f"{_fmt(v2.x)} {_fmt(v2.y)}")
        if 0 <= ld.front < n_sides:
            sd = sm.sidedefs[ld.front]
            if sd.mid != "wall":
                out.append(f"текстура {sd.mid}")
            if sd.upper != "-":
                out.append(f"текстура_верх {sd.upper}")
            if sd.lower != "-":
                out.append(f"текстура_низ {sd.lower}")
        if ld.special:
            out.append(f"триггер тип {_special_name(ld.special)}")
        if ld.tag:
            out.append(f"тег {ld.tag}")
        if ld.solid:
            out.append("сплошная")
        out.append("")

    for t in sm.things:
        out.extend(_thing_lines(t))
    return "\n".join(out).rstrip("\n") + "\n"


def _line_matches_room(sm: SectorMap, index: int, front: int,
                        back: int | None,
                        defaults: list[tuple[str, str, str]]) -> bool:
    """True when the room contours alone describe this line exactly."""
    ld = sm.linedefs[index]
    if ld.flags or ld.special or ld.tag:
        return False
    if not 0 <= ld.front < len(sm.sidedefs):
        return False
    front_side = sm.sidedefs[ld.front]
    if front_side.sector != front or front_side.x_off or front_side.y_off:
        return False
    if (front_side.upper, front_side.mid, front_side.lower) != defaults[front]:
        return False
    if back is None:
        return ld.back < 0
    if not 0 <= ld.back < len(sm.sidedefs):
        return False
    back_side = sm.sidedefs[ld.back]
    if back_side.sector != back or back_side.x_off or back_side.y_off:
        return False
    return (back_side.upper, back_side.mid, back_side.lower) == defaults[back]
