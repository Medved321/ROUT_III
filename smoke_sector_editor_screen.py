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

sys.stdout.reconfigure(encoding="utf-8")  # labels contain arrows

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pygame  # noqa: E402

from client.sector_editor import SectorEditor, new_room  # noqa: E402
from shared.sector_map import (  # noqa: E402
    LineDef, Sector, SectorMap, SideDef, Vertex,
)
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
    _check_front_side_invariant(scr, surface)
    _check_side_toggle_refusals_reach_the_status_line(scr, surface)
    _check_load_reports_a_repair(scr, surface)
    _check_selection_survives_nothing(scr, surface)
    _check_map_list(scr, surface)
    _check_create_status(scr, surface)
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
    # The map fixtures hold a single player1, so deleting the last thing would
    # remove the only spawn and invalidate the map for the "play" check below.
    # Append a throwaway so the last thing is disposable -- the array-shrink
    # path under test is the same, the spawn stays put.
    ed.add_thing(0.0, 0.0, "imp")
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
    # draw() persists the current map; the stress fixture here is degenerate, so
    # stub the write out and keep the shared maps/sector1.mts on disk intact.
    real_save = scr._save
    scr._save = lambda: None
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
        scr._save = real_save
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
    # "play" saves the current map to maps/<map_name>.mt1 when it is valid. The
    # hit-box check only reads the returned action, so the write is stubbed out:
    # otherwise every run would overwrite the shared fixture map on disk.
    real_save = scr._save
    scr._save = lambda: None
    try:
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
            # The bottom toolbar sits on the canvas row below it; a click in a
            # black bar must not reach any of it.
            if dst.x or dst.y:
                assert mapper._menu_events(
                    [_evt(pygame.MOUSEBUTTONDOWN, pos=(0, 0), button=1)]) == []
    finally:
        scr._save = real_save
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

    # a two-sided linedef gets a solid/portal toggle that changes the model.
    # The portal needs a SECOND sector: the wall is drawn from inside the
    # current one, so a bare «2-ст.» here would aim the back side at the very
    # sector the front belongs to, which is refused as a self-portal.
    ed.cur_sector = 0                         # the wall is drawn inside room 0
    v1 = ed.add_vertex(300, 300)
    v2 = ed.add_vertex(700, 300)
    li = ed.add_line(v1, v2)
    ed.add_sector(floor_h=32, ceil_h=128, light=90)
    ed.cur_sector = len(ed.sectors) - 1   # now standing in the other room
    assert ed.sides[ed.lines[li].front].sector != ed.cur_sector, \
        "fixture: the portal would aim at the wall's own sector"
    scr._set_sel(line=li)
    scr.draw(surface)
    assert "solid" not in scr._prop_chips, "one-sided linedef has no toggle"
    scr._prop("two")
    assert ed.lines[li].back >= 0, scr.status
    scr.draw(surface)
    assert "solid" in scr._prop_chips, scr._prop_chips
    scr._prop("solid")
    assert ed.lines[li].solid is True
    assert ed.sm.line_blocks(li) is True
    scr._prop("solid")
    assert ed.lines[li].solid is False

    # 2D overlay tells a wall, a walkable step and a portal apart
    fresh = SectorEditor(new_room(1600, 1600))
    a = fresh.add_vertex(100, 100)
    b = fresh.add_vertex(900, 100)
    c = fresh.add_vertex(900, 500)
    d = fresh.add_vertex(100, 500)
    ids = [fresh.add_line(*e) for e in ((a, b), (b, c), (c, d), (d, a))]
    sid, errs = fresh.sector_from_contour(ids, floor_h=16, ceil_h=128)
    assert sid > 0 and not errs, errs
    # every thick wall (both faces) is one-sided and therefore a wall
    assert all(fresh.line_wall_kind(i) == "wall"
               for i, ld in enumerate(fresh.lines) if not ld.two_sided)
    # a hand-built portal gives the step/open branches
    p = fresh.add_vertex(200, 900)
    q = fresh.add_vertex(900, 900)
    step = fresh.add_line(p, q)
    fresh.add_sector(floor_h=16, ceil_h=128, light=90)
    back = len(fresh.sectors) - 1
    assert fresh.set_two_sided(step, True, back_sector=back) == []
    assert fresh.line_wall_kind(step) == "step", "a 16 step is walkable"
    fresh.set_line_solid(step, True)            # solid: reads as a wall
    assert fresh.line_wall_kind(step) == "wall"
    fresh.set_line_solid(step, False)
    assert fresh.line_wall_kind(step) == "step"
    fresh.sectors[0].floor_h = 64               # over STEP_HEIGHT: impassable
    assert fresh.line_wall_kind(step) == "wall"
    fresh.sectors[0].floor_h = 16               # equal heights: plain portal
    fresh.sectors[back].floor_h = 16
    assert fresh.line_wall_kind(step) == "open"
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
        # a one-sided line has no lips: only the main texture chip is shown
        scr.draw(surf)
        spare = ed.add_line(*(ed.add_vertex(200, 900), ed.add_vertex(900, 900)))
        scr._set_sel(line=spare)
        scr.draw(surf)
        assert scr._tex_side_idx() == ed.lines[spare].front
        assert scr._tex_visible_keys() == ("mid",)
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
    outer = ids[0]
    assert not ed.lines[outer].two_sided
    scr.editor = ed
    scr.frame_around()

    # A one-sided thick wall has exactly one texture: the main one, on its
    # only side.  Lips belong to a two-sided portal and are not offered.
    scr._set_sel(line=outer)
    scr.draw(surface)
    assert scr._tex_visible_keys() == ("mid",)
    assert (scr._tex_side, scr._tex_target) == ("front", "mid")
    assert "bridge" in scr._tex_rects, sorted(scr._tex_rects)[:4]
    assert scr._tex_side_idx() == ed.lines[outer].front
    for rect in list(scr._tex_rects.values()) + list(scr._tex_chips.values()):
        assert scr.panel.contains(rect), \
            f"texture palette escapes the panel: {rect} not in {scr.panel}"
    assert "lower" not in scr._tex_chips and "front" not in scr._tex_chips
    front = ed.lines[outer].front
    assert ed.sides[front].mid == "wall"
    scr.handle([_evt(pygame.MOUSEBUTTONDOWN,
                     pos=scr._tex_rects["bridge"].center, button=1)])
    assert ed.sides[front].mid == "bridge", ed.sides[front].mid
    assert "Текстура mid" in scr.status, scr.status
    scr._button("undo")
    assert ed.sides[front].mid == "wall"

    # A two-sided portal exposes the full palette, defaulting to front/lower.
    p = ed.add_vertex(200, 900)
    q = ed.add_vertex(900, 900)
    li = ed.add_line(p, q)
    ed.add_sector(floor_h=16, ceil_h=128, light=90)
    assert ed.set_two_sided(li, True, back_sector=len(ed.sectors) - 1) == []
    scr._set_sel(line=li)
    scr.draw(surface)
    assert "bridge" in scr._tex_rects, sorted(scr._tex_rects)[:4]
    assert (scr._tex_side, scr._tex_target) == ("front", "lower")
    assert set(scr._tex_visible_keys()) == {"front", "back", "mid", "upper", "lower"}
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
    # The MAIN wall texture (mid) is now a first-class slot.  A solid wall is
    # painted from its mid texture — the renderer draws no lower/upper lip for an
    # opaque wall — so selecting it must default the palette to STENA (mid), and
    # a click must land in sd.mid.
    assert ed.set_line_solid(li, True) is True
    scr._set_sel(line=li)
    scr.draw(surface)
    assert scr._tex_target == "mid", scr._tex_target
    assert "mid" in scr._tex_chips, sorted(scr._tex_chips)
    assert scr.panel.contains(scr._tex_chips["mid"]), scr._tex_chips["mid"]
    scr.handle([_evt(pygame.MOUSEBUTTONDOWN,
                     pos=scr._tex_rects["martyr"].center, button=1)])
    assert ed.sides[ed.lines[li].front].mid == "martyr", \
        ed.sides[ed.lines[li].front].mid
    assert "martyr" in scr.status, scr.status
    # a non-solid portal goes back to defaulting on the step lip (lower)
    ed.set_line_solid(li, False)
    scr._set_sel(line=li)
    assert scr._tex_target == "lower", scr._tex_target
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


