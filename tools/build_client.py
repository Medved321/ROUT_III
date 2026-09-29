#!/usr/bin/env python3
"""Build a versioned client artifact (zipapp) for the auto-updater.

Usage:
    py -3.12 tools/build_client.py [--version 1.0.1] [--set-latest]
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared.releases import artifact_path, write_latest_pointer  # noqa: E402
from shared.reliability import sha256_hex  # noqa: E402
from shared.version import CLIENT_VERSION, PROTOCOL_VERSION  # noqa: E402

MAIN_TEMPLATE = '''\
import asyncio
import logging
import sys

from client.numpy_bootstrap import require_numpy

if not require_numpy():
    sys.exit(1)

from client.client import GameClient
from client.settings import launch_config
from shared.asyncio_util import use_udp_safe_event_loop

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(name)s %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)


def main():
    host, port, name = launch_config(sys.argv)
    client = GameClient(host, port, name)
    use_udp_safe_event_loop()
    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        client.running = False
        client.renderer.cleanup()
        print("\\nClient stopped.")


if __name__ == "__main__":
    main()
'''

VERSION_PY_TEMPLATE = '''\
from __future__ import annotations

PROTOCOL_VERSION = {protocol}
CLIENT_VERSION = "{client}"
'''

PACKAGES = ("client", "shared", "engine")
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")


def _bundle_numpy(staging: Path) -> bool:
    """Embed the build machine's numpy into the artifact (vendor/numpy + siblings).

    numpy is a mandatory client dependency (task 3.2). We copy the installed
    package dir (and its adjacent ``numpy.libs`` DLL dir on Windows) under
    ``vendor/`` inside the zipapp; at runtime the bootstrap extracts it to a
    cache dir when the interpreter lacks numpy (no pip/network needed).
    Returns True when numpy was bundled.
    """
    try:
        import numpy
    except Exception:
        print("numpy не найден в среде сборки — артефакт будет без встроенного numpy")
        return False
    pkg_dir = Path(numpy.__file__).resolve().parent
    site_dir = pkg_dir.parent
    vendor = staging / "vendor"
    vendor.mkdir(parents=True, exist_ok=True)
    shutil.copytree(pkg_dir, vendor / "numpy", ignore=IGNORE)
    libs_dir = site_dir / "numpy.libs"
    if libs_dir.is_dir():
        shutil.copytree(libs_dir, vendor / "numpy.libs", ignore=IGNORE)
    print(f"numpy {numpy.__version__} встроен в артефакт (vendor/numpy)")
    return True


def _sync_source_version(version: str) -> None:
    target = ROOT / "shared" / "version.py"
    target.write_text(
        VERSION_PY_TEMPLATE.format(protocol=PROTOCOL_VERSION, client=version),
        encoding="utf-8",
    )


def build(version: str, releases_dir: Path, set_latest: bool,
          sync_source: bool = True) -> Path:
    staging = Path(tempfile.mkdtemp(prefix="rout-build-"))
    try:
        for package in PACKAGES:
            shutil.copytree(ROOT / package, staging / package, ignore=IGNORE)
        (staging / "__main__.py").write_text(MAIN_TEMPLATE, encoding="utf-8")
        (staging / "shared" / "version.py").write_text(
            VERSION_PY_TEMPLATE.format(protocol=PROTOCOL_VERSION, client=version),
            encoding="utf-8",
        )
        _bundle_numpy(staging)

        releases_dir.mkdir(parents=True, exist_ok=True)
        target = artifact_path(releases_dir, version)
        zipapp.create_archive(str(staging), str(target), interpreter=None)
        data = target.read_bytes()
        print(f"built {target}")
        print(f"version={version} size={len(data)} sha256={sha256_hex(data)}")
        if set_latest:
            write_latest_pointer(releases_dir, version)
            print(f"latest -> {version}")
            if sync_source:
                _sync_source_version(version)
                print(f"source shared/version.py -> {version}")
        return target
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default=CLIENT_VERSION)
    parser.add_argument("--releases-dir", default=str(ROOT / "releases"))
    parser.add_argument("--set-latest", action="store_true")
    parser.add_argument(
        "--no-source-version", action="store_true",
        help="do not overwrite the source shared/version.py on --set-latest",
    )
    args = parser.parse_args()
    build(
        args.version, Path(args.releases_dir), args.set_latest,
        sync_source=not args.no_source_version,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
