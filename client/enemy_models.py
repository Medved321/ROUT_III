"""Procedural enemy models (change ``enemy-model-overhaul``).

The engine creates no content: this module owns every enemy's appearance and
the engine only asks for a surface through ``gs.enemy_surface_provider``.

A model is **data** (:class:`EnemyModel` + :class:`Part`), never a branch in
the renderer.  One painter (:func:`_paint`) turns a descriptor into a
``pygame.Surface`` for a given (view, animation frame, state, detail level);
all results are cached, so the render path allocates no surfaces per frame.

Geometry is expressed in *model heights*: ``y`` is measured from the feet
(0 = ground, 1 = top of the head) and ``x``/``w``/``r`` from the centre
line, so a model scales as one piece and its proportions survive every
distance bucket.

View index is one of 8 directions, 45 degrees apart (see :func:`view_index`):
0 faces the player, 2 is a profile, 4 is the back.  ``side`` (how profile-ish
the view is) and ``back`` (is the camera behind the model) are what the
painter uses to fold the silhouette, so a model reads as a solid from every
angle instead of staying a flat cut-out.
"""
from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass, field

import pygame

from engine.constants import ENEMY_DB, ENEMY_GAME_HEIGHTS
from engine.quality import MAX_ENEMY_DETAIL, MIN_ENEMY_DETAIL, enemy_detail
from engine.sprite_cache import VIEWS, view_index

# ---------------------------------------------------------------------------
# Distance ladder
# ---------------------------------------------------------------------------

#: On-screen model heights, coarsest to finest.  A model's height in pixels is
#: a continuous function of distance, so keying the cache on it directly would
#: mint a new entry for every step a player takes.  Snapping to a geometric
#: ladder caps the number of live sizes (and therefore the cache) no matter how
#: big the window is or how long the session runs, and mirrors the engine's
#: wall/sprite distance buckets.
#: Pixel heights the cache snaps to.  The model is blitted 1:1 (see
#: ``enemy-projected-scale``), so the rung *is* the drawn size and the top rung
#: is the growth limit -- it has to cover the window, not the internal buffer.
#: In sector mode ``sh = world_h * FOCAL / perp`` is in screen pixels (FOCAL
#: 750, a 56-unit soldier is 420 px at 100 units and 700 px at 60), so a 256
#: cap froze every enemy at 256 px while the walls kept growing -- the enemy
#: read as *shrinking* on approach.
#:
#: Dense below 256 (a 1.26 step, invisible at distance), sparse above: once a
#: model is taller than the window, 512 and 1024 are indistinguishable, so
#: extra rungs would only cost memory.
SIZE_STEPS = (6, 8, 10, 13, 16, 20, 25, 31, 38, 46, 56, 68, 82, 98, 116,
              136, 160, 188, 220, 256, 320, 400, 512, 640, 800, 1024)


def size_bucket(h):
    """Snap a pixel height up to the nearest rung of :data:`SIZE_STEPS`.

    Rounds *up*, never down: the caller blits the surface as-is, so a rung
    below the projected height would draw the enemy smaller than the world
    around it.
    """
    h = max(4, int(h))
    for s in SIZE_STEPS:
        if h <= s:
            return s
    return SIZE_STEPS[-1]


# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------


def _shade(c, f):
    """Scale a colour by ``f`` (<1 darker, >1 lifted towards white)."""
    if f <= 1.0:
        return tuple(max(0, min(255, int(v * f))) for v in c[:3])
    t = f - 1.0
    return tuple(max(0, min(255, int(v + (255 - v) * t))) for v in c[:3])


def palette_for(base, **over):
    """Model palette derived from the type's ``ENEMY_DB`` colour.

    ``over`` wins, so a type that must not read as "generic recolour" (bone
    white revenant, green fireball demon) can pin its own keys.
    """
    pal = {
        'body': tuple(base[:3]),
        'dark': _shade(base, 0.55),
        'light': _shade(base, 1.45),
        'metal': _shade(base, 0.72),
        'bone': (224, 216, 198),
        'eye': (255, 210, 60),
        'accent': _shade(base, 1.9),
    }
    pal.update(over)
    return pal


# ---------------------------------------------------------------------------
# Descriptors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Part:
    """One body part, in model-height units.

    ``detail`` is the minimum detail level (1..3) that keeps the part: the
    quality tier and the distance bucket both lower the level, and the part
    simply stops being drawn.
    """

    kind: str
    w: float = 0.0     # width, for rect-ish kinds
    h: float = 0.0     # height, for rect-ish kinds
    r: float = 0.0     # radius, for round-ish kinds
    y: float = 0.0     # centre height above the feet
    x: float = 0.0     # lateral offset (+ = the model's right)
    detail: int = 1
    tone: str = 'body'


@dataclass(frozen=True)
class EnemyModel:
    """Everything needed to draw one enemy type."""

    etype: str
    stance: str
    w: float                       # silhouette width / height
    anchor: float                  # 0 = stands on the floor, >0 = floats
    parts: tuple
    anim: dict                     # state -> frame count
    pal: dict = field(default_factory=dict)
    h: int = 0                     # world height, mirrors ENEMY_GAME_HEIGHTS
    alpha: int = 255               # <255 for translucent types (spectre)

    def frames(self, state):
        return int(self.anim.get(state) or self.anim.get('idle') or 1)


def _p(kind, y, w=0.0, h=0.0, r=0.0, x=0.0, detail=1, tone='body'):
    return Part(kind, w, h, r, y, x, detail, tone)


def _model(etype, stance, w, parts, anchor=0.0, h=0, alpha=255, **over):
    """Build a model, taking its colour and world height from ``ENEMY_DB``."""
    db = ENEMY_DB.get(etype, {})
    return EnemyModel(
        etype=etype,
        stance=stance,
        w=w,
        anchor=anchor,
        parts=tuple(parts),
        anim={'idle': 2, 'walk': 6, 'attack': 3, 'death': 5},
        pal=palette_for(db.get('col', (160, 90, 90)), **over.pop('pal', {})),
        h=int(h or ENEMY_GAME_HEIGHTS.get(etype, 50)),
        alpha=alpha,
    )