def _check_front_side_invariant(scr, surface) -> None:
    """The panel must not pretend a refused edit worked, and a repair must speak.

    These are the two status strings the reported damage went without: ``Flip``
    on a one-sided wall was accepted silently, and a map repaired on load looked
    exactly like a clean one.  Both are checked through the real widget, because
    the widget is what decides whether the author ever hears about it.
    """
    ed = SectorEditor(new_room(1600, 1600))
    a = ed.add_vertex(100, 100)
    b = ed.add_vertex(900, 100)
    c = ed.add_vertex(900, 500)
    d = ed.add_vertex(100, 500)
    ids = [ed.add_line(*e) for e in ((a, b), (b, c), (c, d), (d, a))]
    ed.sector_from_contour(ids, floor_h=16, ceil_h=128)
    # a genuine one-sided wall, drawn rather than sealed: sealing a portal is
    # refused whenever the sector behind it would lose its contour
    single = ed.add_line(*(ed.add_vertex(100, 700), ed.add_vertex(900, 700)))
    assert not ed.line_two_sided(single)
    scr.editor = ed
    scr.frame_around()

    # Flip on the one-sided wall: refused, explained, and nothing touched
    before = ed.sm.to_json()
    hist = len(ed._history)
    scr._set_sel(line=single)
    scr._prop("flip")
    assert scr.status, "a refused Flip said nothing"
    assert "двусторонней" in scr.status, scr.status
    assert ed.sm.to_json() == before, "a refused Flip changed the map"
    assert len(ed._history) == hist, "a refused Flip left an undo entry"
    scr.draw(surface)

    # a wall the old editor left frontless: the panel calls it one-sided (the
    # shared property would say two-sided), offers no portal textures, and "1-ст."
    # is what brings the sector back — with a status that says so.  The damage is
    # applied directly to a fresh one-sided wall: it is what the old Flip left on
    # disk, not something the editor can be asked to do any more.
    broken = ed.add_line(*(ed.add_vertex(100, 900), ed.add_vertex(900, 900)))
    ld = ed.lines[broken]
    assert ld.back < 0, "precondition: the wall starts one-sided"
    ld.v1, ld.v2 = ld.v2, ld.v1
    ld.front, ld.back = ld.back, ld.front
    ed.sm.invalidate()
    assert ld.two_sided, "precondition: the shared property sees a back side"
    assert ld.front < 0, "precondition: the wall is frontless now"
    scr._set_sel(line=broken)
    assert "1ст." in scr._tool_selection_label(), scr._tool_selection_label()
    assert scr._tex_side_idx() is None, "a frontless wall must not be textured"
    scr.draw(surface)
    footprint = len(ed.sides)
    scr._prop("one")
    assert "восстановлена" in scr.status, scr.status
    assert len(ed.sides) == footprint, "the repair dropped a side"
    assert all(l.front >= 0 for l in ed.lines), "a wall is still frontless"
    assert not ed.validate(), ed.validate()
    scr.draw(surface)
    print("  refused Flip and wall repair both reach the status line")


