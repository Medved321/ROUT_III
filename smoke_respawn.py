"""Smoke: instant respawn — a dead player is back on the point they joined from,
and a player's death is the ONLY thing that brings enemies back: the wave
restarts on it (killed enemies revive on their own spots, survivors are
teleported home, corpses go with them).

The old behaviour (3 s, random spawn point, enemies returning on their own
one-tick timer) is what these checks pin down: the delay, the *identity* of the
respawn point, the fact that every death path -- melee, enemy fireball, friendly
fire, hitscan -- is covered by the one death routine instead of four hand-written
copies, and that nothing but a player's death starts the wave over.
Server-side deterministic sim."""
import server.weapons as weapons
from server.game_world import GameWorld
from server.server import GameServer
from server.game_data import (
    PLAYER_MAX_HP, PLAYER_RESPAWN_TICKS,
)
from shared.components import (
    Health, Player, Position, Velocity, Dash, WeaponComponent,
)
from shared.protocol import INPUT_FLAG_DASH


def _park_player(w, player_id, x, y, name='P'):
    """Add a player, then move it away from its join point."""
    eid = w.add_player(player_id, name)
    pos = w.entities.get_component(eid, Position)
    pos.x, pos.y = x, y
    return eid


def _mk_enemy(eid, x, y, etype='imp', spd=0.0, angle=0.0):
    return {
        'id': eid, 'x': x, 'y': y, 't': etype,
        'hp': 20, 'max_hp': 20, 'spd': spd,
        'col': (1, 2, 3), 'a': True, 'dying': 0,
        'anim_frame': 0, 'anim_state': 0, 'stun': 0, 'alert': False,
        'pa': angle, 'ls': 0, 'd_flash': 0,
        'spawn_x': x, 'spawn_y': y, 'spawn_pa': angle,
        'attack_from': None, 'attack_ticks': 0,
    }


def _state(w, player_id=1):
    for s in w.get_all_player_states():
        if s['id'] == player_id:
            return s
    return None


# ---------------------------------------------------------------- player

def test_player_respawns_at_its_own_start_point():
    """The join point is the respawn point -- not a random spawn_points entry."""
    w = GameWorld(mode='pve')
    eid = w.add_player(1, 'P')
    join = (w.entities.get_component(eid, Position).x,
            w.entities.get_component(eid, Position).y)
    pos = w.entities.get_component(eid, Position)
    pos.x, pos.y = join[0] + 300.0, join[1] + 300.0

    assert w.damage_player(eid, 1000) is True
    w.update()

    s = _state(w)
    assert (round(s['x']), round(s['y'])) == (round(join[0]), round(join[1])), (
        s['x'], s['y'], join)
    assert s['health'] == PLAYER_MAX_HP, s['health']
    assert s['state'] == 0, s['state']
    print("player respawns at its own start point OK")


def test_player_respawn_is_immediate():
    """One tick, not the old TICK_RATE * 3 (=3 s)."""
    w = GameWorld(mode='pve')
    eid = w.add_player(1, 'P')
    w.damage_player(eid, 1000)
    assert PLAYER_RESPAWN_TICKS == 1, PLAYER_RESPAWN_TICKS
    for _ in range(4):
        w.update()
    assert _state(w)['health'] == PLAYER_MAX_HP, "must be back within 4 ticks"
    print("player respawn is immediate OK")


def test_player_death_is_published_at_least_one_snapshot():
    """Instant must not mean invisible: the corpse/flash is the death feedback."""
    w = GameWorld(mode='pve')
    eid = w.add_player(1, 'P')
    w.damage_player(eid, 1000)
    s = _state(w)
    assert s['health'] == 0 and s['state'] == 1, s
    w.update()
    assert _state(w)['health'] > 0, "back in the fight on the very next tick"
    print("player death visible in a snapshot OK")


