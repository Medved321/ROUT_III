"""Collect what a phone says about a ROUT III startup -- without guessing.

The APK's own diagnostics (``client/fatal.py``) write a boot marker and a log
into app-private storage, and mirror every line to ``stderr``, which p4a turns
into logcat.  Those are the only two places where an instant close leaves a
trace, and neither is reachable from the phone's file manager on Android 11+.

This script is the desktop end of that trail, so a crash report never depends
on someone remembering the right ``adb`` incantation:

    py -3.12 tools/android_log.py                  # launch + capture + pull logs
    py -3.12 tools/android_log.py --no-launch      # only pull the stored logs
    py -3.12 tools/android_log.py --seconds 30     # longer capture window
    py -3.12 tools/android_log.py --reinstall releases/rout3-1.2.1-arm64-v8a-debug.apk

What it does, in order:

1. finds ``adb`` (PATH, ``ANDROID_HOME``/``ANDROID_SDK_ROOT``, the usual
   Windows/Linux/macOS SDK locations) and lists devices;
2. prints the device model, Android version and ABI -- the three facts that
   decide whether the APK can even run there;
3. clears logcat, launches the app through ``monkey`` (no launcher guesswork)
   and records logcat for a few seconds, including the ``crash`` buffer;
4. reads ``files/rout3-startup.log``, ``files/rout3-boot.json`` and the
   external mirror through ``run-as`` / ``adb shell cat``;
5. writes everything to one text file and prints the lines that name the app,
   Python, SDL, the native linker or the system crash handler.

It never modifies the phone beyond clearing logcat and launching the app.
Uninstalling is a separate, explicit flag because it also wipes the saved
config and the boot marker.

No third-party imports: the tool has to work on a bare Python install.
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PACKAGE = "org.rout3.rout3"
OUT_DIR = os.environ.get("ROUT3_DIAG_DIR") or "/tmp/opencode"

#: Logcat lines worth showing in the summary.  The full capture is still saved;
#: this is only what a human should read first.
INTERESTING = (
    "org.rout3",
    "PythonActivity",
    "python",
    "Python",
    "SDL",
    "libc",
    "linker",
    "DEBUG",
    "AndroidRuntime",
    "FATAL",
    "Traceback",
    "stage:",
    "FAILED at stage",
    "numpy",
    "pygame",
)

ADB_CANDIDATES = (
    "adb",
    "adb.exe",
    os.path.join(os.environ.get("ANDROID_HOME", ""), "platform-tools", "adb"),
    os.path.join(os.environ.get("ANDROID_SDK_ROOT", ""), "platform-tools", "adb"),
    "/usr/lib/android-sdk/platform-tools/adb",
    "/opt/android-sdk/platform-tools/adb",
    os.path.expanduser("~/Android/Sdk/platform-tools/adb"),
    os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"),
    os.path.expanduser("~/Android/Sdk/platform-tools/adb.exe"),
    os.path.expanduser("~/AppData/Local/Android/Sdk/platform-tools/adb.exe"),
)


# ── process plumbing ─────────────────────────────────────────────────
def find_adb() -> str | None:
    for candidate in ADB_CANDIDATES:
        if not candidate:
            continue
        found = shutil.which(candidate)
        if found:
            return found
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def run(adb: str, *args: str, timeout: float = 30.0) -> tuple[int, str]:
    """Run one adb command; always return text, never raise."""
    try:
        proc = subprocess.run([adb, *args], capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"<adb {' '.join(args)}: {exc}>"
    text = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    if err.strip():
        text = (text + "\n" + err).strip()
    return proc.returncode, text


def shell(adb: str, command: str, timeout: float = 30.0) -> tuple[int, str]:
    return run(adb, "shell", command, timeout=timeout)


def devices(adb: str) -> list[str]:
    code, text = run(adb, "devices")
    if code != 0:
        return []
    out = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            out.append(parts[0])
    return out


# ── capture ──────────────────────────────────────────────────────────
def capture_logcat(adb: str, serial: str | None, launch: bool, package: str,
                   seconds: float) -> str:
    base = ["-s", serial] if serial else []
    sections: list[str] = []

    if launch:
        run(adb, *base, "logcat", "-c")
        code, text = run(adb, *base, "shell", "monkey", "-p", package,
                         "-c", "android.intent.category.LAUNCHER", "1")
        sections.append(f"$ adb shell monkey -p {package} ... -> {code}\n{text}")
        # Monkey returns as soon as the intent is dispatched; the app then needs
        # a moment to unpack ``private.tar`` and start Python.  Give the slowest
        # plausible startup (cold install on a cheap phone) room to fail.
        time.sleep(min(seconds, 3.0))

    try:
        proc = subprocess.Popen([adb, *base, "logcat", "-v", "time"],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
    except OSError as exc:
        return "\n".join(sections + [f"<logcat: {exc}>"])

    started = time.monotonic()
    chunks: list[bytes] = []
    try:
        while time.monotonic() - started < seconds:
            time.sleep(0.2)
    finally:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=10)
            chunks.append(out or b"")
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()
            chunks.append(out or b"")
    sections.append("$ adb logcat -v time\n" + b"".join(chunks).decode("utf-8", "replace"))

    # The crash buffer is separate and short: it holds the tombstone reason for
    # a native death even after the main buffer rolled over.
    code, text = run(adb, *base, "logcat", "-b", "crash", "-d", "-v", "time")
    sections.append(f"$ adb logcat -b crash -d -> {code}\n{text}")
    return "\n\n".join(sections)


def pull_app_files(adb: str, serial: str | None, package: str) -> str:
    base = ["-s", serial] if serial else []
    sections: list[str] = []
    private = f"/data/data/{package}/files"
    external = f"/storage/emulated/0/Android/data/{package}/files"

    for name in ("rout3-boot.json", "rout3-startup.log"):
        code, text = run(adb, *base, "shell", "run-as", package,
                         "cat", f"files/{name}")
        sections.append(f"$ adb shell run-as {package} cat files/{name} -> {code}\n{text}")
    code, text = shell(adb, f"ls -la {private}")
    sections.append(f"$ adb shell ls -la {private} -> {code}\n{text}")
    for name in ("rout3-boot.json", "rout3-startup.log"):
        code, text = shell(adb, f"cat {external}/{name}")
        sections.append(f"$ adb shell cat {external}/{name} -> {code}\n{text}")
    return "\n\n".join(sections)


def device_facts(adb: str, serial: str | None) -> str:
    base = ["-s", serial] if serial else []
    props = ("ro.product.manufacturer", "ro.product.model", "ro.build.version.release",
             "ro.build.version.sdk", "ro.product.cpu.abi", "ro.product.cpu.abilist")
    lines = []
    for prop in props:
        _, text = run(adb, *base, "shell", "getprop", prop, timeout=10)
        lines.append(f"{prop} = {text.strip()}")
    _, text = run(adb, *base, "shell", "dumpsys", "package", DEFAULT_PACKAGE,
                  timeout=20)
    for line in text.splitlines():
        if "versionName" in line or "versionCode" in line or "primaryCpuAbi" in line:
            lines.append(line.strip())
    return "\n".join(lines)


def summary(text: str) -> list[str]:
    seen: list[str] = []
    for line in text.splitlines():
        lowered = line.lower()
        if any(token.lower() in lowered for token in INTERESTING):
            if line not in seen:
                seen.append(line)
    return seen


def apk_archs(path: str) -> list[str]:
    """``['arm64-v8a']`` for an APK, or ``[]`` when it cannot be read."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except (OSError, zipfile.BadZipFile):
        return []
    archs = set()
    for name in names:
        parts = name.split("/")
        if len(parts) == 3 and parts[0] == "lib":
            archs.add(parts[1])
    return sorted(archs)


