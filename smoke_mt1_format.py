"""Smoke: ``.mt1`` container and lump round-trip (task 1.1+).

Grows with the format change:
- 1.1  container pack/unpack, catalog integrity, corrupt-byte detection
- 1.2  geometry lumps round-trip (added as the slices land)
- 2.1  BSP lumps round-trip
"""
import struct
import tempfile

from shared import mt1
from shared.mt1 import (
    ALL_LUMPS,
    ENTRY_SIZE,
    HEADER_SIZE,
    L_LINEDEFS,
    L_NAMES,
    L_SECTORS,
    L_SIDEDEFS,
    L_THINGS,
    L_VERTEXES,
    MAGIC,
    MT1_VERSION,
    Mt1Code,
    Mt1Error,
    pack,
    unpack,
)
from shared.sector_map import (
    LINE_FLAG_SOLID,
    LineDef,
    Sector,
    SectorMap,
    SideDef,
    Thing,
    Vertex,
)


def _slices() -> dict[str, bytes]:
    payloads = {
        name: bytes([i % 256 for i in range(4 * (i + 1))])
        for i, name in enumerate(ALL_LUMPS)
    }
    return payloads


def _check_roundtrip(data: dict[str, bytes]) -> None:
    raw = pack(data)
    out = unpack(raw)
    assert list(out.keys()) == list(data.keys()), (
        f"order/names: {list(out.keys())} vs {list(data.keys())}")
    for name, payload in data.items():
        assert out[name] == payload, f"payload mismatch for {name}"
    print("container round-trip OK")


def _check_header(raw: bytes) -> None:
    n_lumps = len(ALL_LUMPS)
    magic, version, count, cat_off, _ = mt1.HEADER_STRUCT.unpack(raw[:HEADER_SIZE])
    assert magic == MAGIC
    assert version == MT1_VERSION
    assert count == n_lumps
    assert cat_off == HEADER_SIZE
    print("header OK")


def _check_corrupt(raw: bytes) -> None:
    # Catalog entry pointing beyond the file end.
    bad = bytearray(raw)
    cat_off = HEADER_SIZE
    bad[cat_off:cat_off + 4] = struct.pack("!I", 0x7FFFFFFF)
    try:
        unpack(bytes(bad))
        raise AssertionError("out-of-file lump offset was accepted")
    except Mt1Error as e:
        assert e.code == Mt1Code.BAD_CATALOG_ENTRY, e
    # Bad catalog offset.
    bad2 = bytearray(raw)
    bad2[8:12] = struct.pack("!I", 0xFFFFFFF0)
    try:
        unpack(bytes(bad2))
        raise AssertionError("bad catalog offset was accepted")
    except Mt1Error as e:
        assert e.code == Mt1Code.BAD_CATALOG_OFFSET, e
    # Checksum break: flip one payload byte.
    bad3 = bytearray(raw)
    bad3[HEADER_SIZE + len(_slices()) * ENTRY_SIZE + 3] ^= 0xFF
    try:
        unpack(bytes(bad3))
        raise AssertionError("flipped payload byte was accepted")
    except Mt1Error as e:
        assert e.code == Mt1Code.BAD_CHECKSUM, e
    # Wrong magic.
    bad4 = bytearray(raw)
    bad4[0:4] = b"NOPE"
    try:
        unpack(bytes(bad4))
        raise AssertionError("bad magic was accepted")
    except Mt1Error as e:
        assert e.code == Mt1Code.BAD_MAGIC, e
    print("container corruption rejected (bounds/offset/checksum/magic) OK")


def _check_duplicate_and_name() -> None:
    try:
        pack([("A", b"x"), ("A", b"y")])
        raise AssertionError("duplicate lump accepted")
    except Mt1Error as e:
        assert e.code == Mt1Code.DUPLICATE_LUMP
    try:
        pack({L_VERTEXES: b"x", "TOOLONGNAME": b"y"})
        raise AssertionError("oversized lump name accepted")
    except Mt1Error as e:
        assert e.code == Mt1Code.BAD_LUMP_NAME
    print("duplicate/oversized lump rejection OK")