def test_player_respawn_refills_the_ammo_reserve():
    """The reserve emptied by fighting comes back full; guns/selection do not."""
    w = GameWorld(mode='pve')
    eid = w.add_player(1, 'P')
    wp = w.entities.get_component(eid, WeaponComponent)
    spent = {'pstl': 0, 'shl': 3, 'slg': 100, 'rck': 0, 'BFG': 0}
    lost_guns = {0, 1}
    wp.ammo.update(spent)
    wp.owned.clear()
    wp.owned.update(lost_guns)
    wp.current = 0

    w.damage_player(eid, 1000)
    w.update()

    assert dict(wp.ammo) == dict(WeaponComponent().ammo), wp.ammo
    assert wp.owned == lost_guns, wp.owned
    assert wp.current == 0, wp.current
    print("player respawn refills ammo, keeps guns OK")


def test_player_respawn_clears_dash_and_cooldowns():
    """A player killed mid-dash must not keep dashing out of the respawn point."""
    w = GameWorld(mode='pve')
    eid = w.add_player(1, 'P')
    dash = w.entities.get_component(eid, Dash)
    vel = w.entities.get_component(eid, Velocity)
    wp = w.entities.get_component(eid, WeaponComponent)
    dash.ticks, dash.cooldown = 9, 40
    vel.dx, vel.dy = 3.0, -4.0
    wp.fire_cooldown = 7

    w.damage_player(eid, 1000)
    w.update()

    assert dash.ticks == 0 and dash.cooldown == 0, (dash.ticks, dash.cooldown)
    assert vel.dx == 0.0 and vel.dy == 0.0, (vel.dx, vel.dy)
    assert wp.fire_cooldown == 0, wp.fire_cooldown
    print("respawn clears dash/velocity/weapon cooldown OK")


def test_all_death_paths_schedule_a_respawn():
    """Melee, enemy fireball, friendly fire and hitscan all respawn.

    They used to be four copies of the same block; a copy that forgot the timer
    left a 0 HP player stuck in the world with no way back.  Each case drives the
    real call site with its own damage value -- HP is drained first, so a path
    that silently stopped killing would show up instead of passing on its own.
    """
    def world_at(x, y):
        w = GameWorld(mode='pve')
        eid = _park_player(w, 1, x, y)
        hp = w.entities.get_component(eid, Health)
        hp.value = 1                      # the next real hit is lethal
        return w, eid

    def melee(w, eid):
        en = _mk_enemy(1, 400.0, 318.0, etype='soldier', spd=0.0)
        w.enemies.append(en)
        # Spy on the one entry point for player damage.  Its residue is not
        # observable from outside anymore: the death it causes schedules the
        # respawn, and the respawn restarts the wave, which clears `en['ls']`
        # on the very same tick -- so "the enemy still looks like it just
        # attacked" is no longer evidence that the melee branch fired.
        hits = []
        real = w.damage_player

        def spy(pid, dmg):
            hits.append((pid, dmg))
            return real(pid, dmg)

        w.damage_player = spy
        try:
            for _ in range(20):           # ENEMY_MELEE_COOLDOWN gates the first hit
                w.update()
                if hits:
                    break
        finally:
            w.damage_player = real
        assert hits and hits[0][0] == eid, "the melee branch never fired"
        assert hits[0][1] > 0, hits
        assert eid not in w._respawn, "respawn still pending: not tested"
    w, eid = world_at(400.0, 300.0)
    melee(w, eid)
    assert w.entities.get_component(eid, Health).value == PLAYER_MAX_HP

    def enemy_ball(w, eid):
        assert w._resolve_enemy_ball(
            {'x': 600.0, 'y': 300.0, 'dmg': 15, 'src': 99}) is True
        assert eid in w._respawn
        w.update()
    w, eid = world_at(600.0, 300.0)
    enemy_ball(w, eid)

    def friendly_fire(w, eid):
        assert w._resolve_player_ball(
            {'x': 600.0, 'y': 300.0, 'dmg': 15, 'src': 7,
             'from_player': True}) is True
        assert eid in w._respawn
        w.update()
    w, eid = world_at(600.0, 300.0)
    friendly_fire(w, eid)

    def hitscan(w, eid):
        assert weapons._hitscan_players(w, 7, 600.0, 200.0, 1.5708, 15, 600.0)
        assert eid in w._respawn
        w.update()
    w, eid = world_at(600.0, 300.0)
    hitscan(w, eid)

    for w, eid in [(w, eid)]:
        assert w.entities.get_component(eid, Health).value == PLAYER_MAX_HP
    print("every death path respawns the player OK")


