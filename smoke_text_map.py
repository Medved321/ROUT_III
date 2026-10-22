"""Smoke-тест текстового формата карт `.mts` (change `map-text-format`).

Покрывает:
  * парсинг примера из спеки и авто-геометрию (вершины/линии/стороны);
  * общую стену между КОМНАТАМИ → двусторонний linedef;
  * round-trip `parse(render(m))` без потери данных;
  * текстуры, диапазон света, механика-данные (special/tag), вещи;
  * понятные русские ошибки с номером строки;
  * стандартная библиотека + `shared.sector_map` (без pygame/numpy);
  * сверка таблиц текстур/врагов/предметов с движком;
  * `.mts` → `.mt1` даёт валидный бинарный контейнер.

Запуск: py -3.12 smoke_text_map.py
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared import text_map
from shared.sector_map import SectorMap, Sector, SideDef, Thing, Vertex, LineDef

# ── эталонная карта из спецификации ─────────────────────────────────────────

SPEC_MAP = """\
# формат: ROUT III .mts v1
# Пример из спеки: зал, погреб, рычаг на общей стене
КОМНАТА Зал
пол 0
потолок 128
свет 160
пол_текстура floor
потол_текстура ceiling
стена wall
угол 0 0
угол 512 0
угол 512 512
угол 0 512
тег 2

КОМНАТА Погреб
пол -32
потолок 96
свет 40
пол_текстура lava
потол_текстура ceiling
стена stone
угол 512 0
угол 1024 0
угол 1024 512
угол 512 512

СТЕНА 512 0 512 512
текстура switch
триггер тип рычаг тег 2

ИГРОК 320 320
ВРАГ 960 320 солдат
АПТЕЧКА 320 500
ПАТРОНЫ 256 256
"""


def spec_map() -> SectorMap:
    return text_map.parse(SPEC_MAP)


SIMPLE_ROOM = """\
КОМНАТА Зал
пол 0
потолок 128
свет 160
угол 0 0
угол 512 0
угол 512 512
угол 0 512

ИГРОК 320 320
"""

DARK_CELLAR = """\
КОМНАТА Погреб
пол_текстура lava
потол_текстура ceiling
стена wall
свет 40
угол 0 0
угол 512 0
угол 512 512
угол 0 512

ИГРОК 320 320
"""

# Комната с двумя вложенными комнатами (дырами): внешний контур + один общий
# контур-дыра (объединение двух внутренних квадратов), затем сами внутренние
# комнаты. Общая грань двух внутренних комнат двусторонняя, но не граничит с
# внешней — она должна получить front = меньший индекс.
NESTED_HOLE = """\
КОМНАТА Внешняя
угол 0 0
угол 0 400
угол 400 400
угол 400 0
контур
угол 100 100
угол 100 300
угол 200 300
угол 300 300
угол 300 100
угол 200 100

КОМНАТА Левая
угол 100 100
угол 100 300
угол 200 300
угол 200 100

КОМНАТА Правая
угол 200 100
угол 200 300
угол 300 300
угол 300 100

