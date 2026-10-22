"""Server-side weapon firing logic (px space).

Ported from ROUT_II_III ``game_actions/shooting.py`` + ``combat.py`` and
adapted to the authoritative server.  Shots are resolved as hitscan rays
against walls, enemies and (in PvP) players.
"""
from __future__ import annotations

import math
import random
from typing import NamedTuple

from shared.combat import wpn_cooldown_ticks
from shared.components import (Position, Rotation, Health, Player,
                             WeaponComponent, Powerup)
from server.game_data import (
    WPN, MELEE_RANGE, PLAYER_MELEE_DMG, MELEE_ANGLE_THRESHOLD,
    PLAYER_RADIUS, ENEMY_RADIUS,
    PLAYER_PROJ_SPEED, PLAYER_PROJ_MAX_DIST, MAX_PLAYER_PROJ,
    BERSERK_DMG_MULT,
)

MAX_HP = 200


class FireResult(NamedTuple):
    """Server decision about one fire intent.

    A refused intent is a normal outcome, not an error: the client sends an
    intent on every gate opening and only the server knows whether the weapon
    was ready.  Returning it makes the decision visible instead of silent.
    """

    fired: bool
    reason: str = ''          # '' | 'cooldown' | 'no_ammo' | 'proj_cap' | 'noop'
    cooldown: int = 0         # ticks remaining on the weapon after the call


def apply_weapon_switch(world, entity_id: int, weapon_id: int) -> None:
    wp = world.entities.get_component(entity_id, WeaponComponent)
    if wp is None:
        return
    if 0 <= weapon_id < len(WPN) and weapon_id in wp.owned:
        wp.current = weapon_id


def _enemies_in_range(world, x: float, y: float, rng: float):
    for e in world.enemies:
        if not e['a'] or e['hp'] <= 0:
            continue
        if math.hypot(e['x'] - x, e['y'] - y) <= rng:
            yield e


# Spatial grid cell for shot resolution: 4 tiles.  The grid is built ONCE per
# handle_shoot() (not per pellet), so SHOTGUN x7 / SSG x14 resolve every pellet
# from the same buckets instead of walking the whole roster 7/14 times.
_SHOT_GRID_CELL = 256.0