def arch_warning(archs: list[str], facts: str) -> str | None:
    """The single fact that makes an installed APK unlaunchable.

    A 64-bit-only APK on a 32-bit device *installs* and then closes before any
    Python runs, so there is no marker, no logcat from the app and nothing to
    read on the next start.  Naming it here saves a whole build cycle.
    """
    if not archs:
        return None
    abilist = ""
    for line in facts.splitlines():
        if line.startswith("ro.product.cpu.abilist") or line.startswith("ro.product.cpu.abi "):
            abilist += " " + line.split("=", 1)[-1]
    supported = set(abilist.split())
    if supported and not (set(archs) & supported):
        return (f"APK содержит только {', '.join(archs)}, а устройство "
                f"поддерживает {', '.join(sorted(supported))}. Такой APK "
                "устанавливается и закрывается мгновенно, ещё до Python.")
    return None


def newest_apk() -> str | None:
    found = sorted(glob.glob(os.path.join(ROOT, "releases", "rout3-*.apk")))
    return found[-1] if found else None


# ── entry point ──────────────────────────────────────────────────────
def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--package", default=DEFAULT_PACKAGE)
    parser.add_argument("--serial", default=None,
                        help="device serial when several are attached")
    parser.add_argument("--seconds", type=float, default=15.0,
                        help="how long to record logcat (default: 15)")
    parser.add_argument("--no-launch", action="store_true",
                        help="do not clear logcat or start the app; only read files")
    parser.add_argument("--reinstall", metavar="APK",
                        help="adb install -r the APK first (keeps app data)")
    parser.add_argument("--out", default=None, help="report path")
    args = parser.parse_args(argv)

    adb = find_adb()
    if adb is None:
        print("adb не найден.", file=sys.stderr)
        print("Установите platform-tools или укажите ANDROID_HOME. "
              "Без adb журнал можно достать только на телефоне: "
              "Android/data/<пакет>/files/rout3-startup.log", file=sys.stderr)
        return 2

    attached = devices(adb)
    if args.serial:
        if args.serial not in attached:
            print(f"Устройство {args.serial} не в режиме 'device' "
                  f"(видно: {attached or 'ничего'})", file=sys.stderr)
            return 2
        serial = args.serial
    elif len(attached) == 1:
        serial = attached[0]
    elif not attached:
        print("Телефон не виден. Включите «Отладку по USB» и разрешите "
              "подключение на экране телефона.", file=sys.stderr)
        return 2
    else:
        print("Подключено несколько устройств, укажите --serial: "
              + ", ".join(attached), file=sys.stderr)
        return 2

    print(f"adb: {adb}\nустройство: {serial}\n")
    facts = device_facts(adb, serial)
    print(facts, "\n")

    apk = args.reinstall or newest_apk()
    if apk and not os.path.isabs(apk):
        apk = os.path.join(ROOT, apk)
    warning = arch_warning(apk_archs(apk) if apk else [], facts)
    if warning:
        print("ВНИМАНИЕ: " + warning + "\n")

    if args.reinstall:
        print(f"Установка {apk} ...")
        code, text = run(adb, "-s", serial, "install", "-r", apk, timeout=300)
        print(f"install -> {code}\n{text}\n")
        if code != 0:
            return 3
        # ``install -r`` keeps the previous boot marker; clear it so the next
        # start reports *this* run instead of the last one.
        run(adb, "-s", serial, "shell", "run-as", args.package,
            "rm", "-f", "files/rout3-boot.json", timeout=15)

    logcat = capture_logcat(adb, serial, not args.no_launch, args.package,
                            args.seconds)
    files = pull_app_files(adb, serial, args.package)
    report = "\n\n".join((
        "ROUT III — Android startup report",
        f"time: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"package: {args.package}",
        "device:\n" + facts,
        logcat,
        files,
    ))

    out = args.out or os.path.join(
        OUT_DIR, f"rout3-android-{time.strftime('%Y%m%d-%H%M%S')}.txt")
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as handle:
            handle.write(report + "\n")
        saved = out
    except OSError as exc:
        saved = f"<не удалось сохранить отчёт: {exc}>"

    print("\n── важное ──")
    highlights = summary(report)
    if highlights:
        print("\n".join(highlights[-80:]))
    else:
        print("совпадений нет — смотрите полный отчёт")
    print(f"\nотчёт: {saved}")

    boot = "rout3-boot.json" in report and '"status": "startup"' in report
    if boot:
        print("\nмаркер остался в статусе 'startup': запуск не дошёл до "
              "цикла событий (см. stage: выше).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
