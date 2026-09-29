"""Smoke: sector map model — serialization, validation, geometry, converter.

Covers tasks 1.1-1.4 of the advanced-sector-editor change.
"""
from __future__ import annotations

from shared.map_data import DEFAULT_MAP, GameMap, TILE
from shared.sector_map import (
    LineDef, Sector, SectorMap, SideDef, Thing, Vertex,
    MAX_THINGS,
)


def make_rect(sm: SectorMap, x1: float, y1: float, x2: float, y2: float,
              sector_id: int) -> None:
    """Append a closed rectangular one-sided contour as ``sector_id`` border."""
    side_id = len(sm.sidedefs)
    sm.sidedefs.append(SideDef(sector=sector_id))
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    for i in range(4):
        ax, ay = corners[i]
        bx, by = corners[(i + 1) % 4]
        cross = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        if cross >= 0:
            ax, ay, bx, by = bx, by, ax, ay

        def vid(px: float, py: float) -> int:
            for idx, v in enumerate(sm.verts):
                if v.x == px and v.y == py:
                    return idx
            sm.verts.append(Vertex(px, py))
            return len(sm.verts) - 1

        sm.linedefs.append(LineDef(vid(ax, ay), vid(bx, by), front=side_id))


def make_simple_map(floor_h: int = 0, ceil_h: int = 128) -> SectorMap:
    sm = SectorMap()
    sm.sectors.append(Sector(floor_h=floor_h, ceil_h=ceil_h))
    make_rect(sm, 0, 0, 512, 512, 0)
    sm.things.append(Thing(256, 256))
    return sm


def test_roundtrip() -> None:
    sm = make_simple_map()
    js = sm.to_json()
    sm2 = SectorMap.from_json(js)
    assert sm2.to_json() == js, "json roundtrip mismatch"
    raw = sm.to_bytes()
    sm3 = SectorMap.from_bytes(raw)
    assert sm3.to_json() == js, "bytes roundtrip mismatch"
    # Unknown format rejected
    bad = dict(js)
    bad["format"] = "tile"
    try:
        SectorMap.from_json(bad)
        raise AssertionError("unknown format accepted")
    except ValueError:
        pass
    bad = dict(js)
    bad["format"] = "wad"
    try:
        SectorMap.from_json(bad)
        raise AssertionError("unknown format accepted")
    except ValueError:
        pass
    # Incompatible version rejected
    bad = dict(js)
    bad["version"] = 99
    try:
        SectorMap.from_json(bad)
        raise AssertionError("bad version accepted")
    except ValueError:
        pass
    # validate_network path
    assert SectorMap.validate_network(raw) is not None
    assert SectorMap.validate_network(b"not json") is None
    assert SectorMap.validate_network(b'{"format":"tile"}') is None
    print("  roundtrip OK")


def test_validation() -> None:
    valid = make_simple_map()
    assert valid.validate() == [], valid.validate()

    # Broken sidedef sector index
    sm = make_simple_map()
    sm.sidedefs[0].sector = 5
    assert any("sector 5 out of range" in e for e in sm.validate()), sm.validate()

    # Ceiling below floor
    sm = make_simple_map(floor_h=100, ceil_h=50)
    errs = sm.validate()
    assert any("floor 100 above ceiling 50" in e for e in errs), errs

    # Missing front side
    sm = make_simple_map()
    sm.linedefs[0].front = -1
    errs = sm.validate()
    assert any("missing front sidedef" in e for e in errs), errs

    # No player1
    sm = make_simple_map()
    sm.things.clear()
    errs = sm.validate()
    assert any("player1" in e for e in errs), errs

    # No sectors
    sm = SectorMap()
    sm.things.append(Thing(0, 0))
    errs = sm.validate()
    assert any("no sectors" in e for e in errs), errs

    # Duplicate one-sided linedefs rejected
    sm = make_simple_map()
    sm.linedefs.append(LineDef(sm.linedefs[0].v1, sm.linedefs[0].v2,
                               front=sm.linedefs[0].front))
    errs = sm.validate()
    assert any("duplicate one-sided" in e for e in errs), errs

    # Identical vertices
    sm = make_simple_map()
    sm.linedefs[0].v2 = sm.linedefs[0].v1
    errs = sm.validate()
    assert any("identical" in e for e in errs), errs

    # Element limits
    sm = make_simple_map()
    sm.things = [Thing(0, 0) for _ in range(MAX_THINGS + 1)]
    errs = sm.validate()
    assert any("too many things" in e for e in errs), errs
    print("  validation OK")


