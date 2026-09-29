"""Bridge between the multiplayer client state and the ported renderer.

The ported ROUT_II_III renderer reads every frame from the module-local
``game_state`` namespace (``gs``).  This bridge:
  * converts an integer-tile ``GameMap`` into the renderer's char grid,
  * publishes px-space server coordinates directly (server runs in px, same
    convention as ROUT_II_III),
  * fills ``gs.players`` (remote players), ``gs.enemies``, ``gs.corpses``
    and ``gs.bul`` (enemy projectiles) for the sprite loop,
  * drives ``gs.p`` weapon/ammo/fire from server state.
"""
from __future__ import annotations

import pygame

from client.engine import constants as const
from client.engine import game_state as gs
from client.engine import rendering
from client.engine import textures as texture_gen
from client.engine import particles
from client.engine.constants import ENEMY_DB as CLIENT_ENEMY_DB, MAX_PARTICLES
from client.engine.game_state import p as player_dict
from shared.protocol import ENEMY_TYPES

# Integer tile -> renderer tile character (see map_editor.TILE_CHAR_TO_TEX)
_TILE_TO_CHAR = {0: '.', 1: '#', 2: 'T', 3: 'W', 4: 'M', 5: 'I', 6: 'P'}
_TILE_DEFAULT_CHAR = '#'

# Player shirt colours, picked by player id.
PLAYER_COLORS = (
    (50, 130, 200), (205, 90, 45), (60, 175, 80), (175, 60, 150),
    (200, 200, 60), (80, 170, 215), (230, 120, 60), (150, 205, 120),
)

_AMMO_HUD_KEYS = ('pstl', 'shl', 'slg', 'rck', 'BFG')


def tile_char(tile: int) -> str:
    return _TILE_TO_CHAR.get(tile, _TILE_DEFAULT_CHAR)


def map_to_grid(tiles: list[list[int]]) -> list[str]:
    return [''.join(tile_char(t) for t in row) for row in tiles]


class RenderBridge:
    """Prepares ``gs`` for the engine and publishes per-frame state."""

    def __init__(self, surface: pygame.Surface, scale: int = const.SCALE):
        self._surface = surface
        gs.screen = pygame.Surface((const.W, const.H))
        gs.font = pygame.font.SysFont("monospace", 34, bold=True)
        gs.sm_font = pygame.font.SysFont("monospace", 18)
        gs.render_scale = scale
        gs.start_hp = 100
        gs.view_dist = const.VIEW_DIST_DEFAULT
        gs.fog_enabled = False
        gs.fog_density = const.FOG_DENSITY_DEFAULT
        texture_gen.gen_textures()
        # impact signature -> cooldown frames (avoid re-spawn every frame)
        self._impact_cooldown: dict[tuple, int] = {}

    def set_map(self, tiles: list[list[int]], size: int) -> None:
        gs.MAP = map_to_grid(tiles)
        gs.M_SZ = size
        gs.explored = [[1] * size for _ in range(size)]

    def update(
        self,
        px: float,
        py: float,
        angle: float,
        health: int,
        players: list[dict],
        enemies: list[dict] | None = None,
        bullets: list[dict] | None = None,
        corpses: list[dict] | None = None,
        weapon: int = 1,
        ammo: dict | None = None,
        fire_timer: int = 0,
        mode: str = 'pve',
        impacts: list[dict] | None = None,
    ) -> None:
        gs.px = px
        gs.py = py
        gs.pa = angle
        gs.pz = 0.0
        gs.pitch = 0.0
        gs.lvl_type = 'pvp' if mode == 'pvp' else 'pve'
        player_dict['hp'] = max(0, int(health))
        player_dict['wpn'] = weapon
        player_dict['guns'] = {0, 1, 2, 3, 4, 5}
        if ammo:
            for key in _AMMO_HUD_KEYS:
                player_dict[key] = int(ammo.get(key, 0))

        # Weapon animation timer: counts down from the weapon's fire rate in
        # frames (same convention as ROUT_II_III's gs.w_tmr).
        gs.w_tmr = max(0, int(fire_timer))
        gs.fire_flag = fire_timer > 0

        gs.players = []
        for pl in players:
            col = PLAYER_COLORS[pl['id'] % len(PLAYER_COLORS)]
            gs.players.append({
                'px': pl['x'],
                'py': pl['y'],
                'pa': pl['angle'],
                'name': pl.get('name', ''),
                'wpn': pl.get('wpn', 1),
                'col': col,
                'state': pl.get('state', 0),
            })

        gs.enemies = [self._enemy_dict(e) for e in (enemies or [])]
        gs.bul = []
        gs.proj = []
        for b in (bullets or []):
            entry = {
                'x': b['x'],
                'y': b['y'],
                'col': b.get('col', (255, 255, 0)),
                'from_player': bool(b.get('from_player', 0)),
            }
            if entry['from_player']:
                gs.proj.append(entry)
            else:
                gs.bul.append(entry)
        gs.corpses = [self._corpse_dict(c) for c in (corpses or [])]
        self._spawn_impact_particles(impacts)

    def _spawn_impact_particles(self, impacts: list[dict] | None) -> None:
        """Spawn local tracer particles for impact events (deduped + budgeted)."""
        self._impact_cooldown = {k: v - 1 for k, v in self._impact_cooldown.items() if v > 1}
        for im in (impacts or []):
            sig = (int(im['x'] / 8), int(im['y'] / 8), im.get('type', 'wall'))
            if sig in self._impact_cooldown:
                continue
            self._impact_cooldown[sig] = 12  # re-allow after ~12 frames
            if len(gs.particles) >= MAX_PARTICLES:
                continue
            x, y = im['x'], im['y']
            if im.get('type', 'wall') == 'enemy':
                particles.spawn_particles(x, y, (200, 60, 40), 8, 3, 20)
                particles.spawn_sparks(x, y, 4, 4)
            else:
                particles.spawn_sparks(x, y, 6, 3)
                particles.spawn_smoke_trail(x, y, (170, 160, 150))

    @staticmethod
    def _enemy_dict(e: dict) -> dict:
        etype = e.get('type', 0)
        if isinstance(etype, int) and 0 <= etype < len(ENEMY_TYPES):
            name = ENEMY_TYPES[etype]
        else:
            name = str(etype)
        db = CLIENT_ENEMY_DB.get(name, {})
        col = e.get('col') or db.get('col', (200, 200, 200))
        hp = e.get('hp', 0)
        state = e.get('state', 0)
        return {
            'x': e['x'], 'y': e['y'],
            'pa': e.get('angle', 0.0),
            'a': state == 0 and hp > 0,
            'dying': 0 if hp > 0 else 15,
            't': name,
            'col': col,
            'hp': hp,
            'max_hp': db.get('hp', hp),
            'd_flash': e.get('d_flash', 0),
            'stun': 0,
            'alert': 0,
            'rush_timer': 0,
            'anim_frame': 0,
            'anim_state': 'walk' if hp > 0 else 'death',
            'ls': 0,
        }

    @staticmethod
    def _corpse_dict(c: dict) -> dict:
        etype = c.get('type', 0)
        if isinstance(etype, int) and 0 <= etype < len(ENEMY_TYPES):
            name = ENEMY_TYPES[etype]
        else:
            name = str(etype)
        db = CLIENT_ENEMY_DB.get(name, {})
        return {
            'x': c['x'], 'y': c['y'],
            't': name,
            'col': db.get('col', (200, 200, 200)),
            'time': 300,
        }

    def render(self) -> None:
        rendering.render()
        self._surface.blit(gs.screen, (0, 0))