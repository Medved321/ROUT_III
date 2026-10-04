"""Smoke: sector editor staircase tool.

The tool is checked the way it fails: on the geometry that decides whether the
stairs are walkable at all.  A staircase is not four walls and a filled quad --
it is a set of CONTIGUOUS sectors, each one a closed directed loop, whose
heights climb in STEP_HEIGHT steps, and it has to leave every sector that was
alive before it alive afterwards.

The load-bearing detail is in _stair_piece: the riser between two treads is one
linedef whose FRONT is the LOWER tread and whose BACK is the upper one.  _ensure
_polys records a front side as v1 -> v2 and a back side as v2 -> v1, so the two
treads see the same edge in opposite directions and each closes its own loop.
With the assignment the other way round both treads end up with a vertex whose
edges only point into it, the loop is discarded as open, and every tread becomes
void -- a staircase that renders as nothing.
"""
from __future__ import annotations

import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shared.sector_map import (  # noqa: E402
    STEP_HEIGHT, LineDef, Sector, SectorMap, SideDef, Vertex,
)
from client.sector_editor import (  # noqa: E402
    STAIR_MAX_STEPS, STAIR_RISE_DEFAULT, SectorEditor,
)

LOW_H, HIGH_H = 0, 64
RAISE = 16


def _rect(sm: SectorMap, sid: int, x0: int, y0: int, x1: int, y1: int) -> None:
    """Append a one-sided rectangle whose front faces ``sid``."""
    base = len(sm.verts)
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        sm.verts.append(Vertex(float(x), float(y)))
    side = len(sm.sidedefs)
    sm.sidedefs.append(SideDef(sector=sid))
    for i in range(4):
        sm.linedefs.append(LineDef(base + i, base + (i + 1) % 4, front=side))


def _map(with_neighbour: bool = False) -> SectorMap:
    """Two rooms at different heights with a shaft between them.

    The shaft is bounded by the RIGHT wall of the low room and the LEFT wall of
    the high room, both one-sided, which is what the tool requires.  With
    ``with_neighbour`` a third sector sits beside the staircase so the rails
    have something to open onto.
    """
    sm = SectorMap([], [], [], [
        Sector(floor_h=LOW_H, ceil_h=128),
        Sector(floor_h=HIGH_H, ceil_h=128),
        Sector(floor_h=LOW_H, ceil_h=128),
    ])
    _rect(sm, 0, 0, 0, 192, 384)          # low room,  right wall at x=192
    _rect(sm, 1, 400, 0, 592, 384)        # high room, left wall at x=400
    if with_neighbour:
        # Deliberately narrower than the shaft: its corners must NOT land on
        # either wall, or the four clicks below become ambiguous (two walls at
        # distance zero) and the test would be measuring the wrong thing.
        _rect(sm, 2, 208, 272, 384, 384)
    sm.invalidate()
    return sm


def _alive(ed: SectorEditor) -> set[int]:
    return {i for i in range(len(ed.sectors)) if ed.sm.has_polygon(i)}


def _points(y0: int = 64, y1: int = 272):
    # Two on the low room's right wall, two on the high room's left wall, all
    # clear of every corner in the map.
    return [(192.0, float(y0)), (192.0, float(y1)),
            (400.0, float(y0)), (400.0, float(y1))]


def test_treads_are_closed_and_stepped():
    ed = SectorEditor(_map())
    before = _alive(ed)
    assert before == {0, 1}, before
    info, errs = ed.build_stair(_points(), RAISE)
    assert not errs, errs
    assert info["steps"] == (HIGH_H - LOW_H) // RAISE, info

    # every tread must be a real sector with a closed polygon
    for si in info["sectors"]:
        assert ed.sm.has_polygon(si), f"tread {si} is void"
    # heights climb, every riser is walkable, and the last tread is exactly the
    # floor of the room it leads into
    heights = [ed.sectors[si].floor_h for si in info["sectors"]]
    assert heights == [16, 32, 48, 64], heights
    assert heights[-1] == HIGH_H, heights
    assert all(0 < b - a <= STEP_HEIGHT for a, b in zip(heights, heights[1:])), heights
    assert 0 < heights[0] - LOW_H <= STEP_HEIGHT, heights
    # treads stay inside the shaft
    for si in info["sectors"]:
        for poly, _bbox, _area in ed.sm._ensure_polys()[si]:
            assert all(192 <= x <= 400 for x, _y in poly), poly
    # nothing else was destroyed
    assert _alive(ed) == before | set(info["sectors"]), sorted(_alive(ed))
    print("  treads closed, stepped, shafted")


