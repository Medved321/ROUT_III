"""Floating slab overlay (OpenSpec change ``floating-sector-platforms``, §5).

A floating sector is a **solid plate**: ``floor_h`` is its walkable top,
``ceil_h`` its underside, and ``floor_h > ceil_h``.  The plate is not part of
the BSP world — its border lines never split the tree (``engine/bsp.py:
_floating_lines``) and never draw a wall band (``sector_render._seg_wall_bands``)
— so the host's floor and ceiling still span the whole footprint underneath.
This module paints the plate on top of that base pass:

* **top** with the slab's ``floor_tex`` and **underside** with its ``ceil_tex``,
  through the existing ``_np_fill_fb`` / ``_draw_solid_flat`` paths, so a plate
  costs the same per-pixel work as any other flat;
* **sides** as vertical bands in the same format ``_draw_wall_bands`` consumes,
  textured with the ``mid`` of the slab's *own* sidedef and shaded by the
  slab's light;
* per column: occluded by the base walls through ``view_buf`` and bounded by
  the residual ``clip_top``/``clip_bot`` opening, exactly like a flat pushed
  during the walk;
* far slabs before near ones, top/underside before sides inside one slab (a
  side face overlaps the near edge of its own top).

The plate is drawn *after* the walls and *before* the single upscale, so it
lands in the same internal buffer for free (design §5).  No Surface is created
per frame.

The per-column occluder intervals it returns are what keeps an actor standing
behind a plate from shining through it: the column z-buffer only knows walls,
so the sprite pass asks :func:`sprite_occluded` instead of pretending the plate
is transparent.  As in the rest of the renderer the test is per column, not per
pixel: a partially covered sprite is drawn whole.
"""
from __future__ import annotations

import math

from engine import constants as const
from engine import game_state as gs
from engine.framebuffer import depth_col
from engine.sector_render import (
    FOCAL,
    HAVE_NP,
    _bbox_visible,
    _draw_solid_flat,
    _draw_wall_bands,
    _light_level,
    _np_fill_fb,
    _push_flat,
)

# Below this forward distance a slab surface is behind the near plane: the
# projection divides by ``t``, and a plate the camera is standing on would
# otherwise paint a screen-filling gradient instead of the floor under it.
_NEAR = 0.5


def _bbox_of_loops(loops) -> tuple:
    x0 = y0 = math.inf
    x1 = y1 = -math.inf
    for points, bbox, _area in loops:
        if bbox[0] < x0:
            x0 = bbox[0]
        if bbox[1] < y0:
            y0 = bbox[1]
        if bbox[2] > x1:
            x1 = bbox[2]
        if bbox[3] > y1:
            y1 = bbox[3]
    return x0, y0, x1, y1


def _near_dist(box, px, py) -> float:
    """Distance from the camera to the closest point of ``box``."""
    dx = box[0] - px if px < box[0] else (px - box[2] if px > box[2] else 0.0)
    dy = box[1] - py if py < box[1] else (py - box[3] if py > box[3] else 0.0)
    return math.hypot(dx, dy)


def _visible_slabs(scene, px, py, pa, view) -> list:
    """Slab sector ids that can appear, far to near (design §5, step 1)."""
    polys = scene.sm._ensure_polys()
    ca, sa = math.cos(pa), math.sin(pa)
    rx, ry = -sa, ca
    tan_h = math.tan(const.FOV * 0.5)
    found = []
    for sid in scene.floating_ids:
        loops = polys[sid] if 0 <= sid < len(polys) else ()
        if not loops:
            continue
        box = _bbox_of_loops(loops)
        if not _bbox_visible(box, px, py, ca, sa, rx, ry, tan_h):
            continue
        near = _near_dist(box, px, py)
        if near > view:
            continue
        found.append((near, sid, loops))
    found.sort(key=lambda item: item[0], reverse=True)
    return found


def _ray_intervals(loops, px, py, dx, dy, t_max) -> list:
    """Forward-distance intervals where the ray ``(px,py)+t*(dx,dy)`` is inside.

    ``dx/dy`` has a forward component of exactly 1 (the column direction is
    ``forward + k*right``), so the solved parameter *is* the view-axis depth
    the rest of the renderer uses for walls, flats and ``z_b``.  Even-odd
    pairing over the sorted crossings handles both convex plates and concave
    outlines with holes.
    """
    ts = []
    for points, bbox, _area in loops:
        if px < bbox[0] and dx <= 0.0:
            continue
        if px > bbox[2] and dx >= 0.0:
            continue
        if py < bbox[1] and dy <= 0.0:
            continue
        if py > bbox[3] and dy >= 0.0:
            continue
        n = len(points)
        ax, ay = points[-1]
        for i in range(n):
            bx, by = points[i]
            ex = bx - ax
            ey = by - ay
            det = dx * ey - dy * ex
            if det > -1e-12 and det < 1e-12:
                ax, ay = bx, by
                continue
            ox = px - ax
            oy = py - ay
            u = (dx * oy - ox * dy) / det
            if u < 0.0 or u >= 1.0:
                ax, ay = bx, by
                continue
            t = (ex * oy - ey * ox) / det
            if t > 0.0:
                ts.append(t)
            ax, ay = bx, by
    if len(ts) < 2:
        return []
    ts.sort()
    out = []
    for i in range(0, len(ts) - 1, 2):
        t0 = ts[i]
        t1 = ts[i + 1]
        if t0 > t_max:
            break
        if t0 < _NEAR:
            t0 = _NEAR
        if t1 > t_max:
            t1 = t_max
        if t1 > t0:
            out.append((t0, t1))
    return out


