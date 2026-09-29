"""Boundary scanner for the standalone engine package (engine-standalone).

AST-scans every ``engine/*.py`` and enforces the import boundary from the
``engine/modularity`` spec: only stdlib, ``pygame``, ``numpy``, the contract
modules ``shared.sector_map`` / ``shared.mt1`` and the package's own modules
(``engine.*``) are allowed.  ``client.*``, ``server.*``, ``shared.map_data``
and ``shared.protocol``/``shared.reliability``/``shared.version`` are
forbidden.

The scanner also checks package composition (``FORBIDDEN_MODULES``): the engine
creates no content, so sprite generation and the tile-editor model must never
reappear inside it (updating_engine D7).  ``enemy_models.py`` is in that set
for the same reason as ``sprites.py``: enemy art is a descriptor registry plus
a painter, and it belongs to the client (change ``enemy-model-overhaul``).

And it checks that no module *inside* the engine builds enemy art of its own.
The engine may ask for a surface through the ``gs.enemy_surface_provider`` slot
and may draw what it gets back; drawing an enemy out of raw ``pygame.draw``
calls is the duplication the enemy-presentation spec forbids
("отдельной ветки отрисовки на каждый тип в рендерере SHALL NOT оставаться"),
so it is a boundary violation, not a style preference.  The check is
deliberately narrow -- it looks for a per-type branch, not at every
``pygame.draw`` call, because the engine still draws items, projectiles and its
own HUD with primitives and always will.

``custom_content`` and ``voxel`` are allowed: optional external mod hooks
(loaded lazily only when custom weapons are in play), not client/server/network.
"""

from __future__ import annotations

import ast
import os
import sys

ENGINE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine")

ALLOWED_SHARED = {"shared.sector_map", "shared.mt1"}
BARE_SHARED_NAMES = {"sector_map", "mt1"}
ALLOWED_EXT = {"custom_content", "voxel"}
FORBIDDEN_PREFIXES = ("client", "server")
FORBIDDEN_SHARED = {"shared.protocol", "shared.reliability", "shared.version",
                    "shared.map_data"}
# Content-generation modules: they belong to the client, not to the render core.
FORBIDDEN_MODULES = {"sprites.py", "map_editor.py", "enemy_models.py"}

#: An enemy-type branch drawn with primitives.  ``item`` types (med/amm/...) are
#: deliberately not here: they are pickup icons, a different capability, and
#: they are still drawn with primitives on purpose.
ENEMY_TYPE_NAMES = frozenset({
    "soldier", "shotgunguy", "chaingunner", "imp", "spectre", "lost_soul",
    "cacodemon", "pain_elemental", "hell_knight", "revenant", "archvile",
    "mancubus", "dog", "pinkie", "arachnotron", "boss_baron", "boss_cyber",
    "boss_spider", "healer", "artillery", "demon", "baron", "pain", "spider",
})


def _enemy_type_branches(tree: ast.AST) -> list[tuple[str, int, str]]:
    """Per-enemy-type ``if/elif`` drawing branches inside the engine.

    Matches a comparison of a name that looks like a type variable against an
    enemy type name.  The provider slot is the one sanctioned way for the
    engine to obtain enemy art, and it is a call, not a branch.
    """
    found: list[tuple[str, int, str]] = []
    type_vars = {"t", "etype", "etype_name", "ename", "enemy_type"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        if not (isinstance(left, ast.Name) and left.id in type_vars):
            continue
        for op, comparator in zip(node.ops, node.comparators):
            if not isinstance(op, (ast.Eq, ast.NotEq)):
                continue
            if isinstance(comparator, ast.Constant) and \
                    comparator.value in ENEMY_TYPE_NAMES:
                found.append(("", node.lineno, f"enemy art branch on {left.id!r}"))
    return found


def _classify(module: str, imported: list[str] | None, stdlib: set[str]) -> str | None:
    """Return an offending module name or None when the import is allowed."""
    if module == "__future__" or module in stdlib or module in {"pygame", "numpy"}:
        return None
    if module == "engine" or module.startswith("engine."):
        return None
    if module in ALLOWED_EXT:
        return None
    if module.startswith(FORBIDDEN_PREFIXES):
        return module
    if module in FORBIDDEN_SHARED:
        return module
    if module == "shared":
        if imported and all(n in BARE_SHARED_NAMES for n in imported):
            return None
        return module
    if module.startswith("shared."):
        if module in ALLOWED_SHARED:
            return None
        return module
    return module


def scan() -> list[tuple[str, int, str]]:
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    errors: list[tuple[str, int, str]] = []
    for fn in sorted(os.listdir(ENGINE_DIR)):
        if fn in FORBIDDEN_MODULES:
            errors.append((os.path.join(ENGINE_DIR, fn), 0,
                           f"content module '{fn}' must not live in engine/"))
            continue
        if not fn.endswith(".py"):
            continue
        path = os.path.join(ENGINE_DIR, fn)
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
        for _, lineno, what in _enemy_type_branches(tree):
            errors.append((path, lineno, what))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    bad = _classify(alias.name, None, stdlib)
                    if bad:
                        errors.append((path, node.lineno, f"import {alias.name}"))
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative import
                    continue
                if node.module is None:
                    continue
                names = [a.name for a in node.names]
                bad = _classify(node.module, names, stdlib)
                if bad:
                    errors.append((path, node.lineno, f"from {node.module} import ..."))
    return errors


def main() -> int:
    errors = scan()
    if errors:
        for path, lineno, what in errors:
            where = os.path.relpath(path)
            loc = f"{where}:{lineno}" if lineno else where
            print(f"{loc}: FORBIDDEN {what}")
        print(f"engine boundary violations: {len(errors)}")
        return 1
    print("engine imports clean: boundary OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())