ИГРОК 200 50
"""


# ── сигнатура модели, не зависящая от направления линий ─────────────────────

def _pos_map(sm: SectorMap) -> dict:
    points = sorted({(round(v.x, 2), round(v.y, 2)) for v in sm.verts})
    return {p: i for i, p in enumerate(points)}


def _vid(sm: SectorMap, pos: dict, index: int) -> int:
    return pos[(round(sm.verts[index].x, 2), round(sm.verts[index].y, 2))]


def _side(sm: SectorMap, index: int) -> tuple:
    sd = sm.sidedefs[index]
    return (sd.upper, sd.mid, sd.lower, sd.x_off, sd.y_off, sd.sector)


def signature(sm: SectorMap) -> tuple:
    """Значимое представление карты: пары вершин, стороны, сектора, вещи."""
    pos = _pos_map(sm)
    verts = sorted(pos)
    sectors = tuple((s.floor_h, s.ceil_h, s.floor_tex, s.ceil_tex, s.light,
                     s.special, s.tag) for s in sm.sectors)
    lines = []
    for ld in sm.linedefs:
        a, b = _vid(sm, pos, ld.v1), _vid(sm, pos, ld.v2)
        key = (a, b) if a <= b else (b, a)
        back = _side(sm, ld.back) if ld.back >= 0 else None
        lines.append((key, _side(sm, ld.front), back, ld.special, ld.tag,
                      ld.flags))
    things = tuple(sorted((round(t.x, 2), round(t.y, 2), t.type, t.angle,
                           round(t.z, 2)) for t in sm.things))
    return (verts, sectors, tuple(sorted(lines)), things)


# ── 1.1 / 1.2: парсинг и авто-геометрия ────────────────────────────────────

def test_parse_spec_map() -> None:
    sm = spec_map()
    assert sm.validate() == [], sm.validate()
    assert len(sm.sectors) == 2, len(sm.sectors)
    # 4 + 4 угла, из них 2 общие вершины (512;0) и (512;512)
    assert len(sm.verts) == 6, len(sm.verts)
    # 4 линии зала + 3 новые линии погреба (общая стена переиспользуется)
    assert len(sm.linedefs) == 7, len(sm.linedefs)
    # 7 передних граней + 1 обратная грань общей стены
    assert len(sm.sidedefs) == 8, len(sm.sidedefs)
    assert sm.sectors[0].floor_h == 0
    assert sm.sectors[0].ceil_h == 128
    assert sm.sectors[0].light == 160
    assert sm.sectors[0].floor_tex == "floor"
    assert sm.sectors[0].ceil_tex == "ceiling"
    assert sm.sectors[0].tag == 2
    assert sm.sectors[1].floor_h == -32
    assert sm.sectors[1].light == 40
    assert sm.spawn_px() == [(320.0, 320.0)], sm.spawn_px()
    assert sm.find_sector_at(320, 320) == 0
    assert sm.find_sector_at(960, 320) == 1


def test_simple_room() -> None:
    """Spec scenario: `КОМНАТА` with four corners → four one-sided lines."""
    sm = text_map.parse(SIMPLE_ROOM)
    assert sm.validate() == [], sm.validate()
    assert len(sm.sectors) == 1
    assert len(sm.verts) == 4, len(sm.verts)
    assert len(sm.linedefs) == 4, len(sm.linedefs)
    assert all(ld.back < 0 for ld in sm.linedefs), "все линии односторонние"
    assert all(0 <= ld.front < len(sm.sidedefs) for ld in sm.linedefs)
    assert len(sm.sidedefs) == 4, len(sm.sidedefs)
    sector = sm.sectors[0]
    assert (sector.floor_h, sector.ceil_h, sector.light) == (0, 128, 160)
    assert sm.spawn_px() == [(320.0, 320.0)], sm.spawn_px()


def test_dark_cellar() -> None:
    """Spec scenario: `lava`/`ceiling`/`wall` + `свет 40`."""
    sm = text_map.parse(DARK_CELLAR)
    assert sm.validate() == [], sm.validate()
    sector = sm.sectors[0]
    assert sector.floor_tex == "lava", sector.floor_tex
    assert sector.ceil_tex == "ceiling", sector.ceil_tex
    assert sector.light == 40, sector.light
    assert all(sd.mid == "wall" for sd in sm.sidedefs), sm.sidedefs
    back = SectorMap.from_mt1(sm.to_mt1())
    assert back.sectors[0].floor_tex == "lava"
    assert back.sectors[0].ceil_tex == "ceiling"
    assert all(sd.mid == "wall" for sd in back.sidedefs), back.sidedefs


def test_two_sided_shared_wall() -> None:
    sm = spec_map()
    shared = [ld for ld in sm.linedefs if ld.back >= 0]
    assert len(shared) == 1, len(shared)
    ld = shared[0]
    front = sm.sidedefs[ld.front].sector
    back = sm.sidedefs[ld.back].sector
    assert {sm.sectors[front].tag, sm.sectors[back].tag} == {0, 2}
    assert front != back
    # стена между залом и погребом: x = 512
    a, b = sm.verts[ld.v1], sm.verts[ld.v2]
    assert {a.x, b.x} == {512.0}
    # триггер-рычаг сохранён на двусторонней линии
    assert ld.special == 1, ld.special
    assert ld.tag == 2, ld.tag


def test_solid_shared_wall() -> None:
    """«сплошная» on a shared room border gives a solid two-sided linedef."""
    src = """\