def _two_rooms_map():
    """Two rooms side by side, split by the one wall they share.

    Built by hand: ``sector_from_contour`` now makes a thick room whose outer
    face is one-sided, so a contour can no longer donate a portal edge.
    """
    sm = SectorMap()
    sm.sectors.append(Sector(floor_h=0, ceil_h=128, light=90))
    sm.sectors.append(Sector(floor_h=64, ceil_h=128, light=120))
    for x, y in ((0.0, 0.0), (800.0, 0.0), (800.0, 1600.0), (0.0, 1600.0),
                 (1600.0, 0.0), (1600.0, 1600.0)):
        sm.verts.append(Vertex(x, y))

    def side(sector):
        sm.sidedefs.append(SideDef(sector=sector))
        return len(sm.sidedefs) - 1

    sm.linedefs.append(LineDef(0, 1, front=side(0)))                    # left
    sm.linedefs.append(LineDef(1, 2, front=side(0), back=side(1)))     # shared
    sm.linedefs.append(LineDef(2, 3, front=side(0)))
    sm.linedefs.append(LineDef(3, 0, front=side(0)))
    sm.linedefs.append(LineDef(1, 4, front=side(1)))                    # right
    sm.linedefs.append(LineDef(4, 5, front=side(1)))
    sm.linedefs.append(LineDef(5, 2, front=side(1)))
    sm.invalidate()
    return sm


