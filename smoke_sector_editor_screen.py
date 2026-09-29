"""Smoke: sector editor screen (tasks 4.3, 4.4).

Canvas render/perf: 3000-line stress map pans/redraws through the cache
path quickly; tool/thing/line interactions via the real event handler;
on-screen FPS meter updates.
"""
from __future__ import annotations

import os
import random
import sys
import time

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pygame  # noqa: E402

from client.sector_editor import SectorEditor, new_room  # noqa: E402
from client.sector_editor_screen import (  # noqa: E402
    BASE_CHIPS_H, HINT_BLOCK_H, MAPS_DIR, MAP_ROW_H, SectorEditorScreen)


def _evt(t, **kw):
    return pygame.event.Event(t, **kw)


def main():
    pygame.init()
    surface = pygame.display.set_mode((1000, 700))
    scr = SectorEditorScreen(1000, 700)
    scr.frame_around()

    # .mts export/import through the text format (task 4.2) — on a real
    # editor map, before the deliberately degenerate stress map below
    _check_mts_round_trip(scr)

    # stress map: ~3000 random one-sided lines
    rnd = random.Random(3)
    ed = scr.editor
    ed.begin_batch()
    pool: list[int] = []
    for _ in range(2400):
        pool.append(ed.add_vertex(rnd.uniform(0, 4000), rnd.uniform(0, 4000)))
    for _ in range(3000):
        ed.add_line(rnd.choice(pool), rnd.choice(pool))
    ed.end_batch()

    # uncached build (first real frame)
    t0 = time.time()
    scr.draw(surface)
    first = time.time() - t0
    # cached redraws
    times = []
    for _ in range(20):
        t0 = time.time()
        scr.draw(surface)
        times.append(time.time() - t0)
    avg = sum(times) / len(times)
    print(f"  canvas  first={first * 1000:.1f}ms avg={avg * 1000:.1f}ms")
    assert first < 0.4, "uncached base build too slow"
    assert avg < 0.08, "cached redraw too slow"
    assert scr.fps >= 0.0

    # tool button
    scr.tool = "select"
    scr.handle([_evt(pygame.MOUSEBUTTONDOWN,
                     pos=scr._tool_buttons["thing"].center, button=1)])
    assert scr.tool == "thing", scr.tool

    # place a thing in the canvas centre
    cx = scr.canvas.centerx
    cy = scr.canvas.centery
    n0 = len(ed.things)
    scr.handle([_evt(pygame.MOUSEBUTTONDOWN, pos=(cx, cy), button=1)])
    scr.handle([_evt(pygame.MOUSEBUTTONUP, pos=(cx, cy), button=1)])
    assert len(ed.things) == n0 + 1
    assert ed.things[-1].type == "imp"
    scr.handle([_evt(pygame.KEYDOWN, key=pygame.K_z, mod=pygame.KMOD_CTRL)])
    assert len(ed.things) == n0

    # draw a line with the line tool
    scr.tool = "line"
    scr.handle([_evt(pygame.MOUSEBUTTONDOWN, pos=(cx, cy), button=1)])
    scr.handle([_evt(pygame.MOUSEBUTTONDOWN, pos=(cx + 90, cy + 30), button=1)])
    scr.handle([_evt(pygame.MOUSEBUTTONUP, pos=(cx + 90, cy + 30), button=1)])
    scr.handle([_evt(pygame.KEYDOWN, key=pygame.K_z, mod=pygame.KMOD_CTRL)])

    # select tool picks a thing
    scr.tool = "select"
    scr.handle([_evt(pygame.MOUSEBUTTONDOWN, pos=(cx, cy), button=1)])
    scr.handle([_evt(pygame.MOUSEBUTTONUP, pos=(cx, cy), button=1)])
    scr.draw(surface)

    # pan & zoom don't crash
    scr._pan = True
    scr._pre = scr._to_world(cx, cy)
    scr.handle([_evt(pygame.MOUSEMOTION, pos=(cx + 60, cy + 40), buttons=(0, 0, 0))])
    scr.handle([_evt(pygame.MOUSEBUTTONUP)])
    scr.handle([_evt(pygame.MOUSEWHEEL, y=0, x=0)])
    scr.draw(surface)

    # E→sector screen exposes toggle to the client wrapper
    assert scr._button("mode") == "tile"
    scr.draw(surface)
    _check_heights_and_solid(scr, surface)
    _check_tex_palette(scr, surface)
    _check_panel_layout(surface)
    _check_selection_survives_nothing(scr, surface)
    _check_map_list(scr, surface)
    print("smoke_sector_editor_screen: ALL OK")