КОМНАТА Зал
угол 0 0
угол 512 0
угол 512 512
угол 0 512

КОМНАТА Кладовая
угол 512 0
угол 1024 0
угол 1024 512
угол 512 512

СТЕНА 512 0 512 512
сплошная
текстура_низ bridge
текстура_верх platform

ИГРОК 256 256
"""
    sm = text_map.parse(src)
    assert sm.validate() == [], sm.validate()
    shared = [ld for ld in sm.linedefs if ld.back >= 0]
    assert len(shared) == 1, len(shared)
    ld = shared[0]
    assert ld.solid is True, "«сплошная» must set the flag"
    assert sm.line_blocks(sm.linedefs.index(ld)) is True
    # the step lips are assigned and the unassigned side stays "-"
    assert sm.sidedefs[ld.front].lower == "bridge"
    assert sm.sidedefs[ld.front].upper == "platform"
    # without the keyword the very same text yields a plain portal
    plain = text_map.parse(src.replace("сплошная\n", ""))
    ld0 = [l for l in plain.linedefs if l.back >= 0][0]
    assert ld0.solid is False
    assert plain.line_blocks(plain.linedefs.index(ld0)) is False, \
        "equal floors stay a passable portal"

    # «сплошная» takes no value
    err = _error(src.replace("сплошная", "сплошная да"))
    assert "не принимает" in err, err

    # a one-sided border has no second room, so the flag makes no sense there
    outside = src.replace("СТЕНА 512 0 512 512", "СТЕНА 0 0 512 0")
    err = _error(outside)
    assert "общей грани двух комнат" in err, err


def test_solid_round_trip() -> None:
    """файл → модель → файл → модель keeps solid and the step lips."""
    sm = text_map.parse("""\
КОМНАТА Зал
угол 0 0
угол 512 0
угол 512 512
угол 0 512
пол 16

КОМНАТА Нижний
угол 512 0
угол 1024 0
угол 1024 512
угол 512 512

СТЕНА 512 0 512 512
сплошная
текстура_низ bridge
текстура_верх platform

ИГРОК 256 256
""")
    assert sm.validate() == [], sm.validate()
    ld = [l for l in sm.linedefs if l.back >= 0][0]
    assert ld.solid is True
    # a 16-unit step is passable geometry, but the flag makes it a wall
    assert abs(sm.sectors[0].floor_h - sm.sectors[1].floor_h) == 16
    assert sm.line_blocks(sm.linedefs.index(ld)) is True

    text = text_map.render(sm)
    assert "сплошная" in text, text
    assert "текстура_низ bridge" in text, text
    assert "текстура_верх platform" in text, text

    again = text_map.parse(text)
    assert again.validate() == [], again.validate()
    ld2 = [l for l in again.linedefs if l.back >= 0][0]
    assert ld2.solid is True, "solid must survive the round-trip"
    assert again.sidedefs[ld2.front].lower == "bridge"
    assert again.sidedefs[ld2.front].upper == "platform"
    assert [s.floor_h for s in again.sectors] == [s.floor_h for s in sm.sectors]
    # and the second pass is byte-stable
    assert text_map.render(again) == text

    # clearing the flag in the model drops the keyword again
    ld2.solid = False
    assert "сплошная" not in text_map.render(again)


def test_triangle_needs_explicit_close() -> None:
    triangle = """\