def test_solid_rails_when_no_neighbour():
    ed = SectorEditor(_map())
    info, errs = ed.build_stair(_points(), RAISE)
    assert not errs, errs
    assert info["rails_open"] is False, info
    assert info["solid_rails"] is True, info
    print("  void on both sides -> rails stay solid")


def test_open_rails_to_a_real_neighbour():
    ed = SectorEditor(_map(with_neighbour=True))
    info, errs = ed.build_stair(_points(), RAISE)
    assert not errs, errs
    assert info["rails_open"] is True, info
    # the neighbour keeps its polygon even though the rails are chords of it
    assert ed.sm.has_polygon(2), "neighbour lost its contour"
    # exactly the rails facing the neighbour became portals
    portals = [li for li in range(len(ed.lines))
               if ed.lines[li].back >= 0
               and ed.sides[ed.lines[li].back].sector == 2]
    assert portals, "no rail opened onto sector 2"
    for li in portals:
        p, q = ed.verts[ed.lines[li].v1], ed.verts[ed.lines[li].v2]
        assert abs(p.y - q.y) < 1e-6, (p, q)     # rails run along the staircase
    print(f"  {len(portals)} rail segments opened onto the neighbour")


def test_undo_redo_is_exact():
    ed = SectorEditor(_map(with_neighbour=True))
    before = ed.sm.to_json()
    info, errs = ed.build_stair(_points(), RAISE)
    assert not errs, errs
    assert len(ed._history) == 1, ed._history
    ed.undo()
    assert ed.sm.to_json() == before, "undo did not restore the map byte for byte"
    ed.redo()
    assert len(ed.sectors) == len(info["sectors"]) + 3, len(ed.sectors)
    print("  undo/redo restore exactly, one history entry")


def _expect_reject(ed: SectorEditor, pts, why: str, rise: int = RAISE):
    before = ed.sm.to_json()
    hist = len(ed._history)
    info, errs = ed.build_stair(pts, rise)
    assert info is None, f"{why}: build unexpectedly succeeded"
    assert errs, f"{why}: no error message"
    assert ed.sm.to_json() == before, f"{why}: map was mutated"
    # a rejected build must not be undoable: the whole thing runs inside one
    # history batch, and the failed attempt is rolled back before the batch is
    # closed, so the batch records no diff at all
    assert len(ed._history) == hist, f"{why}: a failed build left an undo entry"
    return errs[0]


def test_rejections_leave_no_trace():
    ed = SectorEditor(_map())

    # fewer than four points
    _expect_reject(ed, _points()[:3], "three points")

    # two points off every wall
    _expect_reject(ed, [(600.0, 600.0)] * 2 + [(600.0, 610.0)] * 2, "points in the void")

    # all four on the SAME wall
    _expect_reject(ed, [(192.0, 64.0), (192.0, 128.0), (192.0, 192.0), (192.0, 256.0)],
                   "one wall only")

    # equal floor heights: nothing to climb
    flat = SectorEditor(_map())
    flat.sectors[1].floor_h = LOW_H
    flat.sm.invalidate()
    errs = _expect_reject(flat, _points(), "equal floors")
    assert "одной высоте" in errs, errs

    # walls too close to walk along
    _expect_reject(ed, [(192.0, 64.0), (192.0, 66.0), (400.0, 64.0), (400.0, 66.0)],
                   "zero-length staircase")

    # a point on a two-sided line is not a sector edge
    two_sided = SectorEditor(_map())
    li = 1                                    # the low room's right wall
    two_sided.lines[li].back = two_sided._new_side(1, "wall")
    two_sided.sm.invalidate()
    errs = _expect_reject(two_sided, _points(), "two-sided edge")
    assert "односторонняя" in errs, errs

    print("  every rejection is reported and leaves the map untouched")


