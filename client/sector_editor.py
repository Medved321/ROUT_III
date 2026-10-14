"""Sector map editor data model + entity undo/redo (tasks 4.1, 4.2).

Editing operations on a working ``SectorMap``: vertices, linedefs
(sidedefs), sectors, things — each operation leaves the map referenceable,
and entity-level snapshot-diff history provides undo/redo.  Pure model (no
pygame), so it can be exercised headlessly by ``smoke_sector_editor.py``.
"""
from __future__ import annotations

import math

from shared.sector_map import (
    DOOR_LOCK_BY_TEXTURE, DOOR_TEXTURE_BY_LOCK, DOOR_TEXTURE_NONE,
    MAX_SECTOR_HEIGHT, MIN_SECTOR_HEIGHT, SPECIAL_DOORS, STEP_HEIGHT,
    LineDef, Sector, SectorMap, SideDef, Thing, Vertex,
)

ENTITY_KINDS = ("vertices", "linedefs", "sidedefs", "sectors", "things")
GRID_DEFAULT = 16

# ── stairs ────────────────────────────────────────────────────────────────
# The tool's whole point is that every riser stays walkable, so the default
# rise is comfortably below STEP_HEIGHT and the caller's value is clamped to
# it rather than trusted: a "rise" above STEP_HEIGHT would silently produce a
# wall between two treads, which is the one thing a staircase must never be.
STAIR_RISE_DEFAULT = 16
STAIR_RISE_MIN = 1
STAIR_RISE_MAX = STEP_HEIGHT
# World units a click may sit off the wall it is supposed to be on. Generous
# enough for a mouse click, tight enough that a click in the middle of a room
# is not silently snapped onto the nearest wall.
STAIR_POINT_TOL = 12.0
# A tread shorter than this is not a tread you can aim at in the 2D overlay
# nor walk onto deliberately; the tool refuses instead of producing slivers.
STAIR_MIN_RUN = 4.0
# Ceiling for the step count. 64 steps is already 2x MAX_SECTOR_HEIGHT /
# STEP_HEIGHT, so anything beyond it is a mistake (points on the same walls)
# rather than a staircase the author meant.
STAIR_MAX_STEPS = 64
# Neutral texture for the riser and rail faces.  There is no step texture in
# WALL_TEXTURE_ORDER, and the lip is authored later through the wall palette
# (the "lower"/"upper" slots exist for exactly this).
STAIR_FACE_TEX = "wall"


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


def _promote_back_to_front(ld: LineDef) -> bool:
    """Make ``ld``'s back side its front side, keeping the directed contour.

    ``SectorMap._ensure_polys`` reads a front side as ``v1 -> v2`` and a back
    side as ``v2 -> v1``, so a side moving from ``back`` to ``front`` MUST take
    the vertex swap with it: without the swap the sector's edge is traversed
    backwards, the directed cycle stops closing, and the sector silently
    becomes void — the exact damage a bare ``front = back`` does.

    Single place this rule is written down; ``delete_sector``,
    ``set_two_sided`` and ``repair_front_sides`` all go through it.  Returns
    False when there is no back side to promote.
    """
    if ld.back < 0:
        return False
    ld.v1, ld.v2 = ld.v2, ld.v1
    ld.front, ld.back = ld.back, -1
    return True


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


