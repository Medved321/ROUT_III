"""Smoke: sector editor model (tasks 4.1, 4.2).

Vertex/line/sector/thing operations keep the map valid; sector-from-contour
creates a real exploitable sector; imp+med things round-trip through JSON;
undo xN / redo xN returns the exact JSON before/after.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shared.sector_map import (  # noqa: E402
    MAX_SECTOR_HEIGHT, MIN_SECTOR_HEIGHT, SPECIAL_DOORS, LineDef, Sector,
    SectorMap, SideDef, Thing, Vertex,
)
from client.sector_editor import (  # noqa: E402
    GRID_DEFAULT, WALL_THICKNESS, SectorEditor, clamp_sector_heights, new_room,
    save_mt1_with_bsp,
)


def test_vertex_ops():
    ed = SectorEditor()
    n_verts0 = len(ed.verts)
    v = ed.add_vertex(201, 302)
    assert ed.verts[v].x == 208 and ed.verts[v].y == 304, "snap off"
    ed.snap_on = False
    ed.move_vertex(v, 300.5, 100.25)
    assert ed.verts[v].x == 300.5 and ed.verts[v].y == 100.25
    ed.snap_on = True
    assert not ed.validate(), ed.validate()
    ed.undo()  # revert the move
    assert ed.verts[v].x == 208 and ed.verts[v].y == 304
    ed.redo()
    assert ed.verts[v].x == 300.5 and ed.verts[v].y == 100.25
    ed.undo()  # revert the move again
    ed.undo()  # revert the add_vertex
    assert len(ed.verts) == n_verts0
    ed.redo()
    assert 0 <= v < len(ed.verts) and ed.verts[v].x == 208
    # delete a lone floating vertex
    ed.delete_vertex(v)
    assert len(ed.verts) == n_verts0
    assert not ed.validate(), ed.validate()
    ed.undo()
    assert len(ed.verts) == n_verts0 + 1 and ed.verts[n_verts0].x == 208
    ed.snap_on = True
    print("  vertex ops       OK")


def test_line_ops():
    ed = SectorEditor()
    a = ed.add_vertex(400, 400)
    b = ed.add_vertex(800, 400)
    li = ed.add_line(a, b)
    assert ed.lines[li].v1 == a and ed.lines[li].v2 == b
    assert not ed.lines[li].two_sided
    assert not ed.validate(), ed.validate()
    # split into two
    n_lines0 = len(ed.lines)
    nv = ed.split_line_at(li, 600, 400)
    assert len(ed.lines) == n_lines0 + 1
    assert all(not l.two_sided for l in ed.lines)
    assert any(nv in (l.v1, l.v2) for l in ed.lines), "split vertex lost"
    assert not ed.validate(), ed.validate()
    # two-sided + flip + special
    ed.add_sector(floor_h=-32, ceil_h=128, light=100)
    ed.set_two_sided(0, True, back_sector=1)
    assert ed.lines[0].two_sided
    ed.flip_line(0)
    assert not ed.validate(), ed.validate()
    ed.set_line_special(0, special=10, tag=3)
    assert ed.lines[0].special == 10 and ed.lines[0].tag == 3
    # delete the vertex we split with: removes both interior lines
    ed.delete_vertex(nv)
    assert len(ed.lines) == n_lines0 - 1, len(ed.lines)
    assert not ed.validate(), ed.validate()
    # guard rails
    try:
        ed.add_line(-1, 0)
        raise AssertionError("bad vertex accepted")
    except ValueError:
        pass
    print("  line ops         OK")


def test_wall_thickness_constant():
    assert WALL_THICKNESS == GRID_DEFAULT == 16.0
    print("  wall thickness   OK")


def _inner_lines(ed, sid):
    return [i for i, ld in enumerate(ed.lines)
            if 0 <= ld.front < len(ed.sides)
            and ed.sides[ld.front].sector == sid and ld.back < 0]


def test_sector_from_contour():
    ed = SectorEditor(new_room(1600, 1600))
    v1 = ed.add_vertex(400, 400)
    v2 = ed.add_vertex(800, 400)
    v3 = ed.add_vertex(800, 800)
    v4 = ed.add_vertex(400, 800)
    l1 = ed.add_line(v1, v2)   # top
    l2 = ed.add_line(v2, v3)   # right
    l3 = ed.add_line(v3, v4)   # bottom
    l4 = ed.add_line(v4, v1)   # left
    before = ed.sm.to_json()
    hist = len(ed._history)
    sid, errs = ed.sector_from_contour([l1, l2, l3, l4], floor_h=-64, light=120)
    assert sid == 1 and not errs, errs
    assert len(ed._history) == hist + 1, "creation must be one undo step"
    assert not ed.validate(), ed.validate()
    assert ed.sm.find_sector_at(600, 600) == 1
    assert ed.sm.find_sector_at(200, 200) == 0
    assert ed.sm.find_sector_at(600, 1200) == 0
    # the contour is the OUTER face: one-sided, looking back at the room
    for li in (l1, l2, l3, l4):
        ld = ed.lines[li]
        assert ld.back < 0, (li, ld)
        assert ed.sides[ld.front].sector == 0, (li, ed.sides[ld.front].sector)
    # the inner loop is A's own wall, one-sided as well
    inner = _inner_lines(ed, 1)
    assert len(inner) == 4, inner
    assert all(ed.lines[li].back < 0 for li in inner)
    # the 16px strip between the loops is the wall body: void, and blocking
    assert ed.sm.find_sector_at(408, 600) == -1
    assert ed.sm.find_sector_at(392, 600) == 0
    for li in list((l1, l2, l3, l4)) + inner:
        assert ed.sm.line_blocks(li), li
    assert ed.sm.has_polygon(0) and ed.sm.has_polygon(1)
    # inner walls inherit the contour texture
    assert all(ed.sides[ed.lines[li].front].mid == "wall" for li in inner)
    # a 400x400 contour inset by 16 is 368x368
    pts = set()
    for li in inner:
        pts.add((ed.verts[ed.lines[li].v1].x, ed.verts[ed.lines[li].v1].y))
        pts.add((ed.verts[ed.lines[li].v2].x, ed.verts[ed.lines[li].v2].y))
    assert pts == {(416.0, 416.0), (784.0, 416.0),
                   (784.0, 784.0), (416.0, 784.0)}, pts
    # props
    ed.set_sector_props(1, floor_tex="lava", light=200)
    assert ed.sectors[1].floor_tex == "lava" and ed.sectors[1].light == 200
    ed.undo()
    assert ed.sectors[1].floor_tex == "floor"
    assert ed.sm.find_sector_at(600, 600) == 1
    ed.undo()
    assert len(ed.sectors) == 1 and ed.sm.find_sector_at(600, 600) == -1, \
        "the contour interior is a hole again"
    assert ed.sm.find_sector_at(400, 1300) == 0, "room must stay intact"
    assert ed.sm.to_json() == before, "undo must restore the drawing exactly"
    ed.redo()
    assert len(ed.sectors) == 2 and ed.sm.find_sector_at(600, 600) == 1
    print("  sector contour   OK")


def test_sector_from_contour_clockwise():
    """A CW contour is normalized, not rejected: the outside still gets front."""
    ed = SectorEditor(new_room(1600, 1600))
    vs = [ed.add_vertex(x, y) for x, y in
          ((600, 600), (600, 664), (664, 664), (664, 600))]
    ids = [ed.add_line(vs[i], vs[(i + 1) % 4]) for i in range(4)]
    for li in ids:
        ed.sides[ed.lines[li].front].mid = "brick"
    sid, errs = ed.sector_from_contour(ids, floor_h=0)
    assert sid == 1 and not errs, errs
    assert ed.sm.find_sector_at(632, 632) == 1
    assert ed.sm.find_sector_at(632, 608) == -1
    assert ed.sm.find_sector_at(580, 580) == 0
    for li in ids:
        ld = ed.lines[li]
        assert ld.back < 0 and ed.sides[ld.front].sector == 0
        assert ed.sides[ld.front].mid == "brick"
    inner = _inner_lines(ed, 1)
    assert len(inner) == 4
    assert all(ed.sides[ed.lines[li].front].mid == "brick" for li in inner)
    assert not ed.validate(), ed.validate()
    print("  contour CW       OK")


def test_sector_from_contour_portal():
    """A two-sided contour edge stays a portal and is not duplicated."""
    sm = new_room(1600, 1600)
    ed = SectorEditor(sm)
    ed.sectors.append(Sector())              # old interior behind the door
    base = len(ed.verts)
    for x, y in ((600, 600), (664, 600), (664, 664), (600, 664)):
        ed.verts.append(Vertex(x, y))
    ids = []
    for i in range(4):
        if i == 0:                           # top edge: the door
            f = len(ed.sides)
            ed.sides.append(SideDef(mid="door_D", sector=0))
            b = len(ed.sides)
            ed.sides.append(SideDef(mid="door_D", sector=1))
            ed.lines.append(LineDef(base + i, base + (i + 1) % 4,
                                    special=SPECIAL_DOORS, front=f, back=b))
        else:
            ed.sides.append(SideDef(sector=0))
            ed.lines.append(LineDef(base + i, base + (i + 1) % 4,
                                    front=len(ed.sides) - 1))
        ids.append(len(ed.lines) - 1)
    sm.invalidate()
    sid, errs = ed.sector_from_contour(ids, floor_h=0)
    assert sid == 2 and not errs, errs
    assert not ed.validate(), ed.validate()
    door = ed.lines[ids[0]]
    assert door.two_sided and door.special == SPECIAL_DOORS
    assert ed.sides[door.front].sector == 0
    assert ed.sides[door.back].sector == 2
    assert ed.sides[door.front].mid == "door_D"
    assert ed.sides[door.back].mid == "door_D"
    assert not door.solid
    assert not sm.line_blocks(ids[0]), "a door is not a solid wall"
    # only the three solid edges get inner walls; the portal is shared
    inner = _inner_lines(ed, 2)
    assert len(inner) == 3, inner
    assert sm.find_sector_at(632, 632) == 2
    assert sm.find_sector_at(632, 610) == 2, "A reaches the door line"
    assert sm.find_sector_at(632, 655) == -1, "the solid edge keeps its body"
    assert sm.find_sector_at(590, 600) == 0
    print("  contour portal   OK")


def test_sector_from_contour_tent():
    """A lone solid edge between two portals keeps its body as a 'tent'."""
    sm = new_room(1600, 1600)
    ed = SectorEditor(sm)
    ed.sectors.append(Sector())
    base = len(ed.verts)
    for x, y in ((600, 600), (664, 600), (664, 664), (600, 664)):
        ed.verts.append(Vertex(x, y))
    ids = []
    for i in range(4):
        if i in (0, 2):                      # top and bottom are portals
            f = len(ed.sides)
            ed.sides.append(SideDef(mid="door_D", sector=0))
            b = len(ed.sides)
            ed.sides.append(SideDef(mid="door_D", sector=1))
            ed.lines.append(LineDef(base + i, base + (i + 1) % 4,
                                    special=SPECIAL_DOORS, front=f, back=b))
        else:
            ed.sides.append(SideDef(sector=0))
            ed.lines.append(LineDef(base + i, base + (i + 1) % 4,
                                    front=len(ed.sides) - 1))
        ids.append(len(ed.lines) - 1)
    sm.invalidate()
    sid, errs = ed.sector_from_contour(ids, floor_h=0)
    assert sid == 2 and not errs, errs
    assert not ed.validate(), ed.validate()
    inner = _inner_lines(ed, 2)
    assert len(inner) == 4, "two segments per lone solid edge"
    # the tent points sit in the middle of the solid edges, WALL_THICKNESS in
    mids = {(ed.verts[ed.lines[li].v1].x, ed.verts[ed.lines[li].v1].y)
            for li in inner}
    mids |= {(ed.verts[ed.lines[li].v2].x, ed.verts[ed.lines[li].v2].y)
             for li in inner}
    assert (616.0, 632.0) in mids and (648.0, 632.0) in mids, mids
    assert sm.find_sector_at(632, 632) == 2
    assert sm.find_sector_at(610, 632) == -1
    assert sm.find_sector_at(655, 632) == -1
    assert sm.find_sector_at(632, 600) == 2
    assert sm.find_sector_at(632, 650) == 2
    print("  contour tent     OK")


def test_sector_from_contour_rejects_bad_loops():
    """A too-small room and a bad contour are refused before any mutation."""
    for pts in (((400, 400), (432, 400), (432, 432), (400, 432)),
                ((400, 400), (800, 800), (400, 800), (800, 400))):
        ed = SectorEditor(new_room(1600, 1600))
        vs = [ed.add_vertex(x, y) for x, y in pts]
        ids = [ed.add_line(vs[i], vs[(i + 1) % 4]) for i in range(4)]
        before = ed.sm.to_json()
        hist = len(ed._history)
        sid, errs = ed.sector_from_contour(ids)
        assert sid == -1 and errs, (pts, sid, errs)
        assert ed.sm.to_json() == before, "a failed create mutated the map"
        assert len(ed._history) == hist, "a failed create left an undo entry"
    print("  contour refused  OK")


def test_things_roundtrip():
    ed = SectorEditor()
    i_imp = ed.add_thing(96, 96, "imp")
    i_med = ed.add_thing(1504, 1504, "med")
    ed.add_thing(800, 800, "player1")
    ed.set_thing_angle(i_imp, 90)
    ed.move_thing(i_med, 900, 900)
    assert not ed.validate(), ed.validate()
    js = ed.sm.to_json()
    sm2 = SectorMap.from_json(js)
    types = {t.type for t in sm2.things}
    assert "imp" in types and "med" in types
    assert len([t for t in sm2.things if t.type == "player1"]) >= 1
    imp = next(t for t in sm2.things if t.type == "imp")
    assert imp.angle == 90
    ed.delete_thing(i_med)
    assert [t.type for t in ed.things].count("med") == 0
    assert not ed.validate(), ed.validate()
    print("  things+json      OK")


def test_undo_redo_roundtrip():
    ed = SectorEditor()
    start = ed.sm.to_json()
    # composite sequence: several ops, mixed kinds
    ed.begin_batch()
    v1 = ed.add_vertex(100, 100)
    v2 = ed.add_vertex(900, 100)
    ed.add_line(v1, v2)
    t = ed.add_thing(500, 500, "imp")
    ed.set_line_special(0, 5, 1)
    ed.end_batch()
    mid = ed.sm.to_json()
    assert not ed.validate(), ed.validate()
    # one more op outside the batch
    ed.add_thing(700, 700, "med")
    final = ed.sm.to_json()
    assert not ed.validate(), ed.validate()
    # undo everything
    ed.undo()
    ed.undo()
    assert ed.sm.to_json() == start, "undo did not restore the start state"
    # redo everything
    ed.redo()
    ed.redo()
    assert ed.sm.to_json() == final, "redo did not restore the final state"
    print("  undo/redo        OK")


def test_mt1_save_load_bsp():
    """Task 5.1: the editor saves ``.mt1`` with fresh BSP slices; old ``.json``
    imports; any geometry edit invalidates the stored tree (rebuild on save)."""
    from engine.bsp import BspTree, build_bsp
    from shared.mt1 import L_NODES, L_SEGS, L_SUBSECTORS

    ed = SectorEditor()
    v1 = ed.add_vertex(400, 400)
    v2 = ed.add_vertex(800, 400)
    v3 = ed.add_vertex(800, 800)
    v4 = ed.add_vertex(400, 800)
    l1 = ed.add_line(v1, v2)
    l2 = ed.add_line(v2, v3)
    l3 = ed.add_line(v3, v4)
    l4 = ed.add_line(v4, v1)
    sid, errs = ed.sector_from_contour([l1, l2, l3, l4], floor_h=-64, light=120)
    assert sid == 1 and not errs, errs
    assert not ed.validate(), ed.validate()

    with tempfile.TemporaryDirectory() as td:
        mt1_path = os.path.join(td, "editor_test.mt1")
        save_mt1_with_bsp(ed.sm, mt1_path)

        # Reloading gives the model + ride-along BSP slices (D2).
        from shared.sector_map import SectorMap as _SM
        loaded = _SM.load(mt1_path)
        assert loaded._bsp is not None, ".mt1 must carry BSP slices"
        assert not loaded.validate(), loaded.validate()
        stored = BspTree.from_lumps(loaded, loaded._bsp["lumps"][L_SEGS],
                                    loaded._bsp["lumps"][L_SUBSECTORS],
                                    loaded._bsp["lumps"][L_NODES],
                                    loaded._bsp["verts"])
        fresh = build_bsp(loaded)
        assert stored.sub_segs() == fresh.sub_segs(), "stored tree != fresh build"
        assert loaded.find_sector_at(600, 600) == 1, "geometry intact after save"

        # Editing invalidates the stored tree: rebuild on next save.
        ed2 = SectorEditor(loaded)
        ed2.move_vertex(v1, 450, 450)
        assert ed2.sm._bsp is None, "geometry edit must drop BSP slices"
        save_mt1_with_bsp(ed2.sm, mt1_path)
        loaded2 = _SM.load(mt1_path)
        assert loaded2._bsp is not None
        stored2 = BspTree.from_lumps(loaded2, loaded2._bsp["lumps"][L_SEGS],
                                     loaded2._bsp["lumps"][L_SUBSECTORS],
                                     loaded2._bsp["lumps"][L_NODES],
                                     loaded2._bsp["verts"])
        assert stored2.sub_segs() == build_bsp(loaded2).sub_segs(), \
            "re-saved slices must match the moved geometry"

        # Legacy .json import (old JSON sector maps) still loads.
        json_path = os.path.join(td, "legacy.json")
        with open(json_path, "w", encoding="utf-8") as f:
            import json as _json
            _json.dump(ed2.sm.to_json(), f)
        legacy = _SM.load(json_path)
        assert legacy._bsp is None
        assert not legacy.validate(), legacy.validate()
        assert legacy.find_sector_at(600, 600) == 1, "JSON import keeps geometry"

    print("  mt1 save/bsp/import OK")


def test_height_clamp():
    """One clamp for buttons and numeric fields: integral, in range, ordered."""
    assert clamp_sector_heights(0, 128) == (0, 128)
    assert clamp_sector_heights(10.4, 129.6) == (10, 130), "must be integral"
    assert clamp_sector_heights(200, 100) == (100, 100), "floor must not pass ceil"
    assert clamp_sector_heights(5000, 6000) == (MAX_SECTOR_HEIGHT,) * 2
    assert clamp_sector_heights(-5000, -6000) == (MIN_SECTOR_HEIGHT,) * 2
    assert clamp_sector_heights(200, 100)[0] <= clamp_sector_heights(200, 100)[1]

    ed = SectorEditor(new_room(1600, 1600))
    assert ed.sectors[0].floor_h == 0
    # the ±16 buttons go through the same clamp
    assert ed.set_sector_heights(0, floor_h=16) == (16, 128)
    assert ed.set_sector_heights(0, floor_h=0) == (0, 128)
    # a floor above the ceiling is pulled back, and the applied pair is returned
    assert ed.set_sector_heights(0, floor_h=999) == (128, 128)
    assert ed.sectors[0].floor_h == 128 and ed.sectors[0].ceil_h == 128
    assert not ed.validate(), ed.validate()
    # out-of-range input is clamped, not rejected
    assert ed.set_sector_heights(0, ceil_h=99999) == (128, MAX_SECTOR_HEIGHT)
    assert not ed.validate(), ed.validate()
    # undo puts the height back
    ed.set_sector_heights(0, floor_h=64)
    assert ed.sectors[0].floor_h == 64
    ed.undo()
    assert ed.sectors[0].floor_h == 128, ed.sectors[0].floor_h
    print("  height clamp     OK")


def test_solid_flag_and_step_textures():
    """solid toggles, survives a split, and step lips keep their textures."""
    ed = SectorEditor(new_room(1600, 1600))
    box = _contour_box(ed, 400, 400, 800, 800)
    sid, errs = ed.sector_from_contour(box, floor_h=16, ceil_h=128)
    assert sid == 1 and not errs, errs

    # thick walls are one-sided on both faces: the flag is meaningless there
    for li in box + _inner_lines(ed, sid):
        assert ed.set_line_solid(li, True) is False, li
        assert not ed.lines[li].solid

    # a two-sided linedef between two sectors carries the flag
    two = SectorEditor(new_room(1600, 1600))
    v1 = two.add_vertex(400, 400)
    v2 = two.add_vertex(800, 400)
    li = two.add_line(v1, v2)
    two.add_sector(floor_h=0, ceil_h=128, light=100)
    two.add_sector(floor_h=16, ceil_h=128, light=100)
    assert two.set_two_sided(li, True, back_sector=1) == []
    assert two.set_line_solid(li, True) is True
    assert two.sm.line_blocks(li) is True
    two.set_line_solid(li, False)
    assert two.sm.line_blocks(li) is False, "a 16 step stays passable"
    two.set_line_solid(li, True)
    assert two.sm.line_blocks(li) is True
    assert not two.validate(), two.validate()

    # a split gives both halves the flag, like special/tag
    nv = two.split_line_at(li, 600, 400)
    halves = [i for i, ld in enumerate(two.lines) if nv in (ld.v1, ld.v2)]
    assert len(halves) == 2, halves
    assert all(two.lines[i].solid for i in halves), \
        "solid must be inherited by both halves"
    assert not two.validate(), two.validate()
    two.undo()
    assert two.lines[li].solid is True, "undo must restore the flag"

    # step lips are assignable per sidedef and survive the saved map
    front = two.lines[li].front
    assert two.set_side_tex(front, lower="step_low", upper="step_up") is True
    assert two.sides[front].lower == "step_low"
    assert two.sides[front].upper == "step_up"
    assert two.set_side_tex(-1, lower="nope") is False
    # an unassigned lip stays "-"
    assert two.sides[two.lines[li].back].lower == "-"

    # splitting a region off must not wipe an assigned lip
    ed2 = SectorEditor(new_room(1600, 1600))
    ids = _contour_box(ed2, 400, 400, 800, 800)
    ed2.set_side_tex(ed2.lines[ids[0]].front, lower="step_low")
    sid2, errs2 = ed2.sector_from_contour(ids, floor_h=16, ceil_h=128)
    assert sid2 == 1 and not errs2, errs2
    kept = [sd.lower for sd in ed2.sides if sd.lower == "step_low"]
    assert kept, "an assigned step lip was reset to '-'"
    print("  solid + step tex OK")


def _contour_box(ed, x0, y0, x1, y1):
    """A 4-vertex box contour as a list of new linedef ids."""
    v1 = ed.add_vertex(x0, y0)
    v2 = ed.add_vertex(x1, y0)
    v3 = ed.add_vertex(x1, y1)
    v4 = ed.add_vertex(x0, y1)
    return [ed.add_line(a, b) for a, b in
            ((v1, v2), (v2, v3), (v3, v4), (v4, v1))]


def test_solid_cleared_when_line_becomes_one_sided():
    """solid is only valid on two-sided lines, so it goes away with the back."""
    ed = SectorEditor()
    a = ed.add_vertex(400, 400)
    b = ed.add_vertex(800, 400)
    li = ed.add_line(a, b)
    ed.add_sector(floor_h=0, ceil_h=128, light=100)
    ed.add_sector(floor_h=0, ceil_h=128, light=100)
    ed.set_two_sided(li, True, back_sector=1)
    assert ed.set_line_solid(li, True) is True
    assert ed.lines[li].solid is True
    assert not ed.validate(), ed.validate()

    ed.set_two_sided(li, False)
    assert ed.lines[li].back < 0
    assert ed.lines[li].solid is False, \
        "a one-sided line must not keep the solid flag (validation rejects it)"
    assert not ed.validate(), ed.validate()
    assert ed.sm.line_blocks(li) is True, "a one-sided line is still a wall"

    ed.undo()
    assert ed.lines[li].solid is True, "undo must restore the dropped flag"
    print("  solid cleared     OK")


def test_delete_line_breaks_the_contour():
    """Cutting a wall drops that sector's polygon — and only that one.

    The room's inner loop is one-sided, so cutting it leaves A with an open
    path and the hole in the surrounding sector is still there: nothing covers
    the point any more. The two-room case below shows the same on a shared wall.
    """
    ed = SectorEditor(new_room(1600, 1600))
    box = _contour_box(ed, 400, 400, 800, 800)
    ed.sector_from_contour(box, floor_h=-64)
    wall = _inner_lines(ed, 1)[0]
    assert ed.sm.has_polygon(1)
    notes = ed.delete_line(wall)
    assert not ed.validate(), ed.validate()
    assert not ed.sm.has_polygon(1), "the cut sector must lose its polygon"
    assert ed.sm.find_sector_at(600, 600) == -1, \
        "the wall body still keeps the room outside"
    assert any("без замкнутого контура" in n for n in notes), notes

    # the same cut on a shared portal where both rooms close on it
    two = SectorEditor(_two_rooms())
    assert two.sm.find_sector_at(400, 800) == 0
    assert two.sm.find_sector_at(1200, 800) == 1
    notes = two.delete_line(1)                     # the shared wall
    assert not two.validate(), two.validate()
    assert not two.sm.has_polygon(0) and not two.sm.has_polygon(1), \
        "both rooms lose their polygon with the wall between them"
    assert two.sm.find_sector_at(400, 800) == -1, "nothing covers the left room"
    assert two.sm.find_sector_at(1200, 800) == -1, "nor the right one"
    assert any("без замкнутого контура" in n for n in notes), notes
    print("  delete line      OK")


def test_delete_line_keeps_loop_corners():
    """Orphaned corners go; corners a closed loop still shares stay."""
    ed = SectorEditor(new_room(1600, 1600))
    box = _contour_box(ed, 400, 400, 800, 800)
    ed.sector_from_contour(box, floor_h=-64)
    # a dangling line, touching no other line, so its two corners are its own
    d1 = ed.add_vertex(200, 200)
    d2 = ed.add_vertex(400, 200)
    dangle = ed.add_line(d1, d2)
    n_verts, n_lines, n_sides = len(ed.verts), len(ed.lines), len(ed.sides)
    wall = _inner_lines(ed, 1)[0]

    ed.delete_line(wall)
    assert not ed.validate(), ed.validate()
    assert len(ed.lines) == n_lines - 1
    assert len(ed.sides) == n_sides - 1, "the inner line has one side"
    # a closed loop shares every corner with another edge, so cutting one of
    # its lines orphans nothing: the room corners all still have two users
    assert len(ed.verts) == n_verts, len(ed.verts)
    ed.undo()
    assert not ed.validate(), ed.validate()
    assert len(ed.verts) == n_verts and len(ed.sides) == n_sides \
        and len(ed.lines) == n_lines

    # a dangling line takes its own corners with it
    assert not ed.delete_line(dangle), "nothing borders on a dangling line"
    assert len(ed.verts) == n_verts - 2, len(ed.verts)
    assert len(ed.lines) == n_lines - 1, len(ed.lines)
    assert not ed.validate(), ed.validate()
    ed.undo()
    assert len(ed.verts) == n_verts, len(ed.verts)
    print("  delete line      OK")


def test_delete_line_leaves_untouched_sector():
    """A wall nobody's sector depends on changes no geometry."""
    ed = SectorEditor(new_room(1600, 1600))
    a = ed.add_vertex(200, 200)
    b = ed.add_vertex(600, 200)
    spare = ed.add_line(a, b)          # one-sided, borders no sector edge
    n_lines = len(ed.lines)
    notes = ed.delete_line(spare)
    assert not notes, notes            # no sector lost its contour
    assert not ed.validate(), ed.validate()
    assert len(ed.lines) == n_lines - 1
    assert ed.sm.find_sector_at(400, 1200) == 0, "room must stay intact"
    print("  delete spare ln  OK")


