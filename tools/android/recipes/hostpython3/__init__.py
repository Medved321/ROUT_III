"""Host Python 3.12 for python-for-android.

Must be the **same version** as ``tools/android/recipes/python3``: p4a compares
the two in ``HostPython3Recipe.download`` and refuses to build when they differ.
Only the version differs from p4a's built-in recipe.

The one patch p4a ships for hostpython (``fix_ensurepip.patch``) lives next to
the built-in recipe, not in our local directory, so it is referenced by absolute
path.  Verified to apply to CPython 3.12.14's ``Lib/ensurepip/__init__.py``.
"""
from os.path import dirname, join

import pythonforandroid.recipes.hostpython3 as _upstream

_UPSTREAM_RECIPE_DIR = dirname(_upstream.__file__)


class HostPython312Recipe(_upstream.HostPython3Recipe):
    version = "3.12.14"
    url = "https://github.com/python/cpython/archive/refs/tags/v{version}.tar.gz"

    patches = [join(_UPSTREAM_RECIPE_DIR, "fix_ensurepip.patch")]


recipe = HostPython312Recipe()
