"""smoke_linux_launchers.py — регрессионные проверки POSIX-лаунчеров.

Регрессионный тест (design D7) к change `linux-launcher-and-python-install`:
shell-скрипты должны остаться LF-only с shebang, с исполняемым битом в индексе
git, пробрасывать аргументы и ссылаться на общий `tools/ensure_python.sh`;
pinned-константы standalone CPython обязаны совпадать с ожидаемыми, а поиск
интерпретатора — отбрасывать слишком старые и free-threaded сборки.
`tools/bootstrap.py` обязан переиспользовать интерпретатор, которым запущен.

Проверки через `sh` пропускаются (с пометкой SKIP), если POSIX-оболочка недо-
ступна, чтобы тест оставался зелёным на машине без Git Bash/WSL.

Проверки поведения выполняются через сорсинг `tools/ensure_python.sh`: этот
способ не зависит от того, какие интерпретаторы есть в системе, — в отличие от
подмены PATH, которую MSYS/WSL-оболочки переписывают при старте.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS = (
    ROOT / "run_client.sh",
    ROOT / "run_server.sh",
    ROOT / "tools" / "ensure_python.sh",
)
# Пути, передаваемые в `sh`, всегда относительные к корню проекта со слэшами
# вперёд: Git Bash конвертирует PATH, но не аргументы, и путь вида
# `C:\...` в argv не разрешается.
REL_SCRIPTS = tuple(str(p.relative_to(ROOT)).replace(os.sep, "/") for p in SCRIPTS)
ENSURE_REL = REL_SCRIPTS[2]

# Pinned interpreter: python-build-standalone 20260924, install_only bundles.
EXPECTED_VERSION = "3.12.14"
EXPECTED_RELEASE = "20260924"
EXPECTED_URL_BASE = (
    "https://github.com/astral-sh/python-build-standalone/"
    f"releases/download/{EXPECTED_RELEASE}"
)
EXPECTED_SHA256 = {
    "x86_64-unknown-linux-gnu": "5eae8cf79dd47fc2496a4fccc892936be831ce7a84d984b2299dfb1cdb592682",
    "aarch64-unknown-linux-gnu": "c0574c9c9fec561eb192ad510e4e79aaad039929b3531b3bb873e2f630fb14a9",
}

# Явные пути Git for Windows идут первыми: у них контролируемый PATH без
# системного python, тогда как `bash` из WindowsApps — это обёртка WSL,
# подтягивающая PATH дистрибутива вместе с его собственным python3.
SHELL_CANDIDATES = (
    r"C:\Program Files\Git\bin\sh.exe",
    r"C:\Program Files\Git\usr\bin\sh.exe",
    r"C:\Program Files\Git\bin\bash.exe",
    "sh",
    "bash",
)


def find_shell() -> str | None:
    for candidate in SHELL_CANDIDATES:
        if os.path.sep in candidate:
            if Path(candidate).is_file():
                return candidate
            continue
        found = shutil.which(candidate)
        if found:
            return found
    return None


def run_shell(shell: str, args: list[str], env: dict | None = None,
              cwd: Path | None = None) -> subprocess.CompletedProcess:
    merged = dict(os.environ)
    if env:
        merged.update(env)
    return subprocess.run([shell, *args], capture_output=True, text=True,
                          env=merged, cwd=str(cwd or ROOT), timeout=180)


def eval_in_script(shell: str, body: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Source `tools/ensure_python.sh`, then run `body` inside it."""
    return run_shell(shell, ["-c", f". ./{ENSURE_REL}\n{body}"], env=env)


# --- 4.1 формат файлов, ссылки и pinned-константы -------------------------

def test_files_exist_and_are_lf_only():
    for path in SCRIPTS:
        assert path.is_file(), f"missing launcher: {path}"
        raw = path.read_bytes()
        assert raw.startswith(b"#!/usr/bin/env sh\n"), f"{path.name}: bad shebang"
        crlf = raw.count(b"\r\n")
        assert crlf == 0, f"{path.name}: {crlf} CRLF line ending(s); *.sh must be LF"
        assert raw.count(b"\r") == 0, f"{path.name}: stray CR"
        assert raw.endswith(b"\n"), f"{path.name}: no trailing newline"
        offenders = [b for b in raw if b >= 0x80]
        assert not offenders, f"{path.name}: {len(offenders)} non-ASCII byte(s); messages must stay ASCII"
    print("shell launchers exist, shebang + LF-only + ASCII OK")