# Standard humanoid frame: two legs, a torso, a head, two arms, and an
# optional held weapon.  ``gun_w``/``gun_h`` size the weapon so each humanoid
# reads as itself at a glance.
def _biped(w, *, gun=None, gun_w=0.10, gun_h=0.34, head_r=0.10, arm=True,
           tone=None, **kw):
    tone = tone or 'body'
    parts = [
        _p('leg', 0.20, w=0.11, h=0.40, x=-w * 0.18, tone='dark'),
        _p('leg', 0.20, w=0.11, h=0.40, x=w * 0.18, tone='dark'),
        _p('torso', 0.56, w=w * 0.86, h=0.34, tone=tone),
        _p('head', 0.86, r=head_r),
        _p('jaw', 0.78, w=head_r * 1.3, h=head_r * 0.5, detail=3),
    ]
    if arm:
        parts.append(_p('arm', 0.56, h=0.30, x=-w * 0.52, tone='dark'))
        parts.append(_p('arm', 0.56, h=0.30, x=w * 0.52, tone='dark'))
    if gun:
        parts.append(_p(gun, 0.56, w=gun_w, h=gun_h, x=w * 0.56, detail=2,
                        tone='metal'))
    return parts + list(kw.get('extra', ()))


ENEMY_MODELS = {
    # -- humanoids ---------------------------------------------------------
    'soldier': _model('soldier', 'biped', 0.40, _biped(0.40, gun='gun',
                                                       gun_w=0.08, gun_h=0.30)),
    'shotgunguy': _model('shotgunguy', 'biped', 0.44, _biped(
        0.44, gun='gun', gun_w=0.13, gun_h=0.36,
        extra=(_p('gun', 0.44, w=0.09, h=0.16, x=0.10, detail=3, tone='accent'),))),
    'chaingunner': _model('chaingunner', 'biped', 0.46, _biped(
        0.46, gun='gun', gun_w=0.12, gun_h=0.40,
        extra=(_p('gun', 0.72, w=0.07, h=0.10, x=0.10, detail=3, tone='accent'),))),
    'imp': _model('imp', 'biped', 0.38, _biped(
        0.38, head_r=0.11, arm=False,
        extra=(_p('horn', 0.96, r=0.05, x=-0.09, detail=2, tone='bone'),
               _p('horn', 0.96, r=0.05, x=0.09, detail=2, tone='bone'),
               _p('claw', 0.52, h=0.22, x=-0.17, detail=2, tone='bone'),
               _p('claw', 0.52, h=0.22, x=0.17, detail=2, tone='bone'),
               _p('tail', 0.26, h=0.22, x=-0.16, detail=3, tone='dark')))),
    'spectre': _model('spectre', 'biped', 0.36, _biped(
        0.36, head_r=0.09,
        extra=(_p('claw', 0.52, h=0.26, x=-0.15, detail=2, tone='light'),
               _p('claw', 0.52, h=0.26, x=0.15, detail=2, tone='light'))),
        alpha=170),
    'lost_soul': _model('lost_soul', 'biped', 0.34, _biped(
        0.34, head_r=0.11, arm=False,
        extra=(_p('wing', 0.66, w=0.30, h=0.26, x=-0.20, detail=1, tone='accent'),
               _p('wing', 0.66, w=0.30, h=0.26, x=0.20, detail=1, tone='accent'),
               _p('claw', 0.50, h=0.20, x=-0.15, detail=2, tone='bone'),
               _p('claw', 0.50, h=0.20, x=0.15, detail=2, tone='bone'))),
        anchor=0.10),
    'hell_knight': _model('hell_knight', 'biped', 0.52, _biped(
        0.52, head_r=0.11,
        extra=(_p('horn', 0.98, r=0.06, x=-0.12, detail=1, tone='bone'),
               _p('horn', 0.98, r=0.06, x=0.12, detail=1, tone='bone'),
               _p('claw', 0.52, h=0.24, x=-0.24, detail=2, tone='bone'),
               _p('claw', 0.52, h=0.24, x=0.24, detail=2, tone='bone')))),
    'revenant': _model('revenant', 'biped', 0.42, _biped(
        0.42, head_r=0.10, tone='bone',
        extra=(_p('gun', 0.54, w=0.07, h=0.42, x=0.22, detail=2, tone='bone'),))),
    'archvile': _model('archvile', 'biped', 0.40, [
        _p('cloak', 0.42, w=0.40, h=0.84, tone='body'),
        _p('head', 0.90, r=0.09, tone='bone'),
        _p('claw', 0.58, h=0.26, x=-0.19, detail=2, tone='bone'),
        _p('claw', 0.58, h=0.26, x=0.19, detail=2, tone='bone'),
        _p('spike', 0.20, r=0.03, detail=3, tone='accent'),
    ]),
    'boss_baron': _model('boss_baron', 'biped', 0.60, _biped(
        0.60, head_r=0.11,
        extra=(_p('horn', 0.99, r=0.07, x=-0.13, detail=1, tone='bone'),
               _p('horn', 0.99, r=0.07, x=0.13, detail=1, tone='bone'),
               _p('claw', 0.54, h=0.28, x=-0.28, detail=2, tone='bone'),
               _p('claw', 0.54, h=0.28, x=0.28, detail=2, tone='bone')))),
    'boss_cyber': _model('boss_cyber', 'biped', 0.78, _biped(
        0.78, head_r=0.12, arm=False,
        extra=(_p('cannon', 0.74, w=0.16, h=0.20, x=-0.30, detail=1,
                  tone='metal'),
               _p('cannon', 0.74, w=0.16, h=0.20, x=0.30, detail=1,
                  tone='metal'),
               _p('spike', 0.88, r=0.05, x=0.0, detail=2, tone='accent')))),
    'healer': _model('healer', 'biped', 0.40, _biped(
        0.40, head_r=0.10, arm=False,
        extra=(_p('wing', 0.60, w=0.22, h=0.20, x=-0.19, detail=2, tone='accent'),
               _p('wing', 0.60, w=0.22, h=0.20, x=0.19, detail=2, tone='accent'),
               _p('eye', 0.62, r=0.09, detail=2, tone='accent'),
               _p('gun', 0.50, w=0.05, h=0.36, x=0.19, detail=2, tone='bone')))),
    'artillery': _model('artillery', 'biped', 0.55, _biped(
        0.55, head_r=0.10,
        extra=(_p('cannon', 0.76, w=0.15, h=0.18, x=-0.24, detail=1,
                  tone='metal'),
               _p('cannon', 0.76, w=0.15, h=0.18, x=0.24, detail=1,
                  tone='metal'),
               _p('cannon', 0.60, w=0.10, h=0.12, x=0.0, detail=3,
                  tone='metal')))),

    # -- four-legged / low ------------------------------------------------
    'dog': _model('dog', 'quadruped', 0.80, [
        _p('body', 0.34, w=0.78, h=0.30),
        _p('head', 0.40, r=0.13, x=0.42),
        _p('jaw', 0.32, w=0.12, h=0.05, x=0.50, detail=2, tone='bone'),
        _p('leg_quad', 0.17, w=0.05, h=0.30, x=-0.30, tone='dark'),
        _p('leg_quad', 0.17, w=0.05, h=0.30, x=-0.10, tone='dark'),
        _p('leg_quad', 0.17, w=0.05, h=0.30, x=0.14, tone='dark'),
        _p('leg_quad', 0.17, w=0.05, h=0.30, x=0.32, tone='dark'),
        _p('tail', 0.44, h=0.22, x=-0.44, detail=2, tone='dark'),
    ]),
    'pinkie': _model('pinkie', 'quadruped', 0.78, [
        _p('body', 0.36, w=0.74, h=0.32),
        _p('head', 0.46, r=0.14, x=0.36),
        _p('head', 0.34, r=0.11, x=-0.30, detail=2),
        _p('jaw', 0.38, w=0.12, h=0.05, x=0.44, detail=2, tone='bone'),
        _p('leg_quad', 0.18, w=0.055, h=0.32, x=-0.28, tone='dark'),
        _p('leg_quad', 0.18, w=0.055, h=0.32, x=-0.08, tone='dark'),
        _p('leg_quad', 0.18, w=0.055, h=0.32, x=0.12, tone='dark'),
        _p('leg_quad', 0.18, w=0.055, h=0.32, x=0.30, tone='dark'),
        _p('tail', 0.46, h=0.20, x=-0.42, detail=2, tone='dark'),
    ]),
    'arachnotron': _model('arachnotron', 'quadruped', 1.10, [
        _p('body', 0.42, w=1.02, h=0.42),
        _p('head', 0.50, r=0.14, x=0.52),
        _p('jaw', 0.42, w=0.14, h=0.05, x=0.60, detail=2, tone='bone'),
        _p('leg_quad', 0.21, w=0.07, h=0.38, x=-0.44, tone='dark'),
        _p('leg_quad', 0.21, w=0.07, h=0.38, x=-0.16, tone='dark'),
        _p('leg_quad', 0.21, w=0.07, h=0.38, x=0.18, tone='dark'),
        _p('leg_quad', 0.21, w=0.07, h=0.38, x=0.44, tone='dark'),
        _p('spike', 0.80, r=0.05, detail=2, tone='accent'),
        _p('spike', 0.60, r=0.04, x=-0.30, detail=3, tone='accent'),
    ]),
    'boss_spider': _model('boss_spider', 'quadruped', 1.55, [
        _p('body', 0.40, w=1.44, h=0.56),
        _p('head', 0.52, r=0.17, x=0.74),
        _p('jaw', 0.42, w=0.18, h=0.07, x=0.86, detail=2, tone='bone'),
        _p('leg_quad', 0.20, w=0.09, h=0.38, x=-0.60, tone='dark'),
        _p('leg_quad', 0.20, w=0.09, h=0.38, x=-0.22, tone='dark'),
        _p('leg_quad', 0.20, w=0.09, h=0.38, x=0.24, tone='dark'),
        _p('leg_quad', 0.20, w=0.09, h=0.38, x=0.58, tone='dark'),
        _p('spike', 0.86, r=0.06, detail=1, tone='accent'),
        _p('spike', 0.70, r=0.05, x=-0.34, detail=2, tone='accent'),
        _p('eye', 0.52, r=0.08, x=0.74, detail=3, tone='eye'),
    ]),

    # -- floaters / masses ------------------------------------------------
    'cacodemon': _model('cacodemon', 'floater', 0.62, [
        _p('core', 0.46, r=0.30),
        _p('spike', 0.74, r=0.07, detail=1, tone='dark'),
        _p('jaw', 0.50, w=0.24, h=0.09, detail=2, tone='bone'),
        _p('eye', 0.56, r=0.06, x=-0.10, detail=2, tone='eye'),
        _p('eye', 0.56, r=0.06, x=0.10, detail=2, tone='eye'),
        _p('tentacle', 0.22, h=0.16, x=-0.20, detail=2, tone='dark'),
        _p('tentacle', 0.22, h=0.16, x=0.20, detail=2, tone='dark'),
    ], anchor=0.30),
    'pain_elemental': _model('pain_elemental', 'floater', 0.58, [
        _p('core', 0.44, r=0.26),
        _p('core', 0.36, r=0.18, x=0.10, detail=2, tone='accent'),
        _p('eye', 0.52, r=0.05, x=-0.09, detail=2, tone='eye'),
        _p('eye', 0.52, r=0.05, x=0.09, detail=2, tone='eye'),
        _p('tentacle', 0.20, h=0.14, x=-0.22, detail=2, tone='body'),
        _p('tentacle', 0.20, h=0.14, x=0.22, detail=2, tone='body'),
    ], anchor=0.18),
    'mancubus': _model('mancubus', 'turret', 1.00, [
        _p('body', 0.34, w=0.96, h=0.60),
        _p('head', 0.56, r=0.18),
        _p('jaw', 0.44, w=0.18, h=0.06, detail=2, tone='bone'),
        _p('cannon', 0.44, w=0.16, h=0.22, x=-0.34, detail=1, tone='metal'),
        _p('cannon', 0.44, w=0.16, h=0.22, x=0.34, detail=1, tone='metal'),
        _p('eye', 0.62, r=0.05, x=-0.08, detail=2, tone='eye'),
        _p('eye', 0.62, r=0.05, x=0.08, detail=2, tone='eye'),
        _p('spike', 0.70, r=0.05, detail=3, tone='dark'),
    ]),
}

