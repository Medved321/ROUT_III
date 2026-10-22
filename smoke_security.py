"""Security smoke tests: untrusted input cannot exhaust or crash the server.

Covers the four host-side hardening rules that a flood or a malicious peer can
otherwise turn into a memory blow-up or a ``struct.error`` inside the tick:

* S1  per-address tables (``_endpoints``/``client_info``/``_last_seen``) are
      bounded by ``MAX_TRACKED_ADDRS`` under an address-rotating flood, while
      joined sessions and update-pending addresses stay pinned.
* S2  concurrent sessions are capped by ``MAX_SESSIONS``; the extra JOIN gets a
      JOIN_FAIL instead of a silent drop.
* S3  a party cannot grow past ``MAX_PARTY_MEMBERS`` (which would overflow the
      single-byte player count in the world/lobby encoders).
* S4  names are sanitised: control characters (log injection) are stripped and
      a byte-budget truncation never splits a multi-byte character.
* S5  a non-finite ping timestamp is neutralised instead of echoed back as NaN.
"""
import tempfile

import server.network as netmod
import server.server as srvmod
from shared.protocol import (
    Packet, PacketType,
    encode_hello, encode_join_request, encode_reliable,
    sanitize_text, _encode_string, _decode_string,
    encode_ping, decode_ping,
    encode_lobby_state, decode_lobby_state, encode_world_state,
)
from server.network import ServerNetwork, MAX_TRACKED_ADDRS, MAX_SESSIONS
from server.server import GameServer, MAX_PARTY_MEMBERS
from server.player import PlayerSession


class _FakeTransport:
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append((data, addr))


def _hello_datagram(protocol=12, version="1.0.0"):
    return Packet(PacketType.HELLO, 0, encode_hello(protocol, version)).encode()


def _join_datagram(name):
    inner = encode_reliable(0, 0, PacketType.JOIN_REQUEST, 0, 1,
                            encode_join_request(name))
    return Packet(PacketType.RELIABLE_DATA, 0, inner).encode()


# ---------------------------------------------------------------- S1: eviction
def test_address_tables_are_bounded():
    """HELLO flood from rotating ports must not grow the tables without bound.

    The flood here only ever sends HELLO (creating ``client_info`` entries and
    never a session), which is exactly the unbounded path: nothing else removes
    a non-joining address.  The cap keeps every table at or below the limit.
    """
    old = netmod.MAX_TRACKED_ADDRS
    netmod.MAX_TRACKED_ADDRS = 8
    try:
        net = ServerNetwork()
        for i in range(100):
            addr = (f"10.0.0.{i % 250}", 5000 + i)
            net.datagram_received(_hello_datagram(), addr)
        assert len(net._last_seen) <= 8, len(net._last_seen)
        assert len(net.client_info) <= 8, len(net.client_info)
        assert len(net._endpoints) <= 8, len(net._endpoints)
    finally:
        netmod.MAX_TRACKED_ADDRS = old


def test_address_tables_bounded_on_reliable_flood():
    """RELIABLE_DATA (not just HELLO) must respect the same bound.

    This path creates ``_endpoints`` for an address that never joins, so it is
    a second, independent way to grow a per-address table.
    """
    old = netmod.MAX_TRACKED_ADDRS
    netmod.MAX_TRACKED_ADDRS = 8
    try:
        net = ServerNetwork()
        inner = encode_reliable(0, 0, PacketType.GET_LOBBY, 0, 1, b"")
        for i in range(100):
            addr = (f"10.2.0.{i % 250}", 6000 + i)
            net.datagram_received(
                Packet(PacketType.RELIABLE_DATA, 0, inner).encode(), addr)
        assert len(net._last_seen) <= 8, len(net._last_seen)
        assert len(net._endpoints) <= 8, len(net._endpoints)
    finally:
        netmod.MAX_TRACKED_ADDRS = old


