"""Mandatory client dependency bootstrap: ensure numpy imports before pygame.

Attempts, in order:
  1. plain ``import numpy`` (already installed in the interpreter);
  2. numpy bundled inside the running zipapp artifact (``vendor/numpy`` +
     ``vendor/numpy.libs``, see ``tools/build_client.py``) — extracted once to a
     per-artifact cache dir and prepended to ``sys.path``, so no network/pip
     is needed (offline «artifact contains numpy» path);
3. auto-install: prints «Установка numpy…» and runs
      ``sys.executable -m pip install numpy`` (``--user`` is used for a
      system interpreter; inside a venv pip installs into the venv itself).

If numpy still cannot be imported, returns False; entry points print an
explicit message and exit with a non-zero code. There is no silent fallback to
a non-numpy renderer (design D8 / task 3.2).
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

INSTALL_MESSAGE = "Установка numpy…"
FAILED_MESSAGE = """\
Клиенту требуется numpy, но установить его не удалось.

Установите numpy вручную и запустите клиент снова:
    python -m pip install numpy
"""

MARKER = ".complete"


def _numpy_importable() -> bool:
    try:
        import numpy  # noqa: F401
    except Exception:
        return False
    return True


def _auto_install_allowed() -> bool:
    """Honour the same opt-out used by tools/bootstrap.py (offline tests)."""
    return os.environ.get("ROUT_NO_PIP", "") not in ("1", "true", "yes")


def _pip_install() -> int:
    if sys.prefix == sys.base_prefix:
        args = ["--user"]
    else:
        args = []
    cmd = [
        sys.executable, "-m", "pip", "install",
        *args, "--disable-pip-version-check", "numpy",
    ]
    try:
        result = subprocess.run(cmd, text=True)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"Ошибка установки numpy: {exc}", file=sys.stderr)
        return 1
    return result.returncode


def _running_artifact() -> Path | None:
    candidate = Path(sys.argv[0])
    if not candidate.is_file():
        return None
    if candidate.suffix.lower() in (".pyz", ".zip"):
        return candidate.resolve()
    try:
        if zipfile.is_zipfile(candidate):
            return candidate.resolve()
    except OSError:
        return None
    return None


def _cache_root() -> Path:
    override = os.environ.get("ROUT_NUMPY_CACHE")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(base) / "ROUT" / "vendor-numpy"


def _artifact_cache(artifact: Path) -> Path:
    st = artifact.stat()
    key = f"{artifact.name}:{st.st_size}:{st.st_mtime_ns}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    return _cache_root() / digest


def _extract_vendor(artifact: Path) -> Path | None:
    """Extract ``vendor/*`` from the zipapp into its cache; return vendor root."""
    target = _artifact_cache(artifact)
    vendor = target / "vendor"
    if (target / MARKER).is_file() and (vendor / "numpy" / "__init__.py").is_file():
        return vendor
    shutil.rmtree(target, ignore_errors=True)
    try:
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(artifact) as zp:
            names = zp.namelist()
            if "vendor/numpy/__init__.py" not in names:
                shutil.rmtree(target, ignore_errors=True)
                return None
            for name in names:
                if name.startswith("vendor/"):
                    zp.extract(name, target)
    except (OSError, zipfile.BadZipFile) as exc:
        print(f"Не удалось распаковать встроенный numpy: {exc}", file=sys.stderr)
        shutil.rmtree(target, ignore_errors=True)
        return None
    if not (vendor / "numpy" / "__init__.py").is_file():
        shutil.rmtree(target, ignore_errors=True)
        return None
    try:
        (target / MARKER).write_text("ok", encoding="utf-8")
    except OSError:
        pass
    return vendor


def _load_bundled() -> bool:
    artifact = _running_artifact()
    if artifact is None:
        return False
    vendor_root = _extract_vendor(artifact)
    if vendor_root is None:
        return False
    if str(vendor_root) not in sys.path:
        sys.path.insert(0, str(vendor_root))
    return _numpy_importable()


def ensure_numpy() -> bool:
    if _numpy_importable():
        return True
    if _load_bundled():
        return True
    if not _auto_install_allowed():
        return False
    print(INSTALL_MESSAGE, flush=True)
    if _pip_install() != 0:
        return False
    return _numpy_importable()


def require_numpy() -> bool:
    if ensure_numpy():
        return True
    print(FAILED_MESSAGE, file=sys.stderr)
    return False