def test_polygon_alive():
    ed = SectorEditor(new_room(1600, 1600))
    box = _contour_box(ed, 400, 400, 800, 800)
    ed.sector_from_contour(box, floor_h=-64)
    inner = _inner_lines(ed, 1)
    assert len(inner) == 4
    for i in inner:
        assert ed._polygon_alive(1, {i}) is False, \
            "the room dies with any one of its inner walls"
        assert ed._polygon_alive(0, {i}) is True, \
            "the outer room does not use the inner loop"
    # the outer contour is sector 0's hole; the map border keeps it alive
    for i in box:
        assert ed._polygon_alive(0, {i}) is True
        assert ed._polygon_alive(1, {i}) is True
    assert ed._polygon_alive(1, set()) is True
    print("  polygon alive    OK")


def test_delete_sector_merges():
    """Dissolving a carved sector gives its area back to the one around it."""
    ed = SectorEditor(new_room(1600, 1600))
    v1 = ed.add_vertex(400, 400)
    v2 = ed.add_vertex(800, 400)
    v3 = ed.add_vertex(800, 800)
    v4 = ed.add_vertex(400, 800)
    box = [ed.add_line(a, b) for a, b in
           ((v1, v2), (v2, v3), (v3, v4), (v4, v1))]
    sid, errs = ed.sector_from_contour(box, floor_h=-64, light=90)
    assert sid == 1 and not errs, errs
    assert ed.sm.find_sector_at(600, 600) == 1
    n_verts, n_lines, n_sides = len(ed.verts), len(ed.lines), len(ed.sides)

    notes = ed.delete_sector(1)
    assert not ed.validate(), ed.validate()
    assert len(ed.sectors) == 1, len(ed.sectors)
    assert len(ed.lines) == 4, "only the room border must be left"
    # new_room gives the whole border one shared sidedef, so the pit's eight
    # sides go and that single one is all the room ever had
    assert len(ed.sides) == 1, len(ed.sides)
    assert len(ed.verts) == 4, len(ed.verts)
    assert ed.sm.find_sector_at(600, 600) == 0, \
        "the area merges into the surrounding sector"
    assert ed.sm.find_sector_at(200, 200) == 0
    assert ed.sectors[0].floor_h == 0, "surviving sector keeps its own floor"
    assert len(ed.things) == 1, "things are never removed with a sector"
    assert not ed.validate(), ed.validate()
    ed.undo()
    assert not ed.validate(), ed.validate()
    assert len(ed.sectors) == 2 and ed.sm.find_sector_at(600, 600) == 1, \
        "undo must bring the sector and its walls back"
    assert len(ed.verts) == n_verts and len(ed.lines) == n_lines \
        and len(ed.sides) == n_sides
    print("  delete sector    OK")