def test_click_order_does_not_matter():
    """The four points may arrive in any order; pairing is derived, not assumed.

    The pairing of points into two walls is derived by trying all three splits,
    so any order produces the same staircase.  The ORDER OF THE TREADS may flip,
    because the first two clicks decide which room the staircase starts in --
    walking it from the high room labels the treads top-down instead of
    bottom-up.  What must not change is the staircase itself: the same set of
    tread levels, and the same polygons.
    """
    ref_levels = ref_footprints = None
    for order in ((0, 1, 2, 3), (2, 3, 0, 1), (3, 1, 0, 2), (1, 3, 2, 0)):
        ed = SectorEditor(_map())
        pts = _points()
        info, errs = ed.build_stair([pts[i] for i in order], RAISE)
        assert not errs, (order, errs)
        assert len(info["sectors"]) == 4, info
        levels = sorted(ed.sectors[si].floor_h for si in info["sectors"])
        # the tread footprints, canonicalised so vertex order does not matter
        footprints = set()
        for si in info["sectors"]:
            for poly, _bbox, _area in ed.sm._ensure_polys()[si]:
                footprints.add(tuple(sorted((round(x), round(y)) for x, y in poly)))
        if ref_levels is None:
            ref_levels, ref_footprints = levels, footprints
        assert levels == ref_levels, (order, levels)
        assert footprints == ref_footprints, (order, footprints ^ ref_footprints)
    print("  click order changes nothing but the order the treads are labelled in")


def test_rise_is_clamped_and_bounded():
    ed = SectorEditor(_map())
    # a rise above STEP_HEIGHT is clamped, not trusted: a "rise" larger than a
    # step would silently put a wall between two treads
    info, errs = ed.build_stair(_points(), 999)
    assert not errs, errs
    assert info["rise"] <= STEP_HEIGHT, info
    heights = [ed.sectors[si].floor_h for si in info["sectors"]]
    assert all(0 < b - a <= STEP_HEIGHT for a, b in zip(heights, heights[1:])), heights
    # a rise of 1 on a 64-unit gap asks for more steps than the limit allows.
    # A fresh map: the build above consumed the two walls it was given, so the
    # same points would now hit the staircase's own rails instead.
    deep = SectorEditor(_map())
    errs = _expect_reject(deep, _points(), "too many steps", rise=1)
    assert str(STAIR_MAX_STEPS) in errs, errs
    print("  rise clamped to STEP_HEIGHT, step count bounded")


def test_default_rise_and_sector_metadata():
    ed = SectorEditor(_map())
    info, errs = ed.build_stair(_points())
    assert not errs, errs
    assert info["steps"] == (HIGH_H - LOW_H) // STAIR_RISE_DEFAULT, info
    assert info["low"] == LOW_H and info["high"] == HIGH_H, info
    for si in info["sectors"]:
        s = ed.sectors[si]
        assert s.floor_tex == "floor" and s.ceil_tex == "ceiling", s
        assert 0 <= s.light <= 255, s
        assert s.floor_h < s.ceil_h, s
    # the treads inherit the ceiling that both rooms can stand under
    assert all(ed.sectors[si].ceil_h == 128 for si in info["sectors"])
    print("  default rise, ceiling and flats inherit from the rooms")


def test_tool_is_wired_in_the_screen():
    import pygame
    from client.sector_editor_screen import TOOL_KEYS, TOOL_NAMES, SectorEditorScreen
    assert TOOL_NAMES["stair"], TOOL_NAMES
    assert TOOL_KEYS[pygame.K_6] == "stair", TOOL_KEYS
    pygame.init()
    pygame.display.set_mode((1000, 700))
    scr = SectorEditorScreen(1000, 700)
    scr.editor = SectorEditor(_map())
    scr.frame_around()

    # K_6 selects the tool
    scr.handle([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_6,
                                   mod=0, unicode="6")])
    assert scr.tool == "stair", scr.tool

    # four canvas clicks build the staircase and clear the pending points
    n0 = len(scr.editor.sectors)
    for i, (wx, wy) in enumerate(_points()):
        sx, sy = scr._to_screen(wx, wy)
        scr.handle([pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(sx, sy),
                                       button=1)])
        scr.handle([pygame.event.Event(pygame.MOUSEBUTTONUP, pos=(sx, sy),
                                       button=1)])
        if i < 3:
            assert len(scr.stair_pts) == i + 1, scr.stair_pts
    assert scr.stair_pts == [], scr.stair_pts
    assert len(scr.editor.sectors) == n0 + 4, len(scr.editor.sectors)
    assert "Лестница" in scr.status, scr.status

    # Esc clears pending input without leaving the screen, then leaves
    scr.handle([pygame.event.Event(pygame.MOUSEBUTTONDOWN,
                                   pos=scr._to_screen(192.0, 64.0), button=1)])
    assert len(scr.stair_pts) == 1, scr.stair_pts
    assert scr.handle([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE,
                                          mod=0, unicode="\x1b")]) is None
    assert scr.stair_pts == [], scr.stair_pts
    assert scr.handle([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE,
                                          mod=0, unicode="\x1b")]) == "back"

    # switching tools drops pending input
    scr.handle([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_6, mod=0, unicode="6")])
    scr.handle([pygame.event.Event(pygame.MOUSEBUTTONDOWN,
                                   pos=scr._to_screen(192.0, 64.0), button=1)])
    assert scr.stair_pts
    scr.handle([pygame.event.Event(pygame.KEYDOWN, key=pygame.K_1, mod=0, unicode="1")])
    assert scr.tool == "select" and scr.stair_pts == [], (scr.tool, scr.stair_pts)

    # the toolbar button also selects it, and drawing the preview works
    scr.handle([pygame.event.Event(pygame.MOUSEBUTTONDOWN,
                                   pos=scr._tool_buttons["stair"].center, button=1)])
    assert scr.tool == "stair", scr.tool
    surf = pygame.Surface((1000, 700))
    scr.draw(surf)
    print("  tool key, clicks, Esc, toolbar button and preview all wired")


