"""Smoke: enemy infighting — enemy projectiles hurt other enemies (not their
shooter), hurt enemies retaliate, neighbors wake up, death is standard, and the
counter-attack expires back to the player. Server-side deterministic sim."""
import math

from server.game_world import GameWorld
from server.game_data import (
    ENEMY_DB, ENEMY_RADIUS, ENEMY_ALERT_AOE, INFIGHT_TICKS,
)
from shared.components import Position


def _mk_enemy(w, eid_num, x, y, etype='imp', hp=None, spd=1.8, alert=False):
    db = ENEMY_DB[etype]
    return {
        'id': eid_num, 'x': x, 'y': y, 't': etype,
        'hp': db['hp'] if hp is None else hp,
        'max_hp': db['hp'], 'spd': spd,
        'col': db['col'], 'a': True, 'dying': 0,
        'anim_frame': 0, 'anim_state': 0, 'stun': 0, 'alert': alert,
        'pa': 0.0, 'ls': 0, 'd_flash': 0,
        'spawn_x': x, 'spawn_y': y,
        'attack_from': None, 'attack_ticks': 0,
    }


def _park_player(w, x, y):
    eid = w.add_player(1, 'P')
    pos = w.entities.get_component(eid, Position)
    pos.x, pos.y = x, y
    return eid


def test_spawn_fields():
    w = GameWorld(mode='pve')
    _park_player(w, 900.0, 900.0)
    w.spawn_enemies(3)
    assert w.enemies, "no enemies spawned"
    for e in w.enemies:
        assert 'attack_from' in e and e['attack_from'] is None, e
        assert 'attack_ticks' in e and e['attack_ticks'] == 0, e
    print("spawn fields OK")


def test_projectile_hurts_other_enemy():
    w = GameWorld(mode='pve')
    _park_player(w, 900.0, 900.0)
    a = _mk_enemy(w, 1, 500.0, 300.0)
    b = _mk_enemy(w, 2, 560.0, 300.0, hp=60)
    w.enemies.extend([a, b])
    a_hp, b_hp = a['hp'], b['hp']
    bullet = {'x': b['x'], 'y': b['y'], 'dmg': 12, 'src': a['id']}
    assert w._resolve_enemy_ball(bullet) is True
    assert b['hp'] == b_hp - 12, b['hp']
    assert w.enemies[0]['hp'] == a_hp, "attacker must not be hurt by its own ball"
    print("projectile hurts another enemy OK")


def test_no_self_damage():
    w = GameWorld(mode='pve')
    _park_player(w, 900.0, 900.0)
    a = _mk_enemy(w, 1, 500.0, 300.0, hp=60)
    w.enemies.append(a)
    bullet = {'x': a['x'] + 2, 'y': a['y'], 'dmg': 12, 'src': a['id']}
    assert w._resolve_enemy_ball(bullet) is False
    assert a['hp'] == 60, a['hp']
    assert a['attack_from'] is None, "self-hit must not register an attacker"
    print("no self damage OK")


def test_infighting_death():
    w = GameWorld(mode='pve')
    _park_player(w, 900.0, 900.0)
    a = _mk_enemy(w, 1, 500.0, 300.0)
    b = _mk_enemy(w, 2, 560.0, 300.0, hp=5)
    w.enemies.extend([a, b])
    bullet = {'x': b['x'], 'y': b['y'], 'dmg': 12, 'src': a['id']}
    assert w._resolve_enemy_ball(bullet) is True
    assert b['a'] is False and b['hp'] == 0
    assert len(w.corpses) == 1 and w.corpses[0]['t'] == b['t']
    print("death from infighting goes through kill_enemy OK")


def test_retaliation_then_return_to_player():
    w = GameWorld(mode='pve')
    _park_player(w, 600.0, 60.0)      # player up-right, within detect range
    a = _mk_enemy(w, 1, 520.0, 300.0, etype='chaingunner', spd=0.0)
    b = _mk_enemy(w, 2, 580.0, 300.0, etype='chaingunner', spd=1.4)
    w.enemies.extend([a, b])
    bullet = {'x': b['x'], 'y': b['y'], 'dmg': 4, 'src': a['id']}
    assert w._resolve_enemy_ball(bullet) is True
    assert b['attack_from'] == a['id'] and b['attack_ticks'] == INFIGHT_TICKS
    b_x, b_y = b['x'], b['y']
    for _ in range(20):
        w.update()
    # B pursues A (straight left down the row) instead of the player (up-right)
    assert b['x'] < b_x - 15, b['x']
    assert abs(b['y'] - b_y) < 0.01, b['y']
    assert b['attack_ticks'] < INFIGHT_TICKS
    # after the timer expires the retaliation wears off and B targets the player
    for _ in range(INFIGHT_TICKS + 40):
        w.update()
    assert b['attack_from'] is None and b['attack_ticks'] == 0
    assert b['y'] < b_y - 10, "enemy should return to targeting the player"
    print("retaliation timer + return to player OK")


def test_melee_hits_enemy_target():
    w = GameWorld(mode='pve')
    _park_player(w, 900.0, 60.0)
    a = _mk_enemy(w, 1, 640.0, 300.0, etype='chaingunner', spd=0.0, hp=30)
    b = _mk_enemy(w, 2, 660.0, 300.0, etype='soldier', spd=0.0)
    w.enemies.extend([a, b])
    bullet = {'x': b['x'], 'y': b['y'], 'dmg': 4, 'src': a['id']}
    assert w._resolve_enemy_ball(bullet) is True
    a_hp = a['hp']
    for _ in range(12):
        w.update()
    # one melee strike lands inside MELEE_RANGE against the enemy attacker
    assert a['hp'] == a_hp - 15, a['hp']
    assert a['attack_from'] == b['id'], a['attack_from']
    print("melee hits enemy target OK")


def test_neighbor_alert():
    w = GameWorld(mode='pve')
    _park_player(w, 100.0, 700.0)
    a = _mk_enemy(w, 1, 320.0, 320.0)
    b = _mk_enemy(w, 2, 380.0, 320.0)
    c = _mk_enemy(w, 3, 420.0, 320.0, alert=False)
    d = _mk_enemy(w, 4, 820.0, 320.0, alert=False)
    w.enemies.extend([a, b, c, d])
    bullet = {'x': b['x'], 'y': b['y'], 'dmg': 12, 'src': a['id']}
    assert w._resolve_enemy_ball(bullet) is True
    assert b['alert'] is True
    assert c['alert'] is True, "neighbor within ENEMY_ALERT_AOE should wake"
    assert d['alert'] is False, "far enemy should stay asleep"
    print("neighbor alert AOE OK")


if __name__ == '__main__':
    test_spawn_fields()
    test_projectile_hurts_other_enemy()
    test_no_self_damage()
    test_infighting_death()
    test_retaliation_then_return_to_player()
    test_melee_hits_enemy_target()
    test_neighbor_alert()
    print("INFIGHTING SMOKE PASSED")