def test_input_does_not_clear_death_state():
    """PLAYER_INPUT runs at 25 Hz against a 20 Hz tick and must not resurrect.

    Calls the real ``_apply_input`` (it touches nothing on ``self``, so it can be
    driven unbound) with a dash in flight -- the write that used to clear the
    death state, and with it the only trace that the player had died.
    """
    w = GameWorld(mode='pve')
    eid = w.add_player(1, 'P')
    dash = w.entities.get_component(eid, Dash)
    dash.ticks = 5

    GameServer._apply_input(None, w, eid, None, 1, 0, 0.0, False,
                            flags=INPUT_FLAG_DASH)
    pl = w.entities.get_component(eid, Player)
    assert pl.state == 3, "a live player must still publish the dash"

    w.damage_player(eid, 1000)
    assert pl.state == 1
    GameServer._apply_input(None, w, eid, None, 1, 0, 0.0, False,
                            flags=INPUT_FLAG_DASH)
    assert pl.state == 1, "input resurrected a dead player"
    w.update()
    assert pl.state == 0, "respawn must clear the death state"
    print("input does not clear death state OK")


def test_remove_player_forgets_start_point():
    w = GameWorld(mode='pve')
    w.add_player(1, 'P')
    assert 1 in w._player_start
    w.remove_player(1)
    assert 1 not in w._player_start, "stale start point would outlive the player"
    print("remove_player forgets start point OK")


# ---------------------------------------------------------------- enemy

# Long enough that an old one-tick enemy timer, a stray wave timer or a
# "respawn after a few seconds" regression cannot hide inside it: 60 ticks is
# 3 s at TICK_RATE, i.e. longer than the delay this project deleted.
DEAD_TICKS = 60


def test_dead_enemy_stays_down():
    """A kill arms no timer: ticks do not bring an enemy back, only a death
    of a player does -- and there is no dead player in this test."""
    w = GameWorld(mode='pve')
    w.add_player(1, 'P')
    en = _mk_enemy(1, 500.0, 300.0)
    w.enemies.append(en)
    w.kill_enemy(en)

    for _ in range(DEAD_TICKS):
        w.update()

    assert en['a'] is False and en['hp'] == 0, (en['a'], en['hp'])
    assert [e['id'] for e in w.get_enemy_states()] == [], "dead enemy published"
    assert w.enemies, "a dead enemy must keep its record: it carries the home"
    print("a killed enemy stays down until a player dies OK")


def test_player_death_restarts_the_wave():
    """The one trigger: the dead revive on their spots, the living go home too."""
    w = GameWorld(mode='pve')
    w.add_player(1, 'P')
    killed = _mk_enemy(1, 500.0, 300.0)
    roaming = _mk_enemy(2, 640.0, 380.0, etype='soldier')
    w.enemies.extend([killed, roaming])
    w.kill_enemy(killed)
    roaming['x'], roaming['y'] = 900.0, 700.0        # walked away from home
    w.corpses.append({'eid': 99, 'x': 10.0, 'y': 10.0, 't': 'imp',
                      'col': (1, 2, 3), 'time': 500})

    w.damage_player(w.players[1], 1000)
    w.update()

    by_id = {e['id']: e for e in w.enemies}
    dead = by_id[1]
    assert dead['a'] is True and dead['hp'] == dead['max_hp'], dead
    assert (dead['x'], dead['y']) == (500.0, 300.0), dead
    assert (roaming['x'], roaming['y']) == (640.0, 380.0), roaming
    assert w.corpses == [], w.corpses
    assert {e['id'] for e in w.get_enemy_states()} == {1, 2}, \
        w.get_enemy_states()
    print("a player death restarts the wave OK")