def test_tool_buttons_stay_inside_the_panel():
    import pygame
    from client.sector_editor_screen import SectorEditorScreen
    pygame.init()
    pygame.display.set_mode((1000, 700))
    for w, h in ((1000, 700), (1280, 720), (1600, 900)):
        scr = SectorEditorScreen(w, h)
        rects = list(scr._tool_buttons.values())
        for r in rects:
            assert scr.panel.colliderect(r) or scr.panel.contains(r), (w, h, r)
        for i, a in enumerate(rects):
            for b in rects[i + 1:]:
                assert not a.colliderect(b), (w, h, a, b)
        # every drawn button is clickable
        for key, r in scr._tool_buttons.items():
            assert r.width > 0 and r.height > 0, (key, r)
    print("  toolbar buttons fit the panel and never overlap")


def test_mid_build_failure_rolls_back():
    """An exception mid-build must not become a half-built staircase.

    _split_span raises when a point is not on the line it is supposed to split.
    That check is redundant with the plan, but a tool that mutates a map in one
    history batch must not depend on redundancy holding forever: an exception
    escaping the batch would let end_batch record the partial build as a real,
    undoable edit -- the one outcome this tool must never produce.
    """
    ed = SectorEditor(_map())
    before = ed.sm.to_json()

    def boom(*_a, **_kw):
        raise ValueError("лестница: точка не на стене")

    ed._split_span = boom
    info, errs = ed.build_stair(_points(), RAISE)
    assert info is None, info
    assert errs and "не на стене" in errs[0], errs
    assert ed.sm.to_json() == before, "mid-build failure left geometry behind"
    assert not ed._history, "mid-build failure left an undo entry"
    print("  exception mid-build rolls back, no history entry")


def test_flip_on_one_sided_wall_is_refused():
    """Flip needs a back side; on a one-sided wall it must change nothing.

    The bare ``v1 <-> v2`` swap that is all a one-sided wall could do turns its
    only directed edge the wrong way, ``_ensure_polys`` drops the contour and the
    sector silently becomes void.  The refusal is what keeps the map intact, and
    "the map is byte-identical afterwards" is the assertion that matters.
    """
    ed = SectorEditor(_map())
    before = ed.sm.to_json()
    hist = len(ed._history)
    ld = ed.lines[1]                                # low room's right wall
    assert ld.back < 0, "test wall is not one-sided"
    assert ed.flip_line(1) is False, "flip accepted on a one-sided wall"
    assert ed.sm.to_json() == before, "a refused flip mutated the map"
    assert len(ed._history) == hist, "a refused flip left an undo entry"
    assert not ed.lines[1].two_sided, ed.lines[1]
    # these maps carry no spawn, so only the linedef complaints are ours to check
    assert not [e for e in ed.validate() if "sidedef" in e], ed.validate()

    # two-sided with both sectors alive: the flip goes through, and the sector
    # it used to bound keeps its polygon
    ed2 = SectorEditor(_map())
    ed2.set_two_sided(1, True, back_sector=1)
    ed2.sm.invalidate()
    assert not [e for e in ed2.validate() if "sidedef" in e], ed2.validate()
    alive_before = _alive(ed2)
    v1, v2 = ed2.lines[1].v1, ed2.lines[1].v2
    front, back = ed2.lines[1].front, ed2.lines[1].back
    assert ed2.flip_line(1) is True, "flip refused on a two-sided wall"
    assert (ed2.lines[1].v1, ed2.lines[1].v2) == (v2, v1), "vertices not swapped"
    assert (ed2.lines[1].front, ed2.lines[1].back) == (back, front), "sides not swapped"
    ed2.sm.invalidate()
    assert _alive(ed2) == alive_before, sorted(_alive(ed2))
    assert not [e for e in ed2.validate() if "sidedef" in e], ed2.validate()
    print("  flip refused on a one-sided wall, applied on a two-sided one")