def _two_rooms():
    """Two rooms side by side, split by the one wall they share.

    Sector 0 is the left room, sector 1 the right one, and the divider is a
    two-sided linedef (1 -> 2) whose front side belongs to the left room and
    back side to the right one — so the right room's contour runs the divider
    backwards. Dissolving the left room must keep that wall one-sided, still
    aimed the way the right room walks it.
    """
    sm = SectorMap()
    sm.sectors.append(Sector(floor_h=0, ceil_h=128, light=90))
    sm.sectors.append(Sector(floor_h=0, ceil_h=128, light=120))
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
    sm.things.append(Thing(x=400.0, y=800.0, type="player1"))
    sm.invalidate()
    return sm


def test_delete_sector_protects_neighbour():
    """A wall the neighbour needs stays, and the pit takes the sector's place.

    Built by hand rather than by ``sector_from_contour`` (which always hands
    the far side to the enclosing sector): the two rooms share exactly one
    wall, and that wall is the only thing closing the right room's contour.
    Dropping it would leave the survivor without a polygon, so it has to be
    kept — one-sided, and still pointing the way the survivor walks it.
    """
    ed = SectorEditor(_two_rooms())
    assert not ed.validate(), ed.validate()
    shared = 1
    assert ed.lines[shared].two_sided
    assert ed.sm.find_sector_at(400, 800) == 0, ed.sm.find_sector_at(400, 800)
    assert ed.sm.find_sector_at(1200, 800) == 1
    assert ed._polygon_alive(0, {shared}) is False, \
        "the left room closes only with the divider"
    assert ed._polygon_alive(1, {shared}) is False, \
        "and so does the right one"

    notes = ed.delete_sector(0)
    assert not ed.validate(), ed.validate()
    assert len(ed.sectors) == 1 and ed.sectors[0].light == 120, \
        "the right room must survive intact, under the only index left"
    assert ed.sm.find_sector_at(1200, 800) == 0, \
        "the survivor keeps its own area, not a pit"
    assert ed.sm.find_sector_at(400, 800) == -1, \
        "the dissolved room's area is now a pit"
    assert len(ed.lines) == 4 and len(ed.sides) == 4, \
        f"only the right room's four walls are left: {len(ed.lines)}/{len(ed.sides)}"
    assert len(ed.verts) == 4, f"the left room's corners are orphaned: {len(ed.verts)}"
    assert all(not ld.two_sided for ld in ed.lines), \
        "the shared wall has to be legal as a one-sided linedef"
    assert all(ed.sides[ld.front].sector == 0 for ld in ed.lines), \
        "no wall may be left pointing at the dissolved sector"
    assert any("общих стен" in n for n in notes), notes
    assert all(not ld.solid for ld in ed.lines), \
        "a one-sided wall must not keep the solid flag"
    ed.undo()
    assert not ed.validate(), ed.validate()
    assert len(ed.sectors) == 2
    assert ed.sm.find_sector_at(400, 800) == 0
    assert ed.sm.find_sector_at(1200, 800) == 1
    assert ed.lines[shared].two_sided, "undo must restore the shared wall"
    print("  dissolve protects OK")


