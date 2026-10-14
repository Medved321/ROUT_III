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


def _ray_wall_dist(world, x: float, y: float, angle: float, dist: float) -> float:
    """Distance along a ray until it hits a wall, capped by ``dist`` (px).

    Steps from the muzzle with a step much smaller than the entity radius
    (4 px) and includes points closer than 8 px from the muzzle, so a wall
    right in front of the barrel blocks the shot.
    """
    step = 4.0
    nx = x
    ny = y
    travelled = 0.0
    while travelled < dist:
        travelled += step
        nx = x + math.cos(angle) * travelled
        ny = y + math.sin(angle) * travelled
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


def _damage_enemy(world, en, dmg: int, shooter_name: str = '') -> None:
    if not en['a'] or en['hp'] <= 0:
        return
    en['hp'] = max(0, en['hp'] - dmg)
    en['d_flash'] = 5
    if en['hp'] <= 0:
        world.kill_enemy(en, shooter_name)


def _damage_player(world, target_eid: int, dmg: int) -> None:
    world.damage_player(target_eid, dmg)


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
    _damage_player(world, best_eid, dmg)
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
        _fire_melee(world, entity_id, pos, rot, dmg_mult)
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
        return FireResult(True, '', wp.fire_cooldown)

    for _ in range(pellets):
        angle = base_angle + (random.uniform(-cone, cone) if cone else 0.0)
        # Wall distance along the same ray (4 px steps from the muzzle, incl.
        # points closer than 8 px). The nearest of "wall" and "entity" wins.
        end_dist = _ray_wall_dist(world, x, y, angle, rng)

        # PvP players first
        if world.mode == 'pvp':
            if _hitscan_players(world, entity_id, x, y, angle, dmg, end_dist):
                continue

        # Enemies: nearest positive ray-circle intersection, with the wall as
        # an occluder (entity behind the wall is not hit; wall wins ties).
        best = None
        best_t = end_dist
        for e in world.enemies:
            if not e['a'] or e['hp'] <= 0:
                continue
            t = _ray_circle_hit(x, y, angle, e['x'], e['y'], ENEMY_RADIUS, rng)
            if t is None or t >= best_t:
                continue
            best = e
            best_t = t
        if best is not None:
            _damage_enemy(world, best, dmg, pl.name)
            world.impact(best['x'], best['y'], 'enemy')
        elif end_dist < rng:
            # ray terminated at a wall before max range -> wall tracer
            wx = x + math.cos(angle) * end_dist
            wy = y + math.sin(angle) * end_dist
            world.impact(wx, wy, 'wall')

    return FireResult(True, '', wp.fire_cooldown)


def _fire_melee(world, entity_id: int, pos: Position, rot: Rotation,
                dmg_mult: int = 1) -> None:
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
        _damage_enemy(world, best, PLAYER_MELEE_DMG * dmg_mult)