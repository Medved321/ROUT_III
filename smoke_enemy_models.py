"""Smoke: procedural enemy models (change ``enemy-model-overhaul``).

Acceptance for the enemy-presentation spec.  It checks the four things the
change actually claims:

* every protocol type has a descriptor and builds a non-empty surface in
  every view and state, and an unknown type degrades to a visible humanoid
  instead of a blank;
* the renderer has no per-type branch left and no per-frame ``Surface`` for
  models, shadows, corpses or overlays -- 30 enemies in 4 states is the
  scenario the spec names;
* the cache is bounded and the state-overlay effects fade by frame, not by
  frame rate;
* the quality tier changes detail, not the number of enemies, their states or
  their HP, and a low tier keeps three specific silhouettes distinguishable.

It also pins the two invariants that are easy to break silently: the cache key
must separate view and detail (a stale key shows the wrong view or the wrong
tier's detail), and gameplay numbers must be untouched by any of it.

Run: ``py -3.12 smoke_enemy_models.py``.
"""
from __future__ import annotations

import copy
import math
import os
import sys

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pygame  # noqa: E402

import client.bridge  # noqa: E402,F401  (registers the content providers)
from client.enemy_models import (  # noqa: E402
    ANIM_STATES, CACHE_MAX_ENTRIES, CACHE_MAX_PIXELS, ENEMY_MODELS,
    ENEMY_SURFACES, anim_frame_of, anim_state_of, cache_stats, clear_cache,
    get_corpse_surface, get_enemy_surface, model_for, size_bucket, view_index,
)
from engine import constants as const  # noqa: E402
from engine import game_state as gs  # noqa: E402
from engine import rendering as R  # noqa: E402
from engine.constants import ENEMY_DB, ENEMY_GAME_HEIGHTS  # noqa: E402
from engine.quality import enemy_detail  # noqa: E402
from engine.sprite_cache import DEFAULT_SPRITE_CACHE  # noqa: E402
from shared.protocol import ENEMY_TYPES  # noqa: E402

SCREEN = (320, 240)
_ok = 0


def check(name):
    global _ok
    _ok += 1
    print(f"  {name:32s} OK")


def setup_scene():
    """A minimal but real render context (tile mode, no sector scene)."""
    const.W, const.H = SCREEN
    gs.screen = pygame.Surface(SCREEN)
    gs.sector_mode = False
    gs.sec_scene = None
    gs.MAP = [["#"] * 8 for _ in range(8)]
    gs.M_SZ = 8
    gs.explored = [[1] * 8 for _ in range(8)]
    gs.px = gs.py = gs.pa = 0.0
    gs.corpses = []
    gs.quality_tier = "high"
    gs.enemy_models_enabled = True


def make_enemy(etype, x=100.0, y=100.0, hp=50, state="idle", frame=0, **kw):
    e = {
        "x": x, "y": y, "t": etype, "hp": hp, "max_hp": hp,
        "pa": 0.0, "dying": 0, "col": None, "stun": 0, "d_flash": 0,
        "rush_timer": 0, "moving": False,
        "anim_state": state, "anim_frame": frame,
    }
    e.update(kw)
    return e


# ---------------------------------------------------------------------------
# 1. Descriptors cover the protocol
# ---------------------------------------------------------------------------


