"""smoke_perf.py — smoke tests for the low-end-performance change.

Covers: adaptive rendering quality (degrade/restore + bounds), input send rate,
smooth snapshot interpolation (no jumps, hold after missed snapshots, snap for
teleports, local player never interpolated), particle surface caching, effect
limits vs quality, server error throttling and unreachable-address removal.

Run: py -3.12 smoke_perf.py
"""

import asyncio
import logging
import os
import time

os.environ["SDL_VIDEODRIVER"] = "dummy"

import pygame

from client import performance
from client.performance import AdaptiveQuality, input_send_loop
from engine import particles
from engine import game_state as gs
from engine import constants as const
from engine.constants import INPUT_SEND_INTERVAL
from client.network_client import NetworkClient, INTERP_DELAY, MAX_EXTRAPOLATION, SNAP_DISTANCE

# ---------------------------------------------------------------------------
# 1. Adaptive quality: degrading on long frames, restoring on short frames,
#    scale clamped to [base_scale, max_scale].
# ---------------------------------------------------------------------------

def test_adaptive_quality():
    q = AdaptiveQuality(base_scale=4, max_scale=8, target_fps=60.0)
    assert q.scale == 4
    assert q.quality_factor() == 1.0
    assert q.effects_factor() == 1.0

    now = 0.0
    # Long frames (55 ms at 60 Hz) for several seconds -> degrade to max.
    for i in range(5 * 60):
        now += 1.0 / 60.0
        q.sample(55.0, now)
    assert q.scale == 8, q.scale
    assert q.quality_factor() == 0.0
    assert q.effects_factor() == 0.5

    # Short frames (5 ms) for several seconds -> restore to base.
    for i in range(5 * 60):
        now += 1.0 / 60.0
        q.sample(5.0, now)
    assert q.scale == 4, q.scale

    # Never leaves the [base, max] range.
    for i in range(200):
        now += 1.0 / 60.0
        q.sample(1000.0, now)
    assert q.scale == 8
    for i in range(8 * 60):
        now += 1.0 / 60.0
        q.sample(0.5, now)
    assert q.scale == 4
    assert 0.5 <= q.effects_factor() <= 1.0
    assert 0.7 <= q.view_dist_factor() <= 1.0
    print("  ok: adaptive quality degrade/restore + bounds")


def test_apply_quality_wiring():
    # The adapter's scale must drive the global render scale / view distance
    # exactly the way GameClient._apply_quality does.
    from client.client import GameClient

    gc = object.__new__(GameClient)
    gc.quality = AdaptiveQuality(base_scale=4, max_scale=8, target_fps=60.0)
    gc._base_view_dist = 900.0

    gs.render_scale = 4
    from engine import rendering
    from client.raycaster import SCALE as RC_SCALE
    const.SCALE = RC_SCALE

    # degrades the render down
    now = 0.0
    for i in range(5 * 60):
        now += 1.0 / 60.0
        gc.quality.sample(55.0, now)
    gc._apply_quality()
    assert gs.render_scale > 4, gs.render_scale
    assert const.SCALE == gs.render_scale
    assert rendering.SCALE == gs.render_scale
    assert gs.view_dist < 900.0 and gs.view_dist > 0
    assert 0.5 <= gs.effects_factor < 1.0

    # restores to the configured base on short frames
    for i in range(5 * 60):
        now += 1.0 / 60.0
        gc.quality.sample(5.0, now)
    gc._apply_quality()
    assert gs.render_scale == 4
    assert gs.view_dist == 900.0
    assert gs.effects_factor == 1.0
    print("  ok: _apply_quality drives gs.render_scale/view_dist/effects_factor")


# ---------------------------------------------------------------------------
# 2. Input is sent on a dedicated ~25 Hz loop, decoupled from rendering.
# ---------------------------------------------------------------------------

async def _input_rate_check():
    sent = []

    class FakeNet:
        def send_input(self, mx, my, ang, shoot, wpn, flags=0):
            sent.append((mx, my, ang, shoot, wpn))

    task = asyncio.ensure_future(
        input_send_loop(FakeNet(), lambda: (1.0, 2.0, 0.5, False, 1, 0),
                        INPUT_SEND_INTERVAL)
    )
    await asyncio.sleep(0.26)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    rate = len(sent) / 0.26
    assert 20.0 <= rate <= 30.0, rate
    print(f"  ok: input_send_loop rate ~{rate:.1f} Hz (target 25)")


def test_input_interval():
    asyncio.run(_input_rate_check())


# ---------------------------------------------------------------------------
# 3. Snapshot interpolation: monotonic blending, hold after a gap, snap on
#    teleport, local player taken from the newest snapshot unchanged.
# ---------------------------------------------------------------------------