def _side_bands(scene, sid, px, py, pa, eye, F, hc, iw, ih,
                clip_top, clip_bot, view_buf, view) -> list:
    """Vertical side faces of one slab as ``_draw_wall_bands`` bands."""
    borders = scene.slab_borders(sid)
    if not borders:
        return []
    top = scene.floor_h[sid]
    bot = scene.ceil_h[sid]
    light = scene.light[sid]
    ca, sa = math.cos(pa), math.sin(pa)
    tan_h = math.tan(const.FOV * 0.5)
    inv_iw = 1.0 / iw
    bands = []
    for (ax1, ay1, ax2, ay2, mid_tex, sign, seg_len, other) in borders:
        # Two plates on the same level share a side face that has no height
        # difference: drawing both would leave a seam along the bridge.  A
        # level change keeps both faces — that is the visible step.
        if (0 <= other < len(scene.floating) and scene.floating[other]
                and scene.floor_h[other] == top
                and scene.ceil_h[other] == bot):
            continue
        # Outward-facing test: the contour's interior is on the ``sign`` side,
        # so the camera sees the face only from the other one.  A camera inside
        # the footprint (e.g. below the plate) therefore never draws a side.
        cross = (ax2 - ax1) * (py - ay1) - (ay2 - ay1) * (px - ax1)
        if cross * sign >= 0.0:
            continue
        dx1, dy1 = ax1 - px, ay1 - py
        dx2, dy2 = ax2 - px, ay2 - py
        t1 = dx1 * ca + dy1 * sa
        s1 = -dx1 * sa + dy1 * ca
        t2 = dx2 * ca + dy2 * sa
        s2 = -dx2 * sa + dy2 * ca
        if t1 <= 0.0 and t2 <= 0.0:
            continue
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
            continue
        c0 = max(0, int(clo * iw))
        c1i = min(iw, int(chi * iw) + 1)
        if c1i <= c0:
            continue
        ds = s2 - s1
        dt = t2 - t1
        for col in range(c0, c1i):
            k = (2.0 * ((col + 0.5) * inv_iw) - 1.0) * tan_h
            den = ds - k * dt
            u = 0.5 if -1e-12 < den < 1e-12 else (k * t1 - s1) / den
            if u < 0.0:
                u = 0.0
            elif u > 1.0:
                u = 1.0
            t = t1 + u * dt
            if t <= 0.0 or t >= view_buf[col]:
                continue
            y0 = hc + (eye - top) * F / t
            y1 = hc + (eye - bot) * F / t
            if y1 < y0:
                y0, y1 = y1, y0
            if y0 < clip_top[col]:
                y0 = clip_top[col]
            if y1 > clip_bot[col]:
                y1 = clip_bot[col]
            if y1 <= y0:
                continue
            bands.append((col, y0, y1, mid_tex,
                          _light_level(light, t, view), u * seg_len,
                          ax1, ay1, ax2, ay2, t))
    return bands


