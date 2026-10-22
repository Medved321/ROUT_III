"""Smoke: match end -- PvE level exit, PvP time limit, frozen world, respawn on
request (server routing + mode-aware wave reset) and per-player match stats.

Deterministic, server-side: no sockets.  The live routing of PLAYER_RESPAWN and
the "dead cannot fire" guard are driven through the real ``GameServer`` handlers
with a stubbed lobby (the same pattern as smoke_fire_intent / smoke_hitscan).
"""
import server.weapons as weapons
from server.game_world import GameWorld
from server.server import GameServer
from server.player import PlayerSession
from server.game_data import (
    PLAYER_MAX_HP, PVP_MATCH_TICKS, EXIT_RADIUS, TICK_RATE,
)
from shared.components import (
    Health, Player, Position, Rotation, WeaponComponent, Stats, Velocity,
)
from shared.protocol import (
    encode_world_state, decode_world_state, decode_match_block,
    encode_player_respawn, decode_player_respawn,
)


def _stats(w, player_id=1):
    eid = w.players[player_id]
    return w.entities.get_component(eid, Stats)


def _enemy(eid, x, y, etype='imp', spd=0.0):
    return {
        'id': eid, 'x': x, 'y': y, 't': etype,
        'hp': 20, 'max_hp': 20, 'spd': spd,
        'col': (1, 2, 3), 'a': True, 'dying': 0,
        'anim_frame': 0, 'anim_state': 0, 'stun': 0, 'alert': False,
        'pa': 0.0, 'ls': 0, 'd_flash': 0,
        'spawn_x': x, 'spawn_y': y, 'spawn_pa': 0.0,
        'attack_from': None, 'attack_ticks': 0,
    }


def _party_for(world):
    return type('_P', (), {'world': world})()


def _server_with(world):
    server = GameServer()
    server.lobby = type('_L', (), {
        'find_party_of': lambda self, s: _party_for(world)})()

    def respawn(session):
        server._on_player_respawn(session)
    server._respawn_via = respawn
    return server


# ---------------------------------------------------------------------------
# 1. PvE level exit
# ---------------------------------------------------------------------------

def test_exit_wins_pve():
    w = GameWorld(mode='pve')
    eid = w.add_player(1, 'A')
    pos = w.entities.get_component(eid, Position)
    pos.x, pos.y = 400.0, 400.0
    w.exit_pos = (400.0, 400.0)
    assert w.match_phase == 'playing'
    w.update()
    assert w.match_phase == 'over', w.match_phase
    assert w.get_match_state()['phase'] == 'over'
    print("PvE: living player on the exit wins OK")


def test_exit_reached_only_within_radius():
    w = GameWorld(mode='pve')
    eid = w.add_player(1, 'A')
    pos = w.entities.get_component(eid, Position)
    pos.x, pos.y = 400.0, 400.0
    w.exit_pos = (400.0 + EXIT_RADIUS + 5.0, 400.0)
    w.update()
    assert w.match_phase == 'playing', "won from outside the radius"
    pos.x = 400.0 + EXIT_RADIUS - 1.0
    w.update()
    assert w.match_phase == 'over', "did not win inside the radius"
    print("PvE: exit needs the player inside the radius OK")


def test_no_exit_is_endless():
    w = GameWorld(mode='pve')
    w.add_player(1, 'A')
    assert w.exit_pos is None, w.exit_pos       # default map has no `exit` thing
    for _ in range(50):
        w.update()
    assert w.match_phase == 'playing', "a map without exit finished by itself"
    print("PvE: a map without exit stays endless OK")


def test_dead_on_exit_does_not_win():
    w = GameWorld(mode='pve')
    eid = w.add_player(1, 'A')
    pos = w.entities.get_component(eid, Position)
    pos.x, pos.y = 400.0, 400.0
    w.exit_pos = (400.0, 400.0)
    w.damage_player(eid, 1000)
    for _ in range(5):
        w.update()
    assert w.match_phase == 'playing', "a dead player won the level"
    print("PvE: a dead player on the exit does not win OK")