def _check_selection_survives_nothing(scr, surface) -> None:
    """A selection is an array index, so it must never outlive its map.

    Reproduces the reported crash: a linedef was selected, another map was
    loaded, and the next frame read ``lines[sel_line]`` and took the whole
    editor loop down with an IndexError. Every field that addresses a
    non-existent entity has to be dropped instead, and the ones that still
    point at something must be kept.
    """
    big = SectorEditor(new_room(1600, 1600))
    big.begin_batch()
    pool = [big.add_vertex(i * 40.0, (i * 97) % 900.0) for i in range(60)]
    for i in range(80):
        big.add_line(pool[i % 60], pool[(i * 7 + 3) % 60])
    big.end_batch()
    scr.editor = big
    scr.frame_around()
    scr._set_sel(line=len(big.lines) - 1, thing=0, vertex=len(big.verts) - 1,
                 sector=len(big.sectors) - 1)
    scr.draw(surface)                      # everything is live: kept as is
    assert scr.sel_line == len(big.lines) - 1
    assert scr.sel_thing == 0

    # each field past the end of its own array
    scr.sel_line = scr.sel_thing = 9999
    scr.sel_vertex = scr.sel_sector = 9999
    scr.draw(surface)
    assert (scr.sel_line, scr.sel_thing, scr.sel_vertex, scr.sel_sector) \
        == (None, None, None, None)

    # the reported repro, through the real loader
    scr.map_name = "sector1"
    scr._load_mts()
    assert "Загружено" in scr.status, scr.status
    assert scr.sel_line is None
    scr.draw(surface)
    scr._load()
    assert "Загружено" in scr.status, scr.status
    scr.draw(surface)

    # shrinking one array clears that field only, and leaves the others alone.
    # The last thing is the one to go: deleting it pulls the array under the
    # selected index, whereas deleting a lower one just renumbers the target.
    ed = scr.editor
    assert ed.things, "the fixture map must have things to delete"
    last_thing = len(ed.things) - 1
    scr._set_sel(line=0, thing=last_thing, sector=0)
    ed.delete_thing(last_thing)
    scr.draw(surface)
    assert scr.sel_thing is None, "a deleted thing must not stay selected"
    assert scr.sel_line == 0 and scr.sel_sector == 0, "live fields must be kept"
    print("  stale selection dropped OK")