def draw_platforms(frame, scene, px, py, pa, eye, F, hc, iw, ih,
                   iw_f, ih_f, fwidths, clip_top, clip_bot, view_buf,
                   view, vh=None, vy=0):
    """Paint every visible floating slab into ``frame`` (tasks 4.2/4.3).

    Returns the per-ray-column occluder intervals for the sprite pass —
    ``[(t_near, screen_y0, screen_y1), ...]`` per column — or ``None`` when
    nothing was drawn.  ``None`` is the "no plates here" answer the caller
    stores in ``gs.platform_occluders`` and the sprite pass treats as free.
    """
    if vh is None:
        vh = const.H
    if view_buf is None:
        view_buf = [float(view)] * iw
    ca, sa = math.cos(pa), math.sin(pa)
    tan_h = math.tan(const.FOV * 0.5)
    inv_iw = 1.0 / iw
    occluders: list | None = None
    for _near, sid, loops in _visible_slabs(scene, px, py, pa, view):
        top = scene.floor_h[sid]
        bot = scene.ceil_h[sid]
        spans: list = []
        rows = [None] * iw
        if occluders is None:
            occluders = [None] * iw
        for col in range(iw):
            vb = view_buf[col]
            if vb <= _NEAR:
                continue
            k = (2.0 * ((col + 0.5) * inv_iw) - 1.0) * tan_h
            ivs = _ray_intervals(loops, px, py, ca - k * sa, sa + k * ca, vb)
            if not ivs:
                continue
            for (t0, t1) in ivs:
                # Top: visible while the camera is above it.  The projected
                # span is [far edge, near edge] because a floor recedes upward.
                if eye > top:
                    y0 = hc + (eye - top) * F / t1
                    y1 = hc + (eye - top) * F / t0
                    if y0 < clip_top[col]:
                        y0 = clip_top[col]
                    if y1 > clip_bot[col]:
                        y1 = clip_bot[col]
                    _push_flat(spans, col, y0, y1, sid, True, ih)
                # Underside: visible while the camera is below it; near edge
                # is the top of the span (a ceiling recedes downward).
                if eye < bot:
                    y0 = hc + (eye - bot) * F / t0
                    y1 = hc + (eye - bot) * F / t1
                    if y0 < clip_top[col]:
                        y0 = clip_top[col]
                    if y1 > clip_bot[col]:
                        y1 = clip_bot[col]
                    _push_flat(spans, col, y0, y1, sid, False, ih)
                # Occluder interval: the whole plate volume in this column
                # (four projected corners), so a sprite behind it disappears
                # whether the top, the underside or a side covers it.
                ys = (hc + (eye - top) * F / t0, hc + (eye - top) * F / t1,
                      hc + (eye - bot) * F / t0, hc + (eye - bot) * F / t1)
                ylo = min(ys)
                yhi = max(ys)
                if rows[col] is None:
                    rows[col] = []
                rows[col].append((t0, ylo, yhi))
        if spans:
            if gs.flat_textured and HAVE_NP:
                _np_fill_fb(spans, scene, px, py, pa, eye, view,
                            FOCAL * ih_f / max(vh, 1.0),
                            iw, ih, iw_f, ih_f, fwidths)
            else:
                for (x, y0, y1, sid_f, is_floor) in spans:
                    _draw_solid_flat(frame, x, y0, y1, scene, sid_f, is_floor,
                                     eye, F, hc, ih, ih_f, iw, iw_f,
                                     fwidths, view)
        bands = _side_bands(scene, sid, px, py, pa, eye, F, hc, iw, ih,
                            clip_top, clip_bot, view_buf, view)
        if bands:
            _draw_wall_bands(frame, bands, scene, view, iw, ih, iw_f, ih_f,
                             fwidths)
        if any(rows):
            scale = vh / float(ih)
            for col in range(iw):
                r = rows[col]
                if not r:
                    continue
                dst = occluders[col]
                if dst is None:
                    dst = occluders[col] = []
                for (t0, ylo, yhi) in r:
                    y0 = vy + ylo * scale
                    y1 = vy + yhi * scale
                    if y1 < y0:
                        y0, y1 = y1, y0
                    dst.append((t0, y0, y1))
    return occluders


def sprite_occluded(occluders, sx: int, half_w: int, top: int, bottom: int,
                    perp: float, screen_w: int | None = None) -> bool:
    """True when a nearer slab covers the sprite's whole box (task 4.5).

    Every column the sprite occupies must hold an interval that starts in
    front of it and spans at least its vertical extent.  The test is
    deliberately conservative: the moment one column is uncovered the sprite
    is drawn *whole*, because the renderer has no per-pixel clipping and a
    half-sprite would be a worse lie than an overlapping one.

    Every column really is tested, not a sample of them: a sprite blit costs
    orders of magnitude more than this scan (a few list lookups per column),
    and a sampled test would drop a sprite whose plate has a one-column hole —
    the opposite of the conservative rule the box test is here to enforce.
    """
    if not occluders:
        return False
    if screen_w is None:
        screen_w = const.W
    top = int(math.floor(top))
    bottom = int(math.ceil(bottom))
    x0 = sx - max(0, int(half_w))
    x1 = sx + max(0, int(half_w))
    if x1 < 0 or x0 >= screen_w:
        return False
    x0 = max(0, x0)
    x1 = min(screen_w - 1, x1)
    for sxc in range(x0, x1 + 1):
        col = depth_col(occluders, sxc, screen_w)
        if col < 0:
            return False
        entries = occluders[col]
        if not entries:
            return False
        for (t_near, y0, y1) in entries:
            if t_near <= perp and y0 <= top and y1 >= bottom:
                break
        else:
            return False
    return True