def _sample_map() -> SectorMap:
    """Two connected rooms with distinct textures/heights + a negative z
    thing, exercising all attribute kinds of the model."""
    sm = SectorMap()
    for x, y in ((0, 0), (512, 0), (512, 512), (0, 512),
                 (1024, 128), (1024, 384), (704, 384), (704, 128)):
        sm.verts.append(Vertex(float(x), float(y)))
    side_a = SideDef(upper="-", mid="wall", lower="-",
                     x_off=3, y_off=-2, sector=0)
    side_b = SideDef(upper="-", mid="-", lower="-",
                     x_off=7, y_off=5, sector=1)
    sm.sidedefs.append(side_a)
    sm.sidedefs.append(side_b)
    sm.sectors.append(Sector(floor_h=0, ceil_h=128, floor_tex="floor",
                             ceil_tex="ceiling", light=160, special=1, tag=7))
    sm.sectors.append(Sector(floor_h=-64, ceil_h=192, floor_tex="lava",
                             ceil_tex="skies", light=96, special=0, tag=0))
    # Outer room A (CCW) + two-sided portal to room B.
    sm.linedefs = [
        LineDef(0, 1, front=0),
        LineDef(1, 2, front=0),
        LineDef(2, 3, front=0),
        LineDef(3, 0, front=0),
        LineDef(1, 4, front=0, back=1),  # portal 1->2
        LineDef(4, 5, front=1),
        LineDef(5, 6, front=1),
        LineDef(6, 7, front=1),
        LineDef(7, 4, front=1),
    ]
    sm.things = [
        Thing(256.0, 256.0, "player1", angle=0),
        Thing(880.0, 256.0, "imp", angle=90, z=0.0),
    ]
    sm.invalidate()
    return sm


def _attrs_map(sm: SectorMap) -> tuple:
    return (
        [(v.x, v.y) for v in sm.verts],
        [(ld.v1, ld.v2, ld.flags, ld.special, ld.tag, ld.front, ld.back)
         for ld in sm.linedefs],
        [(sd.upper, sd.mid, sd.lower, sd.x_off, sd.y_off, sd.sector)
         for sd in sm.sidedefs],
        [(s.floor_h, s.ceil_h, s.floor_tex, s.ceil_tex, s.light, s.special,
          s.tag) for s in sm.sectors],
        [(t.x, t.y, t.type, t.angle, t.z) for t in sm.things],
    )


def test_geometry_lumps():
    sm = _sample_map()
    a0 = _attrs_map(sm)
    lumps = mt1.encode_geometry(
        [(v.x, v.y) for v in sm.verts],
        [(ld.v1, ld.v2, ld.flags, ld.special, ld.tag, ld.front, ld.back)
         for ld in sm.linedefs],
        [(sd.upper, sd.mid, sd.lower, sd.x_off, sd.y_off, sd.sector)
         for sd in sm.sidedefs],
        [(s.floor_h, s.ceil_h, s.floor_tex, s.ceil_tex, s.light, s.special,
          s.tag) for s in sm.sectors],
        [(t.x, t.y, t.type, t.angle, t.z) for t in sm.things],
    )
    assert set(lumps) == {L_VERTEXES, L_LINEDEFS, L_SIDEDEFS, L_SECTORS,
                          L_THINGS, L_NAMES}

    # Packed + unpacked slice round-trip must be byte-identical.
    raw = pack(lumps)
    out = unpack(raw)
    for name, payload in lumps.items():
        assert out[name] == payload, name

    # String dedup: "wall" appears twice but must live once in NAMES.
    names = mt1.decode_names(lumps[L_NAMES])
    assert names.count("wall") == 1
    assert names.count("-") == 1  # all "-" sides dedupe to a single entry

    v2, l2, sd2, s2, t2 = mt1.decode_geometry(out)
    a1 = (v2, l2, sd2, s2, t2)
    assert a1 == a0, (a0, a1)

    # Re-encode from the decoded tuples -> identical slices (stable round-trip).
    lumps2 = mt1.encode_geometry(v2, l2, sd2, s2, t2)
    assert lumps2["VERTEXES"] == lumps["VERTEXES"]
    assert lumps2["LINEDEFS"] == lumps["LINEDEFS"]
    assert lumps2["SIDEDEFS"] == lumps["SIDEDEFS"]
    assert lumps2["SECTORS"] == lumps["SECTORS"]
    assert lumps2["THINGS"] == lumps["THINGS"]
    assert lumps2["NAMES"] == lumps["NAMES"]
    print("geometry slices round-trip exact (values + names dedup) OK")


