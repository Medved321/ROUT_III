"""Server authoritative game world (px coordinates, ported from ROUT_II_III).

All positions are in pixel space (1 tile = 64 px, center of an empty cell
at ``tile * 64 + 32``).  Movement uses the ROUT_II_III smooth per-axis
``can_move`` sliding model instead of snapping to cell edges, which fixes
the weird player pushback in corners.
"""

from __future__ import annotations

import math
import random

from shared.components import (
    EntityManager, Position, Velocity, Rotation,
    Health, Player, Collider, WeaponComponent, Dash, Armor, Powerup, Keys
)
from shared.map_data import GameMap, TILE
from shared.sector_map import SectorMap, KEY_BITS
from shared.combat import USE_REASON_OK, USE_REASON_CODES
from server.game_data import (
    PLAYER_RADIUS, PLAYER_MAX_HP, ENEMY_DB, ENEMY_RADIUS,
    ENEMY_DETECT_RANGE, ENEMY_ALERT_AOE, ENEMY_MELEE_COOLDOWN,
    MELEE_RANGE, RANGED_DB, SPAWN_POOL, CORPSE_LIFETIME,
    PLAYER_RESPAWN_TICKS,
    TICK_RATE, FPS_60, MAX_PLAYER_PROJ, IMPACT_TTL, MAX_IMPACTS,
    BALL_SUB_STEP, INFIGHT_TICKS, MAX_MOVE_STEP_PX,
    ITEM_DB, ITEM_PICKUP_DIST,
    ARMOR_MAX, AMMO_KEYS, AMMO_MAX, WPN,
    DOOR_OPEN_TICKS, DOOR_HOLD_TICKS, DOOR_CLOSE_TICKS, DOOR_USE_DIST,
)
from server.weapons import _damage_enemy

# Reason codes for a refused "use" attempt (mirrors shared.combat).
USE_REASON_LOCKED = USE_REASON_CODES['locked']
# Radius (px) at which a point counts as being on a door segment: half a bullet
# sub-step, so a projectile stepping at BALL_SUB_STEP px cannot tunnel through a
# door's zero-thickness linedef.
DOOR_POINT_RADIUS = BALL_SUB_STEP * 0.5


def _spawn_px(spawns: list[tuple[float, float]]) -> tuple[float, float]:
    """Pick a pixel-space spawn (common for tile and sector maps)."""
    if not spawns:
        return 96.0, 96.0
    return random.choice(spawns)