def test_geometry() -> None:
    # Nested sectors: inner square fully inside outer square (independent
    # contours), inner has its own heights.
    sm = SectorMap()
    sm.sectors.append(Sector(floor_h=0, ceil_h=128))    # outer
    sm.sectors.append(Sector(floor_h=16, ceil_h=96))    # inner
    make_rect(sm, 0, 0, 512, 512, 0)
    make_rect(sm, 128, 128, 384, 384, 1)
    sm.things.append(Thing(10, 10))          # outer ring
    sm.things.append(Thing(256, 256))        # inner room
    sm.things.append(Thing(200, 100))        # inner room (x=200,y=100 is in inner? y=100 <128 -> outer)
    assert sm.validate() == [], sm.validate()

    # Inner point -> inner sector (smallest containing loop)
    assert sm.find_sector_at(256, 256) == 1, sm.find_sector_at(256, 256)
    # Ring point -> outer sector
    assert sm.find_sector_at(10, 10) == 0, sm.find_sector_at(10, 10)
    assert sm.find_sector_at(64, 256) == 0, sm.find_sector_at(64, 256)
    # Outside everything -> void
    assert sm.find_sector_at(-100, 256) == -1
    assert sm.find_sector_at(600, 256) == -1

    # Floor/ceil follow the innermost sector
    assert sm.get_floor_at(256, 256) == 16
    assert sm.get_ceil_at(256, 256) == 96
    assert sm.get_floor_at(10, 10) == 0
    assert sm.get_ceil_at(10, 10) == 128

    # Spawns
    spawns = sm.get_spawn_points()
    assert spawns == [(10.0, 10.0), (256.0, 256.0), (200.0, 100.0)], spawns
    assert sm.spawn_px() == spawns
    print("  geometry OK")


def test_from_tiles() -> None:
    gm = GameMap()  # DEFAULT_MAP
    sm = SectorMap.from_tiles(gm)
    errs = sm.validate()
    assert errs == [], errs
    # Walkability parity: every empty cell centre is inside a sector,
    # every wall cell centre is void.
    mismatches = 0
    for y in range(gm.height):
        for x in range(gm.width):
            cx = (x + 0.5) * TILE
            cy = (y + 0.5) * TILE
            tile_empty = gm.get_tile(x, y) == 0
            in_sector = sm.find_sector_at(cx, cy) >= 0
            if tile_empty != in_sector:
                mismatches += 1
    assert mismatches == 0, f"{mismatches} walkability mismatches"
    # One spawn per region
    assert len(sm.get_spawn_points()) >= 1
    # Spawns are inside sectors
    for sx, sy in sm.get_spawn_points():
        assert sm.find_sector_at(sx, sy) >= 0, (sx, sy)
    # Roundtrip after conversion
    sm2 = SectorMap.from_bytes(sm.to_bytes())
    assert sm2.validate() == []
    assert len(sm2.linedefs) == len(sm.linedefs)
    print(f"  from_tiles OK ({len(sm.sectors)} sectors, "
          f"{len(sm.linedefs)} linedefs, "
          f"{len(sm.get_spawn_points())} spawns)")


