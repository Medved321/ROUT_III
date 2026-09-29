"""Smoke: content providers wired through ``engine.game_state`` (updating_engine 6).

The engine creates no sprites and knows no editor palette, so both arrive as
``gs.sprite_provider`` / ``gs.tile_provider``.  Two scenarios:

  1. providers registered (as ``client/bridge.py`` does) -- a tile frame with a
     bot renders the bot with a non-empty surface and maps different tile
     characters to different texture names;
  2. no providers at all -- ``rendering.render()`` must not raise, the frame
     buffer must stay non-empty, and every wall must fall back to 'wall'.

Run: ``py -3.12 smoke_content_providers.py``.
"""
from __future__ import annotations

import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pygame  # noqa: E402

from engine import game_state as gs  # noqa: E402
from engine import rendering  # noqa: E402
from engine import textures as texgen  # noqa: E402
from engine.constants import W, H, SCALE, TILE  # noqa: E402

# Room with two different wall materials so the texture check has teeth: '#'
# is brick, 'T' is stone.  Both must survive the provider round-trip.
ROOM = [
    "#########",
    "#TTTTT#T#",
    "#.......#",
    "#.......#",
    "#.......#",
    "#########",
]
BOT_X, BOT_Y = 3.5 * TILE, 3.0 * TILE


def _init():
    pygame.init()
    gs.screen = pygame.display.set_mode((W, H))
    gs.font = pygame.font.SysFont("monospace", 34, bold=True)
    gs.sm_font = pygame.font.SysFont("monospace", 18)
    gs.fog_enabled = False
    gs.view_dist = 900
    gs.render_scale = SCALE
    gs.vs_scale = 2
    gs.flat_textured = True
    gs.sector_mode = False
    gs.sector_grid = []
    gs.sectors = []
    gs.bridge_open = set()
    gs.dwall_hp = {}
    gs.explored = []
    gs.particles = []
    gs.decals = []
    gs.weather = []
    gs.enemies = []
    gs.items = []
    gs.bul = []
    gs.proj = []
    gs.corpses = []
    gs.portals = []
    gs.barrels = []
    gs.bots = []
    gs.players = []
    gs.exit_pos = (0, 0)
    gs.show_minimap = False
    gs.show_crosshair = False
    gs.p['guns'] = {0, 1}
    gs.MAP = [list(r) for r in ROOM]
    gs.M_SZ = len(ROOM)
    texgen.gen_textures()
    from engine.wallcache import DEFAULT_CACHE
    DEFAULT_CACHE.clear_tex()


def _place_bot():
    """One bot in front of the player, facing them (front view)."""
    gs.px, gs.py, gs.pa = 1.5 * TILE, 3.0 * TILE, 0.0
    gs.bots = [{
        'x': BOT_X, 'y': BOT_Y,
        'pa': 3.14159265,          # looking back at the player -> 'front'
        'col': (200, 40, 40),
        'hp': 150,
        'wpn': 1,
        'anim_frame': 0,
        'anim_state': 'idle',
    }]


def _render():
    gs.w_tmr = 0
    rendering.render()


def test_providers_registered():
    from client.bridge import register_content_providers
    from client.map_editor import TILE_CHAR_TO_TEX
    from client.sprite_factory import get_bot_surface

    register_content_providers()
    assert gs.sprite_provider is get_bot_surface
    assert callable(gs.tile_provider)
    # Registration is idempotent: calling it twice changes nothing.
    register_content_providers()
    assert gs.sprite_provider is get_bot_surface

    # (b) different tile characters -> different texture names.
    assert TILE_CHAR_TO_TEX['#'] != TILE_CHAR_TO_TEX['T']
    assert gs.tile_provider('#') == TILE_CHAR_TO_TEX['#']
    assert gs.tile_provider('T') == TILE_CHAR_TO_TEX['T']
    assert gs.tile_provider('?') == 'wall', "unknown tile must degrade to 'wall'"
    print("  providers registered, tile chars map to distinct textures OK")

    # (a) the bot is drawn from a non-empty surface.
    surf = gs.sprite_provider((200, 40, 40), 64, 64, 0, 'idle', 3.14159265, 1,
                             view='front')
    assert surf.get_width() == 64 and surf.get_height() == 64
    box = surf.get_bounding_rect()
    assert box.width > 0 and box.height > 0, "bot surface is empty"
    opaque = sum(1 for y in range(box.top, box.bottom, 4)
                 for x in range(box.left, box.right, 4)
                 if surf.get_at((x, y))[3] > 0)
    assert opaque > 0, "bot surface fully transparent"

    _place_bot()
    before = gs.screen.get_at((W // 2, H // 2))[:3]
    _render()
    after = gs.screen.get_at((W // 2, H // 2))[:3]
    assert gs.frame is not None
    assert max(gs.frame.buf) > 0, "frame buffer empty (VOID)"
    assert after != before, f"bot did not change the frame: {after}"
    print("  bot drawn through gs.sprite_provider                OK")
    gs.bots = []

    # The engine really asks the provider for every wall: a stub that returns
    # 'wall' for everything must produce a different frame than the real
    # table (brick vs stone), i.e. the 'T' wall is not silently flattened.
    gs.bots = []
    _render()
    real_frame = bytes(gs.frame.buf)
    gs.tile_provider = lambda ch: 'wall'
    _render()
    neutral_frame = bytes(gs.frame.buf)
    assert real_frame != neutral_frame, \
        "gs.tile_provider ignored: 'T' and '#' walls render identically"
    print("  engine consults gs.tile_provider per wall          OK")


def test_providers_absent():
    gs.sprite_provider = None
    gs.tile_provider = None

    _place_bot()
    _render()  # must degrade, not raise
    assert gs.frame is not None
    assert len(gs.frame.buf) == gs.frame.iw * gs.frame.ih * 3
    assert max(gs.frame.buf) > 0, "frame buffer empty without providers"
    print("  render without providers does not raise            OK")

    # No provider == a provider that answers 'wall' for every character.
    no_provider_frame = bytes(gs.frame.buf)
    gs.bots = []
    _render()
    wall_only_frame = bytes(gs.frame.buf)
    gs.tile_provider = lambda ch: 'wall'
    _render()
    assert bytes(gs.frame.buf) == wall_only_frame, \
        "no-provider fallback is not the neutral 'wall' texture"
    print("  no provider == neutral 'wall' fallback             OK")

    # The blank bot sprite contributes no pixels: the frame is identical with
    # and without the bot in the list.
    assert no_provider_frame == wall_only_frame, "blank bot sprite still drew"
    print("  blank bot sprite draws nothing                     OK")


def main():
    _init()
    print("content providers through engine.game_state:")
    test_providers_registered()
    test_providers_absent()
    print("smoke_content_providers: ALL OK")


if __name__ == "__main__":
    main()