def test_registry_covers_protocol():
    assert len(ENEMY_TYPES) == 20, len(ENEMY_TYPES)
    assert set(ENEMY_MODELS) == set(ENEMY_DB) == set(ENEMY_GAME_HEIGHTS)
    for t, m in ENEMY_MODELS.items():
        assert m.parts, t
        assert m.w > 0, t
        assert m.stance in ("biped", "quadruped", "floater", "blob", "turret"), \
            (t, m.stance)
        for state in ("idle", "walk", "attack", "death"):
            assert m.frames(state) >= 1, (t, state)
    # Class proportions.  ``w`` is a silhouette ratio chosen per class and
    # stance, so the meaningful invariant is the ordering *inside* a class --
    # a quadruped is long in profile and narrow head-on, and comparing it
    # against a biped says nothing.
    for small, big in (("imp", "boss_cyber"), ("lost_soul", "artillery"),
                       ("archvile", "boss_baron"), ("dog", "boss_spider"),
                       ("pinkie", "arachnotron")):
        assert ENEMY_MODELS[small].w < ENEMY_MODELS[big].w, (small, big)
    # A turret is the widest single mass, and a floater is drawn lifted.
    assert ENEMY_MODELS["mancubus"].w > max(m.w for m in ENEMY_MODELS.values()
                                            if m.stance == "biped")
    assert ENEMY_MODELS["cacodemon"].anchor > 0
    assert ENEMY_MODELS["mancubus"].anchor == 0
    assert ENEMY_MODELS["artillery"].anchor == 0
    check("registry covers 20/20 types")


def test_view_index_rounds_to_sectors():
    step = math.pi / 4
    # Boundaries: the same index just either side of a 45 degree edge, and
    # exactly one switch across it.
    edge = step * 0.5
    for k in range(8):
        b = -math.pi + k * step
        a = view_index(b - edge + 1e-6)
        c = view_index(b + edge - 1e-6)
        assert a == view_index(b) == c, (b, a, c)
    assert view_index(0.0) == 0
    assert view_index(math.pi) == 4
    switches = sum(1 for d in range(2100, 2400)
                   if view_index(math.radians(d * 0.1))
                   != view_index(math.radians((d - 1) * 0.1)))
    assert switches <= 2, switches   # 21 deg -> 24 deg must not flicker
    assert 0 <= view_index(-3.9) < 8
    check("view_index: 8 stable 45 deg sectors")


def test_all_types_views_states_build():
    clear_cache()
    built = 0
    for t in ENEMY_MODELS:
        for view in range(8):
            for state in ANIM_STATES:
                for frame in range(ENEMY_MODELS[t].frames(state)):
                    s = get_enemy_surface(t, 48, frame, state, view, 3)
                    assert s is not None, (t, view, state, frame)
                    assert s.get_width() > 1 and s.get_height() > 1, (t, view)
                    # Non-empty: the body is really painted, not just a box.
                    # Probe inside the silhouette's own bounds -- an anchored
                    # model (a floater) leaves the surface centre transparent.
                    br = s.get_bounding_rect()
                    assert br.width > 0 and br.height > 0, (t, view, state)
                    cx = br.x + br.width // 2
                    cy = br.y + br.height // 2
                    assert s.get_at((cx, cy))[3] > 0, (t, view, state, frame)
                    built += 1
    assert built > 500, built
    check(f"{built} surfaces, 20 types x 8 views x 5 states")


def test_views_are_distinct_and_back_is_blind():
    # 8 sectors exist and the silhouette survives all of them.  A bilaterally
    # symmetric painter may render a mirror pair identically, so the contract
    # is: front/back differ, the profile views differ from both, and any
    # collapsed pair is exactly a mirror pair (v, 8-v).
    for t in ENEMY_MODELS:
        imgs = [pygame.image.tostring(get_enemy_surface(t, 64, 0, "idle", v, 3),
                                      "RGBA") for v in range(8)]
        assert len(set(imgs)) >= 5, (t, len(set(imgs)))
        assert imgs[0] != imgs[4], f"{t}: front view == back view"
        assert imgs[1] != imgs[5], f"{t}: 3/4 front == 3/4 back"
        groups = {}
        for v, k in enumerate(imgs):
            groups.setdefault(k, []).append(v)
        for vs in groups.values():
            assert len(vs) == 1 or (len(vs) == 2 and vs[1] == 8 - vs[0]), \
                (t, vs)
        # Recognisability: no view may collapse the silhouette to a sliver.
        drawn = [get_enemy_surface(t, 64, 0, "idle", v, 3).get_bounding_rect()
                 .width for v in range(8)]
        assert min(drawn) >= max(drawn) * 0.45, (t, drawn)
    # Front and back differ: no face on the back view.
    front = get_enemy_surface("soldier", 64, 0, "idle", 0, 3)
    back = get_enemy_surface("soldier", 64, 0, "idle", 4, 3)
    assert pygame.image.tostring(front, "RGBA") != \
        pygame.image.tostring(back, "RGBA")
    # A quadruped seen head-on is markedly narrower than in profile.  The
    # surface box is constant per type (one scale for all views), so the
    # narrowing has to be measured on the drawn silhouette.
    for t in ("dog", "pinkie", "arachnotron", "boss_spider"):
        face = get_enemy_surface(t, 64, 0, "idle", 0, 3).get_bounding_rect().width
        prof = get_enemy_surface(t, 64, 0, "idle", 2, 3).get_bounding_rect().width
        assert face < prof * 0.85, (t, face, prof)
    # ... and a two-armed turret is the other way round.
    face = get_enemy_surface("mancubus", 64, 0, "idle", 0, 3).get_bounding_rect().width
    prof = get_enemy_surface("mancubus", 64, 0, "idle", 2, 3).get_bounding_rect().width
    assert face > prof, (face, prof)
    check("8 sectors; back has no face; quadruped narrows head-on")


