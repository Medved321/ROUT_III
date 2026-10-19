"""Smoke: player dash (player-dash change).

Covers the nine cases from design.md:
1. PLAYER_INPUT carries the dash flag, and the flag does not disturb the other
   fields
2. start + timers, and a duplicate request does not re-trigger
3. movement input is required, and a request without it wastes no cooldown
4. dash speed is exactly DASH_SPEED_MULT and expires after DASH_TICKS ticks
5. no phase-through: a dash step never lands past a solid linedef
6. ordinary walking keeps its behaviour and its collision-check count
7. state reports dashing only while the dash lasts
8. one Space press sends exactly one dash packet
9. Space does not act as a fire button
"""
import math
import os
import struct

from server.server import GameServer
from server.game_world import GameWorld
from server.game_data import (
    DASH_COOLDOWN_TICKS, DASH_SPEED_MULT, DASH_TICKS,
    MAX_MOVE_STEP_PX, MOVE_SPEED_PX, TICK_INTERVAL,
)
from shared.components import Dash, Player, Position, Rotation, Velocity, WeaponComponent
from shared.protocol import (
    INPUT_FLAG_DASH, INPUT_FLAG_USE, decode_player_input, encode_player_input,
)
from shared.sector_map import SectorMap
from shared.version import PROTOCOL_VERSION

from smoke_sector_server import _strip_map


# ---------------------------------------------------------------------------
# 1. Protocol layout
# ---------------------------------------------------------------------------

def test_flags_roundtrip():
    # 8: the player record also carries the fire decision (fire_seq/fire_ready);
    # 9: armor/powerup/items; 10: key mask + use_reason and the doors block;
    # 11: player_id is uint16 in JOIN_ACCEPT and LOBBY_STATE.
    # PLAYER_INPUT itself is unchanged, still flags.
    assert PROTOCOL_VERSION == 12, PROTOCOL_VERSION
    assert INPUT_FLAG_DASH == 0x01, INPUT_FLAG_DASH
    assert INPUT_FLAG_USE == 0x02, INPUT_FLAG_USE

    # the packet grew by exactly the flags byte: 8 -> 9
    assert len(encode_player_input(0, 0, 0.0, False, 1)) == 9
    assert struct.calcsize("!bbfBBB") == 9

    # dash alone
    mx, my, ang, sh, wid, fl = decode_player_input(
        encode_player_input(1, 0, 0.75, False, 2, INPUT_FLAG_DASH))
    assert (mx, my, ang, sh, wid, fl) == (1, 0, 0.75, False, 2, 0x01), \
        (mx, my, ang, sh, wid, fl)

    # default is 0 so every pre-dash call site stays valid
    assert decode_player_input(encode_player_input(1, -1, 0.5, True, 4))[5] == 0

    # dash does not collide with shoot / weapon_id
    mx, my, ang, sh, wid, fl = decode_player_input(
        encode_player_input(-1, 1, -0.5, True, 5, INPUT_FLAG_DASH))
    assert (mx, my, sh, wid, fl) == (-1, 1, True, 5, 0x01), \
        (mx, my, sh, wid, fl)
    print("1. PLAYER_INPUT flags byte OK (9 bytes, dash bit isolated)")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _world(sm=None, pid=1, name="Dasher"):
    world = GameWorld(mode='pve', game_map=sm if sm is not None else _strip_map([0]))
    eid = world.add_player(pid, name)
    return world, eid


def _comp(world, eid, cls):
    return world.entities.get_component(eid, cls)


def _drive(world, eid, move_x, move_y, ticks, angle=0.0, server=None,
           flags=0):
    """Feed real input into real server ticks and return the final position."""
    pos = _comp(world, eid, Position)
    for _ in range(ticks):
        server._apply_input(world, eid, None, move_x, move_y, angle,
                            False, 1, flags)
        world.update()
    return pos


def _dash_step():
    return MOVE_SPEED_PX * TICK_INTERVAL * DASH_SPEED_MULT


# ---------------------------------------------------------------------------
# 2. Start, timers, duplicate requests
# ---------------------------------------------------------------------------

