"""BSP builder for sector maps (OpenSpec change ``bsp-renderer-mt1-format``,
task 2.2).  Pure stdlib, no numpy.

Implements the classic DOOM-style recursive binary space partition over the
map's linedefs:

* every linedef contributes one seg per visible side (front always; front+back
  for two-sided lines), giving window/step borders both of their edges;
* a partition line is picked from the current seg set (heuristic: fewest
  splits, then most balanced total side length -- D3);
* segs crossing the partition are split at the intersection point, collinear
  segs go to the front child (each boundary is emitted exactly once);
* recursion stops when no partition can separate the set any further (the set
  is convex / degenerate) or a hard depth cap is hit, producing a subsector;
* every node's front/back child gets a bbox for render-time frustum culling.

``BspTree`` exposes the flat arrays the renderer walks at runtime, and bridges
to the ``SEGS/SUBSECTORS/NODES`` lumps of ``shared.mt1`` (split endpoints are
appended to the vertex table, DOOM-style).
"""
from __future__ import annotations

import math

from shared.sector_map import SectorMap

MAX_DEPTH = 200
_EPS = 1e-7


class Seg:
    """Work item of a partition: a directed boundary piece.

    ``x1..u_off`` describe the geometric piece; ``linedef``/``side`` say which
    sidedef of which linedef owns its textures; ``front_sec`` is the sector
    index on the right of the direction (used by flat/visplane logic).
    """

    __slots__ = ("x1", "y1", "x2", "y2", "linedef", "side", "u_off",
                 "front_sec")

    def __init__(self, x1, y1, x2, y2, linedef, side, u_off, front_sec):
        self.x1 = float(x1)
        self.y1 = float(y1)
        self.x2 = float(x2)
        self.y2 = float(y2)
        self.linedef = linedef
        self.side = side
        self.u_off = float(u_off)
        self.front_sec = front_sec

    @property
    def dx(self) -> float:
        return self.x2 - self.x1

    @property
    def dy(self) -> float:
        return self.y2 - self.y1

    def length(self) -> float:
        return math.hypot(self.dx, self.dy) or 1.0

    def split(self, t: float) -> "Seg":
        """Truncate self to 0..t and return the t..1 tail as a new seg."""
        sx = self.x1 + self.dx * t
        sy = self.y1 + self.dy * t
        tail = Seg(sx, sy, self.x2, self.y2, self.linedef, self.side,
                   self.u_off + t * self.length(), self.front_sec)
        self.x2, self.y2 = sx, sy
        return tail


def _side(px: float, py: float, sx: float, sy: float,
          sdx: float, sdy: float) -> float:
    """Signed cross product of (p - s) against partition dir; + = left side."""
    return sdx * (py - sy) - sdy * (px - sx)


def _intersect(s: Seg, sx: float, sy: float, sdx: float, sdy: float):
    """t in (0,1) where seg ``s`` crosses the infinite partition line."""
    den = sdx * s.dy - sdy * s.dx      # B: side grows by this per unit t
    if abs(den) < 1e-12:
        return None
    a = sdx * (s.y1 - sy) - sdy * (s.x1 - sx)  # side at the seg start
    t = -a / den                                # side=0 along the seg
    return t if 0.0 < t < 1.0 else None


class Sub:
    __slots__ = ("segs", "idx")

    def __init__(self, segs: list):
        self.segs = segs
        self.idx = -1


class Node:
    __slots__ = ("px", "py", "pdx", "pdy", "front", "back",
                 "fbox", "bbox", "idx", "_f", "_b", "_union")

    def __init__(self, px, py, pdx, pdy, front, back):
        self.px, self.py, self.pdx, self.pdy = px, py, pdx, pdy
        self.front = front      # Node | Sub
        self.back = back
        self.fbox = None
        self.bbox = None
        self.idx = -1


def _bbox_of(segs: list) -> tuple:
    if not segs:
        return (0.0, 0.0, 0.0, 0.0)
    xs = [v for s in segs for v in (s.x1, s.x2)]
    ys = [v for s in segs for v in (s.y1, s.y2)]
    return (min(xs), min(ys), max(xs), max(ys))