def test_pinned_addresses_survive_eviction():
    """A joined session and an update-pending address must never be evicted.

    Evicting the session's tables would orphan a live player; evicting the
    update-pending address would wipe ``protocol_ok``/``needs_update`` and make
    the follow-up FILE_REQUEST look unauthorised.
    """
    old = netmod.MAX_TRACKED_ADDRS
    netmod.MAX_TRACKED_ADDRS = 8
    try:
        net = ServerNetwork()
        session_addr = ("10.9.0.1", 7001)
        net.client_info[session_addr] = {"protocol_ok": True}
        net._endpoint(session_addr)
        net.players[session_addr] = PlayerSession(1, "p", session_addr)

        update_addr = ("10.9.0.2", 7002)
        net.client_info[update_addr] = {"protocol_ok": True, "needs_update": True}
        net._last_seen[update_addr] = -1000.0  # oldest: first to be evicted

        for i in range(100):
            addr = (f"10.8.0.{i % 250}", 8000 + i)
            net.datagram_received(_hello_datagram(), addr)

        assert session_addr in net.players
        assert session_addr in net._last_seen, "session addr evicted"
        assert session_addr in net.client_info, "session info evicted"
        assert update_addr in net.client_info, "update-pending addr evicted"
        assert update_addr in net._last_seen, "update-pending addr evicted"
    finally:
        netmod.MAX_TRACKED_ADDRS = old


def test_remove_player_drops_tables():
    """Leaving must clear all three tables, not just the session."""
    net = ServerNetwork()
    addr = ("10.7.0.1", 9001)
    net.client_info[addr] = {"protocol_ok": True}
    net._endpoint(addr)
    net.players[addr] = PlayerSession(1, "p", addr)
    net.remove_player(addr)
    assert addr not in net.players
    assert addr not in net._endpoints
    assert addr not in net.client_info
    assert addr not in net._last_seen


# -------------------------------------------------------------- S2: sessions
def test_session_cap_refuses_and_does_not_allocate():
    """Once at the cap, a JOIN is refused with a JOIN_FAIL and no new session.

    The refusal must be a real message (the client would otherwise wait forever)
    and must not bump the player-id counter, so ids stay dense.
    """
    old = netmod.MAX_SESSIONS
    netmod.MAX_SESSIONS = 3
    try:
        net = ServerNetwork()
        transport = _FakeTransport()
        net.transport = transport
        for i in range(6):
            addr = (f"10.1.0.{i}", 4000 + i)
            net.client_info[addr] = {"protocol_ok": True}
            net.datagram_received(_join_datagram(f"p{i}"), addr)
        assert len(net.players) == 3, len(net.players)
        # The three refusals produced reliable traffic (JOIN_FAIL); the accepted
        # three produced none from _handle_join itself.
        assert transport.sent, "refusal was silent"
        # No id was consumed for the refused joins.
        assert net._next_player_id == 4, net._next_player_id
    finally:
        netmod.MAX_SESSIONS = old


# ---------------------------------------------------------------- S3: parties
def test_party_member_cap():
    """A full party refuses the extra member and keeps the encoders valid."""
    with tempfile.TemporaryDirectory() as tmp:
        srv = GameServer(releases_dir=tmp)
        srv.network.transport = _FakeTransport()
        party = srv.lobby.create_party("P")
        for i in range(MAX_PARTY_MEMBERS):
            sess = PlayerSession(i + 1, f"p{i}", (f"10.0.0.{i}", 9500 + i))
            srv.network.players[sess.address] = sess
            srv.network.client_info[sess.address] = {"protocol_ok": True}
            srv._on_join_party(sess, party.id)
        assert len(party.members) == MAX_PARTY_MEMBERS

        extra = PlayerSession(99, "x", ("10.0.0.99", 9999))
        srv.network.players[extra.address] = extra
        srv.network.client_info[extra.address] = {"protocol_ok": True}
        srv._on_join_party(extra, party.id)
        assert len(party.members) == MAX_PARTY_MEMBERS
        assert extra.player_id not in party.members, "over-cap member joined"

        # The single-byte count is the reason for the cap: it must still encode.
        players = party.world.get_all_player_states()
        encode_world_state(0, players)
        snapshot = srv.lobby.get_snapshot()
        encode_lobby_state(snapshot)


