from __future__ import annotations

import re
from pathlib import Path

from shared.reliability import sha256_hex

LATEST_POINTER = "latest.txt"
ARTIFACT_PREFIX = "rout-client-"
ARTIFACT_SUFFIX = ".pyz"
_VERSION_RE = re.compile(r"^[0-9A-Za-z._-]{1,64}$")


def parse_version(version: str) -> tuple[int, ...]:
    """Split a version into leftmost numeric components (malformed -> empty)."""
    parts: list[int] = []
    for token in version.replace("_", ".").replace("-", ".").split("."):
        if token.isdigit():
            parts.append(int(token))
        else:
            break
    return tuple(parts)


def is_newer(candidate: str, base: str) -> bool:
    """True when candidate is numerically newer than base (component-wise).

    Empty or non-numeric versions are never considered newer.
    """
    if not (_valid(candidate) and _valid(base)):
        return False
    cand = parse_version(candidate)
    ref = parse_version(base)
    if not cand or not ref:
        return False
    return cand > ref


def artifact_filename(version: str) -> str:
    return f"{ARTIFACT_PREFIX}{version}{ARTIFACT_SUFFIX}"


def artifact_path(releases_dir, version: str) -> Path:
    return Path(releases_dir) / artifact_filename(version)


def _valid(version: str) -> bool:
    return bool(version) and bool(_VERSION_RE.match(version))


def read_latest_pointer(releases_dir) -> str | None:
    pointer = Path(releases_dir) / LATEST_POINTER
    if not pointer.is_file():
        return None
    version = pointer.read_text(encoding="utf-8").strip()
    return version if _valid(version) else None


def write_latest_pointer(releases_dir, version: str) -> None:
    if not _valid(version):
        raise ValueError(f"invalid version: {version!r}")
    directory = Path(releases_dir)
    directory.mkdir(parents=True, exist_ok=True)
    pointer = directory / LATEST_POINTER
    tmp = pointer.with_suffix(".tmp")
    tmp.write_text(version, encoding="utf-8")
    tmp.replace(pointer)


def list_versions(releases_dir) -> list[str]:
    directory = Path(releases_dir)
    if not directory.is_dir():
        return []
    versions = []
    for entry in directory.glob(f"{ARTIFACT_PREFIX}*{ARTIFACT_SUFFIX}"):
        name = entry.name[len(ARTIFACT_PREFIX):-len(ARTIFACT_SUFFIX)]
        if _valid(name):
            versions.append(name)
    return versions


def latest_version(releases_dir) -> str | None:
    pointer = read_latest_pointer(releases_dir)
    if pointer and artifact_path(releases_dir, pointer).is_file():
        return pointer
    versions = list_versions(releases_dir)
    if not versions:
        return None
    return sorted(versions)[-1]


def read_artifact(releases_dir, version: str) -> bytes | None:
    path = artifact_path(releases_dir, version)
    if not path.is_file():
        return None
    return path.read_bytes()


def artifact_info(releases_dir, version: str) -> dict | None:
    data = read_artifact(releases_dir, version)
    if data is None:
        return None
    return {
        "version": version,
        "path": str(artifact_path(releases_dir, version)),
        "size": len(data),
        "sha256": sha256_hex(data),
    }


class ReleaseStore:
    """Serves the newest client artifact and caches it for handshake answers."""

    def __init__(self, releases_dir):
        self.dir = Path(releases_dir)
        self._cached_version: str | None = None
        self._cached_info: dict | None = None

    def latest(self) -> dict | None:
        version = latest_version(self.dir)
        if version is None:
            self._cached_version = None
            self._cached_info = None
            return None
        if version != self._cached_version or self._cached_info is None:
            info = artifact_info(self.dir, version)
            self._cached_version = version
            self._cached_info = info
        return self._cached_info

    def read(self, version: str) -> bytes | None:
        return read_artifact(self.dir, version)