def _check_map_list(scr, surface) -> None:
    """The open map list must be visible, clickable and clipped to the window.

    The picker chrome was drawn before the canvas, so the canvas blitted over
    the list: the rows were invisible and their clicks went to the canvas.
    """
    scr._map_list_open = False
    real = scr._scan_map_files()
    assert real, f"no maps to list in {MAPS_DIR}"
    assert len(real) == len(set(real)), f"a map is listed twice: {real}"
    # synthetic names: the row geometry is all that matters here, and a name
    # that does not exist keeps the loader from swapping the map mid-check
    scr._map_files = [f"probe{i}" for i in range(8)]
    scr.draw(surface)

    scr._map_list_open = True
    scr.draw(surface)
    assert scr._map_rows, "the open list must expose clickable rows"
    # the rows really do sit on top of the canvas, otherwise this proves nothing
    assert any(scr.canvas.colliderect(rect) for _n, rect in scr._map_rows), \
        "the fixture must exercise the overlap case"
    # a row deep under the canvas used to be painted over by it: the same pixel
    # with and without the list open has to come out different
    deep = [r for _n, r in scr._map_rows
            if scr.canvas.collidepoint(r.center) and r.centery > scr.canvas.y + 40]
    assert deep, "the fixture must have a row well inside the canvas"
    probe = deep[0].center
    with_list = surface.get_at(probe)[:3]
    scr._map_list_open = False
    scr.draw(surface)
    assert surface.get_at(probe)[:3] != with_list, \
        f"the open list is invisible at {probe}: the canvas is drawn over it"

    # a click on a row picks the map instead of reaching the canvas. The name
    # does not exist on disk, so the loader cannot swap the map underneath the
    # check: any edit to the geometry is then unambiguously a canvas click.
    ed, n_verts = scr.editor, len(scr.editor.verts)
    ed_status = scr.status
    scr._map_list_open = True
    scr.draw(surface)
    _row, rect = scr._map_rows[0]
    scr.tool = "vertex"                      # a leaked click would add a vertex
    scr.handle([_evt(pygame.MOUSEBUTTONDOWN, pos=rect.center, button=1)])
    assert scr._map_list_open is False, "picking a row must close the list"
    assert "Не найдено" in scr.status, scr.status
    assert scr.editor is ed, "the failed load must keep the current map"
    assert len(scr.editor.verts) == n_verts, "the click leaked into the canvas"
    scr.tool = "select"
    scr.status = ed_status
    scr.map_name = "sector1"

    # a click outside closes the list without hitting the canvas
    scr._map_list_open = True
    scr.draw(surface)
    scr.handle([_evt(pygame.MOUSEBUTTONDOWN, pos=(2, scr.height - 2), button=1)])
    assert scr._map_list_open is False

    # the name field still takes focus
    scr.handle([_evt(pygame.MOUSEBUTTONDOWN, pos=scr.name_box.center, button=1)])
    assert scr._name_focused
    scr._name_focused = False

    # a long list is clipped to the window and says what did not fit
    try:
        scr._map_list_open = True
        scr._map_files = real * 40
        scr.draw(surface)
        clipped = pygame.image.tostring(surface, "RGB")
        assert 0 < len(scr._map_rows) < len(scr._map_files), \
            f"expected a clipped list, got {len(scr._map_rows)} of {len(scr._map_files)}"
        last = scr._map_rows[-1][1]
        assert last.bottom + MAP_ROW_H <= scr.height, \
            f"the overflow row escapes the window: {last} in {scr.height}"
        # only the "…ещё N" row is left, and it is reported on screen
        scr._map_files = [n for n, _r in scr._map_rows]
        scr.draw(surface)
        assert clipped != pygame.image.tostring(surface, "RGB"), \
            "the maps that did not fit are not accounted for on screen"
    finally:
        scr._map_files = real
        scr._map_list_open = False
        scr.draw(surface)
    print("  map list overlays the canvas OK")

    # The checks above inject canvas coordinates straight into handle(), which is
    # the contract the screen expects. This one goes through the client's real
    # translation: pygame reports window pixels, and the 1000x700 canvas is
    # scaled and letterboxed on the way out, so a toolbar hit-box that drifts
    # from its drawn box would activate the neighbour at 4K.
    from client.client import GameClient
    from client.renderer import SCREEN_HEIGHT, SCREEN_WIDTH, Renderer

    class _Mapper:
        _menu_events = GameClient._menu_events

        def __init__(self, renderer):
            self.renderer = renderer

    renderer = Renderer((1000, 700))
    mapper = _Mapper(renderer)
    for size in ((640, 480), (1000, 700), (1600, 900), (3840, 2160)):
        renderer.set_window_size(size)
        dst = renderer.canvas_rect()
        for key, want in (("play", "play"), ("back", "back"), ("save", None)):
            rect = scr.buttons[key]
            win = (dst.x + int(rect.centerx * dst.w / SCREEN_WIDTH),
                   dst.y + int(rect.centery * dst.h / SCREEN_HEIGHT))
            before = (scr.map_name, scr.editor)
            got = scr.handle(mapper._menu_events(
                [_evt(pygame.MOUSEBUTTONDOWN, pos=win, button=1)]))
            assert got == want, f"{key} at {size} gave {got!r}, want {want!r}"
            # A toolbar click must never leak into the canvas it sits above.
            if want is None:
                assert scr.map_name == before[0] and scr.editor is before[1]
        # The bottom toolbar sits on the canvas row below it; a click in a black
        # bar must not reach any of it.
        if dst.x or dst.y:
            assert mapper._menu_events(
                [_evt(pygame.MOUSEBUTTONDOWN, pos=(0, 0), button=1)]) == []
    renderer.set_window_size((1000, 700))
    print("  toolbar hit through the window→canvas mapping OK")


