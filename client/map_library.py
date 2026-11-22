"""Saved sector maps: one catalogue and one validated loader.

The lobby and the sector editor used to disagree: the editor scanned
``maps/`` itself, read only ``.mts`` on a click and the lobby had no list at
all.  This module owns both the directory and the two rules -- deduplicated
names from ``.mts``/``.mt1`` and ``.mts``-first loading with validation -- so a
map shown in a list can always be loaded and sent to a party.

The public functions never raise: an unreadable, malformed or unsafe name
comes back as ``(name, None, errors)`` and the caller decides what to show.
Client-only; no network, engine or server imports.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from shared import text_map
from shared.sector_map import SectorMap

MAPS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "maps"
)

MAP_EXTENSIONS = (".mts", ".mt1")


@dataclass(frozen=True)
class MapSelection:
    """A validated map ready to go into ``CREATE_PARTY``.

    Payload is captured at selection time: a file may disappear or change
    afterwards, but the party gets exactly the bytes the player picked.
    """

    name: str
    payload: bytes


def list_map_names(maps_dir: str | os.PathLike | None = None) -> list[str]:
    """Sorted base names of saved maps, once per name regardless of extension."""
    root = maps_dir if maps_dir is not None else MAPS_DIR
    try:
        entries = os.listdir(root)
    except OSError:
        return []
    names = {os.path.splitext(fn)[0] for fn in entries
             if fn.lower().endswith(MAP_EXTENSIONS)}
    return sorted(names)


def source_path(name: str, maps_dir: str | os.PathLike | None = None) -> str:
    """File the loader would read for ``name`` (``.mts`` before ``.mt1``)."""
    root = maps_dir if maps_dir is not None else MAPS_DIR
    mts = os.path.join(root, f"{name}.mts")
    if os.path.exists(mts):
        return mts
    return os.path.join(root, f"{name}.mt1")


def _bad_name(name: str) -> bool:
    """A UI-provided name must never escape the maps directory."""
    return (not isinstance(name, str) or not name
            or name in (".", "..")
            or os.path.basename(name) != name
            or "/" in name or "\\" in name)


def load(name: str, *, validate: bool = True,
         maps_dir: str | os.PathLike | None = None
         ) -> tuple[str, bytes | None, list[str]]:
    """Read a saved sector map and return ``(name, .mt1 bytes, errors)``.

    ``.mts`` is preferred over ``.mt1`` for the same name.  Text maps are
    parsed by ``text_map.load`` and then checked as the very bytes that go on
    the wire; binary maps are checked with ``SectorMap.validate_bytes``.
    With ``validate=False`` a text/binary map that parses but fails model
    validation still returns its bytes together with the errors, which is what
    the editor needs to open a damaged map for repair.
    """
    if _bad_name(name):
        return name, None, [f"недопустимое имя карты: {name!r}"]
    root = maps_dir if maps_dir is not None else MAPS_DIR
    mts = os.path.join(root, f"{name}.mts")
    mt1 = os.path.join(root, f"{name}.mt1")
    if os.path.exists(mts):
        try:
            sm = text_map.load(Path(mts), validate=validate)
        except Exception as exc:
            return name, None, [f"{name}.mts: {exc}"]
        try:
            payload = sm.to_bytes()
        except Exception as exc:
            return name, None, [f"{name}.mts: {exc}"]
        errs = SectorMap.validate_bytes(payload)
        if validate and errs:
            return name, None, errs
        return name, payload, errs
    if os.path.exists(mt1):
        try:
            with open(mt1, "rb") as fh:
                payload = fh.read()
        except OSError as exc:
            return name, None, [f"{name}.mt1: {exc}"]
        errs = SectorMap.validate_bytes(payload)
        if validate and errs:
            return name, None, errs
        return name, payload, errs
    return name, None, [f"Не найдено: maps/{name}.mts / .mt1"]