КОМНАТА Треугольник
угол 0 0
угол 256 0
угол 128 256

ИГРОК 64 32
"""
    err = _error(triangle)
    assert "не замкнут" in err, err
    closed = triangle.replace("угол 128 256", "угол 128 256\nугол 0 0")
    sm = text_map.parse(closed)
    assert sm.validate() == [], sm.validate()
    assert len(sm.sectors) == 1
    assert len(sm.linedefs) == 3, len(sm.linedefs)


def _two_sided_profile(sm: SectorMap) -> list:
    """Двусторонние линии как (пара высот секторов, solid) — индексы не важны."""
    def sec(si: int) -> tuple:
        s = sm.sectors[si]
        return (round(s.floor_h, 2), round(s.ceil_h, 2))
    return sorted(
        (tuple(sorted((sec(sm.sidedefs[ld.front].sector),
                       sec(sm.sidedefs[ld.back].sector)))),
         bool(getattr(ld, "solid", False)))
        for ld in sm.linedefs if ld.back >= 0)


def test_room_with_hole() -> None:
    """Spec: вложенный `контур` = дыра сектора, её грани двусторонние."""
    sm = text_map.parse(NESTED_HOLE)
    assert sm.validate() == [], sm.validate()
    assert len(sm.sectors) == 3, len(sm.sectors)
    assert len(text_map._sector_loops(sm, 0)) == 2, "внешняя имеет дыру"
    assert len(text_map._sector_loops(sm, 1)) == 1, "левая — одна петля"
    assert len(text_map._sector_loops(sm, 2)) == 1, "правая — одна петля"
    assert sm.find_sector_at(200, 50) == 0, "точка спавна — во внешней"
    assert sm.find_sector_at(150, 200) == 1, "левая комната"
    assert sm.find_sector_at(250, 200) == 2, "правая комната"

    shared = [ld for ld in sm.linedefs if ld.back >= 0]
    assert len(shared) == 7, len(shared)
    # грани внешнего сектора с дырой: front = внешняя (индекс 0)
    outer_edges = [ld for ld in shared
                   if 0 in (sm.sidedefs[ld.front].sector,
                            sm.sidedefs[ld.back].sector)]
    assert outer_edges, "есть грани внешней комнаты с внутренними"
    for ld in outer_edges:
        assert sm.sidedefs[ld.front].sector == 0, "front = внешняя комната"
        assert sm.sidedefs[ld.front].sector < sm.sidedefs[ld.back].sector
    # общая грань двух внутренних комнат: front = меньший индекс
    inner = [ld for ld in shared if sm.sidedefs[ld.front].sector == 1][0]
    assert sm.sidedefs[inner.back].sector == 2

    # двусторонность переживает render → parse
    again = text_map.parse(text_map.render(sm))
    assert again.validate() == [], again.validate()
    assert _two_sided_profile(again) == _two_sided_profile(sm)
    assert signature(again) == signature(sm)
    assert text_map.render(again) == text_map.render(sm), "текст канонический"


def test_hole_triangle_and_single_loop() -> None:
    """Треугольник-дыра разбирается; сектор из одной петли не даёт `контур`."""
    single = text_map.parse(SIMPLE_ROOM)
    assert "контур" not in text_map.render(single), \
        "одноконтурный сектор не печатает строк `контур`"

    tri_hole = """\
КОМНАТА Внешняя
угол 0 0
угол 0 400
угол 400 400
угол 400 0
контур
угол 100 100
угол 200 300
угол 300 100
угол 100 100

КОМНАТА Внутренняя
угол 100 100
угол 200 300
угол 300 100
угол 100 100