# ---------------------------------------------------------------------------
# 2. PvP time limit / winner / draw
# ---------------------------------------------------------------------------

def test_pvp_timer_ends_the_match_by_frags():
    w = GameWorld(mode='pvp')
    assert w.pvp_match_ticks == PVP_MATCH_TICKS
    a = w.add_player(1, 'A')
    b = w.add_player(2, 'B')
    w.entities.get_component(a, Stats).kills = 3
    w.entities.get_component(b, Stats).kills = 1
    w.pvp_match_ticks = 3
    w.update()
    w.update()
    assert w.match_phase == 'playing'
    assert w.get_match_state()['time_left'] == 0
    w.update()
    assert w.match_phase == 'over'
    assert w.winner_id == 1, w.winner_id
    print("PvP: timer expiry picks the player with the most frags OK")


def test_pvp_draw_on_equal_frags():
    w = GameWorld(mode='pvp')
    a = w.add_player(1, 'A')
    b = w.add_player(2, 'B')
    w.entities.get_component(a, Stats).kills = 2
    w.entities.get_component(b, Stats).kills = 2
    w.pvp_match_ticks = 1
    w.update()
    assert w.match_phase == 'over'
    assert w.winner_id == 0, "a top-tie must be a draw (winner 0)"
    print("PvP: equal top frags is a draw OK")


def test_pvp_time_left_counts_down():
    w = GameWorld(mode='pvp')
    w.add_player(1, 'A')
    w.pvp_match_ticks = TICK_RATE * 2
    assert w.get_match_state()['time_left'] == 2
    w.update()
    assert w.get_match_state()['time_left'] == 1
    print("PvP: time_left is published in seconds OK")


# ---------------------------------------------------------------------------
# 3. Freeze after the match is over
# ---------------------------------------------------------------------------

def test_world_freezes_when_over():
    w = GameWorld(mode='pvp')
    eid = w.add_player(1, 'A')
    vel = w.entities.get_component(eid, Velocity)
    vel.dx, vel.dy = 50.0, 0.0
    en = _enemy(1, 500.0, 300.0, spd=40.0)
    en['alert'] = True
    w.enemies.append(en)
    w.pvp_match_ticks = 1
    w.update()
    assert w.match_phase == 'over'
    p0 = (w.entities.get_component(eid, Position).x,
          w.entities.get_component(eid, Position).y)
    e0 = (en['x'], en['y'])
    tick0 = w.tick
    for _ in range(20):
        w.update()
    assert w.tick == tick0 + 20, "the tick must keep advancing (snapshots)"
    p1 = (w.entities.get_component(eid, Position).x,
          w.entities.get_component(eid, Position).y)
    assert p1 == p0, ("a frozen world must not move players", p0, p1)
    assert (en['x'], en['y']) == e0, "a frozen world must not move enemies"
    print("over: the world is frozen but the tick keeps ticking OK")


# ---------------------------------------------------------------------------
# 4. Respawn on request (server routing + mode-aware reset)
# ---------------------------------------------------------------------------

def test_respawn_routing_via_server():
    w = GameWorld(mode='pve')
    w.add_player(1, 'A')
    server = _server_with(w)
    session = PlayerSession(1, 'A', ('127.0.0.1', 1))
    w.damage_player(w.players[1], 1000)
    server._respawn_via(session)
    assert w.entities.get_component(w.players[1], Health).value == PLAYER_MAX_HP
    print("PLAYER_RESPAWN routing respawns the player OK")


def test_dead_player_cannot_fire_respawn_restores():
    w = GameWorld(mode='pve')
    eid = w.add_player(1, 'A')
    pos = w.entities.get_component(eid, Position)
    pos.x, pos.y = 200.0, 300.0
    w.entities.get_component(eid, Rotation).angle = 0.0
    server = _server_with(w)
    session = PlayerSession(1, 'A', ('127.0.0.1', 1))

    server._on_player_fire(session, 1, 1)
    assert session.last_fire_result.fired is True, session.last_fire_result
    assert session.last_fire_seq == 1

    w.damage_player(eid, 1000)
    server._on_player_fire(session, 2, 1)
    assert session.last_fire_result.fired is False, "a dead player fired"
    assert session.last_fire_result.reason == 'noop'
    assert session.last_fire_seq == 1, "a refusal must not advance fire_seq"

    server._respawn_via(session)
    server._on_player_fire(session, 3, 1)
    assert session.last_fire_result.fired is True, "respawn did not restore fire"
    print("dead cannot fire, respawn restores it OK")