def _make_state(tick, p2_x, enemy_x, bullet_x, local_x=10.0):
    return (
        tick,
        [
            {"id": 1, "x": local_x, "y": 0.0, "angle": 0.0, "health": 100, "state": 0,
             "name": "Me", "wpn": 1, "ammo": {}},
            {"id": 2, "x": p2_x, "y": 0.0, "angle": 0.0, "health": 100, "state": 0,
             "name": "P2", "wpn": 1, "ammo": {}},
        ],
        [{"id": 10, "x": enemy_x, "y": 0.0, "angle": 0.0, "type": 1, "hp": 30, "state": 0}],
        [{"x": bullet_x, "y": 0.0, "col": 1, "from_player": 0}],
        [],
        "pve",
        [],
    )


def test_interpolation():
    nc = NetworkClient()
    nc.player_id = 1
    s0 = _make_state(100, p2_x=0.0, enemy_x=0.0, bullet_x=0.0)
    s1 = _make_state(101, p2_x=50.0, enemy_x=20.0, bullet_x=40.0)
    nc.snapshots = [(0.0, s0), (0.05, s1)]

    # Just before t=0 -> oldest known position (no negative extrapolation).
    st = nc.interpolated_state(0.0 + INTERP_DELAY - 0.02)
    p2 = next(p for p in st[1] if p["id"] == 2)
    assert abs(p2["x"] - 0.0) < 1e-6, p2["x"]

    # Mid-interval -> halfway between the two snapshots.
    target = 0.025 + INTERP_DELAY
    prev_x = None
    for k in range(11):
        now = INTERP_DELAY + 0.025 + (k - 5) * 0.005
        st = nc.interpolated_state(now)
        px = next(p for p in st[1] if p["id"] == 2)["x"]
        ex = next(e for e in st[2] if e["id"] == 10)["x"]
        bx = st[3][0]["x"] if st[3] else None
        if prev_x is not None:
            # monotonic rise, each step ~5px apart — no jumps
            assert px - prev_x >= -1e-6, (prev_x, px)
            assert px - prev_x <= 10.0, (prev_x, px)
        prev_x = px
        assert abs(ex - px * 0.4) < 1e-6
        if bx is not None:
            assert abs(bx - px * 0.8) < 1e-6
    assert abs(prev_x - 50.0) < 1e-6, prev_x
    print("  ok: interpolation is monotonic between snapshots (players/enemies/bullets)")

    # Local player (id=1) is always taken from the newest snapshot: its x never
    # lags behind or blends.
    for now in [INTERP_DELAY - 0.01, INTERP_DELAY + 0.02, INTERP_DELAY + 0.04]:
        st = nc.interpolated_state(now)
        me = next(p for p in st[1] if p["id"] == 1)
        assert me["x"] == 10.0, (now, me["x"])
    print("  ok: local player not interpolated")


def test_missed_snapshot_hold():
    nc = NetworkClient()
    nc.player_id = 1
    s0 = _make_state(100, p2_x=0.0, enemy_x=0.0, bullet_x=0.0)
    s1 = _make_state(101, p2_x=50.0, enemy_x=20.0, bullet_x=40.0)
    nc.snapshots = [(0.0, s0), (0.05, s1)]

    # Within extrapolation window -> keeps sliding forward.
    target = 0.05 + INTERP_DELAY + 0.05  # 50 ms beyond newest < MAX_EXTRAPOLATION
    st = nc.interpolated_state(target)
    p2 = next(p for p in st[1] if p["id"] == 2)
    assert p2["x"] > 50.0, p2["x"]

    # Far beyond the window -> hold the newest position, no teleport/stutter.
    st = nc.interpolated_state(0.05 + INTERP_DELAY + MAX_EXTRAPOLATION + 0.5)
    p2 = next(p for p in st[1] if p["id"] == 2)
    assert abs(p2["x"] - 50.0) < 1e-6, p2["x"]
    print("  ok: bounded extrapolation then hold (no jump) after missed snapshot")


def test_teleport_snap():
    nc = NetworkClient()
    nc.player_id = 1
    s0 = _make_state(100, p2_x=0.0, enemy_x=0.0, bullet_x=0.0)
    s1 = _make_state(101, p2_x=500.0, enemy_x=400.0, bullet_x=0.0)
    nc.snapshots = [(0.0, s0), (0.05, s1)]

    st = nc.interpolated_state(0.025 + INTERP_DELAY)
    p2 = next(p for p in st[1] if p["id"] == 2)
    assert p2["x"] == 500.0, p2["x"]  # snapped instantly, no long blend
    print("  ok: teleport motion snaps instantly (SNAP_DISTANCE honored)")


# ---------------------------------------------------------------------------
# 4. Particles: cached surfaces are reused, effects scale with quality.
# ---------------------------------------------------------------------------