def test_delete_sector_refuses_last():
    ed = SectorEditor(new_room(1600, 1600))
    assert len(ed.sectors) == 1
    before = ed.sm.to_json()
    notes = ed.delete_sector(0)
    assert any("последний" in n for n in notes), notes
    assert ed.sm.to_json() == before, "a refused delete must change nothing"
    assert not ed.validate(), ed.validate()
    assert ed.delete_sector(7), "out-of-range index is reported, not silent"
    print("  refuse last sec  OK")


def test_delete_vertex_from_selection():
    """The vertex op the UI could not reach until now."""
    ed = SectorEditor()
    n_verts = len(ed.verts)
    a = ed.add_vertex(400, 400)
    b = ed.add_vertex(800, 400)
    li = ed.add_line(a, b)
    ed.delete_vertex(a)
    assert not ed.validate(), ed.validate()
    assert len(ed.verts) == n_verts + 1, "only the vertex is gone"
    assert li not in range(len(ed.lines)), "its linedef goes with it"
    ed.undo()
    assert not ed.validate(), ed.validate()
    assert len(ed.verts) == n_verts + 2
    print("  delete vertex    OK")


def _per_wall_rooms() -> SectorMap:
    """Two rooms side by side, every wall with its OWN sidedef.

    ``new_room`` shares one sidedef across all four walls, and that hides the
    damage this change is about: with a single shared side the greedy contour
    walk simply routes around one backwards edge and the room survives.  The
    reported bug needs per-wall sides, which is what the editor produces as soon
    as a room is split — so this is the geometry the tests below work on.
    """
    sm = SectorMap([], [], [], [
        Sector(floor_h=0, ceil_h=128),
        Sector(floor_h=64, ceil_h=128),
    ])
    sm.things.append(Thing(96, 96))
    for x0, x1, sid in ((0, 192, 0), (400, 592, 1)):
        base = len(sm.verts)
        for x, y in ((x0, 0), (x1, 0), (x1, 384), (x0, 384)):
            sm.verts.append(Vertex(float(x), float(y)))
        side = len(sm.sidedefs)
        sm.sidedefs.append(SideDef(sector=sid))
        for i in range(4):
            sm.linedefs.append(LineDef(base + i, base + (i + 1) % 4, front=side))
    sm.invalidate()
    return sm