#: Type used when the snapshot carries something the registry does not know
#: (a mod type, ``custom:*``, a typo).  It is a real model, not a blank.
FALLBACK_TYPE = 'soldier'


def model_for(etype):
    """Descriptor for ``etype``; the generic humanoid for anything unknown."""
    m = ENEMY_MODELS.get(etype)
    if m is not None:
        return m
    return ENEMY_MODELS[FALLBACK_TYPE]


def is_known(etype):
    return etype in ENEMY_MODELS


def known_types():
    return tuple(ENEMY_MODELS)


# ---------------------------------------------------------------------------
# View (8 directions, 45 degrees apart)
# ---------------------------------------------------------------------------

# 0 faces the player, 2 is a profile, 4 is the back.  ``VIEWS`` and
# ``view_index`` are imported from ``engine.sprite_cache``: the engine picks
# the view (it has the angles) and this module builds it, so the rounding rule
# has exactly one definition and the engine never imports the client.
assert VIEWS == 8, "the descriptor painter is written for 8 views"


# ---------------------------------------------------------------------------
# Animation
# ---------------------------------------------------------------------------

ANIM_STATES = ('idle', 'walk', 'attack', 'death', 'pain')
#: ``pain`` reuses the ``idle`` geometry: the difference is the stun overlay,
#: which is a state effect, not a different body.
ANIM_GEOMETRY = {'pain': 'idle'}