def _check_heights_and_solid(scr, surface) -> None:
    """Numeric height fields, the solid toggle and the 2D wall/step overlay."""
    ed = scr.editor
    scr._set_sel(sector=0)
    scr.draw(surface)
    assert "floor" in scr._height_fields and "ceil" in scr._height_fields, \
        "sector properties must expose numeric height fields"

    # click the floor field, type a value, apply it with Enter
    ed.set_sector_heights(0, floor_h=0)
    scr.handle([_evt(pygame.MOUSEBUTTONDOWN,
                     pos=scr._height_fields["floor"].center, button=1)])
    assert scr._height_edit == "floor", scr._height_edit
    for ch in "48":
        scr.handle([_evt(pygame.KEYDOWN, key=pygame.K_a, unicode=ch)])
    scr.handle([_evt(pygame.KEYDOWN, key=pygame.K_RETURN)])
    assert scr._height_edit is None
    assert ed.sectors[0].floor_h == 48, ed.sectors[0].floor_h

    # a floor above the ceiling is clamped, and the panel says what was applied
    ed.set_sector_heights(0, floor_h=0, ceil_h=64)
    scr.draw(surface)
    scr.handle([_evt(pygame.MOUSEBUTTONDOWN,
                     pos=scr._height_fields["floor"].center, button=1)])
    for ch in "999":
        scr.handle([_evt(pygame.KEYDOWN, key=pygame.K_a, unicode=ch)])
    scr.handle([_evt(pygame.KEYDOWN, key=pygame.K_RETURN)])
    assert ed.sectors[0].floor_h == 64, ed.sectors[0].floor_h
    assert "64" in scr.status, scr.status
    s0 = ed.sectors[0]
    assert s0.floor_h <= s0.ceil_h, (s0.floor_h, s0.ceil_h)

    # the ±16 buttons share the clamp and report the applied pair
    ed.set_sector_heights(0, floor_h=0, ceil_h=128)
    scr._set_sel(sector=0)
    scr.draw(surface)
    scr._prop("floor_up")
    assert ed.sectors[0].floor_h == 16, ed.sectors[0].floor_h
    assert "пол 16" in scr.status, scr.status
    scr._prop("ceil_dn")
    scr._prop("ceil_dn")
    assert ed.sectors[0].ceil_h == 96, ed.sectors[0].ceil_h
    assert ed.sectors[0].floor_h == 16

    # a two-sided linedef gets a solid/portal toggle that changes the model
    v1 = ed.add_vertex(300, 300)
    v2 = ed.add_vertex(700, 300)
    li = ed.add_line(v1, v2)
    scr._set_sel(line=li)
    scr.draw(surface)
    assert "solid" not in scr._prop_chips, "one-sided linedef has no toggle"
    scr._prop("two")
    scr.draw(surface)
    assert "solid" in scr._prop_chips, scr._prop_chips
    scr._prop("solid")
    assert ed.lines[li].solid is True
    assert ed.sm.line_blocks(li) is True
    scr._prop("solid")
    assert ed.lines[li].solid is False

    # 2D overlay tells a wall, a walkable step and a portal apart
    fresh = SectorEditor(new_room(1600, 1600))
    fresh.begin_batch()
    a = fresh.add_vertex(100, 100)
    b = fresh.add_vertex(900, 100)
    c = fresh.add_vertex(900, 500)
    d = fresh.add_vertex(100, 500)
    ids = [fresh.add_line(*e) for e in ((a, b), (b, c), (c, d), (d, a))]
    fresh.end_batch()
    sid, errs = fresh.sector_from_contour(ids, floor_h=16, ceil_h=128)
    assert sid > 0 and not errs, errs
    fresh.set_line_solid(ids[0], True)          # solid: reads as a wall
    assert fresh.line_wall_kind(ids[0]) == "wall"
    fresh.set_line_solid(ids[0], False)
    assert fresh.line_wall_kind(ids[0]) == "step", "a 16 step is walkable"
    fresh.sectors[sid].floor_h = 64             # over STEP_HEIGHT: impassable
    assert fresh.line_wall_kind(ids[0]) == "wall"
    fresh.sectors[sid].floor_h = 0              # equal heights: plain portal
    assert fresh.line_wall_kind(ids[0]) == "open"
    # the one-sided room border is always a wall
    assert all(fresh.line_wall_kind(i) == "wall"
               for i, ld in enumerate(fresh.lines) if not ld.two_sided)
    # the overlay actually draws all three kinds
    scr._set_sel()
    scr.editor = fresh
    scr.frame_around()
    scr.draw(surface)
    print("  heights + solid UI OK")


