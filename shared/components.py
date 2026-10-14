from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class Component:
    pass


@dataclass
class Position(Component):
    x: float = 0.0
    y: float = 0.0


@dataclass
class Velocity(Component):
    dx: float = 0.0
    dy: float = 0.0


@dataclass
class Rotation(Component):
    angle: float = 0.0


@dataclass
class Health(Component):
    value: int = 100
    max_value: int = 100


@dataclass
class Player(Component):
    player_id: int = 0
    name: str = ""
    state: int = 0  # 0=alive, 1=dead, 2=shooting


@dataclass
class WeaponComponent(Component):
    """Server-side weapon state for a player."""
    current: int = 1        # index into WPN (0=chainsaw, 1=pistol, ...)
    owned: set = field(default_factory=lambda: {0, 1, 2, 3, 4, 5})
    ammo: dict = field(default_factory=lambda: {
        'pstl': 200, 'shl': 40, 'slg': 100, 'rck': 10, 'BFG': 5,
    })
    fire_cooldown: int = 0  # ticks remaining before next shot


@dataclass
class Armor(Component):
    """Server-side armor: soaks damage before ``Health`` does.

    ``kind`` is ``'green'`` / ``'blue'`` and only picks the HUD colour -- both
    behave identically, so there is no second rule to keep in sync.  Absorb is
    total, not a fraction of the hit (see ``ARMOR_MAX``).
    """
    value: int = 0
    max_value: int = 200
    kind: str = "green"


@dataclass
class Powerup(Component):
    """Server-side timed buff.

    ``kind`` is ``'BERSERK'`` / ``'HASTE'`` / ``'INVULN'`` -- the same uppercase
    spellings the HUD matches on, so the snapshot carries it verbatim.  ``ticks``
    counts down in server ticks and the buff is gone at zero; a re-pickup
    refreshes it rather than stacking a second copy of the same effect.
    """
    kind: str = ""
    ticks: int = 0


@dataclass
class Keys(Component):
    """Per-player key bitmask (see ``shared.sector_map.KEY_BITS``).

    Bits: red = 0x01, yellow = 0x02, blue = 0x04.  A key is a normal pickup
    that never respawns on a timer -- the level restart puts it back on the map
    (design D9).
    """
    mask: int = 0


@dataclass
class Dash(Component):
    """Server-side dash state for a player.

    ``ticks`` counts down while the dash bonus speed is applied; ``cooldown``
    gates the next dash and is set by the dash start itself, which makes the
    start idempotent when the same input packet is processed twice.
    """
    ticks: int = 0        # ticks of dash speed remaining
    cooldown: int = 0     # ticks until the next dash is allowed


@dataclass
class Enemy(Component):
    """Server-side enemy (PvE)."""
    enemy_type: str = "soldier"
    ai_state: str = "idle"       # idle | chase | attack | dying | dead
    cooldown: int = 0            # attack cooldown ticks
    target_id: int = -1          # player_id being chased
    wander_tick: int = 0


@dataclass
class Corpse(Component):
    """Marker for a dead body that should fade / be recycled."""
    tick_left: int = 600


@dataclass
class Collider(Component):
    radius: float = 0.3


@dataclass
class Sprite(Component):
    texture_id: int = 0


class EntityManager:
    def __init__(self):
        self._next_id: int = 1
        self._entities: dict[int, dict[type, Component]] = {}

    def create(self) -> int:
        eid = self._next_id
        self._next_id += 1
        self._entities[eid] = {}
        return eid

    def destroy(self, entity_id: int) -> None:
        self._entities.pop(entity_id, None)

    def add_component(self, entity_id: int, component: Component) -> None:
        self._entities[entity_id][type(component)] = component

    def get_component(self, entity_id: int, comp_type: type) -> Component | None:
        return self._entities.get(entity_id, {}).get(comp_type)

    def remove_component(self, entity_id: int, comp_type: type) -> None:
        entity = self._entities.get(entity_id)
        if entity:
            entity.pop(comp_type, None)

    def query(self, *comp_types: type) -> list[tuple[int, ...]]:
        result = []
        for eid, comps in self._entities.items():
            if all(ct in comps for ct in comp_types):
                result.append((eid, comps))
        return result

    def has(self, entity_id: int) -> bool:
        return entity_id in self._entities

    def all_entities(self) -> dict[int, dict[type, Component]]:
        return self._entities