def _poly_points(sm: SectorMap, sid: int) -> set:
    """Sector ``sid``'s polygon, as a set of points: order/rotation insensitive."""
    return {p for poly, _bbox, _area in sm._ensure_polys()[sid] for p in poly}


def _flipped(ld) -> None:
    """Corrupt a linedef the way the old flip did: sides swap, vertices swap."""
    ld.v1, ld.v2 = ld.v2, ld.v1
    ld.front, ld.back = ld.back, ld.front


def test_one_sided_flip_is_refused():
    """Flip is a front/back swap, and a one-sided wall has no back to swap with.

    Swapping alone re-aims the wall but keeps the sector's directed edge, so the
    room survives — while ``validate()`` already calls the map broken ("missing
    front sidedef").  The damage lands on the NEXT action: "1-ст." clears the
    back side too, the wall stops bounding anything and the room goes void.  The
    refusal is what keeps the map out of that state at all.
    """
    ed = SectorEditor(_per_wall_rooms())
    # what the old flip plus the old "1-ст." did to this very wall, recorded so
    # the test cannot pass for the wrong reason
    probe = SectorEditor(_per_wall_rooms())
    _flipped(probe.lines[1])
    probe.lines[1].back = -1            # the old one-sided toggle cleared it too
    probe.sm.invalidate()
    assert [e for e in probe.validate() if "missing front" in e], probe.validate()
    assert not probe.sm.has_polygon(0), "precondition: the room went void"

    before = ed.sm.to_json()
    assert ed.flip_line(1) is False, "flip accepted on a one-sided wall"
    assert ed.sm.to_json() == before, "a refused flip mutated the map"
    assert not ed._history, "a refused flip left an undo entry"
    assert not ed.lines[1].two_sided, ed.lines[1]
    assert not ed.validate(), ed.validate()
    assert ed.sm.has_polygon(0), "the room lost its contour"
    print("  one-sided flip refused, map untouched")