def test_geometry_bad_names_index():
    lumps = mt1.encode_geometry([(0.0, 0.0)], [(0, 0, 0, 0, 0, -1, -1)], [],
                                [], [(8.0, 8.0, "player1", 0, 0.0)])
    lumps[L_SIDEDEFS] = mt1.encode_sidedefs([(3, 0, 0, 0, 0, 0)])  # idx 3 OOB
    try:
        mt1.decode_geometry(lumps)
        raise AssertionError("out-of-range NAMES index accepted")
    except Mt1Error as e:
        assert e.code == Mt1Code.NAMES_INDEX, e
    print("out-of-range NAMES index rejected OK")


def test_geometry_range_guard():
    try:
        mt1.encode_sectors([(0, 0, 0, 0, 10**6, 0, 0)])  # light OOB
        raise AssertionError("light > u16 accepted")
    except Mt1Error as e:
        assert e.code == Mt1Code.OUT_OF_RANGE, e
    try:
        mt1.encode_sectors([(70000, 0, 0, 0, 0, 0, 0)])  # floor_h OOB
        raise AssertionError("sector height > i16 accepted")
    except Mt1Error as e:
        assert e.code == Mt1Code.OUT_OF_RANGE, e
    print("wire-format range guard OK")


def test_model_import_save(tmp_path: str) -> None:
    # Old JSON map (as written by the previous version) must load as an
    # import and save as a readable .mt1 with identical values.
    import json as _json
    import os

    sm = _sample_map()
    js_path = os.path.join(tmp_path, "map1.json")
    with open(js_path, "w", encoding="utf-8") as f:
        _json.dump(sm.to_json(), f, ensure_ascii=False)

    js_loaded = SectorMap.load(js_path)
    assert js_loaded.to_json() == sm.to_json(), "JSON import lost values"

    mt1_path = os.path.join(tmp_path, "map1.mt1")
    js_loaded.save(mt1_path)
    mt1_loaded = SectorMap.load(mt1_path)
    assert mt1_loaded.to_json() == sm.to_json(), "mt1 save/load lost values"
    with open(mt1_path, "rb") as f:
        head = f.read(4)
    assert head == MAGIC, "saved file is not an .mt1 container"

    # to_bytes is pure .mt1 (no JSON anywhere).
    raw = sm.to_bytes()
    assert raw[:4] == MAGIC
    assert SectorMap.from_bytes(raw).to_json() == sm.to_json()

    # from_json/to_json API still works (debug/export path).
    assert SectorMap.from_json(sm.to_json()).to_json() == sm.to_json()
    print("model JSON import -> .mt1 save/load round-trip OK")


def test_solid_flag_roundtrip() -> None:
    """The solid flag is a bit in linedef flags: same .mt1 version, no new
    field, and every other flag bit survives the round-trip."""
    assert MT1_VERSION == 1, "solid must not require a container version bump"

    sm = _sample_map()
    # a two-sided linedef in the sample, else build one on a spare pair
    two = next((ld for ld in sm.linedefs if ld.back >= 0), None)
    if two is None:
        two = sm.linedefs[0]
        two.back = sm.sidedefs[0].sector
    before_flags = two.flags
    two.flags |= 0x40
    two.solid = True
    assert two.solid, "solid setter did not take"
    assert two.flags == (0x40 | LINE_FLAG_SOLID), two.flags

    js = sm.to_json()
    back = SectorMap.from_json(js)
    assert back.to_json() == js, "solid lost in JSON"
    raw = sm.to_bytes()
    assert SectorMap.from_bytes(raw).to_json() == js, "solid lost in .mt1"
    # MT1_VERSION is still the one written into the header
    assert raw[:4] == MAGIC
    assert int.from_bytes(raw[4:6], "big") == MT1_VERSION

    two.solid = False
    two.flags = before_flags
    assert two.flags == before_flags
    assert not two.solid
    print("solid flag JSON/.mt1 round-trip on .mt1 version 1 OK")


