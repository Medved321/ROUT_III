"""Sector-mode renderer (OpenSpec change ``bsp-renderer-mt1-format``, Section 4).

BSP sector rendering over DOOM-style geometry:
  * ``SectorScene`` — flat per-object arrays for linedefs/sectors plus lazy
    texture/flat-color caches and the cached BSP tree.
  * ``bsp_clipped_frame`` — near->far BSP walk (task 4.1) feeding every
    visible seg through per-column clipping (task 4.2, ceilingclip/floorclip),
    then visplane flat spans pushed against each wall's seams (task 4.3).
  * ``render_scene`` — the same walk returning the per-column z-buffer (the
    first wall that closes each column) so sprites/particles occlude against
    it; flats textured via numpy or solid by budget; one upscale onto
    ``gs.screen`` (D4/D5).

The DDA portal path and ``LineIndex`` are gone with task 4.4; the tile-mode
path (``client/engine/rendering``) is untouched.
"""
from __future__ import annotations

import math

import pygame

from shared import mt1
from shared.sector_map import LINE_FLAG_SOLID as _LINE_FLAG_SOLID
# Only genuinely immutable constants are imported by value.  ``W``, ``H`` and
# ``FOV`` are *not*: the window size is rewritten on every resize
# (``client/renderer.py``) and the FOV on every settings change
# (``client/settings.py``), while this module is imported lazily -- once, on the
# first sector frame.  Importing them by value froze the sector scene to the
# geometry of whichever window happened to be open at that moment, so the depth
# buffer, the projected sprite columns and the screen disagreed after any
# resize, and the FOV change never reached the BSP rays at all.  They are read
# through ``const`` at the point of use instead.
from engine.constants import TILE, TEX_W, CAM_Z
from engine import constants as const
from engine import game_state as gs
from engine.wallcache import DEFAULT_CACHE, bucket_for_dist

try:
    import numpy as np

    HAVE_NP = True
except Exception:  # pragma: no cover - numpy is optional
    np = None
    HAVE_NP = False

FOCAL = 750.0
VOID = (18, 16, 22)
CELL = TILE                       # 64px spatial grid cell
LIGHT_STEPS = 16

_FALLBACK_WALL = (122, 108, 92)
_FALLBACK_FLAT = (72, 64, 96)


def _bucket_strip_column(cache, raw, level, dist, tex_x, hh, heights, edges):
    """Full cache strip column resampled to the band height ``hh``.

    The (texture, light, distance bucket) strip holds each column already
    scaled to the bucket's projection height; "fetch + slice" is one bulk op
    when the band height matches (D3 hot path).  Bands of other heights are
    resampled from the same strip — equivalent to a 64->hh nearest-neighbour
    scale.  Returns ``None`` on cache miss/error so the caller falls back."""
    bucket = bucket_for_dist(max(0.0, dist), edges)
    try:
        strip = cache.strip(raw, level, bucket)
    except Exception:
        return None
    if strip is None:
        return None
    hb = heights[bucket]
    tx = tex_x % TEX_W
    col = strip[tx * hb * 3:(tx + 1) * hb * 3]
    if hh == hb:
        return col
    if HAVE_NP:
        arr = np.frombuffer(col, dtype=np.uint8).reshape(hb, 3)
        idx = (np.arange(hh, dtype=np.int64) * (hb - 1)) // max(1, hh - 1)
        return arr[idx].tobytes()
    out = bytearray(hh * 3)
    for r in range(hh):
        sr = (r * (hb - 1)) // max(1, hh - 1)
        out[r * 3:r * 3 + 3] = col[sr * 3:sr * 3 + 3]
    return bytes(out)


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _light_factor(light: int, rd: float, view: float) -> float:
    f = (light / 255.0) * max(0.0, 1.0 - rd / (max(view, 1.0) * 1.2))
    return _clamp(f, 0.0, 1.0)


def _light_level(light: int, rd: float, view: float) -> int:
    return int(_light_factor(light, rd, view) * (LIGHT_STEPS - 1e-9))


