"""Sector map editor data model + entity undo/redo (tasks 4.1, 4.2).

Editing operations on a working ``SectorMap``: vertices, linedefs
(sidedefs), sectors, things — each operation leaves the map referenceable,
and entity-level snapshot-diff history provides undo/redo.  Pure model (no
pygame), so it can be exercised headlessly by ``smoke_sector_editor.py``.
"""
from __future__ import annotations

from shared.sector_map import (
    MAX_SECTOR_HEIGHT, MIN_SECTOR_HEIGHT, STEP_HEIGHT,
    LineDef, Sector, SectorMap, SideDef, Thing, Vertex,
)

ENTITY_KINDS = ("vertices", "linedefs", "sidedefs", "sectors", "things")
GRID_DEFAULT = 16


def clamp_sector_heights(floor_h, ceil_h) -> tuple[int, int]:
    """Normalize a floor/ceiling pair: integral, in range, ``floor <= ceil``.

    Single clamp for the whole editor — the ``пол ▲/▼`` buttons and the numeric
    fields both go through it, so the map never goes invalid mid-edit and a
    rejected value is visible in the panel as the applied one.
    """
    fl = int(round(float(floor_h)))
    ce = int(round(float(ceil_h)))
    fl = max(MIN_SECTOR_HEIGHT, min(MAX_SECTOR_HEIGHT, fl))
    ce = max(MIN_SECTOR_HEIGHT, min(MAX_SECTOR_HEIGHT, ce))
    if fl > ce:
        # the ceiling wins when the floor is pushed through it
        fl = ce
    return fl, ce


def snap_point(x: float, y: float, step: int | None = None,
               on: bool = True) -> tuple[float, float]:
    step = GRID_DEFAULT if step is None else step
    if not on or step <= 0:
        return float(x), float(y)
    return round(x / step) * step, round(y / step) * step


def _item_from_json(kind: str, item) -> object:
    if kind == "vertices":
        return Vertex(float(item[0]), float(item[1]))
    if kind == "linedefs":
        return LineDef(int(item[0]), int(item[1]), int(item[2]), int(item[3]),
                       int(item[4]), int(item[5]), int(item[6]))
    if kind == "sidedefs":
        return SideDef(str(item[0]), str(item[1]), str(item[2]), int(item[3]),
                       int(item[4]), int(item[5]))
    if kind == "sectors":
        return Sector(int(item[0]), int(item[1]), str(item[2]), str(item[3]),
                      int(item[4]), int(item[5]), int(item[6]))
    return Thing(float(item[0]), float(item[1]), str(item[2]), int(item[3]),
                 float(item[4]))


def _apply_item(obj, kind: str, item) -> None:
    if kind == "vertices":
        obj.x, obj.y = float(item[0]), float(item[1])
    elif kind == "linedefs":
        (obj.v1, obj.v2, obj.flags, obj.special, obj.tag,
         obj.front, obj.back) = (
            int(item[0]), int(item[1]), int(item[2]), int(item[3]),
            int(item[4]), int(item[5]), int(item[6]))
    elif kind == "sidedefs":
        (obj.upper, obj.mid, obj.lower, obj.x_off, obj.y_off,
         obj.sector) = (
            str(item[0]), str(item[1]), str(item[2]), int(item[3]),
            int(item[4]), int(item[5]))
    elif kind == "sectors":
        (obj.floor_h, obj.ceil_h, obj.floor_tex, obj.ceil_tex, obj.light,
         obj.special, obj.tag) = (
            int(item[0]), int(item[1]), str(item[2]), str(item[3]),
            int(item[4]), int(item[5]), int(item[6]))
    else:
        obj.x, obj.y, obj.type, obj.angle, obj.z = (
            float(item[0]), float(item[1]), str(item[2]), int(item[3]),
            float(item[4]))


