"""Android packaging: the APK inputs are what the build expects.

The APK itself is built by cloud CI (``.github/workflows/android.yml``) because
the NDK has no linux-aarch64 host toolchain.  Everything *around* that build is
still in this repository and can be wrong in ways that only show up after a
40-minute CI run, so it is checked here instead:

* ``buildozer.spec`` asks for the right recipe set, architecture, API level,
  orientation and permissions, and pins the APK version to ``CLIENT_VERSION``;
* the local recipes pin the versions the project needs (pygame >= 2.5,
  Python 3.12) and the two Python recipes agree -- p4a refuses the build
  otherwise;
* the pieces that are easy to get wrong are not shipped inside the APK
  (``music/``, tests, specs, the desktop updater);
* ``main.py`` (the p4a entrypoint) disables SDL's synthetic touch mouse *before*
  pygame is imported, works without argv, and requires numpy;
* a startup failure is never silent: the entry point logs a stage-by-stage
  file next to the app (mirrored into its external files dir) and leaves the
  traceback on screen, because p4a's ``start.c`` would otherwise just
  ``exit()`` the activity;
* on Android the settings file lives in the app-private directory (survives an
  APK update), while the program tree is replaced on update;
* a fullscreen renderer ignores the saved window preset, and the client does not
  write the device size back over it;
* numpy is never pip-installed inside an APK.

Run: ``SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python smoke_android_build.py``
"""
from __future__ import annotations

import configparser
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
import pygame  # noqa: E402

pygame.init()
screen = pygame.display.set_mode((1000, 700))

from client import mobile  # noqa: E402
from client.renderer import Renderer  # noqa: E402
from shared.version import CLIENT_VERSION  # noqa: E402


def check(condition, message):
    assert condition, message


def read_spec() -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    parser.read(ROOT / "buildozer.spec", encoding="utf-8")
    return parser