def test_gitattributes_and_exec_bit():
    attributes = ROOT / ".gitattributes"
    assert attributes.is_file(), ".gitattributes missing (needed for *.sh eol=lf)"
    text = attributes.read_text(encoding="utf-8")
    assert "*.sh" in text and "eol=lf" in text, text

    git = shutil.which("git")
    if git is None:
        print("SKIP exec bit: git not available")
        return
    result = subprocess.run(
        [git, "ls-files", "-s", "--",
         *[str(p.relative_to(ROOT)).replace(os.sep, "/") for p in SCRIPTS]],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    modes = {}
    for line in result.stdout.splitlines():
        meta, _, name = line.partition("\t")
        modes[name] = meta.split()[0]
    for path in SCRIPTS:
        name = str(path.relative_to(ROOT)).replace(os.sep, "/")
        if name not in modes:
            print(f"SKIP exec bit for {name}: not staged in git")
            continue
        assert modes[name] == "100755", f"{name}: git mode {modes[name]}, want 100755"
    print(".gitattributes pins *.sh to LF; git exec bit (100755) OK")


def test_launcher_structure():
    client = SCRIPTS[0].read_text(encoding="utf-8")
    server = SCRIPTS[1].read_text(encoding="utf-8")
    for name, text in (("run_client.sh", client), ("run_server.sh", server)):
        assert "tools/ensure_python.sh" in text, f"{name}: must source tools/ensure_python.sh"
        assert '"$@"' in text, f'{name}: must forward arguments with "$@"'
        assert "rout_ensure_python" in text, f"{name}: must use rout_ensure_python"
        assert "bootstrap.py" in text, f"{name}: must go through tools/bootstrap.py"
    assert "tools/launch_client.py" in client, "run_client.sh must target tools/launch_client.py"
    assert "run_server.py" in server, "run_server.sh must target run_server.py"
    print('launchers source ensure_python.sh, forward "$@", target the right script OK')


def test_pinned_constants():
    text = SCRIPTS[2].read_text(encoding="utf-8")
    assert f"ROUT_PY_VERSION={EXPECTED_VERSION}" in text, "pinned version changed"
    assert f"releases/download/{EXPECTED_RELEASE}" in text, "pinned release changed"
    assert EXPECTED_URL_BASE in text, "pinned URL base changed"
    assert f"cpython-{EXPECTED_VERSION}%2B{EXPECTED_RELEASE}" in text, "pinned archive prefix changed"
    for triple, digest in EXPECTED_SHA256.items():
        assert triple in text, f"triple {triple} missing"
        assert digest in text, f"sha256 for {triple} changed or missing"
    assert "-install_only.tar.gz" in text, "archive suffix missing"
    print(f"pinned CPython {EXPECTED_VERSION} ({EXPECTED_RELEASE}) + sha256 constants OK")


# --- 4.2 синтаксис и выбор интерпретатора ----------------------------------

def test_shell_syntax():
    shell = find_shell()
    if shell is None:
        print("SKIP sh -n: no POSIX shell (sh/bash) available")
        return
    for rel in REL_SCRIPTS:
        result = run_shell(shell, ["-n", rel])
        assert result.returncode == 0, f"sh -n {rel} failed:\n{result.stderr}"
    print(f"sh -n OK for {len(REL_SCRIPTS)} scripts ({shell})")


def _stub_python(directory: Path, name: str, major: int, minor: int,
                 free_threaded: int, echo_argv: bool = False) -> Path:
    """Write an executable stub that answers the ensure_python version probe.

    With echo_argv it also prints its own arguments, one ``ARG <value>`` per
    line, so a caller can assert what the launcher actually executed.
    """
    directory.mkdir(parents=True, exist_ok=True)
    stub = directory / name
    lines = ["#!/bin/sh", 'if [ "$1" = "-c" ]; then']
    lines.append(f'  echo "{major} {minor} {0 if echo_argv else free_threaded}"')
    lines.append("  exit 0")
    lines.append("fi")
    if echo_argv:
        lines.append('for a in "$@"; do echo "ARG $a"; done')
        lines.append("exit 0")
    else:
        lines.append("exit 1")
    stub.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\n")
    stub.chmod(0o755)
    return stub


def test_launchers_run_from_any_directory_and_forward_arguments():
    """End-to-end: a stub interpreter reveals the resolved root and the argv.

    Each launcher runs from an unrelated cwd, so a broken root computation or
    a mangled "$@" becomes visible without a real Python or network access.
    """
    shell = find_shell()
    if shell is None:
        print("SKIP launcher argv check: no POSIX shell available")
        return
    elsewhere = tempfile.mkdtemp(prefix="rout_elsewhere_")
    try:
        with tempfile.TemporaryDirectory(dir=str(ROOT), prefix=".smoke_linux_") as tmp:
            prefix = Path(tmp) / "prefix"
            _stub_python(prefix / "python" / "bin", "python3", 3, 12, 0, echo_argv=True)
            tail = ["--flag", "a value with spaces", "--after"]
            for script, target in (("run_client.sh", "launch_client.py"),
                                   ("run_server.sh", "run_server.py")):
                result = run_shell(
                    shell, [script, *tail],
                    env={"ROUT_PYTHON_DIR": str(prefix), "ROUT_PYTHON_NO_INSTALL": "1"},
                    cwd=elsewhere,
                )
                assert result.returncode == 0, f"{script}: {result.stderr}"
                argv = [ln[4:] for ln in result.stdout.splitlines() if ln.startswith("ARG ")]
                assert argv, f"{script}: launcher never exec'd the stub interpreter"
                assert Path(argv[0]).name == target, \
                    f"{script}: wrong target, got {argv[0]!r}"
                assert Path(argv[0]).is_absolute(), \
                    f"{script}: target not resolved against the script root: {argv[0]!r}"
                assert argv[1] == "run", f"{script}: lost 'run' subcommand: {argv}"
                assert Path(argv[2]).name == "bootstrap.py", f"{script}: {argv}"
                assert argv[3:] == tail, f"{script}: arguments mangled: {argv[3:]}"
    finally:
        shutil.rmtree(elsewhere, ignore_errors=True)
    print("launchers: run from any cwd; target and \"$@\" arrive intact")


def test_version_gate():
    """Version and free-threadedness gate, independent of the host's PATH."""
    shell = find_shell()
    if shell is None:
        print("SKIP version gate: no POSIX shell available")
        return

    with tempfile.TemporaryDirectory(dir=str(ROOT), prefix=".smoke_linux_") as tmp:
        rel = Path(tmp).name
        cases = {
            "ok_312": (3, 12, 0, "ACCEPTED"),
            "ok_313": (3, 13, 0, "ACCEPTED"),
            "too_old_310": (3, 10, 0, "REJECTED"),
            "too_old_311": (3, 11, 0, "REJECTED"),
            "free_threaded": (3, 12, 1, "REJECTED"),
            "too_old_29": (2, 9, 0, "REJECTED"),
        }
        body_lines = []
        for name, (major, minor, ft, _expected) in cases.items():
            _stub_python(Path(tmp) / name, "python3.12", major, minor, ft)
            # The function echoes the resolved path with no trailing newline,
            # so it is captured instead of letting it merge with the marker.
            body_lines.append(
                f'_got=$(rout_python_usable "./{rel}/{name}/python3.12" 2>/dev/null) && '
                f'echo "{name} ACCEPTED $_got" || echo "{name} REJECTED"'
            )
        result = eval_in_script(shell, "\n".join(body_lines))
        assert result.returncode == 0, result.stderr
        reported = {}
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                reported[parts[0]] = parts[1]
        for name, (_m, _n, _ft, expected) in cases.items():
            got = reported.get(name)
            assert got == expected, f"{name}: expected {expected}, got {got} (stdout={result.stdout!r})"
    print("version gate: 3.10/3.11/2.9 and free-threaded rejected, 3.12/3.13 accepted")


def test_interpreter_discovery():
    """A usable interpreter is found and used without downloading anything."""
    shell = find_shell()
    if shell is None:
        print("SKIP interpreter discovery: no POSIX shell available")
        return

    with tempfile.TemporaryDirectory(dir=str(ROOT), prefix=".smoke_linux_") as tmp:
        rel = Path(tmp).name
        # 1. ROUT_PYTHON_DIR wins over PATH.
        prefix = Path(tmp) / "prefix"
        _stub_python(prefix / "python" / "bin", "python3", 3, 12, 0)
        result = run_shell(
            shell, [ENSURE_REL],
            env={"ROUT_PYTHON_NO_INSTALL": "1", "ROUT_PYTHON_DIR": str(prefix)},
        )
        assert result.returncode == 0, result.stderr
        # `command -v` echoes the candidate as given, so on Windows the path
        # keeps its backslashes; compare on the normalised form.
        chosen = result.stdout.strip().replace("\\", "/")
        assert chosen.endswith(f"{rel}/prefix/python/bin/python3"), \
            f"ROUT_PYTHON_DIR ignored: {result.stdout!r}"
        print("discovery: ROUT_PYTHON_DIR takes precedence over PATH")

        # 2. Nothing usable -> falls through to provisioning and stops there
        #    when the download is forbidden. An empty candidate list removes
        #    any dependence on what the host happens to have installed.
        body = (
            'ROUT_PY_CANDIDATES=""\n'
            "rout_ensure_python\n"
            'echo "UNREACHED rc=$?"\n'
        )
        result = eval_in_script(
            shell, body,
            env={"ROUT_PYTHON_NO_INSTALL": "1", "ROUT_PYTHON_DIR": ""},
        )
        assert result.returncode != 0, "falling back must not report success"
        assert "UNREACHED" not in result.stdout, result.stdout
        assert "No suitable CPython" in result.stderr, result.stderr
        assert "Downloading" not in result.stderr, \
            f"ROUT_PYTHON_NO_INSTALL must skip the download: {result.stderr}"
        print("discovery: no usable interpreter -> provisioning, download forbidden -> clean failure")


def _fake_uname(directory: Path, machine: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    uname = directory / "uname"
    uname.write_text(f"#!/bin/sh\necho {machine}\n", encoding="ascii", newline="\n")
    uname.chmod(0o755)
    return directory


def _dead_curl(directory: Path) -> Path:
    """A curl that always fails, so a broken test fails fast instead of
    pulling 64 MB over the network."""
    directory.mkdir(parents=True, exist_ok=True)
    curl = directory / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        "echo 'test stub: download disabled' >&2\n"
        "exit 22\n",
        encoding="ascii", newline="\n",
    )
    curl.chmod(0o755)
    return directory


def test_unsupported_architecture():
    shell = find_shell()
    if shell is None:
        print("SKIP architecture check: no POSIX shell available")
        return
    with tempfile.TemporaryDirectory(dir=str(ROOT), prefix=".smoke_linux_") as tmp:
        rel = Path(tmp).name
        bin_dir = _fake_uname(Path(tmp) / "bin", "riscv64")
        _dead_curl(bin_dir)
        # PATH is rewritten *inside* the sourced shell: MSYS/WSL prepend their
        # own entries at startup, so an inherited PATH would not win.
        # rout_install_python is called directly, because the arch check must
        # happen regardless of which interpreters the host provides.
        body = f'PATH="./{rel}/bin:$PATH"\nrout_install_python\n'
        result = eval_in_script(shell, body,
                                env={"ROUT_PYTHON_NO_INSTALL": "", "ROUT_PYTHON_DIR": ""})
        assert result.returncode != 0, "riscv64 must not be accepted"
        assert "Unsupported architecture" in result.stderr, result.stderr
        assert "Downloading" not in result.stderr, f"test must not reach the network: {result.stderr}"
    print("unsupported architecture -> clear error, non-zero exit")


def test_missing_downloader():
    shell = find_shell()
    if shell is None:
        print("SKIP missing-downloader check: no POSIX shell available")
        return
    with tempfile.TemporaryDirectory(dir=str(ROOT), prefix=".smoke_linux_") as tmp:
        rel = Path(tmp).name
        # Only `uname` is on PATH: the architecture is known, but neither curl
        # nor wget resolves, which is exactly the branch under test.
        _fake_uname(Path(tmp) / "bin", "x86_64")
        body = f'PATH="./{rel}/bin"\nrout_install_python\n'
        result = eval_in_script(shell, body,
                                env={"ROUT_PYTHON_NO_INSTALL": "", "ROUT_PYTHON_DIR": ""})
        assert result.returncode != 0, "must not report success without a downloader"
        assert "curl" in result.stderr, result.stderr
        assert "Downloading" not in result.stderr, f"test must not reach the network: {result.stderr}"
    print("no downloader available -> clear error, non-zero exit")


def test_corrupt_archive_is_rejected():
    """A download that returns garbage must not be unpacked (sha256 gate)."""
    shell = find_shell()
    if shell is None:
        print("SKIP corrupt archive check: no POSIX shell available")
        return
    with tempfile.TemporaryDirectory(dir=str(ROOT), prefix=".smoke_linux_") as tmp:
        rel = Path(tmp).name
        bin_dir = _fake_uname(Path(tmp) / "bin", "x86_64")
        curl = bin_dir / "curl"
        curl.write_text(
            "#!/bin/sh\n"
            "out=''; prev=''\n"
            'for a in "$@"; do\n'
            '  if [ "$prev" = "-o" ]; then out=$a; fi\n'
            "  prev=$a\n"
            "done\n"
            "printf 'not the pinned archive' > \"$out\"\n"
            "exit 0\n",
            encoding="ascii", newline="\n",
        )
        curl.chmod(0o755)
        target = Path(tmp) / "provisioned"
        body = f'PATH="./{rel}/bin:$PATH"\nrout_install_python\n'
        result = eval_in_script(
            shell, body,
            env={"ROUT_PYTHON_NO_INSTALL": "", "ROUT_PYTHON_DIR": str(target)},
        )
        assert result.returncode != 0, "corrupt archive must abort the install"
        assert "Checksum mismatch" in result.stderr, result.stderr
        assert not (target / "python" / "bin" / "python3").exists(), \
            "nothing may be unpacked when the checksum does not match"
    print("corrupt archive -> checksum gate aborts, nothing unpacked")


# --- 4.3 bootstrap переиспользует свой интерпретатор -----------------------

def test_bootstrap_reuses_sys_executable():
    """bootstrap must not require a `py`/`python` command name to exist."""
    probe = (
        "import sys; sys.path.insert(0, 'tools');"
        "import bootstrap; bootstrap.PY_CANDIDATES = ();"
        "found = bootstrap.find_python();"
        "print(found[0] if found else 'NONE')"
    )
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                            text=True, cwd=str(ROOT), timeout=60)
    assert result.returncode == 0, result.stderr
    reported = result.stdout.strip().splitlines()[-1].strip()
    assert reported != "NONE", (
        "find_python() found nothing with an empty PY_CANDIDATES: it must fall "
        "back to sys.executable, otherwise `python3 tools/bootstrap.py` fails "
        "on Linux"
    )
    assert Path(reported).name.lower().startswith("python"), reported
    print(f"bootstrap reuses sys.executable even with empty PY_CANDIDATES ({Path(reported).name})")


if __name__ == "__main__":
    test_files_exist_and_are_lf_only()
    test_gitattributes_and_exec_bit()
    test_launcher_structure()
    test_pinned_constants()
    test_shell_syntax()
    test_version_gate()
    test_interpreter_discovery()
    test_unsupported_architecture()
    test_missing_downloader()
    test_corrupt_archive_is_rejected()
    test_bootstrap_reuses_sys_executable()
    print("ALL LINUX LAUNCHER SMOKE TESTS PASSED")