class SectorScene:
    """Flat array snapshot of a SectorMap for fast per-query access (D5)."""

    def __init__(self, sm) -> None:
        self.sm = sm
        verts = sm.verts
        lds = sm.linedefs
        sd = sm.sidedefs
        n = len(lds)
        self.n_lines = n
        self.x1 = [verts[ld.v1].x for ld in lds]
        self.y1 = [verts[ld.v1].y for ld in lds]
        self.x2 = [verts[ld.v2].x for ld in lds]
        self.y2 = [verts[ld.v2].y for ld in lds]
        self.front_sec = [sd[ld.front].sector if 0 <= ld.front < len(sd) else -1
                          for ld in lds]
        self.back_sec = [sd[ld.back].sector if 0 <= ld.back < len(sd) else -1
                         for ld in lds]
        # A two-sided linedef is a portal only while it is not marked solid.
        # Folding the flag in here lets the wall path below stay byte-for-byte
        # the one-sided path: no new branch in the per-column loop.
        self.solid = [bool(ld.flags & _LINE_FLAG_SOLID) for ld in lds]
        self.two_sided = [self.back_sec[i] >= 0 and not self.solid[i]
                          for i in range(n)]
        self.upper = []
        self.mid = []
        self.lower = []
        self.upper_b = []
        self.mid_b = []
        self.lower_b = []
        for ld in lds:
            if 0 <= ld.front < len(sd):
                side = sd[ld.front]
                self.upper.append(side.upper)
                self.mid.append(side.mid)
                self.lower.append(side.lower)
            else:
                self.upper.append(None)
                self.mid.append(None)
                self.lower.append(None)
            if 0 <= ld.back < len(sd):
                side = sd[ld.back]
                self.upper_b.append(side.upper)
                self.mid_b.append(side.mid)
                self.lower_b.append(side.lower)
            else:
                self.upper_b.append(None)
                self.mid_b.append(None)
                self.lower_b.append(None)
        # Door linedefs (``special == SPECIAL_DOORS``) and their live opening
        # fraction (0.0 shut ... 1.0 open).  The fraction is pushed in each
        # frame by the client bridge from the world snapshot; the scene itself
        # never mutates the map (design D2/D8).  A linedef absent from the
        # dict reads as shut (``progress == 0``), which is the safe default.
        door_lister = getattr(sm, 'door_linedefs', None)
        self.door_lines = set(door_lister()) if door_lister else set()
        self.door_progress: dict[int, float] = {}
        self.floor_h = [s.floor_h for s in sm.sectors]
        self.ceil_h = [s.ceil_h for s in sm.sectors]
        self.light = [s.light for s in sm.sectors]
        self.floor_tex = [s.floor_tex for s in sm.sectors]
        self.ceil_tex = [s.ceil_tex for s in sm.sectors]
        self.bsp: object | None = None  # cached BSP tree (built on demand)
        self._flat_rgb_cache: dict[str, tuple] = {}
        self._wall_lit_cache: dict[tuple[str, int], object] = {}
        self._floor_cache: dict[tuple[int, int], int] = {}
        # Stable surface identity for the id-keyed strip cache: resolved wall
        # surfaces and the fallback are memoised so a new frame reuses the
        # same surface objects (otherwise every frame would miss the cache).
        self._wall_surf_cache: dict[str, object] = {}
        self._fallback_wall_s = None

    def sector_of_side(self, side: int) -> int:
        sd = self.sm.sidedefs
        if 0 <= side < len(sd):
            return sd[side].sector
        return -1

    def ensure_bsp(self):
        """Return (and cache) the scene's BSP tree (task 2.3).

        A map loaded from an ``.mt1`` file that carries precomputed
        SEGS/SUBSECTORS/NODES slices reuses them; otherwise the tree is built
        once from the geometry.  Both paths produce the same subsectors.
        """
        from engine.bsp import BspTree, build_bsp

        if self.bsp is None:
            bsp_data = getattr(self.sm, "_bsp", None)
            if bsp_data:
                lumps = bsp_data["lumps"]
                self.bsp = BspTree.from_lumps(
                    self.sm, lumps[mt1.L_SEGS], lumps[mt1.L_SUBSECTORS],
                    lumps[mt1.L_NODES], bsp_data["verts"])
            else:
                self.bsp = build_bsp(self.sm)
        return self.bsp

    def _resolve_surface(self, name, is_flat: bool):
        surf = gs.flats.get(name) if is_flat else gs.textures.get(name)
        if surf is not None:
            return surf
        surf = gs.textures.get(name) if is_flat else gs.flats.get(name)
        if surf is not None:
            return surf
        return None

    def _fallback_wall(self):
        surf = self._fallback_wall_s
        if surf is None:
            surf = pygame.Surface((TEX_W, TEX_W))
            surf.fill(_FALLBACK_WALL)
            self._fallback_wall_s = surf
        return surf

    def _fallback_flat(self):
        surf = pygame.Surface((TEX_W, TEX_W))
        surf.fill(_FALLBACK_FLAT)
        return surf

    def flat_rgb(self, name) -> tuple:
        if not name or name == "-":
            return _FALLBACK_FLAT
        rgb = self._flat_rgb_cache.get(name)
        if rgb is not None:
            return rgb
        surf = self._resolve_surface(name, True)
        if surf is None:
            rgb = _FALLBACK_FLAT
        else:
            try:
                tiny = pygame.transform.smoothscale(surf, (1, 1))
                col = tiny.get_at((0, 0))
                rgb = (_clamp(int(col.r), 0, 255), _clamp(int(col.g), 0, 255),
                       _clamp(int(col.b), 0, 255))
            except Exception:
                rgb = _FALLBACK_FLAT
        self._flat_rgb_cache[name] = rgb
        return rgb

    def flat_color(self, name) -> tuple:
        return self.flat_rgb(name)

    def wall_surface(self, name) -> object:
        surf = self._wall_surf_cache.get(name)
        if surf is None:
            surf = self._resolve_surface(name, False)
            if surf is None:
                surf = self._fallback_wall()
            if surf.get_width() != TEX_W or surf.get_height() != TEX_W:
                surf = pygame.transform.scale(surf, (TEX_W, TEX_W))
            self._wall_surf_cache[name] = surf
        return surf

    def lit_wall(self, name, level: int):
        key = (name, level)
        surf = self._wall_lit_cache.get(key)
        if surf is not None:
            return surf
        base = self.wall_surface(name)
        lit = base.copy()
        m = int(255 * (level + 0.5) / LIGHT_STEPS)
        if m < 255:
            tint = pygame.Surface(lit.get_size())
            tint.fill((m, m, m))
            lit.blit(tint, (0, 0), special_flags=pygame.BLEND_RGB_MULT)
        if len(self._wall_lit_cache) < 4096:
            self._wall_lit_cache[key] = lit
        return lit

    def floor_at(self, x: float, y: float) -> int:
        key = (int(x), int(y))
        f = self._floor_cache.get(key)
        if f is not None:
            return f
        si = self.sm.find_sector_at(x, y)
        f = self.sm.sectors[si].floor_h if si >= 0 else 0
        if len(self._floor_cache) < 512 or key in self._floor_cache:
            self._floor_cache[key] = f
        return f