def test_physics() -> None:
    # Two rooms sharing a two-sided portal at x=512.
    # Room A: x 0..512, Room B: x 512..1024, both y 0..512.
    sm = SectorMap()
    sm.sectors.append(Sector())
    sm.sectors.append(Sector())
    side_a = len(sm.sidedefs)
    sm.sidedefs.append(SideDef(sector=0))
    side_b = len(sm.sidedefs)
    sm.sidedefs.append(SideDef(sector=1))

    def vid(px: float, py: float) -> int:
        for idx, v in enumerate(sm.verts):
            if v.x == px and v.y == py:
                return idx
        sm.verts.append(Vertex(px, py))
        return len(sm.verts) - 1

    def edge(ax, ay, bx, by, side, ref):
        # Orient so `ref` (interior point) lies on the front side
        # (cross((v2-v1), (ref-v1)) < 0).
        cross = (bx - ax) * (ref[1] - ay) - (by - ay) * (ref[0] - ax)
        if cross >= 0:
            ax, ay, bx, by = bx, by, ax, ay
        sm.linedefs.append(LineDef(vid(ax, ay), vid(bx, by), front=side))

    ca, cb = (256, 256), (768, 256)
    # Room A outer walls
    edge(0, 0, 512, 0, side_a, ca)
    edge(0, 512, 0, 0, side_a, ca)
    edge(512, 512, 0, 512, side_a, ca)
    # Room B outer walls
    edge(512, 0, 1024, 0, side_b, cb)
    edge(1024, 0, 1024, 512, side_b, cb)
    edge(1024, 512, 512, 512, side_b, cb)
    # Two-sided portal at x=512 (front = A)
    edge(512, 0, 512, 512, side_a, ca)
    sm.linedefs[-1].back = side_b
    sm.things.append(Thing(256, 256))
    sm.things.append(Thing(768, 256))
    errs = sm.validate()
    assert errs == [], errs
    assert sm.find_sector_at(256, 256) == 0
    assert sm.find_sector_at(768, 256) == 1

    # Wall collision: circle near the left outer wall is blocked.
    assert not sm.can_move_px(8, 256, 16), "should collide with left wall"
    assert sm.can_move_px(64, 256, 16), "open floor should be free"
    # Sliding along the wall at distance >= r is allowed
    assert sm.can_move_px(20, 256, 16)
    # Through the portal (two-sided) movement allowed
    assert sm.can_move_px(512, 256, 16), "portal must be passable"
    # Void is solid
    assert sm.is_wall_px(-50, 256)
    assert not sm.is_wall_px(256, 256)
    # LOS: across the portal (two-sided) is clear
    assert sm.has_los_px(256, 256, 768, 256), "portal LOS must be clear"
    # LOS blocked by the outer one-sided wall
    assert not sm.has_los_px(256, 256, 256, -64), "outer wall blocks LOS"
    print("  physics OK")


def two_room_map(floor_a: int = 0, floor_b: int = 0,
                 solid: bool = False) -> tuple[SectorMap, int]:
    """Room A x 0..512 and room B x 512..1024 (y 0..512) joined by a portal.

    Returns the map and the index of the shared two-sided linedef. ``solid``
    marks that portal as an opaque wall.
    """
    sm = SectorMap()
    sm.sectors.append(Sector(floor_h=floor_a, ceil_h=256))
    sm.sectors.append(Sector(floor_h=floor_b, ceil_h=256))
    side_a = len(sm.sidedefs)
    sm.sidedefs.append(SideDef(sector=0))
    side_b = len(sm.sidedefs)
    sm.sidedefs.append(SideDef(sector=1))

    def vid(px: float, py: float) -> int:
        for idx, v in enumerate(sm.verts):
            if v.x == px and v.y == py:
                return idx
        sm.verts.append(Vertex(px, py))
        return len(sm.verts) - 1

    def edge(ax, ay, bx, by, side, ref):
        cross = (bx - ax) * (ref[1] - ay) - (by - ay) * (ref[0] - ax)
        if cross >= 0:
            ax, ay, bx, by = bx, by, ax, ay
        sm.linedefs.append(LineDef(vid(ax, ay), vid(bx, by), front=side))

    ca, cb = (256, 256), (768, 256)
    edge(0, 0, 512, 0, side_a, ca)
    edge(0, 512, 0, 0, side_a, ca)
    edge(512, 512, 0, 512, side_a, ca)
    edge(512, 0, 1024, 0, side_b, cb)
    edge(1024, 0, 1024, 512, side_b, cb)
    edge(1024, 512, 512, 512, side_b, cb)
    edge(512, 0, 512, 512, side_a, ca)
    portal = len(sm.linedefs) - 1
    sm.linedefs[portal].back = side_b
    sm.linedefs[portal].solid = solid
    sm.things.append(Thing(256, 256))
    sm.things.append(Thing(768, 256))
    return sm, portal


