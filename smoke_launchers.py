"""smoke_launchers.py — побайтовые проверки Windows-лаунчеров (.bat).

Регрессионный тест (D4): `run_client.bat`/`run_server.bat` должны оставаться
ASCII-only с CRLF-переводами строк, сохранять порядок кандидатов интерпретатора
(`py -3.12` → `py -3` → `py` → `python`) и пробрасывать `%*` неизменным.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CLIENT = ROOT / "run_client.bat"
SERVER = ROOT / "run_server.bat"


def _bytes_ok(path: Path) -> list[str]:
    raw = path.read_bytes()
    errors = []
    lf = raw.count(b"\n")
    crlf = raw.count(b"\r\n")
    if lf == 0:
        errors.append("no newlines at all")
    if lf != crlf:
        errors.append(f"bare LF: {lf} LF vs {crlf} CRLF")
    if raw.count(b"\r") != crlf:
        errors.append("stray CR")
    if any(b >= 0x80 for b in raw):
        errors.append("non-ASCII byte(s) >= 0x80")
    if not raw.startswith(b"@echo off\r\n"):
        errors.append("missing '@echo off' header")
    return errors


def _invocations(path: Path) -> list[str]:
    lines = path.read_bytes().decode("ascii").split("\r\n")
    out = []
    for line in lines:
        s = line.strip()
        if s.startswith("py ") or s.startswith("python "):
            out.append(s)
    return out


def test_bat_ascii_crlf():
    for path in (CLIENT, SERVER):
        errors = _bytes_ok(path)
        assert not errors, f"{path.name}: {errors}"
    print(f"bat files ASCII+CRLF OK ({CLIENT.name}, {SERVER.name})")


def test_candidate_order_and_args():
    expected_order = ["py -3.12", "py -3", "py", "python"]
    for path in (CLIENT, SERVER):
        invocations = _invocations(path)
        assert len(invocations) == 4, (path.name, invocations)
        order = []
        for inv in invocations:
            if "-3.12" in inv:
                tag = "py -3.12"
            elif inv.startswith("py -3 "):
                tag = "py -3"
            elif inv.startswith("py "):
                tag = "py"
            else:
                tag = "python"
            order.append(tag)
        assert order == expected_order, (path.name, order)
        for inv in invocations:
            assert inv.endswith("%*"), (path.name, inv)
    lines = CLIENT.read_bytes().decode("ascii").split("\r\n")
    for i, line in enumerate(lines):
        if line.strip().startswith(("py ", "python ")):
            assert "if not errorlevel 1 goto :ok" in lines[i + 1], (i, line)
    print("candidate order (py -3.12 -> py -3 -> py -> python) + %* passthrough OK")


def test_bootstrap_targets():
    client_lines = CLIENT.read_bytes().decode("ascii").split("\r\n")
    server_lines = SERVER.read_bytes().decode("ascii").split("\r\n")
    assert any("tools\\bootstrap.py run tools\\launch_client.py %*" in l for l in client_lines)
    assert any("tools\\bootstrap.py run run_server.py %*" in l for l in server_lines)
    print("bootstrap targets (client -> tools\\launch_client.py, server -> run_server.py) OK")


def test_error_branch_ascii_pause():
    for path in (CLIENT, SERVER):
        lines = path.read_bytes().decode("ascii").split("\r\n")
        text = "\n".join(lines)
        ok_idx = [i for i, l in enumerate(lines) if l.strip() == ":ok"]
        pause_idx = [i for i, l in enumerate(lines) if l.strip() == "pause"]
        assert len(ok_idx) == 1 and len(pause_idx) == 1, (path.name, ok_idx, pause_idx)
        assert pause_idx[0] < ok_idx[0], (path.name, pause_idx, ok_idx)
        assert "Python not found." in text, path.name
        assert "https://python.org" in text, path.name
    print("error branch: ASCII message + pause before :ok OK")


if __name__ == '__main__':
    test_bat_ascii_crlf()
    test_candidate_order_and_args()
    test_bootstrap_targets()
    test_error_branch_ascii_pause()
    print("ALL LAUNCHER SMOKE TESTS PASSED")