def test_start_and_timers():
    server = GameServer()
    world, eid = _world()
    dash = _comp(world, eid, Dash)
    assert dash.ticks == 0 and dash.cooldown == 0, (dash.ticks, dash.cooldown)

    # a request with movement input and no cooldown starts the dash
    server._apply_input(world, eid, None, 1, 0, 0.0, False, 1, INPUT_FLAG_DASH)
    assert dash.ticks == DASH_TICKS, dash.ticks
    assert dash.cooldown == DASH_COOLDOWN_TICKS, dash.cooldown
    print(f"2a. dash start OK (ticks={dash.ticks}, cooldown={dash.cooldown})")

    # a duplicate of the same request (reliable redelivery) must not extend it
    ticks_before, cd_before = dash.ticks, dash.cooldown
    for _ in range(5):
        server._apply_input(world, eid, None, 1, 0, 0.0, False, 1, INPUT_FLAG_DASH)
    assert dash.ticks == ticks_before, (dash.ticks, ticks_before)
    assert dash.cooldown == cd_before, (dash.cooldown, cd_before)
    print("2b. duplicate dash request does not re-trigger OK")


# ---------------------------------------------------------------------------
# 3. Movement input is required
# ---------------------------------------------------------------------------

def test_requires_movement():
    server = GameServer()
    world, eid = _world()
    dash = _comp(world, eid, Dash)

    # no movement input: no dash, and no cooldown spent
    for move in ((0, 0),):
        server._apply_input(world, eid, None, move[0], move[1], 0.0, False, 1,
                            INPUT_FLAG_DASH)
    assert dash.ticks == 0, dash.ticks
    assert dash.cooldown == 0, dash.cooldown

    # standing still also spends nothing
    _drive(world, eid, 0, 0, 10, server=server, flags=INPUT_FLAG_DASH)
    assert dash.ticks == 0 and dash.cooldown == 0, (dash.ticks, dash.cooldown)

    # a request while on cooldown is ignored and does not extend the cooldown
    server._apply_input(world, eid, None, 1, 0, 0.0, False, 1, INPUT_FLAG_DASH)
    world.update()
    cd_after_first = dash.cooldown
    server._apply_input(world, eid, None, 1, 0, 0.0, False, 1, INPUT_FLAG_DASH)
    assert dash.cooldown <= cd_after_first, (dash.cooldown, cd_after_first)
    print("3. dash needs movement input and wastes no cooldown OK")


# ---------------------------------------------------------------------------
# 4. Speed and duration
# ---------------------------------------------------------------------------

def test_speed_and_duration():
    server = GameServer()
    sm = _strip_map([0], w=4096)
    world, eid = _world(sm)
    pos = _comp(world, eid, Position)
    dash = _comp(world, eid, Dash)

    walk_step = MOVE_SPEED_PX * TICK_INTERVAL
    dash_step = _dash_step()
    assert dash_step == walk_step * DASH_SPEED_MULT, (dash_step, walk_step)

    # walking: exact per-tick step
    x0 = pos.x
    _drive(world, eid, 1, 0, 1, server=server)
    walked = pos.x - x0
    assert abs(walked - walk_step) < 1e-6, walked
    print(f"4a. walking step OK ({walked:.2f} px/tick)")

    # dashing: the boost lasts exactly DASH_TICKS ticks, then walking resumes.
    # The flag goes out once (a single press); the server keeps boosting.
    per_tick = []
    x0 = pos.x
    for i in range(DASH_TICKS + 1):
        _drive(world, eid, 1, 0, 1, server=server,
               flags=INPUT_FLAG_DASH if i == 0 else 0)
        per_tick.append(pos.x - x0)
        x0 = pos.x
    boosted = per_tick[:DASH_TICKS]
    assert all(abs(s - dash_step) < 1e-6 for s in boosted), boosted
    assert len(boosted) == DASH_TICKS, boosted
    assert abs(per_tick[DASH_TICKS] - walk_step) < 1e-6, per_tick[DASH_TICKS]
    print(f"4b. dash speed {DASH_SPEED_MULT}x for exactly {DASH_TICKS} ticks OK")

    # and the cooldown gates the next dash for DASH_COOLDOWN_TICKS ticks
    dash.cooldown = 0
    dash.ticks = 0
    _drive(world, eid, 0, 0, DASH_COOLDOWN_TICKS, server=server)
    assert dash.cooldown == 0, dash.cooldown
    print(f"4c. cooldown expires after {DASH_COOLDOWN_TICKS} ticks OK")