def _broken_geometry_map() -> SectorMap:
    """Valid model used to build mutated containers below."""
    sm = SectorMap()
    sm.verts.extend([Vertex(0, 0), Vertex(100, 0), Vertex(100, 100),
                     Vertex(0, 100)])
    sm.sectors.append(Sector(floor_h=0, ceil_h=128))
    sm.sidedefs.append(SideDef(mid="wall", sector=0))
    for i in range(4):
        j = (i + 1) % 4
        sm.linedefs.append(LineDef(i, j, front=0))
    sm.things.append(Thing(50, 50, "player1"))
    return sm


def test_container_validation():
    sm = _broken_geometry_map()
    good = sm.to_bytes()
    assert SectorMap.validate_bytes(good) == []
    assert SectorMap.validate_network(good) is not None

    # Truncated file (cut mid-LINEDEFS).
    cut = good[:len(good) - 13]
    errs = SectorMap.validate_bytes(cut)
    assert errs, "truncated container must be rejected"
    assert SectorMap.validate_network(cut) is None

    # Vertex reference beyond VERTEXES slice: re-encode with a bad index.
    lumps = mt1.unpack(good)
    lumps[L_LINEDEFS] = mt1.encode_linedefs([(9999, 1, 0, 0, 0, 0, -1),
                                             (0, 1, 0, 0, 0, 0, -1),
                                             (1, 2, 0, 0, 0, 0, -1),
                                             (2, 3, 0, 0, 0, 0, -1)])
    bad = mt1.pack(lumps)
    errs = SectorMap.validate_bytes(bad)
    assert any("vertex index out of range" in e for e in errs), errs
    assert SectorMap.validate_network(bad) is None

    # Sector front side missing: linedef front=-1.
    lumps = mt1.unpack(good)
    lumps[L_LINEDEFS] = mt1.encode_linedefs([(0, 1, 0, 0, 0, -1, -1),
                                             (1, 2, 0, 0, 0, -1, -1),
                                             (2, 3, 0, 0, 0, -1, -1),
                                             (3, 0, 0, 0, 0, -1, -1)])
    bad = mt1.pack(lumps)
    errs = SectorMap.validate_bytes(bad)
    assert any("missing front sidedef" in e for e in errs), errs
    assert SectorMap.validate_network(bad) is None

    # No sectors at all.
    lumps = mt1.unpack(good)
    lumps[L_SECTORS] = mt1.encode_sectors([])
    bad = mt1.pack(lumps)
    errs = SectorMap.validate_bytes(bad)
    assert any("no sectors" in e for e in errs), errs
    assert SectorMap.validate_network(bad) is None

    # No player1 spawn.
    lumps = mt1.unpack(good)
    lumps[L_THINGS] = mt1.encode_things([])
    bad = mt1.pack(lumps)
    errs = SectorMap.validate_bytes(bad)
    assert any("player1 spawn" in e for e in errs), errs
    assert SectorMap.validate_network(bad) is None

    # floor_h > ceil_h.
    lumps = mt1.unpack(good)
    lumps[L_SECTORS] = mt1.encode_sectors([(128, 0, 0, 0, 160, 0, 0)])
    bad = mt1.pack(lumps)
    errs = SectorMap.validate_bytes(bad)
    assert any("above ceiling" in e for e in errs), errs
    assert SectorMap.validate_network(bad) is None

    # Oversized payload.
    assert SectorMap.validate_bytes(b"MT1\x00" * 500_000 + b"\x00" * 100_000)
    assert SectorMap.validate_network(
        b"MT1\x00" * 500_000 + b"\x00" * 100_000) is None

    print("container/model validation (truncate/refs/sector/spawn/floor) OK")


