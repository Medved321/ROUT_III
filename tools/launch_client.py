#!/usr/bin/env python3
"""Launcher: run the installed artifact (.pyz) if it is newer than sources.

Usage:
    tools/launch_client.py [--host H] [--port P] [--name N]
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.releases import artifact_path, is_newer, latest_version  # noqa: E402
from shared.version import CLIENT_VERSION  # noqa: E402


def newer(candidate: str, base: str) -> bool:
    """Backward-compatible alias for the shared version comparator."""
    return is_newer(candidate, base)


def select_artifact(releases_dir) -> Path | None:
    latest = latest_version(releases_dir)
    if latest and is_newer(latest, CLIENT_VERSION):
        return artifact_path(releases_dir, latest)
    return None


def default_releases_dir() -> Path:
    env = os.environ.get("ROUT_RELEASES_DIR")
    if env:
        return Path(env)
    return ROOT / "releases"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    artifact = select_artifact(default_releases_dir())
    if artifact is not None:
        print(f"Запуск установленного клиента: {artifact.name}", flush=True)
        cmd = [sys.executable, str(artifact), *argv]
    else:
        print("Запуск клиента из исходников", flush=True)
        cmd = [sys.executable, str(ROOT / "run_client.py"), *argv]
    return subprocess.run(cmd, cwd=str(ROOT)).returncode


if __name__ == "__main__":
    raise SystemExit(main())