def recipe_version(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(r'^\s*version\s*=\s*["\']([^"\']+)["\']', text, re.M)
    check(match is not None, f"{path.name}: no version pin")
    return match.group(1)


# ── 1. buildozer.spec ──────────────────────────────────────────────
def section_spec():
    cfg = read_spec()
    app = cfg["app"]

    check(app["source.dir"] == ".", "the program tree is the repository root")
    check((ROOT / "main.py").is_file(), "p4a needs a main.py at the source root")

    reqs = {part.strip() for part in app["requirements"].split(",")}
    check(reqs == {"python3", "pygame", "numpy"}, reqs)
    check(app["p4a.bootstrap"] == "sdl2", "touch events need the SDL2 bootstrap")
    check(app["p4a.local_recipes"] == "./tools/android/recipes",
          app["p4a.local_recipes"])
    check(app["p4a.branch"] == "v2026.05.09",
          f"p4a revision must be pinned, got {app.get('p4a.branch')}")
    check((ROOT / "tools/android/recipes").is_dir(), "local recipes missing")

    check(app["android.archs"] == "arm64-v8a", app["android.archs"])
    check(app["orientation"] == "landscape", app["orientation"])
    check(app["fullscreen"] == "1", "the game must own the whole screen")
    check(app["android.api"] == "36", app["android.api"])
    check(app["android.minapi"] == "24", "numpy's recipe requires NDK API 24")
    check(app["android.ndk_api"] == "24", app["android.ndk_api"])
    check(app["android.ndk"] == "28c", app["android.ndk"])
    check("INTERNET" in app["android.permissions"], app["android.permissions"])
    check(app["android.private_storage"] in ("1", "True"),
          "settings and maps must live in app-private storage")
    check(app["version"] == CLIENT_VERSION,
          (app["version"], CLIENT_VERSION))

    # What must not travel inside the APK.  music/ is 107 MB and a missing
    # track is already defined as silence; tests and specs are desktop-only.
    excluded_dirs = {p.strip() for p in app["source.exclude_dirs"].split(",")}
    for name in (".git", "music", "tools", "openspec", "releases"):
        check(name in excluded_dirs, f"{name} must not be packaged")
    patterns = app["source.exclude_patterns"]
    check("smoke_*.py" in patterns, "tests must not be packaged")
    check("config.json" in patterns, "the desktop config must not ship")
    print("buildozer.spec: recipes, arm64, API 24, landscape, exclusions OK")


# ── 2. local recipe pins ──────────────────────────────────────────
def section_recipes():
    base = ROOT / "tools/android/recipes"
    py = recipe_version(base / "python3" / "__init__.py")
    host = recipe_version(base / "hostpython3" / "__init__.py")
    pg = recipe_version(base / "pygame" / "__init__.py")

    check(py == host, f"python3 {py} != hostpython3 {host}: p4a will refuse")
    check(py.startswith("3.12."), f"project targets Python 3.12, recipe pins {py}")
    check(pg == "2.6.1", pg)

    required = re.search(r"pygame>=([\d.]+)", (ROOT / "requirements.txt").read_text())
    check(required is not None, "requirements.txt has no pygame floor")
    floor = tuple(int(x) for x in required.group(1).split("."))
    pinned = tuple(int(x) for x in pg.split("."))
    check(pinned >= floor, f"recipe pygame {pg} < requirements {required.group(1)}")

    # The local python3 recipe must reference upstream patches by absolute path:
    # p4a resolves relative patch names against the local recipe dir, which has
    # no patches/ of its own.
    text = (base / "python3" / "__init__.py").read_text(encoding="utf-8")
    check("_UPSTREAM_RECIPE_DIR" in text and "join(_UPSTREAM_RECIPE_DIR" in text,
          "local recipe must point at p4a's patch directory")
    check("Recipe.apply_patches(self" in text,
          "must not re-enter upstream apply_patches (it re-adds relative names)")

    # pygame must come from the PyPI sdist: it carries the generated C, while
    # the GitHub tag tarball does not, and p4a never installs Cython.
    pg_text = (base / "pygame" / "__init__.py").read_text(encoding="utf-8")
    check("files.pythonhosted.org" in pg_text,
          "pygame recipe must use the sdist (the tag tarball has no generated C)")

    # CPython 3.12's grpmodule.c calls setgrent()/getgrent()/endgrent()
    # unconditionally; bionic only declares them from API 26 while the APK is
    # built against API 24, so the module fails with "call to undeclared
    # function".  A project patch must exist and be wired into the recipe.
    patch = base / "python3" / "patches" / "grp-no-group-enumeration.patch"
    check(patch.is_file(), "CPython grpmodule build patch is missing")
    ptext = patch.read_text(encoding="utf-8")
    check("--- a/Modules/grpmodule.c" in ptext
          and "+++ b/Modules/grpmodule.c" in ptext,
          "grp patch must be a -p1 diff of Modules/grpmodule.c")
    check("__ANDROID_API__ < 26" in ptext and "setgrent" in ptext,
          "grp patch must gate the group enumeration on the API level")
    check("_LOCAL_PATCHES" in text and "join(here, name)" in text,
          "python3 recipe must apply the project patch from its own directory")
    print("recipes: pygame 2.6.1 (sdist), python/hostpython 3.12 agree OK")


# ── 3. main.py ordering and startup ───────────────────────────────
def section_entrypoint():
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    touch_at = src.index("SDL_TOUCH_MOUSE_EVENTS")
    # pygame is initialised by the client import chain; anything that reaches
    # it must come after the environment variable.
    first_pygame_reach = min(src.index("from client.client import"),
                             src.index("from client.numpy_bootstrap import"))
    check(touch_at < first_pygame_reach,
          "SDL_TOUCH_MOUSE_EVENTS must be set before pygame can initialise")
    check('os.environ.setdefault("SDL_TOUCH_MOUSE_EVENTS", "0")' in src,
          "the synthetic touch mouse must be disabled")
    check("launch_config([])" in src, "Android has no CLI arguments")
    check("require_numpy()" in src, "the APK must refuse to run without numpy")
    check("asyncio.run(client.run())" in src, "the entrypoint must run the client")

    # p4a's start.c ends with exit(ret): anything that escapes closes the app
    # with no dialog.  A missing numpy used to ``raise SystemExit(1)``, which
    # on a phone is indistinguishable from a native crash.
    check("raise SystemExit" not in src, "nothing may exit on the way out")
    check("sys.exit(main())" not in src,
          "SystemExit escaping the entrypoint is a silent close on the phone")
    check(re.search(r"try:\s*\n\s*shown = show_fatal\(", src) is not None,
          "the crash reporter itself must be guarded; it is the last line of defence")
    check("except BaseException" in src,
          "startup failures, SystemExit included, must be reported")
    check("show_fatal(" in src, "a failure must leave its traceback on screen")
    check("startup.stage(" in src, "stage markers are the only clue for a native crash")
    check(src.index("def _startup(") < src.index("from client.client import"),
          "the client import must sit inside the guarded startup")

    fatal_src = (ROOT / "client/fatal.py").read_text(encoding="utf-8")
    head = fatal_src.split("def show_fatal", 1)[0]
    check(re.search(r"^import pygame", head, re.M) is None,
          "the reporter must not import pygame at module level (that may be the bug)")
    print("main.py: env before pygame, no argv, numpy required, failures visible OK")


# ── 4. writable config location ──────────────────────────────────
def section_writable_root():
    import client.settings as settings_mod

    # Desktop: the config sits next to the launcher, as it always did.
    check(mobile.writable_root() == str(ROOT),
          f"desktop root is {mobile.writable_root()}, expected {ROOT}")
    check(settings_mod.CONFIG_PATH == os.path.join(str(ROOT), "config.json"),
          settings_mod.CONFIG_PATH)

    # Android: ``CONFIG_PATH`` is computed at import time from the platform
    # environment, so the only honest check is a fresh interpreter with the
    # p4a variables set -- monkey-patching ``os.environ`` after the import
    # would test a value that can never happen on the phone.
    with tempfile.TemporaryDirectory(prefix="rout-android-files-") as private:
        app_dir = os.path.join(private, "app")
        os.makedirs(app_dir, exist_ok=True)
        env = dict(os.environ)
        env.update({
            "ANDROID_PRIVATE": private,
            "ANDROID_APP_PATH": app_dir,
            "ANDROID_ARGUMENT": app_dir,
            "SDL_VIDEODRIVER": "dummy",
            "SDL_AUDIODRIVER": "dummy",
        })
        probe = (
            "import json, os\n"
            "import client.mobile as mobile\n"
            "import client.settings as s\n"
            "cfg = s.Settings()\n"
            "cfg.set('control_mode', 'Телефон')\n"
            "cfg.set('server_host', '192.168.1.120')\n"
            "cfg.set('server_port', 7777)\n"
            "cfg.save()\n"
            "again = s.Settings()\n"
            "again.load()\n"
            "print(json.dumps({\n"
            "    'root': mobile.writable_root(),\n"
            "    'path': s.CONFIG_PATH,\n"
            "    'mode': again.get('control_mode'),\n"
            "    'address': again.address_text,\n"
            "}))\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120,
        )
        check(proc.returncode == 0,
              f"Android config probe failed:\n{proc.stdout}\n{proc.stderr}")
        line = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")][-1]
        info = json.loads(line)
        check(info["root"] == private,
              f"config must live next to app/, not inside it: {info['root']}")
        expected = os.path.join(private, "config.json")
        check(info["path"] == expected,
              f"CONFIG_PATH must be {expected}, got {info['path']}")
        check(info["mode"] == "Телефон", info["mode"])
        check(info["address"] == "192.168.1.120:7777", info["address"])
        check(os.path.isfile(expected), "settings were not written where we claim")
    print("config lives in the app-private files dir, survives update OK")


# ── 5. fullscreen ignores the saved window preset ────────────────
def section_fullscreen():
    from engine import constants as const

    full = Renderer(fullscreen=True)
    size = full.publish_size()
    check(full.fullscreen is True, "Renderer(fullscreen=True) must stay fullscreen")
    check((const.W, const.H) == size, (const.W, const.H, size))
    # A preset must not shrink the phone's frame: the device size wins.
    applied = full.set_window_size((640, 480))
    check(full.fullscreen and applied == size, (applied, size))
    full.screen = None

    windowed = Renderer(fullscreen=False)
    check(windowed.fullscreen is False, "desktop keeps the resizable window")
    applied = windowed.set_window_size((800, 600))
    check(applied == (800, 600), applied)

    # The client must not write the device size back over the saved preset,
    # otherwise a phone run would destroy the desktop window choice.
    import client.client as client_mod

    class _Settings:
        values = {"window_size": "1600×900"}

        def resolution(self):
            return (1600, 900)

        def set(self, key, value):  # pragma: no cover - must not be called
            raise AssertionError(f"fullscreen must not save {key}={value}")

    stub = client_mod.GameClient.__new__(client_mod.GameClient)
    stub.settings = _Settings()
    stub.renderer = Renderer(fullscreen=True)
    stub._apply_window()  # must neither resize nor save
    check(stub.renderer.window_size == size, stub.renderer.window_size)
    stub.renderer.screen = None
    print("fullscreen ignores the saved window preset OK")


# ── 6. the CI build produces the artifact we advertise ───────────
def section_workflow():
    flow = (ROOT / ".github/workflows/android.yml").read_text(encoding="utf-8")
    check("buildozer -v android debug" in flow, "the workflow must build an APK")
    check("bin/*.apk" in flow, "the workflow must upload the APK")
    check("actions/upload-artifact" in flow, "no artifact = nothing to install")
    check("workflow_dispatch" in flow, "the APK must be buildable on demand")
    check("tools/android/recipes/**" in flow,
          "the toolchain cache key must follow the recipe pins")
    # The build is diagnosed from the annotations, not from the job log: the
    # run log is 403 without a token on this public repository.
    check("tee buildozer.log" in flow, "a failed build must leave a log to read")
    check("report_build_log.py" in flow, "the log must reach the check annotations")
    check("rout3-buildozer-log" in flow, "the raw log must be downloadable")
    # A change to the reporter itself must rebuild too; otherwise the next
    # failure is still diagnosed by the previous script.
    check('".github/scripts/**"' in flow,
          "editing the failure reporter must trigger a build")
    print("CI workflow builds and uploads the debug APK OK")


# ── 7. a failed build explains itself without the private job log ─
def section_failure_log():
    script = ROOT / ".github/scripts/report_build_log.py"
    check(script.is_file(), "the failure reporter is missing")

    # ``[DEBUG]`` lines are long.  With a tail window of 150 lines that is far
    # more than four annotations hold, so the chunks must be taken from the
    # *end* of the window; otherwise the verdict below is never reached.
    chatter = "".join(f"[DEBUG]   [arm64-v8a] Compile : thing{i}.c" + " " * 400
                      + "x\n" for i in range(400))
    log = "".join([
        "Running 'make'\n",
        "  CC  Modules/posixmodule.c\n",
        "patch: **** Only garbage was found in the patch input.\n",
        "Error: one of the patches failed to apply\n",
        chatter,
        "Exception in thread background thread for pid 42:\n",
        "Traceback (most recent call last):\n",
        '  File "toolchain.py", line 1, in <module>\n',
        "BuildInterruptingException: python3 failed\n",
        # The marker sits past the old 300-char cut, so the annotation only
        # reaches it when long lines keep enough of themselves.
        ("E" * 650) + "TAIL_MARKER\n",
        # buildozer dumps its environment on failure; all of it is noise.
        "".join(f"#     ENV_VAR_{i} = 'value'\n" for i in range(150)),
        "# Command failed: buildozer -v android debug",
    ])
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        log_path = tmp / "buildozer.log"
        log_path.write_text(log, encoding="utf-8")
        summary = tmp / "summary.md"
        env = dict(os.environ, GITHUB_STEP_SUMMARY=str(summary))
        run = subprocess.run(
            [sys.executable, str(script), str(log_path)],
            capture_output=True, text=True, env=env)
        check(run.returncode == 0, "reporting a failure must never fail the job")
        out = run.stdout

        check("::error title=buildozer errors::" in out,
              "the failing lines must become an annotation")
        # GitHub stores only ten annotations per step, so the tail has to be
        # emitted before the keyword matches or it is dropped.
        check(out.splitlines()[0].startswith("::error title=buildozer tail::"),
              "the tail must be the first annotation")
        check("patches failed to apply" in out,
              "the annotation must quote the failure, not just say it failed")
        check("BuildInterruptingException: python3 failed" in out,
              "the tail of the traceback must survive")
        check("TAIL_MARKER" in out,
              "long lines must keep enough to hold the failing command")
        check("ENV_VAR_0" not in out,
              "the environment dump must not push the traceback out")
        check("# Command failed: buildozer" in out,
              "the final verdict must survive the tail window")
        tail_cmds = [line for line in out.splitlines()
                     if line.startswith("::error title=buildozer tail::")]
        check(any("Command failed: buildozer" in line for line in tail_cmds),
              "the tail must reach the verdict, not stop in the middle of the log")
        check("%0A" in out,
              "a multiline annotation must be escaped, not dropped")
        check(len(out.splitlines()) <= 10,
              "GitHub stores ten annotations per step; emitting more loses the tail")
        check(all(line.startswith("::error") for line in out.splitlines()),
              "every emitted line must be a workflow command")
        summary_text = summary.read_text(encoding="utf-8") if summary.is_file() else ""
        check("BuildInterruptingException: python3 failed" in summary_text,
              "a human reading the run needs the traceback in the step summary")
    print("a failed cloud build publishes its own diagnosis OK")


# ── 4. on-device crash reporting ─────────────────────────────────
CRASH_SCRIPT = r'''
import os, sys
from client import fatal

# The external mirror is derived from ANDROID_PRIVATE, not asked of Java.
assert fatal.external_files_dir("/data/user/0/org.rout3.rout3/files") == \
    "/storage/emulated/0/Android/data/org.rout3.rout3/files"
assert fatal.external_files_dir("") is None
assert fatal.external_files_dir(None) is None

log = fatal.StartupLog()
assert log.path, "the startup log was not created anywhere"
assert log.paths[0].startswith(os.environ["ANDROID_PRIVATE"]), log.paths
log.stage("numpy import")
try:
    raise ImportError("boom-numpy")
except ImportError as exc:
    text = log.crash(exc)
log.close()

data = open(log.path, encoding="utf-8").read()
assert "stage: numpy import" in data, data
assert "ImportError: boom-numpy" in data, data
assert "Этап: numpy import" in text, text

# The failed run leaves a boot marker, and the next launch sees the stage even
# when the traceback never reached the screen (a native crash closes the app
# before any Python can draw).
again = fatal.StartupLog()
assert again.previous, "the next launch must see the failed run"
assert again.previous["stage"] == "numpy import", again.previous
assert again.previous["status"] == "failed", again.previous
# The traceback travels with the marker: when the exception *is* the display,
# the current run cannot draw it and only the next launch can.
tail = "\n".join(again.previous.get("tail") or [])
assert "ImportError: boom-numpy" in tail, again.previous
assert again.previous.get("python"), "the marker names the interpreter that died"
# Once startup completes, a later normal close is not a crash.
again.mark_running()
again.close()
third = fatal.StartupLog()
assert third.previous is None, third.previous
third.clear_boot()
third.close()
fourth = fatal.StartupLog()
assert fourth.previous is None, fourth.previous
fourth.close()

# An unwritable candidate set must degrade to memory, never raise.
ro = fatal.StartupLog(private="/proc/definitely-not-here", app_root="/proc/nope")
ro.write("still fine")
ro.stage("numpy import")
ro.clear_boot()
ro.close()

# The screen itself must draw with whatever driver the device has.
assert fatal.show_fatal(text, log=log, interactive=False) is True
import pygame
raw = pygame.image.tobytes(pygame.display.get_surface(), "RGB")
total = len(raw) // 3
ink = total - raw.count(b"\x08\x08\x0c")
assert ink > 2000, f"the report screen is blank ({total} px, {ink} ink)"
print("CRASH REPORTER OK")
'''


def section_crash_reporter():
    with tempfile.TemporaryDirectory() as tmp:
        private = os.path.join(tmp, "files")
        app_root = os.path.join(private, "app")
        os.makedirs(app_root)
        env = dict(os.environ)
        env.update({
            "ANDROID_ARGUMENT": app_root,
            "ANDROID_APP_PATH": app_root,
            "ANDROID_UNPACK": app_root,
            "ANDROID_PRIVATE": private,
            "P4A_BOOTSTRAP": "sdl2",
            "PYTHONPATH": str(ROOT),
            "SDL_VIDEODRIVER": "dummy",
            "SDL_AUDIODRIVER": "dummy",
        })
        proc = subprocess.run([sys.executable, "-c", CRASH_SCRIPT], cwd=tmp,
                              env=env, capture_output=True, text=True, timeout=180)
        check(proc.returncode == 0,
              f"crash reporter failed:\n{proc.stdout}\n{proc.stderr}")
        check("CRASH REPORTER OK" in proc.stdout, proc.stdout)
    print("startup crash: log mirror, stage markers, on-screen traceback OK")


# ── 4b. the desktop end of the same trail ────────────────────────
def section_device_tool():
    import importlib.util

    path = ROOT / "tools" / "android_log.py"
    spec = importlib.util.spec_from_file_location("android_log", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # The one fact that makes an installed APK close before Python runs: its
    # ABI is not on the device.  Naming it saves a whole build/test cycle.
    arm64 = "ro.product.cpu.abi = arm64-v8a\nro.product.cpu.abilist = arm64-v8a,armeabi-v7a"
    only32 = "ro.product.cpu.abi = armeabi-v7a\nro.product.cpu.abilist = armeabi-v7a"
    check(module.arch_warning(["arm64-v8a"], arm64) is None,
          "an arm64 APK on an arm64 phone must not warn")
    warning = module.arch_warning(["arm64-v8a"], only32)
    check(warning is not None and "arm64-v8a" in warning,
          "a 64-bit-only APK on a 32-bit phone must be named as the cause")
    check(module.arch_warning([], arm64) is None, "an unknown APK must stay quiet")

    apk = ROOT / "releases" / "rout3-1.2.0-arm64-v8a-debug.apk"
    if apk.is_file():
        check(module.apk_archs(str(apk)) == ["arm64-v8a"],
              "the shipped APK must declare exactly the arm64 ABI")

    # The log lives in app-private storage and in the separate crash buffer;
    # both are invisible to a file manager on Android 11+.
    script = path.read_text(encoding="utf-8")
    check("run-as" in script and "rout3-startup.log" in script,
          "the tool must read the app-private startup log")
    check("-b\", \"crash\"" in script or "-b crash" in script,
          "the native crash buffer is separate from the main logcat buffer")
    check(module.summary("I/x: hello\nE/PythonActivity: boom\nI/y: ok") ==
          ["E/PythonActivity: boom"], "the summary must keep only relevant lines")

    proc = subprocess.run([sys.executable, str(path), "--help"],
                          capture_output=True, text=True, timeout=60)
    check(proc.returncode == 0 and "--no-launch" in proc.stdout,
          f"the tool must document its flags:\n{proc.stdout}\n{proc.stderr}")
    print("device tool: ABI mismatch, crash buffer, app-private log OK")


def main():
    section_spec()
    section_recipes()
    section_entrypoint()
    section_crash_reporter()
    section_device_tool()
    section_writable_root()
    section_fullscreen()
    section_workflow()
    section_failure_log()
    print("ALL ANDROID PACKAGING SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()