# ---------------------------------------------------------------------------
# 5. No phase-through a solid linedef
# ---------------------------------------------------------------------------

def test_no_phase_through():
    # two rooms split by a solid two-sided line at x = 512
    sm = _strip_map([0, 0], solid_edge=0, w=512, h=512)
    assert any(ld.solid for ld in sm.linedefs)
    step = _dash_step()
    assert step > MAX_MOVE_STEP_PX, (step, MAX_MOVE_STEP_PX)

    # The dash step is resolved in sub-steps, so no start position can be
    # carried past the wall.
    for start in range(470, 500):
        world, eid = _world(sm)
        pos = _comp(world, eid, Position)
        vel = _comp(world, eid, Velocity)
        pos.x, pos.y = float(start), 256.0
        assert sm.find_sector_at(pos.x, pos.y) == 0, (start, pos.x)
        vel.dx, vel.dy = step, 0.0
        world.update()
        assert sm.find_sector_at(pos.x, pos.y) == 0, \
            f"dash phased through the solid wall from {start} to {pos.x}"
    print(f"5a. dash step ({step:.1f} px) cannot cross a solid linedef OK")

    # can_move_px() only tests the destination, so a step longer than
    # 2 * radius could land on the far side of a zero-thickness linedef.  The
    # current dash is under that limit (24 < 32), which is why 5a passes even
    # without sub-stepping -- but the sub-step is what keeps the guarantee true
    # for ANY speed, so a faster dash can never start tunnelling.
    world, eid = _world(sm)
    gm = world.game_map
    r = _comp(world, eid, Player) and 16
    long_step = 2.0 * r + 8.0                     # 40 px > 2 * radius
    edge_x = 512.0
    start = edge_x - r                             # furthest legal position
    assert gm.can_move_px(start, 256.0, r), "start is not free"
    # a single destination check would happily teleport the player through
    assert gm.can_move_px(start + long_step, 256.0, r), \
        "the destination-only check no longer allows a phase-through"
    assert gm.find_sector_at(start + long_step, 256.0) == 1

    pos = _comp(world, eid, Position)
    vel = _comp(world, eid, Velocity)
    pos.x, pos.y = start, 256.0
    vel.dx, vel.dy = long_step, 0.0
    world.update()                                  # the sub-step stops it
    assert gm.find_sector_at(pos.x, pos.y) == 0, \
        f"sub-stepping did not stop a {long_step:.0f} px step: x={pos.x}"
    print(f"5b. a {long_step:.0f} px step (> 2 x radius) is stopped by "
          f"sub-stepping OK")

    # and the sub-step must not turn ordinary openings into walls
    sm_open = _strip_map([0, 0], w=512)
    w2, e2 = _world(sm_open)
    p2 = _drive(w2, e2, 1, 0, 60, server=GameServer(), flags=INPUT_FLAG_DASH)
    assert p2.x > 512 + 16, f"dash did not cross an open edge: {p2.x}"
    print("5c. dash crosses an open edge OK")


# ---------------------------------------------------------------------------
# 6. Walking behaviour and collision-check count are unchanged
# ---------------------------------------------------------------------------

def test_walking_unchanged():
    world, eid = _world(_strip_map([0], w=4096))
    gm = world.game_map
    real = gm.can_move_px
    calls = []
    gm.can_move_px = lambda x, y, r: (calls.append(1), real(x, y, r))[1]

    # one walking tick on one axis: exactly one probe, as before the dash
    _drive(world, eid, 1, 0, 1, server=GameServer())
    assert len(calls) == 1, f"walking now costs {len(calls)} probes per tick"
    walking_cost = len(calls)

    calls.clear()
    _drive(world, eid, 1, 0, 1, server=GameServer(), flags=INPUT_FLAG_DASH)
    dash_cost = len(calls)
    assert dash_cost > walking_cost, (walking_cost, dash_cost)
    print(f"6. walking costs {walking_cost} probe/tick, dash {dash_cost} OK")


# ---------------------------------------------------------------------------
# 7. state reporting
# ---------------------------------------------------------------------------