def test_one_sided_toggle_repairs_a_frontless_wall():
    """"1-ст." on a wall whose front side is gone must bring it back.

    The old code cleared the back side as well, which left a linedef with no
    sides at all — a wall belonging to no sector.  That is what actually killed
    the room: with the wall no longer bounding it, the contour stops closing.
    The back side becomes the front instead, with the vertex swap that keeps the
    directed edge identical, so the polygon comes back exactly as it was and the
    caller is told a repair happened.
    """
    ed = SectorEditor(_per_wall_rooms())
    footprint = _poly_points(ed.sm, 0)
    ld = ed.lines[1]
    _flipped(ld)
    ed.sm.invalidate()
    assert ld.front < 0 and ld.back >= 0, (ld.front, ld.back)
    assert [e for e in ed.validate() if "missing front" in e], ed.validate()
    n_sides = len(ed.sides)

    notes = ed.set_two_sided(1, False)
    ed.sm.invalidate()
    assert notes and "восстановлена" in notes[0], notes
    assert ld.front >= 0 and ld.back == -1, (ld.front, ld.back)
    assert len(ed.sides) == n_sides, "no side may be dropped on the way"
    assert all(l.front >= 0 for l in ed.lines), "a linedef is left frontless"
    assert _poly_points(ed.sm, 0) == footprint, "the room's polygon moved"
    assert not ed.validate(), ed.validate()
    # and it is a real edit: one undo step puts the damage back
    assert ed.can_undo(), "the repair left no undo entry"
    ed.undo()
    assert ed.lines[1].front < 0, "undo did not put the frontless wall back"
    print("  one-sided toggle repairs a frontless wall")