def _two_room_map() -> SectorMap:
    """Room A (0..512)^2 + room B, joined by a two-sided portal.  Portal's
    front/back segs must land in different subsectors for correct near->far
    ordering at the window."""
    sm = SectorMap()
    for x, y in ((0, 0), (512, 0), (512, 512), (0, 512),
                 (1024, 128), (1024, 384), (704, 384), (704, 128)):
        sm.verts.append(Vertex(float(x), float(y)))
    sm.sidedefs.append(SideDef(mid="wall", sector=0))
    sm.sidedefs.append(SideDef(mid="wall", sector=1))
    sm.sectors.append(Sector(floor_h=0, ceil_h=128, floor_tex="floor",
                             ceil_tex="ceiling"))
    sm.sectors.append(Sector(floor_h=0, ceil_h=128, floor_tex="floor",
                             ceil_tex="ceiling"))
    # Outer room A (CCW) + two-sided portal to room B.
    sm.linedefs = [
        LineDef(0, 1, front=0),
        LineDef(1, 2, front=0),
        LineDef(2, 3, front=0),
        LineDef(3, 0, front=0),
        LineDef(1, 4, front=0, back=1),  # portal: A side + B side
        LineDef(4, 5, front=1),
        LineDef(5, 6, front=1),
        LineDef(6, 7, front=1),
        LineDef(7, 4, front=1),
    ]
    sm.things = [
        Thing(256.0, 256.0, "player1", angle=0),
        Thing(880.0, 256.0, "player1", angle=90),
    ]
    sm.invalidate()
    return sm


def _visit(tree, px: float, py: float) -> list[int]:
    """Near->far subsector order for viewpoint (px, py), DOOM R_RenderBSPNode."""
    from engine.bsp import Sub

    order: list[int] = []

    def walk(node) -> None:
        if isinstance(node, Sub):
            order.append(node.idx)
            return
        sx, sy = node.px, node.py
        ddx, ddy = node.pdx, node.pdy
        side = ddx * (py - sy) - ddy * (px - sx)
        if side >= 0:
            near, far = node.front, node.back
        else:
            near, far = node.back, node.front
        walk(near)
        walk(far)

    walk(tree.root)
    return order


def test_bsp_builder():
    from engine.bsp import build_bsp

    # A single convex room collapses into one subsector with no nodes.
    sm = SectorMap()
    for x, y in ((0, 0), (512, 0), (512, 512), (0, 512)):
        sm.verts.append(Vertex(float(x), float(y)))
    sm.sidedefs.append(SideDef(mid="wall", sector=0))
    sm.sectors.append(Sector(0, 128))
    sm.linedefs = [
        LineDef(0, 1, front=0), LineDef(1, 2, front=0),
        LineDef(2, 3, front=0), LineDef(3, 0, front=0),
    ]
    sm.things.append(Thing(256, 256, "player1"))
    sm.invalidate()
    tree = build_bsp(sm)
    assert tree.seg_count() == 4
    assert tree.subsector_count() == 1
    assert tree.node_count() == 0
    assert tree.sub_segs() == [[0, 1, 2, 3]]

    # Two rooms + portal: the *rooms* must land in disjoint subsectors and,
    # seen from a viewpoint in one room, that room's subsectors must all
    # be visited (drawn) before the other room's — near->far at the tree level.
    tree = build_bsp(_two_room_map())
    a_lins, b_lins = {0, 1, 2, 3}, {5, 6, 7, 8}

    def subs_of(lins) -> set:
        return {tree.subsector_of_seg(si)
                for si in range(tree.seg_count()) if tree.linedefs[si] in lins}

    subs_a, subs_b = subs_of(a_lins), subs_of(b_lins)
    assert subs_a and subs_b
    assert subs_a.isdisjoint(subs_b), "rooms must split into distinct subs"

    def all_before(order, first: set, second: set) -> bool:
        return max(order.index(s) for s in first) < min(order.index(s)
                                                        for s in second)

    assert all_before(_visit(tree, 100.0, 100.0), subs_a, subs_b), \
        "from room A room A must draw first (near walls clip the window)"
    assert all_before(_visit(tree, 880.0, 256.0), subs_b, subs_a), \
        "from room B room B must draw first"

    # Every subsector visited exactly once from either side (full coverage).
    for order in (_visit(tree, 100.0, 100.0), _visit(tree, 880.0, 256.0)):
        assert sorted(order) == list(range(tree.subsector_count()))

    # Finite tree: recursive build terminates with a bounded depth.
    def depth(node) -> int:
        from engine.bsp import Sub as _Sub
        if isinstance(node, _Sub):
            return 0
        return 1 + max(depth(node.front), depth(node.back))

    assert 0 < depth(tree.root) <= 64
    print("BSP builder: convex collapse, room split, near->far order OK")