def test_passability() -> None:
    """line_blocks + the single passability rule behind movement and LOS."""
    from shared import mt1 as _mt1
    from shared.sector_map import LINE_FLAG_SOLID, STEP_HEIGHT

    assert STEP_HEIGHT == 24, STEP_HEIGHT

    # flags round-trip keeps other bits intact
    sm, portal = two_room_map()
    ld = sm.linedefs[portal]
    ld.flags |= 0x40
    ld.solid = True
    assert ld.solid and ld.flags == (0x40 | LINE_FLAG_SOLID), ld.flags
    ld.solid = False
    assert not ld.solid and ld.flags == 0x40, ld.flags

    # one-sided linedef, two-sided equal heights, step 16, step 32, solid
    sm_eq, p_eq = two_room_map(0, 0)
    sm_step, p_step = two_room_map(0, 16)
    sm_tall, p_tall = two_room_map(0, 32)
    sm_solid, p_solid = two_room_map(0, 0, solid=True)
    assert sm_eq.validate() == [], sm_eq.validate()
    assert sm_step.validate() == [], sm_step.validate()
    assert sm_tall.validate() == [], "a tall step is valid geometry"
    assert sm_solid.validate() == [], sm_solid.validate()

    wall = 0  # Room A's outer wall at y=0 is one-sided
    assert sm_eq.line_blocks(wall) is True
    assert sm_eq.line_blocks(p_eq) is False
    assert sm_step.line_blocks(p_step) is False
    assert sm_tall.line_blocks(p_tall) is True
    assert sm_solid.line_blocks(p_solid) is True

    # exactly at the limit the step still passes, one unit more does not
    sm_lim, p_lim = two_room_map(0, STEP_HEIGHT)
    sm_over, p_over = two_room_map(0, STEP_HEIGHT + 1)
    assert sm_lim.line_blocks(p_lim) is False
    assert sm_over.line_blocks(p_over) is True

    # movement and sight agree with line_blocks
    assert sm_eq.can_move_px(512, 256, 16)
    assert sm_step.can_move_px(512, 256, 16)
    assert not sm_tall.can_move_px(512, 256, 16)
    assert not sm_solid.can_move_px(512, 256, 16)
    assert sm_eq.has_los_px(256, 256, 768, 256)
    assert sm_step.has_los_px(256, 256, 768, 256)
    assert not sm_tall.has_los_px(256, 256, 768, 256)
    assert not sm_solid.has_los_px(256, 256, 768, 256)

    # a solid wall is opaque but its outer walls still behave
    assert not sm_solid.is_wall_px(768, 256)
    assert sm_solid.is_wall_px(1200, 256)

    # REJECT stays a one-sided-wall mechanism: a two-sided linedef always bounds
    # one of the two sectors it joins, so that sector's bbox touches the wall and
    # the corner test bails out. Guard the code path with the layout it does
    # serve — three sectors, a void-free gap, a one-sided wall in the middle.
    gap = SectorMap()
    for fh in (0, 0, 0):
        gap.sectors.append(Sector(floor_h=fh, ceil_h=256))
    sides = []
    for si in range(3):
        sides.append(len(gap.sidedefs))
        gap.sidedefs.append(SideDef(sector=si))

    def gvid(px: float, py: float) -> int:
        for idx, v in enumerate(gap.verts):
            if v.x == px and v.y == py:
                return idx
        gap.verts.append(Vertex(px, py))
        return len(gap.verts) - 1

    def gedge(ax, ay, bx, by, side, ref):
        cross = (bx - ax) * (ref[1] - ay) - (by - ay) * (ref[0] - ax)
        if cross >= 0:
            ax, ay, bx, by = bx, by, ax, ay
        gap.linedefs.append(LineDef(gvid(ax, ay), gvid(bx, by), front=side))

    gedge(0, 0, 400, 0, sides[0], (200, 256))
    gedge(0, 512, 0, 0, sides[0], (200, 256))
    gedge(400, 512, 0, 512, sides[0], (200, 256))
    gedge(425, 0, 425, 512, sides[1], (500, 256))   # one-sided wall in the gap
    gedge(600, 0, 1000, 0, sides[2], (800, 256))
    gedge(1000, 0, 1000, 512, sides[2], (800, 256))
    gedge(1000, 512, 600, 512, sides[2], (800, 256))
    gap.things.append(Thing(200, 256))
    rej = gap.build_reject()
    assert _mt1.reject_test(rej, 3, 0, 2), "one-sided wall must reject sight"
    assert not _mt1.reject_test(rej, 3, 0, 1), "sector 0 sees its neighbour"

    # making that wall a two-sided line does not open sight 0<->2: the gap
    # between the rooms belongs to no sector, so REJECT stays right either way
    for solid, floor_b in ((True, 0), (False, 32)):
        gap.linedefs[6].back = sides[1]
        gap.linedefs[6].solid = solid
        gap.sectors[1].floor_h = floor_b
        gap.invalidate()
        assert _mt1.reject_test(gap.build_reject(), 3, 0, 2)
        assert gap.line_blocks(6) is True, (solid, floor_b)
    assert not _mt1.reject_test(gap.build_reject(), 3, 0, 1), \
        "sector 0 still sees its neighbour"

    # validation: solid is only meaningful on a two-sided linedef
    sm_bad, p_bad = two_room_map()
    sm_bad.linedefs[wall].solid = True
    errs = sm_bad.validate()
    assert any("solid flag on a one-sided linedef" in e for e in errs), errs

    # passability flips survive invalidate() without an explicit rebuild, and
    # movement and sight keep agreeing with line_blocks on both sides
    sm_flip, p_flip = two_room_map(0, 0, solid=True)
    sm_flip.build_reject()
    assert not sm_flip.has_los_px(256, 256, 768, 256)
    sm_flip.linedefs[p_flip].solid = False
    sm_flip.invalidate()
    assert sm_flip.has_los_px(256, 256, 768, 256), \
        "clearing solid must restore sight after invalidate()"
    sm_flip.linedefs[p_flip].solid = True
    sm_flip.invalidate()
    assert not sm_flip.has_los_px(256, 256, 768, 256)
    assert not sm_flip.can_move_px(512, 256, 16)
    print("  passability OK")


