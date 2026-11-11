# Buildozer spec for ROUT III — the Android client.
#
# One command builds the APK (on an x86_64 host with the Android SDK/NDK):
#
#     buildozer android debug
#
# The artifact lands in ``bin/`` and is installed with ``adb install -r`` or by
# opening it on the phone.  The build is also run by
# ``.github/workflows/android.yml``, which is the supported way to obtain an
# APK without a local Android toolchain.
#
# See ``openspec/changes/phone-client-android/design.md`` (D12) for why
# python-for-android with the ``sdl2`` bootstrap is used, and for the version
# pins below.

[app]

title = ROUT III
package.name = rout3
package.domain = org.rout3

# The repository root is the program tree; ``main.py`` is the p4a entrypoint.
source.dir = .
source.include_exts = py,png,jpg,jpeg,json,txt,mt1,mts,atlas

# Build inputs that must not travel inside the APK.  ``music/`` alone is 107 MB
# and the engine already treats a missing track as silence; tests, specs and
# release artifacts are desktop-only.
source.exclude_dirs = .git,.github,.venv,.buildozer,__pycache__,bin,openspec,releases,tools,music
source.exclude_patterns = smoke_*.py,config.json,*.pyc,*.pyo,*.bat,*.sh,README.md,SPEC.md,AGENTS.md,pyproject.toml,requirements.txt

version = 1.2.1

# Python, pygame and numpy are compiled into the APK; the client must never
# need pip, the network or a system interpreter at startup.
requirements = python3,pygame,numpy

# Local recipes pin the versions the project actually needs:
#   pygame   2.6.1  (requirements.txt asks >= 2.5; p4a ships 2.1.0)
#   python3  3.12.14 and hostpython3 3.12.14 (project is Python 3.12; p4a ships 3.14)
# The two python recipes must carry the *same* version: p4a refuses to build
# otherwise (``HostPython3Recipe.download``).
p4a.local_recipes = ./tools/android/recipes
p4a.bootstrap = sdl2
# Pin the p4a revision the local recipes were written against: recipe internals
# (patch locations, `apply_patches` signature) are not a stable API.
p4a.branch = v2026.05.09

orientation = landscape
fullscreen = 1

# numpy's recipe needs NDK API 24; the phone target is Android 12+ anyway.
android.archs = arm64-v8a
android.api = 36
android.minapi = 24
android.ndk_api = 24
# p4a's RECOMMENDED_NDK_VERSION for this revision; buildozer downloads it.
android.ndk = 28c

android.permissions = INTERNET
android.accept_sdk_license = True

# Keep the app private: no requestLegacyExternalStorage, no shared storage
# permission. Settings and editor maps live in the app's own files directory.
android.private_storage = True

# The updater is disabled on Android (client/mobile.py: UPDATER_ENABLED); the
# APK version is the client version.
android.release_artifact = apk

[buildozer]

log_level = 2
warn_on_root = 1
