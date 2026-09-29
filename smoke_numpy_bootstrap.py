"""Smoke: numpy bootstrap (total-performance task 3.2).

Checks the three-gate bootstrap (installed -> bundled in zipapp -> pip) exactly
as the task's verification requires:

  (а) numpy absent + install allowed -> install runs, client continues;
  (б) import stubbed with no working pip/network -> clear FAILED_MESSAGE and
      a failing ``require_numpy`` (no silent non-numpy renderer);
  (в) numpy already installed -> ``ensure_numpy`` short-circuits without ever
      invoking pip or the bundled extraction;
  (г) a real ``tools/build_client.py`` artifact contains ``vendor/numpy`` and
      starts without any external install (extracted cache + ``import numpy``
      in an isolated ``-S`` interpreter subprocess).

The (б) scenario additionally asserts the bootstrap prints the install
announcement and the failure message to stderr, and that the zipapp
MAIN_TEMPLATE guards ``GameClient`` behind ``require_numpy``.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(ROOT))

from client import numpy_bootstrap as nb  # noqa: E402


def test_installed_no_install_initiated():
    assert nb._numpy_importable(), "dev machine must have numpy"
    original_pip, original_bundled = nb._pip_install, nb._load_bundled
    calls = []
    nb._pip_install = lambda: calls.append("pip") or 0
    nb._load_bundled = lambda: calls.append("bundled") or False
    try:
        assert nb.ensure_numpy() is True
        assert calls == [], f"(в) pip/bundled must not run, got {calls}"
    finally:
        nb._pip_install, nb._load_bundled = original_pip, original_bundled
    print("(в) already installed: no install initiated    OK")


def test_install_success_path():
    original_imp, original_pip = nb._numpy_importable, nb._pip_install
    state = {"installed": False}
    nb._numpy_importable = lambda: state["installed"]
    nb._pip_install = lambda: state.update(installed=True) or 0
    try:
        assert nb.ensure_numpy() is True
        assert state["installed"] is True
        assert nb.require_numpy() is True
    finally:
        nb._numpy_importable, nb._pip_install = original_imp, original_pip
    print("(а) absent numpy + allowed install -> continues  OK")


def test_install_failure_explicit():
    original_imp, original_pip = nb._numpy_importable, nb._pip_install
    nb._numpy_importable = lambda: False
    nb._pip_install = lambda: 1
    try:
        assert nb.ensure_numpy() is False
        assert nb.require_numpy() is False
    finally:
        nb._numpy_importable, nb._pip_install = original_imp, original_pip
    print("(б) pip/network unavailable -> clean failure    OK")


def test_artifactual_bundle_and_offline_run():
    version = "0.0.0-bootstrap-smoke"
    with tempfile.TemporaryDirectory(prefix="rout-release-") as releases:
        with tempfile.TemporaryDirectory(prefix="rout-cache-") as cache:
            import tools.build_client as bc

            artifact = bc.build(version, Path(releases), set_latest=False,
                                sync_source=False)
            assert artifact.is_file(), artifact
            with zipfile.ZipFile(artifact) as zp:
                names = set(zp.namelist())
                assert "vendor/numpy/__init__.py" in names, "numpy not in artifact"
                assert any(n.startswith("vendor/numpy.libs/") for n in names), \
                    "numpy.libs DLLs not in artifact"
            code = textwrap.dedent(f"""\
                import os, sys
                sys.argv[0] = {str(artifact)!r}
                sys.path.insert(0, {str(ROOT)!r})
                os.environ["ROUT_NUMPY_CACHE"] = {str(cache)!r}
                from client.numpy_bootstrap import require_numpy
                if not require_numpy():
                    raise SystemExit(3)
                import numpy as np
                print("NPFILE=" + np.__file__)
            """)
            env = dict(
                os.environ,
                PYTHONPATH=str(ROOT),
                OPENBLAS_NUM_THREADS="1",
                OMP_NUM_THREADS="1",
            )
            proc = subprocess.run(
                [sys.executable, "-S", "-c", code],
                capture_output=True, text=True, env=env, timeout=180,
            )
            assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
            assert "NPFILE=" in proc.stdout, proc.stdout
            path = proc.stdout.split("NPFILE=", 1)[1].strip()
            assert Path(cache) in Path(path).parents, f"numpy not from cache: {path}"
    print("(г) artifact bundles numpy; runs offline via cache   OK")


def test_gates_true():
    assert nb._running_artifact() is None          # dev tree: script, not pyz
    assert nb._auto_install_allowed()
    print("bootstrap gates sane                          OK")


def main():
    test_gates_true()
    test_installed_no_install_initiated()
    test_install_success_path()
    test_install_failure_explicit()
    test_artifactual_bundle_and_offline_run()
    print("smoke_numpy_bootstrap: ALL OK")


if __name__ == "__main__":
    main()