# --- framebuffer target (unified backend, total-performance task 1.4) ------
# The sector scene is composed into the same fixed-resolution ``gs.frame`` that
# the tile renderer uses, then transferred to the screen with one scale per
# frame.  Ray casting keeps the settings-driven column count (``render_scale``)
# and vertical step count (``vs_scale``); results are mapped into the frame
# with bulk slice fills (walls as sampled column strips, flats as rect/numpy
# fills) — no per-pixel Python loops in the hot path.

_NP_RGB_CACHE = {}


def _fb_ensure():
    """Current tier framebuffer (created once per resolution)."""
    from engine.rendering import _ensure_frame

    return _ensure_frame()


def _fb_dims(frame):
    return frame.iw, frame.ih


def _fb_widths(iw, iw_f):
    """Map each ray column to its framebuffer column range ``[(x0, x1))``."""
    out = []
    for i in range(iw):
        x0 = (i * iw_f) // iw
        x1 = max(x0 + 1, ((i + 1) * iw_f) // iw)
        out.append((x0, x1))
    return out


def _fb_ys(user_y0, user_y1, ih, ih_f):
    """Map a vertical band in ray-column space to framebuffer rows."""
    from engine.rendering import _frame_ys

    return _frame_ys(user_y0, user_y1, ih, ih_f)


def _np_flat_rgb(scene, tex_name, is_floor):
    from engine import np_flats

    surf = scene._resolve_surface(tex_name, True)
    if surf is None:
        col = scene.flat_rgb(tex_name)
        arr = np.zeros((TEX_W, TEX_W, 3), dtype=np.uint8)
        arr[:] = col
        return arr
    key = (id(surf), is_floor)
    arr = _NP_RGB_CACHE.get(key)
    if arr is not None:
        return arr
    try:
        arr = np_flats.FlatMaps.tex_array(surf)
    except Exception:
        arr = None
    if arr is None:
        col = scene.flat_rgb(tex_name)
        arr = np.zeros((TEX_W, TEX_W, 3), dtype=np.uint8)
        arr[:] = col
    if len(_NP_RGB_CACHE) < 64:
        _NP_RGB_CACHE[key] = arr
    return arr


def _np_fill_fb(spans, scene, px, py, pa, eye, view, focal,
                iw, ih, iw_f, ih_f, fwidths):
    """Rasterize textured flats through the precomputed numpy offset maps.

    Task 3.3: perspective comes from the row-depth map (``v_off``) per
    scanline and the per-column tangent map (``u_off``) — one broadcast per
    span clamped by ``clip_top/clip_bottom`` (already baked into the spans).
    Walls are drawn in a later phase, so flats never paint over walls.
    """
    from engine import np_flats

    if not HAVE_NP:
        return
    view_arr = _fb_ensure().np_view()
    if view_arr is None:
        return
    maps = np_flats.get_maps(iw_f, ih_f, focal)

    for is_floor in (True, False):
        groups: dict[int, list] = {}
        order: list[int] = []
        for (x, y0i, y1i, sid, floor) in spans:
            if floor != is_floor or y1i <= y0i:
                continue
            g = groups.get(sid)
            if g is None:
                g = groups[sid] = []
                order.append(sid)
            x0, x1 = fwidths[x]
            fy0, fy1 = _fb_ys(y0i, y1i, ih, ih_f)
            if x1 > x0 and fy1 > fy0:
                g.append((x0, x1, fy0, fy1))
        for sid in order:
            sub = groups[sid]
            tex_name = scene.floor_tex[sid] if is_floor else scene.ceil_tex[sid]
            flat = _np_flat_rgb(scene, tex_name, is_floor)
            z = scene.floor_h[sid] if is_floor else scene.ceil_h[sid]
            light = scene.light[sid]
            xas_l = []
            yas_l = []
            for (x0, x1, fy0, fy1) in sub:
                w = x1 - x0
                h = fy1 - fy0
                xas_l.append(np.repeat(np.arange(x0, x1, dtype=np.int32), h))
                yas_l.append(np.tile(np.arange(fy0, fy1, dtype=np.int32), w))
            if not xas_l:
                continue
            xa = np.concatenate(xas_l)
            ya = np.concatenate(yas_l)
            adj = maps.sample_pixels(xa, ya, px, py, pa, eye, z, is_floor,
                                     flat, light, view)
            view_arr[ya, xa, :] = adj


def _draw_solid_flat(frame, x, y0, y1, scene, sid, is_floor, eye, F, hc,
                     ih, ih_f, iw, iw_f, fwidths, view):
    """Solid flat: one distance-shaded colour fill per column into the frame."""
    if y1 <= y0:
        return
    z = scene.floor_h[sid] if is_floor else scene.ceil_h[sid]
    if is_floor:
        row = y1 - 0.5
        rd = (eye - z) * F / max(row - hc, 0.5)
    else:
        row = y0 + 0.5
        rd = (z - eye) * F / max(hc - row, 0.5)
    rd = max(rd, 0.5)
    name = scene.floor_tex[sid] if is_floor else scene.ceil_tex[sid]
    col = scene.flat_color(name)
    lv_raw = _light_factor(scene.light[sid], rd, view) * (LIGHT_STEPS - 1e-9)
    lv0 = int(lv_raw)
    frac = lv_raw - lv0
    lv1 = min(LIGHT_STEPS - 1, lv0 + 1)
    m0 = (lv0 + 0.5) / LIGHT_STEPS
    m1 = (lv1 + 0.5) / LIGHT_STEPS
    m = m0 + (m1 - m0) * frac
    c = (int(col[0] * m), int(col[1] * m), int(col[2] * m))
    x0, x1 = fwidths[x]
    fy0, fy1 = _fb_ys(y0, y1, ih, ih_f)
    if x1 > x0 and fy1 > fy0:
        frame.fill_rect(x0, fy0, x1 - x0, fy1 - fy0, c)


def _push_flat(spans, x, y0, y1, sid, is_floor, ih):
    if y0 < 0.0:
        y0 = 0.0
    if y1 > ih:
        y1 = float(ih)
    if y1 > y0:
        spans.append((x, y0, y1, sid, is_floor))


# --- BSP near->far traversal (change ``bsp-renderer-mt1-format``, 4.1) ---
# The walk replaces the DDA portal recursion: the tree yields subsectors in
# DOOM order (near -> far), node bboxes provide frustum culling, and each
# leaf emits its segs exactly once. Walls are then clipped per column against
# the near walls drawn first — far walls behind closed columns never touch
# the buffer (R_RenderSegLoop semantics).


def _bbox_visible(box, px, py, fwx, fwy, rx, ry, tan_h) -> bool:
    """Conservative bbox-in-frustum test (task 4.1): cull only when the box
    lies entirely behind the camera or entirely beyond one frustum plane."""
    x0, y0, x1, y1 = box
    behind = True
    all_right = True
    all_left = True
    for wx in (x0, x1):
        for wy in (y0, y1):
            dx, dy = wx - px, wy - py
            t = dx * fwx + dy * fwy
            s = dx * rx + dy * ry
            if t > 0.0:
                behind = False
            if s <= t * tan_h:
                all_right = False
            if s >= -t * tan_h:
                all_left = False
    return not behind and not all_right and not all_left


def _walk_subsectors(tree, px, py, pa) -> list:
    """Visible subsector indices, near-to-far (DOOM R_RenderBSPNode order).

    On each node the child holding the viewpoint is visited first (its walls
    are closer and will occlude the far child per column), then the opposite
    child.  Cost is O(depth + visible nodes), not O(map lines): subtrees
    outside the frustum / behind the camera are skipped via their node bbox.
    """
    ca, sa = math.cos(pa), math.sin(pa)
    rx, ry = -sa, ca
    tan_h = math.tan(const.FOV * 0.5)
    nodes = tree._node_list
    if not nodes:
        out = []
        for si in range(tree.subsector_count()):
            if _bbox_visible(tree.sub_box(si), px, py, ca, sa, rx, ry, tan_h):
                out.append(si)
        return out
    out = []
    stack = [("n", len(nodes) - 1)]  # root is last (post-order build)
    while stack:
        kind, i = stack.pop()
        if kind == "s":
            if _bbox_visible(tree.sub_box(i), px, py, ca, sa, rx, ry, tan_h):
                out.append(i)
            continue
        n = nodes[i]
        if not _bbox_visible(n._union, px, py, ca, sa, rx, ry, tan_h):
            continue
        a = n.pdx * (py - n.py) - n.pdy * (px - n.px)
        child_near = n._f if a >= 0.0 else n._b  # viewer side of the splitter
        child_far = n._b if a >= 0.0 else n._f
        # LIFO: push far first so the near subtree is processed first.
        stack.append(child_far)
        stack.append(child_near)
    return out


def _seg_wall_bands(bands, scene, tree, si, px, py, pa, F, hc, iw, ih, eye,
                    view, clip_top=None, clip_bot=None, flat_spans=None,
                    view_buf=None, solid_depth=None):
    """Project one seg into ``bands``, DOOM ``R_RenderSegLoop`` style.

    Without clip arrays the seg is emitted as raw projected bands (no
    depth ordering — a debug/projection mode only).  With ``clip_top``/
    ``clip_bot`` each band is drawn only into the currently *open* column
    interval and the clips are updated per column:

    * solid wall — mid band inside ``[max(yc,top), min(yf,bot)]``; the
      column is NOT closed in the clip arrays (an irreversible close is
      order-dependent and is exactly what used to evict a nearer wall).
      Ownership of the column is recorded as ``solid_depth[col] = t``;
    * two-sided wall — only the upper/lower lips are emitted where they
      intersect the open interval; the ceiling bound tightens to the lower
      edge of the upper lip, the floor bound to the upper edge of the lower
      lip (DOOM ``ceilingclip``/``floorclip``).

    **Column ownership is order-independent** (change
    ``sector-near-wall-occlusion``): ``solid_depth[col]`` carries the depth
    of the NEAREST solid wall seen so far in that column, and a column whose
    clip interval is already pinched shut is skipped outright.  Segs inside
    one subsector arrive in an arbitrary order, so "drawn earlier" never
    implies "nearer" — only the per-column minimum decides.  A seg lying
    entirely behind nearer solid walls (``t >= solid_depth[col]`` in every
    column of its span) is discarded without touching the clips, the flats
    or the z-buffer, and the nearer wall is always drawn whichever seg came
    first.

    With ``flat_spans`` (task 4.3) the seg ALSO contributes the front
    sector's floor/ceiling visplane spans: in each still-open column the
    ceiling fills ``[clip_top, min(clip_bot, yc)]`` and the floor fills
    ``[max(clip_top, yf), clip_bot]`` — the same bounds the DDA path pushed
    at the sector's far boundary.  The seam rows ``yc/yf`` are read before
    the clip arrays are updated, so the flat of the front sector is always
    bounded by THIS wall, never by the walls behind it; a column already
    owned by a nearer solid wall is skipped before the push, so flats never
    paint over a nearer wall either.

    Band tuples mirror the DDA ``wall_bands`` shape; ``u`` is the absolute
    offset along the linedef in world units so split segs and back faces stay
    texture-continuous.

    With ``view_buf`` (task 4.4) the seg records the occlusion depth of the
    wall that CLOSES each column — the nearest solid wall (``solid_depth``)
    or the two-sided lip that pinches the interval shut — into
    ``view_buf[col]``, the per-column z-buffer for sprites/particles.
    """
    s = tree.segs[si]
    ax1, ay1 = s.x1, s.y1
    ax2, ay2 = s.x2, s.y2
    li = s.linedef
    cur = s.front_sec
    if li < 0 or li >= scene.n_lines or not (0 <= cur < len(scene.floor_h)):
        return
    ca, sa = math.cos(pa), math.sin(pa)
    tan_h = math.tan(const.FOV * 0.5)
    dx1, dy1 = ax1 - px, ay1 - py
    dx2, dy2 = ax2 - px, ay2 - py
    t1 = dx1 * ca + dy1 * sa
    s1 = -dx1 * sa + dy1 * ca
    t2 = dx2 * ca + dy2 * sa
    s2 = -dx2 * sa + dy2 * ca
    if t1 <= 0.0 and t2 <= 0.0:
        return
    # Near-plane clip: a NON-POSITIVE end (``== 0.0`` included — the eye sits
    # exactly on the vertex) is moved just past the plane before anything is
    # divided by it.  See change ``sector-near-wall-occlusion``, task 2.1.
    if t1 <= 0.0:
        k = -t1 / (t2 - t1)
        s1 += (s2 - s1) * k
        t1 = 1e-6
    elif t2 <= 0.0:
        k = -t2 / (t1 - t2)
        s2 += (s1 - s2) * k
        t2 = 1e-6
    ca1 = 0.5 + 0.5 * s1 / (t1 * tan_h)
    ca2 = 0.5 + 0.5 * s2 / (t2 * tan_h)
    clo = ca1 if ca1 < ca2 else ca2
    chi = ca1 if ca1 > ca2 else ca2
    if chi < 0.0 or clo > 1.0:
        return
    c0 = max(0, int(clo * iw))
    c1i = min(iw, int(chi * iw) + 1)
    if c1i <= c0:
        return
    # Two-sided linedefs store both faces as twin segs; only the face whose
    # side the camera stands on is ever visible (``cross((v2-v1),(cam-v1))
    # < 0`` — the camera is on the RIGHT of the seg's own direction, winding
    # convention independent).  The reversed twin must not contribute bands,
    # flats or clip movement: whichever of the two the in-leaf seg order visits
    # first, it would lower ``clip_bot`` to ITS floor line before the visible
    # twin pushes the lower sector's floor, evicting the pit/stair floor
    # (change ``sector-step-down-pit``).  One-sided walls have no twin and are
    # always drawn.
    if scene.back_sec[li] >= 0:
        if (ax2 - ax1) * (py - ay1) - (ay2 - ay1) * (px - ax1) >= 0.0:
            return
    if s.side == 1:
        back = scene.front_sec[li]
        up_tex, mid_tex, low_tex = (scene.upper_b[li], scene.mid_b[li],
                                    scene.lower_b[li])
    else:
        back = scene.back_sec[li]
        up_tex, mid_tex, low_tex = (scene.upper[li], scene.mid[li],
                                    scene.lower[li])
    Sf = scene.floor_h[cur]
    Sc = scene.ceil_h[cur]
    two = (scene.two_sided[li] and back >= 0 and back != cur
           and 0 <= back < len(scene.floor_h))
    if two:
        Bf = scene.floor_h[back]
        Bc = scene.ceil_h[back]
        if Bf >= Sc or Bc <= Sf:
            two = False
    # A door is drawn as a mid-texture plate whose vertical extent shrinks with
    # its opening fraction (design D8): anchored to the ceiling, it covers the
    # whole opening when shut and has receded entirely at progress 1.  This
    # reuses the column clip arrays — the plate behaves like an upper lip, so
    # the far side shows only through the gap below it.
    is_door = li in scene.door_lines
    if clip_top is not None:
        if solid_depth is None:
            solid_depth = [math.inf] * iw
        # Early-out (task 1.4): when EVERY column of the span is already
        # owned by a nearer solid wall the seg can contribute nothing — no
        # band, no flats, no clip movement.  ``t`` over the span never drops
        # below ``min(t1, t2)``, so comparing that bound against
        # ``solid_depth`` is a sound (conservative) whole-seg test.
        t_lo = t1 if t1 < t2 else t2
        covered = True
        for col in range(c0, c1i):
            if solid_depth[col] > t_lo:
                covered = False
                break
        if covered:
            return
    seg_len = math.hypot(ax2 - ax1, ay2 - ay1) or 1.0
    u_off = s.u_off
    ds = s2 - s1
    dt = t2 - t1
    inv_iw = 1.0 / iw
    if clip_top is None:
        for col in range(c0, c1i):
            k = (2.0 * ((col + 0.5) * inv_iw) - 1.0) * tan_h
            den = ds - k * dt
            u = 0.5 if abs(den) < 1e-12 else (k * t1 - s1) / den
            if u < 0.0:
                u = 0.0
            elif u > 1.0:
                u = 1.0
            t = t1 + u * dt
            if t <= 0.0:
                continue
            lv = _light_level(scene.light[cur], t, view)
            tu = u_off + u * seg_len
            yc = hc + (eye - Sc) * F / t
            yf = hc + (eye - Sf) * F / t
            if not two:
                if yf <= yc:
                    continue
                y0 = 0.0 if yc < 0.0 else yc
                y1 = float(ih) if yf > ih else yf
                if y1 > y0:
                    bands.append((col, y0, y1, mid_tex, lv, tu,
                                  ax1, ay1, ax2, ay2, t))
                continue
            ycb = hc + (eye - Bc) * F / t
            yfb = hc + (eye - Bf) * F / t
            if abs(ycb - yc) > 0.75:
                y0 = yc if yc < ycb else ycb
                y1 = ycb if ycb > yc else yc
                if y0 < 0.0:
                    y0 = 0.0
                if y1 > ih:
                    y1 = float(ih)
                if y1 > y0:
                    bands.append((col, y0, y1, up_tex, lv, tu,
                                  ax1, ay1, ax2, ay2, t))
            if abs(yfb - yf) > 0.75:
                y0 = yf if yf < yfb else yfb
                y1 = yfb if yfb > yf else yf
                if y0 < 0.0:
                    y0 = 0.0
                if y1 > ih:
                    y1 = float(ih)
                if y1 > y0:
                    bands.append((col, y0, y1, low_tex, lv, tu,
                                  ax1, ay1, ax2, ay2, t))
        return
    for col in range(c0, c1i):
        k = (2.0 * ((col + 0.5) * inv_iw) - 1.0) * tan_h
        den = ds - k * dt
        u = 0.5 if abs(den) < 1e-12 else (k * t1 - s1) / den
        if u < 0.0:
            u = 0.0
        elif u > 1.0:
            u = 1.0
        t = t1 + u * dt
        if t <= 0.0:
            continue
        # Order-independent gate: this column already has a NEARER solid wall
        # (tasks 1.2/1.5) — nothing of this seg, flats included, may show.
        if t >= solid_depth[col]:
            continue
        ceilt = clip_top[col]
        flrob = clip_bot[col]
        # A column already shut by a nearer two-sided lip stays untouched:
        # the band interval below would be empty anyway, and re-running the
        # lip maths would rewrite the clips of a closed column.
        if ceilt >= flrob:
            continue
        lv = _light_level(scene.light[cur], t, view)
        tu = u_off + u * seg_len
        yc = hc + (eye - Sc) * F / t
        yf = hc + (eye - Sf) * F / t
        if flat_spans is not None:
            _push_flat(flat_spans, col, ceilt,
                       yc if yc < flrob else flrob, cur, False, ih)
            _push_flat(flat_spans, col,
                       yf if yf > ceilt else ceilt, flrob, cur, True, ih)
        if is_door:
            p = scene.door_progress.get(li, 0.0)
            if p < 1.0:
                # Ceiling-anchored plate: bottom edge rises toward the ceiling
                # as the door opens (0 -> full opening, 1 -> gone).
                pb = yc + (1.0 - p) * (yf - yc)
                lo = yc if yc > ceilt else ceilt
                hi = pb if pb < flrob else flrob
                if hi > lo:
                    bands.append((col, lo, hi, mid_tex, lv, tu,
                                  ax1, ay1, ax2, ay2, t))
                if pb > clip_top[col]:
                    clip_top[col] = pb
            if p <= 0.0:
                # Fully shut: the door owns the column so nothing behind it
                # shows through, and it is the occlusion distance for sprites.
                solid_depth[col] = t
                if view_buf is not None and t < view_buf[col]:
                    view_buf[col] = t
            continue
        if not two:
            y0 = yc if yc > ceilt else ceilt
            y1 = yf if yf < flrob else flrob
            if y1 > y0:
                bands.append((col, y0, y1, mid_tex, lv, tu,
                              ax1, ay1, ax2, ay2, t))
            # The nearest solid wall owns the column from now on.  The clip
            # arrays are deliberately NOT closed (task 1.2): an irreversible
            # close is what used to evict a nearer wall drawn later.
            solid_depth[col] = t
            if view_buf is not None and t < view_buf[col]:
                view_buf[col] = t
            continue
        ycb = hc + (eye - Bc) * F / t
        yfb = hc + (eye - Bf) * F / t
        yl = yc if yc > ceilt else ceilt
        yh = yf if yf < flrob else flrob
        # Upper lip: back ceiling BELOW the front ceiling on screen.
        if ycb > yc:
            r1 = ycb if ycb < yh else yh
            if r1 > yl:
                bands.append((col, yl, r1, up_tex, lv, tu,
                              ax1, ay1, ax2, ay2, t))
            clip_top[col] = r1 if r1 < ycb else ycb
        else:
            clip_top[col] = yl
        # Lower lip: the band is the difference between the two floor lines
        # on screen, in BOTH directions — step up (``yfb < yf``) and pit down
        # (``yfb > yf``).  For the step up this is algebraically the old
        # ``r0``/``yh`` pair (identity proof — change ``sector-step-down-pit``,
        # design); for the pit it draws the face that used to be skipped.
        # ``clip_bot`` follows the BACK floor line (``max(yfb, yl)``), so the
        # lower sector's floor is not evicted by the front floor line.  It
        # never moves past the clip the column already has: a farther lip may
        # not reopen pixels a nearer seg closed (nearer wins), otherwise a
        # far wall's band dips over the near face it stands behind.
        lo = yfb if yfb < yf else yf
        hi = yfb if yfb > yf else yf
        if lo < yl:
            lo = yl
        if hi > flrob:
            hi = flrob
        if hi > lo:
            bands.append((col, lo, hi, low_tex, lv, tu,
                          ax1, ay1, ax2, ay2, t))
        nb = yfb if yfb > yl else yl
        clip_bot[col] = nb if nb < flrob else flrob
        # The first (nearest) wall that closes this column is the occlusion
        # distance for the z-buffer (task 4.4 — sprites/particles/fog).
        if view_buf is not None and clip_top[col] >= clip_bot[col] \
                and t < view_buf[col]:
            view_buf[col] = t


def _draw_wall_bands(frame, bands, scene, view, iw, ih, iw_f, ih_f, fwidths):
    """Draw projected wall bands through the distance-bucket strip cache."""
    DEFAULT_CACHE.set_view(view)
    heights = DEFAULT_CACHE.bucket_heights()
    edge_list = DEFAULT_CACHE._bucket_edges
    for (x, y0, y1, tex_name, level, u, ax1, ay1, ax2, ay2, dist) in bands:
        if y1 <= y0:
            continue
        x0, x1 = fwidths[x]
        fy0, fy1 = _fb_ys(y0, y1, ih, ih_f)
        hh = fy1 - fy0
        if hh <= 0:
            continue
        if tex_name is None or tex_name == "-":
            tex_x = 0
            raw = scene._fallback_wall()
        else:
            tex_x = int(u) % TEX_W
            raw = scene.wall_surface(tex_name)
        # Hot path: fetch the pre-scaled (texture, light, distance bucket)
        # strip column and slice it into the frame (one bulk write per column).
        data = _bucket_strip_column(DEFAULT_CACHE, raw, level, dist, tex_x,
                                    hh, heights, edge_list)
        if data is None:
            # Fallback: legacy lit-surface strip (cache miss / non-64px).
            try:
                if tex_name and tex_name != "-":
                    surf = scene.lit_wall(tex_name, level)
                else:
                    surf = raw
                if surf is None:
                    surf = scene._fallback_wall()
                strip_b = surf.subsurface(
                    (tex_x % TEX_W, 0, 1, min(TEX_W, surf.get_height())))
            except Exception:
                strip_b = pygame.Surface((1, 1))
                strip_b.fill(_FALLBACK_WALL)
            if hh != strip_b.get_height():
                strip_b = pygame.transform.scale(strip_b, (1, hh))
            data = pygame.image.tobytes(strip_b, "RGB")
            if data is None or len(data) < hh * 3:
                continue
        for cx in range(x0, x1):
            frame.blit_column(cx, fy0, data, hh)


def bsp_painter_frame(vh=None, vy=None) -> list:
    """Near->far BSP wall frame (task 4.1 traversal, clipped in 4.2).

    Thin wrapper over :func:`bsp_clipped_frame` — since the walk is
    near-to-far, correct images require the per-column clip pass, so painter
    and clipped variants share it.  Returns the visible subsector order.
    """
    return bsp_clipped_frame(vh, vy)[0]


def bsp_clipped_frame(vh=None, vy=None, view_buf=None) -> tuple:
    """Near->far BSP wall frame with DOOM column clipping (task 4.2).

    Walks the tree near-to-far and feeds every visible seg through
    ``_seg_wall_bands`` with the per-column clip arrays.  Near walls close /
    tighten their columns first, so far walls draw only into the openings
    (or are discarded entirely).  Returns ``(order, clip_top, clip_bot)`` so
    task 4.3 can bound flat spans by the final opening frontier.  An optional
    ``view_buf`` collects the per-column occlusion distances (task 4.4).
    """
    vh = vh if vh is not None else const.H
    vy = vy if vy is not None else 0
    scene = gs.sec_scene
    if scene is None:
        gs.screen.fill(VOID)
        pygame.draw.rect(gs.screen, VOID, (0, vy, const.W, vh))
        return [], [], []
    scale = max(1, gs.render_scale)
    vs = max(1, gs.vs_scale)
    iw = const.W // scale
    ih = max(2, vh // vs)
    frame = _fb_ensure()
    iw_f, ih_f = _fb_dims(frame)
    if iw_f < 1 or ih_f < 1:
        return [], [], []
    frame.clear(VOID)
    fwidths = _fb_widths(iw, iw_f)
    px, py, pa = gs.px, gs.py, gs.pa
    if _player_sector(scene) < 0:
        _upscale(frame, vy, vh)
        return [], [], []
    eye = _current_eye(scene)
    F = FOCAL * ih / vh
    hc = ih / 2.0
    view = max(1, gs.view_dist)
    bsp = scene.ensure_bsp()
    clip_top = [0.0] * iw
    clip_bot = [float(ih)] * iw
    # Nearest solid wall that already owns each column (task 1.1).  A farther
    # solid processed first merely raises it; a nearer one still passes the
    # per-column gate and repaints, so the result does not depend on the
    # arbitrary seg order inside a subsector.
    solid_depth = [math.inf] * iw
    order = _walk_subsectors(bsp, px, py, pa)
    bands = []
    flat_spans = []
    for sub_i in order:
        for si in bsp.sub_segs()[sub_i]:
            _seg_wall_bands(bands, scene, bsp, si, px, py, pa,
                            F, hc, iw, ih, eye, view,
                            clip_top, clip_bot, flat_spans, view_buf,
                            solid_depth)
    # ---- Phase B: visplane flats (task 4.3) ----
    if flat_spans:
        if gs.flat_textured and HAVE_NP:
            _np_fill_fb(flat_spans, scene, px, py, pa, eye, view,
                        FOCAL * ih_f / max(vh, 1.0),
                        iw, ih, iw_f, ih_f, fwidths)
        else:
            for (x, y0, y1, sid, is_floor) in flat_spans:
                _draw_solid_flat(frame, x, y0, y1, scene, sid, is_floor,
                                 eye, F, hc, ih, ih_f, iw, iw_f,
                                 fwidths, view)
    # ---- Phase C: walls (bucket strips, sliced into the frame) ----
    _draw_wall_bands(frame, bands, scene, view, iw, ih, iw_f, ih_f, fwidths)
    _upscale(frame, vy, vh)
    return order, clip_top, clip_bot


def _upscale(frame, vy, vh):
    """One scale of the framebuffer onto the screen per frame."""
    try:
        if vh == const.H and vy == 0:
            frame.draw_to(gs.screen, (const.W, vh))
        else:
            surf = pygame.transform.scale(frame.to_surface(), (const.W, vh))
            gs.screen.blit(surf, (0, vy))
    except Exception:
        gs.screen.fill(VOID)
        pygame.draw.rect(gs.screen, VOID, (0, vy, const.W, vh))


def _current_eye(scene: SectorScene | None) -> float:
    if scene is not None:
        return scene.floor_at(gs.px, gs.py) + CAM_Z
    return CAM_Z


def project_object(wx: float, wy: float, world_h: float,
                   vh: float | None = None, vy: float | None = None,
                   eye: float | None = None) -> tuple | None:
    """Project a world object to (sx, sh, ground_y, perp) or None.

    ``sx`` is a screen column of the *current* window: the FOV and width are
    read live, so a resize or an FOV change moves the projection with the
    scene instead of leaving it projected for whichever window was open when
    this module was first imported.
    """
    if vh is None:
        vh = const.H
    if vy is None:
        vy = 0
    scene = gs.sec_scene
    edx, edy = wx - gs.px, wy - gs.py
    dst = math.hypot(edx, edy)
    ang = math.atan2(edy, edx) - gs.pa
    while ang > math.pi:
        ang -= 2 * math.pi
    while ang < -math.pi:
        ang += 2 * math.pi
    if abs(ang) >= 1.0 or dst < 1:
        return None
    perp = dst * math.cos(ang)
    if perp < 1:
        perp = 1
    if eye is None:
        eye = _current_eye(scene)
    floor_z = scene.floor_at(wx, wy) if scene is not None else 0.0
    sh = world_h * FOCAL / perp
    ground_y = vy + vh / 2 + (eye - floor_z) * FOCAL / perp
    sx = int((0.5 * (ang / (const.FOV / 2)) + 0.5) * const.W)
    return sx, sh, ground_y, perp


def _player_sector(scene: SectorScene | None) -> int:
    if scene is None:
        return -1
    try:
        return scene.sm.find_sector_at(gs.px, gs.py)
    except Exception:
        return -1


def render_scene(vh=None, vy=None) -> list:
    """Render the sector scene via the near->far BSP walk (task 4.4).

    Thin wrapper over :func:`bsp_clipped_frame`: mirrors the DDA-era contract
    so the only caller (``engine/rendering.render``) keeps working —
    draws walls + flats into ``gs.frame`` with one upscale and returns the
    per-column z-buffer (first distance that closes each column, else
    ``view``) for sprite/particle/fog occlusion.

    The returned list *is* the grid: it has ``const.W // max(1,
    gs.render_scale)`` entries, which is not the tile path's
    ``const.W // SCALE`` and does not have to be.  Callers must map a screen
    column onto it with ``engine.framebuffer.depth_col`` rather than
    recomputing a column count from a scale.
    """
    vh = vh if vh is not None else const.H
    vy = vy if vy is not None else 0
    scale = max(1, gs.render_scale)
    iw = const.W // scale
    scene = gs.sec_scene
    if scene is None:
        gs.screen.fill(VOID)
        pygame.draw.rect(gs.screen, VOID, (0, vy, const.W, vh))
        return [999.0] * iw
    view = max(1, gs.view_dist)
    view_buf = [view] * iw
    bsp_clipped_frame(vh, vy, view_buf)
    return view_buf


def project_eye() -> float:
    return _current_eye(gs.sec_scene)