class BspTree:
    """Precomputed partition: subsectors + node hierarchy with child bboxes."""

    def __init__(self, root):
        """``root`` is the built Node/Sub hierarchy; the final segment array
        (split pieces included) is collected from its leaves in post-order,
        which also makes every subsector's segs a contiguous SEGS range."""
        self.segs: list[Seg] = []
        self.root = root
        self._sub_list: list[Sub] = []
        self._sub_segs: list[list[int]] = []
        self._node_list: list[Node] = []
        self._build_indexes(root, 0)

    # --- index flat structures for renderer hot paths ---------------------
    @property
    def x1(self) -> list[float]:
        return [s.x1 for s in self.segs]

    @property
    def y1(self) -> list[float]:
        return [s.y1 for s in self.segs]

    @property
    def x2(self) -> list[float]:
        return [s.x2 for s in self.segs]

    @property
    def y2(self) -> list[float]:
        return [s.y2 for s in self.segs]

    @property
    def linedefs(self) -> list[int]:
        return [s.linedef for s in self.segs]

    @property
    def sides(self) -> list[int]:
        return [s.side for s in self.segs]

    @property
    def secs(self) -> list[int]:
        return [s.front_sec for s in self.segs]

    def sub_segs(self) -> list[list[int]]:
        return self._sub_segs

    def sub_box(self, si: int) -> tuple:
        return _bbox_of(self._sub_list[si].segs)

    def node_box(self, ni: int) -> tuple:
        n = self._node_list[ni]
        fb, bb = n.fbox, n.bbox
        return (min(fb[0], bb[0]), min(fb[1], bb[1]),
                max(fb[2], bb[2]), max(fb[3], bb[3]))

    def subsector_of_seg(self, si: int) -> int:
        for sub_i, sl in enumerate(self._sub_segs):
            if si in sl:
                return sub_i
        return -1

    def _build_indexes(self, node, seg_start: int) -> tuple:
        """Post-order: fill flat lists, record child refs + bboxes, extend
        ``self.segs`` with the leaf segs so subsectors stay contiguous."""
        if isinstance(node, Sub):
            node.idx = len(self._sub_list)
            self._sub_list.append(node)
            n0 = seg_start
            self.segs.extend(node.segs)
            seg_start += len(node.segs)
            self._sub_segs.append(list(range(n0, seg_start)))
            return "s", node.idx, _bbox_of(node.segs), seg_start

        fkind, fidx, fbox, seg_start = self._build_indexes(node.front, seg_start)
        bkind, bidx, bbox, seg_start = self._build_indexes(node.back, seg_start)
        node.idx = len(self._node_list)
        self._node_list.append(node)
        node.fbox = fbox
        node.bbox = bbox
        node._f = (fkind, fidx)
        node._b = (bkind, bidx)
        node._union = (min(fbox[0], bbox[0]), min(fbox[1], bbox[1]),
                       max(fbox[2], bbox[2]), max(fbox[3], bbox[3]))
        return "n", node.idx, node._union, seg_start

    # --- stats -------------------------------------------------------------
    def subsector_count(self) -> int:
        return len(self._sub_list)

    def node_count(self) -> int:
        return len(self._node_list)

    def seg_count(self) -> int:
        return len(self.segs)

    # --- lump bridge --------------------------------------------------------
    def to_lumps(self, verts) -> dict:
        """Serialize to SEGS/SUBSECTORS/NODES bytes.

        ``verts`` is the map's original vertex list (any iterable of ``.x/.y``
        or 2-tuples).  Split endpoints that don't already exist in it are
        appended, DOOM-style, so every seg can reference its endpoints by
        index.  Returns ``{lump_name: bytes, "VERTEX_EXTRA": [(x, y), ...]}``
        where ``VERTEX_EXTRA`` is the full extended vertex table the SEGS lump
        indexes into.
        """
        from shared import mt1

        table: list[tuple[float, float]] = []
        idx_of: dict[tuple[float, float], int] = {}
        for v in verts:
            x, y = (v.x, v.y) if hasattr(v, "x") else (v[0], v[1])
            idx = len(table)
            table.append((float(x), float(y)))
            idx_of.setdefault((round(float(x), 6), round(float(y), 6)), idx)

        def vid(x: float, y: float) -> int:
            key = (round(x, 6), round(y, 6))
            ex = idx_of.get(key)
            if ex is not None:
                return ex
            ex = len(table)
            idx_of[key] = ex
            table.append((float(x), float(y)))
            return ex

        seg_recs = []
        for s in self.segs:
            seg_recs.append((vid(s.x1, s.y1), vid(s.x2, s.y2),
                             s.linedef, s.side, round(s.u_off, 6)))

        sub_recs = [(sl[0] if sl else 0, len(sl)) for sl in self._sub_segs]

        node_recs = []
        for n in self._node_list:
            f = n._f[1] | mt1.LEAF_FLAG if n._f[0] == "s" else n._f[1]
            b = n._b[1] | mt1.LEAF_FLAG if n._b[0] == "s" else n._b[1]
            node_recs.append((n.px, n.py, n.pdx, n.pdy,
                              n.fbox[0], n.fbox[1], n.fbox[2], n.fbox[3],
                              n.bbox[0], n.bbox[1], n.bbox[2], n.bbox[3],
                              f, b))

        return {
            mt1.L_SEGS: mt1.encode_segs(seg_recs),
            mt1.L_SUBSECTORS: mt1.encode_subsectors(sub_recs),
            mt1.L_NODES: mt1.encode_nodes(node_recs),
            "VERTEX_EXTRA": table,
        }

    @classmethod
    def from_lumps(cls, sm: SectorMap, segs_bytes: bytes, subs_bytes: bytes,
                   nodes_bytes: bytes, verts) -> "BspTree":
        """Rebuild a tree from its persistent lumps (``verts`` = the extended
        vertex table).  Segs are grouped per subsector in the lump, so the
        subsector records map onto contiguous ranges."""
        from shared import mt1

        vert_xy = []
        for v in verts:
            x, y = (v.x, v.y) if hasattr(v, "x") else (v[0], v[1])
            vert_xy.append((float(x), float(y)))
        vx = [p[0] for p in vert_xy]
        vy = [p[1] for p in vert_xy]

        secs = [s.sector for s in sm.sidedefs]

        def sec_of(li: int, side: int) -> int:
            if not (0 <= li < len(sm.linedefs)):
                return -1
            ld = sm.linedefs[li]
            sd = ld.front if side == 0 else ld.back
            return secs[sd] if 0 <= sd < len(secs) else -1

        segs: list[Seg] = []
        for v1, v2, li, side, u_off in mt1.decode_segs(segs_bytes):
            segs.append(Seg(vx[v1], vy[v1], vx[v2], vy[v2], li, side, u_off,
                            sec_of(li, side)))

        subs: list[Sub] = []
        for first, count in mt1.decode_subsectors(subs_bytes):
            subs.append(Sub(segs[first:first + count]))

        node_recs = mt1.decode_nodes(nodes_bytes)
        if not node_recs:
            root = subs[0] if subs else Sub([])
            return cls(root)

        nodes: list[Node] = [None] * len(node_recs)  # type: ignore[list-item]
        # Post-order = children carry smaller indices, so rebuild ascending.
        for i in range(len(node_recs)):
            (px, py, pdx, pdy, fx0, fy0, fx1, fy1,
             bx0, by0, bx1, by1, f, b) = node_recs[i]
            node = Node(px, py, pdx, pdy, None, None)
            node.fbox = (fx0, fy0, fx1, fy1)
            node.bbox = (bx0, by0, bx1, by1)
            front = subs[f & ~mt1.LEAF_FLAG] if f & mt1.LEAF_FLAG else nodes[f]
            back = subs[b & ~mt1.LEAF_FLAG] if b & mt1.LEAF_FLAG else nodes[b]
            node.front = front
            node.back = back
            nodes[i] = node
        return cls(nodes[-1])