def test_bsp_lumps_roundtrip():
    from engine.bsp import build_bsp
    from shared.mt1 import (L_NODES, L_SEGS, L_SUBSECTORS, decode_nodes,
                            decode_segs, decode_subsectors)

    sm = _two_room_map()
    tree = build_bsp(sm)
    lumps = tree.to_lumps(sm.verts)

    segs = decode_segs(lumps[L_SEGS])
    subs = decode_subsectors(lumps[L_SUBSECTORS])
    nodes = decode_nodes(lumps[L_NODES])
    n_verts = len(lumps["VERTEX_EXTRA"])

    # Every seg endpoint indexes into the (extended) vertex table.
    assert all(0 <= v1 < n_verts and 0 <= v2 < n_verts for v1, v2, *_ in segs)

    # Subsector records are non-overlapping ranges covering all segs exactly
    # once (contiguous by construction, validated like the renderer uses it).
    covered = sorted(i for f, c in subs for i in range(f, f + c))
    assert covered == list(range(len(segs))), "subsectors must tile all segs"
    assert len(segs) == tree.seg_count()
    assert len(subs) == tree.subsector_count()
    assert len(nodes) == tree.node_count()

    # Node children: subsector refs carry the leaf flag, node refs do not.
    for n in nodes:
        for ch in (n[12], n[13]):
            if ch & mt1.LEAF_FLAG:
                assert (ch & ~mt1.LEAF_FLAG) < len(subs)
            else:
                assert ch < len(nodes)

    # Rebuild from lumps == freshly built tree (same structure, segs, order).
    rebuilt = type(tree).from_lumps(
        sm, lumps[L_SEGS], lumps[L_SUBSECTORS], lumps[L_NODES],
        lumps["VERTEX_EXTRA"])
    assert rebuilt.seg_count() == tree.seg_count()
    assert rebuilt.subsector_count() == tree.subsector_count()
    assert rebuilt.node_count() == tree.node_count()
    assert rebuilt.sub_segs() == tree.sub_segs()
    for a, b in zip(rebuilt.segs, tree.segs):
        assert (round(a.x1, 4), round(a.y1, 4), round(a.x2, 4),
                round(a.y2, 4), a.linedef, a.side, a.front_sec) == (
            round(b.x1, 4), round(b.y1, 4), round(b.x2, 4),
            round(b.y2, 4), b.linedef, b.side, b.front_sec)

    print("BSP lumps: refs in range, subsector tiling, node flags, "
          "rebuild==build OK")