def test_wave_reset_clears_ai_state():
    """A survivor is not carried over: stun, aggro and timers all start clean."""
    w = GameWorld(mode='pve')
    w.add_player(1, 'P')
    en = _mk_enemy(1, 500.0, 300.0)
    w.enemies.append(en)
    en.update({'stun': 7, 'alert': True, 'ls': 900, 'attack_from': 4,
               'attack_ticks': 30, 'dying': 3, 'd_flash': 5, 'anim_frame': 2})

    w.damage_player(w.players[1], 1000)
    w.update()

    r = w.enemies[0]
    assert (r['stun'], r['alert'], r['ls']) == (0, False, 0), r
    assert r['attack_from'] is None and r['attack_ticks'] == 0, r
    assert r['dying'] == 0 and r['d_flash'] == 0, r
    assert r['anim_frame'] == 0, r
    print("wave reset clears AI state OK")


def test_enemy_is_absent_from_snapshot_while_dead():
    """Hidden for as long as it is down, not just for one tick."""
    w = GameWorld(mode='pve')
    w.add_player(1, 'P')
    en = _mk_enemy(1, 500.0, 300.0)
    w.enemies.append(en)
    w.kill_enemy(en)
    assert [e['id'] for e in w.get_enemy_states()] == [], "dead enemy published"
    for _ in range(DEAD_TICKS):
        w.update()
        assert [e['id'] for e in w.get_enemy_states()] == [], \
            "a dead enemy came back without a player's death"
    print("dead enemy hidden from the snapshot until the wave restarts OK")


def test_corpse_is_cleared_before_its_enemy_returns():
    """The body must not survive into the life it belongs to: killed exactly on
    its home point, it would otherwise lie inside the revived enemy."""
    w = GameWorld(mode='pve')
    w.add_player(1, 'P')
    en = _mk_enemy(1, 500.0, 300.0)
    w.enemies.append(en)
    w.kill_enemy(en)
    assert [c['eid'] for c in w.corpses] == [1]
    assert (en['x'], en['y']) == (500.0, 300.0), "killed away from home"

    w.damage_player(w.players[1], 1000)
    w.update()

    assert [c for c in w.corpses if c.get('eid') == 1] == [], w.corpses
    assert en['a'] is True, "the enemy did not come back"
    print("corpse cleared before its enemy returns OK")


def test_player_death_is_the_only_trigger():
    """A kill with a live player arms nothing: there is no timer to run out."""
    w = GameWorld(mode='pve')
    w.add_player(1, 'P')
    en = _mk_enemy(1, 500.0, 300.0)
    w.enemies.append(en)
    w.kill_enemy(en)
    assert not w._respawn, w._respawn
    for _ in range(DEAD_TICKS):
        w.update()
        assert not w._respawn, "a killed enemy armed a player respawn"
    assert en['a'] is False and en['hp'] == 0, (en['a'], en['hp'])
    print("nothing but a player's death starts the wave over OK")


if __name__ == '__main__':
    test_player_respawns_at_its_own_start_point()
    test_player_respawn_is_immediate()
    test_player_death_is_published_at_least_one_snapshot()
    test_player_respawn_clears_dash_and_cooldowns()
    test_player_respawn_refills_the_ammo_reserve()
    test_all_death_paths_schedule_a_respawn()
    test_input_does_not_clear_death_state()
    test_remove_player_forgets_start_point()
    test_dead_enemy_stays_down()
    test_player_death_restarts_the_wave()
    test_wave_reset_clears_ai_state()
    test_enemy_is_absent_from_snapshot_while_dead()
    test_corpse_is_cleared_before_its_enemy_returns()
    test_player_death_is_the_only_trigger()
    print("RESPAWN SMOKE PASSED")