def test_match_over_blocks_firing():
    w = GameWorld(mode='pve')
    eid = w.add_player(1, 'A')
    w.entities.get_component(eid, Position).x = 200.0
    w.entities.get_component(eid, Rotation).angle = 0.0
    server = _server_with(w)
    session = PlayerSession(1, 'A', ('127.0.0.1', 1))
    w.match_phase = 'over'
    server._on_player_fire(session, 1, 1)
    assert session.last_fire_result.fired is False
    assert session.last_fire_result.reason == 'noop'
    print("a match that is over blocks firing OK")


def test_dead_player_input_is_ignored():
    w = GameWorld(mode='pve')
    eid = w.add_player(1, 'A')
    pos = w.entities.get_component(eid, Position)
    pos.x, pos.y = 300.0, 300.0
    w.damage_player(eid, 1000)
    GameServer._apply_input(None, w, eid, None, 1, 1, 0.0, False, 1, 0)
    vel = w.entities.get_component(eid, Velocity)
    assert (vel.dx, vel.dy) == (0.0, 0.0), "dead input moved the player"
    pl = w.entities.get_component(eid, Player)
    assert pl.state == 1, pl.state
    print("a dead player's movement input is ignored OK")


def test_protocol_respawn_is_empty_and_symmetric():
    payload = encode_player_respawn()
    assert payload == b"", payload
    assert decode_player_respawn(payload) is None
    print("PLAYER_RESPAWN payload is empty OK")


# ---------------------------------------------------------------------------
# 5. Stats
# ---------------------------------------------------------------------------

def test_kill_and_damage_credit_the_shooter():
    w = GameWorld(mode='pve')
    eid = w.add_player(1, 'A')
    en = _enemy(1, 500.0, 300.0)
    en['hp'] = 20
    w.enemies.append(en)
    weapons._damage_enemy(w, en, 12, eid)
    st = _stats(w)
    assert st.damage_dealt == 12, st.damage_dealt
    assert st.kills == 0
    weapons._damage_enemy(w, en, 20, eid)
    assert st.damage_dealt == 32, st.damage_dealt
    assert st.kills == 1, st.kills
    print("kills and dealt damage are credited to the shooter OK")


def test_infighting_credits_nobody():
    w = GameWorld(mode='pve')
    w.add_player(1, 'A')
    en = _enemy(1, 500.0, 300.0)
    w.enemies.append(en)
    weapons._damage_enemy(w, en, 999)          # shooter_eid None: infighting
    assert _stats(w).kills == 0 and _stats(w).damage_dealt == 0
    print("an infighting kill is nobody's frag OK")


def test_damage_taken_and_pvp_frag():
    w = GameWorld(mode='pvp')
    a = w.add_player(1, 'A')
    b = w.add_player(2, 'B')
    w.damage_player(b, 30, source_eid=a)
    assert _stats(w, 1).damage_dealt == 30
    assert _stats(w, 2).damage_taken == 30
    w.damage_player(b, 1000, source_eid=a)
    assert _stats(w, 1).kills == 1, "a PvP kill must be a frag"
    assert _stats(w, 2).deaths == 1
    print("taken damage and PvP frags are recorded OK")


def test_shots_fired_and_hit():
    w = GameWorld(mode='pve')
    eid = w.add_player(1, 'A')
    pos = w.entities.get_component(eid, Position)
    pos.x, pos.y = 100.0, 100.0
    w.entities.get_component(eid, Rotation).angle = 0.0
    w.enemies.append(_enemy(1, 260.0, 100.0))
    weapons.apply_weapon_switch(w, eid, 1)      # pistol (hitscan)
    res = weapons.handle_shoot(w, eid)
    assert res.fired, res
    st = _stats(w)
    assert st.shots_fired == 1, st.shots_fired
    assert st.shots_hit == 1, st.shots_hit
    print("shots fired and hit are recorded OK")