def test_sealing_a_wall_refuses_to_void_the_room_behind_it():
    """"1-ст." must not turn the room behind the wall into a void.

    This is the reported loss, in the shape the panel can produce it: sealing
    the wall two rooms share drops the back side, and with it the only edge
    that closed the far room's contour.  The sector stops being walkable, its
    area is gone and its height disappears from the panel — silently, with no
    note and nothing to undo but the whole edit.  The toggle is refused and the
    refusal names the sector that would have been lost.
    """
    ed = SectorEditor(_two_rooms())
    shared = 1
    assert ed.lines[shared].two_sided, "fixture: the rooms must share a portal"
    assert ed.sm.has_polygon(0) and ed.sm.has_polygon(1)
    before = ed.sm.to_json()

    notes = ed.set_two_sided(shared, False)
    assert notes, "sealing the shared wall was allowed"
    assert "сектора 1" in notes[0], notes
    assert "контур" in notes[0], notes
    assert ed.sm.to_json() == before, "a refused toggle changed the map"
    assert ed.lines[shared].two_sided, "the portal was dropped anyway"
    assert ed.sm.has_polygon(1), "the far room died"
    assert ed.sectors[1].floor_h == 0, "the far room lost its height"
    assert not ed.validate(), ed.validate()
    assert not ed.can_undo(), "a refusal left an undo entry"

    # a wall whose far sector has ANOTHER contour to close still seals: the
    # question is "does the sector survive", not "is the wall shared"
    ed2 = SectorEditor(_two_rooms())
    sm = ed2.sm
    base = len(sm.verts)
    for x, y in ((2400.0, 0.0), (3200.0, 0.0), (3200.0, 800.0), (2400.0, 800.0)):
        sm.verts.append(Vertex(x, y))
    for v1, v2 in ((base, base + 1), (base + 1, base + 2),
                   (base + 2, base + 3), (base + 3, base)):
        sm.sidedefs.append(SideDef(sector=1))          # a second island of
        sm.linedefs.append(LineDef(v1, v2,             # the same right room
                                   front=len(sm.sidedefs) - 1))
    sm.invalidate()
    assert ed2.sm.has_polygon(1)
    assert ed2.set_two_sided(1, False) == [], \
        "the room survives on its second contour, so sealing is fine"
    assert ed2.lines[1].back == -1
    assert ed2.sm.has_polygon(1), "the island room lost everything"
    assert not ed2.validate(), ed2.validate()
    print("  sealing a wall refuses to void the room behind it")