def make_diff(before: dict, after: dict) -> dict:
    """Entity-level snapshot diff (changed entities only, task 4.2).

    Per entity kind, either ``("ix", {i: (old, new)})`` for equal-length
    edits, ``("ap", tail)`` / ``("pop", removed)`` for a single append/pop
    at the tail, or ``("full", old_list, new_list)`` for structural
    reshapes.  Unchanged kinds are omitted.
    """
    diff: dict[str, tuple] = {}
    for kind in ENTITY_KINDS:
        b = before[kind]
        a = after[kind]
        if b == a:
            continue
        if len(b) == len(a):
            changed: dict[int, tuple] = {}
            for i in range(len(b)):
                if b[i] != a[i]:
                    changed[i] = (b[i], a[i])
            diff[kind] = ("ix", changed)
        elif len(a) == len(b) + 1 and a[:-1] == b:
            diff[kind] = ("ap", a[-1])
        elif len(b) == len(a) + 1 and b[:-1] == a:
            diff[kind] = ("pop", b[-1])
        else:
            diff[kind] = ("full", b, a)
    return diff


class SectorEditor:
    """Working buffer over a ``SectorMap`` with history undo/redo + snap."""

    def __init__(self, sm: SectorMap | None = None):
        self.sm = sm if sm is not None else new_room(1600.0, 1600.0)
        self.snap_on = True
        self.snap_step = GRID_DEFAULT
        self.cur_sector = 0
        self.cur_line_mid = "wall"
        self._history: list[dict] = []
        self._redo_stack: list[dict] = []
        self._batch_depth = 0
        self._batch_before: dict | None = None
        self._pending_before: dict | None = None

    # ── snapping helpers ──────────────────────────────────────────────
    def snap(self, x: float, y: float) -> tuple[float, float]:
        return snap_point(x, y, self.snap_step, self.snap_on)

    # ── history ───────────────────────────────────────────────────────
    def _record(self) -> None:
        if self._batch_depth == 0:
            self._pending_before = self.sm.to_json()

    def _after_mutate(self) -> None:
        self.sm.invalidate()
        if self._batch_depth:
            return
        before = self._pending_before
        self._pending_before = None
        if before is not None:
            diff = make_diff(before, self.sm.to_json())
            if diff:
                self._history.append(diff)
                self._redo_stack.clear()

    def begin_batch(self) -> None:
        if self._batch_depth == 0:
            self._batch_before = self.sm.to_json()
        self._batch_depth += 1

    def end_batch(self) -> None:
        if self._batch_depth <= 0:
            return
        self._batch_depth -= 1
        if self._batch_depth == 0:
            before = self._batch_before
            self._batch_before = None
            if before is not None:
                diff = make_diff(before, self.sm.to_json())
                if diff:
                    self._history.append(diff)
                    self._redo_stack.clear()

    def validate(self) -> list[str]:
        return self.sm.validate()

    # ── lists ─────────────────────────────────────────────────────────
    @property
    def verts(self) -> list[Vertex]:
        return self.sm.verts

    @property
    def lines(self) -> list[LineDef]:
        return self.sm.linedefs

    @property
    def sides(self) -> list[SideDef]:
        return self.sm.sidedefs

    @property
    def sectors(self) -> list[Sector]:
        return self.sm.sectors

    @property
    def things(self) -> list[Thing]:
        return self.sm.things

    # ── vertex operations ─────────────────────────────────────────────
    def add_vertex(self, x: float, y: float) -> int:
        x, y = self.snap(x, y)
        self._record()
        self.verts.append(Vertex(x, y))
        self._after_mutate()
        return len(self.verts) - 1

    def move_vertex(self, idx: int, x: float, y: float) -> None:
        if not (0 <= idx < len(self.verts)):
            return
        x, y = self.snap(x, y)
        self._record()
        self.verts[idx].x = x
        self.verts[idx].y = y
        self._after_mutate()

    def delete_vertex(self, idx: int) -> None:
        """Remove the vertex and every linedef touching it; compact arrays."""
        if not (0 <= idx < len(self.verts)):
            return
        self._record()
        removed = [i for i, ld in enumerate(self.lines)
                   if ld.v1 == idx or ld.v2 == idx]
        removed_set = set(removed)
        keep_lines = [ld for i, ld in enumerate(self.lines)
                      if i not in removed_set]
        used = {ld.front for ld in keep_lines}
        used |= {ld.back for ld in keep_lines if ld.back >= 0}
        side_map: dict[int, int] = {}
        for i, sd in enumerate(self.sides):
            if i in used:
                side_map[i] = len(side_map)
        keep_sides = [self.sides[i] for i in sorted(side_map)]
        vert_map: dict[int, int] = {}
        for i, v in enumerate(self.verts):
            if i != idx:
                vert_map[i] = len(vert_map)
        for ld in keep_lines:
            ld.v1 = vert_map[ld.v1]
            ld.v2 = vert_map[ld.v2]
            ld.front = side_map[ld.front]
            if ld.back >= 0:
                ld.back = side_map[ld.back]
        self.sm.verts = [v for i, v in enumerate(self.verts) if i != idx]
        self.sm.sidedefs = keep_sides
        self.sm.linedefs = keep_lines
        self._after_mutate()

    # ── generic entity removal ────────────────────────────────────────
    def _drop(self, lines: set[int] | None = None, sides: set[int] | None = None,
              sectors: set[int] | None = None, verts: set[int] | None = None,
              prune_orphan_verts: bool = True, record: bool = True) -> None:
        """Remove entities and renumber every surviving reference to them.

        Indices are hard references (``LineDef.v1/v2`` -> verts,
        ``front/back`` -> sidedefs, ``SideDef.sector`` -> sectors), so the
        removal order is fixed: lines decide which sides are still needed,
        sides carry the sector remap, sectors the side remap, and vertices go
        last.  Unreferenced sidedefs fall out on the way — that is what makes
        ``_polygon_alive`` see the same wall list ``_ensure_polys`` will.

        ``record=False`` is for a caller that already touched the map before
        calling in: the undo snapshot has to be taken *before* those edits, or
        undo would restore a half-mutated map.
        """
        lines = lines or set()
        sides = sides or set()
        sectors = sectors or set()
        if record:
            self._record()

        keep_lines = [ld for i, ld in enumerate(self.lines) if i not in lines]
        # a side survives only if a surviving line still points at it AND it
        # was not explicitly dropped
        used = {ld.front for ld in keep_lines}
        used |= {ld.back for ld in keep_lines if ld.back >= 0}
        used -= sides
        keep_sides = [sd for i, sd in enumerate(self.sides)
                      if i in used and sd.sector not in sectors]
        side_map = {i: n for n, i in enumerate(
            i for i, sd in enumerate(self.sides)
            if i in used and sd.sector not in sectors)}

        drop_verts = set(verts or ())
        if prune_orphan_verts:
            # a vertex nothing points at any more is dead weight
            still_used = ({ld.v1 for ld in keep_lines}
                          | {ld.v2 for ld in keep_lines})
            drop_verts |= {i for i in range(len(self.verts))
                           if i not in still_used}
        keep_verts = [v for i, v in enumerate(self.verts)
                      if i not in drop_verts]
        vert_map = {i: n for n, i in enumerate(
            i for i in range(len(self.verts)) if i not in drop_verts)}
        sector_map = {i: n for n, i in enumerate(
            i for i in range(len(self.sectors)) if i not in sectors)}

        for ld in keep_lines:
            ld.v1 = vert_map[ld.v1]
            ld.v2 = vert_map[ld.v2]
            ld.front = side_map[ld.front]
            if ld.back >= 0:
                ld.back = side_map[ld.back]
        for sd in keep_sides:
            sd.sector = sector_map[sd.sector]
        self.sm.verts = keep_verts
        self.sm.sidedefs = keep_sides
        self.sm.linedefs = keep_lines
        self.sm.sectors = [s for i, s in enumerate(self.sectors)
                           if i not in sectors]
        if self.sm.sectors:
            self.cur_sector = (0 if self.cur_sector in sectors
                               else min(sector_map.get(self.cur_sector,
                                                       self.cur_sector),
                                        len(self.sm.sectors) - 1))
        else:
            self.cur_sector = 0
        self._after_mutate()

    def _polygon_alive(self, sid: int, skip: set[int]) -> bool:
        """Would sector ``sid`` still close a contour if ``skip`` went away?

        Same criterion as ``SectorMap._ensure_polys`` — a closed directed
        cycle of at least three vertices — kept separate so the editor can ask
        the question BEFORE mutating the map and without touching the shared
        polygon cache the renderer and physics rely on.
        """
        sm = self.sm
        n_sides = len(sm.sidedefs)
        outs: dict[int, list[int]] = {}
        for i, ld in enumerate(sm.linedefs):
            if i in skip or ld.v1 == ld.v2:
                continue
            if 0 <= ld.front < n_sides and sm.sidedefs[ld.front].sector == sid:
                outs.setdefault(ld.v1, []).append(ld.v2)
            if 0 <= ld.back < n_sides and sm.sidedefs[ld.back].sector == sid:
                outs.setdefault(ld.v2, []).append(ld.v1)
        work = {v: list(dst) for v, dst in outs.items()}
        while True:
            start = next((v for v, dst in work.items() if dst), None)
            if start is None:
                return False
            path = [start]
            cur = start
            closed = False
            while True:
                dst = work.get(cur)
                if not dst:
                    break
                nxt = dst.pop()
                if nxt == start:
                    closed = True
                    break
                path.append(nxt)
                cur = nxt
            if closed and len(path) >= 3:
                return True

    def _stranded_things(self) -> list[int]:
        """Things left standing outside every sector (index in ``things``)."""
        return [i for i, t in enumerate(self.things)
                if self.sm.find_sector_at(t.x, t.y) < 0]

    def delete_line(self, idx: int) -> list[str]:
        """Remove a linedef with both of its sides; report the sectors left open.

        A sector that loses one of its walls loses its closed contour, and an
        open contour is not a polygon (``_ensure_polys`` drops it) — so its
        area becomes void nobody can walk into.  That is the deliberate
        semantics: removing a wall never repairs a sector, it just says which
        ones are now holed.
        """
        if not (0 <= idx < len(self.lines)):
            return []
        notes = self._open_sector_notes({idx})
        self._drop(lines={idx}, prune_orphan_verts=True)
        return notes

    def _open_sector_notes(self, dropping: set[int]) -> list[str]:
        """Sectors left without a closed contour once ``dropping`` is gone."""
        out: list[str] = []
        for sid in range(len(self.sectors)):
            if not self._polygon_alive(sid, dropping):
                out.append(f"сектор {sid} остался без замкнутого контура "
                           f"(его область стала пустотой)")
        return out

    def delete_sector(self, idx: int) -> list[str]:
        """Dissolve a sector, its sides and the walls that bound it.

        The area merges back into whatever surrounded it, as if it had never
        been carved out.  A wall shared with a neighbour is checked first
        (``_polygon_alive``): if dropping it would leave that neighbour without
        a polygon, the wall stays as a one-sided wall of the neighbour and the
        dissolved sector's side is discarded — the neighbouring room survives
        and a pit takes the sector's place.
        """
        if not (0 <= idx < len(self.sectors)):
            return ["сектор не выделен"]
        if len(self.sectors) <= 1:
            return ["нельзя удалить последний сектор карты"]

        sm = self.sm
        mine = {i for i, sd in enumerate(sm.sidedefs) if sd.sector == idx}
        border = [i for i, ld in enumerate(sm.linedefs)
                  if ld.front in mine or ld.back in mine]
        # snapshot before anything is touched: the kept walls are rewritten
        # below, and undo has to be able to bring the two-sided wall back
        self._record()

        drop_lines: set[int] = set()
        keep_walls: list[tuple[int, int]] = []   # (line, side to discard)
        for li in border:
            ld = sm.linedefs[li]
            # the side of THIS sector, and the neighbour across the wall
            own = ld.front if ld.front in mine else ld.back
            other = ld.back if own == ld.front else ld.front
            if other in mine or other < 0:
                # nothing survives on the far side: the wall was interior to
                # this sector (or one-sided), so it goes with it
                drop_lines.add(li)
                continue
            neighbour = sm.sidedefs[other].sector
            if not self._polygon_alive(neighbour, drop_lines | {li}):
                keep_walls.append((li, own))
            else:
                drop_lines.add(li)

        # A surviving wall must be legal on its own: "solid" is a two-sided
        # flag and validate() rejects it on a one-sided linedef. Promoting the
        # neighbour's side to the front also re-aims the edge, because a
        # back side runs v2->v1 while a front side runs v1->v2 — without the
        # swap the neighbour is left with a contour that does not close.
        for li, own in keep_walls:
            ld = sm.linedefs[li]
            if own == ld.front:
                ld.v1, ld.v2 = ld.v2, ld.v1
                ld.front, ld.back = ld.back, -1
            else:
                ld.back = -1
            ld.solid = False

        self._drop(lines=drop_lines, sides=mine, sectors={idx},
                   prune_orphan_verts=True, record=False)

        notes = [f"сектор {idx} расформирован"]
        if keep_walls:
            notes.append(f"{len(keep_walls)} общих стен сохранено: без них "
                         f"соседний сектор остался бы без замкнутого контура")
        stranded = self._stranded_things()
        if stranded:
            notes.append("вещи вне секторов: " +
                         ", ".join(str(i) for i in stranded))
        return notes

    # ── linedef operations ────────────────────────────────────────────
    def _new_side(self, sector: int, mid: str | None = None) -> int:
        self.sides.append(SideDef(sector=sector, mid=mid or self.cur_line_mid))
        return len(self.sides) - 1

    def add_line(self, v1: int, v2: int, front_sector: int | None = None,
                 back_sector: int | None = None,
                 mid: str | None = None) -> int:
        """Add a linedef with a fresh front (and optionally back) sidedef."""
        if not (0 <= v1 < len(self.verts) and 0 <= v2 < len(self.verts)):
            raise ValueError("linedef: vertex index out of range")
        if v1 == v2:
            raise ValueError("linedef: vertices are identical")
        front_sector = self.cur_sector if front_sector is None else front_sector
        if not (0 <= front_sector < len(self.sectors)):
            raise ValueError("linedef: no such front sector")
        if back_sector is not None and not (0 <= back_sector < len(self.sectors)):
            raise ValueError("linedef: no such back sector")
        front = self._new_side(front_sector, mid)
        back = self._new_side(back_sector, mid) if back_sector is not None else -1
        self._record()
        self.lines.append(LineDef(v1, v2, front=front, back=back))
        self._after_mutate()
        return len(self.lines) - 1

    def flip_line(self, idx: int) -> None:
        if not (0 <= idx < len(self.lines)):
            return
        self._record()
        ld = self.lines[idx]
        ld.v1, ld.v2 = ld.v2, ld.v1
        ld.front, ld.back = ld.back, ld.front
        self._after_mutate()

    def set_two_sided(self, idx: int, two_sided: bool,
                      back_sector: int | None = None) -> None:
        if not (0 <= idx < len(self.lines)):
            return
        self._record()
        ld = self.lines[idx]
        if two_sided:
            if ld.back < 0:
                back_sector = self.cur_sector if back_sector is None else back_sector
                if 0 <= back_sector < len(self.sectors):
                    mid = self.sides[ld.front].mid if ld.front >= 0 else None
                    ld.back = self._new_side(back_sector, mid)
        else:
            if ld.back >= 0:
                if ld.back == len(self.sides) - 1:
                    self.sides.pop()
                ld.back = -1
                # "solid" is only meaningful on a two-sided line, and the
                # validator rejects it on a one-sided one, so drop the flag.
                ld.solid = False
        self._after_mutate()

    def set_line_special(self, idx: int, special: int = 0,
                         tag: int = 0) -> None:
        if not (0 <= idx < len(self.lines)):
            return
        self._record()
        self.lines[idx].special = int(special)
        self.lines[idx].tag = int(tag)
        self._after_mutate()

    def set_line_solid(self, idx: int, solid: bool) -> bool:
        """Toggle the "solid wall" flag on a two-sided linedef.

        Returns False (and changes nothing) for a one-sided linedef, where the
        flag is meaningless — the wall is already impassable.
        """
        if not (0 <= idx < len(self.lines)):
            return False
        ld = self.lines[idx]
        if ld.back < 0:
            return False
        self._record()
        ld.solid = bool(solid)
        self._after_mutate()
        return True

    def set_side_tex(self, side_idx: int, upper: str | None = None,
                     mid: str | None = None, lower: str | None = None) -> bool:
        """Assign a sidedef's upper/mid/lower textures (step lips included)."""
        if not (0 <= side_idx < len(self.sides)):
            return False
        sd = self.sides[side_idx]
        self._record()
        if upper is not None:
            sd.upper = upper
        if mid is not None:
            sd.mid = mid
        if lower is not None:
            sd.lower = lower
        self._after_mutate()
        return True

    def line_wall_kind(self, idx: int) -> str:
        """How a linedef reads on the 2D editor overlay.

        ``"wall"`` — impassable: one-sided, flagged ``solid``, or a floor step
        over ``STEP_HEIGHT``. ``"step"`` — a walkable floor step. ``"open"`` —
        equal-height portal.
        """
        if not (0 <= idx < len(self.lines)):
            return "open"
        ld = self.lines[idx]
        if ld.solid or ld.back < 0:
            return "wall"
        delta = self._floor_delta(idx)
        if delta > STEP_HEIGHT:
            return "wall"
        return "step" if delta > 0 else "open"

    def _floor_delta(self, idx: int) -> int:
        """Absolute floor-height difference across a two-sided linedef."""
        ld = self.lines[idx]
        if ld.back < 0:
            return 0
        front = self.sides[ld.front].sector if ld.front >= 0 else -1
        back = self.sides[ld.back].sector if ld.back >= 0 else -1
        if not (0 <= front < len(self.sectors)) or not (0 <= back < len(self.sectors)):
            return 0
        return abs(self.sectors[front].floor_h - self.sectors[back].floor_h)

    def set_sector_heights(self, idx: int, floor_h: int | None = None,
                           ceil_h: int | None = None) -> tuple[int, int] | None:
        """Set a sector's floor/ceiling through the shared clamp.

        Both entry points — the ``пол ▲/▼`` buttons and the numeric fields —
        funnel through here, so the value is integral, inside
        ``MIN_SECTOR_HEIGHT..MAX_SECTOR_HEIGHT`` and never leaves
        ``floor_h <= ceil_h``. Returns the pair actually applied.
        """
        if not (0 <= idx < len(self.sectors)):
            return None
        s = self.sectors[idx]
        want_floor = s.floor_h if floor_h is None else floor_h
        want_ceil = s.ceil_h if ceil_h is None else ceil_h
        got_floor, got_ceil = clamp_sector_heights(want_floor, want_ceil)
        if (got_floor, got_ceil) == (s.floor_h, s.ceil_h):
            return got_floor, got_ceil
        self._record()
        s.floor_h, s.ceil_h = got_floor, got_ceil
        self._after_mutate()
        return got_floor, got_ceil

    def split_line_at(self, idx: int, x: float, y: float) -> int:
        """Split linedef ``idx`` at a point; returns the new vertex index."""
        if not (0 <= idx < len(self.lines)):
            raise ValueError("split: no such linedef")
        x, y = self.snap(x, y)
        ld = self.lines[idx]
        v1, v2 = ld.v1, ld.v2
        f = ld.flags, ld.special, ld.tag
        fs = self.sides[ld.front]
        bs = self.sides[ld.back] if ld.back >= 0 else None
        self._record()
        nv = len(self.verts)
        self.verts.append(Vertex(x, y))
        fa = len(self.sides)
        self.sides.append(SideDef(upper=fs.upper, mid=fs.mid, lower=fs.lower,
                                  x_off=fs.x_off, y_off=fs.y_off,
                                  sector=fs.sector))
        if bs is not None:
            self.sides.append(SideDef(upper=bs.upper, mid=bs.mid,
                                      lower=bs.lower, x_off=bs.x_off,
                                      y_off=bs.y_off, sector=bs.sector))
        self.lines[idx] = LineDef(v1, nv, *f, front=fa,
                                  back=fa + 1 if bs is not None else -1)
        self.lines.append(LineDef(nv, v2, *f, front=fa,
                                  back=fa + 1 if bs is not None else -1))
        self._after_mutate()
        return nv

    # ── sector operations ─────────────────────────────────────────────
    def add_sector(self, floor_h: int = 0, ceil_h: int = 128,
                   floor_tex: str = "floor", ceil_tex: str = "ceiling",
                   light: int = 160) -> int:
        self._record()
        self.sectors.append(Sector(floor_h=floor_h, ceil_h=ceil_h,
                                   floor_tex=floor_tex, ceil_tex=ceil_tex,
                                   light=light))
        self._after_mutate()
        return len(self.sectors) - 1

    def set_sector_props(self, idx: int, floor_h: int | None = None,
                         ceil_h: int | None = None,
                         floor_tex: str | None = None,
                         ceil_tex: str | None = None,
                         light: int | None = None,
                         special: int | None = None,
                         tag: int | None = None) -> None:
        if not (0 <= idx < len(self.sectors)):
            return
        self._record()
        s = self.sectors[idx]
        if floor_h is not None:
            s.floor_h = int(floor_h)
        if ceil_h is not None:
            s.ceil_h = int(ceil_h)
        if floor_tex is not None:
            s.floor_tex = floor_tex
        if ceil_tex is not None:
            s.ceil_tex = ceil_tex
        if light is not None:
            s.light = max(0, min(255, int(light)))
        if special is not None:
            s.special = int(special)
        if tag is not None:
            s.tag = int(tag)
        self._after_mutate()

    def _contour_loop(self, line_ids: list[int]) -> tuple[list[LineDef] | None, str]:
        """Order the edges into a closed loop, or ``(None, reason)``."""
        if len(line_ids) < 3:
            return None, "контур должен содержать не менее 3 рёбер"
        adjacent: dict[int, list[int]] = {}
        for li in line_ids:
            if not (0 <= li < len(self.lines)):
                return None, f"лайндеф {li}: не существует"
            ld = self.lines[li]
            if ld.v1 == ld.v2:
                return None, "вырожденное ребро в контуре"
            adjacent.setdefault(ld.v1, []).append(li)
            adjacent.setdefault(ld.v2, []).append(li)
        for v, ils in adjacent.items():
            if len(ils) != 2:
                return None, "контур не замкнут (рёбра не образуют цикл)"

        order: list[LineDef] = []
        visited: set[int] = set()
        first_ld = self.lines[line_ids[0]]
        cur_v = first_ld.v1
        start_v = first_ld.v1
        for _ in range(len(line_ids) + 1):
            cands = [li for li in adjacent.get(cur_v, []) if li not in visited]
            if not cands:
                break
            li = cands[0]
            visited.add(li)
            ld = self.lines[li]
            order.append(ld)
            cur_v = ld.v2 if ld.v1 == cur_v else ld.v1
            if cur_v == start_v and len(visited) == len(line_ids):
                break
        if len(order) != len(line_ids) or cur_v != start_v:
            return None, "контур не замкнут"
        return order, ""

    def sector_from_contour(self, line_ids: list[int],
                            floor_h: int = 0, ceil_h: int = 128,
                            floor_tex: str = "floor", ceil_tex: str = "ceiling",
                            light: int = 160) -> tuple[int, list[str]]:
        """Turn a closed chain of existing linedefs into a new sector.

        Every edge becomes two-sided: the side facing AWAY from the contour
        centroid keeps an existing sector as front, the interior gets a fresh
        sector as back.  Returns ``(sector_id, [errors])``; on error the map
        is untouched.
        """
        if len(set(line_ids)) != len(line_ids):
            return -1, ["повторяющиеся рёбра в контуре"]
        order, reason = self._contour_loop(line_ids)
        if order is None:
            return -1, [reason]
        new_id = len(self.sectors)
        loop_v = [ld.v1 for ld in order] + [order[-1].v2]
        xs = [self.verts[v].x for v in loop_v]
        ys = [self.verts[v].y for v in loop_v]
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)

        self._record()
        self.sectors.append(Sector(floor_h=floor_h, ceil_h=ceil_h,
                                   floor_tex=floor_tex, ceil_tex=ceil_tex,
                                   light=light))
        for ld in order:
            ax, ay = self.verts[ld.v1].x, self.verts[ld.v1].y
            bx, by = self.verts[ld.v2].x, self.verts[ld.v2].y
            cross = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
            if cross < 0:
                ld.v1, ld.v2 = ld.v2, ld.v1   # interior lands on back side
                ld.front, ld.back = ld.back, ld.front
            ax, ay = self.verts[ld.v1].x, self.verts[ld.v1].y
            bx, by = self.verts[ld.v2].x, self.verts[ld.v2].y
            mx, my = (ax + bx) / 2, (ay + by) / 2
            ddx, ddy = mx - cx, my - cy
            ln = (ddx * ddx + ddy * ddy) ** 0.5 or 1.0
            eps = 1.0
            outside = self.sm.find_sector_at(mx + ddx / ln * eps,
                                             my + ddy / ln * eps)
            if outside < 0:
                outside = self.sm.find_sector_at(mx - ddx / ln * eps,
                                                 my - ddy / ln * eps)
            if outside < 0:
                outside = self.cur_sector
            if ld.front >= 0:
                self.sides[ld.front].sector = outside
            else:
                ld.front = self._new_side(outside)
            outer = self.sides[ld.front]
            ld.back = self._new_side(new_id, outer.mid)
            # a step lip the author already assigned to the outer side must
            # survive the split instead of dropping back to "-"
            self.sides[ld.back].upper = outer.upper
            self.sides[ld.back].lower = outer.lower
        self._after_mutate()
        return new_id, []

    # ── thing operations ──────────────────────────────────────────────
    def add_thing(self, x: float, y: float, typ: str = "player1") -> int:
        x, y = self.snap(x, y)
        self._record()
        self.things.append(Thing(x, y, type=typ))
        self._after_mutate()
        return len(self.things) - 1

    def delete_thing(self, idx: int) -> None:
        if not (0 <= idx < len(self.things)):
            return
        self._record()
        self.things.pop(idx)
        self._after_mutate()

    def move_thing(self, idx: int, x: float, y: float) -> None:
        if not (0 <= idx < len(self.things)):
            return
        x, y = self.snap(x, y)
        self._record()
        self.things[idx].x = x
        self.things[idx].y = y
        self._after_mutate()

    def set_thing_angle(self, idx: int, angle: int) -> None:
        if not (0 <= idx < len(self.things)):
            return
        self._record()
        self.things[idx].angle = int(angle) % 360
        self._after_mutate()

    # ── undo / redo ───────────────────────────────────────────────────
    def can_undo(self) -> bool:
        return bool(self._history)

    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def _apply_diff(self, diff: dict, forward: bool) -> None:
        mapping = {
            "vertices": self.verts, "linedefs": self.lines,
            "sidedefs": self.sides, "sectors": self.sectors,
            "things": self.things,
        }
        order = list(ENTITY_KINDS)
        if not forward:
            order.reverse()
        for kind in order:
            item = diff.get(kind)
            if item is None:
                continue
            op = item[0]
            lst = mapping[kind]
            if op == "ix":
                for i, (old, new) in item[1].items():
                    _apply_item(lst[i], kind, new if forward else old)
            elif op == "ap":
                if forward:
                    lst.append(_item_from_json(kind, item[1]))
                else:
                    lst.pop()
            elif op == "pop":
                if forward:
                    lst.pop()
                else:
                    lst.append(_item_from_json(kind, item[1]))
            elif op == "full":
                lst[:] = [_item_from_json(kind, v)
                          for v in (item[2] if forward else item[1])]
        self.sm.invalidate()

    def undo(self) -> None:
        if not self._history:
            return
        diff = self._history.pop()
        self._apply_diff(diff, forward=False)
        self._redo_stack.append(diff)

    def redo(self) -> None:
        if not self._redo_stack:
            return
        diff = self._redo_stack.pop()
        self._apply_diff(diff, forward=True)
        self._history.append(diff)


