"""Bridge between the multiplayer client state and the ported renderer.

The ported ROUT_II_III renderer reads every frame from the module-local
``game_state`` namespace (``gs``).  This bridge:
  * converts an integer-tile ``GameMap`` into the renderer's char grid,
  * publishes px-space server coordinates directly (server runs in px, same
    convention as ROUT_II_III),
  * fills ``gs.players`` (remote players), ``gs.enemies``, ``gs.corpses``
    and ``gs.bul`` (enemy projectiles) for the sprite loop,
  * drives ``gs.p`` weapon/ammo/fire from server state,
  * registers the content providers the engine consumes from ``gs``
    (sprites, tile->texture), so the engine stays free of content creation.
"""
from __future__ import annotations

import math

import pygame

from engine import constants as const
from engine import game_state as gs
from engine import rendering
from engine import textures as texture_gen
from engine import particles
from engine.constants import ENEMY_DB as CLIENT_ENEMY_DB, MAX_PARTICLES
from engine.game_state import p as player_dict
from engine.sprite_cache import DEFAULT_SPRITE_CACHE
from shared.protocol import ENEMY_TYPES

from client.enemy_models import ENEMY_SURFACES
from client.map_editor import CHAR_TO_TILE, TILE_CHAR_TO_TEX
from client.sprite_factory import get_bot_surface

# Integer tile -> renderer tile character.  Inverted once from the single
# source of truth (client/map_editor.py), which also owns
# ``TILE_CHAR_TO_TEX`` -- the wall texture per tile character.
_TILE_TO_CHAR = {v: k for k, v in CHAR_TO_TILE.items()}
_TILE_DEFAULT_CHAR = '#'


def tile_texture(tile_char: str) -> str:
    """Wall texture name for a renderer tile character.

    Registered as ``gs.tile_provider``; an unknown character falls back to the
    neutral 'wall' texture exactly like the engine's no-provider path.
    """
    return TILE_CHAR_TO_TEX.get(tile_char, 'wall')


def register_content_providers() -> None:
    """Publish this client's content factories to the engine (updating_engine D2).

    Idempotent: plain assignment, so the order of calls does not matter as long
    as it happens before the first rendered frame.  Called at import time so the
    providers are live from client startup, not only after joining a party.
    """
    gs.sprite_provider = get_bot_surface
    gs.tile_provider = tile_texture
    gs.enemy_surface_provider = ENEMY_SURFACES


register_content_providers()

# Player shirt colours, picked by player id.
PLAYER_COLORS = (
    (50, 130, 200), (205, 90, 45), (60, 175, 80), (175, 60, 150),
    (200, 200, 60), (80, 170, 215), (230, 120, 60), (150, 205, 120),
)

_AMMO_HUD_KEYS = ('pstl', 'shl', 'slg', 'rck', 'BFG')

# Slot for the local player in the dash bookkeeping above: ``players`` only ever
# holds the others, but the local dash is the one that gets screen streaks.
LOCAL_DASH_ID = -1


def tile_char(tile: int) -> str:
    return _TILE_TO_CHAR.get(tile, _TILE_DEFAULT_CHAR)


def map_to_grid(tiles: list[list[int]]) -> list[str]:
    return [''.join(tile_char(t) for t in row) for row in tiles]