def test_party_cap_on_create_is_the_only_member():
    """Creating a party never trips the member cap (creator is member #1)."""
    with tempfile.TemporaryDirectory() as tmp:
        srv = GameServer(releases_dir=tmp)
        srv.network.transport = _FakeTransport()
        sess = PlayerSession(1, "creator", ("10.5.0.1", 5001))
        srv.network.players[sess.address] = sess
        srv.network.client_info[sess.address] = {"protocol_ok": True}
        srv._on_create_party(sess, "Solo")
        party = srv.lobby.find_party_of(sess)
        assert party is not None and len(party.members) == 1


# ----------------------------------------------------------- S4: text hygiene
def test_control_characters_stripped():
    """Newlines/tabs/NULs must not survive into a log line or a name."""
    dirty = "evil\nPlayer joined: admin\x00\t\r\x9f"
    clean = sanitize_text(dirty)
    assert "\n" not in clean and "\t" not in clean and "\r" not in clean
    assert "\x00" not in clean and "\x9f" not in clean
    assert "evilPlayer joined: admin" == clean
    # Round trip through the wire stays clean.
    rt = _decode_string(_encode_string(dirty))[0]
    assert "\n" not in rt and "\x00" not in rt


def test_truncation_never_splits_a_character():
    """Byte truncation must cut on a character boundary, not mid-sequence.

    A naive ``bytes[:32]`` split of a multi-byte string produces an invalid
    UTF-8 tail; the decode on the other side then raises.  The sanitised value
    must always round-trip without error and stay within the byte budget.
    """
    text = "б" * 64              # 2 bytes each -> the 32-byte cut lands mid-char
    clean = sanitize_text(text)
    assert len(clean.encode("utf-8")) <= 32
    # Each survivor is a whole character.
    assert clean == "б" * 16, repr(clean)
    encoded = _encode_string(text)
    assert encoded[0] <= 32
    decoded, _ = _decode_string(encoded)
    assert decoded == "б" * 16


def test_join_request_sanitises_name():
    """A JOIN name arrives sanitised even when the payload is hostile."""
    from shared.protocol import decode_join_request
    payload = encode_join_request("x\ninject\ty")
    assert decode_join_request(payload) == "xinjecty"
    # Invalid UTF-8 in the name must not raise in the datagram handler.
    assert decode_join_request(b"\x04\xff\xfe\xfd\xff") is not None


def test_lobby_state_names_are_sanitised():
    """Party and player names are sanitised on the way onto the wire."""
    snapshot = [{
        "id": 1, "name": "P\narty", "mode": "pve",
        "players": [{"id": 2, "name": "b\r\nob"}],
    }]
    decoded = decode_lobby_state(encode_lobby_state(snapshot))
    assert decoded[0]["name"] == "Party"
    assert decoded[0]["players"][0]["name"] == "bob"


# ----------------------------------------------------------------- S5: ping
def test_non_finite_ping_is_neutralised():
    """inf/nan must not be echoed back as an RTT-poisoning timestamp."""
    assert decode_ping(encode_ping(float("nan"))) == 0.0
    assert decode_ping(encode_ping(float("inf"))) == 0.0
    assert decode_ping(encode_ping(float("-inf"))) == 0.0
    # A finite timestamp survives untouched.
    assert decode_ping(encode_ping(123.5)) == 123.5


def main():
    test_address_tables_are_bounded()
    test_address_tables_bounded_on_reliable_flood()
    test_pinned_addresses_survive_eviction()
    test_remove_player_drops_tables()
    test_session_cap_refuses_and_does_not_allocate()
    test_party_member_cap()
    test_party_cap_on_create_is_the_only_member()
    test_control_characters_stripped()
    test_truncation_never_splits_a_character()
    test_join_request_sanitises_name()
    test_lobby_state_names_are_sanitised()
    test_non_finite_ping_is_neutralised()
    print("SECURITY SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()
