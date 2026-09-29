#!/usr/bin/env python3
"""Bootstrap for a bare machine: create/repair an isolated venv and run a script.

Usage:
    tools/bootstrap.py [--venv DIR] run <script> [args...]
    tools/bootstrap.py [--venv DIR] ensure
    tools/bootstrap.py [--venv DIR] <python args...>

If ROUT_NO_PIP=1 the pip install step is skipped (used by offline tests).
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VENV = PROJECT_ROOT / ".venv"
REQUIREMENTS = PROJECT_ROOT / "requirements.txt"

# The project targets CPython 3.12; 3.13+ is accepted, free-threaded builds
# are not: pygame/numpy publish no wheels for them, so pip silently falls back
# to compiling from source (minutes of build, usually a failure afterwards).
MIN_PY = (3, 12)
PY_CANDIDATES = (("py", "-3.12"), ("py", "-3.13"), ("py", "-3"), ("py",), ("python",))

# Probes version, free-threadedness and the real executable in one go.
_PROBE = (
    "import sys, sysconfig;"
    "print('%d.%d|%d|%s' % (sys.version_info[0], sys.version_info[1],"
    " 1 if sysconfig.get_config_var('Py_GIL_DISABLED') else 0, sys.executable))"
)

# Human-readable reasons why a candidate interpreter was skipped, filled by
# find_python() and used for the "install Python 3.12" error message.
_REJECTIONS: list[str] = []

USAGE = """\
ROUT III bootstrap

Usage:
  tools/bootstrap.py [--venv DIR] run <script> [args...]   Create/repair the venv
                                                           and run <script> inside it.
  tools/bootstrap.py [--venv DIR] ensure                    Only ensure the venv and
                                                           print its python path.
  tools/bootstrap.py [--venv DIR] <python args...>          Run arbitrary python args
                                                           in the venv (e.g. -c ...).

Environment:
  ROUT_NO_PIP=1    skip `pip install -r requirements.txt` (offline/tests).
