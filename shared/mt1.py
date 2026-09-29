"""Binary sector-map container ``.mt1`` for the DOOM-1 BSP path.

WAD-like layout (big-endian, per spec map/sector-format):

    header   magic b"MT1\\x00" + u16 format version + u32 lump count
             + u32 catalog offset + u32 content checksum
    catalog  count * { u32 offset, u32 size, 8s ASCII name }
    lumps    payloads following the catalog (offsets point past it)

This module owns the container mechanics only (task 1.1): pack/unpack with
strict, coded errors and catalog integrity checks.  The geometry and BSP
lump encoders live in the same file in later sections of this change but
the container API is fully independent (it accepts any named chunk).
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from enum import IntEnum

MAGIC = b"MT1\x00"
MT1_VERSION = 1

HEADER_STRUCT = struct.Struct("!4sHII I")
"""magic(4) version(u16) count(u32) catalog_offset(u32) checksum(u32)."""

ENTRY_STRUCT = struct.Struct("!II8s")
"""catalog entry: lump offset(u32) size(u32) name(8s)."""

HEADER_SIZE = HEADER_STRUCT.size         # 18
ENTRY_SIZE = ENTRY_STRUCT.size           # 16
NAME_SIZE = 8                            # NUL-padded ASCII in catalog

MAX_LUMPS = 0xFFFF  # u16 count if we ever switch the header; keep vs spec

# Canonical lump names (all geometry + BSP slices).
L_VERTEXES = "VERTEXES"
L_LINEDEFS = "LINEDEFS"
L_SIDEDEFS = "SIDEDEFS"
L_SECTORS = "SECTORS"
L_THINGS = "THINGS"
L_NAMES = "NAMES"
L_SEGS = "SEGS"
# 8-byte catalog names only (WAD-compatible, ``8s``); Doom shortens
# SUBSECTORS to SSECTORS for exactly this reason.
L_SUBSECTORS = "SSECTORS"
L_NODES = "NODES"
L_REJECT = "REJECT"

ALL_LUMPS = (
    L_VERTEXES, L_LINEDEFS, L_SIDEDEFS, L_SECTORS, L_THINGS, L_NAMES,
    L_SEGS, L_SUBSECTORS, L_NODES, L_REJECT,
)

# --- geometry lump record formats (big-endian, task 1.2) -------------------
VERTEX_STRUCT = struct.Struct("!ff")         # x f32, y f32
LINEDEF_STRUCT = struct.Struct("!IIHHHII")   # v1 u32, v2 u32, flags/special/
                                             # tag u16, front u32, back u32
SIDEDEF_STRUCT = struct.Struct("!IIIhhI")    # upper/mid/lower u32 NAMES idx,
                                             # x_off i16, y_off i16, sector u32
SECTOR_STRUCT = struct.Struct("!hhIIHHH")    # floor i16, ceil i16, floor/ceil
                                             # u32 NAMES idx, light/special/
                                             # tag u16
THING_STRUCT = struct.Struct("!ffIHf")       # x f32, y f32, type u32 NAMES
                                             # idx, angle u16, z f32

LINE_OFFSET = 2**32 - 1  # u32 sentinel for "no sidedef" (-1)
FLAT_OFFSET = 2**32 - 1  # u32 sentinel for missing texture name

N_SIDEDEF_TEX = 3   # upper / mid / lower

# --- BSP lump record formats (big-endian, task 2.1) ------------------------
SEG_STRUCT = struct.Struct("!IIIBf")
"""seg: v1 u32, v2 u32 (into VERTEXES), linedef u32, side u8 (0 front /
1 back), u_off f32 along the original linedef (pixels)."""

SUBSECTOR_STRUCT = struct.Struct("!HH")
"""subsector: first_seg u16, seg_count u16."""

NODE_STRUCT = struct.Struct("!4f8f II")
"""node: partition point (px,py) + dir (pdx,pdy) f32; front bbox
(minx,miny,maxx,maxy); back bbox (same order); child0 u32, child1 u32.
Child = node index, or subsector index with bit 31 set (leaf)."""

LEAF_FLAG = 0x80000000


class Mt1Code(IntEnum):
    """Strict, machine-readable failure codes for container operations."""

    TRUNCATED = 1            # byte buffer ended mid-header/catalog/lump
    BAD_MAGIC = 2            # first 4 bytes are not ``MT1\\x00``
    UNSUPPORTED_VERSION = 3  # format version not supported by this reader
    BAD_CHECKSUM = 4         # catalog range holds, payload checksum differs
    BAD_CATALOG_OFFSET = 5   # catalog offset is out of file bounds
    BAD_CATALOG_ENTRY = 6    # an entry offset/size falls out of file bounds
    BAD_LUMP_NAME = 7        # lump name is not valid 1-8 ASCII
    DUPLICATE_LUMP = 8       # two catalog entries share one name
    TOO_MANY_LUMPS = 9       # count overflows the catalog space of the file
    MALFORMED_LUMP = 10      # lump data is not a valid stream of records
    OUT_OF_RANGE = 11        # a field value exceeds the wire-format range
    NAMES_INDEX = 12         # a NAMES reference is out of the table bounds


class Mt1Error(ValueError):
    """Container error carrying a stable ``code`` plus a human message."""

    def __init__(self, code: Mt1Code, message: str):
        self.code = code
        super().__init__(message)


def _check_name(raw: bytes) -> str | None:
    if len(raw) != NAME_SIZE:
        return None
    name = raw.rstrip(b"\x00")
    if not name or len(name) > NAME_SIZE:
        return None
    try:
        return name.decode("ascii")
    except UnicodeDecodeError:
        return None


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    name: str
    offset: int
    size: int


def _pack_lumps(lumps: list[tuple[str, bytes]]) -> bytes:
    names = [n for n, _ in lumps]
    if len(names) != len(set(names)):
        raise Mt1Error(Mt1Code.DUPLICATE_LUMP, "duplicate lump names")
    catalog_offset = HEADER_SIZE
    data_offset = catalog_offset + len(lumps) * ENTRY_SIZE
    data = bytearray()
    offsets: list[int] = []
    for _, payload in lumps:
        offsets.append(data_offset + len(data))
        data += payload
    checksum = zlib.crc32(bytes(data)) & 0xFFFFFFFF

    catalog = bytearray()
    for (name, payload), off in zip(lumps, offsets):
        if len(name) > NAME_SIZE:
            raise Mt1Error(Mt1Code.BAD_LUMP_NAME, f"lump name too long: {name!r}")
        catalog += struct.pack("!II8s", off, len(payload), name.encode("ascii"))

    # Layout: header, catalog, then lump payloads (offsets point past catalog).
    l2 = list(lumps)
    header = HEADER_STRUCT.pack(MAGIC, MT1_VERSION, len(l2), catalog_offset,
                                checksum)
    return header + bytes(catalog) + bytes(data)


def pack(lumps: dict[str, bytes] | list[tuple[str, bytes]]) -> bytes:
    """Serialize named chunks into an ``.mt1`` container byte string.

    ``dict`` preserves insertion order; a list of tuples is also accepted.
    Names SHALL be 1-8 byte ASCII.
    """
    items = list(lumps.items()) if isinstance(lumps, dict) else lumps
    return _pack_lumps(items)


def unpack(data: bytes) -> dict[str, bytes]:
    """Parse a container; returns ``{name: payload}`` in catalog order.

    Raises :class:`Mt1Error` with a strict code for any structural defect.
    """
    if len(data) < HEADER_SIZE:
        raise Mt1Error(Mt1Code.TRUNCATED, "container shorter than its header")
    magic, version, count, catalog_off, stored_crc = HEADER_STRUCT.unpack(
        data[:HEADER_SIZE])
    if magic != MAGIC:
        raise Mt1Error(Mt1Code.BAD_MAGIC,
                       f"bad magic {magic!r}, expected {MAGIC!r}")
    if version != MT1_VERSION:
        raise Mt1Error(Mt1Code.UNSUPPORTED_VERSION,
                       f"unsupported container version {version}")
    if catalog_off + count * ENTRY_SIZE > len(data) or catalog_off < HEADER_SIZE:
        raise Mt1Error(
            Mt1Code.BAD_CATALOG_OFFSET,
            f"catalog at {catalog_off} for {count} entries out of bounds"
            f" (file {len(data)} bytes)")

    entries: list[CatalogEntry] = []
    seen: set[str] = set()
    raw_crc = bytearray()
    for i in range(count):
        off, size, raw_name = ENTRY_STRUCT.unpack(
            data[catalog_off + i * ENTRY_SIZE:catalog_off + (i + 1) * ENTRY_SIZE])
        name = _check_name(raw_name)
        if name is None:
            raise Mt1Error(Mt1Code.BAD_LUMP_NAME,
                           f"catalog {i}: invalid lump name {raw_name!r}")
        if name in seen:
            raise Mt1Error(Mt1Code.DUPLICATE_LUMP,
                           f"catalog {i}: duplicate lump {name!r}")
        seen.add(name)
        if off < catalog_off + count * ENTRY_SIZE or off + size > len(data):
            raise Mt1Error(
                Mt1Code.BAD_CATALOG_ENTRY,
                f"lump {name!r} at {off} size {size} out of file bounds"
                f" (file {len(data)} bytes, data starts at"
                f" {catalog_off + count * ENTRY_SIZE})")
        entries.append(CatalogEntry(name, off, size))
        raw_crc += data[off:off + size]

    actual_crc = zlib.crc32(bytes(raw_crc)) & 0xFFFFFFFF
    if actual_crc != stored_crc:
        raise Mt1Error(Mt1Code.BAD_CHECKSUM,
                       f"checksum mismatch: stored {stored_crc:#x},"
                       f" actual {actual_crc:#x}")

    return {e.name: bytes(data[e.offset:e.offset + e.size]) for e in entries}


# --- geometry codecs (task 1.2) --------------------------------------------
# All codecs work on native Python values (tuples/lists of int/float/str),
# keeping this module free of SectorMap imports (the model layer converts).


def _store_signed(value: int, lo: int, hi: int, lump: str, field: str) -> int:
    if value < lo or value > hi:
        raise Mt1Error(
            Mt1Code.OUT_OF_RANGE,
            f"{lump} {field}: value {value} out of {lo}..{hi}",
        )
    return value


def _read_u32_sidedef(value: int) -> int:
    """Reconstruct a -1 (no sidedef) sentinel from a u32 field."""
    return -1 if value == LINE_OFFSET else value


def _names_indices(names: list[str]) -> dict[str, int]:
    # Unique strings in first-seen order (table referenced by u32 indexes).
    uniq: list[str] = []
    seen: set[str] = set()
    for n in names:
        if n not in seen:
            seen.add(n)
            uniq.append(n)
    return {n: i for i, n in enumerate(uniq)}


def encode_names(strings: list[str]) -> bytes:
    """NAMES lump: u16 count, then each string as u16-len + UTF-8 bytes."""
    if len(strings) > 0xFFFF:
        raise Mt1Error(Mt1Code.OUT_OF_RANGE,
                       f"NAMES: too many strings {len(strings)}")
    body = bytearray()
    body += struct.pack("!H", len(strings))
    for s in strings:
        enc = s.encode("utf-8")
        if len(enc) > 0xFFFF:
            raise Mt1Error(Mt1Code.OUT_OF_RANGE,
                           "NAMES: string too long")
        body += struct.pack("!H", len(enc))
        body += enc
    return bytes(body)


def decode_names(data: bytes) -> list[str]:
    if len(data) < 2:
        raise Mt1Error(Mt1Code.MALFORMED_LUMP, "NAMES: truncated count")
    count = struct.unpack("!H", data[:2])[0]
    out: list[str] = []
    off = 2
    for i in range(count):
        if off + 2 > len(data):
            raise Mt1Error(Mt1Code.MALFORMED_LUMP,
                           f"NAMES: string {i} header cut off")
        n = struct.unpack("!H", data[off:off + 2])[0]
        off += 2
        if off + n > len(data):
            raise Mt1Error(Mt1Code.MALFORMED_LUMP,
                           f"NAMES: string {i} cut off")
        out.append(data[off:off + n].decode("utf-8", "replace"))
        off += n
    return out


def encode_vertices(verts: list[tuple[float, float]]) -> bytes:
    body = bytearray()
    for x, y in verts:
        body += VERTEX_STRUCT.pack(float(x), float(y))
    return bytes(body)


def decode_vertices(data: bytes) -> list[tuple[float, float]]:
    n = len(data) // VERTEX_STRUCT.size
    if len(data) % VERTEX_STRUCT.size:
        raise Mt1Error(Mt1Code.MALFORMED_LUMP,
                       "VERTEXES: size not a multiple of record size")
    return [VERTEX_STRUCT.unpack_from(data, i * VERTEX_STRUCT.size) for i in range(n)]


def encode_linedefs(lines: list[tuple[int, int, int, int, int, int, int]]) -> bytes:
    """Each line: (v1, v2, flags, special, tag, front, back); -1 -> u32 max."""
    body = bytearray()
    for v1, v2, flags, special, tag, front, back in lines:
        body += LINEDEF_STRUCT.pack(
            _store_signed(v1, 0, 2**32 - 1, "LINEDEFS", "v1"),
            _store_signed(v2, 0, 2**32 - 1, "LINEDEFS", "v2"),
            _store_signed(flags, 0, 0xFFFF, "LINEDEFS", "flags"),
            _store_signed(special, 0, 0xFFFF, "LINEDEFS", "special"),
            _store_signed(tag, 0, 0xFFFF, "LINEDEFS", "tag"),
            _store_signed(front, 0, 2**32 - 1, "LINEDEFS", "front")
            if front >= 0 else LINE_OFFSET,
            _store_signed(back, 0, 2**32 - 1, "LINEDEFS", "back")
            if back >= 0 else LINE_OFFSET,
        )
    return bytes(body)


def decode_linedefs(data: bytes) -> list[tuple[int, int, int, int, int, int, int]]:
    out: list[tuple[int, int, int, int, int, int, int]] = []
    for i in range(len(data) // LINEDEF_STRUCT.size):
        v1, v2, flags, special, tag, front, back = LINEDEF_STRUCT.unpack_from(
            data, i * LINEDEF_STRUCT.size)
        out.append((v1, v2, flags, special, tag,
                    _read_u32_sidedef(front),
                    _read_u32_sidedef(back)))
    return out


def encode_sidedefs(
    sides: list[tuple[int, int, int, int, int, int]],
) -> bytes:
    """Each side: (upper_idx, mid_idx, lower_idx, x_off, y_off, sector)."""
    body = bytearray()
    for upper, mid, lower, x_off, y_off, sector in sides:
        body += SIDEDEF_STRUCT.pack(
            _store_signed(upper, 0, 2**32 - 1, "SIDEDEFS", "upper"),
            _store_signed(mid, 0, 2**32 - 1, "SIDEDEFS", "mid"),
            _store_signed(lower, 0, 2**32 - 1, "SIDEDEFS", "lower"),
            _store_signed(x_off, -32768, 32767, "SIDEDEFS", "x_off"),
            _store_signed(y_off, -32768, 32767, "SIDEDEFS", "y_off"),
            _store_signed(sector, 0, 2**32 - 1, "SIDEDEFS", "sector"),
        )
    return bytes(body)


def decode_sidedefs(data: bytes) -> list[tuple[int, int, int, int, int, int]]:
    out: list[tuple[int, int, int, int, int, int]] = []
    for i in range(len(data) // SIDEDEF_STRUCT.size):
        upper, mid, lower, x_off, y_off, sector = SIDEDEF_STRUCT.unpack_from(
            data, i * SIDEDEF_STRUCT.size)
        out.append((upper, mid, lower, x_off, y_off, sector))
    return out


def encode_sectors(sectors: list[tuple[int, int, int, int, int, int, int]]) -> bytes:
    """Each sector: (floor_h, ceil_h, floor_tex_idx, ceil_tex_idx, light,
    special, tag)."""
    body = bytearray()
    for floor_h, ceil_h, floor_tex, ceil_tex, light, special, tag in sectors:
        body += SECTOR_STRUCT.pack(
            _store_signed(floor_h, -32768, 32767, "SECTORS", "floor_h"),
            _store_signed(ceil_h, -32768, 32767, "SECTORS", "ceil_h"),
            _store_signed(floor_tex, 0, 2**32 - 1, "SECTORS", "floor_tex"),
            _store_signed(ceil_tex, 0, 2**32 - 1, "SECTORS", "ceil_tex"),
            _store_signed(light, 0, 0xFFFF, "SECTORS", "light"),
            _store_signed(special, 0, 0xFFFF, "SECTORS", "special"),
            _store_signed(tag, 0, 0xFFFF, "SECTORS", "tag"),
        )
    return bytes(body)


def decode_sectors(data: bytes) -> list[tuple[int, int, int, int, int, int, int]]:
    out: list[tuple[int, int, int, int, int, int, int]] = []
    for i in range(len(data) // SECTOR_STRUCT.size):
        floor_h, ceil_h, floor_tex, ceil_tex, light, special, tag = \
            SECTOR_STRUCT.unpack_from(data, i * SECTOR_STRUCT.size)
        out.append((floor_h, ceil_h, floor_tex, ceil_tex, light, special, tag))
    return out


def encode_things(things: list[tuple[float, float, int, int, float]]) -> bytes:
    """Each thing: (x, y, type_idx, angle, z)."""
    body = bytearray()
    for x, y, type_idx, angle, z in things:
        body += THING_STRUCT.pack(
            float(x), float(y),
            _store_signed(type_idx, 0, 2**32 - 1, "THINGS", "type"),
            _store_signed(angle, 0, 0xFFFF, "THINGS", "angle"),
            float(z),
        )
    return bytes(body)


def decode_things(data: bytes) -> list[tuple[float, float, int, int, float]]:
    out: list[tuple[float, float, int, int, float]] = []
    for i in range(len(data) // THING_STRUCT.size):
        x, y, type_idx, angle, z = THING_STRUCT.unpack_from(
            data, i * THING_STRUCT.size)
        out.append((x, y, type_idx, angle, z))
    return out


SsSidedef = list[tuple[str, str, str, int, int, int]]
"""side: (upper, mid, lower texture names, x_off, y_off, sector)."""

SsSector = list[tuple[int, int, str, str, int, int, int]]
"""sector: (floor_h, ceil_h, floor_tex, ceil_tex, light, special, tag)."""

SsThing = list[tuple[float, float, str, int, float]]
"""thing: (x, y, type name, angle, z)."""

SsLine = list[tuple[int, int, int, int, int, int, int]]
"""linedef: (v1, v2, flags, special, tag, front, back)."""

SsVertex = list[tuple[float, float]]


def _collect_geometry_names(
    sidedefs: SsSidedef, sectors: SsSector, things: SsThing,
) -> list[str]:
    strings: list[str] = []
    for upper, mid, lower, _, _, _ in sidedefs:
        strings += [upper, mid, lower]
    for _, _, floor_tex, ceil_tex, _, _, _ in sectors:
        strings += [floor_tex, ceil_tex]
    for _, _, type_name, _, _ in things:
        strings.append(type_name)
    return list(dict.fromkeys(strings))  # unique, first-seen order


def encode_geometry(
    verts: SsVertex,
    linedefs: SsLine,
    sidedefs: SsSidedef,
    sectors: SsSector,
    things: SsThing,
) -> dict[str, bytes]:
    """Encode the source-geometry lumps; returns {VERTEXES..NAMES} bytes.

    Texture/type names are turned into a NAMES table automatically; every
    other lump references positions of the raw degree-1 tables.
    """
    names = _collect_geometry_names(sidedefs, sectors, things)
    idx = _names_indices(names)
    lumps = {
        L_VERTEXES: encode_vertices(verts),
        L_LINEDEFS: encode_linedefs(linedefs),
        L_SIDEDEFS: encode_sidedefs([
            (idx[upper], idx[mid], idx[lower], x_off, y_off, sector)
            for upper, mid, lower, x_off, y_off, sector in sidedefs]),
        L_SECTORS: encode_sectors([
            (floor_h, ceil_h, idx[floor_tex], idx[ceil_tex], light, special, tag)
            for floor_h, ceil_h, floor_tex, ceil_tex, light, special, tag
            in sectors]),
        L_THINGS: encode_things([
            (x, y, idx[type_name], angle, z)
            for x, y, type_name, angle, z in things]),
        L_NAMES: encode_names(names),
    }
    return lumps


def decode_geometry(
    lumps: dict[str, bytes],
) -> tuple[SsVertex, SsLine, SsSidedef, SsSector, SsThing]:
    """Decode source-geometry lumps -- strict inverse of ``encode_geometry``.

    Any NAMES reference outside the table raises ``Mt1Error(NAMES_INDEX)``.
    """
    names = decode_names(lumps.get(L_NAMES, b""))
    n_names = len(names)

    def resolve(idx: int, fallback: str, what: str) -> str:
        if idx >= n_names:
            raise Mt1Error(Mt1Code.NAMES_INDEX,
                           f"{what}: NAMES index {idx} out of {n_names}")
        return names[idx]

    verts = decode_vertices(lumps.get(L_VERTEXES, b""))
    linedefs = decode_linedefs(lumps.get(L_LINEDEFS, b""))
    sidedefs = [
        (resolve(u, "-", "SIDEDEFS upper"),
         resolve(m, "-", "SIDEDEFS mid"),
         resolve(lo, "-", "SIDEDEFS lower"),
         x_off, y_off, sector)
        for u, m, lo, x_off, y_off, sector
        in decode_sidedefs(lumps.get(L_SIDEDEFS, b""))
    ]

    sectors = [
        (floor_h, ceil_h,
         resolve(ft, "floor", "SECTORS floor"),
         resolve(ct, "ceiling", "SECTORS ceil"),
         light, special, tag)
        for floor_h, ceil_h, ft, ct, light, special, tag
        in decode_sectors(lumps.get(L_SECTORS, b""))
    ]

    things = [
        (x, y, resolve(ti, "player1", "THINGS type"), angle, z)
        for x, y, ti, angle, z in decode_things(lumps.get(L_THINGS, b""))
    ]

    return verts, linedefs, sidedefs, sectors, things


# --- BSP codecs (task 2.1) -------------------------------------------------

SsSeg = list[tuple[int, int, int, int, float]]
"""seg: (v1, v2, linedef, side, u_off)."""

SsSubsector = list[tuple[int, int]]
"""subsector: (first_seg, seg_count)."""

SsNode = list[tuple[float, float, float, float, float, float, float, float,
                    float, float, float, float, int, int]]
"""node: (px, py, pdx, pdy, fminx, fminy, fmaxx, fmaxy, bminx, bminy, bmaxx,
bmaxy, child0, child1). Child = node idx, or subsector idx | LEAF_FLAG."""


def encode_segs(segs: SsSeg) -> bytes:
    body = bytearray()
    for v1, v2, linedef, side, u_off in segs:
        body += SEG_STRUCT.pack(
            _store_signed(v1, 0, 2**32 - 1, "SEGS", "v1"),
            _store_signed(v2, 0, 2**32 - 1, "SEGS", "v2"),
            _store_signed(linedef, 0, 2**32 - 1, "SEGS", "linedef"),
            _store_signed(side, 0, 1, "SEGS", "side"),
            u_off,
        )
    return bytes(body)


def decode_segs(data: bytes) -> SsSeg:
    out: SsSeg = []
    for i in range(len(data) // SEG_STRUCT.size):
        v1, v2, linedef, side, u_off = SEG_STRUCT.unpack_from(
            data, i * SEG_STRUCT.size)
        out.append((v1, v2, linedef, side, u_off))
    return out


def encode_subsectors(subs: SsSubsector) -> bytes:
    body = bytearray()
    for first, count in subs:
        body += SUBSECTOR_STRUCT.pack(
            _store_signed(first, 0, 0xFFFF, "SUBSECTORS", "first_seg"),
            _store_signed(count, 0, 0xFFFF, "SUBSECTORS", "seg_count"),
        )
    return bytes(body)


def decode_subsectors(data: bytes) -> SsSubsector:
    out: SsSubsector = []
    for i in range(len(data) // SUBSECTOR_STRUCT.size):
        first, count = SUBSECTOR_STRUCT.unpack_from(
            data, i * SUBSECTOR_STRUCT.size)
        out.append((first, count))
    return out


def encode_nodes(nodes: SsNode) -> bytes:
    body = bytearray()
    for n in nodes:
        px, py, pdx, pdy, fx0, fy0, fx1, fy1, bx0, by0, bx1, by1, \
            ch0, ch1 = n
        body += NODE_STRUCT.pack(px, py, pdx, pdy, fx0, fy0, fx1, fy1,
                                 bx0, by0, bx1, by1, ch0, ch1)
    return bytes(body)


def decode_nodes(data: bytes) -> SsNode:
    out: SsNode = []
    for i in range(len(data) // NODE_STRUCT.size):
        vals = NODE_STRUCT.unpack_from(data, i * NODE_STRUCT.size)
        out.append((vals[0], vals[1], vals[2], vals[3], vals[4], vals[5],
                    vals[6], vals[7], vals[8], vals[9], vals[10], vals[11],
                    vals[12], vals[13]))
    return out


def encode_reject(flags: list[tuple[int, int]]) -> bytes:
    """Bit-packed matrix over pairs ``(a, b)``; a and b are sector indices.

    Callers must hand already-conservative single bits: ``1`` = "a cannot
    see b".  We pack bit index ``a*n + b`` (row-major, MSB first).
    """
    if not flags:
        return b""
    n = max(max(a, b) for a, b in flags) + 1
    size = (n * n + 7) // 8
    out = bytearray(size)
    for a, b in flags:
        idx = a * n + b
        out[idx // 8] |= 0x80 >> (idx % 8)
    return bytes(out)


def decode_reject(data: bytes) -> bytearray:
    return bytearray(data)


def reject_size(n_sectors: int) -> int:
    return (n_sectors * n_sectors + 7) // 8


def reject_test(reject: bytearray | bytes, n_sectors: int,
                a: int, b: int) -> bool:
    """True if the packed reject table marks (a, b) as non-visible."""
    idx = a * n_sectors + b
    byte = reject[idx // 8] if idx // 8 < len(reject) else 0
    return bool(byte & (0x80 >> (idx % 8)))