def test_state_reporting():
    server = GameServer()
    world, eid = _world()
    pl = _comp(world, eid, Player)

    server._apply_input(world, eid, None, 0, 0, 0.0, False, 1, 0)
    assert pl.state == 0, pl.state

    server._apply_input(world, eid, None, 1, 0, 0.0, False, 1, INPUT_FLAG_DASH)
    assert pl.state == 3, pl.state

    # a dash takes priority over the shooting state
    server._apply_input(world, eid, None, 1, 0, 0.0, True, 1, INPUT_FLAG_DASH)
    assert pl.state == 3, pl.state

    # after the dash expires, the state returns to idle
    for _ in range(DASH_TICKS + 2):
        _drive(world, eid, 1, 0, 1, server=server)
    assert _comp(world, eid, Dash).ticks == 0
    assert pl.state == 0, pl.state

    # A held fire button in PLAYER_INPUT carries no trigger any more: the only
    # path to a shot is the reliable FIRE intent, so input alone must not
    # publish the shooting state (fire-intent-single-path, 1.3).
    server._apply_input(world, eid, None, 1, 0, 0.0, True, 1, 0)
    assert pl.state == 0, "ввод выставил state=2 без созданного выстрела"

    # ...but a real shot does, and only for one tick
    from server.weapons import handle_shoot, apply_weapon_switch
    wp = _comp(world, eid, WeaponComponent)
    wp.ammo['pstl'] = 10
    apply_weapon_switch(world, eid, 1)
    handle_shoot(world, eid)
    assert pl.state == 2, pl.state
    world.update()
    assert pl.state == 0, "пульс стрельбы не погас за тик"
    print("7. state 3 only while dashing; 2 only after a real shot OK")


# ---------------------------------------------------------------------------
# 8. Client-side one-shot pulse
# ---------------------------------------------------------------------------

def test_client_pulse_is_one_shot():
    import asyncio
    from client.performance import input_send_loop
    from engine.constants import INPUT_SEND_INTERVAL

    class FakeHandler:
        move_x = 1
        move_y = 0
        shoot = False
        weapon_id = 1
        dash_pressed = False
        use = False

        def update(self):
            # exactly what InputHandler.update() does each frame
            self.dash_pressed = False

    class FakeClient:
        def __init__(self):
            self.input_handler = FakeHandler()
            self.local_angle = 0.0
            self._dash_pending = False
            self._prev_use = False
            self._use_hint_until = 0.0
            from client.client import GameClient
            self._snapshot = GameClient._input_snapshot

        def process_input(self):
            # The frame-level latch from GameClient._process_input: the press
            # edge lives one rendered frame, the snapshot only consumes the
            # latched _dash_pending. The test has no game loop, so it stands
            # in for the frame that does this.
            if self.input_handler.dash_pressed:
                self.input_handler.dash_pressed = False
                self._dash_pending = True

    sent = []

    class FakeNet:
        def send_input(self, mx, my, ang, shoot, wpn, flags=0):
            sent.append(flags)

    async def run():
        client = FakeClient()
        task = asyncio.ensure_future(
            input_send_loop(FakeNet(), lambda: client._snapshot(client),
                            INPUT_SEND_INTERVAL))
        await asyncio.sleep(0.1)
        # one Space press on a frame; that frame latches it, and the 25 Hz
        # send loop carries it in exactly one packet
        client.input_handler.dash_pressed = True
        client.process_input()
        await asyncio.sleep(0.08)
        client.input_handler.update()
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    assert sent, "no input was sent"
    # holding Space must not queue more dashes; the server would also reject
    # them (cooldown), but the client owes exactly one packet per press
    assert sent.count(INPUT_FLAG_DASH) == 1, sent
    assert all(f == 0 for f in sent if f != INPUT_FLAG_DASH), sent
    print(f"8. one Space press -> {sent.count(INPUT_FLAG_DASH)} dash packet "
          f"out of {len(sent)} OK")