"""


def _probe(candidate: list[str] | tuple[str, ...]) -> tuple[tuple[int, int], bool, str] | None:
    """(version, free_threaded, executable) for a candidate, or None if unusable."""
    try:
        result = subprocess.run(
            [*candidate, "-c", _PROBE],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    parts = result.stdout.strip().split("|")
    if len(parts) != 3:
        return None
    try:
        major, minor = parts[0].split(".")
        return (int(major), int(minor)), parts[1] == "1", parts[2]
    except ValueError:
        return None


def find_python() -> list[str] | None:
    """First interpreter that satisfies the project requirements.

    Returns ``None`` when nothing suitable is installed -- deliberately not a
    silent fallback to an arbitrary interpreter: a free-threaded or too old
    build has no pygame wheels, and the resulting source build looks like a
    hang.
    """
    _REJECTIONS.clear()
    for candidate in PY_CANDIDATES:
        info = _probe(candidate)
        if info is None:
            continue
        version, free_threaded, executable = info
        label = " ".join(candidate)
        if free_threaded:
            _REJECTIONS.append(
                f"  - {label} -> {version[0]}.{version[1]} free-threaded "
                f"({executable}): нет wheels для pygame/numpy"
            )
            continue
        if version < MIN_PY:
            _REJECTIONS.append(
                f"  - {label} -> {version[0]}.{version[1]} ({executable}): "
                f"нужен Python {MIN_PY[0]}.{MIN_PY[1]}+"
            )
            continue
        return list(candidate)
    return None


def _no_python_error() -> RuntimeError:
    lines = [
        f"Подходящий Python {MIN_PY[0]}.{MIN_PY[1]}+ (обычная сборка, "
        "не free-threaded) не найден."
    ]
    if _REJECTIONS:
        lines.append("Отклонено:")
        lines.extend(_REJECTIONS)
    lines.append(
        "Установите обычный Python 3.12 и повторите запуск, например:\n"
        "  winget install --id Python.Python.3.12 -e"
    )
    return RuntimeError("\n".join(lines))


def venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return Path(venv_dir) / "Scripts" / "python.exe"
    return Path(venv_dir) / "bin" / "python"


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise RuntimeError("Команда завершилась с ошибкой: " + " ".join(cmd))


def _create_venv(base: list[str], venv_dir: Path) -> None:
    print("Создание виртуального окружения…")
    _run([*base, "-m", "venv", str(venv_dir)])


def _force_rmtree(path: Path) -> None:
    """Remove a venv directory, including read-only files.

    ``shutil.rmtree(ignore_errors=True)`` leaves a half-deleted tree behind
    (the leftover ``Scripts/python.exe`` is what makes the next ``venv`` call
    fail with "Unable to copy ... venvlaunchert.exe"), so a partial removal
    has to be reported instead of ignored.
    """

    def _retry(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE)
        except OSError:
            pass
        try:
            func(target)
        except OSError:
            pass

    try:
        shutil.rmtree(path, onexc=_retry)
    except TypeError:  # Python < 3.12
        shutil.rmtree(path, onerror=_retry)
    if path.exists():
        raise RuntimeError(
            f"Не удалось удалить сломанное окружение {path}. Закройте IDE, "
            "консоль или процессы, использующие .venv, и повторите запуск."
        )


def _recreate_venv(base: list[str], venv_dir: Path) -> None:
    print("Сбойная среда — пересоздаю…")
    _force_rmtree(venv_dir)
    _create_venv(base, venv_dir)


def _imports_ok(python: Path) -> bool:
    result = subprocess.run(
        [str(python), "-c", "import pygame"],
        capture_output=True, text=True, timeout=60,
    )
    return result.returncode == 0


def _venv_ok(python: Path) -> bool:
    """True when the venv runs on a supported interpreter and has pygame."""
    info = _probe([str(python)])
    if info is None:
        return False
    version, free_threaded, _executable = info
    if free_threaded or version < MIN_PY:
        return False
    return _imports_ok(python)


def _install(python: Path) -> None:
    print("Установка зависимостей (pip install -r requirements.txt)…")
    # --only-binary=:all: turns a missing wheel into an immediate, readable
    # error instead of a multi-minute source build that looks like a hang.
    try:
        _run([str(python), "-m", "pip", "install", "--only-binary=:all:",
              "-r", str(REQUIREMENTS)])
    except RuntimeError:
        raise RuntimeError(
            "Не удалось установить зависимости: для этого интерпретатора нет "
            "готовых wheels (pygame/numpy). Проверьте подключение к сети и "
            "версию Python — повторная попытка доустановит зависимости."
        )


def ensure(venv_dir: Path | None = None, no_pip: bool = False) -> Path:
    venv_dir = Path(venv_dir) if venv_dir is not None else DEFAULT_VENV
    base = find_python()
    if base is None:
        raise _no_python_error()
    python = venv_python(venv_dir)

    if not python.exists():
        _create_venv(base, venv_dir)
    elif not _venv_ok(python):
        _recreate_venv(base, venv_dir)

    if no_pip:
        return python

    if not _imports_ok(python):
        _install(python)
        if not _imports_ok(python):
            raise RuntimeError(
                "Не удалось настроить окружение: pygame не импортируется. "
                "Проверьте сеть или установите Python 3.12+ с https://python.org"
            )
    return python


def run_argv(venv_dir: Path | None, args: list[str]) -> int:
    python = ensure(venv_dir)
    return subprocess.run([str(python), *args]).returncode


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    no_pip = os.environ.get("ROUT_NO_PIP", "") in ("1", "true", "yes")
    venv: Path | None = None

    args: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "--venv":
            i += 1
            if i >= len(argv):
                print("Ошибка: --venv требует путь", file=sys.stderr)
                return 2
            venv = Path(argv[i])
            i += 1
        elif argv[i] == "--no-pip":
            no_pip = True
            i += 1
        else:
            args = argv[i:]
            break

    if not args:
        print(USAGE)
        return 1

    if args[0] == "run":
        args = args[1:]
        if not args:
            print(USAGE)
            return 1
        try:
            python = ensure(venv, no_pip=no_pip)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return subprocess.run([str(python), *args]).returncode

    if args[0] == "ensure":
        try:
            python = ensure(venv, no_pip=no_pip)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(str(python))
        return 0

    try:
        python = ensure(venv, no_pip=no_pip)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return subprocess.run([str(python), *args]).returncode


if __name__ == "__main__":
    raise SystemExit(main())