# --- construction -----------------------------------------------------------

def _initial_segs(sm: SectorMap) -> list:
    segs: list = []
    verts = sm.verts
    secs = [s.sector for s in sm.sidedefs]
    for li, ld in enumerate(sm.linedefs):
        v1, v2 = verts[ld.v1], verts[ld.v2]
        x1, y1, x2, y2 = v1.x, v1.y, v2.x, v2.y
        length = math.hypot(x2 - x1, y2 - y1) or 1.0
        front_sec = secs[ld.front] if 0 <= ld.front < len(secs) else -1
        segs.append(Seg(x1, y1, x2, y2, li, 0, 0.0, front_sec))
        if ld.back >= 0:
            back_sec = secs[ld.back] if ld.back < len(secs) else -1
            segs.append(Seg(x2, y2, x1, y1, li, 1, length, back_sec))
    return segs


def _score_partition(segs: list, cand: Seg):
    """Lower-is-better candidate score (None if it cannot separate the set).

    ``(splits, front_len - back_len, -line_len)`` implements D3: minimize
    split count first, then prefer a balanced total length, then prefer the
    longest partition line.
    """
    sx, sy = cand.x1, cand.y1
    sdx, sdy = cand.dx, cand.dy
    splits = 0
    len_fr = 0.0
    len_bk = 0.0
    has_bk = False
    has_fr = False
    for s in segs:
        a = _side(s.x1, s.y1, sx, sy, sdx, sdy)
        b = _side(s.x2, s.y2, sx, sy, sdx, sdy)
        if a > _EPS or b > _EPS:
            has_fr = True
            len_fr += s.length()
        if a < -_EPS or b < -_EPS:
            has_bk = True
            len_bk += s.length()
        if a > _EPS and b < -_EPS or a < -_EPS and b > _EPS:
            splits += 1
    if not has_fr or not has_bk:
        return None
    return splits, abs(len_fr - len_bk), -cand.length()


