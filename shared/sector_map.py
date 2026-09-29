from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from pathlib import Path

from shared import mt1
from shared.map_data import TILE, GameMap

SECTOR_FORMAT = "sector"
SECTOR_FORMAT_VERSION = 1

MAP_FORMAT_TILE = 0
MAP_FORMAT_SECTOR = 1

# Element limits guard against maliciously huge maps (spec: sector-format).
MAX_VERTICES = 16384
MAX_LINEDEFS = 16384
MAX_SIDEDEFS = 32768
MAX_SECTORS = 2048
MAX_THINGS = 1024
MAX_SECTOR_BYTES = 1_048_576

# Linedef flag: classic Doom "blocks players" bit (informational; one-sided
# linedefs always block regardless of flags).
LINEDEF_BLOCKING = 0x01

# Linedef flag: "solid wall" — a two-sided linedef that renders as an opaque
# wall and blocks movement, sight, hitscans and projectiles even when the
# neighbouring sectors have equal heights. Bit 0 stays reserved for
# LINEDEF_BLOCKING, so this one is bit 1.
LINE_FLAG_SOLID = 0x02

# Maximum floor-height difference (world units, both directions) that a
# two-sided linedef lets an actor cross. Beyond it the linedef blocks like a
# wall. Symmetric: passability is one boolean per linedef.
STEP_HEIGHT = 24

# Bounds used when an editor clamps a sector's floor/ceiling pair.
MIN_SECTOR_HEIGHT = -512
MAX_SECTOR_HEIGHT = 1024


@dataclass(slots=True)
class Vertex:
    x: float
    y: float


@dataclass(slots=True)
class LineDef:
    v1: int
    v2: int
    flags: int = 0
    special: int = 0
    tag: int = 0
    front: int = -1
    back: int = -1

    @property
    def two_sided(self) -> bool:
        return self.back >= 0

    @property
    def solid(self) -> bool:
        """True when this two-sided linedef is an opaque wall."""
        return bool(self.flags & LINE_FLAG_SOLID)

    @solid.setter
    def solid(self, value: bool) -> None:
        if value:
            self.flags |= LINE_FLAG_SOLID
        else:
            self.flags &= ~LINE_FLAG_SOLID


@dataclass(slots=True)
class SideDef:
    upper: str = "-"
    mid: str = "wall"
    lower: str = "-"
    x_off: int = 0
    y_off: int = 0
    sector: int = 0


@dataclass(slots=True)
class Sector:
    floor_h: int = 0
    ceil_h: int = 128
    floor_tex: str = "floor"
    ceil_tex: str = "ceiling"
    light: int = 160
    special: int = 0
    tag: int = 0


@dataclass(slots=True)
class Thing:
    x: float
    y: float
    type: str = "player1"
    angle: int = 0
    z: float = 0.0


def _dist2_point_seg(px: float, py: float, x1: float, y1: float,
                     x2: float, y2: float) -> float:
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0.0 and dy == 0.0:
        return (px - x1) ** 2 + (py - y1) ** 2
    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0
    qx = x1 + t * dx
    qy = y1 + t * dy
    return (px - qx) ** 2 + (py - qy) ** 2


def _orient(ax: float, ay: float, bx: float, by: float,
            cx: float, cy: float) -> int:
    v = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    if abs(v) < 1e-9:
        return 0
    return 1 if v > 0 else -1


def _segments_cross(ax: float, ay: float, bx: float, by: float,
                    cx: float, cy: float, dx: float, dy: float) -> bool:
    o1 = _orient(ax, ay, bx, by, cx, cy)
    o2 = _orient(ax, ay, bx, by, dx, dy)
    o3 = _orient(cx, cy, dx, dy, ax, ay)
    o4 = _orient(cx, cy, dx, dy, bx, by)
    return o1 != o2 and o3 != o4