def test_wall_without_a_front_side_is_not_a_staircase_end():
    """A stair end reads its FRONT side — so a missing front must be refused.

    ``sides[-1]`` is the last sidedef in the file, i.e. somebody else's wall, and
    its floor height is a number the tool would happily build steps from.  Both
    messages the old code produced here were lies about the map.
    """
    # (a) both sides gone — what "1-ст." left behind before the repair
    ed = SectorEditor(_map())
    ed.lines[1].front = -1
    ed.lines[1].back = -1
    ed.sm.invalidate()
    assert ed.sides[-1].sector == ed.sides[ed.lines[5].front].sector, \
        "test no longer reproduces the sides[-1] mix-up"
    errs = _expect_reject(ed, _points(), "wall with no sides")
    assert "передней стороны" in errs, errs
    assert "одной высоте" not in errs, errs

    # (b) only the back side survived — what "Flip" used to leave behind
    ed2 = SectorEditor(_map())
    ld = ed2.lines[1]
    ld.v1, ld.v2 = ld.v2, ld.v1
    ld.front, ld.back = ld.back, ld.front
    assert ld.front < 0 and ld.back >= 0, (ld.front, ld.back)
    ed2.sm.invalidate()
    errs = _expect_reject(ed2, _points(), "wall with no front side")
    assert "передней стороны" in errs, errs
    assert "проём между секторами" not in errs, errs
    print("  stair end needs its own front side, and is told which wall is broken")


def test_stair_end_in_a_void_sector_is_refused():
    """A wall whose sector lost its contour bounds emptiness, not a room.

    The floor height is still a number, and building steps to it would put a
    staircase into the void — reachable from nowhere and leading nowhere.
    """
    ed = SectorEditor(_map())
    ed._drop(lines={0}, record=False)     # low room loses its top wall
    assert not ed.sm.has_polygon(0), "sector 0 was supposed to become void"
    errs = _expect_reject(ed, _points(), "stair end in a void sector")
    assert "не замкнулся в контур" in errs, errs
    print("  stair end in a sector that is void is refused by name")


def test_two_ends_of_one_sector_are_named_as_such():
    """Two walls of the same room are a bad pick, not a height problem.

    "полы секторов на одной высоте" is what the author saw, and it sent them
    looking for a height bug: both ends were simply walls of ONE sector, which
    has no height difference to climb by definition.
    """
    ed = SectorEditor(_map())
    # the low room's bottom wall (y=0) and top wall (y=384): two one-sided
    # walls of sector 0, with no corners in common
    for li in (0, 2):
        assert ed.lines[li].back < 0, "fixture: both must be one-sided"
        assert ed.sides[ed.lines[li].front].sector == 0
    pts = [(96.0, 0.0), (48.0, 0.0), (96.0, 384.0), (48.0, 384.0)]
    before = ed.sm.to_json()
    info, errs = ed.build_stair(pts)
    assert info is None, info
    msg = "; ".join(errs)
    assert "одному сектору 0" in msg, errs
    assert "одной высоте" not in msg, errs
    assert ed.sm.to_json() == before, "a rejected staircase changed the map"
    print("  two ends in one sector are named, not called a height tie")


def main():
    print("smoke_sector_stairs")
    test_treads_are_closed_and_stepped()
    test_solid_rails_when_no_neighbour()
    test_open_rails_to_a_real_neighbour()
    test_undo_redo_is_exact()
    test_mid_build_failure_rolls_back()
    test_rejections_leave_no_trace()
    test_flip_on_one_sided_wall_is_refused()
    test_wall_without_a_front_side_is_not_a_staircase_end()
    test_stair_end_in_a_void_sector_is_refused()
    test_two_ends_of_one_sector_are_named_as_such()
    test_click_order_does_not_matter()
    test_rise_is_clamped_and_bounded()
    test_default_rise_and_sector_metadata()
    test_tool_is_wired_in_the_screen()
    test_tool_buttons_stay_inside_the_panel()
    print("OK")


if __name__ == "__main__":
    main()