def _check_panel_layout(surface) -> None:
    """The side panel must fit its contents at any reasonable window size.

    The thing palette used to be a fixed two-column grid, which pushed the
    base chips, the flats and the hints off the bottom of a 700px window.
    """
    for w, h in ((1000, 700), (1280, 720), (1600, 900), (1152, 648)):
        scr = SectorEditorScreen(w, h)
        ed = SectorEditor(new_room(1600, 1600))
        a = ed.add_vertex(100, 100)
        b = ed.add_vertex(900, 100)
        c = ed.add_vertex(900, 500)
        d = ed.add_vertex(100, 500)
        ids = [ed.add_line(*e) for e in ((a, b), (b, c), (c, d), (d, a))]
        ed.sector_from_contour(ids, floor_h=16, ceil_h=128)
        scr.editor = ed
        scr.frame_around()
        scr._set_sel(line=ids[0])
        surf = pygame.Surface((w, h))
        scr.draw(surf)
        rects = [r for items in scr._palette_items for _n, r, _c in items]
        rects += [r for r, _c in scr._base_chips.values()]
        rects += list(scr._tex_chips.values()) + list(scr._tex_rects.values())
        rects += list(scr._prop_chips.values()) + list(scr._tool_buttons.values())
        for rect in rects:
            assert scr.panel.contains(rect), \
                f"{w}x{h}: {rect} escapes the panel {scr.panel}"
        assert scr.panel.contains(pygame.Rect(scr.panel.x, scr._hint_y,
                                              scr.panel.width, HINT_BLOCK_H)), \
            f"{w}x{h}: hints at {scr._hint_y} fall outside {scr.panel}"
        # the thing palette still selects a type by click
        name, rect, _ch = scr._palette_items[0][0]
        scr._set_sel()
        scr.thing_type = name
        scr._panel_down(rect.center)
        assert scr.thing_type == name
        # a one-sided line shows the flats block instead of the textures
        scr.draw(surf)
        scr._set_sel(line=ids[1])
        ed.set_two_sided(ids[1], False)
        scr.draw(surf)
        assert scr._tex_side_idx() is None
    print("  panel layout fits OK")


def _check_tex_palette(scr, surface) -> None:
    """The wall texture palette writes the chosen slot of the chosen side."""
    ed = SectorEditor(new_room(1600, 1600))
    a = ed.add_vertex(100, 100)
    b = ed.add_vertex(900, 100)
    c = ed.add_vertex(900, 500)
    d = ed.add_vertex(100, 500)
    ids = [ed.add_line(*e) for e in ((a, b), (b, c), (c, d), (d, a))]
    sid, errs = ed.sector_from_contour(ids, floor_h=16, ceil_h=128)
    assert sid > 0 and not errs, errs
    li = ids[0]
    assert ed.lines[li].two_sided
    scr.editor = ed
    scr.frame_around()

    # no palette while nothing (or a one-sided line) is selected
    scr._set_sel(line=ids[1])
    ed.set_two_sided(ids[1], False)
    scr.draw(surface)
    assert scr._tex_side_idx() is None, "one-sided linedef has no texture palette"
    assert not scr._base_chips_y > scr.panel.bottom - BASE_CHIPS_H, \
        "base chips must stay inside the panel"

    # a two-sided linedef exposes the palette, defaulting to the front/lower
    scr._set_sel(line=li)
    scr.draw(surface)
    assert "bridge" in scr._tex_rects, sorted(scr._tex_rects)[:4]
    assert (scr._tex_side, scr._tex_target) == ("front", "lower")
    for rect in list(scr._tex_rects.values()) + list(scr._tex_chips.values()):
        assert scr.panel.contains(rect), \
            f"texture palette escapes the panel: {rect} not in {scr.panel}"
    front = ed.lines[li].front
    back = ed.lines[li].back
    assert ed.sides[front].lower == "-"

    # click "bridge" -> front side, lower lip
    scr.handle([_evt(pygame.MOUSEBUTTONDOWN,
                     pos=scr._tex_rects["bridge"].center, button=1)])
    assert ed.sides[front].lower == "bridge", ed.sides[front].lower
    assert "bridge" in scr.status, scr.status
    # undo removes just that assignment
    scr._button("undo")
    assert ed.sides[front].lower == "-"

    # switch slot to upper, then to the back side
    scr.draw(surface)
    scr.handle([_evt(pygame.MOUSEBUTTONDOWN,
                     pos=scr._tex_chips["upper"].center, button=1)])
    assert scr._tex_target == "upper"
    scr.handle([_evt(pygame.MOUSEBUTTONDOWN,
                     pos=scr._tex_rects["platform"].center, button=1)])
    assert ed.sides[front].upper == "platform", ed.sides[front].upper
    # the target slot is kept when only the side changes
    scr.draw(surface)
    scr.handle([_evt(pygame.MOUSEBUTTONDOWN,
                     pos=scr._tex_chips["back"].center, button=1)])
    assert scr._tex_side == "back" and scr._tex_target == "upper"
    scr.draw(surface)
    scr.handle([_evt(pygame.MOUSEBUTTONDOWN,
                     pos=scr._tex_rects["stone"].center, button=1)])
    assert ed.sides[back].upper == "stone", ed.sides[back].upper
    assert ed.sides[front].lower == "-", "the front lip was not touched"
    # the back side lower lip is assignable from the same palette
    scr.draw(surface)
    scr.handle([_evt(pygame.MOUSEBUTTONDOWN,
                     pos=scr._tex_chips["lower"].center, button=1)])
    assert scr._tex_target == "lower"
    scr.draw(surface)
    scr.handle([_evt(pygame.MOUSEBUTTONDOWN,
                     pos=scr._tex_rects["wood"].center, button=1)])
    assert ed.sides[back].lower == "wood", ed.sides[back].lower
    # back on the front side, the earlier assignment is still there
    scr.draw(surface)
    scr.handle([_evt(pygame.MOUSEBUTTONDOWN,
                     pos=scr._tex_chips["front"].center, button=1)])
    assert scr._tex_side == "front"
    scr.draw(surface)
    scr.handle([_evt(pygame.MOUSEBUTTONDOWN,
                     pos=scr._tex_rects["wall"].center, button=1)])
    assert ed.sides[front].lower == "wall", ed.sides[front].lower
    assert ed.sides[front].upper == "platform", "the upper lip was not reset"
    assert ed.sides[back].lower == "wood", "the back lip was not reset"
    assert ed.sides[back].upper == "stone"
    assert ed.sides[front].lower == "wall"
    # the palette survives deselect/reselect with the assigned values in place
    scr._set_sel()
    scr.draw(surface)
    assert scr._tex_side_idx() is None
    scr._set_sel(line=li)
    scr.draw(surface)
    assert not ed.validate(), ed.validate()
    # and the assigned names are ones the .mts format accepts
    from shared.text_map import WALL_TEXTURES
    for sd in ed.sides:
        for name in (sd.lower, sd.upper, sd.mid):
            if name and name != "-":
                assert name in WALL_TEXTURES, name
    print("  wall tex palette OK")