class SectorMap:
    """DOOM-style sector map: vertices, linedefs, sidedefs, sectors, things.

    Provides the same interface the server already uses on ``GameMap``
    (``is_wall_px`` / ``can_move_px`` / ``has_los_px`` / ``spawn_px``)
    plus sector-specific geometry helpers.
    """

    def __init__(
        self,
        verts: list[Vertex] | None = None,
        linedefs: list[LineDef] | None = None,
        sidedefs: list[SideDef] | None = None,
        sectors: list[Sector] | None = None,
        things: list[Thing] | None = None,
    ):
        self.verts: list[Vertex] = verts if verts is not None else []
        self.linedefs: list[LineDef] = linedefs if linedefs is not None else []
        self.sidedefs: list[SideDef] = sidedefs if sidedefs is not None else []
        self.sectors: list[Sector] = sectors if sectors is not None else []
        self.things: list[Thing] = things if things is not None else []
        self._polys = None       # per-sector loops cache
        self._index = None       # spatial grid of blocking lines cache
        self._bsp = None         # BSP slices carried in an .mt1 file (if any)
        self._reject = None      # (bytes, n) REJECT table (built lazily)
        self._blocking = None    # per-linedef passability cache (built lazily)

    # --- serialization -----------------------------------------------------
    def to_json(self) -> dict:
        return {
            "format": SECTOR_FORMAT,
            "version": SECTOR_FORMAT_VERSION,
            "vertices": [[v.x, v.y] for v in self.verts],
            "linedefs": [
                [ld.v1, ld.v2, ld.flags, ld.special, ld.tag, ld.front, ld.back]
                for ld in self.linedefs
            ],
            "sidedefs": [
                [sd.upper, sd.mid, sd.lower, sd.x_off, sd.y_off, sd.sector]
                for sd in self.sidedefs
            ],
            "sectors": [
                [s.floor_h, s.ceil_h, s.floor_tex, s.ceil_tex, s.light,
                 s.special, s.tag]
                for s in self.sectors
            ],
            "things": [
                [t.x, t.y, t.type, t.angle, t.z] for t in self.things
            ],
        }

    @staticmethod
    def from_json(data: dict) -> SectorMap:
        if not isinstance(data, dict):
            raise ValueError("sector map: not a JSON object")
        fmt = data.get("format")
        if fmt != SECTOR_FORMAT:
            raise ValueError(f"unknown map format: {fmt!r}")
        ver = data.get("version")
        if ver != SECTOR_FORMAT_VERSION:
            raise ValueError(f"unsupported sector format version: {ver!r}")
        try:
            verts = [Vertex(float(v[0]), float(v[1])) for v in data["vertices"]]
            linedefs = [
                LineDef(int(ld[0]), int(ld[1]), int(ld[2]), int(ld[3]),
                        int(ld[4]), int(ld[5]), int(ld[6]))
                for ld in data["linedefs"]
            ]
            sidedefs = [
                SideDef(str(sd[0]), str(sd[1]), str(sd[2]), int(sd[3]),
                        int(sd[4]), int(sd[5]))
                for sd in data["sidedefs"]
            ]
            sectors = [
                Sector(int(s[0]), int(s[1]), str(s[2]), str(s[3]), int(s[4]),
                       int(s[5]), int(s[6]))
                for s in data["sectors"]
            ]
            things = [
                Thing(float(t[0]), float(t[1]), str(t[2]), int(t[3]),
                      float(t[4]))
                for t in data["things"]
            ]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ValueError(f"sector map: malformed payload: {exc}") from exc
        return SectorMap(verts, linedefs, sidedefs, sectors, things)

    def to_mt1(self, extra_lumps: dict[str, bytes] | None = None) -> bytes:
        """Serialize to an ``.mt1`` container (geometry + optional BSP lumps)."""
        lumps = mt1.encode_geometry(
            [(v.x, v.y) for v in self.verts],
            [(ld.v1, ld.v2, ld.flags, ld.special, ld.tag, ld.front, ld.back)
             for ld in self.linedefs],
            [(sd.upper, sd.mid, sd.lower, sd.x_off, sd.y_off, sd.sector)
             for sd in self.sidedefs],
            [(s.floor_h, s.ceil_h, s.floor_tex, s.ceil_tex, s.light,
              s.special, s.tag) for s in self.sectors],
            [(t.x, t.y, t.type, t.angle, t.z) for t in self.things],
        )
        if extra_lumps:
            lumps.update(extra_lumps)
        return mt1.pack(lumps)

    @staticmethod
    def from_mt1(data: bytes,
                  extra_lumps: dict[str, bytes] | None = None) -> SectorMap:
        """Build a model from ``.mt1`` bytes (geometry; BSP lumps ignored here)."""
        if len(data) > MAX_SECTOR_BYTES:
            raise ValueError("sector map: payload too large")
        lumps = mt1.unpack(data)
        if extra_lumps:
            lumps.update(extra_lumps)
        verts, linedefs, sidedefs, sectors, things = mt1.decode_geometry(lumps)
        sm = SectorMap(
            [Vertex(v[0], v[1]) for v in verts],
            [LineDef(*ld) for ld in linedefs],
            [SideDef(upper=sd[0], mid=sd[1], lower=sd[2], x_off=sd[3],
                     y_off=sd[4], sector=sd[5]) for sd in sidedefs],
            [Sector(floor_h=s[0], ceil_h=s[1], floor_tex=s[2], ceil_tex=s[3],
                    light=s[4], special=s[5], tag=s[6]) for s in sectors],
            [Thing(t[0], t[1], t[2], t[3], t[4]) for t in things],
        )
        # BSP slices (if the file was saved with them) ride along so the
        # scene can reuse them instead of rebuilding (task 2.3).
        if (mt1.L_SEGS in lumps and mt1.L_SUBSECTORS in lumps
                and mt1.L_NODES in lumps):
            sm._bsp = {
                "lumps": lumps,
                "verts": verts,  # extended vertex table incl. split points
            }
        return sm

    def to_bytes(self) -> bytes:
        return self.to_mt1()

    @staticmethod
    def from_bytes(data: bytes) -> SectorMap:
        return SectorMap.from_mt1(data)

    @staticmethod
    def validate_bytes(data: bytes) -> list[str]:
        """Container-level validation of ``.mt1`` bytes.

        Returns a detailed list of errors (catalog bounds, checksum, version,
        NAMES references, model limits/references, spawn presence,
        ``floor_h <= ceil_h``). Empty list = fully valid map.
        """
        if len(data) > MAX_SECTOR_BYTES:
            return [f"sector map: payload too large "
                    f"({len(data)} > {MAX_SECTOR_BYTES})"]
        try:
            lumps = mt1.unpack(data)
        except mt1.Mt1Error as exc:
            return [f"container: {exc}"]
        try:
            verts, linedefs, sidedefs, sectors, things = mt1.decode_geometry(
                lumps)
        except mt1.Mt1Error as exc:
            return [f"geometry: {exc}"]
        sm = SectorMap(
            [Vertex(v[0], v[1]) for v in verts],
            [LineDef(*ld) for ld in linedefs],
            [SideDef(upper=sd[0], mid=sd[1], lower=sd[2], x_off=sd[3],
                     y_off=sd[4], sector=sd[5]) for sd in sidedefs],
            [Sector(floor_h=s[0], ceil_h=s[1], floor_tex=s[2], ceil_tex=s[3],
                    light=s[4], special=s[5], tag=s[6]) for s in sectors],
            [Thing(t[0], t[1], t[2], t[3], t[4]) for t in things],
        )
        return sm.validate()

    @staticmethod
    def validate_network(data: bytes) -> SectorMap | None:
        """Build a SectorMap from untrusted client bytes, or None."""
        if SectorMap.validate_bytes(data):
            return None
        return SectorMap.from_mt1(data)

    @staticmethod
    def load(path: str | Path) -> SectorMap:
        """Load a sector map. Reads ``.mt1`` containers and, as backwards
        compatible import, old ``"format": "sector"`` JSON maps."""
        with open(path, "rb") as f:
            data = f.read()
        if data.startswith(b"{") or data.startswith(b"["):
            try:
                return SectorMap.from_json(json.loads(data.decode("utf-8")))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ValueError(f"sector map: invalid JSON: {exc}") from exc
        return SectorMap.from_mt1(data)

    def save(self, path: str | Path) -> None:
        with open(path, "wb") as f:
            f.write(self.to_mt1())

    # --- validation --------------------------------------------------------
    def validate(self) -> list[str]:
        errs: list[str] = []
        if len(self.verts) > MAX_VERTICES:
            errs.append(f"too many vertices: {len(self.verts)}")
        if len(self.linedefs) > MAX_LINEDEFS:
            errs.append(f"too many linedefs: {len(self.linedefs)}")
        if len(self.sidedefs) > MAX_SIDEDEFS:
            errs.append(f"too many sidedefs: {len(self.sidedefs)}")
        if len(self.sectors) > MAX_SECTORS:
            errs.append(f"too many sectors: {len(self.sectors)}")
        if len(self.things) > MAX_THINGS:
            errs.append(f"too many things: {len(self.things)}")

        n_verts = len(self.verts)
        n_sides = len(self.sidedefs)
        n_secs = len(self.sectors)

        if n_secs < 1:
            errs.append("map has no sectors")

        pair_count: dict[tuple[int, int], int] = {}
        for i, ld in enumerate(self.linedefs):
            if not (0 <= ld.v1 < n_verts) or not (0 <= ld.v2 < n_verts):
                errs.append(f"linedef {i}: vertex index out of range")
                continue
            if ld.v1 == ld.v2:
                errs.append(f"linedef {i}: vertices are identical")
            if ld.front < 0 or ld.front >= n_sides:
                errs.append(f"linedef {i}: missing front sidedef")
            if ld.back < -1 or ld.back >= n_sides:
                errs.append(f"linedef {i}: back sidedef index out of range")
            if (ld.flags & LINE_FLAG_SOLID) and ld.back < 0:
                errs.append(
                    f"linedef {i}: solid flag on a one-sided linedef"
                )
            key = (ld.v1, ld.v2) if ld.v1 <= ld.v2 else (ld.v2, ld.v1)
            pair_count[key] = pair_count.get(key, 0) + 1

        for i, sd in enumerate(self.sidedefs):
            if not (0 <= sd.sector < n_secs):
                errs.append(f"sidedef {i}: sector {sd.sector} out of range")

        for i, s in enumerate(self.sectors):
            if s.floor_h > s.ceil_h:
                errs.append(
                    f"sector {i}: floor {s.floor_h} above ceiling {s.ceil_h}"
                )

        # Duplicate vertex pairs are allowed only for two-sided borders.
        dup: dict[tuple[int, int], list[int]] = {}
        for i, ld in enumerate(self.linedefs):
            if ld.v1 == ld.v2 or not (0 <= ld.v1 < n_verts and 0 <= ld.v2 < n_verts):
                continue
            key = (ld.v1, ld.v2) if ld.v1 <= ld.v2 else (ld.v2, ld.v1)
            dup.setdefault(key, []).append(i)
        for key, ids in dup.items():
            if len(ids) < 2:
                continue
            if not all(self.linedefs[i].two_sided for i in ids):
                errs.append(
                    f"duplicate one-sided linedefs at vertices {key}"
                )

        # Front sidedefs must reference an existing sector (already covered by
        # sidedef sector bounds, but front side itself must exist).
        for i, ld in enumerate(self.linedefs):
            if 0 <= ld.front < n_sides and not (
                0 <= self.sidedefs[ld.front].sector < n_secs
            ):
                errs.append(f"linedef {i}: front side has no valid sector")

        if not any(t.type == "player1" for t in self.things):
            errs.append("map has no player1 spawn")
        for i, t in enumerate(self.things):
            if not (math.isfinite(t.x) and math.isfinite(t.y)):
                errs.append(f"thing {i}: non-finite coordinates")

        return errs

    # --- geometry services -------------------------------------------------
    def invalidate(self) -> None:
        """Drop polygon/index caches after geometry edits."""
        self._polys = None
        self._index = None
        self._bsp = None   # stored BSP slices are stale once geometry changed
        self._blocking = None  # passability depends on flags and floor heights
        self._reject = None    # REJECT is built from geometry + blocking set

    def _ensure_blocking(self) -> list[bool]:
        """Precomputed per-linedef passability for the hot collision paths.

        A linedef blocks when it is one-sided, when it carries the ``solid``
        flag, or when the neighbouring sectors' floors differ by more than
        ``STEP_HEIGHT`` (see ``line_blocks``). The hot loops in ``can_move_px``
        and ``has_los_px`` then only need a list index instead of re-deriving
        the rule per candidate per query.
        """
        if self._blocking is not None:
            return self._blocking
        self._blocking = [self.line_blocks(i)
                          for i in range(len(self.linedefs))]
        return self._blocking

    def line_blocks(self, li: int) -> bool:
        """True when linedef ``li`` blocks movement, sight, hitscan, shots.

        One-sided linedefs always block. A two-sided linedef with the ``solid``
        flag always blocks. Otherwise it is a portal that an actor may cross
        only when the neighbouring floor heights differ by no more than
        ``STEP_HEIGHT`` — symmetric, so the answer never depends on direction.
        """
        ld = self.linedefs[li]
        if ld.back < 0:
            return True
        if ld.flags & LINE_FLAG_SOLID:
            return True
        n_sides = len(self.sidedefs)
        if not (0 <= ld.front < n_sides and 0 <= ld.back < n_sides):
            return True
        n_secs = len(self.sectors)
        fs = self.sidedefs[ld.front].sector
        bs = self.sidedefs[ld.back].sector
        if not (0 <= fs < n_secs and 0 <= bs < n_secs):
            return True
        return abs(self.sectors[fs].floor_h
                   - self.sectors[bs].floor_h) > STEP_HEIGHT

    def _ensure_polys(self):
        if self._polys is not None:
            return self._polys
        n_secs = len(self.sectors)
        n_sides = len(self.sidedefs)
        # Directed boundary segments per sector: start vertex -> end vertices.
        outs: list[dict[int, list[int]]] = [dict() for _ in range(n_secs)]
        for ld in self.linedefs:
            if 0 <= ld.front < n_sides:
                s = self.sidedefs[ld.front].sector
                if 0 <= s < n_secs and ld.v1 != ld.v2:
                    outs[s].setdefault(ld.v1, []).append(ld.v2)
            if 0 <= ld.back < n_sides:
                s = self.sidedefs[ld.back].sector
                if 0 <= s < n_secs and ld.v1 != ld.v2:
                    outs[s].setdefault(ld.v2, []).append(ld.v1)
        verts = self.verts
        polys: list[list[tuple[list[tuple[float, float]], tuple[float, float, float, float], float]]] = [
            [] for _ in range(n_secs)
        ]
        for s in range(n_secs):
            m = outs[s]
            while True:
                start = next((k for k, v in m.items() if v), None)
                if start is None:
                    break
                path = [start]
                cur = start
                closed = False
                while True:
                    nxt_list = m.get(cur)
                    if not nxt_list:
                        break
                    nxt = nxt_list.pop()
                    if nxt == start:
                        closed = True
                        break
                    path.append(nxt)
                    cur = nxt
                if not closed or len(path) < 3:
                    continue  # open contour: drop (spec: validation catches it)
                points = [(verts[i].x, verts[i].y) for i in path]
                xs = [p[0] for p in points]
                ys = [p[1] for p in points]
                bbox = (min(xs), min(ys), max(xs), max(ys))
                area2 = 0.0
                np_ = len(points)
                for i in range(np_):
                    x1, y1 = points[i]
                    x2, y2 = points[(i + 1) % np_]
                    area2 += x1 * y2 - x2 * y1
                polys[s].append((points, bbox, abs(area2) * 0.5))
        self._polys = polys
        return polys

    def has_polygon(self, sid: int) -> bool:
        """True when sector ``sid`` has at least one closed contour loop.

        A sector that lost a wall keeps an open directed path, which
        ``_ensure_polys`` discards — so it has no polygon and
        ``find_sector_at`` reports void inside it.  The editor asks this
        before dropping a shared wall: if the neighbour would stop being a
        polygon without it, the wall stays.
        """
        return bool(self._ensure_polys()[sid]) if 0 <= sid < len(self.sectors) \
            else False

    @staticmethod
    def _point_in_loop(px: float, py: float,
                       loop: list[tuple[float, float]]) -> bool:
        inside = False
        n = len(loop)
        j = n - 1
        for i in range(n):
            xi, yi = loop[i]
            xj, yj = loop[j]
            if (yi > py) != (yj > py):
                xint = (xj - xi) * (py - yi) / (yj - yi) + xi
                if px < xint:
                    inside = not inside
            j = i
        return inside

    def find_sector_at(self, x: float, y: float) -> int:
        """Innermost sector containing the point, or -1 (void = solid)."""
        polys = self._ensure_polys()
        best = -1
        best_area = math.inf
        for si, loops in enumerate(polys):
            inside_count = 0
            inner_area = math.inf
            for points, bbox, area in loops:
                if x < bbox[0] or x > bbox[2] or y < bbox[1] or y > bbox[3]:
                    continue
                if self._point_in_loop(x, y, points):
                    inside_count += 1
                    if area < inner_area:
                        inner_area = area
            if inside_count & 1 and inner_area < best_area:
                best = si
                best_area = inner_area
        return best

    def get_spawn_points(self) -> list[tuple[float, float]]:
        return [(t.x, t.y) for t in self.things if t.type == "player1"]

    def get_floor_at(self, x: float, y: float) -> int:
        si = self.find_sector_at(x, y)
        return self.sectors[si].floor_h if si >= 0 else 0

    def get_ceil_at(self, x: float, y: float) -> int:
        si = self.find_sector_at(x, y)
        return self.sectors[si].ceil_h if si >= 0 else 128

    # --- physics (shared GameMap interface) --------------------------------
    def _ensure_index(self):
        if self._index is not None:
            return self._index
        grid: dict[tuple[int, int], list[int]] = {}
        verts = self.verts
        blocking = self._ensure_blocking()
        for i, ld in enumerate(self.linedefs):
            if not blocking[i]:
                continue
            if not (0 <= ld.v1 < len(verts) and 0 <= ld.v2 < len(verts)):
                continue
            x1, y1 = verts[ld.v1].x, verts[ld.v1].y
            x2, y2 = verts[ld.v2].x, verts[ld.v2].y
            cx0 = int(min(x1, x2)) // TILE
            cx1 = int(max(x1, x2)) // TILE
            cy0 = int(min(y1, y2)) // TILE
            cy1 = int(max(y1, y2)) // TILE
            for cy in range(cy0, cy1 + 1):
                for cx in range(cx0, cx1 + 1):
                    grid.setdefault((cx, cy), []).append(i)
        self._index = grid
        return grid

    def _blocking_candidates(self, x: float, y: float, r: float) -> list[int]:
        grid = self._ensure_index()
        cx0 = int(x - r) // TILE
        cx1 = int(x + r) // TILE
        cy0 = int(y - r) // TILE
        cy1 = int(y + r) // TILE
        checked: list[int] = []  # per-query stamp (D3): dedupe across cells
        seen = set()
        for cy in range(cy0, cy1 + 1):
            for cx in range(cx0, cx1 + 1):
                cell = grid.get((cx, cy))
                if cell:
                    for li in cell:
                        if li not in seen:
                            seen.add(li)
                            checked.append(li)
        return checked

    def is_wall_px(self, x: float, y: float) -> bool:
        """True if the point is solid (void or outside all sectors)."""
        return self.find_sector_at(x, y) < 0

    def can_move_px(self, x: float, y: float, r: float) -> bool:
        """True if a circle of radius ``r`` at (x, y) stays in walkable area."""
        if self.find_sector_at(x, y) < 0:
            return False
        verts = self.verts
        r2 = r * r
        for li in self._blocking_candidates(x, y, r):
            ld = self.linedefs[li]
            x1, y1 = verts[ld.v1].x, verts[ld.v1].y
            x2, y2 = verts[ld.v2].x, verts[ld.v2].y
            if _dist2_point_seg(x, y, x1, y1, x2, y2) < r2:
                return False
        return True

    def build_reject(self) -> bytes:
        """Build a conservative REJECT table (task 2.4).

        Bit ``a*n+b`` is set only when every straight segment from sector a
        to sector b must cross a one-sided wall:

        * the sectors' bboxes are disjoint (overlapping boxes can always
          see each other through the shared area), and
        * a one-sided linedef whose segment fully separates the two bboxes
          exists: both bboxes lie strictly on opposite sides of the line,
          and both bboxes' projections onto the perpendicular axis fall
          inside the wall's own projection span.

        This errs toward "transparent" (never wrongly claims occlusion), so
        it is safe to use as a ``has_los_px`` early-out.
        """
        self._reject = self._build_reject_table()
        return self._reject[0]

    def _build_reject_table(self) -> tuple[bytes, int]:
        import math as _math

        from shared import mt1 as _mt1

        n = len(self.sectors)
        if n == 0 or n > 256:
            return (b"", n)  # perf guard: build is O(n^2 * walls)
        if not self.linedefs:
            return (b"", n)
        bboxes = [self._sector_bbox(i) for i in range(n)]
        blocking = self._ensure_blocking()
        walls = [li for li in range(len(self.linedefs)) if blocking[li]]
        flags: list[tuple[int, int]] = []
        verts = self.verts
        for a in range(n):
            ba = bboxes[a]
            if ba is None:
                continue
            for b in range(a + 1, n):
                bb = bboxes[b]
                if bb is None:
                    continue
                if (ba[0] <= bb[2] and bb[0] <= ba[2]
                        and ba[1] <= bb[3] and bb[1] <= ba[3]):
                    continue  # bboxes overlap/cross-> possible sight
                if self._shadowed_by_wall(a, b, ba, bb, walls, verts):
                    flags.append((a, b))
                    flags.append((b, a))
        data = _mt1.encode_reject(flags)
        return (data, n)

    def _sector_bbox(self, si: int):
        verts = self.verts
        xs: list[float] = []
        ys: list[float] = []
        for ld in self.linedefs:
            for sd in (ld.front, ld.back):
                if sd >= 0 and self.sidedefs[sd].sector == si:
                    v1 = verts[ld.v1]
                    v2 = verts[ld.v2]
                    xs.extend((v1.x, v2.x))
                    ys.extend((v1.y, v2.y))
        if not xs:
            return None
        return (min(xs), min(ys), max(xs), max(ys))

    @staticmethod
    def _corners_sides(box, v1x, v1y, dx, dy):
        """Sign of every bbox corner vs the directed line, or None if mixed."""
        signs = []
        for px, py in ((box[0], box[1]), (box[2], box[1]),
                       (box[2], box[3]), (box[0], box[3])):
            tags = dx * (py - v1y) - dy * (px - v1x)
            signs.append(1 if tags > 0.5 else (-1 if tags < -0.5 else 0))
        if 0 in signs:
            return None
        s = signs[0]
        return s if all(x == s for x in signs) else None

    def _shadowed_by_wall(self, a: int, b: int, ba, bb, walls, verts) -> bool:
        for li in walls:
            ld = self.linedefs[li]
            v1 = verts[ld.v1]
            v2 = verts[ld.v2]
            dx = v2.x - v1.x
            dy = v2.y - v1.y
            if dx == 0 and dy == 0:
                continue
            sa = self._corners_sides(ba, v1.x, v1.y, dx, dy)
            sb = self._corners_sides(bb, v1.x, v1.y, dx, dy)
            if sa is None or sb is None or sa == sb:
                continue
            # Every ray between the boxes crosses the wall's infinite line
            # (handled above).  The crossing happens inside the wall segment
            # iff both boxes' projections onto the wall direction fall inside
            # the segment's own projection span.
            f1 = v1.x * dx + v1.y * dy
            f2 = v2.x * dx + v2.y * dy
            lo = min(f1, f2) - 1e-3
            hi = max(f1, f2) + 1e-3
            ok = True
            for box in (ba, bb):
                for px, py in ((box[0], box[1]), (box[2], box[1]),
                               (box[2], box[3]), (box[0], box[3])):
                    if not (lo <= px * dx + py * dy <= hi):
                        ok = False
                        break
                if not ok:
                    break
            if ok:
                return True
        return False

    def has_los_px(self, x1: float, y1: float, x2: float, y2: float) -> bool:
        """True if no one-sided linedef crosses the segment."""
        if math.hypot(x2 - x1, y2 - y1) < 1.0:
            return True
        if self._reject is not None:
            reject, n = self._reject
            if reject:
                sa = self.find_sector_at(x1, y1)
                sb = self.find_sector_at(x2, y2)
                if sa >= 0 and sb >= 0 and mt1.reject_test(reject, n, sa, sb):
                    return False
        grid = self._ensure_index()
        verts = self.verts
        cx0 = int(min(x1, x2)) // TILE
        cx1 = int(max(x1, x2)) // TILE
        cy0 = int(min(y1, y2)) // TILE
        cy1 = int(max(y1, y2)) // TILE
        checked: list[int] = []
        seen = set()
        for cy in range(cy0, cy1 + 1):
            for cx in range(cx0, cx1 + 1):
                cell = grid.get((cx, cy))
                if cell:
                    for li in cell:
                        if li not in seen:
                            seen.add(li)
                            checked.append(li)
        for li in checked:
            ld = self.linedefs[li]
            ax, ay = verts[ld.v1].x, verts[ld.v1].y
            bx, by = verts[ld.v2].x, verts[ld.v2].y
            if _segments_cross(x1, y1, x2, y2, ax, ay, bx, by):
                return False
        return True

    def spawn_px(self) -> list[tuple[float, float]]:
        """Pixel spawn positions from ``player1`` things."""
        return self.get_spawn_points()

    def open_points(self, count: int = 64) -> list[tuple[float, float]]:
        """Walkable sample points (common interface with GameMap)."""
        if not self.verts:
            return []
        xs = [v.x for v in self.verts]
        ys = [v.y for v in self.verts]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        pts: list[tuple[float, float]] = []
        if x1 > x0 and y1 > y0:
            for _ in range(count * 8):
                if len(pts) >= count:
                    break
                x = random.uniform(x0, x1)
                y = random.uniform(y0, y1)
                if self.find_sector_at(x, y) >= 0:
                    pts.append((x, y))
        if not pts:
            pts = list(self.get_spawn_points())
        return pts

    # --- tile map converter (D11) -----------------------------------------
    @staticmethod
    def from_tiles(game_map: GameMap) -> SectorMap:
        """Convert a tile map into a sector map (D11 / task 1.4).

        Connected empty regions become sectors, walls become one-sided
        merged linedefs, one ``player1`` spawns per region.
        """
        w = game_map.width
        h = game_map.height
        tiles = game_map.tiles
        seen = [[False] * w for _ in range(h)]
        regions: list[list[tuple[int, int]]] = []
        for y in range(h):
            for x in range(w):
                if tiles[y][x] != 0 or seen[y][x]:
                    continue
                seen[y][x] = True
                stack = [(x, y)]
                cells: list[tuple[int, int]] = []
                while stack:
                    cx, cy = stack.pop()
                    cells.append((cx, cy))
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = cx + dx, cy + dy
                        if (0 <= nx < w and 0 <= ny < h
                                and not seen[ny][nx]
                                and tiles[ny][nx] == 0):
                            seen[ny][nx] = True
                            stack.append((nx, ny))
                regions.append(cells)

        sm = SectorMap()
        vert_lookup: dict[tuple[int, int], int] = {}

        def vert_id(cx: int, cy: int) -> int:
            key = (cx, cy)
            idx = vert_lookup.get(key)
            if idx is None:
                idx = len(sm.verts)
                sm.verts.append(Vertex(float(cx * TILE), float(cy * TILE)))
                vert_lookup[key] = idx
            return idx

        for cells in regions:
            rid = len(sm.sectors)
            sm.sectors.append(Sector())
            side_id = len(sm.sidedefs)
            sm.sidedefs.append(SideDef(sector=rid))

            # Boundary edges against walls / border, directionless.
            horiz: dict[int, list[tuple[int, int]]] = {}
            vert_: dict[int, list[tuple[int, int]]] = {}
            for cx, cy in cells:
                if cy == 0 or tiles[cy - 1][cx] != 0:
                    horiz.setdefault(cy, []).append((cx, cx + 1))
                if cy + 1 >= h or tiles[cy + 1][cx] != 0:
                    horiz.setdefault(cy + 1, []).append((cx, cx + 1))
                if cx == 0 or tiles[cy][cx - 1] != 0:
                    vert_.setdefault(cx, []).append((cy, cy + 1))
                if cx + 1 >= w or tiles[cy][cx + 1] != 0:
                    vert_.setdefault(cx + 1, []).append((cy, cy + 1))

            # Merge collinear runs into single linedefs.
            segments: list[tuple[int, int, int, int, int, int]] = []
            for y, runs in horiz.items():
                runs.sort()
                lo, hi = runs[0]
                for rlo, rhi in runs[1:]:
                    if rlo <= hi:
                        hi = max(hi, rhi)
                    else:
                        segments.append((lo, y, hi, y, 0, 0))
                        lo, hi = rlo, rhi
                segments.append((lo, y, hi, y, 0, 0))
            for x, runs in vert_.items():
                runs.sort()
                lo, hi = runs[0]
                for rlo, rhi in runs[1:]:
                    if rlo <= hi:
                        hi = max(hi, rhi)
                    else:
                        segments.append((x, lo, x, hi, 0, 0))
                        lo, hi = rlo, rhi
                segments.append((x, lo, x, hi, 0, 0))

            # Orient each segment so the region lies on the front side
            # (cross((v2-v1), (p-v1)) < 0 in y-down world coords).
            for (x1, y1, x2, y2, _, _) in segments:
                ref_cell = None
                for cx, cy in cells:
                    if (y1 == y2 and y1 in (cy, cy + 1)
                            and min(x1, x2) <= cx < max(x1, x2)):
                        ref_cell = (cx, cy)
                        break
                    if (x1 == x2 and x1 in (cx, cx + 1)
                            and min(y1, y2) <= cy < max(y1, y2)):
                        ref_cell = (cx, cy)
                        break
                if ref_cell is None:
                    ref_cell = cells[0]
                px = (ref_cell[0] + 0.5) * TILE
                py = (ref_cell[1] + 0.5) * TILE
                ax, ay = float(x1 * TILE), float(y1 * TILE)
                bx, by = float(x2 * TILE), float(y2 * TILE)
                cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
                v1, v2 = (x1, y1), (x2, y2)
                if cross >= 0:
                    v1, v2 = v2, v1
                sm.linedefs.append(LineDef(vert_id(*v1), vert_id(*v2), front=side_id))

            # One spawn per region: cell centre closest to the centroid.
            n = len(cells)
            mx = sum(c[0] for c in cells) / n
            my = sum(c[1] for c in cells) / n
            best_cell = min(
                cells, key=lambda c: (c[0] - mx) ** 2 + (c[1] - my) ** 2
            )
            sm.things.append(
                Thing((best_cell[0] + 0.5) * TILE, (best_cell[1] + 0.5) * TILE)
            )

        sm.invalidate()
        return sm
