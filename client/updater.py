from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from shared.releases import (
    artifact_path,
    latest_version,
    list_versions,
    read_latest_pointer,
    write_latest_pointer,
)
from shared.reliability import sha256_hex, verify_sha256

logger = logging.getLogger(__name__)


class UpdateError(Exception):
    pass


def default_releases_dir() -> Path:
    env = os.environ.get("ROUT_RELEASES_DIR")
    if env:
        return Path(env)
    return Path.cwd() / "releases"


def apply_update(
    releases_dir, version: str, data: bytes, expected_sha256: str
) -> Path:
    """Store a verified artifact as its own version file and move the pointer.

    The running artifact has a different versioned filename, so it is never
    overwritten while in use.
    """
    if not expected_sha256 or not verify_sha256(data, expected_sha256):
        raise UpdateError(
            f"Контрольная сумма не совпала для версии {version} "
            f"(получено {sha256_hex(data)[:12]}...)"
        )
    directory = Path(releases_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = artifact_path(directory, version)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    write_latest_pointer(directory, version)
    logger.info(f"Обновление применено: {path}")
    return path


def rollback(releases_dir) -> str | None:
    """Point back to the previous installed version (if any)."""
    current = latest_version(releases_dir)
    versions = sorted(list_versions(releases_dir))
    previous = [v for v in versions if v != current]
    if not previous:
        return None
    target = previous[-1]
    write_latest_pointer(releases_dir, target)
    logger.info(f"Откат на версию {target}")
    return target


def running_artifact() -> Path | None:
    candidate = Path(sys.argv[0])
    if candidate.suffix == ".pyz" and candidate.is_file():
        return candidate.resolve()
    return None


def restart(artifact: Path) -> subprocess.Popen:
    """Start the freshly installed artifact. The caller must exit afterwards."""
    logger.info(f"Перезапуск клиента: {artifact}")
    return subprocess.Popen(
        [sys.executable, str(artifact)],
        cwd=str(Path.cwd()),
        close_fds=True,
    )


def current_version() -> str:
    return read_latest_pointer(default_releases_dir()) or "unknown"