def test_particle_cache_and_limits():
    pygame.init()

    nc = const.W // const.SCALE
    z_b = [999.0] * nc
    gs.screen = pygame.Surface((const.W, const.H))
    gs.px, gs.py, gs.pa, gs.pitch = 320.0, 320.0, 0.0, 0.0
    gs.render_scale = 4
    gs.effects_factor = 1.0
    particles._SMOKE_SURFACES.clear()
    particles._SCORCH_SURFACES.clear()

    gs.particles = [{
        "x": 340.0, "y": 320.0, "vx": 0.0, "vy": 0.0, "life": 20, "max_life": 30,
        "col": (180, 180, 180), "size": 8, "z": 10, "zv": 0.0, "smoke": True,
    }]
    for _ in range(3):
        particles.draw_particles(z_b)
    assert len(particles._SMOKE_SURFACES) == 1, len(particles._SMOKE_SURFACES)

    # Same cached surface object reused across frames.
    surf = particles._cached_circle_surface(particles._SMOKE_SURFACES, 64, (180, 180, 180))
    assert particles._cached_circle_surface(particles._SMOKE_SURFACES, 64, (180, 180, 180)) is surf
    gs.particles.clear()

    # Effect counts scale with quality (and never fall below the floor).
    gs.effects_factor = 1.0
    assert particles._effect_limit(200) == 200
    gs.effects_factor = 0.5
    assert particles._effect_limit(200) == 100
    assert particles._effect_limit(8) == 8  # floor
    gs.effects_factor = 1.0
    print("  ok: particle surfaces cached, effect counts scale with quality")
    pygame.quit()


# ---------------------------------------------------------------------------
# 5. Server: error_received throttling and unreachable-address removal.
# ---------------------------------------------------------------------------

class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def test_error_throttling():
    from server import network as svnet

    net = svnet.ServerNetwork()
    net.transport = type("T", (), {})()  # attribute presence only
    cap = _Capture()
    logger = logging.getLogger("server.network")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(cap)

    n = 0
    for _ in range(1000):
        net.error_received(ConnectionResetError(10054, "An existing connection was forcibly closed"))
        n += 1
    net.error_received(RuntimeError("boom"))  # unexpected: still logged as warning

    levels = [r.levelno for r in cap.records]
    assert levels.count(logging.DEBUG) == 1, levels  # 1000 identical errors -> 1 line
    assert logging.WARNING in levels  # non-expected errors are not swallowed
    logger.removeHandler(cap)
    print(f"  ok: {n} identical errors -> {levels.count(logging.DEBUG)} debug lines, warning kept")


class _FakeTransport:
    def __init__(self, bad):
        self.bad = bad
        self.sent = []

    def sendto(self, data, addr):
        if addr in self.bad:
            raise ConnectionResetError(10054, "An existing connection was forcibly closed")
        self.sent.append(addr)


def test_unreachable_broadcast():
    from server import network as svnet

    net = svnet.ServerNetwork()
    good = ("10.0.0.1", 7777)
    bad = ("10.0.0.2", 7777)
    t = _FakeTransport({bad})
    net.transport = t
    for addr in (good, bad):
        net.client_info[addr] = {"protocol_ok": True}

    # First broadcast: good goes out, bad raises and becomes unreachable.
    net.broadcast_world_state([good, bad], 5, [], [], [], [], "pve", [])
    assert bad in net.unreachable
    assert good in t.sent and bad not in t.sent

    # Subsequent broadcasts skip the dead address entirely (no error spam).
    n_before = len(t.sent)
    net.broadcast_world_state([good, bad], 6, [], [], [], [], "pve", [])
    assert len(t.sent) == n_before + 1

    # A direct send to the dead address is guarded too.
    net._send_to(bad, type("P", (), {"encode": lambda self: b"x"})())
    assert bad not in t.sent
    print("  ok: unreachable address excluded from broadcast and direct sends")

    # Once the session is removed the address is forgotten (no leak).
    net.unreachable.add(bad)
    from server.player import PlayerSession
    sess = PlayerSession(7, "Dead", bad)
    net.players[bad] = sess
    removed = net.remove_player(bad)
    assert removed is sess
    assert bad not in net.unreachable and bad not in net.players
    print("  ok: remove_player discards unreachable markers")


def test_server_purges_unreachable():
    from server.network import ServerNetwork
    from server.player import PlayerSession
    from server.server import GameServer

    server = GameServer(host="127.0.0.1", port=0)
    net = server.network
    addr = ("10.0.0.9", 7777)
    sess = PlayerSession(9, "Ghost", addr)
    net.players[addr] = sess
    net.unreachable.add(addr)

    server._remove_stale_players()
    assert addr not in net.players and addr not in net.unreachable
    print("  ok: server removes unreachable sessions without ping timeout")


def main():
    print("smoke_perf:")
    test_adaptive_quality()
    test_apply_quality_wiring()
    test_input_interval()
    test_interpolation()
    test_missed_snapshot_hold()
    test_teleport_snap()
    test_particle_cache_and_limits()
    test_error_throttling()
    test_unreachable_broadcast()
    test_server_purges_unreachable()
    print("ALL PASS")


if __name__ == "__main__":
    main()