def test_bsp_scene_consistency():
    """Task 2.3: a map with BSP slices and the same map without them build
    the identical tree; the scene reuses stored slices instead of rebuilding."""
    from engine.bsp import BspTree, build_bsp
    from shared.mt1 import L_NODES, L_SEGS, L_SUBSECTORS

    sm = _two_room_map()
    bsp = build_bsp(sm)
    lumps = bsp.to_lumps(sm.verts)
    ext_verts = lumps["VERTEX_EXTRA"]

    # Save a full .mt1 carrying geometry (extended vertex table) + BSP lumps.
    sm_ext = SectorMap(
        [Vertex(v[0], v[1]) for v in ext_verts],
        list(sm.linedefs), list(sm.sidedefs), list(sm.sectors),
        list(sm.things),
    )
    with_slices = sm_ext.to_mt1({
        mt1.L_SEGS: lumps[L_SEGS],
        mt1.L_SUBSECTORS: lumps[L_SUBSECTORS],
        mt1.L_NODES: lumps[L_NODES],
    })
    geometry_only = sm_ext.to_mt1()  # same geometry, no BSP lumps

    sm_w = SectorMap.from_bytes(with_slices)
    sm_o = SectorMap.from_bytes(geometry_only)
    assert sm_w._bsp is not None, "BSP slices must ride along the file"
    assert sm_o._bsp is None

    # Stored tree == fresh build of the loaded-with-slices map.
    stored = BspTree.from_lumps(sm_w,
                                sm_w._bsp["lumps"][L_SEGS],
                                sm_w._bsp["lumps"][L_SUBSECTORS],
                                sm_w._bsp["lumps"][L_NODES],
                                sm_w._bsp["verts"])
    fresh_w = build_bsp(sm_w)
    assert stored.sub_segs() == fresh_w.sub_segs()
    assert stored.seg_count() == fresh_w.seg_count()

    # With-slices and without-slices loads produce the same tree (the check
    # the task demands).
    fresh_o = build_bsp(sm_o)
    assert fresh_o.seg_count() == fresh_w.seg_count()
    assert fresh_o.sub_segs() == fresh_w.sub_segs()
    assert fresh_o.node_count() == fresh_w.node_count()

    # The scene caches the tree and uses the stored slices.
    from engine.sector_render import SectorScene
    scene = SectorScene(sm_w)
    assert scene.bsp is None
    tree = scene.ensure_bsp()
    assert scene.bsp is tree, "tree must be cached after first ensure"
    assert tree.subsector_count() == fresh_w.subsector_count()
    assert tree.sub_segs() == fresh_w.sub_segs()

    print("BSP scene consistency: slices vs rebuild identical, "
          "scene cache reuses slices OK")