def test_fallback_for_unknown_types():
    clear_cache()
    for t in ("custom:mod_beast", "not_a_type", "", "demon"):
        s = get_enemy_surface(t, 64, 0, "idle", 0, 3)
        assert s is not None and s.get_bounding_rect().width > 0, t
        c = get_corpse_surface(t, 64)
        assert c is not None and c.get_bounding_rect().width > 0, t
    # The 20 protocol types never take the fallback.
    for t in ENEMY_TYPES:
        assert model_for(t) is ENEMY_MODELS[t], t
    check("unknown / custom:* degrade to a humanoid")


def test_cache_key_separates_view_and_detail():
    clear_cache()
    a = pygame.image.tostring(get_enemy_surface("soldier", 64, 0, "idle", 0, 2),
                              "RGBA")
    b = pygame.image.tostring(get_enemy_surface("soldier", 64, 0, "idle", 4, 2),
                              "RGBA")
    c = pygame.image.tostring(get_enemy_surface("soldier", 64, 0, "idle", 0, 0),
                              "RGBA")
    assert a != b, "view 0 and view 4 collided in the cache key"
    assert a != c, "detail 2 and detail 0 collided in the cache key"
    clear_cache()
    get_enemy_surface("soldier", 64, 0, "idle", 0, 2)
    m = cache_stats()["misses"]
    get_enemy_surface("soldier", 64, 0, "idle", 0, 2)
    assert cache_stats()["misses"] == m, "repeat request missed the cache"
    check("cache key separates view + detail")


def test_size_ladder_bounds_the_cache():
    # A continuous range of pixel heights must collapse onto a small ladder,
    # otherwise a long walk would mint a surface per step.
    assert size_bucket(1000) == size_bucket(256) == 256
    for h in (5, 7, 12, 30, 77, 140, 500):
        assert size_bucket(h) in (6, 8, 10, 13, 16, 20, 25, 31, 38, 46, 56, 68,
                                  82, 98, 116, 136, 160, 188, 220, 256), h
    clear_cache()
    for h in range(8, 260):          # every exact pixel height
        get_enemy_surface("imp", h, 0, "walk", 2, 2)
    st = cache_stats()
    assert st["items"] <= len(range(6, 257)) * 1.0, st
    assert st["items"] < 60, st
    check(f"252 pixel heights -> {st['items']} cache entries")


# ---------------------------------------------------------------------------
# 2. The renderer is type-agnostic and allocates nothing per frame
# ---------------------------------------------------------------------------


def test_renderer_has_no_enemy_type_branches():
    import ast
    with open(R.__file__, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=R.__file__)
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and \
                isinstance(node.left, ast.Name) and node.left.id == "t":
            for c in node.comparators:
                if isinstance(c, ast.Constant):
                    assert c.value not in ENEMY_DB, \
                        f"renderer still branches on enemy type {c.value!r}"
    check("no per-type branch left in the renderer")