class RenderBridge:
    """Prepares ``gs`` for the engine and publishes per-frame state."""

    def __init__(self, surface: pygame.Surface, scale: int | None = None):
        self._surface = surface
        self.set_output_surface(surface)
        # ``gs.font``/``gs.sm_font`` are sized in layout units and owned by
        # ``engine.viewport.ensure_hud_fonts()``, which render() calls every
        # frame -- building them here froze one window's text size for the whole
        # session and made the HUD unreadable at every other resolution.
        # ``gs.render_scale``/``gs.view_dist`` are owned by ``Settings``
        # (``apply_runtime``); never reset them to import-time defaults here.
        if scale is not None:
            gs.render_scale = scale
        gs.start_hp = 100
        gs.fog_enabled = False
        gs.fog_density = const.FOG_DENSITY_DEFAULT
        texture_gen.gen_textures()
        # impact signature (shot id, cell, type) -> dedupe across broadcasts.
        # Server-shot ids are monotonic, so back-to-back hits on the same
        # point still spawn fresh tracers. Old signatures are dropped once the
        # impact expires from the world state.
        self._impact_seen: set = set()
        # Dash feedback, per player id: was dashing last frame, distance walked
        # since the last trail emission, and the position that distance is
        # measured from. Keyed by a player id, with LOCAL_DASH_ID standing in
        # for the local player (he is not in ``players`` -- the client does not
        # draw his own body). ``state`` only changes in 20 Hz world snapshots
        # while this runs once per frame, so the effect is driven by the rising
        # edge of the authoritative flag, never by "state == 3" per frame.
        self._dash_active: dict[int, bool] = {}
        self._dash_px: dict[int, float] = {}
        self._dash_pos: dict[int, tuple[float, float]] = {}
        # Per-enemy animation bookkeeping.  Keyed by the authoritative enemy
        # id from the snapshot, so a state change restarts its phase instead
        # of jumping mid-cycle, and an id that vanishes from a snapshot (it
        # died) is forgotten with it.  The snapshot carries states but no
        # animation frames, and 20 Hz snapshots must not drive a 60 fps
        # animation, so the frame index is derived locally from this clock.
        self._anim_state: dict = {}
        self._anim_started: dict = {}
        # Last seen position/health per enemy id.  The wire format carries
        # only id/x/y/angle/type/hp/state (shared/protocol.py), so "is it
        # walking", "did it just get hit" and "is it swinging" are all
        # *derived* here rather than sent.  No protocol change, no new truth.
        self._enemy_prev: dict = {}
        self._clock = 0.0
        self._last_tick = pygame.time.get_ticks()

    # -- enemy animation ---------------------------------------------------

    def _reset_enemy_anim(self) -> None:
        self._anim_state.clear()
        self._anim_started.clear()
        self._enemy_prev.clear()

    def advance_clock(self, dt_ms: float) -> None:
        """Advance the client animation clock (ms since the party started)."""
        self._clock += max(0.0, float(dt_ms))

    def _enemy_anim(self, e: dict, key) -> tuple[str, int]:
        """``(state, frame)`` for one enemy, restarting phase on a change.

        ``design D7``: the state comes from the authoritative fields, the
        frame is a local derivative of that state, so a dropped snapshot
        cannot leave an enemy stuck mid-cycle.
        """
        from client.enemy_models import anim_frame_of, anim_state_of, model_for

        state = anim_state_of(e, self._clock)
        if self._anim_state.get(key) != state:
            self._anim_state[key] = state
            self._anim_started[key] = self._clock
        model = model_for(e['t'])
        frame = anim_frame_of(model, state, self._clock,
                              self._anim_started.get(key, self._clock))
        return state, frame

    def set_output_surface(self, surface: pygame.Surface) -> None:
        """Point the engine at the surface the frame must end up on.

        Normally the display surface, so ``render()`` ends the frame with a
        single blit. A window resize hands us a *new* ``Surface`` object (the
        old one is invalidated by ``set_mode``), so this is called again on
        every resize. The engine's own output surface is re-allocated to the
        new ``const.W``/``const.H``, which the renderer has already updated --
        the engine draws for that pixel grid and must not keep the old one.
        """
        self._surface = surface
        gs.screen = pygame.Surface((const.W, const.H))

    def set_map(self, tiles: list[list[int]], size: int) -> None:
        gs.MAP = map_to_grid(tiles)
        gs.M_SZ = size
        gs.explored = [[1] * size for _ in range(size)]
        gs.sector_mode = False
        gs.sec_scene = None
        # Model surfaces are keyed by type/view/frame/detail/size, none of which
        # depend on the map, so they would survive a map change -- but a new map
        # means a new set of types and detail levels, and the cache is bounded,
        # so a clean slate is cheaper than reasoning about what stayed valid.
        # The engine-side sprite cache is keyed the same way, so it goes too.
        ENEMY_SURFACES.clear()
        DEFAULT_SPRITE_CACHE.clear()
        self._reset_enemy_anim()

    def set_sector_map(self, sm) -> None:
        """Activate the sector renderer for a ``SectorMap`` party (3.7)."""
        from engine import sector_render
        from shared.sector_map import SectorMap

        if not isinstance(sm, SectorMap):
            raise TypeError("set_sector_map expects a SectorMap")
        ENEMY_SURFACES.clear()
        DEFAULT_SPRITE_CACHE.clear()
        self._reset_enemy_anim()
        scene = sector_render.SectorScene(sm)
        gs.sector_mode = True
        gs.sec_scene = scene
        gs.MAP = []
        gs.M_SZ = 0
        gs.explored = []
        # Legacy fields still read by map_utils / other render paths.
        gs.sec_verts = [(v.x, v.y) for v in sm.verts]
        gs.sec_lines = [
            [ld.v1, ld.v2,
             scene.sector_of_side(ld.front),
             scene.sector_of_side(ld.back),
             ld.flags & 0xF]
            for ld in sm.linedefs
        ]
        gs.sec_sectors = [
            {
                'fh': s.floor_h, 'ch': s.ceil_h,
                'fc': scene.flat_color(s.floor_tex),
                'cc': scene.flat_color(s.ceil_tex),
                'wc': scene.flat_color('wall'),
            }
            for s in sm.sectors
        ]
        # Exit marker thing, or None (default (0, 0) is truthy and would draw).
        exit_thing = next((t for t in sm.things if t.type in ('exit', 'E')), None)
        gs.exit_pos = (exit_thing.x, exit_thing.y) if exit_thing is not None else None

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
        local_state: int = 0,
        fps: float = 0.0,
    ) -> None:
        # The animation clock runs on the frame period, not on snapshots, so a
        # 20 Hz snapshot stream still animates at the display rate.  Fed here
        # because update() is the one call that happens once per rendered
        # frame, before the enemy entries are built.
        self.advance_clock(pygame.time.get_ticks() - self._last_tick)
        self._last_tick = pygame.time.get_ticks()
        gs.px = px
        gs.py = py
        gs.pa = angle
        gs.pz = 0.0
        gs.pitch = 0.0
        # Measured by the caller, not by the engine: a clock lives in the
        # client, and gs.fps is only the readout for the HUD.
        gs.fps = float(fps)
        gs.lvl_type = 'pvp' if mode == 'pvp' else 'pve'
        player_dict['hp'] = max(0, int(health))
        player_dict['wpn'] = weapon
        player_dict['guns'] = {0, 1, 2, 3, 4, 5}
        if ammo:
            for key in _AMMO_HUD_KEYS:
                player_dict[key] = int(ammo.get(key, 0))

        # Weapon animation timer: how much of the weapon cooldown is left,
        # counted in frames at 60 fps (the convention ROUT_II_III used for
        # ``gs.w_tmr``, and the unit the renderer divides by ``WPN[i]['f']``).
        # The client derives it from a monotonic deadline, so the same value
        # means the same amount of wall-clock time at any frame rate. Any
        # non-negative frame count is accepted; the renderer clamps it.
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

        gs.enemies = [self._enemy_dict(e, i)
                      for i, e in enumerate(enemies or [])]
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
        self._dash_fx(
            [(pl['id'], pl['x'], pl['y'], pl.get('state', 0),
              PLAYER_COLORS[pl['id'] % len(PLAYER_COLORS)])
             for pl in players]
            + [(LOCAL_DASH_ID, px, py, local_state, const.DASH_FX_COLOR)],
        )

    def _dash_fx(self, slots):
        """Burst on the rising edge of ``state == 3``, dust trail while it holds.

        ``slots`` is a list of ``(sid, x, y, state, col)`` -- one entry per
        player, local one included. The trail is emitted per DASH_FX_TRAIL_PX of
        distance covered, not per frame, so it looks the same at 30 and at 240
        fps and stays silent when a dash runs into a wall. The travel direction
        comes from the position delta, which is the only place it is known for
        both remote and local players.
        """
        for sid, x, y, state, col in slots:
            prev = self._dash_active.get(sid, False)
            now = state == 3
            if not now:
                # Falling edge (or a player who was never dashing): drop the slot
                # so a lost or skipped snapshot cannot leave a phantom trail.
                if prev:
                    self._dash_active.pop(sid, None)
                    self._dash_px.pop(sid, None)
                    self._dash_pos.pop(sid, None)
                continue
            if not prev:
                particles.spawn_dash_burst(x, y, col)
                self._dash_active[sid] = True
                self._dash_px[sid] = 0.0
                if sid == LOCAL_DASH_ID:
                    gs.dash_fx = 1.0
            last = self._dash_pos.get(sid)
            self._dash_pos[sid] = (x, y)
            if last is None:
                continue
            dx, dy = x - last[0], y - last[1]
            moved = math.hypot(dx, dy)
            if moved <= 0.0:
                continue
            acc = self._dash_px.get(sid, 0.0) + moved
            while acc >= const.DASH_FX_TRAIL_PX:
                acc -= const.DASH_FX_TRAIL_PX
                particles.spawn_dash_puff(x, y, col, math.atan2(dy, dx))
            self._dash_px[sid] = acc

    def _spawn_impact_particles(self, impacts: list[dict] | None) -> None:
        """Spawn local tracer particles for impact events (deduped + budgeted)."""
        impacts = impacts or []
        # Only keep signatures that are still alive server-side; expired
        # impacts vanish from the world state and free their slot.
        active = {
            (im.get('id', -1), int(im['x'] / 8), int(im['y'] / 8), im.get('type', 'wall'))
            for im in impacts
        }
        self._impact_seen.intersection_update(active)
        for im in impacts:
            sig = (im.get('id', -1), int(im['x'] / 8), int(im['y'] / 8), im.get('type', 'wall'))
            if sig in self._impact_seen:
                continue
            self._impact_seen.add(sig)
            if len(gs.particles) >= particles._effect_limit(MAX_PARTICLES):
                continue
            x, y = im['x'], im['y']
            if im.get('type', 'wall') == 'enemy':
                particles.spawn_particles(x, y, (200, 60, 40), 8, 3, 20)
                particles.spawn_sparks(x, y, 4, 4)
            else:
                particles.spawn_sparks(x, y, 6, 3)
                particles.spawn_smoke_trail(x, y, (170, 160, 150))

    #: A snapshot moves an enemy in map units; this is the per-snapshot
    #: displacement above which we call it "walking".  Enemy speed is a few
    #: units per tick, so 0.35 filters out float jitter without missing a
    #: real step, and it is independent of frame rate because snapshots are
    #: what it is measured on.
    _MOVE_EPS = 0.35
    #: A lunge (the rush attack) covers several times the walk distance in one
    #: snapshot.  There is no attack flag on the wire, so a big single-step
    #: jump is the client's only honest signal that a swing is under way.
    _LUNGE_EPS = 2.2
    #: How long a lunge keeps the attack pose, and how many snapshots the pain
    #: and damage-flash poses last.  Decayed in snapshot steps so they fade in
    #: wall-clock time rather than in "snapshots per second".
    _ATK_MS = 220
    _STUN_FRAMES = 4
    _FLASH_FRAMES = 5

    def _enemy_dict(self, e: dict, index: int) -> dict:
        etype = e.get('type', 0)
        if isinstance(etype, int) and 0 <= etype < len(ENEMY_TYPES):
            name = ENEMY_TYPES[etype]
        else:
            name = str(etype)
        db = CLIENT_ENEMY_DB.get(name, {})
        col = e.get('col') or db.get('col', (200, 200, 200))
        hp = e.get('hp', 0)
        state = e.get('state', 0)
        # The id is authoritative and stable across snapshots, so it keys both
        # the animation phase and the derived motion/health state.  Anything
        # without one (a local test, a mod snapshot) falls back to the slot.
        key = e['id'] if e.get('id') is not None else index
        prev = self._enemy_prev.get(key)
        x, y = e['x'], e['y']
        ls = int(e.get('ls', 0) or 0)
        entry = {
            'x': x, 'y': y,
            'pa': e.get('angle', 0.0),
            'a': state == 0 and hp > 0,
            'dying': 0 if hp > 0 else 15,
            't': name,
            'col': col,
            'hp': hp,
            'max_hp': db.get('hp', hp),
            # Carried over from the previous snapshot of this same id, or 0.
            'd_flash': 0,
            'stun': 0,
            'alert': 0,
            'rush_timer': 0,
            'ls': ls,
            'moving': False,
        }
        if prev is not None:
            px, py, php, p_ls, p_flash, p_stun = prev
            moved = ((x - px) ** 2 + (y - py) ** 2) ** 0.5
            if moved > self._LUNGE_EPS:
                # A swing has its own pose, and it outlives the one snapshot
                # the displacement was visible in.
                entry['ls'] = self._clock
            elif p_ls and self._clock - p_ls < self._ATK_MS:
                entry['ls'] = p_ls
            if hp < php:
                entry['d_flash'] = self._FLASH_FRAMES
                entry['stun'] = self._STUN_FRAMES
            else:
                entry['d_flash'] = max(0, p_flash - 1)
                entry['stun'] = max(0, p_stun - 1)
            entry['moving'] = moved > self._MOVE_EPS
        else:
            entry['moving'] = bool(e.get('moving', hp > 0))
        # Stun is a pain pose and it wins over walk, so the ordering here is
        # irrelevant -- but the flags are read by anim_state_of in one place.
        entry['anim_state'], entry['anim_frame'] = self._enemy_anim(entry, key)
        self._enemy_prev[key] = (x, y, hp, entry['ls'], entry['d_flash'],
                                 entry['stun'])
        return entry

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