# ── stair geometry helpers ────────────────────────────────────────────────
def _stair_segments_cross(a, b, c, d) -> bool:
    """True when segment ``ab`` properly crosses segment ``cd``."""
    def side(p, q, r) -> int:
        v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        if v > 1e-9:
            return 1
        if v < -1e-9:
            return -1
        return 0
    o1, o2 = side(a, b, c), side(a, b, d)
    o3, o4 = side(c, d, a), side(c, d, b)
    return o1 != o2 and o3 != o4


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
        # Restored map is the BASELINE, not an edit: it goes into no history, so
        # Ctrl+Z cannot "un-repair" a file the author just opened.
        self.repair_notes: list[str] = self.repair_front_sides()

    # ── map integrity ─────────────────────────────────────────────────
    def repair_front_sides(self) -> list[str]:
        """Restore "every linedef has a front side" on the working map.

        ``SectorMap.validate`` requires a front sidedef on every linedef and
        ``_ensure_polys`` walks directed front/back edges, so ``front < 0`` is
        a broken map either way: the wall belongs to no sector, and whatever
        the author drew there is unreachable. Two cases, both deterministic:

        * ``back >= 0`` — the back side becomes the front, with the vertex swap
          (``_promote_back_to_front``) that keeps the sector's contour closed;
        * no sides at all — the linedef is orphaned and is dropped the way a
          deleted wall is dropped (``_drop(..., record=False)``), and the sectors
          left without a contour are named by the same helper, because losing a
          wall is a visible loss, not a silent one.

        Runs from ``__init__`` so EVERY load path is covered by construction
        (``.mt1``, ``.mts``, legacy ``.json``, plus maps built in tests) and the
        format loaders stay strict. Notes are handed to the caller for the
        status line — a repaired file must not look untouched.
        """
        promote = [li for li, ld in enumerate(self.lines)
                   if ld.front < 0 and ld.back >= 0]
        orphans = [li for li, ld in enumerate(self.lines)
                   if ld.front < 0 and ld.back < 0]
        if not promote and not orphans:
            return []
        notes: list[str] = []
        # promote first: _drop renumbers linedefs, and these indices are pre-drop
        for li in promote:
            _promote_back_to_front(self.lines[li])
        if orphans:
            drop = set(orphans)
            notes += self._open_sector_notes(drop)
            self._drop(lines=drop, record=False)
            notes.append(f"лайндеф(ы) {', '.join(str(i) for i in orphans)}: "
                         "обе стороны потеряны — стена удалена")
        notes += [f"лайндеф {li}: передняя сторона восстановлена из задней"
                  for li in promote]
        self._after_mutate()
        return notes

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
        # swap the neighbour is left with a contour that does not close
        # (`_promote_back_to_front` owns that rule).
        for li, own in keep_walls:
            ld = self.lines[li]
            if own == ld.front:
                _promote_back_to_front(ld)
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

    def line_two_sided(self, idx: int) -> bool:
        """Editor-side "this wall is a portal" test: BOTH sides exist.

        ``LineDef.two_sided`` in ``shared/`` answers "does it have a back side",
        which is a different question: a wall left with ``front < 0, back >= 0``
        reads as two-sided there, yet it has no front side at all, so every
        editor feature that means "a wall between two sectors" — the staircase
        end check, the panel label, the 2D overlay — would act on a wall that
        belongs to no sector. The shared property stays as it is (renderer and
        physics read it); the editor asks this instead.
        """
        if not (0 <= idx < len(self.lines)):
            return False
        ld = self.lines[idx]
        return ld.front >= 0 and ld.back >= 0

    def flip_line(self, idx: int) -> bool:
        """Reverse a linedef's direction; ``False`` when nothing was done.

        Only a two-sided linedef can be flipped: it needs a back side to swap
        with the front.  On a ONE-sided wall the only thing left to swap is the
        vertex pair, and that turns the wall's single directed edge the wrong
        way — its sector's cycle stops closing, ``_ensure_polys`` drops the
        contour and the sector silently becomes void (see ``_stair_open_rails``
        for how the walk works).  That is how a map was destroyed from the
        properties panel, so the refusal is the point of this method and the
        caller has to show it.
        """
        if not (0 <= idx < len(self.lines)):
            return False
        ld = self.lines[idx]
        if ld.front < 0 or ld.back < 0:
            return False
        self._record()
        ld.v1, ld.v2 = ld.v2, ld.v1
        ld.front, ld.back = ld.back, ld.front
        self._after_mutate()
        return True

    def set_two_sided(self, idx: int, two_sided: bool,
                      back_sector: int | None = None) -> list[str]:
        """Give a linedef a back side, or drop it.  Returns ``[notes]``.

        Both directions are refused when the result would be a lie about the
        map, and the refusal is what the panel shows:

        * ``True`` aims a new back side at ``back_sector`` (``cur_sector`` by
          default).  A two-sided line IS a portal between two DIFFERENT sectors,
          so aiming it at the wall's own sector is refused: that would make a
          self-portal (both sides in one sector — the edge is then walked twice
          in opposite directions, and every feature that reads "a wall between
          two rooms" sees a portal whose two floors are, necessarily, at the
          same height).  An aim at a sector that has no contour yet is NOT
          refused: sectors are routinely created empty and closed later by
          ``sector_from_contour``, and a void sector is reported by
          ``validate()`` when the map is saved or played.
        * ``False`` drops the back side, which removes that wall from the
          contour of the sector behind it.  If that contour was the sector's
          last one, the sector silently becomes void — it stops being walkable,
          its area is gone and its height disappears from the panel, with no
          note and no way back but undo.  So the question is asked first
          (``_polygon_alive``) and the toggle is refused by name.

        The ``front < 0`` case is the repair, not a deletion: a wall whose front
        was lost keeps its back side, promoted to front together with the vertex
        order (``_promote_back_to_front``), so the sector survives.
        """
        if not (0 <= idx < len(self.lines)):
            return []
        ld = self.lines[idx]
        notes: list[str] = []
        if two_sided:
            if ld.front >= 0 and ld.back < 0:
                back_sector = self.cur_sector if back_sector is None else back_sector
                if not (0 <= back_sector < len(self.sectors)):
                    return notes
                front_sector = self.sides[ld.front].sector
                if back_sector == front_sector:
                    notes.append(f"лайндеф {idx}: задняя сторона попала бы в "
                                 f"тот же сектор {front_sector} — проём "
                                 "делается между разными секторами, выберите "
                                 "другой сектор")
                    return notes
                self._record()
                ld.back = self._new_side(back_sector, self.sides[ld.front].mid)
                self._after_mutate()
            return notes
        if ld.front < 0:
            # broken wall: there is no front to keep, so the back has to become
            # it rather than be dropped — that is the repair, not a deletion.
            # A linedef with no sides at all is not this method's business;
            # repair_front_sides deals with those at load time.
            if ld.back < 0:
                return notes
            self._record()
            _promote_back_to_front(ld)
            self._after_mutate()
            notes.append(f"лайндеф {idx}: передняя сторона восстановлена "
                         "из задней")
            return notes
        if ld.back >= 0:
            back_sector = self.sides[ld.back].sector
            # The wall stays, but for the sector behind it this edge is gone: ask
            # whether its contour survives that before dropping the side.
            # (``_polygon_alive`` skips the whole linedef, which is exactly what
            # the sector behind loses — the front sector keeps the wall and is
            # not what is being asked about.)  A sector that has NO contour yet
            # is not at risk: rooms are drawn before they are closed, and an
            # empty sector has nothing to lose.
            if (0 <= back_sector < len(self.sectors)
                    and self.sm.has_polygon(back_sector)
                    and not self._polygon_alive(back_sector, {idx})):
                notes.append(f"лайндеф {idx}: у сектора {back_sector} не "
                             "останется замкнутого контура (его область "
                             "станет пустотой) — стена не станет односторонней")
                return notes
            self._record()
            if ld.back == len(self.sides) - 1:
                self.sides.pop()
            ld.back = -1
            # "solid" is only meaningful on a two-sided line, and the
            # validator rejects it on a one-sided one, so drop the flag.
            ld.solid = False
            self._after_mutate()
        return notes

    def set_line_special(self, idx: int, special: int = 0,
                         tag: int = 0) -> None:
        if not (0 <= idx < len(self.lines)):
            return
        self._record()
        self.lines[idx].special = int(special)
        self.lines[idx].tag = int(tag)
        self._after_mutate()

    def line_door_lock(self, idx: int) -> tuple[bool, str | None]:
        """``(is_door, lock)`` for the panel: lock is a colour word or None."""
        if not (0 <= idx < len(self.lines)):
            return (False, None)
        ld = self.lines[idx]
        if ld.special != SPECIAL_DOORS or ld.front < 0:
            return (False, None)
        return (True, DOOR_LOCK_BY_TEXTURE.get(self.sides[ld.front].mid))

    def set_line_door(self, idx: int, lock: str | None = None) -> bool:
        """Turn a two-sided linedef into a door with the given lock colour.

        The lock is carried by the middle texture of BOTH sides (``door_D`` /
        ``door_R`` / ``door_Y`` / ``door_B``), so the two visible twins agree
        about the key; ``solid`` is cleared because a door is a portal. A
        one-sided wall cannot be a door (there is no second sector) and the
        call is refused.
        """
        if not (0 <= idx < len(self.lines)):
            return False
        ld = self.lines[idx]
        if ld.front < 0 or ld.back < 0:
            return False
        tex = DOOR_TEXTURE_BY_LOCK.get(lock, DOOR_TEXTURE_NONE)
        self._record()
        ld.special = SPECIAL_DOORS
        ld.solid = False
        self.sides[ld.front].mid = tex
        self.sides[ld.back].mid = tex
        self._after_mutate()
        return True

    def clear_line_door(self, idx: int) -> bool:
        """Drop the door special; a door-tagged texture resets to the plain one."""
        if not (0 <= idx < len(self.lines)):
            return False
        ld = self.lines[idx]
        self._record()
        ld.special = 0
        for side in (ld.front, ld.back):
            if 0 <= side < len(self.sides) \
                    and self.sides[side].mid in DOOR_LOCK_BY_TEXTURE:
                self.sides[side].mid = self.cur_line_mid
        self._after_mutate()
        return True

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
        if ld.special == SPECIAL_DOORS and ld.back >= 0:
            return "door"
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

    # ── stairs ─────────────────────────────────────────────────────────
    def _line_param(self, li: int, x: float, y: float) -> tuple[float, float] | None:
        """``(t, distance)`` of the projection of ``(x, y)`` onto linedef ``li``.

        ``None`` when the projection falls outside the segment or the linedef is
        degenerate — a point off the end of a wall is not "on" that wall, and
        silently projecting it onto the tip would build a staircase whose end
        is not where the author clicked.
        """
        if not (0 <= li < len(self.lines)):
            return None
        ld = self.lines[li]
        if not (0 <= ld.v1 < len(self.verts) and 0 <= ld.v2 < len(self.verts)):
            return None
        v1, v2 = self.verts[ld.v1], self.verts[ld.v2]
        dx, dy = v2.x - v1.x, v2.y - v1.y
        den = dx * dx + dy * dy
        if den <= 1e-9:
            return None
        t = ((x - v1.x) * dx + (y - v1.y) * dy) / den
        if t < -1e-6 or t > 1 + 1e-6:
            return None
        t = max(0.0, min(1.0, t))
        return t, math.hypot(x - (v1.x + t * dx), y - (v1.y + t * dy))

    def _nearest_stair_hit(self, x: float, y: float):
        """``(linedef, t, snapped point)`` for the wall nearest a raw click.

        Hit-testing runs on the RAW click and only the *projection* is snapped.
        Snapping the click first looks equivalent but is not: on a wall that is
        not itself grid-aligned — x=200 with a 16-unit grid, say — the snapped
        click misses the wall by 8 units and the nearest line is then the one
        the click happened to be near, i.e. the wrong wall, or nothing at all.
        Here the tolerance is measured against what the author actually did,
        and the grid lands on the wall rather than beside it.
        """
        best = None
        for li in range(len(self.lines)):
            hit = self._line_param(li, x, y)
            if hit is None or hit[1] > STAIR_POINT_TOL:
                continue
            if best is None or hit[1] < best[0]:
                ld = self.lines[li]
                v1, v2 = self.verts[ld.v1], self.verts[ld.v2]
                t = hit[0]
                px = v1.x + (v2.x - v1.x) * t
                py = v1.y + (v2.y - v1.y) * t
                best = (li, t, self.snap(px, py))
        return best

    def _split_span(self, li: int, near: tuple[float, float],
                    far: tuple[float, float]) -> tuple[int, int, int]:
        """Cut linedef ``li`` so the sub-span ``near..far`` is one linedef.

        Returns ``(linedef, v_near, v_far)``.  A point that already coincides
        with a wall endpoint reuses that vertex instead of splitting, so a
        staircase that starts at a corner does not leave a zero-length linedef
        behind.  The two halves ``split_line_at`` leaves behind: the first keeps
        the original index, the second is APPENDED at the tail of the list, so
        the caller cannot assume the second half sits next to the first.
        """
        eps = 1e-3
        near_t = self._line_param(li, *near)
        if near_t is None:
            raise ValueError("лестница: точка не на стене")
        if near_t[0] <= eps:
            v_near = self.lines[li].v1
        else:
            v_near = self.split_line_at(li, *near)
            li = len(self.lines) - 1
        far_t = self._line_param(li, *far)
        if far_t is None:
            raise ValueError("лестница: точка не на стене")
        if far_t[0] >= 1.0 - eps:
            v_far = self.lines[li].v2
        else:
            v_far = self.split_line_at(li, *far)
            # lines[li] is now near..far — the span itself — and the rest of
            # the wall has been pushed to the tail.
        return li, v_near, v_far

    def _stair_edge(self, v1: int, v2: int, front: int, back: int,
                    face_tex: str) -> int:
        """Append a linedef between two vertices with explicit sides.

        Textures: ``mid`` on both sides (the line still walls off whatever is
        behind it) and ``face_tex`` on the ``upper``/``lower`` slots of the
        FRONT side, so the step lip reads as a face in the 2D overlay and in
        the 3D lower lip without the author hunting for a texture name.
        """
        if v1 == v2:
            raise ValueError("лестница: вырожденное ребро")
        mid = self.cur_line_mid
        front_side = self._new_side(front, mid)
        self.sides[front_side].upper = face_tex
        self.sides[front_side].lower = face_tex
        back_side = -1
        if back >= 0:
            back_side = self._new_side(back, mid)
            self.sides[back_side].upper = face_tex
            self.sides[back_side].lower = face_tex
        self.lines.append(LineDef(v1, v2, front=front_side, back=back_side))
        return len(self.lines) - 1

    def _stair_open_rails(self, rails: list[int]) -> bool:
        """Turn each rail into a portal to the sector behind it, when there is one.

        ``v1 -> v2`` of a rail runs along the staircase's own sense, so its
        right-hand normal (below) points AWAY from the tread — the tread owns
        ``front``, the neighbour owns ``back``.  Probing both sides is what
        tells us whether the neighbour exists at all: void on that side means
        the rail stays one-sided, i.e. a solid wall, which is exactly what the
        map had before the staircase.

        The orientation argument depends on the polygon bookkeeping of
        ``SectorMap._ensure_polys``, so it is spelled out here rather than
        left to be rediscovered: ``_ensure_polys`` runs a *destructive* greedy
        walk over each sector's outgoing edges (front gives ``v1 -> v2``, back
        gives ``v2 -> v1``) and DISCARDS a path that does not close, popping the
        edges it consumed on the way.  Two consequences shape the whole tool:

        * a chord (a new edge between two vertices of an existing sector) gives
          one vertex a second outgoing edge, the walk can wander off into the
          chord and eat the sector's real contour — which turns the sector into
          void.  So the staircase's ends are sub-spans of existing walls (the
          contour there is unchanged) and every new vertex is touched only by
          the staircase's own edges, whose directed graph is a clean cycle per
          tread.
        * the two rails ARE chords of whatever sector sits beside the staircase,
          so each of them injects a short 2-cycle (``L_i -> L_{i+1}`` and back).
          That cycle is walked, found too short (``len(path) < 3``) and dropped
          — having consumed nothing else.  The neighbour's own contour is
          untouched.  ``build_stair`` then re-checks every previously alive
          sector and rolls the whole thing back if this reasoning ever stops
          holding on a real map.
        """
        sm = self.sm
        sm.invalidate()
        opened = False
        for li in rails:
            ld = self.lines[li]
            if ld.back >= 0:
                continue
            v1, v2 = self.verts[ld.v1], self.verts[ld.v2]
            mx, my = (v1.x + v2.x) / 2, (v1.y + v2.y) / 2
            dx, dy = v2.x - v1.x, v2.y - v1.y
            ln = math.hypot(dx, dy)
            if ln <= 1e-9:
                continue
            # right-hand normal of v1 -> v2 in a y-down world
            nx, ny = dy / ln, -dx / ln
            eps = max(1.0, ln * 0.01)
            outside = sm.find_sector_at(mx + nx * eps, my + ny * eps)
            if outside < 0:
                outside = sm.find_sector_at(mx - nx * eps, my - ny * eps)
            if outside < 0 or outside == self.sides[ld.front].sector:
                continue
            side = self._new_side(outside)
            self.sides[side].upper = STAIR_FACE_TEX
            self.sides[side].lower = STAIR_FACE_TEX
            ld.back = side
            sm.invalidate()
            opened = True
        return opened

    def _stair_piece(self, plan: dict, solid_rails: bool) -> tuple[dict | None, list[str]]:
        """Build one attempted staircase; ``(None, [reason])`` on failure.

        Mutates the map, so the caller owns the rollback: everything here runs
        inside a single history batch and the caller restores the snapshot if
        this returns an error.
        """
        sm = self.sm
        # ── the two end walls become the staircase's first and last tread edge
        mid_a, a_near, a_far = self._split_span(plan["line_a"], plan["a_near"],
                                                 plan["a_far"])
        mid_b, b_near, b_far = self._split_span(plan["line_b"], plan["b_near"],
                                                 plan["b_far"])
        # Order along the staircase.  The far point of the first wall pairs
        # with the near point of the second one, which is the only pairing that
        # survives both end walls: the tread owns the BACK side of each end
        # wall, so its contour runs v2 -> v1 there, i.e. against the wall's own
        # direction.  Pairing the other way makes one of the two ends close its
        # contour in the wrong direction, and a directed contour that does not
        # close is void (see _stair_open_rails).
        p0, pn = a_far, b_near
        q0, qn = a_near, b_far

        p_start, p_end = self.verts[p0], self.verts[pn]
        q_start, q_end = self.verts[q0], self.verts[qn]
        n = plan["steps"]

        def interp(a: Vertex, b: Vertex, t: float) -> tuple[float, float]:
            return (a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t)

        # Rail/interior vertices. treads 0 and n-1 reuse the wall vertices.
        p_idx = [p0] + [self._stair_vertex(*interp(p_start, p_end, i / n))
                        for i in range(1, n)] + [pn]
        q_idx = [q0] + [self._stair_vertex(*interp(q_start, q_end, i / n))
                        for i in range(1, n)] + [qn]

        self._record()
        for i in range(n):
            self.sectors.append(Sector(floor_h=plan["floors"][i],
                                       ceil_h=plan["ceil_h"],
                                       floor_tex=plan["floor_tex"],
                                       ceil_tex=plan["ceil_tex"],
                                       light=plan["light"]))
        first = len(self.sectors) - n
        self._after_mutate()

        # End walls: keep the outer side as front, hand the back to the tread.
        for li, tread in ((mid_a, first), (mid_b, first + n - 1)):
            ld = self.lines[li]
            side = self._new_side(tread)
            self.sides[side].upper = STAIR_FACE_TEX
            self.sides[side].lower = STAIR_FACE_TEX
            ld.back = side
        self._after_mutate()

        # Interior risers (a back-facing lip) and the two rails per tread.
        rails: list[int] = []
        for i in range(n - 1):
            # Riser between tread i (below) and tread i+1 (above), as an edge
            # q_{i+1} -> p_{i+1}.  The LOWER tread owns it as FRONT: tread i's
            # contour reaches p_{i+1} along its p-rail and then has to leave
            # via the riser, i.e. p_{i+1} -> q_{i+1}.  The UPPER tread therefore
            # sees the same directed edge through its BACK side, which
            # _ensure_polys records as q_{i+1} -> p_{i+1} — exactly the first
            # hop of tread i+1's contour.  Getting this backwards leaves both
            # treads with a vertex whose edges only point into it, and
            # _ensure_polys drops the open contour, i.e. a staircase with a
            # hole in it.
            self._stair_edge(q_idx[i + 1], p_idx[i + 1], first + i, first + i + 1,
                             STAIR_FACE_TEX)
        for i in range(n):
            # Rail along p: v1 -> v2 runs tread-backwards, i.e. along the
            # staircase's own sense, so the tread's front side is p_{i+1}->p_i.
            rails.append(self._stair_edge(p_idx[i + 1], p_idx[i], first + i, -1,
                                          STAIR_FACE_TEX))
            rails.append(self._stair_edge(q_idx[i], q_idx[i + 1], first + i, -1,
                                          STAIR_FACE_TEX))
        self._after_mutate()

        if not solid_rails:
            self._stair_open_rails(rails)

        sm.invalidate()
        lost = self._stair_lost_sectors(plan["alive_before"], first)
        if lost:
            return None, ["лестница: сборка разорвала контур сектора "
                          f"{lost} — откат"]
        bad = [i for i in range(first, len(self.sectors))
               if not sm.has_polygon(i)]
        if bad:
            return None, [f"лестница: ступень {bad[0] - first} не замкнулась "
                          "в контур — откат"]
        return {"steps": n, "rise": plan["max_rise"], "sectors": list(
            range(first, len(self.sectors))), "low": plan["low"],
            "high": plan["high"], "rails_open": (
                not solid_rails
                and any(self.lines[li].back >= 0 for li in rails))}, []

    def _stair_vertex(self, x: float, y: float) -> int:
        self.verts.append(Vertex(x, y))
        return len(self.verts) - 1

    def _stair_lost_sectors(self, alive_before: set[int], first_new: int) -> list[int]:
        """Sectors that had a closed contour and lost it (existing ones only)."""
        sm = self.sm
        return sorted(i for i in alive_before
                      if i < first_new and not sm.has_polygon(i))

    def build_stair(self, pts, rise: int = STAIR_RISE_DEFAULT) -> tuple[dict | None, list[str]]:
        """Build a staircase from four points: two on one wall, two on another.

        ``pts`` are world coordinates as clicked.  Points are snapped first, so
        a click near a wall lands exactly on the grid the split writes to —
        ``split_line_at`` snaps again, and a point that was already snapped is
        a fixed point of that.

        Returns ``(info, errors)``.  On error the map is byte-identical to what
        it was: the build is wrapped in one history batch, and a failed
        attempt is rolled back through ``make_diff`` before the next try, so a
        rejected staircase leaves neither geometry nor an undo entry behind.
        """
        plan, errs = self._stair_plan(pts, rise)
        if errs:
            return None, errs
        before = self.sm.to_json()
        self.begin_batch()
        try:
            info, errs = self._stair_attempt(plan, before, solid_rails=False)
            if errs:
                # Retry with solid rails: the open rails are chords of the
                # neighbouring sector and may have eaten its contour.  Solid
                # rails touch nobody, so if THIS fails the staircase is not
                # placeable here and the map is restored untouched.
                info, errs = self._stair_attempt(plan, before, solid_rails=True)
        finally:
            self.end_batch()
        if errs:
            return None, errs
        self._after_mutate()
        info["solid_rails"] = not info["rails_open"]
        return info, []

    def _stair_attempt(self, plan: dict, before: dict, solid_rails: bool):
        """One build attempt, rolled back to ``before`` however it ends.

        The rollback is in a ``finally`` rather than on the error paths only:
        ``_split_span`` raises on a point that is not on its line, and an
        exception escaping the history batch would let ``end_batch`` record the
        half-built staircase as a real, undoable edit -- the one outcome this
        tool must never produce.
        """
        try:
            info, errs = self._stair_piece(plan, solid_rails=solid_rails)
            if errs:
                self._stair_rollback(before)
            return info, errs
        except ValueError as exc:
            self._stair_rollback(before)
            return None, [str(exc)]

    def _stair_rollback(self, snapshot: dict) -> None:
        """Restore the working map to ``snapshot`` without touching history.

        Called from inside the history batch, so the diff that ``end_batch``
        eventually records is computed against the same snapshot and comes out
        empty: a failed staircase is not an undoable edit.
        """
        self._apply_diff(make_diff(snapshot, self.sm.to_json()), forward=False)

    def _stair_plan(self, pts, rise: int) -> tuple[dict | None, list[str]]:
        """Validate the four points and work out heights — no mutation."""
        if len(pts) != 4:
            return None, ["лестница: нужно 4 точки"]
        raw = [(float(p[0]), float(p[1])) for p in pts]
        hits = [self._nearest_stair_hit(x, y) for x, y in raw]
        if any(h is None for h in hits):
            return None, ["лестница: каждая точка должна лежать на стене"]
        snapped = [h[2] for h in hits]
        if len(set(snapped)) < 4:
            return None, ["лестница: точки должны быть различны"]
        # The click order is not part of the contract: try all three ways of
        # splitting four points into two pairs and keep the first that yields
        # two DIFFERENT walls.  ("first two on one wall" is tried first so the
        # documented click order still behaves as written.)  Each hit carries
        # its own line parameter, so _stair_geometry can order the two points
        # on a wall by the wall's own direction.
        for pair_a, pair_b in (((0, 1), (2, 3)), ((0, 2), (1, 3)),
                               ((0, 3), (1, 2))):
            ia, ja = hits[pair_a[0]][0], hits[pair_a[1]][0]
            ib, jb = hits[pair_b[0]][0], hits[pair_b[1]][0]
            # both points of a pair must sit on the SAME wall, and the two
            # walls must differ
            if ia != ja or ib != jb or ia == ib:
                continue
            return self._stair_geometry(
                ia, (hits[pair_a[0]][1], snapped[pair_a[0]]),
                (hits[pair_a[1]][1], snapped[pair_a[1]]),
                ib, (hits[pair_b[0]][1], snapped[pair_b[0]]),
                (hits[pair_b[1]][1], snapped[pair_b[1]]), rise)
        return None, ["лестница: нужно две точки на одной стене и две на другой"]

    def _stair_geometry(self, la: int, a0: tuple, a1: tuple,
                        lb: int, b0: tuple, b1: tuple,
                        rise: int) -> tuple[dict | None, list[str]]:
        for li in (la, lb):
            ld = self.lines[li]
            # A staircase climbs the room on the FRONT side of its end walls, so
            # the front side is the only one that may be read here — and it has
            # to be checked, not assumed: `self.sides[-1]` is some other wall's
            # side, and a broken linedef (front < 0) silently produced heights
            # from it, i.e. "полы секторов на одной высоте" for a staircase
            # that was perfectly placeable.
            if ld.front < 0:
                return None, [f"лестница: край (лайндеф {li}) — у стены нет "
                              "передней стороны (повреждённая карта), "
                              "выберите другую стену"]
            if ld.back >= 0:
                return None, [f"лестница: край (лайндеф {li}) — проём между "
                              "секторами, нужен край сектора (односторонняя "
                              "стена)"]
        for li in (la, lb):
            # The side's own sector may have lost its contour (it is then void,
            # `find_sector_at` reports nothing and nobody can walk there), and
            # its floor height is still a number we would happily build steps
            # from — reject before the heights are compared.
            sec = self.sides[self.lines[li].front].sector
            if not self.sm.has_polygon(sec):
                return None, [f"лестница: край (лайндеф {li}) — сектор {sec} "
                              "не замкнулся в контур (область пуста), "
                              "почините карту"]
        sec_a = self.sides[self.lines[la].front].sector
        sec_b = self.sides[self.lines[lb].front].sector
        if sec_a == sec_b:
            # Two walls of one room are not a height difference, and "полы
            # секторов на одной высоте" sent the author looking for a height
            # bug that was really a pick: name what was picked.
            return None, [f"лестница: оба края (лайндефы {la} и {lb}) "
                          f"принадлежат одному сектору {sec_a} — ступени "
                          "поднимают между РАЗНЫМИ секторами"]
        a, b = self.sectors[sec_a], self.sectors[sec_b]
        try:
            want = max(STAIR_RISE_MIN, min(STAIR_RISE_MAX, int(rise)))
        except (TypeError, ValueError):
            want = STAIR_RISE_DEFAULT
        delta = int(b.floor_h) - int(a.floor_h)
        if delta == 0:
            return None, ["лестница: полы секторов на одной высоте"]
        steps = max(1, math.ceil(abs(delta) / want))
        if steps > STAIR_MAX_STEPS:
            return None, [f"лестница: {steps} ступеней больше предела "
                          f"{STAIR_MAX_STEPS} — проверьте точки"]
        # Treads interpolate between the two room floors, and the phase is anchored on
        # the UPPER room -- the tread next to it IS that room's floor.  Anchoring
        # on the upper room is also what makes the result independent of which
        # wall the author clicked first: the first two clicks pick the pairing,
        # but they must not be able to shift every tread by one step.
        #
        # The heights are NOT snapped to the editor grid.  That grid governs
        # 2D vertex positions; a floor height is free, and snapping it would
        # quietly break the STEP_HEIGHT guarantee the whole tool exists for --
        # a 64-unit gap over 3 steps wants treads at 21/43/64, and rounding
        # those to the grid yields 16/48/64, i.e. a 32-unit riser that is a
        # wall, not a step.
        delta = int(b.floor_h) - int(a.floor_h)
        ascending = delta >= 0
        base_h = int(a.floor_h)
        # frac runs 1/n..1 towards the upper room, whichever end that is, so
        # the tread adjacent to the upper room lands exactly on its floor and
        # the tread adjacent to the lower room is one riser up from its floor
        floors = [base_h + int(round(delta * ((i + 1) / steps if ascending
                                              else i / steps)))
                  for i in range(steps)]
        base = int(a.floor_h)
        max_rise = max(abs(floors[i] - (base if i == 0 else floors[i - 1]))
                       for i in range(len(floors)))
        if max_rise > STAIR_RISE_MAX:
            return None, [f"лестница: перепад {max_rise} превышает "
                          f"STEP_HEIGHT={STAIR_RISE_MAX}"]
        ceil_h, _ = clamp_sector_heights(int(a.ceil_h), int(b.ceil_h))
        # Treads take the light of the end they sit closer to, so the staircase
        # reads as a ramp out of one room into the other.
        span_h = abs(delta)
        light = int(round((int(a.light) * (span_h - abs(floors[0] - base))
                           + int(b.light) * abs(floors[0] - base)) / span_h))
        self.sm.invalidate()
        alive_before = {i for i in range(len(self.sectors))
                        if self.sm.has_polygon(i)}
        # Order the two points on each wall along its own direction.  The only
        # pairing that survives both end walls is far-of-A to near-of-B (see
        # _stair_piece); check the resulting quad is simple before mutating.
        # Each point arrives as ``(t, (x, y))``, hence the [1] everywhere the
        # coordinates are wanted and the [0] where the order matters.
        a_near, a_far = (a0, a1) if a0[0] <= a1[0] else (a1, a0)
        b_near, b_far = (b0, b1) if b0[0] <= b1[0] else (b1, b0)
        rail1 = (a_far[1], b_near[1])
        rail2 = (a_near[1], b_far[1])
        if _stair_segments_cross(*rail1, *rail2):
            return None, ["лестница: стены перекрещиваются — поменяйте местами "
                          "точки или разверните одну стену (Flip)"]
        run = math.hypot(rail1[0][0] - rail2[0][0], rail1[0][1] - rail2[0][1])
        if run < STAIR_MIN_RUN:
            return None, [f"лестница: точки слишком близко ({run:.0f})"]
        reach = math.hypot(rail1[0][0] - rail1[1][0], rail1[0][1] - rail1[1][1])
        if reach < STAIR_MIN_RUN:
            return None, [f"лестница: стены на расстоянии {reach:.0f} — "
                          "слишком близко друг к другу"]
        stride = reach / steps
        if stride < STAIR_MIN_RUN:
            return None, [f"лестница: {steps} ступеней на {reach:.0f} — подступ "
                          f"{stride:.0f} меньше минимума {STAIR_MIN_RUN:.0f}"]
        return {"line_a": la, "line_b": lb,
                "a_near": a_near[1], "a_far": a_far[1],
                "b_near": b_near[1], "b_far": b_far[1],
                "steps": steps, "floors": floors, "ceil_h": ceil_h,
                "floor_tex": a.floor_tex, "ceil_tex": a.ceil_tex,
                "light": light, "max_rise": max_rise,
                "low": min(int(a.floor_h), int(b.floor_h)),
                "high": max(int(a.floor_h), int(b.floor_h)),
                "span": run, "reach": reach,
                "alive_before": alive_before}, []

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