def new_room(w: float = 1600.0, h: float = 1600.0) -> SectorMap:
    """Empty room sector 0 with a solid border and one player spawn."""
    sm = SectorMap()
    sm.sectors.append(Sector())
    side = len(sm.sidedefs)
    sm.sidedefs.append(SideDef(sector=0))
    for i in range(4):
        sm.verts.append(Vertex((0.0, w, w, 0.0)[i], (0.0, 0.0, h, h)[i]))
    for i in range(4):
        sm.linedefs.append(LineDef(i, (i + 1) % 4, front=side))
    sm.things.append(Thing(w / 2, h / 2))
    sm.invalidate()
    return sm


def save_mt1_with_bsp(sm: SectorMap, path: str) -> None:
    """Save ``sm`` as ``maps/*.mt1`` carrying precomputed BSP slices (D2).

    A FRESH tree is built from the current geometry (the editor never writes
    stale slices), and the extended vertex table the SEGS reference (original
    points + split endpoints) becomes the file's VERTEXES — the same layout
    ``smoke_mt1_format.test_bsp_scene_consistency`` verifies.  On load the
    slices ride back in ``sm._bsp`` and the scene reuses them instead of
    rebuilding.
    """
    from engine.bsp import build_bsp
    from shared import mt1

    lumps = build_bsp(sm).to_lumps(sm.verts)
    ext = lumps.pop("VERTEX_EXTRA")
    ext_sm = SectorMap(
        [Vertex(v[0], v[1]) for v in ext],
        list(sm.linedefs), list(sm.sidedefs),
        list(sm.sectors), list(sm.things),
    )
    with open(path, "wb") as f:
        f.write(ext_sm.to_mt1(lumps))