def test_protocol_roundtrip() -> None:
    from shared.protocol import (
        decode_create_party_ex, decode_join_accept,
        encode_create_party, encode_join_accept,
    )
    from shared.sector_map import MAP_FORMAT_SECTOR, MAP_FORMAT_TILE

    raw = make_simple_map().to_bytes()

    # CREATE_PARTY with tile format (0)
    p = encode_create_party("t", "pve", 16, 16, b"\x00" * 256, 7,
                            MAP_FORMAT_TILE)
    name, mode, mw, mh, mdata, rid, fmt = decode_create_party_ex(p)
    assert (name, mode, mw, mh, rid, fmt) == ("t", "pve", 16, 16, 7, 0)
    assert mdata == b"\x00" * 256

    # CREATE_PARTY with sector format (1), w/h = 0
    p = encode_create_party("s", "pve", 0, 0, raw, 42, MAP_FORMAT_SECTOR)
    name, mode, mw, mh, mdata, rid, fmt = decode_create_party_ex(p)
    assert (name, mw, mh, rid, fmt) == ("s", 0, 0, 42, 1)
    assert mdata == raw
    assert SectorMap.from_bytes(mdata).validate() == []

    # CREATE_PARTY without map
    p = encode_create_party("n", "pvp", 0, 0, b"", 1, MAP_FORMAT_TILE)
    name, mode, mw, mh, mdata, rid, fmt = decode_create_party_ex(p)
    assert mdata == b"" and fmt == 0 and mode == "pvp"

    # JOIN_ACCEPT format 0 / 1
    p = encode_join_accept(5, 16, 16, bytes(256), MAP_FORMAT_TILE)
    pid, mw, mh, has_map, mdata, fmt = decode_join_accept(p)
    assert (pid, mw, mh, has_map, fmt) == (5, 16, 16, True, 0)
    assert mdata == bytes(256)

    p = encode_join_accept(6, 0, 0, raw, MAP_FORMAT_SECTOR)
    pid, mw, mh, has_map, mdata, fmt = decode_join_accept(p)
    assert (pid, mw, mh, has_map, fmt) == (6, 0, 0, True, 1)
    assert mdata == raw

    p = encode_join_accept(7)
    pid, mw, mh, has_map, mdata, fmt = decode_join_accept(p)
    assert not has_map and mdata == b"" and fmt == 0
    print("  protocol roundtrip OK")


if __name__ == "__main__":
    test_roundtrip()
    test_validation()
    test_geometry()
    test_from_tiles()
    test_physics()
    test_passability()
    test_protocol_roundtrip()
    print("SECTOR MODEL TEST PASSED")