def test_two_sided_toggle_refuses_a_self_portal():
    """"2-ст." must not aim the back side at the wall's own sector.

    Both sides in one sector is a self-portal: the edge is walked twice in
    opposite directions, and every "wall between two rooms" feature — the
    staircase included — reads a portal whose two floors are necessarily at the
    same height.  An aim at an EMPTY sector is deliberately still allowed:
    sectors are created empty and closed later by ``sector_from_contour``.
    """
    ed = SectorEditor(_two_rooms())
    wall = 2                                    # one-sided wall of the left room
    ld = ed.lines[wall]
    assert ld.back == -1, "fixture: the wall must be one-sided"
    own = ed.sides[ld.front].sector
    ed.cur_sector = own                        # picked inside that very room
    before = ed.sm.to_json()

    notes = ed.set_two_sided(wall, True)
    assert notes, "a self-portal was created"
    assert f"сектор {own}" in notes[0], notes
    assert ed.sm.to_json() == before, "a refused toggle changed the map"
    assert ld.back == -1, "the self-portal is there anyway"
    assert not ed.can_undo(), "a refusal left an undo entry"

    # an empty (not yet closed) sector is a normal target: rooms are drawn
    # first and closed afterwards
    ed2 = SectorEditor(_two_rooms())
    ed2.sectors.append(Sector(floor_h=32, ceil_h=128, light=80))
    assert ed2.set_two_sided(wall, True, back_sector=len(ed2.sectors) - 1) == []
    assert ed2.lines[wall].back >= 0
    assert ed2.sides[ed2.lines[wall].back].sector == len(ed2.sectors) - 1

    # and the real thing still works: a second, live sector is accepted
    notes = ed.set_two_sided(wall, True, back_sector=1)
    assert notes == [], notes
    assert ld.back >= 0 and ed.sides[ld.back].sector == 1
    assert not ed.validate(), ed.validate()
    ed.undo()
    assert ld.back == -1, "undo did not take the portal back"
    print("  two-sided toggle refuses a self-portal")


def test_load_repairs_a_broken_map():
    """A map saved by the old editor opens repaired, and says so.

    Two cases, both from the reported damage: a wall left with only a back side
    (after Flip) and a wall left with no sides at all (after "1-ст.").  The
    repair is the loaded state, not an edit — it must not be undoable, or the
    first Ctrl+Z would hand the author a map nobody ever drew.
    """
    # (a) frontless, back side alive -> promoted back to front
    sm = _per_wall_rooms()
    _flipped(sm.linedefs[1])
    ed = SectorEditor(sm)
    assert any("восстановлена" in n for n in ed.repair_notes), ed.repair_notes
    assert all(l.front >= 0 for l in ed.lines), "a linedef is left frontless"
    assert ed.validate() == [], ed.validate()
    assert ed.sm.has_polygon(0), "the room did not come back"
    assert not ed._history, "the repair became an undoable edit"

    # (b) no sides at all -> the orphaned wall goes, and the sector it leaves
    # without a contour is named instead of quietly going void
    sm2 = _per_wall_rooms()
    sm2.linedefs[1].front = -1
    sm2.linedefs[1].back = -1
    ed2 = SectorEditor(sm2)
    assert any("удалена" in n for n in ed2.repair_notes), ed2.repair_notes
    assert any("без замкнутого контура" in n for n in ed2.repair_notes), \
        ed2.repair_notes
    assert all(l.front >= 0 for l in ed2.lines), "a linedef is left frontless"
    assert not [e for e in ed2.validate() if "sidedef" in e], ed2.validate()
    assert not ed2._history, "the repair became an undoable edit"

    # a healthy map is left completely alone
    ed3 = SectorEditor(_per_wall_rooms())
    assert ed3.repair_notes == [], ed3.repair_notes
    print("  load repairs a broken map, a healthy one not at all")


def test_two_sided_predicate():
    """``line_two_sided`` asks "both sides?", ``LineDef.two_sided`` asks "back?".

    They disagree exactly on a wall the old editor could produce, and the editor
    features that mean "a wall between two sectors" must not act on it.
    """
    ed = SectorEditor(_per_wall_rooms())
    assert not ed.line_two_sided(1), "a fresh wall is not a portal"
    ld = ed.lines[1]
    ld.front, ld.back = -1, 0
    assert ld.two_sided, "precondition: shared property says 'has a back side'"
    assert not ed.line_two_sided(1), "no front side is not a portal"
    ld.front = 0
    ld.back = len(ed.sides)
    ed.sides.append(SideDef(sector=1))
    assert ed.line_two_sided(1), "both sides is a portal"
    assert not ed.line_two_sided(99), "out-of-range index is not a portal"
    print("  two-sided predicate disagrees with the shared one on purpose")


def main():
    test_vertex_ops()
    test_line_ops()
    test_one_sided_flip_is_refused()
    test_one_sided_toggle_repairs_a_frontless_wall()
    test_sealing_a_wall_refuses_to_void_the_room_behind_it()
    test_two_sided_toggle_refuses_a_self_portal()
    test_load_repairs_a_broken_map()
    test_two_sided_predicate()
    test_sector_from_contour()
    test_sector_from_contour_clockwise()
    test_sector_from_contour_portal()
    test_sector_from_contour_tent()
    test_sector_from_contour_rejects_bad_loops()
    test_wall_thickness_constant()
    test_height_clamp()
    test_solid_flag_and_step_textures()
    test_solid_cleared_when_line_becomes_one_sided()
    test_things_roundtrip()
    test_undo_redo_roundtrip()
    test_delete_line_breaks_the_contour()
    test_delete_line_keeps_loop_corners()
    test_delete_line_leaves_untouched_sector()
    test_polygon_alive()
    test_delete_sector_merges()
    test_delete_sector_protects_neighbour()
    test_delete_sector_refuses_last()
    test_delete_vertex_from_selection()
    test_mt1_save_load_bsp()
    print("smoke_sector_editor: ALL OK")


if __name__ == "__main__":
    main()