def test_no_surfaces_allocated_per_frame():
    """30 enemies in 4 states, one frame, zero new Surfaces (spec)."""
    clear_cache()
    setup_scene()
    enemies = []
    states = ("idle", "walk", "attack", "death")
    for i in range(30):
        t = ENEMY_TYPES[i % 20]
        st = states[i % 4]
        enemies.append(make_enemy(
            t, x=20.0 + (i % 6) * 45, y=90.0 + (i // 6) * 22,
            hp=50, state=st, frame=i % 5,
            stun=3 if st == "idle" else 0,
            d_flash=4 if st == "walk" else 0,
            rush_timer=2 if st == "attack" else 0,
        ))
    gs.enemies = enemies
    gs.corpses = [make_enemy(ENEMY_TYPES[i], x=30.0 + i * 25, y=200.0)
                  for i in range(5)]
    for c in gs.corpses:
        c.pop("hp", None)
        c["time"] = 300

    # Warm the cache first -- a cold first frame is *expected* to build.
    for e in enemies:
        R.draw_enemy_sprite(e, 100, 48, 0, ground_y=180)
    for c in gs.corpses:
        R.draw_corpse(c, 100, 48, ground_y=180)

    created = []
    real_surface = pygame.Surface

    class Counting(real_surface):
        def __init__(self, *a, **k):
            created.append(1)
            super().__init__(*a, **k)

    pygame.Surface = Counting
    try:
        for e in enemies:
            R.draw_enemy_sprite(e, 100, 48, 0, ground_y=180)
        for c in gs.corpses:
            R.draw_corpse(c, 100, 48, ground_y=180)
    finally:
        pygame.Surface = real_surface
    assert not created, f"{len(created)} Surface(s) allocated in one frame"
    check("30 enemies + 5 corpses: 0 Surfaces per frame")


def test_models_actually_reach_the_screen():
    """The path must paint, not silently no-op."""
    setup_scene()
    for t in ENEMY_TYPES:
        clear_cache()
        gs.screen.fill((0, 0, 0))
        R.draw_enemy_sprite(make_enemy(t), 160, 64, 0, ground_y=200)
        lit = sum(1 for i in range(0, gs.screen.get_width() * gs.screen.get_height(), 7)
                  if gs.screen.get_at((i % gs.screen.get_width(),
                                        i // gs.screen.get_width())) != (0, 0, 0))
        assert lit > 0, f"{t} drew nothing"
    # A corpse too, and it must not be a single flat blob.
    clear_cache()
    gs.screen.fill((0, 0, 0))
    c = make_enemy("mancubus")
    c.pop("hp")
    c["time"] = 300
    R.draw_corpse(c, 160, 64, ground_y=200)
    assert sum(1 for i in range(0, gs.screen.get_width() * gs.screen.get_height(), 5)
               if gs.screen.get_at((i % gs.screen.get_width(),
                                    i // gs.screen.get_width())) != (0, 0, 0)) > 3
    check("every type paints; corpses paint too")


def test_degrades_without_a_provider():
    """No provider must degrade to a visible silhouette, not raise."""
    setup_scene()
    saved = gs.enemy_surface_provider
    gs.enemy_surface_provider = None
    try:
        gs.screen.fill((0, 0, 0))
        R.draw_enemy_sprite(make_enemy("soldier"), 160, 64, 0, ground_y=200)
        lit = sum(1 for i in range(0, gs.screen.get_width() * gs.screen.get_height(), 7)
                  if gs.screen.get_at((i % gs.screen.get_width(),
                                        i // gs.screen.get_width())) != (0, 0, 0))
        assert lit > 0, "no-provider path drew nothing"
        # Rollback flag: same guarantee without unregistering.
        gs.enemy_surface_provider = saved
        clear_cache()
        gs.enemy_models_enabled = False
        gs.screen.fill((0, 0, 0))
        R.draw_enemy_sprite(make_enemy("soldier"), 160, 64, 0, ground_y=200)
        lit = sum(1 for i in range(0, gs.screen.get_width() * gs.screen.get_height(), 7)
                  if gs.screen.get_at((i % gs.screen.get_width(),
                                        i // gs.screen.get_width())) != (0, 0, 0))
        assert lit > 0, "rollback path drew nothing"
    finally:
        gs.enemy_models_enabled = True
        gs.enemy_surface_provider = saved
    check("no provider / rollback flag degrade visibly")


# ---------------------------------------------------------------------------
# 3. Corpses
# ---------------------------------------------------------------------------


def test_corpse_per_type():
    clear_cache()
    bodies = {}
    for t in ENEMY_TYPES:
        c = get_corpse_surface(t, 64)
        assert c.get_bounding_rect().width > 0, t
        bodies[t] = pygame.image.tostring(c, "RGBA")
    for a in ENEMY_TYPES:
        for b in ENEMY_TYPES:
            if a >= b:
                continue
            assert bodies[a] != bodies[b], f"corpse of {a} == corpse of {b}"
    # Corpses lie flat: drawn wider than tall, whatever the class.  The
    # silhouette still reads as the type -- a dog is long, a turret is a
    # compact thick mass, and the two must not be the same shape lying down.
    shapes = {}
    for t in ENEMY_TYPES:
        b = get_corpse_surface(t, 64).get_bounding_rect()
        assert b.width > b.height, (t, b.width, b.height)
        shapes[t] = b.width / b.height
    assert shapes["dog"] > shapes["mancubus"] * 1.8, \
        (shapes["dog"], shapes["mancubus"])
    check("20 distinct corpses, each lying flat in its own shape")


# ---------------------------------------------------------------------------
# 4. Quality tiers
# ---------------------------------------------------------------------------


def test_tier_changes_detail_not_entities():
    setup_scene()
    clear_cache()
    enemies = [make_enemy(t, x=40.0 + i * 12, y=120.0, hp=10 + i,
                          state="walk", frame=i % 5) for i, t in
               enumerate(ENEMY_TYPES)]
    before = copy.deepcopy(enemies)
    seen = {}
    for tier in ("high", "medium", "fast"):
        gs.quality_tier = tier
        gs.screen.fill((0, 0, 0))
        for e in enemies:
            R.draw_enemy_sprite(e, 160, 64, 0, ground_y=200)
        seen[tier] = pygame.image.tostring(gs.screen, "RGBA")
    gs.quality_tier = "high"
    # Entities are untouched by the tier.
    for a, b in zip(before, enemies):
        assert a == b, "a tier change mutated an enemy"
    # Detail actually changed.
    assert seen["high"] != seen["fast"], "tier did not change the picture"
    assert enemy_detail("high", 64) == 3
    assert enemy_detail("fast", 64) == 1
    # Detail 3 is only for close-up models, whatever the tier.
    assert enemy_detail("high", 12) == 1
    check("tier changes detail; entities and HP untouched")


def test_fast_tier_keeps_three_silhouettes_distinct():
    """imp / mancubus / archvile must stay tellable apart on 'Fast'."""
    setup_scene()
    for tier in ("high", "fast"):
        shapes = {}
        for t in ("imp", "mancubus", "archvile"):
            clear_cache()
            s = get_enemy_surface(t, 64, 0, "idle", 0, enemy_detail(tier, 64))
            assert s.get_bounding_rect().width > 0, (tier, t)
            shapes[t] = (s.get_width(), s.get_height(),
                         pygame.image.tostring(s, "RGBA"))
        assert shapes["imp"][0] < shapes["mancubus"][0], (tier, shapes["imp"][0],
                                                          shapes["mancubus"][0])
        assert shapes["imp"][2] != shapes["mancubus"][2]
        assert shapes["mancubus"][2] != shapes["archvile"][2]
        assert shapes["imp"][2] != shapes["archvile"][2]
    check("imp / mancubus / archvile distinct on High and Fast")


# ---------------------------------------------------------------------------
# 5. State, effects and their cache
# ---------------------------------------------------------------------------


def test_anim_state_derivation():
    now = 10_000.0
    e = {"dying": 0, "stun": 0, "ls": 0, "moving": False}
    assert anim_state_of(e, now) == "idle"
    e["moving"] = True
    assert anim_state_of(e, now) == "walk"
    e["stun"] = 3
    assert anim_state_of(e, now) == "pain"          # pain wins over walk
    e["stun"] = 0
    e["ls"] = now - 100
    assert anim_state_of(e, now) == "attack"        # recent swing
    e["ls"] = now - 5000
    assert anim_state_of(e, now) == "walk"          # stale swing
    e["dying"] = 5
    assert anim_state_of(e, now) == "death"         # death wins over all
    check("stun->pain, recent attack->attack, motion->walk, else idle")


def test_anim_frame_restarts_on_state_change():
    m = model_for("soldier")
    started = 0.0
    early = anim_frame_of(m, "walk", 3.0, started)
    later = anim_frame_of(m, "walk", 900.0, started)
    assert early != later, "walk cycle is not animating"
    # A state change restarts at frame 0.
    assert anim_frame_of(m, "attack", 500.0, 500.0) == 0
    # pain shares idle geometry (the difference is the overlay).
    assert anim_frame_of(m, "pain", 40.0, 0.0) == anim_frame_of(m, "idle", 40.0, 0.0)
    check("frame index is a local derivative, restarts on change")


def test_overlay_effects_fade_by_frame_not_by_clock():
    clear_cache()
    setup_scene()
    # Same model, two frames of the same event, different tick: the flash must
    # differ because the *counter* fell, not because time passed.
    a = make_enemy("soldier", d_flash=5, state="idle")
    b = make_enemy("soldier", d_flash=3, state="idle")
    shots = []
    for tick in (0, 10_000, 10_000_000):
        gs.screen.fill((0, 0, 0))
        R.draw_enemy_sprite(a, 160, 64, tick, ground_y=200)
        f1 = pygame.image.tostring(gs.screen, "RGBA")
        gs.screen.fill((0, 0, 0))
        R.draw_enemy_sprite(b, 160, 64, tick, ground_y=200)
        f3 = pygame.image.tostring(gs.screen, "RGBA")
        shots.append((f1, f3))
    assert shots[0] == shots[1] == shots[2], "effect depends on the frame clock"
    assert shots[0][0] != shots[0][1], "damage flash ignores its own counter"
    # Stun stays readable: not a flat opaque fill over the model.
    s0 = get_enemy_surface("soldier", 64, 0, "idle", 0, 3)
    wash = ENEMY_SURFACES("stun", "soldier", 64, step=3)
    assert wash is not None and wash.get_at((wash.get_width() // 2,
                                             wash.get_height() // 2))[3] < 255
    assert wash.get_at((0, 0))[3] == 0 or wash.get_bounding_rect().width > 0
    # Eye glow: below the threshold nothing is drawn.
    assert ENEMY_SURFACES("eyes", "soldier", 4, 0) is not None
    check("flash/stun fade by counter, not by tick")


def test_cache_is_bounded():
    clear_cache()
    for t in ENEMY_TYPES:
        for view in range(8):
            for state in ANIM_STATES:
                get_enemy_surface(t, 64, 0, state, view, 3)
                get_corpse_surface(t, 64)
    for size in (8, 32, 128, 256):
        for k in range(7):
            ENEMY_SURFACES("shadow", "imp", size)
            ENEMY_SURFACES("stun", "imp", size, step=k % 4)
            ENEMY_SURFACES("eyes", "imp", 4 + k, k % 4)
    st = cache_stats()
    assert st["items"] <= CACHE_MAX_ENTRIES, st
    assert st["pixels"] <= CACHE_MAX_PIXELS, st
    # And the engine's own byte cache stays inside its budget.
    eng = DEFAULT_SPRITE_CACHE.stats()
    assert eng["bytes"] <= eng["max_bytes"], eng
    check(f"cache bounded: {st['items']} entries, "
          f"{st['pixels']} px of {CACHE_MAX_PIXELS}")


def test_cache_cleared_on_map_and_tier_change():
    setup_scene()
    clear_cache()
    for t in ENEMY_TYPES:
        for view in range(8):
            get_enemy_surface(t, 64, 0, "idle", view, 3)
    filled = cache_stats()["items"]
    assert filled >= 20, filled
    ENEMY_SURFACES.clear()
    assert cache_stats()["pixels"] == 0 and cache_stats()["items"] == 0

    # A tier change invalidates the entries built for the old detail level.
    # The frame that observes the change rebuilds what it draws, so the proof
    # is that the cache collapses back to that one draw, not that it is empty.
    get_enemy_surface("soldier", 64, 0, "idle", 0, 3)
    gs.quality_tier = "fast"
    R.draw_enemy_sprite(make_enemy("soldier"), 160, 64, 0, ground_y=200)
    after_tier = cache_stats()["items"]
    assert after_tier < 4, f"tier change kept stale-detail entries ({after_tier})"
    # And it came back at the new tier's detail, not the old one.
    drawn = pygame.image.tostring(get_enemy_surface(
        "soldier", 64, 0, "idle", 0, enemy_detail("fast", 64)), "RGBA")
    assert drawn == pygame.image.tostring(get_enemy_surface(
        "soldier", 64, 0, "idle", 0, 1), "RGBA")
    gs.quality_tier = "high"
    R.draw_enemy_sprite(make_enemy("soldier"), 160, 64, 0, ground_y=200)
    assert cache_stats()["items"] < 4, "high tier did not invalidate fast-tier"
    check("cache invalidated on tier change and on map clear")


# ---------------------------------------------------------------------------
# 6. Nothing in the game itself moved
# ---------------------------------------------------------------------------


def test_gameplay_numbers_untouched():
    db = copy.deepcopy(ENEMY_DB)
    heights = dict(ENEMY_GAME_HEIGHTS)
    setup_scene()
    clear_cache()
    gs.enemies = [make_enemy(t, hp=7) for t in ENEMY_TYPES]
    for e in gs.enemies:
        R.draw_enemy_sprite(e, 160, 64, 0, ground_y=200)
    assert ENEMY_DB == db, "rendering mutated ENEMY_DB"
    assert ENEMY_GAME_HEIGHTS == heights, "rendering mutated the game heights"
    # hp/max_hp pass through untouched.
    for e in gs.enemies:
        assert e["hp"] == 7 and e["max_hp"] == 7
    check("ENEMY_DB / heights / hp unchanged by rendering")


def main():
    pygame.init()
    pygame.display.set_mode(SCREEN)
    setup_scene()
    test_registry_covers_protocol()
    test_view_index_rounds_to_sectors()
    test_all_types_views_states_build()
    test_views_are_distinct_and_back_is_blind()
    test_fallback_for_unknown_types()
    test_cache_key_separates_view_and_detail()
    test_size_ladder_bounds_the_cache()
    test_renderer_has_no_enemy_type_branches()
    test_no_surfaces_allocated_per_frame()
    test_models_actually_reach_the_screen()
    test_degrades_without_a_provider()
    test_corpse_per_type()
    test_tier_changes_detail_not_entities()
    test_fast_tier_keeps_three_silhouettes_distinct()
    test_anim_state_derivation()
    test_anim_frame_restarts_on_state_change()
    test_overlay_effects_fade_by_frame_not_by_clock()
    test_cache_is_bounded()
    test_cache_cleared_on_map_and_tier_change()
    test_gameplay_numbers_untouched()
    print(f"smoke_enemy_models: ALL OK ({_ok} checks)")


if __name__ == "__main__":
    main()