def _check_side_toggle_refusals_reach_the_status_line(scr, surface) -> None:
    """Both sidedness toggles must be able to SAY no, and the map must survive it.

    ``1-ст.`` on the wall two rooms share used to drop the back side, and with it
    the only edge closing the far room's contour: the sector went void, its
    height vanished from the panel, and nothing said so.  ``2-ст.`` aimed at
    whatever sector was picked last, which can be the wall's own — a self-portal
    that reads, to the staircase, like two floors at the same height.
    """
    ed = SectorEditor(_two_rooms_map())
    scr.editor = ed
    scr.frame_around()

    divider = 1
    assert ed.lines[divider].two_sided, "precondition: a portal between rooms"
    heights = [ed.sectors[i].floor_h for i in range(len(ed.sectors))]
    alive = {i for i in range(len(ed.sectors)) if ed.sm.has_polygon(i)}
    before = ed.sm.to_json()

    # «1-ст.» on the shared wall: refused, the far room keeps its floor
    scr._set_sel(line=divider)
    scr._prop("one")
    assert "контур" in scr.status, scr.status
    assert "сектора" in scr.status, scr.status
    assert ed.sm.to_json() == before, "a refused toggle changed the map"
    assert {i for i in range(len(ed.sectors))
            if ed.sm.has_polygon(i)} == alive, "a room went void"
    assert [ed.sectors[i].floor_h for i in range(len(ed.sectors))] == heights

    # «2-ст.» aimed at the wall's own sector: refused by name.  A fresh line is
    # one-sided, so the panel really is being asked to add a back side.
    stub = ed.add_line(*(ed.add_vertex(200, 1200), ed.add_vertex(800, 1200)))
    assert not ed.line_two_sided(stub), "precondition: one-sided wall"
    ed.cur_sector = ed.sides[ed.lines[stub].front].sector
    before_stub = ed.sm.to_json()          # after the line above was added
    scr._set_sel(line=stub)
    scr._prop("two")
    assert "тот же сектор" in scr.status, scr.status
    assert ed.lines[stub].back == -1, "the self-portal is there anyway"
    assert ed.sm.to_json() == before_stub, "a refused toggle changed the map"
    scr.draw(surface)
    print("  both sidedness toggles refuse by name, and the rooms survive")


