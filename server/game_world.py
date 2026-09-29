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
    Health, Player, Collider, WeaponComponent, Dash
)
from shared.map_data import GameMap, TILE
from shared.sector_map import SectorMap
from server.game_data import (
    PLAYER_RADIUS, PLAYER_MAX_HP, ENEMY_DB, ENEMY_RADIUS,
    ENEMY_DETECT_RANGE, ENEMY_ALERT_AOE, ENEMY_MELEE_COOLDOWN,
    MELEE_RANGE, RANGED_DB, SPAWN_POOL, CORPSE_LIFETIME,
    TICK_RATE, FPS_60, MAX_PLAYER_PROJ, IMPACT_TTL, MAX_IMPACTS,
    BALL_SUB_STEP, INFIGHT_TICKS, MAX_MOVE_STEP_PX,
)
from server.weapons import _damage_enemy


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

        # respawn tracking  entity_id -> ticks remaining
        self._respawn: dict[int, int] = {}
        self._enemy_id_seq = 0
        self._impact_seq = 0

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

        self.players[player_id] = eid
        return eid

    def remove_player(self, player_id: int) -> None:
        eid = self.players.pop(player_id, None)
        if eid is not None:
            self.entities.destroy(eid)
            self._respawn.pop(eid, None)

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
    #  Main tick
    # ------------------------------------------------------------------
    def update(self) -> None:
        self.tick += 1
        self._apply_movement()
        # After the movement that consumed this tick's dash speed, so a dash
        # grants exactly DASH_TICKS ticks of boosted movement.
        self._update_dash()
        self._update_fire_cooldowns()
        self._update_enemies()
        self._update_bullets()
        self._update_impacts()
        self._update_corpses()
        self._update_respawns()
        self.enemies = [e for e in self.enemies if e['a'] or e.get('dying', 0) > 0]

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
            return self.game_map.can_move_px(nxt, pos.y, r)
        return self.game_map.can_move_px(pos.x, nxt, r)

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
                                and self.game_map.has_los_px(ex, ey, other['x'], other['y'])):
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
                    if dst < best and self.game_map.has_los_px(ex, ey, px, py):
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
                if self.game_map.can_move_px(nx, ny, ENEMY_RADIUS):
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
            if self.game_map.can_move_px(new_x, en['y'], ENEMY_RADIUS):
                en['x'] = new_x
            if self.game_map.can_move_px(en['x'], new_y, ENEMY_RADIUS):
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
                        pe = self.entities.get_component(target_eid, Health)
                        if pe:
                            pe.value = max(0, pe.value - db['dmg'])
                            pl = self.entities.get_component(target_eid, Player)
                            if pe.value <= 0 and pl:
                                pl.state = 1
                                self._respawn[target_eid] = TICK_RATE * 3
                    en['ls'] = self.tick

            # --- ranged attack (projectile) --------------------------------
            rdb = RANGED_DB.get(etype)
            if rdb and tx is not None:
                dst = math.hypot(tx - ex, ty - ey)
                if dst < ENEMY_DETECT_RANGE and self.tick - en['ls'] > rdb['cd'] * TICK_RATE / 1000:
                    if self.game_map.has_los_px(ex, ey, tx, ty):
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
                if self.game_map.is_wall_px(b['x'], b['y']):
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
                if self.game_map.is_wall_px(b['x'], b['y']):
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
                    hp.value = max(0, hp.value - b['dmg'])
                    if hp.value <= 0:
                        pl = self.entities.get_component(eid, Player)
                        if pl:
                            pl.state = 1
                            self._respawn[eid] = TICK_RATE * 3
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
                    hp.value = max(0, hp.value - b['dmg'])
                    if hp.value <= 0:
                        pl = self.entities.get_component(eid, Player)
                        if pl:
                            pl.state = 1
                            self._respawn[eid] = TICK_RATE * 3
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
        self.corpses.append({
            'x': en['x'], 'y': en['y'],
            't': en['t'],
            'col': en['col'],
            'time': CORPSE_LIFETIME,
        })

    # ------------------------------------------------------------------
    #  Respawn
    # ------------------------------------------------------------------
    def _update_respawns(self) -> None:
        for eid in list(self._respawn):
            self._respawn[eid] -= 1
            if self._respawn[eid] <= 0:
                self._respawn.pop(eid)
                pos = self.entities.get_component(eid, Position)
                hp = self.entities.get_component(eid, Health)
                pl = self.entities.get_component(eid, Player)
                if pos and hp and pl:
                    sx, sy = _spawn_px(self.spawn_points)
                    pos.x, pos.y = sx, sy
                    hp.value = PLAYER_MAX_HP
                    pl.state = 0

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
                "name": pl.name,
            })
        return states

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