ИГРОК 200 50
"""
    # как и «КОМНАТА», треугольный контур требует явного замыкания
    open_hole = tri_hole.replace("угол 300 100\nугол 100 100\n\nКОМНАТА Внутренняя",
                                 "угол 300 100\n\nКОМНАТА Внутренняя")
    err = _error(open_hole)
    assert "не замкнут" in err, err

    sm = text_map.parse(tri_hole)
    assert sm.validate() == [], sm.validate()
    assert len(sm.sectors) == 2, len(sm.sectors)
    assert len(text_map._sector_loops(sm, 0)) == 2, "внешняя: контур + дыра"
    assert len(text_map._sector_loops(sm, 1)) == 1, "внутренний треугольник"
    assert len([ld for ld in sm.linedefs if ld.back >= 0]) == 3, "3 общей грани"
    again = text_map.parse(text_map.render(sm))
    assert _two_sided_profile(again) == _two_sided_profile(sm)


def test_real_maps_keep_two_sided() -> None:
    """Реальные карты: дыры и двусторонность переживают render → parse."""
    for name, expected in (("sector1", 22), ("sector2", 6)):
        path = ROOT / "maps" / (name + ".mt1")
        if not path.exists():
            continue
        sm = SectorMap.load(str(path))
        assert sum(1 for ld in sm.linedefs if ld.back >= 0) == expected, name
        again = text_map.parse(text_map.render(sm))
        assert again.validate() == [], (name, again.validate())
        assert _two_sided_profile(again) == _two_sided_profile(sm), name


# ── 1.3: render → parse round-trip ─────────────────────────────────────────

def test_canonical_text_is_stable() -> None:
    """Канонический вывод не зависит от порядка углов в исходнике."""
    def grid(rotate: int) -> str:
        lines: list[str] = []
        for gy in range(2):
            for gx in range(2):
                x0, y0 = gx * 512, gy * 512
                corners = [f"угол {x0} {y0}", f"угол {x0 + 512} {y0}",
                           f"угол {x0 + 512} {y0 + 512}", f"угол {x0} {y0 + 512}"]
                lines.append(f"КОМНАТА клетка {gx}_{gy}")
                lines += corners[rotate:] + corners[:rotate]
        lines.append("ИГРОК 64 64")
        return "\n".join(lines) + "\n"

    sm = text_map.parse(grid(0))
    assert len(sm.sectors) == 4, len(sm.sectors)
    assert sum(1 for ld in sm.linedefs if ld.back >= 0) == 4, "4 общие стены"
    text = text_map.render(sm)
    assert text_map.render(text_map.parse(text)) == text

    turned = text_map.parse(grid(2))
    assert signature(turned) == signature(sm)
    assert text_map.render(turned) == text


def test_round_trip() -> None:
    sm = spec_map()
    text = text_map.render(sm)
    assert text.startswith(text_map.MTS_HEADER), text[:60]
    again = text_map.parse(text)
    assert again.validate() == [], again.validate()
    assert signature(sm) == signature(again)
    # текст канонический: повторный рендер не меняется
    assert text_map.render(again) == text
    # заголовок и комментарии не влияют на разбор
    assert text_map.parse(text_map.MTS_HEADER + "\n" + text.split("\n", 1)[1]) \
        .validate() == []


def test_render_covers_sectors_with_rooms() -> None:
    sm = spec_map()
    text = text_map.render(sm)
    assert text.count("\nКОМНАТА ") == 2, text
    walls = [l for l in text.splitlines() if l.startswith("СТЕНА ")]
    assert len(walls) == 1, walls
    assert sorted(walls[0].split()[1:]) == ["0", "512", "512", "512"], walls
    assert "триггер тип рычаг" in text
    assert "тег 2" in text


def test_save_and_load(tmp: Path) -> None:
    sm = spec_map()
    path = tmp / "map.mts"
    text_map.save(sm, path)
    data = path.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf"), "BOM не допускается"
    assert b"\r\n" not in data, "переводы строк LF"
    assert data.decode("utf-8") == text_map.render(sm)
    loaded = text_map.load(path)
    assert signature(loaded) == signature(sm)
    # редактор с BOM не должен ломать загрузку
    bom = tmp / "bom.mts"
    bom.write_bytes(b"\xef\xbb\xbf" + data)
    assert signature(text_map.load(bom)) == signature(sm)


def test_save_refuses_unexpressible_map(tmp: Path) -> None:
    """`save` must not write a file that `parse` cannot read back."""
    broken = SectorMap(
        verts=[Vertex(0.0, 0.0), Vertex(100.0, 0.0), Vertex(50.0, 100.0),
               Vertex(5000.0, 5000.0), Vertex(5100.0, 5000.0)],
        linedefs=[LineDef(0, 1, front=0), LineDef(1, 2, front=0),
                  LineDef(2, 0, front=0),
                  # обрывок стены далеко от сектора: в .mts ему негде жить
                  LineDef(3, 4, front=0)],
        sidedefs=[SideDef(mid="wall", sector=0) for _ in range(4)],
        sectors=[Sector()],
        things=[Thing(20.0, 20.0, "player1")],
    )
    path = tmp / "broken.mts"
    try:
        text_map.save(broken, path)
    except text_map.TextMapError as exc:
        assert "рядом нет ни одной" in str(exc), exc
        assert not path.exists(), "битый файл не должен оставаться на диске"
    else:
        raise AssertionError("ожидалась TextMapError для невыразимой карты")
    # без проверки файл пишется как есть
    text_map.save(broken, path, verify=False)
    assert path.exists()


# ── 2.1 / 2.2: текстуры и свет ─────────────────────────────────────────────

def test_textures() -> None:
    sm = spec_map()
    assert "wall" in text_map.WALL_TEXTURES
    assert "floor" in text_map.FLOOR_TEXTURES
    assert "ceiling" in text_map.CEIL_TEXTURES
    assert "switch" in text_map.WALL_TEXTURES
    wall = [ld for ld in sm.linedefs if ld.back >= 0][0]
    # `СТЕНА` задаёт текстуру грани с обеих сторон
    assert sm.sidedefs[wall.front].mid == "switch"
    assert sm.sidedefs[wall.back].mid == "switch"
    outer = [ld for ld in sm.linedefs
             if ld.back < 0 and sm.sidedefs[ld.front].sector == 1]
    assert outer and all(sm.sidedefs[ld.front].mid == "stone" for ld in outer)
    bad = _error(SPEC_MAP.replace("пол_текстура floor", "пол_текстура мох"))
    assert "неизвестная текстура пола" in bad, bad
    assert "мох" in bad, bad


def test_light_range() -> None:
    for value, ok in (("0", True), ("255", True), ("128", True),
                      ("256", False), ("-1", False)):
        src = SPEC_MAP.replace("свет 160", "свет " + value)
        if ok:
            assert text_map.parse(src).validate() == []
        else:
            err = _error(src)
            assert "свет вне 0..255" in err, (value, err)


# ── 2.3: механика — только данные карты ────────────────────────────────────

def test_mechanics_data() -> None:
    sm = spec_map()
    assert sm.sectors[0].tag == 2
    assert sm.sectors[1].tag == 0
    trigger = [ld for ld in sm.linedefs if ld.special]
    assert len(trigger) == 1, trigger
    assert text_map.SPECIAL_NAMES["рычаг"] == 1
    # ключевое слово в любом регистре
    upper = text_map.parse(SPEC_MAP.replace("КОМНАТА Зал", "КОМНАТА ЗАЛ")
                           .replace("угол 0 0", "УГОЛ 0 0")
                           .replace("Текстура", "текстура"))
    assert upper.validate() == [], upper.validate()
    assert signature(upper) == signature(sm)


# ── 2.4: вещи ─────────────────────────────────────────────────────────────

def test_things() -> None:
    sm = spec_map()
    types = sorted(t.type for t in sm.things)
    assert types == ["amm", "med", "player1", "soldier"], types
    assert len(sm.spawn_px()) == 1
    generic = text_map.parse("""\