def test_bsp_reject_los():
    """Task 2.4: REJECT early-out.  Pairs fully separated by a one-sided wall
    get the bit (has_los False); pairs with an overlapping/connecting area do
    not (LOS through a two-sided portal stays allowed)."""
    sm = SectorMap()
    # Room C (0..100 x 0..400), solid one-sided wall at x=180 (0..400),
    # room D (260..360 x 0..400).
    for x, y in ((0, 0), (100, 0), (100, 400), (0, 400),
                 (180, 0), (180, 400),
                 (260, 0), (360, 0), (360, 400), (260, 400)):
        sm.verts.append(Vertex(float(x), float(y)))
    sm.sectors.append(Sector(0, 128))   # C
    sm.sectors.append(Sector(0, 128))   # D
    sm.sidedefs.append(SideDef(mid="wall", sector=0))   # C border
    sm.sidedefs.append(SideDef(mid="wall", sector=1))   # D border
    sm.sidedefs.append(SideDef(mid="wall", sector=-1))  # the wall (one-sided)
    sm.linedefs = [
        LineDef(0, 1, front=0), LineDef(1, 2, front=0),
        LineDef(2, 3, front=0), LineDef(3, 0, front=0),
        LineDef(4, 5, front=2),               # separating wall (back=-1)
        LineDef(6, 7, front=1), LineDef(7, 8, front=1),
        LineDef(8, 9, front=1), LineDef(9, 6, front=1),
    ]
    sm.things.append(Thing(50, 200, "player1"))
    sm.invalidate()

    data = sm.build_reject()
    n = len(sm.sectors)
    assert mt1.reject_test(data, n, 0, 1), "separated rooms must be rejected"
    assert mt1.reject_test(data, n, 1, 0)

    los = sm.has_los_px
    assert los(50, 200, 310, 200) is False   # C -> D, crosses the wall
    assert los(310, 200, 50, 200) is False   # and back
    assert los(50, 200, 80, 300) is True     # inside the same sector C
    assert los(310, 200, 340, 100) is True   # same sector D

    # Two rooms sharing the wall x=400 with a two-sided portal gap in the
    # middle (y 150..250): no reject bit (bboxes touch), LOS through the
    # portal stays possible, LOS through the solid parts stays blocked.
    sm2 = SectorMap()
    for x, y in ((0, 0), (400, 0), (400, 400), (0, 400),
                 (400, 150), (400, 250),
                 (800, 0), (800, 400)):
        sm2.verts.append(Vertex(float(x), float(y)))
    sm2.sectors.append(Sector(0, 128))   # A
    sm2.sectors.append(Sector(0, 128))   # B
    sm2.sidedefs.append(SideDef(mid="wall", sector=0))
    sm2.sidedefs.append(SideDef(mid="wall", sector=1))
    sm2.sidedefs.append(SideDef(mid="portal", sector=1))  # portal back
    sm2.linedefs = [
        # A: bottom, solid low, solid high, top, left — all one-sided.
        LineDef(0, 1, front=0), LineDef(1, 4, front=0),
        LineDef(5, 2, front=0), LineDef(2, 3, front=0),
        LineDef(3, 0, front=0),
        LineDef(4, 5, front=0, back=2),        # portal (two-sided)
        # B: bottom, right, top, plus its own solid faces on x=400.
        LineDef(1, 6, front=1), LineDef(6, 7, front=1),
        LineDef(7, 2, front=1), LineDef(2, 5, front=1),
        LineDef(4, 1, front=1),
    ]
    sm2.invalidate()
    data2 = sm2.build_reject()
    n2 = len(sm2.sectors)
    assert not mt1.reject_test(data2, n2, 0, 1), \
        "rooms joined by a two-sided portal must not be rejected"
    assert sm2.has_los_px(200, 200, 600, 200) is True, \
        "sight through the two-sided portal must be preserved"
    assert sm2.has_los_px(200, 100, 600, 100) is False, \
        "sight through the solid part of the shared wall must be blocked"

    print("REJECT early-out: wall-separated False, portal stays open OK")


def test_bsp_reject_codec():
    # Bit packing: bit a*n+b, MSB first, row-major.
    flags = [(0, 1), (1, 0), (2, 2)]
    raw = mt1.encode_reject(flags)
    assert len(raw) == (3 * 3 + 7) // 8
    assert mt1.reject_test(raw, 3, 0, 1)
    assert mt1.reject_test(raw, 3, 1, 0)
    assert mt1.reject_test(raw, 3, 2, 2)
    assert not mt1.reject_test(raw, 3, 0, 2)
    assert not mt1.reject_test(raw, 3, 1, 2)
    assert mt1.reject_size(2048) == (2048 * 2048 + 7) // 8
    print("REJECT bit matrix codec OK")


def test_container():
    data = _slices()
    _check_roundtrip(data)
    raw = pack(data)
    _check_header(raw)
    _check_corrupt(raw)
    _check_duplicate_and_name()
    assert L_VERTEXES in unpack(raw)
    assert L_NAMES in unpack(raw)
    assert L_SECTORS in unpack(raw)
    print("MT1 CONTAINER TESTS PASSED")


if __name__ == "__main__":
    test_container()
    test_container_validation()
    test_geometry_lumps()
    test_geometry_bad_names_index()
    test_geometry_range_guard()
    test_bsp_builder()
    test_bsp_lumps_roundtrip()
    test_bsp_scene_consistency()
    test_bsp_reject_los()
    test_bsp_reject_codec()
    test_solid_flag_roundtrip()
    with tempfile.TemporaryDirectory() as tmp:
        test_model_import_save(tmp)