def _ray_candidates(grid: dict, cell: float, live: int, x: float, y: float,
                    ca: float, sa: float, t_end: float) -> list:
    """Superset of live enemies whose circle can meet the ray ``[0, t_end]``.

    The bounding box of the segment expanded by ``ENEMY_RADIUS`` contains the
    centre of every circle that intersects the segment, so enumerating all
    overlapping cells is exact for ANY cell size: this is a range query over
    the whole box, not a 3x3 block around a point, so the AI rule
    "cell >= query radius" does not apply here.  The caller re-checks every
    candidate (``a``/``hp`` and the ray-circle test), so any superset yields
    the same hit as the old full scan.

    When the box spans more cells than there are live enemies (a 16500 px BFG
    ray across an open map), a flat pass over the roster is cheaper than the
    cell walk, so it falls back to one -- both paths stay supersets.
    """
    ex = x + ca * t_end
    ey = y + sa * t_end
    m = ENEMY_RADIUS
    x0, x1 = (x, ex) if x <= ex else (ex, x)
    y0, y1 = (y, ey) if y <= ey else (ey, y)
    cx0, cx1 = int((x0 - m) // cell), int((x1 + m) // cell)
    cy0, cy1 = int((y0 - m) // cell), int((y1 + m) // cell)
    if (cx1 - cx0 + 1) * (cy1 - cy0 + 1) <= live:
        out: list = []
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                bucket = grid.get((cx, cy))
                if bucket:
                    out.extend(bucket)
        return out
    return [en for bucket in grid.values() for en in bucket]


def _ray_wall_dist(world, x: float, y: float, angle: float, dist: float) -> float:
    """Distance along a ray until it hits a wall, capped by ``dist`` (px).

    Steps from the muzzle with a step much smaller than the entity radius
    (4 px) and includes points closer than 8 px from the muzzle, so a wall
    right in front of the barrel blocks the shot.  ``cos``/``sin`` of the
    ray angle are hoisted out of the step loop: they are loop-invariant, and
    a 900 px ray took 225 x 2 trig calls per pellet for the same values.
    """
    step = 4.0
    nx = x
    ny = y
    travelled = 0.0
    ca = math.cos(angle)
    sa = math.sin(angle)
    while travelled < dist:
        travelled += step
        nx = x + ca * travelled
        ny = y + sa * travelled
        if world.is_wall_px(nx, ny):
            return travelled
    return dist


def _ray_circle_hit(
    x: float, y: float, angle: float,
    cx: float, cy: float, r: float, max_dist: float,
) -> float | None:
    """Nearest positive distance along the ray entering a circle, or None.

    ``r`` is the target radius (ENEMY_RADIUS / PLAYER_RADIUS).  Handles
    point-blank shots (origin on/inside the circle -> t=0) and tangential
    hits; a target behind the muzzle or farther than ``max_dist`` is a miss.
    """
    dx = cx - x
    dy = cy - y
    dist2 = dx * dx + dy * dy
    proj = dx * math.cos(angle) + dy * math.sin(angle)
    if proj < 0.0:
        # Behind the muzzle: only a hit when the circle already overlaps it.
        return 0.0 if dist2 <= r * r else None
    lateral2 = dist2 - proj * proj
    if lateral2 > r * r:
        return None
    half = math.sqrt(max(0.0, r * r - lateral2))
    t = proj - half
    if t < 0.0:
        t = 0.0
    if t > max_dist:
        return None
    return t


def _angle_diff(a: float, b: float) -> float:
    d = (a - b) % (2 * math.pi)
    if d > math.pi:
        d -= 2 * math.pi
    return abs(d)


def _damage_enemy(world, en, dmg: int,
                  shooter_eid: int | None = None) -> None:
    """Damage an enemy and credit the shooter's stats.

    ``shooter_eid`` is the player entity that fired, or None for infighting
    (an enemy projectile hitting another enemy) -- only player damage is
    attributed, so an infighting kill is nobody's frag.
    """
    if not en['a'] or en['hp'] <= 0:
        return
    en['hp'] = max(0, en['hp'] - dmg)
    en['d_flash'] = 5
    if shooter_eid is not None:
        world.note_damage_dealt(shooter_eid, dmg)
    if en['hp'] <= 0:
        world.kill_enemy(en)
        if shooter_eid is not None:
            world.note_kill(shooter_eid)


def _damage_player(world, target_eid: int, dmg: int,
                   source_eid: int | None = None) -> None:
    world.damage_player(target_eid, dmg, source_eid)


def _hitscan_players(world, shooter_eid: int, x: float, y: float,
                     angle: float, dmg: int, dist: float) -> bool:
    """PvP only: resolve a hitscan ray against other players (ray-circle)."""
    best_t: float | None = None
    best_eid: int | None = None
    for eid, comps in world.entities.query(Position, Health, Player):
        if eid == shooter_eid:
            continue
        pos: Position = comps[Position]
        hp: Health = comps[Health]
        if hp.value <= 0:
            continue
        t = _ray_circle_hit(x, y, angle, pos.x, pos.y, PLAYER_RADIUS, dist)
        if t is None:
            continue
        if best_t is None or t < best_t:
            best_t = t
            best_eid = eid
    if best_eid is None:
        return False
    _damage_player(world, best_eid, dmg, source_eid=shooter_eid)
    pos = world.entities.get_component(best_eid, Position)
    world.impact(pos.x, pos.y, 'enemy')
    return True


def handle_shoot(world, entity_id: int) -> FireResult:
    """Resolve one fire intent and report the decision.

    Every early exit returns a refusal instead of ``None``: an intent that the
    server drops silently is what the client cannot tell apart from a lost
    packet, and it is what made "every other click does nothing" invisible.
    """
    pos = world.entities.get_component(entity_id, Position)
    rot = world.entities.get_component(entity_id, Rotation)
    wp = world.entities.get_component(entity_id, WeaponComponent)
    pl = world.entities.get_component(entity_id, Player)
    if not pos or not rot or not wp or not pl:
        return FireResult(False, 'noop', 0)

    if wp.fire_cooldown > 0:
        # Refused, but not silent: the client learns the weapon is still hot
        # and cuts its predicted recoil instead of keeping a phantom shot.
        return FireResult(False, 'cooldown', wp.fire_cooldown)
    wdef = WPN[wp.current]

    # BERSERK is read once, here, and both of its effects hang off it: it doubles
    # damage and makes shots free. Reading it at each of the two places it
    # applies would be two chances to apply one of them and forget the other.
    pu = world.entities.get_component(entity_id, Powerup)
    berserk = pu is not None and pu.kind == 'BERSERK'
    dmg_mult = BERSERK_DMG_MULT if berserk else 1

    # melee weapon (chainsaw)
    if wdef.get('melee'):
        wp.fire_cooldown = wpn_cooldown_ticks(wdef['f'])
        pl.state = 2
        world.note_shot_fired(entity_id)
        if _fire_melee(world, entity_id, pos, rot, dmg_mult):
            world.note_shot_hit(entity_id)
        return FireResult(True, '', wp.fire_cooldown)

    # ammo
    ammo_key = wdef['a']
    cost = wdef.get('cost', 1)
    if ammo_key:
        cur = wp.ammo.get(ammo_key, 0)
        if cur < cost:
            # No ammo: no shot, and no state = 2 (that flag means "a shot
            # happened").  The cooldown still stands so a held fire button
            # cannot spam dry-fire intents at the input rate.
            wp.fire_cooldown = wpn_cooldown_ticks(wdef['f'])
            return FireResult(False, 'no_ammo', wp.fire_cooldown)
        # BERSERK skips only the deduction, never the check above: an empty
        # magazine has to keep refusing, or a berserk player would dry-fire
        # forever with no cost to stop it.
        if not berserk:
            wp.ammo[ammo_key] = cur - cost

    wp.fire_cooldown = wpn_cooldown_ticks(wdef['f'])
    pl.state = 2

    x, y = pos.x, pos.y
    base_angle = rot.angle

    pellets = wdef.get('pellets', 1)
    cone = wdef.get('cone', 0.0)
    dmg = wdef['d'] * dmg_mult
    rng = wdef['r'] if wdef['r'] else 100000

    # Projectile weapon: spawn a server-side player ball (plasma for RIFLE).
    # Damage is applied on travel, not instantly.
    if wdef.get('proj'):
        active = sum(1 for b in world.bullets if b.get('from_player'))
        if active >= MAX_PLAYER_PROJ:
            return FireResult(False, 'proj_cap', wp.fire_cooldown)
        spd = wdef.get('proj_spd', PLAYER_PROJ_SPEED)
        world.bullets.append({
            'x': x,
            'y': y,
            'vx': math.cos(base_angle) * spd,
            'vy': math.sin(base_angle) * spd,
            'dmg': dmg,
            'dist': 0.0,
            'max_dist': wdef.get('proj_max_dist', PLAYER_PROJ_MAX_DIST),
            'col': wdef['c'],
            'src': entity_id,
            'from_player': True,
        })
        # Counted at spawn: the shot happened even if the ball never lands.
        # The hit is recorded later, in GameWorld._resolve_player_ball.
        world.note_shot_fired(entity_id)
        return FireResult(True, '', wp.fire_cooldown)

    # One spatial grid for the WHOLE shot, built after the melee/projectile
    # early exits so those shots never pay for it.  FIRE is handled outside the
    # AI tick (GameServer._on_player_fire), so the AI grids inside
    # GameWorld._update_enemies are locals of a different phase and cannot be
    # reused here; this one is short-lived and covers every pellet below.
    # Same builder as the AI (GameWorld._enemy_grid -> live enemies only), and
    # the per-candidate `a`/`hp` re-check also drops anything killed by an
    # earlier pellet of this same volley.
    grid = world._enemy_grid(world.enemies, _SHOT_GRID_CELL)
    live = sum(len(b) for b in grid.values())

    world.note_shot_fired(entity_id)
    any_hit = False
    for _ in range(pellets):
        angle = base_angle + (random.uniform(-cone, cone) if cone else 0.0)
        ca = math.cos(angle)
        sa = math.sin(angle)
        # Wall distance along the same ray (4 px steps from the muzzle, incl.
        # points closer than 8 px). The nearest of "wall" and "entity" wins.
        end_dist = _ray_wall_dist(world, x, y, angle, rng)

        # PvP players first
        if world.mode == 'pvp':
            if _hitscan_players(world, entity_id, x, y, angle, dmg, end_dist):
                any_hit = True
                continue

        # Enemies: nearest positive ray-circle intersection, with the wall as
        # an occluder (entity behind the wall is not hit; wall wins ties).
        best = None
        best_t = end_dist
        for e in _ray_candidates(grid, _SHOT_GRID_CELL, live, x, y, ca, sa,
                                 end_dist):
            if not e['a'] or e['hp'] <= 0:
                continue
            t = _ray_circle_hit(x, y, angle, e['x'], e['y'], ENEMY_RADIUS, rng)
            if t is None or t >= best_t:
                continue
            best = e
            best_t = t
        if best is not None:
            _damage_enemy(world, best, dmg, entity_id)
            world.impact(best['x'], best['y'], 'enemy')
            any_hit = True
        elif end_dist < rng:
            # ray terminated at a wall before max range -> wall tracer
            wx = x + ca * end_dist
            wy = y + sa * end_dist
            world.impact(wx, wy, 'wall')

    # Accuracy is hits/shots at the volley level: a shotgun blast that lands any
    # pellet counts as one hit shot, not seven (the shot is the click).
    if any_hit:
        world.note_shot_hit(entity_id)
    return FireResult(True, '', wp.fire_cooldown)


def _fire_melee(world, entity_id: int, pos: Position, rot: Rotation,
                dmg_mult: int = 1) -> bool:
    """Resolve a melee swing; returns True when it connected (for accuracy)."""
    x, y = pos.x, pos.y
    best = None
    best_d = MELEE_RANGE
    for e in world.enemies:
        if not e['a'] or e['hp'] <= 0:
            continue
        d = math.hypot(e['x'] - x, e['y'] - y)
        if d <= best_d:
            ang = math.atan2(e['y'] - y, e['x'] - x)
            if _angle_diff(ang, rot.angle) <= MELEE_ANGLE_THRESHOLD:
                best = e
                best_d = d
    if best is not None:
        _damage_enemy(world, best, PLAYER_MELEE_DMG * dmg_mult, entity_id)
        return True
    return False