КОМНАТА Зал
угол 0 0
угол 256 0
угол 256 256
угол 0 256

ИГРОК 32 32
ВЕЩЬ 64 64 броня_зеленая
ВЕЩЬ 96 96 плазмоган
АПТЕЧКА 128 128
ПАТРОНЫ 160 160
ВЕЩЬ 200 200 ракетница
ВРАГ 220 220 дробовик
направление 90
высота 24
""")
    kinds = sorted(t.type for t in generic.things)
    assert kinds == ["amm", "armor_green", "med", "player1", "shotgunguy",
                     "wpn_3", "wpn_4"], kinds
    aimed = [t for t in generic.things if t.type == "shotgunguy"][0]
    assert aimed.angle == 90, aimed.angle
    assert aimed.z == 24.0, aimed.z
    assert signature(generic) == signature(text_map.parse(text_map.render(generic)))


# ── 3.1: понятные ошибки с номером строки ─────────────────────────────────

def _error(src: str) -> str:
    try:
        text_map.parse(src)
    except text_map.TextMapError as exc:
        return str(exc)
    raise AssertionError("ожидалась TextMapError")


def test_errors() -> None:
    checks = [
        (SPEC_MAP + "\nТАНЕЦ 10 10\n", "неизвестная команда"),
        ("угол 0 0\nугол 1 1\n", "«угол» без КОМНАТЫ"),
        ("# только комментарий\n", "нет ни одной КОМНАТЫ"),
        (SPEC_MAP.replace("свет 160", "свет 900"), "свет вне 0..255"),
        (SPEC_MAP.replace("стена stone", "стена камень"), "неизвестная текстура стены"),
        (SPEC_MAP.replace("ВРАГ 960 320 солдат", "ВРАГ 960 320 дракон"),
         "неизвестный тип врага"),
        (SPEC_MAP.replace("угол 0 512", "угол 512 0"), "дублируется"),
        (SPEC_MAP.replace("ИГРОК 320 320\n", ""), "нет ИГРОК"),
        (SPEC_MAP.replace("ВРАГ 960 320 солдат", "ВРАГ 960 320"),
         "требует 3 значения"),
        (SPEC_MAP.replace("ИГРОК 320 320", "ИГРОК 320"),
         "требует 2 числа"),
    ]
    for src, needle in checks:
        err = _error(src)
        assert needle in err, (needle, err)
        assert err.startswith("строка "), err
        lineno = int(err.split()[1].rstrip(":"))
        assert 0 < lineno <= src.count("\n") + 1, err


def test_error_line_numbers_are_exact() -> None:
    src = SPEC_MAP.replace("свет 160", "свет 900")
    lineno = next(i for i, line in enumerate(src.splitlines(), 1)
                  if "свет 900" in line)
    err = _error(src)
    assert err == f"строка {lineno}: команда «свет»: свет вне 0..255 (900)", err


# ── 4.1: стандартная библиотека и границы ─────────────────────────────────

_NO_PYGAME = """\
import sys
class _Blocked:
    names = {"pygame", "numpy"}
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in self.names:
            raise ImportError("заблокировано: " + name)
        return None