class GameWorld:
    def __init__(self, mode: str = 'pve',
                 game_map: GameMap | SectorMap | None = None):
        self.entities = EntityManager()
        self.game_map = game_map if game_map is not None else GameMap()
        self.tick: int = 0
        self.mode: str = mode  # 'pve' or 'pvp'

        self.players: dict[int, int] = {}   # player_id -> entity_id
        self.spawn_points: list[tuple[float, float]] = self.game_map.spawn_px()
        self.open_spawns: list[tuple[float, float]] = self.game_map.open_points()

        # state objects for the protocol
        self.enemies: list[dict] = []
        self.bullets: list[dict] = []
        self.corpses: list[dict] = []
        self.impacts: list[dict] = []   # wall/enemy hit markers: {x, y, type, ttl}

        # respawn tracking  entity_id -> ticks remaining.  Players only: a
        # killed enemy has no timer of its own, it waits for a player's death
        # (see _reset_enemies).
        self._respawn: dict[int, int] = {}
        # Where a player joined the map.  This is the point they come back to:
        # respawning on a random `spawn_points` entry would put a respawned
        # player somewhere the camera never looked, which reads as a teleport into
        # an empty corner rather than as a return to the fight.
        self._player_start: dict[int, tuple[float, float]] = {}
        self._enemy_id_seq = 0
        self._impact_seq = 0
        # Pickups placed on the map. A taken item keeps its record and waits out
        # ``respawn`` -- dropping it would lose the position it has to come back
        # to, which is the only reason it was there.
        self.items: list[dict] = []
        self._item_id_seq = 0
        # Items are map geometry, not a wave: unlike enemies they must be there
        # before the first player joins, and they do not depend on there being
        # anyone in the party at all (a PvP party picks them up too).
        self.spawn_items_from_map()

        # Door runtime state, keyed by linedef index.  The map itself stays
        # immutable and shareable between parties: phase/progress/hold all live
        # here, never on LineDef.flags (design D2).
        self.doors: dict[int, dict] = {}
        self._blocked_doors: frozenset[int] = frozenset()
        # Rising-edge tracking for the "use" bit (input is 25 Hz, tick 20 Hz).
        self._use_held: dict[int, bool] = {}
        # Last "use" decision per player_id, for the advisory snapshot byte.
        self._use_reason: dict[int, int] = {}
        self._init_doors()

    # ------------------------------------------------------------------
    #  Player management
    # ------------------------------------------------------------------
    def add_player(self, player_id: int, name: str) -> int:
        eid = self.entities.create()
        sx, sy = _spawn_px(self.spawn_points)

        self.entities.add_component(eid, Position(sx, sy))
        self.entities.add_component(eid, Velocity(0.0, 0.0))
        self.entities.add_component(eid, Rotation(random.uniform(0, 2 * math.pi)))
        self.entities.add_component(eid, Health(PLAYER_MAX_HP, PLAYER_MAX_HP))
        self.entities.add_component(eid, Player(player_id, name, state=0))
        self.entities.add_component(eid, Collider(PLAYER_RADIUS))
        self.entities.add_component(eid, WeaponComponent())
        self.entities.add_component(eid, Dash())
        # Zeroed, not defaulted to a full kit: a player who has never seen an
        # armor pickup must not start the map wearing one.
        self.entities.add_component(eid, Armor())
        self.entities.add_component(eid, Powerup())
        # No keys at spawn: a locked door has to be found, not started behind.
        self.entities.add_component(eid, Keys())

        self.players[player_id] = eid
        self._player_start[player_id] = (sx, sy)
        return eid

    def remove_player(self, player_id: int) -> None:
        eid = self.players.pop(player_id, None)
        self._player_start.pop(player_id, None)
        self._use_reason.pop(player_id, None)
        if eid is not None:
            self.entities.destroy(eid)
            self._respawn.pop(eid, None)
            self._use_held.pop(eid, None)

    def get_player_entity(self, player_id: int) -> int | None:
        return self.players.get(player_id)

    # ------------------------------------------------------------------
    #  PvE enemy spawning (called once per wave or on game start)
    # ------------------------------------------------------------------
    def spawn_enemies(self, count: int) -> None:
        occupied = {(int(e['x']) // int(TILE), int(e['y']) // int(TILE))
                    for e in self.enemies}
        free = [p for p in self.open_spawns
                if (int(p[0]) // TILE, int(p[1]) // TILE) not in occupied]
        if not free:
            free = list(self.open_spawns)
        for _ in range(count):
            if not free:
                break
            sx, sy = random.choice(free)
            free.remove((sx, sy))
            etype = random.choice(SPAWN_POOL)
            self._add_enemy(etype, sx, sy, random.uniform(0, 2 * math.pi))

    def _add_enemy(self, etype: str, x: float, y: float, angle: float,
                   tick_rate: float = TICK_RATE) -> None:
        db = ENEMY_DB[etype]
        self._enemy_id_seq += 1
        self.enemies.append({
            'id': self._enemy_id_seq,
            'x': x,
            'y': y,
            't': etype,
            'hp': db['hp'],
            'max_hp': db['hp'],
            'spd': db['spd'] * (FPS_60 / tick_rate),
            'col': db['col'],
            'a': True,
            'dying': 0,
            'anim_frame': 0,
            'anim_state': 0,
            'stun': 0,
            'alert': False,
            'pa': angle,
            'ls': 0,
            'd_flash': 0,
            'spawn_x': x,
            'spawn_y': y,
            'spawn_pa': angle,
            'attack_from': None,
            'attack_ticks': 0,
        })

    def spawn_enemies_from_map(self) -> int:
        """Spawn every enemy placed on the map (``SectorMap.things``).

        The editor's thing palette writes the engine's enemy type name
        (``soldier``, ``imp``, …), which matches the ``ENEMY_DB`` keys, so a
        placement in the .mts file becomes exactly one server enemy at that
        point with its set facing angle.  Returns the number spawned.
        """
        things = getattr(self.game_map, 'things', None)
        if not things:
            return 0
        count = 0
        for t in things:
            itype = getattr(t, 'type', None)
            if itype not in ENEMY_DB:
                continue
            self._add_enemy(itype, float(t.x), float(t.y),
                            float(getattr(t, 'angle', 0.0)))
            count += 1
        return count

    # ------------------------------------------------------------------
    #  Item spawning (called once per wave or on game start)
    # ------------------------------------------------------------------
    def spawn_items_from_map(self) -> int:
        """Spawn every pickup placed on the map (``SectorMap.things``).

        The editor's thing palette offers items (``med``, ``amm``, ``wpn_*``,
        armor, powerups) from the same list it offers enemies from, so a .mts
        can carry both.  They are two different dictionaries: this one reads
        ``ITEM_DB``, ``spawn_enemies_from_map`` reads ``ENEMY_DB``, and each
        ignores the other's names.  Anything the editor can place and neither
        table knows is dropped -- that includes ``player1`` and ``exit``, which
        are markers and are handled by the map loader.

        Returns the number spawned.
        """
        things = getattr(self.game_map, 'things', None)
        if not things:
            return 0
        count = 0
        for t in things:
            itype = getattr(t, 'type', None)
            if itype not in ITEM_DB:
                continue
            self._item_id_seq += 1
            self.items.append({
                'id': self._item_id_seq,
                'x': float(t.x),
                'y': float(t.y),
                't': itype,
                'a': True,
            })
            count += 1
        return count

    # ------------------------------------------------------------------
    #  Doors (runtime state machine) + dynamic blocking
    # ------------------------------------------------------------------
    def _init_doors(self) -> None:
        """Build the door table from the map. No-op for maps without doors."""
        self.doors = {}
        lister = getattr(self.game_map, 'door_linedefs', None)
        if lister is None:
            return
        for li in lister():
            self.doors[li] = {
                'li': li,
                'phase': 'closed',   # closed | opening | open | closing
                'progress': 0.0,     # 0.0 shut ... 1.0 fully open
                'hold': 0,           # ticks left fully open before reclosing
                'lock': self.game_map.door_lock(li),  # None | 'red'|'yellow'|'blue'
            }
        self._refresh_blocked_doors()

    def _refresh_blocked_doors(self) -> None:
        """Recompute the dynamic block set: every door not fully open.

        A door blocks while it is shut OR moving (opening/closing): only at
        ``progress == 1.0`` is it a passable portal, so a player cannot slip
        through a half-open door and nothing gets caught as it shuts.
        """
        self._blocked_doors = frozenset(
            li for li, d in self.doors.items() if d['progress'] < 1.0)

    def _update_doors(self) -> None:
        if not self.doors:
            return
        for d in self.doors.values():
            phase = d['phase']
            if phase == 'opening':
                d['progress'] += 1.0 / DOOR_OPEN_TICKS
                if d['progress'] >= 1.0:
                    d['progress'] = 1.0
                    d['phase'] = 'open'
                    d['hold'] = DOOR_HOLD_TICKS
            elif phase == 'open':
                d['hold'] -= 1
                if d['hold'] <= 0:
                    # Never shut on an actor standing in the gap: wait another
                    # full hold window instead (Doom door behaviour).
                    if self._door_obstructed(d['li']):
                        d['hold'] = DOOR_HOLD_TICKS
                    else:
                        d['phase'] = 'closing'
            elif phase == 'closing':
                if self._door_obstructed(d['li']):
                    d['phase'] = 'opening'
                else:
                    d['progress'] -= 1.0 / DOOR_CLOSE_TICKS
                    if d['progress'] <= 0.0:
                        d['progress'] = 0.0
                        d['phase'] = 'closed'
            # 'closed': nothing to do.
        self._refresh_blocked_doors()

    def _door_obstructed(self, li: int) -> bool:
        """True when a live actor sits in the door's gap (blocks closing)."""
        blocked = {li}
        door_hit = getattr(self.game_map, 'door_hit_px', None)
        if door_hit is None:
            return False
        for _pid, eid in self.players.items():
            pos = self.entities.get_component(eid, Position)
            hp = self.entities.get_component(eid, Health)
            if pos is not None and hp is not None and hp.value > 0:
                if door_hit(pos.x, pos.y, PLAYER_RADIUS + 2, blocked):
                    return True
        for en in self.enemies:
            if en['a'] and en['hp'] > 0:
                if door_hit(en['x'], en['y'], ENEMY_RADIUS + 2, blocked):
                    return True
        return False

    def _door_closest(self, li: int, x: float, y: float) -> tuple[float, float, float]:
        """Closest point on door linedef ``li`` to (x, y), and its distance."""
        ld = self.game_map.linedefs[li]
        verts = self.game_map.verts
        v1 = verts[ld.v1]
        v2 = verts[ld.v2]
        dx = v2.x - v1.x
        dy = v2.y - v1.y
        l2 = dx * dx + dy * dy
        if l2 <= 0.0:
            return v1.x, v1.y, math.hypot(x - v1.x, y - v1.y)
        t = ((x - v1.x) * dx + (y - v1.y) * dy) / l2
        if t < 0.0:
            t = 0.0
        elif t > 1.0:
            t = 1.0
        cx = v1.x + t * dx
        cy = v1.y + t * dy
        return cx, cy, math.hypot(x - cx, y - cy)

    def _player_keys(self, eid: int) -> int:
        keys = self.entities.get_component(eid, Keys)
        return keys.mask if keys is not None else 0

    def apply_use_input(self, eid: int, pressed: bool) -> None:
        """React to the rising edge of the "use" bit (input is 25 Hz)."""
        prev = self._use_held.get(eid, False)
        self._use_held[eid] = pressed
        if pressed and not prev:
            self.try_use(eid)

    def try_use(self, eid: int) -> int:
        """Open (or re-hold) the nearest door ahead; return the reason code.

        Picks the closest door whose nearest point is within ``DOOR_USE_DIST``
        of the player AND lies in front of them (positive dot with facing),
        then checks its lock against the player's key mask.  A refused attempt
        returns a reason (advisory, shown to the client) and changes nothing.
        """
        pl = self.entities.get_component(eid, Player)
        pos = self.entities.get_component(eid, Position)
        rot = self.entities.get_component(eid, Rotation)
        if pl is None or pos is None or rot is None:
            return USE_REASON_OK
        hp = self.entities.get_component(eid, Health)
        if hp is not None and hp.value <= 0:
            return USE_REASON_OK
        if not self.doors:
            return USE_REASON_OK

        fx = math.cos(rot.angle)
        fy = math.sin(rot.angle)
        best = None
        best_d = DOOR_USE_DIST
        for li in self.doors:
            cx, cy, d = self._door_closest(li, pos.x, pos.y)
            if d >= best_d:
                continue
            # In front: the door's nearest point is ahead of the player.
            if (cx - pos.x) * fx + (cy - pos.y) * fy <= 0.0:
                continue
            best = li
            best_d = d
        if best is None:
            return USE_REASON_OK

        d = self.doors[best]
        lock = d['lock']
        if lock is not None:
            bit = KEY_BITS.get(lock, 0)
            if not (self._player_keys(eid) & bit):
                self._use_reason[pl.player_id] = USE_REASON_LOCKED
                return USE_REASON_LOCKED
        # Open it, or restart the hold if it is already open/opening.
        if d['phase'] == 'open':
            d['hold'] = DOOR_HOLD_TICKS
        elif d['phase'] != 'opening':
            d['phase'] = 'opening'
        self._use_reason[pl.player_id] = USE_REASON_OK
        self._refresh_blocked_doors()
        return USE_REASON_OK

    def get_use_reason(self, player_id: int) -> int:
        return self._use_reason.get(player_id, USE_REASON_OK)

    def get_door_states(self) -> list[dict]:
        """Sparse snapshot: only NON-closed doors (a silent door = closed)."""
        return [
            {'li': li, 'progress': int(round(d['progress'] * 255))}
            for li, d in self.doors.items() if d['phase'] != 'closed'
        ]

    # --- dynamic-blocking wrappers (design D3: one passability rule) --------
    def can_move_px(self, x: float, y: float, r: float) -> bool:
        if self._blocked_doors:
            return self.game_map.can_move_px(x, y, r, self._blocked_doors)
        return self.game_map.can_move_px(x, y, r)

    def has_los_px(self, x1: float, y1: float, x2: float, y2: float) -> bool:
        if self._blocked_doors:
            return self.game_map.has_los_px(x1, y1, x2, y2, self._blocked_doors)
        return self.game_map.has_los_px(x1, y1, x2, y2)

    def is_wall_px(self, x: float, y: float) -> bool:
        """Solid for projectiles/hitscans: void OR a shut/moving door plane.

        ``is_wall_px`` only sees void, so a point just past a closed door lies in
        the neighbouring (walkable) sector and would let a bullet through; the
        door segment is tested explicitly.
        """
        if self.game_map.is_wall_px(x, y):
            return True
        if self._blocked_doors:
            door_hit = getattr(self.game_map, 'door_hit_px', None)
            if door_hit is not None:
                return door_hit(x, y, DOOR_POINT_RADIUS, self._blocked_doors)
        return False

    # ------------------------------------------------------------------
    #  Main tick
    # ------------------------------------------------------------------
    def update(self) -> None:
        self.tick += 1
        # Doors first: their open/shut state decides what movement, sight,
        # hitscans and projectiles may pass this tick.
        self._update_doors()
        self._apply_movement()
        # After the movement that consumed this tick's dash speed, so a dash
        # grants exactly DASH_TICKS ticks of boosted movement.
        self._update_dash()
        self._update_fire_cooldowns()
        self._update_shooting_flags()
        self._update_enemies()
        self._update_bullets()
        self._update_impacts()
        self._update_corpses()
        self._update_powerups()
        self._update_items()
        self._update_respawns()
        # A killed enemy is NOT dropped from the list: its record carries
        # spawn_x/spawn_y -- the home the wave reset needs -- and it is part of
        # the wave until a player's death brings the whole level back.  The
        # snapshot hides it meanwhile (get_enemy_states reports live entries
        # and death animations only).

    # ------------------------------------------------------------------
    #  Movement (smooth per-axis sliding, ROUT_II_III model)
    # ------------------------------------------------------------------
    def _apply_movement(self) -> None:
        for _eid, comps in self.entities.query(Position, Velocity, Collider):
            pos: Position = comps[Position]
            vel: Velocity = comps[Velocity]
            r = comps[Collider].radius
            # can_move_px() only tests the destination, so a dash step could
            # land past a solid sector linedef (zero thickness).  Steps within
            # MAX_MOVE_STEP_PX -- i.e. every ordinary walking step -- take the
            # single-check fast path and pay no extra cost; longer steps are
            # split into equal sub-steps and stop at the first blocked one.
            if vel.dx != 0.0:
                self._advance_axis(pos, vel.dx, r, 0)
            if vel.dy != 0.0:
                self._advance_axis(pos, vel.dy, r, 1)
            vel.dx = 0.0
            vel.dy = 0.0

    def _advance_axis(self, pos: Position, step: float, r: float,
                      axis: int) -> None:
        """Advance ``pos`` along one axis by ``step``, stopping at the first
        blocked sub-step.  Axes stay independent, so wall sliding is preserved.
        """
        cur = pos.x if axis == 0 else pos.y
        a = abs(step)
        if a <= MAX_MOVE_STEP_PX:
            if self._step_free(pos, cur + step, r, axis):
                self._set_axis(pos, cur + step, axis)
            return
        n = int(math.ceil(a / MAX_MOVE_STEP_PX))
        sub = step / n
        start = cur
        for i in range(1, n + 1):
            nxt = start + sub * i
            if not self._step_free(pos, nxt, r, axis):
                break
            self._set_axis(pos, nxt, axis)
            cur = nxt

    def _step_free(self, pos: Position, nxt: float, r: float, axis: int) -> bool:
        if axis == 0:
            return self.can_move_px(nxt, pos.y, r)
        return self.can_move_px(pos.x, nxt, r)

    @staticmethod
    def _set_axis(pos: Position, value: float, axis: int) -> None:
        if axis == 0:
            pos.x = value
        else:
            pos.y = value

    # ------------------------------------------------------------------
    #  Dash timers
    # ------------------------------------------------------------------
    def _update_dash(self) -> None:
        for _eid, comps in self.entities.query(Dash):
            d: Dash = comps[Dash]
            if d.ticks > 0:
                d.ticks -= 1
            if d.cooldown > 0:
                d.cooldown -= 1

    # ------------------------------------------------------------------
    #  Fire cooldowns
    # ------------------------------------------------------------------
    def _update_fire_cooldowns(self) -> None:
        for _eid, comps in self.entities.query(WeaponComponent):
            wp: WeaponComponent = comps[WeaponComponent]
            if wp.fire_cooldown > 0:
                wp.fire_cooldown -= 1

    def _update_shooting_flags(self) -> None:
        """Clear the one-tick `state = 2` shooting pulse.

        handle_shoot() raises it when a shot really happened; the input channel
        no longer publishes it (PLAYER_INPUT carries only the held button), so
        without this the flag would stick and every client would show a
        permanent muzzle flash. Only `2` is touched — dash (`3`) is derived
        from the dash component on every input packet and must survive.
        """
        for _eid, comps in self.entities.query(Player):
            pl: Player = comps[Player]
            if pl.state == 2:
                pl.state = 0

    # ------------------------------------------------------------------
    #  Enemy AI (simplified ROUT_II_III port, px space)
    # ------------------------------------------------------------------
    def _update_enemies(self) -> None:
        player_positions = []
        for _pid, eid in self.players.items():
            pos = self.entities.get_component(eid, Position)
            hp = self.entities.get_component(eid, Health)
            if pos and hp and hp.value > 0:
                player_positions.append((eid, pos.x, pos.y))

        for en in self.enemies:
            if not en['a'] or en['hp'] <= 0:
                continue
            if en['dying'] > 0:
                en['dying'] -= 1
                continue
            if en['stun'] > 0:
                en['stun'] -= 1
                continue

            if en['d_flash'] > 0:
                en['d_flash'] -= 1

            ex, ey = en['x'], en['y']
            etype = en['t']
            espd = en['spd']

            # --- infighting target (retaliate against the attacker) --------
            target_enemy = None
            attacker_id = en.get('attack_from')
            if attacker_id is not None and en.get('attack_ticks', 0) > 0:
                for other in self.enemies:
                    if other['id'] == attacker_id:
                        if (other['a'] and other['hp'] > 0
                                and math.hypot(other['x'] - ex, other['y'] - ey) < ENEMY_DETECT_RANGE
                                and self.has_los_px(ex, ey, other['x'], other['y'])):
                            target_enemy = other
                        break
                if target_enemy is not None:
                    en['attack_ticks'] -= 1
            if attacker_id is not None and target_enemy is None:
                en['attack_from'] = None
                en['attack_ticks'] = 0

            # --- target selection (nearest player with LOS) ----------------
            target_eid, tx, ty = None, None, None
            if target_enemy is None:
                best = ENEMY_DETECT_RANGE
                for peid, px, py in player_positions:
                    dst = math.hypot(px - ex, py - ey)
                    if dst < best and self.has_los_px(ex, ey, px, py):
                        best = dst
                        target_eid, tx, ty = peid, px, py
            else:
                tx, ty = target_enemy['x'], target_enemy['y']

            # --- alert nearby sleeping enemies ----------------------------
            if target_eid is not None or target_enemy is not None:
                en['alert'] = True
                for other in self.enemies:
                    if other is en or not other['a'] or other.get('alert'):
                        continue
                    if math.hypot(other['x'] - ex, other['y'] - ey) < ENEMY_ALERT_AOE:
                        other['alert'] = True

            if not en.get('alert'):
                continue

            # --- separation from other enemies ----------------------------
            sx, sy = 0.0, 0.0
            for other in self.enemies:
                if other is en or not other['a']:
                    continue
                dx = ex - other['x']
                dy = ey - other['y']
                d = math.hypot(dx, dy)
                if 0 < d < ENEMY_RADIUS * 2:
                    sx += dx / d
                    sy += dy / d
            if sx or sy:
                nx = ex + sx * 0.8
                ny = ey + sy * 0.8
                if self.can_move_px(nx, ny, ENEMY_RADIUS):
                    en['x'] = nx
                    en['y'] = ny

            # --- movement toward target / wander --------------------------
            new_pa = en['pa']
            if tx is not None:
                new_pa = math.atan2(ty - ey, tx - ex)

            move_x = math.cos(new_pa) * espd
            move_y = math.sin(new_pa) * espd

            new_x = en['x'] + move_x
            new_y = en['y'] + move_y
            if self.can_move_px(new_x, en['y'], ENEMY_RADIUS):
                en['x'] = new_x
            if self.can_move_px(en['x'], new_y, ENEMY_RADIUS):
                en['y'] = new_y

            en['pa'] = new_pa

            # --- melee attack (if close) -----------------------------------
            db = ENEMY_DB[etype]
            if db.get('melee') and tx is not None:
                dst = math.hypot(tx - ex, ty - ey)
                if dst < MELEE_RANGE and self.tick - en['ls'] > ENEMY_MELEE_COOLDOWN * TICK_RATE / 1000:
                    if target_enemy is not None:
                        _damage_enemy(self, target_enemy, db['dmg'])
                        self._register_enemy_attack(target_enemy, en['id'])
                    else:
                        self.damage_player(target_eid, db['dmg'])
                    en['ls'] = self.tick

            # --- ranged attack (projectile) --------------------------------
            rdb = RANGED_DB.get(etype)
            if rdb and tx is not None:
                dst = math.hypot(tx - ex, ty - ey)
                if dst < ENEMY_DETECT_RANGE and self.tick - en['ls'] > rdb['cd'] * TICK_RATE / 1000:
                    if self.has_los_px(ex, ey, tx, ty):
                        self._fire_enemy_proj(en, rdb, tx, ty)
                        en['ls'] = self.tick

    def _fire_enemy_proj(self, en: dict, rdb: dict, tx: float, ty: float) -> None:
        dx = tx - en['x']
        dy = ty - en['y']
        d = math.hypot(dx, dy)
        if d < 1:
            return
        base_angle = math.atan2(dy, dx)
        cone = rdb.get('cone', 0.06)
        for _ in range(rdb.get('n', 1)):
            angle = base_angle + random.uniform(-cone, cone)
            spd = rdb['spd']
            self.bullets.append({
                'x': en['x'] + math.cos(angle) * 20,
                'y': en['y'] + math.sin(angle) * 20,
                'vx': math.cos(angle) * spd,
                'vy': math.sin(angle) * spd,
                'dmg': rdb['dmg'],
                'dist': 0.0,
                'max_dist': 1200,
                'col': en['col'],
                'src': en['id'],
            })

    # ------------------------------------------------------------------
    #  Bullets
    # ------------------------------------------------------------------
    def _update_bullets(self) -> None:
        alive = []
        for b in self.bullets:
            if not b.get('spawn_checked'):
                b['spawn_checked'] = True
                if b['dist'] > b.get('max_dist', 1200):
                    continue
                if self.is_wall_px(b['x'], b['y']):
                    continue
                hit = (self._resolve_player_ball(b) if b.get('from_player')
                       else self._resolve_enemy_ball(b))
                if hit:
                    continue
            length = math.hypot(b['vx'], b['vy'])
            if length <= 0:
                continue
            steps = max(1, math.ceil(length / BALL_SUB_STEP))
            sx = b['vx'] / steps
            sy = b['vy'] / steps
            step_dist = length / steps
            stopped = False
            for _ in range(steps):
                b['x'] += sx
                b['y'] += sy
                b['dist'] += step_dist
                if b['dist'] > b.get('max_dist', 1200):
                    stopped = True
                    break
                if self.is_wall_px(b['x'], b['y']):
                    stopped = True
                    break
                hit = (self._resolve_player_ball(b) if b.get('from_player')
                       else self._resolve_enemy_ball(b))
                if hit:
                    stopped = True
                    break
            if not stopped:
                alive.append(b)
        # budget guard: keep player projectiles under the cap (drop oldest)
        over = sum(1 for b in alive if b.get('from_player')) - MAX_PLAYER_PROJ
        if over > 0:
            trimmed = []
            dropped = 0
            for b in alive:
                if b.get('from_player') and dropped < over:
                    dropped += 1
                    continue
                trimmed.append(b)
            alive = trimmed
        self.bullets = alive

    def _resolve_enemy_ball(self, b: dict) -> bool:
        """Enemy projectile -> player or (infighting) another enemy hit."""
        for _pid, eid in self.players.items():
            pos = self.entities.get_component(eid, Position)
            hp = self.entities.get_component(eid, Health)
            if pos and hp and hp.value > 0:
                if math.hypot(pos.x - b['x'], pos.y - b['y']) < PLAYER_RADIUS:
                    self.damage_player(eid, b['dmg'])
                    return True
        src = b.get('src')
        for e in self.enemies:
            if not e['a'] or e['hp'] <= 0 or e['id'] == src:
                continue
            if math.hypot(e['x'] - b['x'], e['y'] - b['y']) < ENEMY_RADIUS + 4:
                _damage_enemy(self, e, b['dmg'])
                self._register_enemy_attack(e, src)
                return True
        return False

    def _register_enemy_attack(self, target: dict, attacker_id: int) -> None:
        """Infighting bookkeeping: retaliate against the attacker, alert the AOE."""
        target['attack_from'] = attacker_id
        target['attack_ticks'] = INFIGHT_TICKS
        target['alert'] = True
        for other in self.enemies:
            if other is target or not other['a'] or other.get('alert'):
                continue
            if math.hypot(other['x'] - target['x'], other['y'] - target['y']) < ENEMY_ALERT_AOE:
                other['alert'] = True

    def _resolve_player_ball(self, b: dict) -> bool:
        """Player projectile -> enemy or (PvP) other player hit."""
        for e in self.enemies:
            if not e['a'] or e['hp'] <= 0:
                continue
            if math.hypot(e['x'] - b['x'], e['y'] - b['y']) < ENEMY_RADIUS + 4:
                _damage_enemy(self, e, b['dmg'])
                return True
        src = b.get('src')
        for _pid, eid in self.players.items():
            if eid == src:
                continue
            pos = self.entities.get_component(eid, Position)
            hp = self.entities.get_component(eid, Health)
            if pos and hp and hp.value > 0:
                if math.hypot(pos.x - b['x'], pos.y - b['y']) < PLAYER_RADIUS:
                    self.damage_player(eid, b['dmg'])
                    return True
        return False

    # ------------------------------------------------------------------
    #  Corpses
    # ------------------------------------------------------------------
    def _update_corpses(self) -> None:
        alive = []
        for c in self.corpses:
            c['time'] -= 1
            if c['time'] > 0:
                alive.append(c)
        self.corpses = alive

    def impact(self, x: float, y: float, itype: str) -> None:
        """Record a short-lived impact marker (wall/enemy hit tracer)."""
        if len(self.impacts) >= MAX_IMPACTS:
            self.impacts.pop(0)
        self._impact_seq = (self._impact_seq + 1) & 0xFFFFFFFF
        self.impacts.append({
            'x': x, 'y': y, 'type': itype,
            'id': self._impact_seq, 'ttl': IMPACT_TTL,
        })

    def _update_impacts(self) -> None:
        alive = []
        for im in self.impacts:
            im['ttl'] -= 1
            if im['ttl'] > 0:
                alive.append(im)
        self.impacts = alive[-MAX_IMPACTS:]

    def kill_enemy(self, en: dict, killer: str = '') -> None:
        en['a'] = False
        en['hp'] = 0
        # No timer here on purpose: a dead enemy waits for a player's death,
        # which restarts the whole wave (_reset_enemies).  The record stays in
        # self.enemies -- it carries the spawn point the reset teleports back
        # to -- and the snapshot hides it meanwhile (get_enemy_states only
        # reports active entries).
        self.corpses.append({
            'eid': en['id'],
            'x': en['x'], 'y': en['y'],
            't': en['t'],
            'col': en['col'],
            'time': CORPSE_LIFETIME,
        })

    # ------------------------------------------------------------------
    #  Items
    # ------------------------------------------------------------------
    def _update_powerups(self) -> None:
        """Expire timed buffs.  Runs before pickups, so a buff that runs out
        this tick cannot be re-applied by an item in the same tick and then
        silently last another full duration."""
        for _eid, comps in self.entities.query(Powerup):
            pu: Powerup = comps[Powerup]
            if not pu.kind:
                continue
            pu.ticks -= 1
            if pu.ticks <= 0:
                pu.kind = ""
                pu.ticks = 0

    def _update_items(self) -> None:
        """Hand items to whoever walked over one.

        There is no respawn timer: a taken item stays gone until the wave
        restarts with the player's next life (`_reset_items`).
        """
        for it in self.items:
            if not it['a']:
                continue
            for _pid, eid in self.players.items():
                pos = self.entities.get_component(eid, Position)
                hp = self.entities.get_component(eid, Health)
                # A dead player does not loot: it would be picked up by a body
                # lying on the item and then vanish on respawn.
                if pos is None or hp is None or hp.value <= 0:
                    continue
                if math.hypot(pos.x - it['x'], pos.y - it['y']) > ITEM_PICKUP_DIST:
                    continue
                # True = the item left the floor.  False = refused, and the
                # scan goes on: a player whose pockets are full must not become
                # a wall standing on loot that the next person in range can
                # actually use.
                if self._apply_item(it, eid):
                    break

    def _item_state(self, eid: int) -> tuple:
        """Snapshot of every player field an item effect is allowed to write.

        The refusal rule ("take it only if something would change") is decided
        by comparing this before/after, so the snapshot and ``_apply_item``
        have to grow together: a field written by an effect but missing here is
        a change nobody can see, and the item would be refused while still
        having done its job.
        """
        hp = self.entities.get_component(eid, Health)
        wp = self.entities.get_component(eid, WeaponComponent)
        arm = self.entities.get_component(eid, Armor)
        pu = self.entities.get_component(eid, Powerup)
        keys = self.entities.get_component(eid, Keys)
        return (
            (hp.value, hp.max_value) if hp is not None else None,
            (dict(wp.ammo), frozenset(wp.owned)) if wp is not None else None,
            (arm.value, arm.max_value, arm.kind) if arm is not None else None,
            (pu.kind, pu.ticks) if pu is not None else None,
            keys.mask if keys is not None else None,
        )

    @staticmethod
    def _state_changed(before: tuple, after: tuple) -> bool:
        """Did anything the player would notice change between the two?

        ``armor.kind`` is deliberately not counted: it is the HUD colour, and a
        full-plate player stepping on a blue pickup gets nothing but a
        repaint.  The refusal rule is about the numbers a fight is won with
        (HP, reserve, protection, owned guns, buff clock) -- so ``_apply_item``
        has to put the colour back when it refuses.
        """
        (b_hp, b_wp, b_arm, b_pu, b_keys) = before
        (a_hp, a_wp, a_arm, a_pu, a_keys) = after
        if b_hp != a_hp or b_wp != a_wp or b_pu != a_pu or b_keys != a_keys:
            return True
        if b_arm is None or a_arm is None:
            return b_arm != a_arm
        return (b_arm[0], b_arm[1]) != (a_arm[0], a_arm[1])

    def _restore_item_state(self, eid: int, snap: tuple) -> None:
        """Put back a snapshot taken by ``_item_state`` verbatim."""
        (hp_snap, wp_snap, arm_snap, pu_snap, keys_snap) = snap
        if hp_snap is not None:
            hp = self.entities.get_component(eid, Health)
            if hp is not None:
                hp.value, hp.max_value = hp_snap
        if wp_snap is not None:
            wp = self.entities.get_component(eid, WeaponComponent)
            if wp is not None:
                # Mutated in place: the component may be referenced elsewhere
                # (the HUD reads the same dict), and a copy would silently
                # detach those readers.
                wp.ammo.clear()
                wp.ammo.update(wp_snap[0])
                wp.owned.clear()
                wp.owned.update(wp_snap[1])
        if arm_snap is not None:
            arm = self.entities.get_component(eid, Armor)
            if arm is not None:
                arm.value, arm.max_value, arm.kind = arm_snap
        if pu_snap is not None:
            pu = self.entities.get_component(eid, Powerup)
            if pu is not None:
                pu.kind, pu.ticks = pu_snap
        if keys_snap is not None:
            keys = self.entities.get_component(eid, Keys)
            if keys is not None:
                keys.mask = keys_snap

    def _apply_item(self, it: dict, eid: int) -> bool:
        """Apply one item to a player; return True only if it left the floor.

        The item is taken exactly when it changes something.  A medkit at full
        HP, an ammo box over a full reserve, green armor over full plate or a
        gun the player already owns with a full stack all change nothing, so
        they are refused: the player gains nothing by swallowing them and
        everyone else loses the loot.  "Nothing would change" is measured, not
        listed per category -- a new entry in ITEM_DB cannot silently bring
        the old always-take behaviour back.
        """
        spec = ITEM_DB.get(it['t'])
        if spec is None:
            # A type the server does not know (an editor-only entry): data
            # error, not full pockets, so it keeps leaving the floor as it
            # always did.
            it['a'] = False
            return True
        before = self._item_state(eid)
        kind = spec['kind']
        if kind == 'health':
            hp = self.entities.get_component(eid, Health)
            if hp is not None:
                hp.value = min(hp.max_value, hp.value + spec['hp'])
        elif kind == 'ammo':
            wp = self.entities.get_component(eid, WeaponComponent)
            if wp is not None:
                for key in AMMO_KEYS:
                    wp.ammo[key] = min(AMMO_MAX,
                                       wp.ammo.get(key, 0) + spec['amount'])
        elif kind == 'armor':
            arm = self.entities.get_component(eid, Armor)
            if arm is not None:
                arm.kind = spec['armor_type']
                arm.max_value = max(arm.max_value, spec['points'])
                arm.value = min(arm.max_value,
                                arm.value + spec['points'])
        elif kind == 'powerup':
            pu = self.entities.get_component(eid, Powerup)
            if pu is not None:
                # Refresh, never stack: a second berserk has to replace the
                # first one, or the buff would live twice as long by accident.
                pu.kind = spec['pu']
                pu.ticks = spec['ticks']
        elif kind == 'weapon':
            wp = self.entities.get_component(eid, WeaponComponent)
            if wp is not None:
                wpn = spec['wpn']
                wp.owned.add(wpn)
                ammo_key = WPN[wpn]['a']
                if ammo_key and spec['ammo']:
                    wp.ammo[ammo_key] = min(
                        AMMO_MAX, wp.ammo.get(ammo_key, 0) + spec['ammo'])
        elif kind == 'key':
            keys = self.entities.get_component(eid, Keys)
            if keys is not None:
                keys.mask |= KEY_BITS.get(spec['lock'], 0)
        if not self._state_changed(before, self._item_state(eid)):
            # Nothing to hand over: put back even the cosmetic repaint (the
            # only field the comparison ignores) and leave the item where it is
            # -- no effect and still in the snapshot.
            self._restore_item_state(eid, before)
            return False
        # A taken item stays gone until the wave restarts on the player's next
        # life (design D5): there is no per-item respawn timer, so a key and a
        # medkit are returned by exactly the same rule in `_reset_items`.
        it['a'] = False
        return True

    def get_item_states(self) -> list[dict]:
        return [
            {'id': it['id'], 'x': it['x'], 'y': it['y'], 't': it['t']}
            for it in self.items if it['a']
        ]

    # ------------------------------------------------------------------
    #  Respawn
    # ------------------------------------------------------------------
    def damage_player(self, eid: int, dmg: int) -> bool:
        """Damage a player and handle death.  Returns True if this killed.

        Single entry point for player damage: melee, enemy fireballs, friendly
        fire and hitscan all funnel through here, so "dead" means exactly one
        thing.  Four call sites used to spell it out by hand, and each copy had
        to remember both the death state and the respawn timer -- a site that
        forgot the timer left a 0 HP player wedged in the world forever.

        Armor soaks the hit first and INVULN refuses it outright, so both belong
        here rather than in each of the four callers.
        """
        pu = self.entities.get_component(eid, Powerup)
        if pu is not None and pu.kind == 'INVULN':
            return False
        arm = self.entities.get_component(eid, Armor)
        if arm is not None and arm.value > 0 and dmg > 0:
            absorbed = min(arm.value, dmg)
            arm.value -= absorbed
            dmg -= absorbed
        hp = self.entities.get_component(eid, Health)
        if hp is None or hp.value <= 0:
            return False
        hp.value = max(0, hp.value - dmg)
        if hp.value > 0:
            return False
        pl = self.entities.get_component(eid, Player)
        if pl is not None:
            pl.state = 1
            self._respawn[eid] = PLAYER_RESPAWN_TICKS
        return True

    def _update_respawns(self) -> None:
        for eid in list(self._respawn):
            self._respawn[eid] -= 1
            if self._respawn[eid] > 0:
                continue
            self._respawn.pop(eid)
            self._respawn_player(eid)

    def _respawn_player(self, eid: int) -> None:
        pos = self.entities.get_component(eid, Position)
        hp = self.entities.get_component(eid, Health)
        pl = self.entities.get_component(eid, Player)
        if not (pos and hp and pl):
            return
        # Back to the point they joined from, not a random spawn_points entry:
        # a respawn is meant to read as "regroup", and a random point can drop
        # the player into an empty corner the camera never looked at.
        start = self._player_start.get(pl.player_id)
        if start is None:
            start = (pos.x, pos.y)
        pos.x, pos.y = start
        hp.value = PLAYER_MAX_HP
        pl.state = 0
        # A player killed mid-dash would keep dashing out of the respawn point,
        # and a half-finished step would carry them off it.  Velocity, dash
        # timers and the weapon cooldown are all "state of the old life".
        vel = self.entities.get_component(eid, Velocity)
        if vel is not None:
            vel.dx = vel.dy = 0.0
        dash = self.entities.get_component(eid, Dash)
        if dash is not None:
            dash.ticks = 0
            dash.cooldown = 0
        wp = self.entities.get_component(eid, WeaponComponent)
        if wp is not None:
            wp.fire_cooldown = 0
        # Carried-over protection would be free: a player who died wearing armor
        # would come back still wearing it, and an unexpired invulnerability
        # would make the first tick after a respawn a guaranteed no-damage tick.
        arm = self.entities.get_component(eid, Armor)
        if arm is not None:
            arm.value = 0
        pu = self.entities.get_component(eid, Powerup)
        if pu is not None:
            pu.kind = ""
            pu.ticks = 0
        # The level starts over with the life that just came back: enemies do
        # not respawn on their own timer, so this death is the only thing that
        # brings the wave back (killed ones revive, living ones are teleported
        # home).  Hooked here rather than in damage_player() so the snapshot in
        # which the player is still dead still shows the level as it was left.
        self._reset_enemies()
        # ...and the level includes its doors and all its pickups (design D6):
        # shut every door, clear every key mask and put every taken item back.
        # A lost key would otherwise soft-lock the level forever, and a spent
        # medkit would leave the restarted wave without its loot.
        self._reset_doors()
        self._reset_items()

    def _reset_doors(self) -> None:
        for d in self.doors.values():
            d['phase'] = 'closed'
            d['progress'] = 0.0
            d['hold'] = 0
        self._refresh_blocked_doors()

    def _reset_items(self) -> None:
        """Clear all key masks and return every taken item to the map.

        One rule for the whole level: keys and consumables come back the same
        way, on the same trigger (a player's death), because there is no
        per-item respawn timer any more (design D5/D6).  Position and id are
        preserved -- an item never moves, so putting ``a`` back is enough.
        """
        for _pid, eid in self.players.items():
            keys = self.entities.get_component(eid, Keys)
            if keys is not None:
                keys.mask = 0
        for it in self.items:
            it['a'] = True
        # Stale "locked" feedback belongs to the old life too: a door a player
        # found locked is not locked now that the keys are back.
        self._use_reason.clear()

    def _reset_enemies(self) -> None:
        """Restart the wave: every enemy goes back to its own spawn point.

        Full reset, not "the dead ones come back": a survivor that has chased
        the player across the map is teleported home too, with full HP and
        cleared AI -- the point of the reset is that the level starts over, and
        a half-worn wave would not read as that.

        Corpses belong to an enemy that is alive again, so they go with it;
        leaving them would drop a body either at the revived enemy's feet (a
        kill on the spawn point) or in the middle of a level that just
        restarted.  Items are returned by the same wave restart, in
        ``_reset_items``; in-flight projectiles are left alone because a bullet
        expires within a second on its own.
        """
        for en in self.enemies:
            self._reset_enemy(en)
        self.corpses = []

    @staticmethod
    def _reset_enemy(en: dict) -> None:
        en['x'] = en['spawn_x']
        en['y'] = en['spawn_y']
        en['pa'] = en.get('spawn_pa', en['pa'])
        en['hp'] = en['max_hp']
        en['a'] = True
        en['dying'] = 0
        en['stun'] = 0
        en['alert'] = False
        en['ls'] = 0
        en['d_flash'] = 0
        en['anim_frame'] = 0
        en['anim_state'] = 0
        en['attack_from'] = None
        en['attack_ticks'] = 0

    # ------------------------------------------------------------------
    #  State snapshots for protocol encoding
    # ------------------------------------------------------------------
    def get_all_player_states(self) -> list[dict]:
        states = []
        for player_id, eid in self.players.items():
            pos = self.entities.get_component(eid, Position)
            rot = self.entities.get_component(eid, Rotation)
            hp = self.entities.get_component(eid, Health)
            pl = self.entities.get_component(eid, Player)
            wp = self.entities.get_component(eid, WeaponComponent)
            if not (pos and rot and hp and pl and wp):
                continue
            states.append({
                "id": player_id,
                "x": pos.x,
                "y": pos.y,
                "angle": rot.angle,
                "health": hp.value,
                "state": pl.state,
                "wpn": wp.current,
                "ammo": dict(wp.ammo),
                # Ticks left before this weapon can fire again. Read live from
                # the component (not from the last decision) so the client sees
                # the cooldown counting down; fire_seq is filled in by the
                # server from the session, which is where intents are numbered.
                "fire_ready": wp.fire_cooldown,
                "name": pl.name,
                # Armor and the active buff travel with the player record
                # because both are per-player state the HUD already has a
                # place for; neither is derived from anything else in the
                # snapshot, so sending them separately would mean a second
                # source of truth about the same player.
                "armor": self._armor_state(eid),
                "powerup": self._powerup_state(eid),
                # Key bitmask (R/Y/B) and the advisory reason for the last
                # "use" attempt (0 = none/ok).  Both are per-player state with
                # no other source in the snapshot.
                "keys": self._player_keys(eid),
                "use_reason": self.get_use_reason(player_id),
            })
        return states

    def _armor_state(self, eid: int) -> dict | None:
        """``{'type', 'value'}`` for the HUD, or None when there is none.

        The HUD keys off ``arm['type']`` and ``arm['value']``, so those are the
        exact names here -- not the server-side component's ``kind``/``value``.
        """
        arm = self.entities.get_component(eid, Armor)
        if arm is None or arm.value <= 0:
            return None
        return {"type": arm.kind, "value": int(arm.value)}

    def _powerup_state(self, eid: int) -> str:
        """The active buff's name, or '' -- the HUD string verbatim.

        The countdown is deliberately *not* sent: the HUD draws the name only,
        and adding a timer field would mean the client animating something the
        server owns, which is how the two sides drift apart.
        """
        pu = self.entities.get_component(eid, Powerup)
        return pu.kind if pu is not None and pu.ticks > 0 else ""

    def get_enemy_states(self) -> list[dict]:
        return [
            {
                'id': e['id'], 'x': e['x'], 'y': e['y'],
                'angle': e['pa'], 't': e['t'],
                'hp': e['hp'], 'state': 0 if e['a'] else 1,
                'd_flash': e.get('d_flash', 0),
            }
            for e in self.enemies if e['a'] or e.get('dying', 0) > 0
        ]

    def get_bullet_states(self) -> list[dict]:
        return [
            {'x': b['x'], 'y': b['y'], 'col': b['col'], 'dmg': b['dmg'],
             'from_player': b.get('from_player', False)}
            for b in self.bullets
        ]

    def get_corpse_states(self) -> list[dict]:
        return [
            {'x': c['x'], 'y': c['y'], 't': c['t'], 'col': c['col']}
            for c in self.corpses
        ]

    def get_impact_states(self) -> list[dict]:
        return [
            {'x': im['x'], 'y': im['y'], 'type': im['type'], 'id': im['id']}
            for im in self.impacts
        ]