def anim_state_of(enemy, now):
    """Animation state from authoritative snapshot fields (design D7)."""
    if enemy.get('dying', 0) > 0:
        return 'death'
    if enemy.get('stun', 0) > 0:
        return 'pain'
    ls = enemy.get('ls', 0) or 0
    if ls and now - ls < 300:
        return 'attack'
    if enemy.get('moving'):
        return 'walk'
    return 'idle'


def anim_frame_of(model, state, now, started):
    """Frame index for ``state``, restarted whenever the state changes.

    ``started`` is the client timestamp of the current state; a state change
    therefore always plays from frame 0 instead of jumping mid-cycle.
    """
    state = ANIM_GEOMETRY.get(state, state)
    n = model.frames(state)
    if n <= 1:
        return 0
    # 60 fps reference, like the weapon-animation clock convention.
    step = {'idle': 18, 'walk': 7, 'attack': 5, 'death': 5}.get(state, 12)
    return int(max(0, now - started) // step) % n


# ---------------------------------------------------------------------------
# Detail levels
# ---------------------------------------------------------------------------

#: The tier -> detail-level decision lives in ``engine/quality.py`` (it is a
#: quality-tier question and the engine needs it to key the cache).  Re-exported
#: here so a consumer of this module has the whole model contract in one place.
MIN_DETAIL = MIN_ENEMY_DETAIL
MAX_DETAIL = MAX_ENEMY_DETAIL


def detail_for(tier, size):
    """Detail level for a model ``size`` px tall on quality ``tier``."""
    return enemy_detail(tier, size)


# ---------------------------------------------------------------------------
# Painter
# ---------------------------------------------------------------------------


def _width_mul(stance, side):
    """Silhouette width factor for a view.

    Quadrupeds are the reason views exist: seen head-on a dog is 0.45 of its
    length, seen in profile its full length.  Humanoids fold the other way
    (a body in profile is narrower than face-on).
    """
    if stance == 'quadruped':
        return 0.45 + 0.55 * side
    if stance in ('floater', 'blob'):
        return 1.0 - 0.10 * side
    return 1.0 - 0.38 * side


def _mod(px, py):
    return int(px), int(py)


def _paint(surf, model, view, frame, state, detail, body_h=None, feet=None,
           cx=None, pal=None):
    """Draw one model frame onto ``surf``.

    ``body_h``, ``feet`` and ``cx`` are the painter's geometry and belong to
    :func:`_fit_box`: the silhouette has to land on the requested size and on
    the floor line, which is not something the box the caller happens to
    allocate can give.  They default to the box itself for a plain fill, which
    is only what the fit probe wants.
    """
    W, H = surf.get_size()
    if W < 2 or H < 2:
        return
    pal = pal or model.pal
    th = view * (2.0 * math.pi / VIEWS)
    side = abs(math.sin(th))
    back = math.cos(th) < 0.0
    fwd = math.cos(th)          # +1 face-on, 0 profile, -1 from behind
    lat = math.sin(th)          # which way the model's nose points on screen
    if cx is None:
        cx = W / 2.0
    body_h = H if body_h is None else body_h
    feet = H - 1.0 if feet is None else feet

    state = ANIM_GEOMETRY.get(state, state)
    n = model.frames(state)
    ph = (frame % n) / float(n) if n else 0.0
    wave = math.sin(ph * 2.0 * math.pi)

    bob = swing = reach = fall = 0.0
    if state == 'walk':
        swing = wave
        bob = abs(math.cos(ph * 2.0 * math.pi)) * 0.020
    elif state == 'attack':
        reach = min(1.0, ph * 1.6)
        bob = -0.020 * reach
    elif state == 'death':
        fall = ph
        bob = -0.050 * fall
    else:
        bob = wave * 0.012

    sink = 0.30 * fall
    wmul = _width_mul(model.stance, side)
    alpha = model.alpha

    for part in model.parts:
        if part.detail > detail:
            continue
        tone = pal.get(part.tone, pal['body'])
        col = (*tone, alpha) if alpha != 255 else tone
        py = feet - part.y * (1.0 - sink) * body_h + bob * body_h
        wpx = max(1, int(part.w * wmul * body_h))
        hpx = max(1, int(part.h * (1.0 - sink) * body_h))
        kind = part.kind
        sx = 0.0
        if kind in ('leg', 'arm', 'claw', 'leg_quad', 'tail'):
            sx = swing * (0.09 if part.x >= 0 else -0.09) * body_h
        # Lateral offsets fold with the view just like widths do: a head-on
        # quadruped's legs and snout are *depth*, not width, so they collapse
        # toward the centre line.  This is what makes a dog facing you narrow
        # and a dog in profile long, instead of both being the same cut-out.
        lat_scale = wmul if model.stance == 'quadruped' else (
            1.0 - 0.45 * side if kind in ('arm', 'claw', 'gun', 'cannon',
                                          'wing', 'horn', 'leg')
            else 1.0)
        px = cx + part.x * lat_scale * body_h * (
            1.0 + (0.35 * fall if kind == 'leg' else 0.0))
        pxi, pyi = _mod(px + sx, py)

        if kind == 'leg':
            # Centred on ``y`` like every other part (``leg_quad`` is, and a
            # leg of length h at height y/2 has to stand on the floor): drawn
            # upward from ``y`` instead, the whole biped class hovered a fifth
            # of its box above the floor line and looked small against it.
            lh = max(1, int(hpx * (1.0 - 0.45 * fall)))
            pygame.draw.rect(surf, col, (pxi - wpx // 2, pyi - lh // 2, wpx, lh))
        elif kind == 'arm':
            if back:
                continue
            fwd_len = int(hpx * (0.4 + 0.9 * abs(fwd) * 0.6 + 0.6 * reach))
            pygame.draw.line(surf, col, (pxi, pyi - hpx // 2),
                             (pxi + int(fwd_len * lat * 0.5) - int(0.4 * hpx * lat),
                              pyi - hpx // 2 + fwd_len // 2), max(1, wpx))
        elif kind == 'claw':
            if back:
                continue
            pygame.draw.circle(surf, col, (pxi, pyi + int(hpx * 0.2)),
                               max(1, int(wpx * 0.45 + 1)))
            if detail >= 2:
                for k in (-1, 0, 1):
                    pygame.draw.line(
                        surf, col, (pxi, pyi),
                        (pxi + k * max(1, wpx // 2), pyi + hpx), 1)
        elif kind == 'leg_quad':
            lh = max(1, int(hpx * (1.0 - 0.30 * fall)))
            spread = int(0.10 * body_h * side)
            fx = pxi + spread if part.x >= 0 else pxi - spread
            pygame.draw.line(surf, col, (pxi, pyi - lh // 2), (fx, pyi + lh),
                             max(1, wpx))
        elif kind == 'tail':
            pygame.draw.line(surf, col, (pxi, pyi),
                             (pxi - int(lat * 0.16 * body_h * side + 0.12 * body_h),
                              pyi - int(hpx)), max(1, wpx))
        elif kind in ('torso', 'gun', 'cannon', 'jaw', 'cloak'):
            if kind == 'cloak':
                pygame.draw.polygon(surf, col, [
                    (cx - wpx * 0.9, feet), (cx + wpx * 0.9, feet),
                    (cx + wpx * 0.22, pyi - hpx // 2),
                    (cx - wpx * 0.22, pyi - hpx // 2)])
            elif kind == 'jaw':
                if back or state == 'death':
                    continue
                pygame.draw.rect(surf, col, (pxi - wpx // 2, pyi, wpx, hpx))
            elif kind == 'gun':
                if back:
                    continue
                gl = max(1, int(hpx * (0.35 + 0.65 * abs(fwd))))
                tip = pxi + int((hpx * 0.5 + gl) * lat)
                pygame.draw.line(surf, col, (pxi, pyi), (tip, pyi - gl // 3),
                                 max(1, wpx))
                if state == 'attack' and frame == 0:
                    pygame.draw.circle(surf, pal.get('eye'), (tip, pyi - gl // 3),
                                       max(1, wpx + 1))
            elif kind == 'cannon':
                if back:
                    continue
                gl = max(1, int(hpx * (0.4 + 0.6 * abs(fwd))))
                tip = pxi + int((wpx + gl) * lat)
                pygame.draw.line(surf, col, (pxi, pyi), (tip, pyi - gl // 2),
                                 max(1, wpx))
                pygame.draw.circle(surf, pal.get('dark'), (tip, pyi - gl // 2),
                                   max(1, wpx // 2))
            else:
                pygame.draw.rect(surf, col, (pxi - wpx // 2, pyi - hpx // 2,
                                              wpx, hpx))
                if detail >= 2 and not back and model.stance == 'biped':
                    # chest plate: reads as armour at any distance
                    pygame.draw.rect(surf, pal.get('dark'),
                                     (pxi - wpx // 2, pyi - hpx // 2, wpx, 1))
        elif kind in ('head', 'body', 'core'):
            r = max(1, int(part.r * body_h)) if part.r else max(wpx, hpx) // 2
            if kind == 'body':
                pygame.draw.ellipse(surf, col, (pxi - wpx // 2, pyi - hpx // 2,
                                                wpx, hpx))
                r = min(wpx, hpx) // 3
            else:
                pygame.draw.circle(surf, col, (pxi, pyi), r)
            if not back and detail >= 2:
                er = max(1, int(r * 0.30))
                ex = max(1, int(r * 0.38))
                ey = -max(1, int(r * 0.15))
                if kind == 'core' or model.stance in ('floater', 'blob'):
                    pygame.draw.circle(surf, pal.get('eye'),
                                       (pxi, pyi - er), er)
                else:
                    pygame.draw.circle(surf, pal.get('eye'),
                                       (pxi - ex, pyi + ey), er)
                    pygame.draw.circle(surf, pal.get('eye'),
                                       (pxi + ex, pyi + ey), er)
                    pygame.draw.circle(surf, (10, 10, 10),
                                       (pxi - ex, pyi + ey), max(1, er // 2))
                    pygame.draw.circle(surf, (10, 10, 10),
                                       (pxi + ex, pyi + ey), max(1, er // 2))
        elif kind == 'horn':
            if back:
                continue
            tipx = pxi - int(0.05 * body_h * lat)
            pygame.draw.polygon(surf, col, [
                (pxi - 2, pyi + 2), (pxi + 2, pyi + 2),
                (tipx, pyi - max(2, int(part.r * body_h * 1.8)))])
        elif kind == 'wing':
            # A folded wing is narrow face-on and a full span in profile.
            # (local names: `reach` is the arm-extension term above and must
            # not be shadowed here)
            wl = max(1, int(wpx * (0.35 + 0.65 * side)))
            span = int(wpx * (0.30 + 0.70 * side))
            pygame.draw.polygon(surf, col, [
                (pxi, pyi), (pxi - span, pyi - wl),
                (pxi - int(span * 0.4), pyi + wl // 2),
            ])
        elif kind == 'spike':
            r = max(1, int(part.r * body_h))
            if not back and detail >= 2:
                pygame.draw.circle(surf, col, (pxi, pyi), r)
            elif back:
                pygame.draw.circle(surf, col, (pxi, pyi), r)
        elif kind == 'tentacle':
            pygame.draw.line(surf, col, (pxi, pyi),
                             (pxi + int(0.04 * body_h * lat),
                              pyi + hpx), max(1, wpx))
        elif kind == 'eye':
            if back:
                continue
            pygame.draw.circle(surf, col, (pxi, pyi), max(1, int(part.r * body_h)))


# ---------------------------------------------------------------------------
# Surface cache
# ---------------------------------------------------------------------------

# Bounded by entry count *and* total pixels, so a session that walks a whole
# level cannot grow the cache without limit.  OrderedDict gives LRU eviction
# for free (move_to_end on hit, popitem(last=False) on evict).
CACHE_MAX_ENTRIES = 4096
CACHE_MAX_PIXELS = 12_000_000

_SURF_CACHE = OrderedDict()
_SURF_PIXELS = 0
STATS = {'hits': 0, 'misses': 0, 'evicted': 0}


def _store(key, surf, pixels):
    global _SURF_PIXELS
    _SURF_CACHE[key] = (surf, pixels)
    _SURF_PIXELS += pixels
    while _SURF_CACHE and (len(_SURF_CACHE) > CACHE_MAX_ENTRIES
                           or _SURF_PIXELS > CACHE_MAX_PIXELS):
        _, (_, px) = _SURF_CACHE.popitem(last=False)
        _SURF_PIXELS -= px
        STATS['evicted'] += 1


def _lookup(key):
    item = _SURF_CACHE.get(key)
    if item is None:
        STATS['misses'] += 1
        return None
    _SURF_CACHE.move_to_end(key)
    STATS['hits'] += 1
    return item[0]


def clear_cache() -> None:
    """Drop every cached surface.  Called on map change and tier change."""
    global _SURF_PIXELS
    _SURF_CACHE.clear()
    _SURF_PIXELS = 0
    STATS['hits'] = STATS['misses'] = STATS['evicted'] = 0


def cache_stats():
    return {
        'items': len(_SURF_CACHE),
        'pixels': _SURF_PIXELS,
        'hits': STATS['hits'],
        'misses': STATS['misses'],
        'evicted': STATS['evicted'],
    }


# ---------------------------------------------------------------------------
# Silhouette fit: measured, because a part stack does not fill its own box
# ---------------------------------------------------------------------------

#: Reference box height for the fit probe.  The probe is a cold-path paint that
#: is thrown away (it never reaches the surface cache), so it is sized for
#: accuracy rather than speed.
_FIT_H = 128
_FIT = {}


def _fit(model, view, detail):
    """Measured silhouette extent of one model frame, in body-height units.

    ``part.y`` is a height above the floor, and a stack of them does not fill
    the box it is handed: a dog's head tops out at 0.53 of it, a mancubus at
    0.71, a soldier at 0.77, and a type's parts can sit off the centre line
    too.  The shortfall is a property of the descriptor *and* of the view and
    the detail level, so it is measured rather than declared -- there is no
    per-type constant to write down that stays true.

    Returns ``(height, width, x_offset, bottom_gap)``, all as fractions of
    ``_FIT_H``.  ``bottom_gap`` is the air between the floor line and the
    lowest pixel of the silhouette, i.e. how far the drawing has to slide down
    to stand on the floor.
    """
    key = (model.etype, int(view), int(detail))
    got = _FIT.get(key)
    if got is not None:
        return got
    surf = pygame.Surface((max(4, int(round(_FIT_H * model.w))), _FIT_H),
                          pygame.SRCALPHA)
    _paint(surf, model, int(view), 0, 'idle', int(detail))
    r = surf.get_bounding_rect()
    if r.width < 1 or r.height < 1:
        got = (1.0, 1.0, 0.0, 0.0)
    else:
        got = (r.height / _FIT_H,
               r.width / _FIT_H,
               (r.x + r.width / 2.0) - surf.get_width() / 2.0,
               (_FIT_H - 1) - (r.y + r.height - 1))
        got = (got[0], got[1], got[2] / _FIT_H, got[3] / _FIT_H)
    _FIT[key] = got
    return got


def _fit_box(model, h, view, detail, anchor):
    """Box size and painter geometry for a silhouette ``h`` px tall.

    The model is scaled **uniformly**, so the authored proportions survive and
    only its size is corrected to the projection: a uniform scale of the whole
    silhouette cannot change its shape, which stretching the part stack would.
    The box is then the silhouette plus a pixel of slack, so nothing is
    clipped, and the floor line is where the engine bottom-aligns every type.
    """
    span, wide, off_x, bottom_gap = _fit(model, view, detail)
    body_h = h / max(1e-3, span)
    # A floater keeps air under itself so the engine can bottom-align every
    # type at one line; the gap is the same fraction it was before the fit.
    hover = int(round(h * anchor / max(0.4, 1.0 - anchor)))
    box_h = h + 1 + hover
    box_w = 2 + int(round(wide * body_h))
    floor = box_h - 1 - hover
    return (box_w, box_h, body_h, floor + bottom_gap * body_h,
            box_w / 2.0 - off_x * body_h)


def _build(model, view, frame, state, h, detail):
    """Paint one model frame, sized so its silhouette is ``h`` px tall.

    A floating type's surface is *taller* than its body by the hover offset,
    with the offset as transparent space at the bottom.  That is what lets the
    engine bottom-align every type at the same floor line: a walker fills its
    box, a floater has air under it, and neither needs a per-type offset in
    the renderer.  The visible body is still ``h`` pixels tall either way.
    """
    anchor = min(0.6, max(0.0, float(model.anchor)))
    bw, bh, body_h, feet, cx = _fit_box(model, h, view, detail, anchor)
    surf = pygame.Surface((max(2, bw), max(2, bh)), pygame.SRCALPHA)
    _paint(surf, model, view, frame, state, detail, body_h=body_h, feet=feet,
           cx=cx)
    return surf


# ---------------------------------------------------------------------------
# Public surface API (registered as gs.enemy_surface_provider)
# ---------------------------------------------------------------------------


def get_enemy_surface(etype, h, frame=0, state='idle', view=0, detail=MAX_DETAIL,
                      col=None):
    """SRCALPHA surface for one enemy, cached by its full identity.

    ``h`` is the pixel height the silhouette occupies on screen -- the
    projection's own number, in screen pixels.  ``h`` is snapped up to
    :data:`SIZE_STEPS` so the cache stays bounded; the caller blits the
    surface as-is, which is safe because a rung is never below the requested
    height, and the model's own fit puts the silhouette exactly on it.
    """
    h = size_bucket(h)
    model = model_for(etype)
    key = ('e', etype, col, h, int(frame), state, int(view), int(detail))
    surf = _lookup(key)
    if surf is not None:
        return surf
    if col is not None:
        model = EnemyModel(**{**model.__dict__, 'pal': palette_for(col)})
    surf = _build(model, int(view), int(frame), state, h, int(detail))
    _store(key, surf, surf.get_width() * surf.get_height())
    return surf


def get_corpse_surface(etype, h, col=None, detail=MIN_DETAIL):
    """Lying body of a dead enemy: the same silhouette, flattened and dimmed.

    Built from the model's own death frame in profile view, so a corpse is
    recognisable as the thing that died and stays inside one palette.  The
    flattening is applied to the *silhouette*: scaling the whole surface
    instead mixed in however much transparent padding the box happened to
    carry, so the corpse's proportions came from the box rather than from the
    type -- a squat mass and a long quadruped ended up the same shape.
    """
    h = size_bucket(h)
    model = model_for(etype)
    ch = max(3, int(h * 0.34))
    key = ('c', etype, col, ch, detail)
    surf = _lookup(key)
    if surf is not None:
        return surf
    stand_h = int(ch * 2.8)
    stand = get_enemy_surface(etype, stand_h, frame=model.frames('death') - 1,
                              state='death', view=2, detail=detail, col=col)
    rect = stand.get_bounding_rect()
    if rect.width < 1 or rect.height < 1:
        rect = pygame.Rect(0, 0, stand.get_width(), stand.get_height())
    body = stand.subsurface(rect).copy()
    own = rect.width / float(rect.height)
    cw = max(6, int(round(ch * min(3.0, max(1.4, 1.7 * own)))))
    body = pygame.transform.scale(body, (cw, ch))
    body.fill((96, 88, 96, 255), special_flags=pygame.BLEND_RGBA_MULT)
    _store(key, body, body.get_width() * body.get_height())
    return body


# ---------------------------------------------------------------------------
# State-effect overlays (pre-baked, so the hot path never allocates)
# ---------------------------------------------------------------------------

SHADOW_STEPS = 2
STUN_STEPS = 4
FLASH_STEPS = 6
RUSH_STEPS = 4
EYE_STEPS = 4


def get_shadow_surface(size, alpha=70):
    """Soft contact shadow, cached per quantized size."""
    s = size_bucket(max(4, int(size)))
    key = ('shadow', s, alpha)
    surf = _lookup(key)
    if surf is not None:
        return surf
    surf = pygame.Surface((s, max(2, s // 2)), pygame.SRCALPHA)
    pygame.draw.ellipse(surf, (0, 0, 0, alpha), (0, 0, surf.get_width(),
                                                surf.get_height()))
    _store(key, surf, surf.get_width() * surf.get_height())
    return surf


def get_stun_surface(size, step, detail=2):
    """Translucent stun wash: readable through, and it decays by step."""
    s = size_bucket(max(4, int(size)))
    step = max(0, min(STUN_STEPS - 1, int(step)))
    key = ('stun', s, step)
    surf = _lookup(key)
    if surf is not None:
        return surf
    alpha = 70 + 25 * step
    surf = pygame.Surface((s, s), pygame.SRCALPHA)
    surf.fill((24, 24, 70, alpha))
    surf.fill((90, 110, 200, 40 + 12 * step), special_flags=pygame.BLEND_RGBA_ADD)
    for i in range(0, s, max(2, s // 6)):
        pygame.draw.line(surf, (140, 160, 255, 30 + 10 * step), (0, i),
                         (s, i + s // 3), 1)
    _store(key, surf, s * s)
    return surf


def get_flash_surface(base, step):
    """Additive red flash shaped like the model, ``FLASH_STEPS`` intensity steps."""
    step = max(0, min(FLASH_STEPS - 1, int(step)))
    key = ('flash', base, step)
    surf = _lookup(key)
    if surf is not None:
        return surf
    add = 40 + 40 * step
    surf = base.copy()
    surf.fill((add, add // 4, add // 4, 0), special_flags=pygame.BLEND_RGBA_ADD)
    _store(key, surf, surf.get_width() * surf.get_height())
    return surf


def get_rush_surface(base, step):
    """Red aura around the model's own outline (rushing enemy)."""
    step = max(0, min(RUSH_STEPS - 1, int(step)))
    key = ('rush', base, step)
    surf = _lookup(key)
    if surf is not None:
        return surf
    size = base.get_size()
    # Aura across the whole box, then kept only on the silhouette's edge: the
    # ring is what the aura is multiplied by, so a rushing enemy glows
    # *around* itself instead of turning into a red rectangle.
    surf = pygame.Surface(size, pygame.SRCALPHA)
    surf.fill((255, 40, 40, 50 + 25 * step), special_flags=pygame.BLEND_RGBA_ADD)
    # ``Mask.outline`` is a boundary polyline (a list of them on some builds),
    # so it has to be rasterised before it can be blitted.
    raw = pygame.mask.from_surface(base).outline()
    polys = [raw] if raw and isinstance(raw[0][0], int) else list(raw)
    ring = pygame.Surface(size, pygame.SRCALPHA)
    for poly in polys:
        if len(poly) > 1:
            pygame.draw.lines(ring, (255, 255, 255, 255), False, poly, 1)
        elif poly:
            pygame.draw.circle(ring, (255, 255, 255, 255), poly[0], 1)
    surf.blit(ring, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    _store(key, surf, surf.get_width() * surf.get_height())
    return surf


#: Eye glows are a few pixels across whatever the enemy's size, so they get
#: their own tiny ladder.  Snapping matters as much as for a model: the caller
#: blits the returned surface as-is, and scaling it to an exact size would
#: allocate a surface per enemy per frame -- the very thing this module exists
#: to avoid.
EYE_SIZES = (3, 4, 5, 7, 9, 12, 16)


def _eye_bucket(size):
    size = max(1, int(size))
    for s in EYE_SIZES:
        if size <= s:
            return s
    return EYE_SIZES[-1]


def get_eye_glow_surface(size, step, col=(255, 200, 100)):
    """Eye glow for unlit sectors, cached per quantized size and intensity."""
    s = _eye_bucket(size)
    step = max(0, min(EYE_STEPS - 1, int(step)))
    key = ('eye', s, step, col)
    surf = _lookup(key)
    if surf is not None:
        return surf
    a = 40 + 40 * step
    r = s // 2
    surf = pygame.Surface((s, s), pygame.SRCALPHA)
    pygame.draw.circle(surf, (*col, a), (r, r), max(1, r - 1))
    pygame.draw.circle(surf, (*col, min(255, a + 90)), (r, r), max(1, r // 2))
    _store(key, surf, s * s)
    return surf


# ---------------------------------------------------------------------------
# The engine-facing slot
# ---------------------------------------------------------------------------

#: ``kind`` values the engine may ask for.  Anything else returns ``None``,
#: which the engine treats the same as "no provider" (draw the ghost).
SURFACE_KINDS = ('model', 'corpse', 'shadow', 'stun', 'flash', 'rush', 'eyes')


class EnemySurfaces:
    """The callable registered as ``gs.enemy_surface_provider``.

    One slot, one signature, a ``kind`` discriminator -- the same shape as
    ``gs.sprite_provider``, except this one also serves corpses and state
    overlays, so the renderer can stay free of enemy-type knowledge
    altogether.  Every ``kind`` is a cache lookup returning a finished
    SRCALPHA surface; nothing here allocates in the per-frame path.

    It is an object rather than a bare function so the engine can drive the
    cache lifecycle (``clear``) through the same slot it draws from, without
    needing to know the surfaces came from the client.
    """

    __slots__ = ()

    def __call__(self, kind, etype, h, frame=0, state='idle', view=0,
                 detail=MAX_DETAIL, col=None, step=0, base=None):
        if kind == 'model':
            return get_enemy_surface(etype, h, frame, state, view, detail, col)
        if kind == 'corpse':
            return get_corpse_surface(etype, h, col, detail)
        if kind == 'shadow':
            return get_shadow_surface(h)
        if kind == 'stun':
            return get_stun_surface(h, step, detail)
        if kind == 'flash':
            return get_flash_surface(base, step) if base is not None else None
        if kind == 'rush':
            return get_rush_surface(base, step) if base is not None else None
        if kind == 'eyes':
            return get_eye_glow_surface(h, step)
        return None

    def clear(self):
        """Drop every cached surface (map change, tier change)."""
        clear_cache()

    def stats(self):
        return cache_stats()


#: The instance registered by the bridge, before the first frame.
ENEMY_SURFACES = EnemySurfaces()