def test_item_pickup_counted():
    w = GameWorld(mode='pve')
    eid = w.add_player(1, 'A')
    pos = w.entities.get_component(eid, Position)
    pos.x, pos.y = 100.0, 100.0
    w.entities.get_component(eid, Health).value = 100
    w.items = [{'id': 1, 'x': 100.0, 'y': 100.0, 't': 'med', 'a': True}]
    w.update()
    assert _stats(w).items == 1, _stats(w).items
    print("a picked item is counted OK")


def test_secret_counted_once():
    w = GameWorld(mode='pve')
    eid = w.add_player(1, 'A')
    w.secrets_total = 1
    w._sector_id_at = lambda x, y: 5
    w._sector_special_at = lambda x, y: 9
    w.update()
    assert _stats(w).secrets == 1, _stats(w).secrets
    w.update()
    w.update()
    assert _stats(w).secrets == 1, "a re-entered secret counted twice"
    print("a secret is counted exactly once OK")


def test_stats_survive_respawn():
    w = GameWorld(mode='pve')
    eid = w.add_player(1, 'A')
    _stats(w).kills = 4
    _stats(w).damage_dealt = 99
    w.damage_player(eid, 1000)
    w.respawn_player(1)
    assert _stats(w).kills == 4 and _stats(w).damage_dealt == 99
    print("stats survive a respawn OK")


# ---------------------------------------------------------------------------
# 6. Wire format
# ---------------------------------------------------------------------------

def test_match_block_round_trip():
    w = GameWorld(mode='pvp')
    w.add_player(1, 'A')
    w.entities.get_component(w.players[1], Stats).kills = 2
    w.match_phase = 'over'
    w.winner_id = 1
    w.pvp_match_ticks = TICK_RATE * 5
    w.secrets_total = 3
    match = w.get_match_state()
    assert match == {'phase': 'over', 'winner_id': 1, 'time_left': 5,
                     'secrets_total': 3}, match
    payload = encode_world_state(
        w.tick, w.get_all_player_states(), w.get_enemy_states(),
        w.get_bullet_states(), w.get_corpse_states(), w.mode,
        w.get_impact_states(), w.get_item_states(), w.get_door_states(),
        match)
    players, = (decode_world_state(payload)[1],)
    assert players[0]['stats']['kills'] == 2, players[0]['stats']
    got = decode_match_block(payload)
    assert got == match, got
    print("match block + per-player stats survive the wire OK")


def test_missing_match_block_degrades():
    payload = encode_world_state(1, [], [], [], [], 'pve', [], [], [])
    got = decode_match_block(payload)
    assert got == {'phase': 'playing', 'winner_id': 0, 'time_left': 0,
                   'secrets_total': 0}, got
    print("a payload without a match block reads as playing OK")


if __name__ == '__main__':
    test_exit_wins_pve()
    test_exit_reached_only_within_radius()
    test_no_exit_is_endless()
    test_dead_on_exit_does_not_win()
    test_pvp_timer_ends_the_match_by_frags()
    test_pvp_draw_on_equal_frags()
    test_pvp_time_left_counts_down()
    test_world_freezes_when_over()
    test_respawn_routing_via_server()
    test_dead_player_cannot_fire_respawn_restores()
    test_match_over_blocks_firing()
    test_dead_player_input_is_ignored()
    test_protocol_respawn_is_empty_and_symmetric()
    test_kill_and_damage_credit_the_shooter()
    test_infighting_credits_nobody()
    test_damage_taken_and_pvp_frag()
    test_shots_fired_and_hit()
    test_item_pickup_counted()
    test_secret_counted_once()
    test_stats_survive_respawn()
    test_match_block_round_trip()
    test_missing_match_block_degrades()
    print("MATCH END SMOKE PASSED")