sys.meta_path.insert(0, _Blocked())
from shared.text_map import parse, render
sm = parse("КОМНАТА Зал\\nугол 0 0\\nугол 64 0\\nугол 64 64\\nугол 0 64\\n"
           "ИГРОК 8 8\\n")
assert sm.validate() == [], sm.validate()
assert parse(render(sm)).validate() == []
assert sm.to_mt1()[:4] == b"MT1\\x00"
print("OK", "pygame" in sys.modules, "numpy" in sys.modules)
"""


def test_stdlib_only() -> None:
    out = subprocess.run([sys.executable, "-c", _NO_PYGAME], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    assert "OK False False" in out.stdout, out.stdout

    tree = ast.parse((ROOT / "shared" / "text_map.py").read_text("utf-8"))
    allowed = {"math", "pathlib", "shared", "shared.sector_map", "__future__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name.split(".")[0] in {a.split(".")[0] for a in allowed}, \
                    alias.name
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            assert node.module.split(".")[0] in {a.split(".")[0] for a in allowed}, \
                node.module


def test_tables_match_engine() -> None:
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    from client.map_editor import ENEMY_NAMES, ITEM_NAMES, TILE_CHAR_TO_TEX
    assert set(text_map.ENEMY_TYPES.values()) == set(ENEMY_NAMES.values())
    assert set(text_map.ITEM_TYPES.values()) == set(ITEM_NAMES.values())
    assert set(text_map.THING_TYPES.values()) <= set(ITEM_NAMES.values()) | {"exit"}

    tree = ast.parse((ROOT / "engine" / "textures.py").read_text("utf-8"))
    static = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Subscript)
                and isinstance(node.targets[0].value, ast.Name)
                and node.targets[0].value.id == "textures"
                and isinstance(node.targets[0].slice, ast.Constant)):
            static.add(node.targets[0].slice.value)
    universe = static | set(TILE_CHAR_TO_TEX.values())
    assert "floor" in universe and "lava" in universe
    for name in text_map.WALL_TEXTURES:
        assert name in universe, name
    assert set(text_map.FLOOR_TEXTURES) <= universe
    assert set(text_map.CEIL_TEXTURES) <= universe


# ── 4.3: .mts → .mt1 ──────────────────────────────────────────────────────

def test_mt1_round_trip() -> None:
    sm = spec_map()
    blob = sm.to_mt1()
    assert SectorMap.validate_bytes(blob) == [], SectorMap.validate_bytes(blob)
    back = SectorMap.from_mt1(blob)
    assert signature(back) == signature(sm)
    assert back.validate() == [], back.validate()
    # spec scenario: `special`/`tag` переживают сериализацию без потерь
    lever = [ld for ld in back.linedefs if ld.special == 1]
    assert len(lever) == 1, lever
    assert lever[0].tag == 2, lever[0].tag
    assert sorted(s.tag for s in back.sectors if s.tag) == [2], back.sectors
    things = sorted(t.type for t in back.things)
    assert things == ["amm", "med", "player1", "soldier"], things
    assert back.spawn_px() == [(320.0, 320.0)], back.spawn_px()


# ── runner ─────────────────────────────────────────────────────────────────

def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tests = [
            test_parse_spec_map,
            test_simple_room,
            test_dark_cellar,
            test_two_sided_shared_wall,
            test_solid_shared_wall,
            test_solid_round_trip,
            test_triangle_needs_explicit_close,
            test_room_with_hole,
            test_hole_triangle_and_single_loop,
            test_real_maps_keep_two_sided,
            test_round_trip,
            test_canonical_text_is_stable,
            test_render_covers_sectors_with_rooms,
            partial(test_save_and_load, Path(tmp)),
            partial(test_save_refuses_unexpressible_map, Path(tmp)),
            test_textures,
            test_light_range,
            test_mechanics_data,
            test_things,
            test_errors,
            test_error_line_numbers_are_exact,
            test_stdlib_only,
            test_tables_match_engine,
            test_mt1_round_trip,
        ]
        failed = 0
        for test in tests:
            name = getattr(test, "__name__", None) or test.func.__name__
            try:
                test()
            except Exception as exc:                     # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {type(exc).__name__}: {exc}")
            else:
                print(f"ok   {name}")
    print("ПРОВАЛЕНО:", failed) if failed else print("Все проверки пройдены")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
