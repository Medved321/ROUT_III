from __future__ import annotations

import logging

from server.game_world import GameWorld
from server.player import PlayerSession

logger = logging.getLogger(__name__)


class Party:
    def __init__(self, party_id: int, name: str, mode: str = 'pve', game_map=None):
        self.id = party_id
        self.name = name
        self.mode = mode  # 'pve' or 'pvp'
        self.world = GameWorld(mode=mode, game_map=game_map)
        self.members: dict[int, PlayerSession] = {}  # player_id -> session

    def add_member(self, session: PlayerSession) -> int:
        entity_id = self.world.add_player(session.player_id, session.name)
        self.members[session.player_id] = session
        session.party_id = self.id
        return entity_id

    def remove_member(self, session: PlayerSession) -> None:
        self.world.remove_player(session.player_id)
        self.members.pop(session.player_id, None)
        session.party_id = None

    def has_member(self, session: PlayerSession) -> bool:
        return session.player_id in self.members

    def is_empty(self) -> bool:
        return len(self.members) == 0

    def update(self) -> None:
        self.world.update()


class Lobby:
    def __init__(self):
        self.parties: dict[int, Party] = {}
        self._next_party_id: int = 1

    def create_party(self, name: str, mode: str = 'pve', game_map=None) -> Party:
        party = Party(self._next_party_id, name, mode, game_map=game_map)
        self._next_party_id += 1
        self.parties[party.id] = party
        logger.info(f"Party created: {name} (id={party.id}, mode={mode})")
        return party

    def get_party(self, party_id: int) -> Party | None:
        return self.parties.get(party_id)

    def find_party_of(self, session: PlayerSession) -> Party | None:
        for party in self.parties.values():
            if party.has_member(session):
                return party
        return None

    def remove_player(self, session: PlayerSession) -> Party | None:
        party = self.find_party_of(session)
        if party:
            party.remove_member(session)
            if party.is_empty():
                del self.parties[party.id]
        return party

    def update_all(self) -> None:
        empty = []
        for party in self.parties.values():
            party.update()
            if party.is_empty():
                empty.append(party.id)
        for party_id in empty:
            del self.parties[party_id]

    def get_snapshot(self) -> list[dict]:
        snapshot = []
        for party in self.parties.values():
            players = [
                {"id": session.player_id, "name": session.name}
                for session in party.members.values()
            ]
            snapshot.append({
                "id": party.id, "name": party.name, "mode": party.mode,
                "players": players,
            })
        return snapshot