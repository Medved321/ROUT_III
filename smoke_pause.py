"""Pause must stop the game, not just freeze the picture.

The pause menu stops calling ``_process_input``, so ``InputHandler`` is never
updated while it is up and keeps the last in-game values -- a held W, a held
fire button.  The fixed-rate input loop samples exactly that state, so unless it
is muted the client keeps forwarding the stale keys and the authoritative server
(``server.server._apply_input`` sets velocity, ``server.game_world._apply_movement``
consumes and zeroes it every tick) walks and shoots the player behind the frozen
frame.  That is what the player reports as "the pause does not stop the game".

Covers:
  * a muted loop sends nothing, and does not even sample the snapshot, so a
    latched dash edge cannot be spent from under the menu;
  * a muted loop keeps ticking (it must NOT tear itself down: a should_run=False
    would kill the timer for the rest of the party) and resumes at the same
    rate when unmuted;
  * the client wires the mute to ``_paused``.
"""
import os
import sys
import asyncio

sys.stdout.reconfigure(encoding="utf-8")

from client.performance import input_send_loop


class _Net:
    def __init__(self):
        self.sends = []

    def send_input(self, *args):
        self.sends.append(args)


class _Stop(Exception):
    pass


def _drive(enabled_seq=None, should_run=None, loops=None):
    """Run ``input_send_loop`` under a fake clock for a bounded number of ticks.

    ``enabled_seq[i]`` is the mute predicate value seen on tick ``i`` (the last
    value repeats).  ``loops`` bounds the number of sleeps so the test
    terminates; when ``should_run`` is given instead, that is what ends the loop.
    """
    net = _Net()
    calls = {"tick": 0, "snap": 0}

    def snapshot():
        calls["snap"] += 1
        return (1, 0, 0.0, False, 1, 0)

    enabled = None
    if enabled_seq is not None:
        def enabled():
            i = min(calls["tick"], len(enabled_seq) - 1)
            return enabled_seq[i]

    async def fake_sleep(_interval):
        calls["tick"] += 1
        if loops is not None and calls["tick"] >= loops:
            raise _Stop

    real_sleep = asyncio.sleep
    asyncio.sleep = fake_sleep
    try:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                input_send_loop(net, snapshot, 0.0,
                                should_run=should_run, enabled=enabled))
        except _Stop:
            pass
        finally:
            loop.close()
    finally:
        asyncio.sleep = real_sleep
    return net, calls


def test_unmuted_sends_every_tick():
    net, calls = _drive(enabled_seq=[True], loops=5)
    assert len(net.sends) == 5, net.sends
    assert calls["snap"] == 5, calls
    print("1 unmuted loop sends on every tick OK")


def test_muted_tick_sends_nothing_and_keeps_running():
    net, calls = _drive(enabled_seq=[False], loops=3)
    assert net.sends == [], "a paused client leaked input to the server"
    assert calls["snap"] == 0, \
        "the loop sampled the snapshot while muted (a dash edge can be spent)"
    assert calls["tick"] == 3, "the muted loop tore itself down"
    print("2 muted loop sends nothing, samples nothing, keeps ticking OK")


def test_mute_toggles_without_rebuilding_the_timer():
    net, calls = _drive(enabled_seq=[True, False, False, True], loops=4)
    assert len(net.sends) == 2, net.sends
    assert calls["snap"] == 2, calls
    print("3 mute/unmute yields the same fixed-rate timer OK")


def test_default_enabled_sends_every_tick():
    net, calls = _drive(enabled_seq=None, loops=4)
    assert len(net.sends) == 4, net.sends
    print("4 enabled=None keeps the old always-send behaviour OK")


def test_should_run_still_ends_the_loop():
    state = {"n": 0}

    def should_run():
        state["n"] += 1
        return state["n"] <= 2

    net, calls = _drive(enabled_seq=[True], should_run=should_run)
    assert len(net.sends) == 2, net.sends
    print("5 should_run=False still ends the loop OK")


def test_client_wires_the_mute_to_pause():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "client", "client.py")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    # One gate owns both mute states (pause and the death/victory overlay); the
    # loop must reference it, and it must fold in _paused.
    assert "enabled=self._input_enabled" in src, \
        "pause is not wired into the input-send loop"
    assert "not self._paused and not self._end_active()" in src, \
        "_input_enabled must mute input while paused"
    print("6 client wires the input mute to _paused OK")


if __name__ == "__main__":
    test_unmuted_sends_every_tick()
    test_muted_tick_sends_nothing_and_keeps_running()
    test_mute_toggles_without_rebuilding_the_timer()
    test_default_enabled_sends_every_tick()
    test_should_run_still_ends_the_loop()
    test_client_wires_the_mute_to_pause()
    print("ALL PAUSE SMOKE TESTS PASSED")