def _pick_partition(segs: list):
    best_score = None
    best = None
    seen = set()
    for s in segs:
        key = (round(s.x1, 4), round(s.y1, 4), round(s.x2, 4),
               round(s.y2, 4), s.linedef, s.side)
        if key in seen:
            continue
        seen.add(key)
        score = _score_partition(segs, s)
        if score is None:
            continue
        if best_score is None or score < best_score:
            best_score = score
            best = s
    return best


def _partition(segs: list, cand: Seg):
    front = []
    back = []
    on_line = []
    sec_side: dict = {}          # front_sec -> majority side (+1/-1)
    sx, sy = cand.x1, cand.y1
    sdx, sdy = cand.dx, cand.dy

    def _tally(s: Seg, sign: int) -> None:
        if s.front_sec >= 0:
            sec_side[s.front_sec] = sec_side.get(s.front_sec, 0) + sign

    for s in segs:
        a = _side(s.x1, s.y1, sx, sy, sdx, sdy)
        b = _side(s.x2, s.y2, sx, sy, sdx, sdy)
        if a > _EPS and b > _EPS:
            front.append(s)
            _tally(s, 1)
        elif a < -_EPS and b < -_EPS:
            back.append(s)
            _tally(s, -1)
        elif abs(a) <= _EPS and abs(b) <= _EPS:
            on_line.append(s)                 # collinear: decide below
        else:
            t = _intersect(s, sx, sy, sdx, sdy)
            if t is None:
                # Endpoint touches the line (or parallel): the seg stays
                # on the side its strict endpoint indicates.
                if a > _EPS or b > _EPS:
                    front.append(s)
                    _tally(s, 1)
                elif a < -_EPS or b < -_EPS:
                    back.append(s)
                    _tally(s, -1)
                else:
                    on_line.append(s)
                continue
            head = s
            tail = s.split(t)
            if a > _EPS:                      # start on front side
                front.append(head)
                back.append(tail)
                _tally(head, 1)
                _tally(tail, -1)
            else:                             # start on back side
                back.append(head)
                front.append(tail)
                _tally(head, -1)
                _tally(tail, 1)

    # Collinear segs belong to the side where the rest of their owning
    # sector lies (for a two-sided portal: each side's seg follows its own
    # sector into its own subtree).
    for s in on_line:
        v = sec_side.get(s.front_sec, 0)
        if v < 0:
            back.append(s)
        else:
            front.append(s)
    return front, back


def _build(segs: list, depth: int):
    if depth > MAX_DEPTH:
        return Sub(segs)
    cand = _pick_partition(segs)
    if cand is None:
        # No partition can split this set: it is convex or degenerate.
        return Sub(segs)
    front, back = _partition(segs, cand)
    if not front or not back:
        return Sub(segs)
    nf = _build(front, depth + 1)
    nb = _build(back, depth + 1)
    return Node(cand.x1, cand.y1, cand.dx, cand.dy, nf, nb)


def build_bsp(sm: SectorMap) -> BspTree:
    """Build the BSP tree for a sector map (stdlib only)."""
    segs = _initial_segs(sm)
    if not segs:
        return BspTree(Sub([]))
    return BspTree(_build(segs, 0))