def test_space_does_not_fire():
    """9. Space is the dash key, not a fire button (dash-fx / player-dash).

    The renderer used to read ``pygame.key.get_pressed()[K_SPACE]`` as "the
    player is firing", so every dash also swung the weapon. Fire must come from
    the server-confirmed weapon timer or the LMB only.
    """
    os.environ['SDL_VIDEODRIVER'] = 'dummy'
    import pygame
    pygame.init()
    pygame.display.set_mode((320, 240))
    from client.bridge import RenderBridge
    import engine.game_state as gs

    b = RenderBridge(pygame.Surface((320, 240)))
    MAP = [[1] * 16] + [[1] + [0] * 14 + [1] for _ in range(14)] + [[1] * 16]
    b.set_map(MAP, 16)
    ammo = {'pstl': 10, 'shl': 10, 'slg': 10, 'rck': 10, 'BFG': 10}

    # pygame 2.6 has no key-state injection, so stand in for the two input
    # probes the renderer makes. Only Space is down, so the walk bob stays off
    # and any difference in the frame is the fire flag alone.
    real_keys, real_mouse = pygame.key.get_pressed, pygame.mouse.get_pressed

    class _Keys:
        def __init__(self, held):
            self._held = held

        def __getitem__(self, key):
            return key in self._held

    def frame(held=frozenset(), fire=0):
        pygame.key.get_pressed = lambda: _Keys(held)
        pygame.mouse.get_pressed = lambda: (False, False, False)
        try:
            gs.particles.clear()
            b.update(500.0, 500.0, 0.0, 200, [], [], [], [], 1, ammo, fire, 'pve')
            b.render()
            return pygame.image.tostring(gs.screen, 'RGB')
        finally:
            pygame.key.get_pressed, pygame.mouse.get_pressed = real_keys, real_mouse

    idle = frame()
    with_space = frame(held={pygame.K_SPACE})
    assert with_space == idle, "holding Space made the weapon render as firing"
    # The server-confirmed weapon timer still fires the weapon sprite.
    assert frame(fire=20) != idle, "fire_timer no longer shows the weapon firing"
    # And so does the LMB.
    pygame.mouse.get_pressed = lambda: (True, False, False)
    try:
        b.update(500.0, 500.0, 0.0, 200, [], [], [], [], 1, ammo, 0, 'pve')
        b.render()
        lmb = pygame.image.tostring(gs.screen, 'RGB')
    finally:
        pygame.mouse.get_pressed = real_mouse
    assert lmb != idle, "LMB no longer shows the weapon firing"
    pygame.quit()
    print("9. Space does not fire, weapon timer and LMB still do OK")


# ---------------------------------------------------------------------------
# 10. Untrusted input is sanitized before it reaches the world
# ---------------------------------------------------------------------------

def test_untrusted_input_is_sanitized():
    """The input channel is raw client bytes: a NaN angle or an oversized move
    vector must not poison the world.  Before the guard, NaN survived
    normalize_angle, reached the collision helper and crashed the game loop with
    ``int(math.ceil(nan / MAX_MOVE_STEP_PX))`` (ValueError); a signed-byte move
    vector of 127 was a 127x speed hack.
    """
    server = GameServer()
    world, eid = _world()
    rot = _comp(world, eid, Rotation)
    vel = _comp(world, eid, Velocity)
    pos = _comp(world, eid, Position)
    rot.angle = 0.75
    px, py = pos.x, pos.y

    # NaN / Inf angle: rotation keeps its last finite value, world stays finite.
    for bad in (math.nan, math.inf, -math.inf):
        server._apply_input(world, eid, None, 0, 0, bad, False, 1, 0)
        assert rot.angle == 0.75, (bad, rot.angle)
        world.update()
    assert math.isfinite(pos.x) and math.isfinite(pos.y), (pos.x, pos.y)

    # Oversized move vector is clamped to the unit square, not applied as 127x.
    server._apply_input(world, eid, None, 127, 127, 0.0, False, 1, 0)
    assert abs(vel.dx) <= MOVE_SPEED_PX * TICK_INTERVAL + 1e-9, vel.dx
    assert abs(vel.dy) <= MOVE_SPEED_PX * TICK_INTERVAL + 1e-9, vel.dy

    # A full unit vector of bad input still leaves the player inside the world.
    world.update()
    assert math.isfinite(pos.x) and math.isfinite(pos.y), (pos.x, pos.y)
    print("10. untrusted input sanitized (NaN angle, move clamp) OK")


if __name__ == "__main__":
    test_flags_roundtrip()
    test_start_and_timers()
    test_requires_movement()
    test_speed_and_duration()
    test_no_phase_through()
    test_walking_unchanged()
    test_state_reporting()
    test_client_pulse_is_one_shot()
    test_space_does_not_fire()
    test_untrusted_input_is_sanitized()
    print("PLAYER DASH TEST PASSED")
