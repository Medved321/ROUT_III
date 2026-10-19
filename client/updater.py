from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from shared.releases import (
    artifact_path,
    is_newer,
    latest_version,
    list_versions,
    read_latest_pointer,
    write_latest_pointer,
)
from shared.reliability import sha256_hex, verify_sha256
from shared.version import CLIENT_VERSION

logger = logging.getLogger(__name__)


class UpdateError(Exception):
    pass


def validate_offer(version: str, size: int, sha256: str) -> None:
    """Reject a malformed or downgrade update offer before fetching any bytes.

    The server already only advertises versions newer than the client's, but the
    offer arrives over the same untrusted channel, so the client re-checks it
    rather than trusting the server to be well-behaved.
    """
    if not is_newer(version, CLIENT_VERSION):
        raise UpdateError(
            f"Предложенная версия {version!r} не новее текущей {CLIENT_VERSION}"
        )
    digest = (sha256 or "").lower()
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise UpdateError(f"Некорректная контрольная сумма обновления: {sha256!r}")


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
    # Trust anchor for operators who want one. The advertised hash travels over
    # the same channel as the artifact, so on its own it only catches transit
    # corruption, not a malicious server. If ROUT_UPDATE_SHA256 is set, the
    # artifact must also match that pinned value; without a pin the client trusts
    # the server it connected to (the address guard in NetworkClient ensures the
    # offer came from that server), which is the same trust model as the game.
    pin = (os.environ.get("ROUT_UPDATE_SHA256") or "").strip().lower()
    if pin and sha256_hex(data) != pin:
        raise UpdateError(
            "Контрольная сумма не совпала с закреплённой (ROUT_UPDATE_SHA256)"
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
