"""Python 3.12 for python-for-android.

p4a v2026.05.09 builds Python **3.14.2**, but the client targets Python 3.12
(``pyproject.toml``, ``AGENTS.md``) and pygame 2.6.1 is the version the game is
written and tested against there.  Rather than moving the whole project to a
newer interpreter as part of an Android packaging change, the interpreter
version is pinned here.

The recipe subclasses p4a's own ``python3`` recipe and changes the version,
how patches are located, and one source patch of its own.

Most patches are p4a's.  Locating them is subtle: p4a resolves patch names
relative to the *local* recipe directory, and our directory contains only the
project patch.  The upstream patches are therefore referenced by absolute path
into p4a's built-in recipe, which is also the copy that matches the p4a
revision.  They were verified to apply to CPython 3.12.14:

* ``pyconfig_detection.patch`` -- keeps ``pyconfig.h`` detection working from a
  venv ``site.py``;
* ``reproducible-buildinfo.diff`` -- makes the documented build info
  reproducible;
* ``cpython-311-ctypes-find-library.patch`` -- routes ``ctypes.util.find_library``
  through the Android finder (applies to 3.11+).

One patch is ours (``patches/grp-no-group-enumeration.patch``):
``Modules/grpmodule.c`` calls ``setgrent()``/``getgrent()``/``endgrent()``
unconditionally, and 3.12 never checks for them at configure time.  bionic
declares those functions only from API 26 while ``buildozer.spec`` builds
against ``ndk_api = 24``, so the module failed with ``call to undeclared
function 'setgrent'``.  The patch returns an empty list from ``grp.getgrall()``
below API 26 (there is no group database to walk); ``getgrgid()``/``getgrnam()``
still use the API-24 bionic calls.  ``Modules/pwdmodule.c`` needs no patch: its
``getpwall()`` is already behind ``HAVE_GETPWENT``, which the API-24 configure
test leaves undefined.

``py3.8.1_fix_cortex_a8.patch`` is deliberately not applied: it only matters
for the 32-bit ``armeabi-v7a`` configure branch, and the APK is arm64-only.

The version must match ``tools/android/recipes/hostpython3`` exactly -- p4a's
``HostPython3Recipe.download`` refuses to build when the two disagree.
"""
import shutil
from os.path import dirname, join

import pythonforandroid.recipes.python3 as _upstream
from pythonforandroid.recipe import Recipe

_UPSTREAM_RECIPE_DIR = dirname(_upstream.__file__)

#: Patches p4a applies for every supported version (verified for 3.12.14).
_PATCHES = [
    "patches/pyconfig_detection.patch",
    "patches/reproducible-buildinfo.diff",
    "patches/cpython-311-ctypes-find-library.patch",
]

#: Our own patch, lives next to this file rather than in p4a's recipe.
_LOCAL_PATCHES = [
    "patches/grp-no-group-enumeration.patch",
]


class Python312Recipe(_upstream.Python3Recipe):
    version = "3.12.14"
    url = "https://github.com/python/cpython/archive/refs/tags/v{version}.tar.gz"

    # Overridden in ``apply_patches``; an empty class-level list keeps p4a from
    # looking for our (nonexistent) local ``patches/`` directory first.
    patches: list = []

    def apply_patches(self, arch, build_dir=None):
        names = list(_PATCHES)
        if shutil.which("lld") is not None:
            # Same guard as upstream, but only meaningful for 32-bit ARM.
            names.append("patches/py3.8.1_fix_cortex_a8.patch")
        here = dirname(__file__)
        self.patches = [join(_UPSTREAM_RECIPE_DIR, name) for name in names]
        self.patches += [join(here, name) for name in _LOCAL_PATCHES]
        # Call the base implementation directly: ``Python3Recipe``'s own
        # ``apply_patches`` would append its relative names again.
        Recipe.apply_patches(self, arch, build_dir)


recipe = Python312Recipe()