def _check_mts_round_trip(scr) -> None:
    """The editor map survives `.mts` export → import without losses."""
    from shared import text_map

    ed = scr.editor
    ed.add_thing(400.0, 500.0, "soldier")
    ed.set_thing_angle(len(ed.things) - 1, 135)
    sig = _geometry(ed.sm)
    scr.map_name = "smoke_mts_check"
    path = scr._mts_path("mts")
    try:
        scr._button("save_mts")
        assert "Сохранено" in scr.status, scr.status
        assert os.path.exists(path), path
        scr._button("load_mts")
        assert "Загружено" in scr.status, scr.status
        assert _geometry(scr.editor.sm) == sig
        # the text form is canonical: parse(render(m)) == m
        text = text_map.render(scr.editor.sm)
        assert _geometry(text_map.parse(text, validate=False)) == sig
        assert text_map.render(text_map.parse(text, validate=False)) == text
        scr.draw(pygame.display.get_surface())
    finally:
        scr.map_name = "sector1"
        if os.path.exists(path):
            os.remove(path)


def _geometry(sm):
    """Order-insensitive geometry/thing fingerprint of a `SectorMap`."""
    pos = sorted({(round(v.x, 2), round(v.y, 2)) for v in sm.verts})
    index = {p: i for i, p in enumerate(pos)}

    def vid(i):
        return index[(round(sm.verts[i].x, 2), round(sm.verts[i].y, 2))]

    lines = []
    for ld in sm.linedefs:
        a, b = vid(ld.v1), vid(ld.v2)
        front = sm.sidedefs[ld.front]
        back = sm.sidedefs[ld.back] if ld.back >= 0 else None
        lines.append((
            (a, b) if a <= b else (b, a),
            (front.sector, front.mid, front.upper, front.lower),
            None if back is None else (back.sector, back.mid, back.upper, back.lower),
            ld.special, ld.tag, ld.flags,
        ))
    sectors = sorted((s.floor_h, s.ceil_h, s.floor_tex, s.ceil_tex, s.light,
                      s.special, s.tag) for s in sm.sectors)
    things = sorted((round(t.x, 2), round(t.y, 2), t.type, t.angle, round(t.z, 2))
                    for t in sm.things)
    return (pos, sorted(lines), sectors, things)


if __name__ == "__main__":
    main()