def _check_load_reports_a_repair(scr, surface) -> None:
    """A map that had to be repaired must not open looking clean.

    ``SectorEditor`` repairs the front-side invariant on load, so the file on
    disk is still broken; the status line is where the author learns that, and
    ``validate()`` has to speak AFTER the repair rather than before it.  Both
    load paths are covered, since they are separate code: the ``.mt1`` one used
    to report nothing at all.  The repair itself can only arrive through a
    binary map — the ``.mts`` text form writes one side per wall and cannot
    express a missing front side — so that path is checked for the validation
    half of the message.
    """
    from shared import text_map
    from client.sector_editor import save_mt1_with_bsp

    ed = SectorEditor(new_room(1600, 1600))
    a = ed.add_vertex(100, 100)
    b = ed.add_vertex(900, 100)
    c = ed.add_vertex(900, 500)
    ids = [ed.add_line(*e) for e in ((a, b), (b, c), (c, a))]
    ed.sector_from_contour(ids, floor_h=16, ceil_h=128)
    wall = ed.add_line(*(ed.add_vertex(100, 500), ed.add_vertex(900, 500)))
    ld = ed.lines[wall]
    assert ld.back < 0, "precondition: the wall starts one-sided"
    # A healthy .mts written while the map is still valid, reopened at the end.
    # It keeps its spawn so it validates clean.
    healthy_mts = text_map.render(ed.sm)
    ed.things.clear()
    # what the .mts path is checked for: a validation error it used to swallow
    # (no spawn), which the text form can express and the repair cannot fix
    broken_mts = text_map.render(ed.sm)

    # the damage the old editor could leave: front side gone, back side alive.
    # The .mt1 below carries it; the .mts texts above stay parseable.
    ld.v1, ld.v2 = ld.v2, ld.v1
    ld.front, ld.back = ld.back, ld.front
    ed.sm.invalidate()
    assert [e for e in ed.validate() if "sidedef" in e], ed.validate()

    name = "smoke_broken_load"
    scr.map_name = name                 # BEFORE the paths: _mts_path reads it
    mts_path = scr._mts_path("mts")
    mt1_path = scr._mts_path("mt1")
    os.makedirs(os.path.dirname(mts_path), exist_ok=True)
    with open(mts_path, "w", encoding="utf-8") as fh:
        fh.write(broken_mts)
    save_mt1_with_bsp(ed.sm, mt1_path)
    try:
        # (a) the .mt1 path: repaired on load, and the status names the wall
        scr._load()
        assert "Загружено" in scr.status, scr.status
        assert "починено" in scr.status, scr.status
        assert "ошибок" in scr.status, "validate() must speak after the repair"
        assert scr.editor.repair_notes, "the repair was not recorded"
        assert all(l.front >= 0 for l in scr.editor.lines), \
            "a wall is still frontless after load"
        assert not [e for e in scr.editor.validate() if "sidedef" in e], \
            scr.editor.validate()
        scr.draw(surface)

        # (b) the .mts path reports validation errors, which it used to swallow
        scr._load_mts()
        assert "Загружено" in scr.status, scr.status
        assert "ошибок" in scr.status, scr.status
        assert scr.editor.repair_notes == [], scr.editor.repair_notes
        scr.draw(surface)
    finally:
        for p in (mts_path, mt1_path):
            if os.path.exists(p):
                os.remove(p)
        scr.map_name = "sector1"

    # (c) a healthy map opens with the plain message, no repair noise.  The
    # healthy file is rebuilt here under its own name so this check does not
    # depend on whatever the shared maps/sector1.mts holds on disk.
    healthy_path = os.path.join(MAPS_DIR, "smoke_healthy_load.mts")
    with open(healthy_path, "w", encoding="utf-8") as fh:
        fh.write(healthy_mts)
    scr.map_name = "smoke_healthy_load"
    try:
        scr._load_mts()
        assert scr.status == "Загружено: maps/smoke_healthy_load.mts", scr.status
        assert scr.editor.repair_notes == [], scr.editor.repair_notes
    finally:
        scr.map_name = "sector1"
        if os.path.exists(healthy_path):
            os.remove(healthy_path)
    print("  load status names the repair, and stays quiet on a healthy map")


def _check_create_status(scr, surface) -> None:
    """Creating through the screen builds a thick room and says so.

    The sector is born with its inner loop inset by ``WALL_THICKNESS``: the
    surrounding sector keeps a hole where the old contour was, and the strip
    between the two loops is the solid wall body.
    """
    from client.sector_editor import WALL_THICKNESS

    ed = scr.editor = SectorEditor(new_room(1600, 1600))
    n0 = len(ed.lines)
    scr.contour = []
    scr.frame_around()

    def box(x0, y0, x1, y1):
        v1 = ed.add_vertex(x0, y0)
        v2 = ed.add_vertex(x1, y0)
        v3 = ed.add_vertex(x1, y1)
        v4 = ed.add_vertex(x0, y1)
        return [ed.add_line(a, b) for a, b in
                ((v1, v2), (v2, v3), (v3, v4), (v4, v1))]

    scr.contour = box(300, 300, 700, 700)
    scr._create_sector()
    assert scr.status == "Сектор 1 создан (толщина стены 16)", scr.status
    n = len(ed.lines) - n0
    assert n == 8, f"4 contour + 4 inner lines: {n}"
    inner = [i for i, ld in enumerate(ed.lines)
             if 0 <= ld.front < len(ed.sides)
             and ed.sides[ld.front].sector == 1 and ld.back < 0]
    assert len(inner) == 4, inner
    assert ed.sm.find_sector_at(500, 500) == 1, "the room interior"
    assert ed.sm.find_sector_at(500, 300 + WALL_THICKNESS / 2) == -1, \
        "the strip between the loops is the wall body"
    assert not ed.validate(), ed.validate()
    assert ed.sm.has_polygon(0) and ed.sm.has_polygon(1), "both contours alive"
    scr.draw(surface)
    print("  create status    OK")


if __name__ == "__main__":
    main()